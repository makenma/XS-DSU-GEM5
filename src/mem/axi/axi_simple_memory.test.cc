#include <gtest/gtest.h>

#include "mem/axi/axi_target_adapter.hh"

namespace gem5
{
namespace axi
{
namespace
{

TEST(AxiSimpleMemoryTest, NonPowerOfTwoBusPreservesLanesAcrossPageBoundary)
{
    AxiSimpleMemory memory({{0, 0x3000, 1}});
    for (uint64_t address : {uint64_t(0xff0), uint64_t(0x1000),
                             uint64_t(0xffc)}) {
        AxiAddressRequest request;
        request.address = address;
        request.beatCount = 1;
        request.size = 3;
        request.burst = AxiBurst::Incr;
        AxiDataPacket packet;
        packet.beatCount = 1;
        packet.last = true;
        const uint64_t lane = address % 24;
        packet.byteStrobe = uint64_t{0x55} << lane;
        packet.functionalData.assign(24, uint8_t((address >> 4) ^ 0x5a));
        packet.payloadDigest = payloadDigest(packet.functionalData);
        memory.commitWrite(request, {packet}, 24);
        const auto data = memory.readBeat(request, 0, 24);
        ASSERT_EQ(data.size(), 24u);
        for (uint64_t index = 0; index < data.size(); ++index) {
            const bool written = index >= lane && index < lane + 8 &&
                ((packet.byteStrobe >> index) & 1);
            EXPECT_EQ(data[index], written ? packet.functionalData[index] : 0);
        }
        for (uint64_t offset = 0; offset < 8; ++offset)
            EXPECT_EQ(memory.readByte(address + offset),
                      (offset % 2 == 0) ? uint8_t((address >> 4) ^ 0x5a) : 0);
    }
}

TEST(AxiSimpleMemoryTest, InvalidLaterBeatLeavesEarlierBytesUnchanged)
{
    AxiSimpleMemory memory({{0x1000, 0x1008, 1}});
    AxiAddressRequest request;
    request.address = 0x1000;
    request.beatCount = 2;
    request.size = 3;
    request.burst = AxiBurst::Incr;
    std::vector<AxiDataPacket> packets(2);
    for (uint16_t index = 0; index < packets.size(); ++index) {
        auto &packet = packets[index];
        packet.beatIndex = index;
        packet.beatCount = packets.size();
        packet.last = index + 1 == packets.size();
        packet.byteStrobe = 0xff;
        packet.functionalData.assign(8, 0x71);
        packet.payloadDigest = payloadDigest(packet.functionalData);
    }
    EXPECT_ANY_THROW(memory.commitWrite(request, packets, 8));
    for (uint64_t address = 0x1000; address < 0x1008; ++address)
        EXPECT_EQ(memory.readByte(address), 0);
}

TEST(AxiSimpleMemoryTest, ActualBeatRangeFailurePreservesCommittedPrefix)
{
    AxiSimpleMemory memory({{0x1000, 0x1010, 1}, {0x2000, 0x2004, 1}});
    AxiAddressRequest request;
    request.address = 0x1000;
    request.beatCount = 2;
    request.size = 3;
    request.burst = AxiBurst::Incr;
    std::vector<AxiDataPacket> packets(2);
    for (uint16_t index = 0; index < packets.size(); ++index) {
        auto &packet = packets[index];
        packet.beatIndex = index == 0 ? 0 : 512;
        packet.beatCount = packets.size();
        packet.last = index + 1 == packets.size();
        packet.byteStrobe = 0xff;
        packet.functionalData.assign(8, index == 0 ? 0x31 : 0x62);
        packet.payloadDigest = payloadDigest(packet.functionalData);
    }
    EXPECT_ANY_THROW(memory.commitWrite(request, packets, 8));
    for (uint64_t address = 0x1000; address < 0x1008; ++address)
        EXPECT_EQ(memory.readByte(address), 0x31);
    for (uint64_t address = 0x1008; address < 0x1010; ++address)
        EXPECT_EQ(memory.readByte(address), 0);
    for (uint64_t address = 0x2000; address < 0x2004; ++address)
        EXPECT_EQ(memory.readByte(address), 0x62);
}

TEST(AxiSimpleMemoryTest, UnwrittenBytesRemainZeroAfterSparseWrites)
{
    AxiSimpleMemory memory({{0, 0x10000, 1}});
    EXPECT_EQ(memory.readByte(0), 0);
    EXPECT_EQ(memory.readByte(0xffff), 0);
    memory.writeByte(0x1007, 0xa7);
    memory.writeByte(0xffff, 0xc3);
    EXPECT_EQ(memory.readByte(0x1007), 0xa7);
    EXPECT_EQ(memory.readByte(0x1006), 0);
    EXPECT_EQ(memory.readByte(0x1008), 0);
    EXPECT_EQ(memory.readByte(0xfffe), 0);
    EXPECT_EQ(memory.readByte(0xffff), 0xc3);
}

TEST(AxiSimpleMemoryTest, AdjacentPagesReturnExactFullWidthBeatBytes)
{
    AxiSimpleMemory memory({{0, 0x3000, 1}});
    for (uint64_t address = 0xfe0; address < 0x1020; ++address)
        memory.writeByte(address, uint8_t((address * 13) ^ 0x5a));
    for (uint64_t base : {uint64_t(0xfe0), uint64_t(0x1000)}) {
        AxiAddressRequest request;
        request.address = base;
        request.beatCount = 1;
        request.size = 5;
        request.burst = AxiBurst::Incr;
        const auto bytes = memory.readBeat(request, 0, 32);
        ASSERT_EQ(bytes.size(), 32);
        for (uint64_t lane = 0; lane < bytes.size(); ++lane)
            EXPECT_EQ(bytes[lane], uint8_t(((base + lane) * 13) ^ 0x5a));
    }
}

TEST(AxiSimpleMemoryTest, WriteStrobesPreserveUnselectedLanes)
{
    AxiSimpleMemory memory({{0, 0x3000, 1}});
    AxiAddressRequest request;
    request.address = 0x1000;
    request.beatCount = 1;
    request.size = 5;
    request.burst = AxiBurst::Incr;
    AxiDataPacket packet;
    packet.beatCount = 1;
    packet.beatIndex = 0;
    packet.last = true;
    packet.byteStrobe = 0x80000001;
    packet.functionalData.assign(32, 0x67);
    packet.payloadDigest = payloadDigest(packet.functionalData);
    memory.writeByte(0x1001, 0x22);
    memory.commitWrite(request, {packet}, 32);
    EXPECT_EQ(memory.readByte(0x1000), 0x67);
    EXPECT_EQ(memory.readByte(0x1001), 0x22);
    EXPECT_EQ(memory.readByte(0x1002), 0);
    EXPECT_EQ(memory.readByte(0x101f), 0x67);
}

TEST(AxiSimpleMemoryTest, FillHonorsPartialPagesAndDisjointRanges)
{
    AxiSimpleMemory memory({{0xff0, 0x1020, 1}, {0x3000, 0x3040, 1}});
    memory.fill(0x35);
    EXPECT_EQ(memory.readByte(0xff0), 0x35);
    EXPECT_EQ(memory.readByte(0xfff), 0x35);
    EXPECT_EQ(memory.readByte(0x1000), 0x35);
    EXPECT_EQ(memory.readByte(0x101f), 0x35);
    EXPECT_EQ(memory.readByte(0x303f), 0x35);
    EXPECT_FALSE(memory.contains(0xfef));
    EXPECT_FALSE(memory.contains(0x1020));
    EXPECT_FALSE(memory.contains(0x2000));
    EXPECT_ANY_THROW(memory.readByte(0x2000));
    EXPECT_ANY_THROW(memory.writeByte(0x3040, 0x10));
    memory.fill(0);
    EXPECT_EQ(memory.readByte(0xff0), 0);
    EXPECT_EQ(memory.readByte(0x303f), 0);
}

TEST(AxiSimpleMemoryTest, RangeCoverageRejectsInteriorHoles)
{
    AxiSimpleMemory memory({{0x1000, 0x1100, 1}, {0x1200, 0x1300, 1}});
    EXPECT_TRUE(memory.contains(0x10ff));
    EXPECT_TRUE(memory.contains(0x1200));
    EXPECT_FALSE(memory.containsRange(0x10ff, 0x102));
    EXPECT_TRUE(memory.containsRange(0x1000, 0x100));
    EXPECT_TRUE(memory.containsRange(0x12ff, 1));
    EXPECT_FALSE(memory.containsRange(0x12ff, 2));
    EXPECT_FALSE(memory.containsRange(0x1100, 1));
}

TEST(AxiSimpleMemoryTest, RangeCoverageTraversesAdjacentRangesWithoutOverflow)
{
    AxiSimpleMemory memory({{0x1100, 0x1200, 1}, {0x1000, 0x1100, 1},
                            {UINT64_MAX - 16, UINT64_MAX, 1}});
    EXPECT_TRUE(memory.containsRange(0x10ff, 2));
    EXPECT_TRUE(memory.containsRange(0x1000, 0x200));
    EXPECT_FALSE(memory.containsRange(0x1000, 0));
    EXPECT_FALSE(memory.containsRange(0x0fff, 2));
    EXPECT_TRUE(memory.containsRange(UINT64_MAX - 16, 16));
    EXPECT_FALSE(memory.containsRange(UINT64_MAX - 16, 17));
    EXPECT_FALSE(memory.containsRange(UINT64_MAX, 1));
    EXPECT_FALSE(AxiSimpleMemory().containsRange(0, 1));
}

}
}
}

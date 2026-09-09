#include <gtest/gtest.h>

#include "mem/axi/axi_target_adapter.hh"

namespace gem5
{
namespace axi
{
namespace
{

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

}
}
}

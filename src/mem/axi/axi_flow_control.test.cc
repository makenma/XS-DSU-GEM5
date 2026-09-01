#include <gtest/gtest.h>

#include <cstdint>

#include "mem/axi/axi_initiator_adapter.hh"
#include "mem/axi/axi_target_adapter.hh"

namespace gem5
{
namespace axi
{

namespace
{

AxiAddressRequest
request(uint32_t id, uint64_t address)
{
    AxiAddressRequest req;
    req.axiId = id;
    req.address = address;
    req.beatCount = 1;
    req.size = 3;
    req.burst = AxiBurst::Incr;
    return req;
}

AxiWBeat
wBeat(uint8_t value)
{
    AxiWBeat beat;
    beat.last = true;
    beat.byteStrobe = 0xff;
    beat.functionalData.assign(8, value);
    beat.payloadDigest = payloadDigest(beat.functionalData);
    return beat;
}

AxiInitiatorConfig
sourceConfig()
{
    AxiInitiatorConfig config;
    config.source = {0, 0};
    config.dataBusBytes = 8;
    config.idWidth = 4;
    config.maxOutstandingWrites = 4;
    config.maxOutstandingReads = 4;
    config.fifoDepths = {1, 1, 1, 1, 1};
    config.preAwBursts = 1;
    config.preAwBeats = 1;
    config.bRobTransactions = 4;
    config.rRobBeats = 4;
    config.ranges = {{0, 0x1000, 1}};
    config.defaultErrorTarget = 1;
    config.targetQuotas[1] = {4, 4, 4, 4};
    return config;
}

AxiCommonMeta
meta(uint64_t uid, uint32_t id)
{
    AxiCommonMeta value;
    value.txnUid = uid;
    value.targetSeq = 0;
    value.responseSeq = 0;
    value.srcNode = 0;
    value.srcPort = 0;
    value.dstNode = 1;
    value.axiId = id;
    return value;
}

AxiAddressPacket
addressPacket(uint64_t uid, uint32_t id, uint64_t address)
{
    AxiAddressPacket packet;
    packet.meta = meta(uid, id);
    packet.request = request(id, address);
    packet.writeOrdinal = uid;
    packet.decodeResp = AxiResp::Okay;
    return packet;
}

AxiDataPacket
dataPacket(const AxiAddressPacket &aw, uint8_t value)
{
    AxiDataPacket packet;
    packet.meta = aw.meta;
    packet.writeOrdinal = aw.writeOrdinal;
    packet.beatIndex = 0;
    packet.beatCount = 1;
    packet.last = true;
    packet.byteStrobe = 0xff;
    packet.resp = AxiResp::Okay;
    packet.address = aw.request.address;
    packet.functionalData.assign(8, value);
    packet.payloadDigest = payloadDigest(packet.functionalData);
    return packet;
}

AxiTargetConfig
targetConfig()
{
    AxiTargetConfig config;
    config.dstNode = 1;
    config.dataBusBytes = 8;
    config.capacity = {4, 4, 4, 4};
    config.orphanTransactions = 1;
    config.orphanBeats = 1;
    config.bReadyDepth = 1;
    config.rReadyDepth = 1;
    config.writeServiceDepth = 4;
    config.readServiceDepth = 4;
    config.writeBaseLatency = 0;
    config.readBaseLatency = 0;
    config.sourceQuotas[{0, 0}] = {4, 4, 4, 4};
    config.memoryRanges = {{0, 0x1000, 1}};
    return config;
}

} // anonymous namespace

TEST(AxiBackpressureTest, DepthOneChannelsBackpressure)
{
    AxiInitiatorState source(sourceConfig());
    ASSERT_TRUE(source.tryAcceptAw(request(0, 0x100), 1));
    EXPECT_FALSE(source.tryAcceptAw(request(1, 0x200), 1));
    ASSERT_TRUE(source.hasAwPacket());
    source.popAwPacket();
    ASSERT_TRUE(source.tryAcceptAw(request(1, 0x200), 2));
    ASSERT_TRUE(source.hasAwPacket());
    source.popAwPacket();

    ASSERT_TRUE(source.tryAcceptW(wBeat(0x10), 2));
    EXPECT_FALSE(source.tryAcceptW(wBeat(0x20), 2));
    ASSERT_TRUE(source.hasWPacket());
    source.popWPacket();
    ASSERT_TRUE(source.tryAcceptW(wBeat(0x20), 3));
    ASSERT_TRUE(source.hasWPacket());
    source.popWPacket();

    ASSERT_TRUE(source.tryAcceptAr(request(2, 0x300), 3));
    EXPECT_FALSE(source.tryAcceptAr(request(3, 0x400), 3));
    ASSERT_TRUE(source.hasArPacket());
    source.popArPacket();
    EXPECT_TRUE(source.tryAcceptAr(request(3, 0x400), 4));
}

TEST(AxiBackpressureTest, PausesAndResumesBAndR)
{
    AxiTargetState target(targetConfig());
    const auto write0 = addressPacket(10, 0, 0x100);
    const auto write1 = addressPacket(11, 1, 0x200);
    target.acceptAw(write0, 0);
    target.acceptW(dataPacket(write0, 0x10), 0);
    target.acceptAw(write1, 0);
    target.acceptW(dataPacket(write1, 0x20), 0);
    ASSERT_TRUE(target.hasBPacket());
    for (uint64_t cycle = 1; cycle <= 50; ++cycle) {
        target.advance(cycle);
        EXPECT_EQ(target.occupancy().bReady, 1);
    }
    target.popBPacket();
    ASSERT_TRUE(target.hasBPacket());
    target.popBPacket();

    const auto read0 = addressPacket(20, 2, 0x100);
    const auto read1 = addressPacket(21, 3, 0x200);
    target.acceptAr(read0, 51);
    target.acceptAr(read1, 51);
    ASSERT_TRUE(target.hasRPacket());
    for (uint64_t cycle = 52; cycle <= 101; ++cycle) {
        target.advance(cycle);
        EXPECT_EQ(target.occupancy().rReady, 1);
    }
    target.popRPacket();
    ASSERT_TRUE(target.hasRPacket());
    target.popRPacket();
    EXPECT_EQ(target.completedWrites(), 2);
    EXPECT_EQ(target.completedReads(), 2);
}

TEST(AxiBackpressureTest, QueueFullReturnsNoError)
{
    AxiInitiatorState source(sourceConfig());
    ASSERT_TRUE(source.tryAcceptAw(request(0, 0x100)));
    EXPECT_FALSE(source.tryAcceptAw(request(1, 0x200)));
    AxiBBeat b;
    AxiRBeat r;
    EXPECT_FALSE(source.tryConsumeB(b));
    EXPECT_FALSE(source.tryConsumeR(r));

    AxiTargetState target(targetConfig());
    const auto first = addressPacket(30, 0, 0x100);
    const auto second = addressPacket(31, 1, 0x200);
    target.acceptW(dataPacket(first, 0x30));
    EXPECT_FALSE(target.canAcceptW(dataPacket(second, 0x31)));
    EXPECT_FALSE(target.hasBPacket());
    EXPECT_FALSE(target.hasRPacket());
}

TEST(AxiBackpressureTest, RetriesOnlyOnLaterEdge)
{
    AxiInitiatorState source(sourceConfig());
    const AxiAddressRequest held = request(1, 0x200);
    ASSERT_TRUE(source.tryAcceptAw(request(0, 0x100), 10));
    EXPECT_FALSE(source.tryAcceptAw(held, 10));
    const AxiAddressRequest unchanged = held;
    ASSERT_TRUE(source.hasAwPacket());
    source.popAwPacket();
    ASSERT_TRUE(source.tryAcceptAw(held, 11));
    ASSERT_TRUE(source.hasAwPacket());
    const auto accepted = source.frontAwPacket();
    EXPECT_EQ(held.axiId, unchanged.axiId);
    EXPECT_EQ(held.address, unchanged.address);
    EXPECT_EQ(accepted.request.address, held.address);
    EXPECT_EQ(accepted.meta.acceptedTick, 11);
}

} // namespace axi
} // namespace gem5

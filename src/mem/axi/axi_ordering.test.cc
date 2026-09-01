#include <gtest/gtest.h>

#include <cstdint>
#include <vector>

#include "mem/axi/axi_initiator_adapter.hh"
#include "mem/axi/axi_target_adapter.hh"

namespace gem5
{
namespace axi
{

namespace
{

AxiAddressRequest
request(uint32_t id, uint64_t address = 0x100)
{
    AxiAddressRequest value;
    value.axiId = id;
    value.address = address;
    value.beatCount = 1;
    value.size = 3;
    value.burst = AxiBurst::Incr;
    return value;
}

AxiInitiatorConfig
sourceConfig(uint32_t aw_depth = 8)
{
    AxiInitiatorConfig config;
    config.source = {3, 2};
    config.dataBusBytes = 8;
    config.idWidth = 8;
    config.maxOutstandingWrites = 8;
    config.maxOutstandingReads = 8;
    config.fifoDepths = {aw_depth, 8, 8, 8, 16};
    config.preAwBursts = 4;
    config.preAwBeats = 8;
    config.bRobTransactions = 8;
    config.rRobBeats = 16;
    config.ranges = {{0, 0x1000, 1}, {0x1000, 0x2000, 2}};
    config.defaultErrorTarget = 1;
    config.targetQuotas[1] = {8, 8, 8, 8};
    config.targetQuotas[2] = {8, 8, 8, 8};
    return config;
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

AxiAddressPacket
issueWrite(AxiInitiatorState &source, const AxiAddressRequest &aw,
           uint8_t data = 0x5a)
{
    EXPECT_TRUE(source.tryAcceptAw(aw));
    EXPECT_TRUE(source.tryAcceptW(wBeat(data)));
    EXPECT_TRUE(source.hasAwPacket());
    AxiAddressPacket packet = source.frontAwPacket();
    source.popAwPacket();
    EXPECT_TRUE(source.hasWPacket());
    source.popWPacket();
    return packet;
}

AxiAddressPacket
issueRead(AxiInitiatorState &source, const AxiAddressRequest &ar)
{
    EXPECT_TRUE(source.tryAcceptAr(ar));
    EXPECT_TRUE(source.hasArPacket());
    AxiAddressPacket packet = source.frontArPacket();
    source.popArPacket();
    return packet;
}

AxiBPacket
bPacket(const AxiAddressPacket &aw, AxiResp response = AxiResp::Okay)
{
    AxiBPacket packet;
    packet.meta = aw.meta;
    packet.resp = response;
    return packet;
}

AxiDataPacket
rPacket(const AxiAddressPacket &ar, uint8_t value = 0)
{
    AxiDataPacket packet;
    packet.meta = ar.meta;
    packet.beatIndex = 0;
    packet.beatCount = 1;
    packet.last = true;
    packet.resp = AxiResp::Okay;
    packet.address = ar.request.address;
    packet.functionalData.assign(8, value);
    packet.payloadDigest = payloadDigest(packet.functionalData);
    return packet;
}

AxiCommonMeta
targetMeta(uint64_t uid, uint32_t id, uint64_t target_seq,
           uint64_t response_seq, bool read = false)
{
    AxiCommonMeta meta;
    meta.txnUid = uid;
    meta.targetSeq = target_seq;
    meta.responseSeq = response_seq;
    meta.srcNode = 3;
    meta.srcPort = 2;
    meta.dstNode = 1;
    meta.axiId = id;
    meta.acceptedTick = uid;
    (void)read;
    return meta;
}

AxiAddressPacket
targetAddress(uint64_t uid, uint32_t id, uint64_t target_seq,
              uint64_t response_seq, uint64_t address = 0x100)
{
    AxiAddressPacket packet;
    packet.meta = targetMeta(uid, id, target_seq, response_seq);
    packet.request = request(id, address);
    packet.writeOrdinal = uid;
    packet.decodeResp = AxiResp::Okay;
    return packet;
}

AxiDataPacket
targetW(const AxiAddressPacket &aw, uint8_t value)
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
    config.capacity = {8, 8, 8, 8};
    config.orphanTransactions = 4;
    config.orphanBeats = 4;
    config.bReadyDepth = 8;
    config.rReadyDepth = 8;
    config.writeServiceDepth = 8;
    config.readServiceDepth = 8;
    config.writeBaseLatency = 0;
    config.readBaseLatency = 0;
    config.sourceQuotas[{3, 2}] = {8, 8, 8, 8};
    config.memoryRanges = {{0, 0x1000, 1}};
    return config;
}

} // anonymous namespace

TEST(AxiOrderingTest, AllocatesSequencesAtAcceptance)
{
    AxiInitiatorState source(sourceConfig(1));
    ASSERT_TRUE(source.tryAcceptAw(request(7), 10));
    EXPECT_FALSE(source.tryAcceptAw(request(7), 11));
    ASSERT_TRUE(source.hasAwPacket());
    const AxiAddressPacket first = source.frontAwPacket();
    source.popAwPacket();
    ASSERT_TRUE(source.tryAcceptAw(request(7, 0x200), 12));
    ASSERT_TRUE(source.hasAwPacket());
    const AxiAddressPacket second = source.frontAwPacket();
    source.popAwPacket();
    ASSERT_TRUE(source.tryAcceptAr(request(7, 0x300), 13));
    ASSERT_TRUE(source.hasArPacket());
    const AxiAddressPacket read = source.frontArPacket();

    EXPECT_EQ(first.meta.txnUid & ((uint64_t{1} << 39) - 1), 0);
    EXPECT_EQ(second.meta.txnUid & ((uint64_t{1} << 39) - 1), 1);
    EXPECT_EQ(first.meta.targetSeq, 0);
    EXPECT_EQ(second.meta.targetSeq, 1);
    EXPECT_EQ(first.meta.responseSeq, 0);
    EXPECT_EQ(second.meta.responseSeq, 1);
    EXPECT_EQ(first.meta.acceptedTick, 10);
    EXPECT_EQ(second.meta.acceptedTick, 12);
    EXPECT_NE(read.meta.txnUid & (uint64_t{1} << 39), 0);
    EXPECT_EQ(read.meta.targetSeq, 0);
    EXPECT_EQ(read.meta.responseSeq, 0);
}

TEST(AxiOrderingTest, SameIdReadWaitsOlder)
{
    AxiInitiatorState source(sourceConfig());
    const auto older = issueRead(source, request(4, 0x100));
    const auto younger = issueRead(source, request(4, 0x200));
    source.acceptRPacket(rPacket(younger, 0x22), 1);
    AxiRBeat beat;
    EXPECT_FALSE(source.tryConsumeR(beat));
    source.acceptRPacket(rPacket(older, 0x11), 10);
    ASSERT_TRUE(source.tryConsumeR(beat));
    EXPECT_EQ(beat.functionalData[0], 0x11);
    ASSERT_TRUE(source.tryConsumeR(beat));
    EXPECT_EQ(beat.functionalData[0], 0x22);
    EXPECT_GT(source.progress().sameIdResponsesBlocked, 0);
}

TEST(AxiOrderingTest, DifferentIdReadCanInvert)
{
    AxiInitiatorState source(sourceConfig());
    const auto slow = issueRead(source, request(0, 0x100));
    const auto fast = issueRead(source, request(1, 0x200));
    source.acceptRPacket(rPacket(fast, 0x21), 1);
    AxiRBeat beat;
    ASSERT_TRUE(source.tryConsumeR(beat));
    EXPECT_EQ(beat.axiId, 1);
    source.acceptRPacket(rPacket(slow, 0x10), 10);
    ASSERT_TRUE(source.tryConsumeR(beat));
    EXPECT_EQ(beat.axiId, 0);
}

TEST(AxiOrderingTest, SameIdWriteCommitsInOrder)
{
    auto config = targetConfig();
    config.extraLatency = {{100, 10}, {101, 1}};
    AxiTargetState target(config);
    const auto older = targetAddress(100, 5, 0, 0);
    const auto younger = targetAddress(101, 5, 1, 1);
    target.acceptAw(older, 0);
    target.acceptW(targetW(older, 0x11), 0);
    target.acceptAw(younger, 0);
    target.acceptW(targetW(younger, 0x22), 0);
    target.advance(1);
    EXPECT_FALSE(target.hasBPacket());
    EXPECT_GT(target.progress().sameIdReadyBlocked, 0);
    target.advance(10);
    ASSERT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.frontBPacket().meta.targetSeq, 0);
    target.popBPacket();
    ASSERT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.frontBPacket().meta.targetSeq, 1);
    EXPECT_EQ(target.memory().readByte(0x100), 0x22);
}

TEST(AxiOrderingTest, SameIdBRetiresInOrder)
{
    AxiInitiatorState source(sourceConfig());
    const auto older = issueWrite(source, request(3, 0x100), 0x11);
    const auto younger = issueWrite(source, request(3, 0x200), 0x22);
    source.acceptBPacket(bPacket(younger), 1);
    AxiBBeat beat;
    EXPECT_FALSE(source.tryConsumeB(beat));
    source.acceptBPacket(bPacket(older), 10);
    ASSERT_TRUE(source.tryConsumeB(beat));
    EXPECT_EQ(beat.axiId, 3);
    ASSERT_TRUE(source.tryConsumeB(beat));
    EXPECT_EQ(source.progress().bTransactionsRetired, 2);
}

TEST(AxiOrderingTest, DifferentIdWriteCanInvert)
{
    auto config = targetConfig();
    config.extraLatency = {{200, 10}, {201, 1}};
    AxiTargetState target(config);
    const auto slow = targetAddress(200, 0, 0, 0, 0x100);
    const auto fast = targetAddress(201, 1, 0, 0, 0x200);
    target.acceptAw(slow, 0);
    target.acceptW(targetW(slow, 0x10), 0);
    target.acceptAw(fast, 0);
    target.acceptW(targetW(fast, 0x20), 0);
    target.advance(1);
    ASSERT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.frontBPacket().meta.axiId, 1);
    target.popBPacket();
    target.advance(10);
    ASSERT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.frontBPacket().meta.axiId, 0);
}

TEST(AxiOrderingTest, ReadWriteDomainsIndependent)
{
    AxiInitiatorState source(sourceConfig());
    (void)issueWrite(source, request(6, 0x100), 0x44);
    const auto read = issueRead(source, request(6, 0x200));
    source.acceptRPacket(rPacket(read, 0x33), 1);
    AxiRBeat beat;
    ASSERT_TRUE(source.tryConsumeR(beat));
    EXPECT_EQ(beat.axiId, 6);
    EXPECT_EQ(source.progress().rTransactionsRetired, 1);
    EXPECT_EQ(source.progress().bTransactionsRetired, 0);
}

TEST(AxiOrderingTest, SameIdCrossTargetRetiresGlobally)
{
    AxiInitiatorState source(sourceConfig());
    const auto target_one = issueWrite(source, request(2, 0x100), 0x11);
    const auto target_two = issueWrite(source, request(2, 0x1000), 0x22);
    EXPECT_EQ(target_one.meta.targetSeq, 0);
    EXPECT_EQ(target_two.meta.targetSeq, 0);
    EXPECT_EQ(target_one.meta.responseSeq, 0);
    EXPECT_EQ(target_two.meta.responseSeq, 1);
    source.acceptBPacket(bPacket(target_two), 1);
    AxiBBeat beat;
    EXPECT_FALSE(source.tryConsumeB(beat));
    source.acceptBPacket(bPacket(target_one), 10);
    ASSERT_TRUE(source.tryConsumeB(beat));
    ASSERT_TRUE(source.tryConsumeB(beat));
    EXPECT_EQ(source.progress().bTransactionsRetired, 2);
}

} // namespace axi
} // namespace gem5

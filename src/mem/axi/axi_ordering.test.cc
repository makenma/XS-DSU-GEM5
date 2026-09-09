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
              uint64_t response_seq, uint64_t address = 0x100,
              uint8_t qos = 0)
{
    AxiAddressPacket packet;
    packet.meta = targetMeta(uid, id, target_seq, response_seq);
    packet.request = request(id, address);
    packet.meta.qos = qos;
    packet.request.qos = qos;
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

class AxiReadArbitrationTest : public ::testing::Test
{
  protected:
    static AxiInitiatorConfig endpointConfig(AxiEndpointKey endpoint)
    {
        auto config = sourceConfig();
        config.source = endpoint;
        return config;
    }

    static AxiTargetConfig readTargetConfig(uint32_t ready_depth = 1)
    {
        auto config = targetConfig();
        config.capacity = {24, 24, 24, 24};
        config.bReadyDepth = 24;
        config.rReadyDepth = ready_depth;
        config.readBaseLatency = 10;
        config.sourceQuotas[{3, 5}] = {8, 8, 8, 8};
        config.sourceQuotas[{4, 0}] = {8, 8, 8, 8};
        return config;
    }

    static AxiAddressPacket read(AxiInitiatorState &source, uint32_t id,
                                 uint64_t address, uint16_t beats = 2)
    {
        auto ar = request(id, address);
        ar.beatCount = beats;
        return issueRead(source, ar);
    }

    static void expectBeat(AxiTargetState &target,
                           const AxiAddressPacket &ar, uint16_t index)
    {
        ASSERT_TRUE(target.hasRPacket());
        const auto &packet = target.frontRPacket();
        EXPECT_EQ(packet.meta.txnUid, ar.meta.txnUid);
        EXPECT_EQ(packet.meta.srcNode, ar.meta.srcNode);
        EXPECT_EQ(packet.meta.srcPort, ar.meta.srcPort);
        EXPECT_EQ(packet.meta.axiId, ar.meta.axiId);
        EXPECT_EQ(packet.beatIndex, index);
        EXPECT_EQ(packet.beatCount, ar.request.beatCount);
        EXPECT_EQ(packet.last, index + 1 == ar.request.beatCount);
        EXPECT_EQ(packet.resp, AxiResp::Okay);
        target.popRPacket();
    }

    AxiInitiatorState a{endpointConfig({3, 2})};
    AxiInitiatorState b{endpointConfig({3, 5})};
    AxiInitiatorState c{endpointConfig({4, 0})};
};

} // anonymous namespace

TEST_F(AxiReadArbitrationTest, RoundRobinAcrossReadySources)
{
    AxiTargetState target(readTargetConfig());
    const auto first = read(a, 4, 0x100);
    const auto second = read(c, 7, 0x200);
    target.acceptAr(second, 0);
    target.acceptAr(first, 0);
    EXPECT_FALSE(target.hasRPacket());
    target.advance(10);
    ASSERT_EQ(target.progress().readsCommitted, 2u);
    expectBeat(target, first, 0);
    expectBeat(target, second, 0);
    expectBeat(target, first, 1);
    expectBeat(target, second, 1);
    EXPECT_FALSE(target.hasRPacket());
    EXPECT_EQ(target.occupancy().readReservedBeats, 0u);
}

TEST_F(AxiReadArbitrationTest, SourcePortParticipatesInRoundRobin)
{
    AxiTargetState target(readTargetConfig(8));
    const auto first = read(a, 4, 0x100);
    const auto second = read(b, 4, 0x200);
    target.acceptAr(first, 0);
    target.acceptAr(second, 0);
    target.advance(10);
    ASSERT_EQ(target.occupancy().rReady, 4u);
    expectBeat(target, first, 0);
    expectBeat(target, second, 0);
    expectBeat(target, first, 1);
    expectBeat(target, second, 1);
    EXPECT_FALSE(target.hasRPacket());
}

TEST_F(AxiReadArbitrationTest, FullReadyQueuePreservesNextSource)
{
    AxiTargetState target(readTargetConfig());
    const auto first = read(a, 4, 0x100);
    const auto second = read(b, 4, 0x200);
    const auto third = read(c, 4, 0x300);
    target.acceptAr(first, 0);
    target.acceptAr(second, 0);
    target.acceptAr(third, 0);
    target.advance(10);
    target.advance(11);
    ASSERT_EQ(target.occupancy().rReady, 1u);
    for (uint16_t index = 0; index < 2; ++index) {
        expectBeat(target, first, index);
        expectBeat(target, second, index);
        expectBeat(target, third, index);
    }
    EXPECT_FALSE(target.hasRPacket());
}

TEST_F(AxiReadArbitrationTest, SkipsUnreadySourceThenRejoins)
{
    AxiTargetState target(readTargetConfig());
    const auto first = read(a, 4, 0x100);
    const auto delayed = read(b, 4, 0x200);
    const auto third = read(c, 4, 0x300);
    target.acceptAr(first, 0);
    target.acceptAr(third, 0);
    target.acceptAr(delayed, 5);
    target.advance(10);
    ASSERT_EQ(target.progress().readsCommitted, 2u);
    expectBeat(target, first, 0);
    target.advance(15);
    ASSERT_EQ(target.progress().readsCommitted, 3u);
    expectBeat(target, third, 0);
    expectBeat(target, first, 1);
    expectBeat(target, delayed, 0);
    expectBeat(target, third, 1);
    expectBeat(target, delayed, 1);
    EXPECT_FALSE(target.hasRPacket());
}

TEST_F(AxiReadArbitrationTest, PreservesWithinSourceUidAndBeatOrder)
{
    AxiTargetState target(readTargetConfig());
    const auto older = read(a, 4, 0x100);
    const auto younger = read(a, 4, 0x200);
    const auto other = read(c, 7, 0x300);
    target.acceptAr(younger, 0);
    target.acceptAr(older, 0);
    target.acceptAr(other, 0);
    target.advance(10);
    for (uint16_t index = 0; index < 2; ++index) {
        expectBeat(target, older, index);
        expectBeat(target, other, index);
    }
    expectBeat(target, younger, 0);
    expectBeat(target, younger, 1);
    EXPECT_FALSE(target.hasRPacket());
}

TEST_F(AxiReadArbitrationTest, PreservesTurnAcrossIdle)
{
    AxiTargetState target(readTargetConfig());
    const auto initial = read(a, 4, 0x100);
    target.acceptAr(initial, 0);
    target.advance(10);
    expectBeat(target, initial, 0);
    expectBeat(target, initial, 1);
    EXPECT_FALSE(target.hasRPacket());
    target.advance(11);
    const auto first = read(a, 5, 0x200);
    const auto second = read(b, 7, 0x300);
    target.acceptAr(first, 12);
    target.acceptAr(second, 12);
    target.advance(22);
    for (uint16_t index = 0; index < 2; ++index) {
        expectBeat(target, second, index);
        expectBeat(target, first, index);
    }
    EXPECT_FALSE(target.hasRPacket());
}

TEST_F(AxiReadArbitrationTest, WithinSourceReadyTimePrecedesUid)
{
    const auto blocker = read(c, 7, 0x300, 1);
    const auto older = read(a, 4, 0x100);
    const auto younger = read(a, 5, 0x200);
    auto config = readTargetConfig();
    config.extraLatency[older.meta.txnUid] = 10;
    AxiTargetState target(config);
    target.acceptAr(blocker, 0);
    target.acceptAr(older, 1);
    target.acceptAr(younger, 1);
    target.advance(10);
    target.advance(11);
    target.advance(21);
    ASSERT_EQ(target.progress().readsCommitted, 3u);
    expectBeat(target, blocker, 0);
    expectBeat(target, younger, 0);
    expectBeat(target, younger, 1);
    expectBeat(target, older, 0);
    expectBeat(target, older, 1);
    EXPECT_FALSE(target.hasRPacket());
}

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
    EXPECT_FALSE(source.tryConsumeR(beat, 200));
    source.acceptRPacket(rPacket(older, 0x11), 10);
    ASSERT_TRUE(source.tryConsumeR(beat, 200));
    EXPECT_EQ(beat.functionalData[0], 0x11);
    ASSERT_TRUE(source.tryConsumeR(beat, 200));
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
    ASSERT_TRUE(source.tryConsumeR(beat, 200));
    EXPECT_EQ(beat.axiId, 1);
    source.acceptRPacket(rPacket(slow, 0x10), 10);
    ASSERT_TRUE(source.tryConsumeR(beat, 200));
    EXPECT_EQ(beat.axiId, 0);
}

TEST(AxiOrderingTest, SameIdWriteCommitsInOrder)
{
    auto config = targetConfig();
    config.extraLatency = {{100, 10}, {101, 1}};
    AxiTargetState target(config);
    const auto older = targetAddress(100, 5, 0, 0, 0x100, 3);
    const auto younger = targetAddress(101, 5, 1, 1, 0x100, 9);
    target.acceptAw(older, 0);
    target.acceptW(targetW(older, 0x11), 0);
    target.acceptAw(younger, 0);
    target.acceptW(targetW(younger, 0x22), 0);
    EXPECT_EQ(target.progress().qosTransactions[3], 1);
    EXPECT_EQ(target.progress().qosTransactions[9], 1);
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

TEST(AxiOrderingTest, SameTickWriteUsesConfiguredTieBreak)
{
    AxiTargetConfig config = targetConfig();
    config.writeBaseLatency = 1;
    config.writeCommitTieBreakRanks = {{300, 2}, {301, 0}, {302, 1}};
    AxiTargetState target(config);
    const auto first = targetAddress(300, 0, 0, 0, 0x100);
    const auto second = targetAddress(301, 1, 0, 0, 0x200);
    const auto third = targetAddress(302, 2, 0, 0, 0x300);
    target.acceptAw(first, 0);
    target.acceptW(targetW(first, 0x10), 0);
    target.acceptAw(second, 0);
    target.acceptW(targetW(second, 0x20), 0);
    target.acceptAw(third, 0);
    target.acceptW(targetW(third, 0x30), 0);

    target.advance(1);
    std::vector<uint64_t> order;
    while (target.hasBPacket()) {
        order.push_back(target.frontBPacket().meta.txnUid);
        target.popBPacket();
    }
    EXPECT_EQ(order, (std::vector<uint64_t>{301, 302, 300}));
}

TEST(AxiOrderingTest, DifferentIdDelayedBDoesNotBlockReadyResponse)
{
    AxiTargetConfig config = targetConfig();
    config.bEjectionDelay.emplace(10, 100);
    AxiTargetState target(config);
    const auto slow = targetAddress(10, 0, 0, 0, 0x100);
    const auto fast = targetAddress(11, 1, 0, 0, 0x200);

    target.acceptAw(slow, 0);
    target.acceptW(targetW(slow, 0x10), 0);
    target.acceptAw(fast, 0);
    target.acceptW(targetW(fast, 0x20), 0);

    ASSERT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.frontBPacket().meta.axiId, 1);
    target.popBPacket();
    EXPECT_FALSE(target.hasBPacket());
    target.advance(100);
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
    ASSERT_TRUE(source.tryConsumeR(beat, 200));
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

#include <gtest/gtest.h>

#include <cstdint>
#include <exception>
#include <stdexcept>
#include <vector>

#include "mem/axi/axi_initiator_adapter.hh"
#include "mem/axi/axi_target_adapter.hh"

namespace gem5
{
namespace axi
{

namespace
{

AxiInitiatorConfig
sourceConfig(uint32_t pre_bursts = 4, uint32_t pre_beats = 32,
             uint32_t w_depth = 32)
{
    AxiInitiatorConfig config;
    config.source = {0, 0};
    config.dataBusBytes = 8;
    config.idWidth = 8;
    config.maxOutstandingWrites = 16;
    config.maxOutstandingReads = 16;
    config.fifoDepths = {16, w_depth, 16, 16, 64};
    config.preAwBursts = pre_bursts;
    config.preAwBeats = pre_beats;
    config.bRobTransactions = 16;
    config.rRobBeats = 256;
    config.ranges = {{0, 0x1000, 1}};
    config.defaultErrorTarget = 1;
    config.targetQuotas[1] = {16, 256, 16, 256};
    return config;
}

AxiAddressRequest
addressRequest(uint64_t address, uint16_t beats, uint8_t size = 3,
               uint32_t id = 0)
{
    AxiAddressRequest request;
    request.axiId = id;
    request.address = address;
    request.beatCount = beats;
    request.size = size;
    request.burst = AxiBurst::Incr;
    return request;
}

AxiWBeat
sourceBeat(bool last, uint8_t value = 0x5a, uint64_t strobe = 0xff)
{
    AxiWBeat beat;
    beat.last = last;
    beat.byteStrobe = strobe;
    beat.functionalData.assign(8, value);
    beat.payloadDigest = payloadDigest(beat.functionalData);
    return beat;
}

AxiCommonMeta
meta(uint64_t uid, uint32_t axi_id = 0)
{
    AxiCommonMeta meta;
    meta.txnUid = uid;
    meta.targetSeq = uid;
    meta.responseSeq = uid;
    meta.srcNode = 0;
    meta.srcPort = 0;
    meta.dstNode = 1;
    meta.axiId = axi_id;
    return meta;
}

AxiAddressPacket
awPacket(uint64_t uid, uint16_t beats = 1, uint32_t axi_id = 0)
{
    AxiAddressPacket packet;
    packet.meta = meta(uid, axi_id);
    packet.request = addressRequest(0x100 + uid * 0x20, beats, 3, axi_id);
    packet.writeOrdinal = uid;
    packet.decodeResp = AxiResp::Okay;
    return packet;
}

AxiDataPacket
wPacket(uint64_t uid, uint16_t index = 0, uint16_t beats = 1,
        uint32_t axi_id = 0)
{
    AxiDataPacket packet;
    packet.meta = meta(uid, axi_id);
    packet.writeOrdinal = uid;
    packet.beatIndex = index;
    packet.beatCount = beats;
    packet.last = index + 1 == beats;
    packet.byteStrobe = 0xff;
    packet.resp = AxiResp::Okay;
    packet.functionalData.assign(8, static_cast<uint8_t>(uid + index));
    packet.payloadDigest = payloadDigest(packet.functionalData);
    packet.address = 0x100 + uid * 0x20;
    return packet;
}

AxiTargetConfig
targetConfig(uint32_t contexts = 8, uint32_t beats = 64,
             uint32_t orphan_txns = 4, uint32_t orphan_beats = 32)
{
    AxiTargetConfig config;
    config.dstNode = 1;
    config.dataBusBytes = 8;
    config.capacity = {contexts, beats, 8, 64};
    config.orphanTransactions = orphan_txns;
    config.orphanBeats = orphan_beats;
    config.bReadyDepth = 8;
    config.rReadyDepth = 64;
    config.sourceQuotas[{0, 0}] = {contexts, beats, 8, 64};
    config.memoryRanges = {{0, 0x1000, 1}};
    return config;
}

} // anonymous namespace

TEST(AxiWritePairingTest, PairsAwFirst)
{
    AxiInitiatorState source(sourceConfig());
    ASSERT_TRUE(source.tryAcceptAw(addressRequest(0x100, 1)));
    ASSERT_TRUE(source.tryAcceptW(sourceBeat(true)));
    EXPECT_EQ(source.pairedBursts(), 1);
    ASSERT_TRUE(source.hasAwPacket());
    ASSERT_TRUE(source.hasWPacket());
    EXPECT_EQ(source.frontAwPacket().writeOrdinal, 0);
    EXPECT_EQ(source.frontWPacket().writeOrdinal, 0);
    EXPECT_EQ(source.frontAwPacket().meta.txnUid,
              source.frontWPacket().meta.txnUid);
}

TEST(AxiWritePairingTest, PairsWFirst)
{
    AxiInitiatorState source(sourceConfig());
    ASSERT_TRUE(source.tryAcceptW(sourceBeat(true)));
    EXPECT_FALSE(source.hasWPacket());
    EXPECT_EQ(source.occupancy().unboundBursts, 1);
    ASSERT_TRUE(source.tryAcceptAw(addressRequest(0x100, 1)));
    EXPECT_EQ(source.occupancy().unboundBursts, 0);
    ASSERT_TRUE(source.hasWPacket());
    EXPECT_EQ(source.frontWPacket().writeOrdinal, 0);
}

TEST(AxiWritePairingTest, BuffersMultiplePreAwBursts)
{
    AxiInitiatorState source(sourceConfig());
    ASSERT_TRUE(source.tryAcceptW(sourceBeat(true, 0x10)));
    ASSERT_TRUE(source.tryAcceptW(sourceBeat(true, 0x20)));
    EXPECT_EQ(source.occupancy().unboundBursts, 2);
    ASSERT_TRUE(source.tryAcceptAw(addressRequest(0x100, 1)));
    ASSERT_TRUE(source.tryAcceptAw(addressRequest(0x200, 1)));
    ASSERT_TRUE(source.hasWPacket());
    EXPECT_EQ(source.frontWPacket().writeOrdinal, 0);
    EXPECT_EQ(source.frontWPacket().functionalData[0], 0x10);
    source.popWPacket();
    ASSERT_TRUE(source.hasWPacket());
    EXPECT_EQ(source.frontWPacket().writeOrdinal, 1);
    EXPECT_EQ(source.frontWPacket().functionalData[0], 0x20);
}

TEST(AxiWritePairingTest, PairsNthWithNth)
{
    AxiInitiatorState source(sourceConfig());
    ASSERT_TRUE(source.tryAcceptW(sourceBeat(true, 0xa0)));
    ASSERT_TRUE(source.tryAcceptW(sourceBeat(true, 0xb0)));
    ASSERT_TRUE(source.tryAcceptAw(addressRequest(0x100, 1, 3, 4)));
    ASSERT_TRUE(source.tryAcceptAw(addressRequest(0x200, 1, 3, 7)));
    ASSERT_TRUE(source.hasWPacket());
    EXPECT_EQ(source.frontWPacket().meta.axiId, 4);
    EXPECT_EQ(source.frontWPacket().writeOrdinal, 0);
    source.popWPacket();
    ASSERT_TRUE(source.hasWPacket());
    EXPECT_EQ(source.frontWPacket().meta.axiId, 7);
    EXPECT_EQ(source.frontWPacket().writeOrdinal, 1);
}

TEST(AxiWritePairingTest, BlocksUnboundWInjection)
{
    AxiInitiatorState source(sourceConfig());
    ASSERT_TRUE(source.tryAcceptW(sourceBeat(false)));
    EXPECT_FALSE(source.hasWPacket());
    EXPECT_EQ(source.occupancy().w, 0);
    EXPECT_EQ(source.occupancy().unboundBeats, 1);
}

TEST(AxiWritePairingTest, StreamsPartialWAfterAw)
{
    AxiInitiatorState source(sourceConfig(1, 4, 4));
    for (int i = 0; i < 4; ++i)
        ASSERT_TRUE(source.tryAcceptW(sourceBeat(false, i)));
    EXPECT_FALSE(source.tryAcceptW(sourceBeat(false, 4)));
    ASSERT_TRUE(source.tryAcceptAw(addressRequest(0x100, 16)));

    int injected = 0;
    while (source.hasWPacket()) {
        source.popWPacket();
        ++injected;
    }
    for (int i = 4; i < 16; ++i) {
        ASSERT_TRUE(source.tryAcceptW(sourceBeat(i == 15, i)));
        ASSERT_TRUE(source.hasWPacket());
        source.popWPacket();
        ++injected;
    }
    EXPECT_EQ(injected, 16);
    EXPECT_TRUE(source.finalConsistencyError().empty());
}

TEST(AxiWritePairingTest, KeepsAwReadyWhenWFull)
{
    AxiInitiatorState source(sourceConfig(1, 1, 1));
    ASSERT_TRUE(source.tryAcceptW(sourceBeat(false)));
    EXPECT_FALSE(source.tryAcceptW(sourceBeat(true)));
    EXPECT_TRUE(source.tryAcceptAw(addressRequest(0x100, 2)));
    EXPECT_EQ(source.occupancy().unboundBursts, 0);
}

TEST(AxiTargetOrphanWTest, MergesWBeforeAw)
{
    AxiTargetState target(targetConfig());
    target.acceptW(wPacket(1));
    EXPECT_EQ(target.occupancy().orphanTransactions, 1);
    target.acceptAw(awPacket(1));
    EXPECT_EQ(target.occupancy().orphanTransactions, 0);
    EXPECT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.completedWrites(), 1);
}

TEST(AxiTargetOrphanWTest, BackpressuresNewOrphanAtLimit)
{
    AxiTargetState target(targetConfig(2, 4, 1, 1));
    target.acceptW(wPacket(1));
    EXPECT_FALSE(target.canAcceptW(wPacket(2)));
    EXPECT_EQ(target.occupancy().writeContexts, 1);
}

TEST(AxiTargetOrphanWTest, AllowsAwPastBlockedW)
{
    AxiTargetState target(targetConfig(2, 4, 1, 1));
    target.acceptW(wPacket(1));
    EXPECT_FALSE(target.canAcceptW(wPacket(2)));
    EXPECT_TRUE(target.canAcceptAw(awPacket(1)));
    target.acceptAw(awPacket(1));
    EXPECT_TRUE(target.hasBPacket());
}

TEST(AxiTargetOrphanWTest, ConvertsSharedSlotInPlace)
{
    AxiTargetState target(targetConfig());
    target.acceptW(wPacket(1, 0, 2));
    const auto before = target.occupancy();
    target.acceptAw(awPacket(1, 2));
    const auto after = target.occupancy();
    EXPECT_EQ(before.writeContexts, 1);
    EXPECT_EQ(after.writeContexts, 1);
    EXPECT_EQ(before.writeReservedBeats, after.writeReservedBeats);
    EXPECT_EQ(after.orphanTransactions, 0);
}

TEST(AxiTargetOrphanWTest, RejectsQuotaOversubscription)
{
    auto config = targetConfig(1, 2);
    config.sourceQuotas.clear();
    config.sourceQuotas[{0, 0}] = {1, 1, 1, 1};
    config.sourceQuotas[{1, 0}] = {1, 1, 1, 1};
    EXPECT_THROW(AxiTargetState target(config), std::invalid_argument);
}

TEST(AxiTargetOrphanWTest, RejectsDuplicatePacket)
{
    AxiTargetState target(targetConfig());
    target.acceptW(wPacket(1, 0, 2));
    EXPECT_ANY_THROW(target.acceptW(wPacket(1, 0, 2)));
    target.acceptAw(awPacket(1, 2));
    EXPECT_ANY_THROW(target.acceptAw(awPacket(1, 2)));
}

TEST(AxiLastAndBeatTest, HandlesSingleAnd256BeatLast)
{
    auto single = addressRequest(0x100, 1, 0);
    EXPECT_TRUE(validateAxiWriteBeat(
        single, 0, sourceBeat(true, 1, 1), 8).empty());

    auto longest = addressRequest(0x200, 256, 0);
    for (uint16_t index = 0; index < 256; ++index) {
        const uint64_t lane = uint64_t{1} <<
            (axiBeatAddress(longest, index) % 8);
        EXPECT_TRUE(validateAxiWriteBeat(
            longest, index, sourceBeat(index == 255, index, lane), 8).empty());
    }
}

TEST(AxiLastAndBeatTest, RejectsEarlyWlast)
{
    AxiTargetState target(targetConfig());
    auto early = wPacket(1, 0, 2);
    early.last = true;
    EXPECT_ANY_THROW(target.acceptW(early));
}

TEST(AxiLastAndBeatTest, RejectsMissingWlast)
{
    AxiTargetState target(targetConfig());
    auto missing = wPacket(1);
    missing.last = false;
    EXPECT_ANY_THROW(target.acceptW(missing));
}

TEST(AxiLastAndBeatTest, RejectsExtraWBeat)
{
    AxiTargetState target(targetConfig());
    auto extra = wPacket(1);
    extra.beatIndex = 1;
    EXPECT_ANY_THROW(target.acceptW(extra));
}

TEST(AxiLastAndBeatTest, RejectsDuplicateBeat)
{
    AxiTargetState target(targetConfig());
    target.acceptW(wPacket(1, 0, 2));
    EXPECT_ANY_THROW(target.acceptW(wPacket(1, 0, 2)));
}

TEST(AxiLastAndBeatTest, ReassemblesRByIndex)
{
    AxiInitiatorState source(sourceConfig());
    ASSERT_TRUE(source.tryAcceptAr(addressRequest(0x100, 2, 3, 5)));
    ASSERT_TRUE(source.hasArPacket());
    const AxiAddressPacket ar = source.frontArPacket();
    source.popArPacket();

    AxiDataPacket r1;
    r1.meta = ar.meta;
    r1.beatIndex = 1;
    r1.beatCount = 2;
    r1.last = true;
    r1.functionalData.assign(8, 0x22);
    r1.payloadDigest = payloadDigest(r1.functionalData);
    AxiDataPacket r0 = r1;
    r0.beatIndex = 0;
    r0.last = false;
    r0.functionalData.assign(8, 0x11);
    r0.payloadDigest = payloadDigest(r0.functionalData);

    source.acceptRPacket(r1);
    AxiRBeat beat;
    EXPECT_FALSE(source.tryConsumeR(beat));
    source.acceptRPacket(r0);
    ASSERT_TRUE(source.tryConsumeR(beat));
    EXPECT_FALSE(beat.last);
    EXPECT_EQ(beat.functionalData[0], 0x11);
    ASSERT_TRUE(source.tryConsumeR(beat));
    EXPECT_TRUE(beat.last);
    EXPECT_EQ(beat.functionalData[0], 0x22);
}

} // namespace axi
} // namespace gem5

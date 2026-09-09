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
    EXPECT_FALSE(source.tryConsumeR(r, 200));

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


// Sliding read window: maxOutstandingReads caps the number of in-flight
// read transactions between the AR handshake and the RLAST handshake.  A
// non-last R beat must not release credit; the 5th AR becomes acceptable
// only after the first transaction's RLAST is consumed.
TEST(AxiReadOutstandingTest, SlidingWindowReleasesCreditOnRlast)
{
    AxiInitiatorConfig config;
    config.source = {0, 0};
    config.dataBusBytes = 8;
    config.idWidth = 4;
    config.maxOutstandingWrites = 4;
    config.maxOutstandingReads = 4;
    config.fifoDepths = {8, 8, 8, 8, 64};
    config.preAwBursts = 8;
    config.preAwBeats = 64;
    config.bRobTransactions = 8;
    config.rRobBeats = 64;
    config.ranges = {{0, 0x10000, 1}};
    config.defaultErrorTarget = 1;
    config.targetQuotas[1] = {16, 16, 16, 64};
    for (const auto response : {AxiResp::Okay, AxiResp::SlvErr}) {
        AxiInitiatorState source(config);

        auto bigRead = [](uint32_t id, uint64_t address) {
            AxiAddressRequest req = request(id, address);
            req.beatCount = 8;
            return req;
        };
        auto readBeat = [](const AxiAddressPacket &ar, uint16_t beat_index) {
            AxiDataPacket packet;
            packet.meta = ar.meta;
            packet.beatIndex = beat_index;
            packet.beatCount = ar.request.beatCount;
            packet.last = beat_index + 1 == packet.beatCount;
            packet.resp = AxiResp::Okay;
            packet.address = ar.request.address +
                beat_index * (uint64_t{1} << ar.request.size);
            packet.functionalData.assign(8, uint8_t(beat_index));
            packet.payloadDigest = payloadDigest(packet.functionalData);
            return packet;
        };

        // Accept the first four ARs; each leaves the AR FIFO immediately.
        std::vector<AxiAddressPacket> ars;
        for (uint32_t index = 0; index < 4; ++index) {
            ASSERT_TRUE(source.tryAcceptAr(bigRead(index, 0x100 + index * 0x100)));
            ASSERT_TRUE(source.hasArPacket());
            ars.push_back(source.frontArPacket());
            source.popArPacket();
        }
        EXPECT_EQ(source.occupancy().outstandingReads, 4u);

        // The 5th AR is rejected even though the AR FIFO has been drained.
        EXPECT_FALSE(source.tryAcceptAr(bigRead(4, 0x500)));
        EXPECT_FALSE(source.tryAcceptAr(bigRead(5, 0x600)));

        // Seven non-last R beats of the first transaction release no credit.
        for (uint16_t beat = 0; beat < 7; ++beat) {
            auto packet = readBeat(ars[0], beat);
            ASSERT_TRUE(source.canAcceptRPacket(packet));
            source.acceptRPacket(packet, 100 + beat);
            AxiRBeat consumed;
            ASSERT_TRUE(source.tryConsumeR(consumed, 200));
            EXPECT_FALSE(consumed.last);
            EXPECT_EQ(source.occupancy().outstandingReads, 4u);
            EXPECT_FALSE(source.tryAcceptAr(bigRead(4, 0x500)));
        }

        // The RLAST beat releases the credit: outstanding drops to 3 and the
        // 5th AR is accepted immediately.
        auto last_packet = readBeat(ars[0], 7);
        last_packet.resp = response;
        source.acceptRPacket(last_packet, 150);
        EXPECT_EQ(source.firstCreditReleaseTick(), 0u);
        EXPECT_FALSE(source.tryAcceptAr(bigRead(4, 0x500)));
        AxiRBeat consumed;
        ASSERT_TRUE(source.tryConsumeR(consumed, 200));
        EXPECT_TRUE(consumed.last);
        EXPECT_EQ(source.firstCreditReleaseTick(), 200u);
        EXPECT_EQ(source.occupancy().outstandingReads, 3u);
        EXPECT_TRUE(source.tryAcceptAr(bigRead(0, 0x500)));
    }
}

TEST(AxiObservationTest, AddressRejectionsPreserveFirstReasonPriority)
{
    auto config = sourceConfig();
    config.maxOutstandingReads = 1;
    config.maxOutstandingWrites = 1;
    config.bRobTransactions = 1;
    AxiInitiatorState source(config);
    ASSERT_TRUE(source.tryAcceptAr(request(0, 0x100)));
    ASSERT_TRUE(source.tryAcceptAw(request(0, 0x100)));
    for (int attempt = 0; attempt < 2; ++attempt) {
        EXPECT_FALSE(source.tryAcceptAr(request(1, 0x200)));
        EXPECT_FALSE(source.tryAcceptAw(request(1, 0x200)));
    }
    EXPECT_EQ(source.progress().arRejectionAttempts.fifoFull, 2);
    EXPECT_EQ(source.progress().awRejectionAttempts.fifoFull, 2);
    EXPECT_EQ(source.progress().arRejectionAttempts.outstandingFull, 0);
    EXPECT_EQ(source.progress().awRejectionAttempts.outstandingFull, 0);
    source.popArPacket();
    source.popAwPacket();
    EXPECT_FALSE(source.tryAcceptAr(request(1, 0x200)));
    EXPECT_FALSE(source.tryAcceptAw(request(1, 0x200)));
    EXPECT_EQ(source.progress().arRejectionAttempts.outstandingFull, 1);
    EXPECT_EQ(source.progress().awRejectionAttempts.outstandingFull, 1);
    EXPECT_EQ(source.progress().arRejectionAttempts.responseReservationFull, 0);
    EXPECT_EQ(source.progress().awRejectionAttempts.responseReservationFull, 0);
    EXPECT_EQ(source.occupancy().outstandingReads, 1);
    EXPECT_EQ(source.occupancy().outstandingWrites, 1);
    EXPECT_EQ(source.nextAwOrdinal(), 1);
}

TEST(AxiObservationTest, ReadReservationRejectionsCoverSizeAndTotal)
{
    auto config = sourceConfig();
    config.fifoDepths[3] = 8;
    config.rRobBeats = 2;
    AxiInitiatorState source(config);
    auto request_two = request(0, 0x100);
    request_two.beatCount = 2;
    auto request_three = request_two;
    request_three.beatCount = 3;
    EXPECT_FALSE(source.tryAcceptAr(request_three));
    EXPECT_EQ(source.progress().arRejectionAttempts.responseReservationFull, 1);
    EXPECT_EQ(source.occupancy().outstandingReads, 0);
    ASSERT_TRUE(source.tryAcceptAr(request_two));
    source.popArPacket();
    EXPECT_FALSE(source.tryAcceptAr(request(1, 0x200)));
    EXPECT_EQ(source.progress().arRejectionAttempts.responseReservationFull, 2);
    EXPECT_EQ(source.progress().arRejectionAttempts.fifoFull, 0);
    EXPECT_EQ(source.progress().arRejectionAttempts.outstandingFull, 0);
    EXPECT_EQ(source.resourceOccupancy().rRobReservedBeats, 2);
}

TEST(AxiObservationTest, WriteResponseCapacityCannotUndersizeWindow)
{
    auto config = sourceConfig();
    config.bRobTransactions = config.maxOutstandingWrites - 1;
    EXPECT_THROW(AxiInitiatorState{config}, std::invalid_argument);
}

TEST(AxiObservationTest, ReadStagesArePassiveAndCloseAtRlast)
{
    auto config = sourceConfig();
    config.targetQuotas[1].readContexts = 1;
    AxiInitiatorState source(config);
    ASSERT_TRUE(source.tryAcceptAr(request(0, 0x100)));
    EXPECT_EQ(source.resourceOccupancy().readWaitingQuota, 1);
    EXPECT_EQ(source.resourceOccupancy().readWaitingQuota, 1);
    EXPECT_EQ(source.resourceOccupancy().readGrantedWaitingForward, 0);
    ASSERT_TRUE(source.hasArPacket());
    EXPECT_EQ(source.resourceOccupancy().readGrantedWaitingForward, 1);
    const auto first = source.frontArPacket();
    source.popArPacket();
    EXPECT_EQ(source.resourceOccupancy().readForwardedToMessageBuffer, 1);
    ASSERT_TRUE(source.tryAcceptAr(request(1, 0x200)));
    EXPECT_FALSE(source.hasArPacket());
    EXPECT_FALSE(source.hasArPacket());
    EXPECT_EQ(source.progress().readQuotaStalls, 1);
    EXPECT_EQ(source.resourceOccupancy().readWaitingQuota, 1);
    EXPECT_EQ(source.resourceOccupancy().rRobReservedBeats, 2);
    source.acceptRPacket(dataPacket(first, 0x2a), 10);
    EXPECT_EQ(source.progress().rPacketsBuffered, 1);
    EXPECT_EQ(source.resourceOccupancy().rRobBufferedBeats, 1);
    AxiRBeat consumed;
    ASSERT_TRUE(source.tryConsumeR(consumed, 11));
    EXPECT_EQ(source.resourceOccupancy().rRobReservedBeats, 1);
    EXPECT_EQ(source.resourceOccupancy().rRobBufferedBeats, 0);
    EXPECT_EQ(source.resourceOccupancy().readForwardedToMessageBuffer, 0);
    EXPECT_EQ(source.progress().rTransactionsRetired, 1);
    ASSERT_TRUE(source.hasArPacket());
    EXPECT_EQ(source.resourceOccupancy().readGrantedWaitingForward, 1);
}

TEST(AxiObservationTest, ReadRobLiveBeatsCountReadyAndReorderedOnce)
{
    AxiInitiatorState source(sourceConfig());
    auto req = request(0, 0x100);
    req.beatCount = 2;
    ASSERT_TRUE(source.tryAcceptAr(req));
    const auto ar = source.frontArPacket();
    source.popArPacket();
    auto first = dataPacket(ar, 0x10);
    first.beatCount = 2;
    first.last = false;
    auto last = first;
    last.beatIndex = 1;
    last.last = true;
    source.acceptRPacket(last, 1);
    EXPECT_EQ(source.resourceOccupancy().rRobBufferedBeats, 1);
    source.acceptRPacket(first, 2);
    EXPECT_EQ(source.occupancy().r, 1);
    EXPECT_EQ(source.resourceOccupancy().rRobBufferedBeats, 2);
    AxiRBeat beat;
    ASSERT_TRUE(source.tryConsumeR(beat, 3));
    EXPECT_FALSE(beat.last);
    EXPECT_EQ(source.resourceOccupancy().rRobReservedBeats, 2);
    EXPECT_EQ(source.resourceOccupancy().rRobBufferedBeats, 1);
    ASSERT_TRUE(source.tryConsumeR(beat, 4));
    EXPECT_TRUE(beat.last);
    EXPECT_EQ(source.resourceOccupancy().rRobReservedBeats, 0);
    EXPECT_EQ(source.resourceOccupancy().rRobBufferedBeats, 0);
}

TEST(AxiObservationTest, WriteStagesExcludeUnboundAndRetireOnB)
{
    auto config = sourceConfig();
    config.targetQuotas[1].writeContexts = 1;
    AxiInitiatorState source(config);
    ASSERT_TRUE(source.tryAcceptW(wBeat(0x42)));
    EXPECT_EQ(source.occupancy().unboundBursts, 1);
    EXPECT_EQ(source.resourceOccupancy().bRobReservedTransactions, 0);
    EXPECT_EQ(source.resourceOccupancy().writeWaitingQuota, 0);
    ASSERT_TRUE(source.tryAcceptAw(request(0, 0x100)));
    EXPECT_EQ(source.resourceOccupancy().writeWaitingQuota, 1);
    ASSERT_TRUE(source.hasAwPacket());
    EXPECT_EQ(source.resourceOccupancy().writeGrantedWaitingForward, 1);
    const auto aw = source.frontAwPacket();
    source.popAwPacket();
    source.popWPacket();
    EXPECT_EQ(source.resourceOccupancy().writeForwardedToMessageBuffer, 1);
    ASSERT_TRUE(source.tryAcceptAw(request(1, 0x200)));
    EXPECT_FALSE(source.hasAwPacket());
    EXPECT_FALSE(source.hasAwPacket());
    EXPECT_EQ(source.progress().writeQuotaStalls, 1);
    EXPECT_EQ(source.resourceOccupancy().writeWaitingQuota, 1);
    AxiBPacket response;
    response.meta = aw.meta;
    response.resp = AxiResp::Okay;
    source.acceptBPacket(response, 3);
    EXPECT_EQ(source.resourceOccupancy().bRobReservedTransactions, 2);
    EXPECT_EQ(source.resourceOccupancy().bRobBufferedTransactions, 1);
    EXPECT_EQ(source.progress().bPacketsBuffered, 1);
    AxiBBeat beat;
    ASSERT_TRUE(source.tryConsumeB(beat));
    EXPECT_EQ(source.resourceOccupancy().bRobReservedTransactions, 1);
    EXPECT_EQ(source.resourceOccupancy().bRobBufferedTransactions, 0);
    EXPECT_EQ(source.resourceOccupancy().writeForwardedToMessageBuffer, 0);
    EXPECT_EQ(source.progress().bTransactionsRetired, 1);
    ASSERT_TRUE(source.hasAwPacket());
    EXPECT_EQ(source.resourceOccupancy().writeGrantedWaitingForward, 1);
}

} // namespace axi
} // namespace gem5

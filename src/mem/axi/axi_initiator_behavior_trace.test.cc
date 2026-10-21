#include "mem/axi/axi_behavior_trace_support.hh"

namespace gem5::axi::behavior
{

TEST(AxiBehaviorTrace, StagedWritesKeepOrdinalOrderAcrossCopiesAndBackpressure)
{
    BehaviorTrace trace("StagedWritesKeepOrdinalOrderAcrossCopiesAndBackpressure");
    for (uint32_t depth : {1u, 2u, 8u}) {
        auto config = sourceConfig({1, 0});
        config.fifoDepths[1] = depth;
        AxiInitiatorState original(config);
        const std::array<uint16_t, 3> lengths{3, 2, 1};
        uint64_t tick = 1;
        for (size_t ordinal = 0; ordinal < lengths.size(); ++ordinal) {
            for (uint16_t index = 0; index < lengths[ordinal]; ++index) {
                AxiWBeat beat;
                beat.last = index + 1 == lengths[ordinal];
                beat.byteStrobe = 0xff;
                beat.functionalData.assign(8, uint8_t(ordinal * 16 + index));
                beat.payloadDigest = payloadDigest(beat.functionalData);
                trace.operation("stageW", tick);
                ASSERT_TRUE(original.tryAcceptW(beat, tick++));
                trace.source("original", original);
            }
        }
        std::vector<AxiAddressPacket> addresses;
        for (size_t ordinal = 0; ordinal < lengths.size(); ++ordinal) {
            AxiAddressRequest request;
            request.axiId = ordinal;
            request.address = 0x100 + ordinal * 0x40;
            request.beatCount = lengths[ordinal];
            request.size = 3;
            trace.operation("bindAw", tick);
            ASSERT_TRUE(original.tryAcceptAw(request, tick++));
            trace.source("original", original);
            addresses.push_back(original.frontAwPacket());
            original.popAwPacket();
        }
        AxiInitiatorState copied(original);
        AxiInitiatorState moving(original);
        AxiInitiatorState moved(std::move(moving));
        for (auto *source : {&original, &copied, &moved}) {
            for (size_t ordinal = 0; ordinal < lengths.size(); ++ordinal) {
                for (uint16_t index = 0; index < lengths[ordinal]; ++index) {
                    trace.operation("popStagedW", tick++);
                    ASSERT_TRUE(source->hasWPacket());
                    const auto packet = source->frontWPacket();
                    TraceRow row;
                    append(row, packet);
                    trace.record("packet", row);
                    EXPECT_EQ(packet.writeOrdinal, ordinal);
                    EXPECT_EQ(packet.beatIndex, index);
                    EXPECT_EQ(packet.last, index + 1 == lengths[ordinal]);
                    EXPECT_EQ(packet.functionalData[0], ordinal * 16 + index);
                    source->popWPacket();
                    trace.source("source", *source);
                }
            }
            EXPECT_FALSE(source->hasWPacket());
            for (const auto &address : addresses) {
                AxiBPacket response;
                response.meta = address.meta;
                response.resp = AxiResp::Okay;
                source->acceptBPacket(response, tick);
                AxiBBeat beat;
                ASSERT_TRUE(source->tryConsumeB(beat));
                EXPECT_EQ(beat.axiId, address.meta.axiId);
            }
            trace.operation("advanceDrained", tick++);
            EXPECT_NO_THROW(source->advance());
            trace.source("drained", *source);
            EXPECT_EQ(source->occupancy().outstandingWrites, 0u);
            EXPECT_TRUE(source->finalConsistencyError().empty());
            std::stringstream checkpoint;
            {
                boost::archive::binary_oarchive archive(checkpoint);
                EXPECT_NO_THROW(archive << *source);
            }
            AxiInitiatorState restored(config);
            boost::archive::binary_iarchive archive(checkpoint);
            EXPECT_NO_THROW(archive >> restored);
            trace.source("restored", restored);
        }
    }
}

TEST(AxiBehaviorTrace, StagedWriteValidationWaitsForReadyCapacity)
{
    BehaviorTrace trace("StagedWriteValidationWaitsForReadyCapacity");
    auto config = sourceConfig({1, 0});
    config.fifoDepths[1] = 1;
    AxiInitiatorState source(config);
    AxiAddressRequest request;
    request.address = 0x100;
    request.beatCount = 1;
    request.size = 3;
    ASSERT_TRUE(source.tryAcceptAw(request, 1));
    AxiWBeat beat;
    beat.last = true;
    beat.byteStrobe = 0xff;
    beat.functionalData.assign(8, 0x61);
    beat.payloadDigest = payloadDigest(beat.functionalData);
    ASSERT_TRUE(source.tryAcceptW(beat, 2));
    ASSERT_TRUE(source.tryAcceptW(beat, 3));
    request.axiId = 1;
    request.address = 0x109;
    request.size = 0;
    trace.operation("bindMalformedStagedWWhileFull", 4);
    ASSERT_TRUE(source.tryAcceptAw(request, 4));
    trace.source("source", source);
    source.popAwPacket();
    source.popAwPacket();
    trace.operation("validateAfterPop", 5);
    EXPECT_ANY_THROW(source.popWPacket());
    trace.source("afterError", source);
    trace.operation("advanceAfterError", 6);
    EXPECT_NO_THROW(source.advance());
    trace.source("afterAdvance", source);
    EXPECT_EQ(source.occupancy().w, 0u);
}

TEST(AxiBehaviorTrace, BFullPendingAndQueuedHead)
{
    BehaviorTrace trace("BFullPendingAndQueuedHead");
    for (bool older_queued : {false, true}) {
        auto config = sourceConfig({1, 0});
        config.fifoDepths[2] = 1;
        SourceProbe source(trace, "b-full." + std::to_string(older_queued), config);
        const auto older = source.issue(true, 1, 1, 0);
        const auto younger = source.issue(true, 1, 1, 0, 2);
        std::optional<AxiAddressPacket> blocker;
        if (older_queued) {
            source.receiveB(older, 30, 1);
        } else {
            blocker = source.issue(true, 7, 1, 0);
            source.receiveB(*blocker, 0, 1);
        }
        source.receiveB(younger, 1, 2, AxiResp::SlvErr);
        if (!older_queued)
            source.receiveB(older, 30, 2);
        source.advance(3);
        EXPECT_EQ(source.state.occupancy().b, 1u);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
        const auto read = source.issue(false, 8, 1, 4);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
        auto beat = source.consumeB(5);
        ASSERT_TRUE(beat);
        EXPECT_EQ(beat->axiId, older_queued ? 1u : 7u);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked,
                  older_queued ? 0u : 1u);
        if (!older_queued) {
            beat = source.consumeB(6);
            ASSERT_TRUE(beat);
            EXPECT_EQ(beat->axiId, 1u);
            EXPECT_EQ(beat->resp, AxiResp::Okay);
        }
        beat = source.consumeB(7);
        ASSERT_TRUE(beat);
        EXPECT_EQ(beat->axiId, 1u);
        EXPECT_EQ(beat->resp, AxiResp::SlvErr);
        EXPECT_FALSE(source.consumeB(7));
        source.receiveR(read, 0, 0x81, 7, 7);
        ASSERT_TRUE(source.consumeR(8));
        EXPECT_EQ(source.state.occupancy().outstandingWrites, 0u);
        EXPECT_EQ(source.state.occupancy().outstandingReads, 0u);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked,
                  older_queued ? 0u : 1u);
    }
    auto config = sourceConfig({1, 0});
    config.fifoDepths[2] = 2;
    SourceProbe source(trace, "b-queued-head", config);
    const auto older = source.issue(true, 1, 1, 0);
    const auto younger = source.issue(true, 1, 1, 0, 2);
    const auto other = source.issue(true, 2, 1, 0);
    source.receiveB(older, 30, 1);
    source.advance(2);
    source.advance(2);
    EXPECT_EQ(source.state.occupancy().b, 1u);
    source.receiveB(younger, 1, 3, AxiResp::SlvErr);
    source.advance(3);
    EXPECT_EQ(source.state.occupancy().b, 1u);
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 1u);
    source.receiveB(other, 20, 4);
    auto beat = source.consumeB(5);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 1u);
    EXPECT_EQ(beat->resp, AxiResp::Okay);
    EXPECT_EQ(source.state.occupancy().b, 2u);
    beat = source.consumeB(6);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 2u);
    source.advance(6);
    EXPECT_EQ(source.state.occupancy().b, 1u);
    beat = source.consumeB(7);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->resp, AxiResp::SlvErr);
    EXPECT_FALSE(source.consumeB(8));
    EXPECT_EQ(source.state.progress().bTransactionsRetired, 3u);
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 1u);
}

TEST(AxiBehaviorTrace, RFullPendingBeforeAndAfterRlast)
{
    BehaviorTrace trace("RFullPendingBeforeAndAfterRlast");
    for (uint16_t beats : {1u, 2u}) {
        auto config = sourceConfig({1, 0});
        config.fifoDepths[4] = 1;
        SourceProbe source(trace, "r-full." + std::to_string(beats), config);
        const auto older = source.issue(false, 1, beats, 0);
        const auto younger = source.issue(false, 1, 1, 0, 2);
        source.receiveR(older, 0, 0x10, 30, 1);
        source.receiveR(younger, 0, 0x50, 1, 2);
        if (beats == 2)
            source.receiveR(older, 1, 0x20, 1, 2);
        source.advance(3);
        EXPECT_EQ(source.state.occupancy().r, 1u);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
        const auto other = source.issue(false, 8, 1, 4);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
        auto beat = source.consumeR(5);
        ASSERT_TRUE(beat);
        EXPECT_EQ(beat->functionalData[0], 0x10);
        EXPECT_EQ(beat->last, beats == 1);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked,
                  beats == 1 ? 0u : 1u);
        if (beats == 2) {
            beat = source.consumeR(6);
            ASSERT_TRUE(beat);
            EXPECT_TRUE(beat->last);
            EXPECT_EQ(beat->functionalData[0], 0x2d);
        }
        beat = source.consumeR(7);
        ASSERT_TRUE(beat);
        EXPECT_TRUE(beat->last);
        EXPECT_EQ(beat->functionalData[0], 0x50);
        EXPECT_FALSE(source.consumeR(7));
        source.receiveR(other, 0, 0x81, 7, 7);
        ASSERT_TRUE(source.consumeR(8));
        EXPECT_EQ(source.state.occupancy().outstandingReads, 0u);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked,
                  beats == 1 ? 0u : 1u);
    }
}

TEST(AxiBehaviorTrace, RNextBeatReentersWithEarlierReadyTick)
{
    BehaviorTrace trace("RNextBeatReentersWithEarlierReadyTick");
    auto config = sourceConfig({1, 0});
    config.fifoDepths[4] = 1;
    SourceProbe source(trace, "r-ready-order", config);
    const auto blocker = source.issue(false, 7, 1, 0);
    const auto burst = source.issue(false, 1, 2, 0);
    const auto first = source.issue(false, 2, 1, 0);
    const auto last = source.issue(false, 3, 1, 0);
    source.receiveR(blocker, 0, 0x70, 0, 1);
    source.receiveR(burst, 1, 0x20, 1, 2);
    source.receiveR(burst, 0, 0x10, 30, 3);
    source.receiveR(first, 0, 0x50, 20, 4);
    auto beat = source.consumeR(5);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 7u);
    beat = source.consumeR(6);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 2u);
    source.receiveR(last, 0, 0x60, 20, 7);
    beat = source.consumeR(8);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 1u);
    EXPECT_FALSE(beat->last);
    EXPECT_EQ(beat->functionalData[0], 0x10);
    beat = source.consumeR(9);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 1u);
    EXPECT_TRUE(beat->last);
    EXPECT_EQ(beat->functionalData[0], 0x2d);
    beat = source.consumeR(10);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 3u);
    EXPECT_FALSE(source.consumeR(11));
    EXPECT_EQ(source.state.progress().rTransactionsRetired, 4u);
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
    EXPECT_EQ(source.state.occupancy().outstandingReads, 0u);
}

TEST(AxiBehaviorTrace, RQueuedSlotDuplicateDoesNotReenterReadyFifo)
{
    BehaviorTrace trace("RQueuedSlotDuplicateDoesNotReenterReadyFifo");
    auto config = sourceConfig({1, 0});
    config.fifoDepths[4] = 1;
    SourceProbe source(trace, "r-queued-duplicate", config);
    const auto burst = source.issue(false, 1, 3, 0);
    source.receiveR(burst, 0, 0x10, 30, 1);
    EXPECT_EQ(source.state.occupancy().r, 1u);
    source.receiveR(burst, 0, 0xd0, 1, 2);
    source.receiveR(burst, 1, 0x10, 1, 3);
    source.receiveR(burst, 2, 0x10, 1, 4);
    EXPECT_EQ(source.state.progress().rPacketsBuffered, 4u);
    EXPECT_EQ(source.state.resourceOccupancy().rRobBufferedBeats, 4u);
    EXPECT_EQ(source.state.resourceOccupancy().rRobReservedBeats, 3u);
    EXPECT_EQ(source.state.occupancy().r, 1u);
    EXPECT_EQ(source.state.occupancy().outstandingReads, 1u);
    EXPECT_EQ(source.state.progress().rTransactionsRetired, 0u);
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
    const std::array<size_t, 3> buffered{3, 2, 0};
    for (uint16_t index = 0; index < 3; ++index) {
        const auto beat = source.consumeR(5 + index);
        ASSERT_TRUE(beat);
        const auto expected = writeBeat(burst, index, 0x10);
        EXPECT_EQ(beat->axiId, 1u);
        EXPECT_EQ(beat->resp, AxiResp::Okay);
        EXPECT_EQ(beat->last, index == 2);
        EXPECT_EQ(beat->functionalData, expected.functionalData);
        EXPECT_EQ(beat->payloadDigest, expected.payloadDigest);
        EXPECT_EQ(source.state.resourceOccupancy().rRobBufferedBeats,
                  buffered[index]);
        EXPECT_EQ(source.state.resourceOccupancy().rRobReservedBeats,
                  index == 2 ? 0u : 3u);
        EXPECT_EQ(source.state.occupancy().r, index == 2 ? 0u : 1u);
        EXPECT_EQ(source.state.occupancy().outstandingReads,
                  index == 2 ? 0u : 1u);
        EXPECT_EQ(source.state.progress().rPacketsBuffered, 4u);
        EXPECT_EQ(source.state.progress().rTransactionsRetired,
                  index == 2 ? 1u : 0u);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
    }
    EXPECT_FALSE(source.consumeR(8));
}

TEST(AxiBehaviorTrace, RNonHeadLaterBeatsAreObservedTogether)
{
    BehaviorTrace trace("RNonHeadLaterBeatsAreObservedTogether");
    auto config = sourceConfig({1, 0});
    config.fifoDepths[4] = 1;
    SourceProbe source(trace, "r-later-pending", config);
    const auto blocker = source.issue(false, 7, 1, 0);
    const auto older = source.issue(false, 1, 1, 0);
    const auto first = source.issue(false, 1, 2, 0, 2);
    const auto second = source.issue(false, 1, 2, 0);
    source.receiveR(blocker, 0, 0x70, 0, 1);
    source.receiveR(first, 1, 0x20, 1, 2);
    source.receiveR(second, 1, 0x40, 1, 3);
    source.receiveR(older, 0, 0x10, 30, 4);
    source.advance(4);
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
    auto beat = source.consumeR(5);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 7u);
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 2u);
    EXPECT_EQ(source.state.occupancy().r, 1u);
    source.advance(5);
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 2u);
    beat = source.consumeR(6);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->functionalData[0], 0x10);
    EXPECT_EQ(source.state.occupancy().r, 0u);
    EXPECT_FALSE(source.consumeR(6));
    source.receiveR(second, 0, 0x30, 1, 7);
    EXPECT_EQ(source.state.occupancy().r, 0u);
    EXPECT_FALSE(source.consumeR(7));
    source.receiveR(first, 0, 0x10, 30, 8);
    for (uint8_t value : {0x10, 0x2d, 0x30, 0x4d}) {
        beat = source.consumeR(9);
        ASSERT_TRUE(beat);
        EXPECT_EQ(beat->axiId, 1u);
        EXPECT_EQ(beat->functionalData[0], value);
    }
    EXPECT_FALSE(source.consumeR(10));
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 2u);
    EXPECT_EQ(source.state.progress().rTransactionsRetired, 4u);
    EXPECT_EQ(source.state.resourceOccupancy().rRobBufferedBeats, 0u);
    EXPECT_EQ(source.state.occupancy().outstandingReads, 0u);
}

TEST(AxiBehaviorTrace, REqualReadyTickOrdersByUid)
{
    BehaviorTrace trace("REqualReadyTickOrdersByUid");
    auto config = sourceConfig({1, 0});
    config.fifoDepths[4] = 1;
    SourceProbe source(trace, "r-ready-tie", config);
    const auto blocker = source.issue(false, 7, 1, 0);
    const auto first = source.issue(false, 5, 1, 0, 2);
    const auto second = source.issue(false, 1, 1, 0);
    ASSERT_LT(first.meta.txnUid, second.meta.txnUid);
    source.receiveR(blocker, 0, 0x70, 0, 1);
    source.receiveR(second, 0, 0x20, 20, 2);
    source.receiveR(first, 0, 0x10, 20, 3);
    auto beat = source.consumeR(4);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 7u);
    beat = source.consumeR(5);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 5u);
    EXPECT_EQ(beat->functionalData[0], 0x10);
    beat = source.consumeR(6);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 1u);
    EXPECT_EQ(beat->functionalData[0], 0x20);
    EXPECT_FALSE(source.consumeR(7));
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
    EXPECT_EQ(source.state.progress().rTransactionsRetired, 3u);
    EXPECT_EQ(source.state.occupancy().outstandingReads, 0u);
}

TEST(AxiBehaviorTrace, RActiveCopiesAndMovesDrainIndependently)
{
    BehaviorTrace trace("RActiveCopiesAndMovesDrainIndependently");
    auto config = sourceConfig({1, 0});
    config.fifoDepths[4] = 1;
    SourceProbe original(trace, "r-original", config);
    const auto older = original.issue(false, 1, 2, 0);
    const auto younger = original.issue(false, 1, 2, 0, 2);
    const auto other = original.issue(false, 3, 2, 0);
    original.receiveR(older, 0, 0x10, 10, 1);
    original.receiveR(older, 1, 0x20, 30, 2);
    original.receiveR(younger, 1, 0x30, 1, 3);
    original.receiveR(other, 1, 0x50, 1, 4);
    EXPECT_EQ(original.state.progress().sameIdResponsesBlocked, 0u);
    EXPECT_EQ(original.state.resourceOccupancy().rRobBufferedBeats, 4u);
    SourceProbe copied(trace, "r-copied", original.state, 4);
    SourceProbe moved(trace, "r-moved", std::move(copied.state), 4);
    moved.receiveR(other, 0, 0x90, 5, 5);
    moved.receiveR(younger, 0, 0x80, 5, 5);
    trace.operation("observe.original.afterMovedInputs", 5);
    trace.source("r-original", original.state);
    EXPECT_EQ(original.state.resourceOccupancy().rRobBufferedBeats, 4u);
    EXPECT_EQ(moved.state.resourceOccupancy().rRobBufferedBeats, 6u);
    original.receiveR(other, 0, 0x40, 5, 6);
    original.receiveR(younger, 0, 0x60, 5, 6);
    for (SourceProbe *source : {&moved, &original}) {
        const bool is_moved = source == &moved;
        const std::array<uint32_t, 6> ids{1, 3, 3, 1, 1, 1};
        const std::array<bool, 6> last{false, false, true, true, false, true};
        const std::array<uint8_t, 6> values{
            0x10, uint8_t(is_moved ? 0x90 : 0x40), 0x5d,
            0x2d, uint8_t(is_moved ? 0x80 : 0x60), 0x3d};
        for (size_t index = 0; index < ids.size(); ++index) {
            const auto beat = source->consumeR(10 + index);
            ASSERT_TRUE(beat);
            EXPECT_EQ(beat->axiId, ids[index]);
            EXPECT_EQ(beat->last, last[index]);
            EXPECT_EQ(beat->functionalData[0], values[index]);
        }
        EXPECT_FALSE(source->consumeR(16));
        EXPECT_EQ(source->state.progress().sameIdResponsesBlocked, 1u);
        EXPECT_EQ(source->state.progress().rTransactionsRetired, 3u);
        EXPECT_EQ(source->state.resourceOccupancy().rRobBufferedBeats, 0u);
        EXPECT_EQ(source->state.occupancy().outstandingReads, 0u);
        if (is_moved) {
            trace.operation("observe.original.afterMovedDrain", 16);
            trace.source("r-original", original.state);
            EXPECT_EQ(original.state.occupancy().r, 1u);
            EXPECT_EQ(original.state.resourceOccupancy().rRobBufferedBeats, 6u);
            EXPECT_EQ(original.state.progress().rTransactionsRetired, 0u);
            EXPECT_EQ(original.state.progress().sameIdResponsesBlocked, 0u);
        }
    }
}

}

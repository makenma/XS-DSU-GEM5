#include "mem/axi/axi_behavior_trace_support.hh"

namespace gem5::axi::behavior
{

[[noreturn]] void
finishReadCommitPanic(BehaviorTrace &trace, const AxiTargetState &state)
{
    EXPECT_NE(gtestLogOutput.str().find(
        "AXI_PROTOCOL: read targetSeq retired twice"), std::string::npos);
    const auto progress = state.progress();
    const auto occupancy = state.occupancy();
    TraceRow row;
    row.field("error", "read_target_seq_retired_twice");
    append(row, occupancy);
    append(row, progress);
    trace.record("terminal", row);
    std::_Exit(::testing::Test::HasFailure() ? 87 : 86);
}

TEST(AxiBehaviorTrace, ReadStaleBlocksOtherDomainAndStopsUidPrefix)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("ReadStaleBlocksOtherDomainAndStopsUidPrefix");
        auto config = targetConfig(false, 16);
        config.rReadyDepth = 1;
        config.extraLatency[20] = 3;
        TargetProbe target(trace, "stale-prefix", config);
        ASSERT_TRUE(target.ar(address(50, 1, 0, 0x100), 0));
        ASSERT_TRUE(target.ar(address(20, 1, 0, 0x108), 0));
        target.advance(2);
        ASSERT_EQ(target.state.progress().readsCommitted, 1u);
        ASSERT_EQ(target.state.occupancy().rReady, 1u);
        ASSERT_TRUE(target.ar(address(10, 2, 1, 0x110), 3));
        ASSERT_TRUE(target.ar(address(30, 3, 1, 0x118), 3));
        ASSERT_TRUE(target.ar(address(5, 4, 0, 0x120, 2), 3));
        const auto write = address(80, 8, 0, 0x140);
        ASSERT_TRUE(target.aw(write, 3));
        ASSERT_TRUE(target.w(writeBeat(write, 0, 0x60), 3));
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(5);
        } catch (...) {
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
            EXPECT_EQ(target.state.progress().readsCommitted, 1u);
            EXPECT_EQ(target.state.progress().readCommittedBytes, 8u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().serviceReady, 6u);
            EXPECT_EQ(target.state.occupancy().rReady, 1u);
            EXPECT_EQ(target.state.occupancy().readContexts, 4u);
            EXPECT_EQ(target.state.occupancy().readReservedBeats, 5u);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{80}));
            EXPECT_EQ(target.first.policyBlocked, (std::vector<uint64_t>{0}));
            EXPECT_EQ(target.first.observerBlocked, (std::vector<uint64_t>{0}));
            EXPECT_EQ(target.second.observerBlocked, (std::vector<uint64_t>{0}));
            finishReadCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: read targetSeq retired twice");
}

TEST(AxiBehaviorTrace, ReadStaleWithoutEligibleStillFails)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("ReadStaleWithoutEligibleStillFails");
        auto config = targetConfig(false, 16);
        config.rReadyDepth = 1;
        config.extraLatency[20] = 3;
        TargetProbe target(trace, "stale-no-eligible", config);
        ASSERT_TRUE(target.ar(address(50, 1, 0, 0x100), 0));
        ASSERT_TRUE(target.ar(address(20, 1, 0, 0x108), 0));
        target.advance(2);
        ASSERT_EQ(target.state.progress().readsCommitted, 1u);
        ASSERT_TRUE(target.ar(address(10, 2, 1, 0x110), 3));
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(5);
        } catch (...) {
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
            EXPECT_EQ(target.state.progress().readsCommitted, 1u);
            EXPECT_EQ(target.state.progress().readCommittedBytes, 8u);
            EXPECT_EQ(target.state.progress().serviceReady, 3u);
            EXPECT_EQ(target.state.occupancy().rReady, 1u);
            EXPECT_EQ(target.state.occupancy().readContexts, 2u);
            finishReadCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: read targetSeq retired twice");
}

TEST(AxiBehaviorTrace, ReadDuplicateZeroSelectsReadyTimeBeforeUid)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("ReadDuplicateZeroSelectsReadyTimeBeforeUid");
        auto config = targetConfig(false, 16);
        config.readBaseLatency = 0;
        config.extraLatency = {{100, 2}, {1, 5}, {200, 5}};
        TargetProbe target(trace, "duplicate-zero", config);
        ASSERT_TRUE(target.ar(address(100, 1, 0, 0x100, 2), 0));
        ASSERT_TRUE(target.ar(address(1, 1, 0, 0x110), 0));
        ASSERT_TRUE(target.ar(address(200, 2, 0, 0x118), 0));
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(5);
        } catch (...) {
            EXPECT_EQ(target.state.progress().readsCommitted, 1u);
            EXPECT_EQ(target.state.progress().readCommittedBytes, 16u);
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
            EXPECT_EQ(target.state.progress().serviceReady, 3u);
            EXPECT_EQ(target.state.occupancy().rReady, 0u);
            EXPECT_EQ(target.state.occupancy().readContexts, 3u);
            EXPECT_EQ(target.state.occupancy().readReservedBeats, 4u);
            finishReadCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: read targetSeq retired twice");
}

TEST(AxiBehaviorTrace, ReadDuplicateOneRechecksPreviouslyBlockedUid)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("ReadDuplicateOneRechecksPreviouslyBlockedUid");
        auto config = targetConfig(false, 16);
        config.readBaseLatency = 0;
        config.extraLatency = {{500, 2}, {100, 1}, {5, 4}};
        TargetProbe target(trace, "duplicate-one", config);
        ASSERT_TRUE(target.ar(address(500, 1, 0, 0x100), 0));
        ASSERT_TRUE(target.ar(address(100, 1, 1, 0x108, 2), 0));
        ASSERT_TRUE(target.ar(address(5, 1, 1, 0x118, 3), 0));
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(5);
        } catch (...) {
            EXPECT_EQ(target.state.progress().readsCommitted, 2u);
            EXPECT_EQ(target.state.progress().readCommittedBytes, 24u);
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
            EXPECT_EQ(target.state.progress().serviceReady, 3u);
            EXPECT_EQ(target.state.occupancy().rReady, 0u);
            EXPECT_EQ(target.state.occupancy().readContexts, 3u);
            EXPECT_EQ(target.state.occupancy().readReservedBeats, 6u);
            finishReadCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: read targetSeq retired twice");
}

TEST(AxiBehaviorTrace, ReadFullFifoFreezesBeforeLaterMemoryWrites)
{
    BehaviorTrace trace("ReadFullFifoFreezesBeforeLaterMemoryWrites");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.rReadyDepth = 1;
        TargetProbe target(trace, "full-freeze." + std::to_string(synthetic), config);
        target.memory(0x100, 0x11, 0);
        ASSERT_TRUE(target.ar(address(900, 9, 0, 0x100), 0));
        target.advance(3);
        ASSERT_EQ(target.state.occupancy().rReady, 1u);
        ASSERT_EQ(target.state.completedReads(), 1u);
        ASSERT_TRUE(target.ar(address(10, 1, 0, 0x100, 2), 3));
        ASSERT_TRUE(target.ar(address(20, 1, 1, 0x110), 3));
        target.memory(0x100, 0x51, 3);
        target.memory(0x108, 0x52, 3);
        target.memory(0x110, 0x53, 3);
        target.advance(10);
        EXPECT_EQ(target.state.progress().readsCommitted, 3u);
        EXPECT_EQ(target.state.progress().readCommittedBytes, 32u);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
        EXPECT_EQ(target.state.completedReads(), 1u);
        EXPECT_EQ(target.state.occupancy().readContexts, 2u);
        EXPECT_EQ(target.state.occupancy().readReservedBeats, 3u);
        EXPECT_TRUE(target.state.canAcceptAr(address(91, 7, 0, 0x200, 61)));
        EXPECT_FALSE(target.state.canAcceptAr(address(92, 7, 0, 0x200, 62)));
        target.advance(10);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
        EXPECT_EQ(target.state.progress().readsCommitted, 3u);
        target.memory(0x100, 0xa1, 10);
        target.memory(0x108, 0xa2, 10);
        target.memory(0x110, 0xa3, 10);
        const auto filler = target.r(10);
        ASSERT_TRUE(filler);
        EXPECT_EQ(filler->meta.txnUid, 900u);
        EXPECT_EQ(filler->functionalData.front(), 0x11);
        for (uint16_t index = 0; index < 3; ++index) {
            const auto packet = target.r(10);
            ASSERT_TRUE(packet);
            EXPECT_EQ(packet->meta.txnUid, index < 2 ? 10u : 20u);
            EXPECT_EQ(packet->beatIndex, index == 1 ? 1u : 0u);
            EXPECT_EQ(packet->last, index != 0);
            std::vector<uint8_t> expected(8, 0);
            expected.front() = uint8_t(0x51 + index);
            EXPECT_EQ(packet->functionalData, expected);
            EXPECT_EQ(packet->payloadDigest, payloadDigest(expected));
            EXPECT_EQ(target.state.occupancy().readReservedBeats,
                      index == 0 ? 1u : 0u);
            EXPECT_EQ(target.state.occupancy().readContexts,
                      index == 0 ? 1u : 0u);
        }
        EXPECT_EQ(target.state.completedReads(), 3u);
        EXPECT_FALSE(target.state.hasRPacket());
        EXPECT_TRUE(target.state.canAcceptAr(address(93, 7, 0, 0x200, 64)));
        target.drain(11);
    }
}

TEST(AxiBehaviorTrace, ReadObservationFollowsWriteObserver)
{
    BehaviorTrace trace("ReadObservationFollowsWriteObserver");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.extraLatency[10] = 20;
        TargetProbe target(trace, "read-observer." + std::to_string(synthetic), config);
        ASSERT_TRUE(target.ar(address(10, 1, 0, 0x100), 0));
        ASSERT_TRUE(target.ar(address(20, 1, 1, 0x100), 0));
        const auto write = address(30, 9, 0, 0x100);
        ASSERT_TRUE(target.aw(write, 0));
        ASSERT_TRUE(target.w(writeBeat(write, 0, 0x66), 0));
        target.advance(10);
        EXPECT_EQ(target.first.policyBlocked, (std::vector<uint64_t>{0}));
        EXPECT_EQ(target.first.observerBlocked, (std::vector<uint64_t>{0}));
        EXPECT_EQ(target.second.observerBlocked, (std::vector<uint64_t>{0}));
        EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{30}));
        EXPECT_EQ(target.second.uids, target.first.uids);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
        EXPECT_EQ(target.state.progress().readsCommitted, 0u);
        EXPECT_EQ(target.state.progress().writesCommitted, 1u);
        target.advance(10);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
        target.advance(40);
        EXPECT_EQ(target.state.progress().readsCommitted, 2u);
        EXPECT_EQ(target.state.progress().readCommittedBytes, 16u);
        for (uint64_t uid : {10u, 20u}) {
            const auto packet = target.r(40);
            ASSERT_TRUE(packet);
            EXPECT_EQ(packet->meta.txnUid, uid);
            EXPECT_EQ(packet->functionalData, writeBeat(write, 0, 0x66).functionalData);
        }
        EXPECT_EQ(target.state.occupancy().readReservedBeats, 0u);
        target.drain(41);
    }
}

TEST(AxiBehaviorTrace, ReadCommitMetadataAndUidReuse)
{
    BehaviorTrace trace("ReadCommitMetadataAndUidReuse");
    const uint64_t maximum = std::numeric_limits<uint64_t>::max();
    auto config = targetConfig(false, 16);
    config.readBaseLatency = 0;
    config.extraLatency = {{0, 2}, {maximum, 1}, {9, 1}};
    TargetProbe target(trace, "read-metadata", config);
    auto head = address(0, 1, 0, 0x100);
    head.meta.responseSeq = 70;
    head.meta.acceptedTick = 1000;
    head.meta.qos = head.request.qos = 3;
    auto younger = address(maximum, 1, 1, 0x108);
    younger.meta.responseSeq = 2;
    younger.meta.acceptedTick = 0;
    younger.meta.qos = younger.request.qos = 7;
    ASSERT_TRUE(target.ar(head, 0));
    ASSERT_TRUE(target.ar(younger, 0));
    ASSERT_TRUE(target.ar(address(9, 2, maximum, 0x110), 0));
    target.advance(5);
    EXPECT_EQ(target.state.progress().readsCommitted, 2u);
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
    for (const auto &expected : {head, younger}) {
        const auto packet = target.r(5);
        ASSERT_TRUE(packet);
        EXPECT_EQ(packet->meta.txnUid, expected.meta.txnUid);
        EXPECT_EQ(packet->meta.targetSeq, expected.meta.targetSeq);
        EXPECT_EQ(packet->meta.responseSeq, expected.meta.responseSeq);
        EXPECT_EQ(packet->meta.acceptedTick, expected.meta.acceptedTick);
        EXPECT_EQ(packet->meta.qos, expected.meta.qos);
        EXPECT_EQ(packet->meta.axiId, expected.meta.axiId);
    }
    auto reused = head;
    reused.meta.targetSeq = 2;
    reused.meta.responseSeq = 99;
    reused.meta.acceptedTick = 2000;
    ASSERT_TRUE(target.ar(reused, 5));
    target.advance(7);
    const auto packet = target.r(7);
    ASSERT_TRUE(packet);
    EXPECT_EQ(packet->meta.txnUid, 0u);
    EXPECT_EQ(packet->meta.targetSeq, 2u);
    EXPECT_EQ(packet->meta.responseSeq, 99u);
    EXPECT_EQ(packet->meta.acceptedTick, 2000u);
    EXPECT_EQ(target.state.progress().readsCommitted, 3u);
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
    EXPECT_EQ(target.state.progress().qosTransactions[3], 2u);
    EXPECT_EQ(target.state.progress().qosTransactions[7], 1u);
    EXPECT_EQ(target.state.occupancy().readContexts, 1u);
    EXPECT_EQ(target.state.occupancy().readReservedBeats, 1u);
    target.advance(7);
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
    EXPECT_FALSE(target.state.hasRPacket());
}

TEST(AxiBehaviorTrace, ReadCommitCopiesMoveAndIdleCheckpoint)
{
    BehaviorTrace trace("ReadCommitCopiesMoveAndIdleCheckpoint");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.extraLatency = {{10, 20}, {30, 40}};
        TargetProbe target(trace, "copy-original." + std::to_string(synthetic), config);
        ASSERT_TRUE(target.ar(address(10, 1, 0, 0x100), 0));
        ASSERT_TRUE(target.ar(address(20, 1, 1, 0x100), 0));
        ASSERT_TRUE(target.ar(address(30, 2, 0, 0x100), 0));
        target.advance(5);
        ASSERT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
        ASSERT_EQ(target.state.occupancy().readContexts, 3u);
        ASSERT_EQ(target.state.occupancy().readServices, 2u);
        AxiTargetState copied = target.state;
        AxiTargetState moved = std::move(copied);
        trace.operation("copyAndMove", 5);
        trace.target("moved", moved);
        target.memory(0x100, 0x11, 5);
        moved.memory().writeByte(0x100, 0x77);
        trace.target("moved", moved);
        for (AxiTargetState *state : {&target.state, &moved}) {
            const std::string owner = state == &target.state ? "original" : "moved";
            trace.operation("advance." + owner, 50);
            state->advance(50);
            trace.target(owner, *state);
            EXPECT_EQ(state->progress().readsCommitted, 3u);
            EXPECT_EQ(state->progress().sameIdReadyBlocked, 1u);
            EXPECT_EQ(state->occupancy().readContexts, 0u);
            EXPECT_EQ(state->occupancy().readReservedBeats, 0u);
            for (uint64_t uid : {10u, 20u, 30u}) {
                ASSERT_TRUE(state->hasRPacket());
                const auto packet = state->frontRPacket();
                EXPECT_EQ(packet.meta.txnUid, uid);
                EXPECT_EQ(packet.functionalData.front(),
                          state == &target.state ? 0x11 : 0x77);
                TraceRow row;
                row.field("owner", owner);
                append(row, packet);
                trace.record("outputR", row);
                state->popRPacket();
                trace.target(owner, *state);
            }
            EXPECT_FALSE(state->hasRPacket());
            EXPECT_EQ(state->completedReads(), 3u);
            EXPECT_TRUE(state->canAcceptAr(address(40, 1, 2, 0x200, 64)));
        }
        ASSERT_TRUE(target.first.uids.empty());
        std::stringstream checkpoint;
        {
            boost::archive::binary_oarchive archive(checkpoint);
            archive << target.state;
        }
        const std::string bytes = checkpoint.str();
        TraceRow row;
        row.field("synthetic", synthetic)
            .field("bytes", bytesHex(std::vector<uint8_t>(bytes.begin(), bytes.end())));
        trace.record("checkpoint", row);
        TargetProbe restored(trace, "restored." + std::to_string(synthetic), config);
        {
            boost::archive::binary_iarchive archive(checkpoint);
            archive >> restored.state;
        }
        restored.snapshot();
        std::stringstream roundtrip;
        {
            boost::archive::binary_oarchive archive(roundtrip);
            archive << restored.state;
        }
        EXPECT_EQ(roundtrip.str(), bytes);
        EXPECT_EQ(restored.state.memory().readByte(0x100), 0x11);
        ASSERT_TRUE(restored.ar(address(40, 1, 2, 0x100), 60));
        restored.advance(65);
        const auto packet = restored.r(65);
        ASSERT_TRUE(packet);
        EXPECT_EQ(packet->meta.txnUid, 40u);
        EXPECT_EQ(packet->meta.targetSeq, 2u);
        EXPECT_EQ(packet->functionalData.front(), 0x11);
        EXPECT_EQ(restored.state.progress().readsCommitted, 4u);
        restored.drain(66);
    }
}

TEST(AxiBehaviorTrace, ReadCommitGateWrapReobservesDuplicateMaximum)
{
    BehaviorTrace trace("ReadCommitGateWrapReobservesDuplicateMaximum");
    const uint64_t maximum = std::numeric_limits<uint64_t>::max();
    for (bool previouslyBlocked : {false, true}) {
        auto config = targetConfig(false, 16);
        config.readBaseLatency = 0;
        config.extraLatency[30] = 1;
        if (previouslyBlocked)
            config.extraLatency[5] = 1;
        else
            config.extraLatency[10] = config.extraLatency[20] = 1;
        const std::array<uint64_t, 2> values{
            maximum - uint64_t(previouslyBlocked), previouslyBlocked ? 4u : 3u};
        ASSERT_EQ(values[0] + values[1], 2u);
        const std::string name = std::to_string(previouslyBlocked);
        TargetProbe seed(trace, "wrap-seed." + name, config);
        seed.memory(0x100, 0x61, 0);
        ASSERT_TRUE(seed.ar(address(100, 1, 0, 0x100), 0));
        ASSERT_TRUE(seed.ar(address(101, 2, 0, 0x108), 0));
        for (uint64_t uid : {100u, 101u}) {
            const auto packet = seed.r(0);
            ASSERT_TRUE(packet);
            ASSERT_EQ(packet->meta.txnUid, uid);
        }
        ASSERT_EQ(seed.state.progress().serviceReady, 2u);
        ASSERT_EQ(seed.state.progress().readsCommitted, 2u);
        ASSERT_EQ(seed.state.progress().architecturalCommits, 2u);
        ASSERT_EQ(seed.state.progress().readCommittedBytes, 16u);
        ASSERT_EQ(seed.state.progress().sameIdReadyBlocked, 0u);
        ASSERT_EQ(seed.state.completedReads(), 2u);
        ASSERT_EQ(seed.state.occupancy().readContexts, 0u);
        ASSERT_EQ(seed.state.occupancy().readReservedBeats, 0u);
        ASSERT_EQ(seed.state.occupancy().rReady, 0u);
        ASSERT_TRUE(seed.state.canAcceptAr(address(90, 3, 0, 0x200, 64)));
        std::stringstream checkpoint;
        {
            boost::archive::binary_oarchive archive(checkpoint);
            seed.state.serialize(archive, 0);
        }
        TraceRow history;
        const std::string bytes = checkpoint.str();
        history.field("firstGate", values[0]).field("secondGate", values[1])
            .field("bytes", bytesHex(std::vector<uint8_t>(bytes.begin(), bytes.end())));
        trace.record("gateSeed", history);
        TargetProbe target(trace, "wrap-restored." + name, config);
        {
            boost::archive::binary_iarchive archive(checkpoint);
            GateSeedArchive wrapper(archive, values);
            target.state.serialize(wrapper, 0);
            ASSERT_EQ(wrapper.matchedMaps, 1u);
        }
        target.snapshot();
        ASSERT_EQ(target.state.memory().readByte(0x100), 0x61);
        ASSERT_EQ(target.state.progress().readsCommitted, 2u);
        ASSERT_EQ(target.state.completedReads(), 2u);
        ASSERT_TRUE(target.first.uids.empty());
        ASSERT_TRUE(target.second.uids.empty());
        ASSERT_TRUE(target.ar(address(10, 1, maximum, 0x100), 0));
        ASSERT_TRUE(target.ar(address(20, 1, maximum, 0x100), 0));
        if (previouslyBlocked) {
            ASSERT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
            ASSERT_TRUE(target.ar(address(5, 1, maximum - 1, 0x100), 0));
        } else {
            ASSERT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
        }
        ASSERT_TRUE(target.ar(address(30, 2, values[1], 0x100), 0));
        target.advance(1);
        const uint64_t commits = previouslyBlocked ? 5 : 4;
        const uint64_t blocked = previouslyBlocked ? 2 : 1;
        EXPECT_EQ(target.state.progress().readsCommitted, commits);
        EXPECT_EQ(target.state.progress().architecturalCommits, commits);
        EXPECT_EQ(target.state.completedReads(), commits);
        EXPECT_EQ(target.state.progress().readCommittedBytes, commits * 8);
        EXPECT_EQ(target.state.progress().serviceReady, commits + 1);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, blocked);
        EXPECT_EQ(target.state.occupancy().rReady, commits - 2);
        EXPECT_EQ(target.state.occupancy().readContexts, 1u);
        EXPECT_EQ(target.state.occupancy().readReservedBeats, 1u);
        EXPECT_EQ(target.state.occupancy().readServices, 0u);
        target.advance(1);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, blocked);
        const auto expectedUids = previouslyBlocked ?
            std::vector<uint64_t>{5, 10, 30} : std::vector<uint64_t>{10, 30};
        for (uint64_t uid : expectedUids) {
            const auto packet = target.r(1);
            ASSERT_TRUE(packet);
            EXPECT_EQ(packet->meta.txnUid, uid);
            EXPECT_EQ(packet->meta.targetSeq,
                      uid == 30 ? values[1] : maximum - uint64_t(uid == 5));
            std::vector<uint8_t> expected(8, 0);
            expected.front() = 0x61;
            EXPECT_EQ(packet->functionalData, expected);
            EXPECT_EQ(packet->payloadDigest, payloadDigest(expected));
            EXPECT_TRUE(packet->last);
        }
        EXPECT_FALSE(target.state.hasRPacket());
        EXPECT_EQ(target.state.occupancy().readContexts, 1u);
        EXPECT_EQ(target.state.occupancy().readReservedBeats, 1u);
        EXPECT_TRUE(target.state.canAcceptAr(address(91, 3, 0, 0x200, 63)));
        EXPECT_FALSE(target.state.canAcceptAr(address(92, 3, 0, 0x200, 64)));
        target.advance(1);
        EXPECT_EQ(target.state.progress().readsCommitted, commits);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, blocked);
    }
}

}

#include "mem/axi/axi_behavior_trace_support.hh"

namespace gem5::axi::behavior
{

[[noreturn]] void
finishWriteCommitPanic(BehaviorTrace &trace, const AxiTargetState &state,
    const std::string &message = "AXI_PROTOCOL: write targetSeq retired twice")
{
    EXPECT_NE(gtestLogOutput.str().find(message), std::string::npos);
    TraceRow row;
    row.field("error", message);
    append(row, state.occupancy());
    append(row, state.progress());
    row.field("completedWrites", state.completedWrites())
        .field("completedReads", state.completedReads());
    trace.record("terminal", row);
    std::_Exit(::testing::Test::HasFailure() ? 87 : 86);
}

class WriteMemoryObserver : public AxiWriteCommitObserver
{
  public:
    WriteMemoryObserver(BehaviorTrace &trace, const AxiTargetState &state)
        : trace(trace), state(state)
    {}

    void onAxiWriteCommitted(const AxiAddressRequest &request,
        const std::vector<AxiDataPacket> &, AxiResp response) override
    {
        const auto value = state.memory().readByte(request.address);
        values.push_back(value);
        responses.push_back(response);
        TraceRow row;
        row.field("address", request.address).field("value", value)
            .field("response", response);
        trace.record("observerMemory", row);
    }

    std::vector<uint8_t> values;
    std::vector<AxiResp> responses;

  private:
    BehaviorTrace &trace;
    const AxiTargetState &state;
};

TEST(AxiBehaviorTrace, WriteDuplicateZeroDepthOneDefersUntilPop)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("WriteDuplicateZeroDepthOneDefersUntilPop");
        auto config = targetConfig(false, 16);
        config.bReadyDepth = 1;
        config.writeBaseLatency = 0;
        config.extraLatency = {{100, 2}, {1, 5}};
        TargetProbe target(trace, "duplicate-zero-depth-one", config);
        const auto older = address(100, 1, 0, 0x100, 2);
        const auto duplicate = address(1, 1, 0, 0x110);
        for (const auto &packet : {older, duplicate}) {
            ASSERT_TRUE(target.aw(packet, 0));
            for (uint16_t beat = 0; beat < packet.request.beatCount; ++beat)
                ASSERT_TRUE(target.w(writeBeat(packet, beat, 0x40), 0));
        }
        target.advance(5);
        ASSERT_EQ(target.first.uids, (std::vector<uint64_t>{100}));
        ASSERT_EQ(target.state.completedWrites(), 1u);
        ASSERT_EQ(target.state.progress().writesCommitted, 1u);
        ASSERT_EQ(target.state.progress().writeCommittedBytes, 16u);
        ASSERT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
        ASSERT_EQ(target.state.occupancy().bReady, 1u);
        ASSERT_EQ(target.state.occupancy().writeContexts, 1u);
        target.advance(5);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.b(5);
        } catch (...) {
            EXPECT_EQ(target.state.occupancy().bReady, 0u);
            EXPECT_EQ(target.state.occupancy().writeReservedBeats, 1u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().writeCommittedBytes, 16u);
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
            EXPECT_EQ(target.state.memory().readByte(0x100), 0x40);
            EXPECT_EQ(target.state.memory().readByte(0x110), 0u);
            finishWriteCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: write targetSeq retired twice");
}

TEST(AxiBehaviorTrace, WriteDuplicateZeroDepthTwoFailsInSameAdvance)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("WriteDuplicateZeroDepthTwoFailsInSameAdvance");
        auto config = targetConfig(false, 16);
        config.bReadyDepth = 2;
        config.writeBaseLatency = 0;
        config.extraLatency = {{100, 2}, {1, 5}};
        TargetProbe target(trace, "duplicate-zero-depth-two", config);
        for (const auto &packet : {address(100, 1, 0, 0x100, 2),
                                   address(1, 1, 0, 0x110)}) {
            ASSERT_TRUE(target.aw(packet, 0));
            for (uint16_t beat = 0; beat < packet.request.beatCount; ++beat)
                ASSERT_TRUE(target.w(writeBeat(packet, beat, 0x40), 0));
        }
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(5);
        } catch (...) {
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{100}));
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().writeCommittedBytes, 16u);
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
            EXPECT_EQ(target.state.progress().serviceReady, 2u);
            EXPECT_EQ(target.state.occupancy().bReady, 1u);
            EXPECT_EQ(target.state.occupancy().writeContexts, 1u);
            EXPECT_EQ(target.state.occupancy().writeReservedBeats, 1u);
            EXPECT_EQ(target.state.memory().readByte(0x100), 0x40);
            EXPECT_EQ(target.state.memory().readByte(0x110), 0u);
            finishWriteCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: write targetSeq retired twice");
}

TEST(AxiBehaviorTrace, WriteDelayedBStillDefersDuplicateObservation)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("WriteDelayedBStillDefersDuplicateObservation");
        auto config = targetConfig(false, 16);
        config.bReadyDepth = 1;
        config.writeBaseLatency = 0;
        config.extraLatency = {{100, 2}, {1, 5}};
        config.bEjectionDelay[100] = 10;
        TargetProbe target(trace, "duplicate-delayed-b", config);
        for (const auto &packet : {address(100, 1, 0, 0x100, 2),
                                   address(1, 1, 0, 0x110)}) {
            ASSERT_TRUE(target.aw(packet, 0));
            for (uint16_t beat = 0; beat < packet.request.beatCount; ++beat)
                ASSERT_TRUE(target.w(writeBeat(packet, beat, 0x40), 0));
        }
        target.advance(5);
        EXPECT_EQ(target.state.occupancy().bReady, 1u);
        EXPECT_FALSE(target.state.hasBPacket());
        EXPECT_FALSE(target.b(5));
        target.advance(14);
        EXPECT_FALSE(target.state.hasBPacket());
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
        EXPECT_EQ(target.state.progress().writesCommitted, 1u);
        target.advance(15);
        ASSERT_TRUE(target.state.hasBPacket());
        EXPECT_EQ(target.state.progress().writesCommitted, 1u);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.b(15);
        } catch (...) {
            EXPECT_EQ(target.state.occupancy().bReady, 0u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().writeCommittedBytes, 16u);
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
            EXPECT_EQ(target.state.progress().lastWriteCommitCycle, 5u);
            finishWriteCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: write targetSeq retired twice");
}

TEST(AxiBehaviorTrace, WriteDuplicateOneRechecksFlagAfterBFull)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("WriteDuplicateOneRechecksFlagAfterBFull");
        auto config = targetConfig(false, 16);
        config.bReadyDepth = 2;
        config.writeBaseLatency = 0;
        config.extraLatency = {{500, 2}, {100, 1}, {5, 4}};
        config.bEjectionDelay[500] = 10;
        TargetProbe target(trace, "duplicate-one", config);
        for (const auto &packet : {address(500, 1, 0, 0x100),
                                   address(100, 1, 1, 0x108, 2),
                                   address(5, 1, 1, 0x118, 3)}) {
            ASSERT_TRUE(target.aw(packet, 0));
            for (uint16_t beat = 0; beat < packet.request.beatCount; ++beat)
                ASSERT_TRUE(target.w(writeBeat(packet, beat, 0x40), 0));
        }
        target.advance(5);
        EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{500, 100}));
        EXPECT_EQ(target.first.policyBlocked, (std::vector<uint64_t>{2, 2}));
        EXPECT_EQ(target.first.observerBlocked, (std::vector<uint64_t>{2, 2}));
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
        EXPECT_EQ(target.state.progress().writesCommitted, 2u);
        EXPECT_EQ(target.state.progress().writeCommittedBytes, 24u);
        EXPECT_EQ(target.state.occupancy().bReady, 2u);
        EXPECT_FALSE(target.state.hasBPacket());
        target.advance(5);
        target.advance(14);
        EXPECT_FALSE(target.state.hasBPacket());
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
        target.advance(15);
        ASSERT_TRUE(target.state.hasBPacket());
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.b(15);
        } catch (...) {
            EXPECT_EQ(target.state.occupancy().bReady, 1u);
            EXPECT_EQ(target.state.occupancy().writeContexts, 1u);
            EXPECT_EQ(target.state.occupancy().writeReservedBeats, 3u);
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
            EXPECT_EQ(target.state.progress().writesCommitted, 2u);
            EXPECT_EQ(target.state.progress().writeCommittedBytes, 24u);
            EXPECT_EQ(target.state.memory().readByte(0x118), 0u);
            finishWriteCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: write targetSeq retired twice");
}

TEST(AxiBehaviorTrace, WriteStaleBlocksOtherDomainAndStopsUidPrefix)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("WriteStaleBlocksOtherDomainAndStopsUidPrefix");
        auto config = targetConfig(false, 16);
        config.bReadyDepth = 1;
        config.extraLatency[20] = 3;
        TargetProbe target(trace, "write-stale-prefix", config);
        for (const auto &packet : {address(50, 1, 0, 0x100),
                                   address(20, 1, 0, 0x108)}) {
            ASSERT_TRUE(target.aw(packet, 0));
            ASSERT_TRUE(target.w(writeBeat(packet, 0, 0x50), 0));
        }
        target.advance(2);
        ASSERT_EQ(target.state.progress().writesCommitted, 1u);
        for (auto packet : {address(10, 2, 1, 0x110),
                            address(30, 3, 1, 0x118),
                            address(5, 4, 0, 0x120, 2)}) {
            if (packet.meta.txnUid == 5)
                packet.request.size = 2;
            ASSERT_TRUE(target.aw(packet, 3));
            for (uint16_t beat = 0; beat < packet.request.beatCount; ++beat) {
                auto data = writeBeat(packet, beat, 0x70);
                if (packet.meta.txnUid == 5)
                    data.byteStrobe = 0x80;
                ASSERT_TRUE(target.w(data, 3));
            }
        }
        ASSERT_TRUE(target.ar(address(80, 8, 0, 0x140), 3));
        ASSERT_TRUE(target.b(3));
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(5);
        } catch (...) {
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().writeCommittedBytes, 8u);
            EXPECT_EQ(target.state.progress().readsCommitted, 0u);
            EXPECT_EQ(target.state.progress().serviceReady, 6u);
            EXPECT_EQ(target.state.occupancy().bReady, 0u);
            EXPECT_EQ(target.state.occupancy().rReady, 0u);
            EXPECT_EQ(target.state.occupancy().writeContexts, 4u);
            EXPECT_EQ(target.state.occupancy().writeReservedBeats, 5u);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{50}));
            EXPECT_EQ(target.second.uids, target.first.uids);
            EXPECT_EQ(target.state.memory().readByte(0x100), 0x50);
            EXPECT_EQ(target.state.memory().readByte(0x120), 0u);
            finishWriteCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: write targetSeq retired twice");
}

TEST(AxiBehaviorTrace, WriteStaleWithoutEligibleStillFails)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("WriteStaleWithoutEligibleStillFails");
        auto config = targetConfig(false, 16);
        config.bReadyDepth = 1;
        config.extraLatency[20] = 3;
        TargetProbe target(trace, "write-stale-no-eligible", config);
        for (const auto &packet : {address(50, 1, 0, 0x100),
                                   address(20, 1, 0, 0x108)}) {
            ASSERT_TRUE(target.aw(packet, 0));
            ASSERT_TRUE(target.w(writeBeat(packet, 0, 0x50), 0));
        }
        target.advance(2);
        const auto blocked = address(10, 2, 1, 0x110);
        ASSERT_TRUE(target.aw(blocked, 3));
        ASSERT_TRUE(target.w(writeBeat(blocked, 0, 0x70), 3));
        ASSERT_TRUE(target.b(3));
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(5);
        } catch (...) {
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().writeCommittedBytes, 8u);
            EXPECT_EQ(target.state.progress().serviceReady, 3u);
            EXPECT_EQ(target.state.occupancy().writeContexts, 2u);
            EXPECT_EQ(target.state.occupancy().bReady, 0u);
            finishWriteCommitPanic(trace, target.state);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: write targetSeq retired twice");
}

TEST(AxiBehaviorTrace, WriteMalformedBeatValidationWaitsForCapacity)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("WriteMalformedBeatValidationWaitsForCapacity");
        auto config = targetConfig(false, 16);
        config.bReadyDepth = 1;
        config.writeBaseLatency = 0;
        config.extraLatency = {{5, 1}, {10, 1}};
        TargetProbe target(trace, "write-malformed-capacity", config);
        const auto blocker = address(900, 9, 0, 0x100);
        ASSERT_TRUE(target.aw(blocker, 0));
        ASSERT_TRUE(target.w(writeBeat(blocker, 0, 0x50), 0));
        auto malformed = address(5, 1, 0, 0x108);
        malformed.request.size = 2;
        auto beat = writeBeat(malformed, 0, 0x60);
        beat.byteStrobe = 0x80;
        ASSERT_TRUE(target.aw(malformed, 0));
        ASSERT_TRUE(target.w(beat, 0));
        const auto blocked = address(10, 2, 1, 0x110);
        ASSERT_TRUE(target.aw(blocked, 0));
        ASSERT_TRUE(target.w(writeBeat(blocked, 0, 0x70), 0));
        target.advance(1);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
        EXPECT_EQ(target.state.progress().writesCommitted, 1u);
        target.advance(1);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.b(1);
        } catch (...) {
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().writeCommittedBytes, 8u);
            EXPECT_EQ(target.state.occupancy().bReady, 0u);
            EXPECT_EQ(target.state.occupancy().writeContexts, 2u);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900}));
            EXPECT_EQ(target.state.memory().readByte(0x108), 0u);
            finishWriteCommitPanic(trace, target.state,
                "AXI_PROTOCOL: AXI WSTRB selects a lane outside the transfer");
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: AXI WSTRB selects a lane outside the transfer");
}

TEST(AxiBehaviorTrace, WriteReadyTimeRankAndUidPreserveMemoryOrder)
{
    BehaviorTrace trace("WriteReadyTimeRankAndUidPreserveMemoryOrder");
    const uint64_t maximum = std::numeric_limits<uint64_t>::max();
    auto config = targetConfig(false, 16);
    config.writeBaseLatency = 0;
    config.extraLatency = {{100, 1}, {1, 3}, {0, 2}, {maximum, 2},
                           {7, 2}, {9, 2}, {5, 2}};
    config.writeCommitTieBreakRanks = {{1, 0}, {0, 5}, {maximum, 5},
        {9, std::numeric_limits<uint32_t>::max()}, {5, 0}};
    TargetProbe target(trace, "write-rank", config);
    WriteMemoryObserver memory(trace, target.state);
    target.state.registerWriteCommitObserver(&memory);
    const std::array<uint64_t, 7> uids{1, maximum, 9, 7, 0, 5, 100};
    for (uint32_t id = 0; id < uids.size(); ++id) {
        auto packet = address(uids[id], id, 0, 0x100);
        packet.meta.acceptedTick = maximum - id;
        ASSERT_TRUE(target.aw(packet, 0));
        ASSERT_TRUE(target.w(writeBeat(packet, 0, uint8_t(uids[id])), 0));
    }
    target.advance(5);
    const std::vector<uint64_t> order{100, 5, 0, maximum, 7, 9, 1};
    EXPECT_EQ(target.first.uids, order);
    EXPECT_EQ(target.second.uids, order);
    EXPECT_EQ(memory.values, (std::vector<uint8_t>{100, 5, 0, 255, 7, 9, 1}));
    EXPECT_EQ(target.state.memory().readByte(0x100), 1u);
    EXPECT_EQ(target.state.progress().writesCommitted, 7u);
    EXPECT_EQ(target.state.progress().writeCommittedBytes, 56u);
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
    EXPECT_EQ(target.state.progress().lastWriteCommitCycle, 5u);
    for (uint64_t uid : order) {
        const auto response = target.b(5);
        ASSERT_TRUE(response);
        EXPECT_EQ(response->meta.txnUid, uid);
    }
    target.drain(6);
}

TEST(AxiBehaviorTrace, WritePolicyFaultReplayAndObserverMemory)
{
    BehaviorTrace trace("WritePolicyFaultReplayAndObserverMemory");
    struct Scenario
    {
        AxiResp transport;
        AxiWritePreCommitPolicy::Decision policy;
        bool postFault;
        bool writesMemory;
        AxiResp observerResponse;
        AxiResp bResponse;
    };
    const std::array<Scenario, 6> scenarios{{
        {AxiResp::Okay, {AxiResp::Okay, true}, false, true,
            AxiResp::Okay, AxiResp::Okay},
        {AxiResp::Okay, {AxiResp::Okay, false}, true, true,
            AxiResp::Okay, AxiResp::SlvErr},
        {AxiResp::SlvErr, {AxiResp::Okay, true}, true, false,
            AxiResp::SlvErr, AxiResp::SlvErr},
        {AxiResp::Okay, {AxiResp::SlvErr, true}, true, false,
            AxiResp::SlvErr, AxiResp::SlvErr},
        {AxiResp::Okay, {AxiResp::Okay, false}, false, false,
            AxiResp::Okay, AxiResp::Okay},
        {AxiResp::Okay, {AxiResp::Okay, true}, true, true,
            AxiResp::Okay, AxiResp::SlvErr}
    }};
    for (bool synthetic : {false, true}) {
        for (size_t index = 0; index < scenarios.size(); ++index) {
            const auto &scenario = scenarios[index];
            auto config = targetConfig(synthetic, 16);
            config.transactionFaults[10] = scenario.transport;
            if (scenario.postFault)
                config.transactionPostCommitFaults[10] = AxiResp::SlvErr;
            config.writeCommitObserverReplays[10] = 2;
            TargetProbe target(trace, "write-policy." +
                std::to_string(synthetic) + "." + std::to_string(index), config);
            target.first.policies[0x100] = scenario.policy;
            WriteMemoryObserver memory(trace, target.state);
            target.state.registerWriteCommitObserver(&memory);
            target.memory(0x100, 0x11, 0);
            const auto packet = address(10, 1, 0, 0x100, 2);
            ASSERT_TRUE(target.aw(packet, 0));
            for (uint16_t beat = 0; beat < 2; ++beat) {
                auto data = writeBeat(packet, beat, 0x60);
                data.byteStrobe = 0x55;
                ASSERT_TRUE(target.w(data, 0));
            }
            target.advance(10);
            const auto expected = scenario.writesMemory ? 0x60 : 0x11;
            EXPECT_EQ(memory.values, (std::vector<uint8_t>(3, expected)));
            EXPECT_EQ(memory.responses,
                (std::vector<AxiResp>(3, scenario.observerResponse)));
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{10, 10, 10}));
            EXPECT_EQ(target.second.uids, target.first.uids);
            EXPECT_EQ(target.first.policyBlocked, (std::vector<uint64_t>{0}));
            EXPECT_EQ(target.state.progress().writeCommittedBytes,
                scenario.writesMemory ? 8u : 0u);
            EXPECT_EQ(target.state.progress().lastWriteCommitCycle,
                scenario.writesMemory ? 10u : 0u);
            EXPECT_EQ(target.state.memory().readByte(0x100), expected);
            EXPECT_EQ(target.state.memory().readByte(0x101), 0u);
            EXPECT_EQ(target.state.memory().readByte(0x108),
                scenario.writesMemory ? 0x6d : 0u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.completedWrites(), 1u);
            const auto response = target.b(10);
            ASSERT_TRUE(response);
            EXPECT_EQ(response->resp, scenario.bResponse);
            target.drain(11);
        }
    }
}

TEST(AxiBehaviorTrace, WriteOrphanAcceptedTickRetainsModeSpecificAdmission)
{
    BehaviorTrace trace("WriteOrphanAcceptedTickRetainsModeSpecificAdmission");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.writeServiceDepth = 1;
        config.syntheticHbmQueueDepth = 1;
        TargetProbe target(trace, "write-orphan." + std::to_string(synthetic), config);
        auto blocker = address(900, 9, 0, 0x100);
        blocker.meta.acceptedTick = 0;
        ASSERT_TRUE(target.aw(blocker, 0));
        ASSERT_TRUE(target.w(writeBeat(blocker, 0, 0x10), 0));
        auto orphan = address(20, 1, 0, 0x108, 2);
        orphan.meta.acceptedTick = 0;
        ASSERT_TRUE(target.w(writeBeat(orphan, 1, 0x20), 0));
        ASSERT_TRUE(target.w(writeBeat(orphan, 0, 0x20), 0));
        EXPECT_EQ(target.state.occupancy().orphanTransactions, 1u);
        orphan.meta.acceptedTick = 100;
        ASSERT_TRUE(target.aw(orphan, 0));
        auto competitor = address(30, 2, 0, 0x118);
        competitor.meta.acceptedTick = 1;
        ASSERT_TRUE(target.aw(competitor, 0));
        ASSERT_TRUE(target.w(writeBeat(competitor, 0, 0x30), 0));
        EXPECT_EQ(target.state.occupancy().orphanTransactions, 0u);
        target.advance(3);
        EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900}));
        target.advance(7);
        EXPECT_EQ(target.first.uids,
            synthetic ? (std::vector<uint64_t>{900, 20}) :
                        (std::vector<uint64_t>{900, 30}));
        target.advance(11);
        const auto order = synthetic ? std::vector<uint64_t>{900, 20, 30} :
                                       std::vector<uint64_t>{900, 30, 20};
        EXPECT_EQ(target.first.uids, order);
        EXPECT_EQ(target.state.progress().writeCommittedBytes, 32u);
        for (uint64_t uid : order) {
            const auto response = target.b(11);
            ASSERT_TRUE(response);
            EXPECT_EQ(response->meta.txnUid, uid);
            if (uid == 20) {
                EXPECT_EQ(response->meta.acceptedTick, 100u);
            }
        }
        target.drain(12);
    }
}

TEST(AxiBehaviorTrace, WriteUidReuseWhileOldBStillQueued)
{
    BehaviorTrace trace("WriteUidReuseWhileOldBStillQueued");
    const uint64_t maximum = std::numeric_limits<uint64_t>::max();
    auto config = targetConfig(false, 16);
    config.bReadyDepth = 4;
    config.writeBaseLatency = 0;
    config.extraLatency = {{0, 2}, {maximum, 1}, {9, 1}};
    TargetProbe target(trace, "write-uid-reuse", config);
    auto head = address(0, 1, 0, 0x100);
    head.meta.responseSeq = 70;
    head.meta.acceptedTick = 1000;
    head.meta.qos = head.request.qos = 3;
    auto younger = address(maximum, 1, 1, 0x108);
    younger.meta.responseSeq = 2;
    younger.meta.acceptedTick = 0;
    younger.meta.qos = younger.request.qos = 7;
    for (const auto &packet : {head, younger, address(9, 2, maximum, 0x118)}) {
        ASSERT_TRUE(target.aw(packet, 0));
        ASSERT_TRUE(target.w(writeBeat(packet, 0, 0x40), 0));
    }
    target.advance(5);
    ASSERT_EQ(target.state.progress().writesCommitted, 2u);
    ASSERT_EQ(target.state.occupancy().bReady, 2u);
    auto reused = head;
    reused.meta.targetSeq = 2;
    reused.meta.responseSeq = 99;
    reused.meta.acceptedTick = 2000;
    reused.request.address = 0x110;
    ASSERT_TRUE(target.aw(reused, 5));
    ASSERT_TRUE(target.w(writeBeat(reused, 0, 0x50), 5));
    target.advance(7);
    EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{0, maximum, 0}));
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
    EXPECT_EQ(target.state.progress().writesCommitted, 3u);
    EXPECT_EQ(target.state.progress().writeCommittedBytes, 24u);
    EXPECT_EQ(target.state.occupancy().bReady, 3u);
    for (const auto &expected : {younger, head, reused}) {
        const auto response = target.b(7);
        ASSERT_TRUE(response);
        EXPECT_EQ(response->meta.txnUid, expected.meta.txnUid);
        EXPECT_EQ(response->meta.targetSeq, expected.meta.targetSeq);
        EXPECT_EQ(response->meta.responseSeq, expected.meta.responseSeq);
        EXPECT_EQ(response->meta.acceptedTick, expected.meta.acceptedTick);
        EXPECT_EQ(response->meta.qos, expected.meta.qos);
    }
    EXPECT_EQ(target.state.progress().qosTransactions[3], 2u);
    EXPECT_EQ(target.state.progress().qosTransactions[7], 1u);
    EXPECT_EQ(target.state.occupancy().writeContexts, 1u);
    EXPECT_EQ(target.state.occupancy().writeReservedBeats, 1u);
    EXPECT_TRUE(target.state.canAcceptAw(address(91, 3, 0, 0x200, 63)));
    EXPECT_FALSE(target.state.canAcceptAw(address(92, 3, 0, 0x200, 64)));
    EXPECT_FALSE(target.state.hasBPacket());
    target.advance(7);
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
}

TEST(AxiBehaviorTrace, WriteCopiesMovesAndIdleCheckpointKeepOwners)
{
    BehaviorTrace trace("WriteCopiesMovesAndIdleCheckpointKeepOwners");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.extraLatency = {{10, 20}, {30, 40}};
        AxiTargetState original(config);
        for (const auto &packet : {address(10, 1, 0, 0x100),
                                   address(20, 1, 1, 0x108),
                                   address(30, 2, 0, 0x110)}) {
            trace.operation("copy-setup.acceptAw", 0);
            original.acceptAw(packet, 0);
            trace.target("copy-original", original);
            trace.operation("copy-setup.acceptW", 0);
            original.acceptW(writeBeat(packet, 0, 0x60), 0);
            trace.target("copy-original", original);
        }
        trace.operation("copy-setup.advance", 5);
        original.advance(5);
        trace.target("copy-original", original);
        ASSERT_EQ(original.progress().sameIdReadyBlocked, 1u);
        ASSERT_EQ(original.occupancy().writeServices, 2u);
        AxiTargetState copied = original;
        AxiTargetState moved = std::move(copied);
        CommitRecorder first(trace, original, "copy-original");
        CommitRecorder second(trace, moved, "copy-moved");
        original.registerWriteCommitObserver(&first);
        moved.registerWriteCommitObserver(&second);
        original.setPreCommitPolicy(&first);
        moved.setPreCommitPolicy(&second);
        original.memory().writeByte(0x280, 0x11);
        moved.memory().writeByte(0x280, 0x77);
        for (AxiTargetState *state : {&original, &moved}) {
            const std::string owner = state == &original ? "original" : "moved";
            trace.operation("copy.advance." + owner, 50);
            state->advance(50);
            trace.target(owner, *state);
            EXPECT_EQ(state->progress().writesCommitted, 3u);
            EXPECT_EQ(state->progress().writeCommittedBytes, 24u);
            EXPECT_EQ(state->progress().sameIdReadyBlocked, 1u);
            EXPECT_EQ(state->occupancy().writeContexts, 0u);
            EXPECT_EQ(state->occupancy().writeReservedBeats, 0u);
            EXPECT_EQ(state->memory().readByte(0x280),
                state == &original ? 0x11 : 0x77);
            for (uint64_t uid : {10u, 20u, 30u}) {
                ASSERT_TRUE(state->hasBPacket());
                const auto response = state->frontBPacket();
                EXPECT_EQ(response.meta.txnUid, uid);
                TraceRow row;
                row.field("owner", owner);
                append(row, response.meta);
                row.field("resp", response.resp);
                trace.record("outputB", row);
                state->popBPacket();
                trace.target(owner, *state);
            }
            EXPECT_FALSE(state->hasBPacket());
            EXPECT_EQ(state->completedWrites(), 3u);
            EXPECT_TRUE(state->canAcceptAw(address(40, 1, 2, 0x200, 64)));
        }
        EXPECT_EQ(first.uids, (std::vector<uint64_t>{10, 20, 30}));
        EXPECT_EQ(second.uids, first.uids);
        std::stringstream checkpoint;
        {
            boost::archive::binary_oarchive archive(checkpoint);
            archive << original;
        }
        const std::string bytes = checkpoint.str();
        TraceRow row;
        row.field("synthetic", synthetic)
            .field("bytes", bytesHex(std::vector<uint8_t>(bytes.begin(), bytes.end())));
        trace.record("checkpoint", row);
        TargetProbe restored(trace, "write-restored." + std::to_string(synthetic), config);
        {
            boost::archive::binary_iarchive archive(checkpoint);
            archive >> restored.state;
        }
        std::stringstream roundtrip;
        {
            boost::archive::binary_oarchive archive(roundtrip);
            archive << restored.state;
        }
        EXPECT_EQ(roundtrip.str(), bytes);
        EXPECT_EQ(restored.state.memory().readByte(0x280), 0x11);
        const auto next = address(40, 1, 2, 0x100);
        ASSERT_TRUE(restored.aw(next, 60));
        ASSERT_TRUE(restored.w(writeBeat(next, 0, 0x70), 60));
        restored.advance(65);
        EXPECT_EQ(restored.state.progress().writesCommitted, 4u);
        EXPECT_EQ(restored.state.memory().readByte(0x100), 0x70);
        ASSERT_TRUE(restored.b(65));
        restored.drain(66);
    }
}

TEST(AxiBehaviorTrace, WriteGateWrapReobservesMaximumOnlyAfterBPop)
{
    BehaviorTrace trace("WriteGateWrapReobservesMaximumOnlyAfterBPop");
    const uint64_t maximum = std::numeric_limits<uint64_t>::max();
    for (bool previouslyBlocked : {false, true}) {
        auto config = targetConfig(false, 16);
        config.bReadyDepth = 1;
        config.writeBaseLatency = 0;
        config.extraLatency[30] = 1;
        if (previouslyBlocked)
            config.extraLatency[5] = 1;
        else
            config.extraLatency[10] = config.extraLatency[20] = 1;
        const std::array<uint64_t, 2> values{
            maximum - uint64_t(previouslyBlocked), previouslyBlocked ? 4u : 3u};
        ASSERT_EQ(values[0] + values[1], 2u);
        TargetProbe seed(trace, "write-wrap-seed." + std::to_string(previouslyBlocked), config);
        for (const auto &packet : {address(100, 1, 0, 0x100),
                                   address(101, 2, 0, 0x108)}) {
            ASSERT_TRUE(seed.aw(packet, 0));
            ASSERT_TRUE(seed.w(writeBeat(packet, 0, 0x20), 0));
        }
        for (uint64_t uid : {100u, 101u}) {
            const auto response = seed.b(0);
            ASSERT_TRUE(response);
            ASSERT_EQ(response->meta.txnUid, uid);
        }
        ASSERT_EQ(seed.state.completedWrites(), 2u);
        ASSERT_EQ(seed.state.progress().writesCommitted, 2u);
        ASSERT_EQ(seed.state.progress().writeCommittedBytes, 16u);
        ASSERT_EQ(seed.state.occupancy().writeContexts, 0u);
        ASSERT_EQ(seed.state.occupancy().bReady, 0u);
        std::stringstream checkpoint;
        {
            boost::archive::binary_oarchive archive(checkpoint);
            seed.state.serialize(archive, 0);
        }
        TraceRow history;
        const auto bytes = checkpoint.str();
        history.field("firstGate", values[0]).field("secondGate", values[1])
            .field("bytes", bytesHex(std::vector<uint8_t>(bytes.begin(), bytes.end())));
        trace.record("gateSeed", history);
        TargetProbe target(trace, "write-wrap-restored." +
            std::to_string(previouslyBlocked), config);
        {
            boost::archive::binary_iarchive archive(checkpoint);
            GateSeedArchive wrapper(archive, values);
            target.state.serialize(wrapper, 0);
            ASSERT_EQ(wrapper.matchedMaps, 1u);
        }
        for (const auto &packet : {address(10, 1, maximum, 0x100),
                                   address(20, 1, maximum, 0x100)}) {
            ASSERT_TRUE(target.aw(packet, 0));
            ASSERT_TRUE(target.w(writeBeat(packet, 0, 0x40), 0));
        }
        if (previouslyBlocked) {
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 2u);
            const auto beforeWrap = address(5, 1, maximum - 1, 0x100);
            ASSERT_TRUE(target.aw(beforeWrap, 0));
            ASSERT_TRUE(target.w(writeBeat(beforeWrap, 0, 0x50), 0));
        }
        const auto independent = address(30, 2, values[1], 0x108);
        ASSERT_TRUE(target.aw(independent, 0));
        ASSERT_TRUE(target.w(writeBeat(independent, 0, 0x60), 0));
        target.advance(1);
        EXPECT_EQ(target.state.progress().writesCommitted, 3u);
        EXPECT_EQ(target.state.occupancy().bReady, 1u);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked,
            previouslyBlocked ? 2u : 0u);
        target.advance(1);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked,
            previouslyBlocked ? 2u : 0u);
        const auto order = previouslyBlocked ? std::vector<uint64_t>{5, 10, 30} :
                                              std::vector<uint64_t>{10, 30};
        for (uint64_t uid : order) {
            const auto response = target.b(1);
            ASSERT_TRUE(response);
            EXPECT_EQ(response->meta.txnUid, uid);
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked,
                previouslyBlocked ? 2u : 1u);
        }
        const uint64_t commits = previouslyBlocked ? 5 : 4;
        EXPECT_EQ(target.state.progress().writesCommitted, commits);
        EXPECT_EQ(target.state.completedWrites(), commits);
        EXPECT_EQ(target.state.progress().architecturalCommits, commits);
        EXPECT_EQ(target.state.progress().writeCommittedBytes, commits * 8);
        EXPECT_EQ(target.state.progress().serviceReady, commits + 1);
        EXPECT_EQ(target.state.occupancy().writeContexts, 1u);
        EXPECT_EQ(target.state.occupancy().writeReservedBeats, 1u);
        EXPECT_EQ(target.state.occupancy().writeServices, 0u);
        EXPECT_EQ(target.state.occupancy().bReady, 0u);
        EXPECT_TRUE(target.state.canAcceptAw(address(91, 3, 0, 0x200, 63)));
        EXPECT_FALSE(target.state.canAcceptAw(address(92, 3, 0, 0x200, 64)));
        EXPECT_FALSE(target.state.hasBPacket());
        target.advance(1);
        EXPECT_EQ(target.state.progress().writesCommitted, commits);
        EXPECT_EQ(target.state.progress().sameIdReadyBlocked,
            previouslyBlocked ? 2u : 1u);
    }
}

}

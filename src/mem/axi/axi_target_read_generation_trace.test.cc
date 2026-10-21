#include "mem/axi/axi_behavior_trace_support.hh"

namespace gem5::axi::behavior
{

TEST(AxiBehaviorTrace, ReadGenerationLateUidPreemptsAndRoundRobins)
{
    BehaviorTrace trace("ReadGenerationLateUidPreemptsAndRoundRobins");
    auto config = targetConfig(false, 16);
    config.rReadyDepth = 1;
    config.readBaseLatency = 0;
    TargetProbe target(trace, "generation-late", config);
    const auto old = address(90, 1, 0, 0x100, 3, {1, 0});
    const auto other = address(70, 2, 0, 0x120, 3, {2, 0});
    const auto late = address(0, 3, 0, 0x140, 2, {1, 0});
    ASSERT_TRUE(target.ar(old, 0));
    ASSERT_TRUE(target.ar(other, 0));
    const auto first = target.r(0);
    ASSERT_TRUE(first);
    EXPECT_EQ(first->meta.txnUid, old.meta.txnUid);
    EXPECT_EQ(first->beatIndex, 0u);
    ASSERT_TRUE(target.ar(late, 0));
    target.advance(0);
    EXPECT_EQ(target.state.progress().readsCommitted, 3u);
    EXPECT_EQ(target.state.occupancy().rReady, 1u);
    const std::vector<std::pair<AxiAddressPacket, uint16_t>> expected{
        {other, 0}, {late, 0}, {other, 1}, {late, 1},
        {other, 2}, {old, 1}, {old, 2}};
    for (const auto &[ar, index] : expected) {
        const auto packet = target.r(0);
        ASSERT_TRUE(packet);
        EXPECT_EQ(packet->meta.txnUid, ar.meta.txnUid);
        EXPECT_EQ(packet->meta.srcNode, ar.meta.srcNode);
        EXPECT_EQ(packet->meta.srcPort, ar.meta.srcPort);
        EXPECT_EQ(packet->beatIndex, index);
        EXPECT_EQ(packet->beatCount, ar.request.beatCount);
        EXPECT_EQ(packet->last, index + 1 == ar.request.beatCount);
    }
    EXPECT_FALSE(target.state.hasRPacket());
    EXPECT_EQ(target.state.completedReads(), 3u);
    EXPECT_EQ(target.state.occupancy().readContexts, 0u);
    EXPECT_EQ(target.state.occupancy().readReservedBeats, 0u);
}

TEST(AxiBehaviorTrace, ReadGenerationFrozenCopiesAndMovesDrainIndependently)
{
    BehaviorTrace trace("ReadGenerationFrozenCopiesAndMovesDrainIndependently");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.rReadyDepth = 1;
        TargetProbe target(trace, "generation-original." +
                           std::to_string(synthetic), config);
        for (uint64_t index = 0; index < 3; ++index)
            target.memory(0x100 + index * 8, uint8_t(0x31 + index), 0);
        for (uint64_t index = 0; index < 2; ++index)
            target.memory(0x120 + index * 8, uint8_t(0x41 + index), 0);
        const auto blocker = address(800, 9, 0, 0x180, 1, {1, 1});
        const auto a = address(90, 1, 0, 0x100, 3, {1, 0});
        const auto b = address(70, 2, 0, 0x120, 2, {2, 0});
        ASSERT_TRUE(target.ar(blocker, 0));
        target.advance(20);
        ASSERT_TRUE(target.ar(a, 20));
        ASSERT_TRUE(target.ar(b, 20));
        target.advance(40);
        ASSERT_EQ(target.state.progress().readsCommitted, 3u);
        ASSERT_EQ(target.state.occupancy().readContexts, 2u);
        ASSERT_EQ(target.state.occupancy().readReservedBeats, 5u);
        const auto first = target.r(40);
        ASSERT_TRUE(first);
        ASSERT_EQ(first->meta.txnUid, blocker.meta.txnUid);
        ASSERT_EQ(target.state.frontRPacket().meta.txnUid, b.meta.txnUid);
        ASSERT_EQ(target.state.frontRPacket().beatIndex, 0u);
        trace.operation("copyAndMoveFrozenGeneration", 40);
        AxiTargetState copied = target.state;
        AxiTargetState moved = std::move(copied);
        trace.target("generation-moved", moved);
        target.memory(0x100, 0xa1, 40);
        trace.operation("writeMemory.generation-moved", 40);
        moved.memory().writeByte(0x100, 0xc1);
        trace.target("generation-moved", moved);
        EXPECT_EQ(target.state.memory().readByte(0x100), 0xa1);
        EXPECT_EQ(moved.memory().readByte(0x100), 0xc1);
        const std::vector<std::pair<AxiAddressPacket, uint16_t>> expected{
            {b, 0}, {a, 0}, {b, 1}, {a, 1}, {a, 2}};
        for (AxiTargetState *state : {&target.state, &moved}) {
            const std::string owner = state == &target.state ?
                "generation-original" : "generation-moved";
            for (const auto &[ar, index] : expected) {
                trace.operation("popR." + owner, 40);
                ASSERT_TRUE(state->hasRPacket());
                const auto packet = state->frontRPacket();
                EXPECT_EQ(packet.meta.txnUid, ar.meta.txnUid);
                EXPECT_EQ(packet.meta.srcNode, ar.meta.srcNode);
                EXPECT_EQ(packet.meta.srcPort, ar.meta.srcPort);
                EXPECT_EQ(packet.beatIndex, index);
                EXPECT_EQ(packet.last, index + 1 == ar.request.beatCount);
                std::vector<uint8_t> expected_data(8, 0);
                expected_data.front() = uint8_t(
                    (ar.request.address == 0x100 ? 0x31 : 0x41) + index);
                EXPECT_EQ(packet.functionalData, expected_data);
                EXPECT_EQ(packet.payloadDigest, payloadDigest(expected_data));
                TraceRow row;
                row.field("owner", owner);
                append(row, packet);
                trace.record("outputR", row);
                state->popRPacket();
                trace.target(owner, *state);
            }
            EXPECT_FALSE(state->hasRPacket());
            EXPECT_EQ(state->completedReads(), 3u);
            EXPECT_EQ(state->occupancy().readContexts, 0u);
            EXPECT_EQ(state->occupancy().readReservedBeats, 0u);
        }
        EXPECT_TRUE(target.first.uids.empty());
        EXPECT_TRUE(target.second.uids.empty());
    }
}

TEST(AxiBehaviorTrace, ReadGenerationCursorSurvivesChangesAndCheckpoint)
{
    BehaviorTrace trace("ReadGenerationCursorSurvivesChangesAndCheckpoint");
    auto config = targetConfig(false, 16);
    config.rReadyDepth = 1;
    config.capacity = {40, 320, 40, 320};
    config.sourceQuotas[{0, 9}] = {8, 64, 8, 64};
    config.sourceQuotas[{3, 0}] = {8, 64, 8, 64};
    TargetProbe target(trace, "generation-before-checkpoint", config);
    const auto initial = address(500, 1, 0, 0x100, 1, {1, 1});
    ASSERT_TRUE(target.ar(initial, 0));
    target.advance(2);
    const auto initial_packet = target.r(2);
    ASSERT_TRUE(initial_packet);
    ASSERT_EQ(initial_packet->meta.txnUid, initial.meta.txnUid);
    ASSERT_FALSE(target.state.hasRPacket());
    target.advance(2);
    trace.operation("checkpoint.generation-idle-cursor", 2);
    std::stringstream checkpoint;
    {
        boost::archive::binary_oarchive archive(checkpoint);
        archive << target.state;
    }
    const std::string bytes = checkpoint.str();
    TraceRow checkpoint_row;
    checkpoint_row.field("bytes", bytesHex(
        std::vector<uint8_t>(bytes.begin(), bytes.end())));
    trace.record("checkpoint", checkpoint_row);
    TargetProbe restored(trace, "generation-restored", config);
    trace.operation("restore.generation-idle-cursor", 2);
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
    const auto lower = address(91, 2, 0, 0x120, 1, {1, 0});
    const auto higher = address(20, 3, 0, 0x140, 1, {2, 0});
    ASSERT_TRUE(restored.ar(lower, 3));
    ASSERT_TRUE(restored.ar(higher, 3));
    restored.advance(5);
    const auto higher_packet = restored.r(5);
    ASSERT_TRUE(higher_packet);
    EXPECT_EQ(higher_packet->meta.txnUid, higher.meta.txnUid);
    const auto returning = address(80, 4, 0, 0x160, 1, {1, 1});
    const auto left = address(2, 5, 0, 0x180, 1, {0, 9});
    const auto right = address(3, 6, 0, 0x1a0, 1, {3, 0});
    ASSERT_TRUE(restored.ar(returning, 5));
    ASSERT_TRUE(restored.ar(left, 5));
    ASSERT_TRUE(restored.ar(right, 5));
    restored.advance(7);
    for (const auto &ar : {lower, returning, right, left}) {
        const auto packet = restored.r(7);
        ASSERT_TRUE(packet);
        EXPECT_EQ(packet->meta.txnUid, ar.meta.txnUid);
        EXPECT_EQ(packet->meta.srcNode, ar.meta.srcNode);
        EXPECT_EQ(packet->meta.srcPort, ar.meta.srcPort);
        EXPECT_EQ(packet->beatIndex, 0u);
        EXPECT_TRUE(packet->last);
    }
    EXPECT_FALSE(restored.state.hasRPacket());
    EXPECT_EQ(restored.state.completedReads(), 6u);
    EXPECT_EQ(restored.state.occupancy().readContexts, 0u);
}

TEST(AxiBehaviorTrace, ReadGenerationRlastEnqueueReleasesQuotaBeforeUidReuse)
{
    BehaviorTrace trace("ReadGenerationRlastEnqueueReleasesQuotaBeforeUidReuse");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.rReadyDepth = 1;
        config.readBaseLatency = 0;
        config.capacity.readContexts = 1;
        config.capacity.readResponseBeats = 3;
        config.sourceQuotas.clear();
        config.sourceQuotas[{1, 0}] = {8, 64, 1, 3};
        TargetProbe target(trace, "generation-reuse." +
                           std::to_string(synthetic), config);
        for (uint64_t index = 0; index < 3; ++index)
            target.memory(0x100 + index * 8, uint8_t(0x31 + index), 0);
        const auto old = address(77, 1, 0, 0x100, 3, {1, 0});
        const auto blocked = address(78, 9, 0, 0x200, 1, {1, 0});
        const auto reused = address(77, 1, 1, 0x100, 3, {1, 0});
        const uint64_t ready = synthetic ? 10 : 0;
        ASSERT_TRUE(target.ar(old, 0));
        target.advance(ready);
        ASSERT_EQ(target.state.occupancy().readContexts, 1u);
        ASSERT_EQ(target.state.occupancy().readReservedBeats, 3u);
        EXPECT_EQ(target.state.completedReads(), 0u);
        EXPECT_FALSE(target.ar(blocked, ready));
        const auto beat0 = target.r(ready);
        ASSERT_TRUE(beat0);
        EXPECT_EQ(beat0->beatIndex, 0u);
        EXPECT_EQ(target.state.occupancy().readContexts, 1u);
        EXPECT_EQ(target.state.occupancy().readReservedBeats, 3u);
        EXPECT_EQ(target.state.completedReads(), 0u);
        EXPECT_FALSE(target.ar(blocked, ready));
        const auto beat1 = target.r(ready);
        ASSERT_TRUE(beat1);
        EXPECT_EQ(beat1->beatIndex, 1u);
        ASSERT_EQ(target.state.occupancy().rReady, 1u);
        ASSERT_EQ(target.state.frontRPacket().meta.txnUid, old.meta.txnUid);
        ASSERT_EQ(target.state.frontRPacket().beatIndex, 2u);
        ASSERT_TRUE(target.state.frontRPacket().last);
        EXPECT_EQ(target.state.occupancy().readContexts, 0u);
        EXPECT_EQ(target.state.occupancy().readReservedBeats, 0u);
        EXPECT_EQ(target.state.completedReads(), 1u);
        for (uint64_t index = 0; index < 3; ++index)
            target.memory(0x100 + index * 8, uint8_t(0x71 + index), ready);
        ASSERT_TRUE(target.ar(reused, ready));
        const uint64_t reused_ready = ready + (synthetic ? 10 : 0);
        target.advance(reused_ready);
        EXPECT_EQ(target.state.progress().readsCommitted, 2u);
        EXPECT_EQ(target.state.completedReads(), 1u);
        EXPECT_EQ(target.state.occupancy().readContexts, 1u);
        EXPECT_EQ(target.state.occupancy().readReservedBeats, 3u);
        EXPECT_FALSE(target.ar(blocked, reused_ready));
        const auto old_last = target.r(reused_ready);
        ASSERT_TRUE(old_last);
        EXPECT_EQ(old_last->meta.targetSeq, 0u);
        EXPECT_EQ(old_last->beatIndex, 2u);
        EXPECT_EQ(old_last->functionalData.front(), 0x33);
        for (uint16_t index = 0; index < 3; ++index) {
            const auto packet = target.r(reused_ready);
            ASSERT_TRUE(packet);
            EXPECT_EQ(packet->meta.txnUid, reused.meta.txnUid);
            EXPECT_EQ(packet->meta.targetSeq, 1u);
            EXPECT_EQ(packet->meta.srcNode, reused.meta.srcNode);
            EXPECT_EQ(packet->meta.srcPort, reused.meta.srcPort);
            EXPECT_EQ(packet->beatIndex, index);
            EXPECT_EQ(packet->last, index == 2);
            std::vector<uint8_t> expected_data(8, 0);
            expected_data.front() = uint8_t(0x71 + index);
            EXPECT_EQ(packet->functionalData, expected_data);
            EXPECT_EQ(packet->payloadDigest, payloadDigest(expected_data));
            EXPECT_EQ(target.state.completedReads(), index == 0 ? 1u : 2u);
            EXPECT_EQ(target.state.occupancy().readContexts, index == 0 ? 1u : 0u);
            EXPECT_EQ(target.state.occupancy().readReservedBeats,
                      index == 0 ? 3u : 0u);
        }
        EXPECT_FALSE(target.state.hasRPacket());
        EXPECT_EQ(target.state.progress().readCommittedBytes, 48u);
        EXPECT_TRUE(target.state.canAcceptAr(address(77, 1, 2, 0x100)));
    }
}

TEST(AxiBehaviorTrace, ReadGenerationZeroAndMaximumKeysRemainCandidates)
{
    BehaviorTrace trace("ReadGenerationZeroAndMaximumKeysRemainCandidates");
    const uint64_t maximum = std::numeric_limits<uint64_t>::max();
    const AxiEndpointKey maximum_source{
        std::numeric_limits<uint32_t>::max(),
        std::numeric_limits<uint16_t>::max()};
    for (uint64_t now : {uint64_t{0}, maximum}) {
        auto config = targetConfig(false, 16);
        config.rReadyDepth = 1;
        config.readBaseLatency = 0;
        config.capacity = {40, 320, 40, 320};
        config.sourceQuotas[{0, 0}] = {8, 64, 8, 64};
        config.sourceQuotas[maximum_source] = {8, 64, 8, 64};
        TargetProbe target(trace, "generation-extreme." +
                           std::to_string(now), config);
        const auto blocker = address(300, 1, 0, 0x100, 1, {1, 1});
        const auto lower = address(17, 2, 0, 0x120, 1, {0, 0});
        const auto high = address(maximum, 3, 0, 0x140, 2, maximum_source);
        const auto zero = address(0, 4, 0, 0x160, 1, maximum_source);
        ASSERT_TRUE(target.ar(blocker, 0));
        ASSERT_TRUE(target.ar(lower, now));
        ASSERT_TRUE(target.ar(high, now));
        ASSERT_TRUE(target.ar(zero, now));
        target.advance(now);
        const std::vector<std::pair<AxiAddressPacket, uint16_t>> expected{
            {blocker, 0}, {zero, 0}, {lower, 0}, {high, 0}, {high, 1}};
        for (const auto &[ar, index] : expected) {
            const auto packet = target.r(now);
            ASSERT_TRUE(packet);
            EXPECT_EQ(packet->meta.txnUid, ar.meta.txnUid);
            EXPECT_EQ(packet->meta.srcNode, ar.meta.srcNode);
            EXPECT_EQ(packet->meta.srcPort, ar.meta.srcPort);
            EXPECT_EQ(packet->beatIndex, index);
            EXPECT_EQ(packet->last, index + 1 == ar.request.beatCount);
        }
        EXPECT_FALSE(target.state.hasRPacket());
        EXPECT_EQ(target.state.completedReads(), 4u);
        EXPECT_EQ(target.state.occupancy().readContexts, 0u);
        EXPECT_EQ(target.state.occupancy().readReservedBeats, 0u);
    }
}

}

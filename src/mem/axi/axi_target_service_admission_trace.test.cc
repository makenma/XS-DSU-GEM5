#include "mem/axi/axi_behavior_trace_support.hh"

#include <iostream>
#include <stdexcept>
#include <utility>

namespace gem5::axi::behavior
{

[[noreturn]] void
finishServiceAdmissionError(BehaviorTrace &trace, const AxiTargetState &state,
                            const char *expected, bool mock_panic)
{
    if (mock_panic) {
        EXPECT_NE(gtestLogOutput.str().find(expected), std::string::npos);
    }
    std::cerr << expected << std::endl;
    trace.target("service-terminal", state);
    TraceRow row;
    row.field("error", expected).field("mockPanic", mock_panic);
    trace.record("terminal", row);
    std::_Exit(::testing::Test::HasFailure() ? 87 : 86);
}

TEST(AxiBehaviorTrace, ServiceOrphanModesKeepContextAndAwOrdering)
{
    BehaviorTrace trace("ServiceOrphanModesKeepContextAndAwOrdering");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.writeBaseLatency = config.readBaseLatency = 0;
        config.writeServiceDepth = config.readServiceDepth = 1;
        config.syntheticHbmQueueDepth = 1;
        config.extraLatency[900] = 5;
        TargetProbe target(trace, "service-orphan." + std::to_string(synthetic), config);
        auto blocker = address(900, 9, 0, 0x100);
        blocker.meta.acceptedTick = 0;
        ASSERT_TRUE(target.aw(blocker, 0));
        ASSERT_TRUE(target.w(writeBeat(blocker, 0, 0x10), 0));
        auto first = address(20, 1, 0, 0x108, 2);
        first.meta.acceptedTick = 1;
        ASSERT_TRUE(target.w(writeBeat(first, 0, 0x20), 0));
        ASSERT_TRUE(target.w(writeBeat(first, 1, 0x20), 0));
        first.meta.acceptedTick = 9;
        ASSERT_TRUE(target.aw(first, 0));
        auto second = address(10, 2, 0, 0x128, 2);
        second.meta.acceptedTick = 4;
        ASSERT_TRUE(target.w(writeBeat(second, 0, 0x30), 0));
        second.meta.acceptedTick = 3;
        ASSERT_TRUE(target.aw(second, 0));
        EXPECT_EQ(target.state.occupancy().orphanTransactions, 0u);
        EXPECT_EQ(target.state.occupancy().writeServices, 1u);
        EXPECT_EQ(target.state.progress().serviceReady, 0u);
        auto last = writeBeat(second, 1, 0x30);
        last.meta.acceptedTick = std::numeric_limits<Tick>::max();
        ASSERT_TRUE(target.w(last, 0));
        target.advance(0);
        EXPECT_EQ(target.state.occupancy().writeContexts, 3u);
        EXPECT_EQ(target.state.occupancy().writeServices, 1u);
        EXPECT_EQ(target.state.progress().writesCommitted, 0u);
        if (synthetic) {
            target.advance(6);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900}));
            EXPECT_EQ(target.state.syntheticBackendStats().submitted, 2u);
            target.advance(8);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900, 20}));
            target.advance(10);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900, 20, 10}));
        } else {
            target.advance(5);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900, 10}));
            target.advance(5);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900, 10, 20}));
        }
        EXPECT_EQ(target.state.progress().serviceReady, 3u);
        EXPECT_EQ(target.state.progress().writeCommittedBytes, 40u);
        EXPECT_EQ(target.first.uids, target.second.uids);
        target.drain(11);
        EXPECT_EQ(target.state.completedWrites(), 3u);
    }
}

TEST(AxiBehaviorTrace, ServiceKeysKeepZeroMaximumAndUidTies)
{
    BehaviorTrace trace("ServiceKeysKeepZeroMaximumAndUidTies");
    const uint64_t maximum = std::numeric_limits<uint64_t>::max();
    for (bool synthetic : {false, true}) {
        for (bool read : {false, true}) {
            auto config = targetConfig(synthetic, 16);
            config.writeBaseLatency = config.readBaseLatency = 0;
            config.writeServiceDepth = config.readServiceDepth = 1;
            config.syntheticHbmQueueDepth = 1;
            config.extraLatency[900] = 5;
            config.writeCommitTieBreakRanks = {{maximum, 0}, {0, 1}, {80, 2}, {40, 3}};
            TargetProbe target(trace, "service-keys." + std::to_string(synthetic) +
                               "." + std::to_string(read), config);
            std::vector<AxiAddressPacket> packets{
                address(900, 9, 0, 0x100), address(maximum, 1, 0, 0x108),
                address(0, 2, 0, 0x110), address(80, 3, 0, 0x118),
                address(40, 4, 0, 0x120)};
            for (size_t index = 0; index < packets.size(); ++index) {
                auto &packet = packets[index];
                packet.meta.acceptedTick = index == 1 || index == 2 ? maximum : 0;
                if (read) {
                    ASSERT_TRUE(target.ar(packet, 0));
                } else {
                    ASSERT_TRUE(target.aw(packet, 0));
                    ASSERT_TRUE(target.w(writeBeat(packet, 0, uint8_t(index * 16)), 0));
                }
            }
            EXPECT_EQ(target.state.progress().serviceReady, 0u);
            if (synthetic) {
                target.advance(6);
                EXPECT_EQ(target.state.syntheticBackendStats().submitted, 2u);
                EXPECT_EQ(target.state.progress().serviceReady, 1u);
                target.advance(6);
                EXPECT_EQ(target.state.syntheticBackendStats().submitted, 2u);
                EXPECT_EQ(target.state.progress().serviceReady, 1u);
                for (uint64_t now : {7u, 8u, 9u, 10u})
                    target.advance(now);
            } else {
                target.advance(5);
                EXPECT_EQ(target.state.progress().serviceReady, 2u);
                target.advance(5);
                EXPECT_EQ(target.state.progress().serviceReady, 4u);
                target.advance(5);
            }
            EXPECT_EQ(target.state.progress().serviceReady, 5u);
            target.advance(10);
            const std::vector<uint64_t> expected{900, 40, 80, 0, maximum};
            for (uint64_t uid : expected) {
                if (read) {
                    const auto packet = target.r(10);
                    ASSERT_TRUE(packet);
                    EXPECT_EQ(packet->meta.txnUid, uid);
                } else {
                    const auto packet = target.b(10);
                    ASSERT_TRUE(packet);
                    EXPECT_EQ(packet->meta.txnUid, uid);
                }
            }
            if (!read) {
                EXPECT_EQ(target.first.uids, expected);
            }
            EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
            target.drain(11);
        }
    }
}

TEST(AxiBehaviorTrace, ServiceCrossDirectionEqualKeySubmitsWriteFirst)
{
    BehaviorTrace trace("ServiceCrossDirectionEqualKeySubmitsWriteFirst");
    auto config = targetConfig(true, 16);
    config.writeBaseLatency = config.readBaseLatency = 0;
    config.writeServiceDepth = config.readServiceDepth = 1;
    config.syntheticHbmQueueDepth = 1;
    config.extraLatency[900] = 5;
    TargetProbe target(trace, "service-cross-key", config);
    auto blocker = address(900, 9, 0, 0x100);
    blocker.meta.acceptedTick = 0;
    ASSERT_TRUE(target.ar(blocker, 0));
    auto write = address(50, 1, 0, 0x140);
    write.meta.acceptedTick = std::numeric_limits<Tick>::max();
    auto read = address(50, 2, 0, 0x140);
    read.meta.acceptedTick = write.meta.acceptedTick;
    ASSERT_TRUE(target.aw(write, 0));
    ASSERT_TRUE(target.w(writeBeat(write, 0, 0x60), 0));
    ASSERT_TRUE(target.ar(read, 0));
    target.advance(6);
    EXPECT_EQ(target.state.syntheticBackendStats().submitted, 2u);
    EXPECT_EQ(target.state.syntheticBackendStats().completed, 1u);
    EXPECT_EQ(target.state.syntheticBackendStats().retired, 1u);
    EXPECT_EQ(target.state.occupancy().writeServices, 1u);
    EXPECT_EQ(target.state.occupancy().readServices, 0u);
    EXPECT_EQ(target.state.progress().writesCommitted, 0u);
    EXPECT_EQ(target.state.progress().readsCommitted, 1u);
    target.advance(7);
    EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{50}));
    EXPECT_EQ(target.state.syntheticBackendStats().submitted, 3u);
    EXPECT_EQ(target.state.syntheticBackendStats().retired, 2u);
    EXPECT_EQ(target.state.occupancy().readServices, 1u);
    target.advance(8);
    EXPECT_EQ(target.state.progress().serviceReady, 3u);
    EXPECT_EQ(target.state.syntheticBackendStats().readBytesServiced, 16u);
    EXPECT_EQ(target.state.syntheticBackendStats().writeBytesServiced, 8u);
    EXPECT_EQ(target.state.syntheticBackendStats().busyCycles, 3u);
    EXPECT_EQ(target.state.syntheticBackendStats().highWater, 1u);
    const auto response = target.b(8);
    ASSERT_TRUE(response);
    EXPECT_EQ(response->meta.txnUid, 50u);
    const auto first = target.r(8);
    ASSERT_TRUE(first);
    EXPECT_EQ(first->meta.txnUid, 900u);
    const auto second = target.r(8);
    ASSERT_TRUE(second);
    EXPECT_EQ(second->meta.txnUid, 50u);
    EXPECT_EQ(second->functionalData, writeBeat(write, 0, 0x60).functionalData);
    target.drain(9);
}

TEST(AxiBehaviorTrace, ServiceDirectionDepthAllowsOtherDirection)
{
    BehaviorTrace trace("ServiceDirectionDepthAllowsOtherDirection");
    for (bool held_write : {false, true}) {
        auto config = targetConfig(true, 16);
        config.writeBaseLatency = config.readBaseLatency = 0;
        config.writeServiceDepth = config.readServiceDepth = 1;
        config.syntheticHbmQueueDepth = 2;
        config.extraLatency[900] = 5;
        TargetProbe target(trace, "service-direction." + std::to_string(held_write), config);
        auto blocker = address(900, 9, 0, 0x100);
        auto pending = address(1, 1, 0, 0x108);
        auto opposite = address(2, 2, 0, 0x110);
        blocker.meta.acceptedTick = pending.meta.acceptedTick = 0;
        opposite.meta.acceptedTick = std::numeric_limits<Tick>::max();
        for (const auto &packet : {blocker, pending}) {
            if (held_write) {
                ASSERT_TRUE(target.aw(packet, 0));
                ASSERT_TRUE(target.w(writeBeat(packet, 0, 0x30), 0));
            } else {
                ASSERT_TRUE(target.ar(packet, 0));
            }
        }
        if (held_write) {
            ASSERT_TRUE(target.ar(opposite, 0));
        } else {
            ASSERT_TRUE(target.aw(opposite, 0));
            ASSERT_TRUE(target.w(writeBeat(opposite, 0, 0x50), 0));
        }
        EXPECT_EQ(target.state.syntheticBackendStats().submitted, 2u);
        EXPECT_EQ(target.state.occupancy().writeServices, 1u);
        EXPECT_EQ(target.state.occupancy().readServices, 1u);
        target.advance(1);
        EXPECT_EQ(target.state.progress().serviceReady, 1u);
        EXPECT_EQ(target.state.syntheticBackendStats().submitted, 2u);
        EXPECT_EQ(target.state.syntheticBackendStats().retired, 1u);
        EXPECT_EQ(target.state.occupancy().writeServices, held_write ? 1u : 0u);
        EXPECT_EQ(target.state.occupancy().readServices, held_write ? 0u : 1u);
        target.advance(1);
        EXPECT_EQ(target.state.syntheticBackendStats().submitted, 2u);
        target.advance(6);
        EXPECT_EQ(target.state.progress().serviceReady, 2u);
        EXPECT_EQ(target.state.syntheticBackendStats().submitted, 3u);
        EXPECT_EQ(target.state.syntheticBackendStats().retired, 2u);
        target.advance(7);
        EXPECT_EQ(target.state.progress().serviceReady, 3u);
        EXPECT_EQ(target.state.syntheticBackendStats().busyCycles, 3u);
        EXPECT_EQ(target.state.syntheticBackendStats().highWater, 2u);
        EXPECT_EQ(target.state.syntheticBackendStats().requestSlotCycles, 8u);
        EXPECT_EQ(target.state.syntheticBackendStats().queueFullCycles, 1u);
        target.drain(8);
    }
}

TEST(AxiBehaviorTrace, ServiceTwoRoundsAndResponsePopUseSameNow)
{
    BehaviorTrace trace("ServiceTwoRoundsAndResponsePopUseSameNow");
    for (bool read : {false, true}) {
        auto config = targetConfig(false, 16);
        config.writeBaseLatency = config.readBaseLatency = 0;
        config.writeServiceDepth = config.readServiceDepth = 1;
        config.extraLatency[900] = 1;
        TargetProbe target(trace, "service-two-rounds." + std::to_string(read), config);
        const std::vector<uint64_t> expected{900, 10, 20, 30};
        for (size_t index = 0; index < expected.size(); ++index) {
            auto packet = address(expected[index], uint32_t(index), 0, 0x100 + 8 * index);
            packet.meta.acceptedTick = 0;
            if (read) {
                ASSERT_TRUE(target.ar(packet, 0));
            } else {
                ASSERT_TRUE(target.aw(packet, 0));
                ASSERT_TRUE(target.w(writeBeat(packet, 0, 0x20), 0));
            }
        }
        target.advance(1);
        EXPECT_EQ(target.state.progress().serviceReady, 2u);
        EXPECT_EQ(target.state.progress().architecturalCommits, 2u);
        EXPECT_EQ(target.state.occupancy().readContexts, read ? 2u : 0u);
        EXPECT_EQ(target.state.occupancy().writeContexts, read ? 0u : 2u);
        if (read) {
            const auto packet = target.r(1);
            ASSERT_TRUE(packet);
            EXPECT_EQ(packet->meta.txnUid, 900u);
        } else {
            const auto packet = target.b(1);
            ASSERT_TRUE(packet);
            EXPECT_EQ(packet->meta.txnUid, 900u);
        }
        EXPECT_EQ(target.state.progress().serviceReady, 4u);
        EXPECT_EQ(target.state.progress().architecturalCommits, 4u);
        target.advance(1);
        EXPECT_EQ(target.state.progress().serviceReady, 4u);
        for (uint64_t uid : {10u, 20u, 30u}) {
            if (read) {
                const auto packet = target.r(1);
                ASSERT_TRUE(packet);
                EXPECT_EQ(packet->meta.txnUid, uid);
            } else {
                const auto packet = target.b(1);
                ASSERT_TRUE(packet);
                EXPECT_EQ(packet->meta.txnUid, uid);
            }
        }
        target.drain(2);
    }
}

TEST(AxiBehaviorTrace, ServiceCopiesMovePendingOrphansReuseUidAndArchive)
{
    BehaviorTrace trace("ServiceCopiesMovePendingOrphansReuseUidAndArchive");
    for (bool synthetic : {false, true}) {
        auto config = targetConfig(synthetic, 16);
        config.writeBaseLatency = config.readBaseLatency = 0;
        config.writeServiceDepth = config.readServiceDepth = 1;
        config.syntheticHbmQueueDepth = 1;
        config.extraLatency[900] = 5;
        AxiTargetState original(config);
        auto blocker = address(900, 9, 0, 0x100);
        auto pending = address(20, 2, 0, 0x108);
        auto orphan = address(30, 3, 0, 0x118, 2);
        blocker.meta.acceptedTick = pending.meta.acceptedTick = 0;
        orphan.meta.acceptedTick = 1;
        for (const auto &packet : {blocker, pending}) {
            trace.operation("service-copy.acceptAw", 0);
            TraceRow aw;
            append(aw, packet);
            trace.record("inputAw", aw);
            original.acceptAw(packet, 0);
            trace.target("service-original", original);
            trace.operation("service-copy.acceptW", 0);
            const auto beat = writeBeat(packet, 0, 0x30);
            TraceRow w;
            append(w, beat);
            trace.record("inputW", w);
            original.acceptW(beat, 0);
            trace.target("service-original", original);
        }
        trace.operation("service-copy.acceptOrphan", 0);
        const auto first_beat = writeBeat(orphan, 0, 0x50);
        TraceRow first_input;
        append(first_input, first_beat);
        trace.record("inputW", first_input);
        original.acceptW(first_beat, 0);
        trace.target("service-original", original);
        ASSERT_EQ(original.occupancy().writeContexts, 3u);
        ASSERT_EQ(original.occupancy().writeServices, 1u);
        ASSERT_EQ(original.occupancy().orphanTransactions, 1u);
        {
            std::stringstream active;
            boost::archive::binary_oarchive archive(active);
            EXPECT_THROW(original.serialize(archive, 0), std::runtime_error);
            TraceRow row;
            row.field("error", "E_CHECKPOINT_BOUNDARY: AXI target is active");
            trace.record("archiveRejected", row);
        }
        AxiTargetState copied = original;
        AxiTargetState moved = std::move(copied);
        CommitRecorder first(trace, original, "service-original");
        CommitRecorder second(trace, moved, "service-moved");
        original.registerWriteCommitObserver(&first);
        moved.registerWriteCommitObserver(&second);
        original.setPreCommitPolicy(&first);
        moved.setPreCommitPolicy(&second);
        original.memory().writeByte(0x280, 0x11);
        moved.memory().writeByte(0x280, 0x77);
        for (AxiTargetState *state : {&original, &moved}) {
            const std::string owner = state == &original ? "original" : "moved";
            trace.operation("service-copy.bindOrphan." + owner, 0);
            TraceRow aw;
            append(aw, orphan);
            trace.record("inputAw", aw);
            state->acceptAw(orphan, 0);
            trace.target(owner, *state);
            EXPECT_EQ(state->occupancy().orphanTransactions, 0u);
            EXPECT_EQ(state->occupancy().writeServices, 1u);
            trace.operation("service-copy.lastW." + owner, 0);
            const auto last = writeBeat(orphan, 1, 0x50);
            TraceRow w;
            append(w, last);
            trace.record("inputW", w);
            state->acceptW(last, 0);
            trace.target(owner, *state);
            for (uint64_t now : {6u, 7u, 9u}) {
                trace.operation("service-copy.advance." + owner, now);
                state->advance(now);
                trace.target(owner, *state);
            }
            EXPECT_EQ(state->progress().serviceReady, 3u);
            EXPECT_EQ(state->progress().writesCommitted, 3u);
            EXPECT_EQ(state->occupancy().writeContexts, 0u);
            EXPECT_EQ(state->occupancy().bReady, 3u);
            auto reused = address(20, 2, 1, 0x138);
            reused.meta.acceptedTick = std::numeric_limits<Tick>::max();
            trace.operation("service-copy.reuseAw." + owner, 10);
            TraceRow reuse_aw;
            append(reuse_aw, reused);
            trace.record("inputAw", reuse_aw);
            state->acceptAw(reused, 10);
            trace.target(owner, *state);
            trace.operation("service-copy.reuseW." + owner, 10);
            const auto reuse_beat = writeBeat(reused, 0, 0x70);
            TraceRow reuse_w;
            append(reuse_w, reuse_beat);
            trace.record("inputW", reuse_w);
            state->acceptW(reuse_beat, 10);
            trace.target(owner, *state);
            trace.operation("service-copy.advanceReuse." + owner, 11);
            state->advance(11);
            trace.target(owner, *state);
            EXPECT_EQ(state->progress().writesCommitted, 4u);
            EXPECT_EQ(state->progress().writeCommittedBytes, 40u);
            EXPECT_EQ(state->memory().readByte(0x280), state == &original ? 0x11 : 0x77);
            for (uint64_t uid : {900u, 20u, 30u, 20u}) {
                ASSERT_TRUE(state->hasBPacket());
                const auto response = state->frontBPacket();
                EXPECT_EQ(response.meta.txnUid, uid);
                TraceRow row;
                row.field("owner", owner);
                append(row, response.meta);
                row.field("resp", response.resp);
                trace.operation("service-copy.popB." + owner, 11);
                trace.record("outputB", row);
                state->popBPacket();
                trace.target(owner, *state);
            }
            EXPECT_FALSE(state->hasBPacket());
            std::stringstream checkpoint;
            {
                boost::archive::binary_oarchive archive(checkpoint);
                archive << *state;
            }
            const auto bytes = checkpoint.str();
            TraceRow row;
            row.field("owner", owner).field("synthetic", synthetic)
                .field("bytes", bytesHex(std::vector<uint8_t>(bytes.begin(), bytes.end())));
            trace.record("checkpoint", row);
            TargetProbe restored(trace, "service-restored." + owner +
                                 "." + std::to_string(synthetic), config);
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
            auto next = address(20, 2, 2, 0x140);
            next.meta.acceptedTick = 0;
            ASSERT_TRUE(restored.aw(next, 20));
            ASSERT_TRUE(restored.w(writeBeat(next, 0, 0x80), 20));
            restored.advance(21);
            EXPECT_EQ(restored.state.progress().writesCommitted, 5u);
            EXPECT_EQ(restored.state.memory().readByte(0x140), 0x80);
            ASSERT_TRUE(restored.b(21));
            restored.drain(22);
        }
        EXPECT_EQ(first.uids, (std::vector<uint64_t>{900, 20, 30, 20}));
        EXPECT_EQ(second.uids, first.uids);
        AxiTargetState read_original(config);
        for (uint64_t uid : {900u, 30u, 20u}) {
            auto packet = address(uid, uint32_t(uid), 0,
                uid == 900 ? 0x100 : uid == 20 ? 0x108 : 0x118);
            packet.meta.acceptedTick = uid == 900 ? 0 : uid == 20 ? 1 : 2;
            trace.operation("service-read-copy.acceptAr", 0);
            TraceRow row;
            append(row, packet);
            trace.record("inputAr", row);
            read_original.acceptAr(packet, 0);
            trace.target("service-read-original", read_original);
        }
        ASSERT_EQ(read_original.occupancy().readContexts, 3u);
        ASSERT_EQ(read_original.occupancy().readServices, 1u);
        ASSERT_EQ(read_original.occupancy().rReady, 0u);
        ASSERT_EQ(read_original.progress().serviceReady, 0u);
        AxiTargetState read_copied = read_original;
        AxiTargetState read_moved = std::move(read_copied);
        for (AxiTargetState *state : {&read_moved, &read_original}) {
            const bool is_moved = state == &read_moved;
            const std::string owner = is_moved ? "read-moved" : "read-original";
            const uint8_t value = is_moved ? 0x77 : 0x11;
            trace.operation("service-read-copy.memory." + owner, 0);
            TraceRow memory;
            memory.field("owner", owner).field("address", 0x108).field("value", value);
            trace.record("inputMemory", memory);
            state->memory().writeByte(0x108, value);
            trace.target(owner, *state);
            for (uint64_t now : {6u, 7u, 8u}) {
                trace.operation("service-read-copy.advance." + owner, now);
                state->advance(now);
                trace.target(owner, *state);
            }
            EXPECT_EQ(state->progress().serviceReady, 3u);
            EXPECT_EQ(state->progress().readsCommitted, 3u);
            EXPECT_EQ(state->progress().readCommittedBytes, 24u);
            EXPECT_EQ(state->occupancy().readContexts, 0u);
            EXPECT_EQ(state->occupancy().readServices, 0u);
            EXPECT_EQ(state->occupancy().rReady, 3u);
            for (uint64_t uid : {900u, 20u, 30u}) {
                ASSERT_TRUE(state->hasRPacket());
                const auto packet = state->frontRPacket();
                EXPECT_EQ(packet.meta.txnUid, uid);
                EXPECT_TRUE(packet.last);
                std::vector<uint8_t> expected(8, 0);
                if (uid == 20)
                    expected[0] = value;
                EXPECT_EQ(packet.functionalData, expected);
                TraceRow row;
                row.field("owner", owner);
                append(row, packet);
                trace.operation("service-read-copy.popR." + owner, 8);
                trace.record("outputR", row);
                state->popRPacket();
                trace.target(owner, *state);
            }
            EXPECT_FALSE(state->hasRPacket());
            EXPECT_EQ(state->completedReads(), 3u);
            EXPECT_EQ(state->occupancy().readReservedBeats, 0u);
            EXPECT_EQ(state->progress().architecturalCommits, 3u);
            if (is_moved) {
                EXPECT_EQ(read_original.occupancy().readContexts, 3u);
                EXPECT_EQ(read_original.occupancy().readServices, 1u);
                EXPECT_EQ(read_original.occupancy().rReady, 0u);
                EXPECT_EQ(read_original.progress().serviceReady, 0u);
                EXPECT_EQ(read_original.memory().readByte(0x108), 0u);
                trace.target("service-read-original-unadvanced", read_original);
            }
        }
    }
}

TEST(AxiBehaviorTrace, ServiceLatencyOverflowWaitsForActualSelection)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("ServiceLatencyOverflowWaitsForActualSelection");
        auto config = targetConfig(false, 16);
        config.writeBaseLatency = 1;
        config.writeServiceDepth = 1;
        config.extraLatency = {{900, 4}, {30, std::numeric_limits<uint32_t>::max()}};
        TargetProbe target(trace, "service-latency-overflow", config);
        for (uint64_t uid : {900u, 30u, 20u}) {
            auto packet = address(uid, uint32_t(uid), 0, 0x100 + uid % 3 * 8);
            packet.meta.acceptedTick = uid == 30 ? std::numeric_limits<Tick>::max() : 0;
            ASSERT_TRUE(target.aw(packet, 0));
            ASSERT_TRUE(target.w(writeBeat(packet, 0, 0x30), 0));
        }
        target.advance(5);
        EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900}));
        EXPECT_EQ(target.state.progress().serviceReady, 1u);
        EXPECT_EQ(target.state.occupancy().writeServices, 1u);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(6);
        } catch (...) {
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900, 20}));
            EXPECT_EQ(target.state.progress().serviceReady, 2u);
            EXPECT_EQ(target.state.progress().writesCommitted, 2u);
            EXPECT_EQ(target.state.occupancy().writeServices, 0u);
            EXPECT_EQ(target.state.occupancy().writeContexts, 1u);
            EXPECT_EQ(target.state.occupancy().bReady, 2u);
            finishServiceAdmissionError(trace, target.state,
                "AXI target service latency overflows uint32", true);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI target service latency overflows uint32");
}

TEST(AxiBehaviorTrace, ServiceOrdinaryWriteAdmissionPrecedesReadTimeOverflow)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("ServiceOrdinaryWriteAdmissionPrecedesReadTimeOverflow");
        const uint64_t maximum = std::numeric_limits<uint64_t>::max();
        auto config = targetConfig(false, 16);
        config.writeBaseLatency = 0;
        config.readBaseLatency = 1;
        config.writeServiceDepth = config.readServiceDepth = 1;
        config.extraLatency[900] = 1;
        TargetProbe target(trace, "service-ordinary-time-overflow", config);
        auto write_blocker = address(900, 9, 0, 0x100);
        write_blocker.meta.acceptedTick = 0;
        ASSERT_TRUE(target.aw(write_blocker, 0));
        ASSERT_TRUE(target.w(writeBeat(write_blocker, 0, 0x20), 0));
        auto read_blocker = address(901, 9, 0, 0x108);
        read_blocker.meta.acceptedTick = 0;
        ASSERT_TRUE(target.ar(read_blocker, 0));
        auto write = address(30, 1, 0, 0x110);
        write.meta.acceptedTick = maximum;
        ASSERT_TRUE(target.aw(write, 0));
        ASSERT_TRUE(target.w(writeBeat(write, 0, 0x50), 0));
        auto read = address(1, 2, 0, 0x118);
        read.meta.acceptedTick = 0;
        ASSERT_TRUE(target.ar(read, 0));
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            target.advance(maximum);
        } catch (...) {
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{900}));
            EXPECT_EQ(target.state.progress().serviceReady, 2u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().readsCommitted, 1u);
            EXPECT_EQ(target.state.progress().lastWriteCommitCycle, maximum);
            EXPECT_EQ(target.state.occupancy().writeServices, 1u);
            EXPECT_EQ(target.state.occupancy().readServices, 0u);
            EXPECT_EQ(target.state.occupancy().writeContexts, 1u);
            EXPECT_EQ(target.state.occupancy().readContexts, 1u);
            EXPECT_EQ(target.state.occupancy().bReady, 1u);
            EXPECT_EQ(target.state.occupancy().rReady, 1u);
            finishServiceAdmissionError(trace, target.state,
                "AXI target read service-ready time overflows uint64", true);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI target read service-ready time overflows uint64");
}

TEST(AxiBehaviorTrace, ServiceSyntheticTimeOverflowKeepsCompletionPrefix)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("ServiceSyntheticTimeOverflowKeepsCompletionPrefix");
        auto config = targetConfig(true, 16);
        config.readBaseLatency = 0;
        config.readServiceDepth = 1;
        config.syntheticHbmQueueDepth = 1;
        config.extraLatency[30] = 1;
        TargetProbe target(trace, "service-synthetic-time-overflow", config);
        auto blocker = address(900, 9, 0, 0x100);
        blocker.meta.acceptedTick = 0;
        ASSERT_TRUE(target.ar(blocker, 0));
        auto pending = address(30, 1, 0, 0x108);
        pending.meta.acceptedTick = 0;
        ASSERT_TRUE(target.ar(pending, 0));
        try {
            target.advance(std::numeric_limits<uint64_t>::max());
        } catch (const std::invalid_argument &error) {
            const char *expected = "synthetic HBM request size/time invalid";
            EXPECT_STREQ(error.what(), expected);
            EXPECT_EQ(target.state.progress().serviceReady, 1u);
            EXPECT_EQ(target.state.progress().readsCommitted, 1u);
            EXPECT_EQ(target.state.occupancy().readServices, 0u);
            EXPECT_EQ(target.state.occupancy().readContexts, 1u);
            EXPECT_EQ(target.state.occupancy().rReady, 1u);
            EXPECT_EQ(target.state.syntheticBackendStats().submitted, 1u);
            EXPECT_EQ(target.state.syntheticBackendStats().completed, 1u);
            EXPECT_EQ(target.state.syntheticBackendStats().retired, 1u);
            EXPECT_EQ(target.state.syntheticBackendStats().queued, 0u);
            finishServiceAdmissionError(trace, target.state, expected, false);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86), "synthetic HBM request size/time invalid");
}

TEST(AxiBehaviorTrace, ServiceCrossDirectionDuplicateFailsAfterWriteGrant)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("ServiceCrossDirectionDuplicateFailsAfterWriteGrant");
        auto config = targetConfig(true, 16);
        config.writeBaseLatency = config.readBaseLatency = 0;
        config.writeServiceDepth = config.readServiceDepth = 1;
        config.syntheticHbmQueueDepth = 2;
        config.extraLatency = {{900, 5}, {901, 5}};
        TargetProbe target(trace, "service-cross-duplicate", config);
        auto read_blocker = address(900, 9, 0, 0x100);
        read_blocker.meta.acceptedTick = 0;
        ASSERT_TRUE(target.ar(read_blocker, 0));
        auto write_blocker = address(901, 9, 0, 0x108);
        write_blocker.meta.acceptedTick = 0;
        ASSERT_TRUE(target.aw(write_blocker, 0));
        ASSERT_TRUE(target.w(writeBeat(write_blocker, 0, 0x20), 0));
        auto write = address(50, 1, 0, 0x110);
        write.meta.acceptedTick = 0;
        auto read = address(50, 2, 0, 0x118);
        read.meta.acceptedTick = 0;
        ASSERT_TRUE(target.aw(write, 0));
        ASSERT_TRUE(target.w(writeBeat(write, 0, 0x50), 0));
        ASSERT_TRUE(target.ar(read, 0));
        try {
            target.advance(7);
        } catch (const std::invalid_argument &error) {
            const char *expected = "duplicate synthetic HBM request uid";
            EXPECT_STREQ(error.what(), expected);
            EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{901}));
            EXPECT_EQ(target.state.progress().serviceReady, 2u);
            EXPECT_EQ(target.state.progress().writesCommitted, 1u);
            EXPECT_EQ(target.state.progress().readsCommitted, 1u);
            EXPECT_EQ(target.state.occupancy().writeServices, 1u);
            EXPECT_EQ(target.state.occupancy().readServices, 0u);
            EXPECT_EQ(target.state.occupancy().writeContexts, 1u);
            EXPECT_EQ(target.state.occupancy().readContexts, 1u);
            EXPECT_EQ(target.state.occupancy().bReady, 1u);
            EXPECT_EQ(target.state.occupancy().rReady, 1u);
            EXPECT_EQ(target.state.syntheticBackendStats().submitted, 3u);
            EXPECT_EQ(target.state.syntheticBackendStats().completed, 2u);
            EXPECT_EQ(target.state.syntheticBackendStats().retired, 2u);
            EXPECT_EQ(target.state.syntheticBackendStats().queued, 1u);
            finishServiceAdmissionError(trace, target.state, expected, false);
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86), "duplicate synthetic HBM request uid");
}

}

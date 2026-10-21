#include "mem/axi/axi_behavior_trace_support.hh"

namespace gem5::axi::behavior
{

struct Flight
{
    unsigned source;
    AxiAddressPacket packet;
    std::vector<AxiDataPacket> beats;
};

TEST(AxiBehaviorTrace, HbmSelectionKeepsSequenceActiveJobAndFiniteSlots)
{
    BehaviorTrace trace("HbmSelectionKeepsSequenceActiveJobAndFiniteSlots");
    SyntheticHbmBackend original(32, 4);
    ASSERT_TRUE(original.trySubmit(9, SyntheticHbmDirection::Read, 33, 0, 2));
    ASSERT_TRUE(original.trySubmit(1, SyntheticHbmDirection::Write, 32, 0, 2));
    ASSERT_TRUE(original.trySubmit(5, SyntheticHbmDirection::Read, 1, 0, 1));
    original.advance(1);
    EXPECT_TRUE(original.takeCompleted().empty());
    original.advance(2);
    ASSERT_TRUE(original.trySubmit(7, SyntheticHbmDirection::Read, 32, 2, 0));
    original.advance(3);
    SyntheticHbmBackend copied(original);
    SyntheticHbmBackend moving(original);
    SyntheticHbmBackend moved(std::move(moving));
    for (auto *backend : {&original, &copied, &moved}) {
        for (uint64_t now : {3u, 3u, 4u, 5u, 6u, 8u}) {
            trace.operation("advanceBackend", now);
            backend->advance(now);
            const auto stats = backend->stats();
            TraceRow row;
            row.field("submitted", stats.submitted)
                .field("completed", stats.completed)
                .field("retired", stats.retired)
                .field("readBytes", stats.readBytesServiced)
                .field("writeBytes", stats.writeBytesServiced)
                .field("busyCycles", stats.busyCycles)
                .field("fullCycles", stats.queueFullCycles)
                .field("slotCycles", stats.requestSlotCycles)
                .field("queued", stats.queued)
                .field("ready", stats.ready)
                .field("highWater", stats.highWater);
            trace.record("stats", row);
        }
        EXPECT_TRUE(backend->full());
        EXPECT_FALSE(backend->idle());
        const auto completions = backend->takeCompleted();
        ASSERT_EQ(completions.size(), 4u);
        const std::array<uint64_t, 4> uids{5, 9, 1, 7};
        const std::array<uint64_t, 4> ticks{2, 4, 5, 6};
        for (size_t index = 0; index < completions.size(); ++index) {
            const auto &completion = completions[index];
            TraceRow row;
            row.field("uid", completion.uid)
                .field("direction", completion.direction)
                .field("completedAt", completion.completedAt);
            trace.record("completion", row);
            EXPECT_EQ(completion.uid, uids[index]);
            EXPECT_EQ(completion.completedAt, ticks[index]);
        }
        EXPECT_TRUE(backend->idle());
        EXPECT_TRUE(backend->takeCompleted().empty());
        EXPECT_EQ(backend->stats().retired, 4u);
        EXPECT_EQ(backend->stats().busyCycles, 5u);
    }
}

TEST(AxiBehaviorTrace, TargetTransitions)
{
    BehaviorTrace trace("TargetTransitions");
    for (bool synthetic : {false, true}) {
        for (uint32_t depth : {1u, 3u}) {
            auto config = targetConfig(synthetic, depth);
            config.extraLatency[10] = 5;
            config.writeCommitTieBreakRanks = {{12, 1}, {13, 0}};
            config.writeCommitObserverReplays[13] = 2;
            config.transactionFaults[15] = AxiResp::SlvErr;
            config.transactionPostCommitFaults[16] = AxiResp::SlvErr;
            config.bEjectionDelay[12] = 3;
            TargetProbe target(trace, "target." + std::to_string(synthetic) +
                               "." + std::to_string(depth), config);
            target.first.policies[0x158] = {AxiResp::SlvErr, false};
            const auto older = address(10, 1, 0, 0x100, 2);
            const auto younger = address(11, 1, 1, 0x100, 2);
            ASSERT_TRUE(target.aw(older, 0));
            ASSERT_TRUE(target.w(writeBeat(older, 1, 0x10), 0));
            ASSERT_TRUE(target.w(writeBeat(older, 0, 0x10), 0));
            auto orphan = address(14, 4, 0, 0x140, 2);
            orphan.meta.acceptedTick = 1;
            ASSERT_TRUE(target.w(writeBeat(orphan, 0, 0x40), 0));
            ASSERT_TRUE(target.w(writeBeat(orphan, 1, 0x40), 0));
            orphan.meta.acceptedTick = 7;
            ASSERT_TRUE(target.aw(orphan, 0));
            ASSERT_TRUE(target.aw(younger, 0));
            ASSERT_TRUE(target.w(writeBeat(younger, 0, 0x20), 0));
            ASSERT_TRUE(target.w(writeBeat(younger, 1, 0x20), 0));
            for (uint64_t uid : {12u, 13u, 15u, 16u, 17u}) {
                const uint64_t offset = uid == 17 ? 0x158 : 0x160 + (uid - 12) * 8;
                auto packet = address(uid, uint32_t(uid), 0, offset);
                packet.meta.acceptedTick = 0;
                ASSERT_TRUE(target.aw(packet, 0));
                auto beat = writeBeat(packet, 0, uint8_t(uid));
                beat.byteStrobe = 0x55;
                ASSERT_TRUE(target.w(beat, 0));
            }
            ASSERT_TRUE(target.ar(address(100, 1, 0, 0x100, 2), 0));
            ASSERT_TRUE(target.ar(address(101, 1, 0, 0x140, 2, {1, 1}), 0));
            ASSERT_TRUE(target.ar(address(102, 1, 0, 0x160, 2, {2, 0}), 0));
            target.advance(0);
            target.advance(0);
            for (uint64_t now = 1; now <= 40; ++now) {
                target.advance(now);
                if (now == 5)
                    target.memory(0x100, 0xee, now);
                if (now >= 10)
                    target.b(now);
                if (now % 3 == 0)
                    target.r(now);
            }
            target.drain(41);
            EXPECT_EQ(target.state.progress().writesCommitted, 8u);
            EXPECT_EQ(target.state.progress().readsCommitted, 3u);
        }
    }
}

TEST(AxiBehaviorTrace, BCapacityAndTwoPass)
{
    BehaviorTrace trace("BCapacityAndTwoPass");
    auto config = targetConfig(false, 1);
    config.writeBaseLatency = 0;
    config.readBaseLatency = 0;
    config.extraLatency[20] = 1;
    TargetProbe target(trace, "b-capacity", config);
    const auto blocker = address(19, 0, 0, 0x100);
    const auto older = address(20, 1, 0, 0x108);
    const auto younger = address(21, 1, 1, 0x108);
    for (const auto &packet : {blocker, older, younger}) {
        ASSERT_TRUE(target.aw(packet, 0));
        ASSERT_TRUE(target.w(writeBeat(packet, 0, uint8_t(packet.meta.txnUid)), 0));
    }
    target.advance(1);
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 0u);
    ASSERT_TRUE(target.b(1));
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
    ASSERT_TRUE(target.b(1));
    EXPECT_EQ(target.state.progress().sameIdReadyBlocked, 1u);
    ASSERT_TRUE(target.b(1));
    target.drain(2);

    config.rReadyDepth = 8;
    config.extraLatency.clear();
    config.extraLatency[30] = 1;
    TargetProbe reads(trace, "two-pass", config);
    for (uint64_t uid : {30u, 31u, 32u})
        ASSERT_TRUE(reads.ar(address(uid, uint32_t(uid), 0, 0x100), 0));
    reads.advance(1);
    EXPECT_EQ(reads.state.progress().readsCommitted, 2u);
    reads.advance(1);
    EXPECT_EQ(reads.state.progress().readsCommitted, 3u);
    reads.drain(2);
}

TEST(AxiBehaviorTrace, MixedBulkCompletionAndFrozenReads)
{
    BehaviorTrace trace("MixedBulkCompletionAndFrozenReads");
    auto config = targetConfig(true, 8);
    config.readBaseLatency = 0;
    config.writeBaseLatency = 0;
    config.rReadyDepth = 1;
    TargetProbe target(trace, "bulk-ready", config);
    ASSERT_TRUE(target.ar(address(900, 0, 0, 0x100), 0));
    const auto first = address(5, 0, 0, 0x100);
    const auto second = address(500, 1, 0, 0x100);
    ASSERT_TRUE(target.aw(first, 0));
    ASSERT_TRUE(target.w(writeBeat(first, 0, 0x50), 0));
    ASSERT_TRUE(target.aw(second, 0));
    ASSERT_TRUE(target.w(writeBeat(second, 0, 0x90), 0));
    ASSERT_TRUE(target.ar(address(1, 1, 0, 0x100), 0));
    target.advance(10);
    EXPECT_EQ(target.state.progress().serviceReady, 4u);
    EXPECT_EQ(target.state.progress().architecturalCommits, 4u);
    EXPECT_EQ(target.state.syntheticBackendStats().retired, 4u);
    EXPECT_EQ(target.first.uids, (std::vector<uint64_t>{5, 500}));
    target.memory(0x100, 0x33, 10);
    for (uint64_t uid : {1u, 900u}) {
        const auto packet = target.r(10);
        ASSERT_TRUE(packet);
        EXPECT_EQ(packet->meta.txnUid, uid);
        EXPECT_EQ(packet->functionalData, writeBeat(second, 0, 0x90).functionalData);
    }
    target.drain(11);
}

TEST(AxiBehaviorTrace, SeededInitiatorTargetLoop)
{
    BehaviorTrace trace("SeededInitiatorTargetLoop");
    for (bool synthetic : {false, true}) {
        for (uint32_t seed : {7u, 42u}) {
            std::mt19937 random(seed);
            auto config0 = targetConfig(synthetic, 2);
            auto config1 = targetConfig(synthetic, 3, 2);
            config0.readBaseLatency = 7;
            config0.writeBaseLatency = 5;
            config1.readBaseLatency = 1;
            config1.writeBaseLatency = 1;
            TargetProbe target0(trace, "loop0." + std::to_string(synthetic) +
                                "." + std::to_string(seed), config0);
            TargetProbe target1(trace, "loop1." + std::to_string(synthetic) +
                                "." + std::to_string(seed), config1);
            std::array<TargetProbe *, 2> targets{&target0, &target1};
            std::array<AxiInitiatorState, 2> sources{
                AxiInitiatorState(sourceConfig({1, 0})),
                AxiInitiatorState(sourceConfig({1, 1}))};
            std::vector<Flight> writes;
            uint64_t acceptedWrites = 0;
            uint64_t acceptedReads = 0;
            uint64_t consumedWrites = 0;
            uint64_t consumedReads = 0;
            for (uint64_t now = 0; now < 600; ++now) {
                for (unsigned source = 0; source < sources.size(); ++source) {
                    auto &state = sources[source];
                    const std::string sourceName = std::to_string(source);
                    if (now < 32) {
                        AxiAddressRequest request;
                        request.axiId = uint32_t((now / 2) % 2);
                        request.address = (now % 2) * 0x1000 + 0x100 +
                                          ((now / 2 + source) % 8) * 0x20;
                        request.size = 3;
                        request.beatCount = uint16_t(1 + random() % 3);
                        request.qos = uint8_t(random() % 16);
                        trace.operation("source.tryAcceptAr." + sourceName, now);
                        TraceRow input;
                        append(input, request);
                        const bool accepted = state.tryAcceptAr(request, now);
                        input.field("accepted", accepted);
                        trace.record("input", input);
                        trace.source(sourceName, state);
                        acceptedReads += accepted;
                        if (now % 3 != source) {
                            trace.operation("source.tryAcceptAw." + sourceName, now);
                            TraceRow write;
                            append(write, request);
                            const bool granted = state.tryAcceptAw(request, now);
                            write.field("accepted", granted);
                            trace.record("input", write);
                            trace.source(sourceName, state);
                            if (granted) {
                                ++acceptedWrites;
                                for (uint16_t index = 0; index < request.beatCount; ++index) {
                                    AxiWBeat beat;
                                    beat.last = index + 1 == request.beatCount;
                                    beat.byteStrobe = index % 2 ? 0x55 : 0xff;
                                    beat.functionalData.resize(8);
                                    for (auto &byte : beat.functionalData)
                                        byte = uint8_t(random());
                                    beat.payloadDigest = payloadDigest(beat.functionalData);
                                    trace.operation("source.tryAcceptW." + sourceName, now);
                                    const bool stored = state.tryAcceptW(beat, now);
                                    TraceRow data;
                                    data.field("accepted", stored).field("last", beat.last)
                                        .field("strobe", beat.byteStrobe)
                                        .field("digest", beat.payloadDigest)
                                        .field("data", bytesHex(beat.functionalData));
                                    trace.record("input", data);
                                    trace.source(sourceName, state);
                                    ASSERT_TRUE(stored);
                                }
                            }
                        }
                    }
                    trace.operation("source.hasAwPacket." + sourceName, now);
                    const bool aw = state.hasAwPacket();
                    TraceRow awResult;
                    awResult.field("available", aw);
                    trace.record("result", awResult);
                    trace.source(sourceName, state);
                    if (aw) {
                        Flight flight;
                        flight.source = source;
                        trace.operation("source.frontAwPacket." + sourceName, now);
                        flight.packet = state.frontAwPacket();
                        TraceRow packet;
                        append(packet, flight.packet);
                        trace.record("packet", packet);
                        trace.source(sourceName, state);
                        trace.operation("source.popAwPacket." + sourceName, now);
                        state.popAwPacket();
                        trace.source(sourceName, state);
                        for (uint16_t index = 0; index < flight.packet.request.beatCount; ++index) {
                            trace.operation("source.hasWPacket." + sourceName, now);
                            const bool w = state.hasWPacket();
                            TraceRow available;
                            available.field("available", w);
                            trace.record("result", available);
                            trace.source(sourceName, state);
                            ASSERT_TRUE(w);
                            trace.operation("source.frontWPacket." + sourceName, now);
                            flight.beats.push_back(state.frontWPacket());
                            TraceRow data;
                            append(data, flight.beats.back());
                            trace.record("packet", data);
                            trace.source(sourceName, state);
                            trace.operation("source.popWPacket." + sourceName, now);
                            state.popWPacket();
                            trace.source(sourceName, state);
                        }
                        writes.push_back(std::move(flight));
                    }
                    trace.operation("source.hasArPacket." + sourceName, now);
                    const bool ar = state.hasArPacket();
                    TraceRow arResult;
                    arResult.field("available", ar);
                    trace.record("result", arResult);
                    trace.source(sourceName, state);
                    if (ar) {
                        trace.operation("source.frontArPacket." + sourceName, now);
                        const auto packet = state.frontArPacket();
                        TraceRow data;
                        append(data, packet);
                        trace.record("packet", data);
                        trace.source(sourceName, state);
                        ASSERT_TRUE(targets[packet.meta.dstNode - 1]->ar(packet, now));
                        trace.operation("source.popArPacket." + sourceName, now);
                        state.popArPacket();
                        trace.source(sourceName, state);
                    }
                }
                if (!writes.empty()) {
                    const size_t selected = now < 32 ? random() % writes.size() : 0;
                    const auto &flight = writes[selected];
                    auto &target = *targets[flight.packet.meta.dstNode - 1];
                    if (now % 2) {
                        ASSERT_TRUE(target.aw(flight.packet, now));
                        for (const auto &beat : flight.beats)
                            ASSERT_TRUE(target.w(beat, now));
                    } else {
                        for (const auto &beat : flight.beats)
                            ASSERT_TRUE(target.w(beat, now));
                        ASSERT_TRUE(target.aw(flight.packet, now));
                    }
                    writes.erase(writes.begin() + selected);
                }
                for (auto *target : targets) {
                    target->advance(now);
                    if (now < 16 || now % 3 == 0)
                        continue;
                    if (target->state.hasBPacket()) {
                        const auto packet = target->state.frontBPacket();
                        const unsigned source = packet.meta.srcPort;
                        trace.operation("source.acceptBPacket." + std::to_string(source), now);
                        ASSERT_TRUE(sources[source].canAcceptBPacket(packet));
                        sources[source].acceptBPacket(packet, now);
                        trace.source(std::to_string(source), sources[source]);
                        target->b(now);
                    }
                    if (target->state.hasRPacket()) {
                        const auto packet = target->state.frontRPacket();
                        const unsigned source = packet.meta.srcPort;
                        trace.operation("source.acceptRPacket." + std::to_string(source), now);
                        ASSERT_TRUE(sources[source].canAcceptRPacket(packet));
                        sources[source].acceptRPacket(packet, now);
                        trace.source(std::to_string(source), sources[source]);
                        target->r(now);
                    }
                }
                for (unsigned source = 0; source < sources.size(); ++source) {
                    trace.operation("source.tryConsumeB." + std::to_string(source), now);
                    AxiBBeat response;
                    const bool b = sources[source].tryConsumeB(response);
                    TraceRow write;
                    write.field("available", b);
                    if (b) {
                        write.field("id", response.axiId).field("resp", response.resp);
                        ++consumedWrites;
                    }
                    trace.record("consumedB", write);
                    trace.source(std::to_string(source), sources[source]);
                    trace.operation("source.tryConsumeR." + std::to_string(source), now);
                    AxiRBeat beat;
                    const bool r = sources[source].tryConsumeR(beat, now);
                    TraceRow read;
                    read.field("available", r);
                    if (r) {
                        read.field("id", beat.axiId).field("last", beat.last)
                            .field("resp", beat.resp).field("digest", beat.payloadDigest)
                            .field("data", bytesHex(beat.functionalData));
                        consumedReads += beat.last;
                    }
                    trace.record("consumedR", read);
                    trace.source(std::to_string(source), sources[source]);
                }
                if (now >= 32 && acceptedReads == consumedReads &&
                    acceptedWrites == consumedWrites && writes.empty())
                    break;
            }
            EXPECT_GT(acceptedReads, 0u);
            EXPECT_GT(acceptedWrites, 0u);
            EXPECT_EQ(acceptedReads, consumedReads);
            EXPECT_EQ(acceptedWrites, consumedWrites);
            for (unsigned source = 0; source < sources.size(); ++source) {
                trace.source(std::to_string(source), sources[source]);
                EXPECT_EQ(sources[source].occupancy().outstandingReads, 0u);
                EXPECT_EQ(sources[source].occupancy().outstandingWrites, 0u);
                EXPECT_TRUE(sources[source].finalConsistencyError().empty());
            }
            target0.drain(600);
            target1.drain(600);
        }
    }
}

}

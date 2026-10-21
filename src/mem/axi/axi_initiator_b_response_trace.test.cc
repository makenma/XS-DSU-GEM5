#include "mem/axi/axi_behavior_trace_support.hh"

namespace gem5::axi::behavior
{

[[noreturn]] void
finishBResponsePanic(BehaviorTrace &trace, const AxiInitiatorState &state,
                     const char *expected)
{
    EXPECT_NE(gtestLogOutput.str().find(expected), std::string::npos);
    trace.source("b-terminal", state);
    TraceRow row;
    row.field("error", expected);
    trace.record("terminal", row);
    std::_Exit(::testing::Test::HasFailure() ? 87 : 86);
}

TEST(AxiBehaviorTrace, BFullBatchObservesBeforeChoosingAndPromotingHeads)
{
    BehaviorTrace trace("BFullBatchObservesBeforeChoosingAndPromotingHeads");
    for (bool head_queued : {false, true}) {
        auto config = sourceConfig({3, 5});
        config.fifoDepths[2] = 1;
        SourceProbe source(trace, "b-batch." + std::to_string(head_queued), config);
        std::optional<AxiAddressPacket> blocker;
        if (!head_queued)
            blocker = source.issue(true, 7, 1, 0, 2);
        const auto head = source.issue(true, 1, 1, 0, 1);
        const auto younger = source.issue(true, 1, 1, 0, 2);
        const auto last = source.issue(true, 1, 1, 0, 1);
        const auto other = source.issue(true, 2, 1, 0, 1);
        EXPECT_NE(head.meta.txnUid, head.writeOrdinal);
        EXPECT_EQ(head.meta.targetSeq, 0u);
        EXPECT_EQ(younger.meta.targetSeq, 0u);
        EXPECT_EQ(last.meta.targetSeq, 1u);
        EXPECT_EQ(head.meta.responseSeq, 0u);
        EXPECT_EQ(younger.meta.responseSeq, 1u);
        EXPECT_EQ(last.meta.responseSeq, 2u);
        source.receiveB(head_queued ? head : *blocker, 20, 1);
        source.receiveB(last, 0, 2, AxiResp::DecErr);
        source.receiveB(other, 15, 2);
        source.receiveB(younger, 1, 2, AxiResp::SlvErr);
        if (!head_queued)
            source.receiveB(head, 20, 2);
        source.advance(2);
        source.advance(2);
        const size_t transactions = head_queued ? 4 : 5;
        EXPECT_EQ(source.state.progress().bPacketsBuffered, transactions);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
        EXPECT_EQ(source.state.progress().bTransactionsRetired, 0u);
        EXPECT_EQ(source.state.occupancy().b, 1u);
        EXPECT_EQ(source.state.occupancy().outstandingWrites, transactions);
        EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, transactions);
        EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, transactions);
        const std::vector<std::pair<uint32_t, AxiResp>> expected = head_queued ?
            std::vector<std::pair<uint32_t, AxiResp>>{
                {1, AxiResp::Okay}, {1, AxiResp::SlvErr},
                {1, AxiResp::DecErr}, {2, AxiResp::Okay}} :
            std::vector<std::pair<uint32_t, AxiResp>>{
                {7, AxiResp::Okay}, {2, AxiResp::Okay},
                {1, AxiResp::Okay}, {1, AxiResp::SlvErr},
                {1, AxiResp::DecErr}};
        for (size_t index = 0; index < expected.size(); ++index) {
            const auto beat = source.consumeB(3);
            ASSERT_TRUE(beat);
            EXPECT_EQ(beat->axiId, expected[index].first);
            EXPECT_EQ(beat->resp, expected[index].second);
            EXPECT_EQ(source.state.progress().sameIdResponsesBlocked,
                      head_queued ? 1u : 2u);
            EXPECT_EQ(source.state.progress().bTransactionsRetired, index + 1);
            EXPECT_EQ(source.state.progress().bPacketsBuffered, transactions);
            const size_t remaining = expected.size() - index - 1;
            EXPECT_EQ(source.state.occupancy().b, remaining ? 1u : 0u);
            EXPECT_EQ(source.state.occupancy().outstandingWrites, remaining);
            EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, remaining);
            EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, remaining);
        }
        EXPECT_FALSE(source.consumeB(3));
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked,
                  head_queued ? 1u : 2u);
        EXPECT_TRUE(source.state.finalConsistencyError().empty());
    }
}

TEST(AxiBehaviorTrace, BFutureTicksSortAndReceivedPacketsRemainValues)
{
    BehaviorTrace trace("BFutureTicksSortAndReceivedPacketsRemainValues");
    for (bool equal_tick : {false, true}) {
        auto config = sourceConfig({0, 0});
        config.fifoDepths[2] = 1;
        SourceProbe source(trace, "b-future." + std::to_string(equal_tick), config);
        const auto blocker = source.issue(true, 7, 1, 0);
        const auto earlier_uid = source.issue(true, 4, 1, 0);
        const auto later_uid = source.issue(true, 3, 1, 0);
        const auto maximum_tick = source.issue(true, 5, 1, 0);
        EXPECT_EQ(blocker.meta.txnUid, 0u);
        EXPECT_LT(earlier_uid.meta.txnUid, later_uid.meta.txnUid);
        source.receiveB(blocker, 20, 1);
        source.receiveB(maximum_tick, std::numeric_limits<Tick>::max(), 1);
        AxiBPacket packet;
        packet.meta = later_uid.meta;
        packet.meta.qos = 9;
        packet.meta.acceptedTick = std::numeric_limits<Tick>::max();
        packet.meta.semanticBytes = 0;
        packet.resp = AxiResp::SlvErr;
        trace.operation("source.acceptBPacket.uncheckedMetadata", 1);
        TraceRow input;
        append(input, packet.meta);
        input.field("resp", packet.resp).field("readyTick", equal_tick ? 100 : 80);
        trace.record("input", input);
        ASSERT_TRUE(source.state.canAcceptBPacket(packet));
        source.state.acceptBPacket(packet, equal_tick ? 100 : 80);
        trace.source("b-future", source.state);
        trace.operation("mutateCallerBPacket", 1);
        packet.meta.axiId = 15;
        packet.meta.responseSeq = 999;
        packet.resp = AxiResp::ExOkay;
        TraceRow mutated;
        append(mutated, packet.meta);
        mutated.field("resp", packet.resp);
        trace.record("callerMutation", mutated);
        trace.source("b-future", source.state);
        source.receiveB(earlier_uid, 100, 1);
        EXPECT_EQ(source.state.progress().bPacketsBuffered, 4u);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
        EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, 4u);
        EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 4u);
        auto beat = source.consumeB(2);
        ASSERT_TRUE(beat);
        EXPECT_EQ(beat->axiId, 7u);
        EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 3u);
        const std::vector<std::pair<uint32_t, AxiResp>> expected = equal_tick ?
            std::vector<std::pair<uint32_t, AxiResp>>{
                {4, AxiResp::Okay}, {3, AxiResp::SlvErr}, {5, AxiResp::Okay}} :
            std::vector<std::pair<uint32_t, AxiResp>>{
                {3, AxiResp::SlvErr}, {4, AxiResp::Okay}, {5, AxiResp::Okay}};
        for (const auto &[id, response] : expected) {
            beat = source.consumeB(2);
            ASSERT_TRUE(beat);
            EXPECT_EQ(beat->axiId, id);
            EXPECT_EQ(beat->resp, response);
        }
        EXPECT_FALSE(source.consumeB(2));
        EXPECT_EQ(source.state.progress().bTransactionsRetired, 4u);
        EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
        EXPECT_EQ(source.state.occupancy().outstandingWrites, 0u);
        EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, 0u);
        EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 0u);
    }
}

TEST(AxiBehaviorTrace, BCopiesMovePendingHeadsAndRestoreRetirementHistory)
{
    BehaviorTrace trace("BCopiesMovePendingHeadsAndRestoreRetirementHistory");
    auto config = sourceConfig({6, 9});
    config.fifoDepths[2] = 1;
    SourceProbe source(trace, "b-copy-original", config);
    const auto blocker = source.issue(true, 7, 1, 0, 2);
    const auto head = source.issue(true, 1, 1, 0, 1);
    const auto other = source.issue(true, 2, 1, 0, 2);
    const auto younger = source.issue(true, 1, 1, 0, 2);
    const auto late = source.issue(true, 3, 1, 0, 1);
    source.receiveB(blocker, 0, 1);
    source.receiveB(head, 5, 1);
    source.receiveB(other, 10, 1);
    const auto first = source.consumeB(2);
    ASSERT_TRUE(first);
    EXPECT_EQ(first->axiId, 7u);
    source.receiveB(younger, 1, 2, AxiResp::SlvErr);
    EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
    EXPECT_EQ(source.state.progress().bPacketsBuffered, 4u);
    EXPECT_EQ(source.state.progress().bTransactionsRetired, 1u);
    EXPECT_EQ(source.state.occupancy().b, 1u);
    EXPECT_EQ(source.state.occupancy().outstandingWrites, 4u);
    EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, 4u);
    EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 3u);
    trace.operation("copyAndMoveBResponses", 2);
    AxiInitiatorState copied = source.state;
    AxiInitiatorState moved = std::move(copied);
    SourceProbe alternate(trace, "b-copy-moved", std::move(moved), 2);
    source.receiveB(late, 2, 3, AxiResp::DecErr);
    alternate.receiveB(late, 20, 3, AxiResp::DecErr);
    const std::vector<std::pair<uint32_t, AxiResp>> original_expected{
        {1, AxiResp::Okay}, {1, AxiResp::SlvErr},
        {3, AxiResp::DecErr}, {2, AxiResp::Okay}};
    const std::vector<std::pair<uint32_t, AxiResp>> moved_expected{
        {1, AxiResp::Okay}, {1, AxiResp::SlvErr},
        {2, AxiResp::Okay}, {3, AxiResp::DecErr}};
    for (SourceProbe *owner : {&source, &alternate}) {
        const auto &expected = owner == &source ? original_expected : moved_expected;
        for (size_t index = 0; index < expected.size(); ++index) {
            const auto beat = owner->consumeB(3);
            ASSERT_TRUE(beat);
            EXPECT_EQ(beat->axiId, expected[index].first);
            EXPECT_EQ(beat->resp, expected[index].second);
            EXPECT_EQ(owner->state.progress().sameIdResponsesBlocked, 0u);
            EXPECT_EQ(owner->state.progress().bPacketsBuffered, 5u);
            EXPECT_EQ(owner->state.progress().bTransactionsRetired, index + 2);
            const size_t remaining = expected.size() - index - 1;
            EXPECT_EQ(owner->state.occupancy().b, remaining ? 1u : 0u);
            EXPECT_EQ(owner->state.occupancy().outstandingWrites, remaining);
            EXPECT_EQ(owner->state.resourceOccupancy().bRobReservedTransactions, remaining);
            EXPECT_EQ(owner->state.resourceOccupancy().bRobBufferedTransactions, remaining);
        }
        EXPECT_FALSE(owner->consumeB(3));
        EXPECT_TRUE(owner->state.finalConsistencyError().empty());
    }
    trace.operation("checkpoint.idleBResponses", 3);
    std::stringstream original_archive;
    std::stringstream moved_archive;
    {
        boost::archive::binary_oarchive archive(original_archive);
        archive << source.state;
    }
    {
        boost::archive::binary_oarchive archive(moved_archive);
        archive << alternate.state;
    }
    EXPECT_EQ(original_archive.str(), moved_archive.str());
    const std::string bytes = original_archive.str();
    TraceRow checkpoint;
    checkpoint.field("bytes", bytesHex(std::vector<uint8_t>(bytes.begin(), bytes.end())));
    trace.record("checkpoint", checkpoint);
    SourceProbe restored(trace, "b-copy-restored", config);
    trace.operation("restore.idleBResponses", 3);
    {
        boost::archive::binary_iarchive archive(original_archive);
        archive >> restored.state;
    }
    trace.source("b-copy-restored", restored.state);
    const auto next = restored.issue(true, 1, 1, 4, 2);
    EXPECT_EQ(next.writeOrdinal, 5u);
    EXPECT_EQ(next.meta.responseSeq, 2u);
    EXPECT_EQ(next.meta.targetSeq, 1u);
    restored.receiveB(next, std::numeric_limits<Tick>::max(), 4);
    const auto beat = restored.consumeB(4);
    ASSERT_TRUE(beat);
    EXPECT_EQ(beat->axiId, 1u);
    EXPECT_EQ(restored.state.progress().bPacketsBuffered, 6u);
    EXPECT_EQ(restored.state.progress().bTransactionsRetired, 6u);
    EXPECT_FALSE(restored.consumeB(4));
    EXPECT_EQ(source.state.progress().bTransactionsRetired, 5u);
    EXPECT_EQ(alternate.state.progress().bTransactionsRetired, 5u);
}

TEST(AxiBehaviorTrace, BRetiredUidFailsBeforeFieldsAndExOkay)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("BRetiredUidFailsBeforeFieldsAndExOkay");
        auto config = sourceConfig({3, 5});
        config.fifoDepths[2] = 1;
        SourceProbe source(trace, "b-retired-invalid", config);
        const auto address = source.issue(true, 1, 1, 0);
        source.receiveB(address, 10, 1);
        ASSERT_TRUE(source.consumeB(1));
        auto invalid = address;
        invalid.meta.axiId = 15;
        ++invalid.meta.targetSeq;
        AxiBPacket query;
        query.meta = invalid.meta;
        query.resp = AxiResp::ExOkay;
        trace.operation("source.canAcceptBPacket.retired", 2);
        const bool accepted = source.state.canAcceptBPacket(query);
        TraceRow eligibility;
        eligibility.field("accepted", accepted);
        trace.record("eligibility", eligibility);
        EXPECT_FALSE(accepted);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            source.receiveB(invalid, 0, 2, AxiResp::ExOkay);
        } catch (...) {
            EXPECT_EQ(source.state.progress().bPacketsBuffered, 1u);
            EXPECT_EQ(source.state.progress().bTransactionsRetired, 1u);
            EXPECT_EQ(source.state.occupancy().b, 0u);
            EXPECT_EQ(source.state.occupancy().outstandingWrites, 0u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, 0u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 0u);
            finishBResponsePanic(trace, source.state, "AXI_PROTOCOL: unknown B txnUid");
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86), "AXI_PROTOCOL: unknown B txnUid");
}

TEST(AxiBehaviorTrace, BFieldsFailBeforeQueuedDuplicateAndExOkay)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("BFieldsFailBeforeQueuedDuplicateAndExOkay");
        auto config = sourceConfig({3, 5});
        config.fifoDepths[2] = 1;
        SourceProbe source(trace, "b-fields-invalid", config);
        const auto address = source.issue(true, 1, 1, 0);
        source.receiveB(address, 10, 1);
        auto invalid = address;
        invalid.meta.axiId = 2;
        AxiBPacket query;
        query.meta = invalid.meta;
        trace.operation("source.canAcceptBPacket.fields", 2);
        const bool accepted = source.state.canAcceptBPacket(query);
        TraceRow eligibility;
        eligibility.field("accepted", accepted);
        trace.record("eligibility", eligibility);
        EXPECT_TRUE(accepted);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            source.receiveB(invalid, 0, 2, AxiResp::ExOkay);
        } catch (...) {
            EXPECT_EQ(source.state.progress().bPacketsBuffered, 1u);
            EXPECT_EQ(source.state.progress().bTransactionsRetired, 0u);
            EXPECT_EQ(source.state.occupancy().b, 1u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, 1u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 1u);
            finishBResponsePanic(trace, source.state, "AXI_PROTOCOL: B fields conflict with AW");
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86), "AXI_PROTOCOL: B fields conflict with AW");
}

TEST(AxiBehaviorTrace, BPartiallyInjectedWriteFailsBeforeExOkay)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("BPartiallyInjectedWriteFailsBeforeExOkay");
        auto config = sourceConfig({3, 5});
        config.fifoDepths[2] = 1;
        SourceProbe source(trace, "b-partial-w", config);
        AxiAddressRequest request;
        request.axiId = 1;
        request.address = 0x100;
        request.beatCount = 2;
        request.size = 3;
        trace.operation("source.tryAcceptAw.partial", 0);
        const bool accepted = source.state.tryAcceptAw(request, 0);
        TraceRow input;
        append(input, request);
        input.field("accepted", accepted);
        trace.record("input", input);
        trace.source("b-partial-w", source.state);
        ASSERT_TRUE(accepted);
        const auto address = source.state.lastAcceptedAw();
        for (uint16_t index = 0; index < 2; ++index) {
            AxiWBeat beat;
            beat.last = index == 1;
            beat.byteStrobe = 0xff;
            beat.functionalData.assign(8, uint8_t(0x40 + index));
            beat.payloadDigest = payloadDigest(beat.functionalData);
            trace.operation("source.tryAcceptW.partial", 0);
            const bool stored = source.state.tryAcceptW(beat, 0);
            TraceRow data;
            data.field("accepted", stored).field("last", beat.last)
                .field("strobe", beat.byteStrobe).field("digest", beat.payloadDigest)
                .field("data", bytesHex(beat.functionalData));
            trace.record("input", data);
            trace.source("b-partial-w", source.state);
            ASSERT_TRUE(stored);
        }
        trace.operation("source.popAwPacket.partial", 0);
        source.state.popAwPacket();
        trace.source("b-partial-w", source.state);
        trace.operation("source.popWPacket.firstOnly", 0);
        source.state.popWPacket();
        trace.source("b-partial-w", source.state);
        ASSERT_EQ(source.state.occupancy().w, 1u);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            source.receiveB(address, 0, 1, AxiResp::ExOkay);
        } catch (...) {
            EXPECT_EQ(source.state.progress().bPacketsBuffered, 0u);
            EXPECT_EQ(source.state.progress().bTransactionsRetired, 0u);
            EXPECT_EQ(source.state.occupancy().b, 0u);
            EXPECT_EQ(source.state.occupancy().w, 1u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, 1u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 0u);
            finishBResponsePanic(trace, source.state,
                "AXI_PROTOCOL: B arrived before all W beats were injected");
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86),
        "AXI_PROTOCOL: B arrived before all W beats were injected");
}

TEST(AxiBehaviorTrace, BQueuedDuplicateFailsBeforeExOkay)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("BQueuedDuplicateFailsBeforeExOkay");
        auto config = sourceConfig({3, 5});
        config.fifoDepths[2] = 1;
        SourceProbe source(trace, "b-duplicate", config);
        const auto address = source.issue(true, 1, 1, 0);
        source.receiveB(address, 10, 1);
        AxiBPacket query;
        query.meta = address.meta;
        trace.operation("source.canAcceptBPacket.duplicate", 2);
        const bool accepted = source.state.canAcceptBPacket(query);
        TraceRow eligibility;
        eligibility.field("accepted", accepted);
        trace.record("eligibility", eligibility);
        EXPECT_TRUE(accepted);
        source.advance(2);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            source.receiveB(address, 0, 2, AxiResp::ExOkay);
        } catch (...) {
            EXPECT_EQ(source.state.progress().bPacketsBuffered, 1u);
            EXPECT_EQ(source.state.progress().bTransactionsRetired, 0u);
            EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
            EXPECT_EQ(source.state.occupancy().b, 1u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, 1u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 1u);
            finishBResponsePanic(trace, source.state, "AXI_PROTOCOL: duplicate B packet");
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86), "AXI_PROTOCOL: duplicate B packet");
}

TEST(AxiBehaviorTrace, BFullFifoStillRejectsFreshExOkay)
{
    ASSERT_EXIT(([] {
        BehaviorTrace trace("BFullFifoStillRejectsFreshExOkay");
        auto config = sourceConfig({3, 5});
        config.fifoDepths[2] = 1;
        SourceProbe source(trace, "b-full-exokay", config);
        const auto blocker = source.issue(true, 7, 1, 0);
        const auto address = source.issue(true, 1, 1, 0);
        source.receiveB(blocker, 10, 1);
        AxiBPacket query;
        query.meta = address.meta;
        trace.operation("source.canAcceptBPacket.exokay", 2);
        const bool accepted = source.state.canAcceptBPacket(query);
        TraceRow eligibility;
        eligibility.field("accepted", accepted);
        trace.record("eligibility", eligibility);
        EXPECT_TRUE(accepted);
        gtestLogOutput.str("");
        gtestLogOutput.clear();
        try {
            source.receiveB(address, 0, 2, AxiResp::ExOkay);
        } catch (...) {
            EXPECT_EQ(source.state.progress().bPacketsBuffered, 1u);
            EXPECT_EQ(source.state.progress().bTransactionsRetired, 0u);
            EXPECT_EQ(source.state.progress().sameIdResponsesBlocked, 0u);
            EXPECT_EQ(source.state.occupancy().b, 1u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobReservedTransactions, 2u);
            EXPECT_EQ(source.state.resourceOccupancy().bRobBufferedTransactions, 1u);
            finishBResponsePanic(trace, source.state,
                "AXI_PROTOCOL: source received forbidden EXOKAY B");
        }
        std::_Exit(88);
    }()), ::testing::ExitedWithCode(86), "AXI_PROTOCOL: source received forbidden EXOKAY B");
}

}

#include "dev/ai_mesh/serving_mesh_executor_test.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_abi;
using namespace gem5::ai_mesh::serving_test;

TEST(ServingMeshExecutor, RejectedPreflightDoesNotOccupyAContext)
{
    Harness harness;
    harness.executor.setQuantumDriver([](uint64_t) {});
    auto invalid = executionRequest();
    invalid.hostBindings.clear();
    EXPECT_FALSE(harness.executor.submit(invalid).admitted);
    EXPECT_TRUE(harness.executor.submit(executionRequest()).admitted);
    EXPECT_TRUE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.dispatch.selectors.empty());
}

TEST(ServingMeshExecutor, InvalidFrozenTableLeavesNoAppendOrSelectedInstance)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    auto request = executionRequest();
    request.metadataCapacityBytes = 0;
    ASSERT_TRUE(harness.admitOnly(request, 100).admitted);
    harness.executor.onQuantumEdge(150);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_EQ(harness.executor.failureReason(1).find("E_AGENT_PROTOCOL_FATAL:"), 0u);
    EXPECT_TRUE(harness.dispatch.selectors.empty());
    EXPECT_TRUE(harness.dispatch.instance_bindings.empty());
    EXPECT_EQ(harness.dispatch.begins, 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.fatalReason());
    EXPECT_TRUE(harness.manager.validate().empty());
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_EQ(sink.last.infrastructureFailure, agent_abi::E_AGENT_PROTOCOL_FATAL);
}

TEST(ServingMeshExecutor, SameEdgeCancelCannotUndoTheFrozenInstance)
{
    for (const bool cancel_first : {false, true}) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([] { return 100; });
        harness.executor.setQuantumDriver([](uint64_t) {});
        harness.dispatch.on_start = {};
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        if (cancel_first)
            harness.executor.requestCancel(1, 150);
        harness.executor.onQuantumEdge(150);
        if (!cancel_first)
            harness.executor.requestCancel(1, 150);
        harness.executor.onQuantumEdge(151);
        ASSERT_EQ(harness.dispatch.selectors.size(), 1u);
        EXPECT_EQ(harness.dispatch.cancels, 0u);
        EXPECT_EQ(harness.dispatch.fails, 0u);
        EXPECT_EQ(sink.completions, 0u);
        EXPECT_TRUE(harness.manager.hasPin(1));
        harness.executor.onInstanceStarted(1, 1, 1, 160);
        const auto store = kvStoreCommands(harness.program).front();
        harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                       1, 1, 1, 170);
        harness.executor.onDmaTerminal(store.descriptor_id, store.command_id,
                                       128, 0, 1, 1, 1, 180, {{0, 0, 128}});
        harness.executor.onInstanceSettled(1, 1, 1, false, 190);
        harness.executor.onQuantumEdge(191);
        ASSERT_EQ(sink.completions, 1u);
        EXPECT_EQ(sink.last.outcome, NpuExecutionOutcome::Cancelled);
        EXPECT_EQ(harness.dispatch.selectors.size(), 1u);
        EXPECT_FALSE(harness.manager.fatalReason());
    }
}

TEST(ServingMeshExecutor, CancelErrorWinnerUsesTheRealErrorLatchEdge)
{
    for (const auto &[cancel_tick, cancel_first] :
            {std::pair<uint64_t, bool>{169, true}, {170, true},
             {170, false}, {171, false}}) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([] { return 100; });
        harness.executor.setQuantumDriver([](uint64_t) {});
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        harness.executor.onQuantumEdge(150);
        const auto store = kvStoreCommands(harness.program).front();
        harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                       1, 1, 1, 160);
        if (cancel_first)
            harness.executor.requestCancel(1, cancel_tick);
        BatchFailureRecord failure;
        failure.batch_id = failure.program_instance_id = 1;
        failure.first.tick = 170;
        failure.first.code = agent_abi::E_AXI_RESPONSE;
        if (cancel_tick < failure.first.tick)
            failure.cancel_tombstone_snapshot.push_back(1);
        harness.executor.onInstanceFailure(failure);
        if (!cancel_first)
            harness.executor.requestCancel(1, cancel_tick);
        harness.executor.onDmaTerminal(store.descriptor_id, store.command_id,
                                       64, 7, 1, 1, 1, 200, {{0, 0, 64}});
        EXPECT_EQ(sink.completions, 0u);
        harness.settleFailedInstance(1, 1, 1, 210);
        ASSERT_EQ(sink.completions, 1u);
        EXPECT_EQ(sink.last.outcome, cancel_tick < 170 ?
                  NpuExecutionOutcome::Cancelled : NpuExecutionOutcome::Error);
        ASSERT_TRUE(sink.last.kvTerminal);
        EXPECT_EQ(sink.last.kvTerminal->status, cancel_tick < 170 ?
                  KvTerminalStatus::Cancelled : KvTerminalStatus::Error);
        EXPECT_EQ(harness.dispatch.selectors.size(), 1u);
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.fatalReason());
    }
}

TEST(ServingMeshExecutor, GlobalFailureSnapshotOwnsTerminalClassification)
{
    for (const auto &[cancel_tick, cancel_first] :
            {std::pair<uint64_t, bool>{169, true}, {170, true},
             {170, false}, {171, false}}) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([] { return 100; });
        harness.executor.setQuantumDriver([](uint64_t) {});
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        harness.executor.onQuantumEdge(150);
        const auto store = kvStoreCommands(harness.program).front();
        harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                       1, 1, 1, 160);
        if (cancel_first)
            harness.executor.requestCancel(1, cancel_tick);
        BatchFailureRecord failure;
        failure.batch_id = failure.program_instance_id = 1;
        failure.first.tick = 170;
        failure.first.code = agent_abi::E_AXI_RESPONSE;
        if (cancel_tick >= failure.first.tick)
            failure.cancel_tombstone_snapshot.push_back(1);
        harness.executor.onInstanceFailure(failure);
        if (!cancel_first)
            harness.executor.requestCancel(1, cancel_tick);
        harness.executor.onDmaTerminal(store.descriptor_id, store.command_id,
                                       64, 7, 1, 1, 1, 200, {{0, 0, 64}});
        EXPECT_EQ(sink.completions, 0u);
        harness.settleFailedInstance(1, 1, 1, 210);
        ASSERT_EQ(sink.completions, 1u);
        EXPECT_EQ(sink.last.outcome, cancel_tick >= 170 ?
                  NpuExecutionOutcome::Cancelled : NpuExecutionOutcome::Error);
        ASSERT_TRUE(sink.last.kvTerminal);
        EXPECT_EQ(sink.last.kvTerminal->status, cancel_tick >= 170 ?
                  KvTerminalStatus::Cancelled : KvTerminalStatus::Error);
        EXPECT_EQ(harness.dispatch.selectors.size(), 1u);
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.fatalReason());
    }
}

TEST(ServingMeshExecutor, CancelIntentWaitsForTheFrozenInstanceAndKvDrain)
{
    for (const bool kv_first : {false, true}) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([] { return 100; });
        harness.executor.setQuantumDriver([](uint64_t) {});
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        harness.executor.onQuantumEdge(150);
        const auto store = kvStoreCommands(harness.program).front();
        harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                       1, 1, 1, 160);
        EXPECT_EQ(harness.executor.requestCancel(1, 170),
                  ServingMeshExecutor::CancelResult::Accepted);
        harness.executor.onQuantumEdge(171);
        EXPECT_EQ(sink.completions, 0u);
        EXPECT_EQ(harness.dispatch.fails, 0u);
        EXPECT_EQ(harness.dispatch.cancels, 0u);
        EXPECT_TRUE(harness.manager.hasPin(1));
        const auto terminal = [&](uint64_t tick) {
            harness.executor.onDmaTerminal(store.descriptor_id, store.command_id,
                                           128, 0, 1, 1, 1, tick, {{0, 0, 128}});
        };
        if (kv_first)
            terminal(180);
        else
            harness.executor.onInstanceSettled(1, 1, 1, false, 180);
        harness.executor.onQuantumEdge(181);
        EXPECT_EQ(sink.completions, 0u);
        if (kv_first)
            harness.executor.onInstanceSettled(1, 1, 1, false, 190);
        else
            terminal(190);
        EXPECT_EQ(sink.completions, 0u);
        harness.executor.onQuantumEdge(191);
        ASSERT_EQ(sink.completions, 1u);
        EXPECT_EQ(sink.last.outcome, NpuExecutionOutcome::Cancelled);
        ASSERT_TRUE(sink.last.kvTerminal);
        EXPECT_EQ(sink.last.kvTerminal->cached_tokens, 8u);
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.hasClaim(1));
        EXPECT_FALSE(harness.executor.failed(1));
        EXPECT_EQ(harness.dispatch.selectors.size(), 1u);
        EXPECT_EQ(harness.dispatch.finishes, 0u);
        harness.executor.finishServingRun();
        EXPECT_EQ(harness.dispatch.finishes, 1u);
        EXPECT_EQ(harness.executor.requestCancel(1, 192),
                  ServingMeshExecutor::CancelResult::AlreadyTerminal);
        EXPECT_FALSE(harness.manager.fatalReason());
    }
}

TEST(ServingMeshExecutor, CancelIntentBeforeFreezeIsVisibleOnlyOnTheNextEdge)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([] { return 100; });
    harness.executor.setQuantumDriver([](uint64_t) {});
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    EXPECT_EQ(harness.executor.requestCancel(999, 100),
              ServingMeshExecutor::CancelResult::NotFound);
    EXPECT_EQ(harness.executor.requestCancel(1, 100),
              ServingMeshExecutor::CancelResult::Accepted);
    EXPECT_EQ(sink.completions, 0u);
    harness.executor.onQuantumEdge(101);
    ASSERT_EQ(sink.completions, 1u);
    EXPECT_EQ(sink.last.outcome, NpuExecutionOutcome::Cancelled);
    EXPECT_TRUE(harness.dispatch.selectors.empty());
    EXPECT_EQ(harness.dispatch.fails, 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
}

TEST(ServingMeshExecutor, CancelledOutcomeIsSealedBeforeCallbackAndStaysImmutable)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.setQuantumDriver([](uint64_t) {});
    harness.executor.setTickSource([] { return 100; });
    const auto request = executionRequest();
    EXPECT_EQ(harness.executor.completionFor(request).outcome,
              NpuExecutionOutcome::Pending);
    harness.executor.attachSink(&sink);
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    EXPECT_EQ(harness.executor.completionFor(request).outcome,
              NpuExecutionOutcome::Pending);
    sink.on_complete = [&](const NpuExecutionCompletion &completion) {
        EXPECT_EQ(completion.outcome, NpuExecutionOutcome::Cancelled);
        EXPECT_EQ(harness.executor.completionFor(request).outcome,
                  NpuExecutionOutcome::Cancelled);
        EXPECT_FALSE(harness.executor.failed(1));
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.hasClaim(1));
        harness.executor.onQuantumEdge(151);
        EXPECT_EQ(harness.dispatch.begins, 0u);
    };
    std::string reason;
    ASSERT_TRUE(harness.executor.abortBeforeStart(1, 150, reason)) << reason;
    EXPECT_TRUE(reason.empty());
    EXPECT_EQ(sink.completions, 1u);
    const auto store = kvStoreCommands(harness.program).front();
    harness.executor.onInstanceStarted(999, 1, 1, 160);
    harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                   999, 1, 1, 161);
    harness.executor.onDmaTerminal(store.descriptor_id, store.command_id,
                                   64, 7, 999, 1, 1, 162, {{0, 0, 64}});
    harness.executor.onInstanceSettled(999, 1, 1, true, 163);
    EXPECT_EQ(harness.executor.staleInstanceFacts(), 4u);
    EXPECT_EQ(harness.executor.completionFor(request).outcome,
              NpuExecutionOutcome::Cancelled);
    EXPECT_EQ(harness.executor.terminalTick(1), 150u);
    EXPECT_FALSE(harness.executor.failed(1));
    EXPECT_TRUE(harness.executor.failureReason(1).empty());
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_EQ(harness.dispatch.fails, 0u);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, ReleaseOnTheOwnerTerminalEdgePreservesTheSnapshot)
{
    for (const std::string mode : {"success", "cancel", "error"}) {
        const bool cancel = mode == "cancel";
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        uint32_t releases = 0;
        harness.executor.setReleaseOutcomeSink([&](const KvEdgeResult &edge) {
            ASSERT_EQ(edge.releases.size(), 1u);
            EXPECT_EQ(edge.releases.at(99), KvReleaseOutcome::Success);
            EXPECT_TRUE(harness.executor.kvTerminal(1).has_value());
            EXPECT_EQ(sink.completions, 0u);
            releases++;
        });
        if (cancel)
            harness.dispatch.on_start = {};
        const auto request = executionRequest();
        ASSERT_TRUE(harness.executor.submit(request).admitted);
        KvEdgeInputs release;
        release.tick = 160;
        release.releases.push_back({99, request.sessionId, request.kvHandle,
                                    request.generation});
        const auto waiting = harness.manager.commitEdge(release);
        ASSERT_FALSE(waiting.fatal.has_value());
        ASSERT_TRUE(harness.manager.hasReleaseWaiter(99));
        EXPECT_TRUE(waiting.releases.empty());
        if (cancel) {
            std::string reason;
            ASSERT_TRUE(harness.executor.abortBeforeStart(1, 200, reason)) << reason;
        } else if (mode == "error") {
            const auto store = kvStoreCommands(harness.program).front();
            harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                           1, 1, 1, 200);
            harness.executor.onDmaTerminal(store.descriptor_id, store.command_id,
                                           64, 7, 1, 1, 1, 210, {{0, 0, 64}});
            EXPECT_EQ(sink.completions, 0u);
            EXPECT_EQ(releases, 0u);
            BatchFailureRecord failure;
            failure.batch_id = failure.program_instance_id = 1;
            failure.first = {210, {}, agent_abi::E_AXI_RESPONSE};
            harness.executor.onInstanceFailure(failure);
            harness.settleFailedInstance(1, 1, 1, 220);
        } else {
            driveOneGenerate(harness, kvStoreCommands(harness.program));
        }
        EXPECT_FALSE(harness.manager.hasReleaseWaiter(99));
        EXPECT_EQ(releases, 1u);
        EXPECT_EQ(harness.manager.findRecord(request.sessionId, request.kvHandle),
                  nullptr);
        ASSERT_EQ(sink.completions, 1u);
        EXPECT_EQ(sink.last.succeeded(), mode == "success");
        EXPECT_EQ(sink.last.outcome, cancel ? NpuExecutionOutcome::Cancelled
            : mode == "error" ? NpuExecutionOutcome::Error : NpuExecutionOutcome::Success);
        ASSERT_TRUE(sink.last.kvTerminal.has_value());
        EXPECT_EQ(sink.last.kvTerminal->status, cancel ? KvTerminalStatus::Cancelled
            : mode == "error" ? KvTerminalStatus::Error : KvTerminalStatus::Success);
        EXPECT_EQ(sink.last.kvTerminal->generation, 1u);
        EXPECT_EQ(sink.last.kvTerminal->cached_tokens, cancel ? 0u
            : mode == "error" ? 4u : 10u);
        EXPECT_EQ(sink.last.terminalTick, cancel ? 200u
            : mode == "error" ? 220u : 521u);
        EXPECT_TRUE(harness.manager.validate().empty());
        EXPECT_FALSE(harness.manager.fatalReason().has_value())
            << harness.manager.fatalReason().value_or("") << " cancel=" << cancel;
    }
}

TEST(ServingMeshExecutor, AdmissionQueueProgressesPastAnExternallyRejectedHead)
{
    Harness harness;
    const auto handoffs = harness.occupyKvSlots();
    for (uint64_t request_id : {201, 202}) {
        KvEdgeInputs queued;
        queued.tick = request_id - 190;
        KvAdmissionIntent intent;
        intent.request_id = request_id;
        intent.session_id = intent.kv_handle = 201;
        intent.generation = 1;
        intent.required_tokens_after_round = 10;
        queued.admissions.push_back(intent);
        ASSERT_FALSE(harness.manager.commitEdge(queued).fatal.has_value());
        ASSERT_TRUE(harness.manager.hasAdmissionWaiter(request_id));
    }
    harness.executor.setTickSource([] { return 100; });
    std::vector<uint64_t> wakes;
    harness.executor.setQuantumDriver([&](uint64_t tick) { wakes.push_back(tick); });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    harness.executor.onQuantumEdge(101);
    EXPECT_FALSE(harness.manager.hasAdmissionWaiter(1));
    KvEdgeInputs release;
    release.tick = 200;
    release.owner_terminals.push_back(
        {101, KvTerminalStatus::Cancelled, handoffs.at(101), true});
    release.releases.push_back({301, 101, 101, 1});
    ASSERT_FALSE(harness.manager.commitEdge(release).fatal.has_value());
    EXPECT_EQ(wakes.back(), 201u);
    harness.executor.onQuantumEdge(201);
    EXPECT_FALSE(harness.manager.hasAdmissionWaiter(202));
    EXPECT_TRUE(harness.manager.hasAdmissionWaiter(1));
    EXPECT_EQ(wakes.back(), 202u);
    harness.executor.onQuantumEdge(202);
    EXPECT_TRUE(harness.manager.hasClaim(1));
    EXPECT_FALSE(harness.manager.hasPin(1));
    const auto scheduled = wakes.size();
    harness.executor.onQuantumEdge(203);
    EXPECT_EQ(wakes.size(), scheduled);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, AdmissionWaitsForARealPinAndWakesOnResourceRelease)
{
    for (const bool record_table_full : {false, true}) {
        auto limits = capacity();
        limits.record_entries = record_table_full ? 2 : 4;
        Harness harness(loadFixture(
            "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb"),
            geometry(), limits);
        const auto handoffs = harness.occupyKvSlots();
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([] { return 100; });
        std::vector<uint64_t> wakes;
        harness.executor.setQuantumDriver([&](uint64_t tick) {
            wakes.push_back(tick);
        });
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        EXPECT_EQ(harness.manager.hasClaim(1), !record_table_full);
        EXPECT_EQ(sink.admissions, record_table_full ? 0u : 1u);
        EXPECT_FALSE(harness.manager.hasPin(1));
        harness.executor.onQuantumEdge(101);
        EXPECT_EQ(harness.dispatch.begins, 0u);
        EXPECT_TRUE(harness.dispatch.selectors.empty());
        EXPECT_FALSE(harness.executor.failed(1));
        ASSERT_FALSE(harness.manager.fatalReason().has_value());
        const auto waiting = harness.manager.saveLive();
        const auto waiter = std::find_if(waiting.waiters.begin(), waiting.waiters.end(),
            [](const auto &item) { return item.request_id == 1; });
        ASSERT_NE(waiter, waiting.waiters.end());
        EXPECT_EQ(waiter->ready_tick, 100u);
        const auto wake_count = wakes.size();
        KvEdgeInputs release;
        release.tick = 200;
        release.owner_terminals.push_back(
            {101, KvTerminalStatus::Cancelled, handoffs.at(101), true});
        release.releases.push_back({301, 101, 101, 1});
        ASSERT_FALSE(harness.manager.commitEdge(release).fatal.has_value());
        ASSERT_TRUE(harness.manager.hasPin(1));
        EXPECT_EQ(sink.admissions, 1u);
        ASSERT_GT(wakes.size(), wake_count);
        EXPECT_EQ(wakes.back(), 201u);
        harness.executor.onQuantumEdge(200);
        EXPECT_EQ(harness.dispatch.begins, 0u);
        harness.executor.onQuantumEdge(201);
        EXPECT_EQ(harness.dispatch.begins, 1u);
        harness.executor.onQuantumEdge(202);
        EXPECT_EQ(harness.dispatch.begins, 1u);
        EXPECT_FALSE(harness.executor.failed(1));
        EXPECT_TRUE(harness.manager.validate().empty());
    }
}

TEST(ServingMeshExecutor, CancellingAdmissionCannotBeRevivedByAResourceRelease)
{
    for (const bool record_table_full : {false, true}) {
        auto limits = capacity();
        limits.record_entries = record_table_full ? 2 : 4;
        Harness harness(loadFixture(
            "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb"),
            geometry(), limits);
        const auto handoffs = harness.occupyKvSlots();
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([] { return 100; });
        harness.executor.setQuantumDriver([](uint64_t) {});
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        harness.executor.onQuantumEdge(101);
        std::string reason;
        EXPECT_TRUE(harness.executor.abortBeforeStart(1, 150, reason)) << reason;
        EXPECT_FALSE(harness.executor.failed(1));
        EXPECT_TRUE(harness.executor.failureReason(1).empty());
        EXPECT_EQ(sink.last.outcome, NpuExecutionOutcome::Cancelled);
        EXPECT_EQ(sink.last.terminalTick, 150u);
        EXPECT_EQ(sink.last.outputBytes, 0u);
        EXPECT_FALSE(harness.manager.hasAdmissionWaiter(1));
        EXPECT_FALSE(harness.manager.hasClaim(1));
        EXPECT_FALSE(harness.manager.hasPin(1));
        ASSERT_EQ(sink.completions, 1u);
        if (record_table_full) {
            EXPECT_FALSE(sink.last.kvTerminal.has_value());
        } else {
            ASSERT_TRUE(sink.last.kvTerminal.has_value());
            EXPECT_EQ(sink.last.kvTerminal->status, KvTerminalStatus::Cancelled);
            EXPECT_EQ(sink.last.terminalTick, 150u);
        }
        EXPECT_FALSE(harness.executor.abortBeforeStart(1, 151, reason));
        KvEdgeInputs release;
        release.tick = 200;
        release.owner_terminals.push_back(
            {101, KvTerminalStatus::Cancelled, handoffs.at(101), true});
        release.releases.push_back({301, 101, 101, 1});
        ASSERT_FALSE(harness.manager.commitEdge(release).fatal.has_value());
        harness.executor.onQuantumEdge(201);
        EXPECT_EQ(sink.completions, 1u);
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.hasAdmissionWaiter(1));
        EXPECT_TRUE(harness.dispatch.selectors.empty());
        EXPECT_EQ(harness.dispatch.cancels, 0u);
        EXPECT_EQ(harness.dispatch.fails, 0u);
        EXPECT_TRUE(harness.manager.validate().empty());
    }
}

TEST(ServingMeshExecutor, AdmissionBackpressureRetainsAgeAndRoutesDeferredRejection)
{
    for (const bool conflicting_session : {false, true}) {
        auto limits = capacity();
        limits.record_entries = 2;
        limits.admission_wait_entries = conflicting_session ? 2 : 1;
        Harness harness(loadFixture(
            "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb"),
            geometry(), limits);
        const auto handoffs = harness.occupyKvSlots();
        KvEdgeInputs queued;
        queued.tick = 10;
        KvAdmissionIntent blocker;
        blocker.request_id = 201;
        blocker.session_id = blocker.kv_handle = conflicting_session ? 11 : 201;
        blocker.generation = 1;
        blocker.required_tokens_after_round = 10;
        queued.admissions.push_back(blocker);
        ASSERT_FALSE(harness.manager.commitEdge(queued).fatal.has_value());
        ASSERT_TRUE(harness.manager.hasAdmissionWaiter(201));
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([] { return 100; });
        std::vector<uint64_t> wakes;
        harness.executor.setQuantumDriver([&](uint64_t tick) { wakes.push_back(tick); });
        auto request = executionRequest();
        request.deadlineTick = 900;
        request.qos = 7;
        ASSERT_TRUE(harness.executor.submit(request).admitted);
        EXPECT_EQ(harness.manager.hasAdmissionWaiter(1), conflicting_session);
        EXPECT_EQ(sink.admissions, 0u);
        harness.executor.onQuantumEdge(101);
        EXPECT_EQ(wakes.size(), 1u);
        KvEdgeInputs release;
        release.tick = 200;
        release.owner_terminals.push_back(
            {101, KvTerminalStatus::Cancelled, handoffs.at(101), true});
        release.releases.push_back({301, 101, 101, 1});
        ASSERT_FALSE(harness.manager.commitEdge(release).fatal.has_value());
        EXPECT_TRUE(harness.manager.hasPin(201));
        EXPECT_EQ(wakes.back(), 201u);
        if (conflicting_session) {
            KvEdgeInputs external;
            external.tick = 201;
            ASSERT_FALSE(harness.manager.commitEdge(external).fatal.has_value());
            EXPECT_TRUE(harness.executor.failed(1));
            EXPECT_EQ(sink.completions, 1u);
            EXPECT_FALSE(sink.last.succeeded());
            EXPECT_EQ(sink.last.admissionRejectDetail, agent_abi::E_SESSION_EXISTS);
            EXPECT_FALSE(sink.last.kvTerminal.has_value());
            EXPECT_EQ(sink.admissions, 0u);
            EXPECT_FALSE(harness.manager.hasAdmissionWaiter(1));
            EXPECT_TRUE(harness.dispatch.selectors.empty());
        } else {
            harness.executor.onQuantumEdge(201);
            const auto state = harness.manager.saveLive();
            const auto waiter = std::find_if(state.waiters.begin(), state.waiters.end(),
                [](const auto &item) { return item.request_id == 1; });
            ASSERT_NE(waiter, state.waiters.end());
            EXPECT_EQ(waiter->ready_tick, 100u);
            EXPECT_EQ(waiter->deadline_or_max, 900u);
            EXPECT_EQ(waiter->qos, 7u);
            release.tick = 300;
            release.owner_terminals = {
                {102, KvTerminalStatus::Cancelled, handoffs.at(102), true}};
            release.releases = {{302, 102, 102, 1}};
            ASSERT_FALSE(harness.manager.commitEdge(release).fatal.has_value());
            EXPECT_EQ(wakes.back(), 301u);
            EXPECT_EQ(sink.admissions, 1u);
            harness.executor.onQuantumEdge(301);
            EXPECT_EQ(harness.dispatch.begins, 1u);
            EXPECT_FALSE(harness.executor.failed(1));
        }
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
        EXPECT_TRUE(harness.manager.validate().empty());
    }
}

TEST(ServingMeshExecutor, SameEdgeCancelAndFreezeBindTheSameTombstoneOwner)
{
    for (bool cancel_first : {false, true}) {
        Harness harness;
        harness.executor.setTickSource([] { return 100; });
        harness.executor.setQuantumDriver([](uint64_t) {});
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        if (cancel_first) {
            ASSERT_EQ(harness.executor.requestCancel(1, 150), ServingMeshExecutor::CancelResult::Accepted);
        }
        harness.executor.onQuantumEdge(150);
        if (!cancel_first) {
            ASSERT_EQ(harness.executor.requestCancel(1, 150), ServingMeshExecutor::CancelResult::Accepted);
        }
        ASSERT_EQ(harness.dispatch.selectors.size(), 1u);
        ASSERT_EQ(harness.dispatch.member_cancels.size(), 1u) << cancel_first;
        EXPECT_EQ(harness.dispatch.member_cancels.front(), std::make_tuple(1ull, 1ull, 150ull));
    }
}

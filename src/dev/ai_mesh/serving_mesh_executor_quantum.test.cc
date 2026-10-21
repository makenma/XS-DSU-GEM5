#include "dev/ai_mesh/serving_mesh_executor_test.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_abi;
using namespace gem5::ai_mesh::serving_test;

TEST(ServingMeshExecutor, CompletedContextsRetireAndReuseTheirCapacity)
{
    auto limits = capacity();
    limits.record_entries = 32;
    Harness harness(loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb"),
                    geometry(), limits);
    harness.executor.setQuantumDriver([](uint64_t) {});
    for (uint64_t id = 1; id <= 16; ++id) {
        const uint64_t tick = 100 + id * 10;
        harness.executor.setTickSource([tick] { return tick; });
        auto request = executionRequest();
        request.requestId = id;
        request.sessionId = request.kvHandle = 11 + id;
        ASSERT_TRUE(harness.executor.submit(request).admitted) << id;
        EXPECT_EQ(harness.executor.requestCancel(id, tick + 1),
                  ServingMeshExecutor::CancelResult::Accepted);
        harness.executor.onQuantumEdge(tick + 2);
        EXPECT_EQ(harness.executor.completionFor(request).outcome,
                  NpuExecutionOutcome::Cancelled);
        harness.executor.retireCompletedRequest(id);
        EXPECT_EQ(harness.executor.completionFor(request).outcome,
                  NpuExecutionOutcome::Pending);
        EXPECT_FALSE(harness.manager.hasPin(id));
        EXPECT_FALSE(harness.manager.fatalReason());
    }
    EXPECT_TRUE(harness.dispatch.selectors.empty());
}

TEST(ServingMeshExecutor, QuantumSelectsAcrossAdmittedRequestsAndCancelsOnlyTheReadyOwner)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([] { return 100; });
    harness.executor.setQuantumDriver([](uint64_t) {});
    auto first = executionRequest();
    auto second = executionRequest(0x100400000);
    second.requestId = 2;
    second.sessionId = second.kvHandle = 12;
    second.inputAddress += 4096;
    second.metadataAddress = 0x100600000;
    second.qos = 9;
    for (auto &binding : second.hostBindings)
        if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
            binding.address = second.inputAddress;
    ASSERT_TRUE(harness.executor.submit(first).admitted);
    ASSERT_TRUE(harness.executor.submit(second).admitted);
    harness.dispatch.on_start = [&] {
        const auto &binding = harness.dispatch.instance_bindings.back();
        harness.executor.onInstanceStarted(harness.currentInstance(),
            binding.requestId(), binding.requestGeneration(), 150);
    };
    harness.executor.onQuantumEdge(150);
    ASSERT_EQ(harness.dispatch.instance_bindings.size(), 1u);
    EXPECT_EQ(harness.dispatch.instance_bindings.back().requestId(), 2u);
    EXPECT_TRUE(harness.executor.phaseFacts(1).empty());
    EXPECT_EQ(harness.executor.phaseFacts(2).size(), 1u);
    EXPECT_TRUE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.hasPin(2));
    EXPECT_EQ(harness.executor.requestCancel(1, 160),
              ServingMeshExecutor::CancelResult::Accepted);
    harness.executor.onQuantumEdge(161);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.hasPin(2));
    EXPECT_EQ(harness.dispatch.cancels, 0u);
    EXPECT_EQ(harness.dispatch.fails, 0u);
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_EQ(sink.last.outcome, NpuExecutionOutcome::Cancelled);
    EXPECT_FALSE(harness.manager.fatalReason());
}

TEST(ServingMeshExecutor, SelectorsAreInstalledOnlyByTheWinningQuantum)
{
    Harness harness;
    harness.executor.setTickSource([] { return 100; });
    harness.executor.setQuantumDriver([](uint64_t) {});
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    EXPECT_TRUE(harness.dispatch.selectors.empty());
    EXPECT_TRUE(harness.dispatch.fills.empty());
    EXPECT_EQ(harness.dispatch.begins, 0u);
    harness.executor.onQuantumEdge(100);
    EXPECT_TRUE(harness.dispatch.selectors.empty());
    harness.executor.onQuantumEdge(101);
    ASSERT_EQ(harness.dispatch.selectors.size(), 1u);
    EXPECT_EQ(harness.dispatch.selectors.front(), std::make_pair(1u, 1u));
    EXPECT_FALSE(harness.dispatch.fills.empty());
    EXPECT_EQ(harness.dispatch.begins, 1u);
}

TEST(ServingMeshExecutor, InstanceIdentityComesFromDispatcherSelection)
{
    Harness harness;
    harness.dispatch.generation_counter = 40;
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    ASSERT_FALSE(harness.executor.failed(1));
    ASSERT_EQ(harness.currentInstance(), 41u);
    const auto stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    const auto &store = stores.front();
    harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                   41, 1, 1, 200);
    harness.executor.onDmaTerminal(store.descriptor_id, store.command_id,
                                   128, 0, 41, 1, 1, 210, {{0, 0, 128}});
    harness.executor.onInstanceSettled(41, 1, 1, false, 220);
    ASSERT_FALSE(harness.executor.failed(1));
    EXPECT_EQ(harness.executor.phasesCommitted(1), 1u);
    EXPECT_EQ(harness.currentInstance(), 42u);
    const auto accepted = harness.executor.acceptedKvDescriptors(1);
    const auto stale = harness.executor.staleInstanceFacts();
    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id,
                                   41, 1, 1, 230);
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), accepted);
    EXPECT_EQ(harness.executor.staleInstanceFacts(), stale + 1);
}

TEST(ServingMeshExecutor, FailedReadyPhaseDoesNotWaitForANeverStartedInstance)
{
    for (const bool selected : {false, true}) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([] { return 100; });
        harness.executor.setQuantumDriver([](uint64_t) {});
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        harness.executor.onQuantumEdge(101);
        const auto stores = kvStoreCommands(harness.program);
        const auto &store = stores.front();
        harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                       1, 1, 1, 200);
        harness.executor.onDmaTerminal(store.descriptor_id, store.command_id,
                                       128, 0, 1, 1, 1, 210, {{0, 0, 128}});
        harness.executor.onInstanceSettled(1, 1, 1, false, 220);
        ASSERT_EQ(harness.executor.phasesCommitted(1), 0u);
        harness.executor.onQuantumEdge(221);
        ASSERT_EQ(harness.executor.phasesCommitted(1), 1u);
        ASSERT_EQ(harness.dispatch.selectors.size(), 1u);
        if (selected) {
            harness.dispatch.on_start = {};
            harness.executor.onQuantumEdge(222);
            ASSERT_EQ(harness.dispatch.selectors.size(), 2u);
        }
        const auto selected_count = harness.dispatch.selectors.size();
        harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id,
                                       999, 1, 1, 230);
        EXPECT_TRUE(harness.executor.failed(1));
        EXPECT_EQ(harness.dispatch.fails, 0u);
        EXPECT_EQ(harness.dispatch.cancels, selected ? 1u : 0u);
        EXPECT_EQ(sink.completions, 1u);
        EXPECT_FALSE(sink.last.succeeded());
        EXPECT_TRUE(sink.last.kvTerminal.has_value());
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.hasClaim(1));
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
        harness.executor.onQuantumEdge(240);
        EXPECT_EQ(harness.dispatch.selectors.size(), selected_count);
        EXPECT_EQ(sink.completions, 1u);
    }
}

namespace
{

void drivePhase(Harness &harness,
                const std::vector<KvStore> &stores, uint32_t phase,
                uint64_t tick)
{
    auto &executor = harness.executor;
    harness.drainOutput(tick);
    if (phase <= stores.size()) {
        const KvStore &store = stores.at(phase - 1);
        executor.onDmaAccepted(store.descriptor_id, store.command_id, phase,
                               1, 1, tick);
        executor.onDmaTerminal(
            store.descriptor_id, store.command_id,
            phase == 1 ? 128 : 16, 0, phase, 1, 1, tick + 10,
            std::vector<DmaCommittedSegment>{
                {0, 0, uint64_t(phase == 1 ? 128 : 16)}});
    }
    executor.onInstanceSettled(phase, 1, 1, false, tick + 20);
}

}

TEST(ServingMeshExecutor, SchedulerProjectionUsesPhaseMemberTokens)
{
    Harness harness;
    NpuExecutionRequest request = executionRequest();
    SchedulerLimits limits;
    limits.maxActiveSequences = 4;
    limits.maxBatchTokens = 8;
    limits.maxPrefillTokensPerBatch = 8;
    limits.maxDecodeSequencesPerBatch = 4;
    harness.executor.setSchedulerLimits(limits);
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    drivePhase(harness, stores, 1, 200);
    limits.maxBatchTokens = 2;
    limits.maxPrefillTokensPerBatch = 2;
    harness.executor.setSchedulerLimits(limits);
    drivePhase(harness, stores, 2, 400);
    drivePhase(harness, stores, 3, 600);
    drivePhase(harness, stores, 4, 800);
    EXPECT_FALSE(harness.executor.failed(1));
    EXPECT_EQ(harness.executor.phasesCommitted(1), 4u);
    EXPECT_TRUE(harness.executor.finished(1));
}

TEST(ServingMeshExecutor, SchedulerCountsUseExactProfileGeometry)
{
    const std::string fixture = "tests/gem5/ai_mesh/fixtures/gate6/serving_fullview_two_tokens.mshb";
    Harness harness(fixture);
    ASSERT_TRUE(std::any_of(harness.program.transport.agent_instance_profiles.begin(),
        harness.program.transport.agent_instance_profiles.end(), [](const auto &row) {
            return row.member_count == 2 && row.phase == kPhaseDECODE;
        }));
    NpuExecutionRequest request = executionRequest(0x100300000, fixture);
    SchedulerLimits limits;
    limits.maxActiveSequences = 1;
    limits.maxBatchTokens = 64;
    limits.maxPrefillTokensPerBatch = 64;
    limits.maxDecodeSequencesPerBatch = 4;
    limits.batchWaitCycles = 1000;
    harness.executor.setSchedulerLimits(limits);
    const NpuAdmission admission = harness.executor.submit(request);
    ASSERT_TRUE(admission.admitted) << harness.executor.failureReason(1)
                                    << " detail=" << admission.rejectDetail;
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    for (uint32_t phase = 1; phase <= 4; ++phase)
        drivePhase(harness, stores, phase, 200 + 200 * phase);
    EXPECT_FALSE(harness.executor.failed(1));
    EXPECT_EQ(harness.executor.phasesCommitted(1), 4u);
    EXPECT_TRUE(harness.executor.finished(1));
}

TEST(ServingMeshExecutor, QuantumExcludesSameEdgeReadyEntries)
{
    Harness harness;
    harness.executor.setTickSource([]() { return 100; });
    std::vector<uint64_t> requested;
    harness.executor.setQuantumDriver(
        [&requested](uint64_t tick) { requested.push_back(tick); });
    NpuExecutionRequest request = executionRequest();
    request.deadlineTick = 1000;
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    ASSERT_EQ(requested, std::vector<uint64_t>({101u}));
    harness.executor.onQuantumEdge(100);
    EXPECT_EQ(harness.dispatch.begins, 0u);
    harness.executor.onQuantumEdge(101);
    EXPECT_EQ(harness.dispatch.begins, 1u);
}

TEST(ServingMeshExecutor, RunningPhaseCannotBeSelectedByAnotherQuantum)
{
    Harness harness;
    harness.executor.setTickSource([]() { return 100; });
    harness.executor.setQuantumDriver([](uint64_t) {});
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    harness.executor.onQuantumEdge(101);
    ASSERT_EQ(harness.dispatch.begins, 1u);
    const auto binding_count = harness.dispatch.instance_bindings.size();
    harness.executor.onQuantumEdge(101);
    harness.executor.onQuantumEdge(160);
    EXPECT_EQ(harness.dispatch.begins, 1u);
    EXPECT_EQ(harness.dispatch.resumes, 0u);
    EXPECT_EQ(harness.dispatch.instance_bindings.size(), binding_count);
    EXPECT_FALSE(harness.executor.failed(1));
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(harness.executor.phaseFacts(1).size(), 1u);
}

TEST(ServingMeshExecutor, SelectedPhaseRemainsBusyUntilTheRealStart)
{
    Harness harness;
    harness.dispatch.on_start = {};
    harness.executor.setTickSource([]() { return 100; });
    harness.executor.setQuantumDriver([](uint64_t) {});
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    harness.executor.onQuantumEdge(101);
    harness.executor.onQuantumEdge(102);
    EXPECT_EQ(harness.dispatch.begins, 1u);
    EXPECT_EQ(harness.executor.phaseFacts(1).size(), 1u);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_FALSE(harness.executor.failed(1));
    std::string reason;
    EXPECT_TRUE(harness.executor.abortBeforeStart(1, 103, reason));
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
}

TEST(ServingMeshExecutor, QuantumBeforeAnyRequestIsIdle)
{
    Harness harness;
    harness.executor.onQuantumEdge(100);
    EXPECT_EQ(harness.dispatch.begins, 0u);
    EXPECT_EQ(harness.dispatch.resumes, 0u);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
}

TEST(ServingMeshExecutor, QuantumWaitPreservesAgeAndWakesAtDeadline)
{
    for (const uint64_t period : {1ULL, 500ULL}) {
        SCOPED_TRACE(period);
        const std::string fixture = "tests/gem5/ai_mesh/fixtures/gate6/serving_fullview_two_tokens.mshb";
        Harness harness(fixture);
        harness.executor.setTickSource([]() { return 100; });
        harness.executor.setClockPeriod(period);
        NpuExecutionRequest request = executionRequest(0x100300000, fixture);
        request.deadlineTick = 1000;
        SchedulerLimits limits;
        limits.maxActiveSequences = 4;
        limits.maxBatchTokens = 64;
        limits.maxPrefillTokensPerBatch = 64;
        limits.maxDecodeSequencesPerBatch = 4;
        limits.batchWaitCycles = 20;
        harness.executor.setSchedulerLimits(limits);
        std::vector<uint64_t> requested;
        harness.executor.setQuantumDriver(
            [&requested](uint64_t tick) { requested.push_back(tick); });
        ASSERT_TRUE(harness.executor.submit(request).admitted);
        const uint64_t deadline = 100 + 20 * period;
        ASSERT_EQ(requested, std::vector<uint64_t>({101}));
        harness.executor.onQuantumEdge(100 + period);
        EXPECT_EQ(harness.dispatch.begins, 0u);
        EXPECT_FALSE(harness.executor.failed(1));
        EXPECT_EQ(requested.back(), deadline);
        harness.executor.onQuantumEdge(deadline - 1);
        EXPECT_EQ(harness.dispatch.begins, 0u);
        EXPECT_FALSE(harness.executor.failed(1));
        harness.executor.onQuantumEdge(deadline);
        EXPECT_EQ(harness.dispatch.begins, 1u);
        EXPECT_FALSE(harness.executor.failed(1));
    }
}

TEST(ServingMeshExecutor, QuantumDeadlineFailsTheHeadRequest)
{
    Harness harness;
    harness.executor.setTickSource([]() { return 100; });
    NpuExecutionRequest request = executionRequest();
    request.deadlineTick = 1000;
    SchedulerLimits limits;
    limits.maxActiveSequences = 4;
    limits.maxBatchTokens = 4;
    limits.maxPrefillTokensPerBatch = 4;
    limits.maxDecodeSequencesPerBatch = 4;
    limits.batchWaitCycles = 20;
    harness.executor.setSchedulerLimits(limits);
    std::vector<uint64_t> requested;
    harness.executor.setQuantumDriver(
        [&requested](uint64_t tick) { requested.push_back(tick); });
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    harness.executor.onQuantumEdge(101);
    EXPECT_FALSE(harness.executor.failed(1));
    harness.executor.onQuantumEdge(120);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_EQ(harness.dispatch.begins, 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
}

TEST(ServingMeshExecutor, FrozenMembersConsumeTheGlobalActiveSequenceBudget)
{
    Harness harness;
    harness.executor.setTickSource([] { return 100; });
    harness.executor.setQuantumDriver([](uint64_t) {});
    SchedulerLimits limits;
    limits.maxActiveSequences = 1;
    limits.maxBatchTokens = limits.maxPrefillTokensPerBatch = 64;
    limits.maxDecodeSequencesPerBatch = 4;
    harness.executor.setSchedulerLimits(limits);
    auto first = executionRequest();
    auto second = executionRequest(0x100400000);
    second.requestId = 2;
    second.sessionId = second.kvHandle = 12;
    second.inputAddress += 4096;
    second.metadataAddress = 0x100600000;
    for (auto &binding : second.hostBindings)
        if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
            binding.address = second.inputAddress;
    ASSERT_TRUE(harness.executor.submit(first).admitted);
    ASSERT_TRUE(harness.executor.submit(second).admitted);
    harness.dispatch.on_start = {};
    harness.executor.onQuantumEdge(150);
    ASSERT_EQ(harness.dispatch.begins, 1u);
    for (uint64_t tick = 151; tick < 155; ++tick)
        harness.executor.onQuantumEdge(tick);
    EXPECT_EQ(harness.dispatch.begins, 1u);
    EXPECT_FALSE(harness.executor.failed(2));
    EXPECT_TRUE(harness.executor.phaseFacts(2).empty());
}

TEST(ServingMeshExecutor, MaterializerFailureKeepsFrozenIdentityAndWaitsForGlobalReduction)
{
    Harness harness;
    MoePreparationError error;
    error.set(7, 0, mesh_abi::MaterializerCheckSite::COUNT, "capacity",
              agent_abi::E_MOE_MATERIALIZE_CAPACITY);
    harness.dispatch.preparation_failure = error;
    bool started = false;
    harness.dispatch.on_start = [&] { started = true; };
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    ASSERT_EQ(harness.currentInstance(), 1u);
    ASSERT_EQ(harness.dispatch.failures.size(), 1u);
    EXPECT_FALSE(started);
    EXPECT_FALSE(harness.executor.failed(1));
    BatchFailureRecord failure;
    failure.batch_id = failure.program_instance_id = 1;
    failure.first = harness.dispatch.failures.front();
    harness.executor.onInstanceFailure(failure);
    EXPECT_TRUE(harness.executor.failed(1));
    const auto completion = harness.executor.completionFor(executionRequest());
    ASSERT_TRUE(completion.failure);
    EXPECT_EQ(completion.failure->code, agent_abi::E_MOE_MATERIALIZE_CAPACITY);
    EXPECT_FALSE(harness.manager.hasPin(1));
}

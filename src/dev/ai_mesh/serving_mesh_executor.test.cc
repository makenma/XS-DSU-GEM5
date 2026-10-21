#include "dev/ai_mesh/serving_mesh_executor_test.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_abi;
using namespace gem5::ai_mesh::serving_test;

TEST(ServingMeshExecutor, CompletionCannotBeReadThroughAnotherIdentity)
{
    Harness harness;
    const auto request = executionRequest();
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_TRUE(harness.executor.completionFor(request).succeeded());
    for (const bool change_generation : {false, true}) {
        auto foreign = request;
        if (change_generation)
            ++foreign.generation;
        else
            ++foreign.requestId;
        const auto completion = harness.executor.completionFor(foreign);
        EXPECT_FALSE(completion.succeeded());
        EXPECT_FALSE(completion.kvTerminal.has_value());
        EXPECT_EQ(completion.phaseInstances, 0u);
        EXPECT_EQ(completion.outputBytes, 0u);
        EXPECT_EQ(completion.semanticDigest, (std::array<uint8_t, 32>{}));
    }
    EXPECT_TRUE(harness.executor.completionFor(request).succeeded());
}

TEST(ServingMeshExecutor, UnknownCompletionBeforeAdmissionHasNoState)
{
    Harness harness;
    const auto completion = harness.executor.completionFor(executionRequest());
    EXPECT_FALSE(completion.succeeded());
    EXPECT_FALSE(completion.kvTerminal.has_value());
    EXPECT_EQ(completion.phaseInstances, 0u);
}

TEST(ServingMeshExecutor, RepeatedAdmissionCannotReplaceAnAdmittedIdentity)
{
    for (const bool change_generation : {false, true}) {
        Harness harness;
        const auto original = executionRequest();
        std::string reason;
        ASSERT_TRUE(harness.admitOnly(original, 100).admitted);
        const bool had_claim = harness.manager.hasClaim(original.requestId);
        const bool had_pin = harness.manager.hasPin(original.requestId);
        ASSERT_TRUE(had_claim || had_pin);
        const auto selectors = harness.dispatch.selectors;
        auto other = original;
        if (change_generation)
            ++other.generation;
        const auto duplicate = harness.admitOnly(other, 120);
        EXPECT_FALSE(duplicate.admitted);
        EXPECT_EQ(duplicate.rejectDetail, agent_abi::E_KV_STATE);
        const auto &retained = harness.executor.context(1).identity();
        EXPECT_EQ(retained.request_id, original.requestId);
        EXPECT_EQ(retained.generation, original.generation);
        EXPECT_EQ(harness.executor.lastTick(), 100u);
        EXPECT_EQ(harness.dispatch.selectors, selectors);
        EXPECT_EQ(harness.dispatch.begins, 0u);
        EXPECT_EQ(harness.dispatch.cancels, 0u);
        EXPECT_EQ(harness.dispatch.fails, 0u);
        EXPECT_EQ(harness.manager.hasClaim(original.requestId), had_claim);
        EXPECT_EQ(harness.manager.hasPin(original.requestId), had_pin);
        ASSERT_TRUE(harness.executor.abortBeforeStart(1, 150, reason)) << reason;
        EXPECT_FALSE(harness.manager.hasClaim(original.requestId));
        EXPECT_FALSE(harness.manager.hasPin(original.requestId));
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
    }
}

TEST(ServingMeshExecutor, TerminalSnapshotPrecedesCompletion)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    uint32_t pin_count_at_completion = 0;
    uint32_t claim_count_at_completion = 0;
    uint32_t kv_dma_at_completion = 0;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
        const KvRecord *record = harness.manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        pin_count_at_completion = record->pin_count;
        claim_count_at_completion = record->admission_claim_count;
        kv_dma_at_completion = record->outstanding_kv_dma;
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.hasClaim(1));
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    const NpuAdmission admission =
        harness.executor.submit(executionRequest());
    ASSERT_TRUE(admission.admitted);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_EQ(sink.completions, 1u);
    EXPECT_EQ(pin_count_at_completion, 0u);
    EXPECT_EQ(claim_count_at_completion, 0u);
    EXPECT_EQ(kv_dma_at_completion, 0u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_EQ(observed->request_id, 1u);
    EXPECT_EQ(observed->status, KvTerminalStatus::Success);
    EXPECT_EQ(sink.last.kvTerminal->cached_tokens, 10u);
    EXPECT_EQ(sink.last.kvTerminal->valid_bytes, 160u);
    EXPECT_EQ(sink.last.phaseInstances, 4u);
    EXPECT_EQ(sink.last.requestStartTick, 100u);
    EXPECT_EQ(sink.last.terminalTick, 521u);
    EXPECT_EQ(harness.executor.terminalTick(1), 521u);
    EXPECT_LT(harness.executor.phaseFacts(1).back().core_drain_tick,
              sink.last.terminalTick);
}

TEST(ServingMeshExecutor, TerminalSnapshotSurvivesLaterKvEdges)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_EQ(sink.completions, 1u);
    ASSERT_TRUE(harness.executor.kvTerminal(1).has_value());
    const KvTerminalSnapshot frozen = *harness.executor.kvTerminal(1);
    const uint64_t terminal_tick = harness.executor.terminalTick(1);

    MeshKvManager &manager = harness.manager;
    KvEdgeInputs later;
    later.tick = 900;
    KvAdmissionIntent next;
    next.request_id = next.session_id = next.kv_handle = 12;
    next.generation = 1;
    next.contract_digest = identity().contract_digest;
    next.required_tokens_after_round = 10;
    later.admissions.push_back(next);
    const KvEdgeResult result = manager.commitEdge(later);
    EXPECT_FALSE(result.fatal.has_value());

    const KvTerminalSnapshot &after = *harness.executor.kvTerminal(1);
    EXPECT_EQ(after.request_id, frozen.request_id);
    EXPECT_EQ(after.status, KvTerminalStatus::Success);
    EXPECT_EQ(after.cached_tokens, frozen.cached_tokens);
    EXPECT_EQ(after.valid_bytes, frozen.valid_bytes);
    EXPECT_EQ(after.state, frozen.state);
    EXPECT_EQ(harness.executor.terminalTick(1), terminal_tick);
    EXPECT_EQ(sink.last.terminalTick, terminal_tick);
    EXPECT_EQ(sink.last.kvTerminal->status, KvTerminalStatus::Success);
}

TEST(ServingMeshExecutor, SubmitAdmitsAndCompletesOnceWithRealOutput)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    const NpuAdmission admission =
        harness.executor.submit(executionRequest());
    EXPECT_TRUE(admission.admitted);
    EXPECT_EQ(harness.executor.kind(), NpuExecutorKind::ServingMesh);
    EXPECT_EQ(harness.executor.modeledServiceNs(executionRequest()), 0u);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_TRUE(sink.last.succeeded());
    EXPECT_TRUE(sink.last.coreStarted);
    EXPECT_EQ(sink.last.phaseInstances, 4u);
    EXPECT_EQ(sink.last.outputBytes, 256u);
    EXPECT_TRUE(sink.last.outputPayload.empty());
    EXPECT_EQ(harness.dispatch.publish_content.size(), 256u);
    std::array<uint8_t, 32> expected{};
    for (size_t i = 0; i < expected.size(); ++i)
        expected[i] = static_cast<uint8_t>(0x40 + i + 1);
    EXPECT_EQ(sink.last.semanticDigest, expected);
    EXPECT_FALSE(harness.executor.failed(1));
}

TEST(ServingMeshExecutor, BindsKvInputAndTokenContentAtAbsolutePositions)
{
    Harness harness;
    auto request = executionRequest();
    request.inputDigest.fill(0x3b);
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_EQ(harness.dispatch.fills.size(), 4u);
    EXPECT_EQ(harness.dispatch.fills[0].content,
              agentInputSurrogateBytes(request.inputDigest.data(), 128));
    const auto seed = surrogateSeed(request.inputDigest.data(), request.programId,
                                    request.profileId, request.requestedProfileKey);
    for (uint32_t ordinal = 0; ordinal < request.outputTokens; ++ordinal) {
        const auto token = surrogateTokenDigest(seed.data(), ordinal);
        EXPECT_EQ(harness.dispatch.fills[ordinal + 1].content,
                  surrogateOutputBytes(token.data(), 16));
    }
    EXPECT_NE(harness.dispatch.fills[1].content,
              harness.dispatch.fills[2].content);
}

TEST(ServingMeshExecutor, BindsAllocatedNonzeroKvSlotAndFrozenReadWindow)
{
    Harness harness;
    KvEdgeInputs occupied;
    KvAdmissionIntent first;
    first.request_id = first.session_id = first.kv_handle = 99;
    first.generation = 1;
    first.contract_digest = identity().contract_digest;
    first.required_tokens_after_round = 10;
    occupied.admissions.push_back(first);
    ASSERT_FALSE(harness.manager.commitEdge(occupied).fatal);
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    ASSERT_FALSE(harness.dispatch.instance_bindings.empty());
    const auto prefill = harness.dispatch.instance_bindings.back();
    EXPECT_EQ(slotAddress(prefill, memberSlot(prefill, false), 0, 128, true),
              std::optional<uint64_t>(geometry().slotBase(1)));
    EXPECT_FALSE(slotAddress(prefill, memberSlot(prefill, false), 0, 1, false));
    const auto stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    const auto decode = harness.dispatch.instance_bindings.back();
    EXPECT_EQ(slotAddress(decode, memberSlot(decode, false), 0, 128, false),
              std::optional<uint64_t>(geometry().slotBase(1)));
    EXPECT_FALSE(slotAddress(decode, memberSlot(decode, false), 0, 129, false));
    EXPECT_EQ(slotAddress(decode, memberSlot(decode, false), 128, 16, true),
              std::optional<uint64_t>(geometry().slotBase(1) + 128));
    EXPECT_FALSE(slotAddress(prefill, memberSlot(prefill, false), 0, 1, false));
}

TEST(ServingMeshExecutor, RejectsUnreachableKvSlotsBeforeInstallingSelectors)
{
    Harness harness;
    harness.dispatch.kv_reachable = false;
    const auto admission = harness.executor.submit(executionRequest());
    EXPECT_FALSE(admission.admitted);
    EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ROLE);
    EXPECT_TRUE(harness.dispatch.selectors.empty());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_EQ(harness.dispatch.begins, 0u);
}

TEST(ServingMeshExecutor, SubmitRejectsAnUnknownProfileWithoutCompletion)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    NpuExecutionRequest request = executionRequest();
    request.profileId = 9;
    const NpuAdmission admission = harness.executor.submit(request);
    EXPECT_FALSE(admission.admitted);
    EXPECT_EQ(admission.rejectDetail, agent_abi::E_REQUEST_PROFILE);
    EXPECT_EQ(sink.completions, 0u);
    EXPECT_EQ(harness.dispatch.selectors.size(), 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FaultReportsOneFailedCompletion)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    BatchFailureRecord failure;
    failure.batch_id = failure.program_instance_id = 1;
    failure.first = {190, {}, agent_abi::E_DCORE_ENGINE};
    harness.executor.onInstanceFailure(failure);
    harness.settleFailedInstance(1, 1, 1, 200);
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_FALSE(sink.last.succeeded());
    EXPECT_EQ(sink.last.phaseInstances, 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
}

TEST(ServingMeshExecutor, RejectsRequestsWithoutTheBoundProfileProof)
{
    struct Case
    {
        const char *name;
        uint32_t detail;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"wrong_key", agent_abi::E_REQUEST_PROFILE,
         [](NpuExecutionRequest &request) {
             request.requestedProfileKey ^= 1;
         }},
        {"input_bytes", agent_abi::E_BINDING_ROLE,
         [](NpuExecutionRequest &request) { request.inputBytes = 1; }},
        {"input_tokens", agent_abi::E_HOST_IO_SIZE_MISMATCH,
         [](NpuExecutionRequest &request) { request.inputTokens = 999; }},
        {"output_capacity", agent_abi::E_HOST_IO_SIZE_MISMATCH,
         [](NpuExecutionRequest &request) {
             request.outputCapacityBytes = 32;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.bytes = 32;
         }},
        {"output_capacity_single_sided", agent_abi::E_BINDING_ROLE,
         [](NpuExecutionRequest &request) {
             request.outputCapacityBytes = 32;
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, item.detail) << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_EQ(harness.dispatch.resumes, 0u) << item.name;
        EXPECT_TRUE(harness.dispatch.selectors.empty()) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
    }
}

TEST(ServingMeshExecutor, OldInstanceFactsAndRepeatedSettlesAreDropped)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);

    // Phase 1 completes and commits, which retires instance 1.
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   1, 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, 1, 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    ASSERT_EQ(harness.executor.phasesCommitted(1), 1u);

    // Phase 2 is armed as instance 2.  A fact from the old instance that
    // reuses the same request identity *and* the same descriptor/command ids
    // must not reach the current ledger.
    const uint32_t accepted = harness.executor.acceptedKvDescriptors(1);
    const uint32_t terminal = harness.executor.terminalKvDescriptors(1);
    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id,
                                   1, 1, 1, 300);
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, 1, 1, 1,
                                           310,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), accepted);
    EXPECT_EQ(harness.executor.terminalKvDescriptors(1), terminal);
    EXPECT_EQ(harness.executor.staleInstanceFacts(), 2u);

    // The current instance drains before its append terminal arrives: the
    // settle is consumed once and the phase stays open.
    harness.executor.onInstanceSettled(2, 1, 1, false, 320);
    const uint32_t settles = harness.executor.instanceSettles(1);
    EXPECT_EQ(settles, 2u);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 1u);

    // Repeating that settle cannot count or advance anything.
    harness.executor.onInstanceSettled(2, 1, 1, false, 321);
    harness.settleFailedInstance(2, 1, 1, 322);
    EXPECT_EQ(harness.executor.instanceSettles(1), settles);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 1u);
    EXPECT_EQ(harness.executor.staleInstanceFacts(), 4u);
    EXPECT_FALSE(harness.executor.failed(1));

    // The same instance's drain facts are still accepted and close the phase.
    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id,
                                   2, 1, 1, 330);
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, 2, 1, 1,
                                           340,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), accepted + 1);
    EXPECT_EQ(harness.executor.terminalKvDescriptors(1), terminal + 1);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 2u);
    EXPECT_FALSE(harness.executor.failed(1));

    // A settle for an instance that was never armed is a contract violation.
    harness.executor.onInstanceSettled(99, 1, 1, false, 350);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_EQ(harness.executor.phasesCommitted(1), 2u);
}

TEST(ServingMeshExecutor, LateDmaFactsCannotRewriteASealedSuccess)
{
    for (bool terminal : {false, true}) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        const NpuExecutionRequest request = executionRequest();
        ASSERT_TRUE(harness.executor.submit(request).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        driveOneGenerate(harness, stores);
        ASSERT_TRUE(harness.executor.finished(1));
        ASSERT_EQ(sink.completions, 1u);
        ASSERT_TRUE(sink.last.succeeded());
        ASSERT_TRUE(harness.executor.completionFor(request).succeeded());
        if (terminal)
            harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, 99, 1, 1,
                                           600,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
        else
            harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                           stores[0].command_id, 99, 1, 1, 600);
        EXPECT_FALSE(harness.executor.failed(1));
        EXPECT_TRUE(harness.executor.completionFor(request).succeeded());
        EXPECT_EQ(harness.dispatch.fails, 0u);
        EXPECT_EQ(sink.completions, 1u);
        EXPECT_TRUE(sink.last.succeeded());
    }
}

TEST(ServingMeshExecutor, UnknownInstanceDmaFactsFailTheRequest)
{
    const auto stores_for = [](Harness &harness) {
        return kvStoreCommands(harness.program);
    };
    {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([]() { return 100; });
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        const std::vector<KvStore> stores = stores_for(harness);
        ASSERT_FALSE(stores.empty());
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id, 99, 1, 1, 200);
        EXPECT_TRUE(harness.executor.failed(1));
        EXPECT_EQ(harness.dispatch.fails, 1u);
        EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), 0u);
        EXPECT_EQ(sink.completions, 0u);
    }
    {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([]() { return 100; });
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        const std::vector<KvStore> stores = stores_for(harness);
        ASSERT_FALSE(stores.empty());
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id,
                                       harness.currentInstance(), 1, 1, 200);
        ASSERT_EQ(harness.executor.acceptedKvDescriptors(1), 1u);
        harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, 99, 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
        EXPECT_TRUE(harness.executor.failed(1));
        EXPECT_EQ(harness.dispatch.fails, 1u);
        EXPECT_EQ(harness.executor.terminalKvDescriptors(1), 0u);
        EXPECT_EQ(sink.completions, 0u);

        harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
        harness.executor.onInstanceSettled(harness.currentInstance(), 1, 1,
                                           false, 320);
        EXPECT_EQ(sink.completions, 1u);
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.hasClaim(1));
        EXPECT_TRUE(harness.manager.validate().empty());
        EXPECT_EQ(harness.executor.terminalKvDescriptors(1), 1u);
    }
}

TEST(ServingMeshExecutor, DmaFactsBeforeTheRealStartRollBackThePrestartClaim)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.dispatch.on_start = nullptr;
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    ASSERT_FALSE(harness.executor.finished(1));
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   1, 1, 1, 200);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_EQ(harness.dispatch.cancels, 1u);
    EXPECT_EQ(harness.dispatch.fails, 0u);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(sink.last.kvTerminal.has_value());
    EXPECT_EQ(sink.last.kvTerminal->status, KvTerminalStatus::Error);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, AStaleInstanceDrainCannotCloseTheCurrentPhase)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   1, 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, 1, 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    ASSERT_EQ(harness.executor.phasesCommitted(1), 1u);
    ASSERT_FALSE(harness.executor.failed(1));

    harness.executor.onInstanceSettled(1, 1, 1, false, 300);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 1u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   1, 1, 1, 310);
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), 1u);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 1u);
    EXPECT_FALSE(harness.executor.failed(1));

    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id,
                                   2, 1, 1, 400);
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, 2, 1, 1,
                                           410,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    harness.executor.onInstanceSettled(2, 1, 1, false, 420);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 2u);
    EXPECT_FALSE(harness.executor.failed(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, UsesScopedMemberSymbolsForEveryPhase)
{
    Harness harness;
    auto request = executionRequest();
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_TRUE(harness.executor.completionFor(request).succeeded());
    for (const auto &view : harness.dispatch.instance_bindings) {
        for (const auto &instance : harness.program.transport.agent_instance_profiles) {
            if (instance.instance_profile_id != view.instanceProfileId())
                continue;
            const auto &member = harness.program.transport.agent_instance_member_bindings.at(instance.member_binding_first);
            if (member.static_input_symbol_id) {
                const auto slot = symbolSlot(view, member.static_input_symbol_id);
                EXPECT_EQ(slotAddress(view, slot, 0, request.inputBytes, false), request.inputAddress);
            }
            if (member.static_output_symbol_id) {
                const auto slot = symbolSlot(view, member.static_output_symbol_id);
                EXPECT_EQ(slotAddress(view, slot, 0, request.outputCapacityBytes, true), request.outputAddress);
            }
        }
    }
}

TEST(ServingMeshExecutor, InstallsEveryVerifiedRoleIntoTheExecutionView)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    NpuExecutionRequest request = executionRequest();
    ASSERT_FALSE(request.hostBindings.empty());
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    ASSERT_FALSE(harness.dispatch.instance_bindings.empty());
    const ServingInstanceBinding prefill = harness.dispatch.instance_bindings.back();
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_TRUE(harness.executor.completionFor(request).succeeded());
    const ServingInstanceBinding &publish = harness.dispatch.instance_bindings.back();
    uint32_t installed = 0;
    for (const agent_abi::BindingRecord &binding : request.hostBindings) {
        if (binding.kind == agent_abi::kBindingKindKV_EXTERNAL)
            continue;
        const bool write = binding.kind == agent_abi::kBindingKindHOST_OUTPUT;
        const auto &view = write ? publish : prefill;
        uint32_t slot = 0;
        if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
            slot = symbolSlot(view, binding.symbol_id);
        } else {
            const auto &table = harness.program.transport;
            const auto instance = std::find_if(table.agent_instance_profiles.begin(), table.agent_instance_profiles.end(),
                [&](const auto &row) { return row.instance_profile_id == view.instanceProfileId(); });
            ASSERT_NE(instance, table.agent_instance_profiles.end());
            const auto &member = table.agent_instance_member_bindings.at(instance->member_binding_first);
            slot = symbolSlot(view, write ? member.static_output_symbol_id : member.static_input_symbol_id);
        }
        const auto resolved = slotAddress(view, slot, 0, binding.bytes, write);
        ASSERT_TRUE(resolved.has_value()) << binding.symbol_id;
        EXPECT_EQ(*resolved, binding.address) << binding.symbol_id;
        ++installed;
    }
    EXPECT_EQ(installed, 3u);
}

TEST(ServingMeshExecutor, RejectsATamperedHostBindingTableBeforeAdmission)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"missing", [](NpuExecutionRequest &request) {
             request.hostBindings.pop_back();
         }},
        {"extra", [](NpuExecutionRequest &request) {
             request.hostBindings.push_back(request.hostBindings[0]);
         }},
        {"wrong_role", [](NpuExecutionRequest &request) {
             request.hostBindings[0].kind =
                 agent_abi::kBindingKindKV_EXTERNAL;
         }},
        {"wrong_flags", [](NpuExecutionRequest &request) {
             request.hostBindings[0].flags = agent_abi::kBindingFlagsWRITE;
         }},
        {"zero_range", [](NpuExecutionRequest &request) {
             request.hostBindings[0].bytes = 0;
         }},
        {"zero_address", [](NpuExecutionRequest &request) {
             request.hostBindings[0].address = 0;
         }},
        {"kv_physical_address", [](NpuExecutionRequest &request) {
             request.hostBindings[2].address = 0x400000;
         }},
        {"kv_wrong_slot_bytes", [](NpuExecutionRequest &request) {
             request.hostBindings[2].bytes = 8192;
         }},
        {"absent", [](NpuExecutionRequest &request) {
             request.hostBindings.clear();
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ROLE)
            << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_TRUE(harness.dispatch.selectors.empty()) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
    }
}

TEST(ServingMeshExecutor, RejectsOverlappingRequestRangesBeforeAdmission)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"output_aliases_input", [](NpuExecutionRequest &request) {
             request.outputAddress = request.inputAddress;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.address = request.inputAddress;
         }},
        {"metadata_aliases_input", [](NpuExecutionRequest &request) {
             request.metadataAddress = request.inputAddress;
             request.metadataCapacityBytes = 200;
         }},
        {"metadata_overlaps_output", [](NpuExecutionRequest &request) {
             request.metadataAddress = request.outputAddress;
             request.metadataCapacityBytes = 64;
         }},
        {"partial_overlap", [](NpuExecutionRequest &request) {
             request.metadataAddress = request.outputAddress +
                 request.outputCapacityBytes - 64;
             request.metadataCapacityBytes = 128;
         }},
        {"range_overflow", [](NpuExecutionRequest &request) {
             request.metadataAddress = UINT64_MAX - 32;
             request.metadataCapacityBytes = 64;
         }},
        {"weight_overlap", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
                     binding.address = 0x800200000;
                     binding.bytes = 128;
                 }
             request.metadataAddress = 0x800200000;
             request.metadataCapacityBytes = 64;
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail,
                  agent_abi::E_BINDING_ALIAS_MISMATCH) << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_TRUE(harness.dispatch.selectors.empty()) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
    }
    Harness harness;
    NpuExecutionRequest adjacent = executionRequest();
    adjacent.metadataAddress = adjacent.outputAddress +
        adjacent.outputCapacityBytes;
    adjacent.metadataCapacityBytes = 200;
    const NpuAdmission admission = harness.executor.submit(adjacent);
    EXPECT_TRUE(admission.admitted);
}

TEST(ServingMeshExecutor, WireBindingsCannotDisagreeWithTheCheckedHeader)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"output_address", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.address = request.inputAddress;
         }},
        {"input_address", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                     binding.address = request.outputAddress;
         }},
        {"output_bytes", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.bytes = request.inputBytes;
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ROLE)
            << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_TRUE(harness.dispatch.selectors.empty()) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
    }
}

TEST(ServingMeshExecutor, RejectsZeroRequestAddressesBeforeAdmission)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"input_double_zero", [](NpuExecutionRequest &request) {
             request.inputAddress = 0;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                     binding.address = 0;
         }},
        {"output_double_zero", [](NpuExecutionRequest &request) {
             request.outputAddress = 0;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.address = 0;
         }},
        {"metadata_zero", [](NpuExecutionRequest &request) {
             request.metadataAddress = 0;
             request.metadataCapacityBytes = 200;
         }},
        {"input_single_zero", [](NpuExecutionRequest &request) {
             request.inputAddress = 0;
         }},
        {"weight_zero", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL)
                     binding.address = 0;
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ROLE)
            << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_TRUE(harness.dispatch.selectors.empty()) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
    }
}

TEST(ServingMeshExecutor, RejectsCrossKindAndKvRangeOverlapsBeforeAdmission)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"input_aliases_weight", [](NpuExecutionRequest &request) {
             request.inputAddress = 0x800200000;
             for (auto &binding : request.hostBindings) {
                 if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                     binding.address = request.inputAddress;
                 if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
                     binding.address = request.inputAddress;
                     binding.bytes = request.inputBytes;
                 }
             }
         }},
        {"input_partially_overlaps_weight", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings) {
                 if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                     binding.address = 0x800200000 - 64;
                 if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
                     binding.address = 0x800200000;
                     binding.bytes = request.inputBytes;
                 }
             }
         }},
        {"output_aliases_kv_region", [](NpuExecutionRequest &request) {
             request.outputAddress = geometry().region_base +
                 geometry().slot_bytes;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.address = request.outputAddress;
         }},
        {"metadata_inside_kv_region", [](NpuExecutionRequest &request) {
             request.metadataAddress = geometry().region_base + 64;
             request.metadataCapacityBytes = 128;
         }},
    };
    Harness adjacentHarness;
    NpuExecutionRequest adjacent = executionRequest();
    adjacent.outputAddress = geometry().region_base +
        geometry().regionBytes();
    for (auto &binding : adjacent.hostBindings)
        if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
            binding.address = adjacent.outputAddress;
    EXPECT_TRUE(adjacentHarness.executor.submit(adjacent).admitted);
}

TEST(ServingMeshExecutor, HostBindingRequestRangeRules)
{
    AgentHostBindingPlan plan;
    plan.programId = 1;
    plan.profileId = 1;
    AgentHostBindingRequirement input;
    input.symbolId = 1;
    input.kind = agent_abi::kBindingKindHOST_INPUT;
    input.flags = exactBindingFlags(input.kind);
    AgentHostBindingRequirement output;
    output.symbolId = 2;
    output.kind = agent_abi::kBindingKindHOST_OUTPUT;
    output.flags = exactBindingFlags(output.kind);
    AgentHostBindingRequirement weight;
    weight.symbolId = 3;
    weight.kind = agent_abi::kBindingKindWEIGHT_EXTERNAL;
    weight.flags = exactBindingFlags(weight.kind);
    weight.platformAddress = 0x800200000;
    weight.requiredBytes = 128;
    AgentHostBindingRequirement aliasWeight = weight;
    aliasWeight.symbolId = 4;

    auto record = [](uint32_t symbol, uint16_t kind, uint16_t flags,
                     uint64_t address, uint64_t bytes) {
        agent_abi::BindingRecord record;
        record.symbol_id = symbol;
        record.kind = kind;
        record.flags = flags;
        record.address = address;
        record.bytes = bytes;
        return record;
    };
    const uint64_t kvBegin = 0x400000;
    const uint64_t kvBytes = 8192;
    std::string reason;

    plan.requirements = {input, output, weight};
    std::vector<agent_abi::BindingRecord> control = {
        record(1, input.kind, input.flags, 0x1000, 128),
        record(2, output.kind, output.flags, 0x2000, 128),
        record(3, weight.kind, weight.flags, weight.platformAddress,
               weight.requiredBytes),
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  control, plan, 0x1000, 128, 0x2000, 128, 0x3000, 128,
                  kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::Match) << reason;

    plan.requirements = {input, output, weight, aliasWeight};
    std::vector<agent_abi::BindingRecord> equalWeights = {
        control[0], control[1], control[2],
        record(4, aliasWeight.kind, aliasWeight.flags,
               weight.platformAddress, weight.requiredBytes),
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  equalWeights, plan, 0x1000, 128, 0x2000, 128, 0x3000, 128,
                  kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::AliasMismatch);

    std::vector<agent_abi::BindingRecord> duplicateSymbol = {
        control[0], control[1], control[2], control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  duplicateSymbol, plan, 0x1000, 128, 0x2000, 128, 0x3000,
                  128, kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::Mismatch);

    plan.requirements = {input, output, weight};
    std::vector<agent_abi::BindingRecord> zeroInput = {
        record(1, input.kind, input.flags, 0, 128),
        control[1], control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  zeroInput, plan, 0, 128, 0x2000, 128, 0x3000, 128, kvBegin,
                  kvBytes, 4096, reason),
              HostBindingVerdict::Mismatch);
    EXPECT_NE(reason.find("E_BINDING_ROLE"), std::string::npos) << reason;

    std::vector<agent_abi::BindingRecord> zeroOutput = {
        control[0],
        record(2, output.kind, output.flags, 0, 128),
        control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  zeroOutput, plan, 0x1000, 128, 0, 128, 0x3000, 128, kvBegin,
                  kvBytes, 4096, reason),
              HostBindingVerdict::Mismatch);

    std::vector<agent_abi::BindingRecord> zeroMetadata = control;
    EXPECT_EQ(verifyHostBindingRequest(
                  zeroMetadata, plan, 0x1000, 128, 0x2000, 128, 0, 128,
                  kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::Mismatch);

    std::vector<agent_abi::BindingRecord> inputAtWeight = {
        record(1, input.kind, input.flags, weight.platformAddress, 128),
        control[1], control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  inputAtWeight, plan, weight.platformAddress, 128, 0x2000,
                  128, 0x3000, 128, kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::AliasMismatch);

    std::vector<agent_abi::BindingRecord> outputAtKv = {
        control[0],
        record(2, output.kind, output.flags, kvBegin + 64, 128),
        control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  outputAtKv, plan, 0x1000, 128, kvBegin + 64, 128, 0x3000,
                  128, kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::AliasMismatch);

    std::vector<agent_abi::BindingRecord> adjacentToKv = {
        control[0],
        record(2, output.kind, output.flags, kvBegin + kvBytes, 128),
        control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  adjacentToKv, plan, 0x1000, 128, kvBegin + kvBytes, 128,
                  0x5000, 128, kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::Match) << reason;
}

TEST(ServingMeshExecutor, DrivesOneGenerateThroughRealKvEdges)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    EXPECT_EQ(harness.dispatch.begins, 1u);
    ASSERT_EQ(harness.dispatch.selectors.size(), 1u);
    EXPECT_EQ(harness.dispatch.selectors[0], std::make_pair(1u, 1u));
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_EQ(stores.size(), 3u);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 0u);

    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), 1u);
    EXPECT_EQ(harness.executor.terminalKvDescriptors(1), 1u);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 1u);
    EXPECT_EQ(harness.dispatch.resumes, 1u);
    record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 8u);
    EXPECT_EQ(record->valid_bytes, 128u);

    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id, harness.currentInstance(), 1, 1, 300);
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, harness.currentInstance(), 1, 1,
                                           310,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    harness.executor.onInstanceSettled(2, 1, 1, false, 320);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 2u);
    EXPECT_EQ(harness.dispatch.resumes, 2u);
    record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 9u);

    harness.executor.onDmaAccepted(stores[2].descriptor_id, stores[2].command_id, harness.currentInstance(), 1, 1, 400);
    harness.executor.onDmaTerminal(stores[2].descriptor_id, stores[2].command_id, 16, 0, harness.currentInstance(), 1, 1,
                                           410,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    harness.executor.onInstanceSettled(3, 1, 1, false, 420);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 3u);
    EXPECT_EQ(harness.dispatch.resumes, 3u);
    record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 10u);
    EXPECT_EQ(record->valid_bytes, 160u);

    harness.drainOutput(500);
    harness.executor.onInstanceSettled(4, 1, 1, false, 520);
    EXPECT_TRUE(harness.executor.finished(1));
    EXPECT_FALSE(harness.executor.failed(1));
    EXPECT_EQ(harness.executor.phasesCommitted(1), 4u);
    EXPECT_EQ(harness.dispatch.resumes, 3u);
    EXPECT_EQ(harness.dispatch.finishes, 0u);
    harness.executor.finishServingRun();
    EXPECT_EQ(harness.dispatch.finishes, 1u);
    ASSERT_EQ(harness.dispatch.selectors.size(), 4u);
    EXPECT_EQ(harness.dispatch.selectors[1], std::make_pair(2u, 2u));
    EXPECT_EQ(harness.dispatch.selectors[2], std::make_pair(3u, 3u));
    EXPECT_EQ(harness.dispatch.selectors[3], std::make_pair(4u, 4u));
    const std::optional<KvTerminalSnapshot> &terminal =
        harness.executor.kvTerminal(1);
    ASSERT_TRUE(terminal.has_value());
    EXPECT_EQ(terminal->request_id, 1u);
    EXPECT_EQ(terminal->status, KvTerminalStatus::Success);
    EXPECT_EQ(terminal->cached_tokens, 10u);
    EXPECT_EQ(harness.executor.terminalTick(1), 521u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->pin_count, 0u);
    EXPECT_EQ(record->admission_claim_count, 0u);
    EXPECT_EQ(record->outstanding_kv_dma, 0u);
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, PhaseFactsRecordTheRealCausalChain)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_EQ(stores.size(), 3u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    ASSERT_EQ(harness.executor.phaseFacts(1).size(), 2u);
    const ServingPhaseFact &prefill = harness.executor.phaseFacts(1)[0];
    EXPECT_EQ(prefill.phase, kPhasePREFILL);
    EXPECT_EQ(prefill.instance_profile_id, 11u);
    EXPECT_EQ(prefill.mesh_profile_id, 1u);
    EXPECT_EQ(prefill.kv_tokens_before, 0u);
    EXPECT_EQ(prefill.append_tokens, 8u);
    EXPECT_EQ(prefill.accepted_descriptors, 1u);
    EXPECT_EQ(prefill.terminal_descriptors, 1u);
    EXPECT_EQ(prefill.append_terminal_tick, 210u);
    EXPECT_EQ(prefill.core_drain_tick, 220u);
    EXPECT_EQ(prefill.commit_tick, 221u);
    const ServingPhaseFact &decode0 = harness.executor.phaseFacts(1)[1];
    EXPECT_EQ(decode0.phase, kPhaseDECODE);
    EXPECT_EQ(decode0.kv_tokens_before, 8u);
    EXPECT_EQ(decode0.append_tokens, 1u);
    EXPECT_EQ(decode0.commit_tick, 0u);
    driveOneGenerate(harness, stores, 2);
    ASSERT_EQ(harness.executor.phaseFacts(1).size(), 4u);
    EXPECT_EQ(harness.executor.phaseFacts(1)[3].phase, kPhasePUBLISH);
    EXPECT_EQ(harness.executor.phaseFacts(1)[3].append_tokens, 0u);
    EXPECT_EQ(harness.executor.phaseFacts(1)[3].accepted_descriptors, 0u);
}

TEST(ServingMeshExecutor, PartialTokenPrefixBlocksThePhase)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_EQ(stores.size(), 3u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 16, 7, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
    EXPECT_EQ(harness.dispatch.resumes, 0u);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 1u);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, RetriedDescriptorTerminalIsNotDoubleCounted)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 201);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           211,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), 1u);
    EXPECT_EQ(harness.executor.terminalKvDescriptors(1), 1u);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->outstanding_kv_dma, 0u);
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, CoreDrainAndAppendTerminalAreBothRequired)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
    harness.executor.onInstanceSettled(1, 1, 1, false, 300);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 1u);
    EXPECT_EQ(harness.dispatch.resumes, 1u);
}

TEST(ServingMeshExecutor, SamePhaseSecondStoreDoesNotBlockTheWrapUp)
{
    Harness harness(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb");
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    const NpuAdmission split_admission = harness.executor.submit(
        executionRequest(0x100300000,
                         "tests/gem5/ai_mesh/fixtures/gate6/"
                         "serving_split_kv.mshb"));
    ASSERT_TRUE(split_admission.admitted)
        << uint32_t(split_admission.rejectDetail);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    ASSERT_EQ(stores[0].command_id + 1, stores[1].command_id);
    ASSERT_EQ(stores[1].descriptor_id, stores[0].descriptor_id + 1);

    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    ASSERT_EQ(harness.executor.acceptedKvDescriptors(1), 1u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 64, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 64}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onInstanceSettled(1, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_NE(observed->status, KvTerminalStatus::Success);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, SamePhaseSecondStoreWaitsForTheIssuedTerminal)
{
    Harness harness(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb");
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    const NpuAdmission split_admission = harness.executor.submit(
        executionRequest(0x100300000,
                         "tests/gem5/ai_mesh/fixtures/gate6/"
                         "serving_split_kv.mshb"));
    ASSERT_TRUE(split_admission.admitted)
        << uint32_t(split_admission.rejectDetail);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed(1));

    harness.executor.onInstanceSettled(1, 1, 1, false, 300);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 64, 0, harness.currentInstance(), 1, 1,
                                           320,
                                           std::vector<DmaCommittedSegment>{{0, 0, 64}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(sink.last.kvTerminal.has_value());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FailedRequestWaitsForItsDrainBeforeReporting)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    ASSERT_EQ(harness.executor.acceptedKvDescriptors(1), 1u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_FALSE(harness.manager.fatalReason().has_value());

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);
    EXPECT_EQ(harness.executor.completionFor(executionRequest()).outcome,
              NpuExecutionOutcome::Pending);

    harness.executor.onInstanceSettled(1, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_NE(observed->status, KvTerminalStatus::Success);
    EXPECT_EQ(sink.last.outcome, NpuExecutionOutcome::Error);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FailedRequestWaitsForTheLastKvTerminalAfterTheDrain)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed(1));

    harness.executor.onInstanceSettled(1, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           340,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, UnissuedDescriptorsDoNotBlockTheFailedWrapUp)
{
    Harness harness;
    RecordingSink sink;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), 1u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, SecondPhaseFailureWaitsForItsOwnDrain)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    ASSERT_EQ(harness.executor.phasesCommitted(1), 1u);
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id, harness.currentInstance(), 1, 1, 240);
    harness.executor.onDmaAccepted(stores[1].descriptor_id + 100, stores[1].command_id, harness.currentInstance(), 1, 1, 250);
    EXPECT_TRUE(harness.executor.failed(1));
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onInstanceSettled(2, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(sink.last.kvTerminal.has_value());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, ErroredInstanceDrainUsesTheSameTerminalGate)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);

    harness.settleFailedInstance(1, 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(sink.last.kvTerminal.has_value());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FailureStopsTheRunningCoreThroughTheMesh)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    EXPECT_EQ(harness.dispatch.fails, 0u);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_EQ(harness.dispatch.fails, 1u);
    EXPECT_EQ(harness.dispatch.cancels, 0u);
    ASSERT_EQ(harness.dispatch.failures.size(), 1u);
    const auto &source = harness.dispatch.failures.front().source;
    EXPECT_EQ(source.error_class, mesh_abi::kMoeErrorClassINTERNAL);
    EXPECT_EQ(source.domain, mesh_abi::kMeshObjectDomainKV_RUNTIME);
    EXPECT_EQ(source.object_kind, mesh_abi::kErrorSourceObjectKindKV_APPEND);
    EXPECT_EQ(source.ordinal, 1u);
    EXPECT_EQ(source.generation, 1u);
    EXPECT_EQ(agent_abi::rdU64(source.aux_key.data()), 1u);
    EXPECT_EQ(agent_abi::rdU64(source.aux_key.data() + 8), 1u);
}

TEST(ServingMeshExecutor, ErrorCompletionCarriesTheOwnerTerminal)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    bool ownership_released = false;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
        ownership_released =
            !harness.manager.hasPin(1) && !harness.manager.hasClaim(1);
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    harness.settleFailedInstance(1, 1, 1, 200);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_GE(sink.completions, 1u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_FALSE(observed->status == KvTerminalStatus::Success);
    EXPECT_TRUE(ownership_released);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, MeshFaultReleasesTheKvClaim)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    harness.settleFailedInstance(1, 1, 1, 200);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_FALSE(harness.executor.finished(1));
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FaultedDmaTerminalRecordsTheErrorPrefix)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 0, 7, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
    EXPECT_EQ(harness.dispatch.resumes, 0u);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 0u);
    EXPECT_EQ(record->state, KvState::Error);
    ASSERT_TRUE(record->first_error.has_value());
    EXPECT_EQ(record->first_error->code, uint32_t(agent_abi::E_AXI_RESPONSE));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, MismatchedDescriptorIdentityIsRejected)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), 0u);
}

TEST(ServingMeshExecutor, CancelCompletionCarriesTheOwnerTerminal)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    bool ownership_released = false;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
        ownership_released =
            !harness.manager.hasPin(1) && !harness.manager.hasClaim(1);
    };
    harness.executor.attachSink(&sink);
    harness.dispatch.on_start = nullptr;
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    std::string reason;
    ASSERT_TRUE(harness.executor.abortBeforeStart(1, 150, reason)) << reason;
    EXPECT_FALSE(harness.executor.failed(1));
    EXPECT_EQ(sink.last.outcome, NpuExecutionOutcome::Cancelled);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_FALSE(sink.last.succeeded());
    ASSERT_TRUE(observed.has_value());
    EXPECT_EQ(observed->status, KvTerminalStatus::Cancelled);
    EXPECT_TRUE(ownership_released);
    EXPECT_EQ(sink.last.requestStartTick, 100u);
    EXPECT_EQ(sink.last.terminalTick, 150u);
    EXPECT_EQ(harness.executor.terminalTick(1), 150u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, UnknownRequestProfileIsRejectedWithoutSideEffects)
{
    Harness harness;
    auto bad = executionRequest();
    bad.profileId = 9;
    const auto admission = harness.admitOnly(bad, 100);
    EXPECT_FALSE(admission.admitted);
    EXPECT_EQ(admission.rejectDetail, agent_abi::E_REQUEST_PROFILE);
    EXPECT_EQ(harness.dispatch.selectors.size(), 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, UnknownInstanceCallbackDoesNotAdvanceThePhase)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(999, 1, 1, false, 220);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
    EXPECT_TRUE(harness.executor.failed(1));
    EXPECT_FALSE(harness.executor.finished(1));
    harness.executor.onInstanceSettled(1, 1, 1, false, 230);
    EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
}

TEST(ServingMeshExecutor, AbortBeforeTheRealStartRollsBackTheClaim)
{
    Harness harness;
    std::string reason;
    harness.dispatch.on_start = nullptr;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    ASSERT_TRUE(harness.executor.abortBeforeStart(1, 160, reason)) << reason;
    EXPECT_EQ(harness.dispatch.cancels, 1u);
    EXPECT_EQ(harness.dispatch.begins, 1u);
    EXPECT_FALSE(harness.executor.abortBeforeStart(1, 170, reason));
    EXPECT_EQ(harness.dispatch.cancels, 1u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, TheRealStartConsumesTheRollbackAuthority)
{
    Harness harness;
    std::string reason;
    harness.dispatch.on_start = nullptr;
    ASSERT_TRUE(harness.admitOnly(executionRequest(), 100).admitted);
    harness.executor.onQuantumEdge(150);
    ASSERT_FALSE(harness.executor.failed(1)) << harness.executor.failureReason(1);
    harness.executor.onInstanceStarted(1, 1, 1, 160);
    EXPECT_FALSE(harness.executor.abortBeforeStart(1, 170, reason));
}

TEST(ServingMeshExecutor, SuccessfulBurstPrefixSurvivesDescriptorError)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 64, 7, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 64}});
    harness.settleFailedInstance(1, 1, 1, 220);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 4u);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_TRUE(harness.manager.validate().empty());
    EXPECT_EQ(sink.completions, 1u);
}

TEST(ServingMeshExecutor, CommittedSegmentsCoverOnlyTheirOwnBytes)
{
    const std::string fixture =
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb";
    struct Case
    {
        const char *name;
        std::vector<DmaCommittedSegment> segments;
        uint32_t expected_tokens;
    };
    const Case cases[] = {
        {"leading_prefix", {{0, 0, 16}}, 1u},
        {"leading_two", {{0, 0, 32}}, 2u},
        {"trailing_success", {{0, 48, 16}}, 0u},
        {"out_of_order", {{0, 48, 16}, {0, 16, 16}}, 0u},
        {"reversed_prefix", {{0, 32, 32}, {0, 0, 16}}, 1u},
        {"duplicate", {{0, 16, 16}, {0, 16, 16}}, 0u},
    };
    for (const Case &item : cases) {
        Harness harness(fixture);
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        ASSERT_TRUE(harness.executor.submit(
            executionRequest(0x100300000, fixture)).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        ASSERT_GE(stores.size(), 2u);
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id,
                                       harness.currentInstance(), 1, 1, 200);
        uint64_t committed = 0;
        for (const DmaCommittedSegment &segment : item.segments)
            committed += segment.bytes;
        harness.executor.onDmaTerminal(stores[0].descriptor_id,
                                       stores[0].command_id, committed, 7,
                                       harness.currentInstance(), 1, 1, 210,
                                       item.segments);
        harness.settleFailedInstance(1, 1, 1, 220);
        const KvRecord *record = harness.manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr) << item.name;
        EXPECT_EQ(record->cached_tokens, item.expected_tokens) << item.name;
        EXPECT_EQ(record->state, KvState::Error) << item.name;
        EXPECT_FALSE(harness.manager.fatalReason().has_value()) << item.name;
        EXPECT_TRUE(harness.manager.validate().empty()) << item.name;
        EXPECT_EQ(sink.completions, 1u) << item.name;
    }
}

TEST(ServingMeshExecutor, SameEdgeFaultWinnerDoesNotFollowCallbackOrder)
{
    const std::string fixture =
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb";
    for (bool reverse : {false, true}) {
        Harness harness(fixture);
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        ASSERT_TRUE(harness.executor.submit(
            executionRequest(0x100300000, fixture)).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        ASSERT_GE(stores.size(), 2u);
        for (unsigned index : {0u, 1u})
            harness.executor.onDmaAccepted(stores[index].descriptor_id,
                                           stores[index].command_id,
                                           harness.currentInstance(), 1, 1,
                                           200);
        for (unsigned ordinal : {0u, 1u}) {
            const unsigned index = reverse ? 1u - ordinal : ordinal;
            harness.executor.onDmaTerminal(stores[index].descriptor_id, stores[index].command_id, 0, 7, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{});
        }
        harness.settleFailedInstance(1, 1, 1, 220);
        const KvRecord *record = harness.manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        ASSERT_TRUE(record->first_error.has_value()) << "reverse=" << reverse;
        EXPECT_EQ(record->first_error->source.ordinal,
                  1u)
            << "reverse=" << reverse;
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
        EXPECT_TRUE(harness.manager.validate().empty());
        EXPECT_EQ(sink.completions, 1u);
    }
}

TEST(ServingMeshExecutor, RoundAndFlagsAreProvenBeforeAdmission)
{
    struct Case
    {
        const char *name;
        uint16_t round;
        uint16_t flags;
        uint32_t cached_tokens;
        uint32_t expected_detail;
    };
    const Case cases[] = {
        {"round_zero_reuse", 0, agent_abi::kSqFlagsREQUIRE_KV_REUSE, 0,
         agent_abi::E_KV_FLAG_COMBINATION},
        {"round_zero_reprefill", 0, agent_abi::kSqFlagsALLOW_REPREFILL, 0,
         agent_abi::E_KV_FLAG_COMBINATION},
        {"repair_no_path", 1, 0, 0, agent_abi::E_KV_FLAG_COMBINATION},
        {"repair_both_paths", 1,
         uint16_t(agent_abi::kSqFlagsREQUIRE_KV_REUSE |
                  agent_abi::kSqFlagsALLOW_REPREFILL),
         0, agent_abi::E_KV_FLAG_COMBINATION},
        {"reuse_path_not_reachable", 1,
         agent_abi::kSqFlagsREQUIRE_KV_REUSE, 0,
         agent_abi::E_REQUEST_PROFILE},
        {"cached_tokens_mismatch", 0, 0, 1,
         agent_abi::E_KV_TOKEN_MISMATCH},
    };
    for (const Case &item : cases) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        NpuExecutionRequest request = executionRequest();
        request.repairRound = item.round;
        request.kvFlags = item.flags;
        request.cachedTokens = item.cached_tokens;
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, item.expected_detail) << item.name;
        EXPECT_TRUE(harness.dispatch.selectors.empty()) << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
        EXPECT_FALSE(harness.manager.hasClaim(1)) << item.name;
        EXPECT_EQ(sink.completions, 0u) << item.name;
        EXPECT_TRUE(harness.manager.validate().empty()) << item.name;
    }
}

TEST(ServingMeshExecutor, MissingOrInconsistentSegmentsAreAContractError)
{
    const std::string fixture =
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb";
    struct Case
    {
        const char *name;
        uint64_t committed;
        uint32_t error;
        std::vector<DmaCommittedSegment> segments;
    };
    const Case cases[] = {
        {"missing_success", 64, 0, {}},
        {"missing_fault", 64, 7, {}},
        {"understated", 64, 7, {{0, 0, 32}}},
        {"overstated", 64, 7, {{0, 0, 64}, {0, 32, 16}}},
        {"out_of_range", 64, 7, {{9, 0, 64}}},
    };
    for (const Case &item : cases) {
        Harness harness(fixture);
        ASSERT_TRUE(harness.executor.submit(
            executionRequest(0x100300000, fixture)).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        ASSERT_GE(stores.size(), 2u);
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id,
                                       harness.currentInstance(), 1, 1, 200);
        harness.executor.onDmaTerminal(stores[0].descriptor_id,
                                       stores[0].command_id, item.committed,
                                       item.error, harness.currentInstance(), 1,
                                       1, 210, item.segments);
        EXPECT_TRUE(harness.executor.failed(1)) << item.name;
        EXPECT_EQ(harness.executor.phasesCommitted(1), 0u) << item.name;
        EXPECT_FALSE(harness.manager.fatalReason().has_value()) << item.name;
        EXPECT_TRUE(harness.manager.validate().empty()) << item.name;
    }
}

TEST(ServingMeshExecutor, InvalidSegmentsStillReachOneTerminalAfterDrain)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id,
                                   128, 0, harness.currentInstance(), 1, 1, 210,
                                   {});
    ASSERT_TRUE(harness.executor.failed(1));
    harness.settleFailedInstance(1, 1, 1, 220);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->outstanding_kv_dma, 0u);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, DuplicateOrWrappedSegmentsCannotProveSuccess)
{
    const std::vector<std::vector<DmaCommittedSegment>> cases = {
        {{0, 0, 64}, {0, 0, 64}},
        {{0, UINT64_MAX - 63, 128}},
    };
    for (const auto &segments : cases) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        ASSERT_FALSE(stores.empty());
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id,
                                       harness.currentInstance(), 1, 1, 200);
        harness.executor.onDmaTerminal(stores[0].descriptor_id,
                                       stores[0].command_id, 128, 0,
                                       harness.currentInstance(), 1, 1, 210,
                                       segments);
        EXPECT_TRUE(harness.executor.failed(1));
        harness.executor.onInstanceSettled(1, 1, 1, false, 220);
        EXPECT_TRUE(harness.executor.failed(1));
        EXPECT_EQ(harness.executor.phasesCommitted(1), 0u);
        EXPECT_EQ(sink.completions, 1u);
        const KvRecord *record = harness.manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        EXPECT_EQ(record->outstanding_kv_dma, 0u);
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
        EXPECT_TRUE(harness.manager.validate().empty());
    }
}

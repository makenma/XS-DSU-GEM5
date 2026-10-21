#include "dev/ai_mesh/serving_mesh_executor_test.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::serving_test;

class SharedServingExecution : public ::testing::TestWithParam<bool>
{
  protected:
    const std::string path = "tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb";
    Harness harness{loadFixture(path)};
    RecordingSink sink;
    std::array<NpuExecutionRequest, 2> requests;
    uint64_t tick = 110;

    void SetUp() override
    {
        harness.executor.setQuantumDriver([](uint64_t) {});
        harness.executor.attachSink(&sink);
        for (size_t arrival = 0; arrival < requests.size(); ++arrival) {
            const size_t i = GetParam() ? requests.size() - 1 - arrival : arrival;
            auto &request = requests[i];
            request = executionRequest(0x100300000 + i * 4096, path);
            request.requestId += i;
            request.sessionId += i;
            request.kvHandle += i;
            request.workloadPlanItemId = 2 - i;
            request.inputAddress += i * 4096;
            request.metadataAddress += i * 4096;
            request.outputTokens = request.maxOutputTokens = 1;
            request.inputDigest.fill(i + 1);
            for (auto &binding : request.hostBindings)
                if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                    binding.address = request.inputAddress;
            harness.executor.setTickSource([arrival] { return 100 + arrival; });
            ASSERT_TRUE(harness.executor.submit(request).admitted);
        }
        harness.dispatch.on_start = [this] {
            const auto &binding = harness.dispatch.instance_bindings.back();
            harness.executor.onInstanceStarted(harness.dispatch.generation_counter,
                binding.requestId(), binding.requestGeneration(), tick);
        };
    }

    void drainSelected(bool fault = false)
    {
        const auto binding = harness.dispatch.instance_bindings.back();
        const auto generation = harness.dispatch.generation_counter;
        const auto profile = harness.dispatch.selectors.back().second;
        if (!fault)
            harness.drainOutput(tick - 10);
        if (fault) {
            BatchFailureRecord record;
            record.batch_id = record.program_instance_id = generation;
            record.first.tick = tick + 1;
            record.first.code = agent_abi::E_AXI_RESPONSE;
            harness.executor.onInstanceFailure(record);
        }
        std::set<uint32_t> commands;
        for (const auto &range : binding.invocation()->executionView().ranges)
            commands.insert(range.command_ids.begin(), range.command_ids.end());
        for (const auto &descriptor : harness.program.transport.dma_descriptors) {
            if (descriptor.kind != mesh_abi::kDmaKindSTORE ||
                !commands.count(descriptor.command_id))
                continue;
            const auto access = binding.resolveDescriptorRows(descriptor.descriptor_id, false);
            ASSERT_TRUE(access);
            ASSERT_TRUE(access->member);
            const auto owner = *access->member;
            if (binding.requestId() == 0) {
                EXPECT_EQ(owner.request_id, 2 - owner.ordinal);
            }
            std::vector<DmaCommittedSegment> segments;
            for (uint32_t row = 0; row < descriptor.rows; ++row)
                segments.push_back({row, 0, descriptor.row_bytes});
            harness.executor.onDmaAccepted(descriptor.descriptor_id, descriptor.command_id,
                generation, owner.request_id, owner.generation, tick + 1);
            harness.executor.onDmaTerminal(descriptor.descriptor_id, descriptor.command_id,
                fault ? 0 : descriptor.useful_bytes, fault ? 2 : 0, generation,
                owner.request_id, owner.generation, tick + 2,
                fault ? std::vector<DmaCommittedSegment>{} : segments);
        }
        harness.executor.onInstanceSettled(generation, binding.requestId(),
            binding.requestGeneration(), fault, tick + 3);
    }
};

TEST_P(SharedServingExecution, FreezesTwoOwnersAndCommitsBothLedgers)
{
    for (unsigned phase = 0; phase < 3; ++phase) {
        harness.executor.onQuantumEdge(tick);
        ASSERT_EQ(harness.dispatch.selectors.size(), phase + 1);
        ASSERT_EQ(harness.dispatch.instance_bindings.size(), phase + 1);
        const auto &binding = harness.dispatch.instance_bindings.back();
        ASSERT_EQ(binding.requestId(), 0u);
        const uint32_t profile = harness.dispatch.selectors.back().second;
        const auto selected = std::find_if(harness.program.transport.agent_instance_profiles.begin(),
            harness.program.transport.agent_instance_profiles.end(), [profile](const auto &row) {
                return row.mesh_profile_id == profile;
            });
        ASSERT_NE(selected, harness.program.transport.agent_instance_profiles.end());
        ASSERT_EQ(selected->member_count, 2u);
        drainSelected();
        tick += 10;
        harness.executor.onQuantumEdge(tick);
        for (const auto &request : requests) {
            EXPECT_FALSE(harness.executor.failed(request.requestId));
            EXPECT_EQ(harness.executor.phasesCommitted(request.requestId), phase + 1);
            const auto &fact = harness.executor.phaseFacts(request.requestId).back();
            EXPECT_EQ(fact.request_id, request.requestId);
            EXPECT_EQ(fact.request_generation, request.generation);
            EXPECT_EQ(fact.instance_generation, phase + 1);
        }
        tick += 10;
    }
    EXPECT_EQ(sink.core_starts, 2u);
    EXPECT_EQ(sink.completions, 2u);
    for (const auto &request : requests) {
        EXPECT_TRUE(harness.executor.completionFor(request).succeeded());
        EXPECT_FALSE(harness.manager.hasPin(request.requestId));
    }
}

TEST_P(SharedServingExecution, CancelledFrozenMemberDrainsWithoutCancellingItsPeer)
{
    harness.executor.onQuantumEdge(tick);
    ASSERT_EQ(harness.dispatch.selectors.size(), 1u);
    ASSERT_EQ(harness.executor.requestCancel(1, tick + 1),
              ServingMeshExecutor::CancelResult::Accepted);
    harness.executor.onQuantumEdge(tick + 2);
    EXPECT_EQ(harness.dispatch.cancels, 0u);
    EXPECT_EQ(harness.dispatch.fails, 0u);
    tick += 3;
    drainSelected();
    tick += 10;
    harness.executor.onQuantumEdge(tick);
    EXPECT_EQ(harness.executor.completionFor(requests[0]).outcome,
              NpuExecutionOutcome::Cancelled);
    EXPECT_EQ(harness.executor.completionFor(requests[1]).outcome,
              NpuExecutionOutcome::Pending);
    for (unsigned phase = 0; phase < 2; ++phase) {
        tick += 10;
        harness.executor.onQuantumEdge(tick);
        ASSERT_EQ(harness.dispatch.instance_bindings.back().requestId(), 2u);
        drainSelected();
        tick += 10;
        harness.executor.onQuantumEdge(tick);
    }
    EXPECT_TRUE(harness.executor.completionFor(requests[1]).succeeded());
    EXPECT_EQ(sink.completions, 2u);
}

TEST_P(SharedServingExecution, SharedFaultTerminatesEveryFrozenOwnerAfterDrain)
{
    harness.executor.onQuantumEdge(tick);
    ASSERT_EQ(harness.dispatch.selectors.size(), 1u);
    drainSelected(true);
    EXPECT_EQ(sink.completions, 2u);
    for (const auto &request : requests) {
        EXPECT_EQ(harness.executor.completionFor(request).outcome, NpuExecutionOutcome::Error);
        ASSERT_TRUE(harness.executor.completionFor(request).failure);
        EXPECT_EQ(harness.executor.completionFor(request).failure->code,
                  uint32_t(agent_abi::E_AXI_RESPONSE));
        EXPECT_FALSE(harness.manager.hasPin(request.requestId));
        EXPECT_FALSE(harness.manager.hasClaim(request.requestId));
    }
    harness.executor.onInstanceSettled(1, 0, 0, true, tick + 4);
    EXPECT_EQ(sink.completions, 2u);
}

INSTANTIATE_TEST_SUITE_P(ArrivalOrder, SharedServingExecution, ::testing::Bool());

class ServingCandidates : public ::testing::Test
{
  protected:
    const std::string path = "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb";
    Harness harness{loadFixture(path)};
    void SetUp() override
    {
        harness.executor.setQuantumDriver([](uint64_t) {});
        harness.dispatch.on_start = {};
        for (uint64_t i = 0; i < 2; ++i) {
            auto request = executionRequest(0x100300000 + i * 4096, path);
            request.requestId += i;
            request.sessionId += i;
            request.kvHandle += i;
            request.inputAddress += i * 4096;
            request.metadataAddress += i * 4096;
            for (auto &binding : request.hostBindings)
                if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                    binding.address = request.inputAddress;
            harness.executor.setTickSource([i] { return 100 + i; });
            ASSERT_TRUE(harness.executor.submit(request).admitted);
        }
        harness.executor.onQuantumEdge(110);
        harness.executor.onQuantumEdge(120);
    }
};

TEST_F(ServingCandidates, FreezesAnotherBatchWhileTheFirstWaitsForStart)
{
    ASSERT_EQ(harness.dispatch.selectors.size(), 2u);
    ASSERT_EQ(harness.dispatch.instance_bindings.size(), 2u);
    EXPECT_EQ(harness.dispatch.instance_bindings[0].requestId(), 1u);
    EXPECT_EQ(harness.dispatch.instance_bindings[1].requestId(), 2u);
    harness.executor.onInstanceStarted(1, 1, 1, 130);
    harness.executor.onInstanceStarted(2, 2, 1, 140);
    EXPECT_FALSE(harness.executor.failed(1));
    EXPECT_FALSE(harness.executor.failed(2));
    harness.executor.onQuantumEdge(150);
    EXPECT_EQ(harness.dispatch.selectors.size(), 2u);
}

TEST_F(ServingCandidates, CancellingFrozenOwnerPreservesItsPendingInstance)
{
    harness.executor.onInstanceStarted(1, 1, 1, 130);
    EXPECT_EQ(harness.executor.requestCancel(2, 140), ServingMeshExecutor::CancelResult::Accepted);
    harness.executor.onQuantumEdge(150);
    EXPECT_EQ(harness.dispatch.cancels, 0u);
    EXPECT_EQ(harness.dispatch.fails, 0u);
    EXPECT_TRUE(harness.manager.hasPin(2));
    EXPECT_TRUE(harness.manager.hasPin(1));
    harness.executor.onInstanceStarted(2, 2, 1, 160);
    EXPECT_FALSE(harness.executor.failed(2));
    EXPECT_FALSE(harness.executor.failed(1));
}

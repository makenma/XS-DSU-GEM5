#include "dev/ai_mesh/serving_mesh_executor_test.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::serving_test;

namespace
{

constexpr const char *PrefillFixture = "tests/gem5/ai_mesh/fixtures/gate6/serving_prefill.mshb";

NpuExecutionRequest prefillRequest()
{
    auto request = executionRequest(0x100300000, PrefillFixture);
    request.kvHandle = request.generation = 0;
    request.outputTokens = request.maxOutputTokens = 0;
    for (auto &binding : request.hostBindings) {
        if (binding.kind == agent_abi::kBindingKindREQUEST_WORKSPACE) {
            binding.address = 0x800200000;
            binding.bytes = 64;
        } else if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
            binding.address = 0x800001000;
            binding.bytes = 128;
        }
    }
    return request;
}

}

TEST(ServingPrefill, WorkspaceReservesAnIntervalAndChangesAccessAcrossPhases)
{
    test_support::AdmittedFixture fixture(PrefillFixture);
    auto request = prefillRequest();
    ServingBatchMember member;
    member.request = &request;
    ServingCapacityRequirement required;
    MeshLoadError error;
    ASSERT_TRUE(servingCapacityRequired(fixture.program, required, error));
    EXPECT_EQ(required.batch_interval_entries, 5u);
    MeshServingBatch batch(required);
    for (const auto &instance : fixture.program.transport.agent_instance_profiles) {
        std::string reason;
        ASSERT_EQ(batch.freeze(fixture.admission, instance, &member, 1, reason),
            MeshServingBatch::FreezeResult::Frozen) << reason;
        EXPECT_EQ(batch.intervalCount(), 5u);
        bool found = false;
        for (size_t index = 0; index < batch.memberBindingCount(); ++index) {
            const auto &row = *batch.memberBinding(index);
            if (row.kind != agent_abi::kBindingKindREQUEST_WORKSPACE)
                continue;
            found = true;
            EXPECT_FALSE(row.kv_view);
            const bool write = instance.phase == mesh_abi::kPhasePREFILL;
            EXPECT_EQ(slotAddress(batch.executionBinding(), row.slot_id, 0, 64, write), 0x800200000);
            EXPECT_FALSE(slotAddress(batch.executionBinding(), row.slot_id, 0, 64, !write));
        }
        EXPECT_TRUE(found);
        batch.release();
    }
}

TEST(ServingPrefill, WorkspaceCannotAliasWeightsOrExceedItsDeclaredCapacity)
{
    test_support::AdmittedFixture fixture(PrefillFixture);
    ServingCapacityRequirement required;
    MeshLoadError error;
    ASSERT_TRUE(servingCapacityRequired(fixture.program, required, error));
    for (const bool alias : {false, true}) {
        auto request = prefillRequest();
        for (auto &row : request.hostBindings)
            if (row.kind == agent_abi::kBindingKindREQUEST_WORKSPACE) {
                if (alias)
                    row.address = 0x800001000;
                else
                    row.bytes = 32;
            }
        ServingBatchMember member;
        member.request = &request;
        MeshServingBatch batch(required);
        std::string reason;
        EXPECT_EQ(batch.freeze(fixture.admission, fixture.program.transport.agent_instance_profiles.front(),
            &member, 1, reason), MeshServingBatch::FreezeResult::Invalid);
        EXPECT_FALSE(batch.frozen());
    }
}

TEST(ServingPrefill, InsufficientWorkspaceIntervalCapacityRejectsBeforeFreeze)
{
    test_support::AdmittedFixture fixture(PrefillFixture);
    auto request = prefillRequest();
    ServingBatchMember member;
    member.request = &request;
    MeshServingBatch batch({1, 2, 4});
    std::string reason;
    EXPECT_EQ(batch.freeze(fixture.admission, fixture.program.transport.agent_instance_profiles.front(),
        &member, 1, reason), MeshServingBatch::FreezeResult::NoCapacity);
    EXPECT_FALSE(batch.frozen());
}

TEST(ServingPrefill, CompletesComputedPublicationWithoutKvOrFillAndRetainsLeaseUntilRetirement)
{
    Harness harness(PrefillFixture);
    auto request = prefillRequest();
    harness.executor.setDigestSource({});
    harness.dispatch.on_start = [&]() {
        harness.executor.onInstanceStarted(harness.currentInstance(), request.requestId, request.generation, harness.executor.lastTick() + 1);
    };
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    ASSERT_EQ(harness.dispatch.instance_bindings.size(), 1u);
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(1), 0u);
    harness.executor.onInstanceSettled(1, 1, 0, false, 200);
    ASSERT_EQ(harness.dispatch.instance_bindings.size(), 2u);
    EXPECT_TRUE(harness.dispatch.fills.empty());
    harness.drainOutput(300);
    harness.executor.onInstanceSettled(2, 1, 0, false, 320);
    auto completion = harness.executor.completionFor(request);
    ASSERT_TRUE(completion.succeeded());
    EXPECT_FALSE(completion.kvTerminal);
    EXPECT_EQ(completion.phaseInstances, 2u);
    EXPECT_EQ(completion.outputTokens, 0u);
    EXPECT_EQ(completion.outputBytes, 64u);
    EXPECT_EQ(completion.semanticDigest, (std::array<uint8_t, 32>{}));
    auto next = request;
    next.requestId = 2;
    const auto collision = harness.executor.submit(next);
    EXPECT_FALSE(collision.admitted);
    EXPECT_EQ(collision.rejectDetail, agent_abi::E_BINDING_ALIAS_MISMATCH);
    harness.executor.retireCompletedRequest(1);
    ASSERT_TRUE(harness.admitOnly(next, 400).admitted);
    std::string reason;
    ASSERT_TRUE(harness.executor.abortBeforeStart(2, 410, reason)) << reason;
    EXPECT_FALSE(harness.executor.completionFor(next).kvTerminal);
    harness.executor.retireCompletedRequest(2);
}

TEST(ServingPrefill, InstanceFailureDrainsAndRetiresWithoutKvOwnerTerminal)
{
    Harness harness(PrefillFixture);
    const auto request = prefillRequest();
    harness.dispatch.on_start = [&]() {
        harness.executor.onInstanceStarted(harness.currentInstance(), 1, 0, 150);
    };
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    harness.settleFailedInstance(1, 1, 0, 200);
    const auto completion = harness.executor.completionFor(request);
    EXPECT_EQ(completion.outcome, NpuExecutionOutcome::Error);
    EXPECT_FALSE(completion.kvTerminal);
    EXPECT_EQ(completion.outputBytes, 0u);
    EXPECT_FALSE(harness.manager.fatalReason());
    harness.executor.retireCompletedRequest(1);
}

TEST(ServingPrefill, CancellationWaitsForCoreDrainAndKeepsWorkspaceUntilRetirement)
{
    Harness harness(PrefillFixture);
    auto request = prefillRequest();
    harness.dispatch.kv_reachable = false;
    harness.dispatch.on_start = [&]() {
        harness.executor.onInstanceStarted(harness.currentInstance(), 1, 0, 150);
    };
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    EXPECT_EQ(harness.executor.requestCancel(1, 160), ServingMeshExecutor::CancelResult::Accepted);
    EXPECT_EQ(harness.executor.completionFor(request).outcome, NpuExecutionOutcome::Pending);
    harness.executor.onInstanceSettled(1, 1, 0, false, 200);
    EXPECT_EQ(harness.executor.completionFor(request).outcome, NpuExecutionOutcome::Cancelled);
    EXPECT_FALSE(harness.executor.completionFor(request).kvTerminal);
    EXPECT_EQ(harness.dispatch.instance_bindings.size(), 1u);
    auto next = request;
    next.requestId = 2;
    EXPECT_EQ(harness.admitOnly(next, 300).rejectDetail, agent_abi::E_BINDING_ALIAS_MISMATCH);
    harness.executor.retireCompletedRequest(1);
    ASSERT_TRUE(harness.admitOnly(next, 400).admitted);
    std::string reason;
    ASSERT_TRUE(harness.executor.abortBeforeStart(2, 410, reason)) << reason;
    harness.executor.retireCompletedRequest(2);
    EXPECT_FALSE(harness.manager.fatalReason());
}

TEST(ServingPrefill, PublishFailureReportsOnlyCommittedOutputAndNoKvTerminal)
{
    Harness harness(PrefillFixture);
    const auto request = prefillRequest();
    harness.dispatch.on_start = [&]() {
        harness.executor.onInstanceStarted(harness.currentInstance(), 1, 0, harness.executor.lastTick() + 1);
    };
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    harness.executor.onInstanceSettled(1, 1, 0, false, 200);
    ASSERT_EQ(harness.dispatch.instance_bindings.size(), 2u);
    const auto &ledger = harness.dispatch.instance_bindings.back().outputLedger();
    ASSERT_TRUE(ledger);
    for (const auto &descriptor : harness.program.transport.dma_descriptors) {
        if (!ledger->contains(descriptor.descriptor_id))
            continue;
        ASSERT_TRUE(ledger->issue(descriptor.descriptor_id, 0, 32, 210));
        ASSERT_TRUE(ledger->commit(descriptor.descriptor_id, 0, 32, true, 220));
        ASSERT_TRUE(ledger->issue(descriptor.descriptor_id, 32, 32, 230));
        ASSERT_TRUE(ledger->commit(descriptor.descriptor_id, 32, 32, false, 240));
    }
    harness.settleFailedInstance(2, 1, 0, 250);
    const auto completion = harness.executor.completionFor(request);
    EXPECT_EQ(completion.outcome, NpuExecutionOutcome::Error);
    EXPECT_EQ(completion.outputBytes, 32u);
    EXPECT_EQ(completion.phaseInstances, 1u);
    EXPECT_FALSE(completion.kvTerminal);
    EXPECT_FALSE(harness.manager.fatalReason());
    harness.executor.retireCompletedRequest(1);
}

TEST(ServingPrefill, LiveWorkspaceAndWeightRangesAreProtectedInBothDirections)
{
    for (const bool workspace_is_incoming : {false, true}) {
        Harness harness(PrefillFixture);
        const auto request = prefillRequest();
        ASSERT_TRUE(harness.admitOnly(request, 100).admitted);
        auto next = request;
        next.requestId = 2;
        for (auto &binding : next.hostBindings) {
            if (binding.kind == agent_abi::kBindingKindREQUEST_WORKSPACE)
                binding.address = workspace_is_incoming ? 0x800001000 : 0x800300000;
            if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL)
                binding.address = workspace_is_incoming ? 0x800004000 : 0x800200000;
        }
        const auto admission = harness.admitOnly(next, 110);
        EXPECT_FALSE(admission.admitted);
        EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ALIAS_MISMATCH);
        std::string reason;
        ASSERT_TRUE(harness.executor.abortBeforeStart(1, 120, reason)) << reason;
        harness.executor.retireCompletedRequest(1);
    }
}

TEST(ServingPrefill, DisjointWorkspaceRequestsDoNotCompeteForKvSlots)
{
    Harness harness(PrefillFixture);
    harness.occupyKvSlots();
    const auto first = prefillRequest();
    auto second = first;
    second.requestId = 2;
    for (auto &binding : second.hostBindings)
        if (binding.kind == agent_abi::kBindingKindREQUEST_WORKSPACE)
            binding.address += 0x1000;
    ASSERT_TRUE(harness.admitOnly(first, 100).admitted);
    ASSERT_TRUE(harness.admitOnly(second, 100).admitted);
    std::string reason;
    for (const auto id : {1u, 2u}) {
        EXPECT_FALSE(harness.manager.hasClaim(id));
        EXPECT_FALSE(harness.manager.hasPin(id));
        ASSERT_TRUE(harness.executor.abortBeforeStart(id, 200, reason)) << reason;
        harness.executor.retireCompletedRequest(id);
    }
    EXPECT_FALSE(harness.manager.fatalReason());
}

TEST(ServingPrefill, UnexpectedInstanceProducesAValidInternalFailureSource)
{
    Harness harness(PrefillFixture);
    const auto request = prefillRequest();
    harness.dispatch.on_start = [&]() { harness.executor.onInstanceStarted(1, 1, 0, 150); };
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    harness.executor.onInstanceStarted(2, 1, 0, 160);
    ASSERT_EQ(harness.dispatch.failures.size(), 1u);
    EXPECT_TRUE(validErrorSource(harness.dispatch.failures.front().source));
    EXPECT_EQ(harness.dispatch.failures.front().source.domain, mesh_abi::kMeshObjectDomainINTERNAL);
}

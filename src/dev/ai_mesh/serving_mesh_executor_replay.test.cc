#include "dev/ai_mesh/serving_mesh_executor_test.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::serving_test;

TEST(ServingMeshReplay, BoundaryIncludesTransitiveCohortsAndTheirPredecessors)
{
    Harness harness;
    ServingBatchReplay replay;
    for (const auto &items : {std::vector<uint32_t>{2}, {1, 2}, {3}, {2, 3}, {4}}) {
        ServingReplayBatch batch;
        batch.ordinal = replay.batches.size();
        for (const auto item : items)
            batch.members.push_back({{item, 0, 0, 0}, 0, 1, 0});
        replay.batches.push_back(batch);
    }
    harness.executor.configureReplay(std::move(replay), true);
    EXPECT_EQ(harness.executor.replayBoundaryAdmissions({1}), (std::set<uint32_t>{2, 3}));
    EXPECT_EQ(harness.executor.replayBoundaryAdmissions({3}), (std::set<uint32_t>{1, 2}));
    EXPECT_TRUE(harness.executor.replayBoundaryAdmissions({}).empty());
    EXPECT_TRUE(harness.executor.replayBoundaryAdmissions({9}).empty());
}

TEST(ServingMeshReplay, WaitsForRecordedMemberInsteadOfSchedulingReadyPeer)
{
    Harness harness;
    harness.executor.setQuantumDriver([](uint64_t) {});
    harness.executor.setTickSource([] { return 100; });
    ServingBatchReplay replay;
    replay.batches.push_back({0, mesh_abi::kPhasePREFILL, 0, 11,
        {{{2, 0, 0, 0}, 0, 8, 0}}});
    replay.batches.push_back({1, mesh_abi::kPhasePREFILL, 0, 11,
        {{{1, 0, 0, 0}, 0, 8, 0}}});
    harness.executor.configureReplay(std::move(replay), true);
    auto first = executionRequest();
    first.workloadPlanItemId = 1;
    first.batchReplay = true;
    ASSERT_TRUE(harness.executor.submit(first).admitted);
    harness.executor.onQuantumEdge(101);
    EXPECT_TRUE(harness.dispatch.selectors.empty());
    auto second = executionRequest(0x100400000);
    second.requestId = 2;
    second.sessionId = second.kvHandle = 12;
    second.workloadPlanItemId = 2;
    second.batchReplay = true;
    ASSERT_TRUE(harness.executor.submit(second).admitted);
    harness.dispatch.on_start = [] {};
    harness.dispatch.replay_quiescent = false;
    harness.executor.onQuantumEdge(102);
    EXPECT_TRUE(harness.dispatch.selectors.empty());
    harness.dispatch.replay_quiescent = true;
    harness.executor.onQuantumEdge(103);
    ASSERT_EQ(harness.dispatch.instance_bindings.size(), 1u);
    EXPECT_EQ(harness.dispatch.instance_bindings.back().requestId(), 2u);
    harness.executor.onQuantumEdge(103);
    EXPECT_EQ(harness.dispatch.instance_bindings.size(), 1u);
}

TEST(ServingMeshReplay, RejectsWrongReadyPhaseBeforeStartingAnyCore)
{
    Harness harness;
    harness.executor.setQuantumDriver([](uint64_t) {});
    harness.executor.setTickSource([] { return 100; });
    ServingBatchReplay replay;
    replay.batches.push_back({0, mesh_abi::kPhaseDECODE, 0, 12,
        {{{0, 0, 0, 0}, 0, 1, 0}}});
    harness.executor.configureReplay(std::move(replay), false);
    auto request = executionRequest();
    request.batchReplay = true;
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    EXPECT_ANY_THROW(harness.executor.onQuantumEdge(101));
}

TEST(ServingMeshReplay, RejectsFlagWithoutInstalledArtifact)
{
    Harness harness;
    auto request = executionRequest();
    request.batchReplay = true;
    const auto result = harness.executor.submit(request);
    EXPECT_FALSE(result.admitted);
    EXPECT_EQ(result.rejectDetail, agent_abi::E_BATCH_REPLAY_DIVERGENCE);
    EXPECT_TRUE(harness.dispatch.selectors.empty());
}

TEST(ServingMeshReplay, RejectsAnExtraFlaggedIdentity)
{
    Harness harness;
    ServingBatchReplay replay;
    replay.batches.push_back({0, mesh_abi::kPhasePREFILL, 0, 11,
        {{{2, 0, 0, 0}, 0, 8, 0}}});
    harness.executor.configureReplay(std::move(replay), false);
    auto request = executionRequest();
    request.batchReplay = true;
    EXPECT_FALSE(harness.executor.submit(request).admitted);
    EXPECT_FALSE(harness.manager.hasPin(request.requestId));
}

TEST(ServingMeshReplay, PrematureTerminalAndUnconsumedRunCannotDisappear)
{
    Harness harness;
    harness.executor.setQuantumDriver([](uint64_t) {});
    harness.executor.setTickSource([] { return 100; });
    ServingBatchReplay replay;
    replay.batches.push_back({0, mesh_abi::kPhasePREFILL, 0, 11,
        {{{0, 0, 0, 0}, 0, 8, 0}}});
    harness.executor.configureReplay(std::move(replay), true);
    auto request = executionRequest();
    request.batchReplay = true;
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    EXPECT_EQ(harness.executor.requestCancel(1, 101),
              ServingMeshExecutor::CancelResult::Accepted);
    EXPECT_ANY_THROW(harness.executor.onQuantumEdge(102));
    EXPECT_ANY_THROW(harness.executor.retireCompletedRequest(1));
    EXPECT_ANY_THROW(harness.executor.finishServingRun());
}

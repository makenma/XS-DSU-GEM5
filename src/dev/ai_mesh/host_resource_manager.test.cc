#include <gtest/gtest.h>

#include <array>
#include <vector>

#include "dev/ai_mesh/host_resource_manager.hh"

namespace
{

using gem5::ai_mesh::HostEnqueueResult;
using gem5::ai_mesh::HostResourceManager;
using gem5::ai_mesh::HostServiceTask;
using gem5::ai_mesh::HostStageKindV1;
using gem5::ai_mesh::HostTaskId;
using gem5::ai_mesh::HostTaskState;

constexpr size_t kCompile = static_cast<size_t>(HostStageKindV1::Compile);
constexpr size_t kTest = static_cast<size_t>(HostStageKindV1::Test);
constexpr size_t kLogParse = static_cast<size_t>(HostStageKindV1::LogParse);

HostServiceTask
makeTask(uint64_t id, HostStageKindV1 kind, uint16_t tokens,
         uint64_t arrival, uint32_t user = 0, uint32_t taskSeq = 0,
         uint16_t round = 0)
{
    HostServiceTask task;
    task.hostTaskId = HostTaskId(id);
    task.kind = kind;
    task.arrivalTick = arrival;
    task.userId = user;
    task.taskSeq = taskSeq;
    task.repairRound = round;
    task.hostTokensRequired = tokens;
    return task;
}

std::array<uint64_t, 3>
ids(const std::vector<HostTaskId> &granted)
{
    return {granted[0].value(), granted[1].value(), granted[2].value()};
}

}

class HostResourceManagerTest : public ::testing::Test
{
  protected:
    HostResourceManager manager{
        {1, 1, 1}, {4, 4, 4}, 4, {4, 3, 2}, 16};
};

TEST_F(HostResourceManagerTest, SwrrRoundRobinsAcrossKindsWithWeights)
{
    EXPECT_EQ(manager.tryEnqueue(makeTask(1, HostStageKindV1::Compile, 1, 1)),
              HostEnqueueResult::Accepted);
    EXPECT_EQ(manager.tryEnqueue(makeTask(2, HostStageKindV1::Test, 1, 2)),
              HostEnqueueResult::Accepted);
    EXPECT_EQ(manager.tryEnqueue(makeTask(3, HostStageKindV1::LogParse, 1, 3)),
              HostEnqueueResult::Accepted);
    EXPECT_EQ(manager.tryEnqueue(makeTask(4, HostStageKindV1::Compile, 1, 4)),
              HostEnqueueResult::Accepted);
    EXPECT_EQ(manager.tryEnqueue(makeTask(5, HostStageKindV1::Test, 1, 5)),
              HostEnqueueResult::Accepted);
    EXPECT_EQ(manager.tryEnqueue(makeTask(6, HostStageKindV1::LogParse, 1, 6)),
              HostEnqueueResult::Accepted);

    const auto first = manager.arbitrate(100);
    ASSERT_EQ(first.size(), 3u);
    EXPECT_EQ(ids(first), (std::array<uint64_t, 3>{1, 2, 3}));
    EXPECT_EQ(manager.score(kCompile), -5);
    EXPECT_EQ(manager.score(kTest), 1);
    EXPECT_EQ(manager.score(kLogParse), 4);
    EXPECT_EQ(manager.availableTokens(), 1u);

    EXPECT_TRUE(manager.release(HostTaskId(1)));
    const auto second = manager.arbitrate(100);
    ASSERT_EQ(second.size(), 1u);
    EXPECT_EQ(second[0].value(), 4u);
    EXPECT_TRUE(manager.markRunning(HostTaskId(4)));

    EXPECT_TRUE(manager.release(HostTaskId(2)));
    const auto third = manager.arbitrate(100);
    ASSERT_EQ(third.size(), 1u);
    EXPECT_EQ(third[0].value(), 5u);

    EXPECT_TRUE(manager.release(HostTaskId(3)));
    const auto fourth = manager.arbitrate(100);
    ASSERT_EQ(fourth.size(), 1u);
    EXPECT_EQ(fourth[0].value(), 6u);
}

TEST_F(HostResourceManagerTest, TieBreaksPreferCompileThenTest)
{
    HostResourceManager tieManager{{1, 1, 1}, {4, 4, 4}, 3, {2, 2, 2}, 8};
    tieManager.tryEnqueue(makeTask(1, HostStageKindV1::LogParse, 1, 1));
    tieManager.tryEnqueue(makeTask(2, HostStageKindV1::Test, 1, 1));
    tieManager.tryEnqueue(makeTask(3, HostStageKindV1::Compile, 1, 1));
    const auto granted = tieManager.arbitrate(10);
    ASSERT_EQ(granted.size(), 3u);
    EXPECT_EQ(ids(granted), (std::array<uint64_t, 3>{3, 2, 1}));
}

TEST_F(HostResourceManagerTest, SlotAndTokenAcquisitionIsAtomic)
{
    HostResourceManager atomicManager{{1, 1, 1}, {4, 4, 4}, 2, {4, 3, 2}, 8};
    atomicManager.tryEnqueue(makeTask(1, HostStageKindV1::Compile, 2, 1));
    atomicManager.tryEnqueue(makeTask(2, HostStageKindV1::Compile, 2, 2));
    atomicManager.tryEnqueue(makeTask(3, HostStageKindV1::Test, 2, 3));
    const auto granted = atomicManager.arbitrate(10);
    ASSERT_EQ(granted.size(), 1u);
    EXPECT_EQ(granted[0].value(), 1u);
    EXPECT_EQ(atomicManager.state(HostTaskId(2)), HostTaskState::Queued);
    EXPECT_EQ(atomicManager.state(HostTaskId(3)), HostTaskState::Queued);
    EXPECT_EQ(atomicManager.freeSlots(kTest), 1u);
    EXPECT_EQ(atomicManager.availableTokens(), 0u);
    EXPECT_EQ(atomicManager.score(kCompile), -3);
    EXPECT_EQ(atomicManager.score(kTest), 3);
}

TEST_F(HostResourceManagerTest, ScoresFreezeWhenNothingIsEligible)
{
    manager.tryEnqueue(makeTask(1, HostStageKindV1::Compile, 1, 1));
    manager.tryEnqueue(makeTask(2, HostStageKindV1::Test, 1, 1));
    manager.tryEnqueue(makeTask(3, HostStageKindV1::LogParse, 1, 1));
    ASSERT_EQ(manager.arbitrate(10).size(), 3u);
    const auto frozen = manager.arbitrate(20);
    EXPECT_TRUE(frozen.empty());
    EXPECT_EQ(manager.score(kCompile), -5);
    EXPECT_EQ(manager.score(kTest), 1);
    EXPECT_EQ(manager.score(kLogParse), 4);
}

TEST_F(HostResourceManagerTest, QueueFullWaitersAreAdmittedInArrivalOrder)
{
    HostResourceManager deepManager{{1, 1, 1}, {1, 1, 1}, 4, {4, 3, 2}, 8};
    deepManager.tryEnqueue(makeTask(1, HostStageKindV1::Compile, 1, 3, 2));
    deepManager.tryEnqueue(makeTask(2, HostStageKindV1::Compile, 1, 1, 1));
    deepManager.tryEnqueue(makeTask(3, HostStageKindV1::Compile, 1, 2, 1));
    EXPECT_EQ(deepManager.state(HostTaskId(1)), HostTaskState::Queued);
    EXPECT_EQ(deepManager.state(HostTaskId(2)), HostTaskState::WaitHostEnqueue);
    EXPECT_EQ(deepManager.state(HostTaskId(3)), HostTaskState::WaitHostEnqueue);
    EXPECT_EQ(deepManager.queueLength(kCompile), 1u);
    EXPECT_EQ(deepManager.waitingCount(), 2u);

    const auto granted = deepManager.arbitrate(10);
    ASSERT_EQ(granted.size(), 1u);
    EXPECT_EQ(granted[0].value(), 1u);
    EXPECT_TRUE(deepManager.release(HostTaskId(1)));
    EXPECT_EQ(deepManager.state(HostTaskId(2)), HostTaskState::Queued);
    EXPECT_EQ(deepManager.state(HostTaskId(3)), HostTaskState::WaitHostEnqueue);
    ASSERT_EQ(deepManager.arbitrate(10).size(), 1u);
    EXPECT_TRUE(deepManager.release(HostTaskId(2)));
    EXPECT_EQ(deepManager.state(HostTaskId(3)), HostTaskState::Queued);
}

TEST_F(HostResourceManagerTest, AgingIsRecheckedBeforeEverySwrrPick)
{
    HostResourceManager manager{{1, 1, 2}, {4, 4, 4}, 3, {4, 3, 2}, 8};
    manager.setAgingThresholdTicks(10);
    manager.tryEnqueue(makeTask(1, HostStageKindV1::LogParse, 1, 1));
    manager.tryEnqueue(makeTask(2, HostStageKindV1::LogParse, 2, 2));
    manager.tryEnqueue(makeTask(3, HostStageKindV1::Compile, 2, 99));
    const auto granted = manager.arbitrate(100);
    ASSERT_EQ(granted.size(), 2u);
    EXPECT_EQ(granted[0].value(), 1u);
    EXPECT_EQ(granted[1].value(), 2u);
    EXPECT_EQ(manager.state(HostTaskId(3)), HostTaskState::Queued);
    EXPECT_EQ(manager.availableTokens(), 0u);
    EXPECT_FALSE(manager.hasAgingReservation());
}

TEST_F(HostResourceManagerTest, AgingReservationPausesAllocationUntilTargetRuns)
{
    manager.tryEnqueue(makeTask(1, HostStageKindV1::Test, 3, 1));
    ASSERT_EQ(manager.arbitrate(10).size(), 1u);
    manager.tryEnqueue(makeTask(2, HostStageKindV1::Compile, 3, 10));
    manager.tryEnqueue(makeTask(3, HostStageKindV1::LogParse, 1, 20));
    manager.setAgingThresholdTicks(100);

    const auto blocked = manager.arbitrate(1000);
    EXPECT_TRUE(blocked.empty());
    EXPECT_TRUE(manager.hasAgingReservation());
    EXPECT_EQ(manager.state(HostTaskId(3)), HostTaskState::Queued);
    EXPECT_EQ(manager.score(kCompile), 0);
    EXPECT_EQ(manager.score(kTest), 0);
    EXPECT_EQ(manager.score(kLogParse), 0);

    EXPECT_TRUE(manager.release(HostTaskId(1)));
    const auto resumed = manager.arbitrate(1001);
    ASSERT_EQ(resumed.size(), 2u);
    EXPECT_EQ(resumed[0].value(), 2u);
    EXPECT_EQ(resumed[1].value(), 3u);
    EXPECT_FALSE(manager.hasAgingReservation());
    EXPECT_EQ(manager.score(kCompile), 0);
    EXPECT_EQ(manager.score(kLogParse), 0);
}

TEST_F(HostResourceManagerTest, ReleaseReturnsResourcesExactlyOnce)
{
    manager.tryEnqueue(makeTask(1, HostStageKindV1::Compile, 2, 1));
    ASSERT_EQ(manager.arbitrate(10).size(), 1u);
    EXPECT_TRUE(manager.markRunning(HostTaskId(1)));
    EXPECT_EQ(manager.state(HostTaskId(1)), HostTaskState::RunningTimer);
    EXPECT_FALSE(manager.markRunning(HostTaskId(1)));
    EXPECT_TRUE(manager.release(HostTaskId(1)));
    EXPECT_FALSE(manager.release(HostTaskId(1)));
    EXPECT_EQ(manager.availableTokens(), 4u);
    EXPECT_EQ(manager.freeSlots(kCompile), 1u);
    EXPECT_EQ(manager.state(HostTaskId(1)), HostTaskState::Released);
}

TEST_F(HostResourceManagerTest, RegistryCapacityRejectsFurtherAdmission)
{
    HostResourceManager tinyManager{{1, 1, 1}, {1, 1, 1}, 4, {4, 3, 2}, 1};
    EXPECT_EQ(tinyManager.tryEnqueue(makeTask(1, HostStageKindV1::Compile, 1, 1)),
              HostEnqueueResult::Accepted);
    EXPECT_EQ(tinyManager.tryEnqueue(makeTask(2, HostStageKindV1::Compile, 1, 2)),
              HostEnqueueResult::RejectedRegistryFull);
}

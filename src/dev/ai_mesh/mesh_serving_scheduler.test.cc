#include <gtest/gtest.h>

#include <functional>
#include <fstream>
#include <vector>

#include "dev/ai_mesh/mesh_serving_scheduler.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

using Phase = SchedulerPhase;

TEST(ServingSchedulerTest, MatchesIndependentGoldenDecisions)
{
    std::ifstream input("tests/gem5/ai_mesh/fixtures/gate6/scheduler.golden");
    ASSERT_TRUE(input.good());
    uint32_t cases;
    ASSERT_TRUE(input >> cases);
    ASSERT_EQ(cases, 14u);
    for (uint32_t index = 0; index < cases; ++index) {
        SCOPED_TRACE(index);
        uint64_t now, period;
        SchedulerLimits limits;
        uint32_t count;
        ASSERT_TRUE(input >> now >> period >> limits.maxActiveSequences >> limits.maxBatchTokens
            >> limits.maxPrefillTokensPerBatch >> limits.maxDecodeSequencesPerBatch
            >> limits.prefillStarvationCycles >> limits.batchWaitCycles >> count);
        std::vector<uint32_t> profiles(count);
        for (auto &value : profiles)
            ASSERT_TRUE(input >> value);
        ASSERT_TRUE(input >> count);
        std::vector<SchedulerMember> members(count);
        for (auto &member : members) {
            uint32_t phase, qos, path;
            ASSERT_TRUE(input >> member.requestId >> phase >> member.phaseReadyTick
                >> member.deadlineTick >> qos >> member.programId >> member.requestProfileId
                >> path >> member.validTokensPerMember >> member.kvTokensBefore
                >> member.decodeChunkTokens >> member.logicalSourceRank);
            member.phase = static_cast<SchedulerPhase>(phase);
            member.qos = qos;
            member.pathKind = path;
        }
        uint32_t action, phase, size, qos;
        uint64_t wait;
        ASSERT_TRUE(input >> action >> phase >> size >> qos >> wait >> count);
        std::vector<uint64_t> expected(count);
        for (auto &value : expected)
            ASSERT_TRUE(input >> value);
        const auto decision = servingSchedulerDecide(members, limits, now,
            [profiles](const auto &) { return profiles; }, period);
        EXPECT_EQ(static_cast<uint32_t>(decision.action), action);
        EXPECT_EQ(static_cast<uint32_t>(decision.phase), phase);
        EXPECT_EQ(decision.memberCount, size);
        EXPECT_EQ(decision.batchEffectiveQos, qos);
        EXPECT_EQ(decision.waitUntil, wait);
        EXPECT_EQ(decision.members, expected);
    }
}

SchedulerMember
member(uint64_t id, Phase phase, uint64_t ready = 1000,
       uint64_t deadline = kSchedulerMaxTick, uint8_t qos = 0,
       uint32_t tokens = 8, uint32_t kvBefore = 0, uint32_t chunk = 0)
{
    SchedulerMember value;
    value.requestId = id;
    value.phase = phase;
    value.phaseReadyTick = ready;
    value.deadlineTick = deadline;
    value.qos = qos;
    value.validTokensPerMember = tokens;
    value.kvTokensBefore = kvBefore;
    value.decodeChunkTokens = chunk;
    return value;
}

SchedulerLimits
limits()
{
    SchedulerLimits value;
    value.maxActiveSequences = 4;
    value.maxBatchTokens = 64;
    value.maxPrefillTokensPerBatch = 32;
    value.maxDecodeSequencesPerBatch = 4;
    value.prefillStarvationCycles = 5000;
    value.batchWaitCycles = 2000;
    return value;
}

std::function<std::vector<uint32_t>(const ServingBatchKey &)>
counts(std::vector<uint32_t> values)
{
    return [values](const ServingBatchKey &) { return values; };
}

TEST(ServingSchedulerTest, PublishOutranksEverythingEvenAgedPrefill)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Prefill, 0), member(2, Phase::Publish),
         member(3, Phase::Decode, 990'000)},
        limits(), 1'000'000'000, counts({1}));
    EXPECT_EQ(decision.action, SchedulerAction::Run);
    EXPECT_EQ(decision.phase, Phase::Publish);
    EXPECT_EQ(decision.members, std::vector<uint64_t>({2}));
}

TEST(ServingSchedulerTest, AgedPrefillBeatsDecode)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Decode, 990'000), member(2, Phase::Prefill, 0)},
        limits(), 1'000'000, counts({1}));
    EXPECT_EQ(decision.phase, Phase::Prefill);
    EXPECT_EQ(decision.members, std::vector<uint64_t>({2}));
}

TEST(ServingSchedulerTest, YoungPrefillLosesToDecode)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Decode, 990'000),
         member(2, Phase::Prefill, 998'000)},
        limits(), 1'000'000, counts({1}));
    EXPECT_EQ(decision.phase, Phase::Decode);
    EXPECT_EQ(decision.members, std::vector<uint64_t>({1}));
}

TEST(ServingSchedulerTest, PrefillUsedWhenNoDecode)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Prefill, 900'000)}, limits(), 1'000'000,
        counts({1}));
    EXPECT_EQ(decision.phase, Phase::Prefill);
    EXPECT_EQ(decision.members, std::vector<uint64_t>({1}));
}

TEST(ServingSchedulerTest, EmptySnapshotIdles)
{
    const SchedulerDecision decision =
        servingSchedulerDecide({}, limits(), 5, counts({1}));
    EXPECT_EQ(decision.action, SchedulerAction::Idle);
}

TEST(ServingSchedulerTest, OrderingIsDeadlineThenQosThenAgeThenId)
{
    const std::vector<SchedulerMember> members = {
        member(7, Phase::Publish, 500, kSchedulerMaxTick, 1),
        member(3, Phase::Publish, 400, kSchedulerMaxTick, 2),
        member(9, Phase::Publish, 100, 50, 2),
        member(4, Phase::Publish, 300, 90, 1),
        member(5, Phase::Publish, 200, 90, 1),
    };
    const SchedulerDecision pair = servingSchedulerDecide(
        members, limits(), 1000, counts({1, 2}));
    EXPECT_EQ(pair.members, std::vector<uint64_t>({9, 5}));
    const SchedulerDecision single = servingSchedulerDecide(
        members, limits(), 1000, counts({1}));
    EXPECT_EQ(single.members, std::vector<uint64_t>({9}));
}

TEST(ServingSchedulerTest, PrefixStopsAtIncompatibleKeyAndNeverSkipsHead)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Decode, 1000, kSchedulerMaxTick, 0, 8, 10, 2),
         member(2, Phase::Decode, 1000, kSchedulerMaxTick, 0, 8, 10, 2),
         member(3, Phase::Decode, 1000, kSchedulerMaxTick, 0, 8, 12, 2),
         member(4, Phase::Decode, 1000, kSchedulerMaxTick, 0, 8, 10, 2)},
        limits(), 1'000'000'000, counts({1, 2, 4}));
    EXPECT_EQ(decision.action, SchedulerAction::Run);
    EXPECT_EQ(decision.members, std::vector<uint64_t>({1, 2}));
}

TEST(ServingSchedulerTest, PicksLargestFeasibleCountNotPrefixSize)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Decode, 1000, kSchedulerMaxTick, 0, 8, 5, 2),
         member(2, Phase::Decode, 1000, kSchedulerMaxTick, 0, 8, 5, 2),
         member(3, Phase::Decode, 1000, kSchedulerMaxTick, 0, 8, 5, 2)},
        limits(), 1'000'000'000, counts({1, 2}));
    EXPECT_EQ(decision.members, std::vector<uint64_t>({1, 2}));
}

TEST(ServingSchedulerTest, CapacityLimitsShapeTheFeasibleSet)
{
    SchedulerLimits tight = limits();
    tight.maxPrefillTokensPerBatch = 16;
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Prefill), member(2, Phase::Prefill),
         member(3, Phase::Prefill), member(4, Phase::Prefill)},
        tight, 1'000'000'000, counts({1, 2, 3, 4}));
    EXPECT_EQ(decision.members, std::vector<uint64_t>({1, 2}));
}

TEST(ServingSchedulerTest, WaitsBeforeDeadlineWhenLargerProfileExists)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Decode, 10'000, kSchedulerMaxTick, 0, 8, 5, 2)},
        limits(), 11'000, counts({1, 2}));
    EXPECT_EQ(decision.action, SchedulerAction::Wait);
    EXPECT_EQ(decision.waitUntil, 12'000ull);
}

TEST(ServingSchedulerTest, RunsSmallerBatchAtDeadlineDespiteLargerProfile)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Decode, 10'000, kSchedulerMaxTick, 0, 8, 5, 2),
         member(2, Phase::Decode, 10'500, kSchedulerMaxTick, 0, 8, 5, 2)},
        limits(), 12'000, counts({1, 2}));
    EXPECT_EQ(decision.action, SchedulerAction::Run);
    EXPECT_EQ(decision.members, std::vector<uint64_t>({1, 2}));
}

TEST(ServingSchedulerTest, HeadFailsAtDeadlineWithNoFeasibleCount)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Decode, 10'000, kSchedulerMaxTick, 0, 8, 5, 2)},
        limits(), 12'001, counts({9}));
    EXPECT_EQ(decision.action, SchedulerAction::FailHead);
    EXPECT_EQ(decision.members, std::vector<uint64_t>({1}));
}

TEST(ServingSchedulerTest, PublishNeverWaits)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Publish, 10'000)}, limits(), 10'001,
        counts({1, 2}));
    EXPECT_EQ(decision.action, SchedulerAction::Run);
    EXPECT_EQ(decision.members, std::vector<uint64_t>({1}));
}

TEST(ServingSchedulerTest, ClockPeriodScalesWaitDeadline)
{
    auto bounds = limits();
    bounds.batchWaitCycles = 20;
    const std::vector<SchedulerMember> ready = {member(1, Phase::Decode, 100)};
    for (const uint64_t now : {600ULL, 10099ULL}) {
        const auto decision = servingSchedulerDecide(
            ready, bounds, now, counts({1, 2}), 500);
        EXPECT_EQ(decision.action, SchedulerAction::Wait);
        EXPECT_EQ(decision.waitUntil, 10100u);
    }
    EXPECT_EQ(servingSchedulerDecide(ready, bounds, 10100, counts({1, 2}), 500)
                  .action, SchedulerAction::Run);
}

TEST(ServingSchedulerTest, ClockPeriodScalesPrefillStarvation)
{
    auto bounds = limits();
    bounds.prefillStarvationCycles = 20;
    const std::vector<SchedulerMember> ready = {
        member(1, Phase::Prefill, 100), member(2, Phase::Decode, 100)};
    EXPECT_EQ(servingSchedulerDecide(ready, bounds, 10099, counts({1}), 500)
                  .phase, Phase::Decode);
    EXPECT_EQ(servingSchedulerDecide(ready, bounds, 10100, counts({1}), 500)
                  .phase, Phase::Prefill);
}

TEST(ServingSchedulerTest, ClockScaledDeadlineDoesNotWrap)
{
    auto bounds = limits();
    bounds.batchWaitCycles = kSchedulerMaxTick;
    const auto decision = servingSchedulerDecide(
        {member(1, Phase::Decode, 100)}, bounds, 600, counts({1, 2}), 500);
    EXPECT_EQ(decision.action, SchedulerAction::Wait);
    EXPECT_EQ(decision.waitUntil, kSchedulerMaxTick);
}

TEST(ServingSchedulerTest, BatchEffectiveQoSIsMaxOverFrozenMembers)
{
    const SchedulerDecision decision = servingSchedulerDecide(
        {member(1, Phase::Publish, 100, kSchedulerMaxTick, 1),
         member(2, Phase::Publish, 200, kSchedulerMaxTick, 3),
         member(3, Phase::Publish, 150, kSchedulerMaxTick, 2)},
        limits(), 1000, counts({1, 3}));
    EXPECT_EQ(decision.memberCount, 3u);
    EXPECT_EQ(decision.members,
              std::vector<uint64_t>({2, 3, 1}));
    EXPECT_EQ(decision.batchEffectiveQos, 3);
}

} // namespace
} // namespace ai_mesh
} // namespace gem5

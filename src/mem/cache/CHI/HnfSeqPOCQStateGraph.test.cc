#include <gtest/gtest.h>

#include "mem/cache/CHI/HnfSeqPOCQStateGraph.hh"

namespace gem5::Chi
{

TEST(HnfSeqPocqStateGraphTest, NoHazardSnoopsThenCompletes)
{
    SEQ_POCQ_StateGraph graph;
    SeqPocqState state = SeqPocqState::Idle;

    auto step = graph.tryStep(state, {SeqPocqEventKind::Admit});
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(state, SeqPocqState::HazardCheck);
    ASSERT_EQ(step.actions.size(), 1);
    EXPECT_EQ(step.actions[0], SeqPocqActionKind::CheckHazard);

    step = graph.tryStep(state, {SeqPocqEventKind::HazardClear});
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(state, SeqPocqState::WaitSnoop);
    ASSERT_EQ(step.actions.size(), 1);
    EXPECT_EQ(step.actions[0], SeqPocqActionKind::QueueCleanInvalid);

    step = graph.tryStep(state, {SeqPocqEventKind::SnoopDone});
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(state, SeqPocqState::CompleteIssue);
    ASSERT_EQ(step.actions.size(), 1);
    EXPECT_EQ(step.actions[0], SeqPocqActionKind::IssueCompleteSfEvict);

    step = graph.tryStep(state, {SeqPocqEventKind::CompleteAccepted});
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(state, SeqPocqState::CompleteWait);
    EXPECT_TRUE(step.actions.empty());

    step = graph.tryStep(state, {SeqPocqEventKind::CompleteDone});
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(state, SeqPocqState::Idle);
    ASSERT_EQ(step.actions.size(), 1);
    EXPECT_EQ(step.actions[0], SeqPocqActionKind::Retire);
}

TEST(HnfSeqPocqStateGraphTest, AddressHazardSleepsUntilClear)
{
    SEQ_POCQ_StateGraph graph;
    SeqPocqState state = SeqPocqState::Idle;

    ASSERT_TRUE(graph.tryStep(state, {SeqPocqEventKind::Admit}).stepped);
    auto step = graph.tryStep(state, {SeqPocqEventKind::HazardBlocked});
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(state, SeqPocqState::Sleep);
    EXPECT_TRUE(step.actions.empty());

    step = graph.tryStep(state, {SeqPocqEventKind::HazardClear});
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(state, SeqPocqState::WaitSnoop);
    ASSERT_EQ(step.actions.size(), 1);
    EXPECT_EQ(step.actions[0], SeqPocqActionKind::QueueCleanInvalid);
}

} // namespace gem5::Chi

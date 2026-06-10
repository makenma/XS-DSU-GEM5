#include <gtest/gtest.h>

#include "mem/cache/CHI/HnfPOCQStateGraph.hh"

namespace gem5::Chi
{

namespace
{

PocqEvent
event(PocqEventKind kind)
{
    PocqEvent ev{};
    ev.kind = kind;
    return ev;
}

PocqEvent
lookupDone(bool slcHit, bool sfHit, bool replay)
{
    PocqEvent ev{};
    ev.kind = PocqEventKind::SlcLookupDone;
    ev.slcHit = slcHit;
    ev.sfHit = sfHit;
    ev.replay = replay;
    return ev;
}

void
expectStep(const PocqStepResult& step, PocqState next,
           PocqActionKind action)
{
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(step.nextState, next);
    ASSERT_EQ(step.actions.size(), 1);
    EXPECT_EQ(step.actions.front(), action);
}

} // anonymous namespace

TEST(HnfPocqStateGraphTest, SlcHitPath)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::Idle;

    expectStep(graph.tryStep(state, event(PocqEventKind::Admit)),
               PocqState::SlcLookup, PocqActionKind::DoSlcLookup);
    EXPECT_EQ(state, PocqState::SlcLookup);

    expectStep(graph.tryStep(state, lookupDone(true, true, false)),
               PocqState::SlcUpdate, PocqActionKind::UpdateSlcSf);
    expectStep(graph.tryStep(state, event(PocqEventKind::SlcUpdateDone)),
               PocqState::TxLink, PocqActionKind::QueueCompData);
    expectStep(graph.tryStep(state, event(PocqEventKind::TxLinkDone)),
               PocqState::WaitCompAck, PocqActionKind::WaitCompAck);
    expectStep(graph.tryStep(state, event(PocqEventKind::CompAck)),
               PocqState::Idle, PocqActionKind::Retire);
    EXPECT_EQ(state, PocqState::Idle);
}

TEST(HnfPocqStateGraphTest, SlcMissIssuesMemoryRead)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::SlcLookup;

    expectStep(graph.tryStep(state, lookupDone(false, false, false)),
               PocqState::IssueMcRead, PocqActionKind::QueueTxReq);
    expectStep(graph.tryStep(state, event(PocqEventKind::McDataDone)),
               PocqState::SlcUpdate, PocqActionKind::UpdateSlcSf);
    expectStep(graph.tryStep(state, event(PocqEventKind::SlcUpdateDone)),
               PocqState::TxLink, PocqActionKind::QueueCompData);
}

TEST(HnfPocqStateGraphTest, ReplaySleeps)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::SlcLookup;

    expectStep(graph.tryStep(state, lookupDone(false, false, true)),
               PocqState::Sleep, PocqActionKind::SleepForReplay);
    EXPECT_EQ(state, PocqState::Sleep);
}

TEST(HnfPocqStateGraphTest, NoMatchingEdgeLeavesStateUnchanged)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::Idle;

    PocqStepResult step = graph.tryStep(
        state, event(PocqEventKind::CompAck));
    EXPECT_FALSE(step.stepped);
    EXPECT_EQ(step.nextState, PocqState::Idle);
    EXPECT_TRUE(step.actions.empty());
    EXPECT_EQ(state, PocqState::Idle);
}

} // namespace gem5::Chi

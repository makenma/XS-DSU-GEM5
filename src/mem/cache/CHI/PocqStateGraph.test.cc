#include <gtest/gtest.h>

#include "mem/cache/CHI/PocqStateGraph.hh"

namespace gem5::Chi
{

namespace
{

void
expectState(const PocqMachine &machine, PocqGraphId graph,
            PocqNodeRole role)
{
    ASSERT_NE(machine.state, nullptr);
    EXPECT_EQ(machine.state->key().graph, graph);
    EXPECT_EQ(machine.state->key().role, role);
}

void
expectAction(const PocqStepResult &step, std::size_t index,
             PocqActionKind kind, PocqActionTarget target)
{
    ASSERT_LT(index, step.action_count());
    EXPECT_EQ(step.get_action(index).kind, kind);
    EXPECT_EQ(step.get_action(index).target, target);
}

PocqEvent
lookupDone(bool slc_hit, bool sf_hit, bool order_nonzero = false)
{
    PocqEvent event;
    event.kind = PocqEventKind::SlcLookupDone;
    event.source = PocqEventSource::SlcSf;
    event.slcHit = slc_hit;
    event.sfHit = sf_hit;
    event.orderNonZero = order_nonzero;
    return event;
}

PocqEvent
linkEvent(PocqEventKind kind)
{
    PocqEvent event;
    event.kind = kind;
    event.source = PocqEventSource::LinkLayer;
    return event;
}

} // anonymous namespace

TEST(PocqStateGraphTest, ReadUniqueSlcHit)
{
    PocqGraphPanel panel;
    PocqMachine machine;
    ASSERT_TRUE(panel.initialize(machine, PocqGraphId::ReadUnique));

    auto step = machine.state->step(machine, PocqEvent::enter());
    ASSERT_TRUE(step.has_value());
    ASSERT_EQ(step->action_count(), 1);
    expectAction(*step, 0, PocqActionKind::StartSlcLookup,
                 PocqActionTarget::SlcSf);
    expectState(machine, PocqGraphId::ReadUnique, PocqNodeRole::SlcLookup);

    step = machine.state->step(machine, lookupDone(true, false));
    ASSERT_TRUE(step.has_value());
    ASSERT_TRUE(step->need_action());
    ASSERT_EQ(step->action_count(), 1);
    expectAction(*step, 0, PocqActionKind::SendCompData,
                 PocqActionTarget::LinkLayer);
    expectState(machine, PocqGraphId::ReadUnique,
                PocqNodeRole::WaitCompAck);

    step = machine.state->step(machine, linkEvent(PocqEventKind::CompAck));
    ASSERT_TRUE(step.has_value());
    EXPECT_TRUE(step->is_complete());
    EXPECT_TRUE(machine.completed);
    expectState(machine, PocqGraphId::ReadUnique, PocqNodeRole::Exit);
}

TEST(PocqStateGraphTest, ReadUniqueMissCallsAndReturnsFromMcRead)
{
    PocqGraphPanel panel;
    PocqMachine machine;
    ASSERT_TRUE(panel.initialize(machine, PocqGraphId::ReadUnique));

    ASSERT_TRUE(machine.state->step(machine, PocqEvent::enter()).has_value());

    // SF/SLC miss selects the composite node. The same step enters MCRead,
    // consumes its internal Enter event, emits SendReadNoSnp, and stops at
    // the child graph's first externally waiting state.
    auto step = machine.state->step(machine, lookupDone(false, false));
    ASSERT_TRUE(step.has_value());
    ASSERT_EQ(step->action_count(), 1);
    expectAction(*step, 0, PocqActionKind::SendReadNoSnp,
                 PocqActionTarget::LinkLayer);
    expectState(machine, PocqGraphId::McRead, PocqNodeRole::WaitCompData);
    EXPECT_EQ(machine.subgraph_depth(), 1);

    // CompData exits MCRead, pops the parent composite node, automatically
    // delivers SubGraphDone, and follows the parent edge to WaitCompAck.
    step = machine.state->step(machine, linkEvent(PocqEventKind::CompData));
    ASSERT_TRUE(step.has_value());
    ASSERT_EQ(step->action_count(), 2);
    expectAction(*step, 0, PocqActionKind::SendCompAck,
                 PocqActionTarget::LinkLayer);
    expectAction(*step, 1, PocqActionKind::SendCompData,
                 PocqActionTarget::LinkLayer);
    expectState(machine, PocqGraphId::ReadUnique,
                PocqNodeRole::WaitCompAck);
    EXPECT_EQ(machine.subgraph_depth(), 0);
}

TEST(PocqStateGraphTest, McReadOrderWaitsForReadReceipt)
{
    PocqGraphPanel panel;
    PocqMachine machine;
    ASSERT_TRUE(panel.initialize(machine, PocqGraphId::ReadUnique));
    ASSERT_TRUE(machine.state->step(machine, PocqEvent::enter()).has_value());

    auto step = machine.state->step(
        machine, lookupDone(false, false, true));
    ASSERT_TRUE(step.has_value());
    expectState(machine, PocqGraphId::McRead,
                PocqNodeRole::WaitReadReceipt);

    step = machine.state->step(
        machine, linkEvent(PocqEventKind::ReadReceipt));
    ASSERT_TRUE(step.has_value());
    EXPECT_FALSE(step->need_action());
    expectState(machine, PocqGraphId::McRead, PocqNodeRole::WaitCompData);
}

TEST(PocqStateGraphTest, UnmatchedEventDoesNotMoveState)
{
    PocqGraphPanel panel;
    PocqMachine machine;
    ASSERT_TRUE(panel.initialize(machine, PocqGraphId::ReadUnique));
    ASSERT_TRUE(machine.state->step(machine, PocqEvent::enter()).has_value());

    const PocqNode *before = machine.state;
    const auto step = machine.state->step(
        machine, linkEvent(PocqEventKind::CompAck));
    EXPECT_FALSE(step.has_value());
    EXPECT_EQ(machine.state, before);
}

TEST(PocqStateGraphTest, CleanInvalidKeepsFlushL3OnGuardedEdges)
{
    PocqGraphPanel panel;
    PocqMachine machine;
    ASSERT_TRUE(panel.initialize(machine, PocqGraphId::CleanInvalid));

    auto step = machine.state->step(machine, PocqEvent::enter());
    ASSERT_TRUE(step.has_value());
    expectAction(*step, 0, PocqActionKind::StartSlcLookup,
                 PocqActionTarget::SlcSf);
    expectState(machine, PocqGraphId::CleanInvalid,
                PocqNodeRole::SlcLookup);

    // SLC hit / SF hit executes FlushL3 on the edge, then immediately enters
    // the snoop child graph. There is no FlushL3 node in either graph.
    step = machine.state->step(machine, lookupDone(true, true));
    ASSERT_TRUE(step.has_value());
    ASSERT_EQ(step->action_count(), 2);
    expectAction(*step, 0, PocqActionKind::FlushL3,
                 PocqActionTarget::SlcSf);
    expectAction(*step, 1, PocqActionKind::SendSnpMakeInvalid,
                 PocqActionTarget::LinkLayer);
    expectState(machine, PocqGraphId::SnpCleanInvalid,
                PocqNodeRole::WaitSnpResp);

    step = machine.state->step(machine, linkEvent(PocqEventKind::SnpResp));
    ASSERT_TRUE(step.has_value());
    ASSERT_EQ(step->action_count(), 2);
    expectAction(*step, 0, PocqActionKind::FlushSf,
                 PocqActionTarget::SlcSf);
    expectAction(*step, 1, PocqActionKind::SendComp,
                 PocqActionTarget::LinkLayer);
    EXPECT_TRUE(step->is_complete());
    expectState(machine, PocqGraphId::CleanInvalid, PocqNodeRole::Exit);

    // The other FlushL3 path reuses that same SlcLookup node and exits
    // directly; no duplicate node identity or instance field is required.
    ASSERT_TRUE(panel.initialize(machine, PocqGraphId::CleanInvalid));
    ASSERT_TRUE(machine.state->step(machine, PocqEvent::enter()).has_value());
    step = machine.state->step(machine, lookupDone(true, false));
    ASSERT_TRUE(step.has_value());
    ASSERT_EQ(step->action_count(), 2);
    expectAction(*step, 0, PocqActionKind::FlushL3,
                 PocqActionTarget::SlcSf);
    expectAction(*step, 1, PocqActionKind::SendComp,
                 PocqActionTarget::LinkLayer);
    EXPECT_TRUE(step->is_complete());
}

} // namespace gem5::Chi

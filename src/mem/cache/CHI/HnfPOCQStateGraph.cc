#include "mem/cache/CHI/HnfPOCQStateGraph.hh"

#include <cassert>

namespace gem5::Chi
{

namespace
{

bool
isAdmit(const PocqEvent& event)
{
    return event.kind == PocqEventKind::Admit;
}

bool
isLookupReplay(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcLookupDone && event.replay;
}

bool
isLookupHit(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcLookupDone && !event.replay &&
        event.slcHit;
}

bool
isLookupMissNeedingMemory(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcLookupDone && !event.replay &&
        !event.slcHit;
}

bool
isSlcUpdateDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcUpdateDone;
}

bool
isTxLinkDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::TxLinkDone;
}

bool
isMcDataDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::McDataDone;
}

bool
isCompAck(const PocqEvent& event)
{
    return event.kind == PocqEventKind::CompAck;
}

} // anonymous namespace

PocqEdge::PocqEdge(PocqNode& target, Condition condition,
                   std::initializer_list<PocqActionKind> actions)
    : DGEdge<PocqNode, PocqEdge>(target),
      condition(condition),
      actions(actions)
{}

bool
PocqEdge::matches(const PocqEvent& event) const
{
    return !condition || condition(event);
}

POCQ_StateGraph::POCQ_StateGraph()
    : idle(PocqState::Idle),
      slcLookup(PocqState::SlcLookup),
      slcUpdate(PocqState::SlcUpdate),
      txLink(PocqState::TxLink),
      waitCompAck(PocqState::WaitCompAck),
      issueMcRead(PocqState::IssueMcRead),
      sleep(PocqState::Sleep)
{
    addNode(idle);
    addNode(slcLookup);
    addNode(slcUpdate);
    addNode(txLink);
    addNode(waitCompAck);
    addNode(issueMcRead);
    addNode(sleep);

    addTransition(idle, slcLookup, isAdmit, {PocqActionKind::DoSlcLookup});
    addTransition(slcLookup, sleep, isLookupReplay,
                  {PocqActionKind::SleepForReplay});
    addTransition(slcLookup, slcUpdate, isLookupHit,
                  {PocqActionKind::UpdateSlcSf});
    addTransition(slcLookup, issueMcRead, isLookupMissNeedingMemory,
                  {PocqActionKind::QueueTxReq});
    addTransition(issueMcRead, slcUpdate, isMcDataDone,
                  {PocqActionKind::UpdateSlcSf});
    addTransition(slcUpdate, txLink, isSlcUpdateDone,
                  {PocqActionKind::QueueCompData});
    addTransition(txLink, waitCompAck, isTxLinkDone,
                  {PocqActionKind::WaitCompAck});
    addTransition(waitCompAck, idle, isCompAck,
                  {PocqActionKind::Retire});
}

PocqStepResult
POCQ_StateGraph::tryStep(PocqState& state, const PocqEvent& event) const
{
    PocqStepResult result{};
    result.oldState = state;
    result.nextState = state;

    const PocqNode& node = nodeFor(state);
    for (const auto* edge : node.getEdges()) {
        if (!edge->matches(event)) {
            continue;
        }
        result.stepped = true;
        result.nextState = edge->getTargetNode().getState();
        result.actions = edge->getActions();
        state = result.nextState;
        return result;
    }

    return result;
}

const char*
POCQ_StateGraph::stateName(PocqState state)
{
    switch (state) {
      case PocqState::Idle:
        return "Idle";
      case PocqState::SlcLookup:
        return "SlcLookup";
      case PocqState::SlcUpdate:
        return "SlcUpdate";
      case PocqState::TxLink:
        return "TxLink";
      case PocqState::WaitCompAck:
        return "WaitCompAck";
      case PocqState::IssueMcRead:
        return "IssueMcRead";
      case PocqState::Sleep:
        return "Sleep";
    }
    return "Unknown";
}

const char*
POCQ_StateGraph::eventName(PocqEventKind event)
{
    switch (event) {
      case PocqEventKind::Admit:
        return "Admit";
      case PocqEventKind::SlcLookupDone:
        return "SlcLookupDone";
      case PocqEventKind::SlcUpdateDone:
        return "SlcUpdateDone";
      case PocqEventKind::TxLinkDone:
        return "TxLinkDone";
      case PocqEventKind::McDataDone:
        return "McDataDone";
      case PocqEventKind::CompAck:
        return "CompAck";
    }
    return "Unknown";
}

const char*
POCQ_StateGraph::actionName(PocqActionKind action)
{
    switch (action) {
      case PocqActionKind::DoSlcLookup:
        return "DoSlcLookup";
      case PocqActionKind::UpdateSlcSf:
        return "UpdateSlcSf";
      case PocqActionKind::QueueTxReq:
        return "QueueTxReq";
      case PocqActionKind::QueueCompData:
        return "QueueCompData";
      case PocqActionKind::WaitCompAck:
        return "WaitCompAck";
      case PocqActionKind::Retire:
        return "Retire";
      case PocqActionKind::SleepForReplay:
        return "SleepForReplay";
    }
    return "Unknown";
}

PocqNode&
POCQ_StateGraph::nodeFor(PocqState state)
{
    return const_cast<PocqNode&>(
        static_cast<const POCQ_StateGraph&>(*this).nodeFor(state));
}

const PocqNode&
POCQ_StateGraph::nodeFor(PocqState state) const
{
    switch (state) {
      case PocqState::Idle:
        return idle;
      case PocqState::SlcLookup:
        return slcLookup;
      case PocqState::SlcUpdate:
        return slcUpdate;
      case PocqState::TxLink:
        return txLink;
      case PocqState::WaitCompAck:
        return waitCompAck;
      case PocqState::IssueMcRead:
        return issueMcRead;
      case PocqState::Sleep:
        return sleep;
    }
    assert(false && "Unknown POCQ state");
    return idle;
}

void
POCQ_StateGraph::addTransition(PocqNode& src, PocqNode& dst,
                               PocqEdge::Condition condition,
                               std::initializer_list<PocqActionKind> actions)
{
    edgeStorage.push_back(
        std::make_unique<PocqEdge>(dst, condition, actions));
    connect(src, dst, *edgeStorage.back());
}

} // namespace gem5::Chi

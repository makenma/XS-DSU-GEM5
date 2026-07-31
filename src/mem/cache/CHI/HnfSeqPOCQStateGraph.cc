#include "mem/cache/CHI/HnfSeqPOCQStateGraph.hh"

#include <cassert>

namespace gem5::Chi
{

namespace
{

bool isAdmit(const SeqPocqEvent& event)
{
    return event.kind == SeqPocqEventKind::Admit;
}

bool isHazardBlocked(const SeqPocqEvent& event)
{
    return event.kind == SeqPocqEventKind::HazardBlocked;
}

bool isHazardClear(const SeqPocqEvent& event)
{
    return event.kind == SeqPocqEventKind::HazardClear;
}

bool isSnoopDone(const SeqPocqEvent& event)
{
    return event.kind == SeqPocqEventKind::SnoopDone;
}

bool isCompleteAccepted(const SeqPocqEvent& event)
{
    return event.kind == SeqPocqEventKind::CompleteAccepted;
}

bool isCompleteDone(const SeqPocqEvent& event)
{
    return event.kind == SeqPocqEventKind::CompleteDone;
}

bool isCompleteReplay(const SeqPocqEvent& event)
{
    return event.kind == SeqPocqEventKind::CompleteReplay;
}

} // anonymous namespace

SeqPocqEdge::SeqPocqEdge(
    SeqPocqNode& target, Condition condition,
    std::initializer_list<SeqPocqActionKind> actions)
    : DGEdge<SeqPocqNode, SeqPocqEdge>(target),
      condition(condition), actions(actions)
{}

bool
SeqPocqEdge::matches(const SeqPocqEvent& event) const
{
    return !condition || condition(event);
}

SEQ_POCQ_StateGraph::SEQ_POCQ_StateGraph()
    : idle(SeqPocqState::Idle),
      hazardCheck(SeqPocqState::HazardCheck),
      sleep(SeqPocqState::Sleep),
      waitSnoop(SeqPocqState::WaitSnoop),
      completeIssue(SeqPocqState::CompleteIssue),
      completeWait(SeqPocqState::CompleteWait)
{
    addNode(idle);
    addNode(hazardCheck);
    addNode(sleep);
    addNode(waitSnoop);
    addNode(completeIssue);
    addNode(completeWait);

    addTransition(idle, hazardCheck, isAdmit,
                  {SeqPocqActionKind::CheckHazard});
    addTransition(hazardCheck, sleep, isHazardBlocked, {});
    addTransition(hazardCheck, waitSnoop, isHazardClear,
                  {SeqPocqActionKind::QueueCleanInvalid});
    addTransition(sleep, waitSnoop, isHazardClear,
                  {SeqPocqActionKind::QueueCleanInvalid});
    addTransition(waitSnoop, completeIssue, isSnoopDone,
                  {SeqPocqActionKind::IssueCompleteSfEvict});
    addTransition(completeIssue, completeWait, isCompleteAccepted, {});
    addTransition(completeWait, completeIssue, isCompleteReplay, {});
    addTransition(completeWait, idle, isCompleteDone,
                  {SeqPocqActionKind::Retire});
}

SeqPocqStepResult
SEQ_POCQ_StateGraph::tryStep(SeqPocqState& state,
                             const SeqPocqEvent& event) const
{
    SeqPocqStepResult result{};
    result.oldState = state;
    result.nextState = state;

    for (const auto* edge : nodeFor(state).getEdges()) {
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

const SeqPocqNode&
SEQ_POCQ_StateGraph::nodeFor(SeqPocqState state) const
{
    switch (state) {
      case SeqPocqState::Idle: return idle;
      case SeqPocqState::HazardCheck: return hazardCheck;
      case SeqPocqState::Sleep: return sleep;
      case SeqPocqState::WaitSnoop: return waitSnoop;
      case SeqPocqState::CompleteIssue: return completeIssue;
      case SeqPocqState::CompleteWait: return completeWait;
    }
    assert(false && "Unknown SEQ POCQ state");
    return idle;
}

void
SEQ_POCQ_StateGraph::addTransition(
    SeqPocqNode& src, SeqPocqNode& dst, SeqPocqEdge::Condition condition,
    std::initializer_list<SeqPocqActionKind> actions)
{
    edgeStorage.push_back(
        std::make_unique<SeqPocqEdge>(dst, condition, actions));
    connect(src, dst, *edgeStorage.back());
}

const char*
SEQ_POCQ_StateGraph::stateName(SeqPocqState state)
{
    switch (state) {
      case SeqPocqState::Idle: return "Idle";
      case SeqPocqState::HazardCheck: return "HazardCheck";
      case SeqPocqState::Sleep: return "Sleep";
      case SeqPocqState::WaitSnoop: return "WaitSnoop";
      case SeqPocqState::CompleteIssue: return "CompleteIssue";
      case SeqPocqState::CompleteWait: return "CompleteWait";
    }
    return "Unknown";
}

const char*
SEQ_POCQ_StateGraph::eventName(SeqPocqEventKind event)
{
    switch (event) {
      case SeqPocqEventKind::Admit: return "Admit";
      case SeqPocqEventKind::HazardBlocked: return "HazardBlocked";
      case SeqPocqEventKind::HazardClear: return "HazardClear";
      case SeqPocqEventKind::SnoopDone: return "SnoopDone";
      case SeqPocqEventKind::CompleteAccepted: return "CompleteAccepted";
      case SeqPocqEventKind::CompleteReplay: return "CompleteReplay";
      case SeqPocqEventKind::CompleteDone: return "CompleteDone";
    }
    return "Unknown";
}

const char*
SEQ_POCQ_StateGraph::actionName(SeqPocqActionKind action)
{
    switch (action) {
      case SeqPocqActionKind::CheckHazard: return "CheckHazard";
      case SeqPocqActionKind::QueueCleanInvalid:
        return "QueueCleanInvalid";
      case SeqPocqActionKind::IssueCompleteSfEvict:
        return "IssueCompleteSfEvict";
      case SeqPocqActionKind::Retire: return "Retire";
    }
    return "Unknown";
}

} // namespace gem5::Chi

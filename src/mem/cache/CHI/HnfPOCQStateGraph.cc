#include "mem/cache/CHI/HnfPOCQStateGraph.hh"

#include <cassert>
#include <ostream>

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
isReadTxn(PocqTxnKind txn)
{
    return txn == PocqTxnKind::Unknown ||
        txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique ||
        txn == PocqTxnKind::ReadOnce;
}

bool
isMaintenanceTxn(PocqTxnKind txn)
{
    return txn == PocqTxnKind::CleanInvalid ||
        txn == PocqTxnKind::MakeInvalid ||
        txn == PocqTxnKind::MakeUnique;
}

bool
isWriteTxn(PocqTxnKind txn)
{
    return txn == PocqTxnKind::WriteBackFull ||
        txn == PocqTxnKind::WriteCleanFull ||
        txn == PocqTxnKind::WriteUnique ||
        txn == PocqTxnKind::WriteEvictFull;
}

bool
isAdmitReadNoSnp(const PocqEvent& event)
{
    return isAdmit(event) && event.txn == PocqTxnKind::ReadNoSnp;
}

bool
isAdmitReadLookup(const PocqEvent& event)
{
    return isAdmit(event) && isReadTxn(event.txn);
}

bool
isAdmitMaintenanceLookup(const PocqEvent& event)
{
    return isAdmit(event) && isMaintenanceTxn(event.txn);
}

bool
isAdmitEvict(const PocqEvent& event)
{
    return isAdmit(event) && event.txn == PocqTxnKind::Evict;
}

bool
isAdmitWrite(const PocqEvent& event)
{
    return isAdmit(event) && isWriteTxn(event.txn);
}

bool
isLookupReplay(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcLookupDone && event.replay;
}

bool
isLookupNeedsSnoop(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcLookupDone && !event.replay &&
        event.needsSnoop;
}

bool
isLookupHit(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcLookupDone && !event.replay &&
        !event.needsSnoop && event.slcHit && isReadTxn(event.txn);
}

bool
isLookupMissNeedingMemory(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcLookupDone && !event.replay &&
        !event.needsSnoop && !event.slcHit && isReadTxn(event.txn);
}

bool
isLookupMaintenanceDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcLookupDone && !event.replay &&
        !event.needsSnoop && isMaintenanceTxn(event.txn);
}

bool
isSnoopDoneReadWithData(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SnoopDone &&
        isReadTxn(event.txn) && event.dataAvailable;
}

bool
isSnoopDoneReadNeedsMemory(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SnoopDone &&
        isReadTxn(event.txn) && !event.dataAvailable;
}

bool
isSnoopDoneMaintenance(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SnoopDone &&
        isMaintenanceTxn(event.txn);
}

bool
isSlcUpdateDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcUpdateDone && !event.replay;
}

bool
isSlcUpdateAccepted(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcUpdateAccepted;
}

bool
isSlcUpdateReplay(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcUpdateDone && event.replay;
}

bool
isTxLinkDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::TxLinkDone;
}

bool
isTxLinkDoneNeedsCompAck(const PocqEvent& event)
{
    return isTxLinkDone(event) &&
        (event.needsCompAck || event.txn == PocqTxnKind::Unknown);
}

bool
isTxLinkDoneNoCompAck(const PocqEvent& event)
{
    return isTxLinkDone(event) && !event.needsCompAck &&
        event.txn != PocqTxnKind::Unknown;
}

bool
isMcDataDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::McDataDone;
}

bool
isMcDataDoneReadNoSnp(const PocqEvent& event)
{
    return isMcDataDone(event) &&
        event.txn == PocqTxnKind::ReadNoSnp;
}

bool
isCompAck(const PocqEvent& event)
{
    return event.kind == PocqEventKind::CompAck;
}

bool
isWriteDataDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::WriteDataDone;
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
      waitSnoop(PocqState::WaitSnoop),
      slcUpdateIssue(PocqState::SlcUpdateIssue),
      slcUpdateWait(PocqState::SlcUpdateWait),
      txLink(PocqState::TxLink),
      waitCompAck(PocqState::WaitCompAck),
      issueMcRead(PocqState::IssueMcRead),
      txRsp(PocqState::TxRsp),
      waitWriteData(PocqState::WaitWriteData),
      sleep(PocqState::Sleep)
{
    addNode(idle);
    addNode(slcLookup);
    addNode(waitSnoop);
    addNode(slcUpdateIssue);
    addNode(slcUpdateWait);
    addNode(txLink);
    addNode(waitCompAck);
    addNode(issueMcRead);
    addNode(txRsp);
    addNode(waitWriteData);
    addNode(sleep);

    addTransition(idle, issueMcRead, isAdmitReadNoSnp,
                  {PocqActionKind::QueueTxReq});
    addTransition(idle, slcLookup, isAdmitReadLookup,
                  {PocqActionKind::DoSlcLookup});
    addTransition(idle, slcLookup, isAdmitMaintenanceLookup,
                  {PocqActionKind::DoSlcLookup});
    addTransition(idle, idle, isAdmitEvict,
                  {PocqActionKind::RemoveSharer,
                   PocqActionKind::QueueComp});
    addTransition(idle, waitWriteData, isAdmitWrite,
                  {PocqActionKind::QueueCompDBIDResp,
                   PocqActionKind::WaitWriteData});
    addTransition(slcLookup, sleep, isLookupReplay,
                  {PocqActionKind::SleepForReplay});
    addTransition(slcLookup, waitSnoop, isLookupNeedsSnoop,
                  {PocqActionKind::QueueSnoops});
    addTransition(slcLookup, slcUpdateIssue, isLookupHit,
                  {PocqActionKind::UpdateSlcSf});
    addTransition(slcLookup, issueMcRead, isLookupMissNeedingMemory,
                  {PocqActionKind::QueueTxReq});
    addTransition(slcLookup, idle, isLookupMaintenanceDone,
                  {PocqActionKind::CommitMaintenance,
                   PocqActionKind::QueueComp});
    addTransition(waitSnoop, slcUpdateIssue, isSnoopDoneReadWithData,
                  {PocqActionKind::UpdateSlcSf});
    addTransition(waitSnoop, issueMcRead, isSnoopDoneReadNeedsMemory,
                  {PocqActionKind::QueueTxReq});
    addTransition(waitSnoop, idle, isSnoopDoneMaintenance,
                  {PocqActionKind::CommitMaintenance,
                   PocqActionKind::QueueComp});
    addTransition(issueMcRead, txLink, isMcDataDoneReadNoSnp,
                  {PocqActionKind::QueueCompData});
    addTransition(issueMcRead, slcUpdateIssue, isMcDataDone,
                  {PocqActionKind::UpdateSlcSf});
    addTransition(slcUpdateIssue, slcUpdateWait,
                  isSlcUpdateAccepted, {});
    addTransition(slcUpdateWait, sleep, isSlcUpdateReplay,
                  {PocqActionKind::SleepForReplay});
    addTransition(slcUpdateWait, txLink, isSlcUpdateDone,
                  {PocqActionKind::QueueCompData});
    addTransition(txLink, waitCompAck, isTxLinkDoneNeedsCompAck,
                  {PocqActionKind::WaitCompAck});
    addTransition(txLink, idle, isTxLinkDoneNoCompAck,
                  {PocqActionKind::Retire});
    addTransition(waitWriteData, idle, isWriteDataDone,
                  {PocqActionKind::StoreWriteData, PocqActionKind::Retire});
    addTransition(waitCompAck, idle, isCompAck,
                  {PocqActionKind::Retire});
}

PocqStepResult
POCQ_StateGraph::tryStep(PocqState& state, const PocqEvent& event) const
{
    PocqStepResult result{};
    result.oldState = state;
    result.nextState = state;

    const PocqNode& currentNode = nodeFor(state);

    // [1] If this node has a subGraph, check if we're already inside it.
    if (currentNode.hasSubGraph()) {
        POCQ_StateGraph* subGraph = currentNode.getSubGraph();
        const void* entryKey = static_cast<const void*>(&state);
        auto it = subGraph->entrySubStates_.find(entryKey);

        if (it != subGraph->entrySubStates_.end()) {
            // Already inside the sub-graph — delegate event to it.
            PocqStepResult subResult =
                subGraph->tryStep(it->second, event);

            if (!subResult.stepped) {
                result.stepped = false;
                result.oldState = state;
                return result;
            }

            // Carry sub-graph results: actions always propagate.
            result.stepped = true;
            result.actions = subResult.actions;

            if (it->second != subGraph->getExitNode()->getState()) {
                // Sub-graph stepped but still in progress.
                result.nextState = state;
                return result;
            }

            // Sub-graph reached Exit — clear this entry's sub-state.
            subGraph->entrySubStates_.erase(it);
            // Fall through: try parent edges with the same event.
            // If a parent edge matches, result gets overwritten below.
            // If not, the sub-graph's result (actions) is already in `result`.
        }
        // else: sub-graph was exited (or never entered for this entry).
        // Fall through to try parent edges. Entry happens in step [2]
        // when a parent edge transitions into a composite node.
    }

    // [2] Try outgoing edges from the current node (parent-level).
    for (const auto* edge : currentNode.getEdges()) {
        if (!edge->matches(event)) {
            continue;
        }
        result.stepped = true;
        result.nextState = edge->getTargetNode().getState();
        result.actions = edge->getActions();
        state = result.nextState;

        // If the target node has a subGraph, prepare for entry.
        const PocqNode& nextNode = nodeFor(state);
        if (nextNode.hasSubGraph()) {
            const void* nextKey = static_cast<const void*>(&state);
            nextNode.getSubGraph()->entrySubStates_[nextKey] =
                nextNode.getSubGraph()->getStartNode()->getState();
        }

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
      case PocqState::WaitSnoop:
        return "WaitSnoop";
      case PocqState::SlcUpdateIssue:
        return "SlcUpdateIssue";
      case PocqState::SlcUpdateWait:
        return "SlcUpdateWait";
      case PocqState::TxLink:
        return "TxLink";
      case PocqState::WaitCompAck:
        return "WaitCompAck";
      case PocqState::IssueMcRead:
        return "IssueMcRead";
      case PocqState::TxRsp:
        return "TxRsp";
      case PocqState::WaitWriteData:
        return "WaitWriteData";
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
      case PocqEventKind::SlcUpdateAccepted:
        return "SlcUpdateAccepted";
      case PocqEventKind::SlcUpdateDone:
        return "SlcUpdateDone";
      case PocqEventKind::SnoopDone:
        return "SnoopDone";
      case PocqEventKind::TxLinkDone:
        return "TxLinkDone";
      case PocqEventKind::TxRspDone:
        return "TxRspDone";
      case PocqEventKind::McDataDone:
        return "McDataDone";
      case PocqEventKind::CompAck:
        return "CompAck";
      case PocqEventKind::WriteDataDone:
        return "WriteDataDone";
    }
    return "Unknown";
}

const char*
POCQ_StateGraph::actionName(PocqActionKind action)
{
    switch (action) {
      case PocqActionKind::DoSlcLookup:
        return "DoSlcLookup";
      case PocqActionKind::QueueSnoops:
        return "QueueSnoops";
      case PocqActionKind::CommitRead:
        return "CommitRead";
      case PocqActionKind::CommitMaintenance:
        return "CommitMaintenance";
      case PocqActionKind::RemoveSharer:
        return "RemoveSharer";
      case PocqActionKind::UpdateSlcSf:
        return "UpdateSlcSf";
      case PocqActionKind::QueueTxReq:
        return "QueueTxReq";
      case PocqActionKind::QueueCompData:
        return "QueueCompData";
      case PocqActionKind::QueueComp:
        return "QueueComp";
      case PocqActionKind::QueueCompDBIDResp:
        return "QueueCompDBIDResp";
      case PocqActionKind::WaitCompAck:
        return "WaitCompAck";
      case PocqActionKind::WaitWriteData:
        return "WaitWriteData";
      case PocqActionKind::StoreWriteData:
        return "StoreWriteData";
      case PocqActionKind::FlushSf:
        return "FlushSf";
      case PocqActionKind::FlushL3:
        return "FlushL3";
      case PocqActionKind::WriteL3FlushSf:
        return "WriteL3FlushSf";
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
      case PocqState::WaitSnoop:
        return waitSnoop;
      case PocqState::SlcUpdateIssue:
        return slcUpdateIssue;
      case PocqState::SlcUpdateWait:
        return slcUpdateWait;
      case PocqState::TxLink:
        return txLink;
      case PocqState::WaitCompAck:
        return waitCompAck;
      case PocqState::IssueMcRead:
        return issueMcRead;
      case PocqState::TxRsp:
        return txRsp;
      case PocqState::WaitWriteData:
        return waitWriteData;
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

void
POCQ_StateGraph::markPublicNode(PocqNode& node, SubGraphPublicState role)
{
    switch (role) {
      case SubGraphPublicState::Start:
        startNode_ = &node;
        break;
      case SubGraphPublicState::Exit:
        exitNode_ = &node;
        break;
    }
}

bool
POCQ_StateGraph::isActive() const
{
    return !entrySubStates_.empty();
}

void
POCQ_StateGraph::enter()
{
    // No-op: sub-state is managed per-entry via entrySubStates_ in tryStep.
}

void
POCQ_StateGraph::exit()
{
    entrySubStates_.clear();
}

void
PocqNode::printState(std::ostream& os) const
{
    os << POCQ_StateGraph::stateName(state);
    if (subGraph && !subGraph->entrySubStates_.empty()) {
        os << " > ";
        // Find the first active sub-state and print recursively.
        for (const auto& [key, subState] : subGraph->entrySubStates_) {
            const PocqNode& subNode = subGraph->nodeFor(subState);
            subNode.printState(os);
            break;  // Print only the first active entry's state.
        }
    }
}

} // namespace gem5::Chi

#include "mem/cache/CHI/PocqStateGraph.hh"

#include <cassert>

namespace gem5::Chi
{

namespace
{

constexpr PocqAction
linkAction(PocqActionKind kind)
{
    return {kind, PocqActionTarget::LinkLayer};
}

constexpr PocqAction
slcSfAction(PocqActionKind kind)
{
    return {kind, PocqActionTarget::SlcSf};
}

bool
always(const PocqMachine &, const PocqEvent &)
{
    return true;
}

bool
slcHit(const PocqMachine &, const PocqEvent &event)
{
    return event.slcHit;
}

bool
slcMissSfMiss(const PocqMachine &, const PocqEvent &event)
{
    return !event.slcHit && !event.sfHit;
}

bool
slcHitSfHit(const PocqMachine &, const PocqEvent &event)
{
    return event.slcHit && event.sfHit;
}

bool
slcMissSfHit(const PocqMachine &, const PocqEvent &event)
{
    return !event.slcHit && event.sfHit;
}

bool
slcHitSfMiss(const PocqMachine &, const PocqEvent &event)
{
    return event.slcHit && !event.sfHit;
}

bool
orderNonZero(const PocqMachine &, const PocqEvent &event)
{
    return event.orderNonZero;
}

bool
orderZero(const PocqMachine &, const PocqEvent &event)
{
    return !event.orderNonZero;
}

} // anonymous namespace

PocqEvent
PocqEvent::enter()
{
    return {PocqEventKind::Enter, PocqEventSource::Internal};
}

PocqEvent
PocqEvent::subGraphDone()
{
    return {PocqEventKind::SubGraphDone, PocqEventSource::Internal};
}

void
PocqMachine::reset(const PocqNode *entry)
{
    state = entry;
    completed = false;
    callStack.fill(nullptr);
    callDepth = 0;
}

const PocqAction &
PocqStepResult::get_action(std::size_t index) const
{
    assert(index < actionCount);
    return actions[index];
}

void
PocqStepResult::addAction(const PocqAction &action)
{
    assert(actionCount < actions.size());
    actions[actionCount++] = action;
}

PocqEdge::PocqEdge(PocqNode &target, PocqEventKind event,
                   PocqGuard edge_guard,
                   std::initializer_list<PocqAction> edge_actions)
    : DGEdge<PocqNode, PocqEdge>(target),
      eventKind(event),
      guard(edge_guard)
{
    assert(edge_actions.size() <= actions.size());
    for (const PocqAction &action : edge_actions)
        actions[actionCount++] = action;
}

bool
PocqEdge::matches(const PocqMachine &machine, const PocqEvent &event) const
{
    return event.kind == eventKind && (!guard || guard(machine, event));
}

void
PocqEdge::appendActions(PocqStepResult &result) const
{
    for (std::size_t i = 0; i < actionCount; ++i)
        result.addAction(actions[i]);
}

PocqNode::PocqNode(PocqNodeKey key, PocqNodeKind kind)
    : nodeKey(key), nodeKind(kind)
{}

std::optional<PocqStepResult>
PocqNode::step(PocqMachine &machine, const PocqEvent &event) const
{
    assert(machine.state == this);
    if (machine.completed)
        return std::nullopt;

    PocqStepResult result;
    result.oldState = nodeKey;
    result.newState = nodeKey;

    const PocqEvent *current_event = &event;
    PocqEvent internal_event{};
    bool must_match_internal_event = false;

    // Enter and SubGraphDone are zero-time internal transitions. Cap the
    // number of chained transitions so a malformed graph cannot loop forever.
    constexpr std::size_t MaxMicroSteps = 32;
    for (std::size_t micro_step = 0;
         micro_step < MaxMicroSteps; ++micro_step) {
        const PocqNode *current = machine.state;
        assert(current);

        const PocqEdge *selected = nullptr;
        for (const PocqEdge *edge : current->getEdges()) {
            if (!edge->matches(machine, *current_event))
                continue;

            // Guards on edges sharing an event must be mutually exclusive.
            assert(!selected && "ambiguous POCQ transition");
            selected = edge;
        }

        if (!selected) {
            assert(!must_match_internal_event &&
                   "POCQ internal event has no matching transition");
            if (!result.didTransition)
                return std::nullopt;
            return result;
        }

        result.didTransition = true;
        selected->appendActions(result);

        machine.state = &selected->getTargetNode();
        result.newState = machine.state->key();
        must_match_internal_event = false;

        if (machine.state->kind() == PocqNodeKind::Composite) {
            assert(machine.state->childGraph());
            assert(machine.state->childGraph()->entry());
            assert(machine.callDepth < machine.callStack.size());

            // Store the parent composite node. When the child reaches Exit,
            // SubGraphDone is delivered back to exactly this call site.
            machine.callStack[machine.callDepth++] = machine.state;
            machine.state = machine.state->childGraph()->entry();
            result.newState = machine.state->key();

            // Preserve transaction attributes (for example Order) while
            // changing only the control event delivered to the child entry.
            internal_event = *current_event;
            internal_event.kind = PocqEventKind::Enter;
            internal_event.source = PocqEventSource::Internal;
            current_event = &internal_event;
            must_match_internal_event = true;
            continue;
        }

        if (machine.state->kind() == PocqNodeKind::Immediate) {
            internal_event = *current_event;
            internal_event.kind = PocqEventKind::Enter;
            internal_event.source = PocqEventSource::Internal;
            current_event = &internal_event;
            must_match_internal_event = true;
            continue;
        }

        if (machine.state->kind() == PocqNodeKind::Exit) {
            if (machine.callDepth == 0) {
                machine.completed = true;
                result.completed = true;
                return result;
            }

            machine.state = machine.callStack[--machine.callDepth];
            machine.callStack[machine.callDepth] = nullptr;
            result.newState = machine.state->key();

            internal_event = PocqEvent::subGraphDone();
            current_event = &internal_event;
            must_match_internal_event = true;
            continue;
        }

        // A normal state waits for the next externally supplied event.
        return result;
    }

    assert(false && "too many chained POCQ internal transitions");
    return result;
}

PocqGraphPanel::PocqGraphPanel()
{
    // Create graph containers before wiring cross-graph composite nodes.
    makeGraph(PocqGraphId::McRead);
    makeGraph(PocqGraphId::ReadUnique);
    makeGraph(PocqGraphId::SnpCleanInvalid);
    makeGraph(PocqGraphId::CleanInvalid);

    buildMcRead();
    buildSnpCleanInvalid();
    buildReadUnique();
    buildCleanInvalid();
}

bool
PocqGraphPanel::hasGraph(PocqGraphId id) const
{
    const auto index = static_cast<std::size_t>(id);
    return index < graphs.size() && graphs[index] != nullptr;
}

const PocqGraph &
PocqGraphPanel::graph(PocqGraphId id) const
{
    assert(hasGraph(id));
    return *graphs[static_cast<std::size_t>(id)];
}

const PocqNode *
PocqGraphPanel::node(PocqGraphId graph_id, PocqNodeRole role) const
{
    const auto graph_index = static_cast<std::size_t>(graph_id);
    const auto role_index = static_cast<std::size_t>(role);
    if (graph_index >= GraphCount || role_index >= RoleCount)
        return nullptr;
    return nodeIndex[graph_index][role_index];
}

bool
PocqGraphPanel::initialize(PocqMachine &machine, PocqGraphId graph_id) const
{
    if (!hasGraph(graph_id) || !graph(graph_id).entry())
        return false;
    machine.reset(graph(graph_id).entry());
    return true;
}

std::optional<PocqGraphId>
PocqGraphPanel::graphForReq(ReqMinor opcode)
{
    switch (opcode) {
      case ReqMinor::CleanInvalid:
        return PocqGraphId::CleanInvalid;
      case ReqMinor::ReadUnique:
        return PocqGraphId::ReadUnique;
      default:
        return std::nullopt;
    }
}

PocqGraph &
PocqGraphPanel::makeGraph(PocqGraphId id)
{
    const auto index = static_cast<std::size_t>(id);
    assert(index < graphs.size());
    assert(!graphs[index]);
    graphs[index] = std::make_unique<PocqGraph>(id);
    return *graphs[index];
}

PocqNode &
PocqGraphPanel::makeNode(PocqGraphId graph_id, PocqNodeRole role,
                         PocqNodeKind kind)
{
    const auto graph_index = static_cast<std::size_t>(graph_id);
    const auto role_index = static_cast<std::size_t>(role);
    assert(graph_index < GraphCount);
    assert(role_index < RoleCount);
    assert(hasGraph(graph_id));
    assert(!nodeIndex[graph_index][role_index]);

    auto node = std::make_unique<PocqNode>(
        PocqNodeKey{graph_id, role}, kind);
    PocqNode *node_ptr = node.get();
    nodeStorage.push_back(std::move(node));
    nodeIndex[graph_index][role_index] = node_ptr;
    graphs[graph_index]->addNode(*node_ptr);
    return *node_ptr;
}

PocqEdge &
PocqGraphPanel::addEdge(PocqNode &source, PocqNode &target,
                        PocqEventKind event, PocqGuard guard,
                        std::initializer_list<PocqAction> actions)
{
    auto edge = std::make_unique<PocqEdge>(target, event, guard, actions);
    PocqEdge *edge_ptr = edge.get();
    edgeStorage.push_back(std::move(edge));
    const bool inserted = source.addEdge(*edge_ptr);
    assert(inserted);
    return *edge_ptr;
}

void
PocqGraphPanel::buildMcRead()
{
    PocqGraph &mc_read = *graphs[static_cast<std::size_t>(PocqGraphId::McRead)];

    PocqNode &entry = makeNode(PocqGraphId::McRead,
                               PocqNodeRole::Entry,
                               PocqNodeKind::State);
    PocqNode &wait_receipt = makeNode(PocqGraphId::McRead,
                                      PocqNodeRole::WaitReadReceipt,
                                      PocqNodeKind::State);
    PocqNode &wait_data = makeNode(PocqGraphId::McRead,
                                   PocqNodeRole::WaitCompData,
                                   PocqNodeKind::State);
    PocqNode &exit = makeNode(PocqGraphId::McRead,
                              PocqNodeRole::Exit,
                              PocqNodeKind::Exit);

    mc_read.setEntry(&entry);
    mc_read.setExit(&exit);

    addEdge(entry, wait_receipt, PocqEventKind::Enter, orderNonZero,
            {linkAction(PocqActionKind::SendReadNoSnp)});
    addEdge(entry, wait_data, PocqEventKind::Enter, orderZero,
            {linkAction(PocqActionKind::SendReadNoSnp)});
    addEdge(wait_receipt, wait_data, PocqEventKind::ReadReceipt, always);
    addEdge(wait_data, exit, PocqEventKind::CompData, always,
            {linkAction(PocqActionKind::SendCompAck)});
}

void
PocqGraphPanel::buildReadUnique()
{
    PocqGraph &read_unique =
        *graphs[static_cast<std::size_t>(PocqGraphId::ReadUnique)];

    PocqNode &entry = makeNode(PocqGraphId::ReadUnique,
                               PocqNodeRole::Entry,
                               PocqNodeKind::State);
    PocqNode &slc_lookup = makeNode(PocqGraphId::ReadUnique,
                                    PocqNodeRole::SlcLookup,
                                    PocqNodeKind::State);
    PocqNode &call_mc_read = makeNode(PocqGraphId::ReadUnique,
                                      PocqNodeRole::CallMcRead,
                                      PocqNodeKind::Composite);
    PocqNode &wait_comp_ack = makeNode(PocqGraphId::ReadUnique,
                                       PocqNodeRole::WaitCompAck,
                                       PocqNodeKind::State);
    PocqNode &exit = makeNode(PocqGraphId::ReadUnique,
                              PocqNodeRole::Exit,
                              PocqNodeKind::Exit);

    read_unique.setEntry(&entry);
    read_unique.setExit(&exit);
    call_mc_read.setChildGraph(
        graphs[static_cast<std::size_t>(PocqGraphId::McRead)].get());

    addEdge(entry, slc_lookup, PocqEventKind::Enter, always,
            {slcSfAction(PocqActionKind::StartSlcLookup)});
    addEdge(slc_lookup, wait_comp_ack, PocqEventKind::SlcLookupDone,
            slcHit,
            {linkAction(PocqActionKind::SendCompData)});
    addEdge(slc_lookup, call_mc_read, PocqEventKind::SlcLookupDone,
            slcMissSfMiss);
    addEdge(call_mc_read, wait_comp_ack, PocqEventKind::SubGraphDone,
            always,
            {linkAction(PocqActionKind::SendCompData)});
    addEdge(wait_comp_ack, exit, PocqEventKind::CompAck, always);
}

void
PocqGraphPanel::buildSnpCleanInvalid()
{
    PocqGraph &snp_clean_invalid = *graphs[static_cast<std::size_t>(
        PocqGraphId::SnpCleanInvalid)];

    PocqNode &entry = makeNode(PocqGraphId::SnpCleanInvalid,
                               PocqNodeRole::Entry,
                               PocqNodeKind::State);
    PocqNode &wait_snp_resp = makeNode(PocqGraphId::SnpCleanInvalid,
                                       PocqNodeRole::WaitSnpResp,
                                       PocqNodeKind::State);
    PocqNode &exit = makeNode(PocqGraphId::SnpCleanInvalid,
                              PocqNodeRole::Exit,
                              PocqNodeKind::Exit);

    snp_clean_invalid.setEntry(&entry);
    snp_clean_invalid.setExit(&exit);

    addEdge(entry, wait_snp_resp, PocqEventKind::Enter, always,
            {linkAction(PocqActionKind::SendSnpMakeInvalid)});
    addEdge(wait_snp_resp, exit, PocqEventKind::SnpResp, always,
            {slcSfAction(PocqActionKind::FlushSf)});
}

void
PocqGraphPanel::buildCleanInvalid()
{
    PocqGraph &clean_invalid = *graphs[static_cast<std::size_t>(
        PocqGraphId::CleanInvalid)];

    PocqNode &entry = makeNode(PocqGraphId::CleanInvalid,
                               PocqNodeRole::Entry,
                               PocqNodeKind::State);
    PocqNode &slc_lookup = makeNode(PocqGraphId::CleanInvalid,
                                    PocqNodeRole::SlcLookup,
                                    PocqNodeKind::State);
    PocqNode &call_snp = makeNode(PocqGraphId::CleanInvalid,
                                  PocqNodeRole::CallSnpCleanInvalid,
                                  PocqNodeKind::Composite);
    PocqNode &exit = makeNode(PocqGraphId::CleanInvalid,
                              PocqNodeRole::Exit,
                              PocqNodeKind::Exit);

    clean_invalid.setEntry(&entry);
    clean_invalid.setExit(&exit);
    call_snp.setChildGraph(graphs[static_cast<std::size_t>(
        PocqGraphId::SnpCleanInvalid)].get());

    addEdge(entry, slc_lookup, PocqEventKind::Enter, always,
            {slcSfAction(PocqActionKind::StartSlcLookup)});

    // FlushL3 is an action on two guarded edges, not a node. Therefore this
    // graph has exactly one SlcLookup node and needs no instance number.
    addEdge(slc_lookup, call_snp, PocqEventKind::SlcLookupDone,
            slcHitSfHit,
            {slcSfAction(PocqActionKind::FlushL3)});
    addEdge(slc_lookup, call_snp, PocqEventKind::SlcLookupDone,
            slcMissSfHit);
    addEdge(slc_lookup, exit, PocqEventKind::SlcLookupDone,
            slcHitSfMiss,
            {slcSfAction(PocqActionKind::FlushL3),
             linkAction(PocqActionKind::SendComp)});
    addEdge(slc_lookup, exit, PocqEventKind::SlcLookupDone,
            slcMissSfMiss,
            {linkAction(PocqActionKind::SendComp)});
    addEdge(call_snp, exit, PocqEventKind::SubGraphDone, always,
            {linkAction(PocqActionKind::SendComp)});
}

const char *
pocqGraphName(PocqGraphId id)
{
    switch (id) {
      case PocqGraphId::CleanInvalid: return "CleanInvalid";
      case PocqGraphId::Evict: return "Evict";
      case PocqGraphId::McDmt: return "McDmt";
      case PocqGraphId::McRead: return "McRead";
      case PocqGraphId::MakeInvalid: return "MakeInvalid";
      case PocqGraphId::MakeUnique: return "MakeUnique";
      case PocqGraphId::ReadNoSnp: return "ReadNoSnp";
      case PocqGraphId::ReadOnce: return "ReadOnce";
      case PocqGraphId::ReadShared: return "ReadShared";
      case PocqGraphId::ReadUnique: return "ReadUnique";
      case PocqGraphId::SnpCleanInvalid: return "SnpCleanInvalid";
      case PocqGraphId::SnpMakeInvalid: return "SnpMakeInvalid";
      case PocqGraphId::SnpOnce: return "SnpOnce";
      case PocqGraphId::SnpOnceFwd: return "SnpOnceFwd";
      case PocqGraphId::SnpShared: return "SnpShared";
      case PocqGraphId::SnpUnique: return "SnpUnique";
      case PocqGraphId::WriteBackFull: return "WriteBackFull";
      case PocqGraphId::WriteCleanFull: return "WriteCleanFull";
      case PocqGraphId::NumGraphs:
      case PocqGraphId::Invalid:
        return "Invalid";
    }
    return "Invalid";
}

const char *
pocqNodeRoleName(PocqNodeRole role)
{
    switch (role) {
      case PocqNodeRole::Entry: return "Entry";
      case PocqNodeRole::SlcLookup: return "SlcLookup";
      case PocqNodeRole::SlcFill: return "SlcFill";
      case PocqNodeRole::Idle: return "Idle";
      case PocqNodeRole::TxDat: return "TxDat";
      case PocqNodeRole::TxSnp: return "TxSnp";
      case PocqNodeRole::TxRsp: return "TxRsp";
      case PocqNodeRole::McRetry: return "McRetry";
      case PocqNodeRole::CallMcRead: return "CallMcRead";
      case PocqNodeRole::CallMcDmt: return "CallMcDmt";
      case PocqNodeRole::CallSnpCleanInvalid: return "CallSnpCleanInvalid";
      case PocqNodeRole::CallSnpMakeInvalid: return "CallSnpMakeInvalid";
      case PocqNodeRole::CallSnpOnce: return "CallSnpOnce";
      case PocqNodeRole::CallSnpOnceFwd: return "CallSnpOnceFwd";
      case PocqNodeRole::CallSnpShared: return "CallSnpShared";
      case PocqNodeRole::CallSnpUnique: return "CallSnpUnique";
      case PocqNodeRole::WaitReadReceipt: return "WaitReadReceipt";
      case PocqNodeRole::WaitCompData: return "WaitCompData";
      case PocqNodeRole::WaitCompAck: return "WaitCompAck";
      case PocqNodeRole::WaitRnfCompAck: return "WaitRnfCompAck";
      case PocqNodeRole::WaitSnpResp: return "WaitSnpResp";
      case PocqNodeRole::WaitSnpRespData: return "WaitSnpRespData";
      case PocqNodeRole::WaitWriteData: return "WaitWriteData";
      case PocqNodeRole::Exit: return "Exit";
      case PocqNodeRole::NumRoles:
      case PocqNodeRole::Invalid:
        return "Invalid";
    }
    return "Invalid";
}

const char *
pocqActionName(PocqActionKind action)
{
    switch (action) {
      case PocqActionKind::StartSlcLookup: return "StartSlcLookup";
      case PocqActionKind::SendReadNoSnp: return "SendReadNoSnp";
      case PocqActionKind::SendCompAck: return "SendCompAck";
      case PocqActionKind::SendCompData: return "SendCompData";
      case PocqActionKind::SendComp: return "SendComp";
      case PocqActionKind::SendCompDbidResp: return "SendCompDbidResp";
      case PocqActionKind::SendSnpOnce: return "SendSnpOnce";
      case PocqActionKind::SendSnpOnceFwd: return "SendSnpOnceFwd";
      case PocqActionKind::SendSnpShared: return "SendSnpShared";
      case PocqActionKind::SendSnpUnique: return "SendSnpUnique";
      case PocqActionKind::SendSnpMakeInvalid: return "SendSnpMakeInvalid";
      case PocqActionKind::FlushSf: return "FlushSf";
      case PocqActionKind::FlushL3: return "FlushL3";
      case PocqActionKind::WriteL3: return "WriteL3";
      case PocqActionKind::WriteL3FlushSf: return "WriteL3FlushSf";
      case PocqActionKind::UpdateSf: return "UpdateSf";
    }
    return "Invalid";
}

} // namespace gem5::Chi

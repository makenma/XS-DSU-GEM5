#include "dev/ai_mesh/control_trigger_coordinator.hh"

#include <algorithm>

namespace gem5
{
namespace ai_mesh
{

namespace
{

bool
implementedTrigger(uint8_t kind)
{
    return kind <= static_cast<uint8_t>(ControlTriggerEvent::AfterGenerateTerminal);
}

bool
terminalState(ControlRecordState state)
{
    return state == ControlRecordState::ControlTerminal ||
        state == ControlRecordState::LocalSubmitFailed ||
        state == ControlRecordState::SuppressedByRunCutoff;
}

}

ControlTriggerCoordinator::ControlTriggerCoordinator(size_t capacity)
    : capacity_(capacity)
{}

bool
ControlTriggerCoordinator::loadFromImage(const AgentPlanImage &image)
{
    records_.clear();
    if (image.controlActions().size() > capacity_)
        return false;
    for (const AgentControlAction &action : image.controlActions()) {
        if (!implementedTrigger(action.triggerKind))
            return false;
        Record record;
        record.action = action;
        records_.push_back(record);
    }
    return true;
}

bool
ControlTriggerCoordinator::matchesAnchor(
    const Record &record, ControlTriggerEvent kind, const ControlAnchor &anchor,
    uint32_t afterControlOrdinal) const
{
    if (static_cast<uint8_t>(kind) != record.action.triggerKind)
        return false;
    if (kind == ControlTriggerEvent::ScenarioStart)
        return true;
    if (kind == ControlTriggerEvent::AfterReleaseTerminal)
        return record.action.hasAfterOrdinal &&
            record.action.afterControlOrdinal == afterControlOrdinal;
    return record.action.hasAnchor &&
        record.action.anchorUserId == anchor.userId &&
        record.action.anchorTaskSeq == anchor.taskSeq &&
        record.action.anchorRepairRound == anchor.repairRound;
}

std::vector<size_t>
ControlTriggerCoordinator::onAuthoritativeEvent(
    ControlTriggerEvent kind, const ControlAnchor &anchor,
    uint32_t afterControlOrdinal, uint64_t now)
{
    std::vector<size_t> ready;
    for (size_t index = 0; index < records_.size(); ++index) {
        Record &record = records_[index];
        if (record.state != ControlRecordState::WaitTrigger || record.fired)
            continue;
        if (!matchesAnchor(record, kind, anchor, afterControlOrdinal))
            continue;
        record.fired = true;
        record.state = ControlRecordState::Ready;
        record.deliveryEdge = kind == ControlTriggerEvent::SameEdgeAsTerminal ?
            now : now + 1;
        ready.push_back(index);
    }
    return ready;
}

std::vector<size_t>
ControlTriggerCoordinator::due(uint64_t edge) const
{
    std::vector<size_t> indexes;
    for (size_t index = 0; index < records_.size(); ++index)
        if (records_[index].state == ControlRecordState::Ready &&
                records_[index].deliveryEdge <= edge)
            indexes.push_back(index);
    return indexes;
}

std::optional<uint64_t>
ControlTriggerCoordinator::nextDueEdge() const
{
    std::optional<uint64_t> earliest;
    for (const Record &record : records_)
        if (record.state == ControlRecordState::Ready &&
                (!earliest || record.deliveryEdge < *earliest))
            earliest = record.deliveryEdge;
    return earliest;
}

bool
ControlTriggerCoordinator::markMaterialized(size_t index)
{
    if (index >= records_.size() ||
            records_[index].state != ControlRecordState::Ready)
        return false;
    records_[index].state = ControlRecordState::Materialized;
    return true;
}

bool
ControlTriggerCoordinator::markControlTerminal(size_t index)
{
    if (index >= records_.size() ||
            records_[index].state != ControlRecordState::Materialized)
        return false;
    records_[index].state = ControlRecordState::ControlTerminal;
    return true;
}

bool
ControlTriggerCoordinator::markLocalSubmitFailed(size_t index)
{
    if (index >= records_.size() ||
            records_[index].state != ControlRecordState::Materialized)
        return false;
    records_[index].state = ControlRecordState::LocalSubmitFailed;
    return true;
}

bool
ControlTriggerCoordinator::markSuppressedByRunCutoff(size_t index)
{
    if (index >= records_.size() ||
            records_[index].state != ControlRecordState::WaitTrigger)
        return false;
    records_[index].state = ControlRecordState::SuppressedByRunCutoff;
    return true;
}

bool
ControlTriggerCoordinator::allTerminal() const
{
    for (const Record &record : records_)
        if (!terminalState(record.state))
            return false;
    return true;
}

const AgentControlAction &
ControlTriggerCoordinator::action(size_t index) const
{
    return records_.at(index).action;
}

ControlRecordState
ControlTriggerCoordinator::state(size_t index) const
{
    return records_.at(index).state;
}

}

}

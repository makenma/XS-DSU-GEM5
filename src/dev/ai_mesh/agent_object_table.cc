#include "dev/ai_mesh/agent_object_table.hh"

#include <limits>

namespace gem5
{
namespace ai_mesh
{

AgentObjectTable::AgentObjectTable(size_t capacity) : capacity(capacity)
{}

std::optional<AgentObjectId>
AgentObjectTable::reserve(AgentObjectKind kind, uint64_t plannedBytes,
                          uint64_t allocationBytes)
{
    if (plannedBytes == 0 || allocationBytes < plannedBytes)
        return std::nullopt;
    if (live >= capacity || nextValue == std::numeric_limits<uint64_t>::max())
        return std::nullopt;
    Record record;
    record.kind = kind;
    record.planned = plannedBytes;
    record.allocation = allocationBytes;
    const AgentObjectId id(nextValue++);
    records.emplace(id.value(), record);
    ++live;
    return id;
}

AgentObjectTable::Record *
AgentObjectTable::find(AgentObjectId id)
{
    const auto entry = records.find(id.value());
    return entry == records.end() ? nullptr : &entry->second;
}

const AgentObjectTable::Record *
AgentObjectTable::find(AgentObjectId id) const
{
    const auto entry = records.find(id.value());
    return entry == records.end() ? nullptr : &entry->second;
}

bool
AgentObjectTable::beginProduce(AgentObjectId id)
{
    Record *record = find(id);
    if (!record || record->state != AgentObjectState::Reserved)
        return false;
    record->state = AgentObjectState::Producing;
    return true;
}

bool
AgentObjectTable::extendValidPrefix(AgentObjectId id, uint64_t bytes)
{
    Record *record = find(id);
    if (!record || record->state != AgentObjectState::Producing)
        return false;
    if (bytes > record->planned - record->valid)
        return false;
    record->valid += bytes;
    return true;
}

bool
AgentObjectTable::commit(AgentObjectId id)
{
    Record *record = find(id);
    if (!record || record->state != AgentObjectState::Producing)
        return false;
    if (record->valid != record->planned)
        return false;
    record->state = AgentObjectState::Committed;
    record->producerDrained = true;
    return true;
}

bool
AgentObjectTable::commitHostObject(AgentObjectId id)
{
    Record *record = find(id);
    if (!record || record->state != AgentObjectState::Reserved)
        return false;
    record->valid = record->planned;
    record->state = AgentObjectState::Committed;
    record->producerDrained = true;
    return true;
}

bool
AgentObjectTable::markProducerDrained(AgentObjectId id)
{
    Record *record = find(id);
    if (!record)
        return false;
    record->producerDrained = true;
    return true;
}

bool
AgentObjectTable::acquireConsumerRef(AgentObjectId id)
{
    Record *record = find(id);
    if (!record ||
        (record->state != AgentObjectState::Committed &&
         record->state != AgentObjectState::Consuming))
        return false;
    record->state = AgentObjectState::Consuming;
    ++record->refs;
    return true;
}

bool
AgentObjectTable::releaseConsumerRef(AgentObjectId id)
{
    Record *record = find(id);
    if (!record || record->refs == 0)
        return false;
    --record->refs;
    return true;
}

bool
AgentObjectTable::readAllowed(AgentObjectId id, uint64_t offset,
                              uint64_t bytes) const
{
    const Record *record = find(id);
    if (!record ||
        (record->state != AgentObjectState::Committed &&
         record->state != AgentObjectState::Consuming))
        return false;
    if (offset > record->valid || bytes > record->valid - offset)
        return false;
    return true;
}

bool
AgentObjectTable::poison(AgentObjectId id)
{
    Record *record = find(id);
    if (!record ||
        (record->state == AgentObjectState::Released ||
         record->state == AgentObjectState::AbortedPoisoned ||
         record->state == AgentObjectState::ErrorDrained))
        return false;
    record->state = AgentObjectState::AbortedPoisoned;
    return true;
}

bool
AgentObjectTable::errorDrain(AgentObjectId id)
{
    Record *record = find(id);
    if (!record || record->state != AgentObjectState::AbortedPoisoned)
        return false;
    if (!record->producerDrained)
        return false;
    record->refs = 0;
    record->state = AgentObjectState::ErrorDrained;
    return true;
}

bool
AgentObjectTable::release(AgentObjectId id)
{
    Record *record = find(id);
    if (!record ||
        (record->state != AgentObjectState::Committed &&
         record->state != AgentObjectState::Consuming &&
         record->state != AgentObjectState::ErrorDrained))
        return false;
    if (record->refs != 0 || !record->producerDrained)
        return false;
    record->state = AgentObjectState::Released;
    --live;
    return true;
}

bool
AgentObjectTable::known(AgentObjectId id) const
{
    return find(id) != nullptr;
}

AgentObjectState
AgentObjectTable::state(AgentObjectId id) const
{
    const Record *record = find(id);
    return record ? record->state : AgentObjectState::Released;
}

AgentObjectKind
AgentObjectTable::kind(AgentObjectId id) const
{
    const Record *record = find(id);
    return record ? record->kind : AgentObjectKind::GeneratedCode;
}

uint64_t
AgentObjectTable::plannedBytes(AgentObjectId id) const
{
    const Record *record = find(id);
    return record ? record->planned : 0;
}

uint64_t
AgentObjectTable::allocationBytes(AgentObjectId id) const
{
    const Record *record = find(id);
    return record ? record->allocation : 0;
}

uint64_t
AgentObjectTable::validBytes(AgentObjectId id) const
{
    const Record *record = find(id);
    return record ? record->valid : 0;
}

uint32_t
AgentObjectTable::consumerRefs(AgentObjectId id) const
{
    const Record *record = find(id);
    return record ? record->refs : 0;
}

size_t
AgentObjectTable::liveCount() const
{
    return live;
}

}
}

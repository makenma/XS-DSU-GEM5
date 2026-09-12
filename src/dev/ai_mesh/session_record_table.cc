#include "dev/ai_mesh/session_record_table.hh"

namespace gem5
{
namespace ai_mesh
{

SessionRecordTable::SessionRecordTable(uint32_t capacity) : capacity(capacity)
{}

const SessionRecord *
SessionRecordTable::find(uint64_t sessionId, uint64_t kvHandle) const
{
    for (const SessionRecord &record : records)
        if (record.sessionId == sessionId && record.kvHandle == kvHandle)
            return &record;
    return nullptr;
}

bool
SessionRecordTable::insert(uint64_t sessionId, uint64_t kvHandle,
                           uint32_t generation)
{
    if (find(sessionId, kvHandle) != nullptr ||
            records.size() >= capacity)
        return false;
    SessionRecord record;
    record.sessionId = sessionId;
    record.kvHandle = kvHandle;
    record.generation = generation;
    records.push_back(record);
    return true;
}

bool
SessionRecordTable::erase(uint64_t sessionId, uint64_t kvHandle)
{
    for (size_t index = 0; index < records.size(); ++index)
        if (records[index].sessionId == sessionId &&
                records[index].kvHandle == kvHandle) {
            records.erase(records.begin() + index);
            ++tombstoneCount;
            return true;
        }
    return false;
}

}

}

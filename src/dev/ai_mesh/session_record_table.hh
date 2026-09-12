#ifndef DEV_AI_MESH_SESSION_RECORD_TABLE_HH
#define DEV_AI_MESH_SESSION_RECORD_TABLE_HH

#include <cstdint>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

struct SessionRecord
{
    uint64_t sessionId = 0;
    uint64_t kvHandle = 0;
    uint32_t generation = 0;
};

class SessionRecordTable
{
  public:
    explicit SessionRecordTable(uint32_t capacity);

    const SessionRecord *find(uint64_t sessionId, uint64_t kvHandle) const;
    bool insert(uint64_t sessionId, uint64_t kvHandle, uint32_t generation);
    bool erase(uint64_t sessionId, uint64_t kvHandle);
    size_t size() const { return records.size(); }
    uint64_t tombstones() const { return tombstoneCount; }

  private:
    const uint32_t capacity;
    std::vector<SessionRecord> records;
    uint64_t tombstoneCount = 0;
};

}

}

#endif

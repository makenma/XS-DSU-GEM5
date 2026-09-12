#ifndef DEV_AI_MESH_AGENT_OBJECT_TABLE_HH
#define DEV_AI_MESH_AGENT_OBJECT_TABLE_HH

#include <cstddef>
#include <cstdint>
#include <optional>
#include <unordered_map>

#include "dev/ai_mesh/agent_runtime_types.hh"

namespace gem5
{
namespace ai_mesh
{

enum class AgentObjectKind : uint8_t
{
    GeneratedCode = 0,
    RawLog = 1,
    Excerpt = 2,
};

enum class AgentObjectState : uint8_t
{
    Reserved = 0,
    Producing,
    Committed,
    Consuming,
    Released,
    AbortedPoisoned,
    ErrorDrained,
};

class AgentObjectTable
{
  public:
    explicit AgentObjectTable(size_t capacity);

    std::optional<AgentObjectId> reserve(AgentObjectKind kind,
                                         uint64_t plannedBytes,
                                         uint64_t allocationBytes);
    bool beginProduce(AgentObjectId id);
    bool extendValidPrefix(AgentObjectId id, uint64_t bytes);
    bool commit(AgentObjectId id);
    bool commitHostObject(AgentObjectId id);
    bool markProducerDrained(AgentObjectId id);
    bool acquireConsumerRef(AgentObjectId id);
    bool releaseConsumerRef(AgentObjectId id);
    bool readAllowed(AgentObjectId id, uint64_t offset,
                     uint64_t bytes) const;
    bool poison(AgentObjectId id);
    bool errorDrain(AgentObjectId id);
    bool release(AgentObjectId id);

    bool known(AgentObjectId id) const;
    AgentObjectState state(AgentObjectId id) const;
    AgentObjectKind kind(AgentObjectId id) const;
    uint64_t plannedBytes(AgentObjectId id) const;
    uint64_t allocationBytes(AgentObjectId id) const;
    uint64_t validBytes(AgentObjectId id) const;
    uint32_t consumerRefs(AgentObjectId id) const;
    size_t liveCount() const;

  private:
    struct Record
    {
        AgentObjectKind kind = AgentObjectKind::GeneratedCode;
        AgentObjectState state = AgentObjectState::Reserved;
        uint64_t planned = 0;
        uint64_t allocation = 0;
        uint64_t valid = 0;
        uint32_t refs = 0;
        bool producerDrained = false;
    };

    Record *find(AgentObjectId id);
    const Record *find(AgentObjectId id) const;

    size_t capacity;
    std::unordered_map<uint64_t, Record> records;
    uint64_t nextValue = 1;
    size_t live = 0;
};

}
}
#endif

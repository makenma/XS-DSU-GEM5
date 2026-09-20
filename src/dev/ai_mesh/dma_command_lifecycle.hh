#ifndef __DEV_AI_MESH_DMA_COMMAND_LIFECYCLE_HH__
#define __DEV_AI_MESH_DMA_COMMAND_LIFECYCLE_HH__

#include <cstdint>
#include <map>
#include <set>
#include <vector>

#include "dev/ai_mesh/mesh_runtime_observations.hh"

namespace gem5
{
namespace ai_mesh
{

struct DmaCommandState
{
    uint32_t generation = 0;
    uint32_t completion_event = 0;
    uint32_t transfer_id = 0;
    uint16_t receiver_core = 0;
    std::vector<uint32_t> receiver_allocations;
    std::vector<uint32_t> descriptors;
    std::set<uint32_t> pending;
    size_t next_descriptor = 0;
    bool terminal = false;
    bool failed = false;
    bool cancelled = false;
    bool error_published = false;
    std::set<uint32_t> committed;
    uint64_t expected_bytes = 0;
    uint64_t committed_bytes = 0;
    bool published = false;
    uint32_t notifications = 0;
};

class DmaCommandLifecycle
{
  public:
    struct Admission
    {
        uint32_t command_id = 0;
        uint32_t generation = 0;
        uint32_t completion_event = 0;
        uint32_t transfer_id = 0;
        uint16_t receiver_core = 0;
        std::vector<uint32_t> receiver_allocations;
        uint64_t expected_bytes = 0;
        std::vector<uint32_t> descriptors;
    };

    struct Retirement
    {
        bool known = false;
        bool duplicate = false;
        bool drained = false;
        bool first_failure = false;
        bool notify = false;
        bool has_transfer = false;
        uint16_t peer_core = 0;
        uint32_t transfer_id = 0;
        TransferObservation progress;
    };

    const DmaCommandState *find(uint32_t command_id) const;
    bool admitted(uint32_t command_id) const;
    bool submitting(uint32_t command_id, uint32_t generation) const;
    DmaCommandState *admit(const Admission &admission);
    uint32_t nextDescriptor(uint32_t command_id) const;
    void noteSubmitted(uint32_t command_id, uint32_t descriptor_id);
    bool fail(uint32_t command_id);
    bool cancel(uint32_t command_id);
    void markErrorPublished(uint32_t command_id);
    void finish(uint32_t command_id);
    void evict(uint32_t command_id) { commands.erase(command_id); }
    Retirement retire(uint32_t command_id, uint32_t descriptor_id,
                      uint64_t useful_bytes, bool success, uint16_t peer_core);
    const std::map<uint32_t, DmaCommandState> &states() const
    {
        return commands;
    }
    bool drained() const;
    void clear() { commands.clear(); }

  private:
    TransferObservation progressOf(const DmaCommandState &state) const;
    std::map<uint32_t, DmaCommandState> commands;
};

} // namespace ai_mesh
} // namespace gem5

#endif // __DEV_AI_MESH_DMA_COMMAND_LIFECYCLE_HH__

#ifndef DEV_AI_MESH_MESH_DMA_OBSERVER_HH
#define DEV_AI_MESH_MESH_DMA_OBSERVER_HH

#include <cstdint>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

struct DmaCommittedSegment
{
    uint32_t row = 0;
    uint64_t offset_in_row = 0;
    uint64_t bytes = 0;
};

class MeshDmaObserver
{
  public:
    virtual ~MeshDmaObserver() = default;
    virtual void onDmaAccepted(uint32_t descriptor_id, uint32_t command_id,
                               uint64_t instance_generation,
                               uint64_t request_id,
                               uint32_t request_generation,
                               uint64_t tick) = 0;
    virtual void onDmaTerminal(
        uint32_t descriptor_id, uint32_t command_id, uint64_t committed_bytes,
        uint32_t error_code, uint64_t instance_generation, uint64_t request_id,
        uint32_t request_generation, uint64_t tick,
        const std::vector<DmaCommittedSegment> &committed_segments) = 0;
};

}
}
#endif

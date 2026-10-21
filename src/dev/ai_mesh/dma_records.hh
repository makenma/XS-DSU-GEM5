#ifndef DEV_AI_MESH_DMA_RECORDS_HH
#define DEV_AI_MESH_DMA_RECORDS_HH

#include <cstdint>
#include <string>
#include <vector>

#include "base/checkpoint_fields.hh"
#include "dev/ai_mesh/mesh_dma_observer.hh"

namespace gem5
{
namespace ai_mesh
{

struct ActualTraffic
{
    GEM5_CHECKPOINT_AGGREGATE()
    uint64_t read_bytes = 0;
    uint64_t write_bytes = 0;
    uint64_t p2p_bytes = 0;
    uint64_t fill_bytes = 0;
    uint32_t read_bursts = 0;
    uint32_t write_bursts = 0;
    uint32_t p2p_bursts = 0;
    uint64_t read_discarded_bytes = 0;
    uint64_t write_drained_uncommitted_bytes = 0;
    uint32_t error_code = 0;
    uint16_t dma_kind = 0;
    std::string payload_digest;
    std::vector<DmaCommittedSegment> committed_segments;
};

enum class DmaStatus : uint8_t
{
    OK = 0,
    AXI_READ_ERROR = 1,
    AXI_WRITE_ERROR = 2,
    SUPPRESSED = 3,
    INDEX_OUT_OF_RANGE = 4,
};

inline const char *
dmaStatusName(DmaStatus status)
{
    switch (status) {
      case DmaStatus::OK:
        return "OK";
      case DmaStatus::AXI_READ_ERROR:
        return "AXI_READ_ERROR";
      case DmaStatus::AXI_WRITE_ERROR:
        return "AXI_WRITE_ERROR";
      case DmaStatus::INDEX_OUT_OF_RANGE:
        return "INDEX_OUT_OF_RANGE";
      case DmaStatus::SUPPRESSED:
        return "SUPPRESSED";
    }
    return "OK";
}

} // namespace ai_mesh
} // namespace gem5

#endif

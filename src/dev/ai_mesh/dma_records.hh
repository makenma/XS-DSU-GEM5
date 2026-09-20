#ifndef DEV_AI_MESH_DMA_RECORDS_HH
#define DEV_AI_MESH_DMA_RECORDS_HH

#include <cstdint>
#include <string>

namespace gem5
{
namespace ai_mesh
{

struct ActualTraffic
{
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
    std::string payload_digest;
};

enum class DmaStatus : uint8_t
{
    OK = 0,
    AXI_READ_ERROR = 1,
    AXI_WRITE_ERROR = 2,
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
    }
    return "OK";
}

} // namespace ai_mesh
} // namespace gem5

#endif

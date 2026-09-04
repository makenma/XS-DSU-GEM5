#ifndef DEV_AI_MESH_DMA_TYPES_HH
#define DEV_AI_MESH_DMA_TYPES_HH

#include <cstdint>
#include <map>
#include <string>

#include "dev/ai_mesh/mesh_binary.hh"
#include "sim/clocked_object.hh"

namespace gem5
{
namespace ai_mesh
{

class MeshDummyCore;

// Per-descriptor actual traffic row shared by the mock transport and the
// real AXI DMA engine (result-JSON oracle reconciliation).
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

// Abstract DMA engine front-end owned by a MeshDummyCore.  The mock engine
// (analytic completion over MockAxiTransport) and the real engine
// (AxiGarnetBridge backed) implement the same submit contract: false means
// finite-queue backpressure and must not consume or account the descriptor.
class DmaEngineBase : public ClockedObject
{
  public:
    DmaEngineBase(const ClockedObjectParams &p) : ClockedObject(p) {}

    virtual bool submit(const DecodedDmaDescriptor &descriptor, Tick issue_tick) = 0;
    virtual void bindFillPattern(uint32_t command_id, uint64_t pattern) = 0;
    virtual bool idle() const = 0;
    virtual void bindOwner(MeshDummyCore *core, const struct RuntimeArch *arch) = 0;
    virtual const std::map<uint32_t, ActualTraffic> &actualTraffic() const = 0;
    // Live (not yet terminal) descriptor count across both directions.
    virtual uint32_t liveDescriptors() const = 0;
};

} // namespace ai_mesh
} // namespace gem5

#endif

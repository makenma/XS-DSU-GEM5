#ifndef DEV_AI_MESH_DMA_TYPES_HH
#define DEV_AI_MESH_DMA_TYPES_HH

#include <cstdint>
#include <map>
#include <string>

#include "dev/ai_mesh/dma_records.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "sim/clocked_object.hh"

namespace gem5
{
namespace ai_mesh
{

class MeshDummyCore;

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
    // Called once per program instance before the first command issues: the
    // engine must be drained and must not carry executable state across the
    // instance boundary.
    virtual void beginInstance() {}
    // Observation-only functional read of an admitted endpoint address, local
    // or peer.  It never changes timing, traffic or business state and returns
    // false when the backend cannot serve the read.
    virtual bool readFunctional(uint64_t address, uint64_t size, uint8_t *out)
    {
        return false;
    }
};

} // namespace ai_mesh
} // namespace gem5

#endif

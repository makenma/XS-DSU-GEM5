#ifndef DEV_AI_MESH_DMA_TYPES_HH
#define DEV_AI_MESH_DMA_TYPES_HH

#include <cstdint>
#include <vector>
#include <map>
#include <string>

#include "dev/ai_mesh/dma_records.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_dma_observer.hh"
#include "dev/ai_mesh/runtime_key.hh"
#include "dev/ai_mesh/serving_instance_binding.hh"
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

    // Identity is derived by the core (the owner of wire ids) and passed in:
    // the engine only carries typed keys, so overlay descriptors are not
    // forced back through static wire-id lookups.
    virtual void bindInstance(const ServingInstanceBinding &value)
    {
        (void)value;
    }

    virtual bool submit(const DecodedDmaDescriptor &descriptor,
                        const RuntimeObjectKey &descriptor_key,
                        const RuntimeObjectKey &command_key,
                        const RuntimeObjectKey &completion_event,
                        Tick issue_tick) = 0;
    virtual void bindFillPattern(RuntimeObjectKey command,
                                 uint64_t pattern) = 0;
    // Exact contract content of a fill; engines that install fill rows must
    // honour it (see MoeOverlayDmaPort).
    virtual void bindFillContent(RuntimeObjectKey command,
                                 const std::vector<uint8_t> &content,
                                 bool install_bytes)
    {
        (void)command;
        (void)content;
        (void)install_bytes;
    }
    virtual void releaseFillBindings(InstanceGeneration instance) {}
    virtual bool idle() const = 0;
    virtual void bindOwner(MeshDummyCore *core, const struct RuntimeArch *arch) = 0;
    virtual const std::map<RuntimeObjectKey, ActualTraffic> &
    actualTraffic() const = 0;
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

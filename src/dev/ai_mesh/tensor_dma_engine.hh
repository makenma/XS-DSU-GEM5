#ifndef DEV_AI_MESH_TENSOR_DMA_ENGINE_HH
#define DEV_AI_MESH_TENSOR_DMA_ENGINE_HH

#include <cstdint>
#include <map>
#include <vector>

#include "dev/ai_mesh/dma_types.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_splitter.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

struct TensorDmaEngineParams;

namespace ai_mesh
{

class MockAxiTransport;

// Analytic mock DMA engine (Gate 1 prototype scope): completion is a
// scheduled analytic latency and bytes move through MockAxiTransport at
// completion time.  Retained for the mock unit-test layer.
class TensorDmaEngine : public DmaEngineBase
{
  public:
    using Params = TensorDmaEngineParams;

    TensorDmaEngine(const Params &p);

    bool submit(const DecodedDmaDescriptor &descriptor, Tick issue_tick) override;
    void bindFillPattern(uint32_t command_id, uint64_t pattern) override
    {
        fill_patterns[command_id] = pattern;
    }

    bool idle() const override { return outstanding == 0; }
    void bindOwner(MeshDummyCore *core, const RuntimeArch *arch) override;
    const std::map<uint32_t, ActualTraffic> &actualTraffic() const override;
    uint32_t liveDescriptors() const override { return outstanding; }

  private:
    void completeDescriptor(const DecodedDmaDescriptor &descriptor, Tick commit_tick);

    struct EngineEvent : public Event
    {
        TensorDmaEngine *engine;
        DecodedDmaDescriptor descriptor;
        Tick commit_tick;

        EngineEvent(TensorDmaEngine *engine_, const DecodedDmaDescriptor &descriptor_,
                    Tick commit_tick_)
            : Event(), engine(engine_), descriptor(descriptor_), commit_tick(commit_tick_)
        {
            setFlags(AutoDelete);
        }

        void process() override { engine->completeDescriptor(descriptor, commit_tick); }
        const char *description() const override { return "ai_mesh.dma.complete"; }
    };

    uint16_t core_id;
    Cycles setup_cycles;
    uint32_t descriptor_queue_depth;
    uint32_t max_outstanding;
    MockAxiTransport *transport = nullptr;
    MeshDummyCore *owner = nullptr;
    const RuntimeArch *arch = nullptr;
    uint32_t outstanding = 0;
    std::map<uint32_t, uint64_t> fill_patterns;
};

} // namespace ai_mesh
} // namespace gem5

#endif

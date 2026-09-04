#ifndef DEV_AI_MESH_MESH_SPLITTER_HH
#define DEV_AI_MESH_MESH_SPLITTER_HH

#include <cstdint>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"

namespace gem5
{
namespace ai_mesh
{

// Independent C++ implementation of the spec 7.2 burst splitter.  The
// Python reference splitter is a separate implementation; both are pinned
// to the shared cross-language golden vectors.
struct AxiBurst
{
    uint64_t beat_base = 0;
    uint64_t logical_start = 0;
    uint64_t useful_bytes = 0;
    uint32_t beats = 0;
};

std::vector<AxiBurst> splitBursts(uint64_t address, uint64_t useful, uint32_t width,
                                  uint32_t max_beats);

struct DmaPlan
{
    uint32_t bursts = 0;
    uint64_t beats = 0;
};

DmaPlan planDescriptor(const DecodedDmaDescriptor &descriptor, uint32_t width);

// Burst plan shaped by the REMOTE endpoint: source for reads (LOAD/
// PREFETCH), destination for writes (STORE/P2P).  The local side follows
// the same logical bytes but its address alignment does not shape bursts.
DmaPlan planDescriptorRemote(const DecodedDmaDescriptor &descriptor, uint32_t width);

// Serialized single-engine DMA latency in CYCLES: setup + per-burst base
// latency + one cycle per beat.  Kept pure so both the engine and the unit
// tests share one definition.
inline uint64_t dmaTransferCycles(uint64_t setup_cycles, uint64_t burst_base_latency,
                                  uint32_t bursts, uint64_t beats)
{
    return setup_cycles + burst_base_latency * bursts + beats;
}

} // namespace ai_mesh
} // namespace gem5

#endif

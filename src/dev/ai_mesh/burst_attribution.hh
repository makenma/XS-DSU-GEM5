#ifndef DEV_AI_MESH_BURST_ATTRIBUTION_HH
#define DEV_AI_MESH_BURST_ATTRIBUTION_HH

#include <cstdint>

#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

// Every real AXI burst with the admitted execution it belongs to and the
// retirement facts it earned: which instance, core, command generation and
// descriptor submitted it, which burst of that descriptor's admitted plan it
// is, and when its response, local SRAM commit and descriptor terminal tick
// happened.  The attribution is recorded where the burst is submitted, so a
// completion is never matched by address order or by the last descriptor map.
struct BurstAttribution
{
    uint32_t instance = 0;
    uint16_t core_id = 0;
    uint32_t command_id = 0;
    uint32_t generation = 0;
    uint32_t descriptor_id = 0;
    uint32_t burst_index = 0;
    uint64_t ordinal = 0;
    bool read = false;
    uint16_t axi_id = 0;
    uint64_t address = 0;
    uint32_t beats = 0;
    uint32_t beat_bytes = 0;
    uint64_t useful_bytes = 0;
    uint64_t logical_start = 0;
    Tick issue_tick = 0;         // burst submitted to the bridge
    Tick ar_aw_tick = 0;         // real AR/AW handshake, from the bridge
    Tick response_tick = 0;      // last R beat / B response
    Tick local_commit_tick = 0;  // LOAD/PREFETCH SRAM commit
    Tick retire_tick = 0;        // RLAST consumed / B consumed
    Tick done_tick = 0;          // descriptor terminal tick
    bool errored = false;
};

} // namespace ai_mesh
} // namespace gem5

#endif

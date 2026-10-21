#ifndef DEV_AI_MESH_MESH_CLOCK_HH
#define DEV_AI_MESH_MESH_CLOCK_HH

#include "sim/clocked_object.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

// The next core clock edge strictly after the current tick.  Runtime events
// are published on this edge so a completion processed in one cycle can only
// be observed in a later cycle (spec 8.2/8.3: at least one core cycle per
// event/barrier level, no same-cycle recursive completion).
inline Tick
nextCoreEdge(const ClockedObject &object)
{
    const Tick period = object.clockPeriod();
    return (curTick() / period + 1) * period;
}

} // namespace ai_mesh
} // namespace gem5

#endif

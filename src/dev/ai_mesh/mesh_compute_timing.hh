#ifndef DEV_AI_MESH_MESH_COMPUTE_TIMING_HH
#define DEV_AI_MESH_MESH_COMPUTE_TIMING_HH

#include <cstdint>

#include "base/logging.hh"

namespace gem5
{
namespace ai_mesh
{

// Single source of the analytic engine formulas: the static program path and
// the MoE overlay both charge engine cycles through these functions, so one
// kernel geometry cannot end up with two timing models.
inline uint64_t tensorEngineCycles(uint64_t batch, uint64_t m, uint64_t n,
                                   uint64_t k, uint64_t macs,
                                   uint64_t efficiency_q16,
                                   const char *context)
{
    fatal_if(macs == 0 || efficiency_q16 == 0,
             "%s has no tensor throughput capability", context);
    const __int128 work = __int128(batch) * m * n * k;
    const __int128 numerator = work * 65536;
    const __int128 denominator = __int128(macs) * efficiency_q16;
    fatal_if(work >= (__int128(1) << 63) ||
                 numerator / denominator >= (__int128(1) << 63),
             "%s workload exceeds the schedulable cycle range", context);
    return uint64_t((numerator + denominator - 1) / denominator);
}

// Contributor fan-in of 1 is a copy-through: it pays the frame but no
// reduction ops, which is exactly what the contract charge is.
inline uint64_t reduceEngineCycles(uint64_t elements, uint64_t fan_in,
                                   uint64_t ops_per_cycle,
                                   const char *context)
{
    fatal_if(ops_per_cycle == 0,
             "%s has no reduce throughput capability", context);
    fatal_if(fan_in == 0, "%s has no reduction contributor", context);
    const __int128 ops = __int128(elements) * (fan_in - 1);
    fatal_if(ops >= (__int128(1) << 63),
             "%s workload exceeds the schedulable cycle range", context);
    return uint64_t((ops + ops_per_cycle - 1) / ops_per_cycle);
}

inline uint64_t framedCycles(uint64_t setup, uint64_t engine, uint64_t flush)
{
    return setup + engine + flush;
}

} // namespace ai_mesh
} // namespace gem5

#endif

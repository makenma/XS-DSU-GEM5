#ifndef DEV_AI_MESH_NPU_EXECUTION_SELECTOR_HH
#define DEV_AI_MESH_NPU_EXECUTION_SELECTOR_HH

#include <cstdint>
#include <limits>

namespace gem5
{
namespace ai_mesh
{

inline constexpr uint64_t kNpuNoDeadline = std::numeric_limits<uint64_t>::max();

struct NpuExecutionCandidate
{
    uint64_t requestId = 0;
    uint64_t deadlineTick = kNpuNoDeadline;
    uint64_t readyTick = 0;
    uint8_t qos = 0;
};

constexpr bool
npuExecutionPrecedes(const NpuExecutionCandidate &left,
                     const NpuExecutionCandidate &right)
{
    if (left.deadlineTick != right.deadlineTick)
        return left.deadlineTick < right.deadlineTick;
    if (left.qos != right.qos)
        return left.qos > right.qos;
    if (left.readyTick != right.readyTick)
        return left.readyTick < right.readyTick;
    return left.requestId < right.requestId;
}

}
}

#endif

#ifndef DEV_AI_MESH_MESH_IR_SPANS_HH
#define DEV_AI_MESH_MESH_IR_SPANS_HH

#include <cstdint>

namespace gem5
{
namespace ai_mesh
{

inline bool spanFits(uint64_t begin, uint64_t count, uint64_t total)
{
    return begin <= total && count <= total - begin;
}

inline uint64_t checkedMul(uint64_t a, uint64_t b, bool &overflow)
{
    if (a != 0 && b > ~uint64_t(0) / a)
        overflow = true;
    return a * b;
}

inline uint64_t checkedMulAdd(uint64_t a, uint64_t b, uint64_t c, bool &overflow)
{
    const uint64_t product = checkedMul(a, b, overflow);
    if (product > ~uint64_t(0) - c)
        overflow = true;
    return product + c;
}

} // namespace ai_mesh
} // namespace gem5

#endif

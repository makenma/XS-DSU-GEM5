#include "dev/ai_mesh/agent_ring_state.hh"

#include <stdexcept>

namespace gem5
{
namespace ai_mesh
{

RingGeometry::RingGeometry(uint32_t depth) : _depth(depth)
{
    if (_depth == 0 || (_depth & (_depth - 1)) != 0)
        throw std::invalid_argument("ring depth must be a power of two");
}

template class RingState<SqSeq>;
template class RingState<CqSeq>;

}
}

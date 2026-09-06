#include "dev/ai_mesh/agent_protocol_layout.hh"

#include <limits>
#include <stdexcept>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

bool
powerOfTwo(uint32_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

uint64_t
checkedSpan(uint64_t base, uint32_t depth, uint32_t recordBytes)
{
    if (!powerOfTwo(depth))
        throw std::invalid_argument("ring depth must be a power of two");
    if (depth > std::numeric_limits<uint64_t>::max() / recordBytes)
        throw std::invalid_argument("ring span overflows uint64");
    const uint64_t span = uint64_t(depth) * recordBytes;
    if (base > std::numeric_limits<uint64_t>::max() - span)
        throw std::invalid_argument("ring address range overflows uint64");
    return span;
}

}

AgentProtocolLayout::AgentProtocolLayout(
    uint64_t sqBaseValue, uint32_t sqDepthValue,
    uint64_t cqBaseValue, uint32_t cqDepthValue)
    : sqBase(sqBaseValue), cqBase(cqBaseValue),
      sqDepth(sqDepthValue), cqDepth(cqDepthValue),
      sqSpan(checkedSpan(sqBaseValue, sqDepthValue,
                         agent_abi::kSqDescriptorBytes)),
      cqSpan(checkedSpan(cqBaseValue, cqDepthValue,
                         agent_abi::kCqDescriptorBytes))
{
}

uint64_t
AgentProtocolLayout::sqAddress(SqSeq sequence) const
{
    return sqBase + (sequence.value() % sqDepth) *
        agent_abi::kSqDescriptorBytes;
}

uint64_t
AgentProtocolLayout::cqAddress(CqSeq sequence) const
{
    return cqBase + (sequence.value() % cqDepth) *
        agent_abi::kCqDescriptorBytes;
}

}
}

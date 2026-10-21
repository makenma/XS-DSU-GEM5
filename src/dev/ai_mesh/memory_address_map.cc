#include "dev/ai_mesh/memory_address_map.hh"

#include <algorithm>
#include <limits>

namespace gem5::ai_mesh
{
MemoryAddressMap::MemoryAddressMap(std::vector<axi::AxiRange> ranges)
    : decoder(std::move(ranges), 0)
{}

std::optional<std::vector<MemoryAddressSpan>>
MemoryAddressMap::split(uint64_t address, uint64_t size) const
{
    if (!size || size > std::numeric_limits<uint64_t>::max() - address)
        return std::nullopt;
    std::vector<MemoryAddressSpan> result;
    const uint64_t end = address + size;
    for (const auto &range : decoder.ranges()) {
        if (range.end <= address)
            continue;
        if (range.start > address)
            return std::nullopt;
        const uint64_t next = std::min(end, range.end);
        result.push_back({range.dstNode, address, next - address});
        address = next;
        if (address == end)
            return result;
    }
    return std::nullopt;
}
}

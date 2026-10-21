#include "dev/ai_mesh/npu_memory_registry.hh"

#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/mesh_payload_digest.hh"
#include "dev/ai_mesh/npu_memory_endpoint.hh"

namespace gem5::ai_mesh
{
NpuMemoryRegistry::NpuMemoryRegistry(std::vector<NpuMemoryEndpoint *> endpoints)
    : members(std::move(endpoints)), address_map({})
{
    std::vector<axi::AxiRange> ranges;
    for (auto *endpoint : members) {
        fatal_if(!endpoint || endpoint->memoryRanges().empty(),
                 "memory registry requires a backed endpoint");
        const auto &owned = endpoint->memoryRanges();
        fatal_if(!by_node.emplace(owned.front().dstNode, endpoint).second,
                 "memory registry repeats a target owner");
        ranges.insert(ranges.end(), owned.begin(), owned.end());
    }
    address_map = MemoryAddressMap(std::move(ranges));
}

bool
NpuMemoryRegistry::contains(uint64_t address, uint64_t size) const
{
    return address_map.split(address, size).has_value();
}

bool
NpuMemoryRegistry::read(uint64_t address, uint64_t size, uint8_t *out) const
{
    const auto spans = address_map.split(address, size);
    if (!spans)
        return false;
    for (const auto &span : *spans) {
        if (!by_node.at(span.owner)->readBytes(span.address, span.size, out))
            return false;
        out += span.size;
    }
    return true;
}

bool
NpuMemoryRegistry::installImage(const std::vector<MemoryImageSpan> &image)
{
    std::vector<std::vector<MemoryAddressSpan>> plans;
    for (const auto &row : image) {
        auto spans = address_map.split(row.address, row.data.size());
        if (!spans)
            return false;
        plans.push_back(std::move(*spans));
    }
    for (size_t i = 0; i < image.size(); ++i)
        for (const auto &span : plans[i])
            if (!by_node.at(span.owner)->writeBytes(span.address, span.size,
                    image[i].data.data() + span.address - image[i].address))
                return false;
    return true;
}

std::string
NpuMemoryRegistry::rangeDigest(uint64_t address, uint64_t size) const
{
    if (!contains(address, size))
        return {};
    std::vector<uint8_t> bytes(size);
    if (!read(address, size, bytes.data()))
        return {};
    return dualFnvDigest(bytes.data(), size);
}

bool
NpuMemoryRegistry::installFiles(const std::vector<MemoryFileSpan> &image,
    std::string &reason)
{
    return installMemoryFiles(image, address_map,
        [this](uint32_t owner, uint64_t address, size_t size, const uint8_t *data) {
            return by_node.at(owner)->writeBytes(address, size, data);
        }, reason);
}

bool
NpuMemoryRegistry::idle() const
{
    return std::all_of(members.begin(), members.end(),
        [](const auto *endpoint) { return endpoint->functionalIdle(); });
}
}

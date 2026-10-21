#include "dev/ai_mesh/mesh_splitter.hh"
#include "dev/ai_mesh/dma_kind.hh"

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

#include <algorithm>
#include <numeric>

namespace gem5
{
namespace ai_mesh
{

std::vector<AxiBurst> splitBursts(uint64_t address, uint64_t useful, uint32_t width,
                                 uint32_t max_beats)
{
    std::vector<AxiBurst> bursts;
    uint64_t a = address;
    uint64_t remaining = useful;
    while (remaining > 0) {
        uint64_t beat_base = a & ~uint64_t(width - 1);
        uint64_t head = a - beat_base;
        uint64_t page_cap = 4096 - (beat_base & 0xFFF);
        uint64_t burst_cap = uint64_t(max_beats) * width;
        uint64_t take = std::min(remaining, std::min(page_cap - head, burst_cap - head));
        uint64_t beats = (head + take + width - 1) / width;
        bursts.push_back({beat_base, a, take, uint32_t(beats), 0, a - address});
        a += take;
        remaining -= take;
    }
    return bursts;
}

IndexedRowPlan planIndexedRows(uint64_t base, uint64_t stride,
                               uint64_t row_bytes, uint64_t table_rows,
                               uint32_t width, uint32_t max_beats)
{
    IndexedRowPlan result;
    const uint64_t period = 4096 / std::gcd(stride, uint64_t(4096));
    uint64_t firstBursts = 0;
    uint64_t firstBeats = 0;
    for (uint64_t index = 0; index < std::min(table_rows, period); ++index) {
        const uint64_t residue = ((base % 4096) + index * (stride % 4096)) % 4096;
        const auto bursts = splitBursts(residue, row_bytes, width, max_beats);
        uint64_t beats = 0;
        for (const auto &burst : bursts)
            beats += burst.beats;
        if (!index) {
            firstBursts = bursts.size();
            firstBeats = beats;
        }
        result.exact = result.exact && firstBursts == bursts.size() && firstBeats == beats;
        result.bursts = std::max(result.bursts, uint64_t(bursts.size()));
        result.beats = std::max(result.beats, beats);
    }
    return result;
}

DmaPlan planDescriptor(const DecodedDmaDescriptor &descriptor, uint32_t width)
{
    DmaPlan plan;
    if (descriptor.rows == 0 || descriptor.row_bytes == 0)
        return plan;
    for (uint32_t row = 0; row < descriptor.rows; row++) {
        uint64_t base =
            descriptor.src.offset_bytes + uint64_t(row) * descriptor.src_stride_bytes;
        auto bursts = splitBursts(base, descriptor.row_bytes, width,
                                  descriptor.max_burst_beats);
        plan.bursts += uint32_t(bursts.size());
        for (const auto &burst : bursts)
            plan.beats += burst.beats;
    }
    return plan;
}

DmaPlan planDescriptorRemote(const DecodedDmaDescriptor &descriptor, uint32_t width)
{
    const bool is_read =
        dmaKindIsRead(descriptor.kind);
    const DecodedDmaEndpoint &remote = is_read ? descriptor.src : descriptor.dst;
    return planDescriptorRemoteAt(remote.offset_bytes, descriptor, width);
}

DmaPlan planDescriptorRemoteAt(uint64_t remote_base,
                               const DecodedDmaDescriptor &descriptor,
                               uint32_t width)
{
    const bool is_read =
        dmaKindIsRead(descriptor.kind);
    const uint64_t stride =
        is_read ? descriptor.src_stride_bytes : descriptor.dst_stride_bytes;
    DmaPlan plan;
    if (descriptor.rows == 0 || descriptor.row_bytes == 0)
        return plan;
    for (uint32_t row = 0; row < descriptor.rows; row++) {
        const uint64_t base = remote_base + uint64_t(row) * stride;
        const auto bursts = splitBursts(base, descriptor.row_bytes, width,
                                        descriptor.max_burst_beats);
        plan.bursts += uint32_t(bursts.size());
        for (const auto &burst : bursts)
            plan.beats += burst.beats;
    }
    return plan;
}

} // namespace ai_mesh
} // namespace gem5

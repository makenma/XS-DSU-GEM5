#include "dev/ai_mesh/mesh_moe_overlay.hh"

#include <algorithm>
#include <map>
#include <tuple>

namespace gem5::ai_mesh
{

bool MoeOverlayGraph::finalize(const std::vector<mesh_abi::MoeDynamicRegion> &regions,
                              std::string &error)
{
    using namespace mesh_abi;
    using Key = std::tuple<uint32_t, uint16_t, uint32_t>;
    auto candidate = *this;
    candidate.scratch_entries.clear();
    const auto reject = [&](const char *message) {
        error = message;
        return false;
    };
    std::map<Key, uint32_t> ordinals;
    candidate.assignOrdinals();
    for (size_t i = 0; i < entries.size(); ++i) {
        const auto &entry = entries[i];
        if (!entry.ordinal || !ordinals.emplace(
                Key{entry.region_id, entry.kind, entry.ordinal},
                candidate.entries[i].ordinal).second)
            return reject("E_MOE_MATERIALIZATION_V: duplicate insertion identity");
    }
    const auto rewrite = [&](uint32_t region, uint16_t kind, uint32_t &ordinal) {
        if (!ordinal && kind == kMeshObjectKindEVENT)
            return std::any_of(regions.begin(), regions.end(), [region](const auto &row) {
                return row.region_id == region;
            });
        const auto found = ordinals.find({region, kind, ordinal});
        if (found == ordinals.end())
            return false;
        ordinal = found->second;
        return true;
    };
    std::map<std::pair<uint32_t, uint16_t>, uint32_t> counts;
    for (auto &entry : candidate.entries) {
        const auto region = std::find_if(regions.begin(), regions.end(), [&](const auto &row) {
            return row.region_id == entry.region_id;
        });
        if (entry.kind > kMeshObjectKindVIEW ||
                (entry.region_id && (region == regions.end() || region->core_id != entry.owner_core)) ||
                (!entry.region_id && entry.kind != kMeshObjectKindTRANSFER &&
                 !((entry.kind == kMeshObjectKindCOMMAND && entry.role == kMoeCommandRoleGROUP_EXIT) ||
                   (entry.kind == kMeshObjectKindEVENT && entry.role == kMoeEventRoleOVERLAY_EXIT))))
            return reject("E_MOE_MATERIALIZATION_V: invalid object region or owner");
        ++counts[{entry.region_id, entry.kind}];
        if (entry.kind == kMeshObjectKindVIEW &&
                entry.backing_kind == kMoeViewBackingOVERLAY_ALLOCATION &&
                !rewrite(entry.ref_region, kMeshObjectKindALLOCATION, entry.ref_ordinal))
            return reject("E_MOE_MATERIALIZATION_V: missing allocation reference");
        if (entry.kind == kMeshObjectKindCOMMAND && entry.ref_ordinal &&
                !rewrite(entry.ref_region, kMeshObjectKindDESCRIPTOR, entry.ref_ordinal))
            return reject("E_MOE_MATERIALIZATION_V: missing descriptor reference");
        if ((entry.src_view_ordinal && !rewrite(entry.src_view_region,
                kMeshObjectKindVIEW, entry.src_view_ordinal)) ||
                (entry.dst_view_ordinal && !rewrite(entry.dst_view_region,
                kMeshObjectKindVIEW, entry.dst_view_ordinal)))
            return reject("E_MOE_MATERIALIZATION_V: missing endpoint view");
        const std::pair<uint32_t, uint32_t (*)[3]> references[] = {
            {entry.wait_count, entry.wait_refs}, {entry.signal_count, entry.signal_refs},
            {entry.view_count, entry.view_refs}};
        for (const auto &[count, refs] : references) {
            if (count > MoeOverlayEntry::kMaxRefs)
                return reject("E_MOE_MATERIALIZE_CAPACITY: reference count exceeds capacity");
            for (uint32_t i = 0; i < count; ++i)
                if (refs[i][1] > kMeshObjectKindVIEW ||
                        !rewrite(refs[i][0], refs[i][1], refs[i][2]))
                    return reject("E_MOE_MATERIALIZATION_V: unresolved object reference");
        }
    }
    for (const auto &entry : candidate.entries) {
        if (entry.kind != kMeshObjectKindVIEW ||
                entry.backing_kind != kMoeViewBackingOVERLAY_ALLOCATION)
            continue;
        const auto allocation = std::find_if(candidate.entries.begin(), candidate.entries.end(),
            [&](const auto &row) {
                return row.kind == kMeshObjectKindALLOCATION && row.region_id == entry.ref_region &&
                       row.ordinal == entry.ref_ordinal;
            });
        if (allocation == candidate.entries.end() || entry.region_id != allocation->region_id ||
                entry.owner_core != allocation->owner_core || entry.offset > allocation->bytes ||
                entry.bytes > allocation->bytes - entry.offset)
            return reject("E_MOE_MATERIALIZATION_V: view exceeds its owning allocation");
    }
    for (auto &payload : candidate.payload_entries)
        if (!rewrite(payload.region_id, kMeshObjectKindDESCRIPTOR, payload.ordinal))
            return reject("E_MOE_MATERIALIZATION_V: missing fill descriptor");
    for (const auto &region : regions) {
        const std::pair<uint16_t, uint32_t> bounds[] = {
            {kMeshObjectKindCOMMAND, region.max_overlay_commands},
            {kMeshObjectKindEVENT, region.max_overlay_events},
            {kMeshObjectKindDESCRIPTOR, region.max_overlay_descriptors},
            {kMeshObjectKindALLOCATION, region.max_overlay_allocations}};
        for (const auto &[kind, bound] : bounds)
            if (counts[{region.region_id, kind}] > bound)
                return reject("E_MOE_MATERIALIZE_CAPACITY: region object capacity exhausted");
        if (!region.scratch_alignment ||
                (region.scratch_alignment & (region.scratch_alignment - 1)) ||
                region.scratch_offset > UINT64_MAX - region.scratch_bytes)
            return reject("E_MOE_MATERIALIZE_CAPACITY: invalid scratch geometry");
        const uint64_t end = region.scratch_offset + region.scratch_bytes;
        uint64_t cursor = region.scratch_offset;
        std::vector<const MoeOverlayEntry *> allocations;
        for (const auto &entry : candidate.entries)
            if (entry.kind == kMeshObjectKindALLOCATION && entry.region_id == region.region_id)
                allocations.push_back(&entry);
        std::sort(allocations.begin(), allocations.end(), [](const auto *left, const auto *right) {
            if (left->secondary_kind != right->secondary_kind)
                return left->secondary_kind < right->secondary_kind;
            return left->canonicalKey() < right->canonicalKey();
        });
        for (const auto *entry : allocations) {
            const uint64_t alignment = std::max(entry->alignment, region.scratch_alignment);
            if (!entry->alignment || (entry->alignment & (entry->alignment - 1)) ||
                    cursor > UINT64_MAX - (alignment - 1))
                return reject("E_MOE_MATERIALIZE_CAPACITY: invalid allocation alignment");
            cursor = (cursor + alignment - 1) & ~(alignment - 1);
            if (cursor > end || entry->bytes > end - cursor)
                return reject("E_MOE_MATERIALIZE_CAPACITY: scratch exhausted");
            candidate.addScratch({region.region_id, entry->ordinal, cursor, entry->bytes});
            cursor += entry->bytes;
        }
    }
    if (!candidate.validate(error))
        return false;
    *this = std::move(candidate);
    error.clear();
    return true;
}

}

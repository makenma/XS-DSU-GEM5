#include "dev/ai_mesh/mesh_moe_overlay.hh"

#include <algorithm>
#include <cstring>
#include <map>
#include <set>

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

// Canonical key comparison per contract 7.2.3: numeric fields in the
// declared order, never a memcmp over the packed runtime layout.
bool keyLess(const std::vector<uint64_t> &left,
             const std::vector<uint64_t> &right)
{
    return std::lexicographical_compare(left.begin(), left.end(),
                                        right.begin(), right.end());
}

int kindGroup(uint16_t kind)
{
    switch (kind) {
    case mesh_abi::kMeshObjectKindALLOCATION: return 0;
    case mesh_abi::kMeshObjectKindVIEW: return 1;
    case mesh_abi::kMeshObjectKindCOMMAND: return 2;
    case mesh_abi::kMeshObjectKindEVENT: return 3;
    case mesh_abi::kMeshObjectKindDESCRIPTOR: return 4;
    case mesh_abi::kMeshObjectKindTRANSFER: return 5;
    default: return 6;
    }
}

uint32_t regionOf(const MoeOverlayEntry &entry)
{
    return entry.kind == mesh_abi::kMeshObjectKindTRANSFER ? 0
                                                           : entry.region_id;
}

std::vector<MoeOverlayEntry> canonicalOrder(
    const std::vector<MoeOverlayEntry> &entries)
{
    std::vector<MoeOverlayEntry> ordered = entries;
    std::sort(ordered.begin(), ordered.end(),
              [](const MoeOverlayEntry &left, const MoeOverlayEntry &right) {
                  const int left_group = kindGroup(left.kind);
                  const int right_group = kindGroup(right.kind);
                  if (left_group != right_group)
                      return left_group < right_group;
                  if (regionOf(left) != regionOf(right))
                      return regionOf(left) < regionOf(right);
                  return left.ordinal < right.ordinal;
              });
    return ordered;
}

void putU16(std::vector<uint8_t> &out, uint64_t value)
{
    out.push_back(uint8_t(value));
    out.push_back(uint8_t(value >> 8));
}

void putU32(std::vector<uint8_t> &out, uint64_t value)
{
    for (int i = 0; i < 4; i++)
        out.push_back(uint8_t(value >> (8 * i)));
}

void putU64(std::vector<uint8_t> &out, uint64_t value)
{
    for (int i = 0; i < 8; i++)
        out.push_back(uint8_t(value >> (8 * i)));
}

} // anonymous namespace

std::vector<uint64_t> MoeOverlayEntry::canonicalKey() const
{
    using namespace mesh_abi;
    switch (kind) {
    case kMeshObjectKindALLOCATION:
        return {owner_core, secondary_kind, expert_id, src_core, phase};
    case kMeshObjectKindVIEW: {
        std::vector<uint64_t> key = {
            owner_core, secondary_kind, access, validity_extent,
            backing_kind, ref_region, ref_ordinal, offset, bytes,
            semantic_owner_kind, semantic_owner_ref0};
        appendTokenWords(key);
        return key;
    }
    case kMeshObjectKindTRANSFER:
        return {secondary_kind, src_core, dst_core, expert_id, chunk_ordinal};
    case kMeshObjectKindDESCRIPTOR: {
        std::vector<uint64_t> key = {secondary_kind, owner_core, phase,
                                     src_core, dst_core, expert_id,
                                     chunk_ordinal};
        appendTokenWords(key);
        return key;
    }
    case kMeshObjectKindEVENT: {
        std::vector<uint64_t> key = {secondary_kind, owner_core, phase,
                                     src_core, dst_core, expert_id,
                                     chunk_ordinal, role};
        appendTokenWords(key);
        return key;
    }
    case kMeshObjectKindCOMMAND: {
        std::vector<uint64_t> key = {phase, owner_core, src_core, dst_core,
                                     expert_id, chunk_ordinal, role};
        appendTokenWords(key);
        return key;
    }
    default:
        return {static_cast<uint64_t>(kind), region_id};
    }
}

void MoeOverlayGraph::assignOrdinals()
{
    std::map<std::pair<uint32_t, uint16_t>, std::vector<size_t>> buckets;
    for (size_t index = 0; index < entries.size(); index++) {
        const auto &entry = entries[index];
        uint32_t region = entry.kind == mesh_abi::kMeshObjectKindTRANSFER
                              ? 0
                              : entry.region_id;
        buckets[{region, entry.kind}].push_back(index);
    }
    for (auto &bucket : buckets) {
        auto &indices = bucket.second;
        std::stable_sort(indices.begin(), indices.end(),
                         [this](size_t left, size_t right) {
                             return keyLess(entries[left].canonicalKey(),
                                            entries[right].canonicalKey());
                         });
        for (size_t position = 0; position < indices.size(); position++)
            entries[indices[position]].ordinal = uint32_t(position + 1);
    }
}

bool MoeOverlayGraph::validate(std::string &error) const
{
    using namespace mesh_abi;
    std::map<std::pair<uint32_t, uint16_t>, std::vector<uint32_t>> ordinals;
    std::set<std::pair<uint16_t, std::vector<uint64_t>>> keys;
    std::map<std::pair<uint32_t, uint32_t>, size_t> eventProducers;
    for (const auto &entry : entries) {
        uint32_t region = entry.kind == kMeshObjectKindTRANSFER
                              ? 0
                              : entry.region_id;
        if (region > 0 && region > 0x0000FFFF) {
            error = "overlay region id out of range";
            return false;
        }
        ordinals[{region, entry.kind}].push_back(entry.ordinal);
        std::vector<uint64_t> key = entry.canonicalKey();
        if (!keys.insert({entry.kind, key}).second) {
            error = "duplicate canonical overlay key";
            return false;
        }
        if (entry.kind == kMeshObjectKindEVENT)
            eventProducers[{region, entry.ordinal}] = 0;
    }
    for (auto &bucket : ordinals) {
        auto &values = bucket.second;
        std::sort(values.begin(), values.end());
        for (size_t index = 0; index < values.size(); index++)
            if (values[index] != index + 1) {
                error = "overlay ordinals must be dense from 1";
                return false;
            }
    }
    for (const auto &entry : entries) {
        if (entry.kind != kMeshObjectKindEVENT)
            continue;
        if (entry.wait_count != 1) {
            error = "overlay event needs exactly one producer";
            return false;
        }
    }
    for (const auto &entry : entries) {
        if (entry.kind == kMeshObjectKindEVENT && entry.ordinal == 0) {
            error = "overlay ordinal must be positive";
            return false;
        }
    }
    return true;
}

std::vector<uint8_t> MoeOverlayGraph::wireBytes() const
{
    // The Python projection concatenates one array per kind in this order,
    // each sorted by (region, ordinal); the digest mirrors that order.
    std::vector<MoeOverlayEntry> ordered = canonicalOrder(entries);
    std::vector<uint8_t> body;
    body.reserve(ordered.size() * 2 + ordered.size() * 18 * 4);
    for (const auto &entry : ordered) {
        putU16(body, entry.kind);
        putU32(body, entry.region_id);
        putU32(body, entry.ordinal);
        putU32(body, entry.owner_core);
        putU32(body, entry.secondary_kind);
        putU32(body, entry.expert_id);
        putU32(body, entry.src_core);
        putU32(body, entry.dst_core);
        putU32(body, entry.chunk_ordinal);
        putU32(body, entry.phase);
        putU32(body, entry.role);
        putU32(body, entry.access);
        putU32(body, entry.bytes);
        putU32(body, entry.alignment);
        putU32(body, entry.offset);
        putU32(body, entry.ref_region);
        putU32(body, entry.ref_ordinal);
        putU32(body, entry.backing_kind);
        putU32(body, entry.semantic_owner_kind);
        putU32(body, entry.semantic_owner_ref0);
        putU32(body, entry.validity_extent);
        putU32(body, entry.wait_count);
        putU32(body, entry.signal_count);
        body.insert(body.end(), entry.token.begin(), entry.token.end());
    }
    return body;
}

std::array<uint8_t, 32> MoeOverlayGraph::digest() const
{
    return agentSha256(wireBytes());
}

namespace
{

uint64_t readU32(const std::vector<uint8_t> &image, size_t offset)
{
    uint64_t value = 0;
    for (int i = 0; i < 4; i++)
        value |= uint64_t(image[offset + i]) << (8 * i);
    return value;
}

uint64_t readU64(const std::vector<uint8_t> &image, size_t offset)
{
    uint64_t value = 0;
    for (int i = 0; i < 8; i++)
        value |= uint64_t(image[offset + i]) << (8 * i);
    return value;
}

} // anonymous namespace

std::vector<uint8_t> MoeOverlayImage::encode(const MoeOverlayGraph &graph,
                                             uint32_t layer_id) const
{
    std::vector<MoeOverlayEntry> ordered = canonicalOrder(graph.objects());
    std::vector<uint8_t> body;
    putU32(body, kMagic);
    putU32(body, kVersion);
    putU32(body, layer_id);
    putU32(body, uint32_t(ordered.size()));
    putU32(body, kRefsPerObject);
    putU32(body, uint32_t(graph.scratch().size()));
    putU32(body, graph.fillMode());
    putU32(body, uint32_t(graph.payloadEntries().size()));
    body.insert(body.end(), graph.programDigest().begin(),
                graph.programDigest().end());
    while (body.size() < kHeaderBytes)
        body.push_back(0);
    for (const auto &entry : ordered) {
        putU16(body, entry.kind);
        putU32(body, entry.region_id);
        putU32(body, entry.ordinal);
        putU32(body, entry.owner_core);
        putU32(body, entry.secondary_kind);
        putU32(body, entry.expert_id);
        putU32(body, entry.src_core);
        putU32(body, entry.dst_core);
        putU32(body, entry.chunk_ordinal);
        putU32(body, entry.phase);
        putU32(body, entry.role);
        putU32(body, entry.access);
        putU32(body, entry.bytes);
        putU32(body, entry.alignment);
        putU32(body, entry.offset);
        putU32(body, entry.ref_region);
        putU32(body, entry.ref_ordinal);
        putU32(body, entry.backing_kind);
        putU32(body, entry.semantic_owner_kind);
        putU32(body, entry.semantic_owner_ref0);
        putU32(body, entry.validity_extent);
        putU32(body, entry.wait_count);
        putU32(body, entry.signal_count);
        body.insert(body.end(), entry.token.begin(), entry.token.end());
        putU32(body, entry.src_view_region);
        putU32(body, entry.src_view_ordinal);
        putU32(body, entry.dst_view_region);
        putU32(body, entry.dst_view_ordinal);
        for (uint32_t ref = 0; ref < kRefsPerObject; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++)
                putU32(body, entry.wait_refs[ref][field]);
        for (uint32_t ref = 0; ref < kRefsPerObject; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++)
                putU32(body, entry.signal_refs[ref][field]);
        for (uint32_t ref = 0; ref < kRefsPerObject; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++)
                putU32(body, entry.view_refs[ref][field]);
    }
    const auto &scratch_rows = graph.scratch();
    for (const auto &interval : scratch_rows) {
        putU32(body, interval.region_id);
        putU32(body, interval.ordinal);
        putU64(body, interval.offset);
        putU64(body, interval.bytes);
    }
    for (const auto &payload : graph.payloadEntries()) {
        putU32(body, payload.region_id);
        putU32(body, payload.ordinal);
        putU64(body, payload.offset);
        putU64(body, payload.bytes);
    }
    body.insert(body.end(), graph.payloadBlob().begin(),
                graph.payloadBlob().end());
    return body;
}

bool MoeOverlayImage::decode(const std::vector<uint8_t> &image,
                             MoeOverlayGraph &graph, uint32_t &layer_id,
                             std::string &error) const
{
    if (image.size() < kHeaderBytes) {
        error = "overlay image smaller than its header";
        return false;
    }
    if (readU32(image, 0) != kMagic) {
        error = "overlay image magic mismatch";
        return false;
    }
    if (readU32(image, 4) != kVersion) {
        error = "overlay image version mismatch";
        return false;
    }
    layer_id = uint32_t(readU32(image, 8));
    const uint64_t count = readU32(image, 12);
    if (readU32(image, 16) != kRefsPerObject) {
        error = "overlay image reference bound mismatch";
        return false;
    }
    const uint64_t scratch_count = readU32(image, 20);
    const uint64_t payload_count = readU32(image, 28);
    const uint64_t fixed = kHeaderBytes + count * kRecordBytes +
                           scratch_count * kScratchEntryBytes +
                           payload_count * kPayloadEntryBytes;
    if (image.size() < fixed) {
        error = "overlay image size does not match its object count";
        return false;
    }
    const uint64_t payload_bytes = image.size() - fixed;
    for (uint64_t index = 0; index < count; index++) {
        const size_t base = kHeaderBytes + index * kRecordBytes;
        MoeOverlayEntry entry;
        entry.kind = uint16_t(image[base]);
        entry.region_id = uint32_t(readU32(image, base + 2));
        entry.ordinal = uint32_t(readU32(image, base + 6));
        entry.owner_core = uint32_t(readU32(image, base + 10));
        entry.secondary_kind = uint32_t(readU32(image, base + 14));
        entry.expert_id = uint32_t(readU32(image, base + 18));
        entry.src_core = uint32_t(readU32(image, base + 22));
        entry.dst_core = uint32_t(readU32(image, base + 26));
        entry.chunk_ordinal = uint32_t(readU32(image, base + 30));
        entry.phase = uint32_t(readU32(image, base + 34));
        entry.role = uint32_t(readU32(image, base + 38));
        entry.access = uint32_t(readU32(image, base + 42));
        entry.bytes = uint32_t(readU32(image, base + 46));
        entry.alignment = uint32_t(readU32(image, base + 50));
        entry.offset = uint32_t(readU32(image, base + 54));
        entry.ref_region = uint32_t(readU32(image, base + 58));
        entry.ref_ordinal = uint32_t(readU32(image, base + 62));
        entry.backing_kind = uint32_t(readU32(image, base + 66));
        entry.semantic_owner_kind = uint32_t(readU32(image, base + 70));
        entry.semantic_owner_ref0 = uint32_t(readU32(image, base + 74));
        entry.validity_extent = uint32_t(readU32(image, base + 78));
        entry.wait_count = uint32_t(readU32(image, base + 82));
        entry.signal_count = uint32_t(readU32(image, base + 86));
        std::copy(image.begin() + base + 90, image.begin() + base + 122,
                  entry.token.begin());
        entry.src_view_region = uint32_t(readU32(image, base + 122));
        entry.src_view_ordinal = uint32_t(readU32(image, base + 126));
        entry.dst_view_region = uint32_t(readU32(image, base + 130));
        entry.dst_view_ordinal = uint32_t(readU32(image, base + 134));
        size_t ref_base = base + 138;
        for (uint32_t ref = 0; ref < kRefsPerObject; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++) {
                entry.wait_refs[ref][field] = uint32_t(readU32(image, ref_base));
                ref_base += 4;
            }
        for (uint32_t ref = 0; ref < kRefsPerObject; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++) {
                entry.signal_refs[ref][field] =
                    uint32_t(readU32(image, ref_base));
                ref_base += 4;
            }
        for (uint32_t ref = 0; ref < kRefsPerObject; ref++) {
            bool unused = true;
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++) {
                entry.view_refs[ref][field] = uint32_t(readU32(image,
                                                               ref_base));
                ref_base += 4;
                unused = unused && entry.view_refs[ref][field] == 0xFFFFFFFF;
            }
            if (!unused)
                entry.view_count = ref + 1;
        }
        graph.add(entry);
    }
    graph.setFillMode(readU32(image, 24));
    graph.setProgramDigest(image.data() + 32);
    size_t scratch_base = kHeaderBytes + count * kRecordBytes;
    for (uint64_t index = 0; index < scratch_count; index++) {
        MoeScratchInterval interval;
        interval.region_id = uint32_t(readU32(image, scratch_base));
        interval.ordinal = uint32_t(readU32(image, scratch_base + 4));
        interval.offset = readU64(image, scratch_base + 8);
        interval.bytes = readU64(image, scratch_base + 16);
        graph.addScratch(interval);
        scratch_base += kScratchEntryBytes;
    }
    size_t payload_base = scratch_base;
    for (uint64_t index = 0; index < payload_count; index++) {
        MoeFillPayload payload;
        payload.region_id = uint32_t(readU32(image, payload_base));
        payload.ordinal = uint32_t(readU32(image, payload_base + 4));
        payload.offset = readU64(image, payload_base + 8);
        payload.bytes = readU64(image, payload_base + 16);
        if (payload.offset + payload.bytes > payload_bytes) {
            error = "overlay payload escapes its blob";
            return false;
        }
        graph.addPayload(payload);
        payload_base += kPayloadEntryBytes;
    }
    if (payload_bytes > 0)
        graph.addPayloadBytes(image.data() + payload_base, payload_bytes);
    return true;
}

} // namespace ai_mesh
} // namespace gem5

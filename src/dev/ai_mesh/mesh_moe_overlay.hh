#ifndef DEV_AI_MESH_MESH_MOE_OVERLAY_HH
#define DEV_AI_MESH_MESH_MOE_OVERLAY_HH

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

// Runtime projection of one materialized overlay object.  The layout is
// kind-agnostic on purpose: every field of every object family is present
// and unused entries carry the frozen sentinel, mirroring the canonical
// Python projection one field at a time.
struct MoeOverlayEntry
{
    static constexpr uint16_t kSentinel16 = 0xFFFF;
    uint16_t kind = 0;
    uint32_t region_id = 0;
    uint32_t ordinal = 0;
    uint32_t owner_core = 0;
    uint32_t secondary_kind = 0;
    uint32_t expert_id = kSentinel16;
    uint32_t src_core = kSentinel16;
    uint32_t dst_core = kSentinel16;
    uint32_t chunk_ordinal = 0;
    uint32_t phase = 0;
    uint32_t role = 0;
    uint32_t access = 0;
    uint32_t bytes = 0;
    uint32_t alignment = 0;
    uint32_t offset = 0;
    uint32_t ref_region = 0;
    uint32_t ref_ordinal = 0;
    uint32_t src_view_region = 0;
    uint32_t src_view_ordinal = 0;
    uint32_t dst_view_region = 0;
    uint32_t dst_view_ordinal = 0;
    uint32_t backing_kind = 0;
    uint32_t semantic_owner_kind = 0;
    uint32_t semantic_owner_ref0 = 0;
    uint32_t validity_extent = 0;
    std::array<uint8_t, 32> token{};
    static constexpr uint32_t kMaxRefs = 32;
    uint32_t wait_count = 0;
    uint32_t signal_count = 0;
    uint32_t view_count = 0;
    // Referenced overlay objects as (region_id, kind, ordinal) triples; the
    // ordinal is only unique inside a kind, so the kind is part of the
    // identity.  Unused slots carry the sentinel and are ignored.
    static constexpr uint32_t kRefFields = 3;
    uint32_t wait_refs[kMaxRefs][kRefFields] = {
        {0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF},
    };
    uint32_t signal_refs[kMaxRefs][kRefFields] = {
        {0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF},
    };
    // Overlay views this object reads or writes: DMA descriptors name their
    // endpoints through view refs and compute commands see every operand row
    // through the same identity, so the runtime charges real SRAM service
    // instead of an opcode-shaped estimate.
    uint32_t view_refs[kMaxRefs][kRefFields] = {
        {0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF},
    };

    // Canonical key of the object per contract 7.2.3.  Only the fields the
    // contract names for this kind take part; the rest are ignored.
    std::vector<uint64_t> canonicalKey() const;

  private:
    void appendTokenWords(std::vector<uint64_t> &key) const;
};

inline void MoeOverlayEntry::appendTokenWords(
    std::vector<uint64_t> &key) const
{
    for (int word = 0; word < 4; word++) {
        uint64_t value = 0;
        for (int i = 0; i < 8; i++)
            value |= uint64_t(token[word * 8 + i]) << (8 * i);
        key.push_back(value);
    }
}

// Absolute SRAM interval of one overlay allocation ordinal.
struct MoeScratchInterval
{
    uint32_t region_id = 0;
    uint32_t ordinal = 0;
    uint64_t offset = 0;
    uint64_t bytes = 0;
};

// Functional bytes one fill descriptor installs.  The runtime never invents
// fill content: the materializer ships the exact contract row and the engine
// either writes it (FUNCTIONAL_BYTES) or only digests it (the other modes).
struct MoeFillPayload
{
    uint32_t region_id = 0;
    uint32_t ordinal = 0;
    uint64_t offset = 0;
    uint64_t bytes = 0;
};

class MoeOverlayGraph
{
  public:
    void add(const MoeOverlayEntry &entry) { entries.push_back(entry); }

    void addPayload(const MoeFillPayload &payload)
    {
        payload_entries.push_back(payload);
    }

    void addPayloadBytes(const uint8_t *data, uint64_t bytes)
    {
        payload_blob.insert(payload_blob.end(), data, data + bytes);
    }

    std::pair<const uint8_t *, uint64_t>
    resolvePayload(uint32_t region_id, uint32_t ordinal) const
    {
        for (const auto &payload : payload_entries)
            if (payload.region_id == region_id &&
                payload.ordinal == ordinal) {
                if (payload.offset + payload.bytes > payload_blob.size())
                    return {nullptr, 0};
                return {payload_blob.data() + payload.offset, payload.bytes};
            }
        return {nullptr, 0};
    }

    void setFillMode(uint32_t mode) { fill_mode_value = mode; }

    void setProgramDigest(const uint8_t *digest)
    {
        std::copy(digest, digest + 32, program_digest.begin());
    }

    const std::array<uint8_t, 32> &programDigest() const
    {
        return program_digest;
    }
    uint32_t fillMode() const { return fill_mode_value; }
    const std::vector<MoeFillPayload> &payloadEntries() const
    {
        return payload_entries;
    }
    const std::vector<uint8_t> &payloadBlob() const { return payload_blob; }

    const std::vector<MoeOverlayEntry> &objects() const { return entries; }

    void addScratch(const MoeScratchInterval &interval)
    {
        scratch_entries.push_back(interval);
    }

    const std::vector<MoeScratchInterval> &scratch() const
    {
        return scratch_entries;
    }

    // Absolute SRAM interval of an allocation, or nullptr when the ordinal is
    // not resident in this image (the region never executes here).
    const MoeScratchInterval *resolveAllocation(uint32_t region_id,
                                                uint32_t ordinal) const
    {
        for (const auto &interval : scratch_entries)
            if (interval.region_id == region_id && interval.ordinal == ordinal)
                return &interval;
        return nullptr;
    }

    // Canonical ordering and dense per-(region, kind) ordinals, mirroring
    // OverlayBuilder::finish() on the Python side.
    void assignOrdinals();

    // Structural checks: dense ordinals, unique canonical keys, valid
    // region labels and a cycle-free wait graph over event producers.
    bool validate(std::string &error) const;

    // Fixed-width little-endian encoding of the canonically ordered object
    // graph, hashed with SHA-256.  The Python projection writes the same
    // bytes in the same order, so the two digests must be equal.
    std::array<uint8_t, 32> digest() const;

    // Same bytes that feed `digest()`, exposed for cross-language debugging.
    std::vector<uint8_t> wireBytes() const;

  private:
    std::vector<MoeOverlayEntry> entries;
    std::vector<MoeScratchInterval> scratch_entries;
    std::vector<MoeFillPayload> payload_entries;
    std::vector<uint8_t> payload_blob;
    uint32_t fill_mode_value = 0;
    std::array<uint8_t, 32> program_digest{};
};

// Fixed-width overlay image (no JSON parser in the runtime): a 32 B header
// followed by one record per object.  A record is the canonical 122 B digest
// layout plus the explicit wait/signal references; the digest covers only the
// canonical part, so image round-trips cannot change object identity.
class MoeOverlayImage
{
  public:
    static constexpr uint32_t kMagic = 0x56454f4d; // "MOEV"
    static constexpr uint32_t kVersion = 4;
    static constexpr uint32_t kHeaderBytes = 64;
    static constexpr uint32_t kRefsPerObject = MoeOverlayEntry::kMaxRefs;
    // 122 B canonical layout + endpoint view refs + three reference lists
    // (waits, signals, the object's own views).
    static constexpr uint32_t kRecordBytes =
        138 + kRefsPerObject * MoeOverlayEntry::kRefFields * 4 * 3;
    static constexpr uint32_t kScratchEntryBytes = 24;
    static constexpr uint32_t kPayloadEntryBytes = 24;

    std::vector<uint8_t> encode(const MoeOverlayGraph &graph,
                                uint32_t layer_id) const;
    bool decode(const std::vector<uint8_t> &image, MoeOverlayGraph &graph,
                uint32_t &layer_id, std::string &error) const;
};

} // namespace ai_mesh
} // namespace gem5

#endif

#ifndef DEV_AI_MESH_MESH_WEIGHT_TAGS_HH
#define DEV_AI_MESH_MESH_WEIGHT_TAGS_HH

#include <array>
#include <cstdint>
#include <vector>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

struct DecodedProgram;

// WeightFillTagTupleV1 wire record (contract 7.2): the complete tuple, not
// its hash, is the weight identity.  Its canonical order is the unsigned
// lexicographic order of the 84 wire bytes, so encoding is the only
// comparison source; no memcmp over numeric fields.
struct WeightFillTagTupleV1
{
    static constexpr uint32_t kBytes = 84;
    std::array<uint8_t, 32> program_semantic_digest{};
    uint32_t weight_symbol_id = 0;
    uint64_t weight_region_offset = 0;
    uint64_t weight_bytes = 0;
    std::array<uint8_t, 32> resolved_content_digest{};
};

// Resolved weight region of one reachable symbol, as derived from the
// verified program weight registry (util/mesh_ir/mesh_ir/weight_registry.py).
struct WeightRegionBindingV1
{
    uint32_t weight_symbol_id = 0;
    uint64_t weight_region_offset = 0;
    uint64_t weight_bytes = 0;
    std::array<uint8_t, 32> resolved_content_digest{};
};

struct WeightTagEntry
{
    uint32_t weight_tag_index = 0;
    WeightFillTagTupleV1 tuple;
};

// One expert of one core: the exact tag and region the cache resolves it to.
// The tag index space is the program-wide manifest, so every core indexes the
// same table even though it only fills its own experts.
struct WeightTagSiteV1
{
    uint32_t weight_tag_index = 0;
    uint16_t expert_id = 0;
    uint16_t core_id = 0;
    uint32_t weight_symbol_id = 0;
    uint64_t weight_region_offset = 0;
    uint64_t weight_bytes = 0;
};

std::array<uint8_t, WeightFillTagTupleV1::kBytes> encodeWeightFillTagTuple(
    const WeightFillTagTupleV1 &tuple);

// Per-core canonical tag table: every reachable program's weight tuples are
// exact-byte deduplicated, sorted and given a dense zero-based index.
std::vector<WeightTagEntry> buildWeightTagManifest(
    const std::array<uint8_t, 32> &program_semantic_digest,
    const std::vector<WeightRegionBindingV1> &bindings);

std::vector<WeightTagSiteV1> weightTagSitesOf(const DecodedProgram &program,
                                              uint16_t core_id);

std::vector<uint8_t> weightTagManifestBytes(
    const std::vector<WeightTagEntry> &manifest);

std::array<uint8_t, 32> weightTagManifestDigest(
    const std::vector<WeightTagEntry> &manifest);

} // namespace ai_mesh
} // namespace gem5

#endif

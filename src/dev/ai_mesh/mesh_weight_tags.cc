#include "dev/ai_mesh/mesh_weight_tags.hh"

#include <algorithm>
#include <cstring>
#include <tuple>

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/mesh_binary.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

constexpr char kManifestMagic[] = "AI_MESH_WEIGHT_TAG_V1";

void appendU32(std::vector<uint8_t> &out, uint32_t value)
{
    for (int i = 0; i < 4; i++)
        out.push_back(uint8_t(value >> (8 * i)));
}

} // anonymous namespace

std::array<uint8_t, WeightFillTagTupleV1::kBytes> encodeWeightFillTagTuple(
    const WeightFillTagTupleV1 &tuple)
{
    std::array<uint8_t, WeightFillTagTupleV1::kBytes> out{};
    std::memcpy(out.data(), tuple.program_semantic_digest.data(), 32);
    for (int i = 0; i < 4; i++)
        out[32 + i] = uint8_t(tuple.weight_symbol_id >> (8 * i));
    for (int i = 0; i < 8; i++)
        out[36 + i] = uint8_t(tuple.weight_region_offset >> (8 * i));
    for (int i = 0; i < 8; i++)
        out[44 + i] = uint8_t(tuple.weight_bytes >> (8 * i));
    std::memcpy(out.data() + 52, tuple.resolved_content_digest.data(), 32);
    return out;
}

std::vector<WeightTagEntry> buildWeightTagManifest(
    const std::array<uint8_t, 32> &program_semantic_digest,
    const std::vector<WeightRegionBindingV1> &bindings)
{
    std::vector<std::array<uint8_t, WeightFillTagTupleV1::kBytes>> encoded_set;
    encoded_set.reserve(bindings.size());
    for (const auto &binding : bindings) {
        WeightFillTagTupleV1 tuple;
        tuple.program_semantic_digest = program_semantic_digest;
        tuple.weight_symbol_id = binding.weight_symbol_id;
        tuple.weight_region_offset = binding.weight_region_offset;
        tuple.weight_bytes = binding.weight_bytes;
        tuple.resolved_content_digest = binding.resolved_content_digest;
        encoded_set.push_back(encodeWeightFillTagTuple(tuple));
    }
    std::sort(encoded_set.begin(), encoded_set.end());
    encoded_set.erase(std::unique(encoded_set.begin(), encoded_set.end()),
                      encoded_set.end());
    std::vector<WeightTagEntry> manifest;
    manifest.reserve(encoded_set.size());
    uint32_t index = 0;
    for (const auto &encoded : encoded_set) {
        WeightTagEntry entry;
        entry.weight_tag_index = index++;
        std::memcpy(entry.tuple.program_semantic_digest.data(),
                    encoded.data(), 32);
        entry.tuple.weight_symbol_id =
            uint32_t(encoded[32]) | (uint32_t(encoded[33]) << 8) |
            (uint32_t(encoded[34]) << 16) | (uint32_t(encoded[35]) << 24);
        entry.tuple.weight_region_offset = 0;
        entry.tuple.weight_bytes = 0;
        for (int i = 0; i < 8; i++) {
            entry.tuple.weight_region_offset |=
                uint64_t(encoded[36 + i]) << (8 * i);
            entry.tuple.weight_bytes |= uint64_t(encoded[44 + i]) << (8 * i);
        }
        std::memcpy(entry.tuple.resolved_content_digest.data(),
                    encoded.data() + 52, 32);
        manifest.push_back(entry);
    }
    return manifest;
}

std::vector<uint8_t> weightTagManifestBytes(
    const std::vector<WeightTagEntry> &manifest)
{
    std::vector<uint8_t> out;
    out.insert(out.end(), kManifestMagic,
               kManifestMagic + sizeof(kManifestMagic) - 1);
    out.push_back(0);
    appendU32(out, uint32_t(manifest.size()));
    for (const auto &entry : manifest) {
        appendU32(out, entry.weight_tag_index);
        const auto encoded = encodeWeightFillTagTuple(entry.tuple);
        out.insert(out.end(), encoded.begin(), encoded.end());
    }
    return out;
}

std::array<uint8_t, 32> weightTagManifestDigest(
    const std::vector<WeightTagEntry> &manifest)
{
    return agentSha256(weightTagManifestBytes(manifest));
}

std::vector<WeightTagSiteV1> weightTagSitesOf(const DecodedProgram &program,
                                              uint16_t core_id)
{
    std::vector<WeightTagSiteV1> sites;
    for (const auto &expert : program.moe_expert_specs) {
        WeightTagSiteV1 site;
        site.expert_id = expert.expert_id;
        site.core_id = expert.core_id;
        site.weight_symbol_id = expert.weight_symbol_id;
        site.weight_region_offset = expert.weight_region_offset;
        site.weight_bytes = expert.weight_bytes;
        sites.push_back(site);
    }
    std::sort(sites.begin(), sites.end(),
              [](const WeightTagSiteV1 &left, const WeightTagSiteV1 &right) {
                  return std::make_tuple(left.weight_symbol_id,
                                         left.weight_region_offset,
                                         left.weight_bytes,
                                         left.core_id, left.expert_id) <
                         std::make_tuple(right.weight_symbol_id,
                                         right.weight_region_offset,
                                         right.weight_bytes,
                                         right.core_id, right.expert_id);
              });
    std::vector<std::tuple<uint32_t, uint64_t, uint64_t>> order;
    for (const auto &site : sites) {
        const auto region = std::make_tuple(site.weight_symbol_id,
                                            site.weight_region_offset,
                                            site.weight_bytes);
        if (order.empty() || order.back() != region)
            order.push_back(region);
    }
    std::vector<WeightTagSiteV1> unique;
    for (const auto &site : sites) {
        const auto region = std::make_tuple(site.weight_symbol_id,
                                            site.weight_region_offset,
                                            site.weight_bytes);
        if (!unique.empty() && unique.back().core_id == site.core_id &&
            std::make_tuple(unique.back().weight_symbol_id,
                            unique.back().weight_region_offset,
                            unique.back().weight_bytes) == region)
            continue;
        WeightTagSiteV1 entry = site;
        entry.weight_tag_index = uint32_t(
            std::lower_bound(order.begin(), order.end(), region) -
            order.begin());
        unique.push_back(entry);
    }
    std::vector<WeightTagSiteV1> local;
    for (const auto &site : unique)
        if (site.core_id == core_id)
            local.push_back(site);
    return local;
}

} // namespace ai_mesh
} // namespace gem5

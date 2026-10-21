#include "dev/ai_mesh/mesh_cache_replay.hh"

#include <stdexcept>
#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/mesh_json.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/mesh_weight_tags.hh"
#include "dev/ai_mesh/mesh_invocation_binding.hh"

namespace gem5::ai_mesh
{
namespace
{
void exact(const boost::json::object &object, std::initializer_list<const char *> keys)
{
    if (object.size() != keys.size() || std::any_of(keys.begin(), keys.end(),
            [&](const char *key) { return !object.contains(key); }))
        throw std::invalid_argument("cache image has missing or unknown fields");
}

uint64_t number(const boost::json::object &object, const char *key, uint64_t limit)
{
    const auto &value = object.at(key);
    if (value.is_string() && limit == UINT64_MAX) {
        const auto &text = value.as_string();
        if (text.size() != 18 || text[0] != '0' || text[1] != 'x' ||
                !std::all_of(text.begin() + 2, text.end(), [](char ch) {
                    return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f');
                }))
            throw std::invalid_argument("cache u64-json is not canonical");
        const uint64_t result = std::stoull(std::string(text.data() + 2, 16), nullptr, 16);
        if (result <= (uint64_t(1) << 53) - 1)
            throw std::invalid_argument("small cache u64-json must be an integer");
        return result;
    }
    if ((!value.is_uint64() && !value.is_int64()) ||
            (value.is_int64() && value.as_int64() < 0))
        throw std::invalid_argument("cache image integer has invalid type or sign");
    const uint64_t result = value.is_uint64() ? value.as_uint64() : value.as_int64();
    if (result > limit || (limit == UINT64_MAX && result > (uint64_t(1) << 53) - 1))
        throw std::invalid_argument("cache image integer exceeds its width");
    return result;
}

std::vector<uint8_t> bytes(const boost::json::value &value)
{
    const auto &text = value.as_string();
    if (text.size() % 2)
        throw std::invalid_argument("cache image hex has odd length");
    std::vector<uint8_t> out;
    out.reserve(text.size() / 2);
    uint8_t byte = 0;
    for (size_t i = 0; i < text.size(); ++i) {
        const char ch = text[i];
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f')))
            throw std::invalid_argument("cache image hex is not canonical");
        byte = (byte << 4) | (ch <= '9' ? ch - '0' : ch - 'a' + 10);
        if (i % 2) out.push_back(byte);
    }
    return out;
}

bool digestEquals(const boost::json::value &value, const std::array<uint8_t, 32> &digest)
{
    const auto raw = bytes(value);
    return raw.size() == digest.size() && std::equal(raw.begin(), raw.end(), digest.begin());
}
}

std::optional<CacheStartupImage>
CacheStartupImage::prepare(const std::string &image, const std::string &replay,
    const MeshProgramAdmission &admission,
    const std::string &effective_digest, const std::string &image_digest,
    const std::map<uint16_t, MoeWeightCache::Config> &configs, std::string &reason)
{
    const auto &program = admission.program();
    const auto &arch = admission.arch();
    CacheStartupImage result;
    try {
        const std::set<uint32_t> physical_cores(arch.core_ids.begin(), arch.core_ids.end());
        if (physical_cores.size() != arch.core_ids.size() || configs.size() != physical_cores.size())
            throw std::invalid_argument("cache owners do not cover the physical architecture");
        for (const auto &[core, config] : configs)
            if (!physical_cores.count(core) || config.core_id != core)
                throw std::invalid_argument("cache owner identity differs from the architecture");
        for (const auto &[core, config] : configs)
            result.caches.emplace(core, std::make_unique<MoeWeightCache>(config));
        if (image.empty()) {
            if (!replay.empty()) throw std::invalid_argument("warm replay requires a verified weight image");
            return result;
        }
        auto document = parseMeshJson(image, reason);
        if (!document) return std::nullopt;
        const auto &root = document->as_object();
        exact(root, {"program_semantic_digest", "effective_architecture_digest", "model_weight_image_digest",
                     "program_weight_registry_digest", "weight_tag_manifest_digest", "symbols", "reservation_token_bound"});
        if (!digestEquals(root.at("program_semantic_digest"), (program).metadata.semantic_sha256) ||
                root.at("effective_architecture_digest").as_string() != effective_digest ||
                root.at("model_weight_image_digest").as_string() != image_digest)
            throw std::invalid_argument("weight installation identity mismatch");
        for (const auto key : {"effective_architecture_digest", "model_weight_image_digest",
                              "program_weight_registry_digest", "weight_tag_manifest_digest"})
            if (bytes(root.at(key)).size() != 32)
                throw std::invalid_argument("weight installation digest has invalid width");
        const uint32_t token_bound = number(root, "reservation_token_bound", UINT32_MAX - 1);
        std::set<uint32_t> expected;
        for (const auto &requirement : program.transport.agent_request_binding_requirements)
            if (requirement.binding_kind == agent_abi::kBindingKindWEIGHT_EXTERNAL)
                expected.insert(requirement.symbol_id);
        std::map<uint32_t, MemoryImageSpan> symbols;
        uint32_t previous = 0;
        for (const auto &entry : root.at("symbols").as_array()) {
            const auto &row = entry.as_object();
            exact(row, {"symbol_id", "address", "data_hex"});
            const uint32_t symbol = number(row, "symbol_id", UINT32_MAX);
            if (symbol <= previous || !expected.erase(symbol))
                throw std::invalid_argument("weight symbol coverage or ordering mismatch");
            previous = symbol;
            MemoryImageSpan memory{number(row, "address", UINT64_MAX), bytes(row.at("data_hex"))};
            std::set<std::pair<uint64_t, uint64_t>> ranges;
            for (const auto &[variant_id, variant] : admission.context().variants()) {
                const auto source = admission.context().symbolBinding(
                    variant->entrypoint_id, variant->profile_id, symbol);
                if (!source)
                    continue;
                MeshInvocationBinding binding;
                MeshLoadError error;
                if (!resolveInvocationBinding(admission, variant_id, {}, binding, error))
                    throw std::invalid_argument(error.code + ": " + error.message);
                const auto *resolved = binding.binding(source->slot->slot_id);
                if (!resolved || source->tensor->role != mesh_abi::kTensorRoleWEIGHT ||
                        resolved->access != mesh_abi::kAccessKindREAD_ONLY)
                    throw std::invalid_argument("weight symbol has no admitted read-only binding");
                ranges.emplace(resolved->allocation_address, resolved->allocation_size_bytes);
            }
            if (ranges.size() != 1)
                throw std::invalid_argument("weight symbol has no unique admitted range");
            const auto [address, span] = *ranges.begin();
            if (memory.data.empty() || memory.data.size() != span ||
                    memory.address != address || memory.address > UINT64_MAX - span)
                throw std::invalid_argument("weight bytes or address differ from installed bindings");
            for (const auto &other : result.weights)
                if (memory.address < other.address + other.data.size() &&
                        other.address < memory.address + span)
                    throw std::invalid_argument("weight source ranges overlap");
            symbols.emplace(symbol, memory);
            result.weights.push_back(std::move(memory));
        }
        if (!expected.empty()) throw std::invalid_argument("weight symbols are missing");
        std::map<uint32_t, std::vector<uint8_t>> tag_data;
        std::map<uint32_t, WeightTagEntry> manifest_rows;
        for (const auto &[core, config] : configs)
            for (const auto &site : weightTagSitesOf(program, core)) {
                const auto &source = symbols.at(site.weight_symbol_id).data;
                if (!site.weight_bytes || site.weight_region_offset > source.size() ||
                        site.weight_bytes > source.size() - site.weight_region_offset)
                    throw std::invalid_argument("weight tag exceeds image content");
                std::vector<uint8_t> data(source.begin() + site.weight_region_offset,
                    source.begin() + site.weight_region_offset + site.weight_bytes);
                tag_data[site.weight_tag_index] = data;
                manifest_rows[site.weight_tag_index] = {site.weight_tag_index,
                    {(program).metadata.semantic_sha256, site.weight_symbol_id, site.weight_region_offset,
                     site.weight_bytes, agentSha256(data)}};
            }
        std::vector<WeightTagEntry> manifest;
        for (const auto &[index, entry] : manifest_rows) {
            if (index != manifest.size()) throw std::invalid_argument("weight tags are not dense");
            manifest.push_back(entry);
        }
        if (!digestEquals(root.at("weight_tag_manifest_digest"), weightTagManifestDigest(manifest)))
            throw std::invalid_argument("weight content or tag manifest digest mismatch");
        if (replay.empty()) return result;
        auto replay_document = parseMeshJson(replay, reason);
        if (!replay_document) return std::nullopt;
        const auto &replay_root = replay_document->as_object();
        exact(replay_root, {"schema", "version", "effective_architecture_digest", "model_weight_image_digest",
                           "program_weight_registry_digest", "weight_tag_manifest_digest", "cores"});
        if (replay_root.at("schema").as_string() != "cache_state_replay_v1" || number(replay_root, "version", 1) != 1)
            throw std::invalid_argument("cache replay schema or version mismatch");
        for (const auto key : {"effective_architecture_digest", "model_weight_image_digest",
                              "program_weight_registry_digest", "weight_tag_manifest_digest"})
            if (replay_root.at(key) != root.at(key))
                throw std::invalid_argument("cache replay digest mismatch");
        const auto &cores = replay_root.at("cores").as_array();
        if (cores.size() != configs.size()) throw std::invalid_argument("cache replay core coverage mismatch");
        auto owner = configs.begin();
        for (const auto &entry : cores) {
            const auto &row = entry.as_object();
            exact(row, {"core_id", "cache_generation", "next_fill_incarnation", "slots", "failure_tombstones"});
            MoeWeightCache::ReplayState state;
            state.core_id = number(row, "core_id", UINT16_MAX);
            if (state.core_id != owner->first) throw std::invalid_argument("cache replay core order mismatch");
            state.cache_generation = number(row, "cache_generation", UINT32_MAX);
            state.next_fill_incarnation = number(row, "next_fill_incarnation", UINT32_MAX);
            if (state.next_fill_incarnation > UINT32_MAX - token_bound)
                throw std::invalid_argument("warm cache identities cannot cover the run");
            auto &cache = *result.caches.at(owner->first);
            const auto &slots = row.at("slots").as_array();
            if (slots.size() != owner->second.slot_count)
                throw std::invalid_argument("cache replay slot coverage mismatch");
            for (const auto &value : slots) {
                const auto &slot = value.as_object();
                exact(slot, {"slot_id", "state", "weight_tag_index", "valid_bytes", "lru_rank"});
                const uint32_t id = number(slot, "slot_id", UINT32_MAX);
                if (id != state.slots.size()) throw std::invalid_argument("cache replay slot order mismatch");
                MoeWeightCache::ReplaySlot target;
                target.valid_bytes = number(slot, "valid_bytes", UINT64_MAX);
                if (number(slot, "state", 1)) {
                    target.weight_tag_index = number(slot, "weight_tag_index", UINT32_MAX);
                    target.lru_rank = number(slot, "lru_rank", UINT32_MAX);
                    const auto found = tag_data.find(*target.weight_tag_index);
                    if (found == tag_data.end() || found->second.size() != target.valid_bytes)
                        throw std::invalid_argument("cache replay bytes differ from tag content");
                    result.slots[state.core_id].push_back({cache.slotBase(id), found->second});
                } else if (!slot.at("weight_tag_index").is_null() ||
                           !slot.at("lru_rank").is_null() || target.valid_bytes)
                    throw std::invalid_argument("invalid cache slot carries live metadata");
                state.slots.push_back(target);
            }
            std::optional<uint32_t> prior_tag;
            for (const auto &value : row.at("failure_tombstones").as_array()) {
                const auto &failure = value.as_object();
                exact(failure, {"weight_tag_index", "first_error"});
                const uint32_t tag = number(failure, "weight_tag_index", UINT32_MAX);
                if (prior_tag && tag <= *prior_tag) throw std::invalid_argument("cache failure order mismatch");
                prior_tag = tag;
                const auto &error = failure.at("first_error").as_object();
                exact(error, {"tick", "error_code", "source_key_wire"});
                ErrorCandidate candidate;
                candidate.tick = number(error, "tick", UINT64_MAX);
                candidate.code = number(error, "error_code", UINT32_MAX);
                const auto wire = bytes(error.at("source_key_wire"));
                mesh_abi::AbiError decode_error;
                if (wire.size() != mesh_abi::kErrorSourceKeysBytes ||
                        !mesh_abi::decodeErrorSourceKey(wire.data(), candidate.source, decode_error))
                    throw std::invalid_argument("cache failure source wire invalid");
                state.failure_tombstones.emplace(tag, candidate);
            }
            if (!cache.installReplay(state, reason)) return std::nullopt;
            ++owner;
        }
        reason.clear();
        return result;
    } catch (const std::exception &error) {
        reason = error.what();
        return std::nullopt;
    }
}
}

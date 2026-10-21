#include "dev/ai_mesh/mesh_moe_population.hh"

#include <algorithm>

#include "dev/ai_mesh/mesh_moe_rng.hh"

namespace gem5::ai_mesh
{

bool MoeSemanticToken::valid() const
{
    return (phase == 1 && sequence == 0) || (phase == 2 && token == 0) ||
           (phase == 3 && sequence == 0 && token == 0);
}

std::array<uint8_t, 32> MoeSemanticToken::encode() const
{
    std::array<uint8_t, 32> bytes{};
    const uint32_t values[] = {item, user, task, sequence, token};
    const uint32_t offsets[] = {0, 8, 12, 20, 24};
    for (size_t field = 0; field < 5; ++field)
        for (size_t byte = 0; byte < 4; ++byte)
            bytes[offsets[field] + byte] = values[field] >> (8 * byte);
    bytes[16] = repair;
    bytes[17] = repair >> 8;
    bytes[18] = phase;
    return bytes;
}

bool MoePopulation::freezeUniform(const mesh_abi::MoeLayerSpec &layer,
    std::vector<MoePopulationToken> tokens,
    const std::array<uint8_t, 32> &workload_digest, uint64_t seed, std::string &error)
{
    return freeze(layer, std::move(tokens), [&](const auto &population) {
        std::vector<uint32_t> selected;
        for (const auto &token : population) {
            MoeRngKey key;
            key.master_seed = seed;
            key.workload_plan_digest = workload_digest;
            key.user_id = token.uid.user;
            key.task_seq = token.uid.task;
            key.repair_round = token.uid.repair;
            key.phase = token.uid.phase;
            key.sequence_ordinal = token.uid.sequence;
            key.token_ordinal = token.uid.token;
            key.layer_id = layer.layer_id;
            key.logical_source_rank = token.source_rank;
            const auto experts = moeRngWithoutReplacement(layer.expert_count, layer.top_k, key);
            selected.insert(selected.end(), experts.begin(), experts.end());
        }
        return selected;
    }, error);
}

bool MoePopulation::freeze(const mesh_abi::MoeLayerSpec &layer,
    std::vector<MoePopulationToken> tokens, const Selector &select, std::string &error)
{
    *this = {};
    const auto reject = [&](const char *code) {
        error = code;
        return false;
    };
    if (!layer.expert_count || !layer.top_k || layer.top_k > layer.expert_count ||
            !layer.capacity_factor_q16 ||
            layer.overflow_policy > mesh_abi::kMoeOverflowPolicyFAIL ||
            tokens.size() > layer.max_tokens_per_frozen_batch ||
            tokens.size() > layer.max_routes / layer.top_k)
        return reject("E_CAPACITY_PLAN");
    std::sort(tokens.begin(), tokens.end(), [](const auto &left, const auto &right) {
        return left.uid < right.uid;
    });
    for (size_t i = 0; i < tokens.size(); ++i) {
        if (!tokens[i].uid.valid() || tokens[i].uid.phase == 3 ||
                (i && !(tokens[i - 1].uid < tokens[i].uid)))
            return reject("E_MOE_UID");
    }
    const uint64_t routes = uint64_t(tokens.size()) * layer.top_k;
    if (routes > UINT64_MAX / layer.capacity_factor_q16)
        return reject("E_CAPACITY_PLAN");
    const uint64_t numerator = routes * layer.capacity_factor_q16;
    const uint64_t denominator = uint64_t(layer.expert_count) * 65536;
    const uint64_t capacity = numerator / denominator + (numerator % denominator != 0);
    MoePopulation candidate;
    candidate.expert_capacity = capacity;
    candidate.expert_loads.resize(layer.expert_count);
    candidate.expert_padding.resize(layer.expert_count);
    candidate.selected.reserve(routes);
    const auto experts = select(tokens);
    if (experts.size() != routes)
        return reject("E_MOE_PROVIDER");
    size_t selected_index = 0;
    for (const auto &token : tokens) {
        std::vector<bool> seen(layer.expert_count, false);
        for (uint16_t slot = 0; slot < layer.top_k; ++slot) {
            const auto expert = experts[selected_index++];
            if (expert >= layer.expert_count || seen[expert])
                return reject("E_MOE_PROVIDER");
            seen[expert] = true;
            const bool accepted = candidate.expert_loads[expert] < capacity;
            if (!accepted && layer.overflow_policy == mesh_abi::kMoeOverflowPolicyFAIL)
                return reject("E_MOE_CAPACITY");
            candidate.expert_loads[expert] += accepted;
            candidate.selected.push_back({token, slot, static_cast<uint16_t>(expert), accepted});
        }
    }
    if (layer.overflow_policy == mesh_abi::kMoeOverflowPolicyPAD_TO_CAPACITY) {
        if (capacity > UINT32_MAX)
            return reject("E_CAPACITY_PLAN");
        for (size_t i = 0; i < layer.expert_count; ++i)
            candidate.expert_padding[i] = capacity - candidate.expert_loads[i];
    }
    *this = std::move(candidate);
    error.clear();
    return true;
}

}

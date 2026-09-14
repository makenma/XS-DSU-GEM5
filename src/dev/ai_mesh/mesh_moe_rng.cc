#include "dev/ai_mesh/mesh_moe_rng.hh"

#include <cstring>

#include "dev/ai_mesh/agent_sha256.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

void putU16(std::array<uint8_t, MoeRngKey::kBytes> &out, size_t offset,
            uint16_t value)
{
    out[offset] = uint8_t(value);
    out[offset + 1] = uint8_t(value >> 8);
}

void putU32(std::array<uint8_t, MoeRngKey::kBytes> &out, size_t offset,
            uint32_t value)
{
    for (int i = 0; i < 4; i++)
        out[offset + i] = uint8_t(value >> (8 * i));
}

void putU64(std::array<uint8_t, MoeRngKey::kBytes> &out, size_t offset,
            uint64_t value)
{
    for (int i = 0; i < 8; i++)
        out[offset + i] = uint8_t(value >> (8 * i));
}

} // anonymous namespace

std::array<uint8_t, MoeRngKey::kBytes> MoeRngKey::encode() const
{
    std::array<uint8_t, kBytes> out{};
    putU32(out, 0, rng_schema_version);
    putU32(out, 4, 0);
    putU64(out, 8, master_seed);
    std::memcpy(out.data() + 16, workload_plan_digest.data(), 32);
    putU32(out, 48, user_id);
    putU32(out, 52, task_seq);
    putU16(out, 56, repair_round);
    out[58] = phase;
    out[59] = 0;
    putU32(out, 60, sequence_ordinal);
    putU32(out, 64, token_ordinal);
    putU32(out, 68, layer_id);
    putU32(out, 72, logical_source_rank);
    putU16(out, 76, topk_slot);
    putU16(out, 78, draw_id);
    return out;
}

uint64_t splitmix64Once(uint64_t seed64)
{
    constexpr uint64_t gamma = 0x9E3779B97F4A7C15ull;
    constexpr uint64_t mul1 = 0xBF58476D1CE4E5B9ull;
    constexpr uint64_t mul2 = 0x94D049BB133111EBull;
    uint64_t z = seed64 + gamma;
    z = (z ^ (z >> 30)) * mul1;
    z = (z ^ (z >> 27)) * mul2;
    return z ^ (z >> 31);
}

uint64_t moeRngSeed64(const MoeRngKey &key)
{
    const auto bytes = key.encode();
    const std::vector<uint8_t> payload(bytes.begin(), bytes.end());
    const auto digest = agentSha256(payload);
    uint64_t seed = 0;
    for (int i = 0; i < 8; i++)
        seed |= uint64_t(digest[i]) << (8 * i);
    return seed;
}

uint64_t moeRngDraw(const MoeRngKey &key)
{
    return splitmix64Once(moeRngSeed64(key));
}

uint64_t moeRngThreshold(uint64_t total, uint64_t r)
{
    const unsigned __int128 product =
        (unsigned __int128)r * (unsigned __int128)total;
    return uint64_t(product >> 64);
}

uint32_t moeRngCategorical(const std::vector<uint64_t> &weights,
                           const MoeRngKey &key)
{
    uint64_t total = 0;
    for (uint64_t weight : weights)
        total += weight;
    const uint64_t limit = moeRngThreshold(total, moeRngDraw(key));
    uint64_t cumulative = 0;
    for (size_t index = 0; index < weights.size(); index++) {
        cumulative += weights[index];
        if (cumulative > limit)
            return uint32_t(index);
    }
    return uint32_t(weights.size());
}

std::vector<uint32_t> moeRngWithoutReplacement(uint32_t expert_count,
                                               uint32_t top_k,
                                               const MoeRngKey &base)
{
    std::vector<uint32_t> remaining;
    remaining.reserve(expert_count);
    for (uint32_t index = 0; index < expert_count; index++)
        remaining.push_back(index);
    std::vector<uint32_t> selected;
    selected.reserve(top_k);
    for (uint32_t slot = 0; slot < top_k; slot++) {
        MoeRngKey key = base;
        key.topk_slot = uint16_t(slot);
        key.draw_id = 0;
        const uint64_t r = moeRngDraw(key);
        const uint64_t index =
            uint64_t(((unsigned __int128)r * remaining.size()) >> 64);
        selected.push_back(remaining[index]);
        remaining.erase(remaining.begin() + index);
    }
    return selected;
}

} // namespace ai_mesh
} // namespace gem5

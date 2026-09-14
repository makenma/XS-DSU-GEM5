#ifndef DEV_AI_MESH_MESH_MOE_RNG_HH
#define DEV_AI_MESH_MESH_MOE_RNG_HH

#include <array>
#include <cstdint>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

// Frozen keyed RNG of contract 7.5: 80 B key, SHA-256 seeding, one
// SplitMix64 round and a Q32 threshold sampler.  The same inputs must
// produce byte-identical draws in Python and C++; substituting a library
// PRNG, modulo sampling or an extra draw is a contract violation.
struct MoeRngKey
{
    static constexpr uint32_t kBytes = 80;
    static constexpr uint32_t kSchemaVersion = 1;
    uint32_t rng_schema_version = kSchemaVersion;
    uint64_t master_seed = 0;
    std::array<uint8_t, 32> workload_plan_digest{};
    uint32_t user_id = 0;
    uint32_t task_seq = 0;
    uint16_t repair_round = 0;
    uint8_t phase = 0;
    uint32_t sequence_ordinal = 0;
    uint32_t token_ordinal = 0;
    uint32_t layer_id = 0;
    uint32_t logical_source_rank = 0;
    uint16_t topk_slot = 0;
    uint16_t draw_id = 0;

    std::array<uint8_t, kBytes> encode() const;
};

uint64_t splitmix64Once(uint64_t seed64);

uint64_t moeRngSeed64(const MoeRngKey &key);

uint64_t moeRngDraw(const MoeRngKey &key);

uint64_t moeRngThreshold(uint64_t total, uint64_t r);

uint32_t moeRngCategorical(const std::vector<uint64_t> &weights,
                           const MoeRngKey &key);

// uniform_smoke without-replacement mapping: ascending remaining set, one
// draw per top-k slot, index = high64(r * remaining.size()).
std::vector<uint32_t> moeRngWithoutReplacement(uint32_t expert_count,
                                               uint32_t top_k,
                                               const MoeRngKey &base);

} // namespace ai_mesh
} // namespace gem5

#endif

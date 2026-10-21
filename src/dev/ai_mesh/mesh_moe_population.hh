#ifndef DEV_AI_MESH_MESH_MOE_POPULATION_HH
#define DEV_AI_MESH_MESH_MOE_POPULATION_HH

#include <array>
#include <cstdint>
#include <functional>
#include <string>
#include <tuple>
#include <vector>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5::ai_mesh
{

struct MoeSemanticToken
{
    uint32_t item = 0;
    uint32_t user = 0;
    uint32_t task = 0;
    uint16_t repair = 0;
    uint8_t phase = 0;
    uint32_t sequence = 0;
    uint32_t token = 0;

    bool valid() const;
    std::array<uint8_t, 32> encode() const;
    bool operator<(const MoeSemanticToken &other) const
    {
        return std::tie(item, user, task, repair, phase, sequence, token) <
            std::tie(other.item, other.user, other.task, other.repair,
                     other.phase, other.sequence, other.token);
    }
};

struct MoePopulationToken
{
    MoeSemanticToken uid;
    uint32_t member = 0;
    uint32_t source_rank = 0;
};

struct MoeSelectedRoute
{
    MoePopulationToken token;
    uint16_t slot = 0;
    uint16_t expert = 0;
    bool accepted = false;
};

class MoePopulation
{
  public:
    using Selector = std::function<std::vector<uint32_t>(const std::vector<MoePopulationToken> &)>;
    bool freeze(const mesh_abi::MoeLayerSpec &layer,
                std::vector<MoePopulationToken> tokens, const Selector &select,
                std::string &error);
    bool freezeUniform(const mesh_abi::MoeLayerSpec &layer,
                       std::vector<MoePopulationToken> tokens,
                       const std::array<uint8_t, 32> &workload_digest,
                       uint64_t seed, std::string &error);
    const std::vector<MoeSelectedRoute> &routes() const { return selected; }
    const std::vector<uint32_t> &loads() const { return expert_loads; }
    const std::vector<uint32_t> &padding() const { return expert_padding; }
    uint64_t expertCapacity() const { return expert_capacity; }

  private:
    std::vector<MoeSelectedRoute> selected;
    std::vector<uint32_t> expert_loads;
    std::vector<uint32_t> expert_padding;
    uint64_t expert_capacity = 0;
};

}
#endif

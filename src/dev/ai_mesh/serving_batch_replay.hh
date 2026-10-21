#ifndef DEV_AI_MESH_SERVING_BATCH_REPLAY_HH
#define DEV_AI_MESH_SERVING_BATCH_REPLAY_HH

#include <optional>
#include <string>
#include <tuple>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/mesh_binary.hh"

namespace gem5::ai_mesh
{
struct ServingReplayIdentity
{
    uint32_t item = 0;
    uint32_t user = 0;
    uint32_t task = 0;
    uint16_t repair = 0;
    auto key() const { return std::tie(item, user, task, repair); }
    bool operator<(const ServingReplayIdentity &other) const { return key() < other.key(); }
    bool operator==(const ServingReplayIdentity &other) const { return key() == other.key(); }
};

struct ServingReplayMember
{
    ServingReplayIdentity identity;
    uint32_t sequenceBegin = 0;
    uint32_t validTokens = 0;
    uint32_t logicalSourceRank = 0;
};

struct ServingReplayBatch
{
    uint64_t ordinal = 0;
    uint16_t phase = 0;
    uint32_t decodeIteration = 0;
    uint32_t profile = 0;
    std::vector<ServingReplayMember> members;
};

struct ServingBatchReplay
{
    std::vector<ServingReplayBatch> batches;
    std::string digest;
    static std::optional<ServingBatchReplay> parse(const std::string &json,
        const AgentPlanImage &plan, const DecodedProgram &program, std::string &reason);
};
}
#endif

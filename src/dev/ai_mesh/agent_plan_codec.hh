#ifndef DEV_AI_MESH_AGENT_PLAN_CODEC_HH
#define DEV_AI_MESH_AGENT_PLAN_CODEC_HH

#include <cstdint>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"

namespace gem5
{
namespace ai_mesh
{

struct PlanWireAddresses
{
    uint64_t inputBase = 0;
    uint64_t parameterBase = 0;
    uint64_t outputBase = 0;
    uint64_t metadataBase = 0;
};

std::vector<uint8_t>
buildPlanParameter(const AgentPlanRound &round, const AgentPlanTask &task,
                   uint32_t userId, const PlanWireAddresses &addresses,
                   const uint8_t workloadDigest[32],
                   uint32_t outputChunkBytes);

std::vector<uint8_t>
buildControlParameter(uint8_t commandKind, uint64_t targetRequestId,
                      uint64_t kvHandle, uint32_t generation);

uint16_t kvPolicyToSqFlags(uint8_t kvPolicy);

}
}
#endif

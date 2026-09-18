#ifndef DEV_AI_MESH_AGENT_PLAN_CODEC_HH
#define DEV_AI_MESH_AGENT_PLAN_CODEC_HH

#include <cstdint>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

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

uint64_t planParameterBytes(const AgentPlanRound &round,
                           size_t binding_count);

bool normalizeBindings(std::vector<agent_abi::BindingRecord> &bindings,
                       std::string &reason);

bool bindingTableRepresentable(size_t binding_count, size_t tail_bytes,
                               std::string &reason);

uint32_t parameterBlockBytes(const AgentPlanRound &round,
                             size_t binding_count);

std::vector<agent_abi::BindingRecord>
resolveHostBindings(const AgentHostBindingPlan &plan,
                    const PlanWireAddresses &addresses,
                    const AgentPlanRound &round,
                    uint64_t kvSessionSlotBytes);

std::vector<uint8_t>
buildPlanParameter(const AgentPlanRound &round, const AgentPlanTask &task,
                   uint32_t userId, const PlanWireAddresses &addresses,
                   const uint8_t workloadDigest[32],
                   uint32_t outputChunkBytes,
                   const std::vector<agent_abi::BindingRecord> &bindings = {});

std::vector<uint8_t>
buildControlParameter(uint8_t commandKind, uint64_t targetRequestId,
                      uint64_t kvHandle, uint32_t generation);

uint16_t kvPolicyToSqFlags(uint8_t kvPolicy);

}
}
#endif

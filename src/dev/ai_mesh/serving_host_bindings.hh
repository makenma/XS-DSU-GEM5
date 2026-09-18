#ifndef DEV_AI_MESH_SERVING_HOST_BINDINGS_HH
#define DEV_AI_MESH_SERVING_HOST_BINDINGS_HH

#include <cstdint>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

uint16_t exactBindingFlags(uint16_t kind);

enum class HostBindingVerdict
{
    Match,
    Absent,
    Mismatch,
    AliasMismatch,
};

HostBindingVerdict
verifyHostBindingRequest(
    const std::vector<agent_abi::BindingRecord> &bindings,
    const AgentHostBindingPlan &plan, uint64_t inputAddress,
    uint64_t inputBytes, uint64_t outputAddress, uint64_t outputBytes,
    uint64_t metadataAddress, uint64_t metadataBytes,
    uint64_t kvRegionAddress, uint64_t kvRegionBytes,
    uint64_t kvSessionSlotBytes, std::string &reason);

HostBindingVerdict
verifyHostBindingRequirements(
    const std::vector<agent_abi::BindingRecord> &bindings,
    const std::vector<mesh_abi::AgentRequestBindingRequirement> &requirements,
    uint64_t inputAddress, uint64_t inputBytes, uint64_t outputAddress,
    uint64_t outputBytes, uint64_t metadataAddress, uint64_t metadataBytes,
    uint64_t kvRegionAddress, uint64_t kvRegionBytes,
    uint64_t kvSessionSlotBytes, std::string &reason);

std::vector<agent_abi::BindingRecord>
expectedHostBindings(
    const std::vector<mesh_abi::AgentRequestBindingRequirement> &requirements,
    uint64_t inputAddress, uint64_t inputBytes, uint64_t outputAddress,
    uint64_t outputBytes, uint64_t kvSessionSlotBytes);

}
}
#endif

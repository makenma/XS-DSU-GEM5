#ifndef DEV_AI_MESH_NPU_SERVING_FRONTEND_KV_MAP_HH
#define DEV_AI_MESH_NPU_SERVING_FRONTEND_KV_MAP_HH

#include <optional>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"

namespace gem5
{
namespace ai_mesh
{

struct KvAdmissionMapping
{
    bool success = false;
    bool admitted = false;
    bool protocol_error = false;
    agent_abi::DetailCode detail = agent_abi::E_KV_STATE;
};

KvAdmissionMapping mapKvAdmissionOutcome(
    KvAdmissionOutcome outcome,
    std::optional<KvPromotionOutcome> promotion);

}
}

#endif

#include "dev/ai_mesh/npu_serving_frontend_kv_map.hh"

namespace gem5
{
namespace ai_mesh
{

KvAdmissionMapping
mapKvAdmissionOutcome(KvAdmissionOutcome outcome,
                      std::optional<KvPromotionOutcome> promotion)
{
    KvAdmissionMapping mapping;
    switch (outcome) {
      case KvAdmissionOutcome::Claimed:
        mapping.admitted = true;
        if (promotion && *promotion == KvPromotionOutcome::Pinned) {
            mapping.success = true;
            return mapping;
        }
        if (promotion && *promotion == KvPromotionOutcome::ReuseRequired) {
            mapping.detail = agent_abi::E_KV_REUSE_REQUIRED;
        } else if (promotion &&
                   *promotion == KvPromotionOutcome::TokenMismatch) {
            mapping.detail = agent_abi::E_KV_TOKEN_MISMATCH;
        }
        return mapping;
      case KvAdmissionOutcome::FlagCombination:
        mapping.detail = agent_abi::E_KV_FLAG_COMBINATION;
        return mapping;
      case KvAdmissionOutcome::SessionExists:
        mapping.detail = agent_abi::E_SESSION_EXISTS;
        return mapping;
      case KvAdmissionOutcome::SessionNotFound:
        mapping.detail = agent_abi::E_KV_SESSION_NOT_FOUND;
        return mapping;
      case KvAdmissionOutcome::StaleGeneration:
        mapping.detail = agent_abi::E_KV_STALE_GENERATION;
        return mapping;
      case KvAdmissionOutcome::ContractMismatch:
        mapping.detail = agent_abi::E_KV_CONTRACT_MISMATCH;
        return mapping;
      case KvAdmissionOutcome::KvState:
        mapping.detail = agent_abi::E_KV_STATE;
        return mapping;
      default:
        mapping.protocol_error = true;
        return mapping;
    }
}

}
}

#ifndef DEV_AI_MESH_MESH_ERROR_CANDIDATE_HH
#define DEV_AI_MESH_MESH_ERROR_CANDIDATE_HH

#include <tuple>
#include "dev/ai_mesh/mesh_checkpoint_fields.hh"
#include <algorithm>
#include <stdexcept>
#include <cstring>
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/runtime_key.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5::ai_mesh
{
struct RequestTerminalPosition
{
    GEM5_CHECKPOINT_AGGREGATE()
    uint64_t instance = 0;
    uint32_t phase = 0;
};

struct ErrorCandidate
{
    GEM5_CHECKPOINT_AGGREGATE()
    uint64_t tick = 0;
    mesh_abi::ErrorSourceKey source;
    uint32_t code = 0;
};

inline bool validErrorSource(const mesh_abi::ErrorSourceKey &s)
{
    using namespace mesh_abi;
    const bool aux_zero = std::all_of(s.aux_key.begin(), s.aux_key.end(),
        [](uint8_t value) { return value == 0; });
    if (s.reserved)
        return false;
    if (s.domain == kMeshObjectDomainSTATIC || s.domain == kMeshObjectDomainMOE_OVERLAY) {
        if (s.core_id_or_ffff == UINT16_MAX || !s.ordinal || !aux_zero ||
                (s.domain == kMeshObjectDomainSTATIC && (s.region_group_id || s.region_id)))
            return false;
        switch (s.error_class) {
          case kMoeErrorClassDMA_AXI: return s.object_kind == kErrorSourceObjectKindDESCRIPTOR;
          case kMoeErrorClassSRAM: return s.object_kind == kErrorSourceObjectKindDESCRIPTOR ||
              s.object_kind == kErrorSourceObjectKindCOMMAND || s.object_kind == kErrorSourceObjectKindALLOCATION;
          case kMoeErrorClassCOMMAND: return s.object_kind == kErrorSourceObjectKindCOMMAND;
          case kMoeErrorClassEVENT: return s.object_kind == kErrorSourceObjectKindEVENT;
          default: return false;
        }
    }
    if (s.domain == kMeshObjectDomainWEIGHT_FILL)
        return (s.error_class == kMoeErrorClassWEIGHT_FILL || s.error_class == kMoeErrorClassDMA_AXI ||
                s.error_class == kMoeErrorClassSRAM) &&
            s.object_kind == kErrorSourceObjectKindWEIGHT_FILL_OBLIGATION &&
            !s.region_group_id && !s.region_id && !s.ordinal && !s.generation &&
            s.core_id_or_ffff != UINT16_MAX && agent_abi::rdU16(s.aux_key.data()) == s.core_id_or_ffff &&
            agent_abi::rdU32(s.aux_key.data() + 12);
    if (s.domain == kMeshObjectDomainKV_RUNTIME)
        return (((s.error_class == kMoeErrorClassDMA_AXI || s.error_class == kMoeErrorClassSRAM) &&
                s.object_kind == kErrorSourceObjectKindDESCRIPTOR && s.ordinal && s.core_id_or_ffff != UINT16_MAX) ||
                (s.error_class == kMoeErrorClassINTERNAL && s.object_kind == kErrorSourceObjectKindKV_APPEND &&
                s.ordinal == 1 && s.core_id_or_ffff == UINT16_MAX)) &&
            agent_abi::rdU64(s.aux_key.data()) && agent_abi::rdU64(s.aux_key.data() + 8);
    if (s.core_id_or_ffff != UINT16_MAX || s.generation)
        return false;
    if (s.domain == kMeshObjectDomainCOORDINATOR) {
        if (s.error_class == kMoeErrorClassMATERIALIZE)
            return s.object_kind == kErrorSourceObjectKindMATERIALIZER_CHECK &&
                s.ordinal <= kMaterializerCheckSiteCACHE_BINDING && aux_zero;
        return s.error_class == kMoeErrorClassCACHE_RESERVATION &&
            s.object_kind == kErrorSourceObjectKindCACHE_RESERVATION && !s.region_id && s.ordinal &&
            agent_abi::rdU32(s.aux_key.data() + 12) == 0;
    }
    return s.domain == kMeshObjectDomainINTERNAL && s.error_class == kMoeErrorClassINTERNAL &&
        s.object_kind == kErrorSourceObjectKindINTERNAL_CHECK && !s.region_group_id && !s.region_id &&
        s.ordinal <= mesh_abi::kInternalCheckSiteLEDGER && aux_zero;
}

inline std::array<uint8_t, mesh_abi::kErrorSourceKeysBytes>
errorSourceKeyBytes(const mesh_abi::ErrorSourceKey &key)
{
    std::array<uint8_t, mesh_abi::kErrorSourceKeysBytes> out{};
    agent_abi::wrU16(out.data() + mesh_abi::kErrorSourceKeysErrorClassOffset,
           key.error_class);
    agent_abi::wrU16(out.data() + mesh_abi::kErrorSourceKeysCoreIdOrFfffOffset,
           key.core_id_or_ffff);
    out[mesh_abi::kErrorSourceKeysDomainOffset] = key.domain;
    out[mesh_abi::kErrorSourceKeysObjectKindOffset] = key.object_kind;
    agent_abi::wrU32(out.data() + mesh_abi::kErrorSourceKeysRegionGroupIdOffset,
           key.region_group_id);
    agent_abi::wrU32(out.data() + mesh_abi::kErrorSourceKeysRegionIdOffset,
           key.region_id);
    agent_abi::wrU32(out.data() + mesh_abi::kErrorSourceKeysOrdinalOffset, key.ordinal);
    agent_abi::wrU32(out.data() + mesh_abi::kErrorSourceKeysGenerationOffset,
           key.generation);
    std::memcpy(out.data() + mesh_abi::kErrorSourceKeysAuxKeyOffset,
                key.aux_key.data(), key.aux_key.size());
    return out;
}

inline mesh_abi::ErrorSourceKey runtimeErrorSource(uint16_t error_class,
    uint16_t core, const RuntimeObjectKey &object, uint32_t generation = 0)
{
    mesh_abi::ErrorSourceKey source;
    source.error_class = error_class;
    source.core_id_or_ffff = core;
    source.domain = static_cast<uint8_t>(object.domain);
    switch (object.kind) {
      case mesh_abi::MeshObjectKind::COMMAND: source.object_kind = mesh_abi::kErrorSourceObjectKindCOMMAND; break;
      case mesh_abi::MeshObjectKind::EVENT: source.object_kind = mesh_abi::kErrorSourceObjectKindEVENT; break;
      case mesh_abi::MeshObjectKind::DESCRIPTOR: source.object_kind = mesh_abi::kErrorSourceObjectKindDESCRIPTOR; break;
      case mesh_abi::MeshObjectKind::TRANSFER: source.object_kind = mesh_abi::kErrorSourceObjectKindTRANSFER; break;
      case mesh_abi::MeshObjectKind::ALLOCATION: source.object_kind = mesh_abi::kErrorSourceObjectKindALLOCATION; break;
      default: throw std::invalid_argument("runtime object has no error-source kind");
    }
    source.region_group_id = object.regionGroupId;
    source.region_id = object.regionId;
    source.ordinal = object.ordinal;
    source.generation = generation;
    return source;
}

inline bool errorCandidateLess(const ErrorCandidate &left,
                               const ErrorCandidate &right)
{
    const auto &a = left.source;
    const auto &b = right.source;
    return std::tie(left.tick, a.error_class, a.core_id_or_ffff, a.domain,
                    a.object_kind, a.region_group_id, a.region_id, a.ordinal,
                    a.generation, a.aux_key, left.code) <
           std::tie(right.tick, b.error_class, b.core_id_or_ffff, b.domain,
                    b.object_kind, b.region_group_id, b.region_id, b.ordinal,
                    b.generation, b.aux_key, right.code);
}
}
#endif

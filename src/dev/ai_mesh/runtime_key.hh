#ifndef DEV_AI_MESH_RUNTIME_KEY_HH
#define DEV_AI_MESH_RUNTIME_KEY_HH

#include <cstdint>

#include "dev/ai_mesh/agent_runtime_types.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

struct InstanceGenerationTag;
struct RepeatGenerationTag;

using InstanceGeneration = SequenceValue<InstanceGenerationTag>;
using RepeatGeneration = SequenceValue<RepeatGenerationTag>;

class MeshCoreId
{
  public:
    static constexpr uint16_t INVALID_VALUE = 0xffff;
    static constexpr uint16_t MAX_VALUE = 0xfffe;

    constexpr MeshCoreId() = default;
    constexpr explicit MeshCoreId(uint16_t value) : _value(value) {}

    static constexpr bool admissible(uint64_t value)
    {
        return value <= MAX_VALUE;
    }

    constexpr uint16_t value() const { return _value; }
    constexpr bool valid() const { return _value != INVALID_VALUE; }

    friend constexpr bool operator==(MeshCoreId lhs, MeshCoreId rhs)
    { return lhs._value == rhs._value; }
    friend constexpr bool operator!=(MeshCoreId lhs, MeshCoreId rhs)
    { return !(lhs == rhs); }
    friend constexpr bool operator<(MeshCoreId lhs, MeshCoreId rhs)
    { return lhs._value < rhs._value; }

  private:
    uint16_t _value = INVALID_VALUE;
};

struct RuntimeObjectKey
{
    InstanceGeneration instance;
    mesh_abi::MeshObjectDomain domain = mesh_abi::MeshObjectDomain::STATIC;
    uint32_t regionGroupId = 0;
    uint32_t regionId = 0;
    mesh_abi::MeshObjectKind kind = mesh_abi::MeshObjectKind::COMMAND;
    uint32_t ordinal = 0;

    friend constexpr bool operator==(const RuntimeObjectKey &lhs,
                                     const RuntimeObjectKey &rhs)
    {
        return lhs.instance == rhs.instance && lhs.domain == rhs.domain &&
               lhs.regionGroupId == rhs.regionGroupId &&
               lhs.regionId == rhs.regionId && lhs.kind == rhs.kind &&
               lhs.ordinal == rhs.ordinal;
    }
    friend constexpr bool operator!=(const RuntimeObjectKey &lhs,
                                     const RuntimeObjectKey &rhs)
    { return !(lhs == rhs); }
    friend constexpr bool operator<(const RuntimeObjectKey &lhs,
                                    const RuntimeObjectKey &rhs)
    {
        if (lhs.instance != rhs.instance)
            return lhs.instance < rhs.instance;
        if (lhs.domain != rhs.domain)
            return lhs.domain < rhs.domain;
        if (lhs.regionGroupId != rhs.regionGroupId)
            return lhs.regionGroupId < rhs.regionGroupId;
        if (lhs.regionId != rhs.regionId)
            return lhs.regionId < rhs.regionId;
        if (lhs.kind != rhs.kind)
            return lhs.kind < rhs.kind;
        return lhs.ordinal < rhs.ordinal;
    }
};

constexpr RuntimeObjectKey
staticProgramObject(InstanceGeneration instance, mesh_abi::MeshObjectKind kind,
                    uint32_t ordinal)
{
    RuntimeObjectKey key;
    key.instance = instance;
    key.kind = kind;
    key.ordinal = ordinal;
    return key;
}

constexpr RuntimeObjectKey
overlayObject(InstanceGeneration instance, uint16_t regionGroupId,
              uint16_t regionId, mesh_abi::MeshObjectKind kind,
              uint32_t ordinal)
{
    RuntimeObjectKey key;
    key.instance = instance;
    key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    key.regionGroupId = regionGroupId;
    key.regionId = regionId;
    key.kind = kind;
    key.ordinal = ordinal;
    return key;
}

}
}

#endif

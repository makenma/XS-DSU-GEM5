#ifndef DEV_AI_MESH_MESH_SERVING_CAPACITY_HH
#define DEV_AI_MESH_MESH_SERVING_CAPACITY_HH

#include "dev/ai_mesh/mesh_execution_view.hh"

namespace gem5::ai_mesh
{
struct ServingCapacityRequirement
{
    uint64_t batch_weight_binding_entries = 0;
    uint64_t instance_member_binding_entries = 0;
    uint64_t batch_interval_entries = 0;
};

struct ServingCacheCapacityRequirement
{
    uint64_t lines = 0;
    uint64_t occurrences = 0;
};

ServingCacheCapacityRequirement servingStrictCacheCapacity(const DecodedProgram &program,
    const ProfileExecutionView &view, uint16_t core);

bool servingCapacityFor(const DecodedProgram &program,
    const mesh_abi::AgentInstanceProfile &instance, ServingCapacityRequirement &out, MeshLoadError &error);

bool servingCapacityRequired(const DecodedProgram &program,
                             ServingCapacityRequirement &out, MeshLoadError &error);
bool servingCapacityPlan(const DecodedProgram &program,
                         const ServingCapacityRequirement &configured,
                         ServingCapacityRequirement &required, MeshLoadError &error);
}
#endif

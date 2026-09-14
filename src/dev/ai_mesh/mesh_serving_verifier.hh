#ifndef DEV_AI_MESH_MESH_SERVING_VERIFIER_HH
#define DEV_AI_MESH_MESH_SERVING_VERIFIER_HH

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{

struct ServingCapacityRequirement
{
    uint64_t batch_weight_binding_entries = 0;
    uint64_t instance_member_binding_entries = 0;
    uint64_t batch_interval_entries = 0;
};

// Semantic verification of the Agent serving V1 extension, mirroring
// util/mesh_ir/mesh_ir/abi/serving_verifier.py.  Programs without the
// feature bit must carry no serving table; programs with it must satisfy
// every cross-reference of contract 10.3.1.
bool verifyServingV1(const DecodedProgram &program, const RuntimeArch &arch,
                     MeshLoadError &error);

// Static per-context CapacityPlan requirement derived from the serving
// tables (main contract 10.3.1, capacity table row for the three
// *_per_context entries).  Mirrors util/mesh_ir/mesh_ir/serving_profiles.py.
bool servingCapacityRequired(const DecodedProgram &program,
                             ServingCapacityRequirement &out,
                             MeshLoadError &error);

} // namespace ai_mesh
} // namespace gem5

#endif

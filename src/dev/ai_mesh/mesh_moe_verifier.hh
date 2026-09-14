#ifndef DEV_AI_MESH_MESH_MOE_VERIFIER_HH
#define DEV_AI_MESH_MESH_MOE_VERIFIER_HH

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{

// Semantic verification of the Dynamic MoE V1 extension, mirroring
// util/mesh_ir/mesh_ir/abi/moe_verifier.py.  Programs without the feature
// bit must carry no MoE table; programs with it must satisfy every
// cross-reference, bound and weight-binding rule of contract 7.2.
bool verifyMoeV1(const DecodedProgram &program, const RuntimeArch &arch,
                 MeshLoadError &error);

} // namespace ai_mesh
} // namespace gem5

#endif

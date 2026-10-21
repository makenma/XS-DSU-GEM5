#ifndef GEM5_DEV_AI_MESH_MESH_IR_COMPUTE_VERIFIER_HH
#define GEM5_DEV_AI_MESH_MESH_IR_COMPUTE_VERIFIER_HH

#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

namespace gem5
{
namespace ai_mesh
{

struct RuntimeArch;
class VerifiedProgramGeometry;

bool verifyProgramComputeDomain(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    MeshLoadError &error);

}
}

#endif

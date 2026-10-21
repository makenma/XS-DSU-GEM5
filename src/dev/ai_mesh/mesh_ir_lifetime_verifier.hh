#ifndef DEV_AI_MESH_MESH_IR_LIFETIME_VERIFIER_HH
#define DEV_AI_MESH_MESH_IR_LIFETIME_VERIFIER_HH

#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

namespace gem5
{
namespace ai_mesh
{

struct RuntimeArch;
class VerifiedProgramGeometry;
class VerifiedProgramBacking;
class VerifiedIntrinsicDependencyFacts;

bool verifyProgramLifetimeDomain(
    const DecodedProgram &, const RuntimeArch &,
    const ProgramSemanticContext &, const VerifiedProgramGeometry &,
    const VerifiedProgramBacking &, const VerifiedIntrinsicDependencyFacts &,
    MeshLoadError &);

}
}

#endif

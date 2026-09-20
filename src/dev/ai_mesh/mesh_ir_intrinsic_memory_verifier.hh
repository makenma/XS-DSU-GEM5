#ifndef GEM5_DEV_AI_MESH_MESH_IR_INTRINSIC_MEMORY_VERIFIER_HH
#define GEM5_DEV_AI_MESH_MESH_IR_INTRINSIC_MEMORY_VERIFIER_HH

#include "dev/ai_mesh/mesh_ir_intrinsic_dependency_facts.hh"
#include "dev/ai_mesh/mesh_ir_physical_operation_verifier.hh"
#include "dev/ai_mesh/mesh_ir_region.hh"

namespace gem5
{
namespace ai_mesh
{

bool verifyProgramIntrinsicMemoryDomain(
    const DecodedProgram &, const ProgramSemanticContext &,
    const VerifiedProgramGeometry &, const VerifiedIntrinsicDependencyFacts &,
    const VerifiedMatrixContractionFacts &,
    MeshLoadError &);

}
}

#endif

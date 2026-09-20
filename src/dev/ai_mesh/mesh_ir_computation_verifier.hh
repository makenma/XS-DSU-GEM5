#ifndef GEM5_DEV_AI_MESH_MESH_IR_COMPUTATION_VERIFIER_HH
#define GEM5_DEV_AI_MESH_MESH_IR_COMPUTATION_VERIFIER_HH

#include "dev/ai_mesh/mesh_ir_physical_operation_verifier.hh"

namespace gem5
{
namespace ai_mesh
{

bool verifyProgramComputationDomain(
    const DecodedProgram &, const ProgramSemanticContext &,
    const VerifiedProgramGeometry &, VerifiedMatrixContractionFacts &,
    MeshLoadError &);

}
}

#endif

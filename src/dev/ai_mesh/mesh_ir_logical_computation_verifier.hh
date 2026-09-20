#ifndef GEM5_DEV_AI_MESH_MESH_IR_LOGICAL_COMPUTATION_VERIFIER_HH
#define GEM5_DEV_AI_MESH_MESH_IR_LOGICAL_COMPUTATION_VERIFIER_HH

#include "dev/ai_mesh/mesh_ir_region.hh"

namespace gem5
{
namespace ai_mesh
{

bool verifyProgramLogicalComputationDomain(
    const DecodedProgram &, const ProgramSemanticContext &, MeshLoadError &);

bool verifyMatrixAccumulatorTensorContract(
    const DecodedProgram &, const ProgramSemanticContext &,
    const mesh_abi::semantic_abi::KernelComputation &,
    const mesh_abi::semantic_abi::KernelTensor &, MeshLoadError &);

}
}

#endif

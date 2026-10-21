#ifndef DEV_AI_MESH_MESH_SERVING_TENSOR_BINDINGS_HH
#define DEV_AI_MESH_MESH_SERVING_TENSOR_BINDINGS_HH

#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

namespace gem5::ai_mesh
{

uint64_t requestWorkspaceBytes(const DecodedProgram &, uint16_t programId, uint16_t profileId);

bool verifyServingTensorBindings(const DecodedProgram &program,
    const ProgramSemanticContext &context, MeshLoadError &error);

}

#endif

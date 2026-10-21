#ifndef DEV_AI_MESH_MESH_SERVING_WORKSPACE_HH
#define DEV_AI_MESH_MESH_SERVING_WORKSPACE_HH

#include "dev/ai_mesh/mesh_execution_view.hh"
#include "dev/ai_mesh/mesh_ir_region.hh"

namespace gem5::ai_mesh
{

bool verifyWorkspacePublication(const DecodedProgram &,
    const ProgramSemanticContext &, const VerifiedProgramGeometry &,
    const ExecutionViewSet &, const mesh_abi::AgentRequestProfile &, MeshLoadError &);

}

#endif

#ifndef DEV_AI_MESH_MESH_SERVING_VERIFIER_HH
#define DEV_AI_MESH_MESH_SERVING_VERIFIER_HH

#include "dev/ai_mesh/mesh_execution_view.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"
#include "dev/ai_mesh/mesh_ir_dma_verifier.hh"

namespace gem5
{
namespace ai_mesh
{

bool verifyServingV1(const ExecutionViewSet &views, const DecodedProgram &program,
                     const ProgramSemanticContext &context, const VerifiedDmaDomain &dma,
                     const VerifiedProgramGeometry &geometry, const RuntimeArch &arch,
                     MeshLoadError &error);

} // namespace ai_mesh
} // namespace gem5

#endif

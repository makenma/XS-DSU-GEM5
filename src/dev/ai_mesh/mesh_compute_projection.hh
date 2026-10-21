#ifndef DEV_AI_MESH_MESH_COMPUTE_PROJECTION_HH
#define DEV_AI_MESH_MESH_COMPUTE_PROJECTION_HH

#include "dev/ai_mesh/mesh_compute_commit.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"

namespace gem5::ai_mesh
{

bool projectComputeCommit(const MeshProgramAdmission &admission,
                          const DecodedCommand &command,
                          RuntimeObjectKey key, ComputeCommitRequest &out,
                          MeshLoadError &error);

}

#endif

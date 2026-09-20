#ifndef GEM5_DEV_AI_MESH_MESH_BINARY_VALIDATION_HH
#define GEM5_DEV_AI_MESH_MESH_BINARY_VALIDATION_HH

#include "dev/ai_mesh/mesh_binary_storage.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_binary_detail
{

bool validateProgramStorage(
    const ProgramStorage &program, MeshLoadError &error);

}
}
}

#endif

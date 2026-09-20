#ifndef GEM5_DEV_AI_MESH_MESH_BINARY_CANONICAL_HH
#define GEM5_DEV_AI_MESH_MESH_BINARY_CANONICAL_HH

#include <iosfwd>

#include "dev/ai_mesh/mesh_binary_storage.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_binary_detail
{

bool writeProgramCanonical(
    const ProgramStorage &program, mesh_abi::CanonicalProjection projection,
    std::ostream &out, MeshLoadError &error);
bool writeSemanticValueCanonical(
    const ProgramStorage &program,
    const mesh_abi::semantic_abi::SemanticRef &value,
    std::ostream &out, MeshLoadError &error);
bool validateProgramSemanticChecksum(
    const ProgramStorage &program, MeshLoadError &error);

}
}
}

#endif

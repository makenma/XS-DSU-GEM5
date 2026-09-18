#ifndef DEV_AI_MESH_MESH_KV_LAYOUT_HH
#define DEV_AI_MESH_MESH_KV_LAYOUT_HH

#include <array>
#include <cstdint>

#include "dev/ai_mesh/mesh_binary.hh"

namespace gem5
{
namespace ai_mesh
{

bool kvLayoutDigest(const DecodedProgram &program,
                    std::array<uint8_t, 32> &digest, MeshLoadError &error);

}
}
#endif

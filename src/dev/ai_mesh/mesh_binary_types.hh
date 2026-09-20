#ifndef GEM5_DEV_AI_MESH_MESH_BINARY_TYPES_HH
#define GEM5_DEV_AI_MESH_MESH_BINARY_TYPES_HH

#include <cstdint>
#include <string>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

using MeshBytes = std::vector<uint8_t>;

struct MeshLoadError
{
    std::string code;
    std::string message;
};

}
}

#endif

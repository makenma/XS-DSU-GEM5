#ifndef DEV_AI_MESH_MESH_SERVING_PROJECTION_HH
#define DEV_AI_MESH_MESH_SERVING_PROJECTION_HH

#include <array>
#include <cstdint>

#include "dev/ai_mesh/mesh_binary.hh"

namespace gem5
{
namespace ai_mesh
{

bool programProfileKeyBaseDigest(
    const DecodedProgram &program, std::array<uint8_t, 32> &out,
    MeshLoadError &error);
uint64_t requestProfileKey(const mesh_abi::AgentRequestProfile &record,
                          const std::array<uint8_t, 32> &base_digest);
bool verifyRequestProfileKeys(const DecodedProgram &program,
                              MeshLoadError &error);
bool verifyRequestPhaseMode(const DecodedProgram &program,
    const mesh_abi::AgentRequestProfile &request, MeshLoadError &error);

}
}

#endif

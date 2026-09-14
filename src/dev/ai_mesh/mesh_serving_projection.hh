#ifndef DEV_AI_MESH_MESH_SERVING_PROJECTION_HH
#define DEV_AI_MESH_MESH_SERVING_PROJECTION_HH

#include <array>
#include <cstdint>
#include <string>

#include "dev/ai_mesh/mesh_binary.hh"

namespace gem5
{
namespace ai_mesh
{

// Canonical Mesh semantic projection, byte-identical to Python
// mesh_ir.model.Program.canonical_dict + canonical_json_bytes.  Mirrors
// util/mesh_ir/mesh_ir/serving_profiles.py.
std::string canonicalProgramProjection(const DecodedProgram &program,
                                       bool zero_request_keys);
std::array<uint8_t, 32> programProfileKeyBaseDigest(
    const DecodedProgram &program);
std::array<uint8_t, 32> programSemanticDigest(const DecodedProgram &program);
uint64_t requestProfileKey(const mesh_abi::AgentRequestProfile &record,
                           const std::array<uint8_t, 32> &base_digest);
bool verifyRequestProfileKeys(const DecodedProgram &program,
                              MeshLoadError &error);

} // namespace ai_mesh
} // namespace gem5

#endif

#ifndef GEM5_DEV_AI_MESH_MESH_BINARY_STORAGE_HH
#define GEM5_DEV_AI_MESH_MESH_BINARY_STORAGE_HH

#include <cstdint>
#include <string>
#include <vector>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary_envelope.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_binary_detail
{

struct ProgramStorage
{
    MeshHeader header;
    mesh_abi::semantic_abi::ProgramMetadata metadata;
    mesh_abi::TransportTables transport;
    std::vector<std::string> semantic_strings;
    std::vector<uint64_t> semantic_u64_values;
    std::vector<int64_t> semantic_i64_values;
    std::vector<mesh_abi::semantic_abi::SemanticRef> semantic_references;
    std::vector<uint8_t> semantic_bytes;
    std::vector<mesh_abi::semantic_abi::SemanticIntegerValue>
        semantic_integer_values;
    mesh_abi::semantic_abi::SemanticTables semantic_tables;
};

bool decodeProgramStorage(
    const MeshBytes &image, const DecodedEnvelope &envelope,
    ProgramStorage &out, MeshLoadError &error);
bool encodeProgramStorage(
    const ProgramStorage &program, std::vector<MeshSection> &out,
    MeshLoadError &error);
bool validateProgramMetadata(
    const ProgramStorage &program, MeshLoadError &error);

}
}
}

#endif

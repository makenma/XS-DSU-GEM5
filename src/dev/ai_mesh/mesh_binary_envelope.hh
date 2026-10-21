#ifndef GEM5_DEV_AI_MESH_MESH_BINARY_ENVELOPE_HH
#define GEM5_DEV_AI_MESH_MESH_BINARY_ENVELOPE_HH

#include <array>
#include <cstdint>
#include <vector>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary_types.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_binary_detail
{

struct MeshHeader
{
    uint16_t abi_major = mesh_abi::kAbiMajor;
    uint16_t abi_minor = mesh_abi::kAbiMinor;
    uint64_t required_features = mesh_abi::kRequiredFeatures;
    std::array<uint8_t, 32> arch_digest{};
};

struct MeshSection
{
    uint16_t type = 0;
    uint32_t record_bytes = 0;
    uint64_t count = 0;
    MeshBytes payload;
};

struct MeshSectionView
{
    uint16_t type = 0;
    uint32_t record_bytes = 0;
    uint64_t offset = 0;
    uint64_t size = 0;
    uint64_t count = 0;
};

struct DecodedEnvelope
{
    MeshHeader header;
    std::vector<MeshSectionView> sections;
};

bool decodeMeshEnvelope(
    const MeshBytes &image, DecodedEnvelope &out, MeshLoadError &error);
bool encodeMeshEnvelope(
    const MeshHeader &header, const std::vector<MeshSection> &sections,
    MeshBytes &out, MeshLoadError &error);
bool validateMeshHeader(const MeshHeader &header, MeshLoadError &error);
uint32_t meshCrc32(const uint8_t *data, size_t size);

}
}
}

#endif

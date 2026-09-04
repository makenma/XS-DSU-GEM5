#ifndef DEV_AI_MESH_MESH_BINARY_HH
#define DEV_AI_MESH_MESH_BINARY_HH

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

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

using DecodedDmaEndpoint = mesh_abi::DmaEndpoint;
using DecodedDmaDescriptor = mesh_abi::DmaDescriptor;
using DecodedCommand = mesh_abi::Command;
using DecodedEvent = mesh_abi::Event;
using DecodedStream = mesh_abi::Stream;
using DecodedAllocation = mesh_abi::Allocation;
using DecodedTensor = mesh_abi::Tensor;
using DecodedShard = mesh_abi::Shard;
using DecodedOperand = mesh_abi::CommandOperand;
using DecodedTrafficRow = mesh_abi::ExpectedTraffic;
using DecodedProfile = mesh_abi::Profile;
using DecodedRelocation = mesh_abi::Relocation;
using DecodedEntrypoint = mesh_abi::Entrypoint;

struct DecodedAttr
{
    uint16_t kind = 0;
    std::array<uint8_t, 28> payload{};
    mesh_abi::AttrPayload typed{};

    uint32_t payloadU32(size_t offset) const
    {
        return static_cast<uint32_t>(payload[offset]) |
               (static_cast<uint32_t>(payload[offset + 1]) << 8) |
               (static_cast<uint32_t>(payload[offset + 2]) << 16) |
               (static_cast<uint32_t>(payload[offset + 3]) << 24);
    }
    uint64_t payloadU64(size_t offset) const
    {
        uint64_t value = 0;
        for (size_t i = 0; i < 8; i++)
            value |= static_cast<uint64_t>(payload[offset + i]) << (8 * i);
        return value;
    }

    template <typename T>
    const T &as() const
    {
        return std::get<T>(typed);
    }
};

struct DecodedProgram
{
    uint16_t abi_major = 0;
    uint16_t abi_minor = 0;
    std::string arch_digest_hex;
    std::vector<std::string> strings;
    std::vector<DecodedEntrypoint> entrypoints;
    std::vector<DecodedProfile> profiles;
    std::vector<DecodedTensor> tensors;
    std::vector<DecodedShard> shards;
    std::vector<DecodedAllocation> allocations;
    std::vector<DecodedStream> streams;
    std::vector<DecodedCommand> commands;
    std::vector<uint32_t> waits;
    std::vector<DecodedOperand> operands;
    std::vector<DecodedEvent> events;
    std::vector<DecodedDmaDescriptor> descriptors;
    std::vector<DecodedAttr> attrs;
    std::vector<DecodedRelocation> relocations;
    std::vector<DecodedTrafficRow> traffic;
    std::vector<mesh_abi::SourceMap> source_map;
    std::vector<mesh_abi::ContentDigest> content_digests;
};

// Parse and integrity-check a .mshb image.  Throws nothing; returns false
// and fills `error` with a stable code on any violation (fail closed).
bool decodeMeshBinary(const MeshBytes &image, DecodedProgram &out, MeshLoadError &error);
bool decodeMeshBinaryInto(const MeshBytes &image, DecodedProgram &out,
                          MeshLoadError &error);

// Recompute section CRC32s and the header payload SHA-256 in place.  Used to
// craft structurally-illegal-but-checksum-valid binaries for fail-closed
// cross-language parity tests (the same mutations the Python side builds).
void recomputeMeshChecksums(MeshBytes &image);

} // namespace ai_mesh
} // namespace gem5

#endif

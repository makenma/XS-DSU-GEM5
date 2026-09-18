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
using DecodedProfileStreamRange = mesh_abi::ProfileStreamRange;
using DecodedProfile = mesh_abi::Profile;
using DecodedRelocation = mesh_abi::Relocation;
using DecodedEntrypoint = mesh_abi::Entrypoint;
using DecodedMoeLayerSpec = mesh_abi::MoeLayerSpec;
using DecodedMoeExpertSpec = mesh_abi::MoeExpertSpec;
using DecodedMoeDynamicRegion = mesh_abi::MoeDynamicRegion;
using DecodedMoeKernelSpec = mesh_abi::MoeKernelSpec;
using DecodedAgentRequestProfile = mesh_abi::AgentRequestProfile;
using DecodedAgentInstanceProfile = mesh_abi::AgentInstanceProfile;
using DecodedAgentSourceCore = mesh_abi::AgentSourceCoreMap;
using DecodedAgentInstanceMemberBinding = mesh_abi::AgentInstanceMemberBinding;
using DecodedAgentRequestBindingRequirement =
    mesh_abi::AgentRequestBindingRequirement;
using DecodedAgentPublishSurrogateBinding =
    mesh_abi::AgentPublishSurrogateBinding;

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
    uint64_t required_features = 0;
    bool has_moe_v1 = false;
    bool has_profile_scoped_execution_v1 = false;
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
    std::vector<DecodedProfileStreamRange> profile_stream_ranges;
    std::vector<mesh_abi::SourceMap> source_map;
    std::vector<mesh_abi::ContentDigest> content_digests;
    std::vector<DecodedMoeLayerSpec> moe_layer_specs;
    std::vector<DecodedMoeExpertSpec> moe_expert_specs;
    std::vector<DecodedMoeDynamicRegion> moe_dynamic_regions;
    std::vector<DecodedMoeKernelSpec> moe_kernel_specs;
    bool has_serving_v1 = false;
    std::vector<DecodedAgentRequestProfile> agent_request_profiles;
    std::vector<DecodedAgentInstanceProfile> agent_instance_profiles;
    std::vector<DecodedAgentSourceCore> agent_source_core_map;
    std::vector<DecodedAgentInstanceMemberBinding>
        agent_instance_member_bindings;
    std::vector<DecodedAgentRequestBindingRequirement>
        agent_request_binding_requirements;
    std::vector<DecodedAgentPublishSurrogateBinding>
        agent_publish_surrogate_bindings;
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

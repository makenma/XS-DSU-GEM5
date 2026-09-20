#ifndef DEV_AI_MESH_MESH_BINARY_HH
#define DEV_AI_MESH_MESH_BINARY_HH

#include "dev/ai_mesh/mesh_binary_storage.hh"

namespace gem5
{
namespace ai_mesh
{

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
using DecodedAttr = mesh_abi::TypedOpAttr;
using DecodedProgram = mesh_binary_detail::ProgramStorage;

bool decodeMeshBinary(const MeshBytes &image, DecodedProgram &out, MeshLoadError &error);
bool encodeMeshBinary(
    const DecodedProgram &program, MeshBytes &out, MeshLoadError &error);

} // namespace ai_mesh
} // namespace gem5

#endif

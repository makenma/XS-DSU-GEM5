#ifndef DEV_AI_MESH_MESH_INVOCATION_BINDING_HH
#define DEV_AI_MESH_MESH_INVOCATION_BINDING_HH

#include <array>
#include <cstdint>
#include <map>
#include <vector>

#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{

class MeshProgramAdmission;

// One dispatch-time binding supplied by the configuration.  It may only
// narrow a compiler-declared binding slot (same region/owner/access, enough
// size and alignment); it can never widen it.
struct DispatchBinding
{
    uint32_t slot_id = 0;
    uint32_t region_id = 0;
    uint16_t owner_core = 0xffff;
    uint64_t allocation_offset_bytes = 0;
    uint64_t allocation_size_bytes = 0;
    uint32_t allocation_alignment_bytes = 0;
    uint32_t access = 0;
};

struct ResolvedBindingFact
{
    uint32_t slot_id = 0;
    uint32_t region_id = 0;
    uint16_t owner_core = 0xffff;
    uint64_t allocation_offset_bytes = 0;
    uint64_t allocation_address = 0;
    uint64_t allocation_size_bytes = 0;
    uint32_t allocation_alignment_bytes = 0;
    uint32_t access = 0;
};

// The resolved binding of exactly one selected Program variant.  It is built
// once during Relocating and is the single entry point every runtime consumer
// (DMA endpoints, P2P transfer plan) uses for actual addresses.  The
// compiler's reference binding and admitted Program identity are untouched.
class MeshInvocationBinding
{
  public:
    uint64_t variantId() const { return variant_id_; }
    const std::map<uint32_t, ResolvedBindingFact> &bindings() const
    {
        return bindings_;
    }
    const ResolvedBindingFact *binding(uint32_t slot_id) const;
    uint64_t endpointAddress(uint32_t descriptor_id, bool source) const;
    bool hasEndpoint(uint32_t descriptor_id) const
    {
        return endpoint_addresses_.count(descriptor_id) != 0;
    }

  private:
    uint64_t variant_id_ = 0;
    std::map<uint32_t, ResolvedBindingFact> bindings_;
    std::map<uint32_t, std::array<uint64_t, 2>> endpoint_addresses_;

    friend bool resolveInvocationBinding(
        const MeshProgramAdmission &, uint64_t,
        const std::vector<DispatchBinding> &, MeshInvocationBinding &,
        MeshLoadError &);
};

bool resolveInvocationBinding(
    const MeshProgramAdmission &admission, uint64_t variant_id,
    const std::vector<DispatchBinding> &dispatch,
    MeshInvocationBinding &out, MeshLoadError &error);

} // namespace ai_mesh
} // namespace gem5

#endif

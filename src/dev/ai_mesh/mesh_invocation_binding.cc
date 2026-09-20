#include "dev/ai_mesh/mesh_invocation_binding.hh"

#include <algorithm>
#include <iterator>
#include <set>

#include "base/logging.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

bool
powerOfTwo(uint64_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

uint64_t
checkedAdd(uint64_t a, uint64_t b, bool &overflow)
{
    if (a > ~uint64_t(0) - b)
        overflow = true;
    return a + b;
}

bool
regionBase(const RuntimeArch &arch, uint32_t region_id, uint16_t owner_core,
           uint64_t &base, uint64_t &limit, MeshLoadError &error)
{
    const RuntimeArch::Region *region = arch.region(uint16_t(region_id));
    if (!region)
        return fail("E_RELOCATION", "address references an unknown region",
                    error);
    if (region->perCore()) {
        if (!arch.hasCore(owner_core))
            return fail("E_RELOCATION",
                        "SRAM address references an unknown core", error);
        bool overflow = false;
        base = region->tileBase(owner_core, overflow);
        if (overflow)
            return fail("E_ABI_OVERFLOW", "core aperture base overflows",
                        error);
        limit = region->tile_bytes;
        return true;
    }
    if (owner_core != 0xffff)
        return fail("E_RELOCATION",
                    "non-per-core region requires the invalid owner identity",
                    error);
    base = region->base;
    limit = region->bytes;
    return true;
}

const mesh_abi::semantic_abi::Binding *
referenceBinding(const DecodedProgram &program,
                 const mesh_abi::semantic_abi::BindingSlot &slot)
{
    const auto &reference = slot.reference_binding;
    if (reference.row_id == 0 ||
        reference.row_id > program.semantic_tables.binding_rows.size())
        return nullptr;
    return &program.semantic_tables.binding_rows[reference.row_id - 1];
}

bool
fabricTargetCovers(const RuntimeArch &arch, uint32_t region_id,
                   uint16_t owner_core, uint64_t region_offset, uint64_t size,
                   uint32_t access, MeshLoadError &error)
{
    if (arch.fabric_targets.empty())
        return true;
    const uint64_t end = region_offset + size;
    size_t matches = 0;
    for (const auto &target : arch.fabric_targets)
        for (const auto &range : target.ranges) {
            if (range.region_id != region_id ||
                range.owner_core != owner_core)
                continue;
            const bool permitted =
                access == mesh_abi::kAccessKindREAD_ONLY || range.writable;
            const bool point =
                size == 0 && range.offset_bytes <= region_offset &&
                region_offset < range.offset_bytes + range.size_bytes;
            const bool interval =
                size > 0 && range.offset_bytes <= region_offset &&
                end <= range.offset_bytes + range.size_bytes;
            if (permitted && (point || interval))
                matches++;
        }
    if (matches != 1)
        return fail("E_RELOCATION",
                    "address does not resolve to exactly one permitted "
                    "fabric target",
                    error);
    return true;
}

} // anonymous namespace

const ResolvedBindingFact *
MeshInvocationBinding::binding(uint32_t slot_id) const
{
    auto found = bindings_.find(slot_id);
    return found == bindings_.end() ? nullptr : &found->second;
}

uint64_t
MeshInvocationBinding::endpointAddress(uint32_t descriptor_id,
                                       bool source) const
{
    auto found = endpoint_addresses_.find(descriptor_id);
    fatal_if(found == endpoint_addresses_.end(),
             "invocation binding has no endpoint address for descriptor %u",
             descriptor_id);
    return found->second[source ? 0 : 1];
}

bool
resolveInvocationBinding(const MeshProgramAdmission &admission,
                         uint64_t variant_id,
                         const std::vector<DispatchBinding> &dispatch,
                         MeshInvocationBinding &out, MeshLoadError &error)
{
    const auto &variants = admission.context().variants();
    auto variant = variants.find(variant_id);
    if (variant == variants.end())
        return fail("E_RELOCATION", "selected Program variant is unknown",
                    error);
    const RuntimeArch &arch = admission.arch();

    std::map<uint32_t, const mesh_abi::semantic_abi::BindingSlot *> slots;
    for (const auto &[slot_id, slot] : admission.context().bindingSlots()) {
        if (admission.context().owner("binding_slots", slot_id) !=
            variant->second)
            continue;
        slots.emplace(uint32_t(slot_id), slot);
    }

    std::vector<DispatchBinding> effective = dispatch;
    if (effective.empty()) {
        for (const auto &[slot_id, slot] : slots) {
            const mesh_abi::semantic_abi::Binding *reference =
                referenceBinding(admission.program(), *slot);
            if (!reference)
                return fail("E_RELOCATION",
                            "binding slot reference binding is missing", error);
            DispatchBinding binding;
            binding.slot_id = uint32_t(reference->slot_id);
            binding.region_id = uint32_t(reference->region_id);
            binding.owner_core = uint16_t(reference->owner_core);
            binding.allocation_offset_bytes = reference->allocation_offset_bytes;
            binding.allocation_size_bytes = reference->allocation_size_bytes;
            binding.allocation_alignment_bytes =
                uint32_t(reference->allocation_alignment_bytes);
            binding.access = static_cast<uint32_t>(reference->access);
            effective.push_back(binding);
        }
    }

    std::map<uint32_t, const DispatchBinding *> by_slot;
    for (const auto &entry : effective)
        if (!by_slot.emplace(entry.slot_id, &entry).second)
            return fail("E_RELOCATION", "dispatch binding repeats a slot",
                        error);
    if (by_slot.size() != slots.size())
        return fail("E_RELOCATION",
                    "dispatch bindings must cover every slot exactly once",
                    error);

    MeshInvocationBinding resolved;
    resolved.variant_id_ = variant_id;
    for (const auto &[slot_id, slot] : slots) {
        auto found = by_slot.find(slot_id);
        if (found == by_slot.end())
            return fail("E_RELOCATION",
                        "dispatch bindings must cover every slot exactly once",
                        error);
        const DispatchBinding &binding = *found->second;
        if (binding.slot_id != slot->slot_id ||
            binding.region_id != slot->region_id ||
            binding.owner_core != slot->owner_core ||
            binding.access != static_cast<uint32_t>(slot->access))
            return fail("E_RELOCATION",
                        "binding overrides an immutable slot constraint",
                        error);
        if (binding.access != mesh_abi::kAccessKindREAD_ONLY &&
            binding.access != mesh_abi::kAccessKindREAD_WRITE)
            return fail("E_RELOCATION", "binding access is invalid", error);
        if (!powerOfTwo(binding.allocation_alignment_bytes))
            return fail("E_RELOCATION", "binding alignment is invalid", error);
        if (binding.allocation_size_bytes < slot->required_allocation_bytes ||
            binding.allocation_alignment_bytes <
                slot->required_allocation_alignment_bytes ||
            binding.allocation_alignment_bytes %
                    slot->required_allocation_alignment_bytes !=
                0)
            return fail("E_RELOCATION",
                        "binding does not satisfy slot size or alignment",
                        error);
        uint64_t base = 0;
        uint64_t limit = 0;
        if (!regionBase(arch, binding.region_id, binding.owner_core, base, limit,
                        error))
            return false;
        bool overflow = false;
        const uint64_t address =
            checkedAdd(base, binding.allocation_offset_bytes, overflow);
        if (overflow)
            return fail("E_ABI_OVERFLOW",
                        "binding allocation address overflows", error);
        if (arch.axi_address_bits < 64) {
            const uint64_t address_limit = uint64_t(1)
                << arch.axi_address_bits;
            if (address >= address_limit ||
                (binding.allocation_size_bytes != 0 &&
                 address + binding.allocation_size_bytes - 1 >= address_limit))
                return fail("E_RELOCATION",
                            "resolved interval exceeds the AXI address width",
                            error);
        }
        if (address % binding.allocation_alignment_bytes != 0)
            return fail("E_RELOCATION",
                        "binding allocation base violates its alignment",
                        error);
        if (limit < binding.allocation_size_bytes ||
            binding.allocation_offset_bytes >
                limit - binding.allocation_size_bytes)
            return fail("E_RELOCATION",
                        "binding allocation exceeds its region", error);
        if (!fabricTargetCovers(arch, binding.region_id, binding.owner_core,
                                binding.allocation_offset_bytes,
                                binding.allocation_size_bytes, binding.access,
                                error))
            return false;
        ResolvedBindingFact fact;
        fact.slot_id = binding.slot_id;
        fact.region_id = binding.region_id;
        fact.owner_core = binding.owner_core;
        fact.allocation_offset_bytes = binding.allocation_offset_bytes;
        fact.allocation_address = address;
        fact.allocation_size_bytes = binding.allocation_size_bytes;
        fact.allocation_alignment_bytes = binding.allocation_alignment_bytes;
        fact.access = binding.access;
        resolved.bindings_.emplace(slot_id, fact);
    }

    for (auto left = resolved.bindings_.begin();
         left != resolved.bindings_.end(); ++left)
        for (auto right = std::next(left); right != resolved.bindings_.end();
             ++right) {
            if (left->second.region_id != right->second.region_id ||
                left->second.owner_core != right->second.owner_core)
                continue;
            const uint64_t left_end = left->second.allocation_address +
                left->second.allocation_size_bytes;
            const uint64_t right_end = right->second.allocation_address +
                right->second.allocation_size_bytes;
            if (std::max(left->second.allocation_address,
                         right->second.allocation_address) <
                    std::min(left_end, right_end) &&
                (left->second.access == mesh_abi::kAccessKindREAD_WRITE ||
                 right->second.access == mesh_abi::kAccessKindREAD_WRITE))
                return fail("E_RELOCATION",
                            "writable dispatch bindings overlap", error);
        }

    for (const auto &descriptor :
         admission.program().transport.dma_descriptors) {
        std::array<uint64_t, 2> addresses{0, 0};
        for (bool source : {true, false}) {
            const DescriptorBindingFact *fact = admission.dma().descriptorBinding(
                descriptor.descriptor_id, source);
            if (!fact)
                return fail("E_RELOCATION",
                            "descriptor endpoint binding fact is missing",
                            error);
            if (fact->slotId() == 0) {
                addresses[source ? 0 : 1] =
                    source ? admission.dma().sourceAddress(
                                 descriptor.descriptor_id)
                           : admission.dma().destinationAddress(
                                 descriptor.descriptor_id);
                continue;
            }
            auto binding = resolved.bindings_.find(fact->slotId());
            if (binding == resolved.bindings_.end())
                return fail("E_RELOCATION",
                            "descriptor endpoint uses an unbound slot", error);
            bool overflow = false;
            addresses[source ? 0 : 1] =
                checkedAdd(binding->second.allocation_address, fact->begin(),
                           overflow);
            if (overflow)
                return fail("E_ABI_OVERFLOW",
                            "descriptor endpoint address overflows", error);
        }
        resolved.endpoint_addresses_.emplace(descriptor.descriptor_id,
                                             addresses);
    }

    out = resolved;
    return true;
}

} // namespace ai_mesh
} // namespace gem5

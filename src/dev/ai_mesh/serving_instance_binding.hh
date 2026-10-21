#ifndef DEV_AI_MESH_SERVING_INSTANCE_BINDING_HH
#define DEV_AI_MESH_SERVING_INSTANCE_BINDING_HH

#include <cstdint>
#include <algorithm>
#include <optional>
#include <memory>
#include "dev/ai_mesh/publish_segment_ledger.hh"
#include "dev/ai_mesh/mesh_error_candidate.hh"
#include "dev/ai_mesh/mesh_program_invocation.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"
#include <utility>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

struct ServingByteWindow
{
    uint64_t begin = 0;
    uint64_t bytes = 0;
};

struct ServingMemberIdentity
{
    uint64_t request_id = 0;
    uint32_t generation = 0;
    uint32_t ordinal = 0;
};

struct ServingResolvedAccess
{
    uint64_t address = 0;
    std::optional<ServingMemberIdentity> member;
};

struct ServingKvDescriptorIdentity
{
    uint32_t descriptor_id = 0;
    uint32_t phase_ordinal = 0;
    ServingMemberIdentity member;
};

class ServingInstanceBinding
{
  public:
    ServingInstanceBinding() = default;
    ServingInstanceBinding(uint64_t request_id, uint32_t generation)
        : request_id(request_id), generation(generation) {}
    explicit ServingInstanceBinding(size_t capacity)
        : capacity_limit(capacity)
    { slots.reserve(capacity); }

    void reset(uint64_t request, uint32_t request_generation, uint32_t profile = 0,
               std::optional<uint8_t> qos = std::nullopt)
    {
        slots.clear();
        invocation_.reset();
        output_ledger.reset();
        kv_descriptors.clear();
        request_id = request;
        generation = request_generation;
        instance_profile_id = profile;
        effective_qos = qos;
    }

    uint64_t requestId() const { return request_id; }
    uint32_t requestGeneration() const { return generation; }
    uint32_t instanceProfileId() const { return instance_profile_id; }
    uint8_t effectiveQos(uint8_t fallback) const
    { return effective_qos.value_or(fallback); }

    const std::shared_ptr<const MeshProgramInvocation> &invocation() const
    { return invocation_; }

    bool installInvocation(std::shared_ptr<const MeshProgramInvocation> invocation)
    {
        if (!invocation || invocation_ || !slots.empty())
            return false;
        invocation_ = std::move(invocation);
        return true;
    }

    bool bindSlot(uint32_t slot_id, ServingByteWindow read, ServingByteWindow write,
                  std::optional<ServingMemberIdentity> member = std::nullopt)
    {
        if (!invocation_ || (capacity_limit && slots.size() >= *capacity_limit))
            return false;
        const auto *binding = invocation_->binding().binding(slot_id);
        if (!binding || (write.bytes && binding->access != mesh_abi::kAccessKindREAD_WRITE))
            return false;
        for (const auto window : {read, write})
            if (!spanFits(window.begin, window.bytes, binding->allocation_size_bytes))
                return false;
        if (std::any_of(slots.begin(), slots.end(), [slot_id](const auto &slot) {
                return slot.slot_id == slot_id;
            }))
            return false;
        slots.push_back({slot_id, read, write, member});
        return true;
    }

    std::vector<uint64_t> memberRequests() const
    {
        std::vector<uint64_t> result;
        for (const auto &entry : slots)
            if (entry.member)
                result.push_back(entry.member->request_id);
        std::sort(result.begin(), result.end());
        result.erase(std::unique(result.begin(), result.end()), result.end());
        return result;
    }

    void bindKvDescriptors(std::vector<ServingKvDescriptorIdentity> descriptors)
    {
        std::sort(descriptors.begin(), descriptors.end(), [](const auto &a, const auto &b) {
            return std::tie(a.member.ordinal, a.descriptor_id) <
                   std::tie(b.member.ordinal, b.descriptor_id);
        });
        kv_descriptors = std::move(descriptors);
    }

    std::optional<mesh_abi::ErrorSourceKey> kvDescriptorSource(
        uint32_t descriptor, uint16_t error_class, uint16_t core, uint64_t instance) const
    {
        for (size_t i = 0; i < kv_descriptors.size(); ++i) {
            const auto &row = kv_descriptors[i];
            if (row.descriptor_id != descriptor)
                continue;
            mesh_abi::ErrorSourceKey source;
            source.error_class = error_class;
            source.core_id_or_ffff = core;
            source.domain = mesh_abi::kMeshObjectDomainKV_RUNTIME;
            source.object_kind = mesh_abi::kErrorSourceObjectKindDESCRIPTOR;
            source.region_group_id = row.phase_ordinal;
            source.region_id = row.member.ordinal;
            source.ordinal = i + 1;
            source.generation = row.member.generation;
            agent_abi::wrU64(source.aux_key.data(), row.member.request_id);
            agent_abi::wrU64(source.aux_key.data() + 8, instance);
            return source;
        }
        return std::nullopt;
    }

    void bindOutputLedger(std::shared_ptr<PublishSegmentLedger> ledger)
    { output_ledger = std::move(ledger); }
    const std::shared_ptr<PublishSegmentLedger> &outputLedger() const { return output_ledger; }
    bool empty() const { return slots.empty(); }

    std::optional<ServingResolvedAccess> resolveSlot(
        uint32_t slot_id, uint64_t offset, uint64_t bytes, bool write) const
    {
        if (!invocation_)
            return std::nullopt;
        const auto *binding = invocation_->binding().binding(slot_id);
        if (!binding)
            return std::nullopt;
        for (const auto &slot : slots) {
            if (slot.slot_id != slot_id)
                continue;
            const auto window = write ? slot.write : slot.read;
            if (!window.bytes || offset < window.begin ||
                !spanFits(offset - window.begin, bytes, window.bytes))
                return std::nullopt;
            return ServingResolvedAccess{binding->allocation_address + offset, slot.member};
        }
        return std::nullopt;
    }

    std::optional<ServingResolvedAccess> resolveDescriptorRows(
        uint32_t descriptor_id, bool source) const
    {
        if (!invocation_ || !invocation_->binding().hasEndpoint(descriptor_id))
            return std::nullopt;
        const auto &admission = invocation_->admission();
        const auto descriptor = admission.context().descriptors().find(descriptor_id);
        const auto *fact = admission.dma().descriptorBinding(descriptor_id, source);
        if (descriptor == admission.context().descriptors().end() || !fact)
            return std::nullopt;
        if (!fact->slotId())
            return ServingResolvedAccess{invocation_->binding().endpointAddress(descriptor_id, source), std::nullopt};
        const auto &row = *descriptor->second;
        if (!row.rows || !row.row_bytes)
            return std::nullopt;
        bool overflow = false;
        const auto bytes = checkedMulAdd(row.rows - 1,
            source ? row.src_stride_bytes : row.dst_stride_bytes, row.row_bytes, overflow);
        if (overflow)
            return std::nullopt;
        return resolveSlot(fact->slotId(), fact->begin(), bytes, !source);
    }

  private:
    struct Slot
    {
        uint32_t slot_id;
        ServingByteWindow read;
        ServingByteWindow write;
        std::optional<ServingMemberIdentity> member;
    };
    std::vector<Slot> slots;
    std::shared_ptr<const MeshProgramInvocation> invocation_;
    std::vector<ServingKvDescriptorIdentity> kv_descriptors;
    std::shared_ptr<PublishSegmentLedger> output_ledger;
    std::optional<size_t> capacity_limit;
    uint64_t request_id = 0;
    uint32_t generation = 0;
    uint32_t instance_profile_id = 0;
    std::optional<uint8_t> effective_qos;
};

}
}
#endif

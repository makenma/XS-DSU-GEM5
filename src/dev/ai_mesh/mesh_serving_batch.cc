#include "dev/ai_mesh/mesh_serving_batch.hh"

#include <algorithm>
#include <tuple>

#include "dev/ai_mesh/serving_host_bindings.hh"
#include "dev/ai_mesh/mesh_serving_tensor_bindings.hh"

namespace gem5::ai_mesh
{

MeshServingBatch::MeshServingBatch(const ServingCapacityRequirement &limits)
    : capacity(limits),
      member_table(std::make_unique<FrozenMemberBinding[]>(limits.instance_member_binding_entries)),
      weight_table(std::make_unique<FrozenWeightBinding[]>(limits.batch_weight_binding_entries)),
      interval_table(std::make_unique<FrozenBatchInterval[]>(limits.batch_interval_entries)),
      execution_binding(limits.instance_member_binding_entries + limits.batch_weight_binding_entries)
{}

void
MeshServingBatch::release()
{
    published = false;
    member_count = weight_count = interval_count = 0;
    execution_binding.reset(0, 0);
}

MeshServingBatch::FreezeResult
MeshServingBatch::freeze(std::shared_ptr<const MeshProgramAdmission> admission,
    const mesh_abi::AgentInstanceProfile &instance,
    const ServingBatchMember *members, size_t count, std::string &reason)
{
    if (published)
        return FreezeResult::Busy;
    release();
    const auto invalid = [&](const char *message) {
        reason = message;
        release();
        return FreezeResult::Invalid;
    };
    if (!admission)
        return invalid("frozen batch requires an admitted program");
    const auto &program = admission->program();
    const auto *selected = admission->executionViews().forInstance(instance.mesh_entrypoint_id, instance.mesh_profile_id);
    if (!selected)
        return invalid("frozen batch has no admitted execution view");
    const auto &view = *selected;
    const auto &context = admission->context();
    if (members == nullptr || count == 0 || count != instance.member_count ||
            instance.member_binding_count != count ||
            instance.member_binding_first > program.transport.agent_instance_member_bindings.size() ||
            count > program.transport.agent_instance_member_bindings.size() - instance.member_binding_first)
        return invalid("frozen member population does not match the concrete profile");
    const auto request_profile = std::find_if(program.transport.agent_request_profiles.begin(),
        program.transport.agent_request_profiles.end(), [&](const auto &row) {
            return row.program_id == instance.request_program_id &&
                   row.profile_id == instance.request_profile_id;
        });
    if (request_profile == program.transport.agent_request_profiles.end())
        return invalid("frozen instance has no request profile");
    ServingCapacityRequirement needed;
    MeshLoadError capacityError;
    if (!servingCapacityFor(program, instance, needed, capacityError))
        return invalid("frozen batch has no admitted capacity requirement");
    const bool uses_kv = request_profile->flags & mesh_abi::kAgentRequestFlagsHAS_KV;
    if (needed.instance_member_binding_entries > capacity.instance_member_binding_entries ||
            needed.batch_weight_binding_entries > capacity.batch_weight_binding_entries ||
            needed.batch_interval_entries > capacity.batch_interval_entries) {
        reason = "frozen batch cannot reserve all three tables";
        return FreezeResult::NoCapacity;
    }
    std::vector<DispatchBinding> dispatch;
    const auto appendSlot = [&](const ProgramSymbolBinding &source, uint64_t address, uint64_t bytes) {
        const auto *region = admission->arch().region(source.slot->region_id);
        if (!region || region->perCore() || address < region->base)
            return false;
        dispatch.push_back({uint32_t(source.slot->slot_id), uint32_t(source.slot->region_id),
            uint16_t(source.slot->owner_core), address - region->base, bytes,
            uint32_t(source.slot->required_allocation_alignment_bytes), uint32_t(source.slot->access)});
        return true;
    };
    const auto binding = [](const NpuExecutionRequest &request, uint32_t symbol,
                            uint16_t kind) -> const agent_abi::BindingRecord * {
        const agent_abi::BindingRecord *found = nullptr;
        for (const auto &row : request.hostBindings) {
            if (row.symbol_id != symbol)
                continue;
            if (found || row.kind != kind || row.flags != exactBindingFlags(kind) || !row.bytes)
                return nullptr;
            found = &row;
        }
        return found;
    };
    const auto interval = [&](uint64_t address, uint64_t bytes, BatchIntervalKind kind,
                               uint32_t ordinal, uint32_t symbol) {
        if (address == 0 || bytes == 0 || address > UINT64_MAX - bytes)
            return false;
        interval_table[interval_count++] = {address, address + bytes, kind, ordinal, symbol, {}};
        return true;
    };
    for (size_t ordinal = 0; ordinal < count; ++ordinal) {
        const auto &input = members[ordinal];
        const auto &slot = input.owned_slot;
        const auto &map = program.transport.agent_instance_member_bindings[
            instance.member_binding_first + ordinal];
        if (!input.request)
            return invalid("frozen member has no request");
        const auto &request = *input.request;
        if (request.requestId == 0 || request.programId != instance.request_program_id ||
                request.profileId != instance.request_profile_id ||
                static_cast<uint32_t>(input.path) != instance.path_kind ||
                map.instance_profile_id != instance.instance_profile_id ||
                map.member_ordinal != ordinal ||
                map.expected_logical_source_rank != input.logical_source_rank)
            return invalid("frozen member identity or rank differs from the concrete profile");
        for (size_t previous = 0; previous < ordinal; ++previous)
            if (members[previous].request->requestId == request.requestId)
                return invalid("frozen member request identity is duplicated");
        if (uses_kv && (slot.request_id != request.requestId || slot.session_id != request.sessionId ||
                slot.kv_handle != request.kvHandle || slot.generation != request.generation))
            return invalid("frozen KV owned slot belongs to another request");
        if (!interval(request.inputAddress, request.inputBytes, BatchIntervalKind::Input,
                      ordinal, request_profile->primary_input_symbol_id) ||
                !interval(request.outputAddress, request.outputCapacityBytes, BatchIntervalKind::Output,
                          ordinal, request_profile->primary_output_symbol_id) ||
                !interval(request.metadataAddress, request.metadataCapacityBytes,
                          BatchIntervalKind::Metadata, ordinal, 0))
            return invalid("frozen member physical range is empty or overflows");
        if (uses_kv) {
            if (!interval(slot.address, slot.bytes, BatchIntervalKind::Kv,
                          ordinal, request_profile->primary_kv_symbol_id))
                return invalid("frozen KV range is empty or overflows");
            interval_table[interval_count - 1].owned_slot = slot;
        } else if (input.kv_view || slot.request_id || slot.bytes) {
            return invalid("prefill-only member supplied KV ownership");
        }
        for (const auto &requirement : program.transport.agent_request_binding_requirements) {
            if (requirement.request_program_id != instance.request_program_id ||
                    requirement.request_profile_id != instance.request_profile_id ||
                    requirement.binding_kind != agent_abi::kBindingKindREQUEST_WORKSPACE)
                continue;
            const auto *workspace = binding(request, requirement.symbol_id, requirement.binding_kind);
            if (!workspace || workspace->bytes != requestWorkspaceBytes(program, request.programId, request.profileId) ||
                    !interval(workspace->address, workspace->bytes, BatchIntervalKind::Workspace, ordinal, workspace->symbol_id))
                return invalid("frozen workspace differs from its request capacity");
        }
        for (const auto &mapping : program.transport.agent_instance_tensor_bindings) {
            if (mapping.instance_profile_id != instance.instance_profile_id || mapping.member_ordinal != ordinal)
                continue;
            const auto symbol = mapping.static_symbol_id;
            const auto primary = mapping.request_symbol_id;
            const auto kind = mapping.binding_kind;
            const auto source = context.symbolBinding(instance.mesh_entrypoint_id, instance.mesh_profile_id, symbol);
            const auto *wire = binding(request, primary, kind);
            if (!source || wire == nullptr)
                return invalid("frozen role has no static slot or primary binding");
            if (mapping.offset_bytes > wire->bytes || mapping.span_bytes > wire->bytes - mapping.offset_bytes ||
                    wire->address > UINT64_MAX - mapping.offset_bytes)
                return invalid("frozen tensor subrange exceeds its request binding");
            FrozenMemberBinding row;
            row.ordinal = ordinal;
            row.request_id = request.requestId;
            row.generation = request.generation;
            row.symbol_id = symbol;
            row.kind = kind;
            row.slot_id = source->slot->slot_id;
            row.capacity_bytes = mapping.span_bytes;
            row.address = wire->address + mapping.offset_bytes;
            if (kind == agent_abi::kBindingKindHOST_INPUT) {
                if (wire->address != request.inputAddress || wire->bytes != request.inputBytes)
                    return invalid("frozen input disagrees with its request");
                row.read = {0, mapping.span_bytes};
            } else if (kind == agent_abi::kBindingKindHOST_OUTPUT) {
                if (wire->address != request.outputAddress || wire->bytes != request.outputCapacityBytes)
                    return invalid("frozen output disagrees with its request");
                row.write = {0, mapping.span_bytes};
            } else if (kind == agent_abi::kBindingKindREQUEST_WORKSPACE) {
                if (instance.phase == mesh_abi::kPhasePREFILL)
                    row.write = {0, mapping.span_bytes};
                else if (instance.phase == mesh_abi::kPhasePUBLISH)
                    row.read = {0, mapping.span_bytes};
                else
                    return invalid("workspace has no admitted phase access");
            } else {
                if (!input.kv_view || wire->address != 0 || wire->bytes != slot.bytes)
                    return invalid("frozen KV operand has no pinned view");
                const auto &view = *input.kv_view;
                const uint64_t before = uint64_t(instance.kv_tokens_before) *
                    request_profile->kv_bytes_per_token;
                if (view.request_id != request.requestId || view.member_ordinal != ordinal ||
                        view.session_id != slot.session_id || view.kv_handle != slot.kv_handle ||
                        view.generation != slot.generation || view.contract_digest != request.contractDigest ||
                        view.slot_base != slot.address || view.slot_bytes != slot.bytes ||
                        view.valid_bytes_at_arm != before || before > row.capacity_bytes ||
                        instance.kv_write_bytes_per_member > row.capacity_bytes - before)
                    return invalid("frozen KV view disagrees with its owner or phase window");
                row.address = view.slot_base;
                row.read = {0, view.valid_bytes_at_arm};
                row.write = {before, instance.kv_write_bytes_per_member};
                row.kv_view = view;
            }
            const uint64_t allocation_bytes = kind == agent_abi::kBindingKindKV_EXTERNAL ?
                slot.bytes : row.capacity_bytes;
            if (!appendSlot(*source, row.address, allocation_bytes))
                return invalid("frozen role address is outside its admitted region");
            member_table[member_count++] = row;
        }
        if (map.static_kv_symbol_id == 0 && input.kv_view)
            return invalid("phase without a KV role supplied an operand view");
    }
    for (const auto &requirement : program.transport.agent_request_binding_requirements) {
        if (requirement.request_program_id != instance.request_program_id ||
                requirement.request_profile_id != instance.request_profile_id ||
                requirement.binding_kind != agent_abi::kBindingKindWEIGHT_EXTERNAL)
            continue;
        const auto source = context.symbolBinding(instance.mesh_entrypoint_id, instance.mesh_profile_id, requirement.symbol_id);
        const auto *first = binding(*members[0].request, requirement.symbol_id,
                                   agent_abi::kBindingKindWEIGHT_EXTERNAL);
        if (!first)
            return invalid("frozen batch weight source is missing");
        for (size_t ordinal = 1; ordinal < count; ++ordinal) {
            const auto *other = binding(*members[ordinal].request, requirement.symbol_id,
                                        agent_abi::kBindingKindWEIGHT_EXTERNAL);
            if (!other || other->address != first->address || other->bytes != first->bytes ||
                    other->flags != first->flags)
                return invalid("frozen members disagree on the immutable weight binding");
        }
        weight_table[weight_count++] = {*first, source ? uint32_t(source->slot->slot_id) : 0, first->bytes};
        if (source && !appendSlot(*source, first->address, first->bytes))
            return invalid("frozen weight address is outside its admitted region");
        if (!interval(first->address, first->bytes, BatchIntervalKind::Weight,
                      UINT32_MAX, first->symbol_id))
            return invalid("frozen weight range is empty or overflows");
    }
    std::sort(interval_table.get(), interval_table.get() + interval_count,
        [](const auto &left, const auto &right) {
            return std::tie(left.address, left.end, left.kind, left.ordinal, left.symbol_id) <
                   std::tie(right.address, right.end, right.kind, right.ordinal, right.symbol_id);
        });
    for (size_t i = 1; i < interval_count; ++i)
        if (interval_table[i].address < interval_table[i - 1].end)
            return invalid("frozen physical ranges alias without an immutable declaration");
    uint8_t qos = 0;
    for (size_t i = 0; i < count; ++i)
        qos = std::max(qos, members[i].request->qos);
    execution_binding.reset(count == 1 ? members[0].request->requestId : 0,
                            count == 1 ? members[0].request->generation : 0,
                            instance.instance_profile_id, qos);
    std::shared_ptr<const MeshProgramInvocation> invocation;
    MeshLoadError error;
    if (!prepareProgramInvocation(admission, instance.mesh_entrypoint_id, instance.mesh_profile_id,
                                  dispatch, invocation, error)) {
        reason = error.code + ": " + error.message;
        release();
        return FreezeResult::Invalid;
    }
    if (!execution_binding.installInvocation(std::move(invocation)))
        return invalid("frozen batch cannot install its prepared invocation");
    for (size_t i = 0; i < member_count; ++i) {
        const auto &row = member_table[i];
        if (!execution_binding.bindSlot(row.slot_id, row.read, row.write,
                ServingMemberIdentity{row.request_id, row.generation, row.ordinal}))
            return invalid("frozen static member slots overlap or overflow");
    }
    for (size_t i = 0; i < weight_count; ++i) {
        const auto &row = weight_table[i];
        if (row.slot_id && !execution_binding.bindSlot(row.slot_id, {0, row.binding.bytes}, {}))
            return invalid("frozen static weight slot conflicts with a member");
    }
    std::vector<ServingKvDescriptorIdentity> kv_descriptors;
    for (const auto &range : view.ranges) {
        for (const auto command_id : range.command_ids) {
            for (const auto &descriptor : program.transport.dma_descriptors) {
                if (descriptor.command_id != command_id ||
                        (descriptor.kind != mesh_abi::kDmaKindSTORE &&
                         descriptor.kind != mesh_abi::kDmaKindLOAD))
                    continue;
                const bool write = descriptor.kind == mesh_abi::kDmaKindSTORE;
                const auto *endpoint = admission->dma().descriptorBinding(descriptor.descriptor_id, !write);
                const auto access = execution_binding.resolveDescriptorRows(descriptor.descriptor_id, !write);
                if (!access || !access->member)
                    continue;
                for (size_t i = 0; i < member_count; ++i) {
                    const auto &row = member_table[i];
                    if (row.kind == agent_abi::kBindingKindKV_EXTERNAL &&
                            row.ordinal == access->member->ordinal &&
                            endpoint && row.slot_id == endpoint->slotId()) {
                        kv_descriptors.push_back({descriptor.descriptor_id,
                            members[row.ordinal].phase_ordinal, *access->member});
                        break;
                    }
                }
            }
        }
    }
    execution_binding.bindKvDescriptors(std::move(kv_descriptors));
    if (instance.phase == mesh_abi::kPhasePUBLISH) {
        std::vector<PublishDescriptorPlan> plans;
        for (const auto &range : view.ranges) {
            for (const auto command_id : range.command_ids) {
                for (const auto &descriptor : program.transport.dma_descriptors) {
                    if (descriptor.command_id != command_id ||
                            descriptor.kind != mesh_abi::kDmaKindSTORE)
                        continue;
                    for (size_t member = 0; member < member_count; ++member) {
                        const auto &row = member_table[member];
                        const auto *endpoint = admission->dma().descriptorBinding(descriptor.descriptor_id, false);
                        const auto access = execution_binding.resolveDescriptorRows(descriptor.descriptor_id, false);
                        if (row.kind != agent_abi::kBindingKindHOST_OUTPUT || !endpoint ||
                            endpoint->slotId() != row.slot_id || !access || !access->member ||
                            access->member->ordinal != row.ordinal)
                            continue;
                        if (descriptor.rows != 1 && descriptor.dst_stride_bytes != descriptor.row_bytes)
                            return invalid("PUBLISH descriptor rows do not form a contiguous output interval");
                        plans.push_back({descriptor.descriptor_id, row.request_id,
                            endpoint->begin(), descriptor.useful_bytes});
                    }
                }
            }
        }
        if (plans.empty())
            return invalid("PUBLISH profile has no member output descriptors");
        try {
            execution_binding.bindOutputLedger(std::make_shared<PublishSegmentLedger>(std::move(plans)));
        } catch (const std::invalid_argument &) {
            return invalid("PUBLISH profile has a hole, overlap or duplicate output descriptor");
        }
    }
    published = true;
    reason.clear();
    return FreezeResult::Frozen;
}
}

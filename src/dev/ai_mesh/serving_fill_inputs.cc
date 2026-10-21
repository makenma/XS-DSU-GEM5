#include "dev/ai_mesh/serving_fill_inputs.hh"

#include <algorithm>
#include <set>

namespace gem5
{
namespace ai_mesh
{

std::optional<ServingMemberIdentity>
servingFillMember(const DecodedProgram &program, const ProfileExecutionView &view,
                  const ServingInstanceBinding &binding, uint32_t producer_command_id)
{
    const auto producer = std::find_if(program.transport.commands.begin(), program.transport.commands.end(),
        [&](const auto &command) { return command.command_id == producer_command_id; });
    if (producer == program.transport.commands.end() || !producer->operand_count)
        return std::nullopt;
    const uint32_t allocation = program.transport.command_operands.at(
        producer->operand_begin + producer->operand_count - 1).allocation_id;
    std::set<uint32_t> selected;
    for (const auto &range : view.ranges)
        selected.insert(range.command_ids.begin(), range.command_ids.end());
    if (!selected.count(producer_command_id))
        return std::nullopt;
    std::optional<ServingMemberIdentity> owner;
    for (const auto &command : program.transport.commands) {
        if (!selected.count(command.command_id) || !command.operand_count ||
            program.transport.command_operands.at(command.operand_begin).allocation_id != allocation)
            continue;
        for (const auto &descriptor : program.transport.dma_descriptors) {
            if (descriptor.command_id != command.command_id ||
                descriptor.kind != mesh_abi::kDmaKindSTORE)
                continue;
            const auto access = binding.resolveDescriptorRows(descriptor.descriptor_id, false);
            if (!access || !access->member)
                return std::nullopt;
            if (owner && (owner->request_id != access->member->request_id ||
                owner->generation != access->member->generation ||
                owner->ordinal != access->member->ordinal))
                return std::nullopt;
            owner = access->member;
        }
    }
    return owner;
}

std::optional<ServingFillInputs>
ServingFillInputs::freeze(
    const DecodedProgram &program, const ProfileExecutionView &view,
    InstanceGeneration instance, const ServingInstanceBinding &owner,
    const std::vector<ServingFillBinding> &bindings,
    std::string &reason)
{
    ServingFillInputs result;
    std::set<uint32_t> commands;
    for (const auto &range : view.ranges)
        commands.insert(range.command_ids.begin(), range.command_ids.end());
    for (const auto &binding : bindings) {
        const auto profile = std::find_if(
            program.transport.agent_instance_profiles.begin(),
            program.transport.agent_instance_profiles.end(), [&](const auto &item) {
                return item.instance_profile_id == binding.instance_profile_id;
            });
        const auto command = std::find_if(
            program.transport.commands.begin(), program.transport.commands.end(), [&](const auto &item) {
                return item.command_id == binding.producer_command_id;
            });
        const auto allocation = std::find_if(
            program.transport.allocations.begin(), program.transport.allocations.end(),
            [&](const auto &item) { return item.allocation_id == binding.allocation_id; });
        const auto member = owner.requestId() != 0
            ? std::optional<ServingMemberIdentity>{{owner.requestId(), owner.requestGeneration(), 0}}
            : servingFillMember(program, view, owner, binding.producer_command_id);
        if (instance.value() == 0 || binding.request_id == 0 || !member ||
                binding.request_id != member->request_id ||
                binding.request_generation != member->generation ||
                binding.member_ordinal != member->ordinal ||
                (owner.instanceProfileId() != 0 &&
                 binding.instance_profile_id != owner.instanceProfileId()) ||
                profile == program.transport.agent_instance_profiles.end() ||
                profile->mesh_profile_id != view.profile_id ||
                profile->mesh_entrypoint_id != view.entrypoint_id ||
                binding.member_ordinal >= profile->member_count ||
                command == program.transport.commands.end() ||
                commands.count(binding.producer_command_id) == 0 ||
                command->opcode != mesh_abi::kOpcodeDMA_FILL ||
                allocation == program.transport.allocations.end() ||
                command->operand_count == 0 ||
                program.transport.command_operands.at(command->operand_begin + command->operand_count - 1).
                    allocation_id != binding.allocation_id) {
            reason = "E_BINDING_ROLE: runtime fill producer identity mismatch";
            return std::nullopt;
        }
        const auto descriptor = std::find_if(
            program.transport.dma_descriptors.begin(), program.transport.dma_descriptors.end(),
            [&](const auto &item) { return item.command_id == command->command_id; });
        if (descriptor == program.transport.dma_descriptors.end() ||
                descriptor->kind != mesh_abi::kDmaKindLOCAL_FILL ||
                descriptor->useful_bytes != binding.content.size() ||
                binding.content.empty()) {
            reason = "E_BINDING_ROLE: runtime fill content range mismatch";
            return std::nullopt;
        }
        const auto &attr = program.transport.op_attrs.at(command->attr_index - 1);
        if (attr.as<mesh_abi::FillV1>()->pattern != mesh_abi::kDmaFillRuntimeBoundSentinel) {
            reason = "E_BINDING_ROLE: runtime content requires a sentinel producer";
            return std::nullopt;
        }
        if (!result.producers.emplace(staticProgramObject(instance,
                mesh_abi::MeshObjectKind::COMMAND, command->command_id), binding).second) {
            reason = "E_BINDING_ROLE: duplicate runtime fill producer";
            return std::nullopt;
        }
    }
    for (const auto &record : program.transport.agent_publish_surrogate_bindings) {
        if ((owner.instanceProfileId() != 0 &&
             record.instance_profile_id != owner.instanceProfileId()) ||
            commands.count(record.producer_command_id) == 0)
            continue;
        const auto found = result.producers.find(staticProgramObject(instance,
            mesh_abi::MeshObjectKind::COMMAND, record.producer_command_id));
        if (found == result.producers.end()) {
            reason = "E_BINDING_ROLE: missing runtime PUBLISH fill binding";
            return std::nullopt;
        }
        const auto &binding = found->second;
        if (binding.instance_profile_id != record.instance_profile_id ||
                binding.member_ordinal != record.member_ordinal ||
                binding.allocation_id != record.allocation_id) {
            reason = "E_BINDING_ROLE: runtime PUBLISH member or allocation mismatch";
            return std::nullopt;
        }
    }
    for (const auto &command : program.transport.commands) {
        if (commands.count(command.command_id) == 0 ||
                command.opcode != mesh_abi::kOpcodeDMA_FILL)
            continue;
        const auto &attr = program.transport.op_attrs.at(command.attr_index - 1);
        if (attr.as<mesh_abi::FillV1>()->pattern ==
                mesh_abi::kDmaFillRuntimeBoundSentinel &&
                result.producers.count(staticProgramObject(instance,
                    mesh_abi::MeshObjectKind::COMMAND, command.command_id)) == 0) {
            reason = "E_BINDING_ROLE: missing runtime sentinel fill binding";
            return std::nullopt;
        }
    }
    return result;
}

const std::vector<uint8_t> *
ServingFillInputs::contentFor(const RuntimeObjectKey &command) const
{
    const auto found = producers.find(command);
    return found == producers.end() ? nullptr : &found->second.content;
}

}
}

#include "dev/ai_mesh/serving_fill_inputs.hh"

#include <algorithm>
#include <set>

namespace gem5
{
namespace ai_mesh
{

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
        for (uint32_t index = range.command_begin;
             index < range.command_begin + range.command_count; ++index)
            commands.insert(program.commands.at(index).command_id);
    for (const auto &binding : bindings) {
        const auto profile = std::find_if(
            program.agent_instance_profiles.begin(),
            program.agent_instance_profiles.end(), [&](const auto &item) {
                return item.instance_profile_id == binding.instance_profile_id;
            });
        const auto command = std::find_if(
            program.commands.begin(), program.commands.end(), [&](const auto &item) {
                return item.command_id == binding.producer_command_id;
            });
        const auto allocation = std::find_if(
            program.allocations.begin(), program.allocations.end(),
            [&](const auto &item) { return item.allocation_id == binding.allocation_id; });
        if (instance.value() == 0 || binding.request_id == 0 ||
                binding.request_id != owner.requestId() ||
                binding.request_generation != owner.requestGeneration() ||
                profile == program.agent_instance_profiles.end() ||
                profile->mesh_profile_id != view.profile_id ||
                binding.member_ordinal >= profile->member_count ||
                command == program.commands.end() ||
                commands.count(binding.producer_command_id) == 0 ||
                command->opcode != mesh_abi::kOpcodeDMA_FILL ||
                allocation == program.allocations.end() ||
                command->operand_count == 0 ||
                program.operands.at(command->operand_begin + command->operand_count - 1).
                    allocation_id != binding.allocation_id) {
            reason = "E_BINDING_ROLE: runtime fill producer identity mismatch";
            return std::nullopt;
        }
        const auto descriptor = std::find_if(
            program.descriptors.begin(), program.descriptors.end(),
            [&](const auto &item) { return item.command_id == command->command_id; });
        if (descriptor == program.descriptors.end() ||
                descriptor->kind != mesh_abi::kDmaKindLOCAL_FILL ||
                descriptor->useful_bytes != binding.content.size() ||
                binding.content.empty()) {
            reason = "E_BINDING_ROLE: runtime fill content range mismatch";
            return std::nullopt;
        }
        const auto &attr = program.attrs.at(command->attr_index - 1);
        if (attr.as<mesh_abi::FillV1>().pattern != mesh_abi::kDmaFillRuntimeBoundSentinel) {
            reason = "E_BINDING_ROLE: runtime content requires a sentinel producer";
            return std::nullopt;
        }
        if (!result.producers.emplace(staticProgramObject(instance,
                mesh_abi::MeshObjectKind::COMMAND, command->command_id), binding).second) {
            reason = "E_BINDING_ROLE: duplicate runtime fill producer";
            return std::nullopt;
        }
    }
    for (const auto &record : program.agent_publish_surrogate_bindings) {
        if (commands.count(record.producer_command_id) == 0)
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
    for (const auto &command : program.commands) {
        if (commands.count(command.command_id) == 0 ||
                command.opcode != mesh_abi::kOpcodeDMA_FILL)
            continue;
        const auto &attr = program.attrs.at(command.attr_index - 1);
        if (attr.as<mesh_abi::FillV1>().pattern ==
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

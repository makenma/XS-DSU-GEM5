#include "dev/ai_mesh/mesh_serving_tensor_bindings.hh"

#include <algorithm>
#include <set>
#include <tuple>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5::ai_mesh
{

uint64_t
requestWorkspaceBytes(const DecodedProgram &program, uint16_t programId, uint16_t profileId)
{
    std::set<uint32_t> instances;
    for (const auto &row : program.transport.agent_instance_profiles)
        if (row.request_program_id == programId && row.request_profile_id == profileId)
            instances.insert(row.instance_profile_id);
    uint64_t bytes = 0;
    for (const auto &row : program.transport.agent_instance_tensor_bindings)
        if (instances.count(row.instance_profile_id) && row.binding_kind == agent_abi::kBindingKindREQUEST_WORKSPACE)
            bytes = std::max(bytes, row.offset_bytes + row.span_bytes);
    return bytes;
}

bool
verifyServingTensorBindings(const DecodedProgram &program,
    const ProgramSemanticContext &context, MeshLoadError &error)
{
    using namespace mesh_abi;
    using namespace agent_abi;
    const auto reject = [&](const char *message, const char *code = "E_BINDING_ROLE") {
        error = {code, message};
        return false;
    };
    using RequestKey = std::pair<uint16_t, uint16_t>;
    std::map<uint32_t, const AgentInstanceProfile *> instances;
    std::map<RequestKey, const AgentRequestProfile *> requests;
    std::map<uint32_t, std::vector<const AgentInstanceTensorBinding *>> rows;
    for (const auto &instance : program.transport.agent_instance_profiles)
        if (!instances.emplace(instance.instance_profile_id, &instance).second)
            return reject("tensor mapping instance identity is duplicated", "E_ABI_DUPLICATE");
    for (const auto &request : program.transport.agent_request_profiles)
        if (!requests.emplace(RequestKey{request.program_id, request.profile_id}, &request).second)
            return reject("tensor mapping request identity is duplicated", "E_ABI_DUPLICATE");
    for (const auto &[key, request] : requests) {
        size_t count = 0;
        for (const auto &row : program.transport.agent_request_binding_requirements)
            if (RequestKey{row.request_program_id, row.request_profile_id} == key &&
                    row.binding_kind == kBindingKindREQUEST_WORKSPACE) {
                if (++count > 1 || row.binding_flags != (kBindingFlagsREAD | kBindingFlagsWRITE))
                    return reject("workspace requires one ephemeral request range");
            }
    }
    std::set<std::pair<uint32_t, uint64_t>> occupied;
    std::optional<std::tuple<uint32_t, uint16_t, uint32_t>> previous;
    for (const auto &row : program.transport.agent_instance_tensor_bindings) {
        const auto found = instances.find(row.instance_profile_id);
        if (found == instances.end() || row.member_ordinal >= found->second->member_count)
            return reject("tensor mapping has no instance member");
        const auto &instance = *found->second;
        const RequestKey request_key{instance.request_program_id, instance.request_profile_id};
        const auto request_it = requests.find(request_key);
        if (request_it == requests.end())
            return reject("tensor mapping has no request profile");
        const auto &request = *request_it->second;
        uint32_t root = 0, instance_root = 0;
        uint16_t role = 0;
        std::optional<uint64_t> capacity;
        switch (row.binding_kind) {
          case kBindingKindHOST_INPUT:
            root = request.primary_input_symbol_id;
            instance_root = instance.primary_input_symbol_id;
            role = kTensorRoleINPUT;
            capacity = request.input_binding_bytes;
            break;
          case kBindingKindHOST_OUTPUT:
            root = request.primary_output_symbol_id;
            instance_root = instance.primary_output_symbol_id;
            role = kTensorRoleOUTPUT;
            capacity = request.host_output_bytes;
            break;
          case kBindingKindREQUEST_WORKSPACE:
            root = instance_root = row.request_symbol_id;
            break;
          case kBindingKindKV_EXTERNAL:
            root = request.primary_kv_symbol_id;
            instance_root = instance.primary_kv_symbol_id;
            role = kTensorRoleKV_CACHE;
            if (row.offset_bytes)
                return reject("KV tensor mapping must start at its owned slot");
            break;
          default:
            return reject("tensor mapping has no request role");
        }
        if (!row.request_symbol_id || row.request_symbol_id != root || root != instance_root)
            return reject("tensor mapping request root differs from its member role");
        size_t requirements = 0;
        for (const auto &requirement : program.transport.agent_request_binding_requirements)
            if (RequestKey{requirement.request_program_id, requirement.request_profile_id} == request_key &&
                    requirement.symbol_id == root) {
                if (requirement.binding_kind != row.binding_kind)
                    return reject("tensor mapping root has a different request kind");
                ++requirements;
            }
        if (requirements != 1)
            return reject("tensor mapping root has no unique request requirement");
        if (!row.static_symbol_id || row.static_symbol_id > program.transport.strings.size())
            return reject("tensor mapping static symbol is missing");
        const auto binding = context.symbolBinding(instance.mesh_entrypoint_id,
            instance.mesh_profile_id, row.static_symbol_id);
        if (!binding)
            return reject("binding symbol has no owner in the selected variant", "E_RELOCATION");
        const auto tensor_role = uint16_t(binding->logical_tensor->role);
        if (row.binding_kind == kBindingKindREQUEST_WORKSPACE) {
            if ((tensor_role != kTensorRoleOUTPUT && tensor_role != kTensorRoleACTIVATION && tensor_role != kTensorRoleSTATE) ||
                    binding->object->memory_space != semantic_abi::MemorySpace::HBM ||
                    binding->slot->access != semantic_abi::Access::READ_WRITE ||
                    binding->reference->access != semantic_abi::Access::READ_WRITE)
                return reject("workspace must bind mutable HBM storage");
        } else if (tensor_role != role) {
            return reject("tensor mapping role differs from its static slot");
        }
        if (!occupied.emplace(row.instance_profile_id, binding->slot->slot_id).second)
            return reject("member tensor slots alias within the instance");
        const auto alignment = binding->slot->required_allocation_alignment_bytes;
        if (row.offset_bytes > UINT64_MAX - row.span_bytes)
            return reject("member tensor subrange overflows", "E_ABI_OVERFLOW");
        if (!alignment || row.offset_bytes % alignment ||
                row.span_bytes < binding->slot->required_allocation_bytes ||
                (capacity && row.offset_bytes + row.span_bytes > *capacity))
            return reject("tensor mapping subrange violates slot or request capacity");
        const auto key = std::make_tuple(row.instance_profile_id, row.member_ordinal, row.static_symbol_id);
        if (previous && key <= *previous)
            return reject("tensor mappings must be ordered by instance, member and static symbol", "E_ABI_ORDER");
        previous = key;
        rows[row.instance_profile_id].push_back(&row);
    }
    for (const auto &[identity, pointer] : instances) {
        const auto &instance = *pointer;
        const auto &mapped = rows[identity];
        std::set<uint64_t> expected, actual;
        for (const auto *row : mapped)
            actual.insert(context.symbolBinding(instance.mesh_entrypoint_id,
                instance.mesh_profile_id, row->static_symbol_id)->slot->slot_id);
        for (const auto &[slot_id, slot] : context.bindingSlots()) {
            const auto *owner = context.owner("binding_slots", slot_id);
            if (!owner || owner->entrypoint_id != instance.mesh_entrypoint_id ||
                    owner->profile_id != instance.mesh_profile_id)
                continue;
            bool immutable = false;
            for (const auto &requirement : program.transport.agent_request_binding_requirements) {
                if (requirement.request_program_id != instance.request_program_id ||
                        requirement.request_profile_id != instance.request_profile_id ||
                        requirement.binding_kind != kBindingKindWEIGHT_EXTERNAL)
                    continue;
                const auto binding = context.symbolBinding(instance.mesh_entrypoint_id,
                    instance.mesh_profile_id, requirement.symbol_id);
                if (!binding || binding->slot->slot_id != slot_id)
                    continue;
                const auto &tensor = *binding->logical_tensor;
                if ((tensor.role != semantic_abi::TensorRole::WEIGHT &&
                        tensor.role != semantic_abi::TensorRole::CONSTANT &&
                        tensor.role != semantic_abi::TensorRole::STATE) ||
                        tensor.storage_class != semantic_abi::StorageClass::EXTERNAL ||
                        tensor.access != semantic_abi::Access::READ_ONLY ||
                        !tensor.content_sha256.string_id ||
                        binding->object->memory_space != semantic_abi::MemorySpace::HBM ||
                        binding->slot->memory_space != semantic_abi::MemorySpace::HBM ||
                        binding->slot->access != semantic_abi::Access::READ_ONLY ||
                        binding->reference->access != semantic_abi::Access::READ_ONLY)
                    return reject("persistent model requirement has no admitted immutable HBM content");
                immutable = true;
            }
            if (!immutable)
                expected.insert(slot_id);
        }
        if (actual != expected)
            return reject("tensor mappings do not exactly cover mutable and input variant slots");
        const size_t first = instance.member_binding_first, count = instance.member_binding_count;
        if (count != instance.member_count || first > program.transport.agent_instance_member_bindings.size() ||
                count > program.transport.agent_instance_member_bindings.size() - first)
            return reject("tensor mapping member summaries are incomplete");
        for (size_t ordinal = 0; ordinal < count; ++ordinal) {
            const auto &member = program.transport.agent_instance_member_bindings[first + ordinal];
            if (member.instance_profile_id != identity || member.member_ordinal != ordinal)
                return reject("tensor mapping member summary has a foreign identity");
            const std::pair<uint16_t, uint32_t> summaries[] = {
                {kBindingKindHOST_INPUT, member.static_input_symbol_id},
                {kBindingKindHOST_OUTPUT, member.static_output_symbol_id},
                {kBindingKindKV_EXTERNAL, member.static_kv_symbol_id},
                {kBindingKindREQUEST_WORKSPACE, 0}};
            for (const auto &[kind, summary] : summaries) {
                uint32_t primary = 0;
                std::vector<std::pair<uint64_t, uint64_t>> intervals;
                for (const auto *row : mapped) {
                    if (row->member_ordinal != ordinal || row->binding_kind != kind)
                        continue;
                    primary = primary ? std::min(primary, row->static_symbol_id) : row->static_symbol_id;
                    intervals.emplace_back(row->offset_bytes, row->offset_bytes + row->span_bytes);
                }
                if (kind != kBindingKindREQUEST_WORKSPACE && primary != summary)
                    return reject("member primary summary differs from its tensor mappings");
                std::sort(intervals.begin(), intervals.end());
                for (size_t i = 1; i < intervals.size(); ++i)
                    if (intervals[i - 1].second > intervals[i].first)
                        return reject("member tensor subranges overlap");
            }
        }
    }
    return true;
}

}

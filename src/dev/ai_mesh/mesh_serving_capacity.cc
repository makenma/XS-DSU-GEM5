#include "dev/ai_mesh/mesh_serving_capacity.hh"

#include <algorithm>
#include <utility>
#include <set>
#include <tuple>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5::ai_mesh
{
namespace
{
using namespace mesh_abi;
bool fail(const char *code, const std::string &message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

}

bool servingCapacityFor(const DecodedProgram &program,
                        const AgentInstanceProfile &instance,
                        ServingCapacityRequirement &out, MeshLoadError &error)
{
    uint64_t weights = 0, workspaces = 0;
    for (const auto &requirement :
         program.transport.agent_request_binding_requirements) {
        if (requirement.request_program_id != instance.request_program_id ||
            requirement.request_profile_id != instance.request_profile_id)
            continue;
        if (requirement.binding_kind ==
            ::gem5::ai_mesh::agent_abi::kBindingKindWEIGHT_EXTERNAL)
            weights++;
        if (requirement.binding_kind == agent_abi::kBindingKindREQUEST_WORKSPACE)
            ++workspaces;
    }
    uint64_t bindings = 0;
    for (const auto &row : program.transport.agent_instance_tensor_bindings)
        bindings += row.instance_profile_id == instance.instance_profile_id;
    const auto request = std::find_if(program.transport.agent_request_profiles.begin(),
        program.transport.agent_request_profiles.end(), [&](const auto &row) {
            return row.program_id == instance.request_program_id && row.profile_id == instance.request_profile_id;
        });
    if (!bindings || request == program.transport.agent_request_profiles.end())
        return fail("E_CAPACITY_PLAN", "instance has no tensor mappings or request profile", error);
    const bool uses_kv = request->flags & kAgentRequestFlagsHAS_KV;
    const uint64_t members = instance.member_count;
    out.batch_weight_binding_entries = std::max(
        out.batch_weight_binding_entries, weights);
    out.instance_member_binding_entries = std::max(
        out.instance_member_binding_entries, bindings);
    out.batch_interval_entries = std::max(
        out.batch_interval_entries, (3 + uses_kv + workspaces) * members + weights);
    return true;
}

ServingCacheCapacityRequirement servingStrictCacheCapacity(const DecodedProgram &program,
    const ProfileExecutionView &view, uint16_t core)
{
    std::set<uint32_t> commands;
    for (const auto &range : view.ranges)
        commands.insert(range.command_ids.begin(), range.command_ids.end());
    std::set<uint32_t> layers;
    for (const auto &region : program.transport.moe_dynamic_regions)
        if (commands.count(region.insert_after_command_id))
            layers.insert(region.layer_id);
    using Weight = std::tuple<uint32_t, uint64_t, uint64_t>;
    std::set<Weight> lines;
    std::set<std::pair<uint32_t, Weight>> occurrences;
    for (const auto &expert : program.transport.moe_expert_specs)
        if (expert.core_id == core && layers.count(expert.layer_id)) {
            const Weight weight{expert.weight_symbol_id, expert.weight_region_offset, expert.weight_bytes};
            lines.insert(weight);
            occurrences.emplace(expert.layer_id, weight);
        }
    return {lines.size(), occurrences.size()};
}

bool servingCapacityRequired(const DecodedProgram &program,
                             ServingCapacityRequirement &out,
                             MeshLoadError &error)
{
    out = {};
    if ((program.header.required_features & kFeatureAgentServingV1) == 0)
        return true;
    if (program.transport.agent_instance_profiles.empty())
        return fail("E_CAPACITY_PLAN",
                    "serving program has no reachable profile", error);
    for (const auto &instance : program.transport.agent_instance_profiles)
        if (!servingCapacityFor(program, instance, out, error))
            return false;
    return true;
}

bool servingCapacityPlan(const DecodedProgram &program,
                         const ServingCapacityRequirement &configured,
                         ServingCapacityRequirement &required,
                         MeshLoadError &error)
{
    if (!servingCapacityRequired(program, required, error))
        return false;
    const auto fields = {
        std::make_pair("batch_weight_binding_entries_per_context",
                       &ServingCapacityRequirement::batch_weight_binding_entries),
        std::make_pair("instance_member_binding_entries_per_context",
                       &ServingCapacityRequirement::instance_member_binding_entries),
        std::make_pair("batch_interval_entries_per_context",
                       &ServingCapacityRequirement::batch_interval_entries)};
    for (const auto &[name, field] : fields)
        if (configured.*field < required.*field)
            return fail("E_CAPACITY_PLAN", std::string(name) + " configured " +
                        std::to_string(configured.*field) + " below required " +
                        std::to_string(required.*field), error);
    return true;
}

}

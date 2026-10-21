#include "dev/ai_mesh/mesh_program_loader.hh"

#include <set>

#include "base/logging.hh"

#include "dev/ai_mesh/mesh_moe_materializer.hh"
#include "dev/ai_mesh/mesh_serving_batch.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/mesh_weight_tags.hh"

namespace gem5::ai_mesh
{

void MeshProgramLoader::setServingMoeProvider(MoeProvider provider)
{
    fatal_if(load_ok || moe_provider || !provider,
             "E_MOE_PROVIDER: serving provider must be installed once before startup");
    moe_provider = std::move(provider);
}

bool MeshProgramLoader::prepareServingMoe(const mesh_abi::AgentInstanceProfile &profile,
    const std::vector<ServingBatchMember> &members, const ServingInstanceBinding &binding,
    std::shared_ptr<const MoeOverlaySet> &overlays, MoePreparationError &error)
{
    using namespace mesh_abi;
    auto prepared = std::make_shared<MoeOverlaySet>();
    const auto &invocation = binding.invocation();
    if (!invocation || &invocation->admission() != admission.get()) {
        error.set(0, 0, MaterializerCheckSite::BOUNDS, "MoE batch has no matching admitted invocation");
        return false;
    }
    std::set<uint32_t> commands;
    for (const auto &range : invocation->executionView().ranges)
        commands.insert(range.command_ids.begin(), range.command_ids.end());
    MoeMaterializeConfig config;
    config.program_digest = program_digest;
    config.program_semantic_digest = program().metadata.semantic_sha256;
    config.bus_bytes = runtime_arch.axi_data_bytes;
    config.cached = weight_policy == "cached";
    uint32_t failed_layer = 0;
    auto failed_site = MaterializerCheckSite::BOUNDS;
    const auto reject = [&](const char *message) {
        error.set(failed_layer, 0, failed_site, message);
        return false;
    };
    const auto request_profile = std::find_if(program().transport.agent_request_profiles.begin(),
        program().transport.agent_request_profiles.end(), [&](const auto &row) {
            return row.program_id == profile.request_program_id &&
                   row.profile_id == profile.request_profile_id;
        });
    if (request_profile == program().transport.agent_request_profiles.end())
        return reject("E_MOE_MATERIALIZATION_V: missing request profile");
    for (const auto &layer : program().transport.moe_layer_specs) {
        failed_layer = layer.layer_id;
        size_t reached = 0;
        for (const auto &region : program().transport.moe_dynamic_regions)
            if (region.layer_id == layer.layer_id && commands.count(region.insert_after_command_id))
                ++reached;
        if (!reached)
            continue;
        if (reached != layer.dynamic_region_count || profile.phase == kPhasePUBLISH)
            return reject("E_MOE_MATERIALIZATION_V: incomplete or non-compute layer invocation");
        std::vector<MoePopulationToken> tokens;
        std::vector<MoeMemberSlice> slices;
        for (size_t ordinal = 0; ordinal < members.size(); ++ordinal) {
            const auto &request = *members[ordinal].request;
            if (!ordinal)
                config.workload_digest = request.workloadDigest;
            else if (config.workload_digest != request.workloadDigest)
                return reject("E_MOE_MATERIALIZATION_V: workload identity differs across members");
            MoeMemberSlice slice;
            slice.ordinal = ordinal;
            slice.request_id = request.requestId;
            slice.token_count = profile.valid_tokens_per_member;
            slice.first_token = profile.phase == kPhasePREFILL ? profile.kv_tokens_before :
                profile.kv_tokens_before - request_profile->full_input_tokens;
            bool input = false, output = false;
            for (const auto &descriptor : program().transport.dma_descriptors) {
                if (!commands.count(descriptor.command_id) ||
                        (descriptor.kind != kDmaKindLOAD && descriptor.kind != kDmaKindSTORE))
                    continue;
                const bool store = descriptor.kind == kDmaKindSTORE;
                const auto &local = store ? descriptor.src : descriptor.dst;
                const auto access = binding.resolveDescriptorRows(descriptor.descriptor_id, !store);
                if (!access || !access->member || access->member->ordinal != ordinal)
                    continue;
                const auto command = admission->context().commands().find(descriptor.command_id);
                if (command == admission->context().commands().end())
                    return reject("member DMA has no admitted command");
                const auto *geometry = admission->geometry().access(command->second->source_op_id,
                    store ? GeometryAccessRole::Read : GeometryAccessRole::Write, 0);
                const auto *allocation = geometry ? admission->backing().local(geometry->object().object_id) : nullptr;
                const auto *region = runtime_arch.region(local.region_id);
                if (!allocation || !region)
                    return reject("member DMA has no admitted local allocation");
                bool overflow = false;
                const auto tile = region->tileBase(local.owner_core, overflow);
                const auto address = invocation->binding().endpointAddress(descriptor.descriptor_id, store);
                const uint64_t required = uint64_t(slice.token_count) *
                    (store ? layer.output_token_bytes : layer.token_bytes);
                if (overflow || allocation->offset_bytes > UINT64_MAX - tile ||
                    address < tile + allocation->offset_bytes)
                    return reject("local member slice precedes its allocation");
                const uint64_t offset = address - tile - allocation->offset_bytes;
                if (offset > allocation->size_bytes || required > allocation->size_bytes - offset ||
                    descriptor.useful_bytes < required)
                    return reject("local member slice exceeds its allocation");
                if (offset > UINT32_MAX)
                    return reject("E_MOE_MATERIALIZATION_V: member slice offset exceeds overlay ABI");
                if (store) {
                    if (output)
                        return reject("E_MOE_MATERIALIZATION_V: ambiguous member output slice");
                    slice.output_allocation = allocation->allocation_id;
                    slice.output_offset = offset;
                    output = true;
                } else {
                    if (input)
                        return reject("E_MOE_MATERIALIZATION_V: ambiguous member input slice");
                    slice.input_allocation = allocation->allocation_id;
                    slice.input_offset = offset;
                    slice.core = local.owner_core;
                    input = true;
                }
            }
            if (!input || !output)
                return reject("E_MOE_MATERIALIZATION_V: concrete member lacks a local operand slice");
            slices.push_back(slice);
            for (uint32_t i = 0; i < slice.token_count; ++i) {
                MoeSemanticToken uid{request.workloadPlanItemId, request.userId, request.taskSeq,
                    request.repairRound, uint8_t(profile.phase == kPhasePREFILL ? 1 : 2), 0, 0};
                if (profile.phase == kPhasePREFILL)
                    uid.token = slice.first_token + i;
                else
                    uid.sequence = slice.first_token + i;
                tokens.push_back({uid, uint32_t(ordinal), request.logicalSourceRank});
            }
        }
        config.weight_tags.assign(layer.expert_count, UINT32_MAX);
        for (const auto &expert : program().transport.moe_expert_specs) {
            if (expert.layer_id != layer.layer_id)
                continue;
            for (const auto &site : weightTagSitesOf(program(), expert.core_id))
                if (site.weight_symbol_id == expert.weight_symbol_id &&
                        site.weight_region_offset == expert.weight_region_offset &&
                        site.weight_bytes == expert.weight_bytes)
                    config.weight_tags[expert.expert_id] = site.weight_tag_index;
        }
        MoePopulation population;
        MoeOverlayGraph graph;
        std::string reason;
        const bool frozen = moe_provider ? population.freeze(layer, tokens,
            [&](const auto &canonical) {
                std::vector<std::array<uint64_t, 9>> rows;
                rows.reserve(canonical.size());
                for (const auto &token : canonical) {
                    const auto &uid = token.uid;
                    rows.push_back({uid.item, uid.user, uid.task, uid.repair, uid.phase,
                        uid.sequence, uid.token, token.member, token.source_rank});
                }
                return moe_provider(layer.layer_id, rows);
            }, reason) : population.freezeUniform(layer, tokens, config.workload_digest, moe_master_seed, reason);
        if (!frozen) {
            error.set(layer.layer_id, 0, MaterializerCheckSite::COUNT, reason);
            return false;
        }
        if (!materializeMoeOverlay(program(), layer, population, slices, config, graph, error))
            return false;
        prepared->emplace_back(layer.layer_id, std::move(graph));
    }
    overlays = std::move(prepared);
    return true;
}

}

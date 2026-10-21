#include "dev/ai_mesh/mesh_program_loader.hh"

#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"

#include <algorithm>
#include <fstream>
#include <filesystem>
#include <iterator>
#include <map>

#include "base/logging.hh"
#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/command_rom.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_weight_tags.hh"
#include "dev/ai_mesh/mesh_cache_replay.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mock_axi_transport.hh"
#include "dev/ai_mesh/peer_sram_aperture.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "params/MeshProgramLoader.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

MeshProgramLoader::MeshProgramLoader(const Params &p)
    : SimObject(p),
      program_file(p.program_file),
      arch_digest_hex(p.arch_digest),
      effective_arch_digest(p.effective_arch_digest),
      overlay_image_file(p.overlay_image),
      physical_memory(p.memory_endpoints),
      entrypoint_id(p.entrypoint_id),
      profile_id(p.profile_id),
      core_objects(p.cores.begin(), p.cores.end()),
      transport(p.transport),
      apertures(p.apertures.begin(), p.apertures.end()),
      sink(p.program_file),
      network(p.network),
      bridges(p.bridges.begin(), p.bridges.end())
{
    weight_runtime_image = p.weight_runtime_image;
    memory_file_image = p.memory_file_image;
    weight_image_digest = p.weight_image_digest;
    cache_state_replay = p.cache_state_replay;
    strict_replay = p.strict_replay_serial_batches;
    serving_capacity = {p.batch_weight_binding_entries_per_context,
                        p.instance_member_binding_entries_per_context,
                        p.batch_interval_entries_per_context};
    fatal_if(p.region_ids.size() != p.region_bases.size() ||
                 p.region_ids.size() != p.region_bytes.size() ||
                 p.region_ids.size() != p.region_tile_strides.size() ||
                 p.region_ids.size() != p.region_kinds.size() ||
                 p.region_ids.size() != p.region_tile_bytes.size(),
             "MeshProgramLoader: region parameter arrays differ in length");
    fatal_if(p.binding_slot_ids.size() != p.binding_region_ids.size() ||
                 p.binding_slot_ids.size() != p.binding_owner_cores.size() ||
                 p.binding_slot_ids.size() != p.binding_offsets.size() ||
                 p.binding_slot_ids.size() != p.binding_sizes.size() ||
                 p.binding_slot_ids.size() != p.binding_alignments.size() ||
                 p.binding_slot_ids.size() != p.binding_accesses.size(),
             "MeshProgramLoader: binding parameter arrays differ in length");
    fatal_if(p.fabric_target_names.size() != p.fabric_range_region_ids.size() ||
                 p.fabric_target_names.size() != p.fabric_range_owner_cores.size() ||
                 p.fabric_target_names.size() != p.fabric_range_offsets.size() ||
                 p.fabric_target_names.size() != p.fabric_range_sizes.size() ||
                 p.fabric_target_names.size() != p.fabric_range_writable.size(),
             "MeshProgramLoader: fabric range parameter arrays differ in length");
    runtime_arch.arch_digest_hex = p.arch_digest;
    runtime_arch.core_ids.assign(p.core_ids.begin(), p.core_ids.end());
    runtime_arch.sram_bytes = p.sram_bytes;
    runtime_arch.command_rom_entries = p.command_rom_entries;
    runtime_arch.sram_banks = p.sram_banks;
    runtime_arch.sram_alignment = p.sram_alignment;
    runtime_arch.axi_data_bytes = p.axi_data_bytes;
    runtime_arch.axi_max_burst_beats = p.axi_max_burst_beats;
    runtime_arch.weight_cache_slot_bytes = p.weight_cache_slot_bytes;
    weight_policy = p.weight_policy;
    moe_master_seed = p.moe_master_seed;
    if (p.sram_partition_kinds.size() != p.sram_partition_bases.size() ||
        p.sram_partition_kinds.size() != p.sram_partition_bytes.size() ||
        p.sram_partition_kinds.size() != p.sram_partition_alignments.size() ||
        p.sram_partition_kinds.size() !=
            p.sram_partition_metadata_entries.size() ||
        p.sram_partition_kinds.size() != p.sram_partition_max_pinned.size())
        fatal("MeshProgramLoader: SRAM partition arrays must be parallel");
    for (size_t i = 0; i < p.sram_partition_kinds.size(); i++) {
        RuntimeArch::Partition partition;
        partition.kind = p.sram_partition_kinds[i];
        partition.base = p.sram_partition_bases[i];
        partition.bytes = p.sram_partition_bytes[i];
        partition.alignment = p.sram_partition_alignments[i];
        partition.metadata_entries = p.sram_partition_metadata_entries[i];
        partition.max_pinned_entries = p.sram_partition_max_pinned[i];
        runtime_arch.partitions.push_back(partition);
    }
    runtime_arch.axi_address_bits = p.axi_address_bits;
    for (size_t i = 0; i < p.region_ids.size(); i++) {
        RuntimeArch::Region region;
        region.region_id = p.region_ids[i];
        region.base = p.region_bases[i];
        region.bytes = p.region_bytes[i];
        region.tile_stride = p.region_tile_strides[i];
        region.tile_bytes = p.region_tile_bytes[i];
        region.kind = p.region_kinds[i];
        region.is_sram_aperture =
            region.tile_bytes != 0 ||
            region.kind == RuntimeArch::Region::kKindSramAperture;
        runtime_arch.regions.push_back(region);
    }
    for (size_t i = 0; i < p.binding_slot_ids.size(); i++) {
        DispatchBinding binding;
        binding.slot_id = p.binding_slot_ids[i];
        binding.region_id = p.binding_region_ids[i];
        binding.owner_core = uint16_t(p.binding_owner_cores[i]);
        binding.allocation_offset_bytes = p.binding_offsets[i];
        binding.allocation_size_bytes = p.binding_sizes[i];
        binding.allocation_alignment_bytes = p.binding_alignments[i];
        binding.access = p.binding_accesses[i];
        dispatch_bindings.push_back(binding);
    }
    for (size_t i = 0; i < p.fabric_target_names.size(); i++) {
        const std::string &name = p.fabric_target_names[i];
        auto target = std::find_if(
            runtime_arch.fabric_targets.begin(),
            runtime_arch.fabric_targets.end(),
            [&name](const RuntimeArch::FabricTarget &candidate) {
                return candidate.name == name;
            });
        if (target == runtime_arch.fabric_targets.end()) {
            runtime_arch.fabric_targets.push_back(
                RuntimeArch::FabricTarget{name, {}});
            target = std::prev(runtime_arch.fabric_targets.end());
        }
        RuntimeArch::AddressRange range;
        range.region_id = p.fabric_range_region_ids[i];
        range.owner_core = uint16_t(p.fabric_range_owner_cores[i]);
        range.offset_bytes = p.fabric_range_offsets[i];
        range.size_bytes = p.fabric_range_sizes[i];
        range.writable = p.fabric_range_writable[i] != 0;
        target->ranges.push_back(range);
    }
}

void
MeshProgramLoader::reportRejection(
    const MeshLoadError &error, DiagnosticStage stage, const char *phase,
    const std::vector<std::pair<std::string, std::string>> &extra)
{
    const mesh_diagnostics::Definition *definition =
        mesh_diagnostics::findDefinition(std::string_view(error.code));
    fatal_if(definition == nullptr,
             "MeshProgramLoader: %s: %s reports an unknown diagnostic code",
             error.code, error.message);
    RuntimeDiagnostic record;
    record.code = definition->id;
    record.stage = stage;
    record.context = {{"program_file", program_file},
                      {"phase", phase},
                      {"detail", error.message},
                      {"install_state", installed_cores == 0 ? "pre_install"
                                                            : "post_install"},
                      {"installed_cores", std::to_string(installed_cores)},
                      {"cores", std::to_string(core_objects.size())}};
    record.context.insert(record.context.end(), error.context.begin(),
                          error.context.end());
    for (const auto &entry : extra)
        record.context.push_back(entry);
    const std::string business =
        "MeshProgramLoader: " + error.code + ": " + error.message;
    reportDiagnostic(sink, record, business);
    fatal("%s", business.c_str());
}

LoaderTrafficSnapshot
MeshProgramLoader::trafficSnapshot() const
{
    LoaderTrafficSnapshot snapshot;
    for (const AxiGarnetBridge *bridge : bridges) {
        const AxiGarnetBridge::Counters &counters = bridge->counters();
        snapshot.ar_accepted += counters.arAccepted;
        snapshot.aw_accepted += counters.awAccepted;
        snapshot.w_accepted += counters.wAccepted;
        snapshot.r_beats += counters.rBeatsConsumed;
        snapshot.b_consumed += counters.bConsumed;
        snapshot.b_errors += counters.bErrorCount;
        snapshot.r_errors += counters.rErrorBeats;
    }
    const auto *garnet =
        dynamic_cast<const ruby::garnet::GarnetNetwork *>(network);
    if (garnet != nullptr) {
        const ruby::garnet::GarnetQuiescenceSnapshot quiescence =
            garnet->quiescenceSnapshot();
        snapshot.ni_queued_flits = quiescence.niQueuedFlits;
        snapshot.ni_queued_messages = quiescence.niQueuedMessages;
        snapshot.router_buffered_flits = quiescence.routerBufferedFlits;
        snapshot.non_idle_input_vcs = quiescence.nonIdleInputVcs;
        snapshot.non_idle_output_vcs = quiescence.nonIdleOutputVcs;
        snapshot.data_link_pending_flits = quiescence.dataLinkPendingFlits;
        snapshot.credit_link_pending_credits =
            quiescence.creditLinkPendingCredits;
        snapshot.bridge_pending_items = quiescence.bridgePendingItems;
        snapshot.credit_deficit = quiescence.creditDeficit;
        for (unsigned vnet = 0; vnet < garnet->getNumberOfVirtualNetworks();
             vnet++) {
            snapshot.packets_injected += garnet->packetsInjected(vnet);
            snapshot.packets_received += garnet->packetsReceived(vnet);
            snapshot.flits_injected += garnet->flitsInjected(vnet);
            snapshot.flits_received += garnet->flitsReceived(vnet);
        }
    }
    return snapshot;
}

void MeshProgramLoader::startup()
{
    control_plane.captured = true;
    control_plane.begin_tick = curTick();
    control_plane.begin = trafficSnapshot();
    std::ifstream file(program_file, std::ios::binary);
    if (!file)
        fatal("MeshProgramLoader: cannot open %s", program_file);
    MeshBytes image((std::istreambuf_iterator<char>(file)),
                    std::istreambuf_iterator<char>());
    auto result = std::make_shared<DecodedProgram>();
    MeshLoadError error;
    if (!decodeMeshBinary(image, *result, error))
        reportRejection(error, DiagnosticStage::Load, "decode");
    auto admitted = std::make_shared<MeshProgramAdmission>();
    if (!admitProgram(result, runtime_arch, *admitted, error))
        reportRejection(error, DiagnosticStage::Load, "admission");
    ServingCapacityRequirement required;
    if (!servingCapacityPlan(*result, serving_capacity, required, error))
        reportRejection(error, DiagnosticStage::Load, "serving_capacity");
    admission = std::move(admitted);
    std::copy_n(image.begin() + mesh_abi::kHeaderPayloadSha256Offset,
                program_digest.size(), program_digest.begin());
    program_name = result->transport.strings.empty() ? "program" : result->transport.strings.front();
    if (result->transport.agent_request_profiles.empty()) {
        if (!prepareProgramInvocation(admission, entrypoint_id, profile_id,
                                      dispatch_bindings, invocation, error))
            reportRejection(error, DiagnosticStage::Invocation, "prepare");
    } else if (entrypoint_id || profile_id || !dispatch_bindings.empty()) {
        error = {"E_RELOCATION", "Serving invocation must be selected by its frozen request batch"};
        reportRejection(error, DiagnosticStage::Invocation, "selection");
    }
    for (MeshDummyCore *core : core_objects) {
        if (transport)
            transport->registerCore(core->archCoreId(), core);
        core->dmaEngine()->bindOwner(core, &runtime_arch);
        for (const auto &region : runtime_arch.regions)
            if (region.kind == RuntimeArch::Region::kKindSramAperture)
                core->setDmaGeometry(uint16_t(region.region_id),
                                    runtime_arch.axi_max_burst_beats);
        core->installAdmission(admission);
        if (invocation)
            core->installInvocation(invocation);
        ++installed_cores;
        for (PeerSramAperture *aperture : apertures)
            if (aperture->coreId() == core->archCoreId()) {
                aperture->bindCore(core);
                core->setSramBacking(aperture);
            }
    }
    if (!memory_file_image.empty()) {
        std::ifstream manifest(memory_file_image, std::ios::binary | std::ios::ate);
        const auto size = manifest.tellg();
        fatal_if(!manifest || size < 0 || size > 16 * 1024 * 1024,
                 "E_MEMORY_IMAGE: manifest is missing or exceeds 16 MiB");
        manifest.seekg(0);
        std::string document(size, '\0');
        fatal_if(!manifest.read(document.data(), size), "E_MEMORY_IMAGE: manifest read failed");
        std::string reason;
        auto spans = parseMemoryFileImage(document,
            std::filesystem::path(memory_file_image).parent_path().string(),
            mesh_hash::digestHex(program().metadata.semantic_sha256),
            effective_arch_digest, reason);
        fatal_if(!spans, "E_MEMORY_IMAGE: %s", reason);
        fatal_if(!physical_memory.installFiles(*spans, reason), "E_MEMORY_IMAGE: %s", reason);
    }
    installWeightCaches();
    loadOverlayImage();
    load_ok = true;
    control_plane.end_tick = curTick();
    control_plane.end = trafficSnapshot();
}

void MeshProgramLoader::loadOverlayImage()
{
    if (overlay_image_file.empty())
        return;
    // One image per layer: a comma-separated list installs every reachable
    // layer on its owning core inside one batch.
    std::vector<std::string> files;
    std::string current;
    for (char symbol : overlay_image_file) {
        if (symbol == ',') {
            files.push_back(current);
            current.clear();
            continue;
        }
        current.push_back(symbol);
    }
    files.push_back(current);
    for (const std::string &path : files) {
        if (path.empty())
            fatal("MeshProgramLoader: empty overlay image in %s",
                  overlay_image_file);
        std::ifstream file(path, std::ios::binary);
        if (!file)
            fatal("MeshProgramLoader: cannot open %s", path);
        std::vector<uint8_t> image((std::istreambuf_iterator<char>(file)),
                                   std::istreambuf_iterator<char>());
        MoeOverlayImage codec;
        MoeOverlayGraph graph;
        uint32_t layer_id = 0;
        std::string error;
        if (!codec.decode(image, graph, layer_id, error))
            fatal("MeshProgramLoader: %s: %s", path, error);
        std::string validate_error;
        if (!graph.validate(validate_error))
            fatal("MeshProgramLoader: %s: %s", path, validate_error);
        if (graph.programDigest() != program_digest)
            fatal("MeshProgramLoader: %s: overlay image was materialized for "
                  "another program image (expected digest %s)",
                  path, program_name);
        verifyOverlayWeightPolicy(graph, layer_id, path);
        overlay_graphs.emplace_back(layer_id, std::move(graph));
    }
}

void MeshProgramLoader::verifyOverlayWeightPolicy(const MoeOverlayGraph &graph,
                                                  uint32_t layer_id,
                                                  const std::string &path)
{
    const bool cached = weight_policy == "cached";
    const mesh_abi::MoeLayerSpec *layer = nullptr;
    for (const auto &candidate : program().transport.moe_layer_specs)
        if (candidate.layer_id == layer_id)
            layer = &candidate;
    fatal_if(layer == nullptr ||
                 layer->kernel_spec_index >= program().transport.moe_kernel_specs.size(),
             "%s: overlay layer %u has no frozen kernel spec",
             path, layer_id);
    const mesh_abi::MoeKernelSpec &kernel =
        program().transport.moe_kernel_specs[layer->kernel_spec_index];
    for (const auto &entry : graph.objects()) {
        if (entry.kind == mesh_abi::kMeshObjectKindVIEW &&
            entry.secondary_kind == mesh_abi::kMoeViewKindWEIGHT) {
            const uint32_t expected =
                cached ? mesh_abi::kMoeViewBackingWEIGHT_CACHE_SLOT
                       : mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION;
            if (entry.backing_kind != expected)
                fatal("%s: weight view %u:%u backing kind %u contradicts the "
                      "%s weight policy",
                      path, entry.region_id, entry.ordinal,
                      entry.backing_kind, weight_policy);
        }
        if (cached && entry.kind == mesh_abi::kMeshObjectKindDESCRIPTOR &&
            entry.secondary_kind ==
                mesh_abi::kMoeDescriptorKindSTREAMED_WEIGHT)
            fatal("%s: the cached weight policy forbids a batch-owned "
                  "streamed weight load",
                  path);
        if (entry.kind != mesh_abi::kMeshObjectKindCOMMAND)
            continue;
        if (entry.role != mesh_abi::kMoeCommandRoleEXPERT_COMPUTE &&
            entry.role != mesh_abi::kMoeCommandRoleCOPY_THROUGH &&
            entry.role != mesh_abi::kMoeCommandRoleLOCAL_REDUCE)
            continue;
        std::map<uint32_t, uint32_t> views;
        for (uint32_t index = 0; index < entry.view_count; index++) {
            const MoeOverlayEntry *view = nullptr;
            for (const auto &candidate : graph.objects())
                if (candidate.kind == mesh_abi::kMeshObjectKindVIEW &&
                    candidate.region_id == entry.view_refs[index][0] &&
                    candidate.ordinal == entry.view_refs[index][2])
                    view = &candidate;
            fatal_if(view == nullptr,
                     "%s: compute command %u:%u references a missing view "
                     "%u:%u",
                     path, entry.region_id, entry.ordinal,
                     entry.view_refs[index][0], entry.view_refs[index][2]);
            views[view->secondary_kind]++;
        }
        const auto count = [&views](uint32_t kind) {
            const auto it = views.find(kind);
            return it == views.end() ? 0u : it->second;
        };
        if (entry.role == mesh_abi::kMoeCommandRoleEXPERT_COMPUTE) {
            fatal_if(count(mesh_abi::kMoeViewKindWEIGHT) != 1,
                     "%s: expert compute %u:%u needs exactly one weight "
                     "operand, found %u",
                     path, entry.region_id, entry.ordinal,
                     count(mesh_abi::kMoeViewKindWEIGHT));
            fatal_if(count(mesh_abi::kMoeViewKindEXPERT_OUTPUT) != 1,
                     "%s: expert compute %u:%u needs exactly one expert "
                     "output operand, found %u",
                     path, entry.region_id, entry.ordinal,
                     count(mesh_abi::kMoeViewKindEXPERT_OUTPUT));
            const uint32_t rows =
                count(mesh_abi::kMoeViewKindMEMBER_INPUT) +
                count(mesh_abi::kMoeViewKindDISPATCH_BUFFER) +
                count(mesh_abi::kMoeViewKindPAD_BUFFER);
            fatal_if(rows == 0,
                     "%s: expert compute %u:%u has no input row operand",
                     path, entry.region_id, entry.ordinal);
            fatal_if(entry.bytes == 0 ||
                         entry.bytes % kernel.output_token_bytes != 0,
                     "%s: expert compute %u:%u payload %u B is not a whole "
                     "number of %u B rows",
                     path, entry.region_id, entry.ordinal,
                     entry.bytes, kernel.output_token_bytes);
            fatal_if(entry.bytes / kernel.output_token_bytes > kernel.max_m,
                     "%s: expert compute %u:%u exceeds the kernel max_m",
                     path, entry.region_id, entry.ordinal);
            continue;
        }
        fatal_if(count(mesh_abi::kMoeViewKindMEMBER_OUTPUT) != 1,
                 "%s: combine command %u:%u needs exactly one member output "
                 "operand, found %u",
                 path, entry.region_id, entry.ordinal,
                 count(mesh_abi::kMoeViewKindMEMBER_OUTPUT));
        fatal_if(count(mesh_abi::kMoeViewKindREDUCE_ACCUMULATOR) != 1,
                 "%s: combine command %u:%u needs exactly one accumulator "
                 "operand, found %u",
                 path, entry.region_id, entry.ordinal,
                 count(mesh_abi::kMoeViewKindREDUCE_ACCUMULATOR));
        fatal_if(count(mesh_abi::kMoeViewKindEXPERT_OUTPUT) +
                     count(mesh_abi::kMoeViewKindCOMBINE_BUFFER) >
                     entry.wait_count - 1,
                 "%s: combine command %u:%u names more contributor operands "
                 "than its %u contributors",
                 path, entry.region_id, entry.ordinal,
                 entry.wait_count - 1);
        fatal_if(entry.bytes != kernel.output_token_bytes,
                 "%s: combine command %u:%u serves %u B for a %u B output "
                 "row",
                 path, entry.region_id, entry.ordinal,
                 entry.bytes, kernel.output_token_bytes);
        fatal_if(entry.wait_count == 0,
                 "%s: combine command %u:%u has no contributor",
                 path, entry.region_id, entry.ordinal);
        const uint32_t contributors = entry.wait_count - 1;
        fatal_if(contributors == 0,
                 "%s: combine command %u:%u has no contributor",
                 path, entry.region_id, entry.ordinal);
        fatal_if(entry.role == mesh_abi::kMoeCommandRoleCOPY_THROUGH &&
                     contributors != 1,
                 "%s: copy-through command %u:%u carries %u contributors",
                 path, entry.region_id, entry.ordinal,
                 contributors);
        fatal_if(entry.role == mesh_abi::kMoeCommandRoleLOCAL_REDUCE &&
                     contributors < 2,
                 "%s: local reduce command %u:%u carries %u contributors",
                 path, entry.region_id, entry.ordinal,
                 contributors);
    }
}

void MeshProgramLoader::installOverlayImage(const MeshProgramInvocation &selected,
                                           const MoeOverlaySet *overlays)
{
    const auto &graphs = overlays ? *overlays : overlay_graphs;
    if (graphs.empty())
        return;
    buildOverlayAddressSpace(selected);
    for (const auto &entry : graphs)
        for (MeshDummyCore *core : core_objects)
            core->installOverlay(entry.first, entry.second,
                                 &overlay_addresses);
}

std::vector<uint32_t>
MeshProgramLoader::cacheableTagsOf(uint16_t core_id) const
{
    std::vector<uint32_t> tags;
    for (const auto &site : weightTagSitesOf(program(), core_id))
        tags.push_back(site.weight_tag_index);
    return tags;
}

void MeshProgramLoader::installWeightCaches()
{
    if (weight_policy != "cached") {
        fatal_if(!weight_runtime_image.empty() || !cache_state_replay.empty(),
                 "E_MOE_CACHE: weight replay requires cached policy");
        return;
    }
    const RuntimeArch::Partition *cache = runtime_arch.partition(
        mesh_abi::kSramPartitionKindWEIGHT_CACHE);
    if (cache == nullptr)
        fatal("cached weight policy needs a WEIGHT_CACHE partition");
    if (runtime_arch.weight_cache_slot_bytes == 0)
        fatal("cached weight policy needs a slot size");
    const uint32_t slot_count =
        uint32_t(cache->bytes / runtime_arch.weight_cache_slot_bytes);
    std::map<uint16_t, MoeWeightCache::Config> configs;
    for (MeshDummyCore *core : core_objects) {
        const std::vector<uint32_t> tags = cacheableTagsOf(core->archCoreId());
        MoeWeightCache::Config config;
        config.core_id = core->archCoreId();
        config.slot_count = slot_count;
        config.slot_bytes = runtime_arch.weight_cache_slot_bytes;
        config.partition_base = cache->base;
        config.mshr_slots = slot_count;
        config.eviction_slots = slot_count;
        config.obligation_slots = slot_count;
        config.subscriber_slots = slot_count * 2;
        config.cacheable_tags = tags;
        if (strict_replay)
            for (const auto &profile : program().transport.agent_instance_profiles) {
                const auto needed = servingStrictCacheCapacity(program(), *executionViews().forInstance(profile.mesh_entrypoint_id, profile.mesh_profile_id), config.core_id);
                fatal_if(needed.lines > config.slot_count || needed.lines > config.mshr_slots ||
                    needed.lines > config.obligation_slots || needed.occurrences > config.subscriber_slots,
                    "E_CAPACITY_PLAN: strict replay cold union exceeds cache capacity on core %u profile %u",
                    config.core_id, profile.instance_profile_id);
            }
        fatal_if(!configs.emplace(config.core_id, std::move(config)).second,
                 "E_MOE_CACHE: duplicate physical cache owner");
    }
    std::string reason;
    auto prepared = CacheStartupImage::prepare(weight_runtime_image, cache_state_replay,
        *admission, effective_arch_digest, weight_image_digest, configs, reason);
    fatal_if(!prepared, "E_MOE_CACHE: %s", reason);
    for (MeshDummyCore *core : core_objects) {
        fatal_if(!core->quiescent(), "E_MOE_CACHE: cache startup owner is active");
        for (const auto &slot : prepared->slots[core->archCoreId()]) {
            std::vector<uint8_t> prior(slot.data.size());
            fatal_if(slot.address > runtime_arch.sram_bytes ||
                     slot.data.size() > runtime_arch.sram_bytes - slot.address ||
                     !core->functionalSramRead(slot.address, prior.size(), prior.data()),
                     "E_MOE_CACHE: cache payload escapes physical SRAM");
        }
    }
    if (!prepared->weights.empty())
        fatal_if(!physical_memory.installImage(prepared->weights),
                 "E_MOE_CACHE: weight image escapes physical HBM");
    for (MeshDummyCore *core : core_objects)
        for (const auto &slot : prepared->slots[core->archCoreId()])
            fatal_if(!core->functionalSramWrite(slot.address, slot.data.size(), slot.data.data()),
                     "E_MOE_CACHE: cache payload installation failed");
    for (MeshDummyCore *core : core_objects)
        core->installWeightCache(std::move(prepared->caches.at(core->archCoreId())));
}

void MeshProgramLoader::buildOverlayAddressSpace(const MeshProgramInvocation &selected)
{
    overlay_addresses = MoeOverlayAddressSpace();
    overlay_addresses.max_burst_beats = runtime_arch.axi_max_burst_beats;
    for (const auto &region : runtime_arch.regions) {
        if (region.kind == RuntimeArch::Region::kKindHbm)
            overlay_addresses.hbm_region = uint16_t(region.region_id);
        if (region.kind == RuntimeArch::Region::kKindSramAperture)
            overlay_addresses.sram_region = uint16_t(region.region_id);
    }
    for (const auto &allocation : program().transport.allocations) {
        const auto *owner = admission->context().owner("allocations", allocation.allocation_id);
        if (owner && owner->variant_id == selected.variantId())
            overlay_addresses.allocations[allocation.allocation_id] = {
                allocation.offset_bytes, allocation.size_bytes};
    }
    for (const auto &expert : program().transport.moe_expert_specs) {
        const bool active = std::any_of(program().transport.moe_dynamic_regions.begin(),
            program().transport.moe_dynamic_regions.end(), [&](const auto &region) {
                const auto *owner = admission->context().owner("commands", region.insert_after_command_id);
                return region.layer_id == expert.layer_id && owner && owner->variant_id == selected.variantId();
            });
        if (!active)
            continue;
        const auto symbol = admission->context().symbolBinding(selected.variantId(),
            program().transport.strings.at(expert.weight_symbol_id - 1));
        const auto *binding = symbol ? selected.binding().binding(symbol->slot->slot_id) : nullptr;
        fatal_if(!binding || !spanFits(expert.weight_region_offset, expert.weight_bytes,
                                      binding->allocation_size_bytes),
                 "MoE expert %u:%u has no invocation weight range", expert.layer_id, expert.expert_id);
        mesh_abi::DmaEndpoint source;
        source.memory_space = uint16_t(symbol->object->memory_space);
        source.region_id = binding->region_id;
        source.tensor_id = symbol->tensor->tensor_id;
        source.offset_bytes = binding->allocation_offset_bytes + expert.weight_region_offset;
        overlay_addresses.expert_weights[{expert.layer_id, expert.expert_id}] = {
            source, expert.weight_bytes};
    }
}

} // namespace ai_mesh
} // namespace gem5

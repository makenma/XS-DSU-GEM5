#include "dev/ai_mesh/mesh_program_loader.hh"

#include <fstream>

#include "base/logging.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_weight_tags.hh"
#include "dev/ai_mesh/mock_axi_transport.hh"
#include "dev/ai_mesh/peer_sram_aperture.hh"
#include "params/MeshProgramLoader.hh"

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
      core_objects(p.cores.begin(), p.cores.end()),
      transport(p.transport),
      apertures(p.apertures.begin(), p.apertures.end())
{
    runtime_arch.arch_digest_hex = p.arch_digest;
    runtime_arch.core_ids.assign(p.core_ids.begin(), p.core_ids.end());
    runtime_arch.sram_bytes = p.sram_bytes;
    runtime_arch.sram_banks = p.sram_banks;
    runtime_arch.sram_alignment = p.sram_alignment;
    runtime_arch.axi_data_bytes = p.axi_data_bytes;
    runtime_arch.axi_max_burst_beats = p.axi_max_burst_beats;
    runtime_arch.weight_cache_slot_bytes = p.weight_cache_slot_bytes;
    weight_policy = p.weight_policy;
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
    for (size_t i = 0; i < p.region_ids.size(); i++) {
        RuntimeArch::Region region;
        region.region_id = p.region_ids[i];
        region.base = p.region_bases[i];
        region.bytes = p.region_bytes[i];
        region.tile_stride = p.region_tile_strides[i];
        region.tile_bytes = i < p.region_tile_bytes.size() ? p.region_tile_bytes[i] : 0;
        region.kind = i < p.region_kinds.size() ? p.region_kinds[i] : 0;
        region.is_sram_aperture =
            region.tile_bytes != 0 ||
            region.kind == RuntimeArch::Region::kKindSramAperture;
        runtime_arch.regions.push_back(region);
    }
}

void MeshProgramLoader::startup()
{
    std::ifstream file(program_file, std::ios::binary);
    if (!file)
        fatal("MeshProgramLoader: cannot open %s", program_file);

    MeshBytes image((std::istreambuf_iterator<char>(file)),
                    std::istreambuf_iterator<char>());
    fatal_if(image.size() < 104,
             "MeshProgramLoader: %s: program image is shorter than its "
             "header digest", program_file);
    std::copy(image.begin() + 72, image.begin() + 104,
              program_digest.begin());

    auto result = std::make_shared<DecodedProgram>();
    MeshLoadError error;
    if (!decodeMeshBinary(image, *result, error))
        fatal("MeshProgramLoader: %s: %s", error.code, error.message);
    if (!verifyDecodedProgram(*result, runtime_arch, error))
        fatal("MeshProgramLoader: %s: %s", error.code, error.message);

    decoded = result;
    program_name = result->strings.empty() ? "program" : result->strings[0];
    load_ok = true;

    installWeightCaches();

    for (MeshDummyCore *core : core_objects) {
        if (transport)
            transport->registerCore(core->archCoreId(), core);
        core->dmaEngine()->bindOwner(core, &runtime_arch);
        for (const auto &region : runtime_arch.regions)
            if (region.kind == RuntimeArch::Region::kKindSramAperture)
                core->setDmaGeometry(uint16_t(region.region_id),
                                     runtime_arch.axi_max_burst_beats);
        core->installProgram(decoded);
        for (PeerSramAperture *aperture : apertures)
            if (aperture->coreId() == core->archCoreId()) {
                aperture->bindCore(core);
                core->setSramBacking(aperture);
            }
    }

    loadOverlayImage();

    // P2P transfer destination ranges are precomputed at load; the
    // expectation itself is armed on the receiving aperture when the
    // transfer is submitted and retires when it completes (spec 5.6).
    for (const auto &descriptor : result->descriptors) {
        if (descriptor.kind != mesh_abi::kDmaKindP2P_PUSH)
            continue;
        if (descriptor.transfer_id == 0)
            fatal("MeshProgramLoader: P2P descriptor %u without transfer id",
                  descriptor.descriptor_id);
        if (descriptor.useful_bytes == 0)
            continue;
        const RuntimeArch::Region *dst_region =
            runtime_arch.region(descriptor.dst.region_id);
        fatal_if(!dst_region, "P2P destination region unresolved");
        const uint64_t stride =
            dst_region->tile_stride ? dst_region->tile_stride
                                    : dst_region->tile_bytes;
        TransferPlan plan;
        plan.receiver_core = descriptor.dst.owner_core;
        for (uint32_t row = 0; row < descriptor.rows; row++)
            plan.ranges.emplace_back(
                dst_region->base + uint64_t(descriptor.dst.owner_core) * stride +
                    descriptor.dst.offset_bytes +
                    uint64_t(row) * descriptor.dst_stride_bytes,
                dst_region->base + uint64_t(descriptor.dst.owner_core) * stride +
                    descriptor.dst.offset_bytes +
                    uint64_t(row) * descriptor.dst_stride_bytes +
                    descriptor.row_bytes);
        transfer_plans[descriptor.transfer_id] = std::move(plan);
    }
}

const MeshProgramLoader::TransferPlan *
MeshProgramLoader::transferPlan(uint32_t transfer_id) const
{
    auto it = transfer_plans.find(transfer_id);
    return it == transfer_plans.end() ? nullptr : &it->second;
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
    for (const auto &candidate : decoded->moe_layer_specs)
        if (candidate.layer_id == layer_id)
            layer = &candidate;
    fatal_if(layer == nullptr ||
                 layer->kernel_spec_index >= decoded->moe_kernel_specs.size(),
             "%s: overlay layer %u has no frozen kernel spec",
             path, layer_id);
    const mesh_abi::MoeKernelSpec &kernel =
        decoded->moe_kernel_specs[layer->kernel_spec_index];
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

void MeshProgramLoader::installOverlayImage()
{
    if (overlay_graphs.empty())
        return;
    buildOverlayAddressSpace();
    for (const auto &entry : overlay_graphs)
        for (MeshDummyCore *core : core_objects)
            core->installOverlay(entry.first, entry.second,
                                 &overlay_addresses);
}

std::vector<uint32_t>
MeshProgramLoader::cacheableTagsOf(uint16_t core_id) const
{
    std::vector<uint32_t> tags;
    for (const auto &site : weightTagSitesOf(*decoded, core_id))
        tags.push_back(site.weight_tag_index);
    return tags;
}

void MeshProgramLoader::installWeightCaches()
{
    if (weight_policy != "cached")
        return;
    const RuntimeArch::Partition *cache = runtime_arch.partition(
        mesh_abi::kSramPartitionKindWEIGHT_CACHE);
    if (cache == nullptr)
        fatal("cached weight policy needs a WEIGHT_CACHE partition");
    if (runtime_arch.weight_cache_slot_bytes == 0)
        fatal("cached weight policy needs a slot size");
    const uint32_t slot_count =
        uint32_t(cache->bytes / runtime_arch.weight_cache_slot_bytes);
    for (MeshDummyCore *core : core_objects) {
        const std::vector<uint32_t> tags = cacheableTagsOf(core->archCoreId());
        if (tags.empty())
            continue;
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
        core->installWeightCache(
            std::make_unique<MoeWeightCache>(config));
    }
}

void MeshProgramLoader::buildOverlayAddressSpace()
{
    overlay_addresses = MoeOverlayAddressSpace();
    overlay_addresses.max_burst_beats = runtime_arch.axi_max_burst_beats;
    for (const auto &region : runtime_arch.regions) {
        if (region.kind == RuntimeArch::Region::kKindHbm)
            overlay_addresses.hbm_region = uint16_t(region.region_id);
        if (region.kind == RuntimeArch::Region::kKindSramAperture)
            overlay_addresses.sram_region = uint16_t(region.region_id);
    }
    for (const auto &allocation : decoded->allocations)
        overlay_addresses.allocations[allocation.allocation_id] = {
            allocation.offset_bytes, allocation.size_bytes};
    for (const auto &expert : decoded->moe_expert_specs) {
        for (const auto &relocation : decoded->relocations) {
            if (relocation.symbol_sid != expert.weight_symbol_id)
                continue;
            overlay_addresses.expert_weights[expert.expert_id] = {
                relocation.offset_bytes + expert.weight_region_offset,
                expert.weight_bytes};
            break;
        }
    }
}

} // namespace ai_mesh
} // namespace gem5

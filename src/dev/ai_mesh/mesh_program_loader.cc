#include "dev/ai_mesh/mesh_program_loader.hh"

#include <fstream>

#include "base/logging.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
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

    auto result = std::make_shared<DecodedProgram>();
    MeshLoadError error;
    if (!decodeMeshBinary(image, *result, error))
        fatal("MeshProgramLoader: %s: %s", error.code, error.message);
    if (!verifyDecodedProgram(*result, runtime_arch, error))
        fatal("MeshProgramLoader: %s: %s", error.code, error.message);

    decoded = result;
    program_name = result->strings.empty() ? "program" : result->strings[0];
    load_ok = true;

    for (MeshDummyCore *core : core_objects) {
        if (transport)
            transport->registerCore(core->archCoreId(), core);
        core->dmaEngine()->bindOwner(core, &runtime_arch);
        core->installProgram(decoded);
        for (PeerSramAperture *aperture : apertures)
            if (aperture->coreId() == core->archCoreId()) {
                aperture->bindCore(core);
                core->setSramBacking(aperture);
            }
    }

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

} // namespace ai_mesh
} // namespace gem5

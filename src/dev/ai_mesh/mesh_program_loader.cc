#include "dev/ai_mesh/mesh_program_loader.hh"

#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"

#include <algorithm>
#include <fstream>
#include <iterator>
#include <map>

#include "base/logging.hh"
#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/command_rom.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
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
      entrypoint_id(p.entrypoint_id),
      profile_id(p.profile_id),
      core_objects(p.cores.begin(), p.cores.end()),
      transport(p.transport),
      apertures(p.apertures.begin(), p.apertures.end()),
      sink(p.program_file),
      network(p.network),
      bridges(p.bridges.begin(), p.bridges.end())
{
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
    runtime_arch.sram_banks = p.sram_banks;
    runtime_arch.sram_alignment = p.sram_alignment;
    runtime_arch.axi_data_bytes = p.axi_data_bytes;
    runtime_arch.axi_max_burst_beats = p.axi_max_burst_beats;
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

    admission = admitted;
    program_name = result->transport.strings.empty() ?
        "program" : result->transport.strings[0];

    size_t candidate_count = 0;
    for (const auto &[candidate_id, candidate] : admitted->context().variants()) {
        if (entrypoint_id != 0 && candidate->entrypoint_id != entrypoint_id)
            continue;
        if (profile_id != 0 && candidate->profile_id != profile_id)
            continue;
        candidate_count++;
        if (variant_id != 0) {
            MeshLoadError selection;
            selection.code = "E_RELOCATION";
            selection.message = "invocation selects multiple Program variants";
            reportRejection(selection, DiagnosticStage::Invocation,
                            "variant_selection",
                            {{"entrypoint_id", std::to_string(entrypoint_id)},
                             {"profile_id", std::to_string(profile_id)},
                             {"matching_variants",
                              std::to_string(candidate_count)}});
        }
        variant_id = candidate_id;
    }
    if (variant_id == 0) {
        MeshLoadError selection;
        selection.code = "E_RELOCATION";
        selection.message = "invocation selects no Program variant";
        reportRejection(selection, DiagnosticStage::Invocation,
                        "variant_selection",
                        {{"entrypoint_id", std::to_string(entrypoint_id)},
                         {"profile_id", std::to_string(profile_id)},
                         {"matching_variants", "0"}});
    }

    auto resolved = std::make_shared<MeshInvocationBinding>();
    if (!resolveInvocationBinding(*admitted, variant_id, dispatch_bindings,
                                  *resolved, error))
        reportRejection(error, DiagnosticStage::Invocation,
                        "binding_resolution");
    invocation = resolved;

    std::map<uint16_t, std::shared_ptr<CommandRom>> roms;
    for (MeshDummyCore *core : core_objects) {
        auto rom = std::make_shared<CommandRom>();
        if (!buildCommandRom(*admitted, variant_id, core->archCoreId(), *rom,
                             error))
            reportRejection(error, DiagnosticStage::Invocation,
                            "command_rom",
                            {{"core_id", std::to_string(core->archCoreId())}});
        roms[core->archCoreId()] = std::move(rom);
    }

    // Atomic start: every core is installed only after the whole image, the
    // selected variant, the resolved invocation and every per-core ROM are
    // known good.
    for (MeshDummyCore *core : core_objects) {
        if (transport)
            transport->registerCore(core->archCoreId(), core);
        core->dmaEngine()->bindOwner(core, &runtime_arch);
        core->installAdmission(admitted, roms.at(core->archCoreId()), resolved);
        installed_cores++;
        for (PeerSramAperture *aperture : apertures)
            if (aperture->coreId() == core->archCoreId()) {
                aperture->bindCore(core);
                core->setSramBacking(aperture);
            }
    }
    load_ok = true;

    // P2P transfer destination ranges are precomputed at load; the
    // expectation itself is armed on the receiving aperture when the
    // transfer is submitted and retires when it completes (spec 5.6).
    for (const auto &descriptor : admitted->program().transport.dma_descriptors) {
        if (descriptor.kind != mesh_abi::kDmaKindP2P_PUSH)
            continue;
        if (descriptor.transfer_id == 0)
            fatal("MeshProgramLoader: P2P descriptor %u without transfer id",
                  descriptor.descriptor_id);
        if (descriptor.useful_bytes == 0)
            continue;
        auto plan = transfer_plans.find(descriptor.transfer_id);
        if (plan == transfer_plans.end()) {
            TransferPlan fresh;
            fresh.receiver_core = descriptor.dst.owner_core;
            plan = transfer_plans
                       .emplace(descriptor.transfer_id, std::move(fresh))
                       .first;
        } else {
            fatal_if(plan->second.receiver_core != descriptor.dst.owner_core,
                     "MeshProgramLoader: P2P transfer %u targets multiple cores",
                     descriptor.transfer_id);
        }
        const uint64_t destination_base =
            resolved->endpointAddress(descriptor.descriptor_id, false);
        for (uint32_t row = 0; row < descriptor.rows; row++) {
            const uint64_t start =
                destination_base + uint64_t(row) * descriptor.dst_stride_bytes;
            plan->second.ranges.emplace_back(start, start + descriptor.row_bytes);
        }
        plan->second.useful_bytes += descriptor.useful_bytes;
    }
    for (const auto &[transfer_id, plan] : transfer_plans) {
        const P2PTransferFact *fact = admitted->dma().p2pTransfer(transfer_id);
        fatal_if(fact == nullptr,
                 "MeshProgramLoader: P2P transfer %u has no admitted fact",
                 transfer_id);
        fatal_if(plan.useful_bytes != fact->expectedBytes(),
                 "MeshProgramLoader: P2P transfer %u byte count %llu differs "
                 "from the admitted %llu",
                 transfer_id, (unsigned long long)plan.useful_bytes,
                 (unsigned long long)fact->expectedBytes());
    }
    control_plane.end_tick = curTick();
    control_plane.end = trafficSnapshot();
}

const MeshProgramLoader::TransferPlan *
MeshProgramLoader::transferPlan(uint32_t transfer_id) const
{
    auto it = transfer_plans.find(transfer_id);
    return it == transfer_plans.end() ? nullptr : &it->second;
}

} // namespace ai_mesh
} // namespace gem5

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/mesh_dispatcher.hh"

#include <algorithm>
#include <fstream>
#include <iomanip>
#include <map>

#include "base/logging.hh"
#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/axi_tensor_dma_engine.hh"
#include "dev/ai_mesh/mesh_program_loader.hh"
#include "dev/ai_mesh/mesh_json.hh"
#include "dev/ai_mesh/mock_axi_transport.hh"
#include "dev/ai_mesh/npu_memory_endpoint.hh"
#include "dev/ai_mesh/peer_sram_aperture.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/simple/AggregateNetwork.hh"
#include "mem/ruby/network/simple/SimpleNetwork.hh"

namespace gem5::ai_mesh
{

static void
writeLoaderTrafficSnapshot(std::ostream &out, const LoaderTrafficSnapshot &value)
{
    out << "{\"ar_accepted\": " << value.ar_accepted
        << ", \"aw_accepted\": " << value.aw_accepted
        << ", \"w_accepted\": " << value.w_accepted
        << ", \"r_beats\": " << value.r_beats
        << ", \"b_consumed\": " << value.b_consumed
        << ", \"b_errors\": " << value.b_errors
        << ", \"r_errors\": " << value.r_errors
        << ", \"ni_queued_flits\": " << value.ni_queued_flits
        << ", \"ni_queued_messages\": " << value.ni_queued_messages
        << ", \"router_buffered_flits\": " << value.router_buffered_flits
        << ", \"non_idle_input_vcs\": " << value.non_idle_input_vcs
        << ", \"non_idle_output_vcs\": " << value.non_idle_output_vcs
        << ", \"data_link_pending_flits\": " << value.data_link_pending_flits
        << ", \"credit_link_pending_credits\": "
        << value.credit_link_pending_credits
        << ", \"bridge_pending_items\": " << value.bridge_pending_items
        << ", \"credit_deficit\": " << value.credit_deficit
        << ", \"packets_injected\": " << value.packets_injected
        << ", \"packets_received\": " << value.packets_received
        << ", \"flits_injected\": " << value.flits_injected
        << ", \"flits_received\": " << value.flits_received << "}";
}

void
MeshDispatcher::writeInstanceDestinations(
    std::ostream &out, const std::vector<DestinationSegment> &segments)
{
    out << "[";
    for (size_t index = 0; index < segments.size(); index++) {
        const DestinationSegment &row = segments[index];
        if (index != 0)
            out << ", ";
        out << "{\"descriptor_id\": " << row.descriptor_id
            << ", \"command_id\": " << row.command_id
            << ", \"generation\": " << row.generation
            << ", \"kind\": " << row.kind
            << ", \"row\": " << row.row
            << ", \"address\": " << row.address
            << ", \"sample_phase\": \""
            << (row.sampling == DestinationSampling::DescriptorCompletion
                    ? "descriptor_completion"
                    : row.sampling == DestinationSampling::TransferCommit
                        ? "transfer_commit" : "pending_readback") << "\""
            << ", \"sample_tick\": " << row.sample_tick
            << ", \"size\": " << row.size
            << ", \"bytes_hex\": \"" << row.bytes_hex << "\"}";
    }
    out << "]";
}

void
MeshDispatcher::writeInstanceBursts(
    std::ostream &out, const std::vector<BurstAttribution> &bursts)
{
    out << "[";
    for (size_t index = 0; index < bursts.size(); index++) {
        const BurstAttribution &row = bursts[index];
        if (index != 0)
            out << ", ";
        out << "{\"instance\": " << row.descriptor.instance.value()
            << ", \"core_id\": " << row.core_id
            << ", \"command_id\": " << row.command_id
            << ", \"generation\": " << row.generation
            << ", \"descriptor_id\": " << row.descriptor.ordinal
            << ", \"domain\": " << uint32_t(row.descriptor.domain)
            << ", \"region_group_id\": " << row.descriptor.regionGroupId
            << ", \"region_id\": " << row.descriptor.regionId
            << ", \"burst_index\": " << row.burst_index
            << ", \"ordinal\": " << row.ordinal
            << ", \"channel\": \"" << (row.read ? "AR" : "AW") << '\"'
            << ", \"axi_id\": " << row.axi_id
            << ", \"address\": " << row.address
            << ", \"beats\": " << row.beats
            << ", \"beat_bytes\": " << row.beat_bytes
            << ", \"useful_bytes\": " << row.useful_bytes
            << ", \"logical_start\": " << row.logical_start
            << ", \"issue_tick\": " << row.issue_tick
            << ", \"ar_aw_tick\": " << row.ar_aw_tick
            << ", \"response_tick\": " << row.response_tick
            << ", \"local_commit_tick\": " << row.local_commit_tick
            << ", \"retire_tick\": " << row.retire_tick
            << ", \"done_tick\": " << row.done_tick
            << ", \"errored\": " << (row.errored ? "true" : "false")
            << "}";
    }
    out << "]";
}

void
MeshDispatcher::writeInstanceApertures(
    std::ostream &out, const std::vector<ApertureFrame> &frames)
{
    out << "[";
    bool first_frame = true;
    for (const ApertureFrame &frame : frames) {
        if (!first_frame)
            out << ", ";
        first_frame = false;
        out << "{\"core_id\": " << frame.core_id << ", \"transfers\": [";
        bool first_transfer = true;
        for (const PeerTransferCoverage &row : frame.transfers) {
            if (!first_transfer)
                out << ", ";
            first_transfer = false;
            out << "{\"transfer_id\": " << row.transfer_id
                << ", \"commit_tick\": " << row.commit_tick
                << ", \"expected_bytes\": " << row.expected_bytes
                << ", \"covered_bytes\": " << row.covered_bytes
                << ", \"uncovered_bytes\": " << row.uncovered_bytes
                << ", \"duplicate_notifications\": "
                << row.duplicate_notifications
                << ", \"transactions\": " << row.transactions
                << ", \"replayed_lanes\": " << row.replayed_lanes
                << ", \"notified\": " << (row.notified ? "true" : "false")
                << ", \"abandoned\": " << (row.abandoned ? "true" : "false")
                << ", \"transaction_uids\": [";
            for (size_t uid_index = 0;
                 uid_index < row.transaction_uids.size(); uid_index++) {
                if (uid_index != 0)
                    out << ", ";
                out << row.transaction_uids[uid_index];
            }
            out << "], \"covered_ranges\": [";
            for (size_t range_index = 0;
                 range_index < row.covered_ranges.size(); range_index++) {
                if (range_index != 0)
                    out << ", ";
                out << "{\"address\": " << row.covered_ranges[range_index].first
                    << ", \"size\": " << row.covered_ranges[range_index].second
                    << "}";
            }
            out << "], \"stages\": [";
            bool first_stage = true;
            for (const PeerTransferStage &stage : row.stages) {
                if (!first_stage)
                    out << ", ";
                first_stage = false;
                out << "{\"tick\": " << stage.tick
                    << ", \"covered_bytes\": " << stage.covered_bytes
                    << ", \"uncovered_bytes\": " << stage.uncovered_bytes
                    << ", \"transactions\": " << stage.transactions
                    << ", \"notified\": "
                    << (stage.notified ? "true" : "false") << "}";
            }
            out << "], \"landing_events\": [";
            bool first_event = true;
            for (const PeerLandingEvent &event : row.landing_events) {
                if (!first_event)
                    out << ", ";
                first_event = false;
                out << "{\"tick\": " << event.tick
                    << ", \"txn_uid\": " << event.txn_uid
                    << ", \"ranges\": [";
                for (size_t range_index = 0;
                     range_index < event.ranges.size(); range_index++) {
                    if (range_index != 0)
                        out << ", ";
                    out << "{\"address\": " << event.ranges[range_index].first
                        << ", \"size\": " << event.ranges[range_index].second
                        << "}";
                }
                out << "]}";
            }
            out << "]}";
        }
        out << "]}";
    }
    out << "]";
}

void
MeshDispatcher::writeInstanceBarriers(
    std::ostream &out,
    const std::vector<ProgramScoreboard::BarrierGroup> &barriers)
{
    out << "[";
    bool first_barrier = true;
    for (const ProgramScoreboard::BarrierGroup &group : barriers) {
        if (!first_barrier)
            out << ", ";
        first_barrier = false;
        const char *phase = "collecting";
        if (group.phase == ProgramScoreboard::BarrierPhase::Released)
            phase = "released";
        else if (group.phase == ProgramScoreboard::BarrierPhase::Cancelled)
            phase = "cancelled";
        out << "{\"event_id\": " << group.event_id.ordinal
            << ", \"generation\": " << group.generation.value()
            << ", \"expected\": " << group.expected
            << ", \"phase\": \"" << phase << "\""
            << ", \"release_tick\": "
            << (group.phase == ProgramScoreboard::BarrierPhase::Released
                    ? std::to_string(group.release_tick)
                    : "null")
            << ", \"cancel_tick\": "
            << (group.phase == ProgramScoreboard::BarrierPhase::Cancelled
                    ? std::to_string(group.cancel_tick)
                    : "null")
            << ", \"arrivals\": [";
        bool first_arrival = true;
        for (const ProgramScoreboard::BarrierArrival &arrival : group.arrivals) {
            if (!first_arrival)
                out << ", ";
            first_arrival = false;
            out << "{\"participant\": " << arrival.participant.ordinal
                << ", \"tick\": " << arrival.tick << "}";
        }
        out << "]}";
    }
    out << "]";
}

void MeshDispatcher::writeResultJson()
{
    capturePendingDestinations();
    // Drain-time sampling of the real target owners happens exactly once,
    // after the last instance quiesced; everything below only serializes it.
    for (auto *endpoint : memory.endpoints())
        endpoint->computeVerifyDigests();
    for (PeerSramAperture *aperture : apertures)
        aperture->sampleSentinels();
    if (result_json_path.empty())
        return;
    std::ofstream out(result_json_path);
    out << "{\n  \"transport\": [\n";
    bool first = true;
    // The artifact accounts per descriptor identity across instances; the
    // instance generation stays part of the in-engine key so per-instance
    // state never mixes.
    using TrafficKey =
        std::tuple<uint32_t, uint32_t, uint32_t, uint32_t, uint32_t,
                   uint32_t>;
    std::map<TrafficKey, ActualTraffic> traffic;
    std::map<TrafficKey, RuntimeObjectKey> traffic_first;
    std::set<const std::map<RuntimeObjectKey, ActualTraffic> *> sources;
    for (const MeshDummyCore *core : cores) {
        const auto &rows = core->dmaEngine()->actualTraffic();
        if (!sources.insert(&rows).second)
            continue;
        for (const auto &row : rows) {
            const TrafficKey key(uint32_t(row.first.domain),
                                 uint32_t(core->archCoreId()),
                                 row.first.regionGroupId, row.first.regionId,
                                 uint32_t(row.first.kind), row.first.ordinal);
            traffic_first.emplace(key, row.first);
            ActualTraffic &total = traffic[key];
            total.read_bytes += row.second.read_bytes;
            total.write_bytes += row.second.write_bytes;
            total.p2p_bytes += row.second.p2p_bytes;
            total.fill_bytes += row.second.fill_bytes;
            total.read_bursts += row.second.read_bursts;
            total.write_bursts += row.second.write_bursts;
            total.p2p_bursts += row.second.p2p_bursts;
            total.read_discarded_bytes += row.second.read_discarded_bytes;
            total.write_drained_uncommitted_bytes +=
                row.second.write_drained_uncommitted_bytes;
            total.error_code = row.second.error_code;
            total.dma_kind = row.second.dma_kind;
            if (!row.second.payload_digest.empty())
                total.payload_digest = row.second.payload_digest;
        }
    }
    for (const auto &row : traffic) {
        if (!first)
            out << ",\n";
        first = false;
        out << "    {\"descriptor_id\": " << std::get<5>(row.first)
            << ", \"domain\": " << std::get<0>(row.first)
            << ", \"core_id\": " << std::get<1>(row.first)
            << ", \"region_group_id\": " << std::get<2>(row.first)
            << ", \"region_id\": " << std::get<3>(row.first)
            << ", \"object_kind\": " << std::get<4>(row.first)
            << ", \"kind\": " << row.second.dma_kind
            << ", \"read_bytes\": " << row.second.read_bytes
            << ", \"write_bytes\": " << row.second.write_bytes
            << ", \"p2p_bytes\": " << row.second.p2p_bytes
            << ", \"fill_bytes\": " << row.second.fill_bytes
            << ", \"read_bursts\": " << row.second.read_bursts
            << ", \"write_bursts\": " << row.second.write_bursts
            << ", \"p2p_bursts\": " << row.second.p2p_bursts
            << ", \"read_discarded_bytes\": " << row.second.read_discarded_bytes
            << ", \"write_drained_uncommitted_bytes\": "
            << row.second.write_drained_uncommitted_bytes
            << ", \"error_code\": " << row.second.error_code
            << ", \"dma_kind\": " << row.second.dma_kind
            << ", \"payload_digest\": \"" << row.second.payload_digest << "\"}";
    }
    out << "\n  ],\n  \"cores\": [\n";
    first = true;
    for (const MeshDummyCore *core : cores) {
        if (!first)
            out << ",\n";
        first = false;
        // The core-level outcome lists and the per-instance ledger views below
        // are both projections of the one terminal ledger the cores own.
        std::vector<MeshDummyCore::TerminalRecord> terminals;
        for (const InstanceRecord &record : instance_records) {
            auto ledger = record.cores.find(core->archCoreId());
            if (ledger == record.cores.end())
                continue;
            terminals.insert(terminals.end(), ledger->second.terminals.begin(),
                             ledger->second.terminals.end());
        }
        auto emitIds = [&](MeshDummyCore::TerminalState state) {
            bool first_id = true;
            for (const MeshDummyCore::TerminalRecord &terminal : terminals) {
                if (terminal.state != state)
                    continue;
                if (!first_id)
                    out << ", ";
                first_id = false;
                out << terminal.command_id;
            }
        };
        out << "    {\"core_id\": " << core->archCoreId()
            << ", \"commands_issued\": " << meshJsonCounter(core->commandsIssued.value())
            << ", \"commands_completed\": " << meshJsonCounter(core->commandsCompleted.value())
            << ", \"commands_errored\": " << meshJsonCounter(core->commandsErrored.value())
            << ", \"commands_cancelled\": " << meshJsonCounter(core->commandsCancelled.value())
            << ", \"live_commands\": " << core->liveCommandCount()
            << ", \"live_dma_commands\": " << core->liveDmaCommandCount()
            << ", \"live_dma_tags\": " << core->liveDmaTagCount()
            << ", \"allocation_pins\": " << core->allocationPinCount()
            << ", \"events_published\": " << meshJsonCounter(core->eventsPublished.value())
            << ", \"gemm_cycles\": " << meshJsonCounter(core->gemmCycles.value())
            << ", \"reduce_cycles\": " << meshJsonCounter(core->reduceCycles.value())
            << ", \"sram_bank_conflicts\": " << meshJsonCounter(core->sramBankConflicts.value())
            << ", \"sram_service_cycles\": " << meshJsonCounter(core->sramServiceCycles.value())
            << ", \"sram_read_bytes\": " << core->sramTrafficBytes(false)
            << ", \"sram_write_bytes\": " << core->sramTrafficBytes(true)
            << ", \"sram_banks\": [";
        const auto &bank_reads = core->sramBankBytes(false);
        const auto &bank_writes = core->sramBankBytes(true);
        for (size_t bank = 0; bank < bank_reads.size(); ++bank) {
            if (bank != 0)
                out << ", ";
            out << "{\"bank_id\": " << bank
                << ", \"read_bytes\": " << bank_reads[bank]
                << ", \"write_bytes\": " << bank_writes[bank] << "}";
        }
        out << "], \"sram_bank_count\": " << bank_reads.size()
            << ", \"instance_error\": " << (core->instanceErrored() ? 1 : 0)
            << ", \"error_latch_tick\": " << core->errorLatchTick()
            << ", \"work_drained_tick\": " << core->workDrainedTick()
            << ", \"dma_idle\": " << (core->dmaEngine()->idle() ? 1 : 0)
            << ", \"completed_command_ids\": [";
        emitIds(MeshDummyCore::TerminalState::Completed);
        out << "], \"errored_command_ids\": [";
        emitIds(MeshDummyCore::TerminalState::Errored);
        out << "], \"cancelled_command_ids\": [";
        emitIds(MeshDummyCore::TerminalState::Cancelled);
        out << "], \"dispatch_plan\": [";
        {
            bool first_plan = true;
            for (const MeshDummyCore::CommandGeneration &entry :
                 core->dispatchPlan()) {
                if (!first_plan)
                    out << ", ";
                first_plan = false;
                out << "[" << entry.command_id << ", " << entry.generation
                    << "]";
            }
        }
        out << "], \"engine_occupancy\": {";
        {
            bool first_engine = true;
            for (MeshDummyCore::EngineKind kind :
                 {MeshDummyCore::EngineKind::Tensor,
                  MeshDummyCore::EngineKind::Vector,
                  MeshDummyCore::EngineKind::Reduce}) {
                if (!first_engine)
                    out << ", ";
                first_engine = false;
                out << "\"" << MeshDummyCore::engineName(kind) << "\": "
                    << core->engineOccupancy(kind);
            }
        }
        out << "}, \"command_issue_ticks\": {";
        {
            bool first_it = true;
            for (const auto &entry : core->commandIssueTicks()) {
                if (!first_it)
                    out << ", ";
                first_it = false;
                out << "\"" << entry.first.ordinal << "\": "
                    << entry.second;
            }
        }
        out << "}, \"command_done_ticks\": {";
        bool first_done = true;
        for (const auto &kv : core->commandDoneTicks()) {
            if (!first_done)
                out << ", ";
            first_done = false;
            out << "\"" << kv.first.ordinal << "\": " << kv.second;
        }
        out << "}}";
    }
    out << "\n  ],\n  \"watchdog_fired\": " << watchdog_fired_count << ",\n  \"digests\": [\n";
    first = true;
    for (const MeshDummyCore *core : cores) {
        for (const ComputeOutputObservation &digest : core->computeOutputs()) {
            if (!first)
                out << ",\n";
            first = false;
            out << "    {\"core_id\": " << core->archCoreId()
                << ", \"command_id\": " << digest.command.command_id
                << ", \"generation\": " << digest.command.generation
                << ", \"digest\": \"" << std::hex;
            for (int i = 0; i < 4; i++)
                out << std::setfill('0') << std::setw(8) << digest.digest_words[i];
            out << std::dec << std::setfill(' ') << "\"}";
        }
    }
    out << "\n  ],\n";
    out << "  \"output_span\": {\"semantic_digest\": \"";
    for (uint8_t byte : semantic_digest)
        out << std::hex << std::setfill('0') << std::setw(2) << unsigned(byte);
    out << std::dec << std::setfill(' ') << "\", \"base\": " << output_span_base
        << ", \"bytes\": " << output_span_bytes
        << ", \"digest_before\": \"" << output_digest_before
        << "\", \"digest_after\": \"" << outputSpanDigest() << "\"},\n";
    writeConservationJson(out);
    out << "\n}\n";
}

void
MeshDispatcher::writeConservationJson(std::ofstream &out)
{
    const std::pair<axi::AxiChannel, const char *> channels[] = {
        {axi::AxiChannel::Aw, "AW"}, {axi::AxiChannel::W, "W"},
        {axi::AxiChannel::B, "B"}, {axi::AxiChannel::Ar, "AR"},
        {axi::AxiChannel::R, "R"}};
    const auto writeBlocked = [&out, &channels](const auto &counts) {
        out << "{";
        bool first = true;
        for (const auto &[channel, name] : channels) {
            if (!first)
                out << ", ";
            first = false;
            out << '"' << name << "\": " << counts[unsigned(channel)];
        }
        out << "}";
    };
    out << "  \"core_clock_period_ticks\": " << clockPeriod() << ",\n";
    out << "  \"target_message_buffer_blocked_cycles\": {";
    bool first_target = true;
    for (const PeerSramAperture *aperture : apertures) {
        if (!first_target)
            out << ", ";
        first_target = false;
        out << '"' << aperture->name() << "\": ";
        writeBlocked(aperture->queueHighWater().messageBufferStallCycles);
    }
    for (const auto *endpoint : memory.endpoints()) {
        if (!first_target)
            out << ", ";
        first_target = false;
        out << '"' << endpoint->name() << "\": ";
        writeBlocked(endpoint->queueHighWater().messageBufferStallCycles);
    }
    out << "},\n  \"network_stalls\": {";
    const auto *garnet = dynamic_cast<ruby::garnet::GarnetNetwork *>(network);
    if (garnet) {
        bool first_channel = true;
        for (const auto &[channel, name] : channels) {
            if (!first_channel)
                out << ", ";
            first_channel = false;
            const unsigned vnet = unsigned(channel);
            out << '"' << name << "\": {\"router_credit_stall_vc_cycles\": "
                << garnet->routerCreditStalls(vnet)
                << ", \"router_vc_alloc_stall_vc_cycles\": "
                << garnet->vcAllocStalls(vnet)
                << ", \"ni_credit_stall_vc_cycles\": "
                << garnet->niCreditStalls(vnet)
                << ", \"ni_vc_busy_cycles\": " << garnet->niVcBusyCycles(vnet)
                << "}";
        }
    }
    out << "},\n";

    out << "  \"terminal\": \"" << terminalName() << "\",\n";
    out << "  \"terminal_tick\": " << curTick() << ",\n";
    out << "  \"terminal_instance\": " << instance_counter.value() << ",\n";
    out << "  \"terminal_error_core\": " << drained_error_core.value_or(erroredCoreId()) << ",\n";
    out << "  \"error_drained\": "
        << (program_terminal == ProgramTerminal::ERROR_DRAINED ||
                    program_terminal == ProgramTerminal::PRESTART_FAILED
                ? 1
                : 0)
        << ",\n";

    out << "  \"apertures\": [\n";
    bool first = true;
    for (const PeerSramAperture *aperture : apertures) {
        if (!first)
            out << ",\n";
        first = false;
        out << "    {\"core_id\": " << aperture->coreId()
            << ", \"committed_valid_bytes\": " << aperture->committedBytes()
            << ", \"error_drained_bytes\": " << aperture->errorDrainBytes()
            << ", \"sentinels\": ";
        writeSentinelRanges(out, aperture->sentinelRanges());
        out << "}";
    }
    out << "\n  ],\n";

    out << "  \"arch\": {\"base_digest\": \""
        << loader->arch().arch_digest_hex << "\", \"effective_digest\": \""
        << loader->effectiveArchDigest() << "\"},\n";

    out << "  \"loader_traffic\": ";
    {
        const LoaderTrafficSnapshot *begin = nullptr;
        const LoaderTrafficSnapshot *end = nullptr;
        bool captured = false;
        uint64_t begin_tick = 0, end_tick = 0;
        if (loader != nullptr) {
            const ControlPlaneWindow &window = loader->controlPlaneWindow();
            captured = window.captured;
            begin_tick = window.begin_tick;
            end_tick = window.end_tick;
            begin = &window.begin;
            end = &window.end;
        }
        if (!captured) {
            out << "null";
        } else {
            out << "{\"captured\": true, \"begin_tick\": " << begin_tick
                << ", \"end_tick\": " << end_tick << ", \"begin\": ";
            writeLoaderTrafficSnapshot(out, *begin);
            out << ", \"end\": ";
            writeLoaderTrafficSnapshot(out, *end);
            out << "}";
        }
    }
    out << ",\n";

    out << "  \"instances\": [\n";
    {
        bool first_instance = true;
        const auto emit_instance = [&](uint64_t instance_id, bool finalized,
                                       const std::map<uint16_t, MeshDummyCore::InstanceLedger> &ledgers,
                                       const std::vector<ApertureFrame> &apertures,
                                       const std::vector<BurstAttribution> &bursts,
                                       const std::vector<DestinationSegment> &destinations,
                                       const std::vector<ProgramScoreboard::BarrierGroup> &barriers,
                                       const std::optional<BatchFailureRecord> &failure_record) {
            if (!first_instance)
                out << ",\n";
            first_instance = false;
            out << "    {\"instance\": " << instance_id
                << ", \"finalized\": " << (finalized ? "true" : "false")
                << ", \"cores\": {";
            bool first_core = true;
            for (const auto &kv : ledgers) {
                if (!first_core)
                    out << ", ";
                first_core = false;
                const auto count_state = [&](MeshDummyCore::TerminalState state) {
                    size_t total = 0;
                    for (const MeshDummyCore::TerminalRecord &terminal :
                         kv.second.terminals)
                        if (terminal.state == state)
                            total++;
                    return total;
                };
                out << "\"" << kv.first << "\": {"
                    << "\"completed\": "
                    << count_state(MeshDummyCore::TerminalState::Completed)
                    << ", \"errored\": "
                    << count_state(MeshDummyCore::TerminalState::Errored)
                    << ", \"cancelled\": "
                    << count_state(MeshDummyCore::TerminalState::Cancelled)
                    << ", \"terminals\": [";
                bool first_terminal = true;
                for (const MeshDummyCore::TerminalRecord &terminal :
                     kv.second.terminals) {
                    if (!first_terminal)
                        out << ", ";
                    first_terminal = false;
                    const char *state = "completed";
                    if (terminal.state == MeshDummyCore::TerminalState::Errored)
                        state = "errored";
                    else if (terminal.state ==
                             MeshDummyCore::TerminalState::Cancelled)
                        state = "cancelled";
                    out << "{\"command_id\": " << terminal.command_id
                        << ", \"generation\": " << terminal.generation
                        << ", \"state\": \"" << state << "\"}";
                }
                out << "], \"resources\": {\"live_commands\": "
                    << kv.second.resources.live_commands
                    << ", \"live_dma_commands\": "
                    << kv.second.resources.live_dma_commands
                    << ", \"live_dma_tags\": "
                    << kv.second.resources.live_dma_tags
                    << ", \"allocation_pins\": "
                    << kv.second.resources.allocation_pins
                    << ", \"engine_occupancy\": {";
                bool first_engine = true;
                for (const auto &entry : kv.second.resources.engine_occupancy) {
                    if (!first_engine)
                        out << ", ";
                    first_engine = false;
                    out << "\"" << MeshDummyCore::engineName(entry.first)
                        << "\": " << entry.second;
                }
                out << "}}, \"observations\": ";
                writeObservationFrameJson(out, kv.second.observations);
                out << "}";
            }
            out << "}, \"apertures\": ";
            writeInstanceApertures(out, apertures);
            out << ", \"bursts\": ";
            writeInstanceBursts(out, bursts);
            out << ", \"destinations\": ";
            writeInstanceDestinations(out, destinations);
            out << ", \"barriers\": ";
            writeInstanceBarriers(out, barriers);
        if (failure_record) {
            const auto &failure = *failure_record;
            const auto wire = errorSourceKeyBytes(failure.first.source);
            out << ", \"failure\": {\"batch_id\": " << failure.batch_id
                << ", \"instance\": " << failure.program_instance_id
                << ", \"tick\": " << failure.first.tick
                << ", \"detail\": " << failure.first.code
                << ", \"source_wire\": \"";
            for (const auto byte : wire)
                out << std::hex << std::setw(2) << std::setfill('0') << unsigned(byte);
            out << std::dec << "\", \"cancel_tombstones\": [";
            for (size_t member = 0; member < failure.cancel_tombstone_snapshot.size(); ++member) {
                if (member) out << ",";
                out << failure.cancel_tombstone_snapshot[member];
            }
            out << "]}";
        }
            out << "}";
        };
        for (const InstanceRecord &record : instance_records) {
            emit_instance(record.instance.value(), record.finalized, record.cores,
                          record.apertures, record.bursts,
                          record.destinations, record.barriers, record.failure);
        }
        std::map<uint64_t, std::vector<ProgramScoreboard::BarrierGroup>> live_barriers;
        std::map<uint64_t, std::map<uint16_t, MeshDummyCore::InstanceLedger>> live;
        for (MeshDummyCore *core : cores) {
            const RuntimeObservations &observations = core->runtimeObservations();
            if (!observations.frameActive())
                continue;
            MeshDummyCore::InstanceLedger ledger;
            ledger.terminals = core->currentTerminals();
            ledger.resources = core->currentResources();
            ledger.observations = core->currentObservationFrame();
            live[observations.instanceId()][core->archCoreId()] = std::move(ledger);
            live_barriers[observations.instanceId()] = scoreboard.barrierHistory();
        }
        std::vector<ApertureFrame> live_apertures;
        for (const PeerSramAperture *aperture : apertures) {
            ApertureFrame frame;
            frame.core_id = aperture->coreId();
            frame.transfers = aperture->transferCoverage();
            live_apertures.push_back(std::move(frame));
        }
        const std::vector<BurstAttribution> live_bursts = instanceBursts();
        for (const auto &entry : live)
            emit_instance(entry.first, false, entry.second, live_apertures,
                          live_bursts, live_destinations,
                          live_barriers[entry.first], std::nullopt);
        out << "\n";
    }
    out << "  ],\n";

    out << "  \"dma_timings\": [\n";
    first = true;
    for (const MeshDummyCore *core : cores) {
        const AxiTensorDmaEngine *engine =
            dynamic_cast<const AxiTensorDmaEngine *>(core->dmaEngine());
        if (!engine)
            continue;
        for (const auto &entry : engine->descriptorTimingLog()) {
            if (!first)
                out << ",\n";
            first = false;
            out << "    {\"core_id\": " << core->archCoreId()
                << ", \"instance\": " << entry.descriptor.instance.value()
                << ", \"descriptor_id\": " << entry.descriptor.ordinal
                << ", \"command_id\": " << entry.command.command_id
                << ", \"generation\": " << entry.command.generation
                << ", \"dma_kind\": " << entry.dma_kind
                << ", \"domain\": " << uint32_t(entry.descriptor.domain)
                << ", \"region_group_id\": " << entry.descriptor.regionGroupId
                << ", \"region_id\": " << entry.descriptor.regionId
                << ", \"kind\": " << uint32_t(entry.descriptor.kind)
                << ", \"first_error_tick\": " << entry.timing.first_error_tick
                << ", \"first_ar_tick\": " << entry.timing.first_ar_tick
                << ", \"first_aw_tick\": " << entry.timing.first_aw_tick
                << ", \"first_w_tick\": " << entry.timing.first_w_tick
                << ", \"first_b_tick\": " << entry.timing.first_b_tick
                << ", \"last_r_tick\": " << entry.timing.last_r_tick
                << ", \"local_commit_tick\": " << entry.timing.local_commit_tick
                << ", \"done_tick\": " << entry.timing.done_tick << "}";
        }
    }
    out << "\n  ],\n";

    {
        uint64_t ar = 0, rlast = 0, refill = 0;
        uint32_t peak = 0;
        Tick first_rlast = 0;
        for (const MeshDummyCore *core : cores) {
            const auto *engine =
                dynamic_cast<const AxiTensorDmaEngine *>(core->dmaEngine());
            if (!engine)
                continue;
            ar += engine->readArAccepted();
            rlast += engine->readRlastConsumed();
            peak += engine->peakReadWindow();
            if (first_rlast == 0 ||
                (engine->firstRlastTick() != 0 &&
                 engine->firstRlastTick() < first_rlast))
                first_rlast = engine->firstRlastTick();
        }
        std::vector<Tick> ar_ticks;
        Tick first_credit_release = 0;
        for (const MeshDummyCore *core : cores) {
            const AxiTensorDmaEngine *engine =
                dynamic_cast<const AxiTensorDmaEngine *>(core->dmaEngine());
            const AxiGarnetBridge *bridge = engine ? engine->bridgeOf() : nullptr;
            if (!bridge)
                continue;
            std::vector<Tick> ticks = bridge->arAcceptTicks();
            ar_ticks.insert(ar_ticks.end(), ticks.begin(), ticks.end());
            const Tick release = bridge->firstCreditReleaseTick();
            if (release != 0 &&
                (first_credit_release == 0 || release < first_credit_release))
                first_credit_release = release;
        }
        std::sort(ar_ticks.begin(), ar_ticks.end());
        out << "  \"read_window\": {\"segment_peak\": " << peak
            << ", \"first_credit_release_tick\": " << first_credit_release
            << ", \"ar_accept_ticks\": [";
        for (size_t i = 0; i < ar_ticks.size(); ++i) {
            if (i)
                out << ", ";
            out << ar_ticks[i];
        }
        out << "]},\n";
    }

    out << "  \"burst_timings\": [";
    bool first_burst = true;
    for (const MeshDummyCore *core : cores) {
        const auto *engine = dynamic_cast<const AxiTensorDmaEngine *>(
            core->dmaEngine());
        if (!engine || !engine->bridgeOf())
            continue;
        for (const auto &[ordinal, ticks] : engine->bridgeOf()->burstTimings()) {
            if (!first_burst)
                out << ", ";
            first_burst = false;
            const auto commit = engine->readCommitTicks().find(ordinal);
            out << "{\"core_id\": " << core->archCoreId()
                << ", \"ordinal\": " << ordinal
                << ", \"channel\": \"" << (ticks.read ? "AR" : "AW") << '"'
                << ", \"axi_id\": " << ticks.axi_id
                << ", \"address\": " << ticks.address
                << ", \"beats\": " << ticks.beats
                << ", \"beat_bytes\": " << ticks.beat_bytes
                << ", \"ar_aw_tick\": " << ticks.addr_accept
                << ", \"response_tick\": " << ticks.resp_last
                << ", \"commit_tick\": "
                << (commit == engine->readCommitTicks().end() ? 0 : commit->second)
                << "}";
        }
    }
    out << "],\n";

    out << "  \"bridges\": [\n";
    first = true;
    for (const MeshDummyCore *core : cores) {
        const AxiTensorDmaEngine *engine =
            dynamic_cast<const AxiTensorDmaEngine *>(core->dmaEngine());
        if (!engine || !engine->bridgeOf())
            continue;
        const AxiGarnetBridge *bridge = engine->bridgeOf();
        if (!first)
            out << ",\n";
        first = false;
        const axi::AxiEndpointQueueHighWater high_water = bridge->queueHighWater();
        out << "    {\"core_id\": " << core->archCoreId()
            << ", \"clock_period_ticks\": " << bridge->clockPeriod()
            << ", \"aw_accepted\": " << bridge->acceptedWrites()
            << ", \"ar_accepted\": " << bridge->acceptedReads()
            << ", \"w_accepted\": " << bridge->counters().wAccepted
            << ", \"b_consumed\": " << bridge->completedWrites()
            << ", \"r_beats_consumed\": " << bridge->counters().rBeatsConsumed
            << ", \"b_error_count\": " << bridge->counters().bErrorCount
            << ", \"r_error_beats\": " << bridge->counters().rErrorBeats
            << ", \"read_bursts_submitted\": " << engine->submittedReadBursts()
            << ", \"write_bursts_submitted\": " << engine->submittedWriteBursts()
            << ", \"read_bursts_completed\": " << engine->completedReadBursts()
            << ", \"write_bursts_completed\": " << engine->completedWriteBursts()
            << ", \"error_read_bursts\": " << engine->errorReadBursts()
            << ", \"error_write_bursts\": " << engine->errorWriteBursts()
            << ", \"valid_read_bytes\": " << engine->validReadBytes()
            << ", \"valid_write_bytes\": " << engine->validWriteBytes()
            << ", \"pending_aw\": " << bridge->pendingWrites()
            << ", \"pending_ar\": " << bridge->pendingReads()
            << ", \"outstanding_writes\": " << bridge->outstandingWrites()
            << ", \"outstanding_reads\": " << bridge->outstandingReads()
            << ", \"peak_read_outstanding\": " << bridge->peakOutstandingReads()
            << ", \"peak_write_outstanding\": " << bridge->peakOutstandingWrites()
            << ", \"message_buffer_blocked_cycles\": ";
        writeBlocked(high_water.messageBufferStallCycles);
        out            << ", \"idle\": " << (bridge->idle() ? 1 : 0)
            << ", \"b_retire_order\": [";
        bool first_b = true;
        for (uint64_t ordinal : bridge->counters().bOrder) {
            if (!first_b)
                out << ", ";
            first_b = false;
            out << ordinal;
        }
        out << "]}";
    }
    out << "\n  ],\n";

    out << "  \"memory_endpoints\": [";
    bool first_memory = true;
    for (const auto *endpoint : memory.endpoints()) {
        if (!first_memory)
            out << ", ";
        first_memory = false;
        out << "{\"node\": " << endpoint->memoryRanges().front().dstNode
            << ", \"committed_valid_bytes\": " << endpoint->committedBytes()
            << ", \"error_drained_bytes\": " << endpoint->errorDrainBytes()
            << ", \"idle\": "
            << (endpoint->functionalIdle() ? "true" : "false")
            << ", \"seeds\": [";
        bool first_s = true;
        for (const auto &seed : endpoint->seedRows()) {
            if (!first_s)
                out << ", ";
            first_s = false;
            out << "{\"address\": " << seed.address
                << ", \"size\": " << seed.size
                << ", \"pattern\": " << int(seed.pattern)
                << ", \"digest\": \"" << seed.digest << "\"}";
        }
        out << "], \"sentinels\": ";
        writeSentinelRanges(out, endpoint->sentinelRanges());
        out << ", \"verifies\": [";
        bool first_v = true;
        for (const auto &verify : endpoint->verifyRows()) {
            if (!first_v)
                out << ", ";
            first_v = false;
            out << "{\"address\": " << verify.address
                << ", \"size\": " << verify.size
                << ", \"digest\": \"" << verify.digest << "\""
                << ", \"after16_digest\": \"" << verify.after_digest << "\""
                << ", \"bytes_hex\": "
                << (verify.bytes_hex.empty()
                        ? std::string("null")
                        : std::string("\"") + verify.bytes_hex + "\"")
                << "}";
        }
        out << "]}";
    }
    out << "],\n";

    out << "  \"serving_candidate_events\": [";
    for (size_t i = 0; i < candidate_events.size(); ++i) {
        const auto &event = candidate_events[i];
        if (i)
            out << ",";
        out << "{\"instance\":" << event.instance << ",\"tick\":" << event.tick
            << ",\"event\":\"" << event.event << "\"}";
    }
    out << "],\n";

    out << "  \"cache_reservation\": {\"waiting\": "
        << (cache_reservation_waiting ? 1 : 0)
        << ", \"attempts\": " << cache_reservation_attempts
        << ", \"identity_reassignments\": "
        << cache_identity_reassignments
        << ", \"commits\": " << cache_reservation_commits
        << ", \"candidate_waits\": " << candidate_waits
        << ", \"candidate_attaches\": " << candidate_attaches
        << ", \"candidate_resumes\": " << candidate_resumes
        << ", \"prestart_failures\": " << prestart_failures
        << ", \"starts\": " << batch_starts
        << ", \"pending\": [";
    {
        bool first_pending = true;
        for (const CacheReservationRequest &request :
             cache_reservation_queue) {
            if (!first_pending)
                out << ", ";
            first_pending = false;
            out << "{\"core_id\": " << request.core_id
                << ", \"request_id\": " << request.request_id
                << ", \"layers\": [";
            bool first_layer = true;
            for (const MoeWeightCache::LayerTags &layer : request.demand) {
                if (!first_layer)
                    out << ", ";
                first_layer = false;
                out << layer.layer_id;
            }
            out << "]}";
        }
    }
    out << "]},\n";
    out << "  \"moe\": ";
    if (loader != nullptr && (loader->program().header.required_features & mesh_abi::kFeatureDynamicMoeV1)) {
        out << "{\"gate_state\": \"" << overlayGateState()
            << "\", \"groups_exited\": " << overlay_exits_seen.size()
            << ", \"overlay_exits\": [";
        bool first_exit = true;
        for (uint32_t layer_id : overlay_exits_seen) {
            if (!first_exit)
                out << ", ";
            first_exit = false;
            out << layer_id;
        }
        out << "], \"regions\": [";
        bool first_region = true;
        for (const auto &region : loader->program().transport.moe_dynamic_regions) {
            for (const MeshDummyCore *core : cores) {
                if (core->archCoreId() != region.core_id)
                    continue;
                if (!first_region)
                    out << ", ";
                first_region = false;
                out << "{\"layer_id\": " << region.layer_id
                    << ", \"region_id\": " << region.region_id
                    << ", \"core_id\": " << region.core_id
                    << ", \"entry_tick\": " << core->overlayEntryTick()
                    << ", \"phase\": "
                    << uint32_t(core->overlayGatePhase(region.layer_id))
                    << ", \"armed\": "
                    << (core->overlayGateArmed(region.layer_id) ? 1 : 0)
                    << ", \"drained\": "
                    << (core->overlayDrained() ? 1 : 0);
                const MoeOverlayExecutor *executor =
                    core->overlayExecutor(region.layer_id);
                out << ", \"issued\": "
                    << (executor ? executor->issuedCommands() : 0)
                    << ", \"completed\": "
                    << (executor ? executor->completedCommands() : 0)
                    << ", \"sram_read_bytes\": "
                    << (executor
                            ? executor->sramReadBytesForRegion(
                                  region.region_id)
                            : 0)
                    << ", \"sram_write_bytes\": "
                    << (executor
                            ? executor->sramWriteBytesForRegion(
                                  region.region_id)
                            : 0)
                    << ", \"sram_service_cycles\": "
                    << (executor ? executor->sramServiceCycles() : 0)
                    << ", \"sram_bank_conflicts\": "
                    << (executor ? executor->sramBankConflicts() : 0)
                    << ", \"compute_cycles\": "
                    << (executor ? executor->computeCyclesTotal() : 0)
                    << ", \"compute_commits\": "
                    << (executor ? executor->computeCommits() : 0)
                    << ", \"copy_through_commands\": "
                    << (executor ? executor->copyThroughCommands() : 0)
                    << ", \"local_reduce_commands\": "
                    << (executor ? executor->localReduceCommands() : 0)
                    << ", \"uncommitted_views\": "
                    << (executor ? executor->uncommittedViews() : 0)
                    << ", \"sram_read_by_kind\": ";
                if (executor == nullptr) {
                    out << "{}";
                } else {
                    out << "{";
                    bool first_kind = true;
                    for (const auto &kind : executor->sramKindBytesForRegion(
                             region.region_id)) {
                        if (!first_kind)
                            out << ", ";
                        first_kind = false;
                        out << "\"" << kind.first << "\": "
                            << kind.second.first;
                    }
                    out << "}";
                }
                out << ", \"sram_write_by_kind\": ";
                if (executor == nullptr) {
                    out << "{}";
                } else {
                    out << "{";
                    bool first_kind = true;
                    for (const auto &kind : executor->sramKindBytesForRegion(
                             region.region_id)) {
                        if (!first_kind)
                            out << ", ";
                        first_kind = false;
                        out << "\"" << kind.first << "\": "
                            << kind.second.second;
                    }
                    out << "}";
                }
                out << "}";
            }
        }
        out << "], \"cache_state\": [";
        bool first_state = true;
        for (const MeshDummyCore *core : cores) {
            const MoeWeightCache *cache = core->weightCache();
            if (cache == nullptr)
                continue;
            if (!first_state)
                out << ", ";
            first_state = false;
            out << "{\"core_id\": " << core->archCoreId()
                << ", \"tokens_created\": " << cache->tokensCreated()
                << ", \"token_releases\": " << cache->tokenReleases()
                << ", \"tombstoned_subscribers\": "
                << cache->tombstonedSubscribers()
                << ", \"woken_subscribers\": " << cache->wokenSubscribers()
                << ", \"faulted_fill_terminals\": "
                << cache->faultedFillTerminals()
                << ", \"live_tokens\": " << cache->liveTokens()
                << ", \"live_obligations\": " << cache->obligations().size()
                << ", \"pending_fills\": " << cache->pendingFills()
                << ", \"pending_subscribers\": "
                << cache->pendingSubscribers()
                << ", \"mshr_free\": " << cache->mshrFree()
                << ", \"mshr_slots\": " << cache->mshrSlots()
                << ", \"eviction_free\": " << cache->evictionFree()
                << ", \"eviction_slots\": " << cache->evictionSlots()
                << ", \"obligation_free\": " << cache->obligationFree()
                << ", \"obligation_slots\": " << cache->obligationSlots()
                << ", \"subscriber_free\": " << cache->subscriberFree()
                << ", \"subscriber_slots\": " << cache->subscriberSlots()
                << ", \"valid_lines\": [";
            bool first_line = true;
            for (const auto &slot : cache->slots()) {
                if (slot.state != kCacheSlotValid)
                    continue;
                if (!first_line)
                    out << ", ";
                first_line = false;
                out << "{\"slot_id\": " << slot.slot_id
                    << ", \"weight_tag_index\": " << slot.weight_tag_index
                    << ", \"valid_bytes\": " << slot.valid_bytes;
                std::vector<uint8_t> payload(slot.valid_bytes);
                fatal_if(!core->functionalSramRead(cache->slotBase(slot.slot_id), payload.size(), payload.data()),
                         "cache payload cannot be read for its terminal report");
                out << ", \"content_sha256\": \"";
                for (const auto byte : agentSha256(payload))
                    out << std::hex << std::setfill('0') << std::setw(2) << unsigned(byte);
                out << std::dec << "\"}";
            }
            out << "], \"tombstones\": " << cache->tombstoneCount()
                << ", \"fills_retried\": " << core->cacheFillsRetried()
                << ", \"replay_state\": "
                << (cache->quiescent() ? cache->snapshotJson() : "null")
                << "}";
        }
        out << "], \"cache_fills\": [";
        bool first_fill = true;
        for (const MeshDummyCore *core : cores) {
            const MoeWeightCache *cache = core->weightCache();
            if (cache == nullptr)
                continue;
            for (const auto &fill : cache->fillLog()) {
                if (!first_fill)
                    out << ", ";
                first_fill = false;
                out << "{\"core_id\": " << core->archCoreId()
                    << ", \"weight_tag_index\": "
                    << fill.key.weight_tag_index
                    << ", \"fill_incarnation\": "
                    << fill.key.fill_incarnation
                    << ", \"bytes\": " << fill.bytes
                    << ", \"committed_bytes\": " << fill.committed_bytes
                    << ", \"slot_id\": " << fill.slot_id
                    << ", \"address\": " << fill.address
                    << ", \"done_tick\": " << fill.done_tick << "}";
            }
        }
        out << "]}";
    } else {
        out << "null";
    }
    out << ",\n";

    out << "  \"aggregate_network\": ";
    const auto *simple = dynamic_cast<const ruby::SimpleNetwork *>(network);
    const auto *hybrid = dynamic_cast<const ruby::garnet::GarnetNetwork *>(network);
    const auto *aggregate = hybrid ? hybrid->aggregateModel() : nullptr;
    if (simple || aggregate) {
        const auto snapshot = simple ? simple->quiescenceSnapshot() : aggregate->snapshot();
        out << "{\"queued_messages\": " << snapshot.queuedMessages
            << ", \"serialization_units\": " << snapshot.serializationUnits
            << ", \"forwarded_messages\": " << snapshot.forwardedMessages
            << ", \"wire_bytes\": " << snapshot.wireBytes
            << ", \"blocked_cycles\": " << snapshot.blockedCycles;
        const auto endpoints = simple ? simple->endpointSnapshot() : aggregate->endpointSnapshot();
        out << ", \"endpoint_messages\": " << endpoints.forwardedMessages
                << ", \"endpoint_wire_bytes\": " << endpoints.wireBytes;
        out << ", \"quiescent\": " << (snapshot.empty() ? 1 : 0) << "}";
    } else {
        out << "null";
    }
    out << ",\n  \"credit_ledger\": [";
    {
        const auto *garnet =
            dynamic_cast<const ruby::garnet::GarnetNetwork *>(network);
        bool first_entry = true;
        if (garnet) {
            for (const auto &entry : garnet->creditLedger()) {
                if (!first_entry)
                    out << ", ";
                first_entry = false;
                out << "{\"owner_kind\": " << entry.ownerKind
                    << ", \"owner_id\": " << entry.ownerId
                    << ", \"port_id\": " << entry.portId
                    << ", \"link_id\": " << entry.linkId
                    << ", \"vc\": " << entry.vc
                    << ", \"vnet\": " << entry.vnet
                    << ", \"initial\": " << entry.initial
                    << ", \"sent\": " << entry.sent
                    << ", \"returned\": " << entry.returned
                    << ", \"current\": " << entry.current
                    << ", \"depth\": " << entry.depth
                    << ", \"conserved\": "
                    << (entry.conserved() ? "true" : "false")
                    << ", \"restored\": "
                    << (entry.restored() ? "true" : "false") << "}";
            }
        }
    }
    out << "],\n";

    out << "  \"drain\": {\"active\": "
        << (drain_active ? "true" : "false")
        << ", \"instances_drained\": " << drained_instances
        << ", \"begin_tick\": " << drain_begin_tick
        << ", \"begin_pending\": " << drain_begin_pending
        << ", \"end_tick\": " << drain_end_tick
        << ", \"end_pending\": " << drain_end_pending << "},\n";

    out << "  \"garnet\": ";
    if (network) {
        const auto *garnet =
            dynamic_cast<const ruby::garnet::GarnetNetwork *>(network);
        if (!garnet) {
            out << "null";
        } else {
            const auto snapshot = garnet->quiescenceSnapshot();
            uint64_t packets = 0, bytes = 0;
            for (unsigned vnet = 0; vnet < garnet->getNumberOfVirtualNetworks(); ++vnet) {
                packets += garnet->packetsReceived(vnet);
                bytes += garnet->wireBytesReceived(vnet);
            }
            out << "{\"packets_received\": " << packets
                << ", \"wire_bytes_received\": " << bytes
                << ", \"ni_queued_flits\": " << snapshot.niQueuedFlits
                << ", \"ni_queued_messages\": " << snapshot.niQueuedMessages
                << ", \"router_buffered_flits\": " << snapshot.routerBufferedFlits
                << ", \"non_idle_input_vcs\": " << snapshot.nonIdleInputVcs
                << ", \"non_idle_output_vcs\": " << snapshot.nonIdleOutputVcs
                << ", \"data_link_pending_flits\": " << snapshot.dataLinkPendingFlits
                << ", \"credit_link_pending_credits\": "
                << snapshot.creditLinkPendingCredits
                << ", \"bridge_pending_items\": " << snapshot.bridgePendingItems
                << ", \"credit_deficit\": " << snapshot.creditDeficit
                << ", \"quiescent\": " << (snapshot.empty() ? 1 : 0) << "}";
            out << ",\n  \"garnet_traffic\": {\"flit_bytes\": "
                << garnet->getNiFlitSize() << ", \"vnets\": "
                << garnet->getNumberOfVirtualNetworks()
                << ", \"injected\": {\"packets\": [";
            for (unsigned vnet = 0; vnet < garnet->getNumberOfVirtualNetworks();
                 vnet++) {
                if (vnet != 0)
                    out << ", ";
                out << garnet->packetsInjected(vnet);
            }
            out << "], \"flits\": [";
            for (unsigned vnet = 0; vnet < garnet->getNumberOfVirtualNetworks();
                 vnet++) {
                if (vnet != 0)
                    out << ", ";
                out << garnet->flitsInjected(vnet);
            }
            out << "]}, \"received\": {\"packets\": [";
            for (unsigned vnet = 0; vnet < garnet->getNumberOfVirtualNetworks();
                 vnet++) {
                if (vnet != 0)
                    out << ", ";
                out << garnet->packetsReceived(vnet);
            }
            out << "], \"flits\": [";
            for (unsigned vnet = 0; vnet < garnet->getNumberOfVirtualNetworks();
                 vnet++) {
                if (vnet != 0)
                    out << ", ";
                out << garnet->flitsReceived(vnet);
            }
            out << "]}}";
        }
    } else {
        out << "null";
    }
}

}

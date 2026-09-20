#include "dev/ai_mesh/mesh_dispatcher.hh"

#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"

#include <algorithm>
#include <fstream>
#include <iomanip>
#include <map>
#include <numeric>
#include <set>

#include "base/logging.hh"
#include "base/statistics.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/axi_tensor_dma_engine.hh"
#include "dev/ai_mesh/dma_types.hh"
#include "dev/ai_mesh/mesh_clock.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_program_loader.hh"
#include "dev/ai_mesh/mock_axi_transport.hh"
#include "dev/ai_mesh/npu_memory_endpoint.hh"
#include "dev/ai_mesh/peer_sram_aperture.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "params/MeshDispatcher.hh"
#include "sim/cur_tick.hh"
#include "sim/sim_exit.hh"

namespace gem5
{
namespace ai_mesh
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

bool
MeshDispatcher::readDestinationBytes(uint16_t kind, uint16_t owner_core,
                                     uint64_t address, uint64_t size,
                                     std::vector<uint8_t> &out) const
{
    out.assign(size, 0);
    if (kind == 2) {
        if (endpoint == nullptr)
            return false;
        return endpoint->readBytes(address, size, out.data());
    }
    for (const PeerSramAperture *aperture : apertures) {
        if (aperture->coreId() != owner_core)
            continue;
        const uint64_t base = aperture->sramBase();
        if (address < base || size > aperture->sramBytes()
            || address - base > aperture->sramBytes() - size)
            return false;
        return aperture->read(address - base, size, out.data());
    }
    return false;
}

std::vector<DestinationSegment>
MeshDispatcher::instanceDestinations(
    const std::map<uint16_t, MeshDummyCore::InstanceLedger> &ledgers) const
{
    std::vector<DestinationSegment> segments;
    if (destination_bytes_limit == 0)
        return segments;
    for (const MeshDummyCore *core : cores) {
        const auto ledger = ledgers.find(core->archCoreId());
        if (ledger == ledgers.end())
            continue;
        for (const auto &execution :
             ledger->second.observations.descriptors) {
            const DecodedDmaDescriptor *descriptor =
                core->descriptorById(execution.descriptor_id);
            if (descriptor == nullptr)
                continue;
            const uint16_t kind = descriptor->kind;
            if (kind != 2 && kind != 3 && kind != 5)
                continue;
            if (descriptor->rows == 0 || descriptor->row_bytes == 0)
                continue;
            const uint16_t owner_core = kind == 3
                ? uint16_t(descriptor->dst.owner_core)
                : core->archCoreId();
            const uint64_t base = core->admittedEndpointAddress(
                execution.descriptor_id, false);
            for (uint32_t row = 0; row < descriptor->rows; row++) {
                DestinationSegment segment;
                segment.descriptor_id = execution.descriptor_id;
                segment.command_id = execution.command.command_id;
                segment.generation = execution.command.generation;
                segment.kind = kind;
                segment.row = row;
                segment.address = base
                    + uint64_t(row) * descriptor->dst_stride_bytes;
                segment.size = descriptor->row_bytes;
                if (segment.size > destination_bytes_limit)
                    continue;
                std::vector<uint8_t> bytes;
                if (!readDestinationBytes(kind, owner_core, segment.address,
                                          segment.size, bytes))
                    continue;
                segment.bytes_hex = bytesHex(bytes);
                segments.push_back(std::move(segment));
            }
        }
    }
    return segments;
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
            << ", \"size\": " << row.size
            << ", \"bytes_hex\": \"" << row.bytes_hex << "\"}";
    }
    out << "]";
}

MeshDispatcher::MeshDispatcher(const Params &p)
    : ClockedObject(p),
      loader(p.loader),
      cores(p.cores.begin(), p.cores.end()),
      transport(p.transport),
      apertures(p.apertures.begin(), p.apertures.end()),
      endpoint(p.endpoint),
      network(p.network),
      result_json_path(p.result_json),
      receiver_fault(p.receiver_fault),
      residency_fault(p.residency_fault),
      drain_fault(p.drain_fault),
      destination_bytes_limit(p.destination_bytes_limit),
      total_instances(p.instances),
      watchdog_ticks_value(p.watchdog_ticks),
      start_event(this),
      watchdog_event(this),
      drain_event(this)
{}

void MeshDispatcher::regStats()
{
    ClockedObject::regStats();
    instancesDispatched.name(name() + ".instances_dispatched").desc("Program instances dispatched");
    instancesCompleted.name(name() + ".instances_completed").desc("Program instances completed");
}

void MeshDispatcher::startup()
{
    // The atomic start goes out at the first legal core clock edge; all
    // loader startup work is guaranteed complete before this tick.
    schedule(&start_event, nextCoreEdge(*this));
}

void MeshDispatcher::dispatch()
{
    fatal_if(!loader->loaded(), "dispatcher started before loader installed a program");
    started = true;
    fatal_if(instance_error_latched && !pending_fences.empty(),
             "previous instance left live cross-core fences");
    instance_error_latched = false;
    instance_counter++;
    instancesDispatched++;
    halted_cores.clear();
    drain_active = false;
    drain_begin_tick = 0;
    drain_begin_pending = 0;
    drain_end_tick = 0;
    drain_end_pending = 0;
    for (PeerSramAperture *aperture : apertures)
        aperture->beginInstance();
    scoreboard.reset();
    for (MeshDummyCore *core : cores) {
        core->setDispatcher(this);
        core->dispatchInstance(instance_counter);
    }
    DPRINTF(AiMesh, "dispatcher: instance %u/%u started on %zu cores\n", instance_counter,
            total_instances, cores.size());
    if (watchdog_ticks_value > 0) {
        last_progress_snapshot = progressSample();
        if (watchdog_event.scheduled())
            deschedule(&watchdog_event);
        schedule(&watchdog_event, curTick() + watchdog_ticks_value);
    }
}

uint64_t MeshDispatcher::progressSample() const
{
    // One metric for the arming sample and for every later sample: a class of
    // work that advances between two checks counts in both (spec 8.8).
    uint64_t progress = dma_tags_retired;
    for (const MeshDummyCore *core : cores)
        progress += core->progressSnapshot();
    // Credit return and flit ejection are real progress: without them a
    // network that is still delivering after every core halted would look
    // stalled to the watchdog.
    if (const auto *garnet =
            dynamic_cast<const ruby::garnet::GarnetNetwork *>(network)) {
        const auto snapshot = garnet->quiescenceSnapshot();
        progress += snapshot.creditLinkPendingCredits * 2 +
                    snapshot.dataLinkPendingFlits * 2 + snapshot.niQueuedFlits +
                    snapshot.routerBufferedFlits + snapshot.creditDeficit;
        for (const auto &entry : garnet->creditLedger())
            progress += entry.returned + entry.sent;
    }
    if (transport)
        for (const auto &entry : transport->actualTraffic()) {
            const ActualTraffic &row = entry.second;
            progress += row.read_bytes + row.write_bytes + row.p2p_bytes +
                        row.fill_bytes;
        }
    return progress;
}

void MeshDispatcher::bindScoreboardTo(MeshDummyCore *core)
{
    core->setScoreboard(&scoreboard);
}

const MeshDummyCore *
MeshDispatcher::core(uint16_t core_id) const
{
    for (const MeshDummyCore *candidate : cores)
        if (candidate->archCoreId() == core_id)
            return candidate;
    return nullptr;
}

void MeshDispatcher::notifyCoreHalted(uint16_t core_id)
{
    halted_cores.insert(core_id);
    checkAllHalted();
}

bool
MeshDispatcher::drainSatisfied()
{
    if (const auto *garnet =
            dynamic_cast<const ruby::garnet::GarnetNetwork *>(network)) {
        if (!garnet->isQuiescent())
            return false;
        for (const auto &entry : garnet->creditLedger())
            if (!entry.restored())
                return false;
    }
    for (const MeshDummyCore *core : cores) {
        const auto *engine =
            dynamic_cast<const AxiTensorDmaEngine *>(core->dmaEngine());
        if (engine && engine->bridgeOf() && !engine->bridgeOf()->idle())
            return false;
    }
    for (const PeerSramAperture *aperture : apertures)
        if (aperture->liveExpectations())
            return false;
    if (endpoint && !endpoint->functionalIdle())
        return false;
    return true;
}

std::vector<const MeshDummyCore::InstanceLedger *>
MeshDispatcher::instanceLedgers(uint16_t core_id) const
{
    std::vector<const MeshDummyCore::InstanceLedger *> ledgers;
    for (const InstanceRecord &record : instance_records) {
        const auto found = record.cores.find(core_id);
        if (found != record.cores.end())
            ledgers.push_back(&found->second);
    }
    return ledgers;
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
        out << "{\"instance\": " << row.instance
            << ", \"core_id\": " << row.core_id
            << ", \"command_id\": " << row.command_id
            << ", \"generation\": " << row.generation
            << ", \"descriptor_id\": " << row.descriptor_id
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

std::vector<BurstAttribution>
MeshDispatcher::instanceBursts() const
{
    std::vector<BurstAttribution> rows;
    for (const MeshDummyCore *core : cores) {
        const auto *engine = dynamic_cast<const AxiTensorDmaEngine *>(
            core->dmaEngine());
        if (engine == nullptr)
            continue;
        const AxiGarnetBridge *bridge = engine->bridgeOf();
        for (BurstAttribution row : engine->instanceBursts()) {
            if (bridge != nullptr) {
                const AxiGarnetBridge::OrdinalTicks *ticks =
                    bridge->ordinalTicks(row.ordinal);
                if (ticks != nullptr)
                    row.ar_aw_tick = ticks->addr_accept;
            }
            rows.push_back(row);
        }
    }
    return rows;
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
        out << "{\"event_id\": " << group.event_id
            << ", \"generation\": " << group.generation
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
            out << "{\"participant\": " << arrival.participant
                << ", \"tick\": " << arrival.tick << "}";
        }
        out << "]}";
    }
    out << "]";
}

const MeshDummyCore::InstanceLedger *
MeshDispatcher::lastInstanceLedger(uint16_t core_id) const
{
    for (auto record = instance_records.rbegin();
         record != instance_records.rend(); ++record) {
        const auto found = record->cores.find(core_id);
        if (found != record->cores.end())
            return &found->second;
    }
    return nullptr;
}

void MeshDispatcher::writeResultJson()
{
    // Drain-time sampling of the real target owners happens exactly once,
    // after the last instance quiesced; everything below only serializes it.
    if (endpoint)
        endpoint->computeVerifyDigests();
    for (PeerSramAperture *aperture : apertures)
        aperture->sampleSentinels();
    if (result_json_path.empty())
        return;
    std::ofstream out(result_json_path);
    out << "{\n  \"transport\": [\n";
    bool first = true;
    std::map<uint32_t, ActualTraffic> traffic;
    std::set<const std::map<uint32_t, ActualTraffic> *> sources;
    for (const MeshDummyCore *core : cores) {
        const auto &rows = core->dmaEngine()->actualTraffic();
        if (!sources.insert(&rows).second)
            continue;
        for (const auto &row : rows)
            fatal_if(!traffic.emplace(row).second,
                     "descriptor %u has multiple traffic owners", row.first);
    }
    for (const auto &row : traffic) {
        if (!first)
            out << ",\n";
        first = false;
        out << "    {\"descriptor_id\": " << row.first
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
            << ", \"commands_issued\": " << core->commandsIssued.value()
            << ", \"commands_completed\": " << core->commandsCompleted.value()
            << ", \"commands_errored\": " << core->commandsErrored.value()
            << ", \"commands_cancelled\": " << core->commandsCancelled.value()
            << ", \"live_commands\": " << core->liveCommandCount()
            << ", \"live_dma_commands\": " << core->liveDmaCommandCount()
            << ", \"live_dma_tags\": " << core->liveDmaTagCount()
            << ", \"allocation_pins\": " << core->allocationPinCount()
            << ", \"events_published\": " << core->eventsPublished.value()
            << ", \"gemm_cycles\": " << core->gemmCycles.value()
            << ", \"reduce_cycles\": " << core->reduceCycles.value()
            << ", \"sram_bank_conflicts\": " << core->sramBankConflicts.value()
            << ", \"sram_service_cycles\": " << core->sramServiceCycles.value()
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
                out << "\"" << entry.first << "\": " << entry.second;
            }
        }
        out << "}, \"command_done_ticks\": {";
        bool first_done = true;
        for (const auto &kv : core->commandDoneTicks()) {
            if (!first_done)
                out << ", ";
            first_done = false;
            out << "\"" << kv.first << "\": " << kv.second;
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
    writeConservationJson(out);
    out << "\n}\n";
}

bool
MeshDispatcher::anyCoreErrored() const
{
    for (const MeshDummyCore *core : cores)
        if (core->instanceErrored())
            return true;
    return false;
}

bool
MeshDispatcher::anyInstanceErrored() const
{
    for (const InstanceRecord &record : instance_records)
        for (const auto &entry : record.cores)
            for (const MeshDummyCore::TerminalRecord &terminal :
                 entry.second.terminals)
                if (terminal.state == MeshDummyCore::TerminalState::Errored)
                    return true;
    return anyCoreErrored();
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
    if (endpoint) {
        if (!first_target)
            out << ", ";
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

    out << "  \"error_drained\": " << (anyInstanceErrored() ? 1 : 0) << ",\n";

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
        const auto emit_instance = [&](uint32_t instance_id, bool finalized,
                                       const std::map<uint16_t, MeshDummyCore::InstanceLedger> &ledgers,
                                       const std::vector<ApertureFrame> &apertures,
                                       const std::vector<BurstAttribution> &bursts,
                                       const std::vector<DestinationSegment> &destinations,
                                       const std::vector<ProgramScoreboard::BarrierGroup> &barriers) {
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
            out << "}";
        };
        for (const InstanceRecord &record : instance_records) {
            emit_instance(record.instance_id, record.finalized, record.cores,
                          record.apertures, record.bursts,
                          record.destinations, record.barriers);
        }
        std::map<uint32_t, std::vector<ProgramScoreboard::BarrierGroup>> live_barriers;
        std::map<uint32_t, std::map<uint16_t, MeshDummyCore::InstanceLedger>> live;
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
                          live_bursts, instanceDestinations(entry.second),
                          live_barriers[entry.first]);
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
        for (const auto &kv : engine->descriptorTimings()) {
            if (!first)
                out << ",\n";
            first = false;
            out << "    {\"core_id\": " << core->archCoreId()
                << ", \"descriptor_id\": " << kv.first
                << ", \"first_ar_tick\": " << kv.second.first_ar_tick
                << ", \"first_aw_tick\": " << kv.second.first_aw_tick
                << ", \"first_w_tick\": " << kv.second.first_w_tick
                << ", \"first_b_tick\": " << kv.second.first_b_tick
                << ", \"last_r_tick\": " << kv.second.last_r_tick
                << ", \"local_commit_tick\": " << kv.second.local_commit_tick
                << ", \"done_tick\": " << kv.second.done_tick << "}";
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

    out << "  \"memory_endpoint\": ";
    if (endpoint) {
        out << "{\"committed_valid_bytes\": " << endpoint->committedBytes()
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
    } else {
        out << "null";
    }
    out << ",\n";

    out << "  \"credit_ledger\": [";
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
            out << "{\"ni_queued_flits\": " << snapshot.niQueuedFlits
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

void MeshDispatcher::checkDrain()
{
    if (!drain_active || drain_end_tick != 0)
        return;
    checkAllHalted();
    if (drain_active && drain_end_tick == 0 && !drain_event.scheduled())
        schedule(&drain_event, nextCoreEdge(*this));
}

void MeshDispatcher::checkWatchdog()
{
    if (!started)
        return;
    const uint64_t progress = progressSample();
    bool all_done = true;
    for (const MeshDummyCore *core : cores)
        if (!core->halted() || !core->quiescent())
            all_done = false;
    if (all_done) {
        // Every core stopped, but the unified drain gate may still be waiting
        // for network credits, bridge queues, peer expectations or the target
        // endpoint.  Re-evaluate it on the next real progress event instead of
        // waiting for another halt notification that will never come.
        checkAllHalted();
        if (drain_end_tick != 0)
            return;  // the gate closed and the exit was requested
        if (progress != last_progress_snapshot) {
            last_progress_snapshot = progress;
            if (!watchdog_event.scheduled())
                schedule(&watchdog_event, curTick() + watchdog_ticks_value);
            return;
        }
        // The drain itself made no progress: report what is still pending
        // instead of running to the simulation limit.
        watchdog_fired_count++;
        std::string drain_graph = "  drain pending:";
        if (const auto *garnet =
                dynamic_cast<const ruby::garnet::GarnetNetwork *>(network)) {
            const auto snapshot = garnet->quiescenceSnapshot();
            drain_graph +=
                " ni_flits=" + std::to_string(snapshot.niQueuedFlits) +
                " router_flits=" + std::to_string(snapshot.routerBufferedFlits) +
                " data_link=" + std::to_string(snapshot.dataLinkPendingFlits) +
                " credit_link=" +
                std::to_string(snapshot.creditLinkPendingCredits) +
                " bridge_items=" + std::to_string(snapshot.bridgePendingItems) +
                " deficit=" + std::to_string(snapshot.creditDeficit);
            uint64_t unrestored = 0;
            for (const auto &entry : garnet->creditLedger())
                if (!entry.restored())
                    unrestored++;
            drain_graph += " unrestored_links=" + std::to_string(unrestored);
        }
        for (const MeshDummyCore *core : cores) {
            const auto *engine =
                dynamic_cast<const AxiTensorDmaEngine *>(core->dmaEngine());
            if (engine && engine->bridgeOf() && !engine->bridgeOf()->idle())
                drain_graph += " bridge_core_" +
                               std::to_string(core->archCoreId()) + "_busy";
        }
        for (const PeerSramAperture *aperture : apertures)
            if (aperture->liveExpectations())
                drain_graph += " aperture_" +
                               std::to_string(aperture->coreId()) +
                               "_live_expectation";
        if (endpoint && !endpoint->functionalIdle())
            drain_graph += " endpoint_busy";
        drain_graph += "\n";
        RuntimeDiagnostic record;
        record.code = mesh_diagnostics::DiagnosticCode::E_RUNTIME_DEADLOCK;
        record.stage = DiagnosticStage::Watchdog;
        record.context = {{"instance", std::to_string(instance_counter)},
                          {"watchdog_ticks", std::to_string(watchdog_ticks_value)},
                          {"phase", "drain"}};
        const std::string business =
            "E_RUNTIME_DEADLOCK: the drain made no progress within " +
            std::to_string(watchdog_ticks_value) + " ticks\n" + drain_graph;
        reportRuntimeDiagnostic(record, business);
        fatal("%s", business.c_str());
    }
    if (progress == last_progress_snapshot) {
        watchdog_fired_count++;
        std::string graph;
        for (const MeshDummyCore *core : cores)
            graph += core->waitForGraph() + "\n";
        // Instance-scoped fences wait on tags owned by any core, so their
        // relation is reported by the owner of that cross-core wait.
        for (const PendingFence &fence : pending_fences) {
            graph += "  fence cmd=" + std::to_string(fence.command_id) +
                     " core=" + std::to_string(fence.core_id) +
                     " scope=instance tags=";
            bool first_tag = true;
            for (const auto &waiter : fence.waiting) {
                if (!first_tag)
                    graph += ",";
                first_tag = false;
                uint32_t owner = 0;
                for (const MeshDummyCore *core : cores)
                    if (core->archCoreId() == waiter.first) {
                        owner = core->tagOwner(waiter.second);
                        break;
                    }
                graph += std::to_string(waiter.first) + ":" +
                         std::to_string(waiter.second) + "(cmd" +
                         std::to_string(owner) + ")";
            }
            if (first_tag)
                graph += "none";
            graph += "\n";
        }
        RuntimeDiagnostic record;
        record.code = mesh_diagnostics::DiagnosticCode::E_RUNTIME_DEADLOCK;
        record.stage = DiagnosticStage::Watchdog;
        record.context = {
            {"instance", std::to_string(instance_counter)},
            {"watchdog_ticks", std::to_string(watchdog_ticks_value)}};
        const std::string business =
            "E_RUNTIME_DEADLOCK: no runtime progress within " +
            std::to_string(watchdog_ticks_value) + " ticks\n" + graph;
        reportDiagnostic(loader->diagnostics(), record, business);
        fatal("%s", business.c_str());
    }
    last_progress_snapshot = progress;
    schedule(&watchdog_event, curTick() + watchdog_ticks_value);
}

bool MeshDispatcher::registerFence(MeshDummyCore *core, uint32_t command_id,
                                   uint32_t signal_event)
{
    // ALL_INSTANCE fences observe every core's pre-fence live tags; the
    // fence completes when all of them retire (spec 5.6).
    PendingFence fence;
    fence.core_id = core->archCoreId();
    fence.command_id = command_id;
    fence.signal_event = signal_event;
    for (MeshDummyCore *candidate : cores)
        for (const auto &kv : candidate->liveDmaTagSnapshot())
            fence.waiting.emplace(candidate->archCoreId(), kv.first);
    if (fence.waiting.empty())
        return false;
    pending_fences.push_back(fence);
    return true;
}

void MeshDispatcher::onDmaTagRetired(uint16_t core_id, uint64_t tag)
{
    dma_tags_retired++;
    if (pending_fences.empty())
        return;
    const std::pair<uint16_t, uint64_t> key(core_id, tag);
    for (auto it = pending_fences.begin(); it != pending_fences.end();) {
        it->waiting.erase(key);
        if (!it->waiting.empty()) {
            ++it;
            continue;
        }
        const PendingFence fence = *it;
        it = pending_fences.erase(it);
        for (MeshDummyCore *core : cores)
            if (core->archCoreId() == fence.core_id) {
                core->completeFence(fence.command_id, fence.signal_event);
                break;
            }
    }
}

void MeshDispatcher::cancelFencesOf(uint16_t core_id)
{
    for (auto it = pending_fences.begin(); it != pending_fences.end();) {
        if (it->core_id == core_id)
            it = pending_fences.erase(it);
        else
            ++it;
    }
}

void MeshDispatcher::armTransferExpectation(uint32_t transfer_id)
{
    if (apertures.empty())
        return;  // mock runtime: peer commits flow through the transport
    const auto *plan = loader->transferPlan(transfer_id);
    fatal_if(plan == nullptr, "no transfer plan for transfer %u", transfer_id);
    for (PeerSramAperture *aperture : apertures)
        if (aperture->coreId() == plan->receiver_core) {
            aperture->expectTransfer(transfer_id, plan->ranges);
            return;
        }
    fatal("transfer %u targets core %u without an SRAM aperture",
          transfer_id, plan->receiver_core);
}

bool
MeshDispatcher::expectsTransfer(uint32_t transfer_id) const
{
    for (const PeerSramAperture *aperture : apertures)
        if (aperture->expects(transfer_id))
            return true;
    return false;
}

bool
MeshDispatcher::readFunctional(uint64_t address, uint64_t size,
                               uint8_t *out) const
{
    for (const PeerSramAperture *aperture : apertures) {
        const uint64_t base = aperture->sramBase();
        if (address < base || size > aperture->sramBytes()
            || address - base > aperture->sramBytes() - size)
            continue;
        return aperture->read(address - base, size, out);
    }
    return false;
}

void MeshDispatcher::latchInstanceError(MeshDummyCore *source, Tick tick,
                                        const InstanceFailureFacts &facts)
{
    if (instance_error_latched)
        return;
    instance_error_latched = true;
    instance_root_facts = facts;
    RuntimeDiagnostic record;
    record.code = mesh_diagnostics::DiagnosticCode::E_AXI_RESPONSE;
    record.stage = DiagnosticStage::Runtime;
    record.context = {
        {"instance", std::to_string(instance_counter)},
        {"core_id", std::to_string(facts.core_id)},
        {"command_id", std::to_string(facts.command_id)},
        {"generation", std::to_string(facts.generation)},
        {"descriptor_id", std::to_string(facts.descriptor_id)},
        {"dma_kind", std::to_string(facts.dma_kind)},
        {"status", dmaStatusName(facts.status)}};
    if (facts.transfer_id != 0)
        record.context.push_back(
            {"transfer_id", std::to_string(facts.transfer_id)});
    reportDiagnostic(
        loader->diagnostics(), record,
        "instance " + std::to_string(instance_counter) +
            " failed with E_AXI_RESPONSE on core " +
            std::to_string(facts.core_id) + " command " +
            std::to_string(facts.command_id) + " generation " +
            std::to_string(facts.generation) + " descriptor " +
            std::to_string(facts.descriptor_id) + " dma_kind " +
            std::to_string(facts.dma_kind) + " status " +
            dmaStatusName(facts.status));
    scoreboard.cancelOpen(tick);
    for (PeerSramAperture *aperture : apertures)
        aperture->abandonAllExpectations();
    for (MeshDummyCore *core : cores)
        core->onInstanceError(core == source ? tick : curTick());
}

void MeshDispatcher::reportRuntimeDiagnostic(const RuntimeDiagnostic &record,
                                               const std::string &business)
{
    reportDiagnostic(loader->diagnostics(), record, business);
}

void MeshDispatcher::markReceiverAllocationValid(uint16_t core_id,
                                                uint32_t allocation_id)
{
    for (MeshDummyCore *core : cores)
        if (core->archCoreId() == core_id) {
            core->markAllocationValid(allocation_id);
            return;
        }
}

void MeshDispatcher::routePeerCommit(uint16_t peer_core, uint32_t transfer_id)
{
    MeshDummyCore *target = nullptr;
    for (MeshDummyCore *core : cores)
        if (core->archCoreId() == peer_core) {
            target = core;
            break;
        }
    if (target == nullptr)
        return;
    // Test-only notification routing defects are injected at the delivery
    // boundary: the receiver record is always produced by the actual handler.
    if (receiver_fault == "receive_drop")
        return;
    if (receiver_fault == "receive_redirect") {
        for (MeshDummyCore *core : cores)
            if (core != target) {
                core->onTransferCommitted(transfer_id);
                return;
            }
        return;
    }
    target->onTransferCommitted(transfer_id);
    if (receiver_fault == "receive_duplicate")
        target->onTransferCommitted(transfer_id);
}

void MeshDispatcher::checkAllHalted()
{
    if (!started)
        return;
    for (MeshDummyCore *core : cores) {
        if (!core->halted() || !core->quiescent())
            return;
        if (!halted_cores.count(core->archCoreId()))
            return;
    }
    // HALT only earns the right to drain: the instance is archived and the
    // program exits only once every real owner has emptied.  The drain window
    // opens the first time every core has stopped, before the network is
    // necessarily empty, so a deferred exit is visible.
    if (!drain_active) {
        drain_active = true;
        drain_begin_tick = curTick();
        // Test-only: lose the next credit return at the real delivery boundary
        // once the drain is open, so the gate really has to wait for the
        // ledger to restore.
        if (drain_fault == "drop_credit") {
            auto *garnet = dynamic_cast<ruby::garnet::GarnetNetwork *>(network);
            fatal_if(garnet == nullptr,
                     "drain_fault drop_credit needs a Garnet network");
            garnet->armCreditDropFault(1);
        }
        const auto *garnet =
            dynamic_cast<const ruby::garnet::GarnetNetwork *>(network);
        const auto begin = garnet ? garnet->quiescenceSnapshot()
                                  : ruby::garnet::GarnetQuiescenceSnapshot();
        drain_begin_pending = begin.creditLinkPendingCredits +
                              begin.dataLinkPendingFlits + begin.niQueuedFlits +
                              begin.routerBufferedFlits +
                              begin.nonIdleInputVcs + begin.nonIdleOutputVcs;
    }
    if (!drainSatisfied()) {
        // Keep evaluating on real clock edges until every owner is empty.
        if (!drain_event.scheduled())
            schedule(&drain_event, nextCoreEdge(*this));
        return;
    }
    if (drain_active && drain_end_tick == 0) {
        drain_end_tick = curTick();
        const auto *garnet =
            dynamic_cast<const ruby::garnet::GarnetNetwork *>(network);
        const auto end = garnet ? garnet->quiescenceSnapshot()
                                : ruby::garnet::GarnetQuiescenceSnapshot();
        drain_end_pending = end.creditLinkPendingCredits +
                            end.dataLinkPendingFlits + end.niQueuedFlits +
                            end.routerBufferedFlits + end.nonIdleInputVcs +
                            end.nonIdleOutputVcs;
    }
    {
        InstanceRecord record;
        record.instance_id = instance_counter;
        record.finalized = true;
        record.bursts = instanceBursts();
        record.barriers = scoreboard.barrierHistory();
        for (MeshDummyCore *core : cores)
            record.cores[core->archCoreId()] = core->takeInstanceLedger();
        record.destinations = instanceDestinations(record.cores);
        for (const PeerSramAperture *aperture : apertures) {
            ApertureFrame frame;
            frame.core_id = aperture->coreId();
            frame.transfers = aperture->transferCoverage();
            record.apertures.push_back(std::move(frame));
        }
        instance_records.push_back(std::move(record));
    }
    drained_instances++;
    if (instance_counter < total_instances) {
        // Same immutable CommandROM, fresh per-instance state (spec 17.2.24).
        schedule(&start_event, nextCoreEdge(*this));
        return;
    }
    instancesCompleted++;
    std::string cause = anyInstanceErrored()
        ? "MESH_PROGRAM_ERROR_DRAINED: " + loader->programName()
        : "MESH_PROGRAM_DONE: " + loader->programName();
    DPRINTF(AiMesh, "dispatcher: %s\n", cause.c_str());
    writeResultJson();
    exitSimLoop(cause.c_str());
}

} // namespace ai_mesh
} // namespace gem5

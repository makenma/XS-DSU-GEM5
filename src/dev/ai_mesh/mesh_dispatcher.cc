#include "dev/ai_mesh/mesh_dispatcher.hh"

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

MeshDispatcher::MeshDispatcher(const Params &p)
    : ClockedObject(p),
      loader(p.loader),
      cores(p.cores.begin(), p.cores.end()),
      transport(p.transport),
      apertures(p.apertures.begin(), p.apertures.end()),
      endpoint(p.endpoint),
      network(p.network),
      result_json_path(p.result_json),
      total_instances(p.instances),
      watchdog_ticks_value(p.watchdog_ticks),
      start_event(this),
      watchdog_event(this)
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
    schedule(&start_event, clockEdge() + 1);
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
        uint64_t progress = 0;
        for (const MeshDummyCore *core : cores)
            progress += core->commandsCompleted.value();
        last_progress_snapshot = progress;
        if (watchdog_event.scheduled())
            deschedule(&watchdog_event);
        schedule(&watchdog_event, curTick() + watchdog_ticks_value);
    }
}

void MeshDispatcher::bindScoreboardTo(MeshDummyCore *core)
{
    core->setScoreboard(&scoreboard);
}

void MeshDispatcher::notifyCoreHalted(uint16_t core_id)
{
    halted_cores.insert(core_id);
    checkAllHalted();
}

void MeshDispatcher::writeResultJson()
{
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
        out << "    {\"core_id\": " << core->archCoreId()
            << ", \"commands_issued\": " << core->commandsIssued.value()
            << ", \"commands_completed\": " << core->commandsCompleted.value()
            << ", \"commands_errored\": " << core->commandsErrored.value()
            << ", \"commands_cancelled\": " << core->commandsCancelled.value()
            << ", \"live_commands\": " << (core->quiescent() ? 0 : 1)
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
        bool first_id = true;
        for (uint32_t id : core->completed_command_ids) {
            if (!first_id)
                out << ", ";
            first_id = false;
            out << id;
        }
        out << "], \"errored_command_ids\": [";
        first_id = true;
        for (uint32_t id : core->errored_command_ids) {
            if (!first_id)
                out << ", ";
            first_id = false;
            out << id;
        }
        out << "], \"cancelled_command_ids\": [";
        first_id = true;
        for (uint32_t id : core->cancelled_command_ids) {
            if (!first_id)
                out << ", ";
            first_id = false;
            out << id;
        }
        out << "], \"command_issue_ticks\": {";
        {
            bool first_it = true;
            for (const auto &entry : core->command_issue_ticks) {
                if (!first_it)
                    out << ", ";
                first_it = false;
                out << "\"" << entry.first << "\": " << entry.second;
            }
        }
        out << "}, \"command_done_ticks\": {";
        first_id = true;
        for (const auto &kv : core->commandDoneTicks()) {
            if (!first_id)
                out << ", ";
            first_id = false;
            out << "\"" << kv.first << "\": " << kv.second;
        }
        out << "}}";
    }
    out << "\n  ],\n  \"watchdog_fired\": " << watchdog_fired_count << ",\n  \"digests\": [\n";
    first = true;
    for (const MeshDummyCore *core : cores) {
        for (const auto &digest : core->computeDigests) {
            if (!first)
                out << ",\n";
            first = false;
            out << "    {\"core_id\": " << core->archCoreId()
                << ", \"command_id\": " << digest.command_id
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

    out << "  \"error_drained\": " << (anyCoreErrored() ? 1 : 0) << ",\n";

    out << "  \"apertures\": [\n";
    bool first = true;
    for (const PeerSramAperture *aperture : apertures) {
        if (!first)
            out << ",\n";
        first = false;
        out << "    {\"core_id\": " << aperture->coreId()
            << ", \"committed_valid_bytes\": " << aperture->committedBytes()
            << ", \"error_drained_bytes\": " << aperture->errorDrainBytes()
            << ", \"transfers\": [";
        bool first_t = true;
        for (const auto &kv : aperture->transferCommitTicks()) {
            if (!first_t)
                out << ", ";
            first_t = false;
            out << "{\"transfer_id\": " << kv.first
                << ", \"commit_tick\": " << kv.second << "}";
        }
        out << "]}";
    }
    out << "\n  ],\n";

    out << "  \"arch\": {\"base_digest\": \""
        << loader->arch().arch_digest_hex << "\", \"effective_digest\": \""
        << loader->effectiveArchDigest() << "\"},\n";

    out << "  \"instances\": [\n";
    for (size_t i = 0; i < instance_records.size(); i++) {
        out << "    {\"instance\": " << (i + 1) << ", \"cores\": {";
        bool first_core = true;
        for (const auto &kv : instance_records[i].cores) {
            if (!first_core)
                out << ", ";
            first_core = false;
            out << "\"" << kv.first << "\": {"
                << "\"completed\": " << kv.second.completed.size()
                << ", \"errored\": " << kv.second.errored.size()
                << ", \"cancelled\": " << kv.second.cancelled.size()
                << "}";
        }
        out << "}}" << (i + 1 < instance_records.size() ? "," : "") << "\n";
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
        endpoint->computeVerifyDigests();
        out << "{\"committed_valid_bytes\": " << endpoint->committedBytes()
            << ", \"error_drained_bytes\": " << endpoint->errorDrainBytes()
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
        out << "], \"verifies\": [";
        bool first_v = true;
        for (const auto &verify : endpoint->verifyRows()) {
            if (!first_v)
                out << ", ";
            first_v = false;
            out << "{\"address\": " << verify.address
                << ", \"size\": " << verify.size
                << ", \"digest\": \"" << verify.digest << "\""
                << ", \"after16_digest\": \"" << verify.after_digest << "\"}";
        }
        out << "]}";
    } else {
        out << "null";
    }
    out << ",\n";

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
        }
    } else {
        out << "null";
    }
}

void MeshDispatcher::checkWatchdog()
{
    if (!started)
        return;
    uint64_t progress = 0;
    for (const MeshDummyCore *core : cores)
        progress += core->commandsCompleted.value();
    bool all_done = true;
    for (const MeshDummyCore *core : cores)
        if (!core->halted() || !core->quiescent())
            all_done = false;
    if (all_done)
        return;
    if (progress == last_progress_snapshot) {
        watchdog_fired_count++;
        fatal("MESH_WATCHDOG: no command progress within %llu ticks",
              (unsigned long long)watchdog_ticks_value);
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

void MeshDispatcher::latchInstanceError(MeshDummyCore *source, Tick tick)
{
    if (instance_error_latched)
        return;
    instance_error_latched = true;
    for (PeerSramAperture *aperture : apertures)
        aperture->cancelAllExpectations();
    for (MeshDummyCore *core : cores)
        core->onInstanceError(core == source ? tick : curTick());
}

void MeshDispatcher::routePeerCommit(uint16_t peer_core, uint32_t transfer_id)
{
    for (MeshDummyCore *core : cores)
        if (core->archCoreId() == peer_core) {
            core->onTransferCommitted(transfer_id);
            return;
        }
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
    {
        InstanceRecord record;
        for (MeshDummyCore *core : cores)
            record.cores[core->archCoreId()] = core->takeInstanceLedger();
        instance_records.push_back(std::move(record));
    }
    if (instance_counter < total_instances) {
        // Same immutable CommandROM, fresh per-instance state (spec 17.2.24).
        schedule(&start_event, clockEdge() + 1);
        return;
    }
    instancesCompleted++;
    std::string cause = anyCoreErrored()
        ? "MESH_PROGRAM_ERROR_DRAINED: " + loader->programName()
        : "MESH_PROGRAM_DONE: " + loader->programName();
    DPRINTF(AiMesh, "dispatcher: %s\n", cause.c_str());
    writeResultJson();
    exitSimLoop(cause.c_str());
}

} // namespace ai_mesh
} // namespace gem5

#include "dev/ai_mesh/mesh_dispatcher.hh"

#include <algorithm>
#include <fstream>
#include <iomanip>
#include <map>
#include <tuple>
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
      watchdog_event(this),
      cache_retry_event(this)
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

bool MeshDispatcher::armInstance()
{
    fatal_if(!loader->loaded(), "dispatcher started before loader installed a program");
    if (batch_state != mesh_abi::MeshBatchState::PROGRAM_READY)
        return false;
    if (!batch_setup_done) {
        fatal_if(instance_error_latched && !pending_fences.empty(),
                 "previous instance left live cross-core fences");
        instance_error_latched = false;
        const auto next_instance = checkedSequenceAdd(instance_counter, 1);
        fatal_if(!next_instance, "dispatcher instance generation overflow");
        instance_counter = *next_instance;
        instancesDispatched++;
        started = true;
        halted_cores.clear();
        for (PeerSramAperture *aperture : apertures)
            aperture->beginInstance();
        scoreboard.reset();
        participant_cores.clear();
        overlay_bus.clear();
        overlay_exits_seen.clear();
        for (MeshDummyCore *core : cores) {
            core->setDispatcher(this);
            core->clearRegionGates();
            core->setOverlayBus(&overlay_bus);
            core->armRequest(instance_counter);
            participant_cores.push_back(core);
        }
        loader->installOverlayImage();
        batch_setup_done = true;
    }
    if (!reserveWeightCaches())
        return false;
    armOverlayGates();
    cache_reservation_waiting = false;
    batch_state = mesh_abi::MeshBatchState::REQUEST_ARMED;
    return true;
}

bool MeshDispatcher::startInstance()
{
    // A waiting reservation keeps the batch un-armed: a start here would run
    // the overlay without its weight lines.
    if (cache_reservation_waiting)
        return false;
    if (batch_state != mesh_abi::MeshBatchState::REQUEST_ARMED)
        return false;
    batch_state = mesh_abi::MeshBatchState::REQUEST_RUNNING;
    batch_starts++;
    for (MeshDummyCore *core : participant_cores)
        core->startRequest();
    DPRINTF(AiMesh, "dispatcher: instance %u/%u started on %zu cores\n",
            instance_counter.value(), total_instances,
            participant_cores.size());
    if (watchdog_ticks_value > 0) {
        uint64_t progress = 0;
        for (const MeshDummyCore *core : participant_cores)
            progress += core->workProgress();
        last_progress_snapshot = progress;
        if (watchdog_event.scheduled())
            deschedule(&watchdog_event);
        schedule(&watchdog_event, curTick() + watchdog_ticks_value);
    }
    return true;
}

MeshDummyCore *
MeshDispatcher::coreOf(uint16_t core_id) const
{
    for (MeshDummyCore *core : cores)
        if (core->archCoreId() == core_id)
            return core;
    return nullptr;
}

std::vector<MeshDispatcher::CacheReservationRequest>
MeshDispatcher::cacheDemands() const
{
    std::vector<CacheReservationRequest> requests;
    for (MeshDummyCore *core : cores) {
        if (core->weightCache() == nullptr)
            continue;
        CacheReservationRequest request;
        request.core_id = core->archCoreId();
        std::map<uint32_t, std::map<uint32_t, uint32_t>> demand;
        for (const auto &entry : loader->overlayGraphs()) {
            const uint32_t layer_id = entry.first;
            for (const auto &object : entry.second.objects()) {
                if (object.kind != mesh_abi::kMeshObjectKindVIEW ||
                    object.secondary_kind != mesh_abi::kMoeViewKindWEIGHT)
                    continue;
                if (object.backing_kind !=
                    mesh_abi::kMoeViewBackingWEIGHT_CACHE_SLOT)
                    continue;
                if (object.owner_core != core->archCoreId())
                    continue;
                demand[layer_id][object.ref_ordinal]++;
            }
        }
        for (const auto &layer : demand) {
            MoeWeightCache::LayerTags tags;
            tags.layer_id = layer.first;
            for (const auto &tag : layer.second) {
                tags.tags.push_back(tag.first);
                request.consumers[layer.first][tag.first] = tag.second;
            }
            request.demand.push_back(tags);
        }
        if (request.demand.empty())
            continue;
        requests.push_back(request);
    }
    std::sort(requests.begin(), requests.end(),
              [](const CacheReservationRequest &left,
                 const CacheReservationRequest &right) {
                  return std::tie(left.core_id) < std::tie(right.core_id);
              });
    return requests;
}

bool MeshDispatcher::reserveWeightCaches()
{
    if (loader->weightPolicy() != "cached")
        return true;
    std::vector<CacheReservationRequest> requests;
    if (cache_reservation_queue.empty()) {
        requests = cacheDemands();
        cache_queue_capacity = uint32_t(requests.size());
        cache_reservation_queue.clear();
        next_cache_request_id = 1;
    } else {
        requests = cache_reservation_queue;
    }
    cache_reservation_attempts++;
    if (resolveCacheReservations(requests)) {
        cache_reservation_waiting = false;
        if (cache_retry_event.scheduled())
            deschedule(&cache_retry_event);
        return true;
    }
    cache_reservation_waiting = !instance_error_latched;
    if (!cache_reservation_waiting) {
        if (cache_retry_event.scheduled())
            deschedule(&cache_retry_event);
        return false;
    }
    if (watchdog_ticks_value > 0 && !watchdog_event.scheduled())
        schedule(&watchdog_event, curTick() + watchdog_ticks_value);
    if (!cache_retry_event.scheduled())
        schedule(&cache_retry_event, clockEdge() + 1);
    return false;
}

bool MeshDispatcher::retryCacheReservations()
{
    if (!cache_reservation_waiting)
        return false;
    if (!reserveWeightCaches())
        return false;
    // Resources arrived: finish the arm exactly once and start the batch.
    armOverlayGates();
    cache_reservation_waiting = false;
    batch_state = mesh_abi::MeshBatchState::REQUEST_ARMED;
    startInstance();
    return true;
}

bool MeshDispatcher::resolveCacheReservations(
    const std::vector<CacheReservationRequest> &requests)
{
    // Two phases across every core of the batch: prepare only reads live
    // state, so a batch commits all of its core/layer demand or nothing at
    // all, and a waiting item keeps its frozen request identity.
    struct Prepared
    {
        const CacheReservationRequest *request = nullptr;
        MoeWeightCache *cache = nullptr;
        MoeWeightCache::ReservePlan plan;
    };
    std::vector<Prepared> prepared;
    bool all_committed = true;
    bool any_failed = false;
    for (const CacheReservationRequest &request : requests) {
        MeshDummyCore *core = coreOf(uint16_t(request.core_id));
        if (core == nullptr || core->weightCache() == nullptr)
            continue;
        Prepared entry;
        entry.request = &request;
        entry.cache = core->weightCache();
        entry.plan = entry.cache->prepare(core->cacheBatchId(),
                                          request.demand, curTick());
        if (entry.plan.status == MoeWeightCache::ReserveStatus::FAILED)
            any_failed = true;
        if (entry.plan.status != MoeWeightCache::ReserveStatus::COMMITTED)
            all_committed = false;
        prepared.push_back(entry);
    }
    if (prepared.empty())
        return false;
    if (any_failed) {
        for (Prepared &entry : prepared) {
            MeshDummyCore *core = coreOf(uint16_t(entry.request->core_id));
            const uint64_t batch_id = core->cacheBatchId();
            entry.cache->abortBeforeStart(batch_id);
            entry.cache->noteFailureFanoutDone(batch_id);
        }
        cache_reservation_queue.clear();
        cache_reservation_waiting = false;
        commitPrestartFailure();
        return false;
    }
    if (!all_committed) {
        // The waiting item keeps the identity assigned when it first failed:
        // retries never re-key a live demand.
        std::vector<CacheReservationRequest> waiting;
        for (const CacheReservationRequest &request : requests) {
            CacheReservationRequest queued = request;
            for (const CacheReservationRequest &existing :
                 cache_reservation_queue)
                if (existing.core_id == queued.core_id) {
                    queued.request_id = existing.request_id;
                    if (existing.request_id == 0)
                        cache_identity_reassignments++;
                    break;
                }
            if (queued.request_id == 0)
                queued.request_id = next_cache_request_id++;
            waiting.push_back(queued);
        }
        cache_reservation_queue = waiting;
        fatal_if(cache_reservation_queue.size() > cache_queue_capacity,
                 "cache reservation queue overflowed: %zu of %u",
                 cache_reservation_queue.size(), cache_queue_capacity);
        return false;
    }
    for (Prepared &entry : prepared) {
        MeshDummyCore *core = coreOf(uint16_t(entry.request->core_id));
        const std::vector<MoeCacheToken> tokens =
            entry.cache->commit(entry.plan, curTick());
        core->installCacheTokens(tokens, entry.request->consumers);
        cache_reservation_commits++;
    }
    cache_reservation_queue.clear();
    return true;
}

void MeshDispatcher::armOverlayGates()
{
    fatal_if(loader == nullptr, "dispatcher has no program loader");
    const auto &program = loader->program();
    for (const auto &region : program.moe_dynamic_regions) {
        MeshDummyCore *owner = nullptr;
        for (MeshDummyCore *core : participant_cores)
            if (core->archCoreId() == region.core_id)
                owner = core;
        if (owner == nullptr)
            fatal("MoE region %u has no participating core",
                  region.region_id);
        MoeInsertionGate::RegionSpec spec;
        spec.layer_id = region.layer_id;
        spec.region_id = region.region_id;
        spec.insert_after_command_id = region.insert_after_command_id;
        spec.resume_before_command_id = region.resume_before_command_id;
        spec.resume_before_index = owner->commandIndex(
            region.resume_before_command_id);
        owner->armRegionGate(spec);
    }
}

void MeshDispatcher::notifyOverlayExit(uint32_t layer_id)
{
    if (!overlayGroupExited(layer_id))
        return;
    overlay_exits_seen.insert(layer_id);
}

bool MeshDispatcher::overlayGroupExited(uint32_t layer_id)
{
    bool released = false;
    for (MeshDummyCore *core : participant_cores) {
        if (!core->overlayGateArmed(layer_id))
            continue;
        core->releaseOverlayGroup(layer_id);
        released = true;
    }
    return released;
}

bool MeshDispatcher::allOverlayGroupsExited() const
{
    for (MeshDummyCore *core : participant_cores) {
        for (const auto &region : loader->program().moe_dynamic_regions)
            if (core->archCoreId() == region.core_id &&
                core->overlayGatePhase(region.layer_id) !=
                    MoeInsertionGate::RegionPhase::RELEASED)
                return false;
    }
    return true;
}

std::string MeshDispatcher::overlayGateState() const
{
    std::string state;
    for (MeshDummyCore *core : cores) {
        state += "core " + std::to_string(core->archCoreId()) + ": " +
                 core->overlayGateState();
    }
    return state;
}

void MeshDispatcher::abortBeforeStart()
{
    fatal_if(batch_state != mesh_abi::MeshBatchState::REQUEST_ARMED,
             "prestart abort requires an armed batch");
    batch_state = mesh_abi::MeshBatchState::BATCH_PRESTART_ABORTING;
    for (MeshDummyCore *core : participant_cores)
        core->disarmRequest();
    participant_cores.clear();
    halted_cores.clear();
    batch_state = mesh_abi::MeshBatchState::BATCH_PRESTART_DRAINED;
    DPRINTF(AiMesh, "dispatcher: instance %u aborted before start\n",
            instance_counter.value());
    batch_state = mesh_abi::MeshBatchState::PROGRAM_READY;
    batch_setup_done = false;
    cache_reservation_waiting = false;
}

const char *
MeshDispatcher::terminalName() const
{
    switch (program_terminal) {
      case ProgramTerminal::DONE:
        return "DONE";
      case ProgramTerminal::ERROR_DRAINED:
        return "ERROR_DRAINED";
      case ProgramTerminal::PRESTART_FAILED:
        return "PRESTART_FAILED";
      default:
        return "NONE";
    }
}

std::string MeshDispatcher::programCause() const
{
    switch (program_terminal) {
      case ProgramTerminal::PRESTART_FAILED:
        return "MESH_PROGRAM_PRESTART_FAILED: " + loader->programName();
      case ProgramTerminal::ERROR_DRAINED:
        return "MESH_PROGRAM_ERROR_DRAINED: " + loader->programName();
      case ProgramTerminal::DONE:
        return "MESH_PROGRAM_DONE: " + loader->programName();
      default:
        fatal("program terminal was never committed");
    }
}

void MeshDispatcher::commitPrestartFailure()
{
    // One terminal commit for a batch that never started: undo the setup of
    // every core, drop the batch-owned state and report the program error.
    fatal_if(batch_state == mesh_abi::MeshBatchState::REQUEST_RUNNING ||
                 batch_state == mesh_abi::MeshBatchState::FINAL_DRAINING,
             "prestart failure committed after the batch started");
    DPRINTF(AiMesh, "dispatcher: instance %u prestart failure\n",
            instance_counter.value());
    for (MeshDummyCore *core : participant_cores)
        core->disarmRequest();
    for (MeshDummyCore *core : cores)
        core->clearRegionGates();
    overlay_bus.clear();
    overlay_exits_seen.clear();
    participant_cores.clear();
    halted_cores.clear();
    instance_error_latched = true;
    {
        InstanceRecord record;
        for (MeshDummyCore *core : cores)
            record.cores[core->archCoreId()] = core->takeInstanceLedger();
        instance_records.push_back(std::move(record));
    }
    // A deterministic prestart failure repeats for every later instance of the
    // same frozen batch, so the run terminates with the program error.
    batch_state = mesh_abi::MeshBatchState::BATCH_PRESTART_DRAINED;
    batch_setup_done = false;
    cache_reservation_waiting = false;
    prestart_failures++;
    program_terminal = ProgramTerminal::PRESTART_FAILED;
    const std::string cause = programCause();
    writeResultJson();
    exitSimLoop(cause.c_str());
}

void MeshDispatcher::dispatch()
{
    if (!armInstance())
        return;
    startInstance();
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
        for (const RuntimeObjectKey &id : core->completed_command_ids) {
            if (!first_id)
                out << ", ";
            first_id = false;
            out << id.ordinal;
        }
        out << "], \"errored_command_ids\": [";
        first_id = true;
        for (const RuntimeObjectKey &id : core->errored_command_ids) {
            if (!first_id)
                out << ", ";
            first_id = false;
            out << id.ordinal;
        }
        out << "], \"cancelled_command_ids\": [";
        first_id = true;
        for (const RuntimeObjectKey &id : core->cancelled_command_ids) {
            if (!first_id)
                out << ", ";
            first_id = false;
            out << id.ordinal;
        }
        out << "], \"command_issue_ticks\": {";
        {
            bool first_it = true;
            for (const auto &entry : core->command_issue_ticks) {
                if (!first_it)
                    out << ", ";
                first_it = false;
                out << "\"" << entry.first.ordinal << "\": "
                    << entry.second;
            }
        }
        out << "}, \"command_done_ticks\": {";
        first_id = true;
        for (const auto &kv : core->commandDoneTicks()) {
            if (!first_id)
                out << ", ";
            first_id = false;
            out << "\"" << kv.first.ordinal << "\": " << kv.second;
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
                << ", \"command_id\": " << digest.command.ordinal
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

    out << "  \"terminal\": \"" << terminalName() << "\",\n";
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
        std::map<RuntimeObjectKey, AxiTensorDmaEngine::DescriptorTiming>
            timings;
        for (const auto &kv : engine->descriptorTimings())
            timings[kv.first] = kv.second;
        for (const auto &kv : timings) {
            if (!first)
                out << ",\n";
            first = false;
            out << "    {\"core_id\": " << core->archCoreId()
                << ", \"descriptor_id\": " << kv.first.ordinal
                << ", \"domain\": " << uint32_t(kv.first.domain)
                << ", \"region_group_id\": " << kv.first.regionGroupId
                << ", \"region_id\": " << kv.first.regionId
                << ", \"kind\": " << uint32_t(kv.first.kind)
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

    out << "  \"cache_reservation\": {\"waiting\": "
        << (cache_reservation_waiting ? 1 : 0)
        << ", \"attempts\": " << cache_reservation_attempts
        << ", \"identity_reassignments\": "
        << cache_identity_reassignments
        << ", \"commits\": " << cache_reservation_commits
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
    if (loader != nullptr && loader->program().has_moe_v1) {
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
        for (const auto &region : loader->program().moe_dynamic_regions) {
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
                    << ", \"valid_bytes\": " << slot.valid_bytes << "}";
            }
            out << "], \"tombstones\": " << cache->tombstoneCount()
                << ", \"fills_retried\": " << core->cacheFillsRetried()
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
        progress += core->workProgress();
    bool all_done = true;
    for (const MeshDummyCore *core : cores)
        if (!core->halted() || !core->quiescent())
            all_done = false;
    if (all_done)
        return;
    if (progress == last_progress_snapshot) {
        watchdog_fired_count++;
        std::string state = overlayGateState();
        if (cache_reservation_waiting) {
            state += " cache-reservation-waiting attempts=" +
                     std::to_string(cache_reservation_attempts) +
                     " identity-reassignments=" +
                     std::to_string(cache_identity_reassignments);
            for (const CacheReservationRequest &request :
                 cache_reservation_queue)
                state += " waiting-core=" + std::to_string(request.core_id) +
                         " request-id=" + std::to_string(request.request_id);
        }
        std::string live;
        for (const MeshDummyCore *core : cores) {
            live += " core " + std::to_string(core->archCoreId()) +
                    " issued " +
                    std::to_string(core->commandsIssued.value()) + " done " +
                    std::to_string(core->commandsCompleted.value()) +
                    " state " +
                    std::to_string(uint32_t(core->instanceState())) +
                    (core->tickScheduled() ? " ticking" : " no-tick") +
                    (core->overlayPending() ? " overlay-pending" : " overlay-idle") +
                    (core->quiescent() ? " idle" : " live");
            if (loader != nullptr)
                for (const auto &region :
                     loader->program().moe_dynamic_regions)
                    if (region.core_id == core->archCoreId()) {
                        const MoeOverlayExecutor *executor =
                            core->overlayExecutor(region.layer_id);
                        live += " overlay[" +
                                std::to_string(region.layer_id) + "] " +
                                (executor ? executor->describeBlocked()
                                          : std::string("none"));
                    }
        }
        fatal("MESH_WATCHDOG: no command progress within %llu ticks;%s;%s",
              (unsigned long long)watchdog_ticks_value, state.c_str(),
              live.c_str());
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
                core->completeFence(core->commandKey(fence.command_id),
                                    core->eventKey(fence.signal_event));
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
            core->onTransferCommitted(core->transferKey(transfer_id));
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
    batch_state = mesh_abi::MeshBatchState::PROGRAM_READY;
    batch_setup_done = false;
    cache_reservation_waiting = false;
    participant_cores.clear();
    if (instance_counter.value() < total_instances) {
        // Same immutable CommandROM, fresh per-instance state (spec 17.2.24).
        schedule(&start_event, clockEdge() + 1);
        return;
    }
    instancesCompleted++;
    program_terminal = anyCoreErrored() ? ProgramTerminal::ERROR_DRAINED
                                        : ProgramTerminal::DONE;
    const std::string cause = programCause();
    DPRINTF(AiMesh, "dispatcher: %s\n", cause.c_str());
    writeResultJson();
    exitSimLoop(cause.c_str());
}

} // namespace ai_mesh
} // namespace gem5

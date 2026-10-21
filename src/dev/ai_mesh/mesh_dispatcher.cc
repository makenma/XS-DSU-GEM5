#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/dma_kind.hh"


#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"

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
#include "dev/ai_mesh/mesh_clock.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_program_loader.hh"
#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/mock_axi_transport.hh"
#include "dev/ai_mesh/peer_sram_aperture.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/simple/AggregateNetwork.hh"
#include "mem/ruby/network/simple/SimpleNetwork.hh"
#include "params/MeshDispatcher.hh"
#include "sim/cur_tick.hh"
#include "sim/sim_exit.hh"

namespace gem5
{
namespace ai_mesh
{

bool
MeshDispatcher::readDestinationBytes(uint16_t kind, uint16_t owner_core,
                                     uint64_t address, uint64_t size,
                                     std::vector<uint8_t> &out) const
{
    out.assign(size, 0);
    if (kind == 2) {
        return memory.read(address, size, out.data());
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

void
MeshDispatcher::captureDestination(const MeshDummyCore &core,
                                   const DescriptorKey &key,
                                   DestinationSampling sampling)
{
    if (destination_bytes_limit == 0 || key.descriptor.domain != mesh_abi::MeshObjectDomain::STATIC)
        return;
    const DecodedDmaDescriptor *descriptor = core.descriptorById(key.descriptor.ordinal);
    fatal_if(descriptor == nullptr, "destination sample has no admitted descriptor");
    const uint16_t kind = descriptor->kind;
    if (kind != 2 && kind != 3 && kind != 5)
        return;
    fatal_if(!sampled_destinations.insert(key).second,
             "destination execution was sampled twice");
    const uint16_t owner_core = kind == 3
        ? uint16_t(descriptor->dst.owner_core) : core.archCoreId();
    const uint64_t base = core.admittedEndpointAddress(key.descriptor.ordinal, false);
    for (uint32_t row = 0; row < descriptor->rows; ++row) {
        DestinationSegment segment;
        segment.descriptor_id = key.descriptor.ordinal;
        segment.command_id = key.command.command_id;
        segment.generation = key.command.generation;
        segment.kind = kind;
        segment.row = row;
        segment.address = base + uint64_t(row) * descriptor->dst_stride_bytes;
        segment.size = descriptor->row_bytes;
        segment.sample_tick = curTick();
        segment.sampling = sampling;
        if (segment.size == 0 || segment.size > destination_bytes_limit)
            continue;
        std::vector<uint8_t> bytes;
        fatal_if(!readDestinationBytes(kind, owner_core, segment.address,
                                       segment.size, bytes),
                 "cannot read admitted destination at commit boundary");
        segment.bytes_hex = bytesHex(bytes);
        live_destinations.push_back(std::move(segment));
    }
}


void
MeshDispatcher::capturePendingDestinations()
{
    if (destination_bytes_limit == 0)
        return;
    for (const MeshDummyCore *core : cores) {
        for (const auto &execution : core->runtimeObservations().view().descriptors) {
            const DescriptorKey key{execution.command, execution.descriptor};
            if (!sampled_destinations.count(key))
                captureDestination(*core, key, DestinationSampling::PendingReadback);
        }
    }
}

MeshDispatcher::MeshDispatcher(const Params &p)
    : ClockedObject(p),
      loader(p.loader),
      cores(p.cores.begin(), p.cores.end()),
      transport(p.transport),
      apertures(p.apertures.begin(), p.apertures.end()),
      memory(loader->physicalMemory()),
      output_span_base(p.output_span_base),
      output_span_bytes(p.output_span_bytes),
      network(p.network),
      result_json_path(p.result_json),
      receiver_fault(p.receiver_fault),
      residency_fault(p.residency_fault),
      drain_fault(p.drain_fault),
      destination_bytes_limit(p.destination_bytes_limit),
      total_instances(p.instances),
      autostart(p.autostart),
      watchdog_ticks_value(p.watchdog_ticks),
      failure_record_capacity(p.batch_failure_record_entries),
      failure_reducer(p.batch_failure_record_entries, 0),
      failure_event(this),
      start_event(this),
      watchdog_event(this),
      cache_retry_event(this),
      settle_retry_event(this),
      drain_event(this)
{
    const auto &arch = loader->arch();
    const auto *partition = arch.partition(mesh_abi::kSramPartitionKindWEIGHT_CACHE);
    const uint64_t slots = partition && arch.weight_cache_slot_bytes ?
        partition->bytes / arch.weight_cache_slot_bytes : 0;
    fatal_if(!cores.empty() && slots > SIZE_MAX / cores.size(), "cache edge capacity overflows");
    cache_fill_edge = std::make_unique<CacheFillEdge>(slots * cores.size());
}

void MeshDispatcher::regStats()
{
    ClockedObject::regStats();
    instancesDispatched.name(name() + ".instances_dispatched").desc("Program instances dispatched");
    instancesCompleted.name(name() + ".instances_completed").desc("Program instances completed");
}

void MeshDispatcher::startup()
{
    for (auto *core : cores) {
        core->setDispatcher(this);
        core->setOverlayBus(&overlay_bus);
    }

    // The atomic start goes out at the first legal core clock edge; all
    // loader startup work is guaranteed complete before this tick.
    if (output_span_bytes != 0)
        output_digest_before = outputSpanDigest();
    if (autostart)
        begin();
}

void MeshDispatcher::begin()
{
    fatal_if(started, "dispatcher already started an instance");
    fatal_if(start_event.scheduled(), "dispatcher start is already scheduled");
    schedule(&start_event, clockEdge() + 1);
}

void MeshDispatcher::setDmaObserver(MeshDmaObserver *observer)
{
    for (MeshDummyCore *core : cores)
        core->setDmaObserver(observer);
}

void
MeshDispatcher::bindInstance(const ServingInstanceBinding &value,
                            std::shared_ptr<const MoeOverlaySet> overlays)
{
    fatal_if(staging_candidate == nullptr, "serving binding without a candidate");
    staging_candidate->binding = value;
    staging_candidate->overlays = std::move(overlays);
}

bool MeshDispatcher::prepareMoe(const mesh_abi::AgentInstanceProfile &profile,
    const std::vector<ServingBatchMember> &members, const ServingInstanceBinding &binding,
    std::shared_ptr<const MoeOverlaySet> &overlays, MoePreparationError &error)
{
    return loader->prepareServingMoe(profile, members, binding, overlays, error);
}

bool
MeshDispatcher::canAccessKv(uint64_t address, uint64_t bytes) const
{
    return memory.contains(address, bytes);
}

void
MeshDispatcher::bindSemanticDigest(const std::array<uint8_t, 32> &digest)
{
    fatal_if(staging_candidate == nullptr, "serving digest without a candidate");
    staging_candidate->digest = digest;
    staging_candidate->has_digest = true;
}

void
MeshDispatcher::bindFillContent(const ServingFillBinding &binding)
{
    fatal_if(staging_candidate == nullptr, "serving fill without a candidate");
    staging_candidate->fills.push_back(binding);
}

void MeshDispatcher::setInstanceObserver(MeshInstanceObserver *observer)
{
    instance_observer = observer;
}

void MeshDispatcher::finishExecution()
{
    fatal_if(instance_observer == nullptr,
             "dispatcher has no serving owner");
    fatal_if(started || batch_setup_done || start_event.scheduled() ||
                 selected_view != nullptr,
             "serving execution finished with a live instance");
    commitProgramTerminal();
}

const ProfileExecutionView *MeshDispatcher::executionView() const
{
    if (instance_observer != nullptr) {
        fatal_if(selected_view == nullptr, "serving instance has no selector");
        return selected_view;
    }
    if (loader->defaultInvocation())
        return &loader->defaultInvocation()->executionView();
    fatal("profile execution requires an instance selector");
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
        if (instance_observer == nullptr) {
            const auto next_instance = checkedSequenceAdd(instance_counter, 1);
            fatal_if(!next_instance, "dispatcher instance generation overflow");
            instance_counter = *next_instance;
        }
        if (instance_observer == nullptr)
            fatal_if(!failure_reducer.begin(instance_counter.value(), instance_counter.value(),
                                           instance_binding.memberRequests()),
                     "invalid frozen failure population");
        instancesDispatched++;
        started = true;
        live_destinations.clear();
        sampled_destinations.clear();
        drain_active = false;
        drain_begin_tick = 0;
        drain_begin_pending = 0;
        drain_end_tick = 0;
        drain_end_pending = 0;
        halted_cores.clear();
        for (PeerSramAperture *aperture : apertures)
            aperture->beginInstance();
        scoreboard.reset();
        participant_cores.clear();
        for (MeshDummyCore *core : cores)
            core->setInstanceBinding(instance_binding);
        overlay_bus.clear();
        overlay_exits_seen.clear();
        const auto &invocation = instance_observer ? instance_binding.invocation() : loader->defaultInvocation();
        fatal_if(!invocation, "dispatcher has no frozen invocation");
        const ProfileExecutionView *view = executionView();
        fatal_if(view != &invocation->executionView(), "dispatcher selector differs from its frozen invocation");
        std::string fill_reason;
        auto prepared_fills = ServingFillInputs::freeze(
            loader->program(), *view, instance_counter, instance_binding, pending_fill_bindings,
            fill_reason);
        fatal_if(!prepared_fills, "%s", fill_reason);
        auto fill_inputs = std::make_shared<const ServingFillInputs>(
            std::move(*prepared_fills));
        pending_fill_bindings.clear();
        for (MeshDummyCore *core : cores) {
            core->setDispatcher(this);
            core->clearRegionGates();
            core->setOverlayBus(&overlay_bus);
            core->installInvocation(invocation);
            core->armRequest(instance_counter, *view, fill_inputs);
            participant_cores.push_back(core);
        }
        loader->installOverlayImage(*invocation, serving_overlays.get());
        batch_setup_done = true;
    }
    if (active_candidate) {
        for (const auto &request : active_candidate->demand)
            coreOf(request.core_id)->installCacheTokens(request.tokens, request.consumers);
    } else if (!reserveWeightCaches()) {
        return false;
    }
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
    if (active_candidate)
        candidate_events.push_back({instance_counter.value(), curTick(), "STARTED"});
    for (MeshDummyCore *core : participant_cores)
        core->startRequest();
    if (instance_observer != nullptr)
        instance_observer->onInstanceStarted(instance_counter.value(),
                                             instance_binding.requestId(),
                                             instance_binding.requestGeneration(),
                                             curTick());
    DPRINTF(AiMesh, "dispatcher: instance %u/%u started on %zu cores\n",
            instance_counter.value(), total_instances,
            participant_cores.size());
    if (watchdog_ticks_value > 0) {
        last_progress_snapshot = progressSample();
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

void MeshDispatcher::armOverlayGates()
{
    fatal_if(loader == nullptr, "dispatcher has no program loader");
    const auto &program = loader->program();
    for (const auto &region : program.transport.moe_dynamic_regions) {
        if (serving_overlays && std::none_of(serving_overlays->begin(), serving_overlays->end(),
                [&](const auto &row) { return row.first == region.layer_id; }))
            continue;
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
        for (const auto &region : loader->program().transport.moe_dynamic_regions)
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

void
MeshDispatcher::releaseWaitWindow()
{
    if (cache_retry_event.scheduled())
        deschedule(&cache_retry_event);
    if (watchdog_event.scheduled())
        deschedule(&watchdog_event);
    cache_reservation_queue.clear();
    cache_reservation_waiting = false;
}

void
MeshDispatcher::cancelPendingInstance(uint64_t instance)
{
    if (instance && instance_observer) {
        for (auto &candidate : serving_candidates) {
            if (!candidate || candidate->instance.value() != instance)
                continue;
            for (const auto &request : candidate->demand) {
                auto *cache = coreOf(request.core_id)->weightCache();
                cache->abortBeforeStart(instance);
                cache->noteFailureFanoutDone(instance);
            }
            if (staging_candidate == &*candidate)
                staging_candidate = nullptr;
            failure_reducer.release(instance);
            candidate.reset();
            return;
        }
        if (instance != instance_counter.value())
            return;
    }
    // Only a pending, not-yet-started instance is cancellable.  Validating the
    // state before touching the window keeps a stray cancel on a running or
    // draining instance from silently closing its progress watchdog.
    if (batch_state == mesh_abi::MeshBatchState::REQUEST_ARMED) {
        releaseWaitWindow();
        abortBeforeStart();
        return;
    }
    if (batch_state == mesh_abi::MeshBatchState::PROGRAM_READY &&
            selected_view != nullptr && !batch_setup_done && !started) {
        if (start_event.scheduled())
            deschedule(&start_event);
        releaseWaitWindow();
        pending_fill_bindings.clear();
        selected_view = nullptr;
        failure_reducer.release(instance_counter.value());
        active_candidate.reset();
        retryCacheReservations();
        return;
    }
    if (batch_state != mesh_abi::MeshBatchState::PROGRAM_READY ||
        !batch_setup_done || !cache_reservation_waiting)
        return;
    releaseWaitWindow();
    for (MeshDummyCore *core : participant_cores)
        core->disarmRequest();
    participant_cores.clear();
    halted_cores.clear();
    batch_setup_done = false;
    started = false;
    selected_view = nullptr;
    pending_fill_bindings.clear();
    DPRINTF(AiMesh, "dispatcher: pending instance %u cancelled before start\n",
            instance_counter.value());
}

void
MeshDispatcher::failCurrentInstance(const ErrorCandidate &failure)
{
    if (!participant_cores.empty())
        stageInstanceFailure(failure);
}

void MeshDispatcher::abortBeforeStart()
{
    fatal_if(batch_state != mesh_abi::MeshBatchState::REQUEST_ARMED,
             "prestart abort requires an armed batch");
    releaseWaitWindow();
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
    started = false;
    selected_view = nullptr;
    pending_fill_bindings.clear();
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
    releaseWaitWindow();
    InstanceRecord record;
    record.instance = instance_counter;
    if (failure_reducer.failure(instance_counter.value()))
        record.failure = *failure_reducer.failure(instance_counter.value());
    for (MeshDummyCore *core : participant_cores)
        record.cores[core->archCoreId()] = core->disarmRequest();
    for (MeshDummyCore *core : cores)
        core->clearRegionGates();
    overlay_bus.clear();
    overlay_exits_seen.clear();
    participant_cores.clear();
    halted_cores.clear();
    instance_error_latched = true;
    instance_records.push_back(std::move(record));
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
    if (batch_state != mesh_abi::MeshBatchState::REQUEST_RUNNING ||
            participant_cores.empty())
        return;
    // The settle edge is deferred until every participant is quiescent; keep
    // retrying on the next edge instead of waiting for a later callback that
    // may never come.
    if (!settle_retry_event.scheduled())
        schedule(&settle_retry_event, clockEdge() + 1);
}

std::string
MeshDispatcher::outputSpanDigest() const
{
    if (output_span_bytes == 0)
        return std::string();
    return memory.rangeDigest(output_span_base, output_span_bytes);
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
    if (!memory.idle())
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

int
MeshDispatcher::erroredCoreId() const
{
    for (const MeshDummyCore *core : cores)
        if (core->instanceErrored())
            return int(core->archCoreId());
    return -1;
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
        if (!memory.idle())
            drain_graph += " endpoint_busy";
        drain_graph += "\n";
        RuntimeDiagnostic record;
        record.code = mesh_diagnostics::DiagnosticCode::E_RUNTIME_DEADLOCK;
        record.stage = DiagnosticStage::Watchdog;
        record.context = {{"instance", std::to_string(instance_counter.value())},
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
                     loader->program().transport.moe_dynamic_regions)
                    if (region.core_id == core->archCoreId()) {
                        const MoeOverlayExecutor *executor =
                            core->overlayExecutor(region.layer_id);
                        live += " overlay[" +
                                std::to_string(region.layer_id) + "] " +
                                (executor ? executor->describeBlocked()
                                          : std::string("none"));
                    }
        }
        std::string graph = state + "\n" + live + "\n";
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
            {"instance", std::to_string(instance_counter.value())},
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
    const auto &invocation = instance_observer ? instance_binding.invocation() : loader->defaultInvocation();
    fatal_if(!invocation, "peer expectation has no frozen invocation");
    const auto *plan = invocation->transferPlan(transfer_id);
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

void MeshDispatcher::stageInstanceFailure(const ErrorCandidate &candidate)
{
    stageInstanceFailure(instance_counter.value(), candidate);
    if (candidate.source.domain != mesh_abi::kMeshObjectDomainWEIGHT_FILL)
        return;
    const auto *wire = candidate.source.aux_key.data();
    const auto core = agent_abi::rdU16(wire + mesh_abi::kWeightFillKeysCoreIdOffset);
    const auto tag = agent_abi::rdU32(wire + mesh_abi::kWeightFillKeysWeightTagIndexOffset);
    for (auto &queued : serving_candidates) {
        if (!queued || &*queued == staging_candidate)
            continue;
        for (const auto &request : queued->demand) {
            if (request.core_id != core)
                continue;
            for (const auto &layer : request.demand)
                if (std::find(layer.tags.begin(), layer.tags.end(), tag) != layer.tags.end()) {
                    stageInstanceFailure(queued->instance.value(), candidate);
                    queued->failure_pending = true;
                    break;
                }
        }
    }
}

void MeshDispatcher::stageInstanceFailure(uint64_t instance, const ErrorCandidate &candidate)
{
    if (failure_reducer.failure(instance))
        return;
    fatal_if(candidate.tick != curTick() || !failure_reducer.stage(instance, candidate),
             "instance failure arrived outside its reduction edge");
    failure_pending = true;
    if (!edge_commit_active && !failure_event.scheduled())
        schedule(&failure_event, curTick());
}

void MeshDispatcher::commitInstanceFailure()
{
    edge_commit_active = true;
    cache_fill_edge->commitFailures();
    for (const auto &failure : cache_fill_edge->failures())
        stageInstanceFailure(failure);
    for (auto &queued : serving_candidates) {
        if (!queued || &*queued == staging_candidate || queued->failure_pending)
            continue;
        for (const auto &request : queued->demand)
            for (const auto &layer : request.demand)
                for (const auto tag : layer.tags)
                    if (auto error = coreOf(request.core_id)->weightCache()->failure(tag)) {
                        error->tick = curTick();
                        stageInstanceFailure(queued->instance.value(), *error);
                        queued->failure_pending = true;
                    }
    }
    if (failure_pending)
        fatal_if(!failure_reducer.commitEdge(curTick()), "invalid scenario failure reduction edge");
    failure_pending = false;
    if (const auto *record = failure_reducer.failure(instance_counter.value());
            record && !instance_error_latched) {
        instance_error_latched = true;
        const auto &source = record->first.source;
        if ((source.domain == mesh_abi::kMeshObjectDomainSTATIC ||
             source.domain == mesh_abi::kMeshObjectDomainKV_RUNTIME) &&
            source.error_class == mesh_abi::kMoeErrorClassDMA_AXI) {
            const auto found = loader->programAdmission().context().descriptors().find(source.ordinal);
            fatal_if(found == loader->programAdmission().context().descriptors().end(),
                     "runtime failure has no admitted descriptor");
            const auto &descriptor = *found->second;
            const auto status = dmaKindIsRead(descriptor.kind)
                ? DmaStatus::AXI_READ_ERROR : DmaStatus::AXI_WRITE_ERROR;
            RuntimeDiagnostic diagnostic;
            diagnostic.code = mesh_diagnostics::DiagnosticCode::E_AXI_RESPONSE;
            diagnostic.stage = DiagnosticStage::Runtime;
            diagnostic.context = {
                {"instance", std::to_string(record->program_instance_id)},
                {"core_id", std::to_string(source.core_id_or_ffff)},
                {"command_id", std::to_string(descriptor.command_id)},
                {"generation", std::to_string(source.generation)},
                {"descriptor_id", std::to_string(descriptor.descriptor_id)},
                {"dma_kind", std::to_string(descriptor.kind)},
                {"status", dmaStatusName(status)}};
            if (descriptor.transfer_id)
                diagnostic.context.push_back({"transfer_id", std::to_string(descriptor.transfer_id)});
            reportRuntimeDiagnostic(diagnostic, "E_AXI_RESPONSE: instance " +
                std::to_string(record->program_instance_id) + " descriptor " + std::to_string(descriptor.descriptor_id));
        }
        if (const auto &output = instance_binding.outputLedger())
            output->fail(curTick());
        if (instance_observer)
            instance_observer->onInstanceFailure(*record);
        scoreboard.cancelOpen(curTick());
        for (PeerSramAperture *aperture : apertures)
            aperture->abandonAllExpectations();
        for (MeshDummyCore *core : cores)
            core->onInstanceError(curTick());
    }
    for (auto &queued : serving_candidates) {
        if (!queued)
            continue;
        const auto *failure = failure_reducer.failure(queued->instance.value());
        if (!failure)
            continue;
        const BatchFailureRecord record = *failure;
        InstanceRecord observation;
        observation.instance = queued->instance;
        observation.failure = record;
        instance_records.push_back(std::move(observation));
        cancelPendingInstance(record.program_instance_id);
        ++prestart_failures;
        instance_observer->onInstanceFailure(record);
    }
    cache_fill_edge->commitSuccesses();
    edge_commit_active = false;
    retryCacheReservations();
}

void MeshDispatcher::noteMemberCancel(uint64_t instance, uint64_t request, uint64_t tick)
{
    fatal_if(!failure_reducer.cancel(instance, request, tick), "invalid frozen cancel owner");
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
    if (destination_bytes_limit != 0) {
        for (const MeshDummyCore *core : cores) {
            for (const auto &execution : core->runtimeObservations().view().descriptors) {
                if (execution.descriptor.domain != mesh_abi::MeshObjectDomain::STATIC)
                    continue;
                const auto *descriptor = core->descriptorById(execution.descriptor.ordinal);
                if (descriptor && descriptor->kind == 3 &&
                    descriptor->transfer_id == transfer_id)
                    captureDestination(*core, {execution.command, execution.descriptor},
                                       DestinationSampling::TransferCommit);
            }
        }
    }

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
                core->onTransferCommitted(core->transferKey(transfer_id));
                return;
            }
        return;
    }
    target->onTransferCommitted(target->transferKey(transfer_id));
    if (receiver_fault == "receive_duplicate")
        target->onTransferCommitted(target->transferKey(transfer_id));
}

void MeshDispatcher::retryInstanceSettle()
{
    checkAllHalted();
}

void MeshDispatcher::checkAllHalted()
{
    if (failure_event.scheduled())
        return;
    if (!started)
        return;
    if (batch_state != mesh_abi::MeshBatchState::REQUEST_RUNNING ||
            participant_cores.empty())
        return;
    for (MeshDummyCore *core : participant_cores) {
        if (!core->halted() || !core->quiescent())
            return;
        if (!halted_cores.count(core->archCoreId()))
            return;
        fatal_if(!core->dmaEngine()->idle(),
                 "core %u settled with live DMA traffic",
                 core->archCoreId());
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
        record.instance = instance_counter;
        if (failure_reducer.failure(instance_counter.value()))
            record.failure = *failure_reducer.failure(instance_counter.value());
        record.finalized = true;
        record.bursts = instanceBursts();
        record.barriers = scoreboard.barrierHistory();
        capturePendingDestinations();
        for (MeshDummyCore *core : participant_cores) {
            record.cores[core->archCoreId()] = core->takeInstanceLedger();
            core->releaseInstanceInputs();
        }
        record.destinations = std::move(live_destinations);
        for (const PeerSramAperture *aperture : apertures) {
            ApertureFrame frame;
            frame.core_id = aperture->coreId();
            frame.transfers = aperture->transferCoverage();
            record.apertures.push_back(std::move(frame));
        }
        instance_records.push_back(std::move(record));
    }
    drained_instances++;
    batch_state = mesh_abi::MeshBatchState::PROGRAM_READY;
    batch_setup_done = false;
    cache_reservation_waiting = false;
    participant_cores.clear();
    started = false;
    selected_view = nullptr;
    if (anyCoreErrored() && !drained_error_core)
        drained_error_core = erroredCoreId();
    if (instance_observer != nullptr) {
        candidate_events.push_back({instance_counter.value(), curTick(), "SETTLED"});
        active_candidate.reset();
        instance_observer->onInstanceSettled(instance_counter.value(),
                                             instance_binding.requestId(),
                                             instance_binding.requestGeneration(),
                                             anyCoreErrored(), curTick());
        failure_reducer.release(instance_counter.value());
        retryCacheReservations();
        return;
    }
    failure_reducer.release(instance_counter.value());
    advanceAfterInstance();
}

void MeshDispatcher::advanceAfterInstance()
{
    if (program_terminal != ProgramTerminal::NONE)
        return;
    if (instance_counter.value() < total_instances) {
        // Same immutable CommandROM, fresh per-instance state (spec 17.2.24).
        schedule(&start_event, nextCoreEdge(*this));
        return;
    }
    commitProgramTerminal();
}

void MeshDispatcher::commitProgramTerminal()
{
    if (program_terminal != ProgramTerminal::NONE)
        return;
    instancesCompleted++;
    const bool failed_instance = std::any_of(instance_records.begin(), instance_records.end(),
        [](const auto &record) { return record.failure.has_value(); });
    program_terminal = (failed_instance || drained_error_core || anyInstanceErrored()) ? ProgramTerminal::ERROR_DRAINED
                                        : ProgramTerminal::DONE;
    const std::string cause = programCause();
    DPRINTF(AiMesh, "dispatcher: %s\n", cause.c_str());
    writeResultJson();
    if (instance_observer != nullptr)
        return;
    exitSimLoop(cause.c_str());
}

} // namespace ai_mesh
} // namespace gem5

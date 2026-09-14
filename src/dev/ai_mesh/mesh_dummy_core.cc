#include <algorithm>

#include "dev/ai_mesh/mesh_dummy_core.hh"

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_compute_timing.hh"
#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_program_loader.hh"
#include "params/MeshDummyCore.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

MeshDummyCore::MeshDummyCore(const Params &p)
    : ClockedObject(p),
      core_id_value(p.core_id),
      decode_width_value(p.decode_width),
      admit_window_value(p.admit_window),
      event_visibility(p.event_visibility_cycles),
      reference_compute_forbidden(p.reference_compute),
      tensor_queue_depth_value(p.tensor_queue_depth),
      tensor_setup(p.tensor_setup_cycles),
      tensor_flush(p.tensor_flush_cycles),
      tensor_macs_per_cycle_value(p.tensor_macs_per_cycle),
      tensor_macs_by_dtype_value(p.tensor_macs_by_dtype.begin(), p.tensor_macs_by_dtype.end()),
      vector_queue_depth_value(p.vector_queue_depth),
      vector_elements_per_cycle_value(p.vector_elements_per_cycle),
      vector_elements_by_dtype_value(p.vector_elements_by_dtype.begin(), p.vector_elements_by_dtype.end()),
      reduce_queue_depth_value(p.reduce_queue_depth),
      reduce_setup(p.reduce_setup_cycles),
      reduce_flush(p.reduce_flush_cycles),
      reduce_ops_per_cycle_value(p.reduce_ops_per_cycle),
      reduce_ops_by_dtype_value(p.reduce_ops_by_dtype.begin(), p.reduce_ops_by_dtype.end()),
      dma(p.dma),
      bank_queue_depth_value(p.sram_bank_queue_depth),
      sram(p.sram_bytes, p.sram_banks, p.sram_alignment, p.sram_line_bytes,
           p.sram_read_bytes_per_cycle, p.sram_write_bytes_per_cycle,
           p.sram_read_ports, p.sram_write_ports, clockPeriod(),
           p.sram_bank_queue_depth),
      tick_event(this)
{}

void MeshDummyCore::regStats()
{
    ClockedObject::regStats();
    commandsIssued.name(name() + ".commands_issued").desc("Commands admitted");
    commandsCompleted.name(name() + ".commands_completed").desc("Commands completed");
    commandsErrored.name(name() + ".commands_errored").desc("Commands terminated ERROR");
    commandsCancelled.name(name() + ".commands_cancelled").desc("Commands terminated CANCELLED");
    eventsPublished.name(name() + ".events_published").desc("Events published");
    haltCommands.name(name() + ".halt_commands").desc("HALT commands executed");
    requestBegins.name(name() + ".request_begins").desc("REQUEST_BEGIN markers");
    requestEnds.name(name() + ".request_ends").desc("REQUEST_END markers");
    gemmCycles.name(name() + ".gemm_cycles").desc("Analytic GEMM/BMM timer cycles");
    reduceCommands.name(name() + ".reduce_commands").desc("LOCAL_REDUCE commands");
    reduceCycles.name(name() + ".reduce_cycles").desc("Analytic reduce timer cycles");
    sramBankConflicts.name(name() + ".sram_bank_conflicts").desc("SRAM bank conflict cycles");
    sramServiceCycles.name(name() + ".sram_service_cycles").desc("SRAM bank service cycles");
    poisonReadFaults.name(name() + ".poison_read_faults").desc("Poison reads caught");
}

void MeshDummyCore::startup()
{
    fatal_if(reference_compute_forbidden,
             "MeshDummyCore reference_compute=true is forbidden: numeric compute is "
             "not modeled (spec 5.7)");
}

void MeshDummyCore::installProgram(const std::shared_ptr<const DecodedProgram> &decoded)
{
    program = decoded;
    sram.allocations.clear();
    if (!program)
        return;
    weight_tag_sites = weightTagSitesOf(*program, core_id_value);
    for (const auto &allocation : program->allocations)
        if (allocation.owner_core == core_id_value)
            fatal_if(!sram.registerAllocation(allocation.allocation_id,
                                              allocation.offset_bytes,
                                              allocation.size_bytes),
                     "core %u allocation %u rejected by SRAM metadata",
                     core_id_value, allocation.allocation_id);
}

bool MeshDummyCore::readsResultOperand(const DecodedCommand &command) const
{
    // Single-operand ops are in-place (the operand is both input and
    // output); LOCAL_REDUCE accumulates into its result operand.
    return command.operand_count == 1 ||
           command.opcode == mesh_abi::kOpcodeLOCAL_REDUCE;
}

void MeshDummyCore::checkOperandValidity(const DecodedCommand &command)
{
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->operands[command.operand_begin + i];
        auto it = sram.allocations.find(operand.allocation_id);
        if (it == sram.allocations.end())
            fatal("E_ABI_BOUNDS: compute command %u references unknown "
                  "allocation %u",
                  command.command_id, operand.allocation_id);
        const bool is_result = i + 1 == command.operand_count &&
                               !readsResultOperand(command);
        if (is_result)
            continue; // this command establishes the result's validity
        if (!it->second.valid) {
            poisonReadFaults++;
            fatal("E_TENSOR_NOT_RESIDENT: compute command %u reads allocation %u "
                  "before any producer committed",
                  command.command_id, operand.allocation_id);
        }
    }
}

uint64_t MeshDummyCore::operandView(const DecodedOperand &operand,
                                    uint64_t &offset, uint64_t &span) const
{
    auto it = sram.allocations.find(operand.allocation_id);
    if (it == sram.allocations.end())
        return false;
    offset = it->second.offset;
    span = it->second.size;
    if (!operand.shard_id)
        return true;
    for (const auto &shard : program->shards) {
        if (shard.shard_id != operand.shard_id)
            continue;
        offset = it->second.offset + shard.allocation_offset;
        span = shard.span_bytes;
        return true;
    }
    return false;
}

uint64_t MeshDummyCore::reserveOperandReads(const DecodedCommand &command)
{
    // Serial phase 1 (spec 5.7): every READ_ONLY operand plus every
    // in-place READ_WRITE operand (incl. LOCAL_REDUCE dst_old and single
    // operand in-place elementwise) is serviced at issue over its shard
    // view; the result write follows the engine timer.
    fatal_if(command.operand_count == 0,
             "compute command %u has no operands", command.command_id);
    uint64_t worst_stall = 0;
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->operands[command.operand_begin + i];
        const bool is_result = i + 1 == command.operand_count;
        const bool reads = operand.access == mesh_abi::kAccessKindREAD_ONLY ||
                           (is_result && readsResultOperand(command));
        if (!reads)
            continue;
        uint64_t offset = 0, span = 0;
        if (!operandView(operand, offset, span) || span == 0)
            continue;
        auto result = sram.reserve(curTick(), offset, span, false);
        const uint64_t period = clockPeriod();
        sramServiceCycles += period ? result.service_ticks / period
                                    : result.service_ticks;
        sramBankConflicts += period ? result.conflict_ticks / period
                                    : result.conflict_ticks;
        if (result.stall_ticks > worst_stall)
            worst_stall = result.stall_ticks;
    }
    return worst_stall;
}

Tick MeshDummyCore::serviceResultWrite(RuntimeObjectKey key)
{
    // Serial phase 3: the result operand's SRAM write service happens after
    // the engine timer; the command completes only once it is done.
    for (const auto &command : program->commands) {
        if (command.command_id != key.ordinal || command.operand_count == 0)
            continue;
        const DecodedOperand &dst =
            program->operands[command.operand_begin + command.operand_count - 1];
        uint64_t offset = 0, span = 0;
        if (!operandView(dst, offset, span) || span == 0)
            break;
        auto result = sram.reserve(curTick(), offset, span, true);
        const uint64_t period = clockPeriod();
        sramServiceCycles += period ? result.service_ticks / period
                                    : result.service_ticks;
        sramBankConflicts += period ? result.conflict_ticks / period
                                    : result.conflict_ticks;
        return curTick() + result.stall_ticks;
    }
    return curTick();
}

void MeshDummyCore::CompletionEvent::process()
{
    if (result_write && !result_serviced) {
        result_serviced = true;
        const Tick ready = core->serviceResultWrite(command);
        if (ready > curTick()) {
            core->schedule(this, ready);
            return;
        }
    }
    core->completeCommand(command, signal_event, curTick(), error_terminal);
}

bool MeshDummyCore::dmaSramAdmissible(const DecodedDmaDescriptor &descriptor)
{
    const bool src_is_local =
        descriptor.src.memory_space == mesh_abi::kMemorySpaceCORE_SRAM;
    const DecodedDmaEndpoint &local = src_is_local ? descriptor.src
                                                   : descriptor.dst;
    const uint64_t stride = src_is_local ? descriptor.src_stride_bytes
                                         : descriptor.dst_stride_bytes;
    const uint64_t span = descriptor.rows > 1
        ? (descriptor.rows - 1) * stride + descriptor.row_bytes
        : descriptor.row_bytes;
    const bool local_write = descriptor.kind == mesh_abi::kDmaKindLOAD ||
                             descriptor.kind == mesh_abi::kDmaKindPREFETCH ||
                             descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL;
    return sram.canReserve(curTick(), local.offset_bytes, span, local_write);
}

uint64_t MeshDummyCore::reserveDmaSram(const DecodedDmaDescriptor &descriptor,
                                      bool is_write)
{
    const bool src_is_local =
        descriptor.src.memory_space == mesh_abi::kMemorySpaceCORE_SRAM;
    const DecodedDmaEndpoint &local = src_is_local ? descriptor.src
                                                   : descriptor.dst;
    const uint64_t stride = src_is_local ? descriptor.src_stride_bytes
                                         : descriptor.dst_stride_bytes;
    uint64_t span = descriptor.rows > 1
                        ? (descriptor.rows - 1) * stride + descriptor.row_bytes
                        : descriptor.row_bytes;
    fatal_if(!sram.fits(local.offset_bytes, span),
             "internal invariant broken: DMA local span [%llu,%llu) escapes "
             "SRAM (descriptor of command %u)",
             (unsigned long long)local.offset_bytes,
             (unsigned long long)(local.offset_bytes + span),
             descriptor.command_id);
    auto result = sram.reserve(curTick(), local.offset_bytes, span, is_write);
    const uint64_t period = clockPeriod();
    sramServiceCycles += period ? result.service_ticks / period
                                : result.service_ticks;
    sramBankConflicts += period ? result.conflict_ticks / period
                                : result.conflict_ticks;
    return result.stall_ticks;
}

void MeshDummyCore::checkDmaSourceValidity(RuntimeObjectKey key)
{
    for (const auto &command : program->commands) {
        if (command.command_id != key.ordinal || command.operand_count == 0)
            continue;
        const DecodedOperand &source =
            program->operands[command.operand_begin];
        auto it = sram.allocations.find(source.allocation_id);
        if (it == sram.allocations.end())
            fatal("E_ABI_BOUNDS: DMA command %u references unknown allocation %u",
                  key.ordinal, source.allocation_id);
        if (!it->second.valid) {
            poisonReadFaults++;
            fatal("E_TENSOR_NOT_RESIDENT: DMA command %u reads allocation %u "
                  "before any producer committed",
                  key.ordinal, source.allocation_id);
        }
        return;
    }
}

void MeshDummyCore::markOperandValid(const DecodedCommand &command)
{
    if (command.operand_count == 0)
        return;
    // RECV_WAIT's first operand is the peer receive buffer the transfer
    // committed into; every other command establishes its result operand.
    const uint16_t index =
        command.opcode == mesh_abi::kOpcodeRECV_WAIT ? 0 : command.operand_count - 1;
    const DecodedOperand &dst = program->operands[command.operand_begin + index];
    auto it = sram.allocations.find(dst.allocation_id);
    if (it != sram.allocations.end())
        it->second.valid = true;
}

MeshDummyCore::InstanceLedger MeshDummyCore::takeInstanceLedger()
{
    InstanceLedger ledger;
    ledger.completed = std::move(instance_completed_ids);
    ledger.errored = std::move(instance_errored_ids);
    ledger.cancelled = std::move(instance_cancelled_ids);
    return ledger;
}

void MeshDummyCore::dispatchInstance(InstanceGeneration instance)
{
    armRequest(instance);
    startRequest();
}

void MeshDummyCore::armRequest(InstanceGeneration instance)
{
    fatal_if(!program, "core %u dispatched without an installed program", core_id_value);
    fatal_if(instanceActive(), "core %u already running an instance",
             core_id_value);
    instance_generation = instance;
    instance_state = mesh_abi::MeshCoreInstanceState::REQUEST_ARMED;
    error_latch_tick = 0;
    work_drained_tick = 0;
    live_dma_tags.clear();
    dma_tag_command.clear();
    dma_command_tag.clear();
    next_dma_tag = 1;
    allocation_pins.clear();
    command_done_ticks.clear();
    instance_completed_ids.clear();
    instance_errored_ids.clear();
    instance_cancelled_ids.clear();
    overlay_entry_published.clear();
    overlay_exited.clear();
    overlay_entry_tick = 0;
    cache_fills_issued = 0;
    cache_fills_completed = 0;
    cancelAllPendingVisibility();
    cursors.clear();
    live_per_stream.clear();
    committed_transfers.clear();
    recv_waiters.clear();
    fence_waiters.clear();
    outstanding_axi = 0;
    for (auto &kv : sram.allocations)
        kv.second.valid = false; // per-instance producer dominance
    for (const auto &stream : program->streams) {
        if (stream.core_id != core_id_value)
            continue;
        StreamCursor cursor;
        cursor.next_command = stream.command_begin;
        cursor.end_command = stream.command_begin + stream.command_count;
        cursors[stream.stream_id] = cursor;
    }
    DPRINTF(AiMesh, "core %u instance %u dispatched with %zu streams\n", core_id_value,
            instance.value(), cursors.size());
}

void MeshDummyCore::disarmRequest()
{
    fatal_if(instance_state != mesh_abi::MeshCoreInstanceState::REQUEST_ARMED,
             "core %u disarmed without an armed request", core_id_value);
    cancelAllPendingVisibility();
    cursors.clear();
    instance_state = mesh_abi::MeshCoreInstanceState::PROGRAM_READY;
    DPRINTF(AiMesh, "core %u request disarmed before start\n", core_id_value);
}

void MeshDummyCore::startRequest()
{
    fatal_if(!program, "core %u started without an installed program",
             core_id_value);
    fatal_if(instance_state != mesh_abi::MeshCoreInstanceState::REQUEST_ARMED,
             "core %u started without an armed request", core_id_value);
    if (cursors.empty()) {
        instance_state = mesh_abi::MeshCoreInstanceState::REQUEST_DRAINING;
        finishIfHalted();
        return;
    }
    instance_state = mesh_abi::MeshCoreInstanceState::REQUEST_RUNNING;
    scheduleTick();
}

void MeshDummyCore::scheduleTick()
{
    if (!tick_event.scheduled())
        schedule(&tick_event, clockEdge() + 1);
}

uint32_t MeshDummyCore::regionGateRegionId(uint32_t layer_id) const
{
    for (const auto &region : program->moe_dynamic_regions)
        if (region.layer_id == layer_id && region.core_id == core_id_value)
            return region.region_id;
    fatal("core %u has no MoE region for layer %u", core_id_value, layer_id);
}

bool MeshDummyCore::submitOverlayDma(const mesh_abi::DmaDescriptor &descriptor,
                                     const RuntimeObjectKey &descriptor_key,
                                     const RuntimeObjectKey &command_key,
                                     uint64_t issue_tick)
{
    RuntimeObjectKey completion;
    return dma->submit(descriptor, descriptor_key, command_key, completion,
                       Tick(issue_tick));
}

void MeshDummyCore::bindFillPattern(const RuntimeObjectKey &command,
                                    uint64_t pattern)
{
    dma->bindFillPattern(command, pattern);
}

void MeshDummyCore::installWeightCache(std::unique_ptr<MoeWeightCache> cache)
{
    weight_cache = std::move(cache);
}

void MeshDummyCore::setDmaGeometry(uint16_t region_id, uint32_t burst_beats)
{
    sram_region_id = region_id;
    dma_max_burst_beats = burst_beats;
}

void MeshDummyCore::installCacheTokens(
    const std::vector<MoeCacheToken> &tokens,
    const std::map<uint32_t, std::map<uint32_t, uint32_t>> &consumers)
{
    fatal_if(weight_cache == nullptr, "cache tokens without an installed cache");
    for (const auto &token : tokens) {
        const auto layer = consumers.find(token.layer_id);
        uint32_t count = 0;
        if (layer != consumers.end()) {
            const auto tag = layer->second.find(token.base.weight_tag_index);
            if (tag != layer->second.end())
                count = tag->second;
        }
        weight_cache->setTokenConsumers(token.token_id, count);
        if (!token.has_fill || token.outcome !=
                mesh_abi::kCacheResidencyOutcomeNEW_FILL)
            continue;
        const mesh_abi::WeightFillKey &key = token.fill;
        mesh_abi::DmaDescriptor descriptor;
        descriptor.descriptor_id = key.fill_incarnation;
        descriptor.command_id = key.fill_incarnation;
        descriptor.transfer_id = 0;
        descriptor.owner_core = core_id_value;
        descriptor.kind = mesh_abi::kDmaKindLOAD;
        descriptor.src.memory_space = mesh_abi::kMemorySpaceHBM;
        descriptor.src.region_id = 0;
        descriptor.src.owner_core = 0;
        descriptor.src.offset_bytes = cacheFillSource(key.weight_tag_index);
        descriptor.dst.memory_space = mesh_abi::kMemorySpaceCORE_SRAM;
        descriptor.dst.region_id = sram_region_id;
        descriptor.dst.owner_core = core_id_value;
        descriptor.dst.offset_bytes = weight_cache->slotBase(token.slot_id);
        const uint64_t bytes = cacheFillBytes(key.weight_tag_index);
        descriptor.rows = 1;
        descriptor.row_bytes = bytes;
        descriptor.src_stride_bytes = bytes;
        descriptor.dst_stride_bytes = bytes;
        descriptor.useful_bytes = bytes;
        descriptor.physical_storage_bytes = bytes;
        descriptor.axi_id = key.fill_incarnation & 0xFFFF;
        descriptor.max_burst_beats = dma_max_burst_beats;
        descriptor.completion_event = 0;
        RuntimeObjectKey descriptor_key;
        descriptor_key.instance = instance_generation;
        descriptor_key.domain = mesh_abi::MeshObjectDomain::WEIGHT_FILL;
        descriptor_key.regionGroupId =
            (uint32_t(core_id_value) << 16) | uint16_t(token.layer_id);
        descriptor_key.regionId = token.slot_id;
        descriptor_key.kind = mesh_abi::MeshObjectKind::DESCRIPTOR;
        descriptor_key.ordinal = key.fill_incarnation;
        DPRINTF(AiMesh, "core %u fill submit tag=%u inc=%u slot=%u bytes=%llu\n",
                core_id_value, key.weight_tag_index, key.fill_incarnation,
                token.slot_id, (unsigned long long)bytes);
        weight_cache->markFilling(key);
        PendingCacheFill pending;
        pending.fill = key;
        pending.bytes = bytes;
        pending.token_id = token.token_id;
        if (!submitOverlayDma(descriptor, descriptor_key, descriptor_key,
                              curTick())) {
            // Finite descriptor queue: keep the reservation and the token,
            // and re-submit once the engine accepts work again.
            QueuedCacheFill queued;
            queued.descriptor = descriptor;
            queued.descriptor_key = descriptor_key;
            queued.fill = key;
            queued.pending = pending;
            queued_cache_fills[key.weight_tag_index] = queued;
            continue;
        }
        weight_cache->advanceEngineEdge();
        weight_cache->markIssued(key);
        weight_cache->markInFlight(key);
        pending_cache_fills[key.fill_incarnation] = pending;
        cache_fills_issued++;
    }
}

const WeightTagSiteV1 &MeshDummyCore::weightTagSite(uint32_t tag_index) const
{
    for (const auto &site : weight_tag_sites)
        if (site.weight_tag_index == tag_index)
            return site;
    fatal("weight tag %u has no expert on core %u", tag_index, core_id_value);
}

uint64_t MeshDummyCore::cacheFillSource(uint32_t tag_index) const
{
    return weightTagSite(tag_index).weight_region_offset;
}

uint64_t MeshDummyCore::cacheFillBytes(uint32_t tag_index) const
{
    return weightTagSite(tag_index).weight_bytes;
}

void MeshDummyCore::installOverlay(uint32_t layer_id,
                                   const MoeOverlayGraph &graph,
                                   const MoeOverlayAddressSpace *addresses)
{
    fatal_if(scoreboard == nullptr,
             "core %u cannot install an overlay without a scoreboard",
             core_id_value);
    MoeOverlayExecutor::Config config;
    config.core_id = core_id_value;
    config.dma_bytes_per_cycle = dmaBytesPerCycle;
    config.ticks_per_cycle = clockPeriod();
    auto executor = std::make_unique<MoeOverlayExecutor>(
        config, scoreboard, &sram, overlay_bus, addresses);
    executor->bindDma(this);
    executor->bindCompute(this);
    executor->bindComputeCommit(&computeDigests);
    for (const auto &layer : program->moe_layer_specs)
        if (layer.layer_id == layer_id && layer.kernel_spec_index <
                program->moe_kernel_specs.size())
            overlay_kernels[layer_id] =
                &program->moe_kernel_specs[layer.kernel_spec_index];
    executor->load(layer_id, instance_generation, graph);
    overlay_executors[layer_id] = std::move(executor);
}

bool MeshDummyCore::overlayDrained() const
{
    for (const auto &kv : overlay_executors)
        if (!kv.second->drained())
            return false;
    return !overlay_executors.empty();
}

bool MeshDummyCore::overlayGroupExited(uint32_t layer_id) const
{
    return overlay_exited.count(layer_id) != 0;
}

void MeshDummyCore::tick()
{
    // Late in-flight ticks after the instance finished are harmless.
    if (!instanceActive())
        return;

    if (owned_work_drain_pending && !halted()) {
        if (weight_cache != nullptr) {
            weight_cache->advanceCacheEdge();
            weight_cache->noteOwnedWorkDrained(cache_batch_id);
            if (weight_cache->liveTokensOfBatch(cache_batch_id) != 0) {
                // A cache-owned fill is still draining: its instance-owned
                // token is released exactly once at that fill terminal, so
                // ERROR_DRAINED waits for the cache edge that sees it gone.
                retryQueuedCacheFills();
                scheduleTick();
                return;
            }
        }
        instance_state =
            mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINED;
        DPRINTF(AiMesh, "core %u error-drained at %llu\n", core_id_value,
                (unsigned long long)curTick());
        if (dispatcher)
            dispatcher->notifyCoreHalted(core_id_value);
        return;
    }

    if (instanceErrored() && !halted())
        cancelRemainingCommands();

    retryQueuedCacheFills();
    retryCacheReservations();
    for (auto &kv : overlay_executors) {
        MoeOverlayExecutor *executor = kv.second.get();
        if (executor->started())
            executor->tick(curTick());
        if (executor->failed())
            latchInstanceError(curTick());
        for (const auto &event : executor->signalled()) {
            if (event.kind != mesh_abi::MeshObjectKind::EVENT)
                continue;
            if (event.regionId == 0 && event.ordinal != 0 &&
                overlay_exited.insert(kv.first).second) {
                if (dispatcher != nullptr)
                    dispatcher->notifyOverlayExit(kv.first);
            }
        }
    }

    bool progressed = false;
    // The overlay entry is published on every core edge that reaches the
    // insertion point, independent of whether a static command could issue:
    // a cursor blocked at the gate must not stop the cached fills that the
    // overlay waits for from being observed.
    for (auto &kv : overlay_executors) {
        if (overlay_entry_published.count(kv.first) != 0)
            continue;
        if (region_gate.phase(kv.first) !=
            MoeInsertionGate::RegionPhase::REACHED)
            continue;
        if (weight_cache != nullptr &&
            weight_cache->hasPendingSubscribers()) {
            progressed = true;
            continue;
        }
        RuntimeObjectKey entry;
        entry.instance = instance_generation;
        entry.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
        entry.regionGroupId = uint16_t(kv.first);
        entry.regionId = uint16_t(regionGateRegionId(kv.first));
        entry.kind = mesh_abi::MeshObjectKind::EVENT;
        entry.ordinal = 0;
        DPRINTF(AiMesh, "core %u publish overlay entry layer=%u\n",
                core_id_value, kv.first);
        kv.second->publishEntry(entry, curTick());
        overlay_entry_published.insert(kv.first);
        if (overlay_entry_tick == 0)
            overlay_entry_tick = curTick();
        progressed = true;
    }
    for (auto &kv : cursors) {
        StreamCursor &cursor = kv.second;
        for (uint32_t issued = 0; issued < decode_width_value; issued++) {
            if (cursor.repeat_gate_command.ordinal != 0 &&
                    !cursor.replay.active)
                break; // REPEAT drain gate: post-REPEAT cursor stays closed
            if (liveStreamCommands(kv.first) >= admit_window_value)
                break; // finite admit window: real decode-stage backpressure
            if (region_gate.blockedIndex(cursor.next_command))
                break; // MoE insertion gate: overlay group has not exited yet
            if (cursor.next_command >= cursor.end_command)
                break;
            const uint32_t index = cursor.next_command;
            const DecodedCommand &command = program->commands[index];
            if (!waitsSatisfied(command))
                break;
            if (!tryIssue(command))
                break; // backpressure: retry next cycle
            region_gate.noteIssued(command.command_id);
            if (cursor.next_command == index)            if (cursor.next_command == index)
                cursor.next_command = index + 1; // REPEAT may rewind the cursor
            if (cursor.replay.active &&
                cursor.next_command >= cursor.replay.subrange_end) {
                // Generation window fully issued; gate until it drains.
                cursor.replay.active = false;
                scheduleGenerationCheck(kv.first);
            }
            progressed = true;
        }
    }

    if (progressed || live_commands > 0 || overlayPending() ||
        instance_state != mesh_abi::MeshCoreInstanceState::REQUEST_DRAINING)
        scheduleTick();

    finishIfHalted();
}

bool MeshDummyCore::waitsSatisfied(const DecodedCommand &command) const
{
    fatal_if(scoreboard == nullptr, "core %u has no scoreboard", core_id_value);
    for (uint16_t w = 0; w < command.wait_count; w++)
        if (!scoreboard->visible(staticProgramObject(
                instance_generation, mesh_abi::MeshObjectKind::EVENT,
                program->waits[command.wait_begin + w])))
            return false;
    return true;
}

const DecodedAttr *MeshDummyCore::attrOf(const DecodedCommand &command) const
{
    if (command.attr_index == 0 || command.attr_index > program->attrs.size())
        return nullptr;
    return &program->attrs[command.attr_index - 1];
}

bool MeshDummyCore::fenceScopeMatches(const DmaTagInfo &info, uint16_t scope)
{
    using namespace mesh_abi;
    switch (scope) {
    case kFenceScopeDMA_READ:
        return info.kind == kDmaKindLOAD || info.kind == kDmaKindPREFETCH;
    case kFenceScopeDMA_WRITE:
        return info.kind == kDmaKindSTORE || info.kind == kDmaKindLOCAL_FILL;
    case kFenceScopeP2P:
        return info.kind == kDmaKindP2P_PUSH;
    case kFenceScopeHOST_SHARED_WRITE:
        return info.kind == kDmaKindSTORE &&
               info.dst_space == kMemorySpaceHOST_SHARED;
    case kFenceScopeALL_INSTANCE:
        return true;
    default:
        return false;
    }
}

void MeshDummyCore::completeFence(RuntimeObjectKey command,
                                  RuntimeObjectKey signal_event)
{
    auto *event = new CompletionEvent(this, command, signal_event);
    schedule(event, clockEdge() + 1);
}

bool MeshDummyCore::tryIssue(const DecodedCommand &command)
{
    switch (command.opcode) {
    case mesh_abi::kOpcodeDMA_LOAD:
    case mesh_abi::kOpcodeDMA_STORE:
    case mesh_abi::kOpcodeDMA_P2P_PUSH:
    case mesh_abi::kOpcodeDMA_PREFETCH:
    case mesh_abi::kOpcodeDMA_FILL:
        return issueDma(command);
    case mesh_abi::kOpcodeGEMM:
    case mesh_abi::kOpcodeBMM:
        if (tensor_queue_used >= tensor_queue_depth_value)
            return false;
        tensor_queue_used++;
        issueCompute(command, attrOf(command));
        return true;
    case mesh_abi::kOpcodeELEMENTWISE:
    case mesh_abi::kOpcodeSOFTMAX:
    case mesh_abi::kOpcodeNORM:
        if (vector_queue_used >= vector_queue_depth_value)
            return false;
        vector_queue_used++;
        issueCompute(command, attrOf(command));
        return true;
    case mesh_abi::kOpcodeLOCAL_REDUCE:
        if (reduce_queue_used >= reduce_queue_depth_value)
            return false;
        reduce_queue_used++;
        issueCompute(command, attrOf(command));
        return true;
    default:
        issueControl(command);
        return true;
    }
}

bool MeshDummyCore::weightSlotRange(uint32_t tag_index, uint64_t &offset,
                                    uint64_t &bytes) const
{
    if (weight_cache == nullptr)
        return false;
    for (const auto &slot : weight_cache->slots()) {
        if (slot.state != kCacheSlotValid ||
            slot.weight_tag_index != tag_index || slot.valid_bytes == 0)
            continue;
        offset = weight_cache->slotBase(slot.slot_id);
        bytes = slot.valid_bytes;
        return true;
    }
    return false;
}

bool MeshDummyCore::computeAdmissible(uint16_t opcode) const
{
    switch (opcode) {
    case mesh_abi::kOpcodeGEMM:
    case mesh_abi::kOpcodeBMM:
        return tensor_queue_used < tensor_queue_depth_value;
    case mesh_abi::kOpcodeELEMENTWISE:
    case mesh_abi::kOpcodeSOFTMAX:
    case mesh_abi::kOpcodeNORM:
        return vector_queue_used < vector_queue_depth_value;
    case mesh_abi::kOpcodeLOCAL_REDUCE:
        return reduce_queue_used < reduce_queue_depth_value;
    default:
        return true;
    }
}

void MeshDummyCore::noteComputeAdmitted(uint16_t opcode)
{
    switch (opcode) {
    case mesh_abi::kOpcodeGEMM:
    case mesh_abi::kOpcodeBMM:
        tensor_queue_used++;
        break;
    case mesh_abi::kOpcodeELEMENTWISE:
    case mesh_abi::kOpcodeSOFTMAX:
    case mesh_abi::kOpcodeNORM:
        vector_queue_used++;
        break;
    case mesh_abi::kOpcodeLOCAL_REDUCE:
        reduce_queue_used++;
        break;
    default:
        break;
    }
}

void MeshDummyCore::noteComputeFinished(uint16_t opcode, uint64_t cycles)
{
    switch (opcode) {
    case mesh_abi::kOpcodeGEMM:
    case mesh_abi::kOpcodeBMM:
        fatal_if(tensor_queue_used == 0, "tensor queue underflow");
        tensor_queue_used--;
        gemmCycles += cycles;
        break;
    case mesh_abi::kOpcodeELEMENTWISE:
    case mesh_abi::kOpcodeSOFTMAX:
    case mesh_abi::kOpcodeNORM:
        fatal_if(vector_queue_used == 0, "vector queue underflow");
        vector_queue_used--;
        break;
    case mesh_abi::kOpcodeLOCAL_REDUCE:
        fatal_if(reduce_queue_used == 0, "reduce queue underflow");
        reduce_queue_used--;
        reduceCommands++;
        reduceCycles += cycles;
        break;
    default:
        break;
    }
}

uint64_t MeshDummyCore::computeCycles(const MoeComputeShape &shape) const
{
    const auto kernel = overlay_kernels.find(shape.layer_id);
    fatal_if(kernel == overlay_kernels.end() || kernel->second == nullptr,
             "overlay layer %u has no frozen kernel spec", shape.layer_id);
    const mesh_abi::MoeKernelSpec &spec = *kernel->second;
    fatal_if(spec.output_token_bytes == 0 ||
                 shape.payload_bytes % spec.output_token_bytes != 0,
             "E_ABI_OVERFLOW: overlay layer %u command payload %u B is not a "
             "whole number of %u B rows",
             shape.layer_id, shape.payload_bytes, spec.output_token_bytes);
    const uint64_t rows = shape.payload_bytes / spec.output_token_bytes;
    if (shape.role == mesh_abi::kMoeCommandRoleCOPY_THROUGH ||
        shape.role == mesh_abi::kMoeCommandRoleLOCAL_REDUCE) {
        const uint64_t ops_per_cycle = throughputFor(
            spec.accum_dtype, reduce_ops_by_dtype_value,
            reduce_ops_per_cycle_value);
        const uint64_t engine = reduceEngineCycles(
            rows * spec.n, shape.fan_in, ops_per_cycle, "overlay combine");
        return framedCycles(spec.combine_setup_cycles, engine,
                            spec.combine_flush_cycles);
    }
    const uint64_t macs = throughputFor(
        spec.input_dtype, tensor_macs_by_dtype_value,
        tensor_macs_per_cycle_value);
    const uint64_t engine =
        tensorEngineCycles(spec.batch, rows, spec.n, spec.k, macs,
                           spec.efficiency_q16, "overlay expert");
    return framedCycles(spec.tensor_setup_cycles, engine,
                        spec.tensor_flush_cycles);
}

uint64_t MeshDummyCore::throughputFor(uint16_t dtype,
                                      const std::vector<uint64_t> &by_dtype,
                                      uint64_t fallback) const
{
    // dtype enum: FP32=1 FP16=2 BF16=3 INT8=4 INT32=5.
    if (by_dtype.size() >= 5 && dtype >= 1 && dtype <= 5 && by_dtype[dtype - 1] > 0)
        return by_dtype[dtype - 1];
    return fallback;
}

uint64_t MeshDummyCore::computeCycles(const DecodedCommand &command,
                                      const DecodedAttr *attr) const
{
    fatal_if(attr == nullptr, "compute command %u missing attr", command.command_id);
    switch (command.opcode) {
    case mesh_abi::kOpcodeGEMM:
    case mesh_abi::kOpcodeBMM: {
        uint16_t dtype = 0;
        uint32_t batch, m, n, k;
        if (command.opcode == mesh_abi::kOpcodeGEMM) {
            const auto &gemm = attr->as<mesh_abi::GemmV1>();
            batch = gemm.batch;
            m = gemm.m;
            n = gemm.n;
            k = gemm.k;
            dtype = gemm.dtype;
        } else {
            const auto &bmm = attr->as<mesh_abi::BmmV1>();
            batch = bmm.batch;
            m = bmm.m;
            n = bmm.n;
            k = bmm.k;
            dtype = bmm.dtype;
        }
        uint32_t efficiency = 65536;
        if (command.opcode == mesh_abi::kOpcodeGEMM)
            efficiency = attr->as<mesh_abi::GemmV1>().efficiency_q16;
        else
            efficiency = attr->as<mesh_abi::BmmV1>().efficiency_q16;
        const uint64_t macs = throughputFor(
            dtype, tensor_macs_by_dtype_value, tensor_macs_per_cycle_value);
        char context[64];
        snprintf(context, sizeof(context), "GEMM command %u",
                 command.command_id);
        const uint64_t engine = tensorEngineCycles(batch, m, n, k, macs,
                                                   efficiency, context);
        return framedCycles(tensor_setup, engine, tensor_flush);
    }
    case mesh_abi::kOpcodeELEMENTWISE: {
        const auto &ew = attr->as<mesh_abi::ElementwiseV1>();
        const uint64_t lanes =
            throughputFor(ew.dtype, vector_elements_by_dtype_value,
                          vector_elements_per_cycle_value);
        const __int128 ops =
            __int128(ew.element_count) * ew.ops_per_element;
        fatal_if(ops <= 0 || ops >= (__int128(1) << 63),
                 "E_ABI_OVERFLOW: elementwise workload exceeds the "
                 "schedulable cycle range (command %u)",
                 command.command_id);
        return uint64_t(Cycles((ops + lanes - 1) / lanes));
    }
    case mesh_abi::kOpcodeSOFTMAX: {
        const auto &softmax = attr->as<mesh_abi::SoftmaxV1>();
        const __int128 ops = __int128(2) * softmax.axis_size;
        fatal_if(ops >= (__int128(1) << 63),
                 "E_ABI_OVERFLOW: softmax workload exceeds the schedulable "
                 "cycle range (command %u)",
                 command.command_id);
        return uint64_t(
            Cycles((ops + vector_elements_per_cycle_value - 1) /
                   vector_elements_per_cycle_value));
    }
    case mesh_abi::kOpcodeNORM: {
        const auto &norm = attr->as<mesh_abi::NormV1>();
        const __int128 ops = __int128(2) * norm.element_count;
        fatal_if(ops >= (__int128(1) << 63),
                 "E_ABI_OVERFLOW: norm workload exceeds the schedulable "
                 "cycle range (command %u)",
                 command.command_id);
        return uint64_t(
            Cycles((ops + vector_elements_per_cycle_value - 1) /
                   vector_elements_per_cycle_value));
    }
    case mesh_abi::kOpcodeLOCAL_REDUCE: {
        const auto &reduce = attr->as<mesh_abi::ReduceV1>();
        const uint64_t ops_per_cycle = throughputFor(
            reduce.dtype, reduce_ops_by_dtype_value,
            reduce_ops_per_cycle_value);
        fatal_if(reduce.fan_in < 2,
                 "E_ABI_OVERFLOW: reduce workload exceeds the schedulable "
                 "cycle range (command %u)",
                 command.command_id);
        char context[64];
        snprintf(context, sizeof(context), "LOCAL_REDUCE command %u",
                 command.command_id);
        const uint64_t engine = reduceEngineCycles(
            reduce.element_count, reduce.fan_in, ops_per_cycle, context);
        return framedCycles(reduce_setup, engine, reduce_flush);
    }
    default:
        return 1;
    }
}

void MeshDummyCore::issueCompute(const DecodedCommand &command, const DecodedAttr *attr)
{
    commandsIssued++;
    command_issue_ticks[commandKey(command.command_id)] = curTick();
    checkOperandValidity(command);

    const uint64_t cycles = computeCycles(command, attr);
    // Serial phases (spec 5.7): operand read service -> setup/timer/flush ->
    // result write service; the completion event arms the write phase.
    const uint64_t read_stall = reserveOperandReads(command);
    if (command.opcode == mesh_abi::kOpcodeGEMM || command.opcode == mesh_abi::kOpcodeBMM)
        gemmCycles += cycles;
    if (command.opcode == mesh_abi::kOpcodeLOCAL_REDUCE) {
        reduceCommands++;
        reduceCycles += cycles;
    }
    live_commands++;
    live_per_stream[command.stream_id]++;
    auto *event = new CompletionEvent(this, commandKey(command.command_id),
                                      eventKey(command.signal_event), false,
                                      true);
    schedule(event, clockEdge(Cycles(cycles)) + read_stall + 1);
    DPRINTF(AiMesh, "core %u compute command %u opcode=%u cycles=%llu stall=%llu\n",
            core_id_value,
            command.command_id, command.opcode, (unsigned long long)cycles);
}

void MeshDummyCore::issueControl(const DecodedCommand &command)
{
    commandsIssued++;
    command_issue_ticks[commandKey(command.command_id)] = curTick();

    switch (command.opcode) {
    case mesh_abi::kOpcodeEVENT_WAIT:
        // Dependence already expressed via wait events; one control cycle.
        break;
    case mesh_abi::kOpcodeAXI_FENCE: {
        // Fences only transactions accepted before the fence, filtered by
        // the declared scope (spec 5.6): DMA_READ/DMA_WRITE/P2P/
        // HOST_SHARED_WRITE cover this core's matching tags, ALL_INSTANCE
        // covers every core of the instance via the dispatcher.
        const DecodedAttr *attr = attrOf(command);
        fatal_if(attr == nullptr || attr->kind != mesh_abi::kAttrKindFENCE_V1,
                 "AXI_FENCE command %u missing FENCE_V1 attr",
                 command.command_id);
        const uint16_t scope = attr->as<mesh_abi::FenceV1>().fence_scope;
        std::set<uint64_t> waiting;
        for (uint64_t tag : live_dma_tags) {
            auto it = dma_tag_info.find(tag);
            if (it != dma_tag_info.end() && fenceScopeMatches(it->second, scope))
                waiting.insert(tag);
        }
        if (scope == mesh_abi::kFenceScopeALL_INSTANCE && dispatcher &&
            dispatcher->registerFence(this, command.command_id,
                                      command.signal_event)) {
            live_commands++;
            live_per_stream[command.stream_id]++;
            return; // completed when every captured tag (any core) retires
        }
        if (!waiting.empty()) {
            fence_waiters[commandKey(command.command_id)] = waiting;
            live_commands++;
            live_per_stream[command.stream_id]++;
            return; // completed when every captured tag retires
        }
        break;
    }
    case mesh_abi::kOpcodeREPEAT: {
        const DecodedAttr *attr = attrOf(command);
        issueRepeat(command, attr);
        return;
    }
    case mesh_abi::kOpcodeRECV_WAIT: {
        const DecodedAttr *attr = attrOf(command);
        fatal_if(attr == nullptr, "RECV_WAIT without attr");
        const RuntimeObjectKey transfer =
            transferKey(attr->as<mesh_abi::RecvWaitV1>().transfer_id);
        live_commands++;
        live_per_stream[command.stream_id]++;
        auto done = committed_transfers.find(transfer);
        if (done != committed_transfers.end() && done->second) {
            // Transfer already committed: complete at the next edge.
            auto *event = new CompletionEvent(
                this, commandKey(command.command_id),
                eventKey(command.signal_event));
            schedule(event, clockEdge() + 1);
            return;
        }
        recv_waiters[transfer] = commandKey(command.command_id);
        return;
    }
    case mesh_abi::kOpcodeHALT:
        instance_state = mesh_abi::MeshCoreInstanceState::REQUEST_DRAINING;
        haltCommands++;
        commandsCompleted++;
        completed_command_ids.push_back(commandKey(command.command_id));
        instance_completed_ids.push_back(commandKey(command.command_id));
        finishIfHalted();
        return;
    case mesh_abi::kOpcodeREQUEST_BEGIN:
        requestBegins++;
        break;
    case mesh_abi::kOpcodeREQUEST_END:
        requestEnds++;
        break;
    default:
        break;
    }
    live_commands++;
    live_per_stream[command.stream_id]++;
    auto *event = new CompletionEvent(this, commandKey(command.command_id),
                                      eventKey(command.signal_event));
    schedule(event, clockEdge() + 1);
}

bool MeshDummyCore::tryPinDmaAllocations(const DecodedCommand &command)
{
    // SRAM allocations touched by an in-flight DMA are pinned: a second DMA
    // on the same allocation must wait (spec 7.1 admit-to-completion pin).
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->operands[command.operand_begin + i];
        auto it = sram.allocations.find(operand.allocation_id);
        if (it == sram.allocations.end())
            continue;
        if (allocation_pins[operand.allocation_id] > 0)
            return false;
    }
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->operands[command.operand_begin + i];
        if (sram.allocations.count(operand.allocation_id))
            allocation_pins[operand.allocation_id]++;
    }
    return true;
}

void MeshDummyCore::unpinDmaAllocations(const DecodedCommand &command)
{
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->operands[command.operand_begin + i];
        auto pin = allocation_pins.find(operand.allocation_id);
        if (pin == allocation_pins.end())
            continue;
        fatal_if(pin->second == 0, "allocation pin underflow");
        if (--pin->second == 0)
            allocation_pins.erase(pin);
    }
}

bool MeshDummyCore::issueDma(const DecodedCommand &command)
{
    bool submitted = false;
    for (const auto &descriptor : program->descriptors) {
        if (descriptor.command_id != command.command_id)
            continue;
        fatal_if(submitted,
                 "internal invariant broken: DMA command %u has multiple "
                 "descriptors",
                 command.command_id);
        if (descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL) {
            const DecodedAttr *attr = attrOf(command);
            fatal_if(attr == nullptr || attr->kind != mesh_abi::kAttrKindFILL_V1,
                     "DMA_FILL command %u missing FILL_V1 attr", command.command_id);
            dma->bindFillPattern(
                commandKey(command.command_id),
                attr->as<mesh_abi::FillV1>().pattern);
        }
        if (descriptor.useful_bytes > 0 && !dmaSramAdmissible(descriptor))
            return false; // bank queue full: retry next cycle
        if (!tryPinDmaAllocations(command))
            return false; // pinned by an in-flight DMA: retry next cycle
        if (!dma->submit(descriptor, descriptorKey(descriptor.descriptor_id),
                         commandKey(command.command_id),
                         eventKey(descriptor.completion_event), curTick())) {
            unpinDmaAllocations(command);
            return false; // backpressure: retry next cycle, no side effects
        }
        if (descriptor.kind == mesh_abi::kDmaKindP2P_PUSH &&
            descriptor.useful_bytes > 0 && dispatcher)
            dispatcher->armTransferExpectation(descriptor.transfer_id);
        commandsIssued++;
        command_issue_ticks[commandKey(command.command_id)] = curTick();
        live_commands++;
        live_per_stream[command.stream_id]++;
        outstanding_axi++;
        const uint64_t tag = next_dma_tag++;
        live_dma_tags.insert(tag);
        dma_tag_info[tag] = DmaTagInfo{descriptor.kind,
                                       descriptor.dst.memory_space};
        dma_tag_command[tag] = commandKey(command.command_id);
        dma_command_tag[commandKey(command.command_id)] = tag;
        submitted = true;
    }
    fatal_if(!submitted,
             "internal invariant broken: DMA command %u has no descriptor",
             command.command_id);
    return true;
}

void MeshDummyCore::resetSubrangeEvents(uint16_t stream_id)
{
    const StreamCursor &cursor = cursors[stream_id];
    for (uint32_t index = cursor.replay.subrange_begin;
         index < cursor.replay.subrange_end; index++) {
        const auto &member = program->commands[index];
        if (member.signal_event)
            cancelPendingVisibility(staticProgramObject(
                instance_generation, mesh_abi::MeshObjectKind::EVENT,
                member.signal_event));
        for (const auto &descriptor : program->descriptors)
            if (descriptor.command_id == member.command_id)
                cancelPendingVisibility(staticProgramObject(
                    instance_generation, mesh_abi::MeshObjectKind::EVENT,
                    descriptor.completion_event));
    }
}

void MeshDummyCore::setDispatcher(MeshDispatcher *d)
{
    dispatcher = d;
    if (d)
        d->bindScoreboardTo(this);
}

void MeshDummyCore::cancelAllPendingVisibility()
{
    for (auto &kv : pending_visibility) {
        if (kv.second->scheduled())
            deschedule(kv.second);
    }
    pending_visibility.clear();
}

void MeshDummyCore::cancelPendingVisibility(RuntimeObjectKey event)
{
    scoreboard->unpublish(event);
    auto pending = pending_visibility.find(event);
    if (pending == pending_visibility.end())
        return;
    if (pending->second->scheduled())
        deschedule(pending->second);
    pending_visibility.erase(pending);
}

void MeshDummyCore::issueRepeat(const DecodedCommand &command, const DecodedAttr *attr)
{
    fatal_if(attr == nullptr || attr->kind != mesh_abi::kAttrKindREPEAT_V1,
             "REPEAT command %u missing REPEAT_V1 attr", command.command_id);
    const auto &repeat = attr->as<mesh_abi::RepeatV1>();
    const uint32_t count = repeat.subrange_command_count;
    const uint32_t repeat_count = repeat.repeat_count;

    StreamCursor &cursor = cursors[command.stream_id];
    const uint32_t repeat_index = cursor.next_command; // still at REPEAT
    cursor.repeat_gate_command = commandKey(command.command_id);
    cursor.post_repeat_next = repeat_index + 1;
    cursor.replay.repeat_command_index = repeat_index;
    cursor.replay.subrange_begin = repeat_index - count;
    cursor.replay.subrange_end = repeat_index;
    cursor.replay.total_generations = repeat_count;
    cursor.replay.generation = 1;

    command_issue_ticks[commandKey(command.command_id)] = curTick();
    live_commands++;
    live_per_stream[command.stream_id]++;

    if (repeat_count > 1) {
        // Generations 1..N-1 replay the immutable window; generation 0 was
        // the normal pre-REPEAT execution.  Replay NEVER starts at decode
        // time: the first generation-check event waits until generation 0
        // has fully drained (only the REPEAT itself may remain live), then
        // resets the window events and opens the replay cursor (spec 4.1:
        // sequential generations, {base, generation} event instances).
        DPRINTF(AiMesh, "core %u REPEAT %u: generations=%u subrange=[%u,%u)\n",
                core_id_value, command.command_id, repeat_count,
                cursor.replay.subrange_begin, cursor.replay.subrange_end);
        scheduleGenerationCheck(command.stream_id);
        return;
    }
    // repeat_count == 1: nothing to replay, one control cycle and done.
    auto *event = new CompletionEvent(this, commandKey(command.command_id),
                                      eventKey(command.signal_event));
    schedule(event, clockEdge() + 1);
}

void MeshDummyCore::scheduleGenerationCheck(uint16_t stream_id)
{
    auto *event = new GenerationEvent(this, stream_id);
    schedule(event, clockEdge() + 1);
}

uint32_t MeshDummyCore::liveStreamCommands(uint16_t stream_id) const
{
    auto it = live_per_stream.find(stream_id);
    return it == live_per_stream.end() ? 0 : it->second;
}

void MeshDummyCore::onGenerationDrained(uint16_t stream_id)
{
    StreamCursor &cursor = cursors[stream_id];
    if (cursor.repeat_gate_command.ordinal == 0)
        return;
    // The REPEAT itself is the single permitted live command while draining.
    if (liveStreamCommands(stream_id) > 1) {
        scheduleGenerationCheck(stream_id);
        return;
    }
    if (cursor.replay.generation < cursor.replay.total_generations) {
        resetSubrangeEvents(stream_id);
        cursor.replay.active = true;
        cursor.next_command = cursor.replay.subrange_begin;
        DPRINTF(AiMesh, "core %u REPEAT %u: generation %u starts\n", core_id_value,
                cursor.repeat_gate_command.ordinal, cursor.replay.generation);
        cursor.replay.generation++;
        scheduleTick();
        return;
    }
    // Final generation drained; REPEAT itself completes in one control cycle.
    const RuntimeObjectKey repeat_id = cursor.repeat_gate_command;
    uint32_t signal = 0;
    for (const auto &command : program->commands)
        if (commandKey(command.command_id) == repeat_id) {
            signal = command.signal_event;
            break;
        }
    cursor.repeat_gate_command = RuntimeObjectKey();
    cursor.next_command = cursor.post_repeat_next;
    auto *event = new CompletionEvent(this, repeat_id, eventKey(signal));
    schedule(event, clockEdge() + 1);
}

void MeshDummyCore::onDmaCompleted(RuntimeObjectKey command,
                                   RuntimeObjectKey completion_event,
                                   Tick commit_tick, DmaStatus status)
{
    if (command.domain == mesh_abi::MeshObjectDomain::WEIGHT_FILL) {
        if (weight_cache == nullptr)
            fatal("cache fill completion without an installed cache");
        const auto entry = pending_cache_fills.find(command.ordinal);
        if (entry == pending_cache_fills.end()) {
            // Error drain already released this fill: a late engine
            // completion is expected and carries no further work.
            if (instanceErrored())
                return;
            fatal("cache fill completion without a live fill");
        }
        const PendingCacheFill pending = entry->second;
        pending_cache_fills.erase(entry);
        if (status == DmaStatus::OK) {
            weight_cache->noteFillSuccess(pending.fill, pending.bytes,
                                          commit_tick);
            DPRINTF(AiMesh, "core %u fill done tag=%u bytes=%llu\n",
                    core_id_value, pending.fill.weight_tag_index,
                    (unsigned long long)pending.bytes);
            cache_fills_completed++;
            // The line stays pinned until the last expert of the batch that
            // reads it drains, or until the fault join releases it once the
            // fill terminal, the owned drain and the fanout all happened.
            if (instanceErrored())
                weight_cache->releaseBatchTokens(cache_batch_id);
            return;
        }
        weight_cache->noteFillFailure(
            pending.fill, mesh_abi::kWeightFillFailureSiteCACHE_FILL_AXI_R,
            commit_tick, weightFillSource(pending.fill));
        cache_fills_errored++;
        latchInstanceError(commit_tick);
        weight_cache->releaseToken(pending.token_id);
        return;
    }
    if (command.domain == mesh_abi::MeshObjectDomain::MOE_OVERLAY) {
        const auto executor = overlay_executors.find(command.regionGroupId);
        fatal_if(executor == overlay_executors.end(),
                 "overlay DMA completion for an unknown layer");
        executor->second->completeDma(command, uint8_t(status));
        return;
    }
    if (outstanding_axi > 0)
        outstanding_axi--;
    {
        auto tag_it = dma_command_tag.find(command);
        fatal_if(tag_it == dma_command_tag.end(),
                 "DMA completion without a live tag (command %u)",
                 command.ordinal);
        const uint64_t retired_tag = tag_it->second;
        live_dma_tags.erase(retired_tag);
        dma_tag_info.erase(retired_tag);
        dma_tag_command.erase(retired_tag);
        dma_command_tag.erase(tag_it);
        if (dispatcher)
            dispatcher->onDmaTagRetired(core_id_value, retired_tag);
    }
    for (const auto &candidate : program->commands)
        if (candidate.command_id == command.ordinal) {
            unpinDmaAllocations(candidate);
            break;
        }
    if (status != DmaStatus::OK)
        latchInstanceError(commit_tick);
    if (status == DmaStatus::OK)
        for (const auto &candidate : program->commands)
            if (candidate.command_id == command.ordinal) {
                markOperandValid(candidate);
                break;
            }
    // Publish the descriptor completion at the earliest next core edge.
    // Error completions carry no signal event (no success consumer wake).
    auto *event = new CompletionEvent(
        this, command,
        status == DmaStatus::OK ? completion_event : RuntimeObjectKey(),
        status != DmaStatus::OK);
    schedule(event, clockEdge() + 1);
}

void MeshDummyCore::latchInstanceError(Tick tick)
{
    if (instanceErrored())
        return;
    // Instance-global error latch (spec 5.6): the first error fans out to
    // every participating core; each cancels its undrained work.
    if (dispatcher)
        dispatcher->latchInstanceError(this, tick);
    else {
        instance_state =
            mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINING;
        error_latch_tick = tick;
    }
}

void MeshDummyCore::onInstanceError(Tick tick)
{
    switch (instance_state) {
      case mesh_abi::MeshCoreInstanceState::PROGRAM_READY:
      case mesh_abi::MeshCoreInstanceState::REQUEST_ARMED:
      case mesh_abi::MeshCoreInstanceState::INSTANCE_DONE:
      case mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINED:
        return;
      default:
        break;
    }
    if (!instanceErrored()) {
        instance_state =
            mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINING;
        error_latch_tick = tick;
    }
    cancelRemainingCommands();
    for (auto &kv : overlay_executors)
        kv.second->abort();
    if (weight_cache != nullptr) {
        weight_cache->noteInstanceFault(cache_batch_id, curTick());
        weight_cache->noteFailureFanoutDone(cache_batch_id);
    }
    if (dispatcher)
        dispatcher->cancelFencesOf(core_id_value);
}

void MeshDummyCore::retryCacheReservations()
{
    if (dispatcher != nullptr)
        dispatcher->retryCacheReservations();
}

void MeshDummyCore::retryQueuedCacheFills()
{
    if (queued_cache_fills.empty() || weight_cache == nullptr)
        return;
    for (auto it = queued_cache_fills.begin();
         it != queued_cache_fills.end();) {
        QueuedCacheFill &queued = it->second;
        if (!submitOverlayDma(queued.descriptor, queued.descriptor_key,
                              queued.descriptor_key, curTick())) {
            ++it;
            continue;
        }
        weight_cache->advanceEngineEdge();
        weight_cache->markIssued(queued.fill);
        weight_cache->markInFlight(queued.fill);
        pending_cache_fills[queued.fill.fill_incarnation] = queued.pending;
        cache_fills_issued++;
        cache_fills_retried++;
        it = queued_cache_fills.erase(it);
    }
}

void MeshDummyCore::noteWeightConsumed(uint32_t layer_id, uint32_t tag_index)
{
    if (weight_cache == nullptr)
        return;
    weight_cache->noteConsumerDrained(cache_batch_id, layer_id, tag_index);
}

void MeshDummyCore::cancelRemainingCommands()
{
    // Cancel every command that can no longer run: the not-yet-issued tail
    // of each stream plus waiters whose producer chain is broken by the
    // error.  Already-issued work keeps draining physically.
    for (auto &kv : cursors) {
        StreamCursor &cursor = kv.second;
        if (cursor.repeat_gate_command.ordinal != 0) {
            // The REPEAT itself takes the error terminal (spec 4.1): the
            // gate retires cancelled, the post-REPEAT cursor closes.
            commandsCancelled++;
            cancelled_command_ids.push_back(cursor.repeat_gate_command);
            instance_cancelled_ids.push_back(cursor.repeat_gate_command);
            live_commands--;
            for (const auto &command : program->commands)
                if (commandKey(command.command_id) ==
                    cursor.repeat_gate_command) {
                    auto stream_it = live_per_stream.find(command.stream_id);
                    if (stream_it != live_per_stream.end() && stream_it->second)
                        stream_it->second--;
                    break;
                }
            cursor.repeat_gate_command = RuntimeObjectKey();
            cursor.replay.active = false;
            if (cursor.next_command < cursor.post_repeat_next)
                cursor.next_command = cursor.post_repeat_next;
        }
        for (uint32_t index = cursor.next_command; index < cursor.end_command;
             index++) {
            const DecodedCommand &command = program->commands[index];
            commandsCancelled++;
            cancelled_command_ids.push_back(commandKey(command.command_id));
            instance_cancelled_ids.push_back(commandKey(command.command_id));
        }
        cursor.next_command = cursor.end_command;
    }
    for (auto it = recv_waiters.begin(); it != recv_waiters.end();) {
        commandsCancelled++;
        cancelled_command_ids.push_back(it->second);
        instance_cancelled_ids.push_back(it->second);
        live_commands--;
        const DecodedCommand *command = nullptr;
        for (const auto &candidate : program->commands)
            if (candidate.command_id == it->second.ordinal)
                command = &candidate;
        if (command && live_per_stream.count(command->stream_id))
            live_per_stream[command->stream_id]--;
        it = recv_waiters.erase(it);
    }
    for (auto it = fence_waiters.begin(); it != fence_waiters.end();) {
        commandsCancelled++;
        cancelled_command_ids.push_back(it->first);
        instance_cancelled_ids.push_back(it->first);
        live_commands--;
        const DecodedCommand *command = nullptr;
        for (const auto &candidate : program->commands)
            if (candidate.command_id == it->first.ordinal)
                command = &candidate;
        if (command && live_per_stream.count(command->stream_id))
            live_per_stream[command->stream_id]--;
        it = fence_waiters.erase(it);
    }
}

void MeshDummyCore::onTransferCommitted(RuntimeObjectKey transfer)
{
    committed_transfers[transfer] = true;
    auto it = recv_waiters.find(transfer);
    if (it == recv_waiters.end())
        return; // early commit remembered; later RECV_WAIT completes instantly
    const RuntimeObjectKey command_key = it->second;
    recv_waiters.erase(it);
    for (const auto &candidate : program->commands) {
        if (candidate.command_id != command_key.ordinal)
            continue;
        auto *event = new CompletionEvent(this, command_key,
                                          eventKey(candidate.signal_event));
        schedule(event, clockEdge() + 1);
        return;
    }
}

void MeshDummyCore::notifyPeerCommit(uint16_t peer_core,
                                     RuntimeObjectKey transfer)
{
    // The receiver completes its RECV_WAIT when the P2P bytes have committed
    // to its SRAM tile.  The transport routes this through the loader's core
    // registry indirectly; here the dma engine forwards to the peer core.
    if (peer_core == core_id_value)
        return;
    // Handled by dispatcher wiring: see MeshDispatcher::notifyPeerCommit.
    if (dispatcher)
        dispatcher->routePeerCommit(peer_core, transfer.ordinal);
}

void MeshDummyCore::completeCommand(RuntimeObjectKey command_key,
                                    RuntimeObjectKey signal_event,
                                    Tick done_tick, bool error_terminal)
{
    if (error_terminal) {
        commandsErrored++;
        errored_command_ids.push_back(command_key);
        instance_errored_ids.push_back(command_key);
    }
    bool isCompute = false;
    for (const auto &command : program->commands) {
        if (command.command_id != command_key.ordinal)
            continue;
        isCompute = command.opcode == mesh_abi::kOpcodeGEMM ||
                    command.opcode == mesh_abi::kOpcodeBMM ||
                    command.opcode == mesh_abi::kOpcodeELEMENTWISE ||
                    command.opcode == mesh_abi::kOpcodeSOFTMAX ||
                    command.opcode == mesh_abi::kOpcodeNORM ||
                    command.opcode == mesh_abi::kOpcodeLOCAL_REDUCE;
        switch (command.opcode) {
        case mesh_abi::kOpcodeGEMM:
        case mesh_abi::kOpcodeBMM:
            tensor_queue_used--;
            break;
        case mesh_abi::kOpcodeELEMENTWISE:
        case mesh_abi::kOpcodeSOFTMAX:
        case mesh_abi::kOpcodeNORM:
            vector_queue_used--;
            break;
        case mesh_abi::kOpcodeLOCAL_REDUCE:
            reduce_queue_used--;
            break;
        default:
            if (command.opcode == mesh_abi::kOpcodeDMA_LOAD ||
                command.opcode == mesh_abi::kOpcodeDMA_STORE ||
                command.opcode == mesh_abi::kOpcodeDMA_P2P_PUSH ||
                command.opcode == mesh_abi::kOpcodeDMA_PREFETCH ||
                command.opcode == mesh_abi::kOpcodeDMA_FILL)
                break;
            break;
        }
        if (isCompute) {
            markOperandValid(command);
            // Timing-only compute commits through the shared path: ordered
            // operand spans -> semantic digest -> FUNCTIONAL_BYTES result.
            // The designated result operand joins the digest only for
            // accumulator ops (LOCAL_REDUCE reads dst_old).
            const DecodedAttr *attr = attrOf(command);
            auto view_of = [&](const DecodedOperand &operand,
                               uint64_t &offset, uint64_t &span) -> bool {
                const DecodedAllocation *allocation = nullptr;
                for (const auto &candidate : program->allocations)
                    if (candidate.allocation_id == operand.allocation_id)
                        allocation = &candidate;
                if (!allocation)
                    return false;
                if (operand.shard_id) {
                    for (const auto &shard : program->shards) {
                        if (shard.shard_id != operand.shard_id)
                            continue;
                        offset = allocation->offset_bytes +
                                 shard.allocation_offset;
                        span = shard.span_bytes;
                        return shard.allocation_offset <=
                                   allocation->size_bytes &&
                               span <= allocation->size_bytes -
                                           shard.allocation_offset;
                    }
                }
                offset = allocation->offset_bytes;
                span = allocation->size_bytes;
                return true;
            };
            ComputeCommitRequest request;
            request.command = commandKey(command.command_id);
            request.opcode = command.opcode;
            if (attr != nullptr) {
                request.attributes = attr->payload.data();
                request.attribute_bytes = attr->payload.size();
            }
            const bool accumulates =
                command.opcode == mesh_abi::kOpcodeLOCAL_REDUCE;
            const uint16_t input_count =
                command.operand_count - (accumulates ? 0 : 1);
            for (uint16_t operand_i = 0; operand_i < input_count; operand_i++) {
                const DecodedOperand &input =
                    program->operands[command.operand_begin + operand_i];
                uint64_t offset = 0, span = 0;
                fatal_if(!view_of(input, offset, span),
                         "compute digest view unresolved (command %u operand %u)",
                         command.command_id, operand_i);
                request.inputs.push_back(ComputeSpan{offset, span});
            }
            if (command.operand_count > 0) {
                const DecodedOperand &dst = program->operands[
                    command.operand_begin + command.operand_count - 1];
                uint64_t offset = 0, span = 0;
                fatal_if(!view_of(dst, offset, span),
                         "compute result view unresolved (command %u)",
                         command.command_id);
                request.results.push_back(ComputeSpan{offset, span});
                request.result_allocation_id = dst.allocation_id;
                request.valid_allocations.push_back(dst.allocation_id);
            }
            ComputeCommitter(&sram, &computeDigests).commit(request);
        }
        if (command.opcode == mesh_abi::kOpcodeRECV_WAIT)
            markOperandValid(command);
        break;
    }
    commandsCompleted++;
    completed_command_ids.push_back(command_key);
    instance_completed_ids.push_back(command_key);
    command_done_ticks[command_key] = done_tick;
    if (signal_event.ordinal != 0) {
        uint32_t generation = 0;
        for (const auto &command : program->commands)
            if (command.command_id == command_key.ordinal) {
                auto cursor = cursors.find(command.stream_id);
                if (cursor != cursors.end())
                    generation = cursor->second.replay.generation;
                break;
            }
        publishEvent(signal_event, command_key,
                     RepeatGeneration(generation));
    }
    live_commands--;
    auto stream_it = std::find_if(
        program->commands.begin(), program->commands.end(),
        [&](const DecodedCommand &c) {
            return c.command_id == command_key.ordinal;
        });
    if (stream_it != program->commands.end()) {
        uint16_t stream_id = stream_it->stream_id;
        if (live_per_stream.count(stream_id))
            live_per_stream[stream_id]--;
        auto fence_it = fence_waiters.find(command_key);
        if (fence_it != fence_waiters.end())
            fence_waiters.erase(fence_it); // fences never arrive here (dma-driven)
    }
    // Fence release: any fence whose captured tag set has fully retired
    // fires; post-fence submissions never block it.
    for (auto it = fence_waiters.begin(); it != fence_waiters.end();) {
        bool all_retired = true;
        for (uint64_t tag : it->second)
            if (live_dma_tags.count(tag)) {
                all_retired = false;
                break;
            }
        if (all_retired) {
            const RuntimeObjectKey fence_command = it->first;
            it = fence_waiters.erase(it);
            commandsCompleted++;
            completed_command_ids.push_back(fence_command);
            instance_completed_ids.push_back(fence_command);
            command_done_ticks[fence_command] = done_tick;
            live_commands--;
            for (const auto &command : program->commands)
                if (command.command_id == fence_command.ordinal) {
                    live_per_stream[command.stream_id]--;
                    if (command.signal_event)
                        publishEvent(eventKey(command.signal_event),
                                     commandKey(command.command_id),
                                     RepeatGeneration());
                    break;
                }
        } else {
            ++it;
        }
    }
    scheduleTick();
    finishIfHalted();
}

void MeshDummyCore::publishEvent(RuntimeObjectKey event,
                                 RuntimeObjectKey participant,
                                 RepeatGeneration generation)
{
    for (const auto &program_event : program->events) {
        if (program_event.event_id != event.ordinal)
            continue;
        if (program_event.kind == mesh_abi::kEventKindBARRIER) {
            if (!scoreboard->arrive(event, generation, participant,
                                    program_event.expected_arrivals))
                return;
        }
        break;
    }
    // Signals become visible to consumers only after the configured event
    // visibility delay (spec 5.8: earliest next core edge after the
    // visibility window).
    auto pending = pending_visibility.find(event);
    if (pending != pending_visibility.end()) {
        if (pending->second->scheduled())
            deschedule(pending->second);
        pending_visibility.erase(pending);
    }
    auto *visibility = new VisibilityEvent(this, event);
    pending_visibility[event] = visibility;
    schedule(visibility, clockEdge(event_visibility) + 1);
}

void MeshDummyCore::onEventVisible(RuntimeObjectKey event)
{
    pending_visibility.erase(event);
    scoreboard->set_visible(event);
    eventsPublished++;
    scheduleTick();
}

void MeshDummyCore::finishIfHalted()
{
    if (instanceErrored() && !halted() && live_commands == 0 &&
        recv_waiters.empty() && fence_waiters.empty() &&
        pending_visibility.empty()) {
        // The instance reaches INSTANCE_OWNED_WORK_DRAINED first; the tokens
        // it owns are released exactly once at the following cache edge, and
        // only a zero instance-owned token count may latch ERROR_DRAINED.
        if (!owned_work_drain_pending) {
            owned_work_drain_pending = true;
            instance_state =
                mesh_abi::MeshCoreInstanceState::INSTANCE_OWNED_WORK_DRAINED;
            work_drained_tick = curTick();
            DPRINTF(AiMesh, "core %u error work drained at %llu\n",
                    core_id_value, (unsigned long long)work_drained_tick);
            scheduleTick();
        }
        return;
    }
    // HALT waits for the publication drain: a scheduled-but-invisible
    // signal keeps the instance alive (spec: HALT waits for event
    // publication drain).
    if (instance_state == mesh_abi::MeshCoreInstanceState::REQUEST_DRAINING &&
        live_commands == 0 && pending_visibility.empty() &&
        !overlayPending()) {
        instance_state = mesh_abi::MeshCoreInstanceState::INSTANCE_DONE;
        DPRINTF(AiMesh, "core %u halted with %llu commands completed\n", core_id_value,
                (unsigned long long)commandsCompleted.value());
        if (dispatcher)
            dispatcher->notifyCoreHalted(core_id_value);
    }
}

bool MeshDummyCore::functionalSramRead(uint64_t offset, uint64_t size, uint8_t *out)
{
    return sram.read(offset, size, out);
}

bool MeshDummyCore::functionalSramWrite(uint64_t offset, uint64_t size, const uint8_t *in)
{
    return sram.write(offset, size, in);
}

} // namespace ai_mesh
} // namespace gem5

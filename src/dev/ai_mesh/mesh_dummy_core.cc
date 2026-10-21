#include <algorithm>
#include <array>
#include <cstring>
#include <sstream>
#include <type_traits>
#include <variant>
#include "dev/ai_mesh/dma_kind.hh"

#include "dev/ai_mesh/mesh_dummy_core.hh"

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/command_rom.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_compute_timing.hh"
#include "dev/ai_mesh/mesh_clock.hh"
#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"
#include "dev/ai_mesh/mesh_program_invocation.hh"
#include "dev/ai_mesh/mesh_compute_projection.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_ir_dtype.hh"
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
      tensor_setup(p.tensor_setup_cycles),
      tensor_flush(p.tensor_flush_cycles),
      tensor_macs_per_cycle_value(p.tensor_macs_per_cycle),
      tensor_macs_by_dtype_value(p.tensor_macs_by_dtype.begin(), p.tensor_macs_by_dtype.end()),
      vector_elements_per_cycle_value(p.vector_elements_per_cycle),
      vector_elements_by_dtype_value(p.vector_elements_by_dtype.begin(), p.vector_elements_by_dtype.end()),
      reduce_setup(p.reduce_setup_cycles),
      reduce_flush(p.reduce_flush_cycles),
      reduce_ops_per_cycle_value(p.reduce_ops_per_cycle),
      reduce_ops_by_dtype_value(p.reduce_ops_by_dtype.begin(), p.reduce_ops_by_dtype.end()),
      dma(p.dma),
      bank_queue_depth_value(p.sram_bank_queue_depth),
      compute_bytes_limit(p.compute_bytes_limit),
      sram(p.sram_bytes, p.sram_banks, p.sram_alignment, p.sram_line_bytes,
           p.sram_read_bytes_per_cycle, p.sram_write_bytes_per_cycle,
           p.sram_read_ports, p.sram_write_ports, clockPeriod(),
           p.sram_bank_queue_depth),
      tick_event(this)
{
    engine_slots[EngineKind::Tensor].depth = p.tensor_queue_depth;
    engine_slots[EngineKind::Vector].depth = p.vector_queue_depth;
    engine_slots[EngineKind::Reduce].depth = p.reduce_queue_depth;
}

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
    sramReadBytes.name(name() + ".sram_read_bytes").desc("SRAM bank-served read bytes")
        .functor([this]() { return sramTrafficBytes(false); });
    sramWriteBytes.name(name() + ".sram_write_bytes").desc("SRAM bank-served write bytes")
        .functor([this]() { return sramTrafficBytes(true); });
    poisonReadFaults.name(name() + ".poison_read_faults").desc("Poison reads caught");
}

void MeshDummyCore::startup()
{
    fatal_if(reference_compute_forbidden,
             "MeshDummyCore reference_compute=true is forbidden: numeric compute is "
             "not modeled (spec 5.7)");
}

void MeshDummyCore::installAdmission(
    std::shared_ptr<const MeshProgramAdmission> value)
{
    fatal_if(!value || admission || instanceActive(),
             "core %u requires one initial program admission", core_id_value);
    admission = std::move(value);
    program = admission->programPointer();
    weight_tag_sites = weightTagSitesOf(*program, core_id_value);
}

void MeshDummyCore::installInvocation(
    std::shared_ptr<const MeshProgramInvocation> value)
{
    fatal_if(!value || &value->admission() != admission.get() ||
                 instanceActive() || !quiescent(),
             "core %u cannot install this invocation at an active boundary", core_id_value);
    const auto *rom = value->rom(core_id_value);
    fatal_if(!rom, "core %u has no admitted invocation ROM", core_id_value);
    sram.allocations.clear();
    for (const auto &allocation : program->transport.allocations) {
        const auto *owner = admission->context().owner("allocations", allocation.allocation_id);
        if (allocation.owner_core != core_id_value || !owner ||
            owner->variant_id != value->variantId())
            continue;
        fatal_if(!sram.registerAllocation(allocation.allocation_id,
                     allocation.offset_bytes, allocation.size_bytes),
                 "core %u allocation %u rejected by SRAM metadata",
                 core_id_value, allocation.allocation_id);
    }
    invocation = std::move(value);
    command_rom = rom;
}

bool MeshDummyCore::readsResultOperand(const DecodedCommand &command) const
{
    if (command.operand_count == 0)
        return false;
    const auto *projection = admission->projection().command(command.command_id);
    fatal_if(!projection, "compute command has no admitted projection");
    const auto &result = program->transport.command_operands[
        command.operand_begin + command.operand_count - 1];
    const auto count = admission->geometry().accessCount(
        projection->operationId(), GeometryAccessRole::Read);
    for (size_t index = 0; index < count; ++index) {
        const auto *access = admission->geometry().access(
            projection->operationId(), GeometryAccessRole::Read, index);
        const auto *allocation = admission->backing().local(access->object().object_id);
        fatal_if(!allocation, "compute read has no admitted local allocation");
        if (allocation->allocation_id == result.allocation_id)
            return true;
    }
    return false;
}

bool MeshDummyCore::checkOperandValidity(const DecodedCommand &command)
{
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->transport.command_operands[command.operand_begin + i];
        auto it = sram.allocations.find(operand.allocation_id);
        if (it == sram.allocations.end())
            fatal("E_ABI_BOUNDS: compute command %u references unknown "
                  "allocation %u",
                  command.command_id, operand.allocation_id);
        const bool is_result = i + 1 == command.operand_count &&
                               !readsResultOperand(command);
        if (is_result)
            continue; // this command establishes the result's validity
        if (dispatcher && dispatcher->residencyFault() == "invalidate_compute_input")
            it->second.valid = false;
        if (!it->second.valid) {
            raiseInvalidResidency(command.command_id, operand.allocation_id, i, false);
            return false;
        }
    }
    return true;
}

uint32_t MeshDummyCore::errorGeneration(const RuntimeObjectKey &key) const
{
    if (key.domain != mesh_abi::MeshObjectDomain::STATIC)
        return 0;
    for (size_t index = 0; index < program->transport.commands.size(); ++index) {
        const auto &command = program->transport.commands[index];
        if (command.command_id != key.ordinal)
            continue;
        const auto cursor = cursors.find(command.stream_id);
        if (cursor != cursors.end() && cursor->second.repeat_gate_command.ordinal &&
                index >= cursor->second.replay.subrange_begin && index < cursor->second.replay.subrange_end)
            return cursor->second.replay.generation - 1;
        break;
    }
    return 0;
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
    for (const auto &shard : program->transport.shards) {
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
    // A legally empty tile (the semantic op has no accesses) has no operand
    // phase at all; the engine timer still runs its setup/flush.
    if (command.operand_count == 0)
        return 0;
    uint64_t worst_stall = 0;
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->transport.command_operands[command.operand_begin + i];
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
    for (const auto &command : program->transport.commands) {
        if (command.command_id != key.ordinal || command.operand_count == 0)
            continue;
        const DecodedOperand &dst =
            program->transport.command_operands[command.operand_begin + command.operand_count - 1];
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
        core->observations.recordEngineEnd(
            CommandGeneration{command.ordinal, core->issuedGeneration(command.ordinal)}, curTick());
        const Tick ready = core->serviceResultWrite(command);
        if (ready > curTick()) {
            core->schedule(this, ready);
            return;
        }
    }
    core->completeCommand(command, signal_event, curTick(), error_terminal, suppressed_terminal);
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
    const bool local_write = dmaKindIsRead(descriptor.kind) ||
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
    for (const auto &command : program->transport.commands) {
        if (command.command_id != key.ordinal || command.operand_count == 0)
            continue;
        const DecodedOperand &source =
            program->transport.command_operands[command.operand_begin];
        auto it = sram.allocations.find(source.allocation_id);
        if (it == sram.allocations.end())
            fatal("E_ABI_BOUNDS: DMA command %u references unknown allocation %u",
                  key.ordinal, source.allocation_id);
        if (!it->second.valid)
            raiseInvalidResidency(key.ordinal, source.allocation_id, 0, true);
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
    const DecodedOperand &dst = program->transport.command_operands[command.operand_begin + index];
    auto it = sram.allocations.find(dst.allocation_id);
    if (it != sram.allocations.end())
        it->second.valid = true;
}

MeshDummyCore::InstanceLedger MeshDummyCore::takeInstanceLedger()
{
    observations.recordResidencyEnd(residentAllocations());
    const bool unstarted = instance_state == mesh_abi::MeshCoreInstanceState::REQUEST_ARMED;
    fatal_if(unstarted && (!instance_terminals.empty() || !command_generations.empty() ||
                          !observations.view().commands.empty()),
             "core %u unstarted request contains dispatched commands", core_id_value);
    const std::string mismatch = unstarted ? std::string{} : ledgerMismatch();
    fatal_if(!mismatch.empty(),
             "core %u terminal ledger does not partition the dispatched "
             "command/generation set: %s",
             core_id_value, mismatch);
    fatal_if(live_commands != 0 || liveDmaCommandCount() != 0 ||
                 !live_dma_tags.empty() || !allocation_pins.empty(),
             "core %u finishes an instance with live owned resources: live=%u "
             "live_dma=%zu tags=%zu pins=%zu",
             core_id_value, live_commands, liveDmaCommandCount(),
             live_dma_tags.size(), allocation_pins.size());
    for (MeshDummyCore::EngineKind kind :
         {EngineKind::Tensor, EngineKind::Vector, EngineKind::Reduce})
        fatal_if(engineOccupancy(kind) != 0,
                 "core %u finishes an instance still owning %s engine slots",
                 core_id_value, engineName(kind));
    InstanceLedger ledger;
    for (const auto &terminal : instance_terminals) {
        const auto key = commandKey(terminal.command_id);
        if (terminal.state == TerminalState::Completed) {
            if (std::find(instance_suppressed_ids.begin(), instance_suppressed_ids.end(), key) == instance_suppressed_ids.end())
                ledger.completed.push_back(key);
        } else if (terminal.state == TerminalState::Errored)
            ledger.errored.push_back(key);
        else
            ledger.cancelled.push_back(key);
    }
    ledger.suppressed = std::move(instance_suppressed_ids);
    ledger.terminals = std::move(instance_terminals);
    ledger.resources.live_commands = live_commands;
    ledger.resources.live_dma_commands = liveDmaCommandCount();
    ledger.resources.live_dma_tags = live_dma_tags.size();
    ledger.resources.allocation_pins = allocation_pins.size();
    for (EngineKind kind :
         {EngineKind::Tensor, EngineKind::Vector, EngineKind::Reduce})
        ledger.resources.engine_occupancy.push_back({kind, engineOccupancy(kind)});
    ledger.observations = observations.takeFrame();
    return ledger;
}

std::vector<MeshDummyCore::CommandGeneration> MeshDummyCore::dispatchPlan() const
{
    std::vector<CommandGeneration> plan;
    if (command_rom == nullptr || program == nullptr)
        return plan;
    for (uint16_t stream_id : command_rom->streams()) {
        const std::vector<uint32_t> *indices =
            command_rom->commandIndices(stream_id);
        if (indices == nullptr)
            continue;
        for (size_t ordinal = 0; ordinal < indices->size(); ++ordinal) {
            const uint32_t index = (*indices)[ordinal];
            const DecodedCommand &command = program->transport.commands[index];
            plan.push_back(CommandGeneration{command.command_id, 0});
            if (command.opcode != mesh_abi::kOpcodeREPEAT)
                continue;
            // A REPEAT replays the window that precedes it: generation 0 is
            // the original dispatch and passes 1..repeat_count-1 are the
            // replays.  onGenerationDrained() advances the replay cursor
            // before the window is walked, so pass k is labelled k+1; the
            // label is the generation instance the runtime publishes.
            const DecodedAttr *attr = attrOf(command);
            fatal_if(attr == nullptr || attr->kind != mesh_abi::kAttrKindREPEAT_V1,
                     "REPEAT command %u missing REPEAT_V1 attr",
                     command.command_id);
            const auto &repeat = *attr->as<mesh_abi::RepeatV1>();
            for (uint32_t pass = 1; pass < repeat.repeat_count; pass++)
                for (uint32_t offset = repeat.subrange_command_count; offset > 0;
                     offset--)
                    plan.push_back(CommandGeneration{
                        program->transport.commands[(*indices)[ordinal - offset]].command_id,
                        pass + 1});
        }
    }
    return plan;
}

std::string MeshDummyCore::ledgerMismatch() const
{
    std::map<std::pair<uint32_t, uint32_t>, uint32_t> seen;
    for (const TerminalRecord &record : instance_terminals)
        seen[{record.command_id, record.generation}]++;
    const std::vector<CommandGeneration> plan = dispatchPlan();
    std::set<std::pair<uint32_t, uint32_t>> planned;
    for (const CommandGeneration &entry : plan)
        planned.emplace(entry.command_id, entry.generation);
    std::ostringstream out;
    for (const auto &entry : seen)
        if (entry.second > 1)
            out << "duplicate=(" << entry.first.first << ","
                << entry.first.second << ")x" << entry.second << " ";
    for (const auto &entry : planned)
        if (seen.count(entry) == 0)
            out << "missing=(" << entry.first << "," << entry.second << ") ";
    for (const auto &entry : seen)
        if (planned.count(entry.first) == 0)
            out << "extra=(" << entry.first.first << "," << entry.first.second
                << ") ";
    if (!out.str().empty()) {
        out << "plan=[";
        for (const CommandGeneration &entry : plan)
            out << "(" << entry.command_id << "," << entry.generation << ")";
        out << "] terminals=[";
        for (const TerminalRecord &record : instance_terminals)
            out << "(" << record.command_id << "," << record.generation << ","
                << int(record.state) << ")";
        out << "] generations=[";
        for (const auto &entry : command_generations)
            out << "(" << entry.first << "," << entry.second << ")";
        out << "]";
    }
    return out.str();
}

void MeshDummyCore::armRequest(InstanceGeneration instance,
                              const ProfileExecutionView &view,
                              std::shared_ptr<const ServingFillInputs> fill_inputs)
{
    fatal_if(!program || !invocation || &invocation->executionView() != &view,
             "core %u armed without its prepared invocation", core_id_value);
    fatal_if(instanceActive(), "core %u already running an instance", core_id_value);
    instance_generation = instance;
    cache_batch_id = instance.value();
    runtime_fill_inputs = std::move(fill_inputs);
    instance_state = mesh_abi::MeshCoreInstanceState::REQUEST_ARMED;
    observations.beginFrame(instance.value(), core_id_value);
    error_latch_tick = 0;
    work_drained_tick = 0;
    live_dma_tags.clear();
    dma_tag_command.clear();
    dma_lifecycle.clear();
    dma_descriptor_tag.clear();
    next_dma_tag = 1;
    allocation_pins.clear();

    instance_suppressed_ids.clear();
    overlay_entry_published.clear();
    overlay_exited.clear();
    overlay_executors.clear();
    overlay_kernels.clear();
    overlay_entry_tick = 0;
    cache_fills_issued = 0;
    cache_fills_completed = 0;
    command_generations.clear();
    for (auto &slot : engine_slots)
        slot.second.owners.clear();
    instance_terminals.clear();
    plan_instances_cancelled = false;
    cancelAllPendingVisibility();
    cursors.clear();
    live_per_stream.clear();
    committed_transfers.clear();
    receive_notifications.clear();
    receive_notification_ticks.clear();
    received_allocations.clear();
    recv_waiters.clear();
    fence_waiters.clear();
    outstanding_axi = 0;
    if (dma)
        dma->beginInstance();
    installInitialResidency();
    observations.recordResidencyBegin(residentAllocations());
    for (uint16_t stream_id : command_rom->streams()) {
        const auto *indices = command_rom->commandIndices(stream_id);
        fatal_if(!indices || indices->empty(),
                 "core %u selected stream %u has no commands", core_id_value, stream_id);
        StreamCursor cursor;
        cursor.commands = *indices;
        cursors[stream_id] = std::move(cursor);
    }
    DPRINTF(AiMesh, "core %u instance %u armed with %zu streams\n",
            core_id_value, instance.value(), cursors.size());
}

MeshDummyCore::InstanceLedger MeshDummyCore::disarmRequest()
{
    fatal_if(instance_state != mesh_abi::MeshCoreInstanceState::REQUEST_ARMED,
             "core %u disarmed without an armed request", core_id_value);
    cancelAllPendingVisibility();
    releaseInstanceInputs();
    cursors.clear();
    auto ledger = takeInstanceLedger();
    instance_state = mesh_abi::MeshCoreInstanceState::PROGRAM_READY;
    DPRINTF(AiMesh, "core %u request disarmed before start\n", core_id_value);
    return ledger;
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
        schedule(&tick_event, nextCoreEdge(*this));
}

uint32_t MeshDummyCore::regionGateRegionId(uint32_t layer_id) const
{
    for (const auto &region : program->transport.moe_dynamic_regions)
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
        const auto &site = weightTagSite(key.weight_tag_index);
        fatal_if(!invocation, "weight fill has no frozen invocation");
        const auto symbol = admission->context().symbolBinding(invocation->variantId(),
            program->transport.strings.at(site.weight_symbol_id - 1));
        const auto *binding = symbol ? invocation->binding().binding(symbol->slot->slot_id) : nullptr;
        fatal_if(!binding || site.weight_region_offset > binding->allocation_size_bytes ||
                 site.weight_bytes > binding->allocation_size_bytes - site.weight_region_offset,
                 "weight fill leaves its invocation source");
        descriptor.src.region_id = binding->region_id;
        descriptor.src.tensor_id = symbol->tensor->tensor_id;
        descriptor.src.offset_bytes = binding->allocation_offset_bytes + site.weight_region_offset;
        descriptor.dst.memory_space = mesh_abi::kMemorySpaceCORE_SRAM;
        descriptor.dst.region_id = sram_region_id;
        descriptor.dst.owner_core = core_id_value;
        descriptor.dst.offset_bytes = weight_cache->slotBase(token.slot_id);
        const uint64_t bytes = site.weight_bytes;
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
    for (const auto &layer : program->transport.moe_layer_specs)
        if (layer.layer_id == layer_id && layer.kernel_spec_index <
                program->transport.moe_kernel_specs.size())
            overlay_kernels[layer_id] =
                &program->transport.moe_kernel_specs[layer.kernel_spec_index];
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

    if (instance_state == mesh_abi::MeshCoreInstanceState::INSTANCE_OWNED_WORK_DRAINED) {
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
            weight_cache->hasPendingSubscribers(cache_batch_id)) {
            progressed = true;
            continue;
        }
        RuntimeObjectKey entry;
        entry.instance = instance_generation;
        entry.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
        entry.regionGroupId = kv.first;
        entry.regionId = regionGateRegionId(kv.first);
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
            if (cursor.done() || region_gate.blockedIndex(cursor.current()))
                break;
            const size_t position = cursor.position;
            const uint32_t index = cursor.current();
            const DecodedCommand &command = program->transport.commands[index];
            const uint32_t generation = generationAt(kv.first, cursor.position);
            if (admitWindowBlocked(cursor, command, generation))
                break; // finite admit window: real decode-stage backpressure
            if (!waitsSatisfied(command))
            {
                observations.recordWaitStall(
                    CommandGeneration{command.command_id, generation},
                    curTick());
                break;
            }
            const IssueOutcome outcome = tryIssue(command, generation);
            if (outcome.dispatched)
                command_generations[command.command_id] = generation;
            if (!outcome.resume)
                break; // backpressure: retry next cycle
            region_gate.noteIssued(command.command_id);
            if (cursor.position == position)
                ++cursor.position;
            if (cursor.replay.active &&
                cursor.position >= cursor.replay.subrange_end) {
                // Generation window fully issued; gate until it drains.
                cursor.replay.active = false;
                scheduleGenerationCheck(kv.first);
            }
            progressed = true;
        }
    }

    if (progressed || live_commands > 0 || overlayPending() || !dmaDrained() ||
        instance_state != mesh_abi::MeshCoreInstanceState::REQUEST_DRAINING)
        scheduleTick();

    finishIfHalted();
}

bool MeshDummyCore::waitsSatisfied(const DecodedCommand &command) const
{
    fatal_if(scoreboard == nullptr, "core %u has no scoreboard", core_id_value);
    for (uint16_t w = 0; w < command.wait_count; w++)
        if (!scoreboard->visible(eventKey(program->transport.command_waits[command.wait_begin + w].event_id)))
            return false;
    return true;
}

const DecodedAttr *MeshDummyCore::attrOf(const DecodedCommand &command) const
{
    if (command.attr_index == 0 || command.attr_index > program->transport.op_attrs.size())
        return nullptr;
    return &program->transport.op_attrs[command.attr_index - 1];
}

bool MeshDummyCore::fenceScopeMatches(const DmaTagInfo &info, uint16_t scope)
{
    using namespace mesh_abi;
    switch (scope) {
    case kFenceScopeDMA_READ:
        return dmaKindIsRead(info.kind);
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
    schedule(event, nextCoreEdge(*this));
}

bool MeshDummyCore::admitWindowBlocked(const StreamCursor &cursor,
                                       const DecodedCommand &command,
                                       uint32_t generation) const
{
    // A command that is already admitted and still submitting its descriptor
    // group consumes no new capacity: it continues under downstream
    // backpressure, and blocking it would deadlock a window of one.
    const DmaCommandState *state = dma_lifecycle.find(command.command_id);
    if (state != nullptr && state->generation == generation &&
        !state->terminal)
        return false;
    // The retained REPEAT gate is control/drain state.  It holds a live slot
    // for the generation ledger, but it must not consume the executable window
    // that its own replay needs.
    const uint32_t occupied =
        liveStreamCommands(command.stream_id) -
        (cursor.repeat_gate_command.ordinal != 0 ? 1 : 0);
    return occupied >= admit_window_value;
}

const char *MeshDummyCore::engineName(EngineKind kind)
{
    switch (kind) {
    case EngineKind::Tensor:
        return "tensor";
    case EngineKind::Vector:
        return "vector";
    case EngineKind::Reduce:
        return "reduce";
    }
    return "unknown";
}

MeshDummyCore::IssueOutcome MeshDummyCore::admitEngine(const DecodedCommand &command,
                                                       EngineKind kind,
                                                       uint32_t generation)
{
    EngineSlotState &slot = engine_slots[kind];
    if (slot.owners.size() + slot.overlay_reserved >= slot.depth) {
        recordEngineBlock(kind, command.command_id, generation);
        return IssueOutcome{};
    }
    if (!checkOperandValidity(command))
        return IssueOutcome{};
    observations.closeEngineEpisode(CommandGeneration{command.command_id, generation});
    slot.owners[command.command_id] = generation;
    issueCompute(command, attrOf(command), kind, generation);
    return IssueOutcome{true, true};
}

void MeshDummyCore::releaseEngineSlot(EngineKind kind, uint32_t command_id,
                                     uint32_t generation)
{
    EngineSlotState &slot = engine_slots[kind];
    fatal_if(slot.owners.erase(command_id) != 1,
             "core %u releases an engine slot it does not own (command %u)",
             core_id_value, command_id);
    observations.closeEngineEpisode(CommandGeneration{command_id, generation});
}

void MeshDummyCore::recordEngineBlock(EngineKind kind, uint32_t command_id,
                                     uint32_t generation)
{
    // One episode per command/generation: consecutive refusals with the same
    // occupancy and holder set extend the interval, any change (including a
    // new holder after a retirement) opens a new episode.
    const EngineSlotState &slot = engine_slots[kind];
    std::vector<CommandGeneration> holders;
    holders.reserve(slot.owners.size());
    for (const auto &owner : slot.owners)
        holders.push_back(CommandGeneration{owner.first, owner.second});
    observations.recordEngineBlock(CommandGeneration{command_id, generation},
                                   kind, holders, curTick(), slot.owners.size() + slot.overlay_reserved,
                                   slot.depth);
}

MeshDummyCore::IssueOutcome MeshDummyCore::tryIssue(const DecodedCommand &command,
                                                   uint32_t generation)
{
    switch (command.opcode) {
    case mesh_abi::kOpcodeDMA_LOAD:
    case mesh_abi::kOpcodeDMA_STORE:
    case mesh_abi::kOpcodeDMA_P2P_PUSH:
    case mesh_abi::kOpcodeDMA_PREFETCH:
    case mesh_abi::kOpcodeDMA_GATHER_LOAD:
    case mesh_abi::kOpcodeDMA_FILL: {
        const bool resume = issueDma(command, generation);
        // An admitted command owns its completion even while its descriptor
        // group is still being submitted under backpressure.
        const bool dispatched =
            dma_lifecycle.admitted(command.command_id);
        return IssueOutcome{dispatched, resume};
    }
    case mesh_abi::kOpcodeGEMM:
    case mesh_abi::kOpcodeBMM:
        return admitEngine(command, EngineKind::Tensor, generation);
    case mesh_abi::kOpcodeELEMENTWISE:
    case mesh_abi::kOpcodeSOFTMAX:
    case mesh_abi::kOpcodeNORM:
        return admitEngine(command, EngineKind::Vector, generation);
    case mesh_abi::kOpcodeLOCAL_REDUCE:
        return admitEngine(command, EngineKind::Reduce, generation);
    default:
        issueControl(command, generation);
        return IssueOutcome{true, true};
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
        return engineOccupancy(EngineKind::Tensor) < engine_slots.at(EngineKind::Tensor).depth;
    case mesh_abi::kOpcodeELEMENTWISE:
    case mesh_abi::kOpcodeSOFTMAX:
    case mesh_abi::kOpcodeNORM:
        return engineOccupancy(EngineKind::Vector) < engine_slots.at(EngineKind::Vector).depth;
    case mesh_abi::kOpcodeLOCAL_REDUCE:
        return engineOccupancy(EngineKind::Reduce) < engine_slots.at(EngineKind::Reduce).depth;
    default:
        return true;
    }
}

void MeshDummyCore::noteComputeAdmitted(uint16_t opcode)
{
    switch (opcode) {
    case mesh_abi::kOpcodeGEMM:
    case mesh_abi::kOpcodeBMM:
        engine_slots[EngineKind::Tensor].overlay_reserved++;
        break;
    case mesh_abi::kOpcodeELEMENTWISE:
    case mesh_abi::kOpcodeSOFTMAX:
    case mesh_abi::kOpcodeNORM:
        engine_slots[EngineKind::Vector].overlay_reserved++;
        break;
    case mesh_abi::kOpcodeLOCAL_REDUCE:
        engine_slots[EngineKind::Reduce].overlay_reserved++;
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
        fatal_if(engine_slots[EngineKind::Tensor].overlay_reserved == 0, "tensor queue underflow");
        engine_slots[EngineKind::Tensor].overlay_reserved--;
        gemmCycles += cycles;
        break;
    case mesh_abi::kOpcodeELEMENTWISE:
    case mesh_abi::kOpcodeSOFTMAX:
    case mesh_abi::kOpcodeNORM:
        fatal_if(engine_slots[EngineKind::Vector].overlay_reserved == 0, "vector queue underflow");
        engine_slots[EngineKind::Vector].overlay_reserved--;
        break;
    case mesh_abi::kOpcodeLOCAL_REDUCE:
        fatal_if(engine_slots[EngineKind::Reduce].overlay_reserved == 0, "reduce queue underflow");
        engine_slots[EngineKind::Reduce].overlay_reserved--;
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
            const auto &gemm = *attr->as<mesh_abi::GemmV1>();
            batch = gemm.batch;
            m = gemm.m;
            n = gemm.n;
            k = gemm.k;
            dtype = gemm.dtype;
        } else {
            const auto &bmm = *attr->as<mesh_abi::BmmV1>();
            batch = bmm.batch;
            m = bmm.m;
            n = bmm.n;
            k = bmm.k;
            dtype = bmm.dtype;
        }
        uint32_t efficiency = 65536;
        if (command.opcode == mesh_abi::kOpcodeGEMM)
            efficiency = attr->as<mesh_abi::GemmV1>()->efficiency_q16;
        else
            efficiency = attr->as<mesh_abi::BmmV1>()->efficiency_q16;
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
        const auto &ew = *attr->as<mesh_abi::ElementwiseV1>();
        const uint64_t lanes =
            throughputFor(ew.dtype, vector_elements_by_dtype_value,
                          vector_elements_per_cycle_value);
        const __int128 ops =
            __int128(ew.element_count) * ew.ops_per_element;
        fatal_if(ops >= (__int128(1) << 63),
                 "E_ABI_OVERFLOW: elementwise workload exceeds the "
                 "schedulable cycle range (command %u)",
                 command.command_id);
        // A legally empty tile does no element work.
        if (ops == 0)
            return 0;
        return uint64_t(Cycles((ops + lanes - 1) / lanes));
    }
    case mesh_abi::kOpcodeSOFTMAX: {
        const auto &softmax = *attr->as<mesh_abi::SoftmaxV1>();
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
        const auto &norm = *attr->as<mesh_abi::NormV1>();
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
        const auto &reduce = *attr->as<mesh_abi::ReduceV1>();
        const uint64_t ops_per_cycle = throughputFor(
            reduce.dtype, reduce_ops_by_dtype_value,
            reduce_ops_per_cycle_value);
        fatal_if(reduce.fan_in < 2,
                 "E_ABI_OVERFLOW: reduce workload exceeds the schedulable "
                 "cycle range (command %u)",
                 command.command_id);
        if (reduce.element_count == 0)
            return 0;
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

void MeshDummyCore::issueCompute(const DecodedCommand &command, const DecodedAttr *attr,
                                 EngineKind kind, uint32_t generation)
{
    commandsIssued++;

    const Tick issue_tick = curTick();
    const CommandGeneration key{command.command_id, generation};
    observations.recordCommandAdmission(key, issue_tick);
    const uint64_t cycles = computeCycles(command, attr);
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
                                      eventKey(command.signal_event), false, true);
    const Tick write_ready_tick = clockEdge(Cycles(cycles)) + read_stall + 1;
    schedule(event, write_ready_tick);
    observations.recordEnginePlan(key, kind, cycles, issue_tick + read_stall,
                                  write_ready_tick);
}

void MeshDummyCore::issueControl(const DecodedCommand &command,
                                 uint32_t generation)
{
    commandsIssued++;

    observations.recordCommandAdmission(
        CommandGeneration{command.command_id, generation}, curTick());

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
        const uint16_t scope = attr->as<mesh_abi::FenceV1>()->fence_scope;
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
            transferKey(attr->as<mesh_abi::RecvWaitV1>()->transfer_id);
        live_commands++;
        live_per_stream[command.stream_id]++;
        auto done = committed_transfers.find(transfer);
        if (done != committed_transfers.end() && done->second) {
            // Transfer already committed: complete at the next edge.
            auto *event = new CompletionEvent(
                this, commandKey(command.command_id),
                eventKey(command.signal_event));
            schedule(event, nextCoreEdge(*this));
            return;
        }
        recv_waiters[transfer] = commandKey(command.command_id);
        return;
    }
    case mesh_abi::kOpcodeHALT:
        instance_state = mesh_abi::MeshCoreInstanceState::REQUEST_DRAINING;
        haltCommands++;
        commandsCompleted++;
        recordTerminal(command.command_id, issuedGeneration(command.command_id),
                       TerminalState::Completed, curTick());
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
    schedule(event, nextCoreEdge(*this));
}

uint32_t MeshDummyCore::generationAt(uint16_t stream_id, size_t ordinal) const
{
    auto it = cursors.find(stream_id);
    if (it == cursors.end())
        return 0;
    const StreamCursor::ReplayCursor &replay = it->second.replay;
    // Only the members of the window the replay cursor currently walks are
    // REPEAT generation instances; everything else is dispatched once.
    if (replay.active && ordinal >= replay.subrange_begin &&
        ordinal < replay.subrange_end)
        return replay.generation;
    return 0;
}

uint32_t MeshDummyCore::issuedGeneration(uint32_t command_id) const
{
    auto issued = command_generations.find(command_id);
    return issued == command_generations.end() ? 0 : issued->second;
}

std::vector<uint32_t> MeshDummyCore::residentAllocations() const
{
    std::vector<uint32_t> ids;
    for (const auto &entry : sram.allocations)
        if (entry.second.valid)
            ids.push_back(uint32_t(entry.first));
    return ids;
}

void MeshDummyCore::installInitialResidency()
{
    for (auto &entry : sram.allocations)
        entry.second.valid = false;
    if (admission == nullptr || command_rom == nullptr)
        return;
    if (dispatcher != nullptr &&
        dispatcher->residencyFault() == "skip_pre_resident")
        return;
    for (uint32_t allocation_id : admission->initialResidentAllocations(
             command_rom->variantId(), core_id_value)) {
        const auto found = sram.allocations.find(allocation_id);
        if (found != sram.allocations.end())
            found->second.valid = true;
    }
}

void MeshDummyCore::raiseInvalidResidency(uint32_t command_id,
                                          uint32_t allocation_id,
                                          uint32_t operand_index, bool dma)
{
    ResidencyFacts facts;
    facts.instance_id = observations.frameActive()
                            ? observations.instanceId()
                            : 0;
    facts.core_id = core_id_value;
    facts.command_id = command_id;
    facts.generation = issuedGeneration(command_id);
    facts.allocation_id = allocation_id;
    facts.operand_index = operand_index;
    facts.dma = dma;
    const std::string report = invalidResidencyReport(facts);
    if (dispatcher != nullptr)
        dispatcher->reportRuntimeDiagnostic(invalidResidencyDiagnostic(facts),
                                           report);
    poisonReadFaults++;
    if (dma || !armed_binding.invocation())
        fatal("%s", report.c_str());
    const auto key = commandKey(command_id);
    latchInstanceError({curTick(), runtimeErrorSource(mesh_abi::kMoeErrorClassSRAM,
        core_id_value, key, errorGeneration(key)), uint32_t(agent_abi::E_DCORE_POISON_READ)});
}

void MeshDummyCore::recordTerminal(uint32_t command_id, uint32_t generation,
                                   TerminalState state, Tick terminal_tick)
{
    fatal_if(std::any_of(instance_terminals.begin(), instance_terminals.end(),
                        [&](const auto &row) { return row.command_id == command_id && row.generation == generation; }),
             "command generation received a second terminal");
    instance_terminals.push_back(TerminalRecord{command_id, generation, state});
    auto &history = state == TerminalState::Completed ? completed_command_ids :
                    state == TerminalState::Errored ? errored_command_ids : cancelled_command_ids;
    history.push_back(commandKey(command_id));
    observations.recordCommandTerminal(CommandGeneration{command_id, generation},
                                       terminal_tick);
}

void MeshDummyCore::resetSubrangeEvents(uint16_t stream_id)
{
    const StreamCursor &cursor = cursors.at(stream_id);
    for (size_t ordinal = cursor.replay.subrange_begin;
         ordinal < cursor.replay.subrange_end; ++ordinal) {
        const auto &member = program->transport.commands.at(cursor.commands.at(ordinal));
        if (member.signal_event)
            cancelPendingVisibility(eventKey(member.signal_event));
        const auto *group = admission->dma().commandDescriptors(member.command_id);
        if (group != nullptr)
            for (uint32_t descriptor_id : *group)
                cancelPendingVisibility(eventKey(descriptorById(descriptor_id)->completion_event));
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
    const auto &repeat = *attr->as<mesh_abi::RepeatV1>();
    const uint32_t count = repeat.subrange_command_count;
    const uint32_t repeat_count = repeat.repeat_count;

    StreamCursor &cursor = cursors[command.stream_id];
    const size_t repeat_ordinal = cursor.position;
    cursor.repeat_gate_command = commandKey(command.command_id);
    cursor.post_repeat_position = repeat_ordinal + 1;
    cursor.replay.repeat_ordinal = repeat_ordinal;
    cursor.replay.subrange_begin = repeat_ordinal - count;
    cursor.replay.subrange_end = repeat_ordinal;
    cursor.replay.total_generations = repeat_count;
    cursor.replay.generation = 1;

    live_commands++;
    live_per_stream[command.stream_id]++;

    if (repeat_count > 1) {

        DPRINTF(AiMesh, "core %u REPEAT %u: generations=%u subrange=[%u,%u)\n",
                core_id_value, command.command_id, repeat_count,
                cursor.replay.subrange_begin, cursor.replay.subrange_end);
        scheduleGenerationCheck(command.stream_id);
        return;
    }

    finalizeRepeat(cursor);
}

void MeshDummyCore::finalizeRepeat(StreamCursor &cursor)
{
    const RuntimeObjectKey repeat_id = cursor.repeat_gate_command;
    fatal_if(repeat_id.ordinal == 0, "REPEAT finalization without a retained gate");
    uint32_t signal = 0;
    for (const auto &command : program->transport.commands)
        if (command.command_id == repeat_id.ordinal) {
            signal = command.signal_event;
            break;
        }
    cursor.repeat_gate_command = RuntimeObjectKey();
    cursor.replay.active = false;
    cursor.position = cursor.post_repeat_position;
    auto *event = new CompletionEvent(this, repeat_id, eventKey(signal));
    schedule(event, nextCoreEdge(*this));
}

void MeshDummyCore::scheduleGenerationCheck(uint16_t stream_id)
{
    auto *event = new GenerationEvent(this, stream_id);
    schedule(event, nextCoreEdge(*this));
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

    if (liveStreamCommands(stream_id) > 1) {
        scheduleGenerationCheck(stream_id);
        return;
    }
    if (cursor.replay.generation < cursor.replay.total_generations) {
        resetSubrangeEvents(stream_id);
        cursor.replay.active = true;
        cursor.position = cursor.replay.subrange_begin;
        DPRINTF(AiMesh, "core %u REPEAT %u: generation %u starts\n", core_id_value,
                cursor.repeat_gate_command.ordinal, cursor.replay.generation);
        cursor.replay.generation++;
        scheduleTick();
        return;
    }

    finalizeRepeat(cursor);
}

void MeshDummyCore::latchInstanceError(const ErrorCandidate &candidate)
{
    if (instanceErrored())
        return;
    // Instance-global error latch (spec 5.6): the first error fans out to
    // every participating core; each cancels its undrained work.
    if (dispatcher)
        dispatcher->stageInstanceFailure(candidate);
    else {
        instance_state =
            mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINING;
        error_latch_tick = candidate.tick;
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
    // Cancellation enumerates the dispatch plan, not the cursor tail: every
    // planned command/generation instance that will never be dispatched takes
    // its cancelled terminal, while work that was physically issued keeps
    // draining through its own completion path (spec 8.8).  The cursor is only
    // closed so the decode stage stops issuing.
    for (auto &kv : cursors) {
        StreamCursor &cursor = kv.second;
        if (cursor.repeat_gate_command.ordinal != 0) {
            // The REPEAT itself takes the error terminal (spec 4.1): the
            // gate retires cancelled, the post-REPEAT cursor closes.
            commandsCancelled++;
            recordTerminal(cursor.repeat_gate_command.ordinal,
                           issuedGeneration(cursor.repeat_gate_command.ordinal),
                           TerminalState::Cancelled, curTick());
            live_commands--;
            for (const auto &command : program->transport.commands)
                if (command.command_id == cursor.repeat_gate_command.ordinal) {
                    auto stream_it = live_per_stream.find(command.stream_id);
                    if (stream_it != live_per_stream.end() && stream_it->second)
                        stream_it->second--;
                    break;
                }
            cursor.repeat_gate_command = RuntimeObjectKey();
            cursor.replay.active = false;
            if (cursor.position < cursor.post_repeat_position)
                cursor.position = cursor.post_repeat_position;
        }
        cursor.position = cursor.commands.size();
    }
    // Admitted commands own their pins, live counts and tags: the admission
    // lifecycle finalizes zero-submitted commands immediately and partially
    // submitted ones only after the issued descriptors retire.
    std::vector<uint32_t> admitted;
    for (const auto &entry : dma_lifecycle.states())
        admitted.push_back(entry.first);
    for (uint32_t command_id : admitted)
        cancelDmaCommand(command_id);
    for (auto it = recv_waiters.begin(); it != recv_waiters.end();) {
        commandsCancelled++;
        recordTerminal(it->second.ordinal, issuedGeneration(it->second.ordinal),
                       TerminalState::Cancelled, curTick());
        live_commands--;
        const DecodedCommand *command = nullptr;
        for (const auto &candidate : program->transport.commands)
            if (candidate.command_id == it->second.ordinal)
                command = &candidate;
        if (command && live_per_stream.count(command->stream_id))
            live_per_stream[command->stream_id]--;
        it = recv_waiters.erase(it);
    }
    for (auto it = fence_waiters.begin(); it != fence_waiters.end();) {
        commandsCancelled++;
        recordTerminal(it->first.ordinal, issuedGeneration(it->first.ordinal),
                       TerminalState::Cancelled, curTick());
        live_commands--;
        const DecodedCommand *command = nullptr;
        for (const auto &candidate : program->transport.commands)
            if (candidate.command_id == it->first.ordinal)
                command = &candidate;
        if (command && live_per_stream.count(command->stream_id))
            live_per_stream[command->stream_id]--;
        it = fence_waiters.erase(it);
    }
    if (plan_instances_cancelled)
        return;
    plan_instances_cancelled = true;
    std::set<std::pair<uint32_t, uint32_t>> terminal_keys;
    for (const TerminalRecord &record : instance_terminals)
        terminal_keys.insert({record.command_id, record.generation});
    for (const CommandGeneration &entry : dispatchPlan()) {
        if (terminal_keys.count({entry.command_id, entry.generation}))
            continue;
        // A dispatched instance is retired by its own completion path,
        // including an admitted DMA command draining its issued descriptors.
        // Every other planned instance is a logical cancellation, including
        // the later generations of a command whose current instance is still
        // in flight.
        auto issued = command_generations.find(entry.command_id);
        if (issued != command_generations.end() &&
            issued->second == entry.generation)
            continue;
        commandsCancelled++;
        recordTerminal(entry.command_id, entry.generation,
                       TerminalState::Cancelled, curTick());
    }
}

void MeshDummyCore::markAllocationValid(uint32_t allocation_id)
{
    auto allocation = sram.allocations.find(allocation_id);
    if (allocation != sram.allocations.end())
        allocation->second.valid = true;
}

void MeshDummyCore::onTransferCommitted(RuntimeObjectKey transfer)
{
    fatal_if(transfer.instance != instance_generation ||
             transfer.domain != mesh_abi::MeshObjectDomain::STATIC || !invocation ||
             !invocation->transferPlan(transfer.ordinal),
             "peer commit leaves its admitted invocation");
    committed_transfers[transfer] = true;
    receive_notifications[transfer.ordinal]++;
    receive_notification_ticks[transfer.ordinal] = curTick();
    for (const auto &descriptor : program->transport.dma_descriptors) {
        if (descriptor.kind != mesh_abi::kDmaKindP2P_PUSH ||
            descriptor.transfer_id != transfer.ordinal ||
            descriptor.dst.owner_core != core_id_value)
            continue;
        const auto storage = admittedDestinationStorage(descriptor.descriptor_id);
        fatal_if(!storage, "peer commit has no admitted local destination");
        markAllocationValid(storage->allocation_id);
        received_allocations[transfer.ordinal] = storage->allocation_id;
    }
    auto waiter = recv_waiters.find(transfer);
    if (waiter == recv_waiters.end())
        return;
    const RuntimeObjectKey key = waiter->second;
    recv_waiters.erase(waiter);
    for (const auto &command : program->transport.commands)
        if (command.command_id == key.ordinal) {
            auto *event = new CompletionEvent(this, key, eventKey(command.signal_event));
            schedule(event, nextCoreEdge(*this));
            return;
        }
    fatal("peer waiter has no admitted command");
}

void MeshDummyCore::notifyPeerCommit(uint16_t peer_core, RuntimeObjectKey transfer)
{
    if (peer_core == core_id_value || dispatcher == nullptr ||
        dispatcher->expectsTransfer(transfer.ordinal))
        return;
    dispatcher->routePeerCommit(peer_core, transfer.ordinal);
}

void MeshDummyCore::completeCommand(RuntimeObjectKey command_key,
                                    RuntimeObjectKey signal_event,
                                    Tick done_tick, bool error_terminal, bool suppressed_terminal)
{
    if (error_terminal) {
        commandsErrored++;
        recordTerminal(command_key.ordinal, issuedGeneration(command_key.ordinal),
                       TerminalState::Errored, done_tick);
    }
    bool isCompute = false;
    for (const auto &command : program->transport.commands) {
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
            releaseEngineSlot(EngineKind::Tensor, command.command_id,
                              issuedGeneration(command.command_id));
            break;
        case mesh_abi::kOpcodeELEMENTWISE:
        case mesh_abi::kOpcodeSOFTMAX:
        case mesh_abi::kOpcodeNORM:
            releaseEngineSlot(EngineKind::Vector, command.command_id,
                              issuedGeneration(command.command_id));
            break;
        case mesh_abi::kOpcodeLOCAL_REDUCE:
            releaseEngineSlot(EngineKind::Reduce, command.command_id,
                              issuedGeneration(command.command_id));
            break;
        default:
            break;
        }
        if (isCompute) {
            ComputeCommitRequest request;
            MeshLoadError error;
            fatal_if(!projectComputeCommit(*admission, command, command_key, request, error),
                     "%s: %s", error.code, error.message);
            const auto result = ComputeCommitter(&sram, &computeDigests, compute_bytes_limit).commit(request);
            std::array<uint32_t, 4> words;
            std::copy(std::begin(result.digest.digest_words), std::end(result.digest.digest_words), words.begin());
            observations.recordComputeOutput(
                CommandGeneration{command.command_id, issuedGeneration(command.command_id)},
                result.digest.allocation_id, result.digest.offset, words,
                result.rows, result.merged_rows);
        }
        if (command.opcode == mesh_abi::kOpcodeRECV_WAIT)
            markOperandValid(command);
        break;
    }
    if (!error_terminal) {
        commandsCompleted++;
        if (suppressed_terminal)
            instance_suppressed_ids.push_back(command_key);
        recordTerminal(command_key.ordinal, issuedGeneration(command_key.ordinal),
                       TerminalState::Completed, done_tick);
    }
    if (!error_terminal && signal_event.ordinal != 0)
        publishEvent(signal_event, command_key,
                     RepeatGeneration(issuedGeneration(command_key.ordinal)));
    live_commands--;
    auto stream_it = std::find_if(
        program->transport.commands.begin(), program->transport.commands.end(),
        [&](const DecodedCommand &c) {
            return c.command_id == command_key.ordinal;
        });
    if (stream_it != program->transport.commands.end()) {
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
            recordTerminal(fence_command.ordinal, issuedGeneration(fence_command.ordinal),
                           TerminalState::Completed, done_tick);
            live_commands--;
            for (const auto &command : program->transport.commands)
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
    for (const auto &program_event : program->transport.events) {
        if (program_event.event_id != event.ordinal)
            continue;
        if (program_event.kind == mesh_abi::kEventKindBARRIER) {
            if (!scoreboard->arrive(event, generation, participant,
                                    program_event.expected_arrivals, curTick()))
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

uint64_t MeshDummyCore::progressSnapshot() const
{
    return commandsIssued.value() + commandsCompleted.value() +
           eventsPublished.value() + requestBegins.value() +
           requestEnds.value();
}

void MeshDummyCore::finishIfHalted()
{
    if (instance_state == mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINING &&
        live_commands == 0 && recv_waiters.empty() && fence_waiters.empty() &&
        pending_visibility.empty() && dmaDrained()) {
        instance_state = mesh_abi::MeshCoreInstanceState::INSTANCE_OWNED_WORK_DRAINED;
        work_drained_tick = curTick();
        DPRINTF(AiMesh, "core %u error work drained at %llu\n",
                core_id_value, (unsigned long long)work_drained_tick);
        scheduleTick();
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

bool MeshDummyCore::functionalSramRead(uint64_t offset, uint64_t size, uint8_t *out) const
{
    return sram.read(offset, size, out);
}

bool MeshDummyCore::functionalSramWrite(uint64_t offset, uint64_t size, const uint8_t *in)
{
    return sram.write(offset, size, in);
}

} // namespace ai_mesh
} // namespace gem5

#include <algorithm>
#include <array>
#include <cstring>
#include <sstream>
#include <type_traits>
#include <variant>

#include "dev/ai_mesh/mesh_dummy_core.hh"

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/command_rom.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_clock.hh"
#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"
#include "dev/ai_mesh/mesh_invocation_binding.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_ir_dtype.hh"
#include "dev/ai_mesh/mesh_program_loader.hh"
#include "params/MeshDummyCore.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

static uint64_t
accessElementByteOffset(const GeometryAccessFact &fact,
                        const std::vector<uint64_t> &coordinates)
{
    uint64_t offset = fact.objectByteOffset();
    for (size_t index = 0; index < coordinates.size(); index++)
        offset += coordinates[index] * fact.objectByteStrides()[index];
    return offset;
}

static void
advanceAccessCoordinates(const GeometryAccessFact &fact,
                         std::vector<uint64_t> &coordinates)
{
    for (size_t index = coordinates.size(); index-- > 0;) {
        if (++coordinates[index] < fact.shape()[index])
            return;
        coordinates[index] = 0;
    }
}

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
    poisonReadFaults.name(name() + ".poison_read_faults").desc("Poison reads caught");
}

void MeshDummyCore::startup()
{
    fatal_if(reference_compute_forbidden,
             "MeshDummyCore reference_compute=true is forbidden: numeric compute is "
             "not modeled (spec 5.7)");
}

void MeshDummyCore::installAdmission(
    const std::shared_ptr<const MeshProgramAdmission> &value,
    const std::shared_ptr<const CommandRom> &rom,
    const std::shared_ptr<const MeshInvocationBinding> &invocation)
{
    admission = value;
    command_rom = rom;
    invocation_binding = invocation;
    program = admission ? admission->programPointer() : nullptr;
    sram.allocations.clear();
    if (!program || !command_rom)
        return;
    for (const auto &allocation : program->transport.allocations) {
        if (allocation.owner_core != core_id_value)
            continue;
        const auto *owner = admission->context().owner(
            "allocations", allocation.allocation_id);
        if (owner == nullptr || owner->variant_id != command_rom->variantId())
            continue;
        TensorSram::AllocationState state;
        state.offset = allocation.offset_bytes;
        state.size = allocation.size_bytes;
        sram.allocations[allocation.allocation_id] = state;
    }
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
        if (!it->second.valid)
            raiseInvalidResidency(command.command_id, operand.allocation_id, i,
                                  false);
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
    fatal_if(command.operand_count == 0,
             "compute command %u has no operands", command.command_id);
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

Tick MeshDummyCore::serviceResultWrite(uint32_t command_id)
{
    // Serial phase 3: the result operand's SRAM write service happens after
    // the engine timer; the command completes only once it is done.
    for (const auto &command : program->transport.commands) {
        if (command.command_id != command_id || command.operand_count == 0)
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
            CommandGeneration{command_id, core->issuedGeneration(command_id)},
            curTick());
        const Tick ready = core->serviceResultWrite(command_id);
        if (ready > curTick()) {
            core->schedule(this, ready);
            return;
        }
    }
    core->completeCommand(command_id, signal_event, curTick(), error_terminal);
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

void MeshDummyCore::checkDmaSourceValidity(uint32_t command_id)
{
    for (const auto &command : program->transport.commands) {
        if (command.command_id != command_id || command.operand_count == 0)
            continue;
        const DecodedOperand &source =
            program->transport.command_operands[command.operand_begin];
        auto it = sram.allocations.find(source.allocation_id);
        if (it == sram.allocations.end())
            fatal("E_ABI_BOUNDS: DMA command %u references unknown allocation %u",
                  command_id, source.allocation_id);
        if (!it->second.valid)
            raiseInvalidResidency(command_id, source.allocation_id, 0, true);
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
    const std::string mismatch = ledgerMismatch();
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
        for (uint32_t index : *indices) {
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
                        program->transport.commands[index - offset].command_id,
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

void MeshDummyCore::dispatchInstance(uint32_t instance_id)
{
    fatal_if(!program, "core %u dispatched without an installed program", core_id_value);
    fatal_if(instance_active, "core %u already running an instance", core_id_value);
    instance_active = true;
    observations.beginFrame(instance_id, core_id_value);
    core_halted = false;
    instance_error = false;
    error_drained = false;
    error_latch_tick = 0;
    work_drained_tick = 0;
    live_dma_tags.clear();
    dma_tag_command.clear();
    dma_lifecycle.clear();
    dma_descriptor_tag.clear();
    next_dma_tag = 1;
    allocation_pins.clear();
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
    fatal_if(!command_rom, "core %u dispatched without a command ROM",
             core_id_value);
    installInitialResidency();
    observations.recordResidencyBegin(residentAllocations());
    for (uint16_t stream_id : command_rom->streams()) {
        const std::vector<uint32_t> *indices =
            command_rom->commandIndices(stream_id);
        fatal_if(indices == nullptr || indices->empty(),
                 "core %u selected stream %u has no commands", core_id_value,
                 stream_id);
        StreamCursor cursor;
        cursor.next_command = indices->front();
        cursor.end_command = indices->back() + 1;
        cursors[stream_id] = cursor;
    }
    DPRINTF(AiMesh, "core %u instance %u dispatched with %zu streams\n", core_id_value,
            instance_id, cursors.size());
    if (cursors.empty()) {
        // A core with no streams does not participate; it halts immediately.
        core_halted = true;
        finishIfHalted();
        return;
    }
    scheduleTick();
}

void MeshDummyCore::scheduleTick()
{
    if (!tick_event.scheduled())
        schedule(&tick_event, nextCoreEdge(*this));
}

void MeshDummyCore::tick()
{
    // Late in-flight ticks after the instance finished are harmless.
    if (!instance_active)
        return;

    if (instance_error && !error_drained)
        cancelRemainingCommands();

    bool progressed = false;
    for (auto &kv : cursors) {
        StreamCursor &cursor = kv.second;
        for (uint32_t issued = 0; issued < decode_width_value; issued++) {
            if (cursor.repeat_gate_command != 0 && !cursor.replay.active)
                break; // REPEAT drain gate: post-REPEAT cursor stays closed
            if (cursor.next_command >= cursor.end_command)
                break;
            const uint32_t index = cursor.next_command;
            const DecodedCommand &command = program->transport.commands[index];
            const uint32_t generation = generationAt(kv.first, index);
            if (admitWindowBlocked(cursor, command, generation))
                break; // finite admit window: real decode-stage backpressure
            if (!waitsSatisfied(command))
                break;
            const IssueOutcome outcome = tryIssue(command, generation);
            if (outcome.dispatched)
                command_generations[command.command_id] = generation;
            if (!outcome.resume)
                break; // backpressure: retry next cycle
            if (cursor.next_command == index)
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

    if (progressed || live_commands > 0 || !core_halted || !dmaDrained())
        scheduleTick();

    finishIfHalted();
}

bool MeshDummyCore::waitsSatisfied(const DecodedCommand &command) const
{
    fatal_if(scoreboard == nullptr, "core %u has no scoreboard", core_id_value);
    for (uint16_t w = 0; w < command.wait_count; w++)
        if (!scoreboard->visible(
                program->transport.command_waits[
                    command.wait_begin + w].event_id))
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

void MeshDummyCore::completeFence(uint32_t command_id, uint32_t signal_event)
{
    auto *event = new CompletionEvent(this, command_id, signal_event);
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
        (cursor.repeat_gate_command != 0 ? 1 : 0);
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
    if (slot.owners.size() >= slot.depth) {
        recordEngineBlock(kind, command.command_id, generation);
        return IssueOutcome{};
    }
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
                                   kind, holders, curTick(), slot.owners.size(),
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
        const __int128 work = __int128(batch) * m * n * k;
        const uint64_t macs = throughputFor(
            dtype, tensor_macs_by_dtype_value, tensor_macs_per_cycle_value);
        fatal_if(macs == 0,
                 "E_CAPABILITY_MISMATCH: GEMM dtype has no tensor "
                 "throughput capability (command %u)",
                 command.command_id);
        const __int128 numerator = work * 65536;
        const __int128 denominator = __int128(macs) * efficiency;
        fatal_if(denominator == 0 || work >= (__int128(1) << 63) ||
                     numerator / denominator >= (__int128(1) << 63),
                 "E_ABI_OVERFLOW: GEMM workload exceeds the schedulable "
                 "cycle range (command %u)",
                 command.command_id);
        const uint64_t engine =
            uint64_t((numerator + denominator - 1) / denominator);
        return uint64_t(tensor_setup + Cycles(engine) + tensor_flush);
    }
    case mesh_abi::kOpcodeELEMENTWISE: {
        const auto &ew = *attr->as<mesh_abi::ElementwiseV1>();
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
        const __int128 ops =
            __int128(reduce.element_count) * (reduce.fan_in - 1);
        fatal_if(reduce.fan_in < 2 || ops <= 0 ||
                     ops >= (__int128(1) << 63),
                 "E_ABI_OVERFLOW: reduce workload exceeds the schedulable "
                 "cycle range (command %u)",
                 command.command_id);
        return uint64_t(reduce_setup +
                        Cycles((ops + ops_per_cycle - 1) / ops_per_cycle) +
                        reduce_flush);
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
    auto *event = new CompletionEvent(this, command.command_id,
                                      command.signal_event, false, true);
    const Tick write_ready_tick = clockEdge(Cycles(cycles)) + read_stall + 1;
    schedule(event, write_ready_tick);
    observations.recordEnginePlan(key, kind, cycles, issue_tick + read_stall,
                                  write_ready_tick);
    DPRINTF(AiMesh, "core %u compute command %u opcode=%u cycles=%llu stall=%llu\n",
            core_id_value,
            command.command_id, command.opcode, (unsigned long long)cycles);
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
            fence_waiters[command.command_id] = waiting;
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
        uint32_t transfer_id =
            attr->as<mesh_abi::RecvWaitV1>()->transfer_id;
        live_commands++;
        live_per_stream[command.stream_id]++;
        auto done = committed_transfers.find(transfer_id);
        if (done != committed_transfers.end() && done->second) {
            // Transfer already committed: complete at the next edge.
            auto *event =
                new CompletionEvent(this, command.command_id, command.signal_event);
            schedule(event, nextCoreEdge(*this));
            return;
        }
        recv_waiters[transfer_id] = command.command_id;
        return;
    }
    case mesh_abi::kOpcodeHALT:
        core_halted = true;
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
    auto *event = new CompletionEvent(this, command.command_id, command.signal_event);
    schedule(event, nextCoreEdge(*this));
}

bool MeshDummyCore::tryPinDmaAllocations(const DecodedCommand &command)
{
    // SRAM allocations touched by an in-flight DMA are pinned: a second DMA
    // on the same allocation must wait (spec 7.1 admit-to-completion pin).
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->transport.command_operands[command.operand_begin + i];
        auto it = sram.allocations.find(operand.allocation_id);
        if (it == sram.allocations.end())
            continue;
        if (allocation_pins[operand.allocation_id] > 0)
            return false;
    }
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->transport.command_operands[command.operand_begin + i];
        if (sram.allocations.count(operand.allocation_id))
            allocation_pins[operand.allocation_id]++;
    }
    return true;
}

void MeshDummyCore::unpinDmaAllocations(const DecodedCommand &command)
{
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->transport.command_operands[command.operand_begin + i];
        auto pin = allocation_pins.find(operand.allocation_id);
        if (pin == allocation_pins.end())
            continue;
        fatal_if(pin->second == 0, "allocation pin underflow");
        if (--pin->second == 0)
            allocation_pins.erase(pin);
    }
}

uint32_t MeshDummyCore::generationAt(uint16_t stream_id, uint32_t index) const
{
    auto it = cursors.find(stream_id);
    if (it == cursors.end())
        return 0;
    const StreamCursor::ReplayCursor &replay = it->second.replay;
    // Only the members of the window the replay cursor currently walks are
    // REPEAT generation instances; everything else is dispatched once.
    if (replay.active && index >= replay.subrange_begin &&
        index < replay.subrange_end)
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
    fatal("%s", report.c_str());
}

void MeshDummyCore::recordTerminal(uint32_t command_id, uint32_t generation,
                                   TerminalState state, Tick terminal_tick)
{
    instance_terminals.push_back(TerminalRecord{command_id, generation, state});
    observations.recordCommandTerminal(CommandGeneration{command_id, generation},
                                       terminal_tick);
}

bool MeshDummyCore::issueDma(const DecodedCommand &command, uint32_t generation)
{
    const DmaCommandState *existing = dma_lifecycle.find(command.command_id);
    if (existing != nullptr && existing->generation != generation) {
        // A REPEAT generation re-admits the same command IDs.  The previous
        // generation is fully drained before onGenerationDrained opens the
        // replay cursor, so no descriptor of this command can still be
        // pending here.
        fatal_if(!existing->pending.empty(),
                 "REPEAT generation %u re-admits DMA command %u with pending "
                 "descriptors",
                 generation, command.command_id);
        dma_lifecycle.evict(command.command_id);
    }
    if (!dma_lifecycle.admitted(command.command_id)) {
        const std::vector<uint32_t> *group =
            admission->dma().commandDescriptors(command.command_id);
        fatal_if(group == nullptr || group->empty(),
                 "DMA command %u has no admitted descriptor group",
                 command.command_id);
        if (!tryPinDmaAllocations(command))
            return false; // pinned by an in-flight DMA: retry next cycle
        const DmaCompletionFact *completion =
            admission->dma().descriptorCompletion(group->front());
        DmaCommandLifecycle::Admission entry;
        entry.command_id = command.command_id;
        entry.generation = generation;
        entry.completion_event =
            completion ? completion->completionEventId() : 0;
        entry.descriptors = *group;
        const P2PTransferFact *transfer = nullptr;
        for (uint32_t descriptor_id : *group) {
            const DecodedDmaDescriptor *descriptor =
                descriptorById(descriptor_id);
            if (descriptor == nullptr ||
                descriptor->kind != mesh_abi::kDmaKindP2P_PUSH)
                continue;
            transfer = admission->dma().p2pTransfer(descriptor->transfer_id);
            if (transfer != nullptr) {
                entry.transfer_id = descriptor->transfer_id;
                break;
            }
        }
        if (transfer != nullptr)
            entry.expected_bytes = transfer->expectedBytes();
        if (transfer != nullptr) {
            const size_t writes = admission->geometry().accessCount(
                command.source_op_id, GeometryAccessRole::Write);
            for (size_t index = 0; index < writes; index++) {
                const GeometryAccessFact *fact = admission->geometry().access(
                    command.source_op_id, GeometryAccessRole::Write, index);
                if (fact == nullptr)
                    continue;
                const mesh_abi::Allocation *destination =
                    admission->backing().local(fact->object().object_id);
                if (destination == nullptr)
                    continue;
                entry.receiver_core = fact->object().owner_core;
                entry.receiver_allocations.push_back(
                    destination->allocation_id);
            }
        }
        dma_lifecycle.admit(entry);
        if (dispatcher != nullptr && entry.transfer_id != 0 &&
            dispatcher->receiverFault() == "receive_premature") {
            for (uint32_t allocation_id : entry.receiver_allocations)
                dispatcher->markReceiverAllocationValid(entry.receiver_core,
                                                        allocation_id);
        }
        commandsIssued++;
        observations.recordCommandAdmission(
            CommandGeneration{command.command_id, generation}, curTick());
        live_commands++;
        live_per_stream[command.stream_id]++;
    }
    if (!dma_lifecycle.submitting(command.command_id, generation))
        return true; // no new submission after failure/cancel; completions finalize
    while (uint32_t descriptor_id =
               dma_lifecycle.nextDescriptor(command.command_id)) {
        const DecodedDmaDescriptor *descriptor = descriptorById(descriptor_id);
        fatal_if(descriptor == nullptr,
                 "admitted descriptor %u of command %u is missing",
                 descriptor_id, command.command_id);
        if (descriptor->kind == mesh_abi::kDmaKindLOCAL_FILL) {
            const DecodedAttr *attr = attrOf(command);
            fatal_if(attr == nullptr || attr->kind != mesh_abi::kAttrKindFILL_V1,
                     "DMA_FILL command %u missing FILL_V1 attr", command.command_id);
            dma->bindFillPattern(
                command.command_id,
                attr->as<mesh_abi::FillV1>()->pattern);
        }
        if (descriptor->useful_bytes > 0 && !dmaSramAdmissible(*descriptor))
            return false; // bank queue full: retry next cycle, progress kept
        if (!dma->submit(*descriptor, curTick()))
            return false; // engine backpressure: retry next cycle, progress kept
        dma_lifecycle.noteSubmitted(command.command_id, descriptor_id);
        if (descriptor->kind == mesh_abi::kDmaKindP2P_PUSH &&
            descriptor->useful_bytes > 0 && dispatcher)
            dispatcher->armTransferExpectation(descriptor->transfer_id);
        outstanding_axi++;
        const uint64_t tag = next_dma_tag++;
        live_dma_tags.insert(tag);
        dma_tag_info[tag] = DmaTagInfo{descriptor->kind,
                                       descriptor->dst.memory_space};
        dma_tag_command[tag] = command.command_id;
        dma_descriptor_tag[descriptor_id] = tag;
    }
    return true;
}

void MeshDummyCore::resetSubrangeEvents(uint16_t stream_id)
{
    const StreamCursor &cursor = cursors[stream_id];
    for (uint32_t index = cursor.replay.subrange_begin;
         index < cursor.replay.subrange_end; index++) {
        const auto &member = program->transport.commands[index];
        if (member.signal_event)
            cancelPendingVisibility(member.signal_event);
        for (const auto &descriptor : program->transport.dma_descriptors)
            if (descriptor.command_id == member.command_id)
                cancelPendingVisibility(descriptor.completion_event);
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

void MeshDummyCore::cancelPendingVisibility(uint32_t event_id)
{
    scoreboard->unpublish(event_id);
    auto pending = pending_visibility.find(event_id);
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
    const uint32_t repeat_index = cursor.next_command; // still at REPEAT
    cursor.repeat_gate_command = command.command_id;
    cursor.post_repeat_next = repeat_index + 1;
    cursor.replay.repeat_command_index = repeat_index;
    cursor.replay.subrange_begin = repeat_index - count;
    cursor.replay.subrange_end = repeat_index;
    cursor.replay.total_generations = repeat_count;
    cursor.replay.generation = 1;

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
    // A count of one replays nothing, so the REPEAT retires through the same
    // finalization owner as the last drained generation: the retained gate
    // must not outlive its own control command.
    finalizeRepeat(cursor);
}

void MeshDummyCore::finalizeRepeat(StreamCursor &cursor)
{
    const uint32_t repeat_id = cursor.repeat_gate_command;
    fatal_if(repeat_id == 0, "REPEAT finalization without a retained gate");
    uint32_t signal = 0;
    for (const auto &command : program->transport.commands)
        if (command.command_id == repeat_id) {
            signal = command.signal_event;
            break;
        }
    cursor.repeat_gate_command = 0;
    cursor.replay.active = false;
    cursor.next_command = cursor.post_repeat_next;
    auto *event = new CompletionEvent(this, repeat_id, signal);
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
    if (cursor.repeat_gate_command == 0)
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
                cursor.repeat_gate_command, cursor.replay.generation);
        cursor.replay.generation++;
        scheduleTick();
        return;
    }
    // Final generation drained; the REPEAT retires through the shared
    // finalization owner.
    finalizeRepeat(cursor);
}

void MeshDummyCore::onDmaCompleted(uint32_t command_id, uint32_t descriptor_id,
                                   uint32_t completion_event, Tick commit_tick,
                                   DmaStatus status)
{
    if (outstanding_axi > 0)
        outstanding_axi--;
    uint16_t dma_kind = 0;
    {
        auto tag_it = dma_descriptor_tag.find(descriptor_id);
        fatal_if(tag_it == dma_descriptor_tag.end(),
                 "DMA completion without a live tag (descriptor %u)",
                 descriptor_id);
        const uint64_t retired_tag = tag_it->second;
        auto info_it = dma_tag_info.find(retired_tag);
        if (info_it != dma_tag_info.end())
            dma_kind = info_it->second.kind;
        live_dma_tags.erase(retired_tag);
        dma_tag_info.erase(retired_tag);
        dma_tag_command.erase(retired_tag);
        dma_descriptor_tag.erase(tag_it);
        if (dispatcher)
            dispatcher->onDmaTagRetired(core_id_value, retired_tag);
    }
    const DmaCommandState *state = dma_lifecycle.find(command_id);
    fatal_if(state == nullptr, "DMA completion for unknown command %u",
             command_id);
    if (state->terminal)
        return; // an earlier descriptor already finalized the command
    const DecodedDmaDescriptor *descriptor = descriptorById(descriptor_id);
    const uint64_t retired_bytes =
        descriptor == nullptr ? 0 : descriptor->useful_bytes;
    const uint16_t peer_core =
        descriptor == nullptr ? 0 : descriptor->dst.owner_core;
    DmaCommandLifecycle::Retirement retired = dma_lifecycle.retire(
        command_id, descriptor_id, retired_bytes, status == DmaStatus::OK,
        peer_core);
    fatal_if(!retired.known, "DMA completion for unknown command %u",
             command_id);
    if (retired.has_transfer)
        observations.recordTransferProgress(retired.progress);
    if (retired.notify)
        notifyPeerCommit(peer_core, retired.transfer_id);
    if (retired.first_failure) {
        InstanceFailureFacts facts;
        facts.core_id = core_id_value;
        facts.command_id = command_id;
        facts.generation = state->generation;
        facts.descriptor_id = descriptor_id;
        facts.dma_kind = dma_kind;
        facts.status = status;
        if (descriptor != nullptr &&
            descriptor->kind == mesh_abi::kDmaKindP2P_PUSH)
            facts.transfer_id = descriptor->transfer_id;
        if (!state->error_published) {
            dma_lifecycle.markErrorPublished(command_id);
            if (!instance_error) {
                if (dispatcher)
                    dispatcher->latchInstanceError(this, commit_tick, facts);
                else {
                    instance_error = true;
                    error_latch_tick = commit_tick;
                }
            }
            auto *event = new CompletionEvent(this, command_id, 0, true);
            schedule(event, nextCoreEdge(*this));
        }
    }
    if (!retired.drained)
        return; // not every issued descriptor has physically retired yet
    finalizeDmaCommand(command_id);
}

void MeshDummyCore::finalizeDmaCommand(uint32_t command_id)
{
    const DmaCommandState *current = dma_lifecycle.find(command_id);
    fatal_if(current == nullptr, "finalize of unadmitted DMA command %u",
             command_id);
    if (current->terminal)
        return;
    const bool failed = current->failed;
    const bool cancelled = current->cancelled;
    dma_lifecycle.finish(command_id);
    const DecodedCommand *command = nullptr;
    for (const auto &candidate : program->transport.commands)
        if (candidate.command_id == command_id) {
            command = &candidate;
            break;
        }
    if (command)
        unpinDmaAllocations(*command);
    if (failed) {
        // The error CompletionEvent owns the errored ledger entry and the
        // live_commands decrement when it is processed.
        return;
    }
    if (cancelled) {
        // Exactly one terminal state per command/generation: a command that
        // was cancelled here is never also counted as completed or errored.
        commandsCancelled++;
        recordTerminal(command_id, issuedGeneration(command_id),
                       TerminalState::Cancelled, curTick());
        live_commands--;
        if (command && live_per_stream.count(command->stream_id) &&
            live_per_stream[command->stream_id])
            live_per_stream[command->stream_id]--;
        return;
    }
    if (command)
        markOperandValid(*command);
    // Publish the command completion once, after the last descriptor.
    auto *event = new CompletionEvent(this, command_id,
                                      current->completion_event, false);
    schedule(event, nextCoreEdge(*this));
}

void MeshDummyCore::cancelDmaCommand(uint32_t command_id)
{
    if (!dma_lifecycle.admitted(command_id))
        return; // never admitted: the cursor ledger owns it
    const DmaCommandState *state = dma_lifecycle.find(command_id);
    if (state->terminal || state->failed)
        return; // already finalized, or the error path owns it
    if (!dma_lifecycle.cancel(command_id))
        return; // physically issued descriptors keep draining, then finalize
    finalizeDmaCommand(command_id);
}

void MeshDummyCore::onInstanceError(Tick tick)
{
    if (!instance_error) {
        instance_error = true;
        error_latch_tick = tick;
    }
    cancelRemainingCommands();
    if (dispatcher)
        dispatcher->cancelFencesOf(core_id_value);
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
        if (cursor.repeat_gate_command != 0) {
            // The REPEAT itself takes the error terminal (spec 4.1): the
            // gate retires cancelled, the post-REPEAT cursor closes.
            commandsCancelled++;
            recordTerminal(cursor.repeat_gate_command,
                           issuedGeneration(cursor.repeat_gate_command),
                           TerminalState::Cancelled, curTick());
            live_commands--;
            for (const auto &command : program->transport.commands)
                if (command.command_id == cursor.repeat_gate_command) {
                    auto stream_it = live_per_stream.find(command.stream_id);
                    if (stream_it != live_per_stream.end() && stream_it->second)
                        stream_it->second--;
                    break;
                }
            cursor.repeat_gate_command = 0;
            cursor.replay.active = false;
            if (cursor.next_command < cursor.post_repeat_next)
                cursor.next_command = cursor.post_repeat_next;
        }
        cursor.next_command = cursor.end_command;
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
        recordTerminal(it->second, issuedGeneration(it->second),
                       TerminalState::Cancelled, curTick());
        live_commands--;
        const DecodedCommand *command = nullptr;
        for (const auto &candidate : program->transport.commands)
            if (candidate.command_id == it->second)
                command = &candidate;
        if (command && live_per_stream.count(command->stream_id))
            live_per_stream[command->stream_id]--;
        it = recv_waiters.erase(it);
    }
    for (auto it = fence_waiters.begin(); it != fence_waiters.end();) {
        commandsCancelled++;
        recordTerminal(it->first, issuedGeneration(it->first),
                       TerminalState::Cancelled, curTick());
        live_commands--;
        const DecodedCommand *command = nullptr;
        for (const auto &candidate : program->transport.commands)
            if (candidate.command_id == it->first)
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

void MeshDummyCore::onTransferCommitted(uint32_t transfer_id)
{
    committed_transfers[transfer_id] = true;
    // The receiver owns its own notification record: the arrival tick and
    // count are written here, at the real routing point, not derived from the
    // sender's publish decision.
    receive_notifications[transfer_id]++;
    receive_notification_ticks[transfer_id] = curTick();
    // A committed P2P transfer makes every destination allocation that landed
    // in this core resident, including programs whose only synchronization is
    // the producer's completion event (no explicit RECV_WAIT).
    for (const auto &descriptor : program->transport.dma_descriptors) {
        if (descriptor.kind != mesh_abi::kDmaKindP2P_PUSH ||
            descriptor.transfer_id != transfer_id ||
            descriptor.dst.owner_core != core_id_value ||
            descriptor.dst.shard_id == 0)
            continue;
        for (const auto &shard : program->transport.shards) {
            if (shard.shard_id != descriptor.dst.shard_id)
                continue;
            auto allocation = sram.allocations.find(shard.allocation_id);
            if (allocation != sram.allocations.end()) {
                allocation->second.valid = true;
                received_allocations[transfer_id] = shard.allocation_id;
            }
            break;
        }
    }
    auto it = recv_waiters.find(transfer_id);
    if (it == recv_waiters.end())
        return; // early commit remembered; later RECV_WAIT completes instantly
    uint32_t command_id = it->second;
    recv_waiters.erase(it);
    for (const auto &command : program->transport.commands) {
        if (command.command_id != command_id)
            continue;
        auto *event = new CompletionEvent(this, command_id, command.signal_event);
        schedule(event, nextCoreEdge(*this));
        return;
    }
}

void MeshDummyCore::notifyPeerCommit(uint16_t peer_core, uint32_t transfer_id)
{
    // The receiver completes its RECV_WAIT when the P2P bytes have committed
    // to its SRAM tile.  The transport routes this through the loader's core
    // registry indirectly; here the dma engine forwards to the peer core.
    if (peer_core == core_id_value)
        return;
    if (dispatcher == nullptr)
        return;
    // A real peer aperture owns the release: it notifies from the commit it
    // observed on the receiving tile, so the sender's own accounting must not
    // release the receiver a second time.  Without an expectation (the mock
    // runtime, or a transfer that moves no byte) the sender is the only owner.
    if (dispatcher->expectsTransfer(transfer_id))
        return;
    dispatcher->routePeerCommit(peer_core, transfer_id);
}

void MeshDummyCore::completeCommand(uint32_t command_id, uint32_t signal_event,
                                    Tick done_tick, bool error_terminal)
{
    if (error_terminal) {
        commandsErrored++;
        recordTerminal(command_id, issuedGeneration(command_id),
                       TerminalState::Errored, done_tick);
    }
    bool isCompute = false;
    for (const auto &command : program->transport.commands) {
        if (command.command_id != command_id)
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
            // Timing-only compute annotates the destination with a
            // deterministic digest: sensitive to opcode/shape/attrs,
            // insensitive to tick, addresses and queueing (spec 17.2.12/13).
            const DecodedAttr *attr = attrOf(command);
            uint64_t mix =
                0x9E3779B97F4A7C15ull ^ uint64_t(command.opcode) * 0x100000001B3ull;
            if (attr) {
                std::visit(
                    [&mix](const auto &alternative) {
                        using Payload = std::decay_t<decltype(alternative)>;
                        if constexpr (!std::is_same_v<Payload, std::monostate>) {
                            std::array<uint8_t, sizeof(Payload)> raw{};
                            std::memcpy(raw.data(), &alternative, sizeof(Payload));
                            for (uint8_t byte : raw)
                                mix = (mix ^ byte) * 0x100000001B3ull;
                        }
                    },
                    attr->payload);
            }
            // Ordered input digests over the operand views' canonical
            // logical spans (shard offset+span inside the allocation, or the
            // whole allocation when no shard view exists).  The designated
            // result operand joins only for accumulator ops (LOCAL_REDUCE
            // reads dst_old).  Reads/writes are checked against the view
            // bounds, never silently truncated (spec 17.2.12/13).
            // The admitted geometry owns the operation's verified view facts:
            // ordered logical byte spans with the in-object offset, element
            // strides and valid region, so the allocation base is added once
            // and interior padding never joins the digest or the output.
            const uint64_t operation_id = command.source_op_id;
            const VerifiedProgramGeometry &geometry = admission->geometry();
            const VerifiedProgramBacking &backing = admission->backing();
            auto access_layout = [&](const GeometryAccessFact &fact,
                                     uint64_t &allocation_offset,
                                     uint64_t &element_width,
                                     uint64_t &element_count) -> bool {
                const mesh_abi::Allocation *allocation =
                    backing.local(fact.object().object_id);
                if (allocation == nullptr ||
                    fact.object().memory_space !=
                        mesh_abi::semantic_abi::MemorySpace::CORE_SRAM ||
                    fact.object().owner_core != core_id_value)
                    return false;
                if (fact.shape().size() != fact.objectByteStrides().size())
                    return false;
                if (!dtypeByteWidth(fact.tensor().dtype, element_width))
                    return false;
                allocation_offset = allocation->offset_bytes;
                element_count = fact.logicalElementCount();
                return true;
            };
            auto access_address = [](uint64_t allocation_offset,
                                     const GeometryAccessFact &fact,
                                     const std::vector<uint64_t> &coordinates) {
                return allocation_offset +
                       accessElementByteOffset(fact, coordinates);
            };
            const size_t read_count =
                geometry.accessCount(operation_id, GeometryAccessRole::Read);
            uint8_t chunk[512];
            for (size_t access_index = 0; access_index < read_count;
                 access_index++) {
                const GeometryAccessFact *fact = geometry.access(
                    operation_id, GeometryAccessRole::Read, access_index);
                fatal_if(fact == nullptr,
                         "compute digest read access %u is missing "
                         "(command %u)",
                         unsigned(access_index), command.command_id);
                uint64_t allocation_offset = 0, element_width = 0,
                         element_count = 0;
                fatal_if(!access_layout(*fact, allocation_offset,
                                        element_width, element_count),
                         "compute digest view unresolved (command %u "
                         "access %u)",
                         command.command_id, unsigned(access_index));
                std::vector<uint64_t> coordinates(fact->shape().size(), 0);
                for (uint64_t element = 0; element < element_count; element++) {
                    const uint64_t address =
                        access_address(allocation_offset, *fact, coordinates);
                    fatal_if(!functionalSramRead(address, element_width, chunk),
                             "compute digest read escapes SRAM view "
                             "(command %u)", command.command_id);
                    for (uint64_t b = 0; b < element_width; b++)
                        mix = (mix ^ chunk[b]) * 0x100000001B3ull;
                    advanceAccessCoordinates(*fact, coordinates);
                }
            }
            std::array<uint32_t, 4> digest_words{0, 0, 0, 0};
            uint32_t result_allocation_id = 0;
            uint64_t result_allocation_base = 0, result_offset = 0;
            uint64_t result_element_width = 0, result_element_count = 0;
            const GeometryAccessFact *result_fact = geometry.access(
                operation_id, GeometryAccessRole::Write, 0);
            if (result_fact != nullptr) {
                uint64_t element_width = 0, element_count = 0;
                fatal_if(!access_layout(*result_fact, result_allocation_base,
                                        element_width, element_count),
                         "compute result view unresolved (command %u)",
                         command.command_id);
                const mesh_abi::Allocation *allocation =
                    backing.local(result_fact->object().object_id);
                result_allocation_id = allocation->allocation_id;
                result_element_width = element_width;
                result_element_count = element_count;
                result_offset = result_allocation_base;
            }
            for (int i = 0; i < 4; i++) {
                mix ^= mix >> 30;
                mix *= 0xBF58476D1CE4E5B9ull;
                mix ^= mix >> 27;
                mix *= 0x94D049BB133111EBull;
                mix ^= mix >> 31;
                digest_words[i] = uint32_t(mix >> 32) | uint32_t(mix & 0xFFFFFFFF);
            }
            // FUNCTIONAL_BYTES result: a deterministic byte stream derived
            // from the semantic digest covers exactly the result view's valid
            // elements, so interior padding is never rewritten.  The producer
            // publishes one record per physically contiguous run it writes, so
            // every record's address and size describe exactly the bytes its
            // digest covers.
            std::vector<ContentRowObservation> result_rows;
            std::vector<ContentRowObservation> merged_rows;
            if (result_fact != nullptr) {
                uint64_t stream = mix;
                uint64_t emitted = 0;
                std::vector<uint64_t> coordinates(
                    result_fact->shape().size(), 0);
                const uint64_t row_elements =
                    result_fact->shape().empty() ? result_element_count
                                                 : result_fact->shape().back();
                ContentRowObservation merged;
                std::optional<mesh_hash::Sha256> merged_hash;
                uint64_t element = 0;
                while (element < result_element_count) {
                    const uint64_t rows_elements =
                        row_elements == 0
                            ? result_element_count - element
                            : std::min(row_elements,
                                       result_element_count - element);
                    uint64_t index = 0;
                    while (index < rows_elements) {
                        ContentRowObservation row;
                        mesh_hash::Sha256 row_hash;
                        uint64_t run_bytes = 0;
                        uint64_t run_end = 0;
                        while (index < rows_elements) {
                            const uint64_t address = access_address(
                                result_allocation_base, *result_fact,
                                coordinates);
                            if (run_bytes != 0 && address != run_end)
                                break;
                            if (run_bytes == 0)
                                row.address = address;
                            for (uint64_t b = 0; b < result_element_width; b++) {
                                stream = (stream ^ (emitted + b)) *
                                         0x100000001B3ull;
                                chunk[b] = uint8_t(stream >> 56);
                            }
                            if (element == 0)
                                result_offset = address;
                            fatal_if(!functionalSramWrite(
                                         address, result_element_width, chunk),
                                     "compute result write escapes SRAM view "
                                     "(command %u)", command.command_id);
                            row_hash.update(chunk, result_element_width);
                            if (!merged_hash) {
                                merged.address = address;
                                merged_hash.emplace();
                            }
                            merged_hash->update(chunk, result_element_width);
                            run_bytes += result_element_width;
                            run_end = address + result_element_width;
                            emitted += result_element_width;
                            element++;
                            index++;
                            advanceAccessCoordinates(*result_fact, coordinates);
                        }
                        row.size = run_bytes;
                        row.digest = mesh_hash::digestHex(row_hash.digest());
                        result_rows.push_back(row);
                        const bool more = element < result_element_count;
                        const uint64_t next_address =
                            more ? access_address(result_allocation_base,
                                                  *result_fact, coordinates)
                                 : 0;
                        if (!more || next_address != run_end) {
                            merged.size = run_end - merged.address;
                            merged.digest =
                                mesh_hash::digestHex(merged_hash->digest());
                            // A merged run identical to the fine run it just
                            // closed adds no evidence.
                            if (merged.size != row.size ||
                                merged.address != row.address)
                                merged_rows.push_back(merged);
                            merged_hash.reset();
                        }
                    }
                }
            }
            observations.recordComputeOutput(
                CommandGeneration{command_id, issuedGeneration(command_id)},
                result_allocation_id, result_offset, digest_words,
                result_rows, merged_rows);
        }
        if (command.opcode == mesh_abi::kOpcodeRECV_WAIT)
            markOperandValid(command);
        break;
    }
    if (!error_terminal) {
        commandsCompleted++;
        recordTerminal(command_id, issuedGeneration(command_id),
                       TerminalState::Completed, done_tick);
    }
    if (signal_event) {
        auto issued = command_generations.find(command_id);
        publishEvent(signal_event, command_id,
                     issued == command_generations.end() ? 0 : issued->second);
    }
    live_commands--;
    auto stream_it = std::find_if(program->transport.commands.begin(), program->transport.commands.end(),
                                  [&](const DecodedCommand &c) {
                                      return c.command_id == command_id;
                                  });
    if (stream_it != program->transport.commands.end()) {
        uint16_t stream_id = stream_it->stream_id;
        if (live_per_stream.count(stream_id))
            live_per_stream[stream_id]--;
        auto fence_it = fence_waiters.find(command_id);
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
            uint32_t fence_command = it->first;
            it = fence_waiters.erase(it);
            commandsCompleted++;
            recordTerminal(fence_command, issuedGeneration(fence_command),
                           TerminalState::Completed, done_tick);
            live_commands--;
            for (const auto &command : program->transport.commands)
                if (command.command_id == fence_command) {
                    live_per_stream[command.stream_id]--;
                    if (command.signal_event)
                        publishEvent(command.signal_event,
                                     command.command_id, 0);
                    break;
                }
        } else {
            ++it;
        }
    }
    scheduleTick();
    finishIfHalted();
}

void MeshDummyCore::publishEvent(uint32_t event_id, uint32_t participant,
                                  uint32_t generation)
{
    for (const auto &event : program->transport.events) {
        if (event.event_id != event_id)
            continue;
        if (event.kind == mesh_abi::kEventKindBARRIER) {
            if (!scoreboard->arrive(event_id, generation, participant,
                                    event.expected_arrivals, curTick()))
                return;
        }
        break;
    }
    // Signals become visible to consumers only after the configured event
    // visibility delay (spec 5.8: earliest next core edge after the
    // visibility window).
    auto pending = pending_visibility.find(event_id);
    if (pending != pending_visibility.end()) {
        if (pending->second->scheduled())
            deschedule(pending->second);
        pending_visibility.erase(pending);
    }
    auto *event = new VisibilityEvent(this, event_id);
    pending_visibility[event_id] = event;
    schedule(event, clockEdge(event_visibility) + 1);
}

void MeshDummyCore::onEventVisible(uint32_t event_id)
{
    pending_visibility.erase(event_id);
    scoreboard->set_visible(event_id);
    eventsPublished++;
    scheduleTick();
}

bool MeshDummyCore::dmaDrained() const
{
    if (outstanding_axi != 0 || !live_dma_tags.empty())
        return false;
    if (dma != nullptr && !dma->idle())
        return false;
    return dma_lifecycle.drained();
}

uint64_t MeshDummyCore::progressSnapshot() const
{
    return commandsIssued.value() + commandsCompleted.value() +
           eventsPublished.value() + requestBegins.value() +
           requestEnds.value();
}

void MeshDummyCore::finishIfHalted()
{
    if (instance_active && instance_error && live_commands == 0 &&
        recv_waiters.empty() && fence_waiters.empty() &&
        pending_visibility.empty() && dmaDrained()) {
        // INSTANCE_OWNED_WORK_DRAINED -> INSTANCE_ERROR_DRAINED.
        instance_active = false;
        error_drained = true;
        work_drained_tick = curTick();
        DPRINTF(AiMesh, "core %u error-drained at %llu\n", core_id_value,
                (unsigned long long)work_drained_tick);
        if (dispatcher)
            dispatcher->notifyCoreHalted(core_id_value);
        return;
    }
    // HALT waits for the publication and physical DMA drain: a
    // scheduled-but-invisible signal or an in-flight descriptor keeps the
    // instance alive (spec 8.2/8.8).
    if (core_halted && live_commands == 0 && instance_active &&
        pending_visibility.empty() && dmaDrained()) {
        instance_active = false;
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

const DecodedDmaDescriptor *
MeshDummyCore::descriptorById(uint32_t descriptor_id) const
{
    if (program == nullptr)
        return nullptr;
    for (const auto &descriptor : program->transport.dma_descriptors)
        if (descriptor.descriptor_id == descriptor_id)
            return &descriptor;
    return nullptr;
}

std::optional<DestinationStorageObservation>
MeshDummyCore::admittedDestinationStorage(uint32_t descriptor_id) const
{
    if (admission == nullptr || program == nullptr)
        return std::nullopt;
    const DecodedDmaDescriptor *descriptor = descriptorById(descriptor_id);
    if (descriptor == nullptr)
        return std::nullopt;
    uint32_t operation_id = 0;
    bool resolved = false;
    for (const DecodedCommand &command : program->transport.commands)
        if (command.command_id == descriptor->command_id) {
            operation_id = command.source_op_id;
            resolved = true;
            break;
        }
    if (!resolved)
        return std::nullopt;
    const GeometryAccessFact *fact = admission->geometry().access(
        operation_id, GeometryAccessRole::Write, 0);
    if (fact == nullptr)
        return std::nullopt;
    const mesh_abi::Allocation *allocation =
        admission->backing().local(fact->object().object_id);
    if (allocation == nullptr)
        return std::nullopt;
    DestinationStorageObservation storage;
    storage.allocation_id = allocation->allocation_id;
    storage.base = admittedEndpointAddress(descriptor_id, false) -
                   descriptor->dst.offset_bytes;
    storage.bytes = allocation->size_bytes;
    return storage;
}

uint64_t
MeshDummyCore::admittedEndpointAddress(uint32_t descriptor_id, bool source) const
{
    fatal_if(!invocation_binding,
             "core %u has no resolved invocation for descriptor %u address",
             core_id_value, descriptor_id);
    return invocation_binding->endpointAddress(descriptor_id, source);
}

uint64_t
MeshDummyCore::admittedLocalOffset(uint32_t descriptor_id, bool source) const
{
    const DecodedDmaDescriptor *descriptor = descriptorById(descriptor_id);
    fatal_if(descriptor == nullptr || admission == nullptr,
             "core %u has no admitted descriptor %u for a local offset",
             core_id_value, descriptor_id);
    const DecodedDmaEndpoint &endpoint = source ? descriptor->src
                                                : descriptor->dst;
    const RuntimeArch::Region *region =
        admission->arch().region(uint16_t(endpoint.region_id));
    fatal_if(region == nullptr,
             "core %u cannot resolve the region of descriptor %u endpoint",
             core_id_value, descriptor_id);
    bool overflow = false;
    const uint64_t base = region->tileBase(core_id_value, overflow);
    fatal_if(overflow, "core %u tile base overflows for descriptor %u",
             core_id_value, descriptor_id);
    const uint64_t address = admittedEndpointAddress(descriptor_id, source);
    fatal_if(address < base,
             "core %u descriptor %u endpoint %#llx precedes its tile base %#llx",
             core_id_value, descriptor_id, (unsigned long long)address,
             (unsigned long long)base);
    return address - base;
}

bool
MeshDummyCore::transferCommitted(uint32_t transfer_id) const
{
    const auto found = committed_transfers.find(transfer_id);
    return found != committed_transfers.end() && found->second;
}

uint32_t
MeshDummyCore::receivedAllocation(uint32_t transfer_id) const
{
    const auto found = received_allocations.find(transfer_id);
    return found == received_allocations.end() ? 0 : found->second;
}

bool
MeshDummyCore::allocationValid(uint32_t allocation_id) const
{
    const auto found = sram.allocations.find(allocation_id);
    return found != sram.allocations.end() && found->second.valid;
}

uint32_t
MeshDummyCore::receiveNotificationCount(uint32_t transfer_id) const
{
    const auto found = receive_notifications.find(transfer_id);
    return found == receive_notifications.end() ? 0 : found->second;
}

Tick
MeshDummyCore::receiveNotificationTick(uint32_t transfer_id) const
{
    const auto found = receive_notification_ticks.find(transfer_id);
    return found == receive_notification_ticks.end() ? 0 : found->second;
}

} // namespace ai_mesh
} // namespace gem5

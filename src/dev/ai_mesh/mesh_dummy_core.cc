#include <algorithm>

#include "dev/ai_mesh/mesh_dummy_core.hh"

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
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
    for (const auto &allocation : program->allocations)
        if (allocation.owner_core == core_id_value) {
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

Tick MeshDummyCore::serviceResultWrite(uint32_t command_id)
{
    // Serial phase 3: the result operand's SRAM write service happens after
    // the engine timer; the command completes only once it is done.
    for (const auto &command : program->commands) {
        if (command.command_id != command_id || command.operand_count == 0)
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
    for (const auto &command : program->commands) {
        if (command.command_id != command_id || command.operand_count == 0)
            continue;
        const DecodedOperand &source =
            program->operands[command.operand_begin];
        auto it = sram.allocations.find(source.allocation_id);
        if (it == sram.allocations.end())
            fatal("E_ABI_BOUNDS: DMA command %u references unknown allocation %u",
                  command_id, source.allocation_id);
        if (!it->second.valid) {
            poisonReadFaults++;
            fatal("E_TENSOR_NOT_RESIDENT: DMA command %u reads allocation %u "
                  "before any producer committed",
                  command_id, source.allocation_id);
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

void MeshDummyCore::dispatchInstance(uint32_t instance_id)
{
    fatal_if(!program, "core %u dispatched without an installed program", core_id_value);
    fatal_if(instance_active, "core %u already running an instance", core_id_value);
    instance_active = true;
    core_halted = false;
    instance_error = false;
    error_drained = false;
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
        schedule(&tick_event, clockEdge() + 1);
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
            if (liveStreamCommands(kv.first) >= admit_window_value)
                break; // finite admit window: real decode-stage backpressure
            if (cursor.next_command >= cursor.end_command)
                break;
            const uint32_t index = cursor.next_command;
            const DecodedCommand &command = program->commands[index];
            if (!waitsSatisfied(command))
                break;
            if (!tryIssue(command))
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

    if (progressed || live_commands > 0 || !core_halted)
        scheduleTick();

    finishIfHalted();
}

bool MeshDummyCore::waitsSatisfied(const DecodedCommand &command) const
{
    fatal_if(scoreboard == nullptr, "core %u has no scoreboard", core_id_value);
    for (uint16_t w = 0; w < command.wait_count; w++)
        if (!scoreboard->visible(program->waits[command.wait_begin + w]))
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

void MeshDummyCore::completeFence(uint32_t command_id, uint32_t signal_event)
{
    auto *event = new CompletionEvent(this, command_id, signal_event);
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

void MeshDummyCore::issueCompute(const DecodedCommand &command, const DecodedAttr *attr)
{
    commandsIssued++;
    command_issue_ticks[command.command_id] = curTick();
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
    schedule(event, clockEdge(Cycles(cycles)) + read_stall + 1);
    DPRINTF(AiMesh, "core %u compute command %u opcode=%u cycles=%llu stall=%llu\n",
            core_id_value,
            command.command_id, command.opcode, (unsigned long long)cycles);
}

void MeshDummyCore::issueControl(const DecodedCommand &command)
{
    commandsIssued++;
    command_issue_ticks[command.command_id] = curTick();

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
        uint32_t transfer_id = attr->as<mesh_abi::RecvWaitV1>().transfer_id;
        live_commands++;
        live_per_stream[command.stream_id]++;
        auto done = committed_transfers.find(transfer_id);
        if (done != committed_transfers.end() && done->second) {
            // Transfer already committed: complete at the next edge.
            auto *event =
                new CompletionEvent(this, command.command_id, command.signal_event);
            schedule(event, clockEdge() + 1);
            return;
        }
        recv_waiters[transfer_id] = command.command_id;
        return;
    }
    case mesh_abi::kOpcodeHALT:
        core_halted = true;
        haltCommands++;
        commandsCompleted++;
        completed_command_ids.push_back(command.command_id);
        instance_completed_ids.push_back(command.command_id);
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
                command.command_id,
                attr->as<mesh_abi::FillV1>().pattern);
        }
        if (descriptor.useful_bytes > 0 && !dmaSramAdmissible(descriptor))
            return false; // bank queue full: retry next cycle
        if (!tryPinDmaAllocations(command))
            return false; // pinned by an in-flight DMA: retry next cycle
        if (!dma->submit(descriptor, curTick())) {
            unpinDmaAllocations(command);
            return false; // backpressure: retry next cycle, no side effects
        }
        if (descriptor.kind == mesh_abi::kDmaKindP2P_PUSH &&
            descriptor.useful_bytes > 0 && dispatcher)
            dispatcher->armTransferExpectation(descriptor.transfer_id);
        commandsIssued++;
        command_issue_ticks[command.command_id] = curTick();
        live_commands++;
        live_per_stream[command.stream_id]++;
        outstanding_axi++;
        const uint64_t tag = next_dma_tag++;
        live_dma_tags.insert(tag);
        dma_tag_info[tag] = DmaTagInfo{descriptor.kind,
                                       descriptor.dst.memory_space};
        dma_tag_command[tag] = command.command_id;
        dma_command_tag[command.command_id] = tag;
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
            cancelPendingVisibility(member.signal_event);
        for (const auto &descriptor : program->descriptors)
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
    const auto &repeat = attr->as<mesh_abi::RepeatV1>();
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

    command_issue_ticks[command.command_id] = curTick();
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
    auto *event = new CompletionEvent(this, command.command_id, command.signal_event);
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
    // Final generation drained; REPEAT itself completes in one control cycle.
    const uint32_t repeat_id = cursor.repeat_gate_command;
    uint32_t signal = 0;
    for (const auto &command : program->commands)
        if (command.command_id == repeat_id) {
            signal = command.signal_event;
            break;
        }
    cursor.repeat_gate_command = 0;
    cursor.next_command = cursor.post_repeat_next;
    auto *event = new CompletionEvent(this, repeat_id, signal);
    schedule(event, clockEdge() + 1);
}

void MeshDummyCore::onDmaCompleted(uint32_t command_id, uint32_t completion_event,
                                   Tick commit_tick, DmaStatus status)
{
    if (outstanding_axi > 0)
        outstanding_axi--;
    {
        auto tag_it = dma_command_tag.find(command_id);
        fatal_if(tag_it == dma_command_tag.end(),
                 "DMA completion without a live tag (command %u)", command_id);
        const uint64_t retired_tag = tag_it->second;
        live_dma_tags.erase(retired_tag);
        dma_tag_info.erase(retired_tag);
        dma_tag_command.erase(retired_tag);
        dma_command_tag.erase(tag_it);
        if (dispatcher)
            dispatcher->onDmaTagRetired(core_id_value, retired_tag);
    }
    for (const auto &command : program->commands)
        if (command.command_id == command_id) {
            unpinDmaAllocations(command);
            break;
        }
    if (status != DmaStatus::OK && !instance_error) {
        // Instance-global error latch (spec 5.6): the first error fans out
        // to every participating core; each cancels its undrained work.
        if (dispatcher)
            dispatcher->latchInstanceError(this, commit_tick);
        else {
            instance_error = true;
            error_latch_tick = commit_tick;
        }
    }
    if (status == DmaStatus::OK)
        for (const auto &command : program->commands)
            if (command.command_id == command_id) {
                markOperandValid(command);
                break;
            }
    // Publish the descriptor completion at the earliest next core edge.
    // Error completions carry no signal event (no success consumer wake).
    auto *event = new CompletionEvent(
        this, command_id, status == DmaStatus::OK ? completion_event : 0,
        status != DmaStatus::OK);
    schedule(event, clockEdge() + 1);
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
    // Cancel every command that can no longer run: the not-yet-issued tail
    // of each stream plus waiters whose producer chain is broken by the
    // error.  Already-issued work keeps draining physically.
    for (auto &kv : cursors) {
        StreamCursor &cursor = kv.second;
        if (cursor.repeat_gate_command != 0) {
            // The REPEAT itself takes the error terminal (spec 4.1): the
            // gate retires cancelled, the post-REPEAT cursor closes.
            commandsCancelled++;
            cancelled_command_ids.push_back(cursor.repeat_gate_command);
            instance_cancelled_ids.push_back(cursor.repeat_gate_command);
            live_commands--;
            for (const auto &command : program->commands)
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
        for (uint32_t index = cursor.next_command; index < cursor.end_command;
             index++) {
            const DecodedCommand &command = program->commands[index];
            commandsCancelled++;
            cancelled_command_ids.push_back(command.command_id);
            instance_cancelled_ids.push_back(command.command_id);
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
            if (candidate.command_id == it->second)
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
            if (candidate.command_id == it->first)
                command = &candidate;
        if (command && live_per_stream.count(command->stream_id))
            live_per_stream[command->stream_id]--;
        it = fence_waiters.erase(it);
    }
}

void MeshDummyCore::onTransferCommitted(uint32_t transfer_id)
{
    committed_transfers[transfer_id] = true;
    auto it = recv_waiters.find(transfer_id);
    if (it == recv_waiters.end())
        return; // early commit remembered; later RECV_WAIT completes instantly
    uint32_t command_id = it->second;
    recv_waiters.erase(it);
    for (const auto &command : program->commands) {
        if (command.command_id != command_id)
            continue;
        auto *event = new CompletionEvent(this, command_id, command.signal_event);
        schedule(event, clockEdge() + 1);
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
    // Handled by dispatcher wiring: see MeshDispatcher::notifyPeerCommit.
    if (dispatcher)
        dispatcher->routePeerCommit(peer_core, transfer_id);
}

void MeshDummyCore::completeCommand(uint32_t command_id, uint32_t signal_event,
                                    Tick done_tick, bool error_terminal)
{
    if (error_terminal) {
        commandsErrored++;
        errored_command_ids.push_back(command_id);
        instance_errored_ids.push_back(command_id);
    }
    bool isCompute = false;
    for (const auto &command : program->commands) {
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
            // Timing-only compute annotates the destination with a
            // deterministic digest: sensitive to opcode/shape/attrs,
            // insensitive to tick, addresses and queueing (spec 17.2.12/13).
            const DecodedAttr *attr = attrOf(command);
            uint64_t mix =
                0x9E3779B97F4A7C15ull ^ uint64_t(command.opcode) * 0x100000001B3ull;
            if (attr) {
                for (uint8_t byte : attr->payload)
                    mix = (mix ^ byte) * 0x100000001B3ull;
            }
            // Ordered input digests over the operand views' canonical
            // logical spans (shard offset+span inside the allocation, or the
            // whole allocation when no shard view exists).  The designated
            // result operand joins only for accumulator ops (LOCAL_REDUCE
            // reads dst_old).  Reads/writes are checked against the view
            // bounds, never silently truncated (spec 17.2.12/13).
            const bool accumulates = command.opcode == mesh_abi::kOpcodeLOCAL_REDUCE;
            auto view_of = [&](const DecodedOperand &operand,
                               uint64_t &offset, uint64_t &span)
                -> bool {
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
                               span <=
                                   allocation->size_bytes -
                                       shard.allocation_offset;
                    }
                }
                offset = allocation->offset_bytes;
                span = allocation->size_bytes;
                return true;
            };
            const uint16_t input_count =
                command.operand_count - (accumulates ? 0 : 1);
            uint8_t chunk[512];
            for (uint16_t operand_i = 0; operand_i < input_count; operand_i++) {
                const DecodedOperand &input =
                    program->operands[command.operand_begin + operand_i];
                uint64_t offset = 0, span = 0;
                fatal_if(!view_of(input, offset, span),
                         "compute digest view unresolved (command %u operand %u)",
                         command.command_id, operand_i);
                for (uint64_t done = 0; done < span; done += sizeof(chunk)) {
                    const uint64_t take =
                        span - done < sizeof(chunk) ? span - done
                                                    : sizeof(chunk);
                    fatal_if(!functionalSramRead(offset + done, take, chunk),
                             "compute digest read escapes SRAM view "
                             "(command %u)", command.command_id);
                    for (uint64_t b = 0; b < take; b++)
                        mix = (mix ^ chunk[b]) * 0x100000001B3ull;
                }
            }
            ComputeDigest digest;
            digest.command_id = command.command_id;
            uint64_t result_offset = 0, result_span = 0;
            if (command.operand_count > 0) {
                const DecodedOperand &dst =
                    program->operands[command.operand_begin + command.operand_count - 1];
                digest.allocation_id = dst.allocation_id;
                fatal_if(!view_of(dst, result_offset, result_span),
                         "compute result view unresolved (command %u)",
                         command.command_id);
                digest.offset = result_offset;
            }
            for (int i = 0; i < 4; i++) {
                mix ^= mix >> 30;
                mix *= 0xBF58476D1CE4E5B9ull;
                mix ^= mix >> 27;
                mix *= 0x94D049BB133111EBull;
                mix ^= mix >> 31;
                digest.digest_words[i] = uint32_t(mix >> 32) | uint32_t(mix & 0xFFFFFFFF);
            }
            // FUNCTIONAL_BYTES result: a deterministic byte stream derived
            // from the semantic digest covers exactly the result view span.
            uint64_t stream = mix;
            for (uint64_t done = 0; done < result_span; done += sizeof(chunk)) {
                const uint64_t take =
                    result_span - done < sizeof(chunk) ? result_span - done
                                                       : sizeof(chunk);
                for (uint64_t b = 0; b < take; b++) {
                    stream = (stream ^ uint64_t(done + b)) * 0x100000001B3ull;
                    chunk[b] = uint8_t(stream >> 56);
                }
                fatal_if(!functionalSramWrite(result_offset + done, take, chunk),
                         "compute result write escapes SRAM view (command %u)",
                         command.command_id);
            }
            computeDigests.push_back(digest);
        }
        if (command.opcode == mesh_abi::kOpcodeRECV_WAIT)
            markOperandValid(command);
        break;
    }
    commandsCompleted++;
    completed_command_ids.push_back(command_id);
    instance_completed_ids.push_back(command_id);
    command_done_ticks[command_id] = done_tick;
    if (signal_event) {
        uint32_t generation = 0;
        for (const auto &command : program->commands)
            if (command.command_id == command_id) {
                auto cursor = cursors.find(command.stream_id);
                if (cursor != cursors.end())
                    generation = cursor->second.replay.generation;
                break;
            }
        publishEvent(signal_event, command_id, generation);
    }
    live_commands--;
    auto stream_it = std::find_if(program->commands.begin(), program->commands.end(),
                                  [&](const DecodedCommand &c) {
                                      return c.command_id == command_id;
                                  });
    if (stream_it != program->commands.end()) {
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
            completed_command_ids.push_back(fence_command);
            instance_completed_ids.push_back(fence_command);
            command_done_ticks[fence_command] = done_tick;
            live_commands--;
            for (const auto &command : program->commands)
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
    for (const auto &event : program->events) {
        if (event.event_id != event_id)
            continue;
        if (event.kind == mesh_abi::kEventKindBARRIER) {
            if (!scoreboard->arrive(event_id, generation, participant,
                                    event.expected_arrivals))
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

void MeshDummyCore::finishIfHalted()
{
    if (instance_active && instance_error && live_commands == 0 &&
        recv_waiters.empty() && fence_waiters.empty() &&
        pending_visibility.empty()) {
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
    // HALT waits for the publication drain: a scheduled-but-invisible
    // signal keeps the instance alive (spec: HALT waits for event
    // publication drain).
    if (core_halted && live_commands == 0 && instance_active &&
        pending_visibility.empty()) {
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

} // namespace ai_mesh
} // namespace gem5

"""Agent serving program fixtures and profile assembly (Gate 6 R1)."""

from __future__ import annotations

import dataclasses

from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import hbm_endpoint, sram_endpoint
from mesh_ir.model import (
    StringEntry,
    AgentInstanceMemberBinding,
    AgentInstanceProfile,
    AgentPublishSurrogateBinding,
    AgentRequestBindingRequirement,
    AgentRequestProfile,
    AgentSourceCoreMap,
)

MEMORY_SPACE_CORE_SRAM = A.MEMORY_SPACE.CORE_SRAM
FULL_VIEW_SLOT_SHIFT = 4096

HIDDEN = 8
INTERMEDIATE = 8
FP16 = A.DTYPE.FP16
RO = A.ACCESS_KIND.READ_ONLY
RW = A.ACCESS_KIND.READ_WRITE
HBM_REGION = 0

TOKEN_BYTES = HIDDEN * 2
WEIGHT_BYTES = HIDDEN * INTERMEDIATE * 2
OUTPUT_BYTES = HIDDEN * INTERMEDIATE * 2
KV_BYTES_PER_TOKEN = 16
PREFILL_TOKENS = 8
DECODE_CHUNK_TOKENS = 1
KV_BYTES = PREFILL_TOKENS * KV_BYTES_PER_TOKEN
KV_ALLOC_BYTES = KV_BYTES + KV_BYTES_PER_TOKEN

INPUT_BINDING_BYTES = TOKEN_BYTES * PREFILL_TOKENS
HOST_OUTPUT_BYTES = OUTPUT_BYTES
PUBLISH_CHUNK_BYTES = OUTPUT_BYTES // 2

IN_OFFSET = 0x0000
W_OFFSET = 0x2000
KV_OFFSET = 0x3000
ACT_OFFSET = 0x5000
PUB_OFFSET = 0x4000

IN_BASE = 0x100000
W_BASE = 0x200000
OUT_BASE = 0x300000
KV_BASE = 0x400000


def serving_program(arch):
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "gate6_serving")
    entrypoint_id = builder.entrypoint("main", "serving_prefill",
                                       lifecycle_core=0, lifecycle_stream=0)
    prefill_profile = 1
    decode_profile = builder.profile(entrypoint_id, "serving_decode")
    publish_profile = builder.profile(entrypoint_id, "serving_publish")
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16,
                          A.STORAGE_CLASS.EXTERNAL, RO, (HIDDEN, INTERMEDIATE))
    t_w = builder.tensor("weight", A.TENSOR_ROLE.WEIGHT, FP16,
                         A.STORAGE_CLASS.EXTERNAL, RO,
                         (INTERMEDIATE, HIDDEN))
    t_kv = builder.tensor("kv", A.TENSOR_ROLE.KV_CACHE, A.DTYPE.INT8,
                          A.STORAGE_CLASS.EXTERNAL, RW, (KV_ALLOC_BYTES,))
    t_act = builder.tensor("activation", A.TENSOR_ROLE.ACTIVATION, FP16,
                           A.STORAGE_CLASS.CORE_SRAM, RW,
                           (HIDDEN, INTERMEDIATE))
    t_sur = builder.tensor("surrogate", A.TENSOR_ROLE.ACTIVATION, FP16,
                           A.STORAGE_CLASS.CORE_SRAM, RW,
                           (HIDDEN, INTERMEDIATE))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16,
                           A.STORAGE_CLASS.EXTERNAL, RW,
                           (HIDDEN, INTERMEDIATE))
    symbols = {}
    for name, tensor, offset in (
            ("input", t_in, IN_BASE),
            ("weight", t_w, W_BASE),
            ("kv", t_kv, KV_BASE),
            ("output", t_out, OUT_BASE),
            ("in_slot_0", t_in, IN_BASE),
            ("kv_slot_0", t_kv, KV_BASE),
            ("out_slot_0", t_out, OUT_BASE)):
        builder.relocation(name, A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION,
                           tensor, offset)
        symbols[name] = builder.string(name)

    align = arch.sram_base_alignment_bytes
    a_in = builder.allocation(0, IN_OFFSET, INPUT_BINDING_BYTES, align)
    a_w = builder.allocation(0, W_OFFSET, WEIGHT_BYTES, align)
    a_kv = builder.allocation(0, KV_OFFSET, KV_ALLOC_BYTES, align)
    a_act = builder.allocation(0, ACT_OFFSET, OUTPUT_BYTES, align)
    a_pub = builder.allocation(0, PUB_OFFSET, OUTPUT_BYTES, align)
    s_in = builder.shard(t_in, 0, a_in, (HIDDEN, INTERMEDIATE),
                         INPUT_BINDING_BYTES)
    s_w = builder.shard(t_w, 0, a_w, (INTERMEDIATE, HIDDEN), WEIGHT_BYTES)
    s_kv = builder.shard(t_kv, 0, a_kv, (KV_ALLOC_BYTES,), KV_ALLOC_BYTES)
    s_act = builder.shard(t_act, 0, a_act, (HIDDEN, INTERMEDIATE),
                          OUTPUT_BYTES)
    s_sur = builder.shard(t_sur, 0, a_pub, (HIDDEN, INTERMEDIATE),
                          OUTPUT_BYTES)
    s_out = builder.shard(t_out, 0, a_pub, (HIDDEN, INTERMEDIATE),
                          OUTPUT_BYTES)

    stream = builder.stream(
        0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE |
        A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_in = builder.event()
    e_w = builder.event()
    e_gemm = builder.event()
    e_kvst = builder.event()
    e_kvld = builder.event()
    e_kvst2 = builder.event()
    e_fill = builder.event()
    e_out = builder.event()
    e_out2 = builder.event()
    e_end = builder.event()

    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_in = stream.command(A.OPCODE.DMA_LOAD, waits=(e_begin,),
                            operands=((t_in, s_in, a_in, RO),))
    d_in = builder.dma(cmd_in, A.DMA_KIND.LOAD,
                      src=hbm_endpoint(t_in, s_in, IN_BASE),
                      dst=sram_endpoint(t_in, s_in, 0, IN_OFFSET),
                      rows=1, row_bytes=INPUT_BINDING_BYTES,
                      src_stride=INPUT_BINDING_BYTES,
                      dst_stride=INPUT_BINDING_BYTES,
                      completion_event=e_in)
    builder.oracle(entrypoint_id, prefill_profile, d_in, cmd_in.command_id,
                   A.DMA_KIND.LOAD, IN_BASE, 1, INPUT_BINDING_BYTES,
                   INPUT_BINDING_BYTES)

    cmd_w = stream.command(A.OPCODE.DMA_LOAD, waits=(e_begin,),
                           operands=((t_w, s_w, a_w, RO),))
    d_w = builder.dma(cmd_w, A.DMA_KIND.LOAD,
                     src=hbm_endpoint(t_w, s_w, W_BASE),
                     dst=sram_endpoint(t_w, s_w, 0, W_OFFSET),
                     rows=1, row_bytes=WEIGHT_BYTES,
                     src_stride=WEIGHT_BYTES, dst_stride=WEIGHT_BYTES,
                     completion_event=e_w)
    builder.oracle(entrypoint_id, prefill_profile, d_w, cmd_w.command_id,
                   A.DMA_KIND.LOAD, W_BASE, 1, WEIGHT_BYTES, WEIGHT_BYTES)

    gemm_attr = builder.gemm_attr(1, HIDDEN, INTERMEDIATE, HIDDEN, FP16,
                                  A.DTYPE.FP32)
    stream.command(A.OPCODE.GEMM, waits=(e_in, e_w),
                   operands=((t_in, s_in, a_in, RO),
                             (t_w, s_w, a_w, RO),
                             (t_act, s_act, a_act, RW)),
                   signal_event=e_gemm, attr_index=gemm_attr)

    cmd_kvst = stream.command(A.OPCODE.DMA_STORE, waits=(e_gemm,),
                              operands=((t_kv, s_kv, a_kv, RW),))
    d_kvst = builder.dma(cmd_kvst, A.DMA_KIND.STORE,
                         src=sram_endpoint(t_kv, s_kv, 0, KV_OFFSET),
                         dst=hbm_endpoint(t_kv, s_kv, KV_BASE),
                         rows=1, row_bytes=KV_BYTES,
                         src_stride=KV_BYTES, dst_stride=KV_BYTES,
                         completion_event=e_kvst)
    builder.oracle(entrypoint_id, prefill_profile, d_kvst,
                   cmd_kvst.command_id, A.DMA_KIND.STORE, KV_BASE, 1,
                   KV_BYTES, KV_BYTES)

    cmd_kvld = stream.command(A.OPCODE.DMA_LOAD, waits=(e_kvst,),
                              operands=((t_kv, s_kv, a_kv, RW),))
    d_kvld = builder.dma(cmd_kvld, A.DMA_KIND.LOAD,
                         src=hbm_endpoint(t_kv, s_kv, KV_BASE),
                         dst=sram_endpoint(t_kv, s_kv, 0, KV_OFFSET),
                         rows=1, row_bytes=KV_BYTES,
                         src_stride=KV_BYTES, dst_stride=KV_BYTES,
                         completion_event=e_kvld)
    builder.oracle(entrypoint_id, decode_profile, d_kvld,
                   cmd_kvld.command_id, A.DMA_KIND.LOAD, KV_BASE, 1,
                   KV_BYTES, KV_BYTES)

    cmd_kvst2 = stream.command(A.OPCODE.DMA_STORE, waits=(e_kvld,),
                               operands=((t_kv, s_kv, a_kv, RW),))
    d_kvst2 = builder.dma(cmd_kvst2, A.DMA_KIND.STORE,
                          src=sram_endpoint(t_kv, s_kv, 0,
                                            KV_OFFSET + KV_BYTES),
                          dst=hbm_endpoint(t_kv, s_kv, KV_BASE + KV_BYTES),
                          rows=1, row_bytes=KV_BYTES_PER_TOKEN,
                          src_stride=KV_BYTES_PER_TOKEN,
                          dst_stride=KV_BYTES_PER_TOKEN,
                          completion_event=e_kvst2)
    builder.oracle(entrypoint_id, decode_profile, d_kvst2,
                   cmd_kvst2.command_id, A.DMA_KIND.STORE, KV_BASE + KV_BYTES,
                   1, KV_BYTES_PER_TOKEN, KV_BYTES_PER_TOKEN)

    fill_attr = builder.fill_attr(A.DMA_FILL_RUNTIME_BOUND_SENTINEL)
    cmd_fill = stream.command(A.OPCODE.DMA_FILL, waits=(e_kvst2,),
                              operands=((t_sur, s_sur, a_pub, RW),),
                              attr_index=fill_attr)
    d_fill = builder.dma(cmd_fill, A.DMA_KIND.LOCAL_FILL,
                         src=sram_endpoint(t_sur, s_sur, 0, PUB_OFFSET),
                         dst=sram_endpoint(t_sur, s_sur, 0, PUB_OFFSET),
                         rows=1, row_bytes=OUTPUT_BYTES,
                         src_stride=OUTPUT_BYTES, dst_stride=OUTPUT_BYTES,
                         completion_event=e_fill)
    builder.oracle(entrypoint_id, publish_profile, d_fill,
                   cmd_fill.command_id, A.DMA_KIND.LOCAL_FILL, PUB_OFFSET, 1,
                   OUTPUT_BYTES, OUTPUT_BYTES)

    cmd_out = stream.command(A.OPCODE.DMA_STORE, waits=(e_fill,),
                             operands=((t_out, s_out, a_pub, RW),))
    d_out = builder.dma(cmd_out, A.DMA_KIND.STORE,
                        src=sram_endpoint(t_out, s_out, 0, PUB_OFFSET),
                        dst=hbm_endpoint(t_out, s_out, OUT_BASE),
                        rows=1, row_bytes=PUBLISH_CHUNK_BYTES,
                        src_stride=PUBLISH_CHUNK_BYTES,
                        dst_stride=PUBLISH_CHUNK_BYTES,
                        completion_event=e_out)
    builder.oracle(entrypoint_id, publish_profile, d_out,
                   cmd_out.command_id, A.DMA_KIND.STORE, OUT_BASE, 1,
                   PUBLISH_CHUNK_BYTES, PUBLISH_CHUNK_BYTES)

    cmd_out2 = stream.command(A.OPCODE.DMA_STORE, waits=(e_fill,),
                              operands=((t_out, s_out, a_pub, RW),))
    d_out2 = builder.dma(cmd_out2, A.DMA_KIND.STORE,
                         src=sram_endpoint(t_out, s_out, 0,
                                           PUB_OFFSET + PUBLISH_CHUNK_BYTES),
                         dst=hbm_endpoint(t_out, s_out,
                                          OUT_BASE + PUBLISH_CHUNK_BYTES),
                         rows=1, row_bytes=PUBLISH_CHUNK_BYTES,
                         src_stride=PUBLISH_CHUNK_BYTES,
                         dst_stride=PUBLISH_CHUNK_BYTES,
                         completion_event=e_out2)
    builder.oracle(entrypoint_id, publish_profile, d_out2,
                   cmd_out2.command_id, A.DMA_KIND.STORE,
                   OUT_BASE + PUBLISH_CHUNK_BYTES, 1, PUBLISH_CHUNK_BYTES,
                   PUBLISH_CHUNK_BYTES)

    stream.command(A.OPCODE.REQUEST_END, waits=(e_out, e_out2),
                   signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))

    program = builder.build()
    return dataclasses.replace(
        program,
        required_features=A.AGENT_SERVING_V1,
        agent_request_profiles=[_request_profile(symbols)],
        agent_instance_profiles=_instance_profiles(symbols),
        agent_source_core_map=[AgentSourceCoreMap(core_id=0, reserved=0)],
        agent_instance_member_bindings=_member_bindings(symbols),
        agent_request_binding_requirements=_requirements(symbols),
        agent_publish_surrogate_bindings=[
            AgentPublishSurrogateBinding(
                instance_profile_id=13, member_ordinal=0, reserved0=0,
                allocation_id=a_pub, producer_command_id=cmd_fill.command_id,
                completion_event_id=e_fill, fill_kind=1, allocation_role=1,
                digest_source=0, reserved1=0),
        ],
    )


def _request_profile(symbols):
    return AgentRequestProfile(
        program_id=1, profile_id=1, flags=A.AGENT_REQUEST_FLAGS.HAS_KV,
        reserved0=0, requested_profile_key=0,
        delta_input_tokens=PREFILL_TOKENS,
        full_input_tokens=PREFILL_TOKENS, expected_cached_tokens=0,
        output_tokens=DECODE_CHUNK_TOKENS,
        input_binding_bytes=INPUT_BINDING_BYTES,
        delta_input_dma_bytes=INPUT_BINDING_BYTES,
        full_input_dma_bytes=INPUT_BINDING_BYTES,
        host_output_bytes=HOST_OUTPUT_BYTES,
        primary_input_symbol_id=symbols["input"],
        primary_output_symbol_id=symbols["output"],
        primary_kv_symbol_id=symbols["kv"], source_rank_count=1,
        source_core_map_begin=0, path_mask=0b001,
        kv_bytes_per_token=KV_BYTES_PER_TOKEN,
        publish_chunk_bytes=PUBLISH_CHUNK_BYTES)


def _instance_profiles(symbols):
    return [
        AgentInstanceProfile(
            instance_profile_id=11, request_program_id=1,
            request_profile_id=1,
            path_kind=A.PATH_KIND.INITIAL_PREFILL, phase=A.PHASE.PREFILL,
            member_count=1, decode_chunk_tokens=0, mesh_entrypoint_id=1,
            mesh_profile_id=1, valid_tokens_per_member=PREFILL_TOKENS,
            kv_tokens_before=0, local_padded_members=0,
            local_padded_tokens_per_member=0,
            primary_input_symbol_id=symbols["input"],
            primary_output_symbol_id=0,
            primary_kv_symbol_id=symbols["kv"], flags=0,
            member_binding_first=0, member_binding_count=1,
            host_input_dma_bytes_per_member=INPUT_BINDING_BYTES,
            host_output_dma_bytes_per_member=0,
            kv_read_bytes_per_member=0,
            kv_write_bytes_per_member=KV_BYTES),
        AgentInstanceProfile(
            instance_profile_id=12, request_program_id=1,
            request_profile_id=1,
            path_kind=A.PATH_KIND.INITIAL_PREFILL, phase=A.PHASE.DECODE,
            member_count=1, decode_chunk_tokens=DECODE_CHUNK_TOKENS,
            mesh_entrypoint_id=1, mesh_profile_id=2,
            valid_tokens_per_member=DECODE_CHUNK_TOKENS,
            kv_tokens_before=PREFILL_TOKENS, local_padded_members=0,
            local_padded_tokens_per_member=0, primary_input_symbol_id=0,
            primary_output_symbol_id=0,
            primary_kv_symbol_id=symbols["kv"], flags=0,
            member_binding_first=1, member_binding_count=1,
            host_input_dma_bytes_per_member=0,
            host_output_dma_bytes_per_member=0,
            kv_read_bytes_per_member=KV_BYTES,
            kv_write_bytes_per_member=DECODE_CHUNK_TOKENS *
            KV_BYTES_PER_TOKEN),
        AgentInstanceProfile(
            instance_profile_id=13, request_program_id=1,
            request_profile_id=1,
            path_kind=A.PATH_KIND.INITIAL_PREFILL, phase=A.PHASE.PUBLISH,
            member_count=1, decode_chunk_tokens=0, mesh_entrypoint_id=1,
            mesh_profile_id=3, valid_tokens_per_member=0,
            kv_tokens_before=PREFILL_TOKENS + DECODE_CHUNK_TOKENS,
            local_padded_members=0, local_padded_tokens_per_member=0,
            primary_input_symbol_id=0,
            primary_output_symbol_id=symbols["output"],
            primary_kv_symbol_id=0, flags=0, member_binding_first=2,
            member_binding_count=1, host_input_dma_bytes_per_member=0,
            host_output_dma_bytes_per_member=HOST_OUTPUT_BYTES,
            kv_read_bytes_per_member=0, kv_write_bytes_per_member=0),
    ]


def _member_bindings(symbols):
    return [
        AgentInstanceMemberBinding(
            instance_profile_id=11, member_ordinal=0, reserved0=0,
            static_input_symbol_id=symbols["in_slot_0"],
            static_output_symbol_id=0,
            static_kv_symbol_id=symbols["kv_slot_0"],
            expected_logical_source_rank=0),
        AgentInstanceMemberBinding(
            instance_profile_id=12, member_ordinal=0, reserved0=0,
            static_input_symbol_id=0, static_output_symbol_id=0,
            static_kv_symbol_id=symbols["kv_slot_0"],
            expected_logical_source_rank=0),
        AgentInstanceMemberBinding(
            instance_profile_id=13, member_ordinal=0, reserved0=0,
            static_input_symbol_id=0,
            static_output_symbol_id=symbols["out_slot_0"],
            static_kv_symbol_id=0, expected_logical_source_rank=0),
    ]


def _requirements(symbols):
    records = [
        AgentRequestBindingRequirement(
            request_program_id=1, request_profile_id=1, binding_kind=1,
            binding_flags=1, symbol_id=symbols["input"], reserved=0),
        AgentRequestBindingRequirement(
            request_program_id=1, request_profile_id=1, binding_kind=4,
            binding_flags=5, symbol_id=symbols["weight"], reserved=0),
        AgentRequestBindingRequirement(
            request_program_id=1, request_profile_id=1, binding_kind=2,
            binding_flags=2, symbol_id=symbols["output"], reserved=0),
        AgentRequestBindingRequirement(
            request_program_id=1, request_profile_id=1, binding_kind=3,
            binding_flags=15, symbol_id=symbols["kv"], reserved=0),
    ]
    return sorted(records, key=lambda record: (
        record.request_program_id, record.request_profile_id,
        record.symbol_id))


def keyed_serving_program(arch):
    from mesh_ir.serving_profiles import apply_request_profile_keys

    return apply_request_profile_keys(serving_program(arch))


def dual_member_ranks_program(arch, ranks):
    program = serving_program(arch)
    strings = list(program.strings)
    relocations = list(program.relocations)

    def sid_of(name):
        for index, entry in enumerate(program.strings):
            if entry.value == name:
                return index + 1
        raise KeyError(name)

    def tensor_of(name):
        symbol = sid_of(name)
        return next(r.tensor_id for r in program.relocations
                    if r.symbol_sid == symbol)

    def add_slot(name, tensor_id, offset):
        strings.append(type(program.strings[0])(name))
        symbol = len(strings)
        relocations.append(type(program.relocations[0])(
            relocation_id=len(relocations) + 1, symbol_sid=symbol,
            kind=A.RELOCATION_KIND.TENSOR_BASE, region_id=HBM_REGION,
            tensor_id=tensor_id, reserved=0, offset_bytes=offset,
            reserved2=0))
        return symbol

    in_1 = add_slot("in_slot_1", tensor_of("input"), IN_BASE)
    kv_1 = add_slot("kv_slot_1", tensor_of("kv"), KV_BASE)
    out_1 = add_slot("out_slot_1", tensor_of("output"), OUT_BASE)
    slots = {
        A.PHASE.PREFILL: ((sid_of("in_slot_0"), sid_of("kv_slot_0")),
                          (in_1, kv_1)),
        A.PHASE.DECODE: ((0, sid_of("kv_slot_0")), (0, kv_1)),
        A.PHASE.PUBLISH: ((0, sid_of("out_slot_0")), (0, out_1)),
    }
    bindings = []
    instances = []
    first = 0
    for instance in program.agent_instance_profiles:
        rows = []
        for ordinal in range(2):
            input_symbol, kv_symbol = slots[instance.phase][ordinal]
            if instance.phase == A.PHASE.PUBLISH:
                output_symbol = sid_of("out_slot_0") if ordinal == 0 else out_1
            else:
                output_symbol = 0
            rows.append(AgentInstanceMemberBinding(
                instance_profile_id=instance.instance_profile_id,
                member_ordinal=ordinal, reserved0=0,
                static_input_symbol_id=input_symbol,
                static_output_symbol_id=output_symbol,
                static_kv_symbol_id=kv_symbol,
                expected_logical_source_rank=ranks[ordinal]))
        bindings.extend(rows)
        instances.append(dataclasses.replace(
            instance, member_count=2, member_binding_first=first,
            member_binding_count=2))
        first += 2
    request = dataclasses.replace(program.agent_request_profiles[0],
                                 source_rank_count=2)
    return dataclasses.replace(
        program,
        strings=strings,
        relocations=relocations,
        agent_request_profiles=[request],
        agent_instance_profiles=instances,
        agent_source_core_map=[AgentSourceCoreMap(core_id=0, reserved=0),
                               AgentSourceCoreMap(core_id=1, reserved=0)],
        agent_instance_member_bindings=bindings,
    )


def full_view_serving_program(arch):
    base = serving_program(arch)
    branches = base.commands[1:-2]
    terminal_start = len(base.commands) - 1
    final_end = terminal_start + 2 * len(branches)
    original_ids = {c.command_id: c.command_id for c in base.commands}
    original_ids[base.commands[-2].command_id] = final_end
    original_ids[base.commands[-1].command_id] = final_end + 1
    producers = {c.command_id for c in branches}
    produced = sorted({e.event_id for e in base.events
                       if e.producer_command_id in producers})
    max_event = max(e.event_id for e in base.events)
    branch_maps = []
    for number in range(2):
        branch_maps.append((
            {c.command_id: terminal_start + number * len(branches) + index
             for index, c in enumerate(branches)},
            {event: max_event + number * len(produced) + index + 1
             for index, event in enumerate(produced)},
        ))

    allocations = list(base.allocations)
    shards = list(base.shards)
    events = [dataclasses.replace(
        e, producer_command_id=original_ids.get(e.producer_command_id,
                                                e.producer_command_id))
        for e in base.events]
    descriptors = list(base.dma_descriptors)
    traffic = list(base.expected_traffic)
    relocations = list(base.relocations)
    strings = list(base.strings)
    commands = []
    waits = []
    operands = []

    def add_command(source, command_map, event_map, allocation_shift=0,
                    shard_shift=0, extra_waits=()):
        command = dataclasses.replace(
            source, command_id=command_map[source.command_id],
            signal_event=event_map.get(source.signal_event,
                                       source.signal_event),
            wait_begin=len(waits), operand_begin=len(operands))
        source_waits = base.command_waits[
            source.wait_begin:source.wait_begin + source.wait_count]
        waits.extend(dataclasses.replace(
            w, event_id=event_map.get(w.event_id, w.event_id))
            for w in source_waits)
        waits.extend(dataclasses.replace(base.command_waits[0], event_id=event)
                     for event in extra_waits)
        command = dataclasses.replace(command,
                                      wait_count=command.wait_count +
                                      len(extra_waits))
        source_operands = base.command_operands[
            source.operand_begin:source.operand_begin + source.operand_count]
        operands.extend(
            dataclasses.replace(
                o,
                allocation_id=o.allocation_id + allocation_shift
                if o.allocation_id else 0,
                shard_id=o.shard_id + shard_shift if o.shard_id else 0)
            for o in source_operands)
        commands.append(command)

    for command in base.commands[:-2]:
        add_command(command, original_ids, {})

    new_slots = {}
    for field in ("static_input_symbol_id", "static_output_symbol_id",
                  "static_kv_symbol_id"):
        source_symbol = next(getattr(b, field)
                             for b in base.agent_instance_member_bindings
                             if getattr(b, field))
        relocation = next(r for r in base.relocations
                          if r.symbol_sid == source_symbol)
        strings.append(StringEntry("full_view_ordinal1_" + field))
        new_slots[field] = len(strings)
        relocations.append(dataclasses.replace(
            relocation, relocation_id=len(relocations) + 1,
            symbol_sid=len(strings),
            offset_bytes=relocation.offset_bytes + 4096))

    request = base.agent_request_profiles[0]
    member_tensors = {
        r.tensor_id for r in base.relocations
        if r.symbol_sid in (request.primary_input_symbol_id,
                            request.primary_output_symbol_id,
                            request.primary_kv_symbol_id)}

    for number, (command_map, event_map) in enumerate(branch_maps):
        allocation_shift = (number + 1) * len(base.allocations)
        shard_shift = (number + 1) * len(base.shards)
        sram_shift = (number + 1) * 0x10000
        member_shift = number * 4096
        for allocation in base.allocations:
            allocations.append(dataclasses.replace(
                allocation,
                allocation_id=allocation.allocation_id + allocation_shift,
                offset_bytes=allocation.offset_bytes + sram_shift))
        for shard in base.shards:
            shards.append(dataclasses.replace(
                shard, shard_id=shard.shard_id + shard_shift,
                allocation_id=shard.allocation_id + allocation_shift))
        for event in base.events:
            if event.event_id in event_map:
                events.append(dataclasses.replace(
                    event, event_id=event_map[event.event_id],
                    producer_command_id=command_map[event.producer_command_id]))
        for command in branches:
            add_command(command, command_map, event_map, allocation_shift,
                        shard_shift)
        for descriptor in base.dma_descriptors:
            endpoints = {}
            for side in ("src", "dst"):
                endpoint = getattr(descriptor, side)
                if endpoint.memory_space == MEMORY_SPACE_CORE_SRAM:
                    shift = sram_shift
                else:
                    shift = member_shift if endpoint.tensor_id in \
                        member_tensors else 0
                endpoints[side] = dataclasses.replace(
                    endpoint,
                    shard_id=endpoint.shard_id + shard_shift
                    if endpoint.shard_id else 0,
                    offset_bytes=endpoint.offset_bytes + shift)
            new = dataclasses.replace(
                descriptor, descriptor_id=len(descriptors) + 1,
                command_id=command_map[descriptor.command_id],
                completion_event=event_map[descriptor.completion_event],
                **endpoints)
            descriptors.append(new)
            row = next(r for r in base.expected_traffic
                       if r.descriptor_id == descriptor.descriptor_id)
            traffic.append(dataclasses.replace(
                row, descriptor_id=new.descriptor_id,
                command_id=new.command_id, profile_id=row.profile_id + 3))

    end_command = base.commands[-2]
    end_waits = [w.event_id for w in base.command_waits[
        end_command.wait_begin:end_command.wait_begin + end_command.wait_count]]
    add_command(end_command, original_ids, {}, extra_waits=tuple(
        event_map[event] for _, event_map in branch_maps for event in end_waits))
    add_command(base.commands[-1], original_ids, {})

    instances = list(base.agent_instance_profiles)
    members = list(base.agent_instance_member_bindings)
    publishes = list(base.agent_publish_surrogate_bindings)
    for instance in base.agent_instance_profiles:
        new_id = instance.instance_profile_id + 100
        instances.append(dataclasses.replace(
            instance, instance_profile_id=new_id,
            mesh_profile_id=instance.mesh_profile_id + 3, member_count=2,
            member_binding_first=len(members), member_binding_count=2))
        source = base.agent_instance_member_bindings[
            instance.member_binding_first]
        members.append(dataclasses.replace(
            source, instance_profile_id=new_id, member_ordinal=0))
        fields = {field: new_slots[field] if getattr(source, field) else 0
                  for field in new_slots}
        members.append(dataclasses.replace(
            source, instance_profile_id=new_id, member_ordinal=1, **fields))
        if instance.phase == A.PHASE.PUBLISH:
            original = next(r for r in base.agent_publish_surrogate_bindings
                            if r.instance_profile_id ==
                            instance.instance_profile_id)
            for number, (command_map, event_map) in enumerate(branch_maps):
                publishes.append(dataclasses.replace(
                    original, instance_profile_id=new_id,
                    member_ordinal=number,
                    allocation_id=original.allocation_id +
                    (number + 1) * len(base.allocations),
                    producer_command_id=command_map[original.producer_command_id],
                    completion_event_id=event_map[original.completion_event_id]))

    return dataclasses.replace(
        base, strings=strings, relocations=relocations,
        allocations=allocations, shards=shards, commands=commands,
        command_waits=waits, command_operands=operands, events=events,
        dma_descriptors=descriptors, expected_traffic=traffic,
        streams=[dataclasses.replace(s, command_count=len(commands))
                 for s in base.streams],
        entrypoints=[dataclasses.replace(e, profile_count=6)
                     for e in base.entrypoints],
        profiles=base.profiles + [
            dataclasses.replace(r, profile_id=r.profile_id + 3)
            for r in base.profiles],
        agent_instance_profiles=instances,
        agent_instance_member_bindings=members,
        agent_publish_surrogate_bindings=publishes)



def full_view_alias_program(program):
    slot = next(r for r in program.relocations
                if program.strings[r.symbol_sid - 1].value ==
                "full_view_ordinal1_static_input_symbol_id")
    return dataclasses.replace(
        program,
        relocations=[
            dataclasses.replace(record,
                                offset_bytes=record.offset_bytes -
                                FULL_VIEW_SLOT_SHIFT)
            if record.symbol_sid == slot.symbol_sid else record
            for record in program.relocations])


def full_view_crossed_publish_program(program):
    records = list(program.agent_publish_surrogate_bindings)
    dual = [record for record in records
            if record.instance_profile_id == 113]
    if len(dual) != 2:
        raise ValueError("full view PUBLISH needs two member ordinals")
    crossed = []
    for record in records:
        if record.instance_profile_id != 113:
            crossed.append(record)
            continue
        other = dual[1 - record.member_ordinal]
        crossed.append(dataclasses.replace(
            record, allocation_id=other.allocation_id,
            producer_command_id=other.producer_command_id,
            completion_event_id=other.completion_event_id))
    return dataclasses.replace(program,
                               agent_publish_surrogate_bindings=crossed)

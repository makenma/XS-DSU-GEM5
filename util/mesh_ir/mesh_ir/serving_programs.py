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


def serving_program(arch, output_tokens=1, kv_splits=1):
    return _serving_program(arch, output_tokens, dual=False, kv_splits=kv_splits)


def split_kv_serving_program(arch):
    return _serving_program(arch, 2, dual=False, kv_splits=2)


def keyed_serving_program(arch):
    from mesh_ir.serving_profiles import apply_request_profile_keys

    return apply_request_profile_keys(serving_program(arch))


def full_view_serving_program(arch):
    return _serving_program(arch, 1, dual=True)


def _serving_program(arch, output_tokens, dual, kv_splits=1):
    from mesh_ir.builder import ProgramBuilder

    output_bytes = output_tokens * OUTPUT_BYTES
    kv_alloc_bytes = (PREFILL_TOKENS + output_tokens) * KV_BYTES_PER_TOKEN
    chunk_count = (output_bytes + PUBLISH_CHUNK_BYTES - 1) // \
        PUBLISH_CHUNK_BYTES
    builder = ProgramBuilder(arch, "gate6_serving")
    align = arch.sram_base_alignment_bytes

    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16,
                          A.STORAGE_CLASS.EXTERNAL, RO, (HIDDEN, INTERMEDIATE))
    t_w = builder.tensor("weight", A.TENSOR_ROLE.WEIGHT, FP16,
                         A.STORAGE_CLASS.EXTERNAL, RO,
                         (INTERMEDIATE, HIDDEN))
    t_kv = builder.tensor("kv", A.TENSOR_ROLE.KV_CACHE, A.DTYPE.INT8,
                          A.STORAGE_CLASS.EXTERNAL, RW, (kv_alloc_bytes,))
    t_act = builder.tensor("activation", A.TENSOR_ROLE.ACTIVATION, FP16,
                           A.STORAGE_CLASS.CORE_SRAM, RW,
                           (HIDDEN, INTERMEDIATE))
    t_sur = builder.tensor("surrogate", A.TENSOR_ROLE.ACTIVATION, FP16,
                           A.STORAGE_CLASS.CORE_SRAM, RW,
                           (HIDDEN, INTERMEDIATE * output_tokens))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16,
                           A.STORAGE_CLASS.EXTERNAL, RW,
                           (HIDDEN, INTERMEDIATE * output_tokens))

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

    ordinal_one = {}
    if dual:
        for field, slot, tensor, offset in (
                ("static_input_symbol_id", "in_slot_0", t_in, IN_BASE),
                ("static_output_symbol_id", "out_slot_0", t_out, OUT_BASE),
                ("static_kv_symbol_id", "kv_slot_0", t_kv, KV_BASE)):
            name = "full_view_ordinal1_" + field
            builder.relocation(name, A.RELOCATION_KIND.TENSOR_BASE,
                               HBM_REGION, tensor,
                               offset + FULL_VIEW_SLOT_SHIFT)
            ordinal_one[field] = builder.string(name)

    def make_set(sram_shift):
        allocations = {
            "in": builder.allocation(0, IN_OFFSET + sram_shift,
                                     INPUT_BINDING_BYTES, align),
            "w": builder.allocation(0, W_OFFSET + sram_shift,
                                    WEIGHT_BYTES, align),
            "kv": builder.allocation(0, KV_OFFSET + sram_shift,
                                     kv_alloc_bytes, align),
            "act": builder.allocation(0, ACT_OFFSET + sram_shift,
                                      OUTPUT_BYTES, align),
            "pub": builder.allocation(0, PUB_OFFSET + sram_shift,
                                      output_bytes, align),
        }
        shards = {
            "in": builder.shard(t_in, 0, allocations["in"],
                                (HIDDEN, INTERMEDIATE),
                                INPUT_BINDING_BYTES),
            "w": builder.shard(t_w, 0, allocations["w"],
                               (INTERMEDIATE, HIDDEN), WEIGHT_BYTES),
            "kv": builder.shard(t_kv, 0, allocations["kv"],
                                (kv_alloc_bytes,), kv_alloc_bytes),
            "act": builder.shard(t_act, 0, allocations["act"],
                                 (HIDDEN, INTERMEDIATE), OUTPUT_BYTES),
            "sur": builder.shard(t_sur, 0, allocations["pub"],
                                 (HIDDEN, INTERMEDIATE * output_tokens),
                                 output_bytes),
            "out": builder.shard(t_out, 0, allocations["pub"],
                                 (HIDDEN, INTERMEDIATE * output_tokens),
                                 output_bytes),
        }
        return {"allocations": allocations, "shards": shards,
                "sram_shift": sram_shift}

    set0 = make_set(0)
    set1 = make_set(0x10000) if dual else None
    set2 = make_set(0x20000) if dual else None

    stream = builder.stream(
        0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE |
        A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    fill_attr = builder.fill_attr(A.DMA_FILL_RUNTIME_BOUND_SENTINEL)

    def alloc_of(member, kind):
        return member["allocations"][kind]

    def shard_of(member, kind):
        return member["shards"][kind]

    def sram(tensor, member, kind, offset):
        return sram_endpoint(tensor, shard_of(member, kind), 0,
                             offset + member["sram_shift"])

    def emit_prefill(profile, entrypoint, members):
        e_begin = builder.event()
        stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin,
                       profile_id=profile)
        terminals = []
        for member in members:
            shift = member["hbm_shift"]
            e_in = builder.event()
            e_w = builder.event()
            e_gemm = builder.event()
            e_fill = builder.event()
            cmd_in = stream.command(
                A.OPCODE.DMA_LOAD, waits=(e_begin,),
                operands=((t_in, shard_of(member, "in"),
                           alloc_of(member, "in"), RO),), profile_id=profile)
            d_in = builder.dma(
                cmd_in, A.DMA_KIND.LOAD,
                src=hbm_endpoint(t_in, shard_of(member, "in"),
                                 IN_BASE + shift),
                dst=sram(t_in, member, "in", IN_OFFSET),
                rows=1, row_bytes=INPUT_BINDING_BYTES,
                src_stride=INPUT_BINDING_BYTES,
                dst_stride=INPUT_BINDING_BYTES, completion_event=e_in)
            builder.oracle(entrypoint, profile, d_in, cmd_in.command_id,
                           A.DMA_KIND.LOAD, IN_BASE + shift, 1,
                           INPUT_BINDING_BYTES, INPUT_BINDING_BYTES)
            cmd_w = stream.command(
                A.OPCODE.DMA_LOAD, waits=(e_begin,),
                operands=((t_w, shard_of(member, "w"),
                           alloc_of(member, "w"), RO),), profile_id=profile)
            d_w = builder.dma(
                cmd_w, A.DMA_KIND.LOAD,
                src=hbm_endpoint(t_w, shard_of(member, "w"),
                                 W_BASE + shift),
                dst=sram(t_w, member, "w", W_OFFSET),
                rows=1, row_bytes=WEIGHT_BYTES,
                src_stride=WEIGHT_BYTES, dst_stride=WEIGHT_BYTES,
                completion_event=e_w)
            builder.oracle(entrypoint, profile, d_w, cmd_w.command_id,
                           A.DMA_KIND.LOAD, W_BASE + shift, 1,
                           WEIGHT_BYTES, WEIGHT_BYTES)
            gemm_attr = builder.gemm_attr(1, HIDDEN, INTERMEDIATE, HIDDEN,
                                          FP16, A.DTYPE.FP32)
            stream.command(
                A.OPCODE.GEMM, waits=(e_in, e_w),
                operands=((t_in, shard_of(member, "in"),
                           alloc_of(member, "in"), RO),
                          (t_w, shard_of(member, "w"),
                           alloc_of(member, "w"), RO),
                          (t_act, shard_of(member, "act"),
                           alloc_of(member, "act"), RW)),
                signal_event=e_gemm, attr_index=gemm_attr, profile_id=profile)
            cmd_fill = stream.command(
                A.OPCODE.DMA_FILL, waits=(e_gemm,),
                operands=((t_kv, shard_of(member, "kv"),
                           alloc_of(member, "kv"), RW),),
                attr_index=fill_attr, profile_id=profile)
            d_fill = builder.dma(
                cmd_fill, A.DMA_KIND.LOCAL_FILL,
                src=sram(t_kv, member, "kv", KV_OFFSET),
                dst=sram(t_kv, member, "kv", KV_OFFSET),
                rows=1, row_bytes=KV_BYTES, src_stride=KV_BYTES,
                dst_stride=KV_BYTES, completion_event=e_fill)
            builder.oracle(entrypoint, profile, d_fill, cmd_fill.command_id,
                           A.DMA_KIND.LOCAL_FILL,
                           KV_OFFSET + member["sram_shift"], 1, KV_BYTES,
                           KV_BYTES)
            part_bytes = KV_BYTES // kv_splits
            assert part_bytes * kv_splits == KV_BYTES
            assert part_bytes % KV_BYTES_PER_TOKEN == 0
            for part in range(kv_splits):
                part_offset = part * part_bytes
                part_event = builder.event()
                cmd_store = stream.command(
                    A.OPCODE.DMA_STORE, waits=(e_fill,),
                    operands=((t_kv, shard_of(member, "kv"),
                               alloc_of(member, "kv"), RW),), profile_id=profile)
                d_store = builder.dma(
                    cmd_store, A.DMA_KIND.STORE,
                    src=sram(t_kv, member, "kv", KV_OFFSET + part_offset),
                    dst=hbm_endpoint(t_kv, shard_of(member, "kv"),
                                     KV_BASE + shift + part_offset),
                    rows=1, row_bytes=part_bytes, src_stride=part_bytes,
                    dst_stride=part_bytes, completion_event=part_event)
                builder.oracle(entrypoint, profile, d_store,
                               cmd_store.command_id, A.DMA_KIND.STORE,
                               KV_BASE + shift + part_offset, 1, part_bytes,
                               part_bytes)
                terminals.append(part_event)
        e_end = builder.event()
        stream.command(A.OPCODE.REQUEST_END, waits=tuple(terminals),
                       signal_event=e_end, profile_id=profile)
        stream.command(A.OPCODE.HALT, waits=(e_end,), profile_id=profile)

    def emit_decode(profile, entrypoint, members, index):
        e_begin = builder.event()
        stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin,
                       profile_id=profile)
        prefix = PREFILL_TOKENS + index
        read_bytes = prefix * KV_BYTES_PER_TOKEN
        terminals = []
        for member in members:
            shift = member["hbm_shift"]
            e_load = builder.event()
            e_fill = builder.event()
            cmd_load = stream.command(
                A.OPCODE.DMA_LOAD, waits=(e_begin,),
                operands=((t_kv, shard_of(member, "kv"),
                           alloc_of(member, "kv"), RW),), profile_id=profile)
            d_load = builder.dma(
                cmd_load, A.DMA_KIND.LOAD,
                src=hbm_endpoint(t_kv, shard_of(member, "kv"),
                                 KV_BASE + shift),
                dst=sram(t_kv, member, "kv", KV_OFFSET),
                rows=1, row_bytes=read_bytes, src_stride=read_bytes,
                dst_stride=read_bytes, completion_event=e_load)
            builder.oracle(entrypoint, profile, d_load, cmd_load.command_id,
                           A.DMA_KIND.LOAD, KV_BASE + shift, 1, read_bytes,
                           read_bytes)
            append = KV_OFFSET + member["sram_shift"] + read_bytes
            cmd_fill = stream.command(
                A.OPCODE.DMA_FILL, waits=(e_load,),
                operands=((t_kv, shard_of(member, "kv"),
                           alloc_of(member, "kv"), RW),),
                attr_index=fill_attr, profile_id=profile)
            d_fill = builder.dma(
                cmd_fill, A.DMA_KIND.LOCAL_FILL,
                src=sram_endpoint(t_kv, shard_of(member, "kv"), 0, append),
                dst=sram_endpoint(t_kv, shard_of(member, "kv"), 0, append),
                rows=1, row_bytes=KV_BYTES_PER_TOKEN,
                src_stride=KV_BYTES_PER_TOKEN,
                dst_stride=KV_BYTES_PER_TOKEN, completion_event=e_fill)
            builder.oracle(entrypoint, profile, d_fill, cmd_fill.command_id,
                           A.DMA_KIND.LOCAL_FILL, append, 1,
                           KV_BYTES_PER_TOKEN, KV_BYTES_PER_TOKEN)
            cmd_store = stream.command(
                A.OPCODE.DMA_STORE, waits=(e_fill,),
                operands=((t_kv, shard_of(member, "kv"),
                           alloc_of(member, "kv"), RW),), profile_id=profile)
            e_store = builder.event()
            d_store = builder.dma(
                cmd_store, A.DMA_KIND.STORE,
                src=sram_endpoint(t_kv, shard_of(member, "kv"), 0, append),
                dst=hbm_endpoint(t_kv, shard_of(member, "kv"),
                                 KV_BASE + shift + read_bytes),
                rows=1, row_bytes=KV_BYTES_PER_TOKEN,
                src_stride=KV_BYTES_PER_TOKEN,
                dst_stride=KV_BYTES_PER_TOKEN, completion_event=e_store)
            builder.oracle(entrypoint, profile, d_store, cmd_store.command_id,
                           A.DMA_KIND.STORE, KV_BASE + shift + read_bytes, 1,
                           KV_BYTES_PER_TOKEN, KV_BYTES_PER_TOKEN)
            terminals.append(e_store)
        e_end = builder.event()
        stream.command(A.OPCODE.REQUEST_END, waits=tuple(terminals),
                       signal_event=e_end, profile_id=profile)
        stream.command(A.OPCODE.HALT, waits=(e_end,), profile_id=profile)

    def emit_publish(profile, entrypoint, members):
        e_begin = builder.event()
        stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin,
                       profile_id=profile)
        producers = []
        terminals = []
        for member in members:
            shift = member["hbm_shift"]
            e_fill = builder.event()
            cmd_fill = stream.command(
                A.OPCODE.DMA_FILL, waits=(e_begin,),
                operands=((t_sur, shard_of(member, "sur"),
                           alloc_of(member, "pub"), RW),),
                attr_index=fill_attr, profile_id=profile)
            d_fill = builder.dma(
                cmd_fill, A.DMA_KIND.LOCAL_FILL,
                src=sram(t_sur, member, "sur", PUB_OFFSET),
                dst=sram(t_sur, member, "sur", PUB_OFFSET),
                rows=1, row_bytes=output_bytes, src_stride=output_bytes,
                dst_stride=output_bytes, completion_event=e_fill)
            builder.oracle(entrypoint, profile, d_fill, cmd_fill.command_id,
                           A.DMA_KIND.LOCAL_FILL,
                           PUB_OFFSET + member["sram_shift"], 1, output_bytes,
                           output_bytes)
            for chunk in range(chunk_count):
                offset = chunk * PUBLISH_CHUNK_BYTES
                size = min(PUBLISH_CHUNK_BYTES, output_bytes - offset)
                e_out = builder.event()
                cmd_out = stream.command(
                    A.OPCODE.DMA_STORE, waits=(e_fill,),
                    operands=((t_out, shard_of(member, "out"),
                               alloc_of(member, "pub"), RW),),
                    profile_id=profile)
                d_out = builder.dma(
                    cmd_out, A.DMA_KIND.STORE,
                    src=sram(t_out, member, "out", PUB_OFFSET + offset),
                    dst=hbm_endpoint(t_out, shard_of(member, "out"),
                                     OUT_BASE + shift + offset),
                    rows=1, row_bytes=size, src_stride=size, dst_stride=size,
                    completion_event=e_out)
                builder.oracle(entrypoint, profile, d_out, cmd_out.command_id,
                               A.DMA_KIND.STORE, OUT_BASE + shift + offset, 1,
                               size, size)
                terminals.append(e_out)
            producers.append((alloc_of(member, "pub"), cmd_fill.command_id,
                              e_fill))
        e_end = builder.event()
        stream.command(A.OPCODE.REQUEST_END, waits=tuple(terminals),
                       signal_event=e_end, profile_id=profile)
        stream.command(A.OPCODE.HALT, waits=(e_end,), profile_id=profile)
        return producers

    def add_phase(name, dual_name):
        entrypoint = builder.entrypoint(name, name, 0, 0)
        profile = len(builder.profiles)
        dual_entrypoint = dual_profile = 0
        if dual:
            dual_entrypoint = builder.entrypoint(dual_name, dual_name, 0, 0)
            dual_profile = len(builder.profiles)
        return (entrypoint, profile), (dual_entrypoint, dual_profile)

    prefill_single, prefill_dual = add_phase("serving_prefill",
                                             "serving_prefill_ordinal1")
    decode_singles = []
    decode_duals = []
    for index in range(output_tokens):
        single, doubling = add_phase(f"serving_decode_{index}",
                                     f"serving_decode_{index}_ordinal1")
        decode_singles.append(single)
        decode_duals.append(doubling)
    publish_single, publish_dual = add_phase("serving_publish",
                                             "serving_publish_ordinal1")

    emit_prefill(prefill_single[1], prefill_single[0],
                 [dict(set0, hbm_shift=0)])
    for index in range(output_tokens):
        emit_decode(decode_singles[index][1], decode_singles[index][0],
                    [dict(set0, hbm_shift=0)], index)
    publish_producers = emit_publish(publish_single[1], publish_single[0],
                                     [dict(set0, hbm_shift=0)])

    instances = []
    bindings = []
    publishes = []

    def add_instance(instance_id, selector, phase, member_count,
                     kv_tokens_before):
        bindings_first = len(bindings)
        instances.append(_instance_profile(
            instance_id, selector, phase, member_count, bindings_first,
            kv_tokens_before, output_bytes, symbols))
        for ordinal in range(member_count):
            bindings.append(_member_binding(instance_id, ordinal, phase,
                                            symbols, ordinal_one))

    add_instance(11, prefill_single, A.PHASE.PREFILL, 1, 0)
    for index in range(output_tokens):
        add_instance(12 + index, decode_singles[index], A.PHASE.DECODE, 1,
                     PREFILL_TOKENS + index)
    add_instance(12 + output_tokens, publish_single, A.PHASE.PUBLISH, 1,
                 PREFILL_TOKENS + output_tokens)

    if dual:
        emit_prefill(prefill_dual[1], prefill_dual[0],
                     [dict(set1, hbm_shift=0),
                      dict(set2, hbm_shift=FULL_VIEW_SLOT_SHIFT)])
        for index in range(output_tokens):
            emit_decode(decode_duals[index][1], decode_duals[index][0],
                        [dict(set1, hbm_shift=0),
                         dict(set2, hbm_shift=FULL_VIEW_SLOT_SHIFT)], index)
        dual_publish = emit_publish(
            publish_dual[1], publish_dual[0],
            [dict(set1, hbm_shift=0),
             dict(set2, hbm_shift=FULL_VIEW_SLOT_SHIFT)])
        add_instance(111, prefill_dual, A.PHASE.PREFILL, 2, 0)
        for index in range(output_tokens):
            add_instance(112 + index, decode_duals[index], A.PHASE.DECODE, 2,
                         PREFILL_TOKENS + index)
        add_instance(112 + output_tokens, publish_dual, A.PHASE.PUBLISH, 2,
                     PREFILL_TOKENS + output_tokens)
        for ordinal in range(2):
            allocation, command, event = dual_publish[ordinal]
            publishes.append(AgentPublishSurrogateBinding(
                instance_profile_id=112 + output_tokens,
                member_ordinal=ordinal, reserved0=0,
                allocation_id=allocation, producer_command_id=command,
                completion_event_id=event, fill_kind=1, allocation_role=1,
                digest_source=0, reserved1=0))

    program = builder.build()
    publish_allocation, publish_command, publish_event = publish_producers[0]
    return dataclasses.replace(
        program,
        required_features=A.AGENT_SERVING_V1 |
        A.PROFILE_SCOPED_EXECUTION_V1,
        agent_request_profiles=[
            _request_profile(symbols, output_tokens, output_bytes)],
        agent_instance_profiles=instances,
        agent_source_core_map=[AgentSourceCoreMap(core_id=0, reserved=0)],
        agent_instance_member_bindings=bindings,
        agent_request_binding_requirements=_requirements(symbols),
        agent_publish_surrogate_bindings=[
            AgentPublishSurrogateBinding(
                instance_profile_id=12 + output_tokens, member_ordinal=0,
                reserved0=0, allocation_id=publish_allocation,
                producer_command_id=publish_command,
                completion_event_id=publish_event, fill_kind=1,
                allocation_role=1, digest_source=0, reserved1=0),
        ] + publishes,
    )


def _instance_profile(instance_id, selector, phase, member_count,
                      binding_first, kv_tokens_before, output_bytes, symbols):
    entrypoint, profile = selector
    if phase == A.PHASE.PREFILL:
        primary_input = symbols["input"]
        primary_output = 0
        primary_kv = symbols["kv"]
        host_input = INPUT_BINDING_BYTES
        host_output = 0
        kv_read = 0
        kv_write = KV_BYTES
        decode_chunk = 0
        valid_tokens = PREFILL_TOKENS
    elif phase == A.PHASE.DECODE:
        primary_input = 0
        primary_output = 0
        primary_kv = symbols["kv"]
        host_input = 0
        host_output = 0
        kv_read = kv_tokens_before * KV_BYTES_PER_TOKEN
        kv_write = DECODE_CHUNK_TOKENS * KV_BYTES_PER_TOKEN
        decode_chunk = DECODE_CHUNK_TOKENS
        valid_tokens = DECODE_CHUNK_TOKENS
    else:
        primary_input = 0
        primary_output = symbols["output"]
        primary_kv = 0
        host_input = 0
        host_output = output_bytes
        kv_read = 0
        kv_write = 0
        decode_chunk = 0
        valid_tokens = 0
    return AgentInstanceProfile(
        instance_profile_id=instance_id, request_program_id=1,
        request_profile_id=1, path_kind=A.PATH_KIND.INITIAL_PREFILL,
        phase=phase, member_count=member_count,
        decode_chunk_tokens=decode_chunk, mesh_entrypoint_id=entrypoint,
        mesh_profile_id=profile, valid_tokens_per_member=valid_tokens,
        kv_tokens_before=kv_tokens_before, local_padded_members=0,
        local_padded_tokens_per_member=0,
        primary_input_symbol_id=primary_input,
        primary_output_symbol_id=primary_output,
        primary_kv_symbol_id=primary_kv, flags=0,
        member_binding_first=binding_first,
        member_binding_count=member_count,
        host_input_dma_bytes_per_member=host_input,
        host_output_dma_bytes_per_member=host_output,
        kv_read_bytes_per_member=kv_read,
        kv_write_bytes_per_member=kv_write)


def _member_binding(instance_id, ordinal, phase, symbols, ordinal_one):
    fields = {"static_input_symbol_id": 0, "static_output_symbol_id": 0,
              "static_kv_symbol_id": 0}
    if ordinal == 0:
        fields["static_input_symbol_id"] = symbols["in_slot_0"]
        fields["static_output_symbol_id"] = symbols["out_slot_0"]
        fields["static_kv_symbol_id"] = symbols["kv_slot_0"]
    else:
        for field in fields:
            fields[field] = ordinal_one.get(field, 0)
    input_ok, output_ok, kv_ok = {
        A.PHASE.PREFILL: (True, False, True),
        A.PHASE.DECODE: (False, False, True),
        A.PHASE.PUBLISH: (False, True, False)}[phase]
    if not input_ok:
        fields["static_input_symbol_id"] = 0
    if not output_ok:
        fields["static_output_symbol_id"] = 0
    if not kv_ok:
        fields["static_kv_symbol_id"] = 0
    return AgentInstanceMemberBinding(
        instance_profile_id=instance_id, member_ordinal=ordinal, reserved0=0,
        expected_logical_source_rank=0, **fields)


def _request_profile(symbols, output_tokens=1, output_bytes=HOST_OUTPUT_BYTES):
    return AgentRequestProfile(
        program_id=1, profile_id=1, flags=A.AGENT_REQUEST_FLAGS.HAS_KV,
        reserved0=0, requested_profile_key=0,
        delta_input_tokens=PREFILL_TOKENS,
        full_input_tokens=PREFILL_TOKENS, expected_cached_tokens=0,
        output_tokens=output_tokens * DECODE_CHUNK_TOKENS,
        input_binding_bytes=INPUT_BINDING_BYTES,
        delta_input_dma_bytes=INPUT_BINDING_BYTES,
        full_input_dma_bytes=INPUT_BINDING_BYTES,
        host_output_bytes=output_bytes,
        primary_input_symbol_id=symbols["input"],
        primary_output_symbol_id=symbols["output"],
        primary_kv_symbol_id=symbols["kv"], source_rank_count=1,
        source_core_map_begin=0, path_mask=0b001,
        kv_bytes_per_token=KV_BYTES_PER_TOKEN,
        publish_chunk_bytes=PUBLISH_CHUNK_BYTES)


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

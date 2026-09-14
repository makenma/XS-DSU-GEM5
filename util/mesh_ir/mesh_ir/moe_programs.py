from __future__ import annotations

import hashlib

from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import (
    build_compute_timing_program,
    build_single_core_program,
    hbm_endpoint,
    peer_endpoint,
    sram_endpoint,
)
from mesh_ir.model import (
    ContentDigest,
    MoeDynamicRegion,
    MoeExpertSpec,
    MoeKernelSpec,
    MoeLayerSpec,
    Relocation,
)

WEIGHT_SEED = b"AI_MESH_GATE5_SYNTHETIC_WEIGHT_V1"
TENSOR_BYTES = 8192
HIDDEN = 32
INTERMEDIATE = 64
EXPERT_WEIGHT_BYTES = HIDDEN * INTERMEDIATE * 2
TOKEN_BYTES = HIDDEN * 2
OUTPUT_TOKEN_BYTES = INTERMEDIATE * 2
SCRATCH_OFFSET = 0x80000
SCRATCH_BYTES = 0x40000
INSERT_AFTER = 4
RESUME_BEFORE = 5
ENTRY_EVENT = 4

def synthetic_weight_digest() -> bytes:
    payload = WEIGHT_SEED * (TENSOR_BYTES // len(WEIGHT_SEED) + 1)
    return hashlib.sha256(payload[:TENSOR_BYTES]).digest()


DUAL_WEIGHT_TENSOR_SID = 3
DUAL_HIDDEN = 32
DUAL_INTERMEDIATE = 64
DUAL_TOKEN_BYTES = DUAL_HIDDEN * 2
DUAL_OUTPUT_BYTES = DUAL_INTERMEDIATE * 2
DUAL_EXPERT_BYTES = DUAL_HIDDEN * DUAL_INTERMEDIATE * 2
DUAL_ROWS = 1
DUAL_REGION_SCRATCH = (0x80000, 0x20000)
DUAL_REGION_COMMANDS = 32
DUAL_REGION_EVENTS = 16
DUAL_REGION_DESCRIPTORS = 32
DUAL_REGION_TRANSFERS = 8
DUAL_REGION_ALLOCATIONS = 8
DUAL_TOKEN_BASE = 0x100000
DUAL_OUTPUT_BASE = 0x200000


def moe_dual_program(arch, cross_core_gate=True, capacity_factor_q16=0x10000,
                     top_k=2):
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "gate5_moe_dual")
    entrypoint_id = builder.entrypoint("main", "moe_dual", lifecycle_core=0,
                                       lifecycle_stream=0)
    t_token = builder.tensor("token", A.TENSOR_ROLE.INPUT, A.DTYPE.FP16,
                             A.STORAGE_CLASS.HBM, A.ACCESS_KIND.READ_ONLY,
                             (DUAL_HIDDEN,), sharding_id=1)
    t_weight = builder.tensor("weight", A.TENSOR_ROLE.WEIGHT, A.DTYPE.FP16,
                              A.STORAGE_CLASS.EXTERNAL,
                              A.ACCESS_KIND.READ_ONLY,
                              (2 * DUAL_INTERMEDIATE, DUAL_HIDDEN),
                              sharding_id=1)
    t_partial = builder.tensor("partial", A.TENSOR_ROLE.ACTIVATION,
                               A.DTYPE.FP16, A.STORAGE_CLASS.CORE_SRAM,
                               A.ACCESS_KIND.READ_WRITE,
                               (DUAL_INTERMEDIATE,), sharding_id=2)
    t_output = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, A.DTYPE.FP16,
                              A.STORAGE_CLASS.HBM, A.ACCESS_KIND.READ_WRITE,
                              (DUAL_INTERMEDIATE,))
    builder.relocation("token", A.RELOCATION_KIND.TENSOR_BASE, 0, t_token,
                       DUAL_TOKEN_BASE)
    builder.relocation("weight", A.RELOCATION_KIND.TENSOR_BASE, 0, t_weight, 0)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, 0, t_output,
                       DUAL_OUTPUT_BASE)
    align = arch.sram_base_alignment_bytes
    a_token = builder.allocation(0, 0x0000, DUAL_TOKEN_BYTES, align)
    a_w0 = builder.allocation(0, 0x2000, DUAL_EXPERT_BYTES, align)
    a_part0 = builder.allocation(0, 0x4000, DUAL_OUTPUT_BYTES, align)
    a_peer = builder.allocation(0, 0x6000, DUAL_OUTPUT_BYTES, align)
    a_token1 = builder.allocation(1, 0x0000, DUAL_TOKEN_BYTES, align)
    a_w1 = builder.allocation(1, 0x2000, DUAL_EXPERT_BYTES, align)
    a_part1 = builder.allocation(1, 0x4000, DUAL_OUTPUT_BYTES, align)
    s_token = builder.shard(t_token, 0, a_token, (DUAL_HIDDEN,),
                            DUAL_TOKEN_BYTES, sharding_id=1)
    s_token1 = builder.shard(t_token, 1, a_token1, (DUAL_HIDDEN,),
                             DUAL_TOKEN_BYTES, sharding_id=1)
    s_w0 = builder.shard(t_weight, 0, a_w0,
                         (DUAL_INTERMEDIATE, DUAL_HIDDEN), DUAL_EXPERT_BYTES,
                         sharding_id=1)
    s_w1 = builder.shard(t_weight, 1, a_w1,
                         (DUAL_INTERMEDIATE, DUAL_HIDDEN), DUAL_EXPERT_BYTES,
                         sharding_id=1)
    s_part0 = builder.shard(t_partial, 0, a_part0, (DUAL_INTERMEDIATE,),
                            DUAL_OUTPUT_BYTES, sharding_id=2)
    s_peer = builder.shard(t_partial, 0, a_peer, (DUAL_INTERMEDIATE,),
                           DUAL_OUTPUT_BYTES, sharding_id=3)
    s_part1 = builder.shard(t_partial, 1, a_part1, (DUAL_INTERMEDIATE,),
                            DUAL_OUTPUT_BYTES, sharding_id=2)
    s_output = builder.shard(t_output, 0, a_part0, (DUAL_INTERMEDIATE,),
                             DUAL_OUTPUT_BYTES)
    s_output1 = builder.shard(t_output, 1, a_part1, (DUAL_INTERMEDIATE,),
                              DUAL_OUTPUT_BYTES)
    stream0 = builder.stream(
        0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE |
        A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    events = [builder.event() for _ in range(12)]
    e_begin, e_token, e_w0, e_gemm0, e_recv, e_reduce, e_store, e_end = \
        events[0:8]
    e_token1, e_w1, e_gemm1, e_push = events[8:12]

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    load_token = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_token, s_token, a_token, A.ACCESS_KIND.READ_ONLY),))
    d_token = builder.dma(
        load_token, A.DMA_KIND.LOAD,
        src=hbm_endpoint(t_token, s_token, DUAL_TOKEN_BASE),
        dst=sram_endpoint(t_token, s_token, 0, 0x0000),
        rows=DUAL_ROWS, row_bytes=DUAL_TOKEN_BYTES,
        src_stride=DUAL_TOKEN_BYTES, dst_stride=DUAL_TOKEN_BYTES,
        completion_event=e_token)
    builder.oracle(entrypoint_id, 1, d_token, load_token.command_id,
                   A.DMA_KIND.LOAD, DUAL_TOKEN_BASE, DUAL_ROWS,
                   DUAL_TOKEN_BYTES, DUAL_TOKEN_BYTES)
    load_w0 = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_weight, s_w0, a_w0, A.ACCESS_KIND.READ_ONLY),))
    d_w0 = builder.dma(
        load_w0, A.DMA_KIND.LOAD,
        src=hbm_endpoint(t_weight, s_w0, 0),
        dst=sram_endpoint(t_weight, s_w0, 0, 0x2000),
        rows=DUAL_ROWS, row_bytes=DUAL_EXPERT_BYTES,
        src_stride=DUAL_EXPERT_BYTES, dst_stride=DUAL_EXPERT_BYTES,
        completion_event=e_w0)
    builder.oracle(entrypoint_id, 1, d_w0, load_w0.command_id,
                   A.DMA_KIND.LOAD, 0, DUAL_ROWS, DUAL_EXPERT_BYTES,
                   DUAL_EXPERT_BYTES)
    gemm0 = stream0.command(
        A.OPCODE.GEMM, waits=(e_token, e_w0),
        operands=((t_token, s_token, a_token, A.ACCESS_KIND.READ_ONLY),
                  (t_weight, s_w0, a_w0, A.ACCESS_KIND.READ_ONLY),
                  (t_partial, s_part0, a_part0, A.ACCESS_KIND.READ_WRITE)),
        signal_event=e_gemm0,
        attr_index=builder.gemm_attr(1, 1, DUAL_INTERMEDIATE, DUAL_HIDDEN,
                                     A.DTYPE.FP16, A.DTYPE.FP32))
    recv_waits = (e_gemm0, e_push) if cross_core_gate else (e_gemm0,)
    recv = stream0.command(
        A.OPCODE.RECV_WAIT, waits=recv_waits,
        operands=((t_partial, s_peer, a_peer, A.ACCESS_KIND.READ_WRITE),),
        signal_event=e_recv, attr_index=builder.recv_wait_attr(1))
    stream0.command(
        A.OPCODE.LOCAL_REDUCE, waits=(e_recv,),
        operands=((t_partial, s_peer, a_peer, A.ACCESS_KIND.READ_WRITE),
                  (t_partial, s_part0, a_part0, A.ACCESS_KIND.READ_WRITE)),
        signal_event=e_reduce,
        attr_index=builder.reduce_attr(DUAL_INTERMEDIATE, A.DTYPE.FP16,
                                       A.DTYPE.FP32, fan_in=2))
    store = stream0.command(
        A.OPCODE.DMA_STORE, waits=(e_reduce,),
        operands=((t_output, s_output, a_part0, A.ACCESS_KIND.READ_WRITE),))
    d_store = builder.dma(
        store, A.DMA_KIND.STORE,
        src=sram_endpoint(t_output, s_output, 0, 0x4000),
        dst=hbm_endpoint(t_output, 0, DUAL_OUTPUT_BASE),
        rows=DUAL_ROWS, row_bytes=DUAL_OUTPUT_BYTES,
        src_stride=DUAL_OUTPUT_BYTES, dst_stride=DUAL_OUTPUT_BYTES,
        completion_event=e_store)
    builder.oracle(entrypoint_id, 1, d_store, store.command_id,
                   A.DMA_KIND.STORE, 0x4000, DUAL_ROWS, DUAL_OUTPUT_BYTES,
                   DUAL_OUTPUT_BYTES)
    stream0.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream0.command(A.OPCODE.HALT, waits=(e_end,))

    gated_waits = (e_begin,) if cross_core_gate else ()
    load_token1 = stream1.command(
        A.OPCODE.DMA_LOAD, waits=gated_waits,
        operands=((t_token, s_token1, a_token1, A.ACCESS_KIND.READ_ONLY),))
    d_token1 = builder.dma(
        load_token1, A.DMA_KIND.LOAD,
        src=hbm_endpoint(t_token, s_token1, DUAL_TOKEN_BASE),
        dst=sram_endpoint(t_token, s_token1, 1, 0x0000),
        rows=DUAL_ROWS, row_bytes=DUAL_TOKEN_BYTES,
        src_stride=DUAL_TOKEN_BYTES, dst_stride=DUAL_TOKEN_BYTES,
        completion_event=e_token1)
    builder.oracle(entrypoint_id, 1, d_token1, load_token1.command_id,
                   A.DMA_KIND.LOAD, DUAL_TOKEN_BASE, DUAL_ROWS,
                   DUAL_TOKEN_BYTES, DUAL_TOKEN_BYTES)
    load_w1 = stream1.command(
        A.OPCODE.DMA_LOAD, waits=gated_waits,
        operands=((t_weight, s_w1, a_w1, A.ACCESS_KIND.READ_ONLY),))
    d_w1 = builder.dma(
        load_w1, A.DMA_KIND.LOAD,
        src=hbm_endpoint(t_weight, s_w1, DUAL_EXPERT_BYTES),
        dst=sram_endpoint(t_weight, s_w1, 1, 0x2000),
        rows=DUAL_ROWS, row_bytes=DUAL_EXPERT_BYTES,
        src_stride=DUAL_EXPERT_BYTES, dst_stride=DUAL_EXPERT_BYTES,
        completion_event=e_w1)
    builder.oracle(entrypoint_id, 1, d_w1, load_w1.command_id,
                   A.DMA_KIND.LOAD, DUAL_EXPERT_BYTES, DUAL_ROWS,
                   DUAL_EXPERT_BYTES, DUAL_EXPERT_BYTES)
    gemm1 = stream1.command(
        A.OPCODE.GEMM, waits=(e_token1, e_w1),
        operands=((t_token, s_token1, a_token1, A.ACCESS_KIND.READ_ONLY),
                  (t_weight, s_w1, a_w1, A.ACCESS_KIND.READ_ONLY),
                  (t_partial, s_part1, a_part1, A.ACCESS_KIND.READ_WRITE)),
        signal_event=e_gemm1,
        attr_index=builder.gemm_attr(1, 1, DUAL_INTERMEDIATE, DUAL_HIDDEN,
                                     A.DTYPE.FP16, A.DTYPE.FP32))
    push = stream1.command(
        A.OPCODE.DMA_P2P_PUSH, waits=(e_gemm1,),
        operands=((t_partial, s_part1, a_part1, A.ACCESS_KIND.READ_ONLY),))
    d_push = builder.dma(
        push, A.DMA_KIND.P2P_PUSH,
        src=sram_endpoint(t_partial, s_part1, 1, 0x4000),
        dst=peer_endpoint(t_partial, s_peer, 0, 0x6000),
        rows=DUAL_ROWS, row_bytes=DUAL_OUTPUT_BYTES,
        src_stride=DUAL_OUTPUT_BYTES, dst_stride=DUAL_OUTPUT_BYTES,
        transfer_id=1, completion_event=e_push)
    builder.oracle(entrypoint_id, 1, d_push, push.command_id,
                   A.DMA_KIND.P2P_PUSH, 0x4000, DUAL_ROWS, DUAL_OUTPUT_BYTES,
                   DUAL_OUTPUT_BYTES)
    stream1.command(A.OPCODE.HALT, waits=(e_push,))

    program = builder.build()
    weight = next(t for t in program.tensors
                  if t.role == A.TENSOR_ROLE.WEIGHT)
    weight.flags = A.TENSOR_FLAGS.HAS_CONTENT_SHA256
    weight.content_sha256 = synthetic_weight_digest()
    symbol_sid = next(r.symbol_sid for r in program.relocations
                      if r.tensor_id == weight.tensor_id)
    program.required_features = A.DYNAMIC_MOE_V1
    program.abi_minor = A.FEATURE_MIN_WRITER_MINOR["DYNAMIC_MOE_V1"]
    program.content_digests = [
        ContentDigest(A.TENSOR_ROLE.WEIGHT, 0, weight.tensor_id,
                      weight.content_sha256),
    ]
    program.moe_layer_specs = [
        MoeLayerSpec(
            layer_id=1, kernel_spec_index=0, expert_first=0, expert_count=2,
            top_k=top_k, token_bytes=DUAL_TOKEN_BYTES,
            output_token_bytes=DUAL_OUTPUT_BYTES,
            capacity_factor_q16=capacity_factor_q16,
            overflow_policy=A.MOE_OVERFLOW_POLICY.DROP,
            transport_mode=A.MOE_TRANSPORT_MODE.VARIABLE_ALL_TO_ALL_V,
            dynamic_region_first=0, dynamic_region_count=2,
            max_tokens_per_frozen_batch=8, max_requests_per_batch=2,
            max_routes=16, max_materialized_commands=2 * DUAL_REGION_COMMANDS,
            max_materialized_descriptors=2 * DUAL_REGION_DESCRIPTORS,
            max_materialized_transfers=DUAL_REGION_TRANSFERS,
            max_dynamic_allocations=2 * DUAL_REGION_ALLOCATIONS, flags=0,
            max_materialized_events=2 * DUAL_REGION_EVENTS + 1),
    ]
    program.moe_expert_specs = [
        MoeExpertSpec(
            layer_id=1, expert_id=expert_id, flags=0, core_id=expert_id,
            reserved_core=0, weight_symbol_id=symbol_sid,
            weight_region_offset=expert_id * DUAL_EXPERT_BYTES,
            weight_bytes=DUAL_EXPERT_BYTES, weight_digest_index=0)
        for expert_id in (0, 1)
    ]
    program.moe_dynamic_regions = [
        MoeDynamicRegion(
            region_id=ordinal + 1, layer_id=1, core_id=core_id, stream_id=0,
            insert_after_command_id=insert, resume_before_command_id=resume,
            entry_event_id=entry, scratch_offset=DUAL_REGION_SCRATCH[0],
            scratch_bytes=DUAL_REGION_SCRATCH[1], scratch_alignment=64,
            max_overlay_commands=DUAL_REGION_COMMANDS,
            max_overlay_events=DUAL_REGION_EVENTS,
            max_overlay_descriptors=DUAL_REGION_DESCRIPTORS,
            max_overlay_transfers=DUAL_REGION_TRANSFERS,
            max_overlay_allocations=DUAL_REGION_ALLOCATIONS, flags=0)
        for ordinal, (core_id, insert, resume, entry) in enumerate((
            (0, gemm0.command_id, recv.command_id, e_gemm0),
            (1, gemm1.command_id, push.command_id, e_gemm1)))
    ]
    program.moe_kernel_specs = [
        MoeKernelSpec(
            layer_id=1, expert_opcode=A.OPCODE.GEMM,
            input_dtype=A.DTYPE.FP16, accum_dtype=A.DTYPE.FP32,
            output_dtype=A.DTYPE.FP16, batch=1, n=DUAL_INTERMEDIATE,
            k=DUAL_HIDDEN, transpose_flags=0,
            combine_kind=A.MOE_COMBINE_KIND.LOCAL_REDUCE, algorithm_id=1,
            efficiency_q16=0x10000, tensor_setup_cycles=64,
            tensor_flush_cycles=32, input_token_bytes=DUAL_TOKEN_BYTES,
            output_token_bytes=DUAL_OUTPUT_BYTES, reserved0=0,
            weight_operand_bytes=DUAL_EXPERT_BYTES,
            expert_result_alignment=64, max_m=32, combine_setup_cycles=32,
            combine_flush_cycles=16, flags=0),
    ]
    return program


def moe_min_program(arch):
    program = build_single_core_program(arch)
    weight = next(t for t in program.tensors
                  if t.role == A.TENSOR_ROLE.WEIGHT)
    weight.storage_class = A.STORAGE_CLASS.EXTERNAL
    weight.access = A.ACCESS_KIND.READ_ONLY
    weight.content_sha256 = synthetic_weight_digest()
    weight.flags = A.TENSOR_FLAGS.HAS_CONTENT_SHA256
    symbol_sid = next(r.symbol_sid for r in program.relocations
                      if r.tensor_id == weight.tensor_id)

    program.required_features = A.DYNAMIC_MOE_V1
    program.abi_minor = A.FEATURE_MIN_WRITER_MINOR["DYNAMIC_MOE_V1"]
    program.content_digests = [
        ContentDigest(A.TENSOR_ROLE.WEIGHT, 0, weight.tensor_id,
                      weight.content_sha256),
    ]
    program.moe_layer_specs = [
        MoeLayerSpec(
            layer_id=1, kernel_spec_index=0, expert_first=0, expert_count=2,
            top_k=2, token_bytes=TOKEN_BYTES,
            output_token_bytes=OUTPUT_TOKEN_BYTES, capacity_factor_q16=0x10000,
            overflow_policy=A.MOE_OVERFLOW_POLICY.DROP,
            transport_mode=A.MOE_TRANSPORT_MODE.VARIABLE_ALL_TO_ALL_V,
            dynamic_region_first=0, dynamic_region_count=1,
            max_tokens_per_frozen_batch=8, max_requests_per_batch=2,
            max_routes=16, max_materialized_commands=32,
            max_materialized_descriptors=32, max_materialized_transfers=8,
            max_dynamic_allocations=8, flags=0, max_materialized_events=17,
        ),
    ]
    program.moe_expert_specs = [
        MoeExpertSpec(
            layer_id=1, expert_id=expert_id, flags=0, core_id=0,
            reserved_core=0, weight_symbol_id=symbol_sid,
            weight_region_offset=expert_id * EXPERT_WEIGHT_BYTES,
            weight_bytes=EXPERT_WEIGHT_BYTES, weight_digest_index=0,
        )
        for expert_id in (0, 1)
    ]
    program.moe_dynamic_regions = [
        MoeDynamicRegion(
            region_id=1, layer_id=1, core_id=0, stream_id=0,
            insert_after_command_id=INSERT_AFTER,
            resume_before_command_id=RESUME_BEFORE, entry_event_id=ENTRY_EVENT,
            scratch_offset=SCRATCH_OFFSET, scratch_bytes=SCRATCH_BYTES,
            scratch_alignment=64, max_overlay_commands=32,
            max_overlay_events=16, max_overlay_descriptors=32,
            max_overlay_transfers=8, max_overlay_allocations=8, flags=0,
        ),
    ]
    program.moe_kernel_specs = [
        MoeKernelSpec(
            layer_id=1, expert_opcode=A.OPCODE.GEMM,
            input_dtype=A.DTYPE.FP16, accum_dtype=A.DTYPE.FP32,
            output_dtype=A.DTYPE.FP16, batch=1, n=INTERMEDIATE, k=HIDDEN,
            transpose_flags=0,
            combine_kind=A.MOE_COMBINE_KIND.LOCAL_REDUCE, algorithm_id=1,
            efficiency_q16=0x10000, tensor_setup_cycles=64,
            tensor_flush_cycles=32, input_token_bytes=TOKEN_BYTES,
            output_token_bytes=OUTPUT_TOKEN_BYTES, reserved0=0,
            weight_operand_bytes=EXPERT_WEIGHT_BYTES,
            expert_result_alignment=64,
            max_m=8, combine_setup_cycles=32, combine_flush_cycles=16,
            flags=0,
        ),
    ]
    return program


WEIGHT_TENSOR_ID = 2
WEIGHT_SYMBOL_SID = 4
MULTI_HIDDEN = (32, 16)
MULTI_INTERMEDIATE = 64
MULTI_EXPERTS = (2, 4)
MULTI_SITES = ((4, 5, 4), (6, 7, 6))
MULTI_SCRATCH = ((0x80000, 0x20000), (0xA0000, 0x10000))


def moe_multi_program(arch):
    program = build_compute_timing_program(arch)
    weight = next(t for t in program.tensors
                  if t.role == A.TENSOR_ROLE.WEIGHT)
    weight.storage_class = A.STORAGE_CLASS.EXTERNAL
    weight.access = A.ACCESS_KIND.READ_ONLY
    weight.flags = A.TENSOR_FLAGS.HAS_CONTENT_SHA256
    weight.content_sha256 = synthetic_weight_digest()
    weight.rank = 2
    weight.dims = (64, 64) + (0,) * 6
    program.relocations = [
        Relocation(relocation_id=1, symbol_sid=WEIGHT_SYMBOL_SID,
                   kind=A.RELOCATION_KIND.TENSOR_BASE, region_id=0,
                   tensor_id=weight.tensor_id, reserved=0, offset_bytes=0,
                   reserved2=0),
    ]
    program.required_features = A.DYNAMIC_MOE_V1
    program.abi_minor = A.FEATURE_MIN_WRITER_MINOR["DYNAMIC_MOE_V1"]
    program.content_digests = [
        ContentDigest(A.TENSOR_ROLE.WEIGHT, 0, weight.tensor_id,
                      weight.content_sha256),
    ]
    output = next(t for t in program.tensors
                  if t.role == A.TENSOR_ROLE.OUTPUT)
    output.dims = (MULTI_INTERMEDIATE,) + (0,) * 7
    for shard in program.shards:
        if shard.tensor_id != output.tensor_id:
            continue
        shard.local_shape = output.dims
        shard.valid_shape = output.dims
        shard.span_bytes = MULTI_INTERMEDIATE * 2
        for allocation in program.allocations:
            if allocation.allocation_id == shard.allocation_id:
                allocation.size_bytes = MULTI_INTERMEDIATE * 2
    layers = []
    experts = []
    regions = []
    kernels = []
    expert_offset = 0
    region_offset = 0
    for index, expert_count in enumerate(MULTI_EXPERTS):
        layer_id = index + 1
        hidden = MULTI_HIDDEN[index]
        expert_bytes = hidden * MULTI_INTERMEDIATE * 2
        sites = MULTI_SITES[index]
        scratch = MULTI_SCRATCH[index]
        kernel = MoeKernelSpec(
            layer_id=layer_id, expert_opcode=A.OPCODE.GEMM,
            input_dtype=A.DTYPE.FP16, accum_dtype=A.DTYPE.FP32,
            output_dtype=A.DTYPE.FP16, batch=1,
            n=MULTI_INTERMEDIATE, k=hidden, transpose_flags=0,
            combine_kind=A.MOE_COMBINE_KIND.LOCAL_REDUCE,
            algorithm_id=layer_id, efficiency_q16=0x10000,
            tensor_setup_cycles=64, tensor_flush_cycles=32,
            input_token_bytes=hidden * 2,
            output_token_bytes=MULTI_INTERMEDIATE * 2, reserved0=0,
            weight_operand_bytes=expert_bytes, expert_result_alignment=64,
            max_m=8, combine_setup_cycles=32, combine_flush_cycles=16,
            flags=0)
        kernels.append(kernel)
        layers.append(MoeLayerSpec(
            layer_id=layer_id, kernel_spec_index=index,
            expert_first=expert_offset, expert_count=expert_count, top_k=2,
            token_bytes=hidden * 2,
            output_token_bytes=MULTI_INTERMEDIATE * 2,
            capacity_factor_q16=0x10000,
            overflow_policy=A.MOE_OVERFLOW_POLICY.DROP,
            transport_mode=A.MOE_TRANSPORT_MODE.VARIABLE_ALL_TO_ALL_V,
            dynamic_region_first=region_offset, dynamic_region_count=1,
            max_tokens_per_frozen_batch=8, max_requests_per_batch=2,
            max_routes=16, max_materialized_commands=32,
            max_materialized_descriptors=32, max_materialized_transfers=8,
            max_dynamic_allocations=8, flags=0, max_materialized_events=17))
        for expert_id in range(expert_count):
            experts.append(MoeExpertSpec(
                layer_id=layer_id, expert_id=expert_id, flags=0, core_id=0,
                reserved_core=0, weight_symbol_id=WEIGHT_SYMBOL_SID,
                weight_region_offset=expert_id * expert_bytes,
                weight_bytes=expert_bytes, weight_digest_index=0))
        regions.append(MoeDynamicRegion(
            region_id=1, layer_id=layer_id, core_id=0, stream_id=0,
            insert_after_command_id=sites[0],
            resume_before_command_id=sites[1], entry_event_id=sites[2],
            scratch_offset=scratch[0], scratch_bytes=scratch[1],
            scratch_alignment=64, max_overlay_commands=32,
            max_overlay_events=16, max_overlay_descriptors=32,
            max_overlay_transfers=8, max_overlay_allocations=8, flags=0))
        expert_offset += expert_count
        region_offset += 1
    program.moe_layer_specs = layers
    program.moe_expert_specs = experts
    program.moe_dynamic_regions = regions
    program.moe_kernel_specs = kernels
    return program


DUAL_DROP_CAPACITY_Q16 = 0x8000


def moe_dual_drop_program(arch):
    return moe_dual_program(arch, capacity_factor_q16=DUAL_DROP_CAPACITY_Q16)


def moe_dual_copy_program(arch):
    return moe_dual_program(arch, top_k=1)


QUAD_CORES = 16
QUAD_PAIRS = QUAD_CORES // 2
QUAD_TOKEN_BYTES = HIDDEN * 2
QUAD_OUTPUT_BYTES = INTERMEDIATE * 2
QUAD_EXPERT_BYTES = HIDDEN * INTERMEDIATE * 2
QUAD_TOKEN_BASE = 0x100000
QUAD_OUTPUT_BASE = 0x200000
QUAD_REGION_SCRATCH_BYTES = 0x20000
QUAD_REGION_COMMANDS = 32
QUAD_REGION_EVENTS = 16
QUAD_REGION_DESCRIPTORS = 32
QUAD_REGION_TRANSFERS = 8
QUAD_REGION_ALLOCATIONS = 8


def quad_reducer(core_id: int) -> int:
    return (core_id % QUAD_PAIRS) * 2


def quad_sender(core_id: int) -> int:
    return quad_reducer(core_id) + 1


def moe_quad_program(arch, cross_core_gate=True):
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "gate5_moe_quad")
    entrypoint_id = builder.entrypoint("main", "moe_quad", lifecycle_core=0,
                                       lifecycle_stream=0)
    t_token = builder.tensor("token", A.TENSOR_ROLE.INPUT, A.DTYPE.FP16,
                             A.STORAGE_CLASS.HBM, A.ACCESS_KIND.READ_ONLY,
                             (QUAD_CORES * HIDDEN,), sharding_id=1)
    t_weight = builder.tensor("weight", A.TENSOR_ROLE.WEIGHT, A.DTYPE.FP16,
                              A.STORAGE_CLASS.EXTERNAL,
                              A.ACCESS_KIND.READ_ONLY,
                              (QUAD_CORES * INTERMEDIATE, HIDDEN),
                              sharding_id=1)
    t_partial = builder.tensor("partial", A.TENSOR_ROLE.ACTIVATION,
                               A.DTYPE.FP16, A.STORAGE_CLASS.CORE_SRAM,
                               A.ACCESS_KIND.READ_WRITE,
                               (INTERMEDIATE,), sharding_id=2)
    t_output = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, A.DTYPE.FP16,
                              A.STORAGE_CLASS.HBM, A.ACCESS_KIND.READ_WRITE,
                              (QUAD_CORES * INTERMEDIATE,))
    builder.relocation("token", A.RELOCATION_KIND.TENSOR_BASE, 0, t_token,
                       QUAD_TOKEN_BASE)
    builder.relocation("weight", A.RELOCATION_KIND.TENSOR_BASE, 0, t_weight, 0)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, 0, t_output,
                       QUAD_OUTPUT_BASE)
    align = arch.sram_base_alignment_bytes
    a_token = [builder.allocation(core_id, 0x0000, QUAD_TOKEN_BYTES, align)
               for core_id in range(QUAD_CORES)]
    a_weight = [builder.allocation(core_id, 0x2000, QUAD_EXPERT_BYTES, align)
                for core_id in range(QUAD_CORES)]
    a_partial = [builder.allocation(core_id, 0x4000, QUAD_OUTPUT_BYTES, align)
                 for core_id in range(QUAD_CORES)]
    a_peer = [builder.allocation(core_id, 0x6000, QUAD_OUTPUT_BYTES, align)
              for core_id in range(QUAD_CORES)]
    s_token = [builder.shard(t_token, core_id, a_token[core_id],
                             (QUAD_CORES * HIDDEN,), QUAD_TOKEN_BYTES,
                             sharding_id=1)
               for core_id in range(QUAD_CORES)]
    s_weight = [builder.shard(t_weight, core_id, a_weight[core_id],
                              (QUAD_CORES * INTERMEDIATE, HIDDEN),
                              QUAD_EXPERT_BYTES, sharding_id=1)
                for core_id in range(QUAD_CORES)]
    s_partial = [builder.shard(t_partial, core_id, a_partial[core_id],
                               (INTERMEDIATE,), QUAD_OUTPUT_BYTES,
                               sharding_id=2)
                 for core_id in range(QUAD_CORES)]
    s_peer = [builder.shard(t_partial, core_id, a_peer[core_id],
                            (INTERMEDIATE,), QUAD_OUTPUT_BYTES, sharding_id=3)
              for core_id in range(QUAD_CORES)]
    s_output = [builder.shard(t_output, core_id, a_partial[core_id],
                              (QUAD_CORES * INTERMEDIATE,),
                              QUAD_OUTPUT_BYTES)
                for core_id in range(QUAD_CORES)]
    streams = []
    for core_id in range(QUAD_CORES):
        flags = A.STREAM_FLAGS.IS_LOCAL_CONTROL
        if core_id == 0:
            flags |= A.STREAM_FLAGS.IS_LIFECYCLE
        streams.append(builder.stream(core_id, 0, flags=flags))
    pair_events = []
    for pair in range(QUAD_PAIRS):
        names = ["token", "weight", "gemm", "recv", "reduce", "store",
                 "token_s", "weight_s", "gemm_s", "push_s"]
        if pair == 0:
            names = ["begin"] + names + ["end"]
        pair_events.append({name: builder.event() for name in names})
    lifecycle_begin = pair_events[0]["begin"]
    join_event = builder.event()
    regions = []
    for pair in range(QUAD_PAIRS):
        reducer = quad_reducer(pair)
        sender = quad_sender(pair)
        events = pair_events[pair]
        e_begin = events.get("begin")
        e_end = events.get("end")
        e_token = events["token"]
        e_weight = events["weight"]
        e_gemm = events["gemm"]
        e_recv = events["recv"]
        e_reduce = events["reduce"]
        e_store = events["store"]
        e_join = join_event
        e_token_s = events["token_s"]
        e_weight_s = events["weight_s"]
        e_gemm_s = events["gemm_s"]
        e_push_s = events["push_s"]
        gate_waits = (lifecycle_begin,) if cross_core_gate else ()
        scope = streams[reducer]
        if pair == 0:
            scope.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
            start_waits = (e_begin,)
        else:
            start_waits = gate_waits
        load_token = scope.command(
            A.OPCODE.DMA_LOAD, waits=start_waits,
            operands=((t_token, s_token[reducer], a_token[reducer],
                       A.ACCESS_KIND.READ_ONLY),))
        d_token = builder.dma(
            load_token, A.DMA_KIND.LOAD,
            src=hbm_endpoint(t_token, s_token[reducer],
                             QUAD_TOKEN_BASE + reducer * QUAD_TOKEN_BYTES),
            dst=sram_endpoint(t_token, s_token[reducer], reducer, 0x0000),
            rows=1, row_bytes=QUAD_TOKEN_BYTES,
            src_stride=QUAD_TOKEN_BYTES, dst_stride=QUAD_TOKEN_BYTES,
            completion_event=e_token)
        builder.oracle(entrypoint_id, 1, d_token, load_token.command_id,
                       A.DMA_KIND.LOAD,
                       QUAD_TOKEN_BASE + reducer * QUAD_TOKEN_BYTES, 1,
                       QUAD_TOKEN_BYTES, QUAD_TOKEN_BYTES)
        load_weight = scope.command(
            A.OPCODE.DMA_LOAD, waits=start_waits,
            operands=((t_weight, s_weight[reducer], a_weight[reducer],
                       A.ACCESS_KIND.READ_ONLY),))
        d_weight = builder.dma(
            load_weight, A.DMA_KIND.LOAD,
            src=hbm_endpoint(t_weight, s_weight[reducer],
                             reducer * QUAD_EXPERT_BYTES),
            dst=sram_endpoint(t_weight, s_weight[reducer], reducer, 0x2000),
            rows=1, row_bytes=QUAD_EXPERT_BYTES,
            src_stride=QUAD_EXPERT_BYTES, dst_stride=QUAD_EXPERT_BYTES,
            completion_event=e_weight)
        builder.oracle(entrypoint_id, 1, d_weight, load_weight.command_id,
                       A.DMA_KIND.LOAD, reducer * QUAD_EXPERT_BYTES, 1,
                       QUAD_EXPERT_BYTES, QUAD_EXPERT_BYTES)
        gemm = scope.command(
            A.OPCODE.GEMM, waits=(e_token, e_weight),
            operands=((t_token, s_token[reducer], a_token[reducer],
                       A.ACCESS_KIND.READ_ONLY),
                      (t_weight, s_weight[reducer], a_weight[reducer],
                       A.ACCESS_KIND.READ_ONLY),
                      (t_partial, s_partial[reducer], a_partial[reducer],
                       A.ACCESS_KIND.READ_WRITE)),
            signal_event=e_gemm,
            attr_index=builder.gemm_attr(1, 1, INTERMEDIATE, HIDDEN,
                                         A.DTYPE.FP16, A.DTYPE.FP32))
        recv = scope.command(
            A.OPCODE.RECV_WAIT, waits=(e_gemm,),
            operands=((t_partial, s_peer[reducer], a_peer[reducer],
                       A.ACCESS_KIND.READ_WRITE),),
            signal_event=e_recv,
            attr_index=builder.recv_wait_attr(pair + 1))
        scope.command(
            A.OPCODE.LOCAL_REDUCE, waits=(e_recv,),
            operands=((t_partial, s_peer[reducer], a_peer[reducer],
                       A.ACCESS_KIND.READ_WRITE),
                      (t_partial, s_partial[reducer], a_partial[reducer],
                       A.ACCESS_KIND.READ_WRITE)),
            signal_event=e_reduce,
            attr_index=builder.reduce_attr(INTERMEDIATE, A.DTYPE.FP16,
                                           A.DTYPE.FP32, fan_in=2))
        store = scope.command(
            A.OPCODE.DMA_STORE, waits=(e_reduce,),
            operands=((t_output, s_output[reducer], a_partial[reducer],
                       A.ACCESS_KIND.READ_WRITE),))
        d_store = builder.dma(
            store, A.DMA_KIND.STORE,
            src=sram_endpoint(t_output, s_output[reducer], reducer, 0x4000),
            dst=hbm_endpoint(t_output, 0,
                             QUAD_OUTPUT_BASE +
                             reducer * QUAD_OUTPUT_BYTES),
            rows=1, row_bytes=QUAD_OUTPUT_BYTES,
            src_stride=QUAD_OUTPUT_BYTES, dst_stride=QUAD_OUTPUT_BYTES,
            completion_event=e_store)
        builder.oracle(entrypoint_id, 1, d_store, store.command_id,
                       A.DMA_KIND.STORE, 0x4000, 1, QUAD_OUTPUT_BYTES,
                       QUAD_OUTPUT_BYTES)
        if pair == 0:
            join_waits = tuple(
                event
                for other_events in pair_events
                for event in (other_events["store"], other_events["push_s"])
            )
            scope.command(A.OPCODE.EVENT_SIGNAL, waits=join_waits,
                          signal_event=e_join)
            scope.command(A.OPCODE.REQUEST_END, waits=(e_join,),
                          signal_event=e_end)
            scope.command(A.OPCODE.HALT, waits=(e_end,))
        else:
            scope.command(A.OPCODE.HALT, waits=(e_store,))
        regions.append(MoeDynamicRegion(
            region_id=reducer + 1, layer_id=1, core_id=reducer, stream_id=0,
            insert_after_command_id=gemm.command_id,
            resume_before_command_id=recv.command_id,
            entry_event_id=e_gemm,
            scratch_offset=SCRATCH_OFFSET,
            scratch_bytes=QUAD_REGION_SCRATCH_BYTES, scratch_alignment=align,
            max_overlay_commands=QUAD_REGION_COMMANDS,
            max_overlay_events=QUAD_REGION_EVENTS,
            max_overlay_descriptors=QUAD_REGION_DESCRIPTORS,
            max_overlay_transfers=QUAD_REGION_TRANSFERS,
            max_overlay_allocations=QUAD_REGION_ALLOCATIONS, flags=0))
        sender_scope = streams[sender]
        sender_token = sender_scope.command(
            A.OPCODE.DMA_LOAD, waits=gate_waits,
            operands=((t_token, s_token[sender], a_token[sender],
                       A.ACCESS_KIND.READ_ONLY),))
        d_sender_token = builder.dma(
            sender_token, A.DMA_KIND.LOAD,
            src=hbm_endpoint(t_token, s_token[sender],
                             QUAD_TOKEN_BASE + sender * QUAD_TOKEN_BYTES),
            dst=sram_endpoint(t_token, s_token[sender], sender, 0x0000),
            rows=1, row_bytes=QUAD_TOKEN_BYTES,
            src_stride=QUAD_TOKEN_BYTES, dst_stride=QUAD_TOKEN_BYTES,
            completion_event=e_token_s)
        builder.oracle(entrypoint_id, 1, d_sender_token,
                       sender_token.command_id, A.DMA_KIND.LOAD,
                       QUAD_TOKEN_BASE + sender * QUAD_TOKEN_BYTES, 1,
                       QUAD_TOKEN_BYTES, QUAD_TOKEN_BYTES)
        sender_weight = sender_scope.command(
            A.OPCODE.DMA_LOAD, waits=gate_waits,
            operands=((t_weight, s_weight[sender], a_weight[sender],
                       A.ACCESS_KIND.READ_ONLY),))
        d_sender_weight = builder.dma(
            sender_weight, A.DMA_KIND.LOAD,
            src=hbm_endpoint(t_weight, s_weight[sender],
                             sender * QUAD_EXPERT_BYTES),
            dst=sram_endpoint(t_weight, s_weight[sender], sender, 0x2000),
            rows=1, row_bytes=QUAD_EXPERT_BYTES,
            src_stride=QUAD_EXPERT_BYTES, dst_stride=QUAD_EXPERT_BYTES,
            completion_event=e_weight_s)
        builder.oracle(entrypoint_id, 1, d_sender_weight,
                       sender_weight.command_id, A.DMA_KIND.LOAD,
                       sender * QUAD_EXPERT_BYTES, 1, QUAD_EXPERT_BYTES,
                       QUAD_EXPERT_BYTES)
        sender_gemm = sender_scope.command(
            A.OPCODE.GEMM, waits=(e_token_s, e_weight_s),
            operands=((t_token, s_token[sender], a_token[sender],
                       A.ACCESS_KIND.READ_ONLY),
                      (t_weight, s_weight[sender], a_weight[sender],
                       A.ACCESS_KIND.READ_ONLY),
                      (t_partial, s_partial[sender], a_partial[sender],
                       A.ACCESS_KIND.READ_WRITE)),
            signal_event=e_gemm_s,
            attr_index=builder.gemm_attr(1, 1, INTERMEDIATE, HIDDEN,
                                         A.DTYPE.FP16, A.DTYPE.FP32))
        push = sender_scope.command(
            A.OPCODE.DMA_P2P_PUSH, waits=(e_gemm_s,),
            operands=((t_partial, s_partial[sender], a_partial[sender],
                       A.ACCESS_KIND.READ_ONLY),))
        d_push = builder.dma(
            push, A.DMA_KIND.P2P_PUSH,
            src=sram_endpoint(t_partial, s_partial[sender], sender, 0x4000),
            dst=peer_endpoint(t_partial, s_peer[reducer], reducer, 0x6000),
            rows=1, row_bytes=QUAD_OUTPUT_BYTES,
            src_stride=QUAD_OUTPUT_BYTES, dst_stride=QUAD_OUTPUT_BYTES,
            transfer_id=pair + 1, completion_event=e_push_s)
        builder.oracle(entrypoint_id, 1, d_push, push.command_id,
                       A.DMA_KIND.P2P_PUSH, 0x4000, 1, QUAD_OUTPUT_BYTES,
                       QUAD_OUTPUT_BYTES)
        sender_scope.command(A.OPCODE.HALT, waits=(e_push_s,))
        regions.append(MoeDynamicRegion(
            region_id=sender + 1, layer_id=1, core_id=sender, stream_id=0,
            insert_after_command_id=sender_gemm.command_id,
            resume_before_command_id=push.command_id,
            entry_event_id=e_gemm_s,
            scratch_offset=SCRATCH_OFFSET,
            scratch_bytes=QUAD_REGION_SCRATCH_BYTES, scratch_alignment=align,
            max_overlay_commands=QUAD_REGION_COMMANDS,
            max_overlay_events=QUAD_REGION_EVENTS,
            max_overlay_descriptors=QUAD_REGION_DESCRIPTORS,
            max_overlay_transfers=QUAD_REGION_TRANSFERS,
            max_overlay_allocations=QUAD_REGION_ALLOCATIONS, flags=0))
    program = builder.build()
    weight = next(t for t in program.tensors
                  if t.role == A.TENSOR_ROLE.WEIGHT)
    weight.flags = A.TENSOR_FLAGS.HAS_CONTENT_SHA256
    weight.content_sha256 = synthetic_weight_digest()
    symbol_sid = next(r.symbol_sid for r in program.relocations
                      if r.tensor_id == weight.tensor_id)
    program.required_features = A.DYNAMIC_MOE_V1
    program.abi_minor = A.FEATURE_MIN_WRITER_MINOR["DYNAMIC_MOE_V1"]
    program.content_digests = [
        ContentDigest(A.TENSOR_ROLE.WEIGHT, 0, weight.tensor_id,
                      weight.content_sha256),
    ]
    program.moe_layer_specs = [
        MoeLayerSpec(
            layer_id=1, kernel_spec_index=0, expert_first=0,
            expert_count=QUAD_CORES, top_k=2, token_bytes=QUAD_TOKEN_BYTES,
            output_token_bytes=QUAD_OUTPUT_BYTES,
            capacity_factor_q16=0x80000,
            overflow_policy=A.MOE_OVERFLOW_POLICY.DROP,
            transport_mode=A.MOE_TRANSPORT_MODE.VARIABLE_ALL_TO_ALL_V,
            dynamic_region_first=0, dynamic_region_count=QUAD_CORES,
            max_tokens_per_frozen_batch=32, max_requests_per_batch=16,
            max_routes=64,
            max_materialized_commands=QUAD_CORES * QUAD_REGION_COMMANDS,
            max_materialized_descriptors=QUAD_CORES * QUAD_REGION_DESCRIPTORS,
            max_materialized_transfers=QUAD_CORES * QUAD_REGION_TRANSFERS,
            max_dynamic_allocations=QUAD_CORES * QUAD_REGION_ALLOCATIONS,
            flags=0,
            max_materialized_events=QUAD_CORES * QUAD_REGION_EVENTS + 1),
    ]
    program.moe_expert_specs = [
        MoeExpertSpec(
            layer_id=1, expert_id=expert_id, flags=0, core_id=expert_id,
            reserved_core=0, weight_symbol_id=symbol_sid,
            weight_region_offset=expert_id * QUAD_EXPERT_BYTES,
            weight_bytes=QUAD_EXPERT_BYTES, weight_digest_index=0)
        for expert_id in range(QUAD_CORES)
    ]
    program.moe_dynamic_regions = regions
    program.moe_kernel_specs = [
        MoeKernelSpec(
            layer_id=1, expert_opcode=A.OPCODE.GEMM,
            input_dtype=A.DTYPE.FP16, accum_dtype=A.DTYPE.FP32,
            output_dtype=A.DTYPE.FP16, batch=1, n=INTERMEDIATE, k=HIDDEN,
            transpose_flags=0, combine_kind=A.MOE_COMBINE_KIND.LOCAL_REDUCE,
            algorithm_id=1, efficiency_q16=0x10000, tensor_setup_cycles=64,
            tensor_flush_cycles=32, input_token_bytes=QUAD_TOKEN_BYTES,
            output_token_bytes=QUAD_OUTPUT_BYTES, reserved0=0,
            weight_operand_bytes=QUAD_EXPERT_BYTES,
            expert_result_alignment=64, max_m=32, combine_setup_cycles=32,
            combine_flush_cycles=16, flags=0),
    ]
    return program

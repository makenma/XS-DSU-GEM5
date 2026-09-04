"""Hand-written golden programs for the Dummy Core runtime prototype.

golden_single_core: DMA_LOAD x2 -> GEMM timer -> DMA_STORE on core 0.
golden_dual_core:   per-core DMA_LOAD -> GEMM -> P2P_PUSH -> RECV_WAIT ->
                    LOCAL_REDUCE -> DMA_STORE across cores 0 and 1.

No tensor numerics are computed anywhere; compute opcodes only carry the
timing/digest attributes consumed by the Dummy Core analytic engines.
"""

from __future__ import annotations

import copy
import dataclasses

from mesh_ir.generated import abi as A
from mesh_ir.model import DmaEndpoint

HBM_REGION = 0
SRAM_REGION = 2
NO_CORE = 0xFFFF

FP16 = A.DTYPE.FP16
RO = A.ACCESS_KIND.READ_ONLY
RW = A.ACCESS_KIND.READ_WRITE

M = 64
N = 64
K = 64
ELEM = 2

IN_BYTES = M * K * ELEM
W_BYTES = K * N * ELEM
OUT_BYTES = M * N * ELEM

TP_K = 32
PARTIAL_BYTES = M * N * ELEM
A_SHARD_BYTES = M * TP_K * ELEM
W_SHARD_BYTES = TP_K * N * ELEM


def _hbm(tensor_id, shard_id, offset) -> DmaEndpoint:
    return DmaEndpoint(
        memory_space=A.MEMORY_SPACE.HBM,
        region_id=HBM_REGION,
        owner_core=NO_CORE,
        tensor_id=tensor_id,
        shard_id=shard_id,
        reserved=0,
        offset_bytes=offset,
    )


def _sram(tensor_id, shard_id, core, offset) -> DmaEndpoint:
    return DmaEndpoint(
        memory_space=A.MEMORY_SPACE.CORE_SRAM,
        region_id=SRAM_REGION,
        owner_core=core,
        tensor_id=tensor_id,
        shard_id=shard_id,
        reserved=0,
        offset_bytes=offset,
    )


def _peer(tensor_id, shard_id, core, offset) -> DmaEndpoint:
    return DmaEndpoint(
        memory_space=A.MEMORY_SPACE.PEER_SRAM,
        region_id=SRAM_REGION,
        owner_core=core,
        tensor_id=tensor_id,
        shard_id=shard_id,
        reserved=0,
        offset_bytes=offset,
    )


def build_single_core_program(arch):
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_single_core")
    entrypoint_id = builder.entrypoint("main", "b1_m64n64k64", lifecycle_core=0, lifecycle_stream=0)

    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (M, K))
    t_w = builder.tensor("weight", A.TENSOR_ROLE.WEIGHT, FP16, A.STORAGE_CLASS.HBM, RO, (K, N))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (M, N))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("weight", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_w, 0x200000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x300000)

    a_in = builder.allocation(0, 0x0000, IN_BYTES, arch.sram_base_alignment_bytes)
    a_w = builder.allocation(0, 0x2000, W_BYTES, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x4000, OUT_BYTES, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_in, (M, K), IN_BYTES)
    s_w = builder.shard(t_w, 0, a_w, (K, N), W_BYTES)
    s_out = builder.shard(t_out, 0, a_out, (M, N), OUT_BYTES)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_lin = builder.event()
    e_lw = builder.event()
    e_gemm = builder.event()
    e_store = builder.event()
    e_end = builder.event()

    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_lin = stream.command(
        A.OPCODE.DMA_LOAD,
        waits=(e_begin,),
        operands=((t_in, s_in, a_in, RO),),
        signal_event=0,
    )
    d_lin = builder.dma(
        cmd_lin,
        A.DMA_KIND.LOAD,
        src=_hbm(t_in, s_in, 0x100000),
        dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1,
        row_bytes=IN_BYTES,
        src_stride=IN_BYTES,
        dst_stride=IN_BYTES,
        completion_event=e_lin,
    )
    builder.oracle(entrypoint_id, 1, d_lin, cmd_lin.command_id, A.DMA_KIND.LOAD, 0x100000, 1, IN_BYTES, IN_BYTES)

    cmd_lw = stream.command(
        A.OPCODE.DMA_LOAD,
        waits=(e_begin,),
        operands=((t_w, s_w, a_w, RO),),
        signal_event=0,
    )
    d_lw = builder.dma(
        cmd_lw,
        A.DMA_KIND.LOAD,
        src=_hbm(t_w, s_w, 0x200000),
        dst=_sram(t_w, s_w, 0, 0x2000),
        rows=1,
        row_bytes=W_BYTES,
        src_stride=W_BYTES,
        dst_stride=W_BYTES,
        completion_event=e_lw,
    )
    builder.oracle(entrypoint_id, 1, d_lw, cmd_lw.command_id, A.DMA_KIND.LOAD, 0x200000, 1, W_BYTES, W_BYTES)

    gemm_attr = builder.gemm_attr(1, M, N, K, FP16, A.DTYPE.FP32)
    stream.command(
        A.OPCODE.GEMM,
        waits=(e_lin, e_lw),
        operands=((t_in, s_in, a_in, RO), (t_w, s_w, a_w, RO), (t_out, s_out, a_out, RW)),
        signal_event=e_gemm,
        attr_index=gemm_attr,
    )

    cmd_store = stream.command(
        A.OPCODE.DMA_STORE,
        waits=(e_gemm,),
        operands=((t_out, s_out, a_out, RW),),
        signal_event=0,
    )
    d_store = builder.dma(
        cmd_store,
        A.DMA_KIND.STORE,
        src=_sram(t_out, s_out, 0, 0x4000),
        dst=_hbm(t_out, s_out, 0x300000),
        rows=1,
        row_bytes=OUT_BYTES,
        src_stride=OUT_BYTES,
        dst_stride=OUT_BYTES,
        completion_event=e_store,
    )
    builder.oracle(entrypoint_id, 1, d_store, cmd_store.command_id, A.DMA_KIND.STORE, 0x300000, 1, OUT_BYTES, OUT_BYTES)

    stream.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))

    for command, event_id in (
        (cmd_lin, e_lin),
        (cmd_lw, e_lw),
        (cmd_store, e_store),
    ):
        descriptor = next(d for d in builder.dma_descriptors if d.command_id == command.command_id)
        assert descriptor.completion_event == event_id

    return builder.build()


def build_region_edge_program(arch):
    program = copy.deepcopy(build_single_core_program(arch))
    offsets = {0x100000: 0x500000, 0x200000: 0x600000, 0x300000: 0x700000}
    descriptors = []
    for descriptor in program.dma_descriptors:
        src = descriptor.src
        dst = descriptor.dst
        if src.memory_space == A.MEMORY_SPACE.HBM:
            src = dataclasses.replace(src, offset_bytes=offsets[src.offset_bytes])
        if dst.memory_space == A.MEMORY_SPACE.HBM:
            dst = dataclasses.replace(dst, offset_bytes=offsets[dst.offset_bytes])
        descriptors.append(dataclasses.replace(descriptor, src=src, dst=dst))
    program.dma_descriptors = descriptors
    program.relocations = [
        dataclasses.replace(row, offset_bytes=offsets[row.offset_bytes])
        for row in program.relocations
    ]
    return program


def build_compute_timing_program(arch):
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_compute_timing")
    entrypoint_id = builder.entrypoint(
        "main", "compute_timing", lifecycle_core=0, lifecycle_stream=0
    )
    t_a = builder.tensor(
        "a", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RW, (32,)
    )
    t_b = builder.tensor(
        "b", A.TENSOR_ROLE.WEIGHT, FP16, A.STORAGE_CLASS.HBM, RW, (32,)
    )
    t_out = builder.tensor(
        "out", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (32,)
    )
    a_a = builder.allocation(0, 0x000, 64, arch.sram_base_alignment_bytes)
    a_b = builder.allocation(0, 0x200, 64, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x400, 64, arch.sram_base_alignment_bytes)
    s_a = builder.shard(t_a, 0, a_a, (32,), 64)
    s_b = builder.shard(t_b, 0, a_b, (32,), 64)
    s_out = builder.shard(t_out, 0, a_out, (32,), 64)
    stream = builder.stream(
        0,
        0,
        flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    events = [builder.event() for _ in range(8)]
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=events[0])
    previous = events[0]
    for tensor, shard, allocation, offset, pattern, done in (
        (t_a, s_a, a_a, 0x000, 0x11, events[1]),
        (t_b, s_b, a_b, 0x200, 0x22, events[2]),
    ):
        command = stream.command(
            A.OPCODE.DMA_FILL,
            waits=(previous,),
            operands=((tensor, shard, allocation, RW),),
            attr_index=builder.fill_attr(pattern),
        )
        descriptor = builder.dma(
            command,
            A.DMA_KIND.LOCAL_FILL,
            src=_sram(tensor, shard, 0, offset),
            dst=_sram(tensor, shard, 0, offset),
            rows=1,
            row_bytes=64,
            src_stride=64,
            dst_stride=64,
            completion_event=done,
        )
        builder.oracle(
            entrypoint_id,
            1,
            descriptor,
            command.command_id,
            A.DMA_KIND.LOCAL_FILL,
            offset,
            1,
            64,
            64,
        )
        previous = done
    operands = (
        (t_a, s_a, a_a, RO),
        (t_b, s_b, a_b, RO),
        (t_out, s_out, a_out, RW),
    )
    stream.command(
        A.OPCODE.GEMM,
        waits=(previous,),
        operands=operands,
        signal_event=events[3],
        attr_index=builder.gemm_attr(1, 2, 2, 2, FP16, A.DTYPE.FP32),
    )
    stream.command(
        A.OPCODE.BMM,
        waits=(events[3],),
        operands=operands,
        signal_event=events[4],
        attr_index=builder._attr(
            A.ATTR_KIND.BMM_V1,
            (1, 2, 2, 2, 0, 0, FP16, A.DTYPE.FP32, 0, 65536),
            (
                "batch", "m", "n", "k", "a_transpose", "b_transpose",
                "dtype", "accum_dtype", "epilogue", "efficiency_q16",
            ),
        ),
    )
    stream.command(
        A.OPCODE.ELEMENTWISE,
        waits=(events[4],),
        operands=((t_out, s_out, a_out, RW),),
        signal_event=events[5],
        attr_index=builder.elementwise_attr(32, FP16, ops_per_element=3),
    )
    stream.command(
        A.OPCODE.LOCAL_REDUCE,
        waits=(events[5],),
        operands=((t_a, s_a, a_a, RO), (t_out, s_out, a_out, RW)),
        signal_event=events[6],
        attr_index=builder.reduce_attr(
            32, FP16, A.DTYPE.FP32, fan_in=3
        ),
    )
    stream.command(
        A.OPCODE.REQUEST_END, waits=(events[6],), signal_event=events[7]
    )
    stream.command(A.OPCODE.HALT, waits=(events[7],))
    return builder.build()


def build_dual_core_program(arch):
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_dual_core")
    entrypoint_id = builder.entrypoint("main", "b1_m64n64k64_tp2", lifecycle_core=0, lifecycle_stream=0)

    t_a = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (M, K), sharding_id=1)
    t_w = builder.tensor("weight", A.TENSOR_ROLE.WEIGHT, FP16, A.STORAGE_CLASS.HBM, RO, (K, N), sharding_id=1)
    t_partial = builder.tensor("partial", A.TENSOR_ROLE.ACTIVATION, FP16, A.STORAGE_CLASS.CORE_SRAM, RW, (M, N), sharding_id=2)
    t_final = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (M, N))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_a, 0x100000)
    builder.relocation("weight", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_w, 0x200000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_final, 0x300000)

    core0_a = builder.allocation(0, 0x0000, A_SHARD_BYTES, arch.sram_base_alignment_bytes)
    core0_w = builder.allocation(0, 0x2000, W_SHARD_BYTES, arch.sram_base_alignment_bytes)
    core0_c = builder.allocation(0, 0x4000, PARTIAL_BYTES, arch.sram_base_alignment_bytes)
    core1_a = builder.allocation(1, 0x0000, A_SHARD_BYTES, arch.sram_base_alignment_bytes)
    core1_w = builder.allocation(1, 0x2000, W_SHARD_BYTES, arch.sram_base_alignment_bytes)
    core1_peer = builder.allocation(1, 0x4000, PARTIAL_BYTES, arch.sram_base_alignment_bytes)
    core1_c = builder.allocation(1, 0x6000, PARTIAL_BYTES, arch.sram_base_alignment_bytes)

    s_a0 = builder.shard(t_a, 0, core0_a, (M, TP_K), A_SHARD_BYTES, sharding_id=1)
    s_w0 = builder.shard(t_w, 0, core0_w, (TP_K, N), W_SHARD_BYTES, sharding_id=1)
    s_c0 = builder.shard(t_partial, 0, core0_c, (M, N), PARTIAL_BYTES, sharding_id=2)
    s_a1 = builder.shard(t_a, 1, core1_a, (M, TP_K), A_SHARD_BYTES, sharding_id=1)
    s_w1 = builder.shard(t_w, 1, core1_w, (TP_K, N), W_SHARD_BYTES, sharding_id=1)
    s_peer = builder.shard(t_partial, 1, core1_peer, (M, N), PARTIAL_BYTES, sharding_id=2)
    s_c1 = builder.shard(t_partial, 1, core1_c, (M, N), PARTIAL_BYTES, sharding_id=2)
    s_final = builder.shard(t_final, 1, core1_c, (M, N), PARTIAL_BYTES)

    stream0 = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_la0 = builder.event()
    e_lw0 = builder.event()
    e_g0 = builder.event()
    e_p2p = builder.event()
    e_end0 = builder.event()
    e_la1 = builder.event()
    e_lw1 = builder.event()
    e_g1 = builder.event()
    e_recv = builder.event()
    e_red = builder.event()
    e_store = builder.event()

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_la0 = stream0.command(A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_a, s_a0, core0_a, RO),))
    d_la0 = builder.dma(
        cmd_la0, A.DMA_KIND.LOAD,
        src=_hbm(t_a, s_a0, 0x100000), dst=_sram(t_a, s_a0, 0, 0x0000),
        rows=1, row_bytes=A_SHARD_BYTES, src_stride=A_SHARD_BYTES, dst_stride=A_SHARD_BYTES,
        completion_event=e_la0,
    )
    builder.oracle(entrypoint_id, 1, d_la0, cmd_la0.command_id, A.DMA_KIND.LOAD, 0x100000, 1, A_SHARD_BYTES, A_SHARD_BYTES)

    cmd_lw0 = stream0.command(A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_w, s_w0, core0_w, RO),))
    d_lw0 = builder.dma(
        cmd_lw0, A.DMA_KIND.LOAD,
        src=_hbm(t_w, s_w0, 0x200000), dst=_sram(t_w, s_w0, 0, 0x2000),
        rows=1, row_bytes=W_SHARD_BYTES, src_stride=W_SHARD_BYTES, dst_stride=W_SHARD_BYTES,
        completion_event=e_lw0,
    )
    builder.oracle(entrypoint_id, 1, d_lw0, cmd_lw0.command_id, A.DMA_KIND.LOAD, 0x200000, 1, W_SHARD_BYTES, W_SHARD_BYTES)

    gemm0_attr = builder.gemm_attr(1, M, N, TP_K, FP16, A.DTYPE.FP32)
    stream0.command(
        A.OPCODE.GEMM, waits=(e_la0, e_lw0),
        operands=((t_a, s_a0, core0_a, RO), (t_w, s_w0, core0_w, RO), (t_partial, s_c0, core0_c, RW)),
        signal_event=e_g0, attr_index=gemm0_attr,
    )

    cmd_p2p = stream0.command(A.OPCODE.DMA_P2P_PUSH, waits=(e_g0,), operands=((t_partial, s_c0, core0_c, RO),))
    d_p2p = builder.dma(
        cmd_p2p, A.DMA_KIND.P2P_PUSH,
        src=_sram(t_partial, s_c0, 0, 0x4000), dst=_peer(t_partial, s_peer, 1, 0x4000),
        rows=1, row_bytes=PARTIAL_BYTES, src_stride=PARTIAL_BYTES, dst_stride=PARTIAL_BYTES,
        transfer_id=1,
        completion_event=e_p2p,
    )
    builder.oracle(entrypoint_id, 1, d_p2p, cmd_p2p.command_id, A.DMA_KIND.P2P_PUSH, 0x4000, 1, PARTIAL_BYTES, PARTIAL_BYTES)

    stream0.command(A.OPCODE.REQUEST_END, waits=(e_p2p,), signal_event=e_end0)
    stream0.command(A.OPCODE.HALT, waits=(e_end0,))

    cmd_la1 = stream1.command(A.OPCODE.DMA_LOAD, operands=((t_a, s_a1, core1_a, RO),))
    d_la1 = builder.dma(
        cmd_la1, A.DMA_KIND.LOAD,
        src=_hbm(t_a, s_a1, 0x100000 + A_SHARD_BYTES), dst=_sram(t_a, s_a1, 1, 0x0000),
        rows=1, row_bytes=A_SHARD_BYTES, src_stride=A_SHARD_BYTES, dst_stride=A_SHARD_BYTES,
        completion_event=e_la1,
    )
    builder.oracle(entrypoint_id, 1, d_la1, cmd_la1.command_id, A.DMA_KIND.LOAD, 0x100000 + A_SHARD_BYTES, 1, A_SHARD_BYTES, A_SHARD_BYTES)

    cmd_lw1 = stream1.command(A.OPCODE.DMA_LOAD, operands=((t_w, s_w1, core1_w, RO),))
    d_lw1 = builder.dma(
        cmd_lw1, A.DMA_KIND.LOAD,
        src=_hbm(t_w, s_w1, 0x200000 + W_SHARD_BYTES), dst=_sram(t_w, s_w1, 1, 0x2000),
        rows=1, row_bytes=W_SHARD_BYTES, src_stride=W_SHARD_BYTES, dst_stride=W_SHARD_BYTES,
        completion_event=e_lw1,
    )
    builder.oracle(entrypoint_id, 1, d_lw1, cmd_lw1.command_id, A.DMA_KIND.LOAD, 0x200000 + W_SHARD_BYTES, 1, W_SHARD_BYTES, W_SHARD_BYTES)

    gemm1_attr = builder.gemm_attr(1, M, N, TP_K, FP16, A.DTYPE.FP32)
    stream1.command(
        A.OPCODE.GEMM,
        waits=(e_la1, e_lw1),
        operands=((t_a, s_a1, core1_a, RO), (t_w, s_w1, core1_w, RO), (t_partial, s_c1, core1_c, RW)),
        signal_event=e_g1, attr_index=gemm1_attr,
    )

    recv_attr = builder.recv_wait_attr(1)
    stream1.command(
        A.OPCODE.RECV_WAIT,
        waits=(e_g1,),
        operands=((t_partial, s_peer, core1_peer, RW),),
        signal_event=e_recv,
        attr_index=recv_attr,
    )

    reduce_attr = builder.reduce_attr(PARTIAL_BYTES // ELEM, FP16, A.DTYPE.FP32, op=0)
    stream1.command(
        A.OPCODE.LOCAL_REDUCE,
        waits=(e_recv,),
        operands=((t_partial, s_peer, core1_peer, RW), (t_partial, s_c1, core1_c, RW)),
        signal_event=e_red,
        attr_index=reduce_attr,
    )

    cmd_store = stream1.command(A.OPCODE.DMA_STORE, waits=(e_red,), operands=((t_final, s_final, core1_c, RW),))
    d_store = builder.dma(
        cmd_store, A.DMA_KIND.STORE,
        src=_sram(t_final, s_final, 1, 0x6000), dst=_hbm(t_final, 0, 0x300000),
        rows=1, row_bytes=PARTIAL_BYTES, src_stride=PARTIAL_BYTES, dst_stride=PARTIAL_BYTES,
        completion_event=e_store,
    )
    builder.oracle(entrypoint_id, 1, d_store, cmd_store.command_id, A.DMA_KIND.STORE, 0x6000, 1, PARTIAL_BYTES, PARTIAL_BYTES)

    stream1.command(A.OPCODE.HALT, waits=(e_store,))

    return builder.build()


def build_fill_program(arch):
    """DC-10 coverage: DMA_FILL plus a zero-byte LOAD (>= 1 control cycle)."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_fill")
    entrypoint_id = builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)

    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (8, 8))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (8, 8))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x300000)

    a_buf = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x2000, 128, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (8, 8), 128)
    s_out = builder.shard(t_out, 0, a_out, (8, 8), 128)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_fill = builder.event()
    e_zero = builder.event()
    e_gemm = builder.event()
    e_store = builder.event()
    e_end = builder.event()

    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    fill_attr = builder.fill_attr(0x0123456789ABCDEF)
    cmd_fill = stream.command(
        A.OPCODE.DMA_FILL,
        waits=(e_begin,),
        operands=((t_in, s_in, a_buf, RW),),
        attr_index=fill_attr,
    )
    d_fill = builder.dma(
        cmd_fill,
        A.DMA_KIND.LOCAL_FILL,
        src=_sram(t_in, s_in, 0, 0x0000),
        dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1,
        row_bytes=128,
        src_stride=128,
        dst_stride=128,
        completion_event=e_fill,
    )
    builder.oracle(entrypoint_id, 1, d_fill, cmd_fill.command_id, A.DMA_KIND.LOCAL_FILL, 0, 1, 128, 128)

    cmd_zero = stream.command(
        A.OPCODE.DMA_LOAD,
        waits=(e_begin,),
        operands=((t_in, s_in, a_buf, RO),),
    )
    d_zero = builder.dma(
        cmd_zero,
        A.DMA_KIND.LOAD,
        src=_hbm(t_in, s_in, 0x100000),
        dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1,
        row_bytes=0,
        src_stride=0,
        dst_stride=0,
        completion_event=e_zero,
    )
    builder.oracle(entrypoint_id, 1, d_zero, cmd_zero.command_id, A.DMA_KIND.LOAD, 0x100000, 1, 0, 0)

    gemm_attr = builder.gemm_attr(1, 8, 8, 8, FP16, A.DTYPE.FP32)
    stream.command(
        A.OPCODE.GEMM,
        waits=(e_fill, e_zero),
        operands=((t_in, s_in, a_buf, RO), (t_in, s_in, a_buf, RO), (t_out, s_out, a_out, RW)),
        signal_event=e_gemm,
        attr_index=gemm_attr,
    )

    cmd_store = stream.command(A.OPCODE.DMA_STORE, waits=(e_gemm,), operands=((t_out, s_out, a_out, RW),))
    d_store = builder.dma(
        cmd_store,
        A.DMA_KIND.STORE,
        src=_sram(t_out, s_out, 0, 0x2000),
        dst=_hbm(t_out, s_out, 0x300000),
        rows=1,
        row_bytes=128,
        src_stride=128,
        dst_stride=128,
        completion_event=e_store,
    )
    builder.oracle(entrypoint_id, 1, d_store, cmd_store.command_id, A.DMA_KIND.STORE, 0x2000, 1, 128, 128)

    stream.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


REPEAT_COUNT = 3


def build_repeat_program(arch):
    """DC-35 coverage: REPEAT replays generations of a DMA+compute window.

    Stream: BEGIN -> LOAD -> GEMM -> ELEMENTWISE -> REPEAT(count=3,
    repeat_count=3) -> REQUEST_END -> HALT.  Generation 0 runs as the normal
    pre-REPEAT window; generations 1 and 2 replay it, so the LOAD descriptor
    executes exactly REPEAT_COUNT times and each compute digest appears
    REPEAT_COUNT times.
    """
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_repeat")
    entrypoint_id = builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)

    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (8, 8))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (8, 8))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x300000)

    a_buf = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x2000, 128, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (8, 8), 128)
    s_out = builder.shard(t_out, 0, a_out, (8, 8), 128)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_load = builder.event()
    e_gemm = builder.event()
    e_elem = builder.event()
    e_end = builder.event()

    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_load = stream.command(A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s_in, a_buf, RO),))
    d_load = builder.dma(
        cmd_load, A.DMA_KIND.LOAD,
        src=_hbm(t_in, s_in, 0x100000), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_load,
    )
    builder.oracle(entrypoint_id, 1, d_load, cmd_load.command_id, A.DMA_KIND.LOAD, 0x100000, 1, 128, 128)

    gemm_attr = builder.gemm_attr(1, 8, 8, 8, FP16, A.DTYPE.FP32)
    stream.command(
        A.OPCODE.GEMM, waits=(e_load,),
        operands=((t_in, s_in, a_buf, RO), (t_in, s_in, a_buf, RO), (t_out, s_out, a_out, RW)),
        signal_event=e_gemm, attr_index=gemm_attr,
    )

    elem_attr = builder._attr(
        A.ATTR_KIND.ELEMENTWISE_V1, (64, FP16, 0, 1, 0),
        ("element_count", "dtype", "op", "ops_per_element", "reserved"),
    )
    stream.command(
        A.OPCODE.ELEMENTWISE, waits=(e_gemm,),
        operands=((t_out, s_out, a_out, RW),),
        signal_event=e_elem, attr_index=elem_attr,
    )

    # REPEAT carries no explicit wait on the window tail: generation-0 drain
    # is enforced by the runtime (spec 4.1), not by program order.
    repeat_attr = builder.repeat_attr(1, 3, REPEAT_COUNT)
    stream.command(A.OPCODE.REPEAT, attr_index=repeat_attr)

    stream.command(A.OPCODE.REQUEST_END, waits=(e_elem,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_poison_program(arch):
    """DC-16 coverage: compute reads an allocation whose producer has not
    committed; the runtime must fault E_TENSOR_NOT_RESIDENT at issue."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_poison")
    builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (8, 8))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (8, 8))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    a_buf = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x2000, 128, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (8, 8), 128)
    s_out = builder.shard(t_out, 0, a_out, (8, 8), 128)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_gemm = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    # Deliberately NO DMA_LOAD producer before the GEMM reads a_buf.
    stream.command(
        A.OPCODE.GEMM,
        waits=(e_begin,),
        operands=((t_in, s_in, a_buf, RO), (t_in, s_in, a_buf, RO), (t_out, s_out, a_out, RW)),
        signal_event=e_gemm,
        attr_index=builder.gemm_attr(1, 8, 8, 8, FP16, A.DTYPE.FP32),
    )
    stream.command(A.OPCODE.REQUEST_END, waits=(e_gemm,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_dma_edge_program(arch):
    """Gate 2 DMA edge-case sweep on one core: 1-byte aligned/unaligned,
    bus-width -1/+1, max-burst -1/+1, 4 KiB boundary crossing and a
    multi-burst split, each round-tripped LOAD -> STORE through the real
    AXI-over-Garnet network (unseeded destination ranges prove content
    flow)."""
    from mesh_ir.builder import ProgramBuilder

    W = arch.axi_data_bytes
    CAP = arch.axi_max_burst_beats * W

    pairs = [
        (1, 0x100000, 0x200000),
        (1, 0x100041, 0x200040),
        (W - 1, 0x100080, 0x200080),
        (W + 1, 0x1000C0, 0x2000C0),
        (64, 0x100FF0, 0x201000),
        (CAP - 1, 0x101100, 0x201100),
        (CAP + 1, 0x101400, 0x201400),
        (700, 0x101800, 0x201800),
    ]

    builder = ProgramBuilder(arch, "golden_dma_edge")
    builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (64, 64))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (64, 64))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x200000)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    entrypoint_id = 1
    sram_offset = 0
    prev_event = e_begin
    for size, src_off, dst_off in pairs:
        alloc_bytes = (size + 63) // 64 * 64
        alloc = builder.allocation(0, sram_offset, alloc_bytes,
                                   arch.sram_base_alignment_bytes)
        s_in = builder.shard(t_in, 0, alloc, (size,), size)
        s_out = builder.shard(t_out, 0, alloc, (size,), size)
        e_load = builder.event()
        e_store = builder.event()

        cmd_load = stream.command(
            A.OPCODE.DMA_LOAD, waits=(prev_event,),
            operands=((t_in, s_in, alloc, RO),),
        )
        d_load = builder.dma(
            cmd_load, A.DMA_KIND.LOAD,
            src=_hbm(t_in, 0, src_off), dst=_sram(t_in, s_in, 0, sram_offset),
            rows=1, row_bytes=size, src_stride=size, dst_stride=size,
            completion_event=e_load,
        )
        builder.oracle(entrypoint_id, 1, d_load, cmd_load.command_id,
                       A.DMA_KIND.LOAD, src_off, 1, size, size)

        cmd_store = stream.command(
            A.OPCODE.DMA_STORE, waits=(e_load,),
            operands=((t_out, s_out, alloc, RW),),
        )
        d_store = builder.dma(
            cmd_store, A.DMA_KIND.STORE,
            src=_sram(t_out, s_out, 0, sram_offset),
            dst=_hbm(t_out, 0, dst_off),
            rows=1, row_bytes=size, src_stride=size, dst_stride=size,
            completion_event=e_store,
        )
        builder.oracle(entrypoint_id, 1, d_store, cmd_store.command_id,
                       A.DMA_KIND.STORE, dst_off, 1, size, size)
        prev_event = e_store
        sram_offset += alloc_bytes

    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_END, waits=(prev_event,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_dma_error_program(arch):
    """Gate 2 read-error drain: the LOAD targets an address inside the arch
    HBM region but outside every Garnet target range, so the initiator
    address decoder routes it to the error target and the full burst
    returns DECERR.  The failing descriptor must drain every R beat,
    discard its bytes, latch the instance error and cancel downstream
    commands without requiring REQUEST_END/HALT."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_dma_error")
    builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (8, 8))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (8, 8))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x200000)
    a_buf = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x2000, 128, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (8, 8), 128)
    s_out = builder.shard(t_out, 0, a_out, (8, 8), 128)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_load = builder.event()
    e_gemm = builder.event()
    e_store = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_load = stream.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s_in, a_buf, RO),),
    )
    d_load = builder.dma(
        cmd_load, A.DMA_KIND.LOAD,
        src=_hbm(t_in, 0, 0x800000), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_load,
    )
    builder.oracle(1, 1, d_load, cmd_load.command_id, A.DMA_KIND.LOAD, 0x800000, 1, 128, 128)

    stream.command(
        A.OPCODE.GEMM, waits=(e_load,),
        operands=((t_in, s_in, a_buf, RO), (t_in, s_in, a_buf, RO),
                  (t_out, s_out, a_out, RW)),
        signal_event=e_gemm, attr_index=builder.gemm_attr(1, 8, 8, 8, FP16, A.DTYPE.FP32),
    )
    cmd_store = stream.command(
        A.OPCODE.DMA_STORE, waits=(e_gemm,), operands=((t_out, s_out, a_out, RW),),
    )
    d_store = builder.dma(
        cmd_store, A.DMA_KIND.STORE,
        src=_sram(t_out, s_out, 0, 0x2000), dst=_hbm(t_out, 0, 0x200000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_store,
    )
    builder.oracle(1, 1, d_store, cmd_store.command_id, A.DMA_KIND.STORE, 0x200000, 1, 128, 128)
    stream.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_dma_write_error_program(arch):
    """Gate 2 write-error drain: a STORE of two bursts whose second burst is
    faulted (SLVERR via the target's per-UID plan).  The first burst's
    committed bytes stay committed (no rollback); the faulted burst drains
    all W beats, returns one B, and the descriptor terminates ERROR with
    exactly its bytes accounted as drained-uncommitted."""
    from mesh_ir.builder import ProgramBuilder

    size = arch.axi_max_burst_beats * arch.axi_data_bytes * 2
    builder = ProgramBuilder(arch, "golden_dma_write_error")
    builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (64, 64))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (64, 64))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x200000)
    a_buf = builder.allocation(0, 0x0000, size, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (size,), size)
    s_out = builder.shard(t_out, 0, a_buf, (size,), size)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_load = builder.event()
    e_store = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_load = stream.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s_in, a_buf, RO),),
    )
    d_load = builder.dma(
        cmd_load, A.DMA_KIND.LOAD,
        src=_hbm(t_in, 0, 0x100000), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=size, src_stride=size, dst_stride=size,
        completion_event=e_load,
    )
    builder.oracle(1, 1, d_load, cmd_load.command_id, A.DMA_KIND.LOAD, 0x100000, 1, size, size)

    cmd_store = stream.command(
        A.OPCODE.DMA_STORE, waits=(e_load,), operands=((t_out, s_out, a_buf, RW),),
    )
    d_store = builder.dma(
        cmd_store, A.DMA_KIND.STORE,
        src=_sram(t_out, s_out, 0, 0x0000), dst=_hbm(t_out, 0, 0x200000),
        rows=1, row_bytes=size, src_stride=size, dst_stride=size,
        completion_event=e_store,
    )
    builder.oracle(1, 1, d_store, cmd_store.command_id, A.DMA_KIND.STORE, 0x200000, 1, size, size)
    stream.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_dma_fence_program(arch):
    """DC-28: AXI_FENCE waits only pre-fence-accepted transactions.  The
    fast pre-fence LOAD completes long before the post-fence big STORE
    (issued from a second stream after a tiny compute event), so a fence
    that wrongly waits for all outstanding DMA would finish far too late."""
    from mesh_ir.builder import ProgramBuilder

    small = 64
    big = arch.axi_max_burst_beats * arch.axi_data_bytes * 8
    builder = ProgramBuilder(arch, "golden_dma_fence")
    builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (64, 64))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (64, 64))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x200000)
    a_small = builder.allocation(0, 0x0000, small, arch.sram_base_alignment_bytes)
    a_big = builder.allocation(0, 0x2000, big, arch.sram_base_alignment_bytes)
    s_small = builder.shard(t_in, 0, a_small, (small,), small)
    s_big = builder.shard(t_out, 0, a_big, (big,), big)

    control = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    worker = builder.stream(0, 1)

    e_begin = builder.event()
    e_load = builder.event()
    e_fence = builder.event()
    e_end = builder.event()
    e_fill_done = builder.event()
    e_vec = builder.event()
    e_store = builder.event()

    control.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    cmd_load = control.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s_small, a_small, RO),),
    )
    d_load = builder.dma(
        cmd_load, A.DMA_KIND.LOAD,
        src=_hbm(t_in, 0, 0x100000), dst=_sram(t_in, s_small, 0, 0x0000),
        rows=1, row_bytes=small, src_stride=small, dst_stride=small,
        completion_event=e_load,
    )
    builder.oracle(1, 1, d_load, cmd_load.command_id, A.DMA_KIND.LOAD, 0x100000, 1, small, small)
    # The fence issues right behind the in-flight LOAD (no e_load wait):
    # its watermark snapshot contains exactly that pre-fence transaction.
    control.command(A.OPCODE.AXI_FENCE, waits=(e_begin,), signal_event=e_fence,
                    attr_index=builder.fence_attr(A.FENCE_SCOPE.ALL_INSTANCE))
    control.command(A.OPCODE.REQUEST_END, waits=(e_fence,), signal_event=e_end)
    control.command(A.OPCODE.HALT, waits=(e_end,))

    cmd_fill = worker.command(
        A.OPCODE.DMA_FILL, waits=(e_begin,),
        operands=((t_out, s_big, a_big, RW),),
        attr_index=builder.fill_attr(0xA5),
    )
    d_fill = builder.dma(
        cmd_fill, A.DMA_KIND.LOCAL_FILL,
        src=_sram(t_out, s_big, 0, 0x2000), dst=_sram(t_out, s_big, 0, 0x2000),
        rows=1, row_bytes=big, src_stride=big, dst_stride=big,
        completion_event=e_fill_done,
    )
    builder.oracle(1, 1, d_fill, cmd_fill.command_id,
                   A.DMA_KIND.LOCAL_FILL, 0x2000, 1, big, big)
    worker.command(
        A.OPCODE.ELEMENTWISE, waits=(e_fill_done,),
        operands=((t_out, s_big, a_big, RW),),
        signal_event=e_vec, attr_index=builder.elementwise_attr(8, FP16),
    )
    cmd_store = worker.command(
        A.OPCODE.DMA_STORE, waits=(e_vec,), operands=((t_out, s_big, a_big, RW),),
    )
    d_store = builder.dma(
        cmd_store, A.DMA_KIND.STORE,
        src=_sram(t_out, s_big, 0, 0x2000), dst=_hbm(t_out, 0, 0x200000),
        rows=1, row_bytes=big, src_stride=big, dst_stride=big,
        completion_event=e_store,
    )
    builder.oracle(1, 1, d_store, cmd_store.command_id, A.DMA_KIND.STORE, 0x200000, 1, big, big)
    return builder.build()


def build_dma_pin_program(arch):
    """DC-32: two LOADs into the same SRAM allocation with no event chain
    between them; the in-flight descriptor pins the allocation so the
    second admit waits (backpressure, not corruption)."""
    from mesh_ir.builder import ProgramBuilder

    size = 128
    builder = ProgramBuilder(arch, "golden_dma_pin")
    builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (8, 8))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (8, 8))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x200000)
    a_buf = builder.allocation(0, 0x0000, size, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (size,), size)
    s_out = builder.shard(t_out, 0, a_buf, (size,), size)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_load1 = builder.event()
    e_load2 = builder.event()
    e_store = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_load1 = stream.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s_in, a_buf, RO),),
    )
    d_load1 = builder.dma(
        cmd_load1, A.DMA_KIND.LOAD,
        src=_hbm(t_in, 0, 0x100000), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=size, src_stride=size, dst_stride=size,
        completion_event=e_load1,
    )
    builder.oracle(1, 1, d_load1, cmd_load1.command_id, A.DMA_KIND.LOAD, 0x100000, 1, size, size)

    cmd_load2 = stream.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s_in, a_buf, RO),),
    )
    d_load2 = builder.dma(
        cmd_load2, A.DMA_KIND.LOAD,
        src=_hbm(t_in, 0, 0x100040), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=size, src_stride=size, dst_stride=size,
        completion_event=e_load2,
    )
    builder.oracle(1, 1, d_load2, cmd_load2.command_id, A.DMA_KIND.LOAD, 0x100040, 1, size, size)

    cmd_store = stream.command(
        A.OPCODE.DMA_STORE, waits=(e_load2,), operands=((t_out, s_out, a_buf, RW),),
    )
    d_store = builder.dma(
        cmd_store, A.DMA_KIND.STORE,
        src=_sram(t_out, s_out, 0, 0x0000), dst=_hbm(t_out, 0, 0x200000),
        rows=1, row_bytes=size, src_stride=size, dst_stride=size,
        completion_event=e_store,
    )
    builder.oracle(1, 1, d_store, cmd_store.command_id, A.DMA_KIND.STORE, 0x200000, 1, size, size)
    stream.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


SRAM_PLACEMENT_PARALLEL = (0x0000, 0x0080, 0x0100)   # banks 0, 4, 8
SRAM_PLACEMENT_CONFLICT = (0x0000, 0x0200, 0x0400)   # all bank 0


def _build_sram_placement_program(arch, placement, name):
    """Identical GEMM (shape/dtype/attrs/bytes) with only operand SRAM
    placement varying: conflict placements must complete later than
    parallel placements (SRAM timing participates in completion)."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, name)
    entrypoint_id = builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (8, 8))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (8, 8))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x300000)
    a0 = builder.allocation(0, placement[0], 128, arch.sram_base_alignment_bytes)
    a1 = builder.allocation(0, placement[1], 128, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, placement[2], 128, arch.sram_base_alignment_bytes)
    s0 = builder.shard(t_in, 0, a0, (8, 8), 128)
    s1 = builder.shard(t_in, 0, a1, (8, 8), 128)
    s_out = builder.shard(t_out, 0, a_out, (8, 8), 128)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_l0 = builder.event()
    e_l1 = builder.event()
    e_gemm = builder.event()
    e_store = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_l0 = stream.command(A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s0, a0, RO),))
    d0 = builder.dma(cmd_l0, A.DMA_KIND.LOAD, src=_hbm(t_in, s0, 0x100000),
                     dst=_sram(t_in, s0, 0, placement[0]), rows=1, row_bytes=128,
                     src_stride=128, dst_stride=128, completion_event=e_l0)
    builder.oracle(entrypoint_id, 1, d0, cmd_l0.command_id, A.DMA_KIND.LOAD, 0x100000, 1, 128, 128)

    cmd_l1 = stream.command(A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s1, a1, RO),))
    d1 = builder.dma(cmd_l1, A.DMA_KIND.LOAD, src=_hbm(t_in, s1, 0x100080),
                     dst=_sram(t_in, s1, 0, placement[1]), rows=1, row_bytes=128,
                     src_stride=128, dst_stride=128, completion_event=e_l1)
    builder.oracle(entrypoint_id, 1, d1, cmd_l1.command_id, A.DMA_KIND.LOAD, 0x100080, 1, 128, 128)

    stream.command(A.OPCODE.GEMM, waits=(e_l0, e_l1),
                   operands=((t_in, s0, a0, RO), (t_in, s1, a1, RO), (t_out, s_out, a_out, RW)),
                   signal_event=e_gemm,
                   attr_index=builder.gemm_attr(1, 8, 8, 8, FP16, A.DTYPE.FP32))

    cmd_store = stream.command(A.OPCODE.DMA_STORE, waits=(e_gemm,), operands=((t_out, s_out, a_out, RW),))
    ds = builder.dma(cmd_store, A.DMA_KIND.STORE, src=_sram(t_out, s_out, 0, placement[2]),
                     dst=_hbm(t_out, s_out, 0x300000), rows=1, row_bytes=128,
                     src_stride=128, dst_stride=128, completion_event=e_store)
    builder.oracle(entrypoint_id, 1, ds, cmd_store.command_id, A.DMA_KIND.STORE, 0x300000, 1, 128, 128)

    stream.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_sram_parallel_program(arch):
    return _build_sram_placement_program(arch, SRAM_PLACEMENT_PARALLEL, "golden_sram_parallel")


def build_sram_conflict_program(arch):
    return _build_sram_placement_program(arch, SRAM_PLACEMENT_CONFLICT, "golden_sram_conflict")


def build_poison_elementwise_inplace_program(arch):
    """Single READ_WRITE operand ELEMENTWISE with no producer: in-place ops
    read their operand, so the issue must fault E_TENSOR_NOT_RESIDENT."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_poison_ew")
    builder.entrypoint("main", "b1", lifecycle_core=0, lifecycle_stream=0)
    t = builder.tensor("io", A.TENSOR_ROLE.ACTIVATION, FP16, A.STORAGE_CLASS.CORE_SRAM, RW, (8, 8))
    a = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    s = builder.shard(t, 0, a, (8, 8), 128)
    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_done = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    stream.command(
        A.OPCODE.ELEMENTWISE,
        waits=(e_begin,),
        operands=((t, s, a, RW),),
        signal_event=e_done,
        attr_index=builder.elementwise_attr(64, FP16),
    )
    stream.command(A.OPCODE.REQUEST_END, waits=(e_done,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_poison_store_program(arch):
    """STORE of a never-written CORE_SRAM allocation to HBM."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_poison_store")
    builder.entrypoint("main", "b1", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (8, 8))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (8, 8))
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x300000)
    a = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    s = builder.shard(t_out, 0, a, (8, 8), 128)
    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_store = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    cmd_store = stream.command(A.OPCODE.DMA_STORE, waits=(e_begin,),
                               operands=((t_out, s, a, RW),))
    ds = builder.dma(cmd_store, A.DMA_KIND.STORE, src=_sram(t_out, s, 0, 0x0000),
                     dst=_hbm(t_out, s, 0x300000), rows=1, row_bytes=128,
                     src_stride=128, dst_stride=128, completion_event=e_store)
    builder.oracle(1, 1, ds, cmd_store.command_id, A.DMA_KIND.STORE, 0x300000, 1, 128, 128)
    stream.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    del t_in
    return builder.build()


def build_poison_reduce_dst_old_program(arch):
    """LOCAL_REDUCE whose accumulator (result operand, read as dst_old) has
    no producer: must fault E_TENSOR_NOT_RESIDENT."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_poison_reduce")
    builder.entrypoint("main", "b1", lifecycle_core=0, lifecycle_stream=0)
    t = builder.tensor("io", A.TENSOR_ROLE.ACTIVATION, FP16, A.STORAGE_CLASS.CORE_SRAM, RW, (8, 8))
    a = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    s = builder.shard(t, 0, a, (8, 8), 128)
    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    e_done = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    stream.command(
        A.OPCODE.LOCAL_REDUCE,
        waits=(e_begin,),
        operands=((t, s, a, RW),),
        signal_event=e_done,
        attr_index=builder.reduce_attr(64, FP16, A.DTYPE.FP32),
    )
    stream.command(A.OPCODE.REQUEST_END, waits=(e_done,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_dma_shapes_program(arch):
    """Gate 2 shape coverage on the real network: PREFETCH (LOAD path),
    DMA_FILL, a multi-row P2P push with asymmetric local/remote strides,
    a multi-row STORE with asymmetric strides, and one cross-core NORMAL
    event dependency (core 1 waits an event produced on core 0)."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_dma_shapes")
    builder.entrypoint("main", "b1_m8n8k8", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (64,))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (64,))
    t_partial = builder.tensor("partial", A.TENSOR_ROLE.ACTIVATION, FP16, A.STORAGE_CLASS.CORE_SRAM, RW, (64,), sharding_id=2)
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x300000)

    a_pf = builder.allocation(0, 0x0000, 64, arch.sram_base_alignment_bytes)
    a_f = builder.allocation(0, 0x0080, 128, arch.sram_base_alignment_bytes)
    a_peer = builder.allocation(1, 0x0000, 256, arch.sram_base_alignment_bytes)
    a_c1 = builder.allocation(1, 0x0200, 256, arch.sram_base_alignment_bytes)
    s_pf = builder.shard(t_in, 0, a_pf, (64,), 64)
    s_f = builder.shard(t_partial, 0, a_f, (128,), 128)
    s_peer = builder.shard(t_partial, 1, a_peer, (128,), 192, sharding_id=2)
    s_c1 = builder.shard(t_partial, 1, a_c1, (128,), 128, sharding_id=2)
    s_out = builder.shard(t_out, 1, a_c1, (128,), 128)

    stream0 = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_pf = builder.event()
    e_go = builder.event()
    e_p2p = builder.event()
    e_end0 = builder.event()
    e_c1f = builder.event()
    e_recv = builder.event()
    e_red = builder.event()
    e_store = builder.event()

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_pf = stream0.command(
        A.OPCODE.DMA_PREFETCH, waits=(e_begin,),
        operands=((t_in, s_pf, a_pf, RO),),
    )
    d_pf = builder.dma(
        cmd_pf, A.DMA_KIND.PREFETCH,
        src=_hbm(t_in, 0, 0x100000), dst=_sram(t_in, s_pf, 0, 0x0000),
        rows=1, row_bytes=64, src_stride=64, dst_stride=64,
        completion_event=e_pf,
    )
    builder.oracle(1, 1, d_pf, cmd_pf.command_id, A.DMA_KIND.PREFETCH, 0x100000, 1, 64, 64)

    cmd_fill = stream0.command(
        A.OPCODE.DMA_FILL, waits=(e_pf,),
        operands=((t_partial, s_f, a_f, RW),),
        attr_index=builder.fill_attr(0xA5),
    )
    d_fill = builder.dma(
        cmd_fill, A.DMA_KIND.LOCAL_FILL,
        src=_sram(t_partial, s_f, 0, 0x0080), dst=_sram(t_partial, s_f, 0, 0x0080),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_go,
    )
    builder.oracle(1, 1, d_fill, cmd_fill.command_id, A.DMA_KIND.LOCAL_FILL, 0x0080, 1, 128, 128)

    cmd_p2p = stream0.command(
        A.OPCODE.DMA_P2P_PUSH, waits=(e_go,),
        operands=((t_partial, s_f, a_f, RO),),
    )
    d_p2p = builder.dma(
        cmd_p2p, A.DMA_KIND.P2P_PUSH,
        src=_sram(t_partial, s_f, 0, 0x0080),
        dst=_peer(t_partial, s_peer, 1, 0x0000),
        rows=2, row_bytes=64, src_stride=64, dst_stride=128,
        transfer_id=7, completion_event=e_p2p,
    )
    builder.oracle(1, 1, d_p2p, cmd_p2p.command_id, A.DMA_KIND.P2P_PUSH, 0x0000, 2, 64, 128)

    stream0.command(A.OPCODE.REQUEST_END, waits=(e_p2p,), signal_event=e_end0)
    stream0.command(A.OPCODE.HALT, waits=(e_end0,))

    stream1.command(A.OPCODE.EVENT_WAIT, waits=(e_go,))
    # The reduce accumulates into its result operand, so the accumulator
    # needs a committed producer first: fill it locally.
    cmd_c1f = stream1.command(
        A.OPCODE.DMA_FILL, waits=(e_go,),
        operands=((t_partial, s_c1, a_c1, RW),),
        attr_index=builder.fill_attr(0x00),
    )
    d_c1f = builder.dma(
        cmd_c1f, A.DMA_KIND.LOCAL_FILL,
        src=_sram(t_partial, s_c1, 1, 0x0200),
        dst=_sram(t_partial, s_c1, 1, 0x0200),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_c1f,
    )
    builder.oracle(1, 1, d_c1f, cmd_c1f.command_id,
                   A.DMA_KIND.LOCAL_FILL, 0x0100, 1, 128, 128)
    stream1.command(
        A.OPCODE.RECV_WAIT, waits=(e_go,),
        operands=((t_partial, s_peer, a_peer, RW),),
        signal_event=e_recv, attr_index=builder.recv_wait_attr(7),
    )
    stream1.command(
        A.OPCODE.LOCAL_REDUCE, waits=(e_recv, e_c1f),
        operands=((t_partial, s_peer, a_peer, RW), (t_partial, s_c1, a_c1, RW)),
        signal_event=e_red, attr_index=builder.reduce_attr(64, FP16, A.DTYPE.FP32),
    )
    cmd_store = stream1.command(
        A.OPCODE.DMA_STORE, waits=(e_red,),
        operands=((t_out, s_out, a_c1, RW),),
    )
    d_store = builder.dma(
        cmd_store, A.DMA_KIND.STORE,
        src=_sram(t_out, s_out, 1, 0x0200), dst=_hbm(t_out, 0, 0x300000),
        rows=2, row_bytes=64, src_stride=64, dst_stride=256,
        completion_event=e_store,
    )
    builder.oracle(1, 1, d_store, cmd_store.command_id, A.DMA_KIND.STORE, 0x300000, 2, 64, 256)
    stream1.command(A.OPCODE.HALT, waits=(e_store,))
    return builder.build()


def build_zero_dma_program(arch):
    """Zero-length DMA semantics: rows==0 and row_bytes==0 descriptors of
    every kind are legal, produce zero traffic, and complete at the normal
    descriptor completion point (spec amendment, Gate 2 fix)."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_zero_dma")
    entrypoint_id = builder.entrypoint("main", "zero_dma", lifecycle_core=0, lifecycle_stream=0)

    t_a = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (64, 64))
    t_c = builder.tensor("scratch", A.TENSOR_ROLE.ACTIVATION, FP16, A.STORAGE_CLASS.CORE_SRAM, RW, (64, 64))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_a, 0x100000)

    core0_scratch = builder.allocation(0, 0x0000, 64, arch.sram_base_alignment_bytes)
    core1_peer = builder.allocation(1, 0x0000, 64, arch.sram_base_alignment_bytes)
    s_input = builder.shard(t_a, 0, core0_scratch, (32,), 64)
    s_scratch = builder.shard(t_c, 0, core0_scratch, (32,), 64)
    s_peer = builder.shard(t_c, 1, core1_peer, (32,), 64)

    stream0 = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_load_rows = builder.event()
    e_load_bytes = builder.event()
    e_fill = builder.event()
    e_p2p = builder.event()
    e_end0 = builder.event()
    e_recv = builder.event()

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_load_rows = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_a, s_input, core0_scratch, RO),),
    )
    d_load_rows = builder.dma(
        cmd_load_rows, A.DMA_KIND.LOAD,
        src=_hbm(t_a, 0, 0x100000), dst=_sram(t_a, s_input, 0, 0x0000),
        rows=0, row_bytes=64, src_stride=64, dst_stride=64,
        completion_event=e_load_rows,
    )
    builder.oracle(entrypoint_id, 1, d_load_rows, cmd_load_rows.command_id,
                   A.DMA_KIND.LOAD, 0x100000, 0, 64, 64)

    cmd_load_bytes = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_load_rows,),
        operands=((t_a, s_input, core0_scratch, RO),),
    )
    d_load_bytes = builder.dma(
        cmd_load_bytes, A.DMA_KIND.LOAD,
        src=_hbm(t_a, 0, 0x100040), dst=_sram(t_a, s_input, 0, 0x0000),
        rows=1, row_bytes=0, src_stride=0, dst_stride=0,
        completion_event=e_load_bytes,
    )
    builder.oracle(entrypoint_id, 1, d_load_bytes, cmd_load_bytes.command_id,
                   A.DMA_KIND.LOAD, 0x100040, 1, 0, 0)

    fill_attr = builder.fill_attr(0x5A)
    cmd_fill = stream0.command(
        A.OPCODE.DMA_FILL, waits=(e_load_bytes,),
        operands=((t_c, s_scratch, core0_scratch, RW),),
        attr_index=fill_attr,
    )
    d_fill = builder.dma(
        cmd_fill, A.DMA_KIND.LOCAL_FILL,
        src=_sram(t_c, s_scratch, 0, 0x0000), dst=_sram(t_c, s_scratch, 0, 0x0000),
        rows=0, row_bytes=64, src_stride=64, dst_stride=64,
        completion_event=e_fill,
    )
    builder.oracle(entrypoint_id, 1, d_fill, cmd_fill.command_id,
                   A.DMA_KIND.LOCAL_FILL, 0, 0, 64, 64)

    e_fill0 = builder.event()
    fill0_attr = builder.fill_attr(0x3C)
    cmd_fill0 = stream0.command(
        A.OPCODE.DMA_FILL, waits=(e_fill,),
        operands=((t_c, s_scratch, core0_scratch, RW),),
        attr_index=fill0_attr,
    )
    d_fill0 = builder.dma(
        cmd_fill0, A.DMA_KIND.LOCAL_FILL,
        src=_sram(t_c, s_scratch, 0, 0x0000), dst=_sram(t_c, s_scratch, 0, 0x0000),
        rows=1, row_bytes=0, src_stride=0, dst_stride=0,
        completion_event=e_fill0,
    )
    builder.oracle(entrypoint_id, 1, d_fill0, cmd_fill0.command_id,
                   A.DMA_KIND.LOCAL_FILL, 0, 1, 0, 0)

    cmd_p2p = stream0.command(
        A.OPCODE.DMA_P2P_PUSH, waits=(e_fill0,),
        operands=((t_c, s_scratch, core0_scratch, RO),),
    )
    d_p2p = builder.dma(
        cmd_p2p, A.DMA_KIND.P2P_PUSH,
        src=_sram(t_c, s_scratch, 0, 0x0000), dst=_peer(t_c, s_peer, 1, 0x0000),
        rows=0, row_bytes=64, src_stride=64, dst_stride=64,
        transfer_id=1,
        completion_event=e_p2p,
    )
    builder.oracle(entrypoint_id, 1, d_p2p, cmd_p2p.command_id,
                   A.DMA_KIND.P2P_PUSH, 0x0000, 0, 64, 64)

    stream0.command(A.OPCODE.REQUEST_END, waits=(e_p2p,), signal_event=e_end0)
    stream0.command(A.OPCODE.HALT, waits=(e_end0,))

    recv_attr = builder.recv_wait_attr(1)
    stream1.command(
        A.OPCODE.RECV_WAIT,
        operands=((t_c, s_peer, core1_peer, RW),),
        signal_event=e_recv,
        attr_index=recv_attr,
    )
    stream1.command(A.OPCODE.HALT, waits=(e_recv,))

    return builder.build()


def build_fence_scopes_program(arch):
    """Gate 2 fence-scope matrix: five scopes with in-scope blocking,
    out-of-scope pass-through and a cross-core ALL_INSTANCE fence."""
    from mesh_ir.builder import ProgramBuilder

    HOST_REGION = 1
    builder = ProgramBuilder(arch, "golden_fence_scopes")
    entrypoint_id = builder.entrypoint("main", "fence_scopes",
                                       lifecycle_core=0, lifecycle_stream=0)

    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (8,))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (8,))
    t_shared = builder.tensor("shared", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HOST_SHARED, RW, (8,))
    t_c = builder.tensor("scratch", A.TENSOR_ROLE.ACTIVATION, FP16, A.STORAGE_CLASS.CORE_SRAM, RW, (8,))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x200000)
    builder.relocation("shared", A.RELOCATION_KIND.TENSOR_BASE, HOST_REGION, t_shared, 0x10000)

    def shared_ep(tensor, offset):
        return DmaEndpoint(
            memory_space=A.MEMORY_SPACE.HOST_SHARED, region_id=HOST_REGION,
            owner_core=NO_CORE, tensor_id=tensor, shard_id=0, reserved=0,
            offset_bytes=offset,
        )

    core0_buf = builder.allocation(0, 0x0000, 64, arch.sram_base_alignment_bytes)
    core1_peer = builder.allocation(1, 0x0000, 64, arch.sram_base_alignment_bytes)
    core1_buf = builder.allocation(1, 0x1000, 64, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, core0_buf, (8,), 16)
    s_out = builder.shard(t_out, 0, core0_buf, (8,), 16)
    s_scratch = builder.shard(t_c, 0, core0_buf, (8,), 16)
    s_peer = builder.shard(t_c, 1, core1_peer, (8,), 16)
    s_shared = builder.shard(t_shared, 1, core1_buf, (8,), 16)

    stream0 = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_load = builder.event()
    e_f1 = builder.event()
    e_store = builder.event()
    e_f2 = builder.event()
    e_p2p = builder.event()
    e_f3 = builder.event()
    e_f4 = builder.event()
    e_f5 = builder.event()
    e_end0 = builder.event()
    e_recv = builder.event()
    e_hs = builder.event()

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)

    cmd_load = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,), operands=((t_in, s_in, core0_buf, RO),))
    d_load = builder.dma(
        cmd_load, A.DMA_KIND.LOAD,
        src=_hbm(t_in, s_in, 0x100000), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=16, src_stride=16, dst_stride=16,
        completion_event=e_load)
    builder.oracle(entrypoint_id, 1, d_load, cmd_load.command_id,
                   A.DMA_KIND.LOAD, 0x100000, 1, 16, 16)

    stream0.command(A.OPCODE.AXI_FENCE, waits=(e_load,), signal_event=e_f1,
                    attr_index=builder.fence_attr(A.FENCE_SCOPE.DMA_READ))

    cmd_store = stream0.command(
        A.OPCODE.DMA_STORE, waits=(e_f1,),
        operands=((t_out, s_out, core0_buf, RW),))
    d_store = builder.dma(
        cmd_store, A.DMA_KIND.STORE,
        src=_sram(t_out, s_out, 0, 0x0000), dst=_hbm(t_out, s_out, 0x200000),
        rows=1, row_bytes=16, src_stride=16, dst_stride=16,
        completion_event=e_store)
    builder.oracle(entrypoint_id, 1, d_store, cmd_store.command_id,
                   A.DMA_KIND.STORE, 0x200000, 1, 16, 16)

    stream0.command(A.OPCODE.AXI_FENCE, waits=(e_store,), signal_event=e_f2,
                    attr_index=builder.fence_attr(A.FENCE_SCOPE.DMA_WRITE))

    cmd_p2p = stream0.command(
        A.OPCODE.DMA_P2P_PUSH, waits=(e_f2,),
        operands=((t_c, s_scratch, core0_buf, RO),))
    d_p2p = builder.dma(
        cmd_p2p, A.DMA_KIND.P2P_PUSH,
        src=_sram(t_c, s_scratch, 0, 0x0000), dst=_peer(t_c, s_peer, 1, 0x0000),
        rows=1, row_bytes=16, src_stride=16, dst_stride=16,
        transfer_id=7, completion_event=e_p2p)
    builder.oracle(entrypoint_id, 1, d_p2p, cmd_p2p.command_id,
                   A.DMA_KIND.P2P_PUSH, 0x0000, 1, 16, 16)

    stream0.command(A.OPCODE.AXI_FENCE, waits=(e_p2p,), signal_event=e_f3,
                    attr_index=builder.fence_attr(A.FENCE_SCOPE.P2P))
    stream0.command(A.OPCODE.AXI_FENCE, waits=(e_f3,), signal_event=e_f4,
                    attr_index=builder.fence_attr(A.FENCE_SCOPE.HOST_SHARED_WRITE))
    stream0.command(A.OPCODE.AXI_FENCE, waits=(e_f4,), signal_event=e_f5,
                    attr_index=builder.fence_attr(A.FENCE_SCOPE.ALL_INSTANCE))
    stream0.command(A.OPCODE.REQUEST_END, waits=(e_f5,), signal_event=e_end0)
    stream0.command(A.OPCODE.HALT, waits=(e_end0,))

    recv_attr = builder.recv_wait_attr(7)
    stream1.command(
        A.OPCODE.RECV_WAIT,
        operands=((t_c, s_peer, core1_peer, RW),),
        signal_event=e_recv, attr_index=recv_attr)

    cmd_hs = stream1.command(
        A.OPCODE.DMA_STORE, waits=(e_recv,),
        operands=((t_shared, s_shared, core1_buf, RW),))
    d_hs = builder.dma(
        cmd_hs, A.DMA_KIND.STORE,
        src=_sram(t_shared, s_shared, 1, 0x1000),
        dst=shared_ep(t_shared, 0x10000),
        rows=1, row_bytes=16, src_stride=16, dst_stride=16,
        completion_event=e_hs)
    builder.oracle(entrypoint_id, 1, d_hs, cmd_hs.command_id,
                   A.DMA_KIND.STORE, 0x10000, 1, 16, 16)
    stream1.command(A.OPCODE.HALT, waits=(e_hs,))

    return builder.build()


def build_cross_error_program(arch):
    """Instance-global error drain: core0's LOAD DECERRs (address outside
    the routed HBM window); core1 waits on the failed producer's success
    event and must be cancelled instance-wide instead of hanging."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_cross_fault")
    entrypoint_id = builder.entrypoint("main", "cross_error",
                                       lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (64,))
    t_w = builder.tensor("weight", A.TENSOR_ROLE.WEIGHT, FP16, A.STORAGE_CLASS.HBM, RO, (64,))
    t_c = builder.tensor("partial", A.TENSOR_ROLE.ACTIVATION, FP16, A.STORAGE_CLASS.CORE_SRAM, RW, (64,))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x800000)
    builder.relocation("weight", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_w, 0x100000)

    a_buf = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x2000, 128, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (64,), 128)
    s_w = builder.shard(t_w, 0, a_buf, (64,), 128)
    s_c = builder.shard(t_c, 0, a_out, (64,), 128)

    stream0 = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_load = builder.event()
    e_lw = builder.event()
    e_g = builder.event()
    e_end = builder.event()

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    cmd_load = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_in, s_in, a_buf, RO),))
    d_load = builder.dma(
        cmd_load, A.DMA_KIND.LOAD,
        src=_hbm(t_in, 0, 0x800000), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_load)
    builder.oracle(entrypoint_id, 1, d_load, cmd_load.command_id,
                   A.DMA_KIND.LOAD, 0x800000, 1, 128, 128)
    cmd_lw = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_w, s_w, a_buf, RO),))
    d_lw = builder.dma(
        cmd_lw, A.DMA_KIND.LOAD,
        src=_hbm(t_w, 0, 0x100000), dst=_sram(t_w, s_w, 0, 0x0000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_lw)
    builder.oracle(entrypoint_id, 1, d_lw, cmd_lw.command_id,
                   A.DMA_KIND.LOAD, 0x100000, 1, 128, 128)
    gemm_attr = builder.gemm_attr(1, 8, 8, 8, FP16, A.DTYPE.FP32)
    stream0.command(
        A.OPCODE.GEMM, waits=(e_load, e_lw),
        operands=((t_in, s_in, a_buf, RO), (t_w, s_w, a_buf, RO),
                  (t_c, s_c, a_out, RW)),
        signal_event=e_g, attr_index=gemm_attr)
    stream0.command(A.OPCODE.REQUEST_END, waits=(e_g,), signal_event=e_end)
    stream0.command(A.OPCODE.HALT, waits=(e_end,))

    stream1.command(A.OPCODE.HALT, waits=(e_load,))
    return builder.build()


def build_repeat_error_program(arch):
    """REPEAT generation fails mid-flight: generation 0 completes, the
    faulted generation-1 STORE drains the instance; the repeat gate must
    retire through the error terminal path."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_repeat_error")
    entrypoint_id = builder.entrypoint("main", "repeat_error",
                                       lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (64,))
    t_out = builder.tensor("output", A.TENSOR_ROLE.OUTPUT, FP16, A.STORAGE_CLASS.HBM, RW, (64,))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x100000)
    builder.relocation("output", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_out, 0x200000)

    a_buf = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x2000, 128, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (64,), 128)
    s_out = builder.shard(t_out, 0, a_out, (64,), 128)

    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_load = builder.event()
    e_store = builder.event()
    e_end = builder.event()

    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    cmd_load = stream.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_in, s_in, a_buf, RO),))
    d_load = builder.dma(
        cmd_load, A.DMA_KIND.LOAD,
        src=_hbm(t_in, 0, 0x100000), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_load)
    builder.oracle(entrypoint_id, 1, d_load, cmd_load.command_id,
                   A.DMA_KIND.LOAD, 0x100000, 1, 128, 128)
    cmd_store = stream.command(
        A.OPCODE.DMA_STORE, waits=(e_load,),
        operands=((t_out, s_out, a_out, RW),))
    d_store = builder.dma(
        cmd_store, A.DMA_KIND.STORE,
        src=_sram(t_out, s_out, 0, 0x2000), dst=_hbm(t_out, 0, 0x200000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_store)
    builder.oracle(entrypoint_id, 1, d_store, cmd_store.command_id,
                   A.DMA_KIND.STORE, 0x200000, 1, 128, 128)
    repeat_attr = builder.repeat_attr(1, 2, 3)
    stream.command(A.OPCODE.REPEAT, attr_index=repeat_attr)
    stream.command(A.OPCODE.REQUEST_END, waits=(e_store,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def build_p2p_reuse_program(arch):
    """Two sequential P2P transfers reuse the same destination range: the
    expectation must retire with its transfer instead of colliding at
    install time (spec 5.6 transfer lifetime)."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_p2p_reuse")
    entrypoint_id = builder.entrypoint("main", "p2p_reuse",
                                       lifecycle_core=0, lifecycle_stream=0)
    t_c = builder.tensor("scratch", A.TENSOR_ROLE.ACTIVATION, FP16,
                         A.STORAGE_CLASS.CORE_SRAM, RW, (32,))
    core0_buf = builder.allocation(0, 0x0000, 64, arch.sram_base_alignment_bytes)
    core1_peer = builder.allocation(1, 0x0000, 64, arch.sram_base_alignment_bytes)
    s_src = builder.shard(t_c, 0, core0_buf, (32,), 32)
    s_dst = builder.shard(t_c, 1, core1_peer, (32,), 32)

    stream0 = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_p2p1 = builder.event()
    e_p2p2 = builder.event()
    e_end = builder.event()
    e_recv1 = builder.event()
    e_recv2 = builder.event()

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    cmd1 = stream0.command(
        A.OPCODE.DMA_P2P_PUSH, waits=(e_begin,),
        operands=((t_c, s_src, core0_buf, RO),))
    d1 = builder.dma(
        cmd1, A.DMA_KIND.P2P_PUSH,
        src=_sram(t_c, s_src, 0, 0x0000), dst=_peer(t_c, s_dst, 1, 0x0000),
        rows=1, row_bytes=32, src_stride=32, dst_stride=32,
        transfer_id=21, completion_event=e_p2p1)
    builder.oracle(entrypoint_id, 1, d1, cmd1.command_id,
                   A.DMA_KIND.P2P_PUSH, 0x0000, 1, 32, 32)
    cmd2 = stream0.command(
        A.OPCODE.DMA_P2P_PUSH, waits=(e_p2p1,),
        operands=((t_c, s_src, core0_buf, RO),))
    d2 = builder.dma(
        cmd2, A.DMA_KIND.P2P_PUSH,
        src=_sram(t_c, s_src, 0, 0x0000), dst=_peer(t_c, s_dst, 1, 0x0000),
        rows=1, row_bytes=32, src_stride=32, dst_stride=32,
        transfer_id=22, completion_event=e_p2p2)
    builder.oracle(entrypoint_id, 1, d2, cmd2.command_id,
                   A.DMA_KIND.P2P_PUSH, 0x0000, 1, 32, 32)
    stream0.command(A.OPCODE.REQUEST_END, waits=(e_p2p2,), signal_event=e_end)
    stream0.command(A.OPCODE.HALT, waits=(e_end,))

    recv1 = builder.recv_wait_attr(21)
    stream1.command(
        A.OPCODE.RECV_WAIT,
        operands=((t_c, s_dst, core1_peer, RW),),
        signal_event=e_recv1, attr_index=recv1)
    recv2 = builder.recv_wait_attr(22)
    stream1.command(
        A.OPCODE.RECV_WAIT, waits=(e_recv1,),
        operands=((t_c, s_dst, core1_peer, RW),),
        signal_event=e_recv2, attr_index=recv2)
    stream1.command(A.OPCODE.HALT, waits=(e_recv2,))
    return builder.build()


def build_barrier_e2e_program(arch):
    """Two-core barrier rendezvous (expected_arrivals=2) plus a sequential
    second rendezvous after a REPEAT-free window."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_barrier_e2e")
    entrypoint_id = builder.entrypoint("main", "barrier_e2e",
                                       lifecycle_core=0, lifecycle_stream=0)
    t_c = builder.tensor("scratch", A.TENSOR_ROLE.ACTIVATION, FP16,
                         A.STORAGE_CLASS.CORE_SRAM, RW, (32,))
    core0_buf = builder.allocation(0, 0x0000, 64, arch.sram_base_alignment_bytes)
    core1_buf = builder.allocation(1, 0x0000, 64, arch.sram_base_alignment_bytes)
    s0 = builder.shard(t_c, 0, core0_buf, (32,), 32)
    s1 = builder.shard(t_c, 1, core1_buf, (32,), 32)

    stream0 = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    barrier = builder.event(kind=A.EVENT_KIND.BARRIER, expected_arrivals=2)
    e_end = builder.event()

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    stream0.command(A.OPCODE.BARRIER, waits=(e_begin,), signal_event=barrier)
    stream0.command(A.OPCODE.REQUEST_END, waits=(barrier,), signal_event=e_end)
    stream0.command(A.OPCODE.HALT, waits=(e_end,))

    stream1.command(A.OPCODE.BARRIER, signal_event=barrier)
    stream1.command(A.OPCODE.HALT, waits=(barrier,))
    return builder.build()


def build_cross_fault_program(arch):
    """Per-UID read fault: with instances=2 only the first instance's LOAD
    is faulted, the second must run to normal completion."""
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "golden_cross_fault")
    entrypoint_id = builder.entrypoint("main", "cross_error",
                                       lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", A.TENSOR_ROLE.INPUT, FP16, A.STORAGE_CLASS.HBM, RO, (64,))
    t_w = builder.tensor("weight", A.TENSOR_ROLE.WEIGHT, FP16, A.STORAGE_CLASS.HBM, RO, (64,))
    t_c = builder.tensor("partial", A.TENSOR_ROLE.ACTIVATION, FP16, A.STORAGE_CLASS.CORE_SRAM, RW, (64,))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_in, 0x800000)
    builder.relocation("weight", A.RELOCATION_KIND.TENSOR_BASE, HBM_REGION, t_w, 0x100000)

    a_buf = builder.allocation(0, 0x0000, 128, arch.sram_base_alignment_bytes)
    a_out = builder.allocation(0, 0x2000, 128, arch.sram_base_alignment_bytes)
    s_in = builder.shard(t_in, 0, a_buf, (64,), 128)
    s_w = builder.shard(t_w, 0, a_buf, (64,), 128)
    s_c = builder.shard(t_c, 0, a_out, (64,), 128)

    stream0 = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = builder.stream(1, 0, flags=A.STREAM_FLAGS.IS_LOCAL_CONTROL)

    e_begin = builder.event()
    e_load = builder.event()
    e_lw = builder.event()
    e_g = builder.event()
    e_end = builder.event()

    stream0.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    cmd_load = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_in, s_in, a_buf, RO),))
    d_load = builder.dma(
        cmd_load, A.DMA_KIND.LOAD,
        src=_hbm(t_in, 0, 0x100080), dst=_sram(t_in, s_in, 0, 0x0000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_load)
    builder.oracle(entrypoint_id, 1, d_load, cmd_load.command_id,
                   A.DMA_KIND.LOAD, 0x100080, 1, 128, 128)
    cmd_lw = stream0.command(
        A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_w, s_w, a_buf, RO),))
    d_lw = builder.dma(
        cmd_lw, A.DMA_KIND.LOAD,
        src=_hbm(t_w, 0, 0x100000), dst=_sram(t_w, s_w, 0, 0x0000),
        rows=1, row_bytes=128, src_stride=128, dst_stride=128,
        completion_event=e_lw)
    builder.oracle(entrypoint_id, 1, d_lw, cmd_lw.command_id,
                   A.DMA_KIND.LOAD, 0x100000, 1, 128, 128)
    gemm_attr = builder.gemm_attr(1, 8, 8, 8, FP16, A.DTYPE.FP32)
    stream0.command(
        A.OPCODE.GEMM, waits=(e_load, e_lw),
        operands=((t_in, s_in, a_buf, RO), (t_w, s_w, a_buf, RO),
                  (t_c, s_c, a_out, RW)),
        signal_event=e_g, attr_index=gemm_attr)
    stream0.command(A.OPCODE.REQUEST_END, waits=(e_g,), signal_event=e_end)
    stream0.command(A.OPCODE.HALT, waits=(e_end,))

    stream1.command(A.OPCODE.HALT, waits=(e_load,))
    return builder.build()

"""Hand-written golden programs for the Dummy Core runtime prototype.

golden_single_core: DMA_LOAD x2 -> GEMM timer -> DMA_STORE on core 0.
golden_dual_core:   per-core DMA_LOAD -> GEMM -> P2P_PUSH -> RECV_WAIT ->
                    LOCAL_REDUCE -> DMA_STORE across cores 0 and 1.

No tensor numerics are computed anywhere; compute opcodes only carry the
timing/digest attributes consumed by the Dummy Core analytic engines.
"""

from __future__ import annotations

import dataclasses

from mesh_ir.analysis.sram import SramAllocation
from mesh_ir.builder import ProgramBuilder
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, DType, DmaKind, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, MatmulAttrs, OpCode
from mesh_ir.ir.kernel_ir import AllocAttrs, BarrierAttrs, BlockedMnkLayout, BufferObject, BufferView, ControlToken, DistributionKind, DmaAttrs, ElementRegion, GemmKernelAttrs, KernelComputation, KernelCost, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, KernelTile, LocalCopyAttrs, LocalReduceAttrs, MatrixEpilogueAlgorithm, MatrixEpilogueKernelAttrs, MatrixPhase, OperandAccess, OperandAccessMode, PartialSumDefinition, Placement, RecvWaitAttrs, ReduceKind, StateOrigin, StateTransition, SynthesizedTensorPurpose, TensorShard, TensorState, VectorAlgorithm, VectorKernelAttrs, ViewDeclarationAttrs
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AxiFenceAttrs, ExternalSlotBacking, FenceScope, HaltAttrs, LocalAllocationBacking, ObjectBacking, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.traffic import Binding, BindingSlot

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
REPEAT_COUNT = 3
SRAM_PLACEMENT_PARALLEL = (0, 0x2080, 0x4100)
SRAM_PLACEMENT_CONFLICT = (0, 0x2000, 0x4000)
LOAD_SATURATION_TENSOR_BYTES = 0x40000
LOAD_SATURATION_HBM_BASE = 0x100000


def _declaration_ops(objects, views):
    by_object = {item.object_id: item for item in objects}
    return tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, by_object[item.object_id].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )


def _build_single_core_program(
    arch,
    name: str,
    *,
    hbm_offsets: tuple[int, int, int] = (0x100000, 0x200000, 0x300000),
    sram_offsets: tuple[int, int, int] = (0x0000, 0x2000, 0x4000),
    controls_before_end=(),
):
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, (M, K), (K, 1), StorageClass.EXTERNAL, Access.READ_ONLY, IN_BYTES, IN_BYTES, None),
        KernelTensor(2, 0, None, 2, 0, "weight", TensorRole.WEIGHT, DType.FP16, (K, N), (N, 1), StorageClass.EXTERNAL, Access.READ_ONLY, W_BYTES, W_BYTES, None),
        KernelTensor(3, 1, None, 3, 0, "output", TensorRole.OUTPUT, DType.FP16, (M, N), (N, 1), StorageClass.EXTERNAL, Access.READ_WRITE, OUT_BYTES, OUT_BYTES, None),
    )
    placements = (Placement(1, (0,)),)
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), (M, K), (M, K), 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0, 0), (K, N), (K, N), 0),
        TensorShard(3, 3, 1, 0, DistributionKind.PARTITIONED, (0, 0), (M, N), (M, N), 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (M, K), (K, 1), IN_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, (M, K), (K, 1), IN_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, INVALID_CORE_ID, MemorySpace.HBM, (K, N), (N, 1), W_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(4, 2, 0, MemorySpace.CORE_SRAM, (K, N), (N, 1), W_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 3, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 3, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
    )
    views = tuple(BufferView(index, item.object_id, (1, 1, 2, 2, 3, 3)[index - 1], (0, 0), item.shape, item.shape, 0, item.strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0) for index, item in enumerate(objects, 1))
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EXTERNAL),
        TensorState(5, 4, 0, StateOrigin.EMPTY),
        TensorState(6, 4, 1, StateOrigin.PRODUCED),
        TensorState(7, 5, 0, StateOrigin.EMPTY),
        TensorState(8, 5, 1, StateOrigin.PRODUCED),
        TensorState(9, 6, 0, StateOrigin.EMPTY),
        TensorState(10, 6, 1, StateOrigin.PRODUCED),
    )
    declarations = tuple(KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None) for index, item in enumerate(objects, 1)) + tuple(KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, objects[index - 1].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None) for index, item in enumerate(views, 1))
    input_region = ElementRegion((0, 0), (M, K), (1, 1))
    weight_region = ElementRegion((0, 0), (K, N), (1, 1))
    output_region = ElementRegion((0, 0), (M, N), (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, M, N, K, 1, M, N, K)
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    effects = (
        KernelOp(13, 0, "load:input", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, input_region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, input_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(14, 0, "load:weight", KernelOpcode.DMA, 0, 0, (OperandAccess(4, 3, weight_region, OperandAccessMode.READ),), (StateTransition(5, 6, 4, weight_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 2),
        KernelOp(15, 1, "gemm", KernelOpcode.GEMM, 0, 3, (OperandAccess(3, 2, input_region, OperandAccessMode.READ), OperandAccess(6, 4, weight_region, OperandAccessMode.READ)), (StateTransition(7, 8, 5, output_region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(IN_BYTES + W_BYTES, OUT_BYTES, IN_BYTES + W_BYTES + OUT_BYTES, M * N * K, 0, 0), MatrixPhase.DIRECT, 0), (1, 2), 3),
        KernelOp(16, 0, "store:output", KernelOpcode.DMA, 0, 0, (OperandAccess(8, 5, output_region, OperandAccessMode.READ),), (StateTransition(9, 10, 6, output_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (3,), 4),
    )
    tokens = tuple(ControlToken(index) for index in range(1, 5))
    records = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.MATMUL, (1, 2), 3, attrs),), placements, shards, (), objects, views, states, tokens, declarations + effects)
    allocations = (
        SramAllocation(1, 2, 0, sram_offsets[0], IN_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(2, 4, 0, sram_offsets[1], W_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(3, 5, 0, sram_offsets[2], OUT_BYTES, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, hbm_offsets[0], IN_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, hbm_offsets[1], W_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(3, hbm, INVALID_CORE_ID, hbm_offsets[2], OUT_BYTES, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = tuple(BindingSlot(index, symbol, MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, access, binding) for index, (symbol, size, access, binding) in enumerate(zip(("input", "weight", "output"), (IN_BYTES, W_BYTES, OUT_BYTES), (Access.READ_ONLY, Access.READ_ONLY, Access.READ_WRITE), bindings), 1))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant("main", "b1_m64n64k64", f"{name}:b1_m64n64k64", records=records, allocations=allocations, external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(3, ExternalSlotBacking(2)), ObjectBacking(6, ExternalSlotBacking(3))), binding_slots=slots)
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op_id in tuple(item.op_id for item in effects):
        stream.kernel_command(op_id)
    for control in controls_before_end:
        stream.control_command(control)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_single_core_program(arch):
    return _build_single_core_program(arch, "build_single_core_program")


def build_region_edge_program(arch):
    return _build_single_core_program(
        arch,
        "build_region_edge_program",
        hbm_offsets=(0x500000, 0x600000, 0x700000),
    )


def _named_single(
    arch,
    name,
    *,
    controls=(),
    hbm_offsets=(0x100000, 0x200000, 0x300000),
    sram_offsets=(0x0000, 0x2000, 0x4000),
):
    return _build_single_core_program(
        arch,
        name,
        controls_before_end=controls,
        hbm_offsets=hbm_offsets,
        sram_offsets=sram_offsets,
    )


def _build_p2p_program(arch, name, transfer_count=1):
    size = 64
    tensor = KernelTensor(1, 0, None, 1, 0, "scratch", TensorRole.ACTIVATION, DType.FP16, (32,), (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None)
    placements = (Placement(1, (0, 1)),)
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0,), (32,), (32,), 0),
        TensorShard(2, 1, 1, 1, DistributionKind.REPLICATED, (0,), (32,), (32,), 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, (32,), (1,), size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 1, 1, MemorySpace.CORE_SRAM, (32,), (1,), size, arch.sram_base_alignment_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0,), (32,), (32,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 2, (0,), (32,), (32,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = [TensorState(1, 1, 0, StateOrigin.EMPTY), TensorState(2, 1, 1, StateOrigin.PRODUCED), TensorState(3, 2, 0, StateOrigin.EMPTY)]
    declarations = (
        KernelOp(1, 0, "alloc:1", KernelOpcode.ALLOC, 0, 0, (), (), AllocAttrs(1), (), None),
        KernelOp(2, 0, "alloc:2", KernelOpcode.ALLOC, 1, 0, (), (), AllocAttrs(2), (), None),
        KernelOp(3, 0, "view:1", KernelOpcode.VIEW, 0, 0, (), (), ViewDeclarationAttrs(1), (), None),
        KernelOp(4, 0, "view:2", KernelOpcode.VIEW, 1, 0, (), (), ViewDeclarationAttrs(2), (), None),
    )
    tokens = [ControlToken(1)]
    region = ElementRegion((0,), (32,), (1,))
    effects = [KernelOp(5, 0, "fill:source", KernelOpcode.DMA, 0, 0, (), (StateTransition(1, 2, 1, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x00"), (), 1)]
    previous_state = 3
    previous_recv_token = None
    for ordinal in range(transfer_count):
        transfer_id = 21 + ordinal
        produced_state = len(states) + 1
        states.append(TensorState(produced_state, 2, ordinal + 1, StateOrigin.PRODUCED))
        send_token = len(tokens) + 1
        recv_token = send_token + 1
        tokens.extend((ControlToken(send_token), ControlToken(recv_token)))
        send_id = len(declarations) + len(effects) + 1
        recv_id = send_id + 1
        effects.append(KernelOp(send_id, 0, f"p2p:{transfer_id}", KernelOpcode.DMA, 0, 0, (OperandAccess(2, 1, region, OperandAccessMode.READ),), (StateTransition(previous_state, produced_state, 2, region),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, transfer_id, b""), (1,) if previous_recv_token is None else (previous_recv_token,), send_token))
        effects.append(KernelOp(recv_id, 0, f"recv:{transfer_id}", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(transfer_id, 0, 1, size), (send_token,), recv_token))
        previous_state = produced_state
        previous_recv_token = recv_token
    records = KernelMemoryRecords((tensor,), (), placements, shards, (), objects, views, tuple(states), tuple(tokens), declarations + tuple(effects))
    allocations = (
        SramAllocation(1, 1, 0, 0, size, arch.sram_base_alignment_bytes),
        SramAllocation(2, 2, 1, 0, size, arch.sram_base_alignment_bytes),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant("main", "p2p", f"{name}:p2p", records=records, allocations=allocations)
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    stream0.kernel_command(5)
    for index in range(transfer_count):
        stream0.kernel_command(6 + index * 2)
        stream1.kernel_command(7 + index * 2)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def _build_barrier_program(arch, name):
    participants = (0, 1)
    ops = (
        KernelOp(1, 0, "barrier:1:0", KernelOpcode.BARRIER, 0, 0, (), (), BarrierAttrs(participants), (), 1),
        KernelOp(2, 0, "barrier:1:1", KernelOpcode.BARRIER, 1, 0, (), (), BarrierAttrs(participants), (), 2),
        KernelOp(3, 0, "barrier:2:0", KernelOpcode.BARRIER, 0, 0, (), (), BarrierAttrs(participants), (1, 2), 3),
        KernelOp(4, 0, "barrier:2:1", KernelOpcode.BARRIER, 1, 0, (), (), BarrierAttrs(participants), (1, 2), 4),
    )
    records = KernelMemoryRecords((), (), (), (), (), (), (), (), tuple(ControlToken(index) for index in range(1, 5)), ops)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant("main", "barrier", f"{name}:barrier", records=records, allocations=())
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    stream0.kernel_command(1)
    stream1.kernel_command(2)
    stream0.kernel_command(3)
    stream1.kernel_command(4)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_repeat_program(arch):
    shape = (8, 8)
    strides = (8, 1)
    size = 128
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(2, 1, None, 2, 0, "matrix", TensorRole.ACTIVATION, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
        KernelTensor(3, 2, None, 2, 0, "output", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
    )
    shards = tuple(TensorShard(index, index, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0) for index in range(1, 4))
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 1, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 3, 2, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(4, 3, 3, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 1),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, next(obj.owner_core for obj in objects if obj.object_id == item.object_id), 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY),
        TensorState(5, 3, 1, StateOrigin.PRODUCED),
        TensorState(6, 3, 2, StateOrigin.PRODUCED),
    )
    region = ElementRegion((0, 0), shape, (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, 8, 8, 8, 1, 8, 8, 8)
    elementwise = ElementwiseAttrs()
    effects = (
        KernelOp(8, 0, "repeat:load", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(9, 1, "repeat:gemm", KernelOpcode.GEMM, 0, 2, (OperandAccess(3, 2, region, OperandAccessMode.READ), OperandAccess(3, 2, region, OperandAccessMode.READ)), (StateTransition(4, 5, 3, region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(size * 2, size, size * 3, 512, 0, 0), MatrixPhase.DIRECT, 0), (1,), 2),
        KernelOp(10, 2, "repeat:relu", KernelOpcode.VECTOR, 0, 3, (OperandAccess(5, 3, region, OperandAccessMode.READ),), (StateTransition(5, 6, 4, region),), VectorKernelAttrs(OpCode.RELU, elementwise, tile, KernelCost(size, size, size * 2, 0, 64, 0), VectorAlgorithm.ELEMENTWISE), (2,), 3),
    )
    computations = (
        KernelComputation(1, OpCode.MATMUL, (1, 1), 2, attrs),
        KernelComputation(2, OpCode.RELU, (2,), 3, elementwise),
    )
    records = KernelMemoryRecords(tensors, computations, (Placement(1, (0,)),), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 4)), declarations + effects)
    allocations = (
        SramAllocation(1, 2, 0, 0, size, arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 0, 0x2000, size, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    binding = Binding(1, hbm, INVALID_CORE_ID, 0x100000, size, arch.axi_data_bytes, Access.READ_ONLY)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_repeat_program", 1))
    variant = builder.variant(
        "main",
        "repeat",
        "build_repeat_program:repeat",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(1, ExternalSlotBacking(1)),),
        binding_slots=(BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, binding),),
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op_id in (8, 9, 10):
        stream.kernel_command(op_id)
    stream.control_command(RepeatCommandAttrs(1, 3, REPEAT_COUNT))
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


@dataclasses.dataclass(frozen=True)
class _LoadSpec:
    core_id: int
    shape: tuple[int, ...]
    source_strides: tuple[int, ...]
    destination_strides: tuple[int, ...]
    dtype: DType
    binding_offset: int
    kind: DmaKind = DmaKind.LOAD
    max_burst_beats: int | None = None


def _extent_bytes(shape, strides, dtype):
    if not shape or any(dimension == 0 for dimension in shape):
        return dtype.byte_width if not shape else 0
    return (sum((dimension - 1) * stride for dimension, stride in zip(shape, strides)) + 1) * dtype.byte_width


def _contiguous_strides(shape):
    strides = []
    running = 1
    for dimension in reversed(shape):
        strides.append(running)
        running *= dimension
    return tuple(reversed(strides))


def _build_load_program(arch, name, specs):
    tensors = []
    shards = []
    objects = []
    views = []
    states = []
    tokens = []
    effects = []
    allocations = []
    backings = []
    slots = []
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    local_ordinals = {}
    for index, spec in enumerate(specs, 1):
        rank = len(spec.shape)
        logical = DType(spec.dtype).byte_width
        for dimension in spec.shape:
            logical *= dimension
        source_size = _extent_bytes(spec.shape, spec.source_strides, spec.dtype)
        destination_size = _extent_bytes(spec.shape, spec.destination_strides, spec.dtype)
        tensors.append(KernelTensor(index, 0, None, index, 0, f"input:{index}", TensorRole.INPUT, spec.dtype, spec.shape, spec.source_strides, StorageClass.EXTERNAL, Access.READ_ONLY, logical, source_size, None))
        shards.append(TensorShard(index, index, index, spec.core_id, DistributionKind.PARTITIONED, (0,) * rank, spec.shape, spec.shape, 0))
        external_object_id = index * 2 - 1
        local_object_id = index * 2
        objects.extend((
            BufferObject(external_object_id, index, INVALID_CORE_ID, MemorySpace.HBM, spec.shape, spec.source_strides, source_size, arch.axi_data_bytes, False, 0),
            BufferObject(local_object_id, index, spec.core_id, MemorySpace.CORE_SRAM, spec.shape, spec.destination_strides, destination_size, arch.sram_base_alignment_bytes, False, 0),
        ))
        views.extend((
            BufferView(external_object_id, external_object_id, index, (0,) * rank, spec.shape, spec.shape, 0, spec.source_strides, Layout.CONTIGUOUS_ROW_MAJOR if spec.source_strides == _contiguous_strides(spec.shape) else None, None, 0),
            BufferView(local_object_id, local_object_id, index, (0,) * rank, spec.shape, spec.shape, 0, spec.destination_strides, Layout.CONTIGUOUS_ROW_MAJOR if spec.destination_strides == _contiguous_strides(spec.shape) else None, None, 0),
        ))
        external_state_id = index * 3 - 2
        empty_state_id = index * 3 - 1
        produced_state_id = index * 3
        states.extend((TensorState(external_state_id, external_object_id, 0, StateOrigin.EXTERNAL), TensorState(empty_state_id, local_object_id, 0, StateOrigin.EMPTY), TensorState(produced_state_id, local_object_id, 1, StateOrigin.PRODUCED)))
        token_id = index
        tokens.append(ControlToken(token_id))
        region = ElementRegion((0,) * rank, spec.shape, (1,) * rank)
        op_id = len(specs) * 4 + index
        effects.append(KernelOp(op_id, 0, f"{spec.kind.name.lower()}:{index}", KernelOpcode.DMA, spec.core_id, 0, (OperandAccess(external_state_id, external_object_id, region, OperandAccessMode.READ),), (StateTransition(empty_state_id, produced_state_id, local_object_id, region),), DmaAttrs(spec.kind, spec.core_id, INVALID_CORE_ID, spec.core_id, 0, b"", spec.max_burst_beats), (), token_id))
        alignment = max(arch.sram_base_alignment_bytes, arch.axi_data_bytes)
        local_ordinal = local_ordinals.get(spec.core_id, 0)
        offset = local_ordinal * 0x20000
        size = (destination_size + arch.axi_data_bytes - 1) // arch.axi_data_bytes * arch.axi_data_bytes
        allocations.append(SramAllocation(index, local_object_id, spec.core_id, offset, size, alignment))
        local_ordinals[spec.core_id] = local_ordinal + 1
        binding = Binding(index, hbm, INVALID_CORE_ID, spec.binding_offset, source_size, arch.axi_data_bytes, Access.READ_ONLY)
        slots.append(BindingSlot(index, f"input:{index}", MemorySpace.HBM, hbm, INVALID_CORE_ID, source_size, arch.axi_data_bytes, Access.READ_ONLY, binding))
        backings.append(ObjectBacking(external_object_id, ExternalSlotBacking(index)))
    placements = tuple(Placement(index, (spec.core_id,)) for index, spec in enumerate(specs, 1))
    declarations = tuple(KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None) for index, item in enumerate(objects, 1))
    declarations += tuple(KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, next(obj.owner_core for obj in objects if obj.object_id == item.object_id), 0, (), (), ViewDeclarationAttrs(item.view_id), (), None) for index, item in enumerate(views, 1))
    records = KernelMemoryRecords(tuple(tensors), (), placements, tuple(shards), (), tuple(objects), tuple(views), tuple(states), tuple(tokens), declarations + tuple(effects))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant("main", "dma", f"{name}:dma", records=records, allocations=tuple(allocations), external_backings=tuple(backings), binding_slots=tuple(slots))
    streams = {}
    for spec in specs:
        if spec.core_id not in streams:
            flags = A.STREAM_FLAGS.IS_LOCAL_CONTROL | (A.STREAM_FLAGS.IS_LIFECYCLE if spec.core_id == 0 else 0)
            streams[spec.core_id] = variant.stream(spec.core_id, 0, flags)
    streams[min(streams)].control_command(RequestBeginAttrs())
    for op in effects:
        streams[op.owner_core].kernel_command(op.op_id)
    streams[min(streams)].control_command(RequestEndAttrs())
    for stream in streams.values():
        stream.control_command(HaltAttrs())
    return builder.build()


def load_saturation_layout(shape):
    if shape == "contiguous":
        return ((1, 64 * 1024, 64 * 1024),)
    if shape == "multi_tensor":
        return ((1, 16 * 1024, 16 * 1024),) * 4
    if shape == "strided":
        return ((64, 512, 1024),)
    raise ValueError(f"unknown load saturation shape {shape}")


def _build_load_saturation_program(arch, shape):
    specs = []
    for core_id in (0, 1):
        for index, (rows, row_bytes, source_stride) in enumerate(load_saturation_layout(shape)):
            columns = row_bytes // DType.FP16.byte_width
            logical_shape = (columns,) if rows == 1 else (rows, columns)
            source_strides = (1,) if rows == 1 else (source_stride // DType.FP16.byte_width, 1)
            destination_strides = (1,) if rows == 1 else (columns, 1)
            binding_offset = LOAD_SATURATION_HBM_BASE + (core_id * 8 + index) * LOAD_SATURATION_TENSOR_BYTES
            specs.append(_LoadSpec(core_id, logical_shape, source_strides, destination_strides, DType.FP16, binding_offset))
    return _build_load_program(arch, f"build_load_saturation_{shape}_program", tuple(specs))


def _build_compute_timing_program(arch, name):
    shape = (1, 2, 2)
    lhs_slice_shape = (1, 2, 1)
    rhs_slice_shape = (1, 1, 2)
    strides = (4, 2, 1)
    size = 8
    attrs = MatmulAttrs(batch_axes=(0,), accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "a", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
        KernelTensor(2, 0, None, 2, 0, "b", TensorRole.WEIGHT, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
        KernelTensor(3, 1, None, 3, 0, "gemm", TensorRole.ACTIVATION, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
        KernelTensor(4, 2, None, 4, 0, "bmm", TensorRole.ACTIVATION, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
        KernelTensor(5, 2, SynthesizedTensorPurpose.PARTIAL_SUM, 5, 0, "bmm:partial", TensorRole.ACTIVATION, DType.FP32, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size * 2, size * 2, None),
        KernelTensor(6, 3, None, 6, 0, "relu", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
    )
    placements = (Placement(1, (1,)), Placement(2, (0, 1)), Placement(3, (1,)))
    shards = (
        TensorShard(1, 1, 1, 1, DistributionKind.REPLICATED, (0, 0, 0), shape, shape, 0),
        TensorShard(2, 1, 2, 0, DistributionKind.PARTITIONED, (0, 0, 0), lhs_slice_shape, lhs_slice_shape, 0),
        TensorShard(3, 1, 2, 1, DistributionKind.PARTITIONED, (0, 0, 1), lhs_slice_shape, lhs_slice_shape, 0),
        TensorShard(4, 2, 1, 1, DistributionKind.REPLICATED, (0, 0, 0), shape, shape, 0),
        TensorShard(5, 2, 2, 0, DistributionKind.PARTITIONED, (0, 0, 0), rhs_slice_shape, rhs_slice_shape, 0),
        TensorShard(6, 2, 2, 1, DistributionKind.PARTITIONED, (0, 1, 0), rhs_slice_shape, rhs_slice_shape, 0),
        TensorShard(7, 5, 2, 0, DistributionKind.PARTIAL_SUM, (0, 0, 0), shape, shape, 1),
        TensorShard(8, 5, 2, 1, DistributionKind.PARTIAL_SUM, (0, 0, 0), shape, shape, 1),
        TensorShard(9, 3, 1, 1, DistributionKind.PARTITIONED, (0, 0, 0), shape, shape, 0),
        TensorShard(10, 4, 1, 1, DistributionKind.PARTITIONED, (0, 0, 0), shape, shape, 0),
        TensorShard(11, 6, 3, 1, DistributionKind.PARTITIONED, (0, 0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 2, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 5, 0, MemorySpace.CORE_SRAM, shape, strides, size * 2, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 1, 1, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 2, 1, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 3, 1, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(7, 5, 1, MemorySpace.CORE_SRAM, shape, strides, size * 2, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(8, 5, 1, MemorySpace.CORE_SRAM, shape, strides, size * 2, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(9, 6, 1, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(10, 4, 1, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 2, (0, 0, 0), lhs_slice_shape, lhs_slice_shape, 0, strides, Layout.BLOCKED_MNK, BlockedMnkLayout(1, 1, 1, (0, 1, 2)), 0),
        BufferView(2, 2, 5, (0, 0, 0), rhs_slice_shape, rhs_slice_shape, 0, strides, Layout.BLOCKED_MNK, BlockedMnkLayout(1, 1, 1, (0, 1, 2)), 0),
        BufferView(3, 3, 7, (0, 0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(4, 4, 1, (0, 0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(5, 4, 3, (0, 0, 0), lhs_slice_shape, lhs_slice_shape, 1, strides, Layout.BLOCKED_MNK, BlockedMnkLayout(1, 1, 1, (0, 1, 2)), 0),
        BufferView(6, 5, 4, (0, 0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(7, 5, 6, (0, 0, 0), rhs_slice_shape, rhs_slice_shape, 2, strides, Layout.BLOCKED_MNK, BlockedMnkLayout(1, 1, 1, (0, 1, 2)), 0),
        BufferView(8, 6, 9, (0, 0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(9, 7, 8, (0, 0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(10, 8, 8, (0, 0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(11, 9, 11, (0, 0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(12, 10, 10, (0, 0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EMPTY), TensorState(2, 1, 1, StateOrigin.PRODUCED),
        TensorState(3, 2, 0, StateOrigin.EMPTY), TensorState(4, 2, 1, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY), TensorState(6, 3, 1, StateOrigin.PRODUCED, 1),
        TensorState(7, 4, 0, StateOrigin.EMPTY), TensorState(8, 4, 1, StateOrigin.PRODUCED),
        TensorState(9, 5, 0, StateOrigin.EMPTY), TensorState(10, 5, 1, StateOrigin.PRODUCED),
        TensorState(11, 6, 0, StateOrigin.EMPTY), TensorState(12, 6, 1, StateOrigin.PRODUCED),
        TensorState(13, 7, 0, StateOrigin.EMPTY), TensorState(14, 7, 1, StateOrigin.PRODUCED, 1), TensorState(15, 7, 2, StateOrigin.PRODUCED),
        TensorState(16, 8, 0, StateOrigin.EMPTY), TensorState(17, 8, 1, StateOrigin.PRODUCED, 1),
        TensorState(18, 9, 0, StateOrigin.EMPTY), TensorState(19, 9, 1, StateOrigin.PRODUCED),
        TensorState(20, 10, 0, StateOrigin.EMPTY), TensorState(21, 10, 1, StateOrigin.PRODUCED),
    )
    region = ElementRegion((0, 0, 0), shape, (1, 1, 1))
    lhs_partial = ElementRegion((0, 0, 0), lhs_slice_shape, (1, 1, 1))
    rhs_partial = ElementRegion((0, 0, 0), rhs_slice_shape, (1, 1, 1))
    full_tile = KernelTile(0, 0, 0, 0, 1, 2, 2, 2, 1, 2, 2, 2)
    first_tile = KernelTile(0, 0, 0, 0, 1, 2, 2, 1, 1, 2, 2, 1)
    second_tile = KernelTile(0, 0, 0, 1, 1, 2, 2, 1, 1, 2, 2, 1)
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(23, 0, "timing:00:fill:peer:a", KernelOpcode.DMA, 0, 0, (), (StateTransition(1, 2, 1, lhs_partial),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x11"), (), 1),
        KernelOp(24, 0, "timing:01:fill:peer:b", KernelOpcode.DMA, 0, 0, (), (StateTransition(3, 4, 2, rhs_partial),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x22"), (1,), 2),
        KernelOp(25, 2, "timing:02:bmm:peer", KernelOpcode.BMM, 0, 7, (OperandAccess(2, 1, lhs_partial, OperandAccessMode.READ), OperandAccess(4, 2, rhs_partial, OperandAccessMode.READ)), (StateTransition(5, 6, 3, region),), GemmKernelAttrs(OpCode.BMM, attrs, first_tile, KernelCost(8, 16, 24, 4, 0, 0), MatrixPhase.ACCUMULATE_ONLY, 1), (2,), 3),
        KernelOp(26, 0, "timing:03:p2p", KernelOpcode.DMA, 0, 0, (OperandAccess(6, 3, region, OperandAccessMode.READ),), (StateTransition(16, 17, 10, region),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, 1, b""), (3,), 4),
        KernelOp(27, 0, "timing:04:fill:selected:a", KernelOpcode.DMA, 1, 0, (), (StateTransition(7, 8, 4, region),), DmaAttrs(DmaKind.LOCAL_FILL, 1, 1, 1, 0, b"\x33"), (), 5),
        KernelOp(28, 0, "timing:05:fill:selected:b", KernelOpcode.DMA, 1, 0, (), (StateTransition(9, 10, 6, region),), DmaAttrs(DmaKind.LOCAL_FILL, 1, 1, 1, 0, b"\x44"), (5,), 6),
        KernelOp(29, 1, "timing:06:gemm:selected", KernelOpcode.GEMM, 1, 9, (OperandAccess(8, 4, region, OperandAccessMode.READ), OperandAccess(10, 6, region, OperandAccessMode.READ)), (StateTransition(11, 12, 8, region),), GemmKernelAttrs(OpCode.MATMUL, attrs, full_tile, KernelCost(16, 8, 24, 8, 0, 0), MatrixPhase.DIRECT, 0), (6,), 7),
        KernelOp(30, 2, "timing:07:bmm:selected", KernelOpcode.BMM, 1, 8, (OperandAccess(8, 5, lhs_partial, OperandAccessMode.READ), OperandAccess(10, 7, rhs_partial, OperandAccessMode.READ)), (StateTransition(13, 14, 9, region),), GemmKernelAttrs(OpCode.BMM, attrs, second_tile, KernelCost(8, 16, 24, 4, 0, 0), MatrixPhase.ACCUMULATE_ONLY, 1), (7,), 8),
        KernelOp(31, 0, "timing:08:recv", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(1, 0, 1, 16), (4,), 9),
        KernelOp(32, 3, "timing:09:relu:selected", KernelOpcode.VECTOR, 1, 11, (OperandAccess(12, 8, region, OperandAccessMode.READ),), (StateTransition(18, 19, 11, region),), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), full_tile, KernelCost(8, 8, 16, 0, 4, 0), VectorAlgorithm.ELEMENTWISE), (8,), 10),
        KernelOp(33, 2, "timing:10:reduce:selected", KernelOpcode.LOCAL_REDUCE, 1, 8, (OperandAccess(17, 10, region, OperandAccessMode.READ), OperandAccess(14, 9, region, OperandAccessMode.READ)), (StateTransition(14, 15, 9, region),), LocalReduceAttrs(ReduceKind.SUM, 1, full_tile, KernelCost(32, 16, 48, 0, 0, 4)), (9, 10), 11),
        KernelOp(34, 2, "timing:11:epilogue:selected", KernelOpcode.MATRIX_EPILOGUE, 1, 10, (OperandAccess(15, 9, region, OperandAccessMode.READ),), (StateTransition(20, 21, 12, region),), MatrixEpilogueKernelAttrs(OpCode.BMM, attrs, full_tile, KernelCost(16, 8, 24, 0, 4, 0), MatrixEpilogueAlgorithm.VECTOR_ACCUMULATION), (11,), 12),
    )
    records = KernelMemoryRecords(
        tensors,
        (KernelComputation(1, OpCode.MATMUL, (1, 2), 3, attrs), KernelComputation(2, OpCode.BMM, (1, 2), 4, attrs), KernelComputation(3, OpCode.RELU, (3,), 6, ElementwiseAttrs())),
        placements,
        shards,
        (PartialSumDefinition(1, 2, 4, 5, 2),),
        objects,
        views,
        states,
        tuple(ControlToken(index) for index in range(1, 13)),
        declarations + effects,
    )
    allocations = tuple(
        SramAllocation(index, object_id, core, offset, 64, arch.sram_base_alignment_bytes)
        for index, (object_id, core, offset) in enumerate(((1, 0, 0), (2, 0, 0x100), (3, 0, 0x200), (4, 1, 0), (5, 1, 0x100), (6, 1, 0x200), (7, 1, 0x300), (8, 1, 0x400), (9, 1, 0x500), (10, 1, 0x600)), 1)
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant("main", "compute_timing", f"{name}:compute_timing", records=records, allocations=allocations)
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    for op_id in (23, 24, 25, 26):
        stream0.kernel_command(op_id)
    for op_id in (27, 28, 29, 30, 31, 32, 33, 34):
        stream1.kernel_command(op_id)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_compute_timing_program(arch):
    return _build_compute_timing_program(arch, "build_compute_timing_program")


def build_fill_program(arch):
    shape = (8, 8)
    strides = (8, 1)
    size = 128
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(2, 1, None, 2, 0, "output", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
    )
    views = tuple(
        BufferView(index, item.object_id, (1, 1, 2, 2)[index - 1], (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, item in enumerate(objects, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 2, 2, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY),
        TensorState(6, 3, 1, StateOrigin.PRODUCED),
        TensorState(7, 4, 0, StateOrigin.EMPTY),
        TensorState(8, 4, 1, StateOrigin.PRODUCED),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, next(obj.owner_core for obj in objects if obj.object_id == item.object_id), 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    region = ElementRegion((0, 0), shape, (1, 1))
    zero_region = ElementRegion((0, 0), (1, 0), (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, 8, 8, 8, 1, 8, 8, 8)
    effects = (
        KernelOp(9, 0, "fill:input", KernelOpcode.DMA, 0, 0, (), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\xef\xcd\xab\x89\x67\x45\x23\x01"), (), 1),
        KernelOp(10, 0, "load:zero", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, zero_region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, zero_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (1,), 2),
        KernelOp(11, 1, "gemm", KernelOpcode.GEMM, 0, 2, (OperandAccess(4, 2, region, OperandAccessMode.READ), OperandAccess(4, 2, region, OperandAccessMode.READ)), (StateTransition(5, 6, 3, region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(size * 2, size, size * 3, 512, 0, 0), MatrixPhase.DIRECT, 0), (1, 2), 3),
        KernelOp(12, 0, "store:output", KernelOpcode.DMA, 0, 0, (OperandAccess(6, 3, region, OperandAccessMode.READ),), (StateTransition(7, 8, 4, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (3,), 4),
    )
    records = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.MATMUL, (1, 1), 2, attrs),), (Placement(1, (0,)),), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 5)), declarations + effects)
    allocations = (
        SramAllocation(1, 2, 0, 0, size, arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 0, 0x2000, size, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, size, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x300000, size, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = (
        BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]),
        BindingSlot(2, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_fill_program", 1))
    variant = builder.variant("main", "b1_m8n8k8", "build_fill_program:b1_m8n8k8", records=records, allocations=allocations, external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(4, ExternalSlotBacking(2))), binding_slots=slots)
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op_id in (9, 10, 11, 12):
        stream.kernel_command(op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_fill_offset_program(arch):
    shape = (8, 8)
    strides = (8, 1)
    size = 128
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(2, 1, None, 2, 0, "output", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
    )
    views = tuple(
        BufferView(index, item.object_id, (1, 1, 2, 2)[index - 1], (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, item in enumerate(objects, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 2, 2, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY),
        TensorState(6, 3, 1, StateOrigin.PRODUCED),
        TensorState(7, 4, 0, StateOrigin.EMPTY),
        TensorState(8, 4, 1, StateOrigin.PRODUCED),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, next(obj.owner_core for obj in objects if obj.object_id == item.object_id), 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    region = ElementRegion((0, 0), shape, (1, 1))
    zero_region = ElementRegion((0, 0), (1, 0), (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, 8, 8, 8, 1, 8, 8, 8)
    effects = (
        KernelOp(9, 0, "fill:input", KernelOpcode.DMA, 0, 0, (), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\xef\xcd\xab\x89\x67\x45\x23\x01"), (), 1),
        KernelOp(10, 0, "load:zero", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, zero_region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, zero_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (1,), 2),
        KernelOp(11, 1, "gemm", KernelOpcode.GEMM, 0, 2, (OperandAccess(4, 2, region, OperandAccessMode.READ), OperandAccess(4, 2, region, OperandAccessMode.READ)), (StateTransition(5, 6, 3, region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(size * 2, size, size * 3, 512, 0, 0), MatrixPhase.DIRECT, 0), (1, 2), 3),
        KernelOp(12, 0, "store:output", KernelOpcode.DMA, 0, 0, (OperandAccess(6, 3, region, OperandAccessMode.READ),), (StateTransition(7, 8, 4, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (3,), 4),
    )
    records = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.MATMUL, (1, 1), 2, attrs),), (Placement(1, (0,)),), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 5)), declarations + effects)
    allocations = (
        SramAllocation(1, 2, 0, 0x4000, size, arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 0, 0x2000, size, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, size, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x300000, size, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = (
        BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]),
        BindingSlot(2, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_fill_offset_program", 1))
    variant = builder.variant("main", "b1_m8n8k8", "build_fill_offset_program:b1_m8n8k8", records=records, allocations=allocations, external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(4, ExternalSlotBacking(2))), binding_slots=slots)
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op_id in (9, 10, 11, 12):
        stream.kernel_command(op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


_VIEW_OFFSET_SHAPE = (8, 8)
_VIEW_OFFSET_WHOLE_REGION = ElementRegion((0, 0), _VIEW_OFFSET_SHAPE, (1, 1))
_VIEW_OFFSET_SPLIT_REGION = ElementRegion((0, 0), (8, 3), (1, 2))


def _build_view_offset_compute_program(
    arch,
    name,
    variant_name,
    variant_key,
    input_offset_elements,
    output_offset_elements,
    output_padding_region=None,
    strides=(8, 1),
):
    shape = _VIEW_OFFSET_SHAPE
    size = _extent_bytes(shape, (8, 1), DType.FP16)
    footprint = _extent_bytes(shape, strides, DType.FP16)

    def aligned(value):
        return (
            (value + arch.axi_data_bytes - 1) // arch.axi_data_bytes
        ) * arch.axi_data_bytes

    contiguous = strides == (8, 1)
    layout = Layout.CONTIGUOUS_ROW_MAJOR if contiguous else Layout.BLOCKED_MNK
    blocked = None if contiguous else BlockedMnkLayout(1, 1, 1, (0, 1, 2))
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, footprint, None),
        KernelTensor(2, 1, None, 2, 0, "output", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_WRITE, size, footprint, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
    )
    input_shape = (12, 8) if input_offset_elements else shape
    output_shape = (12, 8) if output_offset_elements else shape
    input_footprint = _extent_bytes(input_shape, strides, DType.FP16)
    output_footprint = _extent_bytes(output_shape, strides, DType.FP16)
    input_staging = (input_shape, input_footprint)
    output_staging = (output_shape, output_footprint)
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, footprint, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, input_staging[0], strides, input_staging[1], arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, output_staging[0], strides, output_staging[1], arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, footprint, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), shape, shape, 0, strides, layout, blocked, 0),
        BufferView(2, 2, 1, (0, 0), shape, shape, input_offset_elements, strides, layout, blocked, 0),
        BufferView(3, 3, 2, (0, 0), shape, shape, output_offset_elements, strides, layout, blocked, 0),
        BufferView(4, 4, 2, (0, 0), shape, shape, 0, strides, layout, blocked, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 2, 2, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY),
        TensorState(6, 3, 1, StateOrigin.PRODUCED),
        TensorState(7, 4, 0, StateOrigin.EMPTY),
        TensorState(8, 4, 1, StateOrigin.PRODUCED),
    ) + ((
        TensorState(9, 3, 2, StateOrigin.PRODUCED),
    ) if output_padding_region is not None else ())
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, next(obj.owner_core for obj in objects if obj.object_id == item.object_id), 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    region = ElementRegion((0, 0), shape, (1, 1))
    zero_region = ElementRegion((0, 0), (1, 0), (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, 8, 8, 8, 1, 8, 8, 8)
    effects = (
        KernelOp(9, 0, "fill:input", KernelOpcode.DMA, 0, 0, (), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\xef\xcd\xab\x89\x67\x45\x23\x01"), (), 1),
        KernelOp(10, 0, "load:zero", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, zero_region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, zero_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (1,), 2),
        KernelOp(11, 1, "gemm", KernelOpcode.GEMM, 0, 2, (OperandAccess(4, 2, region, OperandAccessMode.READ), OperandAccess(4, 2, region, OperandAccessMode.READ)), (StateTransition(5, 6, 3, region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(size * 2, size, size * 3, 512, 0, 0), MatrixPhase.DIRECT, 0), (1, 2), 3),
        KernelOp(12, 0, "store:output", KernelOpcode.DMA, 0, 0, (OperandAccess(6, 3, region, OperandAccessMode.READ),), (StateTransition(7, 8, 4, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (3,), 4),
    ) + ((
        KernelOp(13, 0, "fill:output", KernelOpcode.DMA, 0, 0, (), (StateTransition(6, 9, 3, output_padding_region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x10\x32\x54\x76\x98\xba\xdc\xfe"), (4,), 5),
    ) if output_padding_region is not None else ())
    records = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.MATMUL, (1, 1), 2, attrs),), (Placement(1, (0,)),), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 6 if output_padding_region is not None else 5)), declarations + effects)
    binding_bytes = aligned(footprint)
    allocations = (
        SramAllocation(1, 2, 0, 0x4000, aligned(input_footprint), arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 0, 0x2000, aligned(output_footprint), arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, binding_bytes, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x300000, binding_bytes, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = (
        BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, binding_bytes, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]),
        BindingSlot(2, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, binding_bytes, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant("main", variant_name, variant_key, records=records, allocations=allocations, external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(4, ExternalSlotBacking(2))), binding_slots=slots)
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op_id in ((9, 10, 11, 12, 13) if output_padding_region is not None else (9, 10, 11, 12)):
        stream.kernel_command(op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_fill_view_offset_program(arch):
    return _build_view_offset_compute_program(
        arch,
        "build_fill_view_offset_program",
        "b1_m8n8k8",
        "build_fill_view_offset_program:b1_m8n8k8",
        32,
        0,
    )


def build_fill_view_offset_out_program(arch):
    return _build_view_offset_compute_program(
        arch,
        "build_fill_view_offset_out_program",
        "b1_m8n8k8_out64",
        "build_fill_view_offset_out_program:b1_m8n8k8_out64",
        32,
        32,
    )


def build_fill_view_offset_out_pad_program(arch):
    return _build_view_offset_compute_program(
        arch,
        "build_fill_view_offset_out_pad_program",
        "b1_m8n8k8_out64_pad",
        "build_fill_view_offset_out_pad_program:b1_m8n8k8_out64_pad",
        32,
        32,
        output_padding_region=_VIEW_OFFSET_WHOLE_REGION,
    )


def build_fill_view_offset_in0_program(arch):
    return _build_view_offset_compute_program(
        arch,
        "build_fill_view_offset_in0_program",
        "b1_m8n8k8_in0",
        "build_fill_view_offset_in0_program:b1_m8n8k8_in0",
        0,
        0,
    )


def build_fill_view_offset_in0_out_program(arch):
    return _build_view_offset_compute_program(
        arch,
        "build_fill_view_offset_in0_out_program",
        "b1_m8n8k8_in0_out64",
        "build_fill_view_offset_in0_out_program:b1_m8n8k8_in0_out64",
        0,
        32,
    )


def _padding_seed_program(arch, seed_value, base=None, variant_label=""):
    seed = base if base is not None else build_fill_view_offset_out_pad_program(arch)
    sem = seed.semantics
    tensors = list(sem.kernel_tensors)
    shards = list(sem.logical_shards)
    objects = list(sem.objects)
    views = list(sem.views)
    states = list(sem.states)
    initial_ops = []
    allocations = [
        SramAllocation(a.allocation_id, b.object_id, a.owner_core, a.offset_bytes,
                       a.size_bytes, a.alignment_bytes)
        for a in seed.allocations
        for b in sem.object_backings
        if isinstance(b.backing, LocalAllocationBacking)
        and b.backing.allocation_id == a.allocation_id
    ]
    for index, address in enumerate((0x4000, 0x2000)):
        tensor_id, object_id, view_id = 3 + index, 5 + index, 5 + index
        state_id = len(states) + 1
        tensors.append(KernelTensor(tensor_id, 0, None, tensor_id, 0,
            f"padding-seed-{index}", TensorRole.ACTIVATION, DType.INT8,
            (192,), (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, 192, 192, None))
        shards.append(TensorShard(tensor_id, tensor_id, 1, 0,
            DistributionKind.PARTITIONED, (0,), (192,), (192,), 0))
        objects.append(BufferObject(object_id, tensor_id, 0, MemorySpace.CORE_SRAM,
            (192,), (1,), 192, arch.sram_base_alignment_bytes, False, 0))
        views.append(BufferView(view_id, object_id, tensor_id, (0,), (192,), (192,),
            0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0))
        states.extend((TensorState(state_id, object_id, 0, StateOrigin.EMPTY),
                       TensorState(state_id + 1, object_id, 1, StateOrigin.PRODUCED)))
        initial_ops.append(KernelOp(13 + index, 0, f"padding:seed:{index}",
            KernelOpcode.DMA, 0, 0, (),
            (StateTransition(state_id, state_id + 1, view_id,
                             ElementRegion((0,), (192,), (1,))),),
            DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, bytes([seed_value]) * 8),
            (6,) if index else (), 6 + index))
        allocations.append(SramAllocation(3 + index, object_id, 0, address, 192,
                                          arch.sram_base_alignment_bytes))
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC,
                 item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(6 + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW,
                 next(obj.owner_core for obj in objects
                      if obj.object_id == item.object_id),
                 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    effects = tuple(
        dataclasses.replace(op, op_id=15 + index,
                            after_tokens=(7,) if index == 0 else op.after_tokens)
        for index, op in enumerate(sem.kernel_ops[8:])
    )
    records = KernelMemoryRecords(
        tuple(tensors), sem.computations, sem.placements, tuple(shards),
        sem.partial_sums, tuple(objects), tuple(views), tuple(states),
        tuple(ControlToken(index) for index in range(1, 8)),
        declarations + tuple(initial_ops) + effects,
    )
    label = f"{variant_label}_" if variant_label else ""
    builder = ProgramBuilder(arch, AuthoredProgramOrigin(
        "golden", f"build_fill_view_offset_padding_{label}{seed_value:02x}_program", 1))
    variant = builder.variant(
        "main",
        f"padding_{label}{seed_value:02x}",
        f"build_fill_view_offset_padding_{label}{seed_value:02x}_program:{seed_value:02x}",
        records=records,
        allocations=tuple(allocations),
        external_backings=tuple(
            item for item in sem.object_backings
            if isinstance(item.backing, ExternalSlotBacking)
        ),
        binding_slots=sem.binding_slots,
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op in (*initial_ops, *effects):
        stream.kernel_command(op.op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_fill_view_offset_padding_zero_program(arch):
    return _padding_seed_program(arch, 0x00)


def build_fill_view_offset_padding_pattern_program(arch):
    return _padding_seed_program(arch, 0x5A)


def _row_gap_padding_base(arch):
    return _build_view_offset_compute_program(
        arch,
        "build_fill_view_offset_row_gap_base_program",
        "row_gap_base",
        "build_fill_view_offset_row_gap_base_program:row_gap_base",
        0,
        0,
        output_padding_region=_VIEW_OFFSET_WHOLE_REGION,
        strides=(10, 1),
    )


def build_fill_view_offset_row_gap_zero_program(arch):
    return _padding_seed_program(
        arch, 0x00, base=_row_gap_padding_base(arch), variant_label="row_gap"
    )


def build_fill_view_offset_row_gap_pattern_program(arch):
    return _padding_seed_program(
        arch, 0x5A, base=_row_gap_padding_base(arch), variant_label="row_gap"
    )


def build_fill_view_offset_split_program(arch):
    return _build_view_offset_compute_program(
        arch,
        "build_fill_view_offset_split_program",
        "b1_m8n8k8_split",
        "build_fill_view_offset_split_program:b1_m8n8k8_split",
        32,
        32,
        output_padding_region=_VIEW_OFFSET_SPLIT_REGION,
    )


def build_fill_view_offset_split_zero_program(arch):
    return _padding_seed_program(
        arch,
        0x00,
        base=build_fill_view_offset_split_program(arch),
        variant_label="split",
    )


def build_fill_view_offset_split_pattern_program(arch):
    return _padding_seed_program(
        arch,
        0x5A,
        base=build_fill_view_offset_split_program(arch),
        variant_label="split",
    )


def build_poison_program(arch):
    return _named_single(arch, "build_poison_program")


def build_dma_edge_program(arch):
    width = arch.axi_data_bytes
    cap = arch.axi_max_burst_beats * width
    pairs = (
        (1, 0x100000, 0x200000),
        (1, 0x100041, 0x200040),
        (width - 1, 0x100080, 0x200080),
        (width + 1, 0x1000C0, 0x2000C0),
        (64, 0x100FF0, 0x201000),
        (cap - 1, 0x101100, 0x201100),
        (cap + 1, 0x101400, 0x201400),
        (700, 0x101800, 0x201800),
    )
    tensors = []
    shards = []
    objects = []
    views = []
    states = []
    tokens = []
    effects = []
    allocations = []
    backings = []
    slots = []
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    local_offset = 0
    declaration_count = len(pairs) * 6
    for ordinal, (size, source_offset, destination_offset) in enumerate(pairs, 1):
        input_tensor_id = ordinal * 2 - 1
        output_tensor_id = ordinal * 2
        input_shard_id = ordinal * 2 - 1
        output_shard_id = ordinal * 2
        external_input_id = ordinal * 3 - 2
        local_id = ordinal * 3 - 1
        external_output_id = ordinal * 3
        source_base = source_offset // width * width
        destination_base = destination_offset // width * width
        source_prefix = source_offset - source_base
        destination_prefix = destination_offset - destination_base
        source_backing_size = (source_prefix + size + width - 1) // width * width
        destination_backing_size = (destination_prefix + size + width - 1) // width * width
        tensors.extend((
            KernelTensor(input_tensor_id, 0, None, input_tensor_id, 0, f"input:{ordinal}", TensorRole.INPUT, DType.INT8, (size,), (1,), StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
            KernelTensor(output_tensor_id, 0, None, input_tensor_id, 0, f"output:{ordinal}", TensorRole.OUTPUT, DType.INT8, (size,), (1,), StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
        ))
        shards.extend((
            TensorShard(input_shard_id, input_tensor_id, 1, 0, DistributionKind.PARTITIONED, (0,), (size,), (size,), 0),
            TensorShard(output_shard_id, output_tensor_id, 1, 0, DistributionKind.PARTITIONED, (0,), (size,), (size,), 0),
        ))
        objects.extend((
            BufferObject(external_input_id, input_tensor_id, INVALID_CORE_ID, MemorySpace.HBM, (source_backing_size,), (1,), source_backing_size, width, False, 0),
            BufferObject(local_id, input_tensor_id, 0, MemorySpace.CORE_SRAM, (size,), (1,), size, arch.sram_base_alignment_bytes, False, 0),
            BufferObject(external_output_id, input_tensor_id, INVALID_CORE_ID, MemorySpace.HBM, (destination_backing_size,), (1,), destination_backing_size, width, False, 0),
        ))
        views.extend((
            BufferView(external_input_id, external_input_id, input_shard_id, (0,), (size,), (size,), source_prefix, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
            BufferView(local_id, local_id, input_shard_id, (0,), (size,), (size,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
            BufferView(external_output_id, external_output_id, output_shard_id, (0,), (size,), (size,), destination_prefix, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        ))
        state_base = ordinal * 5 - 4
        states.extend((
            TensorState(state_base, external_input_id, 0, StateOrigin.EXTERNAL),
            TensorState(state_base + 1, local_id, 0, StateOrigin.EMPTY),
            TensorState(state_base + 2, local_id, 1, StateOrigin.PRODUCED),
            TensorState(state_base + 3, external_output_id, 0, StateOrigin.EMPTY),
            TensorState(state_base + 4, external_output_id, 1, StateOrigin.PRODUCED),
        ))
        region = ElementRegion((0,), (size,), (1,))
        load_token = ordinal * 2 - 1
        store_token = ordinal * 2
        tokens.extend((ControlToken(load_token), ControlToken(store_token)))
        load_id = declaration_count + ordinal * 2 - 1
        store_id = declaration_count + ordinal * 2
        effects.extend((
            KernelOp(load_id, 0, f"edge:load:{ordinal}", KernelOpcode.DMA, 0, 0, (OperandAccess(state_base, external_input_id, region, OperandAccessMode.READ),), (StateTransition(state_base + 1, state_base + 2, local_id, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), () if ordinal == 1 else (load_token - 1,), load_token),
            KernelOp(store_id, 0, f"edge:store:{ordinal}", KernelOpcode.DMA, 0, 0, (OperandAccess(state_base + 2, local_id, region, OperandAccessMode.READ),), (StateTransition(state_base + 3, state_base + 4, external_output_id, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (load_token,), store_token),
        ))
        local_size = (size + arch.sram_base_alignment_bytes - 1) // arch.sram_base_alignment_bytes * arch.sram_base_alignment_bytes
        allocations.append(SramAllocation(ordinal, local_id, 0, local_offset, local_size, arch.sram_base_alignment_bytes))
        local_offset += local_size
        input_slot = ordinal * 2 - 1
        output_slot = ordinal * 2
        input_binding = Binding(input_slot, hbm, INVALID_CORE_ID, source_base, source_backing_size, width, Access.READ_ONLY)
        output_binding = Binding(output_slot, hbm, INVALID_CORE_ID, destination_base, destination_backing_size, width, Access.READ_WRITE)
        slots.extend((
            BindingSlot(input_slot, f"input:{ordinal}", MemorySpace.HBM, hbm, INVALID_CORE_ID, source_backing_size, width, Access.READ_ONLY, input_binding),
            BindingSlot(output_slot, f"output:{ordinal}", MemorySpace.HBM, hbm, INVALID_CORE_ID, destination_backing_size, width, Access.READ_WRITE, output_binding),
        ))
        backings.extend((ObjectBacking(external_input_id, ExternalSlotBacking(input_slot)), ObjectBacking(external_output_id, ExternalSlotBacking(output_slot))))
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, objects[index - 1].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    records = KernelMemoryRecords(tuple(tensors), (), (Placement(1, (0,)),), tuple(shards), (), tuple(objects), tuple(views), tuple(states), tuple(tokens), declarations + tuple(effects))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_dma_edge_program", 1))
    variant = builder.variant("main", "dma_edge", "build_dma_edge_program:dma_edge", records=records, allocations=tuple(allocations), external_backings=tuple(backings), binding_slots=tuple(slots))
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for item in effects:
        stream.kernel_command(item.op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_dma_error_program(arch):
    shape = (8, 8)
    strides = (8, 1)
    size = 128
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(2, 1, None, 2, 0, "output", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
    )
    views = tuple(BufferView(index, index, (1, 1, 2, 2)[index - 1], (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0) for index in range(1, 5))
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY), TensorState(5, 3, 1, StateOrigin.PRODUCED),
        TensorState(6, 4, 0, StateOrigin.EMPTY), TensorState(7, 4, 1, StateOrigin.PRODUCED),
    )
    region = ElementRegion((0, 0), shape, (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, 8, 8, 8, 1, 8, 8, 8)
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(9, 0, "read-error:load", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(10, 1, "read-error:gemm", KernelOpcode.GEMM, 0, 2, (OperandAccess(3, 2, region, OperandAccessMode.READ), OperandAccess(3, 2, region, OperandAccessMode.READ)), (StateTransition(4, 5, 3, region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(256, 128, 384, 512, 0, 0), MatrixPhase.DIRECT, 0), (1,), 2),
        KernelOp(11, 0, "read-error:store", KernelOpcode.DMA, 0, 0, (OperandAccess(5, 3, region, OperandAccessMode.READ),), (StateTransition(6, 7, 4, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (2,), 3),
    )
    records = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.MATMUL, (1, 1), 2, attrs),), (Placement(1, (0,)),), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 4)), declarations + effects)
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (Binding(1, hbm, INVALID_CORE_ID, 0x800000, size, arch.axi_data_bytes, Access.READ_ONLY), Binding(2, hbm, INVALID_CORE_ID, 0x200000, size, arch.axi_data_bytes, Access.READ_WRITE))
    slots = (BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]), BindingSlot(2, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_dma_error_program", 1))
    variant = builder.variant("main", "b1_m8n8k8", "build_dma_error_program:b1_m8n8k8", records=records, allocations=(SramAllocation(1, 2, 0, 0, size, arch.sram_base_alignment_bytes), SramAllocation(2, 3, 0, 0x2000, size, arch.sram_base_alignment_bytes)), external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(4, ExternalSlotBacking(2))), binding_slots=slots)
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op_id in (9, 10, 11):
        stream.kernel_command(op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_dma_write_error_program(arch):
    size = 2 * arch.axi_max_burst_beats * arch.axi_data_bytes
    shape = (size // DType.INT8.byte_width,)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.INT8, shape, (1,), StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(2, 0, None, 1, 0, "output", TensorRole.OUTPUT, DType.INT8, shape, (1,), StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, (1,), size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, (1,), size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, (1,), size, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0,), shape, shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 1, (0,), shape, shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 3, 2, (0,), shape, shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY),
        TensorState(5, 3, 1, StateOrigin.PRODUCED),
    )
    region = ElementRegion((0,), shape, (1,))
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(7, 0, "write-error:load", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(8, 0, "write-error:store", KernelOpcode.DMA, 0, 0, (OperandAccess(3, 2, region, OperandAccessMode.READ),), (StateTransition(4, 5, 3, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (1,), 2),
    )
    records = KernelMemoryRecords(tensors, (), (Placement(1, (0,)),), shards, (), objects, views, states, (ControlToken(1), ControlToken(2)), declarations + effects)
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, size, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x200000, size, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = (
        BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]),
        BindingSlot(2, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_dma_write_error_program", 1))
    variant = builder.variant("main", "dma_write_error", "build_dma_write_error_program:dma_write_error", records=records, allocations=(SramAllocation(1, 2, 0, 0, size, arch.sram_base_alignment_bytes),), external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(3, ExternalSlotBacking(2))), binding_slots=slots)
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(7)
    stream.kernel_command(8)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_dma_fence_program(arch):
    small = 64
    big = 8 * arch.axi_max_burst_beats * arch.axi_data_bytes
    input_shape = (small // DType.FP16.byte_width,)
    output_shape = (big // DType.FP16.byte_width,)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, input_shape, (1,), StorageClass.EXTERNAL, Access.READ_ONLY, small, small, None),
        KernelTensor(2, 1, None, 2, 0, "work", TensorRole.ACTIVATION, DType.FP16, output_shape, (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, big, big, None),
        KernelTensor(3, 0, None, 2, 0, "output", TensorRole.OUTPUT, DType.FP16, output_shape, (1,), StorageClass.EXTERNAL, Access.READ_WRITE, big, big, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,), input_shape, input_shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0,), output_shape, output_shape, 0),
        TensorShard(3, 3, 1, 0, DistributionKind.PARTITIONED, (0,), output_shape, output_shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, input_shape, (1,), small, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, input_shape, (1,), small, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, output_shape, (1,), big, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, INVALID_CORE_ID, MemorySpace.HBM, output_shape, (1,), big, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0,), input_shape, input_shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 1, (0,), input_shape, input_shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 3, 2, (0,), output_shape, output_shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(4, 3, 2, (0,), output_shape, output_shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 1),
        BufferView(5, 4, 3, (0,), output_shape, output_shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY), TensorState(5, 3, 1, StateOrigin.PRODUCED), TensorState(6, 3, 2, StateOrigin.PRODUCED),
        TensorState(7, 4, 0, StateOrigin.EMPTY), TensorState(8, 4, 1, StateOrigin.PRODUCED),
    )
    small_region = ElementRegion((0,), input_shape, (1,))
    big_region = ElementRegion((0,), output_shape, (1,))
    vector_region = ElementRegion((0,), (8,), (1,))
    tile = KernelTile(0, 0, 0, 0, 1, 1, 8, 1, 1, 1, 8, 1)
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(10, 0, "fence:fill", KernelOpcode.DMA, 0, 0, (), (StateTransition(4, 5, 3, big_region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\xa5"), (), 2),
        KernelOp(11, 0, "fence:load", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, small_region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, small_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(12, 1, "fence:vector", KernelOpcode.VECTOR, 0, 2, (OperandAccess(5, 3, vector_region, OperandAccessMode.READ),), (StateTransition(5, 6, 4, vector_region),), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile, KernelCost(16, 16, 32, 0, 8, 0), VectorAlgorithm.ELEMENTWISE), (2,), 3),
        KernelOp(13, 0, "fence:store", KernelOpcode.DMA, 0, 0, (OperandAccess(6, 4, big_region, OperandAccessMode.READ),), (StateTransition(7, 8, 5, big_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (3,), 4),
    )
    records = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.RELU, (2,), 2, ElementwiseAttrs()),), (Placement(1, (0,)),), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 5)), declarations + effects)
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (Binding(1, hbm, INVALID_CORE_ID, 0x100000, small, arch.axi_data_bytes, Access.READ_ONLY), Binding(2, hbm, INVALID_CORE_ID, 0x200000, big, arch.axi_data_bytes, Access.READ_WRITE))
    slots = (BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, small, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]), BindingSlot(2, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, big, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_dma_fence_program", 1))
    variant = builder.variant("main", "dma_fence", "build_dma_fence_program:dma_fence", records=records, allocations=(SramAllocation(1, 2, 0, 0, small, arch.sram_base_alignment_bytes), SramAllocation(2, 3, 0, 0x2000, big, arch.sram_base_alignment_bytes)), external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(4, ExternalSlotBacking(2))), binding_slots=slots)
    control = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    worker = variant.stream(0, 1)
    control.control_command(RequestBeginAttrs())
    control.kernel_command(11)
    control.control_command(AxiFenceAttrs(FenceScope.ALL_INSTANCE))
    control.control_command(RequestEndAttrs())
    control.control_command(HaltAttrs())
    for op_id in (10, 12, 13):
        worker.kernel_command(op_id)
    return builder.build()


def build_dma_pin_program(arch):
    size = 128
    shape = (size,)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "source", TensorRole.ACTIVATION, DType.INT8, shape, (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
        KernelTensor(2, 0, None, 1, 0, "output:1", TensorRole.OUTPUT, DType.INT8, shape, (1,), StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
        KernelTensor(3, 0, None, 1, 0, "output:2", TensorRole.OUTPUT, DType.INT8, shape, (1,), StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    shards = tuple(TensorShard(index, index, 1, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0) for index in range(1, 4))
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, (1,), size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, (1,), size, arch.axi_data_bytes, False, 0),
        BufferObject(3, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, (1,), size, arch.axi_data_bytes, False, 0),
    )
    views = tuple(BufferView(index, index, index, (0,), shape, shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0) for index in range(1, 4))
    states = (
        TensorState(1, 1, 0, StateOrigin.EMPTY), TensorState(2, 1, 1, StateOrigin.PRODUCED),
        TensorState(3, 2, 0, StateOrigin.EMPTY), TensorState(4, 2, 1, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY), TensorState(6, 3, 1, StateOrigin.PRODUCED),
    )
    region = ElementRegion((0,), shape, (1,))
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(7, 0, "pin:fill", KernelOpcode.DMA, 0, 0, (), (StateTransition(1, 2, 1, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x22"), (), 1),
        KernelOp(8, 0, "pin:store:1", KernelOpcode.DMA, 0, 0, (OperandAccess(2, 1, region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (1,), 2),
        KernelOp(9, 0, "pin:store:2", KernelOpcode.DMA, 0, 0, (OperandAccess(2, 1, region, OperandAccessMode.READ),), (StateTransition(5, 6, 3, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (1,), 3),
    )
    records = KernelMemoryRecords(tensors, (), (Placement(1, (0,)),), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 4)), declarations + effects)
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (Binding(1, hbm, INVALID_CORE_ID, 0x200000, size, arch.axi_data_bytes, Access.READ_WRITE), Binding(2, hbm, INVALID_CORE_ID, 0x200100, size, arch.axi_data_bytes, Access.READ_WRITE))
    slots = (BindingSlot(1, "output:1", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, bindings[0]), BindingSlot(2, "output:2", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_dma_pin_program", 1))
    variant = builder.variant("main", "dma_pin", "build_dma_pin_program:dma_pin", records=records, allocations=(SramAllocation(1, 1, 0, 0, size, arch.sram_base_alignment_bytes),), external_backings=(ObjectBacking(2, ExternalSlotBacking(1)), ObjectBacking(3, ExternalSlotBacking(2))), binding_slots=slots)
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(0, 1)
    stream0.control_command(RequestBeginAttrs())
    stream0.kernel_command(7)
    stream0.kernel_command(8)
    stream1.kernel_command(9)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    return builder.build()


def build_sram_parallel_program(arch):
    return _named_single(arch, "build_sram_parallel_program", sram_offsets=(0, 0x2080, 0x4100))


def build_sram_conflict_program(arch):
    return _named_single(arch, "build_sram_conflict_program", sram_offsets=(0, 0x2000, 0x4000))


def build_poison_elementwise_inplace_program(arch):
    shape = (8, 8)
    strides = (8, 1)
    size = 128
    tensor = KernelTensor(1, 1, None, 1, 0, "io", TensorRole.ACTIVATION, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None)
    shard = TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0)
    obj = BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0)
    view = BufferView(1, 1, 1, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
    region = ElementRegion((0, 0), shape, (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, 8, 8, 1, 1, 8, 8, 1)
    effects = (
        KernelOp(3, 0, "poison-ew:fill", KernelOpcode.DMA, 0, 0, (), (StateTransition(1, 2, 1, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x55"), (), 1),
        KernelOp(4, 1, "poison-ew:relu", KernelOpcode.VECTOR, 0, 1, (OperandAccess(2, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 1, region),), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile, KernelCost(size, size, size * 2, 0, 64, 0), VectorAlgorithm.ELEMENTWISE), (1,), 2),
    )
    records = KernelMemoryRecords(
        (tensor,),
        (KernelComputation(1, OpCode.RELU, (1,), 1, ElementwiseAttrs()),),
        (Placement(1, (0,)),),
        (shard,),
        (),
        (obj,),
        (view,),
        (TensorState(1, 1, 0, StateOrigin.EMPTY), TensorState(2, 1, 1, StateOrigin.PRODUCED), TensorState(3, 1, 2, StateOrigin.PRODUCED)),
        (ControlToken(1), ControlToken(2)),
        _declaration_ops((obj,), (view,)) + effects,
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_poison_elementwise_inplace_program", 1))
    variant = builder.variant("main", "b1", "build_poison_elementwise_inplace_program:b1", records=records, allocations=(SramAllocation(1, 1, 0, 0, size, arch.sram_base_alignment_bytes),))
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(3)
    stream.kernel_command(4)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_poison_store_program(arch):
    shape = (8, 8)
    strides = (8, 1)
    size = 128
    tensor = KernelTensor(1, 0, None, 1, 0, "output", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None)
    shard = TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0)
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
    )
    views = tuple(BufferView(index, index, 1, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0) for index in (1, 2))
    region = ElementRegion((0, 0), shape, (1, 1))
    effects = (
        KernelOp(5, 0, "poison-store:fill", KernelOpcode.DMA, 0, 0, (), (StateTransition(1, 2, 1, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x66"), (), 1),
        KernelOp(6, 0, "poison-store:store", KernelOpcode.DMA, 0, 0, (OperandAccess(2, 1, region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (1,), 2),
    )
    records = KernelMemoryRecords(
        (tensor,), (), (Placement(1, (0,)),), (shard,), (), objects, views,
        (TensorState(1, 1, 0, StateOrigin.EMPTY), TensorState(2, 1, 1, StateOrigin.PRODUCED), TensorState(3, 2, 0, StateOrigin.EMPTY), TensorState(4, 2, 1, StateOrigin.PRODUCED)),
        (ControlToken(1), ControlToken(2)), _declaration_ops(objects, views) + effects,
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    binding = Binding(1, hbm, INVALID_CORE_ID, 0x300000, size, arch.axi_data_bytes, Access.READ_WRITE)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_poison_store_program", 1))
    variant = builder.variant("main", "b1", "build_poison_store_program:b1", records=records, allocations=(SramAllocation(1, 1, 0, 0, size, arch.sram_base_alignment_bytes),), external_backings=(ObjectBacking(2, ExternalSlotBacking(1)),), binding_slots=(BindingSlot(1, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, binding),))
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(5)
    stream.kernel_command(6)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_poison_reduce_dst_old_program(arch):
    return _build_compute_timing_program(arch, "build_poison_reduce_dst_old_program")


def build_multi_descriptor_program(arch):
    """One DMA command whose source object is a padded two-row layout and
    whose destination is compact.  The segmenter cannot use the compact or
    regular row form, so it emits two descriptors in one completion group; the
    runtime must aggregate them and publish the completion event only after
    the last descriptor commits."""
    shape = (20, 2)
    strides = (3, 2)
    logical_bytes = shape[0] * shape[1] * DType.FP16.byte_width
    storage_bytes = _extent_bytes(shape, strides, DType.FP16)
    region = ElementRegion((0, 0), shape, (1, 1))
    tensor = KernelTensor(1, 0, None, 1, 0, "scratch", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_WRITE, logical_bytes, storage_bytes, None)
    shards = (TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),)
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, storage_bytes, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, strides, storage_bytes, arch.sram_base_alignment_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), shape, shape, 0, strides, None, None, 0),
        BufferView(2, 2, 1, (0, 0), shape, shape, 0, strides, None, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
    )
    effects = (
        KernelOp(5, 0, "multi:load", KernelOpcode.DMA, 0, 0,
                 (OperandAccess(1, 1, region, OperandAccessMode.READ),),
                 (StateTransition(2, 3, 2, region),),
                 DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""),
                 (), 1),
    )
    records = KernelMemoryRecords(
        (tensor,), (), (Placement(1, (0,)),), shards, (), objects, views, states,
        (ControlToken(1),), _declaration_ops(objects, views) + effects,
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_multi_descriptor_program", 1))
    variant = builder.variant(
        "main", "b1", "build_multi_descriptor_program:b1", records=records,
        allocations=(SramAllocation(1, 2, 0, 0, 0x100, arch.sram_base_alignment_bytes),),
        external_backings=(ObjectBacking(1, ExternalSlotBacking(1)),),
        binding_slots=(BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, 0x100, arch.axi_data_bytes, Access.READ_ONLY, Binding(1, hbm, INVALID_CORE_ID, 0x100000, 0x100, arch.axi_data_bytes, Access.READ_ONLY)),),
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(5)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def _build_pre_resident_store_program(arch):
    """A local pre-resident weight read by a DMA store, with no producer: the
    compiler's initialization analysis declares the weight resident before
    execution."""
    shape = (32,)
    strides = (1,)
    size = shape[0] * DType.FP16.byte_width
    region = ElementRegion((0,), shape, (1,))
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "weight", TensorRole.WEIGHT, DType.FP16, shape, strides, StorageClass.PRE_RESIDENT, Access.READ_ONLY, size, size, "ab" * 32),
        KernelTensor(2, 0, None, 2, 0, "sink", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    placements = (Placement(1, (0,)),)
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 2, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0,), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 1, (0,), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
    )
    effects = (
        KernelOp(5, 0, "resident:store", KernelOpcode.DMA, 0, 0,
                 (OperandAccess(1, 1, region, OperandAccessMode.READ),),
                 (StateTransition(2, 3, 2, region),),
                 DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""),
                 (), 1),
    )
    records = KernelMemoryRecords(
        tensors, (), placements, shards, (), objects, views, states,
        (ControlToken(1),), _declaration_ops(objects, views) + effects,
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_pre_resident_store_program", 1))
    variant = builder.variant(
        "main", "pre_resident_store", "build_pre_resident_store_program:pre_resident_store",
        records=records,
        allocations=(SramAllocation(1, 1, 0, 0, size, arch.sram_base_alignment_bytes),),
        external_backings=(ObjectBacking(2, ExternalSlotBacking(1)),),
        binding_slots=(BindingSlot(1, "sink", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, Binding(1, hbm, INVALID_CORE_ID, 0x200000, size, arch.axi_data_bytes, Access.READ_WRITE)),),
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(5)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_pre_resident_weight_program(arch):
    """A pre-resident local weight read by a GEMM before any producer commits;
    the compute result is stored to HBM so the run carries real content."""
    shape = (8, 8)
    strides = (8, 1)
    size = _extent_bytes(shape, strides, DType.FP16)
    region = ElementRegion((0, 0), shape, (1, 1))
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "weight", TensorRole.WEIGHT, DType.FP16, shape, strides, StorageClass.PRE_RESIDENT, Access.READ_ONLY, size, size, "ab" * 32),
        KernelTensor(2, 1, None, 2, 0, "output", TensorRole.OUTPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    placements = (Placement(1, (0,)),)
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 2, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 2, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 3, 2, (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY),
        TensorState(5, 3, 1, StateOrigin.PRODUCED),
    )
    tile = KernelTile(0, 0, 0, 0, 1, 8, 8, 8, 1, 8, 8, 8)
    effects = (
        KernelOp(7, 1, "resident:gemm", KernelOpcode.GEMM, 0, 2,
                 (OperandAccess(1, 1, region, OperandAccessMode.READ), OperandAccess(1, 1, region, OperandAccessMode.READ)),
                 (StateTransition(2, 3, 2, region),),
                 GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(size * 2, size, size * 3, 512, 0, 0), MatrixPhase.DIRECT, 0),
                 (), 1),
        KernelOp(8, 0, "resident:store", KernelOpcode.DMA, 0, 0,
                 (OperandAccess(3, 2, region, OperandAccessMode.READ),),
                 (StateTransition(4, 5, 3, region),),
                 DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""),
                 (1,), 2),
    )
    computations = (KernelComputation(1, OpCode.MATMUL, (1, 1), 2, attrs),)
    records = KernelMemoryRecords(
        tensors, computations, placements, shards, (), objects, views, states,
        (ControlToken(1), ControlToken(2)),
        _declaration_ops(objects, views) + effects,
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_pre_resident_weight_program", 1))
    variant = builder.variant(
        "main", "pre_resident_weight", "build_pre_resident_weight_program:pre_resident_weight",
        records=records,
        allocations=(
            SramAllocation(1, 1, 0, 0, size, arch.sram_base_alignment_bytes),
            SramAllocation(2, 2, 0, 0x2000, size, arch.sram_base_alignment_bytes),
        ),
        external_backings=(ObjectBacking(3, ExternalSlotBacking(1)),),
        binding_slots=(BindingSlot(1, "sink", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, Binding(1, hbm, INVALID_CORE_ID, 0x200000, size, arch.axi_data_bytes, Access.READ_WRITE)),),
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(7)
    stream.kernel_command(8)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_dma_shapes_program(arch):
    dummy_shape = (32,)
    lhs_shape = (2, 1)
    rhs_shape = (1, 16)
    output_shape = (2, 16)
    output_strides = (16, 1)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "prefetch", TensorRole.INPUT, DType.FP16, dummy_shape, (1,), StorageClass.EXTERNAL, Access.READ_ONLY, 64, 64, None),
        KernelTensor(2, 0, None, 2, 0, "lhs", TensorRole.WEIGHT, DType.FP32, lhs_shape, (1, 1), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 8, 8, "0" * 64),
        KernelTensor(3, 0, None, 3, 0, "rhs", TensorRole.WEIGHT, DType.FP32, rhs_shape, (16, 1), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 64, 64, "1" * 64),
        KernelTensor(4, 1, None, 4, 0, "result", TensorRole.OUTPUT, DType.FP32, output_shape, output_strides, StorageClass.CORE_SRAM, Access.READ_WRITE, 128, 128, None),
        KernelTensor(5, 1, SynthesizedTensorPurpose.PARTIAL_SUM, 5, 0, "partial", TensorRole.ACTIVATION, DType.FP32, output_shape, output_strides, StorageClass.CORE_SRAM, Access.READ_WRITE, 128, 128, None),
        KernelTensor(6, 0, None, 5, 0, "output", TensorRole.OUTPUT, DType.FP32, output_shape, output_strides, StorageClass.EXTERNAL, Access.READ_WRITE, 128, 128, None),
    )
    placements = (Placement(1, (0,)), Placement(2, (0, 1)), Placement(3, (0, 1)), Placement(4, (0, 1)), Placement(5, (1,)))
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,), dummy_shape, dummy_shape, 0),
        TensorShard(2, 2, 2, 0, DistributionKind.REPLICATED, (0, 0), lhs_shape, lhs_shape, 0),
        TensorShard(3, 2, 2, 1, DistributionKind.REPLICATED, (0, 0), lhs_shape, lhs_shape, 0),
        TensorShard(4, 3, 3, 0, DistributionKind.REPLICATED, (0, 0), rhs_shape, rhs_shape, 0),
        TensorShard(5, 3, 3, 1, DistributionKind.REPLICATED, (0, 0), rhs_shape, rhs_shape, 0),
        TensorShard(6, 4, 5, 1, DistributionKind.PARTITIONED, (0, 0), output_shape, output_shape, 0),
        TensorShard(7, 5, 4, 0, DistributionKind.PARTIAL_SUM, (0, 0), output_shape, output_shape, 1),
        TensorShard(8, 5, 4, 1, DistributionKind.PARTIAL_SUM, (0, 0), output_shape, output_shape, 1),
        TensorShard(9, 6, 5, 1, DistributionKind.PARTITIONED, (0, 0), output_shape, output_shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, dummy_shape, (1,), 64, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, dummy_shape, (1,), 64, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, lhs_shape, (1, 1), 8, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, 1, MemorySpace.CORE_SRAM, lhs_shape, (1, 1), 8, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 3, 0, MemorySpace.CORE_SRAM, rhs_shape, (16, 1), 64, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 3, 1, MemorySpace.CORE_SRAM, rhs_shape, (16, 1), 64, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(7, 5, 0, MemorySpace.CORE_SRAM, output_shape, output_strides, 128, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(8, 5, 1, MemorySpace.CORE_SRAM, output_shape, output_strides, 128, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(9, 5, 1, MemorySpace.CORE_SRAM, output_shape, (32, 1), 192, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(10, 5, 1, MemorySpace.CORE_SRAM, output_shape, output_strides, 128, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(11, 5, INVALID_CORE_ID, MemorySpace.HBM, output_shape, (64, 1), 320, arch.axi_data_bytes, False, 0),
    )
    view_shards = (1, 1, 2, 3, 4, 5, 7, 8, 8, 8, 9)
    views = tuple(
        BufferView(index, index, view_shards[index - 1], (0,) * len(item.shape), item.shape, item.shape, 0, item.strides, Layout.CONTIGUOUS_ROW_MAJOR if item.strides in ((1,), (1, 1), (16, 1)) else None, None, 0)
        for index, item in enumerate(objects, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.PRE_RESIDENT), TensorState(5, 4, 0, StateOrigin.PRE_RESIDENT), TensorState(6, 5, 0, StateOrigin.PRE_RESIDENT), TensorState(7, 6, 0, StateOrigin.PRE_RESIDENT),
        TensorState(8, 7, 0, StateOrigin.EMPTY), TensorState(9, 7, 1, StateOrigin.PRODUCED), TensorState(10, 7, 2, StateOrigin.PRODUCED, 1),
        TensorState(11, 8, 0, StateOrigin.EMPTY), TensorState(12, 8, 1, StateOrigin.PRODUCED), TensorState(13, 8, 2, StateOrigin.PRODUCED, 1), TensorState(14, 8, 3, StateOrigin.PRODUCED),
        TensorState(15, 9, 0, StateOrigin.EMPTY), TensorState(16, 9, 1, StateOrigin.PRODUCED, 1),
        TensorState(17, 10, 0, StateOrigin.EMPTY), TensorState(18, 10, 1, StateOrigin.PRODUCED, 1),
        TensorState(19, 11, 0, StateOrigin.EMPTY), TensorState(20, 11, 1, StateOrigin.PRODUCED),
    )
    dummy_region = ElementRegion((0,), dummy_shape, (1,))
    lhs_region = ElementRegion((0, 0), lhs_shape, (1, 1))
    rhs_region = ElementRegion((0, 0), rhs_shape, (1, 1))
    output_region = ElementRegion((0, 0), output_shape, (1, 1))
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tile = KernelTile(0, 0, 0, 0, 1, 2, 16, 1, 1, 2, 16, 1)
    cost = KernelCost(72, 128, 200, 32, 0, 0)
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(23, 0, "shapes:fill:accumulator", KernelOpcode.DMA, 1, 0, (), (StateTransition(11, 12, 8, output_region),), DmaAttrs(DmaKind.LOCAL_FILL, 1, 1, 1, 0, b"\x00"), (), 5),
        KernelOp(24, 0, "shapes:fill:source", KernelOpcode.DMA, 0, 0, (), (StateTransition(8, 9, 7, output_region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\xa5"), (), 2),
        KernelOp(25, 1, "shapes:gemm:accumulator", KernelOpcode.GEMM, 1, 8, (OperandAccess(5, 4, lhs_region, OperandAccessMode.READ), OperandAccess(7, 6, rhs_region, OperandAccessMode.READ)), (StateTransition(12, 13, 8, output_region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, cost, MatrixPhase.ACCUMULATE_ONLY, 1), (5,), 6),
        KernelOp(26, 1, "shapes:gemm:source", KernelOpcode.GEMM, 0, 7, (OperandAccess(4, 3, lhs_region, OperandAccessMode.READ), OperandAccess(6, 5, rhs_region, OperandAccessMode.READ)), (StateTransition(9, 10, 7, output_region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, cost, MatrixPhase.ACCUMULATE_ONLY, 1), (2,), 3),
        KernelOp(27, 0, "shapes:p2p", KernelOpcode.DMA, 0, 0, (OperandAccess(10, 7, output_region, OperandAccessMode.READ),), (StateTransition(15, 16, 9, output_region),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, 7, b""), (3,), 4),
        KernelOp(28, 0, "shapes:prefetch", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, dummy_region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, dummy_region),), DmaAttrs(DmaKind.PREFETCH, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(29, 0, "shapes:recv", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(7, 0, 1, 128), (4,), 7),
        KernelOp(30, 0, "shapes:copy", KernelOpcode.LOCAL_COPY, 1, 0, (OperandAccess(16, 9, output_region, OperandAccessMode.READ),), (StateTransition(17, 18, 10, output_region),), LocalCopyAttrs(), (7,), 8),
        KernelOp(31, 1, "shapes:reduce", KernelOpcode.LOCAL_REDUCE, 1, 8, (OperandAccess(18, 10, output_region, OperandAccessMode.READ), OperandAccess(13, 8, output_region, OperandAccessMode.READ)), (StateTransition(13, 14, 8, output_region),), LocalReduceAttrs(ReduceKind.SUM, 1, tile, KernelCost(256, 128, 384, 0, 0, 32)), (6, 8), 9),
        KernelOp(32, 0, "shapes:store", KernelOpcode.DMA, 1, 0, (OperandAccess(14, 8, output_region, OperandAccessMode.READ),), (StateTransition(19, 20, 11, output_region),), DmaAttrs(DmaKind.STORE, 1, 1, INVALID_CORE_ID, 0, b""), (9,), 10),
    )
    computations = (KernelComputation(1, OpCode.MATMUL, (2, 3), 4, attrs),)
    partials = (PartialSumDefinition(1, 1, 4, 5, 4),)
    records = KernelMemoryRecords(tensors, computations, placements, shards, partials, objects, views, states, tuple(ControlToken(index) for index in range(1, 11)), declarations + effects)
    allocations = tuple(SramAllocation(index, object_id, core, offset, size, arch.sram_base_alignment_bytes) for index, (object_id, core, offset, size) in enumerate(((2, 0, 0, 64), (3, 0, 0x1000, 64), (4, 1, 0x1000, 64), (5, 0, 0x1100, 64), (6, 1, 0x1100, 64), (7, 0, 0x2000, 128), (8, 1, 0x2000, 128), (9, 1, 0x2200, 192), (10, 1, 0x2400, 128)), 1))
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (Binding(1, hbm, INVALID_CORE_ID, 0x100000, 64, arch.axi_data_bytes, Access.READ_ONLY), Binding(2, hbm, INVALID_CORE_ID, 0x300000, 320, arch.axi_data_bytes, Access.READ_WRITE))
    slots = (BindingSlot(1, "prefetch", MemorySpace.HBM, hbm, INVALID_CORE_ID, 64, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]), BindingSlot(2, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, 320, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_dma_shapes_program", 1))
    variant = builder.variant("main", "dma_shapes", "build_dma_shapes_program:dma_shapes", records=records, allocations=allocations, external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(11, ExternalSlotBacking(2))), binding_slots=slots)
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    for op_id in (28, 24, 26, 27):
        stream0.kernel_command(op_id)
    for op_id in (23, 25, 29, 30, 31, 32):
        stream1.kernel_command(op_id)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_zero_dma_program(arch):
    shape = (1, 32)
    strides = (32, 1)
    size = 64
    tensor = KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None)
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
        TensorShard(2, 1, 1, 1, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 1, 1, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
    )
    views = tuple(
        BufferView(index, item.object_id, (1, 1, 2)[index - 1], (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, item in enumerate(objects, 1)
    ) + (BufferView(4, 1, 1, (0, 0), (1, 0), (1, 0), 32, strides, None, None, 0),)
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 2, 2, StateOrigin.PRODUCED),
        TensorState(5, 2, 3, StateOrigin.PRODUCED),
        TensorState(6, 2, 4, StateOrigin.PRODUCED),
        TensorState(7, 3, 0, StateOrigin.EMPTY),
        TensorState(8, 3, 1, StateOrigin.PRODUCED),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, next(obj.owner_core for obj in objects if obj.object_id == item.object_id), 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    zero_rows = ElementRegion((0, 0), (0, 32), (1, 1))
    zero_bytes = ElementRegion((0, 0), (1, 0), (1, 1))
    effects = (
        KernelOp(8, 0, "load:zero_rows", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, zero_rows, OperandAccessMode.READ),), (StateTransition(2, 3, 2, zero_rows),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(9, 0, "load:zero_bytes", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 4, zero_bytes, OperandAccessMode.READ),), (StateTransition(3, 4, 2, zero_bytes),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (1,), 2),
        KernelOp(10, 0, "fill:zero_rows", KernelOpcode.DMA, 0, 0, (), (StateTransition(4, 5, 2, zero_rows),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x5a"), (2,), 3),
        KernelOp(11, 0, "fill:zero_bytes", KernelOpcode.DMA, 0, 0, (), (StateTransition(5, 6, 2, zero_bytes),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x3c"), (3,), 4),
        KernelOp(12, 0, "p2p:zero_rows", KernelOpcode.DMA, 0, 0, (OperandAccess(6, 2, zero_rows, OperandAccessMode.READ),), (StateTransition(7, 8, 3, zero_rows),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, 1, b""), (4,), 5),
        KernelOp(13, 0, "recv:zero_rows", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(1, 0, 1, 0), (5,), 6),
    )
    records = KernelMemoryRecords((tensor,), (), (Placement(1, (0, 1)),), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 7)), declarations + effects)
    allocations = (
        SramAllocation(1, 2, 0, 0, size, arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 1, 0, size, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    binding = Binding(1, hbm, INVALID_CORE_ID, 0x100000, size, arch.axi_data_bytes, Access.READ_ONLY)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_zero_dma_program", 1))
    variant = builder.variant("main", "zero_dma", "build_zero_dma_program:zero_dma", records=records, allocations=allocations, external_backings=(ObjectBacking(1, ExternalSlotBacking(1)),), binding_slots=(BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, binding),))
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    for op_id in range(8, 13):
        stream0.kernel_command(op_id)
    stream1.kernel_command(13)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_fence_scopes_program(arch):
    shape = (8,)
    size = 16
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, (1,), StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(2, 0, None, 1, 0, "hbm-output", TensorRole.OUTPUT, DType.FP16, shape, (1,), StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
        KernelTensor(3, 0, None, 1, 0, "shared-output", TensorRole.OUTPUT, DType.FP16, shape, (1,), StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    placements = (Placement(1, (0, 1)), Placement(2, (0,)), Placement(3, (0,)))
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0,), shape, shape, 0),
        TensorShard(2, 1, 1, 1, DistributionKind.REPLICATED, (0,), shape, shape, 0),
        TensorShard(3, 2, 2, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0),
        TensorShard(4, 3, 3, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, (1,), size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, (1,), size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, (1,), size, arch.axi_data_bytes, False, 0),
        BufferObject(4, 1, 1, MemorySpace.CORE_SRAM, shape, (1,), size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 1, INVALID_CORE_ID, MemorySpace.HOST_SHARED, shape, (1,), size, arch.axi_data_bytes, False, 0),
    )
    view_shards = (1, 1, 3, 2, 4)
    views = tuple(BufferView(index, index, view_shards[index - 1], (0,), shape, shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0) for index in range(1, 6))
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY), TensorState(5, 3, 1, StateOrigin.PRODUCED),
        TensorState(6, 4, 0, StateOrigin.EMPTY), TensorState(7, 4, 1, StateOrigin.PRODUCED),
        TensorState(8, 5, 0, StateOrigin.EMPTY), TensorState(9, 5, 1, StateOrigin.PRODUCED),
    )
    region = ElementRegion((0,), shape, (1,))
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(11, 0, "scopes:load", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(12, 0, "scopes:hbm-store", KernelOpcode.DMA, 0, 0, (OperandAccess(3, 2, region, OperandAccessMode.READ),), (StateTransition(4, 5, 3, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (1,), 2),
        KernelOp(13, 0, "scopes:p2p", KernelOpcode.DMA, 0, 0, (OperandAccess(3, 2, region, OperandAccessMode.READ),), (StateTransition(6, 7, 4, region),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, 7, b""), (2,), 3),
        KernelOp(14, 0, "scopes:recv", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(7, 0, 1, size), (3,), 4),
        KernelOp(15, 0, "scopes:shared-store", KernelOpcode.DMA, 0, 0, (OperandAccess(3, 2, region, OperandAccessMode.READ),), (StateTransition(8, 9, 5, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (3,), 5),
    )
    records = KernelMemoryRecords(tensors, (), placements, shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 6)), declarations + effects)
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    host = next(index for index, item in enumerate(arch.regions) if item.kind == "HOST_SHARED")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, arch.axi_data_bytes, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x200000, arch.axi_data_bytes, arch.axi_data_bytes, Access.READ_WRITE),
        Binding(3, host, INVALID_CORE_ID, 0x10000, arch.axi_data_bytes, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = (
        BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, arch.axi_data_bytes, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]),
        BindingSlot(2, "hbm-output", MemorySpace.HBM, hbm, INVALID_CORE_ID, arch.axi_data_bytes, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]),
        BindingSlot(3, "shared-output", MemorySpace.HOST_SHARED, host, INVALID_CORE_ID, arch.axi_data_bytes, arch.axi_data_bytes, Access.READ_WRITE, bindings[2]),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_fence_scopes_program", 1))
    variant = builder.variant("main", "fence_scopes", "build_fence_scopes_program:fence_scopes", records=records, allocations=(SramAllocation(1, 2, 0, 0, arch.axi_data_bytes, arch.sram_base_alignment_bytes), SramAllocation(2, 4, 1, 0, arch.axi_data_bytes, arch.sram_base_alignment_bytes)), external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(3, ExternalSlotBacking(2)), ObjectBacking(5, ExternalSlotBacking(3))), binding_slots=slots)
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    stream0.kernel_command(11)
    stream0.control_command(AxiFenceAttrs(FenceScope.DMA_READ))
    stream0.kernel_command(12)
    stream0.control_command(AxiFenceAttrs(FenceScope.DMA_WRITE))
    stream0.kernel_command(13)
    stream0.control_command(AxiFenceAttrs(FenceScope.P2P))
    stream0.kernel_command(15)
    stream0.control_command(AxiFenceAttrs(FenceScope.HOST_SHARED_WRITE))
    stream0.control_command(AxiFenceAttrs(FenceScope.ALL_INSTANCE))
    stream1.kernel_command(14)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def _build_cross_fault_case(arch, name, first_offset):
    shape = (8, 8)
    strides = (8, 1)
    size = 128
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(2, 0, None, 2, 0, "weight", TensorRole.WEIGHT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(3, 1, None, 3, 0, "result", TensorRole.ACTIVATION, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
        KernelTensor(4, 0, None, 4, 0, "consumer", TensorRole.ACTIVATION, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, size, size, None),
    )
    shards = tuple(TensorShard(index, index, 1 if index < 4 else 2, 0 if index < 4 else 1, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0) for index in range(1, 5))
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, arch.axi_data_bytes, False, 0),
        BufferObject(4, 2, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 3, 0, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 4, 1, MemorySpace.CORE_SRAM, shape, strides, size, arch.sram_base_alignment_bytes, False, 0),
    )
    view_shards = (1, 1, 2, 2, 3, 4)
    views = tuple(BufferView(index, index, view_shards[index - 1], (0, 0), shape, shape, 0, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0) for index in range(1, 7))
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EXTERNAL), TensorState(5, 4, 0, StateOrigin.EMPTY), TensorState(6, 4, 1, StateOrigin.PRODUCED),
        TensorState(7, 5, 0, StateOrigin.EMPTY), TensorState(8, 5, 1, StateOrigin.PRODUCED),
        TensorState(9, 6, 0, StateOrigin.EMPTY), TensorState(10, 6, 1, StateOrigin.PRODUCED),
    )
    region = ElementRegion((0, 0), shape, (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, 8, 8, 8, 1, 8, 8, 8)
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(13, 0, "cross:load:input", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(14, 0, "cross:consumer", KernelOpcode.DMA, 1, 0, (), (StateTransition(9, 10, 6, region),), DmaAttrs(DmaKind.LOCAL_FILL, 1, 1, 1, 0, b"\x5a"), (1,), 4),
        KernelOp(15, 0, "cross:load:weight", KernelOpcode.DMA, 0, 0, (OperandAccess(4, 3, region, OperandAccessMode.READ),), (StateTransition(5, 6, 4, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 2),
        KernelOp(16, 1, "cross:compute", KernelOpcode.GEMM, 0, 3, (OperandAccess(3, 2, region, OperandAccessMode.READ), OperandAccess(6, 4, region, OperandAccessMode.READ)), (StateTransition(7, 8, 5, region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(size * 2, size, size * 3, 512, 0, 0), MatrixPhase.DIRECT, 0), (1, 2), 3),
    )
    records = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.MATMUL, (1, 2), 3, attrs),), (Placement(1, (0,)), Placement(2, (1,))), shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 5)), declarations + effects)
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (Binding(1, hbm, INVALID_CORE_ID, first_offset, size, arch.axi_data_bytes, Access.READ_ONLY), Binding(2, hbm, INVALID_CORE_ID, 0x100000, size, arch.axi_data_bytes, Access.READ_ONLY))
    slots = (BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]), BindingSlot(2, "weight", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, bindings[1]))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant("main", "cross_fault", f"{name}:cross_fault", records=records, allocations=(SramAllocation(1, 2, 0, 0, size, arch.sram_base_alignment_bytes), SramAllocation(2, 4, 0, 0x1000, size, arch.sram_base_alignment_bytes), SramAllocation(3, 5, 0, 0x2000, size, arch.sram_base_alignment_bytes), SramAllocation(4, 6, 1, 0, size, arch.sram_base_alignment_bytes)), external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(3, ExternalSlotBacking(2))), binding_slots=slots)
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    for op_id in (13, 15, 16):
        stream0.kernel_command(op_id)
    stream1.kernel_command(14)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_cross_error_program(arch):
    return _build_cross_fault_case(arch, "build_cross_error_program", 0x800000)


def build_repeat_error_program(arch):
    shape = (64,)
    size = 128
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, (1,), StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None),
        KernelTensor(2, 0, None, 1, 0, "output", TensorRole.OUTPUT, DType.FP16, shape, (1,), StorageClass.EXTERNAL, Access.READ_WRITE, size, size, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0,), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, (1,), size, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, (1,), size, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, (1,), size, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0,), shape, shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 1, (0,), shape, shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 3, 2, (0,), shape, shape, 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY), TensorState(5, 3, 1, StateOrigin.PRODUCED),
    )
    region = ElementRegion((0,), shape, (1,))
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(7, 0, "repeat-error:load", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(8, 0, "repeat-error:store", KernelOpcode.DMA, 0, 0, (OperandAccess(3, 2, region, OperandAccessMode.READ),), (StateTransition(4, 5, 3, region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (1,), 2),
    )
    records = KernelMemoryRecords(tensors, (), (Placement(1, (0,)),), shards, (), objects, views, states, (ControlToken(1), ControlToken(2)), declarations + effects)
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (Binding(1, hbm, INVALID_CORE_ID, 0x100000, size, arch.axi_data_bytes, Access.READ_ONLY), Binding(2, hbm, INVALID_CORE_ID, 0x200000, size, arch.axi_data_bytes, Access.READ_WRITE))
    slots = (BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]), BindingSlot(2, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, Access.READ_WRITE, bindings[1]))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_repeat_error_program", 1))
    variant = builder.variant("main", "repeat_error", "build_repeat_error_program:repeat_error", records=records, allocations=(SramAllocation(1, 2, 0, 0, size, arch.sram_base_alignment_bytes),), external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(3, ExternalSlotBacking(2))), binding_slots=slots)
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(7)
    stream.kernel_command(8)
    stream.control_command(RepeatCommandAttrs(1, 2, REPEAT_COUNT))
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_cross_fault_program(arch):
    return _build_cross_fault_case(arch, "build_cross_fault_program", 0x100080)


def build_read_window_program(arch):
    return _build_load_program(arch, "build_read_window_program", (_LoadSpec(0, (3072,), (1,), (1,), DType.FP16, 0x100000, max_burst_beats=8),))


def build_load_saturation_contiguous_program(arch):
    return _build_load_saturation_program(arch, "contiguous")


def build_load_saturation_multi_tensor_program(arch):
    return _build_load_saturation_program(arch, "multi_tensor")


def build_load_saturation_strided_program(arch):
    return _build_load_saturation_program(arch, "strided")


def build_p2p_reuse_program(arch):
    return _build_p2p_program(arch, "build_p2p_reuse_program", 2)


def build_dual_core_program(arch):
    accumulator_bytes = M * N * DType.FP32.byte_width
    input_shape = (M, K)
    weight_shape = (K, N)
    input_shard_shape = (M, TP_K)
    weight_shard_shape = (TP_K, N)
    output_shape = (M, N)
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, input_shape, (K, 1), StorageClass.EXTERNAL, Access.READ_ONLY, IN_BYTES, IN_BYTES, None),
        KernelTensor(2, 0, None, 2, 0, "weight", TensorRole.WEIGHT, DType.FP16, weight_shape, (N, 1), StorageClass.EXTERNAL, Access.READ_ONLY, W_BYTES, W_BYTES, None),
        KernelTensor(3, 1, None, 3, 0, "output", TensorRole.OUTPUT, DType.FP16, output_shape, (N, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, PARTIAL_BYTES, PARTIAL_BYTES, None),
        KernelTensor(4, 1, SynthesizedTensorPurpose.PARTIAL_SUM, 4, 0, "partial", TensorRole.ACTIVATION, DType.FP32, output_shape, (N, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, accumulator_bytes, accumulator_bytes, None),
    )
    placements = (Placement(1, (0, 1)), Placement(2, (0, 1)), Placement(3, (0, 1)), Placement(4, (1,)))
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0, 0), input_shard_shape, input_shard_shape, 0),
        TensorShard(2, 1, 1, 1, DistributionKind.PARTITIONED, (0, TP_K), input_shard_shape, input_shard_shape, 0),
        TensorShard(3, 2, 2, 0, DistributionKind.PARTITIONED, (0, 0), weight_shard_shape, weight_shard_shape, 0),
        TensorShard(4, 2, 2, 1, DistributionKind.PARTITIONED, (TP_K, 0), weight_shard_shape, weight_shard_shape, 0),
        TensorShard(5, 4, 3, 0, DistributionKind.PARTIAL_SUM, (0, 0), output_shape, output_shape, 1),
        TensorShard(6, 4, 3, 1, DistributionKind.PARTIAL_SUM, (0, 0), output_shape, output_shape, 1),
        TensorShard(7, 3, 4, 1, DistributionKind.PARTITIONED, (0, 0), output_shape, output_shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, input_shape, (K, 1), IN_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, input_shard_shape, (TP_K, 1), A_SHARD_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 1, 1, MemorySpace.CORE_SRAM, input_shard_shape, (TP_K, 1), A_SHARD_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, INVALID_CORE_ID, MemorySpace.HBM, weight_shape, (N, 1), W_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(5, 2, 0, MemorySpace.CORE_SRAM, weight_shard_shape, (N, 1), W_SHARD_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 2, 1, MemorySpace.CORE_SRAM, weight_shard_shape, (N, 1), W_SHARD_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(7, 4, 0, MemorySpace.CORE_SRAM, output_shape, (N, 1), accumulator_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(8, 4, 1, MemorySpace.CORE_SRAM, output_shape, (N, 1), accumulator_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(9, 4, 1, MemorySpace.CORE_SRAM, output_shape, (N, 1), accumulator_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(10, 3, 1, MemorySpace.CORE_SRAM, output_shape, (N, 1), PARTIAL_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(11, 3, INVALID_CORE_ID, MemorySpace.HBM, output_shape, (N, 1), PARTIAL_BYTES, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), input_shard_shape, input_shard_shape, 0, (K, 1), None, None, 0),
        BufferView(2, 2, 1, (0, 0), input_shard_shape, input_shard_shape, 0, (TP_K, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 1, 2, (0, 0), input_shard_shape, input_shard_shape, TP_K, (K, 1), None, None, 0),
        BufferView(4, 3, 2, (0, 0), input_shard_shape, input_shard_shape, 0, (TP_K, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(5, 4, 3, (0, 0), weight_shard_shape, weight_shard_shape, 0, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(6, 5, 3, (0, 0), weight_shard_shape, weight_shard_shape, 0, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(7, 4, 4, (0, 0), weight_shard_shape, weight_shard_shape, TP_K * N, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(8, 6, 4, (0, 0), weight_shard_shape, weight_shard_shape, 0, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(9, 7, 5, (0, 0), output_shape, output_shape, 0, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(10, 8, 6, (0, 0), output_shape, output_shape, 0, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(11, 9, 6, (0, 0), output_shape, output_shape, 0, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(12, 10, 7, (0, 0), output_shape, output_shape, 0, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(13, 11, 7, (0, 0), output_shape, output_shape, 0, (N, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY), TensorState(5, 3, 1, StateOrigin.PRODUCED),
        TensorState(6, 4, 0, StateOrigin.EXTERNAL),
        TensorState(7, 5, 0, StateOrigin.EMPTY), TensorState(8, 5, 1, StateOrigin.PRODUCED),
        TensorState(9, 6, 0, StateOrigin.EMPTY), TensorState(10, 6, 1, StateOrigin.PRODUCED),
        TensorState(11, 7, 0, StateOrigin.EMPTY), TensorState(12, 7, 1, StateOrigin.PRODUCED, 1),
        TensorState(13, 8, 0, StateOrigin.EMPTY), TensorState(14, 8, 1, StateOrigin.PRODUCED, 1), TensorState(15, 8, 2, StateOrigin.PRODUCED),
        TensorState(16, 9, 0, StateOrigin.EMPTY), TensorState(17, 9, 1, StateOrigin.PRODUCED, 1),
        TensorState(18, 10, 0, StateOrigin.EMPTY), TensorState(19, 10, 1, StateOrigin.PRODUCED),
        TensorState(20, 11, 0, StateOrigin.EMPTY), TensorState(21, 11, 1, StateOrigin.PRODUCED),
    )
    input0 = ElementRegion((0, 0), input_shard_shape, (1, 1))
    input1 = ElementRegion((0, 0), input_shard_shape, (1, 1))
    weight0 = ElementRegion((0, 0), weight_shard_shape, (1, 1))
    weight1 = ElementRegion((0, 0), weight_shard_shape, (1, 1))
    output = ElementRegion((0, 0), output_shape, (1, 1))
    tile0 = KernelTile(0, 0, 0, 0, 1, M, N, TP_K, 1, M, N, TP_K)
    tile1 = KernelTile(0, 0, 0, TP_K, 1, M, N, TP_K, 1, M, N, TP_K)
    matrix_cost = KernelCost(A_SHARD_BYTES + W_SHARD_BYTES, accumulator_bytes, A_SHARD_BYTES + W_SHARD_BYTES + accumulator_bytes, M * N * TP_K, 0, 0)
    declarations = _declaration_ops(objects, views)
    effects = (
        KernelOp(25, 0, "dual:00:load:input:0", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, input0, OperandAccessMode.READ),), (StateTransition(2, 3, 2, input0),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(26, 0, "dual:01:load:weight:0", KernelOpcode.DMA, 0, 0, (OperandAccess(6, 5, weight0, OperandAccessMode.READ),), (StateTransition(7, 8, 6, weight0),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 2),
        KernelOp(27, 1, "dual:02:gemm:0", KernelOpcode.GEMM, 0, 5, (OperandAccess(3, 2, input0, OperandAccessMode.READ), OperandAccess(8, 6, weight0, OperandAccessMode.READ)), (StateTransition(11, 12, 9, output),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile0, matrix_cost, MatrixPhase.ACCUMULATE_ONLY, 1), (1, 2), 3),
        KernelOp(28, 0, "dual:03:p2p", KernelOpcode.DMA, 0, 0, (OperandAccess(12, 9, output, OperandAccessMode.READ),), (StateTransition(16, 17, 11, output),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, 1, b""), (3,), 4),
        KernelOp(29, 0, "dual:04:load:input:1", KernelOpcode.DMA, 1, 0, (OperandAccess(1, 3, input1, OperandAccessMode.READ),), (StateTransition(4, 5, 4, input1),), DmaAttrs(DmaKind.LOAD, 1, INVALID_CORE_ID, 1, 0, b""), (), 5),
        KernelOp(30, 0, "dual:05:load:weight:1", KernelOpcode.DMA, 1, 0, (OperandAccess(6, 7, weight1, OperandAccessMode.READ),), (StateTransition(9, 10, 8, weight1),), DmaAttrs(DmaKind.LOAD, 1, INVALID_CORE_ID, 1, 0, b""), (), 6),
        KernelOp(31, 1, "dual:06:gemm:1", KernelOpcode.GEMM, 1, 6, (OperandAccess(5, 4, input1, OperandAccessMode.READ), OperandAccess(10, 8, weight1, OperandAccessMode.READ)), (StateTransition(13, 14, 10, output),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile1, matrix_cost, MatrixPhase.ACCUMULATE_ONLY, 1), (5, 6), 7),
        KernelOp(32, 0, "dual:07:recv", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(1, 0, 1, accumulator_bytes), (4,), 8),
        KernelOp(33, 1, "dual:08:reduce", KernelOpcode.LOCAL_REDUCE, 1, 6, (OperandAccess(17, 11, output, OperandAccessMode.READ), OperandAccess(14, 10, output, OperandAccessMode.READ)), (StateTransition(14, 15, 10, output),), LocalReduceAttrs(ReduceKind.SUM, 1, tile1, KernelCost(accumulator_bytes * 2, accumulator_bytes, accumulator_bytes * 3, 0, 0, M * N)), (8, 7), 9),
        KernelOp(34, 1, "dual:09:epilogue", KernelOpcode.MATRIX_EPILOGUE, 1, 7, (OperandAccess(15, 10, output, OperandAccessMode.READ),), (StateTransition(18, 19, 12, output),), MatrixEpilogueKernelAttrs(OpCode.MATMUL, attrs, tile1, KernelCost(accumulator_bytes, PARTIAL_BYTES, accumulator_bytes + PARTIAL_BYTES, 0, M * N, 0), MatrixEpilogueAlgorithm.VECTOR_ACCUMULATION), (9,), 10),
        KernelOp(35, 0, "dual:10:store", KernelOpcode.DMA, 1, 0, (OperandAccess(19, 12, output, OperandAccessMode.READ),), (StateTransition(20, 21, 13, output),), DmaAttrs(DmaKind.STORE, 1, 1, INVALID_CORE_ID, 0, b""), (10,), 11),
    )
    records = KernelMemoryRecords(
        tensors,
        (KernelComputation(1, OpCode.MATMUL, (1, 2), 3, attrs),),
        placements,
        shards,
        (PartialSumDefinition(1, 1, 3, 4, 3),),
        objects,
        views,
        states,
        tuple(ControlToken(index) for index in range(1, 12)),
        declarations + effects,
    )
    allocations = tuple(
        SramAllocation(index, object_id, core, offset, size, arch.sram_base_alignment_bytes)
        for index, (object_id, core, offset, size) in enumerate((
            (2, 0, 0x0000, A_SHARD_BYTES), (5, 0, 0x2000, W_SHARD_BYTES), (7, 0, 0x4000, accumulator_bytes),
            (3, 1, 0x0000, A_SHARD_BYTES), (6, 1, 0x2000, W_SHARD_BYTES), (9, 1, 0x4000, accumulator_bytes), (8, 1, 0x8000, accumulator_bytes), (10, 1, 0xC000, PARTIAL_BYTES),
        ), 1)
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, IN_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x200000, W_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(3, hbm, INVALID_CORE_ID, 0x300000, PARTIAL_BYTES, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = (
        BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, IN_BYTES, arch.axi_data_bytes, Access.READ_ONLY, bindings[0]),
        BindingSlot(2, "weight", MemorySpace.HBM, hbm, INVALID_CORE_ID, W_BYTES, arch.axi_data_bytes, Access.READ_ONLY, bindings[1]),
        BindingSlot(3, "output", MemorySpace.HBM, hbm, INVALID_CORE_ID, PARTIAL_BYTES, arch.axi_data_bytes, Access.READ_WRITE, bindings[2]),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", "build_dual_core_program", 1))
    variant = builder.variant("main", "b1_m64n64k64_tp2", "build_dual_core_program:b1_m64n64k64_tp2", records=records, allocations=allocations, external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(4, ExternalSlotBacking(2)), ObjectBacking(11, ExternalSlotBacking(3))), binding_slots=slots)
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    for op_id in (25, 26, 27, 28):
        stream0.kernel_command(op_id)
    for op_id in (29, 30, 31, 32, 33, 34, 35):
        stream1.kernel_command(op_id)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_barrier_e2e_program(arch):
    return _build_barrier_program(arch, "build_barrier_e2e_program")

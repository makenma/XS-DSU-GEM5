"""Compiler-accepted runtime pressure fixtures.

These programs exist to make runtime capacity behaviour observable with
dependency-independent work: the engine-slot pressure fixtures place two
same-engine operations whose inputs are produced by shared loads, so both
become ready at the same time.
"""

from __future__ import annotations

from mesh_ir.analysis.sram import SramAllocation
from mesh_ir.builder import ProgramBuilder
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import IN_BYTES, K, M, N, OUT_BYTES, W_BYTES, _declaration_ops, _extent_bytes
from mesh_ir.ir.common import Access, DType, DmaKind, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, MatmulAttrs, OpCode
from mesh_ir.ir.kernel_ir import AllocAttrs, BarrierAttrs, BufferObject, BufferView, ControlToken, DistributionKind, DmaAttrs, ElementRegion, GemmKernelAttrs, KernelComputation, LocalCopyAttrs, LocalReduceAttrs, PartialSumDefinition, RecvWaitAttrs, ReduceKind, SynthesizedTensorPurpose, VectorAlgorithm, VectorKernelAttrs, KernelCost, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, KernelTile, MatrixPhase, OperandAccess, OperandAccessMode, Placement, StateOrigin, StateTransition, TensorShard, TensorState, ViewDeclarationAttrs
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ExternalSlotBacking, HaltAttrs, ObjectBacking, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.traffic import Binding, BindingSlot

HBM_REGION = 0
SRAM_REGION = 2


def build_engine_tensor_pressure_program(arch):
    name = "build_engine_tensor_pressure_program"
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, (M, K), (K, 1), StorageClass.EXTERNAL, Access.READ_ONLY, IN_BYTES, IN_BYTES, None),
        KernelTensor(2, 0, None, 2, 0, "weight", TensorRole.WEIGHT, DType.FP16, (K, N), (N, 1), StorageClass.EXTERNAL, Access.READ_ONLY, W_BYTES, W_BYTES, None),
        KernelTensor(3, 1, None, 3, 0, "result:a", TensorRole.OUTPUT, DType.FP16, (M, N), (N, 1), StorageClass.EXTERNAL, Access.READ_WRITE, OUT_BYTES, OUT_BYTES, None),
        KernelTensor(4, 2, None, 4, 0, "result:b", TensorRole.OUTPUT, DType.FP16, (M, N), (N, 1), StorageClass.EXTERNAL, Access.READ_WRITE, OUT_BYTES, OUT_BYTES, None),
    )
    placements = (Placement(1, (0,)),)
    shards = tuple(
        TensorShard(index, index, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0)
        for index, shape in enumerate(((M, K), (K, N), (M, N), (M, N)), 1)
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (M, K), (K, 1), IN_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, (M, K), (K, 1), IN_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, INVALID_CORE_ID, MemorySpace.HBM, (K, N), (N, 1), W_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(4, 2, 0, MemorySpace.CORE_SRAM, (K, N), (N, 1), W_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 3, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 3, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(7, 4, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(8, 4, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
    )
    owner_of_shard = {1: 1, 2: 1, 3: 3, 4: 4}
    views = tuple(
        BufferView(index, item.object_id, (1, 1, 2, 2, 3, 3, 4, 4)[index - 1], (0, 0), item.shape, item.shape, 0, item.strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, item in enumerate(objects, 1)
    )
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
        TensorState(11, 7, 0, StateOrigin.EMPTY),
        TensorState(12, 7, 1, StateOrigin.PRODUCED),
        TensorState(13, 8, 0, StateOrigin.EMPTY),
        TensorState(14, 8, 1, StateOrigin.PRODUCED),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, objects[index - 1].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    input_region = ElementRegion((0, 0), (M, K), (1, 1))
    weight_region = ElementRegion((0, 0), (K, N), (1, 1))
    result_region = ElementRegion((0, 0), (M, N), (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, M, N, K, 1, M, N, K)
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    gemm_operands = (
        OperandAccess(3, 2, input_region, OperandAccessMode.READ),
        OperandAccess(6, 4, weight_region, OperandAccessMode.READ),
    )
    gemm_cost = KernelCost(IN_BYTES + W_BYTES, OUT_BYTES, IN_BYTES + W_BYTES + OUT_BYTES, M * N * K, 0, 0)
    effects = (
        KernelOp(17, 0, "pressure:load:input", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, input_region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, input_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(18, 0, "pressure:load:weight", KernelOpcode.DMA, 0, 0, (OperandAccess(4, 3, weight_region, OperandAccessMode.READ),), (StateTransition(5, 6, 4, weight_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 2),
        KernelOp(19, 1, "pressure:gemm:a", KernelOpcode.GEMM, 0, 3, gemm_operands, (StateTransition(7, 8, 5, result_region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, gemm_cost, MatrixPhase.DIRECT, 0), (1, 2), 3),
        KernelOp(20, 2, "pressure:gemm:b", KernelOpcode.GEMM, 0, 4, gemm_operands, (StateTransition(11, 12, 7, result_region),), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, gemm_cost, MatrixPhase.DIRECT, 0), (1, 2), 4),
        KernelOp(21, 0, "pressure:store:a", KernelOpcode.DMA, 0, 0, (OperandAccess(8, 5, result_region, OperandAccessMode.READ),), (StateTransition(9, 10, 6, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (3,), 5),
        KernelOp(22, 0, "pressure:store:b", KernelOpcode.DMA, 0, 0, (OperandAccess(12, 7, result_region, OperandAccessMode.READ),), (StateTransition(13, 14, 8, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (4,), 6),
    )
    computations = (
        KernelComputation(1, OpCode.MATMUL, (1, 2), 3, attrs),
        KernelComputation(2, OpCode.MATMUL, (1, 2), 4, attrs),
    )
    records = KernelMemoryRecords(tensors, computations, placements, shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 7)), declarations + effects)
    allocations = (
        SramAllocation(1, 2, 0, 0x0000, IN_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(2, 4, 0, 0x2000, W_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(3, 5, 0, 0x4000, OUT_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(4, 7, 0, 0x6000, OUT_BYTES, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, IN_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x200000, W_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(3, hbm, INVALID_CORE_ID, 0x300000, OUT_BYTES, arch.axi_data_bytes, Access.READ_WRITE),
        Binding(4, hbm, INVALID_CORE_ID, 0x400000, OUT_BYTES, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = tuple(
        BindingSlot(index, symbol, MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, access, binding)
        for index, (symbol, size, access, binding) in enumerate(
            zip(("input", "weight", "result:a", "result:b"), (IN_BYTES, W_BYTES, OUT_BYTES, OUT_BYTES), (Access.READ_ONLY, Access.READ_ONLY, Access.READ_WRITE, Access.READ_WRITE), bindings), 1
        )
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant(
        "main",
        "engine_tensor_pressure",
        f"{name}:engine_tensor_pressure",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(3, ExternalSlotBacking(2)), ObjectBacking(6, ExternalSlotBacking(3)), ObjectBacking(8, ExternalSlotBacking(4))),
        binding_slots=slots,
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op_id in tuple(item.op_id for item in effects):
        stream.kernel_command(op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_engine_vector_pressure_program(arch):
    name = "build_engine_vector_pressure_program"
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, (M, N), (N, 1), StorageClass.EXTERNAL, Access.READ_ONLY, IN_BYTES, IN_BYTES, None),
        KernelTensor(2, 1, None, 2, 0, "result:a", TensorRole.OUTPUT, DType.FP16, (M, N), (N, 1), StorageClass.EXTERNAL, Access.READ_WRITE, OUT_BYTES, OUT_BYTES, None),
        KernelTensor(3, 2, None, 3, 0, "result:b", TensorRole.OUTPUT, DType.FP16, (M, N), (N, 1), StorageClass.EXTERNAL, Access.READ_WRITE, OUT_BYTES, OUT_BYTES, None),
        KernelTensor(4, 3, None, 4, 0, "result:c", TensorRole.OUTPUT, DType.FP16, (M, N), (N, 1), StorageClass.EXTERNAL, Access.READ_WRITE, OUT_BYTES, OUT_BYTES, None),
    )
    placements = (Placement(1, (0,)),)
    shards = tuple(
        TensorShard(index, index, 1, 0, DistributionKind.PARTITIONED, (0, 0), shape, shape, 0)
        for index, shape in enumerate(((M, N), (M, N), (M, N), (M, N)), 1)
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), IN_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), IN_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(5, 3, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 3, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(7, 4, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(8, 4, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
    )
    views = tuple(
        BufferView(index, item.object_id, (1, 1, 2, 2, 3, 3, 4, 4)[index - 1], (0, 0), item.shape, item.shape, 0, item.strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, item in enumerate(objects, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY),
        TensorState(5, 3, 1, StateOrigin.PRODUCED),
        TensorState(6, 4, 0, StateOrigin.EMPTY),
        TensorState(7, 4, 1, StateOrigin.PRODUCED),
        TensorState(8, 5, 0, StateOrigin.EMPTY),
        TensorState(9, 5, 1, StateOrigin.PRODUCED),
        TensorState(10, 6, 0, StateOrigin.EMPTY),
        TensorState(11, 6, 1, StateOrigin.PRODUCED),
        TensorState(12, 7, 0, StateOrigin.EMPTY),
        TensorState(13, 7, 1, StateOrigin.PRODUCED),
        TensorState(14, 8, 0, StateOrigin.EMPTY),
        TensorState(15, 8, 1, StateOrigin.PRODUCED),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, objects[index - 1].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    source_region = ElementRegion((0, 0), (M, N), (1, 1))
    result_region = ElementRegion((0, 0), (M, N), (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, M, N, 1, 1, M, N, 1)
    elementwise = ElementwiseAttrs()
    elementwise_cost = KernelCost(IN_BYTES, OUT_BYTES, IN_BYTES + OUT_BYTES, 0, M * N, 0)
    elementwise_attrs = VectorKernelAttrs(OpCode.RELU, elementwise, tile, elementwise_cost, VectorAlgorithm.ELEMENTWISE)
    effects = (
        KernelOp(17, 0, "pressure:load:input", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, source_region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, source_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
        KernelOp(18, 1, "pressure:relu:a", KernelOpcode.VECTOR, 0, 2, (OperandAccess(3, 2, source_region, OperandAccessMode.READ),), (StateTransition(4, 5, 3, result_region),), elementwise_attrs, (1,), 2),
        KernelOp(19, 2, "pressure:relu:b", KernelOpcode.VECTOR, 0, 3, (OperandAccess(3, 2, source_region, OperandAccessMode.READ),), (StateTransition(8, 9, 5, result_region),), elementwise_attrs, (1,), 3),
        KernelOp(20, 3, "pressure:relu:c", KernelOpcode.VECTOR, 0, 4, (OperandAccess(3, 2, source_region, OperandAccessMode.READ),), (StateTransition(12, 13, 7, result_region),), elementwise_attrs, (1,), 4),
        KernelOp(21, 0, "pressure:store:a", KernelOpcode.DMA, 0, 0, (OperandAccess(5, 3, result_region, OperandAccessMode.READ),), (StateTransition(6, 7, 4, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (2,), 5),
        KernelOp(22, 0, "pressure:store:b", KernelOpcode.DMA, 0, 0, (OperandAccess(9, 5, result_region, OperandAccessMode.READ),), (StateTransition(10, 11, 6, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (3,), 6),
        KernelOp(23, 0, "pressure:store:c", KernelOpcode.DMA, 0, 0, (OperandAccess(13, 7, result_region, OperandAccessMode.READ),), (StateTransition(14, 15, 8, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (4,), 7),
    )
    computations = (
        KernelComputation(1, OpCode.RELU, (1,), 2, elementwise),
        KernelComputation(2, OpCode.RELU, (1,), 3, elementwise),
        KernelComputation(3, OpCode.RELU, (1,), 4, elementwise),
    )
    records = KernelMemoryRecords(tensors, computations, placements, shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 8)), declarations + effects)
    allocations = (
        SramAllocation(1, 2, 0, 0x0000, IN_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 0, 0x2000, OUT_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(3, 5, 0, 0x4000, OUT_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(4, 7, 0, 0x6000, OUT_BYTES, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, IN_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x200000, OUT_BYTES, arch.axi_data_bytes, Access.READ_WRITE),
        Binding(3, hbm, INVALID_CORE_ID, 0x300000, OUT_BYTES, arch.axi_data_bytes, Access.READ_WRITE),
        Binding(4, hbm, INVALID_CORE_ID, 0x400000, OUT_BYTES, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = tuple(
        BindingSlot(index, symbol, MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, access, binding)
        for index, (symbol, size, access, binding) in enumerate(
            zip(("input", "result:a", "result:b", "result:c"), (IN_BYTES, OUT_BYTES, OUT_BYTES, OUT_BYTES), (Access.READ_ONLY, Access.READ_WRITE, Access.READ_WRITE, Access.READ_WRITE), bindings), 1
        )
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant(
        "main",
        "engine_vector_pressure",
        f"{name}:engine_vector_pressure",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(4, ExternalSlotBacking(2)), ObjectBacking(6, ExternalSlotBacking(3)), ObjectBacking(8, ExternalSlotBacking(4))),
        binding_slots=slots,
    )
    lifecycle = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    worker = variant.stream(0, 1)
    lifecycle.control_command(RequestBeginAttrs())
    lifecycle.kernel_command(17)
    lifecycle.kernel_command(18)
    lifecycle.kernel_command(19)
    worker.kernel_command(20)
    lifecycle.kernel_command(21)
    lifecycle.kernel_command(22)
    worker.kernel_command(23)
    lifecycle.control_command(RequestEndAttrs())
    lifecycle.control_command(HaltAttrs())
    return builder.build()


def build_engine_reduce_pressure_program(arch):
    name = "build_engine_reduce_pressure_program"
    accumulator_bytes = M * N * 4
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, (M, K), (K, 1), StorageClass.EXTERNAL, Access.READ_ONLY, IN_BYTES, IN_BYTES, None),
        KernelTensor(2, 0, None, 2, 0, "weight", TensorRole.WEIGHT, DType.FP16, (K, N), (N, 1), StorageClass.EXTERNAL, Access.READ_ONLY, W_BYTES, W_BYTES, None),
        KernelTensor(3, 1, None, 3, 0, "result", TensorRole.OUTPUT, DType.FP16, (M, N), (N, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, OUT_BYTES, OUT_BYTES, None),
        KernelTensor(4, 1, SynthesizedTensorPurpose.PARTIAL_SUM, 4, 0, "partial", TensorRole.ACTIVATION, DType.FP32, (M, N), (N, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, accumulator_bytes, accumulator_bytes, None),
        KernelTensor(5, 0, None, 4, 0, "output", TensorRole.OUTPUT, DType.FP32, (M, N), (N, 1), StorageClass.EXTERNAL, Access.READ_WRITE, accumulator_bytes, accumulator_bytes, None),
    )
    placements = (Placement(1, (0,)), Placement(2, (1,)), Placement(3, (0, 1)), Placement(4, (0, 1)))
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0, 0), (M, K), (M, K), 0),
        TensorShard(2, 2, 1, 0, DistributionKind.REPLICATED, (0, 0), (K, N), (K, N), 0),
        TensorShard(3, 3, 3, 0, DistributionKind.REPLICATED, (0, 0), (M, N), (M, N), 0),
        TensorShard(4, 3, 3, 1, DistributionKind.REPLICATED, (0, 0), (M, N), (M, N), 0),
        TensorShard(5, 4, 4, 0, DistributionKind.PARTIAL_SUM, (0, 0), (M, N), (M, N), 1),
        TensorShard(6, 4, 4, 1, DistributionKind.PARTIAL_SUM, (0, 0), (M, N), (M, N), 1),
        TensorShard(7, 1, 2, 1, DistributionKind.REPLICATED, (0, 0), (M, K), (M, K), 0),
        TensorShard(8, 2, 2, 1, DistributionKind.REPLICATED, (0, 0), (K, N), (K, N), 0),
        TensorShard(9, 5, 1, 0, DistributionKind.REPLICATED, (0, 0), (M, N), (M, N), 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, (M, K), (K, 1), IN_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 2, 0, MemorySpace.CORE_SRAM, (K, N), (N, 1), W_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 4, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), accumulator_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 4, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), accumulator_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 4, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), accumulator_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 4, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), accumulator_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(7, 1, 1, MemorySpace.CORE_SRAM, (M, K), (K, 1), IN_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(8, 2, 1, MemorySpace.CORE_SRAM, (K, N), (N, 1), W_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(9, 4, 1, MemorySpace.CORE_SRAM, (M, N), (N, 1), accumulator_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(10, 1, INVALID_CORE_ID, MemorySpace.HBM, (M, K), (K, 1), IN_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(11, 2, INVALID_CORE_ID, MemorySpace.HBM, (K, N), (N, 1), W_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(12, 4, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), accumulator_bytes, arch.axi_data_bytes, False, 0),
    )
    views = tuple(
        BufferView(index, item.object_id, (1, 2, 5, 5, 5, 5, 7, 8, 6, 1, 2, 9)[index - 1], (0, 0), item.shape, item.shape, 0, item.strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, item in enumerate(objects, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EMPTY),
        TensorState(2, 1, 1, StateOrigin.PRODUCED),
        TensorState(3, 2, 0, StateOrigin.EMPTY),
        TensorState(4, 2, 1, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY),
        TensorState(6, 3, 1, StateOrigin.PRODUCED),
        TensorState(7, 3, 2, StateOrigin.PRODUCED, 1),
        TensorState(8, 3, 3, StateOrigin.PRODUCED),
        TensorState(9, 4, 0, StateOrigin.EMPTY),
        TensorState(10, 4, 1, StateOrigin.PRODUCED),
        TensorState(11, 4, 2, StateOrigin.PRODUCED, 1),
        TensorState(12, 4, 3, StateOrigin.PRODUCED),
        TensorState(13, 5, 0, StateOrigin.EMPTY),
        TensorState(14, 5, 1, StateOrigin.PRODUCED, 1),
        TensorState(15, 6, 0, StateOrigin.EMPTY),
        TensorState(16, 6, 1, StateOrigin.PRODUCED, 1),
        TensorState(17, 7, 0, StateOrigin.EMPTY),
        TensorState(18, 7, 1, StateOrigin.PRODUCED),
        TensorState(19, 8, 0, StateOrigin.EMPTY),
        TensorState(20, 8, 1, StateOrigin.PRODUCED),
        TensorState(21, 9, 0, StateOrigin.EMPTY),
        TensorState(22, 9, 1, StateOrigin.PRODUCED),
        TensorState(23, 9, 2, StateOrigin.PRODUCED, 1),
        TensorState(24, 10, 0, StateOrigin.EXTERNAL),
        TensorState(25, 11, 0, StateOrigin.EXTERNAL),
        TensorState(26, 12, 0, StateOrigin.EMPTY),
        TensorState(27, 12, 1, StateOrigin.PRODUCED),
        TensorState(28, 12, 2, StateOrigin.PRODUCED),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, objects[index - 1].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    input_region = ElementRegion((0, 0), (M, K), (1, 1))
    weight_region = ElementRegion((0, 0), (K, N), (1, 1))
    result_region = ElementRegion((0, 0), (M, N), (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, M, N, K, 1, M, N, K)
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    accumulate = GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(IN_BYTES + W_BYTES, accumulator_bytes, IN_BYTES + W_BYTES + accumulator_bytes, M * N * K, 0, 0), MatrixPhase.ACCUMULATE_ONLY, 1)
    reduce_attrs = LocalReduceAttrs(ReduceKind.SUM, 1, tile, KernelCost(accumulator_bytes * 2, accumulator_bytes, accumulator_bytes * 3, 0, 0, M * N))
    core0_operands = (OperandAccess(2, 1, input_region, OperandAccessMode.READ), OperandAccess(4, 2, weight_region, OperandAccessMode.READ))
    core1_operands = (OperandAccess(18, 7, input_region, OperandAccessMode.READ), OperandAccess(20, 8, weight_region, OperandAccessMode.READ))
    effects = (
        KernelOp(25, 0, "pressure:fill:accumulator:a", KernelOpcode.DMA, 0, 0, (), (StateTransition(5, 6, 3, result_region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x00"), (), 1),
        KernelOp(26, 0, "pressure:fill:accumulator:b", KernelOpcode.DMA, 0, 0, (), (StateTransition(9, 10, 4, result_region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x00"), (), 2),
        KernelOp(27, 0, "pressure:fill:source", KernelOpcode.DMA, 1, 0, (), (StateTransition(21, 22, 9, result_region),), DmaAttrs(DmaKind.LOCAL_FILL, 1, 1, 1, 0, b"\x00"), (), 3),
        KernelOp(28, 0, "pressure:load:input:core0", KernelOpcode.DMA, 0, 0, (OperandAccess(24, 10, input_region, OperandAccessMode.READ),), (StateTransition(1, 2, 1, input_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 4),
        KernelOp(29, 0, "pressure:load:input:core1", KernelOpcode.DMA, 1, 0, (OperandAccess(24, 10, input_region, OperandAccessMode.READ),), (StateTransition(17, 18, 7, input_region),), DmaAttrs(DmaKind.LOAD, 1, INVALID_CORE_ID, 1, 0, b""), (), 5),
        KernelOp(30, 0, "pressure:load:weight:core0", KernelOpcode.DMA, 0, 0, (OperandAccess(25, 11, weight_region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, weight_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 6),
        KernelOp(31, 1, "pressure:gemm:accumulator:a", KernelOpcode.GEMM, 0, 5, core0_operands, (StateTransition(6, 7, 3, result_region),), accumulate, (1, 4, 6), 7),
        KernelOp(32, 1, "pressure:gemm:accumulator:b", KernelOpcode.GEMM, 0, 5, core0_operands, (StateTransition(10, 11, 4, result_region),), accumulate, (2, 4, 6), 8),
        KernelOp(33, 0, "pressure:load:weight:core1", KernelOpcode.DMA, 1, 0, (OperandAccess(25, 11, weight_region, OperandAccessMode.READ),), (StateTransition(19, 20, 8, weight_region),), DmaAttrs(DmaKind.LOAD, 1, INVALID_CORE_ID, 1, 0, b""), (), 9),
        KernelOp(34, 1, "pressure:gemm:source", KernelOpcode.GEMM, 1, 6, core1_operands, (StateTransition(22, 23, 9, result_region),), accumulate, (3, 5, 9), 10),
        KernelOp(35, 0, "pressure:p2p:partial", KernelOpcode.DMA, 1, 0, (OperandAccess(23, 9, result_region, OperandAccessMode.READ),), (StateTransition(13, 14, 5, result_region),), DmaAttrs(DmaKind.P2P_PUSH, 1, 1, 0, 1, b""), (10,), 11),
        KernelOp(36, 0, "pressure:recv:partial", KernelOpcode.RECV_WAIT, 0, 0, (), (), RecvWaitAttrs(1, 1, 0, accumulator_bytes), (11,), 12),
        KernelOp(37, 0, "pressure:copy:partial", KernelOpcode.LOCAL_COPY, 0, 0, (OperandAccess(14, 5, result_region, OperandAccessMode.READ),), (StateTransition(15, 16, 6, result_region),), LocalCopyAttrs(), (12,), 13),
        KernelOp(38, 1, "pressure:reduce:a", KernelOpcode.LOCAL_REDUCE, 0, 5, (OperandAccess(16, 6, result_region, OperandAccessMode.READ), OperandAccess(7, 3, result_region, OperandAccessMode.READ)), (StateTransition(7, 8, 3, result_region),), reduce_attrs, (1, 2, 7, 8, 13), 14),
        KernelOp(39, 1, "pressure:reduce:b", KernelOpcode.LOCAL_REDUCE, 0, 5, (OperandAccess(16, 6, result_region, OperandAccessMode.READ), OperandAccess(11, 4, result_region, OperandAccessMode.READ)), (StateTransition(11, 12, 4, result_region),), reduce_attrs, (1, 2, 7, 8, 13), 15),
        KernelOp(40, 0, "pressure:store:a", KernelOpcode.DMA, 0, 0, (OperandAccess(8, 3, result_region, OperandAccessMode.READ),), (StateTransition(26, 27, 12, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (14,), 16),
        KernelOp(41, 0, "pressure:store:b", KernelOpcode.DMA, 0, 0, (OperandAccess(12, 4, result_region, OperandAccessMode.READ),), (StateTransition(27, 28, 12, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b""), (15,), 17),
    )
    computations = (KernelComputation(1, OpCode.MATMUL, (1, 2), 3, attrs),)
    partials = (PartialSumDefinition(1, 1, 3, 4, 4),)
    records = KernelMemoryRecords(tensors, computations, placements, shards, partials, objects, views, states, tuple(ControlToken(index) for index in range(1, 18)), declarations + effects)
    allocations = (
        SramAllocation(1, 1, 0, 0x0000, IN_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(2, 2, 0, 0x2000, W_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(3, 3, 0, 0x4000, accumulator_bytes, arch.sram_base_alignment_bytes),
        SramAllocation(4, 4, 0, 0x8000, accumulator_bytes, arch.sram_base_alignment_bytes),
        SramAllocation(5, 5, 0, 0xC000, accumulator_bytes, arch.sram_base_alignment_bytes),
        SramAllocation(6, 6, 0, 0x10000, accumulator_bytes, arch.sram_base_alignment_bytes),
        SramAllocation(7, 7, 1, 0x0000, IN_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(8, 8, 1, 0x2000, W_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(9, 9, 1, 0x4000, accumulator_bytes, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    bindings = (
        Binding(1, hbm, INVALID_CORE_ID, 0x100000, IN_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(2, hbm, INVALID_CORE_ID, 0x200000, W_BYTES, arch.axi_data_bytes, Access.READ_ONLY),
        Binding(3, hbm, INVALID_CORE_ID, 0x300000, accumulator_bytes, arch.axi_data_bytes, Access.READ_WRITE),
    )
    slots = tuple(
        BindingSlot(index, symbol, MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, access, binding)
        for index, (symbol, size, access, binding) in enumerate(
            zip(("input", "weight", "output"), (IN_BYTES, W_BYTES, accumulator_bytes), (Access.READ_ONLY, Access.READ_ONLY, Access.READ_WRITE), bindings), 1
        )
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant(
        "main",
        "engine_reduce_pressure",
        f"{name}:engine_reduce_pressure",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(10, ExternalSlotBacking(1)), ObjectBacking(11, ExternalSlotBacking(2)), ObjectBacking(12, ExternalSlotBacking(3))),
        binding_slots=slots,
    )
    owner_stream = {
        op_id: stream
        for stream, op_ids in (
            (variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL), (25, 26, 28, 30, 31, 32, 36, 37, 38, 39, 40, 41)),
            (variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL), (27, 29, 33, 34, 35)),
        )
        for op_id in op_ids
    }
    owner_stream[25].control_command(RequestBeginAttrs())
    for op_id in tuple(item.op_id for item in effects):
        owner_stream[op_id].kernel_command(op_id)
    owner_stream[25].control_command(RequestEndAttrs())
    owner_stream[25].control_command(HaltAttrs())
    owner_stream[27].control_command(HaltAttrs())
    return builder.build()


def _build_double_buffer_program(arch, profile, serialized, reuse_ordered=True):
    name = f"build_double_buffer_{profile}_program"
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, (M, K), (K, 1), StorageClass.EXTERNAL, Access.READ_ONLY, IN_BYTES, IN_BYTES, None),
        KernelTensor(2, 0, None, 2, 0, "weight", TensorRole.WEIGHT, DType.FP16, (K, N), (N, 1), StorageClass.EXTERNAL, Access.READ_ONLY, W_BYTES, W_BYTES, None),
        KernelTensor(3, 1, None, 3, 0, "result", TensorRole.OUTPUT, DType.FP16, (M, N), (N, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, OUT_BYTES, OUT_BYTES, None),
    )
    placements = (Placement(1, (0,)),)
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0, 0), (M, K), (M, K), 0),
        TensorShard(2, 2, 1, 0, DistributionKind.REPLICATED, (0, 0), (K, N), (K, N), 0),
        TensorShard(3, 3, 1, 0, DistributionKind.REPLICATED, (0, 0), (M, N), (M, N), 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, (M, K), (K, 1), IN_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, (M, K), (K, 1), IN_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, (K, N), (N, 1), W_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 3, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 3, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(6, 3, 0, MemorySpace.CORE_SRAM, (M, N), (N, 1), OUT_BYTES, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(7, 1, INVALID_CORE_ID, MemorySpace.HBM, (M, K), (K, 1), IN_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(8, 1, INVALID_CORE_ID, MemorySpace.HBM, (M, K), (K, 1), IN_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(9, 1, INVALID_CORE_ID, MemorySpace.HBM, (M, K), (K, 1), IN_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(10, 2, INVALID_CORE_ID, MemorySpace.HBM, (K, N), (N, 1), W_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(11, 3, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(12, 3, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
        BufferObject(13, 3, INVALID_CORE_ID, MemorySpace.HBM, (M, N), (N, 1), OUT_BYTES, arch.axi_data_bytes, False, 0),
    )
    view_shards = (1, 1, 2, 3, 3, 3, 1, 1, 1, 2, 3, 3, 3)
    views = tuple(
        BufferView(index, item.object_id, view_shards[index - 1], (0, 0), item.shape, item.shape, 0, item.strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, item in enumerate(objects, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EMPTY),
        TensorState(2, 1, 1, StateOrigin.PRODUCED),
        TensorState(3, 1, 2, StateOrigin.PRODUCED),
        TensorState(4, 2, 0, StateOrigin.EMPTY),
        TensorState(5, 2, 1, StateOrigin.PRODUCED),
        TensorState(6, 3, 0, StateOrigin.EMPTY),
        TensorState(7, 3, 1, StateOrigin.PRODUCED),
        TensorState(8, 4, 0, StateOrigin.EMPTY),
        TensorState(9, 4, 1, StateOrigin.PRODUCED),
        TensorState(10, 5, 0, StateOrigin.EMPTY),
        TensorState(11, 5, 1, StateOrigin.PRODUCED),
        TensorState(12, 6, 0, StateOrigin.EMPTY),
        TensorState(13, 6, 1, StateOrigin.PRODUCED),
        TensorState(14, 7, 0, StateOrigin.EXTERNAL),
        TensorState(15, 8, 0, StateOrigin.EXTERNAL),
        TensorState(16, 9, 0, StateOrigin.EXTERNAL),
        TensorState(17, 10, 0, StateOrigin.EXTERNAL),
        TensorState(18, 11, 0, StateOrigin.EMPTY),
        TensorState(19, 11, 1, StateOrigin.PRODUCED),
        TensorState(20, 12, 0, StateOrigin.EMPTY),
        TensorState(21, 12, 1, StateOrigin.PRODUCED),
        TensorState(22, 13, 0, StateOrigin.EMPTY),
        TensorState(23, 13, 1, StateOrigin.PRODUCED),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, objects[index - 1].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    input_region = ElementRegion((0, 0), (M, K), (1, 1))
    weight_region = ElementRegion((0, 0), (K, N), (1, 1))
    result_region = ElementRegion((0, 0), (M, N), (1, 1))
    tile = KernelTile(0, 0, 0, 0, 1, M, N, K, 1, M, N, K)
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    gemm_attrs = GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(IN_BYTES + W_BYTES, OUT_BYTES, IN_BYTES + W_BYTES + OUT_BYTES, M * N * K, 0, 0), MatrixPhase.DIRECT, 0)
    after = {1: (), 2: (), 3: (1, 2), 4: (), 5: (3,), 6: (4,), 7: (3,), 8: (6,), 9: (7,), 10: (9,)}
    if not reuse_ordered:
        after = {**after, 7: ()}
    if serialized:
        after = {
            index: tuple(sorted({*tokens, index - 1}))
            for index, tokens in after.items()
            if index > 1
        } | {1: ()}
    steps = (
        (27, 0, "load:weight", KernelOpcode.DMA, (OperandAccess(17, 10, weight_region, OperandAccessMode.READ),), (StateTransition(6, 7, 3, weight_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b"")),
        (28, 0, "load:input:0", KernelOpcode.DMA, (OperandAccess(14, 7, input_region, OperandAccessMode.READ),), (StateTransition(1, 2, 1, input_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b"")),
        (29, 1, "compute:0", KernelOpcode.GEMM, (OperandAccess(2, 1, input_region, OperandAccessMode.READ), OperandAccess(7, 3, weight_region, OperandAccessMode.READ)), (StateTransition(8, 9, 4, result_region),), gemm_attrs),
        (30, 0, "load:input:1", KernelOpcode.DMA, (OperandAccess(15, 8, input_region, OperandAccessMode.READ),), (StateTransition(4, 5, 2, input_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b"")),
        (31, 0, "store:0", KernelOpcode.DMA, (OperandAccess(9, 4, result_region, OperandAccessMode.READ),), (StateTransition(18, 19, 11, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b"")),
        (32, 1, "compute:1", KernelOpcode.GEMM, (OperandAccess(5, 2, input_region, OperandAccessMode.READ), OperandAccess(7, 3, weight_region, OperandAccessMode.READ)), (StateTransition(10, 11, 5, result_region),), gemm_attrs),
        (33, 0, "load:input:2", KernelOpcode.DMA, (OperandAccess(16, 9, input_region, OperandAccessMode.READ),), (StateTransition(2, 3, 1, input_region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b"")),
        (34, 0, "store:1", KernelOpcode.DMA, (OperandAccess(11, 5, result_region, OperandAccessMode.READ),), (StateTransition(20, 21, 12, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b"")),
        (35, 1, "compute:2", KernelOpcode.GEMM, (OperandAccess(3, 1, input_region, OperandAccessMode.READ), OperandAccess(7, 3, weight_region, OperandAccessMode.READ)), (StateTransition(12, 13, 6, result_region),), gemm_attrs),
        (36, 0, "store:2", KernelOpcode.DMA, (OperandAccess(13, 6, result_region, OperandAccessMode.READ),), (StateTransition(22, 23, 13, result_region),), DmaAttrs(DmaKind.STORE, 0, 0, INVALID_CORE_ID, 0, b"")),
    )
    effects = tuple(
        KernelOp(op_id, computation, f"overlap:{index:02d}:{label}", opcode, 0, 3 if opcode is KernelOpcode.GEMM else 0, reads, writes, operation_attrs, after[index], index)
        for index, (op_id, computation, label, opcode, reads, writes, operation_attrs) in enumerate(steps, 1)
    )
    computations = (KernelComputation(1, OpCode.MATMUL, (1, 2), 3, attrs),)
    records = KernelMemoryRecords(tensors, computations, placements, shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 11)), declarations + effects)
    allocations = (
        SramAllocation(1, 1, 0, 0x0000, IN_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(2, 2, 0, 0x2000, IN_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(3, 3, 0, 0x4000, W_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(4, 4, 0, 0x6000, OUT_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(5, 5, 0, 0x8000, OUT_BYTES, arch.sram_base_alignment_bytes),
        SramAllocation(6, 6, 0, 0xA000, OUT_BYTES, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    addresses = (0x100000, 0x200000, 0x300000, 0x400000, 0x500000, 0x600000, 0x700000)
    sizes = (IN_BYTES, IN_BYTES, IN_BYTES, W_BYTES, OUT_BYTES, OUT_BYTES, OUT_BYTES)
    accesses = (Access.READ_ONLY, Access.READ_ONLY, Access.READ_ONLY, Access.READ_ONLY, Access.READ_WRITE, Access.READ_WRITE, Access.READ_WRITE)
    symbols = ("input:0", "input:1", "input:2", "weight", "output:0", "output:1", "output:2")
    bindings = tuple(
        Binding(index, hbm, INVALID_CORE_ID, address, size, arch.axi_data_bytes, access)
        for index, (address, size, access) in enumerate(zip(addresses, sizes, accesses), 1)
    )
    binding_slots = tuple(
        BindingSlot(index, symbol, MemorySpace.HBM, hbm, INVALID_CORE_ID, size, arch.axi_data_bytes, access, binding)
        for index, (symbol, size, access, binding) in enumerate(zip(symbols, sizes, accesses, bindings), 1)
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant(
        "main",
        f"double_buffer_{profile}",
        f"{name}:double_buffer_{profile}",
        records=records,
        allocations=allocations,
        external_backings=tuple(
            ObjectBacking(object_id, ExternalSlotBacking(slot))
            for object_id, slot in zip((7, 8, 9, 10, 11, 12, 13), (1, 2, 3, 4, 5, 6, 7))
        ),
        binding_slots=binding_slots,
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for op in effects:
        stream.kernel_command(op.op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_double_buffer_overlap_program(arch):
    return _build_double_buffer_program(arch, "overlap", False)


def build_double_buffer_serialized_program(arch):
    return _build_double_buffer_program(arch, "serialized", True)


def build_double_buffer_unsafe_program(arch):
    return _build_double_buffer_program(arch, "unsafe", False, False)


def build_barrier_asymmetric_program(arch):
    name = "build_barrier_asymmetric_program"
    participants = (0, 1)
    shape = (M, K)
    strides = (K, 1)
    slot_bytes = M * K * 2
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP16, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, slot_bytes, slot_bytes, None),
        KernelTensor(2, 0, None, 2, 0, "slow", TensorRole.ACTIVATION, DType.FP16, shape, strides, StorageClass.CORE_SRAM, Access.READ_WRITE, slot_bytes, slot_bytes, None),
    )
    placements = (Placement(1, (0,)), Placement(2, (1,)))
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
        TensorShard(2, 1, 2, 1, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
        TensorShard(3, 2, 2, 1, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
        TensorShard(4, 2, 1, 0, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, slot_bytes, arch.axi_data_bytes, False, 0),
        BufferObject(2, 2, 1, MemorySpace.CORE_SRAM, shape, strides, slot_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 2, 0, MemorySpace.CORE_SRAM, shape, strides, slot_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, 1, MemorySpace.CORE_SRAM, shape, strides, slot_bytes, arch.sram_base_alignment_bytes, False, 0),
    )
    views = tuple(
        BufferView(index, item.object_id, (1, 2, 4, 3)[index - 1], (0, 0), item.shape, item.shape, 0, item.strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, item in enumerate(objects, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
        TensorState(4, 3, 0, StateOrigin.EMPTY),
        TensorState(5, 3, 1, StateOrigin.PRODUCED),
        TensorState(6, 4, 0, StateOrigin.EMPTY),
        TensorState(7, 4, 1, StateOrigin.PRODUCED),
    )
    declarations = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    ) + tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, objects[index - 1].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    region = ElementRegion((0, 0), shape, (1, 1))
    barrier_attrs = BarrierAttrs(participants)
    effects = (
        KernelOp(9, 0, "asym:01:barrier:core0", KernelOpcode.BARRIER, 0, 0, (), (), barrier_attrs, (), 1),
        KernelOp(10, 0, "asym:02:load:core1", KernelOpcode.DMA, 1, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, 1, INVALID_CORE_ID, 1, 0, b""), (), 2),
        KernelOp(11, 0, "asym:03:barrier:core1", KernelOpcode.BARRIER, 1, 0, (), (), barrier_attrs, (2,), 3),
        KernelOp(12, 0, "asym:04:after:core0", KernelOpcode.DMA, 0, 0, (), (StateTransition(4, 5, 3, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x5a"), (1, 3), 4),
        KernelOp(13, 0, "asym:05:after:core1", KernelOpcode.DMA, 1, 0, (), (StateTransition(6, 7, 4, region),), DmaAttrs(DmaKind.LOCAL_FILL, 1, 1, 1, 0, b"\xa5"), (1, 3), 5),
    )
    records = KernelMemoryRecords(tensors, (), placements, shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 6)), declarations + effects)
    allocations = (
        SramAllocation(1, 2, 1, 0x0000, slot_bytes, arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 0, 0x0000, slot_bytes, arch.sram_base_alignment_bytes),
        SramAllocation(3, 4, 1, 0x2000, slot_bytes, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    binding = Binding(1, hbm, INVALID_CORE_ID, 0x100000, slot_bytes, arch.axi_data_bytes, Access.READ_ONLY)
    slot = BindingSlot(1, "input", MemorySpace.HBM, hbm, INVALID_CORE_ID, slot_bytes, arch.axi_data_bytes, Access.READ_ONLY, binding)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant(
        "main",
        "barrier_asymmetric",
        f"{name}:barrier_asymmetric",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(1, ExternalSlotBacking(1)),),
        binding_slots=(slot,),
    )
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    stream0.kernel_command(9)
    stream1.kernel_command(10)
    stream1.kernel_command(11)
    stream0.kernel_command(12)
    stream1.kernel_command(13)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_p2p_multi_descriptor_program(arch):
    name = "build_p2p_multi_descriptor_program"
    shape = (40, 2)
    compact = (2, 1)
    padded = (3, 2)
    logical_bytes = shape[0] * shape[1] * DType.FP16.byte_width
    padded_bytes = _extent_bytes(shape, padded, DType.FP16)
    region = ElementRegion((0, 0), shape, (1, 1))
    tensors = (
        KernelTensor(1, 1, None, 1, 0, "scratch", TensorRole.INPUT, DType.FP16, shape, padded, StorageClass.EXTERNAL, Access.READ_WRITE, logical_bytes, padded_bytes, None),
    )
    placements = (Placement(1, (0,)), Placement(2, (1,)))
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
        TensorShard(2, 1, 2, 1, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, compact, logical_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, compact, logical_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 1, 1, MemorySpace.CORE_SRAM, shape, padded, padded_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, padded, padded_bytes, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), shape, shape, 0, compact, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 1, (0, 0), shape, shape, 0, compact, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 3, 2, (0, 0), shape, shape, 0, padded, None, None, 0),
        BufferView(4, 4, 2, (0, 0), shape, shape, 0, padded, None, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EMPTY),
        TensorState(2, 1, 1, StateOrigin.PRODUCED),
        TensorState(3, 2, 0, StateOrigin.EMPTY),
        TensorState(4, 2, 1, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY),
        TensorState(6, 3, 1, StateOrigin.PRODUCED),
        TensorState(7, 4, 0, StateOrigin.EMPTY),
        TensorState(8, 4, 1, StateOrigin.PRODUCED),
    )
    elementwise = ElementwiseAttrs()
    tile = KernelTile(0, 0, 0, 0, 1, shape[0], shape[1], 1, 1, shape[0], shape[1], 1)
    vector_attrs = VectorKernelAttrs(OpCode.RELU, elementwise, tile, KernelCost(logical_bytes, logical_bytes, logical_bytes * 2, 0, shape[0] * shape[1], 0), VectorAlgorithm.ELEMENTWISE)
    effects = (
        KernelOp(9, 0, "p2p:01:fill", KernelOpcode.DMA, 0, 0, (), (StateTransition(1, 2, 1, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x00"), (), 1),
        KernelOp(10, 1, "p2p:02:relu", KernelOpcode.VECTOR, 0, 1, (OperandAccess(2, 1, region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, region),), vector_attrs, (1,), 2),
        KernelOp(11, 0, "p2p:03:push", KernelOpcode.DMA, 0, 0, (OperandAccess(4, 2, region, OperandAccessMode.READ),), (StateTransition(5, 6, 3, region),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, 1, b""), (2,), 3),
        KernelOp(12, 0, "p2p:04:recv", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(1, 0, 1, logical_bytes), (3,), 4),
        KernelOp(13, 0, "p2p:05:store", KernelOpcode.DMA, 1, 0, (OperandAccess(6, 3, region, OperandAccessMode.READ),), (StateTransition(7, 8, 4, region),), DmaAttrs(DmaKind.STORE, 1, 1, INVALID_CORE_ID, 0, b""), (4,), 5),
    )
    computations = (KernelComputation(1, OpCode.RELU, (1,), 1, elementwise),)
    records = KernelMemoryRecords(tensors, computations, placements, shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 6)), _declaration_ops(objects, views) + effects)
    allocations = (
        SramAllocation(1, 1, 0, 0, 0x100, arch.sram_base_alignment_bytes),
        SramAllocation(2, 2, 0, 0x200, 0x100, arch.sram_base_alignment_bytes),
        SramAllocation(3, 3, 1, 0, 0x200, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    binding = Binding(1, hbm, INVALID_CORE_ID, 0x200000, 0x200, arch.axi_data_bytes, Access.READ_WRITE)
    slot = BindingSlot(1, "sink", MemorySpace.HBM, hbm, INVALID_CORE_ID, 0x200, arch.axi_data_bytes, Access.READ_WRITE, binding)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant(
        "main",
        "p2p_multi_descriptor",
        f"{name}:p2p_multi_descriptor",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(4, ExternalSlotBacking(1)),),
        binding_slots=(slot,),
    )
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    stream0.kernel_command(9)
    stream0.kernel_command(10)
    stream0.kernel_command(11)
    stream1.kernel_command(12)
    stream1.kernel_command(13)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_p2p_prefilled_destination_program(arch):
    """The multi-descriptor P2P push onto a destination allocation that a core-1
    LOCAL_FILL already populated, so residency and this transfer's progress are
    separable facts."""
    name = "build_p2p_prefilled_destination_program"
    shape = (40, 2)
    compact = (2, 1)
    padded = (3, 2)
    logical_bytes = shape[0] * shape[1] * DType.FP16.byte_width
    padded_bytes = _extent_bytes(shape, padded, DType.FP16)
    region = ElementRegion((0, 0), shape, (1, 1))
    tensors = (
        KernelTensor(1, 1, None, 1, 0, "scratch", TensorRole.INPUT, DType.FP16, shape, padded, StorageClass.EXTERNAL, Access.READ_WRITE, logical_bytes, padded_bytes, None),
    )
    placements = (Placement(1, (0,)), Placement(2, (1,)))
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
        TensorShard(2, 1, 2, 1, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, compact, logical_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, compact, logical_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 1, 1, MemorySpace.CORE_SRAM, shape, padded, padded_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, padded, padded_bytes, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), shape, shape, 0, compact, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 1, (0, 0), shape, shape, 0, compact, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 3, 2, (0, 0), shape, shape, 0, padded, None, None, 0),
        BufferView(4, 4, 2, (0, 0), shape, shape, 0, padded, None, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EMPTY),
        TensorState(2, 1, 1, StateOrigin.PRODUCED),
        TensorState(3, 2, 0, StateOrigin.EMPTY),
        TensorState(4, 2, 1, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY),
        TensorState(6, 3, 1, StateOrigin.PRODUCED),
        TensorState(7, 4, 0, StateOrigin.EMPTY),
        TensorState(8, 4, 1, StateOrigin.PRODUCED),
        TensorState(9, 3, 2, StateOrigin.PRODUCED),
    )
    elementwise = ElementwiseAttrs()
    tile = KernelTile(0, 0, 0, 0, 1, shape[0], shape[1], 1, 1, shape[0], shape[1], 1)
    vector_attrs = VectorKernelAttrs(OpCode.RELU, elementwise, tile, KernelCost(logical_bytes, logical_bytes, logical_bytes * 2, 0, shape[0] * shape[1], 0), VectorAlgorithm.ELEMENTWISE)
    effects = (
        KernelOp(9, 0, "p2p:01:fill", KernelOpcode.DMA, 0, 0, (), (StateTransition(1, 2, 1, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x00"), (), 1),
        KernelOp(10, 1, "p2p:02:relu", KernelOpcode.VECTOR, 0, 1, (OperandAccess(2, 1, region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, region),), vector_attrs, (1,), 2),
        KernelOp(11, 0, "p2p:02b:prefill", KernelOpcode.DMA, 1, 0, (), (StateTransition(5, 6, 3, region),), DmaAttrs(DmaKind.LOCAL_FILL, 1, 1, 1, 0, b"\x5a"), (2,), 3),
        KernelOp(12, 0, "p2p:03:push", KernelOpcode.DMA, 0, 0, (OperandAccess(4, 2, region, OperandAccessMode.READ),), (StateTransition(6, 9, 3, region),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, 1, b""), (3,), 4),
        KernelOp(13, 0, "p2p:04:recv", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(1, 0, 1, logical_bytes), (4,), 5),
        KernelOp(14, 0, "p2p:05:store", KernelOpcode.DMA, 1, 0, (OperandAccess(9, 3, region, OperandAccessMode.READ),), (StateTransition(7, 8, 4, region),), DmaAttrs(DmaKind.STORE, 1, 1, INVALID_CORE_ID, 0, b""), (5,), 6),
    )
    computations = (KernelComputation(1, OpCode.RELU, (1,), 1, elementwise),)
    records = KernelMemoryRecords(tensors, computations, placements, shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 7)), _declaration_ops(objects, views) + effects)
    allocations = (
        SramAllocation(1, 1, 0, 0, 0x100, arch.sram_base_alignment_bytes),
        SramAllocation(2, 2, 0, 0x200, 0x100, arch.sram_base_alignment_bytes),
        SramAllocation(3, 3, 1, 0, 0x200, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    binding = Binding(1, hbm, INVALID_CORE_ID, 0x200000, 0x200, arch.axi_data_bytes, Access.READ_WRITE)
    slot = BindingSlot(1, "sink", MemorySpace.HBM, hbm, INVALID_CORE_ID, 0x200, arch.axi_data_bytes, Access.READ_WRITE, binding)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant(
        "main",
        "p2p_prefilled_destination",
        f"{name}:p2p_prefilled_destination",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(4, ExternalSlotBacking(1)),),
        binding_slots=(slot,),
    )
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    stream0.kernel_command(9)
    stream0.kernel_command(10)
    stream0.kernel_command(12)
    stream1.kernel_command(11)
    stream1.kernel_command(13)
    stream1.kernel_command(14)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def build_p2p_cancel_program(arch):
    """A P2P group whose single descriptor is still outstanding when an
    independent core-1 LOAD faults, so the group is cancelled while its later
    completion arrives successfully.

    The core-1 LOAD reads 8192 HBM bytes as one contiguous descriptor with a
    latency that lands between the P2P submission and its padded 4096-byte
    commit.
    """
    name = "build_p2p_cancel_program"
    shape = (8, 256)
    compact = (256, 1)
    padded = (258, 1)
    probe_shape = (1, 4096)
    probe_strides = (4096, 1)
    logical_bytes = shape[0] * shape[1] * DType.FP16.byte_width
    padded_bytes = _extent_bytes(shape, padded, DType.FP16)
    probe_bytes = probe_shape[0] * probe_shape[1] * DType.FP16.byte_width
    region = ElementRegion((0, 0), shape, (1, 1))
    probe_region = ElementRegion((0, 0), probe_shape, (1, 1))
    tensors = (
        KernelTensor(1, 1, None, 1, 0, "scratch", TensorRole.INPUT, DType.FP16, shape, padded, StorageClass.EXTERNAL, Access.READ_WRITE, logical_bytes, padded_bytes, None),
        KernelTensor(2, 0, None, 2, 0, "probe", TensorRole.INPUT, DType.FP16, probe_shape, probe_strides, StorageClass.EXTERNAL, Access.READ_ONLY, probe_bytes, probe_bytes, None),
    )
    placements = (Placement(1, (0,)), Placement(2, (1,)))
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
        TensorShard(2, 1, 2, 1, DistributionKind.REPLICATED, (0, 0), shape, shape, 0),
        TensorShard(3, 2, 2, 1, DistributionKind.REPLICATED, (0, 0), probe_shape, probe_shape, 0),
    )
    objects = (
        BufferObject(1, 1, 0, MemorySpace.CORE_SRAM, shape, compact, logical_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, shape, compact, logical_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(3, 1, 1, MemorySpace.CORE_SRAM, shape, padded, padded_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(4, 2, 1, MemorySpace.CORE_SRAM, probe_shape, probe_strides, probe_bytes, arch.sram_base_alignment_bytes, False, 0),
        BufferObject(5, 2, INVALID_CORE_ID, MemorySpace.HBM, probe_shape, probe_strides, probe_bytes, arch.axi_data_bytes, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), shape, shape, 0, compact, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(2, 2, 1, (0, 0), shape, shape, 0, compact, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(3, 3, 2, (0, 0), shape, shape, 0, padded, None, None, 0),
        BufferView(4, 4, 3, (0, 0), probe_shape, probe_shape, 0, probe_strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        BufferView(5, 5, 3, (0, 0), probe_shape, probe_shape, 0, probe_strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.EMPTY),
        TensorState(2, 1, 1, StateOrigin.PRODUCED),
        TensorState(3, 2, 0, StateOrigin.EMPTY),
        TensorState(4, 2, 1, StateOrigin.PRODUCED),
        TensorState(5, 3, 0, StateOrigin.EMPTY),
        TensorState(6, 3, 1, StateOrigin.PRODUCED),
        TensorState(7, 4, 0, StateOrigin.EMPTY),
        TensorState(8, 4, 1, StateOrigin.PRODUCED),
        TensorState(9, 5, 0, StateOrigin.EXTERNAL),
    )
    elementwise = ElementwiseAttrs()
    tile = KernelTile(0, 0, 0, 0, 1, shape[0], shape[1], 1, 1, shape[0], shape[1], 1)
    vector_attrs = VectorKernelAttrs(OpCode.RELU, elementwise, tile, KernelCost(logical_bytes, logical_bytes, logical_bytes * 2, 0, shape[0] * shape[1], 0), VectorAlgorithm.ELEMENTWISE)
    effects = (
        KernelOp(11, 0, "p2p-cancel:01:fill", KernelOpcode.DMA, 0, 0, (), (StateTransition(1, 2, 1, region),), DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x00"), (), 1),
        KernelOp(12, 1, "p2p-cancel:02:relu", KernelOpcode.VECTOR, 0, 1, (OperandAccess(2, 1, region, OperandAccessMode.READ),), (StateTransition(3, 4, 2, region),), vector_attrs, (1,), 2),
        KernelOp(13, 0, "p2p-cancel:03:push", KernelOpcode.DMA, 0, 0, (OperandAccess(4, 2, region, OperandAccessMode.READ),), (StateTransition(5, 6, 3, region),), DmaAttrs(DmaKind.P2P_PUSH, 0, 0, 1, 1, b""), (2,), 3),
        KernelOp(14, 0, "p2p-cancel:04:recv", KernelOpcode.RECV_WAIT, 1, 0, (), (), RecvWaitAttrs(1, 0, 1, logical_bytes), (3,), 4),
        KernelOp(15, 0, "p2p-cancel:05:load", KernelOpcode.DMA, 1, 0, (OperandAccess(9, 5, probe_region, OperandAccessMode.READ),), (StateTransition(7, 8, 4, probe_region),), DmaAttrs(DmaKind.LOAD, 1, INVALID_CORE_ID, 1, 0, b""), (), 5),
    )
    computations = (KernelComputation(1, OpCode.RELU, (1,), 1, elementwise),)
    records = KernelMemoryRecords(tensors, computations, placements, shards, (), objects, views, states, tuple(ControlToken(index) for index in range(1, 6)), _declaration_ops(objects, views) + effects)
    allocations = (
        SramAllocation(1, 1, 0, 0, 0x1000, arch.sram_base_alignment_bytes),
        SramAllocation(2, 2, 0, 0x1000, 0x1000, arch.sram_base_alignment_bytes),
        SramAllocation(3, 3, 1, 0, 0x2000, arch.sram_base_alignment_bytes),
        SramAllocation(4, 4, 1, 0x2000, probe_bytes, arch.sram_base_alignment_bytes),
    )
    hbm = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    binding = Binding(1, hbm, INVALID_CORE_ID, 0x200000, probe_bytes, arch.axi_data_bytes, Access.READ_ONLY)
    slot = BindingSlot(1, "probe", MemorySpace.HBM, hbm, INVALID_CORE_ID, probe_bytes, arch.axi_data_bytes, Access.READ_ONLY, binding)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("golden", name, 1))
    variant = builder.variant(
        "main",
        "p2p_cancel",
        f"{name}:p2p_cancel",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(5, ExternalSlotBacking(1)),),
        binding_slots=(slot,),
    )
    stream0 = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream2 = variant.stream(1, 1, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    stream0.kernel_command(11)
    stream0.kernel_command(12)
    stream0.kernel_command(13)
    stream1.kernel_command(14)
    stream2.kernel_command(15)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()

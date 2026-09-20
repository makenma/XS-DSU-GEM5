import dataclasses

import pytest

from mesh_ir.analysis.cost import WorkUnit
from mesh_ir.analysis.kernel_work import kernel_local_copy_domain, kernel_local_reduction_domain, kernel_op_work_phases, kernel_work_phases
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DType, Engine, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, MatmulAttrs, NormAttrs, OpCode, ReduceAttrs, SoftmaxAttrs
from mesh_ir.ir.kernel_ir import BufferObject, BufferView, CollectiveAlgorithm, CollectiveAttrs, CollectiveKind, ControlToken, DistributionKind, ElementRegion, GemmKernelAttrs, KernelComputation, KernelCost, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, KernelTile, LocalCopyAttrs, LocalReduceAttrs, MatrixEpilogueAlgorithm, MatrixEpilogueKernelAttrs, MatrixPhase, NormAlgorithm, NormKernelAttrs, OperandAccess, OperandAccessMode, Placement, ReduceKind, ReductionAlgorithm, ReductionKernelAttrs, SoftmaxAlgorithm, SoftmaxKernelAttrs, StateOrigin, StateTransition, SynthesizedTensorPurpose, TensorShard, TensorState, VectorAlgorithm, VectorKernelAttrs
from mesh_ir.ir.kernel_verify import KernelCapabilityRequirement, kernel_capability_requirements
from mesh_ir.scheduled.model import ComputeExecution
from tests.unit.test_gate2_kernel_ir import create_kernel, declarations, local_copy_kernel, real_gemm_kernel, refreshed, semantic_operation_kernel, tile


def _subset(kernel, read_origin, read_shape, write_origin, write_shape, work):
    op = kernel.ops[-1]
    reads = (dataclasses.replace(op.reads[0], region=ElementRegion(read_origin, read_shape, (1,) * len(read_shape))), *op.reads[1:])
    write = dataclasses.replace(op.writes[0], region=ElementRegion(write_origin, write_shape, (1,) * len(write_shape)))
    attrs = dataclasses.replace(op.attrs, cost=work)
    changed = dataclasses.replace(kernel, ops=(*kernel.ops[:-1], dataclasses.replace(op, reads=reads, writes=(write,), attrs=attrs)))
    return dataclasses.replace(changed, semantic_sha256=semantic_sha256(changed.semantic_dict()))


def _work_by_unit(phases):
    return {(item.engine, item.unit): item.operations for phase in phases for item in phase.work}


def _linear_accumulation_and_epilogue_kernel():
    attrs = MatmulAttrs(alpha=0.5, beta=0.25, accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "lhs", TensorRole.WEIGHT, DType.FP16, (2, 3), (3, 1), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 12, 12, "1" * 64),
        KernelTensor(2, 0, None, 2, 0, "rhs", TensorRole.WEIGHT, DType.FP16, (3, 4), (4, 1), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 24, 24, "2" * 64),
        KernelTensor(3, 0, None, 3, 0, "bias", TensorRole.WEIGHT, DType.FP16, (4,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 8, 8, "3" * 64),
        KernelTensor(4, 1, None, 4, 0, "result", TensorRole.OUTPUT, DType.FP16, (2, 4), (4, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, 16, 16, None),
        KernelTensor(5, 1, SynthesizedTensorPurpose.ACCUMULATION, 5, 0, "accumulator", TensorRole.ACTIVATION, DType.FP32, (2, 4), (4, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, 32, 32, None),
    )
    computation = KernelComputation(1, OpCode.LINEAR_BIAS, (1, 2, 3), 4, attrs)
    shapes = ((2, 3), (3, 4), (4,), (2, 4), (2, 4))
    strides = ((3, 1), (4, 1), (1,), (4, 1), (4, 1))
    sizes = (12, 24, 8, 16, 32)
    shards = tuple(TensorShard(index, index, 1, 3, DistributionKind.PARTITIONED, (0,) * len(shape), shape, shape) for index, shape in enumerate(shapes, 1))
    objects = tuple(BufferObject(index, index, 3, MemorySpace.CORE_SRAM, shape, stride, size, 8, False, 0) for index, (shape, stride, size) in enumerate(zip(shapes, strides, sizes), 1))
    views, declarations_ = declarations(objects)
    states = (
        TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT),
        TensorState(2, 2, 0, StateOrigin.PRE_RESIDENT),
        TensorState(3, 3, 0, StateOrigin.PRE_RESIDENT),
        TensorState(4, 5, 0, StateOrigin.EMPTY),
        TensorState(5, 5, 1, StateOrigin.PRODUCED),
        TensorState(6, 4, 0, StateOrigin.EMPTY),
        TensorState(7, 4, 1, StateOrigin.PRODUCED),
    )
    lhs = ElementRegion((0, 0), (2, 3), (1, 1))
    rhs = ElementRegion((0, 0), (3, 4), (1, 1))
    bias = ElementRegion((0,), (4,), (1,))
    result = ElementRegion((0, 0), (2, 4), (1, 1))
    matrix_tile = KernelTile(0, 0, 0, 0, 1, 2, 4, 3, 1, 2, 4, 3)
    ops = (
        KernelOp(11, 1, "linear:accumulate", KernelOpcode.GEMM, 3, 5, (OperandAccess(1, 1, lhs, OperandAccessMode.READ), OperandAccess(2, 2, rhs, OperandAccessMode.READ)), (StateTransition(4, 5, 5, result),), GemmKernelAttrs(OpCode.LINEAR_BIAS, attrs, matrix_tile, KernelCost(36, 32, 68, 24, 0, 0), MatrixPhase.ACCUMULATE_ONLY, 0), (), 1),
        KernelOp(12, 1, "linear:epilogue", KernelOpcode.MATRIX_EPILOGUE, 3, 4, (OperandAccess(5, 5, result, OperandAccessMode.READ), OperandAccess(3, 3, bias, OperandAccessMode.READ)), (StateTransition(6, 7, 4, result),), MatrixEpilogueKernelAttrs(OpCode.LINEAR_BIAS, attrs, matrix_tile, KernelCost(40, 16, 56, 0, 32, 0), MatrixEpilogueAlgorithm.VECTOR_ACCUMULATION), (1,), 2),
    )
    return create_kernel("a" * 64, "b" * 64, "forward", "linear", tensors, (computation,), (Placement(1, (3,)),), shards, (), objects, views, states, (ControlToken(1), ControlToken(2)), (*declarations_, *ops))


def _concrete_local_sum_records():
    base = real_gemm_kernel(partial_output=True)
    objects = (*base.objects, dataclasses.replace(base.objects[-1], object_id=5, buffer_index=1))
    views, declarations_ = declarations(objects, (1, 1, 2, 4, 4))
    states = (
        *base.states,
        TensorState(7, 5, 0, StateOrigin.EMPTY),
        TensorState(8, 5, 1, StateOrigin.PRODUCED, 1),
        TensorState(9, 4, 2, StateOrigin.PRODUCED, 1),
    )
    load = dataclasses.replace(base.ops[-2], op_id=11)
    first = dataclasses.replace(base.ops[-1], op_id=12)
    second = dataclasses.replace(first, op_id=13, stable_key="gemm:2", writes=(StateTransition(7, 8, 5, first.writes[0].region),), after_tokens=(2,), done_token=3)
    local = KernelOp(14, 1, "local-sum", KernelOpcode.LOCAL_REDUCE, 3, 4, (OperandAccess(6, 4, first.writes[0].region, OperandAccessMode.READ), OperandAccess(8, 5, first.writes[0].region, OperandAccessMode.READ)), (StateTransition(6, 9, 4, first.writes[0].region),), LocalReduceAttrs(ReduceKind.SUM, 1, tile(8), KernelCost(64, 32, 96, 0, 0, 8)), (3,), 4)
    kernel = create_kernel(base.arch_digest, base.source_semantic_hash, base.entrypoint, base.profile_id, base.tensors, base.computations, base.placements, base.shards, base.partial_sums, objects, views, states, tuple(ControlToken(index) for index in range(1, 5)), (*declarations_, load, first, second, local))
    return kernel.memory_records(), local.op_id


def test_vector_projection_uses_all_algorithm_primitives_and_is_the_scheduled_payload():
    kernel = semantic_operation_kernel(
        KernelOpcode.VECTOR,
        VectorKernelAttrs(OpCode.GELU, ElementwiseAttrs(approximation="none"), tile(), KernelCost(16, 16, 32, 0, 20, 0), VectorAlgorithm.ELEMENTWISE),
        (((4,), DType.FP32),),
        ((4,), DType.FP32),
    )
    phases = kernel_op_work_phases(kernel.memory_records(), kernel.ops[-1].op_id)
    assert _work_by_unit(phases) == {
        (Engine.VECTOR, WorkUnit.DIV): 4,
        (Engine.VECTOR, WorkUnit.ERF): 4,
        (Engine.VECTOR, WorkUnit.ADD): 4,
        (Engine.VECTOR, WorkUnit.MUL): 8,
    }
    assert ComputeExecution(phases).phases == phases
    assert kernel_capability_requirements(kernel) == (KernelCapabilityRequirement(kernel.ops[-1].op_id, Engine.VECTOR, DType.FP32),)


def test_row_projection_uses_actual_subset_rows_for_reduction_softmax_and_norm():
    reduction = semantic_operation_kernel(
        KernelOpcode.REDUCE,
        ReductionKernelAttrs(OpCode.REDUCE_SUM, ReduceAttrs((1,), False, DType.FP32, DType.FP32), tile(4), KernelCost(128, 16, 144, 0, 0, 28), ReductionAlgorithm.LEFT_TO_RIGHT),
        (((4, 8), DType.FP32),),
        ((4,), DType.FP32),
    )
    reduction = _subset(reduction, (1, 0), (2, 8), (1,), (2,), KernelCost(64, 8, 72, 0, 0, 14))
    softmax = semantic_operation_kernel(
        KernelOpcode.SOFTMAX,
        SoftmaxKernelAttrs(SoftmaxAttrs(1, DType.FP32), tile(32), KernelCost(128, 128, 256, 0, 96, 56), SoftmaxAlgorithm.STABLE_MAX_SUM),
        (((4, 8), DType.FP32),),
        ((4, 8), DType.FP32),
    )
    softmax = _subset(softmax, (1, 0), (2, 8), (1, 0), (2, 8), KernelCost(64, 64, 128, 0, 48, 28))
    norm = semantic_operation_kernel(
        KernelOpcode.NORM,
        NormKernelAttrs(OpCode.LAYERNORM, NormAttrs((1,), 1e-5, False, False), tile(32), KernelCost(128, 128, 256, 0, 112, 56), NormAlgorithm.LAYER_NORM),
        (((4, 8), DType.FP32),),
        ((4, 8), DType.FP32),
    )
    norm = _subset(norm, (1, 0), (2, 8), (1, 0), (2, 8), KernelCost(64, 64, 128, 0, 56, 28))
    assert _work_by_unit(kernel_op_work_phases(reduction.memory_records(), reduction.ops[-1].op_id)) == {(Engine.REDUCE, WorkUnit.ADD): 14}
    assert tuple(phase.engine for phase in kernel_op_work_phases(softmax.memory_records(), softmax.ops[-1].op_id)) == (Engine.REDUCE, Engine.VECTOR, Engine.REDUCE, Engine.VECTOR)
    assert sum(item.operations for phase in kernel_op_work_phases(norm.memory_records(), norm.ops[-1].op_id) for item in phase.work if item.engine is Engine.REDUCE) == 28


def test_row_projection_rejects_split_semantic_axis_and_wrong_output_coordinates():
    base = semantic_operation_kernel(
        KernelOpcode.SOFTMAX,
        SoftmaxKernelAttrs(SoftmaxAttrs(1, DType.FP32), tile(32), KernelCost(128, 128, 256, 0, 96, 56), SoftmaxAlgorithm.STABLE_MAX_SUM),
        (((4, 8), DType.FP32),),
        ((4, 8), DType.FP32),
    )
    split = _subset(base, (1, 2), (2, 4), (1, 2), (2, 4), KernelCost(32, 32, 64, 0, 24, 12))
    shifted = _subset(base, (1, 0), (2, 8), (2, 0), (2, 8), KernelCost(64, 64, 128, 0, 48, 28))
    for kernel in (split, shifted):
        with pytest.raises(MeshIrError) as error:
            kernel_op_work_phases(kernel.memory_records(), kernel.ops[-1].op_id)
        assert error.value.code == "E_EXPORT_LAYOUT"


def test_linear_bias_accumulation_excludes_bias_and_epilogue_applies_it_once():
    kernel = _linear_accumulation_and_epilogue_kernel()
    accumulation = kernel_op_work_phases(kernel.memory_records(), 11)
    epilogue = kernel_op_work_phases(kernel.memory_records(), 12)
    assert _work_by_unit(accumulation) == {(Engine.TENSOR, WorkUnit.MAC): 24}
    assert _work_by_unit(epilogue) == {
        (Engine.VECTOR, WorkUnit.MUL): 16,
        (Engine.VECTOR, WorkUnit.ADD): 8,
        (Engine.VECTOR, WorkUnit.CAST): 8,
    }
    assert kernel_capability_requirements(kernel) == (
        KernelCapabilityRequirement(11, Engine.TENSOR, DType.FP16),
        KernelCapabilityRequirement(12, Engine.VECTOR, DType.FP32),
    )


def test_batched_matmul_physical_regions_require_exact_broadcasted_output_batch():
    attrs = MatmulAttrs(batch_axes=(0,))
    tile_ = KernelTile(0, 0, 0, 0, 3, 3, 5, 4, 3, 3, 5, 4)
    valid = semantic_operation_kernel(
        KernelOpcode.GEMM,
        GemmKernelAttrs(OpCode.MATMUL, attrs, tile_, KernelCost(224, 180, 404, 180, 0, 0), MatrixPhase.DIRECT, 0),
        (((3, 3, 4), DType.FP32), ((1, 4, 5), DType.FP32)),
        ((3, 3, 5), DType.FP32),
    )
    valid.verify()
    full = semantic_operation_kernel(
        KernelOpcode.GEMM,
        GemmKernelAttrs(OpCode.MATMUL, attrs, tile_, KernelCost(384, 180, 564, 180, 0, 0), MatrixPhase.DIRECT, 0),
        (((3, 3, 4), DType.FP32), ((3, 4, 5), DType.FP32)),
        ((3, 3, 5), DType.FP32),
    )
    effect = full.ops[-1]
    reads = (dataclasses.replace(effect.reads[0], region=ElementRegion((0, 0, 0), (2, 3, 4), (1, 1, 1))), effect.reads[1])
    changed = dataclasses.replace(effect, reads=reads, attrs=dataclasses.replace(effect.attrs, cost=KernelCost(336, 180, 516, 180, 0, 0)))
    with pytest.raises(MeshIrError) as error:
        refreshed(dataclasses.replace(full, ops=(*full.ops[:-1], changed))).verify()
    assert error.value.code == "E_EXPORT_LAYOUT"


def test_concrete_local_sum_uses_output_elements_and_physical_input_count():
    records, op_id = _concrete_local_sum_records()
    phases = kernel_op_work_phases(records, op_id)
    assert _work_by_unit(phases) == {(Engine.REDUCE, WorkUnit.ADD): 8}


@pytest.mark.parametrize(("fan_in", "reduction_ops"), ((2, 8), (3, 16)))
def test_segmented_local_sum_uses_total_output_elements_and_group_fan_in(fan_in, reduction_ops):
    records, op_id = _concrete_local_sum_records()
    op = records.ops[op_id - 1]
    first = ElementRegion((0, 0), (1, 4), (1, 1))
    second = ElementRegion((1, 0), (1, 4), (1, 1))
    reads = tuple(
        dataclasses.replace(access, region=region)
        for region in (first, second)
        for access in (*op.reads, *op.reads[: fan_in - 2])
    )
    writes = tuple(dataclasses.replace(op.writes[0], region=region) for region in (first, second))
    cost = KernelCost(32 * fan_in, 32, 32 * (fan_in + 1), 0, 0, reduction_ops)
    changed = dataclasses.replace(records, ops=(*records.ops[: op_id - 1], dataclasses.replace(op, reads=reads, writes=writes, attrs=dataclasses.replace(op.attrs, cost=cost)), *records.ops[op_id:]))
    assert kernel_local_reduction_domain(changed, op_id).rows == 8
    assert kernel_local_reduction_domain(changed, op_id).fan_in == fan_in
    phases = kernel_op_work_phases(changed, op_id)
    assert _work_by_unit(phases) == {(Engine.REDUCE, WorkUnit.ADD): reduction_ops}


def test_local_reduction_domain_rejects_raw_identity_and_access_corruption(monkeypatch):
    records, op_id = _concrete_local_sum_records()
    from mesh_ir.ir import kernel_verify

    monkeypatch.setattr(kernel_verify, "verify_kernel_memory", lambda _: pytest.fail("local projection invoked whole-memory verification"))
    with pytest.raises(MeshIrError) as error:
        kernel_local_reduction_domain(records, True)
    assert error.value.code == "E_ABI_BOUNDS"
    with pytest.raises(MeshIrError) as error:
        kernel_local_reduction_domain(records, records.ops[0].op_id)
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"
    op = records.ops[op_id - 1]
    invalid = dataclasses.replace(op.reads[0], view_id=len(records.views) + 1)
    changed = dataclasses.replace(records, ops=(*records.ops[: op_id - 1], dataclasses.replace(op, reads=(invalid, *op.reads[1:])), *records.ops[op_id:]))
    with pytest.raises(MeshIrError) as error:
        kernel_local_reduction_domain(changed, op_id)
    assert error.value.code == "E_ABI_BOUNDS"
    malformed_region = ElementRegion((0,), (8,), (1,))
    changed = dataclasses.replace(records, ops=(*records.ops[: op_id - 1], dataclasses.replace(op, reads=(dataclasses.replace(op.reads[0], region=malformed_region), *op.reads[1:])), *records.ops[op_id:]))
    with pytest.raises(MeshIrError) as error:
        kernel_local_reduction_domain(changed, op_id)
    assert error.value.code == "E_EXPORT_LAYOUT"
    outside = ElementRegion((2, 0), (1, 4), (1, 1))
    changed = dataclasses.replace(records, ops=(*records.ops[: op_id - 1], dataclasses.replace(op, reads=(dataclasses.replace(op.reads[0], region=outside), *op.reads[1:])), *records.ops[op_id:]))
    with pytest.raises(MeshIrError) as error:
        kernel_local_reduction_domain(changed, op_id)
    assert error.value.code == "E_EXPORT_LAYOUT"
    changed = dataclasses.replace(records, ops=(*records.ops[: op_id - 1], dataclasses.replace(op, op_id=float(op_id)), *records.ops[op_id:]))
    with pytest.raises(MeshIrError) as error:
        kernel_local_reduction_domain(changed, op_id)
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"
    for field, value in (("valid_shape", (2.0, 4)), ("valid_shape", ("2", 4)), ("padded_shape", (2, "4"))):
        view = dataclasses.replace(records.views[op.reads[0].view_id - 1], **{field: value})
        changed = dataclasses.replace(records, views=(*records.views[: view.view_id - 1], view, *records.views[view.view_id:]))
        with pytest.raises(MeshIrError) as error:
            kernel_local_reduction_domain(changed, op_id)
        assert error.value.code == "E_EXPORT_LAYOUT"
    state = dataclasses.replace(records.states[op.reads[0].state_id - 1], state_id=float(op.reads[0].state_id))
    changed = dataclasses.replace(records, states=(*records.states[: op.reads[0].state_id - 1], state, *records.states[op.reads[0].state_id:]))
    with pytest.raises(MeshIrError) as error:
        kernel_local_reduction_domain(changed, op_id)
    assert error.value.code == "E_ABI_BOUNDS"


def test_batch_work_projection_matches_every_single_operation_and_verifies_once(monkeypatch):
    kernel = semantic_operation_kernel(
        KernelOpcode.VECTOR,
        VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(), KernelCost(16, 16, 32, 0, 4, 0), VectorAlgorithm.ELEMENTWISE),
        (((4,), DType.FP32),),
        ((4,), DType.FP32),
    )
    records = kernel.memory_records()
    from mesh_ir.ir import kernel_verify

    native = kernel_verify.verify_kernel_memory
    calls = 0

    def counted(candidate):
        nonlocal calls
        calls += 1
        return native(candidate)

    monkeypatch.setattr(kernel_verify, "verify_kernel_memory", counted)
    projected = kernel_work_phases(records)
    assert calls == 1
    assert len(projected) == len(records.ops)
    assert all(item == () for item in projected[:-1])
    assert projected[-1] == kernel_op_work_phases(records, records.ops[-1].op_id)
    assert calls == 2


def test_batch_work_projection_rejects_invalid_raw_records():
    records, _ = _concrete_local_sum_records()
    invalid = dataclasses.replace(records, ops=(*records.ops[:-1], dataclasses.replace(records.ops[-1], op_id=True)))
    with pytest.raises(MeshIrError) as error:
        kernel_work_phases(invalid)
    assert error.value.code == "E_ABI_BOUNDS"


def test_abstract_collective_cannot_be_projected_as_free_compute():
    kernel = real_gemm_kernel(partial_output=True)
    output = kernel.ops[-1].writes[0].region
    state = TensorState(7, 4, 2, StateOrigin.PRODUCED, 1)
    token = ControlToken(3)
    op = KernelOp(11, 1, "collective", KernelOpcode.COLLECTIVE, 3, 4, (OperandAccess(6, 4, output, OperandAccessMode.READ),), (StateTransition(6, 7, 4, output),), CollectiveAttrs(CollectiveKind.ALL_REDUCE, (3,), CollectiveAlgorithm.RING, 32, ReduceKind.SUM, 1), (2,), 3)
    records = dataclasses.replace(kernel.memory_records(), states=(*kernel.states, state), tokens=(*kernel.tokens, token), ops=(*kernel.ops, op))
    with pytest.raises(MeshIrError) as error:
        kernel_op_work_phases(records, op.op_id)
    assert error.value.code == "E_CAPABILITY_MISMATCH"


def test_nonempty_matrix_epilogue_checks_arity_before_accessing_regions():
    kernel = _linear_accumulation_and_epilogue_kernel()
    epilogue = dataclasses.replace(kernel.ops[-1], writes=())
    records = dataclasses.replace(kernel.memory_records(), states=kernel.states[:-1], ops=(*kernel.ops[:-1], epilogue))
    with pytest.raises(MeshIrError) as error:
        kernel_op_work_phases(records, epilogue.op_id)
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"


def test_zero_work_and_raw_boundary_validation():
    zero = semantic_operation_kernel(
        KernelOpcode.VECTOR,
        VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(0), KernelCost(0, 0, 0, 0, 0, 0), VectorAlgorithm.ELEMENTWISE),
        (((0,), DType.FP32),),
        ((0,), DType.FP32),
    )
    effect = dataclasses.replace(zero.ops[-1], reads=(), writes=())
    zero = refreshed(dataclasses.replace(zero, states=zero.states[:2], ops=(*zero.ops[:-1], effect)))
    zero.verify()
    assert kernel_op_work_phases(zero.memory_records(), zero.ops[-1].op_id) == ()
    nonempty = semantic_operation_kernel(
        KernelOpcode.VECTOR,
        VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(), KernelCost(16, 16, 32, 0, 4, 0), VectorAlgorithm.ELEMENTWISE),
        (((4,), DType.FP32),),
        ((4,), DType.FP32),
    )
    missing = dataclasses.replace(nonempty.ops[-1], reads=(), writes=(), attrs=dataclasses.replace(nonempty.ops[-1].attrs, tile=tile(0), cost=KernelCost(0, 0, 0, 0, 0, 0)))
    with pytest.raises(MeshIrError) as error:
        refreshed(dataclasses.replace(nonempty, states=nonempty.states[:2], ops=(*nonempty.ops[:-1], missing))).verify()
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "partitioned_input", TensorRole.WEIGHT, DType.FP32, (1,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 4, 4, "4" * 64),
        KernelTensor(2, 1, None, 2, 0, "partitioned_output", TensorRole.ACTIVATION, DType.FP32, (1,), (1,), StorageClass.CORE_SRAM, Access.READ_ONLY, 4, 4, None),
    )
    shards = (
        TensorShard(1, 1, 1, 3, DistributionKind.PARTITIONED, (0,), (1,), (1,)),
        TensorShard(2, 1, 1, 7, DistributionKind.PARTITIONED, (1,), (0,), (0,)),
        TensorShard(3, 2, 1, 3, DistributionKind.PARTITIONED, (0,), (1,), (1,)),
        TensorShard(4, 2, 1, 7, DistributionKind.PARTITIONED, (1,), (0,), (0,)),
    )
    objects = (
        BufferObject(1, 1, 3, MemorySpace.CORE_SRAM, (1,), (1,), 4, 8, False, 0),
        BufferObject(2, 2, 3, MemorySpace.CORE_SRAM, (1,), (1,), 4, 8, False, 0),
    )
    views, declarations_ = declarations(objects, (1, 3))
    full = ElementRegion((0,), (1,), (1,))
    actual = KernelOp(5, 1, "nonempty-rank:relu", KernelOpcode.VECTOR, 3, 3, (OperandAccess(1, 1, full, OperandAccessMode.READ),), (StateTransition(2, 3, 2, full),), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(1), KernelCost(4, 4, 8, 0, 1, 0), VectorAlgorithm.ELEMENTWISE), (), 1)
    empty = KernelOp(6, 1, "empty-rank:relu", KernelOpcode.VECTOR, 7, 4, (), (), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(0), KernelCost(0, 0, 0, 0, 0, 0), VectorAlgorithm.ELEMENTWISE), (1,), 2)
    partitioned = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.RELU, (1,), 2, ElementwiseAttrs()),), (Placement(1, (3, 7)),), shards, (), objects, views, (TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED)), (ControlToken(1), ControlToken(2)), (*declarations_, actual, empty))
    assert kernel_op_work_phases(partitioned, 6) == ()
    unrelated_shards = (
        *shards,
        TensorShard(5, 2, 2, 3, DistributionKind.PARTITIONED, (0,), (1,), (1,)),
        TensorShard(6, 2, 2, 7, DistributionKind.PARTITIONED, (1,), (0,), (0,)),
    )
    unrelated = dataclasses.replace(partitioned, placements=(*partitioned.placements, Placement(2, (3, 7))), shards=unrelated_shards, ops=(*partitioned.ops[:-1], dataclasses.replace(empty, result_shard_id=6)))
    with pytest.raises(MeshIrError) as error:
        kernel_op_work_phases(unrelated, 6)
    assert error.value.code == "E_PLACEMENT_INFEASIBLE"
    without_completion = dataclasses.replace(partitioned, ops=(*partitioned.ops[:-1], dataclasses.replace(empty, done_token=None)))
    with pytest.raises(MeshIrError):
        kernel_op_work_phases(without_completion, 6)
    for computation_id in (0, 2):
        invalid = dataclasses.replace(partitioned, ops=(*partitioned.ops[:-1], dataclasses.replace(empty, computation_id=computation_id)))
        with pytest.raises(MeshIrError) as error:
            kernel_op_work_phases(invalid, 6)
        assert error.value.code == "E_ABI_BOUNDS"
    for records, op_id in ((zero, zero.ops[-1].op_id), (zero.memory_records(), True), (zero.memory_records(), len(zero.ops) + 1)):
        with pytest.raises(MeshIrError) as error:
            kernel_op_work_phases(records, op_id)
        assert error.value.code == "E_ABI_BOUNDS"


@pytest.mark.parametrize("phase", (MatrixPhase.ACCUMULATE_FIRST, MatrixPhase.ACCUMULATE_ONLY))
def test_matrix_epilogue_requires_complete_k_coverage(phase):
    kernel = _linear_accumulation_and_epilogue_kernel()
    accumulate = kernel.ops[-2]
    short = dataclasses.replace(
        accumulate,
        reads=(dataclasses.replace(accumulate.reads[0], region=ElementRegion((0, 0), (2, 1), (1, 1))), dataclasses.replace(accumulate.reads[1], region=ElementRegion((0, 0), (1, 4), (1, 1)))),
        attrs=dataclasses.replace(accumulate.attrs, tile=dataclasses.replace(accumulate.attrs.tile, k_extent=1, valid_k=1), cost=KernelCost(12, 32, 44, 8, 0, 0), phase=phase),
    )
    with pytest.raises(MeshIrError) as error:
        refreshed(dataclasses.replace(kernel, ops=(*kernel.ops[:-2], short, kernel.ops[-1]))).verify()
    assert error.value.code == "E_EXPORT_LAYOUT"


def test_matrix_epilogue_rejects_overlapping_and_reset_k_phases():
    kernel = _linear_accumulation_and_epilogue_kernel()
    accumulate, epilogue = kernel.ops[-2:]
    first = dataclasses.replace(
        accumulate,
        reads=(dataclasses.replace(accumulate.reads[0], region=ElementRegion((0, 0), (2, 1), (1, 1))), dataclasses.replace(accumulate.reads[1], region=ElementRegion((0, 0), (1, 4), (1, 1)))),
        attrs=dataclasses.replace(accumulate.attrs, tile=dataclasses.replace(accumulate.attrs.tile, k_extent=1, valid_k=1), cost=KernelCost(12, 32, 44, 8, 0, 0), phase=MatrixPhase.ACCUMULATE_FIRST),
    )
    final = dataclasses.replace(
        accumulate,
        op_id=12,
        stable_key="linear:final-k",
        reads=(dataclasses.replace(accumulate.reads[0], region=ElementRegion((0, 0), (2, 2), (1, 1))), dataclasses.replace(accumulate.reads[1], region=ElementRegion((0, 0), (2, 4), (1, 1))), OperandAccess(5, 5, accumulate.writes[0].region, OperandAccessMode.READ)),
        writes=(StateTransition(5, 8, 5, accumulate.writes[0].region),),
        attrs=dataclasses.replace(accumulate.attrs, tile=dataclasses.replace(accumulate.attrs.tile, k_origin=0, k_extent=2, valid_k=2), cost=KernelCost(56, 32, 88, 16, 0, 0), phase=MatrixPhase.ACCUMULATE_FINAL),
        after_tokens=(1,),
        done_token=2,
    )
    epilogue = dataclasses.replace(epilogue, op_id=13, reads=(dataclasses.replace(epilogue.reads[0], state_id=8), epilogue.reads[1]), after_tokens=(2,), done_token=3)
    provisional = dataclasses.replace(kernel, states=(*kernel.states, TensorState(8, 5, 2, StateOrigin.PRODUCED)), tokens=(ControlToken(1), ControlToken(2), ControlToken(3)), ops=(*kernel.ops[:-2], first, final, epilogue))
    with pytest.raises(MeshIrError) as error:
        refreshed(provisional).verify()
    assert error.value.code == "E_EXPORT_LAYOUT"
    reset = dataclasses.replace(final, reads=final.reads[:2], attrs=dataclasses.replace(final.attrs, phase=MatrixPhase.ACCUMULATE_FIRST, cost=KernelCost(24, 32, 56, 16, 0, 0)))
    with pytest.raises(MeshIrError) as error:
        refreshed(dataclasses.replace(provisional, ops=(*kernel.ops[:-2], first, reset, epilogue))).verify()
    assert error.value.code == "E_EXPORT_LAYOUT"


def test_access_free_empty_matrix_rank_retains_its_completion():
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "lhs", TensorRole.INPUT, DType.FP32, (0, 3), (3, 1), StorageClass.EXTERNAL, Access.READ_ONLY, 0, 0, None),
        KernelTensor(2, 0, None, 2, 0, "rhs", TensorRole.WEIGHT, DType.FP32, (3, 4), (4, 1), StorageClass.EXTERNAL, Access.READ_ONLY, 48, 48, "2" * 64),
        KernelTensor(3, 1, None, 3, 0, "result", TensorRole.OUTPUT, DType.FP32, (0, 4), (4, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, 0, 0, None),
    )
    shards = (
        TensorShard(1, 1, 1, 3, DistributionKind.PARTITIONED, (0, 0), (0, 3), (0, 3)),
        TensorShard(2, 2, 1, 3, DistributionKind.PARTITIONED, (0, 0), (3, 4), (3, 4)),
        TensorShard(3, 3, 1, 3, DistributionKind.PARTITIONED, (0, 0), (0, 4), (0, 4)),
    )
    tile = KernelTile(0, 0, 0, 0, 1, 0, 4, 3, 1, 0, 4, 3)
    op = KernelOp(1, 1, "empty:matmul", KernelOpcode.GEMM, 3, 3, (), (), GemmKernelAttrs(OpCode.MATMUL, attrs, tile, KernelCost(0, 0, 0, 0, 0, 0), MatrixPhase.DIRECT, 0), (), 1)
    records = KernelMemoryRecords(tensors, (KernelComputation(1, OpCode.MATMUL, (1, 2), 3, attrs),), (Placement(1, (3,)),), shards, (), (), (), (), (ControlToken(1),), (op,))
    assert kernel_op_work_phases(records, 1) == ()


def test_local_copy_domain_counts_actual_destination_pieces_once():
    tensor = KernelTensor(1, 0, None, 1, 0, "local", TensorRole.ACTIVATION, DType.FP32, (8,), (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, 32, 32, None)
    shard = TensorShard(1, 1, 1, 3, DistributionKind.PARTITIONED, (0,), (8,), (8,))
    objects = (
        BufferObject(1, 1, 3, MemorySpace.CORE_SRAM, (8,), (1,), 32, 4, False, 0),
        BufferObject(2, 1, 3, MemorySpace.CORE_SRAM, (8,), (1,), 32, 4, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0,), (8,), (8,), 0, (1,), None, None, 0),
        BufferView(2, 2, 1, (0,), (8,), (8,), 0, (1,), None, None, 0),
    )
    states = (TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED))
    pieces = (ElementRegion((0,), (3,), (1,)), ElementRegion((5,), (3,), (1,)))
    op = KernelOp(
        1,
        0,
        "local-copy",
        KernelOpcode.LOCAL_COPY,
        3,
        0,
        tuple(OperandAccess(1, 1, piece, OperandAccessMode.READ) for piece in pieces),
        tuple(StateTransition(2, 3, 2, piece) for piece in pieces),
        LocalCopyAttrs(),
        (),
        1,
    )
    records = KernelMemoryRecords((tensor,), (), (Placement(1, (3,)),), (shard,), (), objects, views, states, (ControlToken(1),), (op,))
    assert kernel_local_copy_domain(records, 1).elements == 6
    assert _work_by_unit(kernel_op_work_phases(local_copy_kernel().memory_records(), 5)) == {(Engine.VECTOR, WorkUnit.COPY): 4}
    with pytest.raises(MeshIrError) as error:
        kernel_local_copy_domain(dataclasses.replace(records, objects=(dataclasses.replace(objects[0], owner_core=7), objects[1])), 1)
    assert error.value.code == "E_TENSOR_NOT_RESIDENT"
    with pytest.raises(MeshIrError) as error:
        kernel_local_copy_domain(dataclasses.replace(records, ops=(dataclasses.replace(op, reads=op.reads[:1], writes=op.writes[:1] + (op.writes[0],)),)), 1)
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"

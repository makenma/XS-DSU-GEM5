from __future__ import annotations

from mesh_ir.analysis.cost import ElementWorkDomain, ExecutionWorkPhase, MatrixAccumulationWorkDomain, MatrixEpilogueWorkDomain, MatrixWorkDomain, RowWorkDomain, estimate_work_phases
from mesh_ir.canonical import checked_add_u64, checked_mul_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DType, Engine, MemorySpace
from mesh_ir.ir.graph_ir import OpCode, ReduceAttrs, ViewAttrs
from mesh_ir.ir.kernel_ir import BufferObject, BufferView, ElementRegion, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, LocalCopyAttrs, MatrixPhase, OperandAccess, OperandAccessMode, StateTransition, SynthesizedTensorPurpose, TensorShard, TensorState


_GRAPH_COMPUTE_OPS = frozenset((KernelOpcode.GEMM, KernelOpcode.BMM, KernelOpcode.MATRIX_EPILOGUE, KernelOpcode.VECTOR, KernelOpcode.DATA_MOVEMENT, KernelOpcode.REDUCE, KernelOpcode.SOFTMAX, KernelOpcode.NORM))
_NO_COMPUTE_OPS = frozenset((KernelOpcode.ALLOC, KernelOpcode.VIEW, KernelOpcode.DMA, KernelOpcode.RECV_WAIT, KernelOpcode.BARRIER))


def _product(values: tuple[int, ...], field: str) -> int:
    result = 1
    for value in values:
        result = checked_mul_u64(result, value, field)
    return result


def _access(records: KernelMemoryRecords, item):
    expected = OperandAccess if type(item) is OperandAccess else StateTransition if type(item) is StateTransition else None
    state_ids = (item.state_id,) if expected is OperandAccess else (item.old_state_id, item.new_state_id) if expected is StateTransition else ()
    expected_mode = OperandAccessMode.READ if expected is OperandAccess else OperandAccessMode.WRITE
    if expected is None or item.mode is not expected_mode or type(item.view_id) is not int or not 1 <= item.view_id <= len(records.views) or any(type(state_id) is not int or not 1 <= state_id <= len(records.states) for state_id in state_ids):
        raise MeshIrError("E_ABI_BOUNDS", "physical work access reference is invalid")
    view = records.views[item.view_id - 1]
    states = tuple(records.states[state_id - 1] for state_id in state_ids)
    if any(type(state) is not TensorState or type(state.state_id) is not int or state.state_id != state_id or type(state.object_id) is not int or state.object_id < 1 for state, state_id in zip(states, state_ids)) or type(view) is not BufferView or type(view.view_id) is not int or view.view_id != item.view_id or type(view.object_id) is not int or view.object_id < 1 or any(state.object_id != view.object_id for state in states) or type(item.region) is not ElementRegion or type(view.shard_id) is not int or not 1 <= view.shard_id <= len(records.shards):
        raise MeshIrError("E_ABI_BOUNDS", "physical work access view is invalid", view_id=item.view_id)
    region_fields = (item.region.origin, item.region.shape, item.region.steps)
    view_shapes = (view.padded_shape, view.valid_shape)
    if any(type(values) is not tuple for values in (*region_fields, *view_shapes)) or len({len(values) for values in (*region_fields, *view_shapes)}) != 1 or any(type(value) is not int for values in (*region_fields, *view_shapes) for value in values) or any(value < 0 for value in (*item.region.origin, *item.region.shape, *view.padded_shape, *view.valid_shape)) or any(value < 1 for value in item.region.steps) or any(valid > padded for valid, padded in zip(view.valid_shape, view.padded_shape)):
        raise MeshIrError("E_EXPORT_LAYOUT", "physical access rank differs from its resident view", view_id=view.view_id)
    shard = records.shards[view.shard_id - 1]
    if type(shard) is not TensorShard or type(shard.shard_id) is not int or shard.shard_id != view.shard_id or type(shard.tensor_id) is not int or not 1 <= shard.tensor_id <= len(records.tensors) or any(type(values) is not tuple or len(values) != len(item.region.shape) or any(type(value) is not int or value < 0 for value in values) for values in (shard.global_origin, view.shard_origin)):
        raise MeshIrError("E_ABI_BOUNDS", "physical work access shard is invalid", view_id=view.view_id)
    tensor = records.tensors[shard.tensor_id - 1]
    if type(tensor) is not KernelTensor or type(tensor.tensor_id) is not int or tensor.tensor_id != shard.tensor_id or type(tensor.dtype) is not DType or type(tensor.shape) is not tuple or len(tensor.shape) != len(item.region.shape) or any(type(value) is not int or value < 0 for value in tensor.shape):
        raise MeshIrError("E_ABI_BOUNDS", "physical work access tensor is invalid", view_id=view.view_id)
    for origin, extent, step, view_extent in zip(item.region.origin, item.region.shape, item.region.steps, view.valid_shape):
        if extent and checked_add_u64(origin, checked_mul_u64(extent - 1, step, "physical access coordinate"), "physical access coordinate") >= view_extent:
            raise MeshIrError("E_EXPORT_LAYOUT", "physical work access exceeds its resident view", view_id=view.view_id)
    coordinates = tuple(
        (
            checked_add_u64(checked_add_u64(shard.global_origin[index], view.shard_origin[index], "access logical coordinate"), item.region.origin[index], "access logical coordinate"),
            item.region.shape[index],
            item.region.steps[index],
        )
        for index in range(len(item.region.shape))
    )
    if any(extent and checked_add_u64(origin, checked_mul_u64(extent - 1, step, "physical logical coordinate"), "physical logical coordinate") >= tensor.shape[index] for index, (origin, extent, step) in enumerate(coordinates)):
        raise MeshIrError("E_EXPORT_LAYOUT", "physical work access exceeds its logical tensor", view_id=view.view_id)
    return tensor, coordinates


def _axes(values: tuple[int, ...], rank: int, op_id: int) -> tuple[int, ...]:
    axes = tuple(value if value >= 0 else value + rank for value in values)
    if not axes or len(set(axes)) != len(axes) or any(axis < 0 or axis >= rank for axis in axes):
        raise MeshIrError("E_EXPORT_LAYOUT", "physical row operation has invalid semantic axes", op_id=op_id)
    return axes


def _row_domain(records: KernelMemoryRecords, op) -> RowWorkDomain:
    source, source_coordinates = _access(records, op.reads[0])
    if op.opcode is KernelOpcode.SOFTMAX:
        semantic_axes = (op.attrs.semantic_attrs.axis,)
    else:
        semantic_axes = op.attrs.semantic_attrs.axes
    axes = _axes(semantic_axes, len(source.shape), op.op_id)
    for axis in axes:
        origin, extent, step = source_coordinates[axis]
        if origin != 0 or step != 1 or extent != source.shape[axis]:
            raise MeshIrError("E_EXPORT_LAYOUT", "physical row operation splits a semantic axis", op_id=op.op_id, axis=axis)
    rows = _product(tuple(coordinate[1] for index, coordinate in enumerate(source_coordinates) if index not in axes), "physical row count")
    fan_in = _product(tuple(source_coordinates[index][1] for index in axes), "physical row fan-in")
    _, output_coordinates = _access(records, op.writes[0])
    if op.opcode is KernelOpcode.REDUCE:
        expected = tuple((0, 1, 1) if index in axes else source_coordinates[index] for index in range(len(source_coordinates))) if op.attrs.semantic_attrs.keepdim else tuple(source_coordinates[index] for index in range(len(source_coordinates)) if index not in axes)
    else:
        expected = source_coordinates
    if output_coordinates != expected:
        raise MeshIrError("E_EXPORT_LAYOUT", "physical row result coordinates differ from its input rows", op_id=op.op_id)
    if op.opcode is KernelOpcode.NORM:
        normalized = tuple(source_coordinates[index] for index in axes)
        for affine in op.reads[1:]:
            _, affine_coordinates = _access(records, affine)
            if affine_coordinates != normalized:
                raise MeshIrError("E_EXPORT_LAYOUT", "normalization affine access differs from its physical rows", op_id=op.op_id)
    return RowWorkDomain(rows, fan_in)


def _aggregate(phases: tuple[ExecutionWorkPhase, ...]) -> tuple[int, int, int]:
    totals = {Engine.TENSOR: 0, Engine.VECTOR: 0, Engine.REDUCE: 0}
    for phase in phases:
        for item in phase.work:
            totals[item.engine] = checked_add_u64(totals[item.engine], item.operations, "physical operation work")
    return totals[Engine.TENSOR], totals[Engine.VECTOR], totals[Engine.REDUCE]


def _local_reduction_domain_verified(records: KernelMemoryRecords, op) -> RowWorkDomain:
    if not op.writes or len(op.reads) % len(op.writes) or len(op.reads) // len(op.writes) < 2:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "local reduction has invalid physical fan-in", op_id=op.op_id)
    fan_in = len(op.reads) // len(op.writes)
    rows = 0
    for index, transition in enumerate(op.writes):
        output, result_coordinates = _access(records, transition)
        rows = checked_add_u64(rows, _product(transition.region.shape, "local reduction output elements"), "local reduction output elements")
        for item in op.reads[index * fan_in : (index + 1) * fan_in]:
            tensor, coordinates = _access(records, item)
            if tensor.dtype is not output.dtype or coordinates != result_coordinates or item.region.steps != transition.region.steps:
                raise MeshIrError("E_EXPORT_LAYOUT", "local reduction accesses differ from its result region", op_id=op.op_id)
    return RowWorkDomain(rows, fan_in)


def kernel_local_reduction_domain(records: KernelMemoryRecords, op_id: int) -> RowWorkDomain:
    if type(records) is not KernelMemoryRecords or any(type(items) is not tuple for items in (records.tensors, records.shards, records.views, records.states, records.ops)) or type(op_id) is not int or not 1 <= op_id <= len(records.ops):
        raise MeshIrError("E_ABI_BOUNDS", "local reduction operation identifier is invalid", op_id=op_id)
    op = records.ops[op_id - 1]
    if type(op) is not KernelOp or type(op.op_id) is not int or op.op_id != op_id or type(op.reads) is not tuple or type(op.writes) is not tuple or op.opcode is not KernelOpcode.LOCAL_REDUCE:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation is not a local reduction", op_id=op_id)
    return _local_reduction_domain_verified(records, op)


def _local_copy_domain_verified(records: KernelMemoryRecords, op) -> ElementWorkDomain:
    if type(op.attrs) is not LocalCopyAttrs or not op.reads or len(op.reads) != len(op.writes):
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "local copy has invalid physical arity", op_id=op.op_id)
    source_objects = set()
    destination_objects = set()
    elements = 0
    for source, destination in zip(op.reads, op.writes):
        source_tensor, source_coordinates = _access(records, source)
        destination_tensor, destination_coordinates = _access(records, destination)
        source_view = records.views[source.view_id - 1]
        destination_view = records.views[destination.view_id - 1]
        if not 1 <= source_view.object_id <= len(records.objects) or not 1 <= destination_view.object_id <= len(records.objects):
            raise MeshIrError("E_ABI_BOUNDS", "local copy object reference is invalid", op_id=op.op_id)
        source_object = records.objects[source_view.object_id - 1]
        destination_object = records.objects[destination_view.object_id - 1]
        if type(source_object) is not BufferObject or type(destination_object) is not BufferObject or source_object.object_id != source_view.object_id or destination_object.object_id != destination_view.object_id:
            raise MeshIrError("E_ABI_BOUNDS", "local copy object record is invalid", op_id=op.op_id)
        if source_object.memory_space is not MemorySpace.CORE_SRAM or destination_object.memory_space is not MemorySpace.CORE_SRAM or source_object.owner_core != op.owner_core or destination_object.owner_core != op.owner_core:
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "local copy endpoints are not owner-local SRAM", op_id=op.op_id)
        if source_tensor.alias_root_tensor_id != destination_tensor.alias_root_tensor_id or source_tensor.dtype is not destination_tensor.dtype or source_coordinates != destination_coordinates or source.region.steps != destination.region.steps:
            raise MeshIrError("E_EXPORT_LAYOUT", "local copy pieces do not preserve logical coordinates", op_id=op.op_id)
        source_objects.add(source_object.object_id)
        destination_objects.add(destination_object.object_id)
        elements = checked_add_u64(elements, _product(destination.region.shape, "local copy elements"), "local copy elements")
    if len(source_objects) != 1 or len(destination_objects) != 1:
        raise MeshIrError("E_ABI_BOUNDS", "local copy pieces must share one source and destination object", op_id=op.op_id)
    return ElementWorkDomain(elements)


def kernel_local_copy_domain(records: KernelMemoryRecords, op_id: int) -> ElementWorkDomain:
    if type(records) is not KernelMemoryRecords or any(type(items) is not tuple for items in (records.tensors, records.shards, records.objects, records.views, records.states, records.ops)) or type(op_id) is not int or not 1 <= op_id <= len(records.ops):
        raise MeshIrError("E_ABI_BOUNDS", "local copy operation identifier is invalid", op_id=op_id)
    op = records.ops[op_id - 1]
    if type(op) is not KernelOp or type(op.op_id) is not int or op.op_id != op_id or type(op.reads) is not tuple or type(op.writes) is not tuple or op.opcode is not KernelOpcode.LOCAL_COPY:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation is not a local copy", op_id=op_id)
    return _local_copy_domain_verified(records, op)


def _empty_physical_compute_target(records: KernelMemoryRecords, op) -> int:
    if op.opcode not in _GRAPH_COMPUTE_OPS or op.reads or op.writes or op.done_token is None or not hasattr(op.attrs, "cost"):
        return 0
    if type(op.computation_id) is not int or not 1 <= op.computation_id <= len(records.computations) or records.computations[op.computation_id - 1].computation_id != op.computation_id:
        raise MeshIrError("E_ABI_BOUNDS", "empty physical operation references an invalid computation", op_id=op.op_id)
    if type(op.result_shard_id) is not int or not 1 <= op.result_shard_id <= len(records.shards) or records.shards[op.result_shard_id - 1].shard_id != op.result_shard_id:
        raise MeshIrError("E_ABI_BOUNDS", "empty physical operation references an invalid result shard", op_id=op.op_id)
    computation = records.computations[op.computation_id - 1]
    assigned = records.shards[op.result_shard_id - 1]
    target_tensor = records.tensors[assigned.tensor_id - 1]
    target_tensor_id = target_tensor.tensor_id
    if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM) and op.attrs.phase is not MatrixPhase.DIRECT:
        if target_tensor.producer_computation_id != computation.computation_id or target_tensor.synthesized_purpose not in (SynthesizedTensorPurpose.ACCUMULATION, SynthesizedTensorPurpose.PARTIAL_SUM):
            return 0
    elif target_tensor_id != computation.result_tensor_id:
        return 0
    cost = op.attrs.cost
    tile = op.attrs.tile
    if assigned.owner_core != op.owner_core or _product(assigned.valid_shape, "empty physical shard"):
        return 0
    if checked_mul_u64(checked_mul_u64(tile.valid_batch, tile.valid_m, "empty physical tile"), tile.valid_n, "empty physical tile"):
        return 0
    if any(getattr(cost, field) for field in ("logical_input_bytes", "logical_output_bytes", "local_storage_bytes", "macs", "vector_ops", "reduction_ops")):
        return 0
    return target_tensor_id


def _kernel_op_work_phases_verified(records: KernelMemoryRecords, op_id: int) -> tuple[ExecutionWorkPhase, ...]:
    op = records.ops[op_id - 1]
    if op.opcode in _NO_COMPUTE_OPS:
        return ()
    if op.opcode is KernelOpcode.COLLECTIVE:
        raise MeshIrError("E_CAPABILITY_MISMATCH", "abstract collective has no physical execution work", op_id=op_id)
    if _empty_physical_compute_target(records, op):
        return ()
    if op.opcode is KernelOpcode.LOCAL_REDUCE:
        result, _ = _access(records, op.writes[0])
        domain = _local_reduction_domain_verified(records, op)
        attrs = ReduceAttrs((1,), False, result.dtype, result.dtype)
        phases = estimate_work_phases(OpCode.REDUCE_SUM, attrs, (result.dtype,), result.dtype, domain)
    elif op.opcode is KernelOpcode.LOCAL_COPY:
        result, _ = _access(records, op.writes[0])
        domain = _local_copy_domain_verified(records, op)
        phases = estimate_work_phases(OpCode.CONTIGUOUS_COPY, ViewAttrs(), (result.dtype,), result.dtype, domain)
    elif op.opcode in _GRAPH_COMPUTE_OPS:
        computation = records.computations[op.computation_id - 1]
        operand_dtypes = tuple(records.tensors[tensor_id - 1].dtype for tensor_id in computation.operand_tensor_ids)
        result_dtype = records.tensors[computation.result_tensor_id - 1].dtype
        tile = op.attrs.tile
        if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM):
            if op.attrs.phase is MatrixPhase.DIRECT:
                domain = MatrixWorkDomain(tile.valid_batch, tile.valid_m, tile.valid_n, tile.valid_k)
            else:
                operand_dtypes = operand_dtypes[:2]
                result_dtype = records.tensors[records.shards[records.views[op.writes[0].view_id - 1].shard_id - 1].tensor_id - 1].dtype
                domain = MatrixAccumulationWorkDomain(tile.valid_batch, tile.valid_m, tile.valid_n, tile.valid_k)
        elif op.opcode is KernelOpcode.MATRIX_EPILOGUE:
            domain = MatrixEpilogueWorkDomain(tile.valid_batch, tile.valid_m, tile.valid_n)
        elif op.opcode in (KernelOpcode.REDUCE, KernelOpcode.SOFTMAX, KernelOpcode.NORM):
            domain = _row_domain(records, op)
        else:
            domain = ElementWorkDomain(_product(op.writes[0].region.shape, "physical output elements"))
        phases = estimate_work_phases(computation.opcode, computation.attrs, operand_dtypes, result_dtype, domain)
    else:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "Kernel opcode has no physical work projection", op_id=op_id, opcode=op.opcode.value)
    if hasattr(op.attrs, "cost") and _aggregate(phases) != (op.attrs.cost.macs, op.attrs.cost.vector_ops, op.attrs.cost.reduction_ops):
        raise MeshIrError("E_EXPORT_LAYOUT", "Kernel work cost differs from physical execution phases", op_id=op_id)
    return phases


def kernel_op_work_phases(records: KernelMemoryRecords, op_id: int) -> tuple[ExecutionWorkPhase, ...]:
    if type(op_id) is not int or op_id < 1:
        raise MeshIrError("E_ABI_BOUNDS", "physical work operation identifier is invalid")
    from mesh_ir.ir.kernel_verify import verify_kernel_memory

    verified = verify_kernel_memory(records)
    if op_id > len(verified.records.ops):
        raise MeshIrError("E_ABI_BOUNDS", "physical work operation is absent", op_id=op_id)
    return _kernel_op_work_phases_verified(verified.records, op_id)


def kernel_work_phases(records: KernelMemoryRecords) -> tuple[tuple[ExecutionWorkPhase, ...], ...]:
    from mesh_ir.ir.kernel_verify import verify_kernel_memory

    verified = verify_kernel_memory(records)
    return tuple(_kernel_op_work_phases_verified(verified.records, op.op_id) for op in verified.records.ops)


__all__ = ["kernel_local_copy_domain", "kernel_local_reduction_domain", "kernel_op_work_phases", "kernel_work_phases"]

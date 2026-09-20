from __future__ import annotations

from mesh_ir.analysis.kernel_work import _empty_physical_compute_target, kernel_local_copy_domain, kernel_local_reduction_domain
from mesh_ir.architecture import ArchManifest
from mesh_ir.canonical import checked_mul_u64, checked_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import DType, DmaKind
from mesh_ir.ir.kernel_ir import BufferObject, DmaAttrs, ElementRegion, KERNEL_ATTR_TYPE_BY_OPCODE, KernelMemoryRecords, KernelOp, KernelOpcode
from mesh_ir.model import OpAttr, Relocation
from mesh_ir.scheduled.model import AxiFenceAttrs, EventSignalAttrs, EventWaitAttrs, FenceScope, HaltAttrs, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.traffic import BindingSlot


_KERNEL_OPCODE_TO_ABI = {
    KernelOpcode.GEMM: A.OPCODE.GEMM,
    KernelOpcode.BMM: A.OPCODE.BMM,
    KernelOpcode.MATRIX_EPILOGUE: A.OPCODE.ELEMENTWISE,
    KernelOpcode.VECTOR: A.OPCODE.ELEMENTWISE,
    KernelOpcode.DATA_MOVEMENT: A.OPCODE.ELEMENTWISE,
    KernelOpcode.LOCAL_COPY: A.OPCODE.ELEMENTWISE,
    KernelOpcode.REDUCE: A.OPCODE.LOCAL_REDUCE,
    KernelOpcode.SOFTMAX: A.OPCODE.SOFTMAX,
    KernelOpcode.NORM: A.OPCODE.NORM,
    KernelOpcode.LOCAL_REDUCE: A.OPCODE.LOCAL_REDUCE,
    KernelOpcode.RECV_WAIT: A.OPCODE.RECV_WAIT,
    KernelOpcode.BARRIER: A.OPCODE.BARRIER,
}

_DMA_KIND_TO_ABI = {
    DmaKind.LOAD: A.OPCODE.DMA_LOAD,
    DmaKind.STORE: A.OPCODE.DMA_STORE,
    DmaKind.P2P_PUSH: A.OPCODE.DMA_P2P_PUSH,
    DmaKind.PREFETCH: A.OPCODE.DMA_PREFETCH,
    DmaKind.LOCAL_FILL: A.OPCODE.DMA_FILL,
}


def effective_dma_burst_beats(attrs: DmaAttrs, arch: ArchManifest) -> int:
    if type(attrs) is not DmaAttrs or type(arch) is not ArchManifest:
        raise MeshIrError("E_ABI_BOUNDS", "DMA burst projection requires exact source and architecture records")
    limit = attrs.max_burst_beats
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 256):
        raise MeshIrError("E_ABI_BOUNDS", "DMA burst limit is outside the supported range")
    if attrs.kind is DmaKind.LOCAL_FILL and limit is not None:
        raise MeshIrError("E_DMA_RANGE", "local fill cannot declare an AXI burst limit")
    if limit is not None and limit > arch.axi_max_burst_beats:
        raise MeshIrError("E_CAPABILITY_MISMATCH", "DMA burst limit exceeds the architecture ceiling")
    return arch.axi_max_burst_beats if limit is None else limit


def empty_dma_row_geometry(region: ElementRegion, dtype: DType) -> tuple[int, int]:
    if type(region) is not ElementRegion or type(dtype) is not DType or type(region.origin) is not tuple or type(region.shape) is not tuple or type(region.steps) is not tuple or not region.shape or not len(region.origin) == len(region.shape) == len(region.steps):
        raise MeshIrError("E_ABI_BOUNDS", "empty DMA geometry requires an exact typed region and dtype")
    if any(type(value) is not int for values in (region.origin, region.shape, region.steps) for value in values):
        raise MeshIrError("E_ABI_BOUNDS", "empty DMA region vectors require exact integers")
    for field, values in (("origin", region.origin), ("shape", region.shape), ("steps", region.steps)):
        for value in values:
            checked_u64(value, f"empty DMA region {field}")
    if any(step < 1 for step in region.steps):
        raise MeshIrError("E_ABI_BOUNDS", "empty DMA region steps must be positive")
    if all(extent > 0 for extent in region.shape):
        raise MeshIrError("E_DMA_RANGE", "empty DMA geometry requires an empty region")
    rows = 1
    for extent in region.shape[:-1]:
        rows = checked_mul_u64(rows, extent, "empty DMA rows")
    row_bytes = checked_mul_u64(region.shape[-1], dtype.byte_width, "empty DMA row bytes")
    return rows, row_bytes


def abi_opcode_for_kernel_op(op: KernelOp) -> int:
    if type(op) is not KernelOp or type(op.opcode) is not KernelOpcode or op.opcode in (KernelOpcode.ALLOC, KernelOpcode.VIEW):
        raise MeshIrError("E_ABI_ENUM", "Kernel operation has no direct ABI opcode projection")
    if op.opcode is KernelOpcode.DMA:
        if type(op.attrs) is not DmaAttrs:
            raise MeshIrError("E_ABI_ENUM", "DMA operation has invalid attributes")
        opcode = _DMA_KIND_TO_ABI.get(op.attrs.kind)
        if opcode is None:
            raise MeshIrError("E_ABI_ENUM", "DMA operation has an invalid kind")
        return opcode
    opcode = _KERNEL_OPCODE_TO_ABI.get(op.opcode)
    if opcode is None:
        raise MeshIrError("E_ABI_ENUM", "Kernel operation family has no ABI opcode projection", kernel_opcode=op.opcode.value)
    return opcode


def abi_projection_for_reference_relocation(
    obj: BufferObject,
    slot: BindingSlot,
    symbol_sid: int,
) -> Relocation:
    if (
        type(obj) is not BufferObject
        or type(slot) is not BindingSlot
        or type(symbol_sid) is not int
        or symbol_sid < 1
    ):
        raise MeshIrError(
            "E_ABI_BOUNDS",
            "reference relocation projection requires exact source records",
        )
    return Relocation(
        slot.slot_id,
        symbol_sid,
        A.RELOCATION_KIND.TENSOR_BASE,
        slot.region_id,
        obj.storage_tensor_id,
        0,
        slot.reference_binding.allocation_offset_bytes,
        0,
    )


def _elements(shape: tuple[int, ...]) -> int:
    result = 1
    for extent in shape:
        result = checked_mul_u64(result, extent, "ABI operation elements")
    return result


def _record(kind: int, values: tuple[int, ...]) -> OpAttr:
    payload_name = A.PAYLOAD_BY_KIND[kind]
    fields = tuple(item["name"] for item in getattr(A, f"{payload_name}_FIELDS"))
    return OpAttr(kind, 0, values, fields)


def abi_projection_for_control(attrs) -> tuple[int, OpAttr | None]:
    opcode = {
        RequestBeginAttrs: A.OPCODE.REQUEST_BEGIN,
        RequestEndAttrs: A.OPCODE.REQUEST_END,
        HaltAttrs: A.OPCODE.HALT,
        EventWaitAttrs: A.OPCODE.EVENT_WAIT,
        EventSignalAttrs: A.OPCODE.EVENT_SIGNAL,
        RepeatCommandAttrs: A.OPCODE.REPEAT,
        AxiFenceAttrs: A.OPCODE.AXI_FENCE,
    }.get(type(attrs))
    if opcode is None:
        raise MeshIrError("E_ABI_ENUM", "control command attributes are invalid")
    if type(attrs) is RepeatCommandAttrs:
        values = (attrs.subrange_begin_stream_ordinal, attrs.subrange_command_count, attrs.repeat_count)
        if any(type(value) is not int for value in values) or not 0 <= values[0] <= 0xFFFFFFFF or not 1 <= values[1] <= 0xFFFFFFFF or not 1 <= values[2] <= 0xFFFFFFFF:
            raise MeshIrError("E_ABI_BOUNDS", "REPEAT command fields are invalid")
        return opcode, _record(A.ATTR_KIND.REPEAT_V1, values + (0,))
    if type(attrs) is AxiFenceAttrs:
        if type(attrs.scope) is not FenceScope:
            raise MeshIrError("E_ABI_ENUM", "AXI fence scope is invalid")
        scope = {
            FenceScope.DMA_READ: A.FENCE_SCOPE.DMA_READ,
            FenceScope.DMA_WRITE: A.FENCE_SCOPE.DMA_WRITE,
            FenceScope.P2P: A.FENCE_SCOPE.P2P,
            FenceScope.HOST_SHARED_WRITE: A.FENCE_SCOPE.HOST_SHARED_WRITE,
            FenceScope.ALL_INSTANCE: A.FENCE_SCOPE.ALL_INSTANCE,
        }[attrs.scope]
        return opcode, _record(A.ATTR_KIND.FENCE_V1, (scope, 0, 0, 0, 0))
    if type(attrs) in (EventWaitAttrs, EventSignalAttrs) and (type(attrs.event_id) is not int or not 1 <= attrs.event_id <= 0xFFFFFFFF):
        raise MeshIrError("E_ABI_BOUNDS", "event control identity is invalid")
    return opcode, None


def abi_attr_for_kernel_op(op: KernelOp, records: KernelMemoryRecords) -> OpAttr | None:
    if type(op) is not KernelOp or type(records) is not KernelMemoryRecords:
        raise MeshIrError("E_ABI_BOUNDS", "operation attribute projection requires exact Kernel records")
    if type(op.opcode) is not KernelOpcode:
        raise MeshIrError("E_ABI_ENUM", "Kernel opcode projection requires an exact enum")
    expected_attrs = KERNEL_ATTR_TYPE_BY_OPCODE.get(op.opcode)
    if expected_attrs is None or type(op.attrs) is not expected_attrs:
        raise MeshIrError("E_ABI_ENUM", "Kernel opcode has the wrong projection attributes")
    views = {item.view_id: item for item in records.views}
    shards = {item.shard_id: item for item in records.shards}
    tensors = {item.tensor_id: item for item in records.tensors}
    empty_target = _empty_physical_compute_target(records, op)
    computation = records.computations[op.computation_id - 1] if empty_target else None
    logical_input_dtype = int(tensors[computation.operand_tensor_ids[0]].dtype) if computation is not None else 0
    target_dtype = int(tensors[empty_target].dtype) if empty_target else 0

    def dtype(access) -> int:
        return int(tensors[shards[views[access.view_id].shard_id].tensor_id].dtype)

    if op.opcode is KernelOpcode.DMA:
        if op.attrs.kind is DmaKind.LOCAL_FILL:
            if len(op.attrs.fill_pattern) > 8:
                raise MeshIrError("E_ABI_BOUNDS", "DMA fill pattern exceeds the ABI projection")
            return _record(A.ATTR_KIND.FILL_V1, (int.from_bytes(op.attrs.fill_pattern, "little"), 0))
        return None
    if op.opcode is KernelOpcode.RECV_WAIT:
        return _record(A.ATTR_KIND.RECV_WAIT_V1, (op.attrs.transfer_id, 0, 0, 0))
    if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM):
        if not empty_target and len(op.reads) < 2:
            raise MeshIrError("E_ABI_ENUM", "matrix operation attributes are invalid")
        tile = op.attrs.tile
        kind = A.ATTR_KIND.GEMM_V1 if op.opcode is KernelOpcode.GEMM else A.ATTR_KIND.BMM_V1
        domain = (tile.valid_batch, tile.valid_m, tile.valid_n, tile.valid_k) if empty_target else (tile.batch_extent, tile.m_extent, tile.n_extent, tile.k_extent)
        return _record(kind, (*domain, int(op.attrs.semantic_attrs.lhs_transpose), int(op.attrs.semantic_attrs.rhs_transpose), logical_input_dtype if empty_target else dtype(op.reads[0]), int(op.attrs.semantic_attrs.accum_dtype), 0, 65536))
    if op.opcode is KernelOpcode.LOCAL_COPY:
        domain = kernel_local_copy_domain(records, op.op_id)
        return _record(A.ATTR_KIND.ELEMENTWISE_V1, (domain.elements, dtype(op.writes[0]), 0, 1, 0))
    if op.opcode in (KernelOpcode.MATRIX_EPILOGUE, KernelOpcode.VECTOR, KernelOpcode.DATA_MOVEMENT):
        if not empty_target and not op.writes:
            raise MeshIrError("E_ABI_ENUM", "vector operation has no result region")
        count = 0 if empty_target else _elements(op.writes[0].region.shape)
        cost = getattr(op.attrs, "cost", None)
        vector_ops = 0 if cost is None else cost.vector_ops
        divisor = max(count, 1)
        per_element = max(1, (vector_ops + divisor - 1) // divisor)
        return _record(A.ATTR_KIND.ELEMENTWISE_V1, (count, target_dtype if empty_target else dtype(op.writes[0]), 0, per_element, 0))
    if op.opcode in (KernelOpcode.REDUCE, KernelOpcode.LOCAL_REDUCE):
        if not empty_target and not op.reads:
            raise MeshIrError("E_ABI_ENUM", "reduction operation has no input region")
        input_dtype = logical_input_dtype if empty_target else dtype(op.reads[0])
        accum_dtype = input_dtype
        if op.opcode is KernelOpcode.REDUCE:
            accum_dtype = int(op.attrs.semantic_attrs.accum_dtype)
        if empty_target:
            rows = 0
            fan_in = 2
        elif op.opcode is KernelOpcode.LOCAL_REDUCE:
            domain = kernel_local_reduction_domain(records, op.op_id)
            rows = domain.rows
            fan_in = domain.fan_in
        else:
            rows = _elements(op.reads[0].region.shape)
            fan_in = 2
        return _record(A.ATTR_KIND.REDUCE_V1, (rows, input_dtype, accum_dtype, 0, fan_in))
    if op.opcode is KernelOpcode.SOFTMAX:
        if not empty_target and not op.reads:
            raise MeshIrError("E_ABI_ENUM", "softmax operation attributes are invalid")
        if empty_target:
            return _record(A.ATTR_KIND.SOFTMAX_V1, (0, logical_input_dtype, A.VECTOR_ALGORITHM.STANDARD, 0))
        shape = op.reads[0].region.shape
        axis = op.attrs.semantic_attrs.axis
        normalized = axis if axis >= 0 else len(shape) + axis
        if not 0 <= normalized < len(shape):
            raise MeshIrError("E_ABI_BOUNDS", "softmax axis is outside its physical region")
        return _record(A.ATTR_KIND.SOFTMAX_V1, (shape[normalized], dtype(op.reads[0]), A.VECTOR_ALGORITHM.STANDARD, 0))
    if op.opcode is KernelOpcode.NORM:
        if not empty_target and not op.writes:
            raise MeshIrError("E_ABI_ENUM", "normalization operation attributes are invalid")
        return _record(A.ATTR_KIND.NORM_V1, (0 if empty_target else _elements(op.writes[0].region.shape), target_dtype if empty_target else dtype(op.writes[0]), A.VECTOR_ALGORITHM.STANDARD, 0))
    return None


__all__ = ["abi_attr_for_kernel_op", "abi_opcode_for_kernel_op", "abi_projection_for_control", "abi_projection_for_reference_relocation", "effective_dma_burst_beats", "empty_dma_row_geometry"]

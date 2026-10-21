from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.analysis.regions import compact_ordered_affine_axes
from mesh_ir.canonical import checked_add_u64, checked_mul_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DType, DmaKind, INVALID_CORE_ID, MemorySpace
from mesh_ir.ir.kernel_ir import DmaAttrs, KernelMemoryRecords, KernelOp, KernelOpcode


@dataclass(frozen=True)
class GatherGeometry:
    rows: int
    row_bytes: int
    table_rows: int
    source_stride_bytes: int
    destination_stride_bytes: int
    index_offset_bytes: int
    index_stride_bytes: int
    table_span_bytes: int
    destination_span_bytes: int

    @property
    def useful_bytes(self) -> int:
        return checked_mul_u64(self.rows, self.row_bytes, "gather useful bytes")

    @property
    def physical_storage_bytes(self) -> int:
        return max(self.table_span_bytes, self.destination_span_bytes) if self.rows else 0

    @property
    def descriptor_geometry(self) -> tuple[int, ...]:
        return (self.rows, self.row_bytes, self.source_stride_bytes,
                self.destination_stride_bytes, self.useful_bytes, self.physical_storage_bytes)

    @classmethod
    def from_kernel(cls, records: KernelMemoryRecords, op: KernelOp) -> GatherGeometry:
        if op.opcode is not KernelOpcode.DMA or type(op.attrs) is not DmaAttrs or op.attrs.kind is not DmaKind.GATHER_LOAD:
            raise MeshIrError("E_DMA_RANGE", "gather geometry requires a gather DMA", op_id=op.op_id)
        attrs = op.attrs
        if len(op.reads) != 2 or len(op.writes) != 1:
            raise MeshIrError("E_DMA_RANGE", "gather requires index and table reads and one destination", op_id=op.op_id)
        if attrs.fill_pattern or attrs.transfer_id or attrs.source_core != INVALID_CORE_ID or attrs.destination_core != attrs.issuing_core or op.owner_core != attrs.issuing_core:
            raise MeshIrError("E_DMA_RANGE", "gather core identities or attributes are invalid", op_id=op.op_id)
        views = {item.view_id: item for item in records.views}
        objects = {item.object_id: item for item in records.objects}
        shards = {item.shard_id: item for item in records.shards}
        tensors = {item.tensor_id: item for item in records.tensors}
        index, source = op.reads
        destination = op.writes[0]
        index_view, source_view, destination_view = (views[item.view_id] for item in (index, source, destination))
        index_object, source_object, destination_object = (objects[item.object_id] for item in (index_view, source_view, destination_view))
        index_tensor, source_tensor, destination_tensor = (tensors[shards[item.shard_id].tensor_id] for item in (index_view, source_view, destination_view))
        if any(item.memory_space is not MemorySpace.CORE_SRAM or item.owner_core != attrs.issuing_core for item in (index_object, destination_object)):
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "gather index and destination must be owner-local SRAM", op_id=op.op_id)
        if source_object.memory_space not in (MemorySpace.HBM, MemorySpace.HOST_SHARED):
            raise MeshIrError("E_DMA_RANGE", "gather table must be external", op_id=op.op_id)
        if index_tensor.dtype is not DType.INT32 or source_tensor.dtype is not destination_tensor.dtype:
            raise MeshIrError("E_EXPORT_DTYPE", "gather requires INT32 indices and matching table and destination dtypes", op_id=op.op_id)
        if len(source.region.shape) != 2 or source.region.origin != (0, 0) or source.region.steps != (1, 1) or source.region.shape != source_view.valid_shape or source_view.valid_shape != source_tensor.shape:
            raise MeshIrError("E_DMA_RANGE", "gather source must cover the complete logical table", op_id=op.op_id)
        if source_view.object_strides[1] != 1 or source_view.object_strides[0] < source.region.shape[1]:
            raise MeshIrError("E_EXPORT_LAYOUT", "gather table requires contiguous nonoverlapping rows", op_id=op.op_id)
        if len(destination.region.shape) != 2 or destination.region.shape[1] != source.region.shape[1] or destination.region.steps != (1, 1) or destination_view.object_strides[1] != 1 or destination_view.object_strides[0] < destination.region.shape[1]:
            raise MeshIrError("E_EXPORT_LAYOUT", "gather destination must contain complete contiguous rows", op_id=op.op_id)
        index_count = 1
        for extent in index.region.shape:
            index_count = checked_mul_u64(index_count, extent, "gather index count")
        rows = destination.region.shape[0]
        if index_count != rows:
            raise MeshIrError("E_DMA_RANGE", "gather index count differs from destination rows", op_id=op.op_id)
        index_strides = tuple(checked_mul_u64(step, stride, "gather index element stride") for step, stride in zip(index.region.steps, index_view.object_strides))
        axes = compact_ordered_affine_axes(index.region.shape, index_strides)
        if len(axes) > 1:
            raise MeshIrError("E_EXPORT_LAYOUT", "gather index traversal requires a constant stride", op_id=op.op_id)
        index_stride = axes[0][1] if axes else 1
        offset = 0
        for origin, stride in zip(index.region.origin, index_view.object_strides):
            offset = checked_add_u64(offset, checked_mul_u64(origin, stride, "gather index origin"), "gather index offset")
        index_offset_bytes = checked_mul_u64(offset, 4, "gather index offset bytes")
        index_stride_bytes = checked_mul_u64(index_stride, 4, "gather index stride bytes")
        width = source_tensor.dtype.byte_width
        row_bytes = checked_mul_u64(source.region.shape[1], width, "gather row bytes")
        source_stride = checked_mul_u64(source_view.object_strides[0], width, "gather table row stride")
        destination_stride = checked_mul_u64(destination_view.object_strides[0], width, "gather destination row stride")
        table_rows = source.region.shape[0]
        table_span = checked_add_u64(checked_mul_u64(table_rows - 1, source_stride, "gather table row displacement"), row_bytes, "gather table span") if table_rows and row_bytes else 0
        destination_span = checked_add_u64(checked_mul_u64(rows - 1, destination_stride, "gather destination displacement"), row_bytes, "gather destination span") if rows and row_bytes else 0
        return cls(rows, row_bytes, table_rows, source_stride, destination_stride,
                   index_offset_bytes, index_stride_bytes, table_span, destination_span)


def verify_staged_gather_result(records: KernelMemoryRecords, op: KernelOp) -> None:
    if len(op.reads) != 1 or len(op.writes) != 1:
        raise MeshIrError("E_DMA_RANGE", "staged gather result requires one ordered read and write", op_id=op.op_id)
    access = op.reads[0]
    producers = tuple(item for item in records.ops
                      if any(write.new_state_id == access.state_id for write in item.writes))
    if len(producers) != 1:
        raise MeshIrError("E_TENSOR_NOT_RESIDENT", "staged gather result lacks a unique producer", op_id=op.op_id)
    producer = producers[0]
    geometry = GatherGeometry.from_kernel(records, producer)
    staged = producer.writes[0]
    if staged.view_id != access.view_id or staged.region != access.region:
        raise MeshIrError("E_DMA_RANGE", "staged gather result must consume every produced occurrence in order", op_id=op.op_id)
    views = {item.view_id: item for item in records.views}
    shards = {item.shard_id: item for item in records.shards}
    tensors = {item.tensor_id: item for item in records.tensors}
    computation = next(item for item in records.computations if item.computation_id == op.computation_id)
    producer_operands = tuple(shards[views[item.view_id].shard_id].tensor_id for item in reversed(producer.reads))
    target_tensor = tensors[shards[views[op.writes[0].view_id].shard_id].tensor_id]
    if producer_operands != computation.operand_tensor_ids or target_tensor.tensor_id != computation.result_tensor_id:
        raise MeshIrError("E_ABI_CHECKSUM", "gather producer differs from embedding computation", op_id=op.op_id)
    expected_shape = producer.reads[0].region.shape + (geometry.row_bytes // target_tensor.dtype.byte_width,)
    if op.writes[0].region.shape != expected_shape:
        raise MeshIrError("E_EXPORT_LAYOUT", "staged gather result shape differs from index shape and table row", op_id=op.op_id)

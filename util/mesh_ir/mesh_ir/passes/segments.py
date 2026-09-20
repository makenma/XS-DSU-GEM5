from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.analysis.regions import compact_ordered_affine_axes
from mesh_ir.canonical import checked_add_u64, checked_mul_u64, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import ElementRegion, KernelOpcode
from mesh_ir.passes.schedule import _ScheduleState
from mesh_ir.scheduled.model import KernelCommandSource
from mesh_ir.scheduled.projection import empty_dma_row_geometry


@dataclass(frozen=True)
class _AddressParameterizedSegment:
    variant_ordinal: int
    kernel_op_id: int
    segment_ordinal: int
    source_access_index: int
    destination_access_index: int
    source_region: ElementRegion
    destination_region: ElementRegion
    rows: int
    row_bytes: int
    source_stride_bytes: int
    destination_stride_bytes: int
    useful_bytes: int
    physical_storage_bytes: int


@dataclass(frozen=True)
class _SegmentState:
    schedule: _ScheduleState
    segments: tuple[_AddressParameterizedSegment, ...]

    @property
    def semantic_sha256(self) -> str:
        return semantic_sha256(self)


def _access_geometry(records, access):
    view = records.views[access.view_id - 1]
    shard = records.shards[view.shard_id - 1]
    tensor = records.tensors[shard.tensor_id - 1]
    region = access.region
    if any(type(value) is not int for values in (region.origin, region.shape, region.steps) for value in values) or len(region.shape) != len(view.object_strides):
        raise MeshIrError("E_EXPORT_LAYOUT", "DMA region has invalid affine rank")
    if len(region.origin) != len(region.shape) or len(region.steps) != len(region.shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "DMA region vectors have different ranks")
    return view, tensor, region


def _element_count(shape: tuple[int, ...], label: str) -> int:
    count = 1
    for extent in shape:
        count = checked_mul_u64(count, extent, label)
    return count


def _constant_sequence_delta(shape: tuple[int, ...], byte_strides: tuple[int, ...], label: str) -> int | None:
    axes = compact_ordered_affine_axes(shape, byte_strides) if shape else ()
    return 0 if not axes else axes[0][1] if len(axes) == 1 else None


def _row_regions(region: ElementRegion):
    rank = len(region.shape)
    if rank == 0:
        yield region, 1
        return
    outer_count = _element_count(region.shape[:-1], "DMA outer rows")
    for linear in range(outer_count):
        remainder = linear
        indices = [0] * (rank - 1)
        for axis in range(rank - 2, -1, -1):
            indices[axis] = remainder % region.shape[axis]
            remainder //= region.shape[axis]
        origin = tuple(checked_add_u64(region.origin[axis], checked_mul_u64(indices[axis], region.steps[axis], "DMA row coordinate"), "DMA row coordinate") if axis < rank - 1 else region.origin[axis] for axis in range(rank))
        yield ElementRegion(origin, (1,) * (rank - 1) + (region.shape[-1],), region.steps), region.shape[-1]


def _slice_row(region: ElementRegion, consumed: int, count: int) -> ElementRegion:
    if not region.shape:
        return region
    origin = region.origin[:-1] + (checked_add_u64(region.origin[-1], checked_mul_u64(consumed, region.steps[-1], "DMA row slice"), "DMA row slice"),)
    return ElementRegion(origin, region.shape[:-1] + (count,), region.steps)


def _paired_geometry(records, source, destination):
    src_view, src_tensor, src_region = _access_geometry(records, source)
    dst_view, dst_tensor, dst_region = _access_geometry(records, destination)
    if src_tensor.dtype.byte_width != dst_tensor.dtype.byte_width:
        raise MeshIrError("E_DMA_RANGE", "DMA source and destination element widths differ")
    source_count = _element_count(src_region.shape, "DMA source elements")
    destination_count = _element_count(dst_region.shape, "DMA destination elements")
    if source_count != destination_count:
        raise MeshIrError("E_DMA_RANGE", "DMA source and destination element counts differ")
    width = src_tensor.dtype.byte_width
    if source_count == 0:
        source_geometry = empty_dma_row_geometry(src_region, src_tensor.dtype)
        destination_geometry = empty_dma_row_geometry(dst_region, dst_tensor.dtype)
        if source_geometry != destination_geometry:
            raise MeshIrError("E_DMA_RANGE", "empty DMA source and destination row geometry differ")
        rows, row_bytes = source_geometry
        return ((src_region, dst_region, rows, row_bytes, row_bytes, row_bytes, 0),)
    if source_count == 1:
        return ((src_region, dst_region, 1, width, width, width, width),)
    src_byte_strides = tuple(checked_mul_u64(checked_mul_u64(step, stride, "DMA source element stride"), width, "DMA source byte stride") for step, stride in zip(src_region.steps, src_view.object_strides))
    dst_byte_strides = tuple(checked_mul_u64(checked_mul_u64(step, stride, "DMA destination element stride"), width, "DMA destination byte stride") for step, stride in zip(dst_region.steps, dst_view.object_strides))
    src_delta = _constant_sequence_delta(src_region.shape, src_byte_strides, "DMA source affine sequence")
    dst_delta = _constant_sequence_delta(dst_region.shape, dst_byte_strides, "DMA destination affine sequence")
    if src_delta is not None and dst_delta is not None:
        if src_delta == width and dst_delta == width:
            row_bytes = checked_mul_u64(source_count, width, "DMA compact bytes")
            return ((src_region, dst_region, 1, row_bytes, row_bytes, row_bytes, row_bytes),)
        if src_delta >= width and dst_delta >= width:
            span = max(checked_add_u64(checked_mul_u64(source_count - 1, src_delta, "DMA source displacement"), width, "DMA source span"), checked_add_u64(checked_mul_u64(source_count - 1, dst_delta, "DMA destination displacement"), width, "DMA destination span"))
            return ((src_region, dst_region, source_count, width, src_delta, dst_delta, span),)
    if src_region.shape and dst_region.shape and src_region.shape[-1] == dst_region.shape[-1] and src_byte_strides[-1] == width and dst_byte_strides[-1] == width:
        rows = source_count // src_region.shape[-1]
        src_outer_delta = _constant_sequence_delta(src_region.shape[:-1], src_byte_strides[:-1], "DMA source row sequence")
        dst_outer_delta = _constant_sequence_delta(dst_region.shape[:-1], dst_byte_strides[:-1], "DMA destination row sequence")
        row_bytes = checked_mul_u64(src_region.shape[-1], width, "DMA row bytes")
        src_stride = row_bytes if rows == 1 else src_outer_delta
        dst_stride = row_bytes if rows == 1 else dst_outer_delta
        if src_stride is not None and dst_stride is not None and src_stride >= row_bytes and dst_stride >= row_bytes:
            span = max(checked_add_u64(checked_mul_u64(rows - 1, src_stride, "DMA source row displacement"), row_bytes, "DMA source span"), checked_add_u64(checked_mul_u64(rows - 1, dst_stride, "DMA destination row displacement"), row_bytes, "DMA destination span"))
            return ((src_region, dst_region, rows, row_bytes, src_stride, dst_stride, span),)
    source_rows = iter(_row_regions(src_region))
    destination_rows = iter(_row_regions(dst_region))
    src_row, src_remaining = next(source_rows)
    dst_row, dst_remaining = next(destination_rows)
    src_consumed = 0
    dst_consumed = 0
    chunks = []
    completed = 0
    while completed < source_count:
        count = min(src_remaining, dst_remaining)
        src = _slice_row(src_row, src_consumed, count)
        dst = _slice_row(dst_row, dst_consumed, count)
        src_stride = src_byte_strides[-1] if src_byte_strides else width
        dst_stride = dst_byte_strides[-1] if dst_byte_strides else width
        if src_stride == width and dst_stride == width:
            row_bytes = checked_mul_u64(count, width, "DMA compact row bytes")
            rows = 1
            src_stride = row_bytes
            dst_stride = row_bytes
        else:
            rows = count
            row_bytes = width
        span = max(checked_add_u64(checked_mul_u64(rows - 1, src_stride, "DMA source chunk displacement"), row_bytes, "DMA source chunk span"), checked_add_u64(checked_mul_u64(rows - 1, dst_stride, "DMA destination chunk displacement"), row_bytes, "DMA destination chunk span"))
        chunks.append((src, dst, rows, row_bytes, src_stride, dst_stride, span))
        completed += count
        src_remaining -= count
        dst_remaining -= count
        src_consumed += count
        dst_consumed += count
        if src_remaining == 0 and completed < source_count:
            src_row, src_remaining = next(source_rows)
            src_consumed = 0
        if dst_remaining == 0 and completed < destination_count:
            dst_row, dst_remaining = next(destination_rows)
            dst_consumed = 0
    return tuple(chunks)

def lower_dma_to_segments_stage(schedule: _ScheduleState) -> _SegmentState:
    if type(schedule) is not _ScheduleState:
        raise MeshIrError("E_ABI_BOUNDS", "pass 18 requires the exact schedule state")
    segments = []
    for variant in schedule.variants:
        records = schedule.hazards.static.variants[variant.variant_ordinal].source.records
        for command in variant.commands:
            if type(command.source) is not KernelCommandSource:
                continue
            op = records.ops[command.source.kernel_op_id - 1]
            if op.opcode is not KernelOpcode.DMA:
                continue
            if op.reads and len(op.reads) != len(op.writes) or not op.reads and len(op.writes) != 1:
                raise MeshIrError("E_DMA_RANGE", "DMA accesses do not form the required ordered pairs", op_id=op.op_id)
            pairs = ((op.writes[0], op.writes[0], 0, 0),) if not op.reads else tuple((source, destination, index, index) for index, (source, destination) in enumerate(zip(op.reads, op.writes)))
            for source, destination, source_index, destination_index in pairs:
                for segment_ordinal, (source_region, destination_region, rows, row_bytes, source_stride, destination_stride, span) in enumerate(_paired_geometry(records, source, destination)):
                    segments.append(_AddressParameterizedSegment(variant.variant_ordinal, op.op_id, segment_ordinal, source_index, destination_index, source_region, destination_region, rows, row_bytes, source_stride, destination_stride, checked_mul_u64(rows, row_bytes, "DMA useful bytes"), span))
    return _SegmentState(schedule, tuple(segments))

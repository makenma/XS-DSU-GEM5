from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from itertools import product

from mesh_ir.canonical import checked_add_u64, checked_mul_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import ElementRegion


def project_broadcast_region(shape: tuple[int, ...], output: ElementRegion) -> ElementRegion:
    if len(shape) > len(output.shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "operand rank exceeds result rank")
    leading = len(output.shape) - len(shape)
    return ElementRegion(
        tuple(0 if extent == 1 else output.origin[leading + axis] for axis, extent in enumerate(shape)),
        tuple(1 if extent == 1 else output.shape[leading + axis] for axis, extent in enumerate(shape)),
        tuple(1 if extent == 1 else output.steps[leading + axis] for axis, extent in enumerate(shape)),
    )


@dataclass(frozen=True, order=True)
class ByteSpan:
    begin: int
    end: int


@dataclass(frozen=True)
class DisjointByteSpanIndex:
    groups: tuple[tuple[ByteSpan, ...], ...]
    _spans: tuple[ByteSpan, ...] = field(init=False, repr=False)
    _owners: tuple[int, ...] = field(init=False, repr=False)
    _ends: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        entries = sorted((span, owner) for owner, spans in enumerate(self.groups)
                         for span in merge_spans(spans))
        if any(left.end > right.begin for (left, _), (right, _) in zip(entries, entries[1:])):
            raise MeshIrError('E_DMA_RANGE', 'source byte ranges overlap')
        object.__setattr__(self, '_spans', tuple(span for span, _ in entries))
        object.__setattr__(self, '_owners', tuple(owner for _, owner in entries))
        object.__setattr__(self, '_ends', tuple(span.end for span, _ in entries))

    def intersections(self, required: tuple[ByteSpan, ...]) -> tuple[tuple[int, tuple[ByteSpan, ...]], ...]:
        result: dict[int, list[ByteSpan]] = {}
        for span in merge_spans(required):
            index = bisect_right(self._ends, span.begin)
            while index < len(self._spans) and self._spans[index].begin < span.end:
                source = self._spans[index]
                piece = ByteSpan(max(span.begin, source.begin), min(span.end, source.end))
                result.setdefault(self._owners[index], []).append(piece)
                index += 1
        return tuple((owner, tuple(spans)) for owner, spans in sorted(result.items()))


def ordered_region_interval(parent: ElementRegion, piece: ElementRegion) -> tuple[int, int]:
    if type(parent) is not ElementRegion or type(piece) is not ElementRegion:
        raise MeshIrError("E_DMA_RANGE", "ordered regions must use exact ElementRegion records")
    vectors = (parent.origin, parent.shape, parent.steps, piece.origin, piece.shape, piece.steps)
    if any(type(values) is not tuple or any(type(value) is not int or value < 0 for value in values) for values in vectors) or any(step < 1 for step in parent.steps + piece.steps):
        raise MeshIrError("E_DMA_RANGE", "ordered region vectors are invalid")
    if not len(parent.origin) == len(parent.shape) == len(parent.steps) == len(piece.origin) == len(piece.shape) == len(piece.steps):
        raise MeshIrError("E_DMA_RANGE", "ordered region ranks differ")
    if any(extent == 0 for extent in parent.shape):
        if piece != parent:
            raise MeshIrError("E_DMA_RANGE", "empty ordered region requires its exact empty piece")
        return 0, 0
    if any(extent == 0 for extent in piece.shape):
        raise MeshIrError("E_DMA_RANGE", "empty piece cannot cover a nonempty ordered region")
    parent_strides = []
    inner = 1
    for extent in reversed(parent.shape):
        parent_strides.append(inner)
        inner = checked_mul_u64(inner, extent, "ordered parent element count")
    parent_strides.reverse()
    starts = []
    ratios = []
    for parent_origin, parent_extent, parent_step, piece_origin, piece_extent, piece_step in zip(parent.origin, parent.shape, parent.steps, piece.origin, piece.shape, piece.steps):
        if piece_origin < parent_origin or (piece_origin - parent_origin) % parent_step or piece_extent > 1 and piece_step % parent_step:
            raise MeshIrError("E_DMA_RANGE", "ordered piece is not on the parent lattice")
        start = (piece_origin - parent_origin) // parent_step
        ratio = piece_step // parent_step if piece_extent > 1 else 0
        last = checked_add_u64(start, checked_mul_u64(piece_extent - 1, ratio, "ordered piece axis end"), "ordered piece axis end")
        if last >= parent_extent:
            raise MeshIrError("E_DMA_RANGE", "ordered piece exceeds its parent")
        starts.append(start)
        ratios.append(ratio)
    count = 1
    for extent in piece.shape:
        count = checked_mul_u64(count, extent, "ordered piece element count")
    contiguous_stride = 1
    for extent, ratio, parent_stride in reversed(tuple(zip(piece.shape, ratios, parent_strides))):
        if extent > 1 and checked_mul_u64(ratio, parent_stride, "ordered piece traversal stride") != contiguous_stride:
            raise MeshIrError("E_DMA_RANGE", "piece traversal is not a continuous parent interval")
        contiguous_stride = checked_mul_u64(contiguous_stride, extent, "ordered piece traversal extent")
    start = 0
    for index, stride in zip(starts, parent_strides):
        start = checked_add_u64(start, checked_mul_u64(index, stride, "ordered piece start"), "ordered piece start")
    return start, count


def compact_ordered_affine_axes(shape: tuple[int, ...], strides: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    if type(shape) is not tuple or type(strides) is not tuple or len(shape) != len(strides) or any(type(extent) is not int or extent < 1 for extent in shape) or any(type(stride) is not int or stride < 0 for stride in strides):
        raise MeshIrError("E_DMA_RANGE", "ordered affine axes are invalid")
    compacted = []
    for extent, stride in reversed(tuple(zip(shape, strides))):
        if extent == 1:
            continue
        if compacted and stride == checked_mul_u64(compacted[0][0], compacted[0][1], "ordered affine coalescing"):
            compacted[0] = (checked_mul_u64(extent, compacted[0][0], "ordered affine extent"), compacted[0][1])
        else:
            compacted.insert(0, (extent, stride))
    return tuple(compacted)


def flat_interval_regions(shape: tuple[int, ...], begin: int, count: int) -> tuple[ElementRegion, ...]:
    if type(shape) is not tuple or any(type(extent) is not int or extent < 0 for extent in shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "flat interval shape is invalid")
    total = 1
    for extent in shape:
        total = checked_mul_u64(total, extent, "flat region elements")
    end = checked_add_u64(begin, count, "flat interval end")
    if end > total:
        raise MeshIrError("E_EXPORT_LAYOUT", "flat interval exceeds its logical region")
    if not count:
        return ()
    if not shape:
        if begin or count != 1:
            raise MeshIrError("E_EXPORT_LAYOUT", "scalar flat interval is invalid")
        return (ElementRegion((), (), ()),)

    def project(extents: tuple[int, ...], start: int, stop: int) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
        if len(extents) == 1:
            return (((start,), (stop - start,)),)
        block = 1
        for extent in extents[1:]:
            block = checked_mul_u64(block, extent, "flat region row elements")
        first = start // block
        last = (stop - 1) // block
        if first == last:
            return tuple(((first, *origin), (1, *piece_shape)) for origin, piece_shape in project(extents[1:], start % block, (stop - 1) % block + 1))
        start_offset = start % block
        pieces = ()
        if start_offset:
            pieces = tuple(((first, *origin), (1, *piece_shape)) for origin, piece_shape in project(extents[1:], start_offset, block))
        full_begin = first + bool(start_offset)
        full_end = stop // block
        if full_end > full_begin:
            pieces += (((full_begin,) + (0,) * (len(extents) - 1), (full_end - full_begin, *extents[1:])),)
        tail = stop % block
        if tail:
            pieces += tuple(((full_end, *origin), (1, *piece_shape)) for origin, piece_shape in project(extents[1:], 0, tail))
        return pieces

    return tuple(ElementRegion(origin, piece_shape, (1,) * len(piece_shape)) for origin, piece_shape in project(shape, begin, end))


def merge_spans(spans: tuple[ByteSpan, ...]) -> tuple[ByteSpan, ...]:
    merged: list[ByteSpan] = []
    for span in sorted(spans):
        if span.begin == span.end:
            continue
        if merged and span.begin <= merged[-1].end:
            merged[-1] = ByteSpan(merged[-1].begin, max(merged[-1].end, span.end))
        else:
            merged.append(span)
    return tuple(merged)


def region_byte_spans(offset: int, shape: tuple[int, ...], strides: tuple[int, ...], width: int) -> tuple[ByteSpan, ...]:
    if any(dimension == 0 for dimension in shape):
        return ()
    axes = sorted((stride, dimension, index) for index, (dimension, stride) in enumerate(zip(shape, strides)) if dimension > 1 and stride > 0)
    dense_axes = set()
    dense_elements = 1
    for stride, dimension, index in axes:
        if stride == dense_elements:
            dense_axes.add(index)
            dense_elements = checked_mul_u64(dense_elements, dimension, "region density")
    outer = tuple((dimension, stride) for index, (dimension, stride) in enumerate(zip(shape, strides)) if dimension > 1 and stride > 0 and index not in dense_axes)
    ranges = (range(dimension) for dimension, _ in outer)
    spans = []
    for indices in product(*ranges):
        element = offset
        for index, (_, stride) in zip(indices, outer):
            element = checked_add_u64(element, checked_mul_u64(index, stride, "region element offset"), "region element offset")
        begin = checked_mul_u64(element, width, "region byte offset")
        size = checked_mul_u64(dense_elements, width, "region byte extent")
        spans.append(ByteSpan(begin, checked_add_u64(begin, size, "region byte end")))
    return merge_spans(tuple(spans))


def intersect_spans(first: tuple[ByteSpan, ...], second: tuple[ByteSpan, ...]) -> tuple[ByteSpan, ...]:
    left, right = merge_spans(first), merge_spans(second)
    output = []
    i = j = 0
    while i < len(left) and j < len(right):
        begin, end = max(left[i].begin, right[j].begin), min(left[i].end, right[j].end)
        if begin < end:
            output.append(ByteSpan(begin, end))
        if left[i].end <= right[j].end:
            i += 1
        else:
            j += 1
    return tuple(output)


def subtract_spans(required: tuple[ByteSpan, ...], available: tuple[ByteSpan, ...]) -> tuple[ByteSpan, ...]:
    required, available = merge_spans(required), merge_spans(available)
    output = []
    index = 0
    for span in required:
        cursor = span.begin
        while index < len(available) and available[index].end <= cursor:
            index += 1
        while index < len(available) and available[index].begin < span.end:
            candidate = available[index]
            if candidate.begin > cursor:
                output.append(ByteSpan(cursor, candidate.begin))
            cursor = max(cursor, candidate.end)
            if cursor >= span.end:
                break
            index += 1
        if cursor < span.end:
            output.append(ByteSpan(cursor, span.end))
    return tuple(output)


def spans_contain(available: tuple[ByteSpan, ...], required: tuple[ByteSpan, ...]) -> bool:
    return not subtract_spans(required, available)


def spans_overlap(first: tuple[ByteSpan, ...], second: tuple[ByteSpan, ...]) -> bool:
    return bool(intersect_spans(first, second))


def view_access_byte_spans(access, views, objects, tensors, shards) -> tuple[ByteSpan, ...]:
    view = views[access.view_id]
    obj = objects[view.object_id]
    shard = shards[view.shard_id]
    tensor = tensors[shard.tensor_id]
    element = view.object_offset_elements
    strides = []
    for origin, step, stride in zip(access.region.origin, access.region.steps, view.object_strides):
        element = checked_add_u64(element, checked_mul_u64(origin, stride, "access element offset"), "access element offset")
        strides.append(checked_mul_u64(step, stride, "access element stride"))
    absolute = region_byte_spans(element, access.region.shape, tuple(strides), tensor.dtype.byte_width)
    view_spans = region_byte_spans(view.object_offset_elements, view.padded_shape, view.object_strides, tensor.dtype.byte_width)
    if not spans_contain(view_spans, absolute) or any(span.end > obj.footprint_bytes for span in absolute):
        raise MeshIrError("E_EXPORT_LAYOUT", "operand region exceeds its view", view_id=view.view_id)
    return absolute

from itertools import product

from mesh_ir.analysis.regions import flat_interval_regions, region_byte_spans
from mesh_ir.canonical import checked_add_u64, checked_mul_u64, checked_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Const, DType, FixedStride, ShapeProductStride
from mesh_ir.ir.graph_ir import GraphValue
from mesh_ir.passes.placement_geometry import DenseLogicalRegion, MappedStorageRegion


def project_mapped_region_pieces(value: GraphValue, parent: DenseLogicalRegion, mapped: MappedStorageRegion) -> tuple[DenseLogicalRegion, ...]:
    if type(value) is not GraphValue or type(parent) is not DenseLogicalRegion or type(mapped) is not MappedStorageRegion:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped region projection records are invalid")
    if type(value.value_id) is not int or value.value_id < 1 or type(value.alias_root) is not int or value.alias_root < 1 or type(value.dtype) is not DType:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped region value identity is invalid")
    if type(mapped.value_id) is not int or mapped.value_id < 1 or type(mapped.alias_root) is not int or mapped.alias_root < 1 or type(mapped.dtype) is not DType:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped region identity is invalid", value_id=value.value_id)
    if mapped.value_id != value.value_id or mapped.alias_root != value.alias_root or mapped.dtype is not value.dtype:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped region identity differs from its logical value", value_id=value.value_id)
    if type(value.shape) is not tuple or any(type(item) is not Const for item in value.shape) or type(value.strides) is not tuple or any(type(item) not in (FixedStride, ShapeProductStride) or item.symbol_ids() for item in value.strides):
        raise MeshIrError("E_SHAPE_UNBOUND", "mapped region projection requires a specialized value", value_id=value.value_id)
    shape = tuple(checked_u64(item.value, "mapped value shape") for item in value.shape)
    strides = tuple(checked_u64(item.evaluate({}), "mapped value stride") for item in value.strides)
    checked_u64(value.storage_offset, "mapped value storage offset")
    if len(shape) != len(strides) or type(parent.origin) is not tuple or type(parent.shape) is not tuple or not len(parent.origin) == len(parent.shape) == len(shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped parent rank is invalid", value_id=value.value_id)
    if any(type(item) is not int or item < 0 for item in parent.origin + parent.shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped parent vectors are invalid", value_id=value.value_id)
    for origin, extent, bound in zip(parent.origin, parent.shape, shape):
        if checked_add_u64(origin, extent, "mapped parent bound") > bound:
            raise MeshIrError("E_EXPORT_LAYOUT", "mapped parent exceeds its logical value", value_id=value.value_id)
    if type(mapped.element_offset) is not int or mapped.element_offset < 0 or type(mapped.shape) is not tuple or type(mapped.strides) is not tuple or len(mapped.shape) != len(mapped.strides) or any(type(item) is not int or item < 0 for item in mapped.shape + mapped.strides):
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped storage vectors are invalid", value_id=value.value_id)
    checked_u64(mapped.element_offset, "mapped storage offset")
    for item in mapped.shape:
        checked_u64(item, "mapped storage shape")
    for item in mapped.strides:
        checked_u64(item, "mapped storage stride")
    if any(not extent for extent in parent.shape) or any(not extent for extent in mapped.shape):
        return ()
    width = value.dtype.byte_width
    mapped_spans = tuple((span.begin // width, span.end // width) for span in mapped.byte_spans)
    parent_base = value.storage_offset
    for origin, stride in zip(parent.origin, strides):
        parent_base = checked_add_u64(parent_base, checked_mul_u64(origin, stride, "mapped parent offset"), "mapped parent offset")
    if mapped.byte_spans == region_byte_spans(parent_base, parent.shape, strides, width):
        return (parent,)
    axes = sorted((stride, extent, index) for index, (extent, stride) in enumerate(zip(parent.shape, strides)) if extent > 1 and stride > 0)
    dense_axes = set()
    dense_elements = 1
    for stride, extent, index in axes:
        if stride == dense_elements:
            dense_axes.add(index)
            dense_elements = checked_mul_u64(dense_elements, extent, "mapped dense elements")
    dense_order = tuple(index for _, _, index in sorted(((strides[index], parent.shape[index], index) for index in dense_axes), reverse=True))
    dense_shape = tuple(parent.shape[index] for index in dense_order)
    outer_axes = tuple(index for index, (extent, stride) in enumerate(zip(parent.shape, strides)) if extent > 1 and stride > 0 and index not in dense_axes)
    pieces = []
    for outer_indices in product(*(range(parent.shape[index]) for index in outer_axes)):
        block_begin = parent_base
        for index, coordinate in zip(outer_axes, outer_indices):
            block_begin = checked_add_u64(block_begin, checked_mul_u64(coordinate, strides[index], "mapped outer offset"), "mapped outer offset")
        block_end = checked_add_u64(block_begin, dense_elements, "mapped dense block end")
        for span_begin, span_end in mapped_spans:
            begin = max(block_begin, span_begin)
            end = min(block_end, span_end)
            if begin >= end:
                continue
            for dense_region in flat_interval_regions(dense_shape, begin - block_begin, end - begin):
                origin = list(parent.origin)
                piece_shape = [1] * len(parent.shape)
                for index, extent, stride in zip(range(len(parent.shape)), parent.shape, strides):
                    if stride == 0:
                        piece_shape[index] = extent
                for index, coordinate in zip(outer_axes, outer_indices):
                    origin[index] += coordinate
                for index, coordinate, extent in zip(dense_order, dense_region.origin, dense_region.shape):
                    origin[index] += coordinate
                    piece_shape[index] = extent
                pieces.append(DenseLogicalRegion(tuple(origin), tuple(piece_shape)))
    compacted = []
    for piece in sorted(pieces, key=lambda item: (item.origin, item.shape)):
        if compacted:
            previous = compacted[-1]
            merge_axes = tuple(
                index
                for index, (left_origin, left_extent, right_origin, right_extent) in enumerate(zip(previous.origin, previous.shape, piece.origin, piece.shape))
                if left_origin != right_origin or left_extent != right_extent
            )
            if len(merge_axes) == 1:
                axis = merge_axes[0]
                if previous.shape[axis] == piece.shape[axis] and checked_add_u64(previous.origin[axis], previous.shape[axis], "mapped piece coalescing") == piece.origin[axis]:
                    merged_shape = list(previous.shape)
                    merged_shape[axis] = checked_add_u64(merged_shape[axis], piece.shape[axis], "mapped piece coalescing")
                    compacted[-1] = DenseLogicalRegion(previous.origin, tuple(merged_shape))
                    continue
        compacted.append(piece)
    return tuple(compacted)

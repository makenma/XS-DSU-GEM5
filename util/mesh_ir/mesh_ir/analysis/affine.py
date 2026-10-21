from mesh_ir.canonical import checked_add_u64, checked_mul_u64, checked_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Layout
from mesh_ir.ir.graph_ir import GraphValue
from math import prod


def rebase_affine_view(source_shape: tuple[int, ...], source_strides: tuple[int, ...],
                       source_offset: int, physical_strides: tuple[int, ...],
                       shape: tuple[int, ...], strides: tuple[int, ...],
                       offset: int) -> tuple[int, tuple[int, ...]]:
    vectors = (source_shape, source_strides, physical_strides, shape, strides)
    if any(type(vector) is not tuple for vector in vectors) or not len(source_shape) == len(source_strides) == len(physical_strides) or len(shape) != len(strides):
        raise MeshIrError('E_EXPORT_LAYOUT', 'affine rebase ranks differ')
    for vector in vectors:
        for value in vector:
            checked_u64(value, 'affine rebase vector')
    checked_u64(source_offset, 'affine source offset')
    checked_u64(offset, 'affine view offset')
    if any(extent == 0 for extent in shape):
        return 0, (0,) * len(shape)
    if offset < source_offset or any(extent == 0 for extent in source_shape):
        raise MeshIrError('E_EXPORT_LAYOUT', 'affine view is outside its producer')
    axes = sorted(((stride, extent, physical) for extent, stride, physical
                   in zip(source_shape, source_strides, physical_strides) if extent > 1), reverse=True)
    groups = []
    for stride, extent, physical in axes:
        if stride == 0:
            raise MeshIrError('E_EXPORT_LAYOUT', 'affine producer mapping is not injective')
        if groups and groups[-1][0] == stride * extent and groups[-1][2] == physical * extent:
            _, outer_extent, _ = groups.pop()
            extent = checked_mul_u64(outer_extent, extent, 'affine merged extent')
        groups.append((stride, extent, physical))
    span = 1
    for stride, extent, _ in reversed(groups):
        if stride < span:
            raise MeshIrError('E_EXPORT_LAYOUT', 'affine producer axes overlap')
        span = checked_add_u64(span, checked_mul_u64(extent - 1, stride, 'affine producer span'), 'affine producer span')
    deltas = [offset - source_offset, *(stride if extent > 1 else 0 for extent, stride in zip(shape, strides))]
    mapped = [0] * len(deltas)
    for stride, extent, physical in groups:
        coefficients = []
        for index, delta in enumerate(deltas):
            coefficient, deltas[index] = divmod(delta, stride)
            coefficients.append(coefficient)
            mapped[index] = checked_add_u64(mapped[index], checked_mul_u64(coefficient, physical, 'affine physical offset'), 'affine physical offset')
        last = coefficients[0]
        for size, coefficient in zip(shape, coefficients[1:]):
            last = checked_add_u64(last, checked_mul_u64(size - 1, coefficient, 'affine axis bound'), 'affine axis bound')
        if last >= extent:
            raise MeshIrError('E_EXPORT_LAYOUT', 'affine view crosses a producer layout boundary')
    if any(deltas):
        raise MeshIrError('E_EXPORT_LAYOUT', 'affine view is outside the producer lattice')
    return mapped[0], tuple(mapped[1:])


def consumer_mapping(value: GraphValue, shape: tuple[int, ...], extent_bytes: int) -> tuple[tuple[int, ...], Layout]:
    strides = tuple(item.evaluate({}) for item in value.strides)
    if len(shape) >= 2:
        matrix = checked_mul_u64(shape[-2], shape[-1], "transposed window elements")
        expected = tuple(checked_mul_u64(prod(shape[index + 1:-2]), matrix, "transposed batch stride")
            for index in range(len(shape) - 2)) + (1, shape[-2])
        elements = 0 if any(item == 0 for item in shape) else 1 + sum((size - 1) * stride for size, stride in zip(shape, strides))
        if strides == expected and checked_mul_u64(elements, value.dtype.byte_width, "transposed window bytes") <= extent_bytes:
            return strides, Layout.TRANSPOSED_2D_VIEW
    return tuple(checked_u64(prod(shape[index + 1:]), "buffer stride") for index in range(len(shape))), Layout.CONTIGUOUS_ROW_MAJOR


def rebase_consumer_view(source_shape: tuple[int, ...], source_strides: tuple[int, ...],
                         source_offset: int, physical_strides: tuple[int, ...],
                         value: GraphValue, shape: tuple[int, ...], offset: int,
                         extent_bytes: int) -> tuple[int, tuple[int, ...], Layout | None]:
    physical_offset, actual = rebase_affine_view(source_shape, source_strides, source_offset,
        physical_strides, shape, tuple(item.evaluate({}) for item in value.strides), offset)
    canonical, layout = consumer_mapping(value, shape, extent_bytes)
    if all(size <= 1 or stride == expected for size, stride, expected in zip(shape, actual, canonical)):
        return physical_offset, canonical, layout
    return physical_offset, actual, None

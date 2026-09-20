import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Const, DType, TensorRole
from mesh_ir.ir.graph_ir import GraphValue
from mesh_ir.passes.bufferize import project_mapped_region_pieces
from mesh_ir.passes.placement_geometry import DenseLogicalRegion, MappedStorageRegion


def _value(shape, strides, *, storage_offset=0, value_id=1, alias_root=1):
    return GraphValue(
        value_id,
        "value",
        TensorRole.INPUT,
        DType.FP32,
        tuple(Const(item) for item in shape),
        strides,
        storage_offset,
        alias_root,
        Access.READ_ONLY,
    )


def _mapped(value, offset, shape, strides):
    return MappedStorageRegion(value.value_id, value.alias_root, value.dtype, offset, shape, strides)


def test_project_mapped_region_clips_a_larger_dense_mapping_to_parent_rows():
    value = _value((4, 5), (5, 1))
    parent = DenseLogicalRegion((1, 1), (2, 3))
    mapped = _mapped(value, 5, (10,), (1,))

    assert project_mapped_region_pieces(value, parent, mapped) == (parent,)


def test_project_mapped_region_preserves_transpose_and_nonzero_slice_mapping():
    transpose = _value((2, 4, 3), (12, 1, 4))
    assert project_mapped_region_pieces(
        transpose,
        DenseLogicalRegion((0, 0, 0), (2, 4, 3)),
        _mapped(transpose, 0, (4,), (1,)),
    ) == (DenseLogicalRegion((0, 0, 0), (1, 4, 1)),)

    sliced = _value((4,), (2,), storage_offset=3)
    assert project_mapped_region_pieces(
        sliced,
        DenseLogicalRegion((1,), (3,)),
        _mapped(sliced, 7, (2,), (2,)),
    ) == (DenseLogicalRegion((2,), (2,)),)


def test_project_mapped_region_preserves_repeated_address_coordinates():
    broadcast = _value((3, 4), (0, 1))
    assert project_mapped_region_pieces(
        broadcast,
        DenseLogicalRegion((0, 0), (3, 4)),
        _mapped(broadcast, 1, (2,), (1,)),
    ) == (DenseLogicalRegion((0, 1), (3, 2)),)

    overlapping = _value((2, 2), (1, 1))
    assert project_mapped_region_pieces(
        overlapping,
        DenseLogicalRegion((0, 0), (2, 2)),
        _mapped(overlapping, 1, (1,), (1,)),
    ) == (
        DenseLogicalRegion((0, 1), (1, 1)),
        DenseLogicalRegion((1, 0), (1, 1)),
    )


def test_project_mapped_region_preserves_disjoint_rows_scalar_and_empty_domains():
    value = _value((2, 4), (4, 1))
    assert project_mapped_region_pieces(
        value,
        DenseLogicalRegion((0, 0), (2, 4)),
        _mapped(value, 0, (2, 2), (4, 1)),
    ) == (DenseLogicalRegion((0, 0), (2, 2)),)

    scalar = _value((), (), storage_offset=5)
    assert project_mapped_region_pieces(scalar, DenseLogicalRegion((), ()), _mapped(scalar, 5, (), ())) == (DenseLogicalRegion((), ()),)

    empty = _value((0, 4), (4, 1))
    assert project_mapped_region_pieces(empty, DenseLogicalRegion((0, 0), (0, 4)), _mapped(empty, 0, (4,), (1,))) == ()


@pytest.mark.parametrize(
    "shape,strides",
    (
        ((0, 1 << 80), (1, 1)),
        ((0,), (1 << 80,)),
    ),
)
def test_project_mapped_region_validates_empty_mapping_vectors_before_return(shape, strides):
    value = _value((2,), (1,))
    with pytest.raises(MeshIrError) as error:
        project_mapped_region_pieces(value, DenseLogicalRegion((0,), (2,)), _mapped(value, 0, shape, strides))
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize(
    "mapped,parent",
    (
        (MappedStorageRegion(2, 1, DType.FP32, 0, (1,), (1,)), DenseLogicalRegion((0, 0), (2, 2))),
        (MappedStorageRegion(1, 2, DType.FP32, 0, (1,), (1,)), DenseLogicalRegion((0, 0), (2, 2))),
        (MappedStorageRegion(True, 1, DType.FP32, 0, (1,), (1,)), DenseLogicalRegion((0, 0), (2, 2))),
        (MappedStorageRegion(1, 1.0, DType.FP32, 0, (1,), (1,)), DenseLogicalRegion((0, 0), (2, 2))),
        (MappedStorageRegion(1, 1, DType.FP16, 0, (1,), (1,)), DenseLogicalRegion((0, 0), (2, 2))),
        (MappedStorageRegion(1, 1, DType.FP32, 0, (1,), (1,)), DenseLogicalRegion((1, 0), (2, 2))),
        (MappedStorageRegion(1, 1, DType.FP32, 0, (1,), (1,)), DenseLogicalRegion((0,), (2,))),
    ),
)
def test_project_mapped_region_rejects_wrong_identity_and_parent_bounds(mapped, parent):
    with pytest.raises(MeshIrError):
        project_mapped_region_pieces(_value((2, 2), (2, 1)), parent, mapped)

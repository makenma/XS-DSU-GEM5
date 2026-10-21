from itertools import product

import pytest

from mesh_ir.analysis.affine import rebase_affine_view
from mesh_ir.diagnostics import MeshIrError


@pytest.mark.parametrize('origin', [0, 8])
def test_rebase_broadcast_preserves_producer_physical_addresses(origin):
    source_shape = (1, 2, 4, 4)
    source_strides = (128, 4, 32, 1)
    physical = (32, 16, 4, 1)
    shape = (1, 2, 4, 4, 4)
    strides = (128, 4, 0, 32, 1)
    offset, actual = rebase_affine_view(source_shape, source_strides, origin, physical, shape, strides, origin)
    addresses = {origin + sum(i * s for i, s in zip(point, source_strides)):
                 sum(i * s for i, s in zip(point, physical))
                 for point in product(*(range(n) for n in source_shape))}
    for point in product(*(range(n) for n in shape)):
        assert offset + sum(i * s for i, s in zip(point, actual)) == addresses[origin + sum(i * s for i, s in zip(point, strides))]
    assert actual[2] == 0


def test_rebase_reshape_and_strided_slice_without_element_enumeration():
    assert rebase_affine_view((4, 8), (8, 1), 64, (8, 1), (2, 4), (16, 2), 66) == (2, (16, 2))
    assert rebase_affine_view((1 << 30, 8), (8, 1), 0, (8, 1), (1 << 32, 2), (2, 1), 0) == (0, (2, 1))


@pytest.mark.parametrize('shape,strides,offset', [((2, 4), (4, 1), 0), ((2,), (1,), 3), ((1,), (0,), 8)])
def test_rebase_rejects_non_affine_or_outside_producer(shape, strides, offset):
    with pytest.raises(MeshIrError):
        rebase_affine_view((2, 2), (4, 1), 0, (2, 1), shape, strides, offset)

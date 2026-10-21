import pytest

from mesh_ir.analysis.regions import project_broadcast_region
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import ElementRegion


@pytest.mark.parametrize('shape,expected', [
    ((8, 16, 32), ElementRegion((2, 3, 4), (2, 5, 7), (1, 2, 3))),
    ((1, 16, 32), ElementRegion((0, 3, 4), (1, 5, 7), (1, 2, 3))),
    ((32,), ElementRegion((4,), (7,), (3,))),
    ((), ElementRegion((), (), ())),
])
def test_broadcast_projection_preserves_nonbroadcast_coordinates(shape, expected):
    output = ElementRegion((2, 3, 4), (2, 5, 7), (1, 2, 3))
    assert project_broadcast_region(shape, output) == expected


def test_broadcast_projection_rejects_operand_rank_above_output():
    with pytest.raises(MeshIrError, match='operand rank exceeds result rank'):
        project_broadcast_region((1, 2), ElementRegion((0,), (2,), (1,)))

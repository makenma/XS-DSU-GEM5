import pytest

from mesh_ir.canonical import semantic_sha256
from mesh_ir.model import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage3_geometry_cases import build_stage3_geometry_cases


CASES = build_stage3_geometry_cases()
POSITIVE_CASES = tuple(case for case in CASES if case.whole_program_code is None)
WHOLE_PROGRAM_REJECTIONS = tuple(
    case for case in CASES if case.whole_program_code is not None
)


@pytest.mark.parametrize("case", POSITIVE_CASES, ids=lambda case: case.case_id)
def test_complete_geometry_inputs_are_accepted(case):
    assert case.program.semantic_sha256 == semantic_sha256(
        case.program.semantic_dict()
    )
    assert verify_program(case.program, case.arch).program is case.program


@pytest.mark.parametrize(
    "case", WHOLE_PROGRAM_REJECTIONS, ids=lambda case: case.case_id
)
def test_many_to_one_geometry_read_is_deferred_to_whole_program_dma(case):
    assert case.program.semantic_sha256 == semantic_sha256(
        case.program.semantic_dict()
    )
    with pytest.raises(MeshIrError) as caught:
        verify_program(case.program, case.arch)
    assert caught.value.code == case.whole_program_code
    assert caught.value.message == "movement source and destination pieces differ"

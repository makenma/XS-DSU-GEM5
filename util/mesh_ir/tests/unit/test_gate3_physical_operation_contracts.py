import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage3_intrinsic_memory_cases import (
    verify_complete_lifecycle,
)
from tests.golden.support.stage3_physical_operation_cases import (
    build_stage3_physical_operation_cases,
)


CASES = build_stage3_physical_operation_cases()
BASELINES = tuple(
    next(case for case in CASES if case.baseline.semantic_sha256 == semantic_sha256)
    for semantic_sha256 in dict.fromkeys(case.baseline.semantic_sha256 for case in CASES)
)


@pytest.mark.parametrize("case", BASELINES, ids=lambda case: f"baseline_{case.case_id}")
def test_complete_physical_operation_baselines_are_accepted(case):
    verify_complete_lifecycle(case.baseline)
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_complete_physical_operation_cases_have_exact_python_diagnostics(case):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    verify_complete_lifecycle(case.program)
    if case.expected_code is None:
        assert verify_program(case.program, case.arch).program is case.program
        return
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code
    assert error.value.message == case.expected_message

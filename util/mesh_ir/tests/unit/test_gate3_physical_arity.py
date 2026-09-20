import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage3_intrinsic_memory_cases import (
    verify_complete_lifecycle,
)
from tests.golden.support.stage3_physical_arity_cases import (
    build_stage3_physical_arity_cases,
)


CASES = build_stage3_physical_arity_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_physical_arity_candidates_reach_exact_python_contract(case):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    verify_complete_lifecycle(case.baseline)
    assert verify_program(case.baseline, case.arch).program is case.baseline
    verify_complete_lifecycle(case.program)
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code
    assert error.value.message == case.expected_message

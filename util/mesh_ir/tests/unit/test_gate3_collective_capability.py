import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage3_collective_capability_cases import (
    build_stage3_collective_capability_cases,
)


CASES = build_stage3_collective_capability_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"baseline_{case.case_id}")
def test_collective_capability_baselines_are_accepted(case):
    assert case.baseline.semantic_sha256 == semantic_sha256(
        case.baseline.semantic_dict()
    )
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_complete_collective_has_exact_capability_diagnostic(case):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    assert case.program.semantic_sha256 == semantic_sha256(
        case.program.semantic_dict()
    )
    assert encode_program(case.program) != encode_program(case.baseline)
    with pytest.raises(MeshIrError) as caught:
        verify_program(case.program, case.arch)
    assert caught.value.code == case.expected_code
    assert caught.value.message == case.expected_message

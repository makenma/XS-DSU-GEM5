import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage3_dma_parent_pair_cases import (
    build_stage3_dma_parent_pair_cases,
)


CASES = build_stage3_dma_parent_pair_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"baseline_{case.case_id}")
def test_dma_parent_pair_baselines_are_accepted(case):
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_dma_parent_pair_cases_have_exact_python_diagnostics(case):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    with pytest.raises(MeshIrError) as caught:
        verify_program(case.program, case.arch)
    assert caught.value.code == case.expected_code

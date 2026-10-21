from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage4_lifetime_cases import (
    build_stage4_lifetime_cases,
)


ROOT = Path(__file__).resolve().parents[4]
ARCH = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
CASES = build_stage4_lifetime_cases(ARCH)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_complete_lifetime_baselines_are_accepted(case):
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_persistent_complete_lifetime_cases_have_exact_python_diagnostics(case):
    if case.expected_code is None:
        assert verify_program(case.program, case.arch).program is case.program
        return
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    assert decode_program(encode_program(case.program)) == case.program
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code
    assert error.value.message == case.expected_message

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage2_dependency_lifecycle_cases import (
    build_stage2_dependency_lifecycle_cases,
)


CASES = build_stage2_dependency_lifecycle_cases()


def test_stage2_dependency_lifecycle_unique_baselines_are_accepted():
    baselines = {}
    for case in CASES:
        baselines.setdefault(
            encode_program(case.baseline),
            (case.baseline, case.arch),
        )
    for baseline, arch in baselines.values():
        assert baseline.semantic_sha256 == semantic_sha256(baseline.semantic_dict())
        assert verify_program(baseline, arch).program is baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_stage2_dependency_lifecycle_cases_have_fresh_integrity_and_exact_python_verdict(case):
    assert case.program.semantic_sha256 == semantic_sha256(case.program.semantic_dict())
    if case.expected_code is None:
        assert verify_program(case.program, case.arch).program is case.program
        return
    assert encode_program(case.program) != encode_program(case.baseline)
    with pytest.raises(MeshIrError) as raised:
        verify_program(case.program, case.arch)
    assert raised.value.code == case.expected_code

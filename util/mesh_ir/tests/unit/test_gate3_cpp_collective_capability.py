import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from tests.golden.support.stage3_collective_capability_cases import (
    build_stage3_collective_capability_cases,
)
from tests.golden.test_mutation_corpus import _cpp_verdict


CASES = build_stage3_collective_capability_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"baseline_{case.case_id}")
def test_cpp_collective_capability_baselines_are_accepted(case, driver, tmp_path):
    assert case.baseline.semantic_sha256 == semantic_sha256(
        case.baseline.semantic_dict()
    )
    assert _cpp_verdict(
        encode_program(case.baseline), driver, tmp_path, case.arch
    ) == "ACCEPTED"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_cpp_collective_has_exact_capability_diagnostic(case, driver, tmp_path):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    assert case.program.semantic_sha256 == semantic_sha256(
        case.program.semantic_dict()
    )
    blob = encode_program(case.program)
    assert blob != encode_program(case.baseline)
    assert _cpp_verdict(blob, driver, tmp_path, case.arch) == (
        f"VERIFY:{case.expected_code}"
    )

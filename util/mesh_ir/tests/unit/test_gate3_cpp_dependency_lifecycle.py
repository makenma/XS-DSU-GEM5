import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from tests.golden.support.stage2_dependency_lifecycle_cases import (
    build_stage2_dependency_lifecycle_cases,
)
from tests.golden.test_mutation_corpus import _cpp_verdict


CASES = build_stage2_dependency_lifecycle_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_cpp_dependency_lifecycle_cases_match_exact_python_contract(
    case, driver, tmp_path,
):
    assert case.program.semantic_sha256 == semantic_sha256(case.program.semantic_dict())
    verdict = _cpp_verdict(encode_program(case.program), driver, tmp_path, case.arch)
    expected = "ACCEPTED" if case.expected_code is None else f"VERIFY:{case.expected_code}"
    assert verdict == expected

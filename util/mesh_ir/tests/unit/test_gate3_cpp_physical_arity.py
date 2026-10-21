import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from tests.golden.support.stage3_physical_arity_cases import (
    build_stage3_physical_arity_cases,
)
from tests.golden.test_mutation_corpus import _cpp_verdict


CASES = build_stage3_physical_arity_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_cpp_physical_arity_cases_have_exact_diagnostics(case, driver, tmp_path):
    assert _cpp_verdict(
        encode_program(case.baseline), driver, tmp_path, case.arch
    ) == "ACCEPTED"
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    assert case.program.semantic_sha256 == semantic_sha256(case.program.semantic_dict())
    assert encode_program(case.program) != encode_program(case.baseline)
    assert _cpp_verdict(
        encode_program(case.program), driver, tmp_path, case.arch
    ) == f"VERIFY:{case.expected_code}"

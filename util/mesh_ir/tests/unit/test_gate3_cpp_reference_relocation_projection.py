import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from tests.golden.support.stage4_reference_relocation_cases import (
    build_stage4_reference_relocation_cases,
)
from tests.golden.test_mutation_corpus import _cpp_verdict


CASES = tuple(
    case
    for case in build_stage4_reference_relocation_cases()
    if case.roundtrips
)
BASELINES = tuple(
    next(case for case in CASES if case.baseline.semantic_sha256 == digest)
    for digest in dict.fromkeys(case.baseline.semantic_sha256 for case in CASES)
)


@pytest.mark.parametrize("case", BASELINES, ids=lambda case: f"baseline_{case.case_id}")
def test_cpp_complete_reference_relocation_baselines_are_accepted(case, driver, tmp_path):
    assert case.baseline.semantic_sha256 == semantic_sha256(case.baseline.semantic_dict())
    assert _cpp_verdict(encode_program(case.baseline), driver, tmp_path, case.arch) == "ACCEPTED"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_cpp_complete_reference_relocations_have_exact_diagnostics(case, driver, tmp_path):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    assert case.program.semantic_sha256 == semantic_sha256(case.program.semantic_dict())
    blob = encode_program(case.program)
    assert blob != encode_program(case.baseline)
    expected = "ACCEPTED" if case.expected_code is None else f"VERIFY:{case.expected_code}"
    assert _cpp_verdict(blob, driver, tmp_path, case.arch) == expected

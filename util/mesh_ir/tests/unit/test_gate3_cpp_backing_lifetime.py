from pathlib import Path

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from tests.golden.support.stage4_lifetime_cases import (
    build_stage4_lifetime_cases,
)
from tests.golden.test_mutation_corpus import _cpp_verdict


ROOT = Path(__file__).resolve().parents[4]
CASES = build_stage4_lifetime_cases(
    load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
)
ACCEPTED_INPUTS = tuple(
    (f"baseline_{case.case_id}", case.arch, case.baseline)
    for case in CASES
) + tuple(
    (case.case_id, case.arch, case.program)
    for case in CASES
    if case.expected_code is None
)
POSITIVE_CASES = tuple(
    next(case for case in ACCEPTED_INPUTS if case[2].semantic_sha256 == digest)
    for digest in dict.fromkeys(case[2].semantic_sha256 for case in ACCEPTED_INPUTS)
)
NEGATIVE_CASES = tuple(case for case in CASES if case.expected_code is not None)


@pytest.mark.parametrize("case", POSITIVE_CASES, ids=lambda case: case[0])
def test_cpp_complete_lifetime_positive_cases_are_accepted(case, driver, tmp_path):
    _, arch, program = case
    assert program.semantic_sha256 == semantic_sha256(program.semantic_dict())
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == "ACCEPTED"


@pytest.mark.parametrize("case", NEGATIVE_CASES, ids=lambda case: case.case_id)
def test_cpp_complete_lifetime_cases_have_exact_diagnostics(case, driver, tmp_path):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    assert case.program.semantic_sha256 == semantic_sha256(case.program.semantic_dict())
    blob = encode_program(case.program)
    assert blob != encode_program(case.baseline)
    assert _cpp_verdict(blob, driver, tmp_path, case.arch) == (
        f"VERIFY:{case.expected_code}"
    )

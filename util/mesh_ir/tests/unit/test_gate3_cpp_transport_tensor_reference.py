import hashlib

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from tests.golden.support.stage4_transport_tensor_reference_cases import (
    build_stage4_transport_tensor_reference_cases,
)
from tests.golden.test_mutation_corpus import _cpp_verdict


CASES = build_stage4_transport_tensor_reference_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_cpp_complete_transport_tensor_reference_baselines_are_accepted(
    case, driver, tmp_path,
):
    blob = encode_program(case.baseline)
    assert hashlib.sha256(blob).hexdigest() == case.baseline_image_sha256
    assert _cpp_verdict(blob, driver, tmp_path, case.arch) == "ACCEPTED"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_cpp_unreferenced_transport_tensor_references_have_exact_diagnostics(
    case, driver, tmp_path,
):
    assert case.program.semantic_sha256 == semantic_sha256(case.program.semantic_dict())
    blob = encode_program(case.program)
    assert hashlib.sha256(blob).hexdigest() == case.program_image_sha256
    assert _cpp_verdict(blob, driver, tmp_path, case.arch) == (
        f"VERIFY:{case.expected_code}"
    )

import hashlib

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage4_transport_tensor_reference_cases import (
    build_stage4_transport_tensor_reference_cases,
)


CASES = build_stage4_transport_tensor_reference_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_complete_transport_tensor_reference_baselines_are_accepted(case):
    blob = encode_program(case.baseline)
    assert hashlib.sha256(blob).hexdigest() == case.baseline_image_sha256
    assert decode_program(blob) == case.baseline
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_unreferenced_transport_tensor_references_have_exact_python_diagnostics(case):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    assert case.program.semantic_sha256 == semantic_sha256(case.program.semantic_dict())
    blob = encode_program(case.program)
    assert hashlib.sha256(blob).hexdigest() == case.program_image_sha256
    assert decode_program(blob) == case.program
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code

import hashlib

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.golden_programs import build_fence_scopes_program
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage4_backing_cases import (
    build_two_variant_shared_symbol_program,
)
from tests.golden.support.stage4_reference_relocation_cases import (
    build_stage4_reference_relocation_cases,
)


CASES = build_stage4_reference_relocation_cases()
BASELINES = tuple(
    next(case for case in CASES if case.baseline.semantic_sha256 == semantic_sha256)
    for semantic_sha256 in dict.fromkeys(case.baseline.semantic_sha256 for case in CASES)
)


@pytest.mark.parametrize("case", BASELINES, ids=lambda case: f"baseline_{case.case_id}")
def test_complete_reference_relocation_baselines_are_accepted(case):
    blob = encode_program(case.baseline)
    assert decode_program(blob) == case.baseline
    if case.baseline_image_sha256 is not None:
        assert hashlib.sha256(blob).hexdigest() == case.baseline_image_sha256
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_complete_reference_relocations_have_exact_python_diagnostics(case):
    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    if case.roundtrips:
        blob = encode_program(case.program)
        assert decode_program(blob) == case.program
        if case.program_image_sha256 is not None:
            assert hashlib.sha256(blob).hexdigest() == case.program_image_sha256
    if case.expected_code is None:
        assert verify_program(case.program, case.arch).program is case.program
        return
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code
    if case.expected_message is not None:
        assert error.value.message == case.expected_message


def test_same_storage_root_uses_distinct_complete_external_slots():
    case = next(
        item for item in CASES if item.case_id == "relocation_slot_identity_mismatch"
    )
    program = build_fence_scopes_program(case.arch)
    assert verify_program(program, case.arch).program is program
    assert tuple(item.tensor_id for item in program.relocations) == (1, 1, 1)
    assert tuple(item.slot_id for item in program.semantics.binding_slots) == (1, 2, 3)
    assert tuple(item.relocation_id for item in program.relocations) == (1, 2, 3)


def test_cross_variant_external_symbols_remain_variant_scoped_controls():
    case = next(
        item for item in CASES if item.case_id == "relocation_slot_identity_mismatch"
    )
    program = build_two_variant_shared_symbol_program(case.arch)
    assert verify_program(program, case.arch).program is program
    assert tuple(item.symbol for item in program.semantics.binding_slots) == (
        "stage4:shared-input",
        "stage4:shared-output",
        "stage4:shared-input",
        "stage4:shared-output",
    )
    assert tuple(
        program.strings[item.symbol_sid - 1].value for item in program.relocations
    ) == tuple(item.symbol for item in program.semantics.binding_slots)
    assert tuple(item.membership.binding_slots.count for item in program.semantics.variants) == (2, 2)

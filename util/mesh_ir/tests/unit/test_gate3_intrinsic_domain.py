import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage3_intrinsic_cases import build_stage3_intrinsic_cases


CASES = build_stage3_intrinsic_cases()
BASELINES = tuple(
    next(case for case in CASES if case.baseline.semantic_sha256 == semantic_sha256)
    for semantic_sha256 in dict.fromkeys(case.baseline.semantic_sha256 for case in CASES)
)


@pytest.mark.parametrize("case", BASELINES, ids=lambda case: f"baseline_{case.case_id}")
def test_complete_intrinsic_case_baselines_are_accepted(case):
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_complete_intrinsic_cases_have_exact_python_diagnostics(case):
    if case.expected_code is None:
        assert verify_program(case.program, case.arch).program is case.program
        return

    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code


def test_equal_stable_keys_in_distinct_variants_are_accepted():
    case = next(
        item
        for item in CASES
        if item.case_id == "logical_stable_key_per_variant_positive"
    )
    first, second = case.program.semantics.variants
    first_keys = {
        item.stable_key
        for item in case.program.semantics.kernel_ops[
            first.membership.kernel_ops.first_id - 1:
            first.membership.kernel_ops.first_id - 1 + first.membership.kernel_ops.count
        ]
    }
    second_keys = {
        item.stable_key
        for item in case.program.semantics.kernel_ops[
            second.membership.kernel_ops.first_id - 1:
            second.membership.kernel_ops.first_id - 1 + second.membership.kernel_ops.count
        ]
    }
    assert first_keys & second_keys
    assert verify_program(case.program, case.arch).program is case.program

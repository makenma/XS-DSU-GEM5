from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage2_control_dependency_cases import build_stage2_control_dependency_cases
from tests.golden.support.stage2_control_fixture import build_event_fanin_fixture


ROOT = Path(__file__).resolve().parents[4]
CASES = build_stage2_control_dependency_cases()
BASELINES = tuple(
    next(case for case in CASES if case.baseline.semantic_sha256 == semantic_sha256)
    for semantic_sha256 in dict.fromkeys(case.baseline.semantic_sha256 for case in CASES)
)


def test_event_fanin_fixture_is_complete_and_immutable():
    fixture = build_event_fanin_fixture()

    assert fixture.arch == load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    assert set(fixture.commands) >= {
        "begin",
        "normal_signal_first",
        "normal_signal_second",
        "normal_waiter",
        "barrier_arrival_core0",
        "barrier_arrival_core1",
        "barrier_waiter_core0",
        "barrier_waiter_core1",
        "end",
        "halt",
    }
    assert set(fixture.events) >= {
        "normal_signal_first",
        "normal_signal_second",
        "barrier_arrivals",
        "barrier_completion",
    }
    assert set(fixture.operations) == {
        "barrier_arrival_core0",
        "barrier_arrival_core1",
        "barrier_waiter_core0",
        "barrier_waiter_core1",
    }
    assert set(fixture.tokens) == set(fixture.operations)
    assert set(fixture.dependencies) >= {
        "normal_signal_to_waiter",
        "barrier_arrival_core0_to_waiter_core0",
        "barrier_arrival_core1_to_waiter_core0",
        "barrier_arrival_core0_to_waiter_core1",
        "barrier_arrival_core1_to_waiter_core1",
    }
    with pytest.raises(TypeError):
        fixture.commands["forged"] = 0


@pytest.mark.parametrize(
    "case",
    BASELINES,
    ids=lambda case: f"baseline_{case.case_id}",
)
def test_complete_case_baselines_are_verified_once(case):
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=lambda case: case.case_id,
)
def test_complete_control_dependency_cases_have_exact_python_verdicts(case):
    if case.expected_code is None:
        assert verify_program(case.program, case.arch).program is case.program
        return

    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code

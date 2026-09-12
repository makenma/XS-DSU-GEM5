from pathlib import Path

import pytest

from mesh_ir.agent_planning import ArenaRegion
from mesh_ir.agent_plan_image import build_agent_plan_image
from mesh_ir.agent_surrogate import (
    load_surrogate_profiles,
    validate_surrogate_profiles,
)
from mesh_ir.agent_workload import load_control_plan, load_workload_plan

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"
SINGLE_USER_IMAGES = ("first_pass", "compile_repair", "test_repair", "repair_limit")


def regions():
    return [
        ArenaRegion("INPUT", 0x0000000101000000, 67108864, 32),
        ArenaRegion("PARAMETER", 0x0000000100100000, 8388608, 8),
        ArenaRegion("OUTPUT", 0x0000000105000000, 67108864, 32),
        ArenaRegion("METADATA", 0x0000000109000000, 8388608, 8),
    ]


def build(name):
    workload = load_workload_plan(FIXTURES / f"agent_workload_plan_su_{name}.json")
    registry = load_surrogate_profiles(
        FIXTURES / f"agent_surrogate_profiles_su_{name}.json"
    )
    validate_surrogate_profiles(registry, workload)
    image, _ = build_agent_plan_image(
        workload, None, "EXPLICIT_ONLY", regions(), registry
    )
    return image


def build_twelve_user():
    workload = load_workload_plan(
        FIXTURES / "agent_workload_plan_twelve_user.json"
    )
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_twelve_user.json"
    )
    validate_surrogate_profiles(registry, workload)
    image, _ = build_agent_plan_image(
        workload, None, "EXPLICIT_ONLY", regions(), registry
    )
    return image


def build_su_output_cancel():
    workload = load_workload_plan(
        FIXTURES / "agent_workload_plan_su_output_drain.json"
    )
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_su_output_cancel.json"
    )
    validate_surrogate_profiles(registry, workload)
    control = load_control_plan(
        FIXTURES / "agent_control_plan_cancel_output.json", workload
    )
    image, _ = build_agent_plan_image(
        workload, control, "EXPLICIT_ONLY", regions(), registry
    )
    return image


@pytest.mark.parametrize("name", SINGLE_USER_IMAGES)
def test_single_user_plan_loads_and_validates(name):
    workload = load_workload_plan(FIXTURES / f"agent_workload_plan_su_{name}.json")
    assert workload.users[0].user_id == 0
    assert len(workload.users) == 1
    assert len(workload.users[0].tasks) == 1
    assert workload.users[0].tasks[0].session_id == 1
    assert workload.users[0].tasks[0].kv_handle == 1


@pytest.mark.parametrize("name", SINGLE_USER_IMAGES)
def test_single_user_checked_in_image_matches_rebuild(name):
    stored = (FIXTURES / f"agent_plan_image_su_{name}.bin").read_bytes()
    assert stored == build(name)


def test_twelve_user_plan_mix_and_staggering():
    workload = load_workload_plan(
        FIXTURES / "agent_workload_plan_twelve_user.json"
    )
    assert len(workload.users) == 12
    outcomes = []
    thinks = []
    for user in workload.users:
        assert len(user.tasks) == 1
        thinks.append(user.tasks[0].think_time_ns)
        first = user.tasks[0].rounds[0]
        if first.compile.outcome == "FAIL":
            outcomes.append("compile_repair")
        elif first.test is not None and first.test.outcome == "FAIL":
            outcomes.append("test_repair")
        else:
            outcomes.append("first_pass")
    assert outcomes.count("first_pass") == 6
    assert outcomes.count("compile_repair") == 3
    assert outcomes.count("test_repair") == 3
    assert thinks == [100000 + 100000 * index for index in range(12)]
    keys = [
        round_.requested_profile_key
        for user in workload.users
        for round_ in user.tasks[0].rounds
    ]
    assert len(set(keys)) == 18


def test_twelve_user_checked_in_image_matches_rebuild():
    stored = (FIXTURES / "agent_plan_image_twelve_user.bin").read_bytes()
    assert stored == build_twelve_user()


def test_su_output_cancel_checked_in_image_matches_rebuild():
    stored = (FIXTURES / "agent_plan_image_su_output_cancel.bin").read_bytes()
    assert stored == build_su_output_cancel()


def build_cancel_late_anchor():
    workload = load_workload_plan(
        FIXTURES / "agent_workload_plan_three_user.json"
    )
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_three_user.json"
    )
    control = load_control_plan(
        FIXTURES / "agent_control_plan_cancel_late_anchor.json", workload
    )
    image, _ = build_agent_plan_image(
        workload, control, "EXPLICIT_ONLY", regions(), registry
    )
    return image


def test_cancel_late_anchor_checked_in_image_matches_rebuild():
    stored = (
        FIXTURES / "agent_plan_image_ctrl_cancel_late_anchor.bin"
    ).read_bytes()
    assert stored == build_cancel_late_anchor()


@pytest.mark.parametrize(
    ("name", "expected_rounds"),
    [
        ("first_pass", 1),
        ("compile_repair", 2),
        ("test_repair", 2),
        ("repair_limit", 1),
    ],
)
def test_single_user_round_closure(name, expected_rounds):
    workload = load_workload_plan(FIXTURES / f"agent_workload_plan_su_{name}.json")
    task = workload.users[0].tasks[0]
    assert len(task.rounds) == expected_rounds
    for index, round_ in enumerate(task.rounds):
        assert round_.repair_round == index
        if index == 0:
            assert round_.kv_policy == "INITIAL"
            assert round_.expected_cached_tokens == 0
            assert round_.full_context_tokens == round_.prompt_tokens
            assert round_.full_context_bytes == round_.prompt_bytes
        else:
            assert (
                round_.full_context_tokens
                == round_.expected_cached_tokens + round_.delta_prompt_tokens
            )
            assert (
                round_.full_context_bytes
                == round_.expected_cached_context_bytes + round_.delta_prompt_bytes
            )
            previous = task.rounds[index - 1]
            assert (
                round_.expected_cached_tokens
                == previous.kv_required_tokens_after_round
            )
            assert round_.expected_cached_context_bytes == previous.full_context_bytes
        assert (
            round_.kv_required_tokens_after_round
            == round_.full_context_tokens + round_.output_tokens
        )

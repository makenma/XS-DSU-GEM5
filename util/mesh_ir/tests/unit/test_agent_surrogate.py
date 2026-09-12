import hashlib
import json
from pathlib import Path

import pytest

from mesh_ir.agent_surrogate import (
    PlanError,
    load_surrogate_profiles,
    validate_surrogate_profiles,
)
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.model import canonical_json_bytes

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"


def test_coding_1u_registry_matches_workload():
    registry = load_surrogate_profiles(FIXTURES / "agent_surrogate_profiles_coding_1u.json")
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_coding_1u.json")
    assert len(registry.profiles) == 2
    assert registry.digest == hashlib.sha256(
        canonical_json_bytes(json.loads(
            (FIXTURES / "agent_surrogate_profiles_coding_1u.json").read_text()
        ))
    ).hexdigest()
    validate_surrogate_profiles(registry, workload)


def test_two_user_registry_matches_workload():
    registry = load_surrogate_profiles(FIXTURES / "agent_surrogate_profiles_two_user.json")
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    assert len(registry.profiles) == 5
    assert [(p.program_id, p.profile_id) for p in registry.profiles] == [
        (1, 1), (1, 2), (1, 3), (1, 4), (1, 5)
    ]
    validate_surrogate_profiles(registry, workload)


def mutate_registry(tmp_path, mutation):
    document = json.loads(
        (FIXTURES / "agent_surrogate_profiles_coding_1u.json").read_text()
    )
    mutation(document)
    target = tmp_path / "profiles.json"
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_profile",
        "unsorted_profiles",
        "mixed_publish_chunk",
    ],
)
def test_registry_rejections(tmp_path, case):
    mutations = {
        "duplicate_profile": lambda d: d["profiles"].append(dict(d["profiles"][0])),
        "unsorted_profiles": lambda d: d["profiles"].reverse(),
        "mixed_publish_chunk": lambda d: d["profiles"][1].__setitem__("publish_chunk_bytes", 8192),
    }
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        load_surrogate_profiles(mutate_registry(tmp_path, mutations[case]))


@pytest.mark.parametrize(
    "case",
    [
        "missing_profile",
        "key_mismatch",
        "input_token_mismatch",
        "input_byte_mismatch",
        "output_token_mismatch",
        "output_byte_mismatch",
    ],
)
def test_cross_validation_rejections(tmp_path, case):
    mutations = {
        "missing_profile": lambda d: d["profiles"].pop(1),
        "key_mismatch": lambda d: d["profiles"][1].__setitem__("profile_key", 9999),
        "input_token_mismatch": lambda d: d["profiles"][1].__setitem__("input_tokens", 4991),
        "input_byte_mismatch": lambda d: d["profiles"][1].__setitem__("input_bytes", 49151),
        "output_token_mismatch": lambda d: d["profiles"][1].__setitem__("output_tokens", 319),
        "output_byte_mismatch": lambda d: d["profiles"][1].__setitem__("output_bytes", 12287),
    }
    registry = load_surrogate_profiles(mutate_registry(tmp_path, mutations[case]))
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_coding_1u.json")
    with pytest.raises(PlanError, match="E_WORKLOAD_PLAN_MISMATCH"):
        validate_surrogate_profiles(registry, workload)

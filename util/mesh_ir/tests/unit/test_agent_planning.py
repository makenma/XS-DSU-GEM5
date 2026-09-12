import hashlib
from pathlib import Path

from mesh_ir.agent_planning import (
    build_command_identity_plan,
    build_host_task_identity_plan,
    reachable_stage_keys,
    validate_command_identity_document,
)
from mesh_ir.agent_workload import load_control_plan, load_workload_plan
from mesh_ir.model import canonical_json_bytes

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"
TWO_USER_DIGEST = "afc9cc56e7bed3228009e27e99b42cff882cdf580430f47a4016c786c6113e5e"
CONTROL_DIGEST = "c6f139406f5bc179640865a1add6b57ef0b280a5126fe50b2a7923caa4cc5603"


def load_two_user():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    control = load_control_plan(FIXTURES / "agent_control_plan_two_user.json", workload)
    return workload, control


def tuples(document):
    return [
        (
            record["user_id"],
            record["per_user_command_seq"],
            record["command_kind"],
            record["task_seq"],
            record["repair_round_or_ffff"],
            record["control_ordinal_or_zero"],
            record["target_request_id_or_zero"],
            record["request_id"],
        )
        for record in document["records"]
    ]


def test_command_identity_explicit_only_segments():
    workload, control = load_two_user()
    document = build_command_identity_plan(workload, control, "EXPLICIT_ONLY")
    assert document["schema"] == "command_identity_plan_v1"
    assert document["version"] == 1
    assert document["workload_plan_digest"] == TWO_USER_DIGEST
    assert document["control_plan_digest"] == CONTROL_DIGEST
    assert document["release_policy"] == "EXPLICIT_ONLY"
    assert tuples(document) == [
        (0, 1, "GENERATE", 0, 0, 0, 0, 1),
        (0, 2, "GENERATE", 0, 1, 0, 0, 2),
        (0, 3, "GENERATE", 1, 0, 0, 0, 3),
        (0, 4, "GENERATE", 1, 1, 0, 0, 4),
        (0, 5, "CANCEL", 0, 1, 1, 2, 5),
        (1, 1, "GENERATE", 0, 0, 0, 0, (1 << 32) | 1),
        (1, 2, "RELEASE_SESSION", 0, 0xFFFF, 2, 0, (1 << 32) | 2),
    ]


def test_command_identity_auto_release_inserts_per_task_segment():
    workload, control = load_two_user()
    document = build_command_identity_plan(workload, control, "AUTO_PER_TASK")
    assert tuples(document) == [
        (0, 1, "GENERATE", 0, 0, 0, 0, 1),
        (0, 2, "GENERATE", 0, 1, 0, 0, 2),
        (0, 3, "GENERATE", 1, 0, 0, 0, 3),
        (0, 4, "GENERATE", 1, 1, 0, 0, 4),
        (0, 5, "RELEASE_SESSION", 0, 0xFFFF, 0, 0, 5),
        (0, 6, "RELEASE_SESSION", 1, 0xFFFF, 0, 0, 6),
        (0, 7, "CANCEL", 0, 1, 1, 2, 7),
        (1, 1, "GENERATE", 0, 0, 0, 0, (1 << 32) | 1),
        (1, 2, "RELEASE_SESSION", 0, 0xFFFF, 0, 0, (1 << 32) | 2),
        (1, 3, "RELEASE_SESSION", 0, 0xFFFF, 2, 0, (1 << 32) | 3),
    ]


def test_command_identity_records_carry_session_tuples():
    workload, control = load_two_user()
    document = build_command_identity_plan(workload, control, "EXPLICIT_ONLY")
    generate = document["records"][0]
    assert generate["session_id_or_zero"] == 1
    assert generate["kv_handle_or_zero"] == 1
    assert generate["generation_or_zero"] == 1
    cancel = document["records"][4]
    assert cancel["session_id_or_zero"] == 0
    assert cancel["kv_handle_or_zero"] == 0
    assert cancel["generation_or_zero"] == 0
    release = document["records"][6]
    assert (release["session_id_or_zero"], release["kv_handle_or_zero"], release["generation_or_zero"]) == (999, 999, 1)


def test_command_identity_digest_omits_self():
    workload, control = load_two_user()
    document = build_command_identity_plan(workload, control, "EXPLICIT_ONLY")
    body = {key: value for key, value in document.items() if key != "command_identity_digest"}
    assert document["command_identity_digest"] == hashlib.sha256(
        canonical_json_bytes(body)
    ).hexdigest()
    validate_command_identity_document(document)


def test_reachable_stage_keys_follow_outcome_closure():
    workload, _ = load_two_user()
    assert reachable_stage_keys(workload) == [
        (0, 0, 0, "COMPILE"),
        (0, 0, 0, "LOG_PARSE"),
        (0, 0, 1, "COMPILE"),
        (0, 0, 1, "TEST"),
        (0, 1, 0, "COMPILE"),
        (0, 1, 0, "TEST"),
        (0, 1, 0, "LOG_PARSE"),
        (0, 1, 1, "COMPILE"),
        (0, 1, 1, "TEST"),
        (1, 0, 0, "COMPILE"),
        (1, 0, 0, "TEST"),
    ]


def test_host_task_identity_assigns_dense_ordinal_ids():
    workload, _ = load_two_user()
    document = build_host_task_identity_plan(workload)
    assert document["schema"] == "host_task_identity_plan_v1"
    assert document["workload_plan_digest"] == TWO_USER_DIGEST
    keys = [
        (record["user_id"], record["task_seq"], record["repair_round"], record["stage_kind"])
        for record in document["records"]
    ]
    assert keys == reachable_stage_keys(workload)
    assert [record["host_task_id"] for record in document["records"]] == list(
        range(1, len(keys) + 1)
    )
    body = {key: value for key, value in document.items() if key != "host_task_identity_digest"}
    assert document["host_task_identity_digest"] == hashlib.sha256(
        canonical_json_bytes(body)
    ).hexdigest()


def test_capacity_counts_explicit_only():
    from mesh_ir.agent_planning import capacity_counts

    workload, control = load_two_user()
    assert capacity_counts(workload, control, "EXPLICIT_ONLY") == {
        "U": 2,
        "G": 5,
        "C": 1,
        "L": 1,
        "A": 0,
        "Q": 7,
        "S": 3,
        "T": 1,
        "O": 9,
        "H": 11,
        "K": 0,
        "ML": 0,
        "live_context_bound": 4,
    }


def test_capacity_counts_auto_release_extends_tuples():
    from mesh_ir.agent_planning import capacity_counts

    workload, control = load_two_user()
    assert capacity_counts(workload, control, "AUTO_PER_TASK") == {
        "U": 2,
        "G": 5,
        "C": 1,
        "L": 1,
        "A": 3,
        "Q": 10,
        "S": 3,
        "T": 4,
        "O": 9,
        "H": 11,
        "K": 0,
        "ML": 0,
        "live_context_bound": 4,
    }


def test_capacity_counts_null_control():
    from mesh_ir.agent_planning import capacity_counts

    workload = load_workload_plan(FIXTURES / "agent_workload_plan_coding_1u.json")
    assert capacity_counts(workload, None, "EXPLICIT_ONLY") == {
        "U": 1,
        "G": 2,
        "C": 0,
        "L": 0,
        "A": 0,
        "Q": 2,
        "S": 1,
        "T": 0,
        "O": 4,
        "H": 4,
        "K": 0,
        "ML": 0,
        "live_context_bound": 1,
    }

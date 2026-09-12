import hashlib
import json
from pathlib import Path

from mesh_ir.agent_workload import (
    control_plan_digest_of,
    load_control_plan,
    load_workload_plan,
    null_control_plan_digest,
    worst_metadata_bytes,
)
from mesh_ir.model import canonical_json_bytes

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"
CODING_1U_DIGEST = "28558c51e8726340fc0f41a2c58c92d77f42ed21a3686aaefce39fb2c7c3afee"
TWO_USER_DIGEST = "afc9cc56e7bed3228009e27e99b42cff882cdf580430f47a4016c786c6113e5e"
CONTROL_DIGEST = "c6f139406f5bc179640865a1add6b57ef0b280a5126fe50b2a7923caa4cc5603"
NULL_CONTROL_DIGEST = "49fcb22b9f99dbce8e30a87871b3d738b99e49e4dde346a381004c218ad1676b"


def test_minimal_fixture_loads_with_frozen_digest():
    plan = load_workload_plan(FIXTURES / "agent_workload_plan_coding_1u.json")
    assert plan.digest == CODING_1U_DIGEST
    assert plan.plan_id == "coding_1u_seed_7"
    assert plan.max_repair_rounds == 6
    assert len(plan.users) == 1
    task = plan.users[0].tasks[0]
    assert task.task_seq == 0
    assert task.effective_max_repair_rounds == 6
    assert task.session_id == 1 and task.kv_handle == 1
    first, second = task.rounds
    assert first.repair_round == 0 and second.repair_round == 1
    assert first.prompt_tokens == 2048 and first.delta_prompt_tokens is None
    assert second.delta_prompt_tokens == 2304 and second.prompt_tokens is None
    assert first.compile.outcome == "FAIL" and first.compile.raw_log_bytes == 104857600
    assert first.test is None and first.log_parse is not None
    assert second.compile.outcome == "SUCCESS" and second.test.outcome == "SUCCESS"
    assert second.log_parse is None
    assert first.kv_policy == "INITIAL" and second.kv_policy == "ALLOW_REPREFILL"


def test_digest_is_canonical_json_sha256_of_document():
    plan = load_workload_plan(FIXTURES / "agent_workload_plan_coding_1u.json")
    canonical = canonical_json_bytes(plan.document)
    assert hashlib.sha256(canonical).hexdigest() == CODING_1U_DIGEST
    assert json.loads(canonical) == json.loads(
        (FIXTURES / "agent_workload_plan_coding_1u.json").read_text()
    )


def test_two_user_fixture_task_level_cap_override():
    plan = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    assert plan.digest == TWO_USER_DIGEST
    assert [user.user_id for user in plan.users] == [0, 1]
    first_user_tasks = plan.users[0].tasks
    assert [task.task_seq for task in first_user_tasks] == [0, 1]
    assert first_user_tasks[0].effective_max_repair_rounds == 1
    assert first_user_tasks[1].effective_max_repair_rounds == 1
    assert plan.users[1].tasks[0].effective_max_repair_rounds == 0


def test_two_user_fixture_program_id_constant_per_task():
    plan = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    for user in plan.users:
        for task in user.tasks:
            assert {round.program_id for round in task.rounds} == {1}


def test_control_plan_fixture_loads_with_frozen_digest():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    control = load_control_plan(
        FIXTURES / "agent_control_plan_two_user.json", workload
    )
    assert control.digest == CONTROL_DIGEST
    assert control.workload_plan_digest == TWO_USER_DIGEST
    cancel, release = control.actions
    assert cancel.control_ordinal == 1 and cancel.opcode == "CANCEL"
    assert cancel.trigger["kind"] == "AFTER_SQ_ACCEPT"
    assert (cancel.issuer_user_id, cancel.issuer_task_seq) == (0, 0)
    assert (cancel.target_user_id, cancel.target_task_seq, cancel.target_repair_round) == (0, 0, 1)
    assert release.opcode == "RELEASE_SESSION"
    assert release.trigger["kind"] == "SCENARIO_START"
    assert (release.target_session_id, release.target_kv_handle, release.target_generation) == (999, 999, 1)


def test_control_plan_digest_of_matches_canonical_formula():
    document = json.loads(
        (FIXTURES / "agent_control_plan_two_user.json").read_text()
    )
    assert (
        control_plan_digest_of(document)
        == hashlib.sha256(canonical_json_bytes(document)).hexdigest()
        == CONTROL_DIGEST
    )


def test_null_control_plan_digest_uses_frozen_domain():
    assert null_control_plan_digest(TWO_USER_DIGEST) == NULL_CONTROL_DIGEST
    assert null_control_plan_digest("0" * 64) == hashlib.sha256(
        b"AI_MESH_NULL_CONTROL_PLAN_V1\x00" + bytes(32)
    ).hexdigest()


def test_worst_metadata_bytes_covers_header_and_mandatory_tlv():
    assert worst_metadata_bytes() == 128 + 8 + 64

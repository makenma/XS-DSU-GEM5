import json
from pathlib import Path

import pytest

from mesh_ir.agent_workload import (
    PlanError,
    load_control_plan,
    load_workload_plan,
)

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"
MINIMAL = FIXTURES / "agent_workload_plan_coding_1u.json"
TWO_USER = FIXTURES / "agent_workload_plan_two_user.json"
CONTROL = FIXTURES / "agent_control_plan_two_user.json"


def write(tmp_path, document, name="plan.json"):
    target = tmp_path / name
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def rounds(document, user=0, task=0):
    return document["users"][user]["tasks"][task]["rounds"]


CASES = {
    "user_gap": (TWO_USER, lambda d: d["users"][0].__setitem__("user_id", 1), "users/0/user_id"),
    "task_gap": (TWO_USER, lambda d: d["users"][0]["tasks"][1].__setitem__("task_seq", 2), "tasks/1/task_seq"),
    "round_gap": (MINIMAL, lambda d: rounds(d)[1].__setitem__("repair_round", 2), "repair_round"),
    "item_duplicate": (MINIMAL, lambda d: rounds(d)[1].__setitem__("workload_plan_item_id", 1), "workload_plan_item_id"),
    "item_zero": (MINIMAL, lambda d: rounds(d)[1].__setitem__("workload_plan_item_id", 0), "workload_plan_item_id"),
    "session_duplicate": (TWO_USER, lambda d: d["users"][0]["tasks"][1].__setitem__("session_id", 1), "session_id"),
    "handle_duplicate": (TWO_USER, lambda d: d["users"][0]["tasks"][1].__setitem__("kv_handle", 1), "kv_handle"),
    "unknown_top_field": (MINIMAL, lambda d: d.__setitem__("extra", 1), "extra"),
    "missing_required": (MINIMAL, lambda d: d["users"][0]["tasks"][0].pop("think_time_ns"), "think_time_ns"),
    "bool_as_u64": (MINIMAL, lambda d: d["users"][0]["tasks"][0].__setitem__("think_time_ns", True), "think_time_ns"),
    "u64_small_as_hex": (MINIMAL, lambda d: d["users"][0]["tasks"][0].__setitem__("think_time_ns", "0x00000000000f4240"), "think_time_ns"),
    "u64_big_as_integer": (MINIMAL, lambda d: d["users"][0]["tasks"][0].__setitem__("think_time_ns", 2**53), "think_time_ns"),
    "empty_after_whitespace_norm": (MINIMAL, lambda d: d.__setitem__("plan_id", " "), "plan_id"),
    "generation_not_one": (MINIMAL, lambda d: d["users"][0]["tasks"][0].__setitem__("initial_kv_generation", 2), "initial_kv_generation"),
    "stage_kind_mismatch": (MINIMAL, lambda d: rounds(d)[0]["compile"].__setitem__("kind", "TEST"), "kind"),
    "compile_fail_with_test": (TWO_USER, lambda d: rounds(d)[0].__setitem__("test", dict(rounds(d)[1]["test"])), "test"),
    "success_with_log_parse": (MINIMAL, lambda d: rounds(d)[1].__setitem__("log_parse", rounds(d)[0]["log_parse"]), "log_parse"),
    "fail_at_cap_with_parse": (
        TWO_USER,
        lambda d: (
            rounds(d, 1, 0)[0].__setitem__("compile", dict(rounds(d, 0, 0)[0]["compile"])),
            rounds(d, 1, 0)[0].pop("test"),
            rounds(d, 1, 0)[0].__setitem__("log_parse", dict(rounds(d, 0, 0)[0]["log_parse"])),
        ),
        "log_parse",
    ),
    "round_beyond_cap": (TWO_USER, lambda d: rounds(d, 1, 0).append(dict(rounds(d, 0, 0)[1], repair_round=1, workload_plan_item_id=6)), "repair_round"),
    "round0_policy_not_initial": (MINIMAL, lambda d: rounds(d)[0].__setitem__("kv_policy", "ALLOW_REPREFILL"), "kv_policy"),
    "token_equation": (MINIMAL, lambda d: rounds(d)[1].__setitem__("full_context_tokens", 4993), "full_context_tokens"),
    "byte_equation": (MINIMAL, lambda d: rounds(d)[1].__setitem__("full_context_bytes", 49153), "full_context_bytes"),
    "closure_tokens": (MINIMAL, lambda d: (rounds(d)[1].__setitem__("expected_cached_tokens", 999), rounds(d)[1].__setitem__("full_context_tokens", 3303)), "expected_cached_tokens"),
    "closure_bytes": (MINIMAL, lambda d: (rounds(d)[1].__setitem__("expected_cached_context_bytes", 999), rounds(d)[1].__setitem__("full_context_bytes", 17383)), "expected_cached_context_bytes"),
    "kv_required_mismatch": (MINIMAL, lambda d: rounds(d)[1].__setitem__("kv_required_tokens_after_round", 5313), "kv_required_tokens_after_round"),
    "round0_cached_nonzero": (MINIMAL, lambda d: rounds(d)[0].__setitem__("expected_cached_tokens", 1), "expected_cached_tokens"),
    "round0_full_not_prompt": (MINIMAL, lambda d: rounds(d)[0].__setitem__("full_context_tokens", 2047), "full_context_tokens"),
    "output_capacity_below_generated": (MINIMAL, lambda d: rounds(d)[1].__setitem__("output_capacity_bytes", 12287), "output_capacity_bytes"),
    "metadata_capacity_below_worst": (MINIMAL, lambda d: rounds(d)[1].__setitem__("output_metadata_capacity_bytes", 128), "output_metadata_capacity_bytes"),
    "compile_read_below_generated": (MINIMAL, lambda d: rounds(d)[1]["compile"]["local_io"].__setitem__("read_bytes", 12287), "read_bytes"),
    "fail_write_below_raw_log": (MINIMAL, lambda d: rounds(d)[0]["compile"]["local_io"].__setitem__("write_bytes", 104857599), "write_bytes"),
    "parse_read_below_raw_log": (MINIMAL, lambda d: rounds(d)[0]["log_parse"]["local_io"].__setitem__("read_bytes", 104857599), "read_bytes"),
    "parse_write_below_excerpt": (MINIMAL, lambda d: rounds(d)[0]["log_parse"]["local_io"].__setitem__("write_bytes", 16383), "write_bytes"),
    "program_id_varies_in_task": (MINIMAL, lambda d: rounds(d)[1].__setitem__("program_id", 2), "program_id"),
    "repair_with_prompt_fields": (MINIMAL, lambda d: (rounds(d)[1].pop("delta_prompt_tokens"), rounds(d)[1].pop("delta_prompt_bytes"), rounds(d)[1].__setitem__("prompt_tokens", 1), rounds(d)[1].__setitem__("prompt_bytes", 1)), "delta_prompt_tokens"),
    "round0_with_delta_fields": (MINIMAL, lambda d: (rounds(d)[0].pop("prompt_tokens"), rounds(d)[0].pop("prompt_bytes"), rounds(d)[0].__setitem__("delta_prompt_tokens", 1), rounds(d)[0].__setitem__("delta_prompt_bytes", 1)), "prompt_tokens"),
    "digest_bad_hex": (CONTROL, lambda d: d.__setitem__("workload_plan_digest", "AB" + "0" * 62), "workload_plan_digest"),
    "control_ordinal_gap": (CONTROL, lambda d: d["actions"][1].__setitem__("control_ordinal", 3), "control_ordinal"),
    "control_issuer_task_missing": (CONTROL, lambda d: d["actions"][0].__setitem__("issuer_task_seq", 9), "issuer_task_seq"),
    "control_cancel_target_missing": (CONTROL, lambda d: d["actions"][0].__setitem__("target_task_seq", 9), "target_task_seq"),
    "control_duplicate_cancel_target": (CONTROL, lambda d: d["actions"].append(dict(d["actions"][0], control_ordinal=3)), "target_task_seq"),
    "control_trigger_anchor_missing": (CONTROL, lambda d: d["actions"][0]["trigger"].__setitem__("event_repair_round", 9), "event_repair_round"),
    "control_release_anchor_on_cancel": (
        CONTROL,
        lambda d: (
            d["actions"][1].__setitem__("trigger", {"kind": "AFTER_RELEASE_TERMINAL", "after_control_ordinal": 1}),
        ),
        "after_control_ordinal",
    ),
    "control_cancel_self_target": (CONTROL, lambda d: d["actions"][0].__setitem__("target_repair_round", 5), "target_repair_round"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_workload_and_control_rejections(tmp_path, case):
    fixture, mutate, needle = CASES[case]
    document = json.loads(fixture.read_text())
    mutate(document)
    if fixture == CONTROL:
        with pytest.raises(PlanError, match="E_AGENT_PLAN") as error:
            load_control_plan(write(tmp_path, document), load_workload_plan(TWO_USER))
    else:
        with pytest.raises(PlanError, match="E_AGENT_PLAN") as error:
            load_workload_plan(write(tmp_path, document))
    assert needle in str(error.value)


RAW_CASES = {
    "duplicate_json_key": (
        MINIMAL,
        '"plan_id": "coding_1u_seed_7"',
        '"plan_id": "a", "plan_id": "b"',
    ),
    "nan_literal": (MINIMAL, '"think_time_ns": 1000000', '"think_time_ns": NaN'),
    "infinity_literal": (MINIMAL, '"max_repair_rounds": 6', '"max_repair_rounds": Infinity'),
    "non_nfc_string": (MINIMAL, '"coding_1u_seed_7"', '"cafe\\u0301"'),
}


@pytest.mark.parametrize("case", sorted(RAW_CASES))
def test_strict_parser_rejections(tmp_path, case):
    fixture, old, new = RAW_CASES[case]
    text = fixture.read_text().replace(old, new)
    target = tmp_path / "plan.json"
    target.write_text(text, encoding="utf-8")
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        load_workload_plan(target)

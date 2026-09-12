import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
CONFIG = REPO / "configs" / "example" / "ai_mesh" / "run_gate4_protocol.py"
FIXTURES = REPO / "tests" / "gem5" / "ai_mesh" / "fixtures" / "gate4"
sys.path.insert(0, str(CONFIG.parent))

from gate3_acceptance import parse_facts_tsv as parse_gate3_facts

SIM_TICK_LIMIT = 400000000000

USER0_ROUND0 = 1
USER0_FINAL = 2
USER1_ROUND0 = (1 << 32) | 1
USER1_CANCEL = (1 << 32) | 2
USER2_ROUND0 = (2 << 32) | 1
USER2_FINAL = (2 << 32) | 2
USER2_CANCEL = (2 << 32) | 3
USER0_RELEASE = 3

SCENARIOS = {
    "cancel_live": {
        "control_request": USER1_CANCEL,
        "target_request": USER1_ROUND0,
        "command_status": "SUCCESS",
        "target_status": "CANCELLED",
        "join_winner": "CANCEL_WINS",
        "completed": 2,
        "failed": 1,
        "terminals": {
            USER0_FINAL: "BUSINESS_DONE",
            USER1_ROUND0: "BUSINESS_FAILED",
            USER2_FINAL: "BUSINESS_DONE",
        },
    },
    "cancel_late": {
        "control_request": USER2_CANCEL,
        "target_request": USER2_ROUND0,
        "command_status": "ALREADY_TERMINAL",
        "target_status": "SUCCESS",
        "join_winner": None,
        "completed": 3,
        "failed": 0,
        "terminals": {
            USER0_FINAL: "BUSINESS_DONE",
            USER1_ROUND0: "BUSINESS_DONE",
            USER2_FINAL: "BUSINESS_DONE",
        },
    },
    "release_notfound": {
        "control_request": USER0_RELEASE,
        "target_request": None,
        "command_status": "NOT_FOUND",
        "target_status": None,
        "join_winner": None,
        "completed": 3,
        "failed": 0,
        "terminals": {
            USER0_FINAL: "BUSINESS_DONE",
            USER1_ROUND0: "BUSINESS_DONE",
            USER2_FINAL: "BUSINESS_DONE",
        },
    },
}


def run_scenario(name, outdir, *extra):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AI_MESH_")
    }
    return subprocess.run(
        [
            str(GEM5),
            f"--outdir={outdir}",
            str(CONFIG),
            "--plan-image",
            str(FIXTURES / f"agent_plan_image_ctrl_{name}.bin"),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
            *extra,
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )


def parse_facts(outdir):
    facts_path = Path(outdir) / "gate4_facts.tsv"
    assert facts_path.exists()
    return (facts_path,) + parse_gate3_facts(facts_path)


def events_of(events, kind):
    return [event for event in events if event["kind"] == kind]


def cq_consume(events, request_id):
    matches = [
        event
        for event in events
        if event["kind"] == "CQ_CONSUME"
        and event["request_id"] == request_id
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_gate4_control_scenario_over_real_axi(name, tmp_path):
    expected = SCENARIOS[name]
    run = run_scenario(name, tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path, events, final, metrics, fatal = parse_facts(tmp_path / "run")
    assert fatal is None
    assert not any(event["kind"] == "FATAL" for event in events)

    command = cq_consume(events, expected["control_request"])
    assert command["status"] == expected["command_status"]

    if expected["target_request"] is not None:
        target = cq_consume(events, expected["target_request"])
        assert target["status"] == expected["target_status"]

    joins = events_of(events, "CANCEL_JOIN_RESOLVED")
    if expected["join_winner"] is None:
        assert joins == []
    else:
        assert [event["status"] for event in joins] == [expected["join_winner"]]
        assert joins[0]["request_id"] == expected["control_request"]

    waiters = events_of(events, "CONTROL_TERMINAL")
    if expected["join_winner"] is None:
        assert [event["status"] for event in waiters] == [
            expected["command_status"]
        ]
        assert waiters[0]["request_id"] == expected["control_request"]
    else:
        assert waiters == []

    terminals = events_of(events, "TASK_TERMINAL")
    terminal_status = {
        event["request_id"]: event["status"] for event in terminals
    }
    assert terminal_status == expected["terminals"]
    assert metrics["completed_tasks"] == expected["completed"]
    assert metrics["failed_tasks"] == expected["failed"]
    assert metrics["npu_fabric_raw_log_bytes"] == 0

    assert final is not None
    assert not final["fatal"]

    repeat = run_scenario(name, tmp_path / "repeat")
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (
        (tmp_path / "repeat" / "gate4_facts.tsv").read_bytes()
        == facts_path.read_bytes()
    )


def test_gate4_cancel_live_suppresses_target_business_pipeline():
    run = run_scenario("cancel_live", Path("/tmp/gate4_control_cancel_live_e2e"))
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
    _, events, _, _, fatal = parse_facts(
        Path("/tmp/gate4_control_cancel_live_e2e")
    )
    assert fatal is None

    user1_stages = [
        event
        for event in events
        if event["kind"] in ("HOST_STAGE_START", "HOST_STAGE_DONE")
        and event["request_id"] == USER1_ROUND0
    ]
    assert user1_stages == []

    generates = [
        event
        for event in events
        if event["kind"] == "SQ_CONSUME"
        and event["request_id"] == USER1_ROUND0
    ]
    assert len(generates) == 1

    cancel_writes = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["channel"] == "AW"
        and event["control"] == "OUTPUT"
    ]
    assert all(
        event["request_id"] != USER1_ROUND0 for event in cancel_writes
    )


def test_gate4_cancel_late_target_business_continues():
    run = run_scenario("cancel_late", Path("/tmp/gate4_control_cancel_late_e2e"))
    assert run.returncode == 0, run.stdout + run.stderr
    _, events, _, _, fatal = parse_facts(
        Path("/tmp/gate4_control_cancel_late_e2e")
    )
    assert fatal is None

    user2_stages = [
        event["object"]
        for event in events
        if event["kind"] == "HOST_STAGE_START"
        and event["request_id"] in (USER2_ROUND0, USER2_FINAL)
    ]
    assert user2_stages == ["COMPILE", "TEST", "LOG_PARSE", "COMPILE", "TEST"]
    assert not any(
        event["kind"] == "CANCEL_JOIN_RESOLVED" for event in events
    )


OUTPUT_CANCEL_TARGET = 1
OUTPUT_CANCEL_COMMAND = 2
OUTPUT_CANCEL_CHUNK_BYTES = 4096
OUTPUT_CANCEL_PREFIX_BYTES = 8192


def run_output_cancel(outdir):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AI_MESH_")
    }
    return subprocess.run(
        [
            str(GEM5),
            f"--outdir={outdir}",
            str(CONFIG),
            "--plan-image",
            str(FIXTURES / "agent_plan_image_su_output_cancel.bin"),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_gate4_cancel_output_partial_prefix_suppresses_remainder():
    outdir = Path("/tmp/gate4_control_cancel_output_e2e")
    run = run_output_cancel(outdir)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
    facts_path, events, final, metrics, fatal = parse_facts(outdir)
    assert fatal is None

    def of_kind(kind):
        return [event for event in events if event["kind"] == kind]

    resolve = of_kind("CONTROL_RESOLVE")
    ready = of_kind("CONTROL_READY")
    joins = of_kind("CANCEL_JOIN_RESOLVED")
    assert [event["status"] for event in resolve] == ["SUCCESS"]
    assert [event["status"] for event in joins] == ["CANCEL_WINS"]
    assert joins[0]["request_id"] == OUTPUT_CANCEL_COMMAND

    consume = {
        event["request_id"]: event["status"]
        for event in of_kind("CQ_CONSUME")
    }
    assert consume[OUTPUT_CANCEL_TARGET] == "CANCELLED"
    assert consume[OUTPUT_CANCEL_COMMAND] == "SUCCESS"

    output_aw = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["channel"] == "AW"
        and event["control"] == "OUTPUT"
    ]
    output_b = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["channel"] == "B"
        and event["control"] == "OUTPUT"
    ]
    assert len(output_aw) == 2
    assert len(output_b) == 2
    assert all(event["tick"] <= resolve[0]["tick"] for event in output_aw)
    assert (
        output_b[0]["tick"] <= ready[0]["tick"] <= output_aw[-1]["tick"]
    )

    terminals = of_kind("TASK_TERMINAL")
    assert [(event["request_id"], event["status"]) for event in terminals] == [
        (OUTPUT_CANCEL_TARGET, "BUSINESS_FAILED")
    ]
    assert terminals[0]["tick"] >= joins[0]["tick"]
    assert not of_kind("HOST_STAGE_START")
    assert metrics["output_cancel_requests"] == 1
    assert (
        metrics["output_cancel_prefix_bytes"] == OUTPUT_CANCEL_PREFIX_BYTES
    )
    assert metrics["output_cancel_prefix_bytes"] % OUTPUT_CANCEL_CHUNK_BYTES == 0
    assert metrics["failed_tasks"] == 1
    assert metrics["completed_tasks"] == 0

    assert final is not None
    assert not final["fatal"]

    repeat = run_output_cancel(Path("/tmp/gate4_control_cancel_output_repeat"))
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (
        Path("/tmp/gate4_control_cancel_output_repeat/gate4_facts.tsv")
        .read_bytes()
        == facts_path.read_bytes()
    )

USER0_CANCEL = 3


def test_gate4_concurrent_cancel_joins_resolve_independently(tmp_path):
    run = run_scenario("multi_cancel", tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path, events, final, metrics, fatal = parse_facts(tmp_path / "run")
    assert fatal is None

    joins = events_of(events, "CANCEL_JOIN_RESOLVED")
    assert [(event["request_id"], event["status"]) for event in joins] == [
        (USER0_CANCEL, "CANCEL_WINS"),
        (USER1_CANCEL, "CANCEL_WINS"),
    ]

    terminals = {
        event["request_id"]: event for event in events_of(events, "TASK_TERMINAL")
    }
    assert terminals[USER0_ROUND0]["status"] == "BUSINESS_FAILED"
    assert terminals[USER1_ROUND0]["status"] == "BUSINESS_FAILED"
    assert terminals[USER2_FINAL]["status"] == "BUSINESS_DONE"
    for event in events_of(events, "TASK_TERMINAL"):
        assert event["tick"] % 1000 == 0, event

    first_facts = facts_path.read_bytes()
    repeat = run_scenario("multi_cancel", tmp_path / "repeat")
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (tmp_path / "repeat" / "gate4_facts.tsv").read_bytes() == first_facts


def test_gate4_concurrent_cancel_illegal_combination_fatals(tmp_path):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AI_MESH_")
    }
    run = subprocess.run(
        [
            str(GEM5),
            "--outdir=" + str(tmp_path / "run"),
            str(CONFIG),
            "--plan-image",
            str(FIXTURES / "agent_plan_image_ctrl_multi_cancel.bin"),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
            "--mutate-cancel-command-status",
            "1:ALREADY_TERMINAL",
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert "AI_MESH_GATE3_INFRA_FATAL" in run.stdout
    _, events, final, metrics, fatal = parse_facts(tmp_path / "run")
    assert fatal is not None
    assert fatal["symbol"] == "E_AGENT_PROTOCOL_FATAL"
    fatal_tick = min(
        event["tick"] for event in events_of(events, "FATAL")
    )
    for event in events:
        if event["tick"] < fatal_tick and event["kind"] in (
            "TASK_TERMINAL",
            "HOST_STAGE_ENQUEUE",
            "HOST_STAGE_START",
        ):
            raise AssertionError(f"premature business event {event}")

def test_gate4_cancel_before_target_uses_standalone_waiter(tmp_path):
    run = run_scenario("cancel_before_target", tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path, events, final, metrics, fatal = parse_facts(tmp_path / "run")
    assert fatal is None

    command = cq_consume(events, USER1_CANCEL)
    assert command["status"] == "NOT_FOUND"
    terminals = events_of(events, "CONTROL_TERMINAL")
    assert [
        (event["request_id"], event["status"]) for event in terminals
    ] == [(USER1_CANCEL, "NOT_FOUND")]
    assert events_of(events, "CANCEL_JOIN_RESOLVED") == []

    target = cq_consume(events, USER1_ROUND0)
    assert target["status"] == "SUCCESS"
    task_terminals = {
        event["request_id"]: event["status"]
        for event in events_of(events, "TASK_TERMINAL")
    }
    assert task_terminals == {
        USER0_FINAL: "BUSINESS_DONE",
        USER1_ROUND0: "BUSINESS_DONE",
        USER2_FINAL: "BUSINESS_DONE",
    }
    for event in events_of(events, "TASK_TERMINAL"):
        assert event["tick"] % 1000 == 0, event

    first_facts = facts_path.read_bytes()
    repeat = run_scenario("cancel_before_target", tmp_path / "repeat")
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (tmp_path / "repeat" / "gate4_facts.tsv").read_bytes() == first_facts

def test_gate4_cancel_of_future_round_uses_standalone_waiter(tmp_path):
    run = run_scenario("cancel_future_round", tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path, events, final, metrics, fatal = parse_facts(tmp_path / "run")
    assert fatal is None

    command = cq_consume(events, USER0_CANCEL)
    assert command["status"] == "NOT_FOUND"
    terminals = events_of(events, "CONTROL_TERMINAL")
    assert [
        (event["request_id"], event["status"]) for event in terminals
    ] == [(USER0_CANCEL, "NOT_FOUND")]
    assert events_of(events, "CANCEL_JOIN_RESOLVED") == []

    future = cq_consume(events, USER0_FINAL)
    assert future["status"] == "SUCCESS"
    task_terminals = {
        event["request_id"]: event["status"]
        for event in events_of(events, "TASK_TERMINAL")
    }
    assert task_terminals == {
        USER0_FINAL: "BUSINESS_DONE",
        USER1_ROUND0: "BUSINESS_DONE",
        USER2_FINAL: "BUSINESS_DONE",
    }
    for event in events_of(events, "TASK_TERMINAL"):
        assert event["tick"] % 1000 == 0, event

    first_facts = facts_path.read_bytes()
    repeat = run_scenario("cancel_future_round", tmp_path / "repeat")
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (tmp_path / "repeat" / "gate4_facts.tsv").read_bytes() == first_facts

@pytest.mark.parametrize(
    "scenario,cancel_request",
    [("cancel_past_round", USER0_CANCEL),
     ("cancel_cross_past_round", USER0_CANCEL)],
)
def test_gate4_cancel_of_terminal_round_is_already_terminal(
    tmp_path, scenario, cancel_request
):
    run = run_scenario(scenario, tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path, events, final, metrics, fatal = parse_facts(tmp_path / "run")
    assert fatal is None

    command = cq_consume(events, cancel_request)
    assert command["status"] == "ALREADY_TERMINAL"
    terminals = events_of(events, "CONTROL_TERMINAL")
    assert [
        (event["request_id"], event["status"]) for event in terminals
    ] == [(cancel_request, "ALREADY_TERMINAL")]
    assert events_of(events, "CANCEL_JOIN_RESOLVED") == []

    target = cq_consume(events, USER0_ROUND0)
    assert target["status"] == "SUCCESS"
    task_terminals = {
        event["request_id"]: event["status"]
        for event in events_of(events, "TASK_TERMINAL")
    }
    assert task_terminals == {
        USER0_FINAL: "BUSINESS_DONE",
        USER1_ROUND0: "BUSINESS_DONE",
        USER2_FINAL: "BUSINESS_DONE",
    }
    for event in events_of(events, "TASK_TERMINAL"):
        assert event["tick"] % 1000 == 0, event

    first_facts = facts_path.read_bytes()
    repeat = run_scenario(scenario, tmp_path / "repeat")
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (tmp_path / "repeat" / "gate4_facts.tsv").read_bytes() == first_facts


USER1_LATE_ANCHOR_CANCEL = (1 << 32) | 2


def _late_anchor_join(tmp_path, *extra):
    run = run_scenario("cancel_late_anchor", tmp_path / "run", *extra)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
    facts_path, events, final, metrics, fatal = parse_facts(tmp_path / "run")
    assert fatal is None
    return facts_path, events, metrics


def test_gate4_late_anchor_join_lets_target_success_win(tmp_path):
    facts_path, events, metrics = _late_anchor_join(tmp_path)

    command = cq_consume(events, USER1_LATE_ANCHOR_CANCEL)
    assert command["status"] == "ALREADY_TERMINAL"
    target = cq_consume(events, USER1_ROUND0)
    assert target["status"] == "SUCCESS"

    assert [
        (event["request_id"], event["status"])
        for event in events_of(events, "CANCEL_JOIN_RESOLVED")
    ] == [(USER1_LATE_ANCHOR_CANCEL, "TARGET_SUCCESS_WINS")]
    assert events_of(events, "CONTROL_TERMINAL") == []

    latched = [
        event
        for event in events
        if event["kind"] == "GENERATE_TERMINAL_LATCHED"
        and event["request_id"] == USER1_ROUND0
    ]
    consumed_command = [
        event
        for event in events
        if event["kind"] == "SQ_CONSUME"
        and event["request_id"] == USER1_LATE_ANCHOR_CANCEL
    ]
    assert [event["tick"] for event in latched] == [
        min(event["tick"] for event in latched)
    ]
    assert latched[0]["tick"] < consumed_command[0]["tick"]

    user1_stages = [
        event["object"]
        for event in events_of(events, "HOST_STAGE_START")
        if event["request_id"] == USER1_ROUND0
    ]
    assert user1_stages == ["COMPILE", "TEST"]
    assert {
        event["request_id"]: event["status"]
        for event in events_of(events, "TASK_TERMINAL")
    } == {
        USER0_FINAL: "BUSINESS_DONE",
        USER1_ROUND0: "BUSINESS_DONE",
        USER2_FINAL: "BUSINESS_DONE",
    }
    assert metrics["completed_tasks"] == 3
    assert metrics["failed_tasks"] == 0

    first_facts = facts_path.read_bytes()
    repeat = run_scenario("cancel_late_anchor", tmp_path / "repeat")
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (tmp_path / "repeat" / "gate4_facts.tsv").read_bytes() == first_facts


def test_gate4_late_anchor_join_lets_target_error_win(tmp_path):
    _, events, metrics = _late_anchor_join(
        tmp_path, "--inject-output-b-error", str(USER1_ROUND0)
    )

    command = cq_consume(events, USER1_LATE_ANCHOR_CANCEL)
    assert command["status"] == "ALREADY_TERMINAL"
    target = cq_consume(events, USER1_ROUND0)
    assert target["status"] == "ERROR"

    assert [
        (event["request_id"], event["status"])
        for event in events_of(events, "CANCEL_JOIN_RESOLVED")
    ] == [(USER1_LATE_ANCHOR_CANCEL, "TARGET_ERROR_WINS")]
    assert events_of(events, "CONTROL_TERMINAL") == []
    assert metrics["output_b_error_requests"] == 1
    assert metrics["failed_tasks"] == 1
    assert {
        event["request_id"]: event["status"]
        for event in events_of(events, "TASK_TERMINAL")
    } == {
        USER0_FINAL: "BUSINESS_DONE",
        USER1_ROUND0: "BUSINESS_FAILED",
        USER2_FINAL: "BUSINESS_DONE",
    }

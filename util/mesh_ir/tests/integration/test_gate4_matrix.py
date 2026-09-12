import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
CONFIG = REPO / "configs" / "example" / "ai_mesh" / "run_gate4_protocol.py"
AGENT_CONFIG = REPO / "configs" / "example" / "ai_mesh" / "run_gate4_agent.py"
FIXTURES = REPO / "tests" / "gem5" / "ai_mesh" / "fixtures" / "gate4"
sys.path.insert(0, str(CONFIG.parent))

from gate3_acceptance import parse_facts_tsv as parse_gate3_facts

TICKS_PER_NS = 1000
SIM_TICK_LIMIT = 2000000000000


def run_plan(outdir, plan_image, *extra):
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
            str(FIXTURES / plan_image),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
            *extra,
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=900,
    )


def facts_of(outdir):
    facts_path = Path(outdir) / "gate4_facts.tsv"
    assert facts_path.exists()
    return facts_path, *parse_gate3_facts(facts_path)


def run_agent(outdir, *extra):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AI_MESH_")
    }
    return subprocess.run(
        [
            str(GEM5),
            f"--outdir={outdir}",
            str(AGENT_CONFIG),
            "--runtime-config",
            str(FIXTURES / "agent_runtime_config_twelve_user.yaml"),
            "--surrogate-profiles",
            str(FIXTURES / "agent_surrogate_profiles_twelve_user.json"),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
            *extra,
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def stage_queue_waits(events):
    enqueue = {}
    starts = {}
    for event in events:
        key = (event["object"], event["request_id"])
        if event["kind"] == "HOST_STAGE_ENQUEUE":
            enqueue[key] = event["tick"]
        elif event["kind"] == "HOST_STAGE_START":
            starts[key] = event["tick"]
    return [starts[key] - enqueue[key] for key in starts if key in enqueue]


def test_gate4_twelve_user_scale_with_queue_and_token_pressure(tmp_path):
    knobs = (
        "--host-compute-tokens",
        "6",
        "--host-service-queue-depth",
        "2",
        "--host-aging-threshold-ns",
        "5000000",
    )
    run = run_plan(tmp_path / "run", "agent_plan_image_twelve_user.bin", *knobs)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None

    terminals = [e for e in events if e["kind"] == "TASK_TERMINAL"]
    assert [e["status"] for e in terminals] == ["BUSINESS_DONE"] * 12
    assert metrics["completed_tasks"] + metrics["failed_tasks"] == 12
    assert metrics["completed_tasks"] == 12
    assert metrics["failed_tasks"] == 0
    assert metrics["infra_failed_tasks"] == 0

    waits = stage_queue_waits(events)
    assert len(waits) == 39
    assert sum(1 for gap in waits if gap > 0) >= 3
    assert max(waits) >= 8 * 1000000 * TICKS_PER_NS
    assert metrics["aging_reservations"] >= 1
    assert metrics["agent_live_objects"] == 0
    assert metrics["npu_fabric_raw_log_bytes"] == 0
    assert metrics["raw_log_bytes"] == 6 * 32768
    assert metrics["excerpt_bytes"] == 6 * 4096
    assert final is not None and not final["fatal"]

    first = facts_path.read_bytes()
    for repeat in ("repeat2", "repeat3"):
        again = run_plan(tmp_path / repeat, "agent_plan_image_twelve_user.bin", *knobs)
        assert again.returncode == 0, again.stdout + again.stderr
        assert (tmp_path / repeat / "gate4_facts.tsv").read_bytes() == first


def test_gate4_agent_twelve_user_token_pressure_aging_completes(tmp_path):
    knobs = ("--host-compute-tokens", "6", "--host-aging-threshold-ns", "5000000")
    started = time.monotonic()
    run = run_agent(tmp_path / "run", *knobs)
    elapsed = time.monotonic() - started
    assert run.returncode == 0, run.stdout + run.stderr
    assert elapsed < 120
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    _, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None
    terminals = [e for e in events if e["kind"] == "TASK_TERMINAL"]
    assert [e["status"] for e in terminals] == ["BUSINESS_DONE"] * 12
    assert metrics["completed_tasks"] == 12
    assert metrics["failed_tasks"] == 0
    assert metrics["infra_failed_tasks"] == 0
    assert metrics["aging_reservations"] >= 1
    waits = stage_queue_waits(events)
    assert len(waits) == 39
    assert max(waits) >= 5 * 1000000 * TICKS_PER_NS
    assert metrics["agent_live_objects"] == 0
    assert final is not None and not final["fatal"]


def test_gate4_agent_token_pool_below_plan_requirement_fails_at_plan_build(
    tmp_path,
):
    run = run_agent(tmp_path / "run", "--host-compute-tokens", "5")
    assert run.returncode != 0
    assert "E_CAPACITY_PLAN" in run.stderr + run.stdout
    assert not (tmp_path / "run" / "gate4_facts.tsv").exists()


def test_gate4_protocol_token_pool_below_plan_requirement_fails_at_startup(
    tmp_path,
):
    run = run_plan(
        tmp_path / "run", "agent_plan_image_twelve_user.bin",
        "--host-compute-tokens", "5",
    )
    assert run.returncode != 0
    assert "compute tokens" in run.stderr + run.stdout
    assert not (tmp_path / "run" / "gate4_facts.tsv").exists()


def test_gate4_stop_after_completed_tasks_cutoff(tmp_path):
    run = run_plan(
        tmp_path / "run",
        "agent_plan_image_twelve_user.bin",
        "--host-compute-tokens",
        "6",
        "--host-service-queue-depth",
        "2",
        "--stop-after-completed-tasks",
        "4",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    _, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None
    terminals = [e for e in events if e["kind"] == "TASK_TERMINAL"]
    finals = (
        metrics["completed_tasks"]
        + metrics["failed_tasks"]
        + metrics["infra_failed_tasks"]
    )
    assert finals == len(terminals)
    assert 4 <= finals <= 16
    assert finals > 4 or len(terminals) == 4

    think_ready = [e for e in events if e["kind"] == "TASK_THINK_READY"]
    doorbells = {
        e["request_id"]
        for e in events
        if e["kind"] == "AXI_ACCEPT"
        and e["control"] == "SQ_DOORBELL"
        and e["channel"] == "AW"
        and e["request_id"] is not None
    }
    started_users = {request >> 32 for request in (e["request_id"] for e in think_ready)}
    doorbell_users = {request >> 32 for request in doorbells}
    assert doorbell_users == started_users
    suppressed = [e for e in events if e["kind"] == "TASK_SUPPRESSED"]
    if suppressed:
        suppressed_users = {e["request_id"] >> 32 for e in suppressed}
        assert not (suppressed_users & doorbell_users)
    assert final is not None and not final["fatal"]
    assert metrics["agent_live_objects"] == 0


def test_gate4_stop_accepting_at_tick_cutoff(tmp_path):
    stop_tick = 350000 * TICKS_PER_NS
    run = run_plan(
        tmp_path / "run",
        "agent_plan_image_twelve_user.bin",
        "--stop-accepting-at-tick",
        str(stop_tick),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    _, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None
    think_ready = [e for e in events if e["kind"] == "TASK_THINK_READY"]
    suppressed = [e for e in events if e["kind"] == "TASK_SUPPRESSED"]
    assert all(e["tick"] < stop_tick for e in think_ready)
    assert suppressed
    assert all(e["tick"] >= stop_tick for e in suppressed)
    started = {e["request_id"] >> 32 for e in think_ready}
    finished = {
        e["request_id"] >> 32 for e in events if e["kind"] == "TASK_TERMINAL"
    }
    assert started == finished
    assert (
        metrics["completed_tasks"]
        + metrics["failed_tasks"]
        + metrics["infra_failed_tasks"]
        == len(finished)
    )
    assert final is not None and not final["fatal"]
    assert metrics["agent_live_objects"] == 0


def test_gate4_cutoff_after_completion_suppresses_anchored_control(tmp_path):
    stop_tick = 1500000000
    run = run_plan(
        tmp_path / "run",
        "agent_plan_image_ctrl_cancel_live.bin",
        "--stop-accepting-at-tick",
        str(stop_tick),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    _, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None
    assert final is not None and not final["fatal"]
    assert metrics["agent_live_objects"] == 0
    assert events, "quiescent drain must publish final facts"

    control_terminals = [e for e in events if e["kind"] == "CONTROL_TERMINAL"]
    assert [e["status"] for e in control_terminals] == [
        "SUPPRESSED_BY_RUN_CUTOFF"
    ]
    assert control_terminals[0]["tick"] >= stop_tick
    cancel_command_request = (1 << 32) | 2
    assert control_terminals[0]["request_id"] == cancel_command_request
    assert not [e for e in events if e["kind"] == "CONTROL_READY"]
    assert not [
        e
        for e in events
        if e["kind"] == "AXI_ACCEPT"
        and e["control"] == "SQ_DOORBELL"
        and e["channel"] == "AW"
        and e["request_id"] == cancel_command_request
    ]

    think_ready = [e for e in events if e["kind"] == "TASK_THINK_READY"]
    suppressed = [e for e in events if e["kind"] == "TASK_SUPPRESSED"]
    started_users = {e["request_id"] >> 32 for e in think_ready}
    assert len(suppressed) >= 1
    assert 1 not in started_users
    finished = {
        e["request_id"] >> 32 for e in events if e["kind"] == "TASK_TERMINAL"
    }
    assert started_users == finished
    assert (
        metrics["completed_tasks"]
        + metrics["failed_tasks"]
        + metrics["infra_failed_tasks"]
        == len(finished)
    )


def test_gate4_zero_request_cutoff_quiesces(tmp_path):
    run = run_plan(
        tmp_path / "run",
        "agent_plan_image_su_first_pass.bin",
        "--stop-accepting-at-tick",
        "1",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    _, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None
    assert final is not None and not final["fatal"]
    assert metrics["frontend_drained"] == 1
    assert not [e for e in events if e["kind"] == "TASK_THINK_READY"]
    assert not [
        e
        for e in events
        if e["kind"] == "AXI_ACCEPT" and e["control"] == "SQ_DOORBELL"
    ]
    assert not [e for e in events if e["kind"] == "TASK_TERMINAL"]
    assert metrics["completed_tasks"] == 0
    assert metrics["failed_tasks"] == 0
    assert metrics["infra_failed_tasks"] == 0
    assert metrics["agent_live_objects"] == 0


def test_gate4_output_b_error_single_cq_partial_prefix(tmp_path):
    faulted_request = (1 << 32) | 1
    run = run_plan(
        tmp_path / "run",
        "agent_plan_image_three_user.bin",
        "--inject-output-b-error",
        str(faulted_request),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None

    terminals = [e for e in events if e["kind"] == "TASK_TERMINAL"]
    statuses = {
        e["request_id"] >> 32: e["status"] for e in terminals
    }
    assert statuses[1] == "BUSINESS_FAILED"
    for user in (0, 2):
        assert statuses[user] == "BUSINESS_DONE"
    faulted_stages = [
        e
        for e in events
        if e["kind"] in ("HOST_STAGE_START", "HOST_STAGE_ENQUEUE")
        and e["request_id"] == faulted_request
    ]
    assert faulted_stages == []

    consumed = [
        e
        for e in events
        if e["kind"] == "CQ_CONSUME" and e["request_id"] == faulted_request
    ]
    assert len(consumed) == 1
    assert consumed[0]["status"] == "ERROR"
    assert metrics["output_b_error_requests"] == 1
    assert metrics["output_b_error_prefix_bytes"] == 0
    assert metrics["completed_tasks"] == 2
    assert metrics["failed_tasks"] == 1
    assert metrics["agent_live_objects"] == 0
    assert final is not None and not final["fatal"]

    repeat = run_plan(
        tmp_path / "repeat",
        "agent_plan_image_three_user.bin",
        "--inject-output-b-error",
        str(faulted_request),
    )
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (
        tmp_path / "repeat" / "gate4_facts.tsv"
    ).read_bytes() == facts_path.read_bytes()


def test_gate4_host_fault_object_produce_infra_failed(tmp_path):
    run = run_plan(
        tmp_path / "run",
        "agent_plan_image_su_compile_repair.bin",
        "--host-fault-site",
        "object_produce",
        "--host-fault-task",
        "0",
        "--host-fault-round",
        "0",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    _, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None
    faults = [e for e in events if e["kind"] == "HOST_FAULT"]
    assert [(e["object"], e["status"]) for e in faults] == [
        ("OBJECT_PRODUCE", "E_HOST_LOCAL_OBJECT")
    ]
    terminals = [e for e in events if e["kind"] == "TASK_TERMINAL"]
    assert [e["status"] for e in terminals] == ["INFRA_FAILED"]
    assert metrics["infra_failed_tasks"] == 1
    assert metrics["completed_tasks"] == 0
    assert metrics["failed_tasks"] == 0
    assert metrics["raw_log_bytes"] == 0
    assert metrics["agent_live_objects"] == 0
    assert final is not None and not final["fatal"]


def test_gate4_host_fault_object_read_infra_failed(tmp_path):
    run = run_plan(
        tmp_path / "run",
        "agent_plan_image_su_first_pass.bin",
        "--host-fault-site",
        "object_read",
        "--host-fault-task",
        "0",
        "--host-fault-round",
        "0",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    _, events, final, metrics, fatal = facts_of(tmp_path / "run")
    assert fatal is None
    faults = [e for e in events if e["kind"] == "HOST_FAULT"]
    assert [(e["object"], e["status"]) for e in faults] == [
        ("OBJECT_READ", "E_HOST_LOCAL_OBJECT")
    ]
    assert [
        e["status"] for e in events if e["kind"] == "TASK_TERMINAL"
    ] == ["INFRA_FAILED"]
    assert metrics["infra_failed_tasks"] == 1
    assert metrics["agent_live_objects"] == 0
    assert final is not None and not final["fatal"]

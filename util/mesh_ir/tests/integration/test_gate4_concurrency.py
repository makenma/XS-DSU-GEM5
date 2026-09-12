import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
CONFIG = REPO / "configs" / "example" / "ai_mesh" / "run_gate4_protocol.py"
FIXTURES = REPO / "tests" / "gem5" / "ai_mesh" / "fixtures" / "gate4"
sys.path.insert(0, str(CONFIG.parent))

from gate3_acceptance import parse_facts_tsv as parse_gate3_facts

SIM_TICK_LIMIT = 2000000000000


def run_twelve_user(outdir, *extra):
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
            str(FIXTURES / "agent_plan_image_twelve_user.bin"),
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


def generate_request_ids(events):
    controls = {
        event["request_id"]
        for event in events
        if event["kind"] == "CONTROL_SUBMIT"
    }
    return {
        event["request_id"]
        for event in events
        if event["kind"] == "SQ_CONSUME" and event["request_id"] is not None
    } - controls


def concurrency_facts(events):
    generates = generate_request_ids(events)
    order = lambda event: (event["tick"], event.get("phase"), event["ordinal"])
    accepts = [
        event
        for event in events
        if event["kind"] == "SQ_CONSUME" and event["request_id"] in generates
    ]
    consumes = [
        event
        for event in events
        if event["kind"] == "CQ_CONSUME" and event["request_id"] in generates
    ]
    first_consume = min(map(order, consumes))
    accepts_before_completion = sum(
        1 for event in accepts if order(event) < first_consume
    )
    stream = sorted(
        [(order(event), 1) for event in accepts]
        + [(order(event), -1) for event in consumes]
    )
    outstanding = 0
    peak_outstanding = 0
    for _, delta in stream:
        outstanding += delta
        peak_outstanding = max(peak_outstanding, outstanding)
    return accepts_before_completion, peak_outstanding


def test_gate4_concurrent_generate_acceptance(tmp_path):
    run = run_twelve_user(tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path = tmp_path / "run" / "gate4_facts.tsv"
    events, final, metrics, fatal = parse_gate3_facts(facts_path)
    assert fatal is None

    terminals = [event for event in events if event["kind"] == "TASK_TERMINAL"]
    assert [event["status"] for event in terminals] == ["BUSINESS_DONE"] * 12
    assert metrics["completed_tasks"] == 12
    assert metrics["failed_tasks"] == 0
    assert metrics["infra_failed_tasks"] == 0
    assert metrics["agent_live_objects"] == 0
    assert metrics["raw_log_bytes"] == 6 * 32768
    assert metrics["excerpt_bytes"] == 6 * 4096
    assert final is not None and not final["fatal"]
    assert final["npu_cq_ack_seq"] == 18
    assert final["driver_cq_consumer_seq"] == 18

    accepts_before, peak_outstanding = concurrency_facts(events)
    assert accepts_before >= 3, accepts_before
    assert peak_outstanding >= 3, peak_outstanding

    first = facts_path.read_bytes()
    for repeat in ("repeat2", "repeat3"):
        again = run_twelve_user(tmp_path / repeat)
        assert again.returncode == 0, again.stdout + again.stderr
        assert (tmp_path / repeat / "gate4_facts.tsv").read_bytes() == first


DRIVER_CLOCK_PERIOD_TICKS = 1000


def assert_manager_wakes_project_to_legal_edges(events):
    for event in events:
        kind = event["kind"]
        if not (kind.startswith("HOST_STAGE_") or kind.startswith("TASK_")):
            continue
        assert event["tick"] % DRIVER_CLOCK_PERIOD_TICKS == 0, event

    compile_starts = [
        event
        for event in events
        if event["kind"] == "HOST_STAGE_START" and event["object"] == "COMPILE"
    ]
    cq_visible = {}
    for event in events:
        if event["kind"] == "CQ_CONSUME" and event["request_id"] not in cq_visible:
            cq_visible[event["request_id"]] = event["tick"]
    assert compile_starts
    for start in compile_starts:
        visible = cq_visible[start["request_id"]]
        projected = -(-visible // DRIVER_CLOCK_PERIOD_TICKS) * (
            DRIVER_CLOCK_PERIOD_TICKS
        )
        assert start["tick"] >= projected, (start, visible)


def test_gate4_manager_wakes_project_to_legal_clock_edges(tmp_path):
    scenarios = (
        ("integral", ()),
        ("nonintegral_aging", ("--host-aging-threshold-ns", "1500")),
    )
    for name, extra in scenarios:
        run = run_twelve_user(tmp_path / name, *extra)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
        facts_path = tmp_path / name / "gate4_facts.tsv"
        events, final, metrics, fatal = parse_gate3_facts(facts_path)
        assert fatal is None
        assert final is not None and not final["fatal"]
        assert metrics["completed_tasks"] + metrics["failed_tasks"] == 12
        assert_manager_wakes_project_to_legal_edges(events)

import os
import re
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

TICKS_PER_NS = 1000
THINK_NS = 1000000
THINK_TICKS = THINK_NS * TICKS_PER_NS
SIM_TICK_LIMIT = 40000000000

STAGE_MIN_NS = {"COMPILE": 1000000, "TEST": 2000000, "LOG_PARSE": 500000}

SINGLE_USER_PATHS = {
    "first_pass": {
        "terminal": "BUSINESS_DONE",
        "stages": ["COMPILE", "TEST"],
        "completed": 1,
        "failed": 0,
        "generates": 1,
        "planned_ns": THINK_NS + 4000000 + 1000000 + 2000000,
        "raw_log_bytes": 0,
        "excerpt_bytes": 0,
        "excerpt_tokens": 0,
        "repair_input_excerpt_bytes": 0,
        "scanned_log_bytes": 0,
    },
    "compile_repair": {
        "terminal": "BUSINESS_DONE",
        "stages": ["COMPILE", "LOG_PARSE", "COMPILE", "TEST"],
        "completed": 1,
        "failed": 0,
        "generates": 2,
        "planned_ns": (
            THINK_NS + 4000000 + 1000000 + 500000 + 3600000 + 1000000
            + 2000000
        ),
        "raw_log_bytes": 32768,
        "excerpt_bytes": 4096,
        "excerpt_tokens": 512,
        "repair_input_excerpt_bytes": 4096,
        "scanned_log_bytes": 32768,
    },
    "test_repair": {
        "terminal": "BUSINESS_DONE",
        "stages": ["COMPILE", "TEST", "LOG_PARSE", "COMPILE", "TEST"],
        "completed": 1,
        "failed": 0,
        "generates": 2,
        "planned_ns": (
            THINK_NS + 4000000 + 1000000 + 2000000 + 500000 + 3600000
            + 1000000 + 2000000
        ),
        "raw_log_bytes": 32768,
        "excerpt_bytes": 4096,
        "excerpt_tokens": 512,
        "repair_input_excerpt_bytes": 4096,
        "scanned_log_bytes": 32768,
    },
    "repair_limit": {
        "terminal": "BUSINESS_FAILED",
        "stages": ["COMPILE"],
        "completed": 0,
        "failed": 1,
        "generates": 1,
        "planned_ns": THINK_NS + 4000000 + 1000000,
        "raw_log_bytes": 0,
        "excerpt_bytes": 0,
        "excerpt_tokens": 0,
        "repair_input_excerpt_bytes": 0,
        "scanned_log_bytes": 0,
    },
}


def run_single_user(name, outdir):
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
            str(FIXTURES / f"agent_plan_image_su_{name}.bin"),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
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


@pytest.mark.parametrize("name", list(SINGLE_USER_PATHS))
def test_gate4_single_user_business_loop_over_real_axi(name, tmp_path):
    expected = SINGLE_USER_PATHS[name]
    run = run_single_user(name, tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path, events, final, metrics, fatal = parse_facts(tmp_path / "run")
    assert fatal is None
    assert not any(event["kind"] == "FATAL" for event in events)

    terminals = [event for event in events if event["kind"] == "TASK_TERMINAL"]
    assert [event["status"] for event in terminals] == [expected["terminal"]]
    assert metrics["completed_tasks"] == expected["completed"]
    assert metrics["failed_tasks"] == expected["failed"]

    starts = [
        event
        for event in events
        if event["kind"] == "HOST_STAGE_START"
    ]
    dones = [
        event
        for event in events
        if event["kind"] == "HOST_STAGE_DONE"
    ]
    assert [event["object"] for event in starts] == expected["stages"]
    assert [event["object"] for event in dones] == expected["stages"]
    for start, done in zip(starts, dones):
        assert start["object"] == done["object"]
        assert start["request_id"] == done["request_id"]
        assert (
            done["tick"] - start["tick"]
            >= STAGE_MIN_NS[start["object"]] * TICKS_PER_NS - 1
        )

    assert metrics["npu_fabric_raw_log_bytes"] == 0
    assert metrics["raw_log_bytes"] == expected["raw_log_bytes"]
    assert metrics["scanned_log_bytes"] == expected["scanned_log_bytes"]
    assert metrics["excerpt_bytes"] == expected["excerpt_bytes"]
    assert metrics["excerpt_tokens"] == expected["excerpt_tokens"]
    assert (
        metrics["repair_input_excerpt_bytes"]
        == expected["repair_input_excerpt_bytes"]
    )
    assert metrics["agent_live_objects"] == 0

    doorbells = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "SQ_DOORBELL"
        and event["channel"] == "AW"
    ]
    assert doorbells
    assert doorbells[0]["tick"] >= THINK_TICKS

    exit_tick = re.search(r"Exiting @ tick (\d+)", run.stdout)
    assert exit_tick
    assert int(exit_tick.group(1)) >= expected["planned_ns"] * TICKS_PER_NS

    assert final is not None
    assert not final["fatal"]
    assert final["npu_cq_ack_seq"] == expected["generates"]
    assert final["driver_cq_consumer_seq"] == expected["generates"]

    repeat = run_single_user(name, tmp_path / "repeat")
    assert repeat.returncode == 0, repeat.stdout + repeat.stderr
    assert (
        (tmp_path / "repeat" / "gate4_facts.tsv").read_bytes()
        == facts_path.read_bytes()
    )

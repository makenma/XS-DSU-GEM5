import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
CONFIG = REPO / "configs" / "example" / "ai_mesh" / "run_gate4_protocol.py"
PLAN_IMAGE = (
    REPO
    / "tests"
    / "gem5"
    / "ai_mesh"
    / "fixtures"
    / "gate4"
    / "agent_plan_image_two_user.bin"
)
sys.path.insert(0, str(CONFIG.parent))

from gate3_acceptance import parse_facts_tsv as parse_gate3_facts


def test_gate4_plan_mode_replays_all_generates_over_real_axi(tmp_path):
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("AI_MESH_")
    }
    run = subprocess.run(
        [
            str(GEM5),
            f"--outdir={tmp_path}",
            str(CONFIG),
            "--plan-image",
            str(PLAN_IMAGE),
            "--sim-tick-limit",
            "2000000000000",
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout

    facts_path = tmp_path / "gate4_facts.tsv"
    assert facts_path.exists()
    events, final, metrics, fatal = parse_gate3_facts(facts_path)

    assert fatal is None
    assert not any(event["kind"] == "FATAL" for event in events)
    assert not any(event["kind"] == "CORE_START" for event in events)
    assert metrics["core_starts"] == 0

    consumed = [
        event for event in events if event["kind"] == "CQ_CONSUME"
    ]
    assert [event["request_id"] for event in consumed] == [
        1,
        (1 << 32) | 1,
        2,
        3,
        4,
    ]
    assert all(event["status"] == "SUCCESS" for event in consumed)

    metadata_reads = [
        event
        for event in events
        if event["kind"] == "LOCAL_READ"
        and event["object"] == "METADATA_READ"
    ]
    assert [event["status"] for event in metadata_reads] == [
        "HEADER",
        "TAIL",
    ] * 5
    assert metrics["metadata_reads_validated"] == 5

    output_writes = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "OUTPUT"
        and event["channel"] == "W"
    ]
    assert sum(event["bytes"] for event in output_writes) == (
        4096 + 2048 + 1024 + 512 + 256
    )

    assert metrics["cq_assignments"] == 5
    assert metrics["frontend_drained"] == 1
    assert final is not None
    assert not final["fatal"]
    assert final["npu_cq_ack_seq"] == 5
    assert final["driver_cq_consumer_seq"] == 5

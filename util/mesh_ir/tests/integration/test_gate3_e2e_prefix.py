import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
MESH_IR_ROOT = REPO / "util" / "mesh_ir"
CONFIG_ROOT = REPO / "configs" / "example" / "ai_mesh"
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
CONFIG = CONFIG_ROOT / "run_gate3_protocol.py"

sys.path.insert(0, str(MESH_IR_ROOT))
sys.path.insert(0, str(CONFIG_ROOT))

from gate3_acceptance import CONTROL_IDS
from mesh_ir.gate3_oracle import parse_facts_tsv as parse_gate3_facts
from gate3_profiles import e2e_d_prefix_steps, profile_named
from mesh_ir.gate3_oracle import validate_observation


def test_e2e_d_prefix_uses_committed_cq_metadata_and_ack(tmp_path):
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
            "--profile",
            "E2E_D_PREFIX",
            "--sim-tick-limit",
            "5000000",
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    profile = profile_named("E2E_D_PREFIX")
    events, final, metrics, fatal = parse_gate3_facts(
        tmp_path / "gate3_facts.tsv"
    )
    assert fatal is None
    validate_observation(
        {
            "schema": "ai_mesh_gate3_observation_v1",
            "version": 1,
            "id": "E2E-D",
            "subcase": "protocol_prefix",
            "configuration": {
                "sq_depth": profile.sq_depth,
                "cq_depth": profile.cq_depth,
                "control_bytes": 8,
                "data_bus_bytes": 64,
                "non_msi_axi_ids": CONTROL_IDS,
            },
            "events": events,
            "final": final,
        }
    )
    assert e2e_d_prefix_steps() == (
        "CQ_READ",
        "METADATA_READ",
        "CQ_HEAD_ACK",
        "DRAIN",
    )
    local_reads = [
        event for event in events if event["kind"] == "LOCAL_READ"
    ]
    cq_local = next(
        event for event in local_reads
        if event["object"] == "CQ_ENTRY_READ"
    )
    metadata_local = [
        event for event in local_reads
        if event["object"] == "METADATA_READ"
    ][-1]
    driver_axi_reads = [
        event for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] in ("CQ_ENTRY_READ", "METADATA_READ")
    ]
    assert not driver_axi_reads, driver_axi_reads
    ack_aw = next(
        event for event in events
        if event["control"] == "CQ_HEAD_ACK" and event["channel"] == "AW"
    )
    ack_commit = next(
        event for event in events if event["kind"] == "CQ_ACK_TARGET_COMMIT"
    )
    ack_b = next(
        event for event in events
        if event["control"] == "CQ_HEAD_ACK" and event["channel"] == "B"
    )
    assert cq_local["ordinal"] < metadata_local["ordinal"] < ack_aw["ordinal"]
    assert ack_aw["ordinal"] < ack_commit["ordinal"] < ack_b["ordinal"]
    assert metrics["metadata_reads_validated"] == 1
    assert final["fatal"] is False
    assert all(
        final[field] == 0
        for field in (
            "live_submissions",
            "live_contexts",
            "live_cq_obligations",
            "msi_rob_entries",
            "ack_wait_b",
        )
    )

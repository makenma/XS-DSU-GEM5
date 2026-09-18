import json
import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
SCRIPT = REPO / "configs" / "example" / "ai_mesh" / "run_gate6_serving.py"
FIXTURES = REPO / "tests" / "gem5" / "ai_mesh" / "fixtures" / "gate6"
SIM_TICK_LIMIT = 400000000000
WEIGHT_DIGEST = "d3c1ec48f8238e96c579ad087d218b8510ca81c9c15f9b05d97b99111fd789b2"
PAYLOAD_FIELDS = (
    "descriptor_id",
    "dma_kind",
    "read_bytes",
    "write_bytes",
    "p2p_bytes",
    "fill_bytes",
    "payload_digest",
    "error_code",
)


def run(outdir, extra):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AI_MESH_")
    }
    result = subprocess.run(
        [
            str(GEM5),
            f"--outdir={outdir}",
            str(SCRIPT),
            "--serving-program",
            str(FIXTURES / "serving_two_tokens.mshb"),
            "--runtime-config",
            str(FIXTURES / "serving_runtime_config.yaml"),
            "--surrogate-profiles",
            str(FIXTURES / "serving_surrogate_profiles.json"),
            "--weight-image-digest",
            WEIGHT_DIGEST,
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
            *extra,
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    match = re.search(r"Exiting @ tick (\d+) because "
                      r"AI_MESH_GATE3_QUIESCENT_SUCCESS", result.stdout)
    assert match, result.stdout[-4000:]
    payload = json.loads(
        (Path(outdir) / "mesh_result.json").read_text("utf-8"))
    return payload, int(match.group(1))


def payloads(result):
    return [
        {field: row[field] for field in PAYLOAD_FIELDS}
        for row in result["transport"]
    ]


def commit_ticks(result):
    return [row["done_tick"] for row in result["dma_timings"]]


def test_slower_sram_write_service_moves_ticks_but_not_payloads(tmp_path):
    baseline, baseline_terminal = run(tmp_path / "baseline", [])
    slowed, slowed_terminal = run(
        tmp_path / "slowed",
        ["--sram-write-bytes-per-cycle-per-bank", "1"],
    )
    assert baseline["terminal"] == slowed["terminal"] == "DONE"
    assert baseline["error_drained"] == slowed["error_drained"] == 0
    assert payloads(baseline) == payloads(slowed)
    assert [row["descriptor_id"] for row in baseline["transport"]] == \
        [row["descriptor_id"] for row in slowed["transport"]]
    assert commit_ticks(baseline) != commit_ticks(slowed)
    assert min(commit_ticks(slowed)) > min(commit_ticks(baseline))
    assert slowed_terminal > baseline_terminal


def test_kv_physical_binding_moves_axi_addresses_and_preserves_content(tmp_path):
    from mesh_ir.abi.decoder import decode_program

    program = decode_program((FIXTURES / "serving_two_tokens.mshb").read_bytes())
    profile = program.agent_request_profiles[0]
    baseline, _ = run(tmp_path / "baseline", [])
    configuration = json.loads((tmp_path / "baseline" / "config.json").read_text())
    old_base = configuration["system"]["gate6_frontend"]["kv_region_base"]
    new_base = old_base + 0x100000
    moved, _ = run(tmp_path / "moved", ["--kv-region-base", str(new_base)])
    assert payloads(baseline) == payloads(moved)
    valid_bytes = (profile.full_input_tokens + profile.output_tokens) * profile.kv_bytes_per_token
    old_rows = [row for row in baseline["burst_timings"]
                if old_base <= row["address"] < old_base + valid_bytes]
    new_rows = [row for row in moved["burst_timings"]
                if new_base <= row["address"] < new_base + valid_bytes]
    assert old_rows and len(old_rows) == len(new_rows)
    assert [row["address"] - old_base for row in old_rows] == \
        [row["address"] - new_base for row in new_rows]
    assert not any(old_base <= row["address"] < old_base + valid_bytes
                   for row in moved["burst_timings"])

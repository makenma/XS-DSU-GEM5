import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build/AXI_MESH/gem5.opt"
CONFIG = REPO / "configs/example/ai_mesh/run_mesh_dma_garnet.py"
ARCH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


def run_dma(tmp_path, program, case, arch_text=None, extra=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    arch = tmp_path / "arch.yaml"
    arch.write_text(arch_text or ARCH.read_text())
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("AI_MESH_")}
    env.update(PYTHONPATH=str(REPO / "util/mesh_ir"),
               AI_MESH_ARTIFACT_DIR=str(tmp_path))
    built = subprocess.run(
        [sys.executable, "-m", "mesh_ir.cli", "build", "--program", program,
         "--arch", str(arch), "--out", str(tmp_path / "program")],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    run = subprocess.run(
        [str(GEM5), f"--outdir={tmp_path / 'm5out'}", str(CONFIG),
         "--case", case, "--arch", str(arch),
         "--mesh-program-dir", str(tmp_path / "program"),
         "--watchdog-ticks", "100000000", "--axi-max-sim-ticks", "200000000",
         *extra], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    (tmp_path / "run.log").write_text(run.stdout + run.stderr)
    assert run.returncode == 0, run.stdout + run.stderr
    return (json.loads((tmp_path / "actual_result.json").read_text()),
            json.loads((tmp_path / "m5out/config.json").read_text())["system"])


def test_queue_architecture_and_each_override_are_effective(tmp_path):
    text = ARCH.read_text().replace("segment_queue_depth: 32", "segment_queue_depth: 1")
    text = text.replace("descriptor_queue_depth: 16", "descriptor_queue_depth: 1")
    digests = set()
    for segment, descriptor in ((1, 1), (4, 1), (8, 1), (1, 2)):
        extra = () if (segment, descriptor) == (1, 1) else (
            "--segment-queue-depth", str(segment),
            "--dma-descriptor-queue-depth", str(descriptor))
        _, system = run_dma(
            tmp_path / f"q{segment}_{descriptor}", "load_saturation_contiguous",
            "load_saturation_contiguous", text, extra=extra)
        for core in (0, 1):
            assert system[f"mesh_core_{core}"]["dma"]["segment_queue_depth"] == segment
            assert system[f"mesh_core_{core}"]["dma"]["descriptor_queue_depth"] == descriptor
            assert system[f"mesh_bridge_{core}"]["ar_queue_depth"] == segment
            assert system[f"mesh_bridge_{core}"]["aw_queue_depth"] == segment
        digests.add(system["mesh_loader"]["effective_arch_digest"])
    assert len(digests) == 4


def test_window_reuses_four_ids_before_first_burst_commit(tmp_path):
    result, system = run_dma(tmp_path, "read_window", "read_outstanding_window")
    assert system["mesh_core_0"]["dma"]["axi_id_count"] == 4
    assert system["mesh_core_0"]["sram_write_bytes_per_cycle"] == 1
    assert len(result["burst_timings"]) == 24
    assert all(row["response_tick"] < row["commit_tick"]
               for row in result["burst_timings"])


def test_r_backpressure_is_reported_at_target_and_network(tmp_path):
    result, _ = run_dma(
        tmp_path, "load_saturation_contiguous", "load_saturation_contiguous",
        extra=("--read-outstanding", "4", "--garnet-buffers-per-vnet", "2,2,2,2,2",
               "--axi-message-buffer-depths", "8,32,8,16,1"))
    assert result["core_clock_period_ticks"] == 500
    targets = result["target_message_buffer_blocked_cycles"]
    assert targets["system.mesh_endpoint"]["R"] > 0
    assert result["network_stalls"]["R"]["ni_vc_busy_cycles"] > 0

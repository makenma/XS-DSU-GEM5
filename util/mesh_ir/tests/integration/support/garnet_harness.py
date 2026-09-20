"""Shared real-AXI/Garnet runtime harness.

``runtime_harness.py`` owns program publication, the published-oracle readers
and the gem5 runtime environment for both backends; this module adds only the
Garnet run entry point, so ``run_mesh_dma_garnet.py`` stays the single runner.
"""

from __future__ import annotations

import subprocess

from tests.integration.support.runtime_harness import (  # noqa: F401
    ARCH,
    REPO,
    build_program,
    build_program_with_arch,
    expected_traffic,
    result_of,
    runtime_environment,
    schedule,
)

GEM5 = REPO / "build/AXI_MESH/gem5.opt"
CONFIG = REPO / "configs/example/ai_mesh/run_mesh_dma_garnet.py"

WATCHDOG_TICKS = "100000000"
MAX_SIM_TICKS = "200000000"

ARTIFACT_NAMES = (
    "actual_result.json",
    "hbm_seed.txt",
    "hbm_verify.txt",
)


def run_garnet(
    tmp_path,
    output_name,
    case,
    program_dir,
    arch=None,
    extra_args=(),
    artifact_dir=None,
    timeout=900,
):
    """Run one Garnet case and archive its log next to the outdir."""
    environment = runtime_environment()
    environment["AI_MESH_ARTIFACT_DIR"] = str(artifact_dir or program_dir)
    command = [
        str(GEM5),
        f"--outdir={tmp_path / output_name}",
        str(CONFIG),
        "--case",
        case,
        "--arch",
        str(arch or ARCH),
        "--mesh-program-dir",
        str(program_dir),
        "--watchdog-ticks",
        WATCHDOG_TICKS,
        "--axi-max-sim-ticks",
        MAX_SIM_TICKS,
        *extra_args,
    ]
    completed = subprocess.run(
        command,
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    (tmp_path / f"{output_name}.log").write_text(completed.stdout + completed.stderr)
    return completed


def output_of(run):
    return run.stdout + run.stderr


def cause_of(run):
    for line in output_of(run).splitlines():
        if "because MESH_PROGRAM" in line:
            return line.split("because ", 1)[1].strip()
    return ""

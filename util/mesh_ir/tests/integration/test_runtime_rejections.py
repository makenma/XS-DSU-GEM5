import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
MESH_IR_ROOT = REPO / "util" / "mesh_ir"
ARCH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
GEM5 = REPO / "build/AXI_MESH/gem5.opt"
CONFIG = REPO / "configs/example/ai_mesh/run_mesh_program.py"


def _build_program(tmp_path, program):
    program_dir = tmp_path / program
    result = subprocess.run(
        [
            str(Path(sys.executable).resolve()),
            "-m",
            "mesh_ir.cli",
            "build",
            "--program",
            program,
            "--arch",
            str(ARCH),
            "--out",
            str(program_dir),
        ],
        cwd=MESH_IR_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return program_dir


def _run_mock(tmp_path, output_name, program, program_dir, extra_args=()):
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("AI_MESH_")
    }
    return subprocess.run(
        [
            str(GEM5),
            f"--outdir={tmp_path / output_name}",
            str(CONFIG),
            "--program",
            program,
            "--mesh-program-dir",
            str(program_dir),
            *extra_args,
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    ("program", "fatal_option", "expected_text"),
    [
        ("single", "reference_compute", "reference_compute=true is forbidden"),
        ("poison", "E_TENSOR_NOT_RESIDENT", "E_TENSOR_NOT_RESIDENT"),
        ("poison_ew", "E_TENSOR_NOT_RESIDENT", "E_TENSOR_NOT_RESIDENT"),
        ("poison_store", "E_TENSOR_NOT_RESIDENT", "E_TENSOR_NOT_RESIDENT"),
        ("poison_reduce", "E_TENSOR_NOT_RESIDENT", "E_TENSOR_NOT_RESIDENT"),
    ],
    ids=(
        "reference_compute",
        "poison",
        "poison_ew",
        "poison_store",
        "poison_reduce",
    ),
)
def test_runtime_rejection_is_observed(
    tmp_path, program, fatal_option, expected_text
):
    program_dir = _build_program(tmp_path, program)
    run = _run_mock(
        tmp_path,
        "m5out",
        program,
        program_dir,
        ("--expect-fatal", fatal_option),
    )
    assert run.returncode != 0
    assert expected_text in run.stdout + run.stderr


def test_mock_arbitration_is_reproducible(tmp_path):
    program_dir = _build_program(tmp_path, "dual")
    first = _run_mock(tmp_path, "m5out-first", "dual", program_dir)
    assert first.returncode == 0, first.stderr
    first_result = (program_dir / "actual_result.json").read_bytes()
    second = _run_mock(tmp_path, "m5out-second", "dual", program_dir)
    assert second.returncode == 0, second.stderr
    assert (program_dir / "actual_result.json").read_bytes() == first_result

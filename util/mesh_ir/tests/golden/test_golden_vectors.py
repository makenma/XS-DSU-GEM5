import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.burst_splitter import plan_descriptor
from mesh_ir.builder import load_arch
from mesh_ir.cli import main as cli_main
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import (
    build_dma_edge_program,
    build_dma_shapes_program,
    build_dma_error_program,
    build_dma_fence_program,
    build_dma_pin_program,
    build_dma_write_error_program,
    build_dual_core_program,
    build_fill_program,
    build_repeat_program,
    build_single_core_program,
)

ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
GOLDEN_VECTORS = Path(__file__).resolve().parent / "burst_splitter_golden.json"


def test_burst_splitter_golden_vectors_pinned():
    cases = json.loads(GOLDEN_VECTORS.read_text())
    assert len(cases) >= 8
    for case in cases:
        plan = plan_descriptor(
            case["row_bytes"], case["rows"], case["base"], case["stride"],
            case["width"], case["max_beats"],
        )
        assert plan.beat_bytes == case["beat_bytes"], case["name"]
        assert plan.useful_bytes == case["useful_bytes"], case["name"]
        actual = [(b.beat_base, b.logical_start, b.useful_bytes, b.beats) for b in plan.bursts]
        expected = [
            (x["beat_base"], x["logical_start"], x["useful_bytes"], x["beats"])
            for x in case["bursts"]
        ]
        assert actual == expected, case["name"]


def test_golden_semantic_shas_are_stable():
    arch = load_arch(ARCH_PATH)
    pinned = json.loads((Path(__file__).resolve().parent / "golden_program_shas.json").read_text())
    builders = {
        "single": build_single_core_program,
        "dual": build_dual_core_program,
        "fill": build_fill_program,
        "repeat": build_repeat_program,
        "dma_edge": build_dma_edge_program,
        "dma_shapes": build_dma_shapes_program,
        "dma_error": build_dma_error_program,
        "dma_write_error": build_dma_write_error_program,
        "dma_fence": build_dma_fence_program,
        "dma_pin": build_dma_pin_program,
    }
    assert set(builders) == set(pinned)
    for name, build in builders.items():
        assert build(arch).semantic_sha256() == pinned[name]


def test_cli_build_and_verify_roundtrip(tmp_path):
    out = tmp_path / "single"
    rc = cli_main(["build", "--program", "single", "--arch", str(ARCH_PATH), "--out", str(out)])
    assert rc == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["status"] == "ok"
    import hashlib

    assert hashlib.sha256((out / "program.mshb").read_bytes()).hexdigest() == manifest["mshb_sha256"]
    rc = cli_main(["verify", "--program", str(out / "program.mshb"), "--arch", str(ARCH_PATH)])
    assert rc == 0


def test_cli_entrypoint_runs(tmp_path):
    repo = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mesh_ir.cli",
            "build",
            "--program",
            "dual",
            "--arch",
            str(ARCH_PATH),
            "--out",
            str(tmp_path / "dual"),
        ],
        cwd=repo / "util" / "mesh_ir",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "dual" / "manifest.json").read_text())
    assert manifest["status"] == "ok"

import json
import runpy
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.burst_splitter import plan_descriptor
from mesh_ir.architecture import load_arch
from mesh_ir.cli import BUILDERS, main as cli_main
from mesh_ir.generated import abi as A

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
    fixtures = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate5"
    moe = runpy.run_path(str(fixtures / "build_gate5_moe_images.py"))["PROGRAMS"]
    assert set(BUILDERS) == set(pinned) | {name for name, _, _ in moe}
    for name, digest in pinned.items():
        assert BUILDERS[name](arch).semantic_sha256 == digest
    for name, factory, arch_path in moe:
        expected = json.loads((fixtures / f"{name}_expected.json").read_text())
        assert BUILDERS[name] is factory
        assert factory(load_arch(arch_path)).semantic_sha256 == expected["semantic_sha256"]


def test_cli_build_publishes_complete_verified_program(tmp_path):
    out = tmp_path / "single"
    assert cli_main(["build", "--program", "single", "--arch", str(ARCH_PATH), "--out", str(out)]) == 0
    manifest = json.loads((out / "manifest.json").read_bytes())
    assert manifest["status"] == "ok"
    assert manifest["kind"] == "golden"
    assert manifest["identity"] == {"arch_name": "xs_ai_mesh_1x2", "program": "single"}
    assert {item.name for item in out.iterdir()} == {
        "checksums.sha256",
        "diagnostics.jsonl",
        "expected_traffic.json",
        "manifest.json",
        "program.mshb",
        "schedule.mesh.json",
    }


def test_cli_entrypoint_publishes_complete_verified_program(tmp_path):
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
    published = json.loads(result.stdout)
    assert published["status"] == "ok"
    assert Path(published["output"]) == (tmp_path / "dual").resolve()
    assert json.loads((tmp_path / "dual" / "manifest.json").read_bytes())["status"] == "ok"

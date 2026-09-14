import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

GEM5 = REPO / "build/AXI_MESH/gem5.opt"
RUNNER = "configs/example/ai_mesh/run_dummy_core_agent.py"
ARCH = "configs/example/ai_mesh/arch/mesh_4x4_moe.yaml"
OVERLAY = ("tests/gem5/ai_mesh/fixtures/gate5/"
           "moe_quad_overlay_objects.bin")
HOST_FIELDS = ("wall_clock", "pid", "hostTickRate", "hostMemory",
               "hostSeconds")


def canonical(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    for field in HOST_FIELDS:
        document.pop(field, None)
    return document


def build_program_dir(root: Path, program: str = "moe_quad",
                      arch: str = ARCH) -> Path:
    program_dir = root / "golden" / program
    result = subprocess.run(
        [sys.executable, "-m", "mesh_ir.cli", "build", "--program",
         program, "--arch", str(REPO / arch), "--out", str(program_dir)],
        cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO / "util/mesh_ir")},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return program_dir


def run_case(root: Path, program_dir: Path, index: int) -> Path:
    outdir = root / ("run%d" % index)
    outdir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "AI_MESH_ARTIFACT_DIR": str(outdir)}
    env.pop("AI_MESH_CHILD_REPORT", None)
    result = subprocess.run(
        [str(GEM5), "--outdir=%s" % outdir, RUNNER, "--case", "moe_quad",
         "--master-seed", "20260901", "--sim-tick-limit", "20000000",
         "--mesh-program-dir", str(program_dir), "--arch", ARCH,
         "--overlay-image", OVERLAY],
        cwd=REPO, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr[-2000:]
    return outdir / "actual_result.json"


def test_gate5_e2e_c_four_by_four_is_deterministic(tmp_path):
    if not GEM5.is_file():
        pytest.skip("gem5.opt is not built")
    program_dir = build_program_dir(tmp_path)
    runs = [canonical(run_case(tmp_path, program_dir, index))
            for index in range(3)]
    for index, document in enumerate(runs[1:], start=1):
        assert document == runs[0], "run %d differs from run 0" % index
    assert runs[0]["moe"]["overlay_exits"] == [1]
    assert len(runs[0]["moe"]["regions"]) == 16
    assert runs[0]["digests"]


FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures/gate5"
CACHED_OVERLAY = ("tests/gem5/ai_mesh/fixtures/gate5/"
                  "moe_dual_overlay_cached.bin")
DUAL_ARCH = "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"


def run_cached_case(root: Path, program_dir: Path) -> dict:
    outdir = root / "cached"
    outdir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "AI_MESH_ARTIFACT_DIR": str(outdir)}
    env.pop("AI_MESH_CHILD_REPORT", None)
    result = subprocess.run(
        [str(GEM5), "--outdir=%s" % outdir, RUNNER, "--case",
         "moe_dual_cached", "--master-seed", "20260901", "--sim-tick-limit",
         "20000000", "--mesh-program-dir", str(program_dir), "--arch",
         DUAL_ARCH, "--overlay-image", CACHED_OVERLAY, "--weight-policy",
         "cached"],
        cwd=REPO, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads((outdir / "actual_result.json").read_text())


def oracle_expectations():
    from mesh_ir.abi.decoder import decode_program
    from mesh_ir.generated import agent_abi as G
    from mesh_ir.moe_materializer import (
        MaterializeConfig,
        member_slice,
    )
    from mesh_ir.moe_oracle import (
        MeshTopology,
        Packetization,
        command_expectations,
        descriptor_expectations,
        moe_traffic_lanes,
    )
    from mesh_ir.moe_overlay_runtime import materialize_overlay
    from mesh_ir.moe_provider import (
        FrozenToken,
        apply_capacity,
        uniform_select,
    )
    from mesh_ir.moe_uid import SemanticTokenUid

    program = decode_program((FIXTURES / "moe_dual.mshb").read_bytes())
    layer = program.moe_layer_specs[0]
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[:layer.dynamic_region_count]
    digest = bytes.fromhex("02" * 32)
    uid = SemanticTokenUid(
        workload_plan_item_id=1, user_id=1, task_seq=1, repair_round=0,
        phase=G.SEMANTIC_PHASE.DECODE, sequence_ordinal=0, token_ordinal=0)
    tokens = [FrozenToken(uid=uid, member_identity="m1", source_rank=0,
                          linear_ordinal=0)]
    members = [member_slice(program, layer, "m1", 11, 0)]
    placement = [spec.core_id for spec in program.moe_expert_specs]
    plan = uniform_select(layer, tokens, digest, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    weight_bytes = {spec.expert_id: spec.weight_bytes
                    for spec in program.moe_expert_specs}
    packetization = Packetization(data_bytes=32, burst_beats=16,
                                  header_bytes=16, flit_bytes=32)
    lanes = moe_traffic_lanes(layer, kernel, plan, capacity, members,
                              placement, weight_bytes, packetization,
                              MeshTopology(1, 2))
    document = json.loads(
        (FIXTURES / "moe_dual_overlay_cached.json").read_text())
    return (descriptor_expectations(document, packetization),
            command_expectations(document), lanes)


def test_gate5_oracle_verifies_a_real_cached_runtime_trace(tmp_path):
    from mesh_ir.moe_oracle import verify_result

    if not GEM5.is_file():
        pytest.skip("gem5.opt is not built")
    program_dir = build_program_dir(tmp_path, "moe_dual", DUAL_ARCH)
    result = run_cached_case(tmp_path, program_dir)
    expectations, counts, lanes = oracle_expectations()
    failures, ok = verify_result(expectations, counts, result,
                                 cache_geometry=(0xC0000, 4096))
    assert ok, failures
    moved = 0
    for row in result["transport"]:
        if row.get("domain", 0) == 1 and row.get("dma_kind") == 3:
            moved += row["p2p_bytes"]
    assert moved == (lanes.lanes["dispatch_remote_dma_bytes"] +
                     lanes.lanes["combine_remote_dma_bytes"])
    assert result["moe"]["cache_fills"]
    assert len(result["moe"]["overlay_exits"]) == 1

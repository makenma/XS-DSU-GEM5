import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
PROBE = REPO / "tests/gem5/ai_mesh/gate6/serving_fault_probe.py"
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures/gate6"
ARCH_YAML = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
WEIGHT_DIGEST = ("d3c1ec48f8238e96c579ad087d218b8510ca81c9c15f9b05d97b9911"
                 "1fd789b2")
SIM_TICK_LIMIT = "10000000000"

sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

from mesh_ir.abi.decoder import decode_program  # noqa: E402
from mesh_ir.abi.encoder import encode_program  # noqa: E402
from mesh_ir.abi.verifier import verify_program  # noqa: E402
from mesh_ir.builder import load_arch  # noqa: E402
from mesh_ir.serving_profiles import apply_request_profile_keys  # noqa: E402


def build_multirow(work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    program = decode_program(
        (FIXTURES / "serving_two_tokens.mshb").read_bytes())
    descriptors = tuple(
        dataclasses.replace(descriptor, rows=2, row_bytes=64,
                            src_stride_bytes=64, dst_stride_bytes=64)
        if descriptor.descriptor_id == 4 else descriptor
        for descriptor in program.dma_descriptors)
    traffic = tuple(
        dataclasses.replace(row, segments=2, bursts=2, aw_count=2, b_count=2)
        if row.descriptor_id == 4 else row
        for row in program.expected_traffic)
    program = apply_request_profile_keys(dataclasses.replace(
        program, dma_descriptors=descriptors, expected_traffic=traffic))
    arch = load_arch(ARCH_YAML)
    verify_program(program, arch)
    blob = encode_program(program)
    verify_program(decode_program(blob), arch)
    (work / "program.mshb").write_bytes(blob)
    (work / "schedule.mesh.json").write_text(
        json.dumps(program.canonical_dict()), encoding="utf-8")
    key = hex(program.agent_request_profiles[0].requested_profile_key)
    workload = json.loads(
        (FIXTURES / "serving_workload_plan.json").read_text("utf-8"))
    workload["users"][0]["tasks"][0]["rounds"][0][
        "requested_profile_key"] = key
    (work / "workload.json").write_text(json.dumps(workload), encoding="utf-8")
    surrogate = json.loads(
        (FIXTURES / "serving_surrogate_profiles.json").read_text("utf-8"))
    surrogate["profiles"][0]["profile_key"] = key
    (work / "surrogate.json").write_text(json.dumps(surrogate),
                                         encoding="utf-8")
    config = yaml.safe_load(
        (FIXTURES / "serving_runtime_config.yaml").read_text("utf-8"))
    config["agent"]["workload_plan"] = "workload.json"
    (work / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return work


def run_arm(work: Path, mode: str, fault_index: int) -> dict:
    out = work / mode
    out.mkdir(exist_ok=True)
    arch = load_arch(ARCH_YAML)
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("AI_MESH_", "GATE6_"))}
    env.update(
        AI_MESH_ARTIFACT_DIR=str(out),
        GATE6_FAULT_ENABLE=str(int(fault_index >= 0)),
        GATE6_FAULT_CORE="0",
        GATE6_FAULT_KIND="write",
        GATE6_FAULT_INDEX=str(fault_index),
        GATE6_SCHEDULE_DIR=str(work),
        GATE6_CORE_IDS=json.dumps(list(arch.core_ids)),
        GATE6_ARCH_DICT=json.dumps({
            "core_ids": list(arch.core_ids),
            "axi_data_bytes": arch.axi_data_bytes,
            "axi_max_burst_beats": arch.axi_max_burst_beats,
            "region_bases": [region.base for region in arch.regions],
            "region_tile_strides": [region.tile_stride or 0
                                    for region in arch.regions],
        }),
    )
    argv = [
        str(GEM5), "--outdir=" + str(out), str(PROBE),
        "--case", "gate6_serving_e2e_d", "--master-seed", "20260901",
        "--sim-tick-limit", SIM_TICK_LIMIT,
        "--serving-program", str(work / "program.mshb"),
        "--runtime-config", str(work / "config.yaml"),
        "--surrogate-profiles", str(work / "surrogate.json"),
        "--weight-image-digest", WEIGHT_DIGEST,
    ]
    with (out / "console.log").open("w") as handle:
        subprocess.run(argv, env=env, stdout=handle,
                       stderr=subprocess.STDOUT, timeout=900, check=False)
    facts = (out / "gate6_facts.tsv").read_text("utf-8")
    result = json.loads((out / "mesh_result.json").read_text("utf-8"))
    metrics = {}
    for line in facts.splitlines():
        fields = line.split("|")
        if fields[0] == "METRIC":
            metrics[fields[1]] = int(fields[2])
    descriptor = next(row for row in result["transport"]
                      if row["descriptor_id"] == 4)
    return {
        "terminal": result["terminal"],
        "cached_tokens": metrics.get("kv_cached_tokens"),
        "write_bytes": descriptor["write_bytes"],
        "drained_uncommitted": descriptor["write_drained_uncommitted_bytes"],
        "write_bursts": descriptor["write_bursts"],
        "fact_lines": facts,
    }


@pytest.fixture(scope="module")
def multirow(tmp_path_factory):
    return build_multirow(tmp_path_factory.mktemp("multirow"))


def test_real_multirow_prefix_tracks_the_faulted_burst(multirow):
    control = run_arm(multirow, "control", -1)
    second = run_arm(multirow, "second_fault", 1)
    first = run_arm(multirow, "first_fault", 0)
    assert control["write_bursts"] == 2
    assert control["terminal"] == "DONE"
    assert control["cached_tokens"] == 10
    assert second["write_bursts"] == 2
    assert second["write_bytes"] == 64
    assert second["drained_uncommitted"] == 64
    assert second["terminal"] == "ERROR_DRAINED"
    assert second["cached_tokens"] == 4
    assert first["write_bursts"] == 2
    assert first["write_bytes"] == 64
    assert first["drained_uncommitted"] == 64
    assert first["terminal"] == "ERROR_DRAINED"
    assert first["cached_tokens"] == 0

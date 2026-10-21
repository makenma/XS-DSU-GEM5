import dataclasses
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.scheduled.addressing import derive_descriptor_executions
from mesh_ir.traffic import calculate_traffic

from .serving_runtime import ARCH_YAML, FIXTURES, run_serving_fixture, write_serving_fixture


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
        dataclasses.replace(row, segments=2, bursts=2, aw_count=2, b_count=2, min_flits=18)
        if row.descriptor_id == 4 else row
        for row in program.expected_traffic)
    program = dataclasses.replace(
        program, dma_descriptors=descriptors, expected_traffic=traffic)
    arch = load_arch(ARCH_YAML)
    report = calculate_traffic(arch, program.semantics.reference_binding_identity_sha256,
                               derive_descriptor_executions(program, arch))
    program = dataclasses.replace(program, semantics=dataclasses.replace(
        program.semantics, intrinsic_traffic=report))
    return write_serving_fixture(work, program)


def run_arm(work: Path, mode: str, fault_index: int) -> dict:
    facts, result, metrics = run_serving_fixture(work, mode, fault_index)
    descriptor = next(row for row in result["transport"]
                      if row["descriptor_id"] == 4)
    timing = next(row for row in result["dma_timings"]
                  if row["descriptor_id"] == 4 and row["domain"] == 0)
    bursts = [row for row in result["burst_timings"]
              if row["core_id"] == timing["core_id"] and row["channel"] == "AW"
              and timing["first_aw_tick"] <= row["ar_aw_tick"] <= timing["first_b_tick"]]
    return {
        "axi_write_bursts": len(bursts),
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
    assert control["axi_write_bursts"] == 2
    assert control["terminal"] == "DONE"
    assert control["cached_tokens"] == 10
    assert second["write_bursts"] == 1
    assert second["axi_write_bursts"] == 2
    assert second["write_bytes"] == 64
    assert second["drained_uncommitted"] == 64
    assert second["terminal"] == "ERROR_DRAINED"
    assert second["cached_tokens"] == 4
    assert first["write_bursts"] == 1
    assert first["axi_write_bursts"] == 2
    assert first["write_bytes"] == 64
    assert first["drained_uncommitted"] == 64
    assert first["terminal"] == "ERROR_DRAINED"
    assert first["cached_tokens"] == 0

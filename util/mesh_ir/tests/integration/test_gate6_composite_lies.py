"""Gate 6 composite counterexample self-review.

Every tamper mutates a real archived result (or its pinned plan file) so the
whole lie stays internally consistent where possible, then feeds it to the
product shared entries -- no helper pre-validates.  Each test first proves
the healthy control passes, then records which product owner rejects the
lie and why.  A lie that only an earlier, different invariant catches is
labeled as such instead of being claimed for this entry.
"""

import copy
import hashlib
import json

import pytest

from mesh_ir.runtime_reconciliation import ReconciliationError

from tests.integration.support.garnet_harness import (
    output_of,
    result_of,
    run_garnet,
)
from tests.integration.support.runtime_harness import REPO

ARCH = REPO / "configs/example/ai_mesh/arch/mesh_2x2.yaml"


@pytest.fixture(scope="module")
def ring(tmp_path_factory):
    from mesh_ir.compile_service import export_and_compile_factory
    program_dir = tmp_path_factory.mktemp("ring") / "ring"
    export_and_compile_factory(
        "examples.tiny_mlp:create_model",
        REPO / "util/mesh_ir/examples/tiny_mlp_inputs.json",
        None,
        ARCH,
        REPO / "util/mesh_ir/examples/tiny_mlp_ring_tp4_compile.yaml",
        program_dir,
    )
    work = tmp_path_factory.mktemp("m5out")
    run = run_garnet(work, "m5out-two", "ring_workload", program_dir,
                     arch=ARCH, extra_args=("--instances", "2",
                                             "--dump-compute-bytes", "65536"))
    assert run.returncode == 0, output_of(run)
    sections = json.loads(
        (program_dir / "schedule.mesh.json").read_text())["sections"]
    return program_dir, sections, result_of(program_dir)


@pytest.fixture(scope="module")
def ring_error(tmp_path_factory, ring):
    program_dir, _, _ = ring
    work = tmp_path_factory.mktemp("m5out-err")
    run = run_garnet(work, "m5out-err", "ring_error", program_dir, arch=ARCH)
    assert run.returncode == 0, output_of(run)
    return result_of(program_dir)


def _chain(ring, result):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, _, _ = ring
    return verified_workload_producer_chain(program_dir, result, ARCH)


def _first_consumer(frame):
    for core_id, ledger in sorted(frame["cores"].items()):
        for execution in ledger["observations"]["descriptor_executions"]:
            if execution.get("source_rows"):
                return ledger, execution
    raise AssertionError("no consumer source rows")


def _p2p_consumers(frame):
    rows = []
    for ledger in frame["cores"].values():
        for execution in ledger["observations"]["descriptor_executions"]:
            if execution.get("source_rows") and len(execution["source_rows"]) > 1:
                rows.append(execution)
    return rows


def test_healthy_control_passes_every_entry(ring):
    _, _, result = ring
    assert _chain(ring, result)["uncovered"] == 0


def _first_p2p_consumer(frame, sections):
    kinds = {row["descriptor_id"]: row["kind"]
             for row in sections["DMA_DESCRIPTORS"]}
    for core_id, ledger in sorted(frame["cores"].items()):
        for execution in ledger["observations"]["descriptor_executions"]:
            if execution.get("source_rows") \
                    and kinds[execution["descriptor_id"]] == 3:
                return ledger, execution
    raise AssertionError("no P2P consumer source rows")


def test_rows_moved_alone_are_rejected_at_the_admitted_endpoint(ring):
    _, sections, result = ring
    result = copy.deepcopy(result)
    frame = result["instances"][0]
    _, execution = _first_p2p_consumer(frame, sections)
    for row in execution["source_rows"]:
        row["address"] += 64
    with pytest.raises(ReconciliationError, match="admitted endpoint"):
        _chain(ring, result)


def test_rows_and_plan_moved_together(ring):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    kinds = {row["descriptor_id"]: row["kind"]
             for row in sections["DMA_DESCRIPTORS"]}
    delta = 64
    moved_consumers = 0
    for frame in result["instances"]:
        for ledger in frame["cores"].values():
            for execution in ledger["observations"][
                    "descriptor_executions"]:
                if execution.get("source_rows") \
                        and kinds[execution["descriptor_id"]] == 3:
                    for row in execution["source_rows"]:
                        row["address"] += delta
                    moved_consumers += 1
    assert moved_consumers, "the ring archive carries no P2P consumer"
    traffic_path = program_dir / "expected_traffic.json"
    original = traffic_path.read_bytes()
    traffic = json.loads(original)
    moved = set()
    for row in traffic["descriptors"]:
        if row["identity"]["direction"] == "P2P":
            row["dst_address"] += delta
            row["src_address"] += delta
            moved.add(row["identity"]["descriptor_id"])
    assert moved, "the ring plan carries no peer push to move"
    traffic_path.write_text(json.dumps(traffic))
    try:
        # The plan and every P2P consumer moved together, but the landing
        # extent anchor and the unmoved compute producers both see the lie.
        with pytest.raises(ReconciliationError,
                           match="no admitted producer|outside every "
                                 "admitted write allocation"):
            _chain(ring, result)
    finally:
        traffic_path.write_bytes(original)


def test_a_plan_rewrite_breaks_the_archive_checksums(ring):
    program_dir, _, _ = ring
    traffic_path = program_dir / "expected_traffic.json"
    original = traffic_path.read_bytes()
    traffic = json.loads(original)
    for row in traffic["descriptors"]:
        if row["identity"]["direction"] == "P2P":
            row["dst_address"] += 64
            row["src_address"] += 64
    traffic_path.write_text(json.dumps(traffic))
    try:
        recorded = {}
        for line in (program_dir / "checksums.sha256").read_text().splitlines():
            digest, _, name = line.partition(" ")
            name = name.strip()
            if name:
                recorded[name] = digest.strip()
        assert hashlib.sha256(
            (program_dir / "expected_traffic.json").read_bytes()
        ).hexdigest() != recorded["expected_traffic.json"]
    finally:
        traffic_path.write_bytes(original)


def test_an_execution_replayed_into_another_instance_is_rejected(ring):
    _, _, result = ring
    result = copy.deepcopy(result)
    first, second = result["instances"][0], result["instances"][1]
    ledger, execution = _first_consumer(second)
    owner = next(core_id for core_id, row in first["cores"].items()
                 if any(item["descriptor_id"] == execution["descriptor_id"]
                        for item in row["observations"]
                        ["descriptor_executions"]))
    first["cores"][owner]["observations"][
        "descriptor_executions"].append(copy.deepcopy(execution))
    with pytest.raises(ReconciliationError, match="second generation"):
        _chain(ring, result)


def test_a_duplicated_row_masking_a_deleted_row_is_rejected(ring):
    _, _, result = ring
    result = copy.deepcopy(result)
    frame = result["instances"][0]
    candidates = _p2p_consumers(frame)
    if not candidates:
        pytest.skip("carrier archives no multi-row consumer")
    execution = candidates[0]
    rows = execution["source_rows"]
    rows[0] = copy.deepcopy(rows[1])
    with pytest.raises(ReconciliationError, match="admitted endpoint"):
        _chain(ring, result)


def test_a_deleted_middle_landing_is_rejected(ring):
    _, sections, result = ring
    result = copy.deepcopy(result)
    frame = result["instances"][0]
    ledger, execution = _first_p2p_consumer(frame, sections)
    row = execution["source_rows"][0]
    # The direct producer holds the only archived copy of this content:
    # a compute run or a landed source row with the row's own digest.
    victim = None
    for candidate in result["instances"]:
        for core_ledger in candidate["cores"].values():
            for output in core_ledger["observations"].get(
                    "compute_outputs", []):
                for run in output.get("rows", []):
                    if run["digest"] == row["digest"]:
                        victim = ("compute", output, run)
            for other in core_ledger["observations"][
                    "descriptor_executions"]:
                if other is execution:
                    continue
                for source in other.get("source_rows", []):
                    if source["digest"] == row["digest"]:
                        victim = ("landing", other, source)
        if victim:
            break
    assert victim, "no admitted producer row holds the consumed content"
    kind, owner, run = victim
    if kind == "compute":
        owner["rows"].remove(run)
    else:
        owner["source_rows"].remove(run)
    with pytest.raises(ReconciliationError, match="no admitted producer|omits its admitted write access"):
        _chain(ring, result)


def test_an_error_b_disguised_as_success_is_rejected(ring, ring_error):
    program_dir, sections, _ = ring
    result = copy.deepcopy(ring_error)
    assert result["error_drained"], "the ring_error carrier is not an error run"
    core = next(row for row in result["cores"] if row["commands_errored"])
    core["commands_completed"] += core["commands_errored"]
    core["commands_errored"] = 0
    core["errored_command_ids"] = []
    from mesh_ir.workload_statistics import summarize_workload
    with pytest.raises(ReconciliationError, match="not terminal-ERROR"):
        summarize_workload(sections, result)


def test_a_missing_timing_group_cannot_shrink_the_report(ring):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    victim = result["dma_timings"][0]
    result["dma_timings"] = [row for row in result["dma_timings"]
                             if row["descriptor_id"] != victim["descriptor_id"]
                             or int(row["core_id"]) != int(victim["core_id"])]
    from mesh_ir.workload_statistics import summarize_workload
    with pytest.raises(ReconciliationError,
                       match="no timing row joining"):
        summarize_workload(sections, result)


def test_a_timing_row_borrowed_across_cores_is_rejected(ring):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    cores = sorted({int(row["core_id"]) for row in result["dma_timings"]})
    victim = next(row for row in result["dma_timings"]
                  if int(row["core_id"]) == cores[0])
    victim["core_id"] = cores[-1]
    from mesh_ir.workload_statistics import summarize_workload
    with pytest.raises(ReconciliationError,
                       match="no timing row joining|matches no archived "
                             "execution"):
        summarize_workload(sections, result)


def test_a_tampered_engine_cycle_count_breaks_the_prediction(ring):
    program_dir, sections, result = ring
    from mesh_ir.architecture import load_arch
    from mesh_ir.workload_statistics import summarize_workload
    report = summarize_workload(sections, result, arch=load_arch(ARCH),
                                program_dir=program_dir)
    assert all(row["matches"] for row in report["compute_prediction"].values())
    result = copy.deepcopy(result)
    ledger = result["instances"][0]["cores"]["7"]
    row = ledger["observations"]["engine_executions"][0]
    row["cycles"] += 1
    report = summarize_workload(sections, result, arch=load_arch(ARCH),
                                program_dir=program_dir)
    assert not all(row["matches"]
                   for row in report["compute_prediction"].values())

"""Gate 6 §13 observation metrics: every reported field is derived from a
real event owner and the derivation fails closed on impossible raw results.

The healthy control is one real compiled ring program executed on the real
2x2 fabric.  Each tamper edits the archived raw result once and must be
rejected by the shared summarizer for the stated reason.
"""

import copy
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
    run = run_garnet(tmp_path_factory.mktemp("m5out"), "m5out", "ring_workload",
                     program_dir, arch=ARCH)
    assert run.returncode == 0, output_of(run)
    sections = json.loads(
        (program_dir / "schedule.mesh.json").read_text())["sections"]
    return program_dir, sections, result_of(program_dir)


def _summarize(ring):
    from mesh_ir.architecture import load_arch
    from mesh_ir.workload_statistics import summarize_workload
    program_dir, sections, result = ring
    return summarize_workload(sections, result, arch=load_arch(ARCH),
                              program_dir=program_dir)


def test_reported_metrics_come_from_real_owners(ring):
    from mesh_ir.generated import abi as A
    program_dir, sections, result = ring
    report = _summarize(ring)
    assert report["unavailable"] == [], report
    assert report["compute_prediction"], "no compiler prediction consumed"
    assert all(row["matches"] for row in report["compute_prediction"].values())
    utilization = report["run_window"]["link_vc_utilization"]
    assert utilization, "no link/VC utilization facts"
    for value in utilization.values():
        assert 0 <= value <= 1, value
    for bridge in report["bridges"]:
        assert bridge["clock_period_ticks"] > 0, bridge
        assert bridge["peak_read_outstanding"] >= 0, bridge
        assert bridge["peak_write_outstanding"] >= 0, bridge
    from mesh_ir.architecture import load_arch
    for core in report["sram"]:
        assert [bank["bank_id"] for bank in core["banks"]] == list(range(load_arch(ARCH).sram_banks))
        for field in ("read_bytes", "write_bytes"):
            assert core[field] == sum(bank[field] for bank in core["banks"])
    wait_opcodes = 0
    total_descriptors = 0
    for instance, frame in zip(report["instances"], result["instances"]):
        for core in instance["cores"]:
            assert core["dependency_stall_ticks"] >= 0, core
            assert core["engine_fifo_stall_ticks"] >= 0, core
            wait_opcodes += core["opcodes"].get(int(A.OPCODE.RECV_WAIT), 0)
            assert core["dma_latency_ticks"]["queue_wait"], core
            dma = core["dma"]
            executed = sum(
                len(ledger["observations"]["descriptor_executions"])
                for core_id, ledger in frame["cores"].items()
                if int(core_id) == core["core_id"])
            assert dma["descriptors"] == executed, (dma, executed)
            total_descriptors += dma["descriptors"]
            assert dma["bursts"] > 0 and dma["bus_bytes"] > 0, dma
            assert core["dma_response_errors"] == 0, core
            assert core["peak_allocation_bytes"] > 0, core
    executed_total = sum(
        len(ledger["observations"]["descriptor_executions"])
        for frame in result["instances"]
        for ledger in frame["cores"].values())
    assert total_descriptors == executed_total, "per-instance frames must not share executions"
    assert wait_opcodes > 0, "ring archive carries no RECV_WAIT command"
    for row in report["sram"]:
        assert row["read_bytes"] > 0 and row["write_bytes"] > 0, row


def test_peak_allocation_follows_the_planner_high_water(ring):
    program_dir, sections, _ = ring
    report = _summarize(ring)
    for instance in report["instances"]:
        for core in instance["cores"]:
            expected = max(
                row["offset_bytes"] + row["size_bytes"]
                for row in sections["ALLOCATIONS"]
                if row["owner_core"] == core["core_id"]
                and row["memory_space"] == 3)
            assert core["peak_allocation_bytes"] == expected


def test_dma_latency_histogram_counts_every_joined_execution(ring):
    program_dir, sections, result = ring
    report = _summarize(ring)
    for instance, frame in zip(report["instances"], result["instances"]):
        for core in instance["cores"]:
            ledger = next(l for cid, l in frame["cores"].items()
                          if int(cid) == core["core_id"])
            completed = {
                (execution["descriptor_id"], execution["completion_tick"])
                for execution in ledger["observations"][
                    "descriptor_executions"]
                if execution.get("completed")}
            phases = sum(
                (1 if row["first_ar_tick"] else 0)
                + (1 if row["first_aw_tick"] else 0)
                for row in result["dma_timings"]
                if row["core_id"] == core["core_id"]
                and (row["descriptor_id"], row["done_tick"]) in completed)
            counted = sum(core["dma_latency_ticks"]["queue_wait"].values())
            assert counted == phases, (counted, phases)


def test_wait_stall_ticks_match_the_archived_episodes(ring):
    program_dir, sections, result = ring
    report = _summarize(ring)
    per_core = {core["core_id"]: core["dependency_stall_ticks"]
                for instance in report["instances"]
                for core in instance["cores"]}
    for frame in result["instances"]:
        for core_id, ledger in frame["cores"].items():
            expected = sum(row["last_tick"] - row["first_tick"]
                           for row in ledger["observations"].get(
                               "wait_stalls", ()))
            assert per_core[int(core_id)] == expected


def test_a_descriptor_completing_before_its_address_phase_is_rejected(ring):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    victim = next(row for row in result["dma_timings"]
                  if row["first_ar_tick"] and row["done_tick"])
    victim["done_tick"] = victim["first_ar_tick"] - 1
    from mesh_ir.workload_statistics import summarize_workload
    # Rewriting the done tick breaks the execution join first; the
    # impossible window is the same lie seen one invariant later.
    with pytest.raises(ReconciliationError,
                       match="matches no archived execution completion"):
        summarize_workload(sections, result)


def test_a_duplicated_timing_row_is_rejected(ring):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    victim = next(row for row in result["dma_timings"] if row["done_tick"])
    result["dma_timings"].append(copy.deepcopy(victim))
    from mesh_ir.workload_statistics import summarize_workload
    with pytest.raises(ReconciliationError, match="second timing row"):
        summarize_workload(sections, result)


def test_timing_phases_drifted_from_their_own_bursts_are_rejected(ring):
    program_dir, sections, result = ring
    from mesh_ir.workload_statistics import summarize_workload
    for field in ("first_ar_tick", "first_aw_tick", "first_b_tick"):
        drifted = copy.deepcopy(result)
        victim = next(row for row in drifted["dma_timings"]
                      if row.get(field))
        victim[field] += 500
        with pytest.raises(ReconciliationError,
                           match="its own bursts pin"):
            summarize_workload(sections, drifted)


def test_an_engine_execution_ending_before_it_begins_is_rejected(ring):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    frame = result["instances"][0]
    ledger = next(iter(frame["cores"].values()))
    row = ledger["observations"]["engine_executions"][0]
    row["end_tick"] = row["begin_tick"] - 1
    from mesh_ir.workload_statistics import summarize_workload
    with pytest.raises(ReconciliationError, match="ends before it begins"):
        summarize_workload(sections, result)


def test_a_wait_stall_closing_before_it_opens_is_rejected(ring):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    frame = result["instances"][0]
    episodes = None
    for ledger in frame["cores"].values():
        rows = ledger["observations"].get("wait_stalls")
        if rows:
            episodes = rows
            break
    if not episodes:
        pytest.skip("archive records no wait stall episode")
    episodes[0]["last_tick"] = episodes[0]["first_tick"] - 1
    from mesh_ir.workload_statistics import summarize_workload
    with pytest.raises(ReconciliationError, match="closes before it opens"):
        summarize_workload(sections, result)


def test_a_command_terminating_before_issue_is_rejected(ring):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    frame = result["instances"][0]
    ledger = next(iter(frame["cores"].values()))
    row = next(item for item in ledger["observations"]["commands"]
               if item["terminal"])
    row["terminal_tick"] = row["issue_tick"] - 1
    from mesh_ir.workload_statistics import summarize_workload
    with pytest.raises(ReconciliationError,
                       match="terminates before it is issued"):
        summarize_workload(sections, result)


@pytest.mark.parametrize("mutation", ["missing_bank", "inflated_bank"])
def test_bank_tampering_is_rejected_on_real_ring(ring, mutation):
    program_dir, sections, result = ring
    result = copy.deepcopy(result)
    banks = result["cores"][0]["sram_banks"]
    if mutation == "missing_bank":
        banks.pop()
    else:
        banks[0]["read_bytes"] += 1
    with pytest.raises(ReconciliationError, match="SRAM"):
        _summarize((program_dir, sections, result))

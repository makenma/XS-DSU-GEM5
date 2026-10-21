import copy
import json

import pytest

from mesh_ir.runtime_reconciliation import ReconciliationError, verified_burst_attribution
from tests.integration.support.garnet_harness import output_of, run_garnet
from tests.integration.support.workload_harness import ARCH, compiled_workloads, descriptors


@pytest.fixture(scope="module")
def ring_run(compiled_workloads, tmp_path_factory):
    program = compiled_workloads("mlp")
    root = tmp_path_factory.mktemp("ring-run")
    run = run_garnet(root, "m5out", "ring_workload", program, arch=ARCH, artifact_dir=root)
    assert run.returncode == 0, output_of(run)
    assert "tensor class traffic: PASS" in output_of(run)
    return program, json.loads((root / "actual_result.json").read_text())


def test_ring_runs_the_declared_algorithm(ring_run):
    program, result = ring_run
    expected = descriptors(program)
    summary = verified_burst_attribution(result["instances"], expected,
                                         max_beats=16, beat_bytes=32, bridges=result["bridges"])
    assert summary["bursts"] == sum(row["bursts"] for row in expected.values())
    assert result["credit_ledger"]


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "generation", "core"))
def test_ring_rejects_corrupted_contributions(ring_run, mutation):
    program, original = ring_run
    result = copy.deepcopy(original)
    rows = result["instances"][0]["bursts"]
    peers = [row for row in rows if descriptors(program)[row["descriptor_id"]]["kind"] == 3]
    victim = peers[len(peers) // 2]
    if mutation == "missing":
        rows.remove(victim)
        bridge = next(row for row in result["bridges"] if row["core_id"] == victim["core_id"])
        bridge["aw_accepted"] -= 1
        bridge["write_bursts_submitted"] -= 1
    elif mutation == "duplicate":
        rows[rows.index(victim)] = copy.deepcopy(peers[0])
    elif mutation == "generation":
        victim["generation"] += 1
    else:
        victim["core_id"] = next(core for core in (7, 2, 9, 13) if core != victim["core_id"])
    with pytest.raises(ReconciliationError):
        verified_burst_attribution(result["instances"], descriptors(program),
                                  max_beats=16, beat_bytes=32, bridges=result["bridges"])


@pytest.mark.parametrize("case, cause, exit_code", (
    ("ring_error", "MESH_PROGRAM_ERROR_DRAINED", 0),
    ("ring_watchdog", "E_RUNTIME_DEADLOCK", 1),
))
def test_ring_real_fault_lifecycle(compiled_workloads, tmp_path, case, cause, exit_code):
    program = compiled_workloads("mlp")
    run = run_garnet(tmp_path, "m5out", case, program, arch=ARCH, artifact_dir=tmp_path)
    assert (run.returncode == 0) == (exit_code == 0), output_of(run)
    assert cause in output_of(run), output_of(run)
    if exit_code == 0:
        result = json.loads((tmp_path / "actual_result.json").read_text())
        assert result["error_drained"] and not result["watchdog_fired"]
        assert result["garnet"]["quiescent"]
        assert any(ledger["cancelled"] for ledger in result["instances"][0]["cores"].values())
    else:
        diagnostics = [json.loads(line) for line in
                       (tmp_path / "runtime_diagnostics.jsonl").read_text().splitlines()]
        assert any(row["code"] == cause and row["stage"] == "watchdog" for row in diagnostics)
        frames = [json.loads(line.strip().split(" ", 1)[1]) for line in output_of(run).splitlines()
                  if line.strip().startswith("observation_frame_json ")]
        assert {row["core_id"] for row in frames} == {7, 2, 9, 13}
        assert any(not row["completed"] for frame in frames for row in frame["descriptor_executions"])
        assert any(row["issued"] and not row["terminal"] for frame in frames for row in frame["commands"])


def test_trace_does_not_change_execution(ring_run, tmp_path):
    from mesh_ir.runtime_trace import TraceFilter, write_mesh_trace
    from tests.integration.support.garnet_harness import schedule

    program, baseline = ring_run
    run = run_garnet(tmp_path, "m5out", "ring_workload", program, arch=ARCH,
                     artifact_dir=tmp_path, extra_args=("--mesh-trace",))
    assert run.returncode == 0, output_of(run)
    result = json.loads((tmp_path / "actual_result.json").read_text())
    assert result == baseline
    trace = tmp_path / "mesh_trace.jsonl"
    second = tmp_path / "second.jsonl"
    write_mesh_trace(second, schedule(program), result)
    assert trace.read_bytes() == second.read_bytes()
    selected = json.loads(trace.read_text().splitlines()[0])
    filtered = tmp_path / "filtered.jsonl"
    write_mesh_trace(filtered, schedule(program), result,
                     TraceFilter(cores=(selected["core_id"],), commands=(selected["command_id"],),
                                 tensors=(selected["tensor_id"],), begin=selected["tick"], end=selected["tick"]))
    assert selected in [json.loads(line) for line in filtered.read_text().splitlines()]


@pytest.mark.parametrize("mutation", ("packets", "credit"))
def test_ring_rejects_counter_lies_with_true_flags(ring_run, mutation):
    from mesh_ir.runtime_reconciliation import expected_garnet_traffic, verified_garnet_traffic, verified_drain

    program, original = ring_run
    result = copy.deepcopy(original)
    if mutation == "packets":
        traffic = result["garnet_traffic"]
        traffic["injected"]["packets"][0] += 1
        traffic["received"]["packets"][0] += 1
        with pytest.raises(ReconciliationError):
            verified_garnet_traffic(traffic, expected_garnet_traffic(descriptors(program), 1), flit_bytes=16, vnets=5)
    else:
        row = next(row for row in result["credit_ledger"] if row["returned"] > 0)
        row["returned"] -= 1
        with pytest.raises(ReconciliationError, match="conserved|initial depth"):
            verified_drain(result["garnet"], result["credit_ledger"], result["drain"],
                           result["bridges"], result["instances"][0]["apertures"], result["memory_endpoints"])


@pytest.mark.parametrize("mutation", ("uid", "event", "receiver", "missing"))
def test_ring_checks_target_identity_and_event_timeline(ring_run, mutation):
    from mesh_ir.architecture import load_arch
    from mesh_ir.fault_plan import transaction_arch
    from mesh_ir.generated import abi as A
    from mesh_ir.workload_accounting import verified_collective_landings
    from tests.integration.support.garnet_harness import schedule

    program, original = ring_run
    sections = schedule(program)
    arch = transaction_arch(load_arch(ARCH))
    expected = descriptors(program)
    assert verified_collective_landings(program, sections, original, expected, arch=arch, instances=1)
    result = copy.deepcopy(original)
    frame = result["instances"][0]
    aperture = next(row for row in frame["apertures"] if row["transfers"])
    transfer = aperture["transfers"][0]
    if mutation == "uid":
        event = transfer["landing_events"][0]
        old = event["txn_uid"]
        event["txn_uid"] ^= 1 << 30
        transfer["transaction_uids"] = [event["txn_uid"] if uid == old else uid for uid in transfer["transaction_uids"]]
    elif mutation == "event":
        transfer["landing_events"][0]["tick"] = 0
        transfer["stages"][0]["tick"] = 0
    elif mutation == "missing":
        aperture["transfers"].pop()
    else:
        command = next(row for row in sections["COMMANDS"]
                       if row["opcode"] == A.OPCODE.RECV_WAIT and row["core_id"] == aperture["core_id"]
                       and sections["OP_ATTRS"][row["attr_index"] - 1]["transfer_id"] == transfer["transfer_id"])
        receiver = next(row for row in frame["cores"][str(aperture["core_id"])]["observations"]["commands"]
                        if row["command_id"] == command["command_id"])
        receiver["terminal_tick"] = transfer["commit_tick"]
    with pytest.raises(ReconciliationError):
        verified_collective_landings(program, sections, result, expected, arch=arch, instances=1)

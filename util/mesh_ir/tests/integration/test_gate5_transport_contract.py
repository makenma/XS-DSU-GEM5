"""Gate 5 transport and oracle contract: the shared entries reject bad results.

The healthy baseline is one real AXI/Garnet run; every further case applies a
single tampering to the archived result and asserts that the same production
entry the runner uses refuses it, with a specific reason.  Real fault injection
and result tampering are separate kinds of evidence and neither replaces the
other.
"""

import copy
import json
import shutil

import pytest

from mesh_ir.producer_content import (
    ProducerContentError,
    verify_transfer_producer_content,
)
from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    loader_control_plane_delta,
    reconcile,
    verified_burst_geometry,
    verified_drain,
    verified_queue_bounds,
)

from tests.integration.support.garnet_harness import (
    ARCH,
    build_program,
    cause_of,
    output_of,
    result_of,
    run_garnet,
    schedule,
)

PROGRAM = "single"
CASE = "dma_basic"
STORE_DESCRIPTOR = 3


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("gate5-transport")
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_garnet(tmp_path, "m5out", CASE, program_dir)
    assert run.returncode == 0, output_of(run)
    assert "MESH_PROGRAM_DONE" in output_of(run), output_of(run)
    oracle = json.loads((program_dir / "gate5_oracle.json").read_text())
    return {
        "program_dir": program_dir,
        "result": result_of(program_dir),
        "schedule": json.loads((program_dir / "schedule.mesh.json").read_text()),
        "expected": json.loads((program_dir / "expected_traffic.json").read_text())[
            "descriptors"
        ],
        "oracle": oracle,
    }


def _expected_rows(baseline):
    kinds = {
        row["descriptor_id"]: row["kind"]
        for row in baseline["schedule"]["sections"]["DMA_DESCRIPTORS"]
    }
    return [
        {**row, **row["identity"], "kind": kinds[row["identity"]["descriptor_id"]]}
        for row in baseline["expected"]
    ]


def _reconcile(baseline, result, expected_rows=None):
    return reconcile(
        cause="MESH_PROGRAM_DONE: contract",
        result=result,
        schedule=baseline["schedule"],
        expected_rows=expected_rows if expected_rows is not None else _expected_rows(baseline),
        error_descriptors=(),
        fault_occurrence=0,
        instances=1,
        traffic_multiplier=1,
        read_payload_digests={
            int(key): value
            for key, value in baseline["oracle"]["read_payload_digests"].items()
        },
    )


def test_healthy_real_run_is_accepted_by_both_entries(baseline):
    summary = _reconcile(baseline, baseline["result"])
    assert summary["status"] == "ok"
    assert summary["outcome"] == "done"
    assert len(summary["descriptors"]) == 3
    produced = verify_transfer_producer_content(
        baseline["program_dir"], baseline["result"], STORE_DESCRIPTOR
    )
    assert produced["instances"], produced
    assert produced["allocation_id"], produced


def test_loader_install_moves_no_traffic_while_dispatch_does(baseline):
    window = baseline["result"]["loader_traffic"]
    assert loader_control_plane_delta(window) == {}
    assert window["end_tick"] >= window["begin_tick"]
    accepted = sum(
        bridge["ar_accepted"] + bridge["aw_accepted"]
        for bridge in baseline["result"]["bridges"]
    )
    assert accepted > 0


def test_missing_loader_window_is_rejected(baseline):
    with pytest.raises(ReconciliationError):
        loader_control_plane_delta(None)
    with pytest.raises(ReconciliationError):
        loader_control_plane_delta({"captured": False})


def test_install_that_moved_traffic_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    result["loader_traffic"]["end"]["ar_accepted"] += 1
    moved = loader_control_plane_delta(result["loader_traffic"])
    assert "ar_accepted" in moved


def test_inverted_loader_window_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    result["loader_traffic"]["end_tick"] = result["loader_traffic"]["begin_tick"] - 1
    with pytest.raises(ReconciliationError) as error:
        loader_control_plane_delta(result["loader_traffic"])
    assert "inverted" in str(error.value)


def test_duplicate_raw_core_identity_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    result["cores"].append(copy.deepcopy(result["cores"][0]))
    with pytest.raises(ReconciliationError) as error:
        _reconcile(baseline, result)
    assert "duplicates core identity" in str(error.value)


def test_duplicate_command_generation_in_the_plan_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    result["cores"][0]["dispatch_plan"].append(
        list(result["cores"][0]["dispatch_plan"][0])
    )
    with pytest.raises(ReconciliationError) as error:
        _reconcile(baseline, result)
    assert "duplicates a command generation" in str(error.value)


def test_execution_moved_to_another_instance_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    first = result["instances"][0]["cores"]["0"]["observations"]
    moved = first["descriptor_executions"].pop()
    second = copy.deepcopy(result["instances"][0])
    second["instance"] = 2
    second["cores"]["0"]["observations"]["descriptor_executions"].append(moved)
    result["instances"].append(second)
    with pytest.raises(ReconciliationError) as error:
        _reconcile(baseline, result)
    assert "instance" in str(error.value)


def test_terminal_outside_the_plan_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    result["instances"][0]["cores"]["0"]["terminals"].append(
        {"command_id": 4242, "generation": 0, "state": "completed"}
    )
    with pytest.raises(ReconciliationError) as error:
        _reconcile(baseline, result)
    assert "outside the plan" in str(error.value)


def test_shrinking_the_expected_plan_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    dropped = [
        row for row in _expected_rows(baseline) if row["descriptor_id"] == STORE_DESCRIPTOR
    ]
    assert dropped
    with pytest.raises(ReconciliationError) as error:
        _reconcile(
            baseline,
            result,
            expected_rows=[
                row
                for row in _expected_rows(baseline)
                if row["descriptor_id"] != STORE_DESCRIPTOR
            ],
        )
    assert "without an admitted group" in str(error.value)


def test_healthy_run_reporting_an_errored_terminal_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    result["instances"][0]["cores"]["0"]["terminals"][0]["state"] = "errored"
    with pytest.raises(ReconciliationError) as error:
        _reconcile(baseline, result)
    assert "healthy completion records" in str(error.value)


def test_transport_bytes_without_an_execution_are_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    result["instances"][0]["cores"]["0"]["observations"][
        "descriptor_executions"
    ] = []
    with pytest.raises(ReconciliationError) as error:
        _reconcile(baseline, result)
    assert "traffic row without any execution" in str(error.value)


def test_shifted_producer_and_transfer_address_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    observations = result["instances"][0]["cores"]["0"]["observations"]
    for row in observations["compute_outputs"]:
        row["offset"] += 64
    for row in observations["descriptor_executions"]:
        if row["descriptor_id"] == STORE_DESCRIPTOR:
            for source in row["source_rows"]:
                source["address"] += 64
    with pytest.raises(ProducerContentError) as error:
        verify_transfer_producer_content(
            baseline["program_dir"], result, STORE_DESCRIPTOR
        )
    assert "producer" in str(error.value)


def test_producer_content_from_a_foreign_offset_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    observations = result["instances"][0]["cores"]["0"]["observations"]
    producer = next(
        row
        for row in observations["compute_outputs"]
        if row["rows"] and row["rows"][0]["address"] > 0
    )
    producer["offset"] = producer["offset"] + producer["rows"][0]["size"]
    with pytest.raises(ProducerContentError) as error:
        verify_transfer_producer_content(
            baseline["program_dir"], result, STORE_DESCRIPTOR
        )
    assert "producer" in str(error.value)


def test_dropping_a_consumer_row_is_rejected(baseline):
    result = copy.deepcopy(baseline["result"])
    observations = result["instances"][0]["cores"]["0"]["observations"]
    for row in observations["descriptor_executions"]:
        if row["descriptor_id"] == STORE_DESCRIPTOR:
            row["source_rows"] = row["source_rows"][:-1]
    with pytest.raises(ProducerContentError):
        verify_transfer_producer_content(
            baseline["program_dir"], result, STORE_DESCRIPTOR
        )


STORE_BEAT_BYTES = 32
WRITE_CHANNEL = "AW"


def _admitted_write_bursts(baseline):
    from mesh_ir.burst_splitter import plan_descriptor

    row = next(
        row for row in _expected_rows(baseline)
        if row["descriptor_id"] == STORE_DESCRIPTOR
    )
    plan = plan_descriptor(
        row["row_bytes"], row["rows"], row["remote_address"],
        row["remote_stride_bytes"], STORE_BEAT_BYTES, row["max_burst_beats"],
    )
    return sorted((burst.beat_base, burst.beats) for burst in plan.bursts)


def _write_geometry(baseline, rows):
    return verified_burst_geometry(
        rows, max_beats=16, beat_bytes=STORE_BEAT_BYTES,
        admitted=_admitted_write_bursts(baseline),
    )


def test_the_admitted_write_split_is_the_archived_one(baseline):
    writes = [
        row for row in baseline["result"]["burst_timings"]
        if row["channel"] == WRITE_CHANNEL
    ]
    assert _write_geometry(baseline, writes) == len(writes)


def test_a_duplicated_write_burst_cannot_mask_a_missing_one(baseline):
    writes = [
        copy.deepcopy(row) for row in baseline["result"]["burst_timings"]
        if row["channel"] == WRITE_CHANNEL
    ]
    assert len(writes) >= 3, writes
    writes[1] = copy.deepcopy(writes[0])
    with pytest.raises(ReconciliationError) as error:
        _write_geometry(baseline, writes)
    assert "admitted split" in str(error.value)


def test_a_write_burst_cannot_be_relabelled_as_a_response(baseline):
    result = copy.deepcopy(baseline["result"])
    index = next(
        index for index, row in enumerate(result["burst_timings"])
        if row["channel"] == WRITE_CHANNEL
    )
    result["burst_timings"][index]["channel"] = "B"
    writes = [
        row for row in result["burst_timings"] if row["channel"] == WRITE_CHANNEL
    ]
    with pytest.raises(ReconciliationError) as error:
        _write_geometry(baseline, writes)
    assert "admitted split" in str(error.value)


def test_a_store_cannot_claim_completion_with_a_short_burst_plan(baseline):
    result = copy.deepcopy(baseline["result"])
    for row in result["instances"][0]["cores"]["0"]["observations"][
        "descriptor_executions"
    ]:
        if row["descriptor_id"] == STORE_DESCRIPTOR:
            row["transfer"]["write_bursts"] -= 1
            row["transfer"]["write_bytes"] -= 512
    with pytest.raises(ReconciliationError) as error:
        _reconcile(baseline, result)
    assert "write_bytes mismatch" in str(error.value)


def test_a_late_success_execution_and_its_traffic_row_cannot_both_vanish(baseline):
    result = copy.deepcopy(baseline["result"])
    observations = result["instances"][0]["cores"]["0"]["observations"]
    observations["descriptor_executions"] = [
        row for row in observations["descriptor_executions"]
        if row["descriptor_id"] != STORE_DESCRIPTOR
    ]
    result["transport"] = [
        row for row in result["transport"]
        if row["descriptor_id"] != STORE_DESCRIPTOR
    ]
    with pytest.raises(ReconciliationError):
        _reconcile(baseline, result)


def _three_instances(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("gate5-drain")
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_garnet(
        tmp_path, "m5out-drain", CASE, program_dir,
        extra_args=("--instances", "3"),
    )
    assert run.returncode == 0, output_of(run)
    return result_of(program_dir)


@pytest.fixture(scope="module")
def drained(tmp_path_factory):
    return _three_instances(tmp_path_factory)


def _drain(drained, instances=3, **overrides):
    result = copy.deepcopy(drained)
    result.update(overrides)
    return verified_drain(
        result.get("garnet"), result.get("credit_ledger"), result.get("drain"),
        result["bridges"],
        [aperture for record in result["instances"]
         for aperture in record["apertures"]],
        result.get("memory_endpoint"), instances,
    )


def test_global_drain_gates_every_instance_and_the_exit(drained):
    window = _drain(drained)
    assert drained["drain"]["instances_drained"] == 3, drained["drain"]
    assert window["end_pending"] == 0, window
    assert drained["garnet"]["quiescent"] in (1, True)
    assert drained["memory_endpoint"]["idle"] in (1, True)
    ledger = drained["credit_ledger"]
    assert ledger and all(entry["restored"] for entry in ledger), ledger[:2]


def test_a_non_quiescent_exit_is_rejected(drained):
    result = copy.deepcopy(drained)
    result["garnet"] = {**result["garnet"], "quiescent": 0}
    with pytest.raises(ReconciliationError) as error:
        _drain(result)
    assert "not quiescent" in str(error.value)


def test_an_unrestored_credit_link_is_rejected(drained):
    result = copy.deepcopy(drained)
    result["credit_ledger"][0] = {**result["credit_ledger"][0], "restored": False}
    with pytest.raises(ReconciliationError) as error:
        _drain(result)
    assert "never returned to its initial depth" in str(error.value)


def test_in_flight_flits_at_the_exit_are_rejected(drained):
    result = copy.deepcopy(drained)
    result["drain"] = {**result["drain"], "end_pending": 4}
    with pytest.raises(ReconciliationError) as error:
        _drain(result)
    assert "still has 4 in-flight" in str(error.value)


def test_an_ungated_instance_is_rejected(drained):
    result = copy.deepcopy(drained)
    result["drain"] = {**result["drain"], "instances_drained": 2}
    with pytest.raises(ReconciliationError) as error:
        _drain(result)
    assert "closed 2 instances but 3 ran" in str(error.value)


def test_a_busy_bridge_at_the_exit_is_rejected(drained):
    result = copy.deepcopy(drained)
    bridges = copy.deepcopy(result["bridges"])
    bridges[0] = {**bridges[0], "idle": 0, "outstanding_reads": 1}
    result["bridges"] = bridges
    with pytest.raises(ReconciliationError) as error:
        _drain(result)
    assert "still holds in-flight work" in str(error.value)


def test_a_busy_endpoint_at_the_exit_is_rejected(drained):
    result = copy.deepcopy(drained)
    result["memory_endpoint"] = {**result["memory_endpoint"], "idle": False}
    with pytest.raises(ReconciliationError) as error:
        _drain(result)
    assert "endpoint is not idle" in str(error.value)


def _run_case(tmp_path, name, case, program, extra=()):
    # Each run publishes its own program: publication is no-replace.
    work = tmp_path / name
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, program)
    run = run_garnet(work, f"m5out-{name}", case, program_dir,
                     extra_args=tuple(extra))
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    return run, result_of(program_dir)


def test_deferred_exit_waits_for_a_slow_network(tmp_path):
    run, result = _run_case(tmp_path, "deferred", "drain_deferred", "single")
    assert "global drain: PASS" in output_of(run), output_of(run)
    drain = result["drain"]
    assert drain["instances_drained"] == 1, drain
    assert drain["begin_pending"] > 0, drain
    assert drain["end_tick"] > drain["begin_tick"], drain
    assert drain["end_pending"] == 0, drain
    assert result["garnet"]["quiescent"] in (1, True)
    assert all(entry["restored"] for entry in result["credit_ledger"])


def test_no_deferral_when_the_network_is_already_empty(tmp_path):
    _, result = _run_case(tmp_path, "control", "dma_basic", "single")
    drain = result["drain"]
    assert drain["begin_pending"] == 0, drain
    assert drain["end_tick"] == drain["begin_tick"], drain


def test_deferral_grows_with_the_credit_round_trip(tmp_path):
    # The plain carrier leaves the per-link latency to the command line, so the
    # two runs differ only in the credit round trip.
    _, short = _run_case(
        tmp_path, "short", "dma_basic", "single",
        extra=("--link-latency", "8"),
    )
    _, long = _run_case(
        tmp_path, "long", "dma_basic", "single",
        extra=("--link-latency", "20"),
    )
    short_wait = short["drain"]["end_tick"] - short["drain"]["begin_tick"]
    long_wait = long["drain"]["end_tick"] - long["drain"]["begin_tick"]
    assert short["drain"]["begin_pending"] > 0, short["drain"]
    assert long["drain"]["begin_pending"] > 0, long["drain"]
    assert short_wait > 0 and long_wait > short_wait, (short_wait, long_wait)
    assert short["drain"]["end_pending"] == long["drain"]["end_pending"] == 0


def _rejected_run(tmp_path, name, *, corrupt=False, tuned_arch=False):
    work = tmp_path / name
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, PROGRAM)
    arch = None
    if corrupt:
        published = work / "program-corrupt"
        shutil.copytree(program_dir, published)
        image = published / "program.mshb"
        data = bytearray(image.read_bytes())
        data[len(data) // 2] ^= 0xFF
        image.write_bytes(bytes(data))
        program_dir = published
    if tuned_arch:
        # A tuning-only change is a legal command line but a different
        # architecture identity, so admission must refuse the program.
        text = ARCH.read_text()
        assert "setup_cycles: 2" in text
        arch = work / "arch-tuned.yaml"
        arch.write_text(text.replace("setup_cycles: 2", "setup_cycles: 5"))
    run = run_garnet(work, "m5out", CASE, program_dir, arch=arch)
    return run, work, program_dir


def _assert_rejected_before_issue(run, work, program_dir, expected_code, phase):
    output = output_of(run)
    assert run.returncode != 0, output
    assert expected_code in output, output
    # No tick of the simulation ran, so no data-plane work could exist.
    assert "Entering event queue" not in output, output
    assert not (program_dir / "actual_result.json").exists()
    assert (work / "m5out" / "stats.txt").read_text().strip() == "", (
        "a rejected admission produced counter output"
    )
    # The loader writes diagnostics into the artifact directory, which the
    # harness pins to the program directory for these runs.
    records = [
        json.loads(line)
        for line in (program_dir / "runtime_diagnostics.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    matches = [row for row in records if row["code"] == expected_code]
    assert matches, records
    record = matches[0]
    assert record["severity"] == "error", record
    context = record["context"]
    assert context["install_state"] == "pre_install", record
    assert context["installed_cores"] == "0", record
    assert context["phase"] == phase, record


def test_admission_rejects_a_corrupt_image_without_issuing_work(tmp_path):
    run, work, program_dir = _rejected_run(tmp_path, "corrupt", corrupt=True)
    _assert_rejected_before_issue(
        run, work, program_dir, "E_ABI_CHECKSUM", "decode"
    )


def test_admission_rejects_a_program_for_a_different_architecture(tmp_path):
    run, work, program_dir = _rejected_run(tmp_path, "tuned", tuned_arch=True)
    _assert_rejected_before_issue(
        run, work, program_dir, "E_ARCH_DIGEST", "admission"
    )


SHALLOW_CASES = {
    "dma_basic_shallow": "single",
    "p2p_frames_shallow": "dual",
}


def _shallow(tmp_path, case, instances=1):
    work = tmp_path / f"{case}-{instances}"
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, SHALLOW_CASES[case])
    arguments = ["--instances", str(instances)]
    run = run_garnet(work, "m5out", case, program_dir, extra_args=tuple(arguments))
    assert run.returncode == 0, output_of(run)
    assert "MESH_PROGRAM_DONE" in output_of(run), output_of(run)
    return result_of(program_dir)


def _queue_frames(result):
    return [
        (record["instance"], {
            core_id: ledger
            for core_id, ledger in record["cores"].items()
            if ledger["observations"]["descriptor_executions"]
        })
        for record in result["instances"]
    ]


def _queue_bounds(result, *, depth=2, window=2):
    return verified_queue_bounds(
        _queue_frames(result), result["bridges"], result.get("network_stalls"),
        descriptor_queue_depth=depth, read_window=window,
    )


@pytest.mark.parametrize("case", sorted(SHALLOW_CASES))
@pytest.mark.parametrize("instances", (1, 3))
def test_shallow_queues_bound_every_owner_and_really_bite(tmp_path, case, instances):
    result = _shallow(tmp_path, case, instances)
    summary = _queue_bounds(result)
    assert summary["descriptor_queue_depth"] == 2, summary
    assert max(summary["descriptor_peaks"]) == 2, summary
    assert summary["busy_cycles"] > 0, summary


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("over-depth", "held .* descriptors in flight"),
        ("depth-never-reached", "never reached its descriptor depth"),
        ("window-overrun", "read window peaked"),
        ("window-never-filled", "never filled its read window"),
        ("unretired-burst", "retire every burst"),
        ("no-blocking", "no measurable source blocking"),
    ),
)
def test_queue_bounds_reject_a_shallow_run_that_did_not_really_bite(
    tmp_path, tamper, message
):
    result = _shallow(tmp_path, "dma_basic_shallow", 1)
    corrupted = copy.deepcopy(result)
    if tamper == "over-depth":
        ledger = corrupted["instances"][0]["cores"]["0"]
        ledger["observations"]["descriptor_executions"][2]["submit_tick"] = (
            ledger["observations"]["descriptor_executions"][0]["submit_tick"]
        )
    elif tamper == "depth-never-reached":
        for record in corrupted["instances"]:
            for ledger in record["cores"].values():
                for index, row in enumerate(
                    ledger["observations"]["descriptor_executions"]
                ):
                    row["submit_tick"] = index * 1000
                    row["completion_tick"] = index * 1000 + 1
    elif tamper == "window-overrun":
        corrupted["bridges"][0]["peak_read_outstanding"] = 3
    elif tamper == "window-never-filled":
        corrupted["bridges"][0]["peak_read_outstanding"] = 1
    elif tamper == "unretired-burst":
        corrupted["bridges"][0]["read_bursts_completed"] -= 1
    elif tamper == "no-blocking":
        for channel in corrupted["network_stalls"].values():
            channel["ni_vc_busy_cycles"] = 0
    with pytest.raises(ReconciliationError, match=message):
        _queue_bounds(corrupted)


DRAIN_STALL_CASE = "drain_stalled"
DRAIN_CONTROL_CASE = "drain_deferred"


def _drain_carrier(tmp_path, case):
    work = tmp_path / case
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, "single")
    run = run_garnet(work, "m5out", case, program_dir)
    return run, program_dir


def test_a_lost_credit_return_is_reported_by_the_drain_phase_watchdog(tmp_path):
    run, program_dir = _drain_carrier(tmp_path, DRAIN_STALL_CASE)
    output = output_of(run)
    assert run.returncode != 0, output
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert "the drain made no progress" in output, output
    # The data path is empty and every core halted: only the credit ledger is
    # still waiting, which is what the drain phase must report.
    assert "unrestored_links=1" in output, output
    assert "deficit=1" in output, output
    assert "MESH_PROGRAM_DONE" not in output, output
    assert "MESH_PROGRAM_ERROR_DRAINED" not in output, output
    records = [
        json.loads(line)
        for line in (program_dir / "runtime_diagnostics.jsonl").read_text().splitlines()
    ]
    deadlocks = [
        row for row in records if row["code"] == "E_RUNTIME_DEADLOCK"
    ]
    assert deadlocks, records
    assert deadlocks[-1]["context"]["phase"] == "drain", deadlocks[-1]
    assert not (program_dir / "actual_result.json").exists()


def test_the_same_carrier_without_the_lost_credit_drains(tmp_path):
    run, program_dir = _drain_carrier(tmp_path, DRAIN_CONTROL_CASE)
    output = output_of(run)
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    window = result_of(program_dir)["drain"]
    assert window["begin_pending"] > 0, window
    assert window["end_pending"] == 0, window

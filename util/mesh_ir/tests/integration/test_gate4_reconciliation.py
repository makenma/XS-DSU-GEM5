"""R19-01: the shared reconciliation entry runs for DONE and ERROR_DRAINED."""

import copy
import hashlib
import json

import pytest

from mesh_ir.runtime_reconciliation import ReconciliationError, reconcile

from tests.integration.support.runtime_harness import (
    admitted_descriptors,
    build_program,
    descriptor_executions,
    result_of,
    run_mock,
    schedule,
    semantic_command,
)

FILL_PROGRAM = "fill_offset"
FILL = "fill:input"
LOAD_PROGRAM = "single"
LOAD = "load:input"
P2P_PROGRAM = "p2p_multi_descriptor"
P2P_PUSH = "p2p:03:push"
REPEAT_PROGRAM = "repeat"
REPEAT_LOAD = "repeat:load"
CANCEL_PROGRAM = "dual"


def _artifact(program_dir):
    path = program_dir / "reconciliation.json"
    assert path.is_file(), f"missing reconciliation artifact at {path}"
    return json.loads(path.read_text())


def _descriptor_row(program_dir, descriptor_id):
    data = _artifact(program_dir)
    return next(
        row for row in data["descriptors"] if row["descriptor_id"] == descriptor_id
    )


def _python_reconcile(
    program_dir,
    cause,
    error_descriptors=(),
    fault_occurrence=0,
    instances=1,
    traffic_multiplier=1,
    result=None,
):
    sections = json.loads((program_dir / "schedule.mesh.json").read_text())
    rows = {
        row["descriptor_id"]: row["kind"]
        for row in sections["sections"]["DMA_DESCRIPTORS"]
    }
    traffic = json.loads((program_dir / "expected_traffic.json").read_text())
    expected_rows = [
        {**row, **row["identity"], "kind": rows[row["identity"]["descriptor_id"]]}
        for row in traffic["descriptors"]
    ]
    return reconcile(
        cause=cause,
        result=result if result is not None else result_of(program_dir),
        schedule=sections,
        expected_rows=expected_rows,
        error_descriptors=list(error_descriptors),
        fault_occurrence=fault_occurrence,
        instances=instances,
        traffic_multiplier=traffic_multiplier,
    )


def test_unreached_fault_occurrence_keeps_the_healthy_run(tmp_path):
    program_dir = build_program(tmp_path, FILL_PROGRAM)
    descriptor = admitted_descriptors(program_dir, semantic_command(program_dir, FILL))[0]
    run = run_mock(
        tmp_path, "m5out", FILL_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor), "--fault-occurrence", "2"),
    )
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    data = _artifact(program_dir)
    assert data["status"] == "ok" and data["outcome"] == "done", data
    row = _descriptor_row(program_dir, descriptor)
    assert row["faulted_executions"] == 0, row
    assert row["successful_executions"] == row["planned_executions"] == 1, row
    assert row["expected_success_bytes"] == row["actual_bytes"]["fill_bytes"] == 128, row


def test_occurrence_beyond_the_plan_keeps_the_healthy_run(tmp_path):
    program_dir = build_program(tmp_path, LOAD_PROGRAM)
    descriptor = admitted_descriptors(program_dir, semantic_command(program_dir, LOAD))[0]
    run = run_mock(
        tmp_path, "m5out", LOAD_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor), "--fault-occurrence", "5"),
    )
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    data = _artifact(program_dir)
    row = _descriptor_row(program_dir, descriptor)
    assert row["faulted_executions"] == 0, row
    assert row["expected_success_bytes"] == row["actual_bytes"]["read_bytes"] > 0, row
    assert data["outcome"] == "done", data


def test_first_generation_error_drains_and_leaves_reconciliation(tmp_path):
    program_dir = build_program(tmp_path, FILL_PROGRAM)
    descriptor = admitted_descriptors(program_dir, semantic_command(program_dir, FILL))[0]
    run = run_mock(
        tmp_path, "m5out", FILL_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    data = _artifact(program_dir)
    assert data["status"] == "ok" and data["outcome"] == "error_drained", data
    row = _descriptor_row(program_dir, descriptor)
    assert row["faulted_executions"] == 1 and row["successful_executions"] == 0, row
    assert row["expected_success_bytes"] == 0, row
    assert row["actual_bytes"]["fill_bytes"] == 0, row
    instance_id = result_of(program_dir)["instances"][0]["instance"]
    observed = descriptor_executions(result_of(program_dir), instance_id, 0, semantic_command(program_dir, FILL), 0)
    assert observed[0]["fault_target_before_digest"], observed[0]
    assert (
        observed[0]["fault_target_before_digest"]
        == observed[0]["fault_target_after_digest"]
    ), observed[0]
    assert observed[0]["landing_digest"] is None, observed[0]


def test_repeat_replay_error_reconciles_its_own_execution_domain(tmp_path):
    program_dir = build_program(tmp_path, REPEAT_PROGRAM)
    descriptor = admitted_descriptors(
        program_dir, semantic_command(program_dir, REPEAT_LOAD)
    )[0]
    run = run_mock(
        tmp_path, "m5out", REPEAT_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor), "--fault-occurrence", "2"),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    data = _artifact(program_dir)
    row = _descriptor_row(program_dir, descriptor)
    assert row["faulted_executions"] == 1, row
    assert row["planned_executions"] == 3, row
    assert row["successful_executions"] == 1, row
    assert row["expected_success_bytes"] == row["actual_bytes"]["read_bytes"], row


def test_second_instance_error_and_never_submitted_cancel_reconcile(tmp_path):
    program_dir = build_program(tmp_path, P2P_PROGRAM)
    descriptor = admitted_descriptors(program_dir, semantic_command(program_dir, P2P_PUSH))[0]
    run = run_mock(
        tmp_path, "m5out", P2P_PROGRAM, program_dir,
        ("--instances", "2", "--error-descriptors", str(descriptor),
         "--fault-occurrence", "2"),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    data = _artifact(program_dir)
    assert data["outcome"] == "error_drained", data
    assert _descriptor_row(program_dir, descriptor)["faulted_executions"] == 1

    cancel_dir = build_program(tmp_path, CANCEL_PROGRAM)
    run = run_mock(
        tmp_path, "m5out-cancel", CANCEL_PROGRAM, cancel_dir,
        ("--error-descriptors", "1,4"),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    data = _artifact(cancel_dir)
    assert data["outcome"] == "error_drained", data
    exempt = [row for row in data["descriptors"] if not row["attempted"]]
    assert exempt, data
    assert all(row["faulted_executions"] == 0 for row in exempt), exempt


def test_watchdog_run_is_not_reconciled_as_a_completion(tmp_path):
    program_dir = build_program(tmp_path, P2P_PROGRAM)
    push = semantic_command(program_dir, P2P_PUSH)
    descriptor = admitted_descriptors(program_dir, push)[-1]
    run = run_mock(
        tmp_path, "m5out", P2P_PROGRAM, program_dir,
        ("--drop-descriptors", str(descriptor), "--watchdog-ticks", "200000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert not (program_dir / "reconciliation.json").exists(), output


def test_reconciliation_rejects_tampered_fault_and_late_success(tmp_path):
    program_dir = build_program(tmp_path, FILL_PROGRAM)
    descriptor = admitted_descriptors(program_dir, semantic_command(program_dir, FILL))[0]
    run = run_mock(
        tmp_path, "m5out", FILL_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    cause = next(
        line.split(" because ")[-1]
        for line in (run.stdout + run.stderr).splitlines()
        if "Exiting" in line
    )
    _python_reconcile(program_dir, cause, error_descriptors=(descriptor,))

    tampered = result_of(program_dir)
    for row in tampered["transport"]:
        if row["descriptor_id"] == descriptor:
            row["fill_bytes"] = 128
            row["payload_digest"] = "deadbeef-0badcafe"
    with pytest.raises(ReconciliationError):
        _python_reconcile(
            program_dir, cause, error_descriptors=(descriptor,), result=tampered
        )

    p2p_dir = build_program(tmp_path, P2P_PROGRAM)
    push = semantic_command(p2p_dir, P2P_PUSH)
    admitted = admitted_descriptors(p2p_dir, push)
    middle = admitted[len(admitted) // 2]
    run = run_mock(
        tmp_path, "m5out-p2p", P2P_PROGRAM, p2p_dir,
        ("--error-descriptors", str(middle)),
    )
    cause = next(
        line.split(" because ")[-1]
        for line in (run.stdout + run.stderr).splitlines()
        if "Exiting" in line
    )
    _python_reconcile(p2p_dir, cause, error_descriptors=(middle,))
    dropped = result_of(p2p_dir)
    late = max(row["descriptor_id"] for row in dropped["transport"])
    assert late > middle, (late, middle)
    dropped["transport"] = [
        row for row in dropped["transport"] if row["descriptor_id"] != late
    ]
    with pytest.raises(ReconciliationError):
        _python_reconcile(
            p2p_dir, cause, error_descriptors=(middle,), result=dropped
        )


def _cause(run):
    return next(
        line.split(" because ")[-1]
        for line in (run.stdout + run.stderr).splitlines()
        if "Exiting" in line
    )


def _executions(result, instance_index=0, core_id="0"):
    return result["instances"][instance_index]["cores"][core_id]["observations"][
        "descriptor_executions"
    ]


def test_reconciliation_rejects_wrong_execution_identity(tmp_path):
    program_dir = build_program(tmp_path, LOAD_PROGRAM)
    run = run_mock(tmp_path, "m5out-load", LOAD_PROGRAM, program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, result=baseline)

    wrong = copy.deepcopy(baseline)
    rows = _executions(wrong)
    first = rows[0]
    other = next(
        (command, generation)
        for command, generation in wrong["cores"][0]["dispatch_plan"]
        if command != first["command_id"]
    )
    first["command_id"] = other[0]
    first["generation"] = other[1]
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=wrong)


def test_reconciliation_rejects_duplicate_terminal(tmp_path):
    program_dir = build_program(tmp_path, LOAD_PROGRAM)
    run = run_mock(tmp_path, "m5out-load", LOAD_PROGRAM, program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, result=baseline)

    duplicate = copy.deepcopy(baseline)
    ledger = duplicate["instances"][0]["cores"]["0"]["terminals"]
    ledger.append(copy.deepcopy(ledger[0]))
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=duplicate)


def test_reconciliation_rejects_a_completed_plan_record_that_vanished(tmp_path):
    program_dir = build_program(tmp_path, LOAD_PROGRAM)
    run = run_mock(tmp_path, "m5out-load", LOAD_PROGRAM, program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, result=baseline)

    sections = schedule(program_dir)
    target = next(
        row for row in _executions(baseline) if row["status"] == "OK"
    )
    descriptor_ids = [
        row["descriptor_id"]
        for row in sections["DMA_DESCRIPTORS"]
        if row["command_id"] == target["command_id"]
    ]
    missing = copy.deepcopy(baseline)
    core = missing["instances"][0]["cores"]["0"]
    core["terminals"] = [
        row
        for row in core["terminals"]
        if (row["command_id"], row["generation"])
        != (target["command_id"], target["generation"])
    ]
    core["observations"]["descriptor_executions"] = [
        row
        for row in core["observations"]["descriptor_executions"]
        if row["command_id"] != target["command_id"]
    ]
    missing["transport"] = [
        row for row in missing["transport"] if row["descriptor_id"] not in descriptor_ids
    ]
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=missing)


def test_reconciliation_rejects_invented_error_execution_facts(tmp_path):
    program_dir = build_program(tmp_path, FILL_PROGRAM)
    descriptor = admitted_descriptors(
        program_dir, semantic_command(program_dir, FILL)
    )[0]
    run = run_mock(
        tmp_path, "m5out-fill", FILL_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, error_descriptors=(descriptor,), result=baseline)

    wrong_occurrence = copy.deepcopy(baseline)
    with pytest.raises(ReconciliationError):
        _python_reconcile(
            program_dir,
            cause,
            error_descriptors=(descriptor,),
            fault_occurrence=99,
            result=wrong_occurrence,
        )

    invented = copy.deepcopy(baseline)
    _executions(invented)[0]["transfer"]["fill_bytes"] = 128
    with pytest.raises(ReconciliationError):
        _python_reconcile(
            program_dir, cause, error_descriptors=(descriptor,), result=invented
        )

    unfinished = copy.deepcopy(baseline)
    _executions(unfinished)[0]["completed"] = False
    with pytest.raises(ReconciliationError):
        _python_reconcile(
            program_dir, cause, error_descriptors=(descriptor,), result=unfinished
        )


def test_reconciliation_rejects_an_omitted_late_success(tmp_path):
    program_dir = build_program(tmp_path, P2P_PROGRAM)
    push = semantic_command(program_dir, P2P_PUSH)
    admitted = admitted_descriptors(program_dir, push)
    middle = admitted[len(admitted) // 2]
    run = run_mock(
        tmp_path, "m5out-p2p", P2P_PROGRAM, program_dir,
        ("--error-descriptors", str(middle)),
    )
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, error_descriptors=(middle,), result=baseline)

    late = max(
        row["descriptor_id"] for row in _executions(baseline) if row["committed"]
    )
    assert late > middle
    removed = copy.deepcopy(baseline)
    removed["instances"][0]["cores"]["0"]["observations"][
        "descriptor_executions"
    ] = [
        row
        for row in _executions(removed)
        if row["descriptor_id"] != late
    ]
    removed["transport"] = [
        row for row in removed["transport"] if row["descriptor_id"] != late
    ]
    with pytest.raises(ReconciliationError):
        _python_reconcile(
            program_dir, cause, error_descriptors=(middle,), result=removed
        )


def test_error_target_readback_matches_the_specified_initial_digest(tmp_path):
    program_dir = build_program(tmp_path, FILL_PROGRAM)
    fill = semantic_command(program_dir, FILL)
    descriptor = admitted_descriptors(program_dir, fill)[0]
    run = run_mock(
        tmp_path, "m5out-fill", FILL_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    assert "MESH_PROGRAM_ERROR_DRAINED" in run.stdout + run.stderr
    result = result_of(program_dir)
    geometry = next(
        row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["descriptor_id"] == descriptor
    )
    span = geometry["row_bytes"] * geometry["rows"]
    expected = hashlib.sha256(bytes(span)).hexdigest()
    rows = descriptor_executions(
        result, result["instances"][0]["instance"], geometry["owner_core"], fill, 0
    )
    assert len(rows) == 1, rows
    assert rows[0]["fault_target_before_digest"] == expected, rows[0]
    assert rows[0]["fault_target_after_digest"] == expected, rows[0]
    assert rows[0]["landing_digest"] is None, rows[0]


def test_reconciliation_rejects_missing_identity_and_lifecycle_facts(tmp_path):
    program_dir = build_program(tmp_path, LOAD_PROGRAM)
    run = run_mock(tmp_path, "m5out-load", LOAD_PROGRAM, program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, result=baseline)

    missing = copy.deepcopy(baseline)
    missing["instances"][0]["cores"].pop("0")
    missing["transport"] = []
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=missing)

    wrong_identity = copy.deepcopy(baseline)
    wrong_identity["instances"][0]["instance"] = 999
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=wrong_identity)

    for state in ("UNKNOWN", "errored", "cancelled"):
        tampered = copy.deepcopy(baseline)
        tampered["instances"][0]["cores"]["0"]["terminals"][0]["state"] = state
        with pytest.raises(ReconciliationError):
            _python_reconcile(program_dir, cause, result=tampered)

    unfinished = copy.deepcopy(baseline)
    _executions(unfinished)[0]["completion_tick"] = None
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=unfinished)

    duplicated = copy.deepcopy(baseline)
    duplicated["transport"].append(copy.deepcopy(duplicated["transport"][0]))
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=duplicated)


def test_fill_error_status_must_match_the_admitted_direction(tmp_path):
    program_dir = build_program(tmp_path, FILL_PROGRAM)
    descriptor = admitted_descriptors(
        program_dir, semantic_command(program_dir, FILL)
    )[0]
    run = run_mock(
        tmp_path, "m5out-fill", FILL_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, error_descriptors=(descriptor,), result=baseline)

    for status in ("AXI_READ_ERROR", "LOST", "UNKNOWN"):
        tampered = copy.deepcopy(baseline)
        _executions(tampered)[0]["status"] = status
        with pytest.raises(ReconciliationError):
            _python_reconcile(
                program_dir, cause, error_descriptors=(descriptor,), result=tampered
            )


@pytest.mark.parametrize(
    "instances,fault_occurrence",
    ((2, 1), (3, 2), (3, 3)),
)
def test_multi_instance_error_in_each_position_reconciles(
    tmp_path, instances, fault_occurrence
):
    program_dir = build_program(tmp_path, FILL_PROGRAM)
    descriptor = admitted_descriptors(
        program_dir, semantic_command(program_dir, FILL)
    )[0]
    run = run_mock(
        tmp_path,
        f"m5out-{instances}-{fault_occurrence}",
        FILL_PROGRAM,
        program_dir,
        (
            "--instances",
            str(instances),
            "--error-descriptors",
            str(descriptor),
            "--fault-occurrence",
            str(fault_occurrence),
        ),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    assert run.returncode != 0, output
    data = _artifact(program_dir)
    assert data["outcome"] == "error_drained", data
    row = _descriptor_row(program_dir, descriptor)
    assert row["planned_executions"] == instances, row
    assert row["faulted_executions"] == 1, row
    assert row["successful_executions"] == instances - 1, row


def test_reconciliation_rejects_duplicate_dispatch_identity(tmp_path):
    program_dir = build_program(tmp_path, LOAD_PROGRAM)
    run = run_mock(tmp_path, "m5out-load", LOAD_PROGRAM, program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, result=baseline)

    repeated_key = copy.deepcopy(baseline)
    plan = repeated_key["cores"][0]["dispatch_plan"]
    plan.append(copy.deepcopy(plan[1]))
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=repeated_key)

    repeated_core = copy.deepcopy(baseline)
    repeated_core["cores"].append(copy.deepcopy(repeated_core["cores"][0]))
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=repeated_core)


def test_reconciliation_rejects_inverted_lifecycle_times(tmp_path):
    program_dir = build_program(tmp_path, LOAD_PROGRAM)
    run = run_mock(tmp_path, "m5out-load", LOAD_PROGRAM, program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    cause = _cause(run)
    baseline = result_of(program_dir)

    co_timed = copy.deepcopy(baseline)
    command = co_timed["instances"][0]["cores"]["0"]["observations"]["commands"][0]
    command["terminal_tick"] = command["issue_tick"]
    execution = _executions(co_timed)[0]
    execution["completion_tick"] = execution["submit_tick"]
    execution["commit_tick"] = execution["submit_tick"]
    _python_reconcile(program_dir, cause, result=co_timed)

    early_terminal = copy.deepcopy(baseline)
    command = early_terminal["instances"][0]["cores"]["0"]["observations"][
        "commands"
    ][0]
    command["terminal_tick"] = command["issue_tick"] - 1
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=early_terminal)

    early_completion = copy.deepcopy(baseline)
    execution = _executions(early_completion)[0]
    execution["completion_tick"] = execution["submit_tick"] - 1
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=early_completion)

    early_commit = copy.deepcopy(baseline)
    execution = _executions(early_commit)[0]
    execution["commit_tick"] = execution["submit_tick"] - 1
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=early_commit)

    early_submission = copy.deepcopy(baseline)
    owner = semantic_command(program_dir, LOAD)
    execution = _executions(early_submission)[0]
    command = next(
        row
        for row in early_submission["instances"][0]["cores"]["0"]["observations"][
            "commands"
        ]
        if row["command_id"] == owner
    )
    execution["submit_tick"] = command["issue_tick"] - 1
    execution["completion_tick"] = execution["submit_tick"]
    execution["commit_tick"] = execution["submit_tick"]
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=early_submission)


def test_reconciliation_rejects_executions_outliving_their_command(tmp_path):
    program_dir = build_program(tmp_path, LOAD_PROGRAM)
    run = run_mock(tmp_path, "m5out-load", LOAD_PROGRAM, program_dir)
    assert run.returncode == 0, run.stdout + run.stderr
    cause = _cause(run)
    baseline = result_of(program_dir)
    _python_reconcile(program_dir, cause, result=baseline)

    owner = semantic_command(program_dir, LOAD)
    late = copy.deepcopy(baseline)
    command = next(
        row
        for row in late["instances"][0]["cores"]["0"]["observations"]["commands"]
        if row["command_id"] == owner
    )
    execution = _executions(late)[0]
    execution["completion_tick"] = command["terminal_tick"] + 1
    execution["commit_tick"] = execution["completion_tick"]
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=late)

    late_commit = copy.deepcopy(baseline)
    command = next(
        row
        for row in late_commit["instances"][0]["cores"]["0"]["observations"][
            "commands"
        ]
        if row["command_id"] == owner
    )
    execution = _executions(late_commit)[0]
    execution["commit_tick"] = command["terminal_tick"] + 1
    with pytest.raises(ReconciliationError):
        _python_reconcile(program_dir, cause, result=late_commit)

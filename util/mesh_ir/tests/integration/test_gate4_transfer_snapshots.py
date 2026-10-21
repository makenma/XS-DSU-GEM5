"""Slice D fix: per-commit destination evidence for a staged P2P transfer."""

import copy
import json

import pytest

from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    admitted_transfer_destination,
    verified_transfer_snapshots,
)

from tests.integration.support.runtime_harness import (
    ARCH,
    admitted_descriptors,
    build_program,
    build_program_with_arch,
    descriptor_executions,
    instance_frame,
    result_of,
    run_mock,
    schedule,
    semantic_command,
    terminal_partition,
)

PROGRAM = "p2p_multi_descriptor"
PREFILLED_PROGRAM = "p2p_prefilled_destination"
CANCEL_PROGRAM = "p2p_cancel"
PUSH = "p2p:03:push"
RECV = "p2p:04:recv"
PREFILL = "p2p:02b:prefill"
CANCEL_PUSH = "p2p-cancel:03:push"
CANCEL_LOAD = "p2p-cancel:05:load"
TRANSFER = 1
CORE_SOURCE = 0
CORE_TARGET = 1


def _snapshots(result, instance_id, core_id=CORE_SOURCE):
    _, core = instance_frame(result, instance_id, core_id)
    assert "transfer_commits" in core["observations"], sorted(core["observations"])
    rows = [
        row
        for row in core["observations"]["transfer_commits"]
        if row["transfer_id"] == TRANSFER
    ]
    assert rows, core["observations"]["transfer_commits"]
    return rows


def _admitted_geometry(program_dir, command_id):
    rows = [
        row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["command_id"] == command_id
    ]
    assert rows, command_id
    return {row["descriptor_id"]: row for row in rows}


def _admitted_destination(program_dir, command_id):
    geometry = _admitted_geometry(program_dir, command_id)
    return admitted_transfer_destination(
        schedule(program_dir), sorted(geometry)
    )


def _fill_pattern(program_dir, command_id):
    sections = schedule(program_dir)
    command = next(
        row for row in sections["COMMANDS"] if row["command_id"] == command_id
    )
    value = sections["OP_ATTRS"][command["attr_index"] - 1]["pattern"]
    return int(value, 16) if isinstance(value, str) else value


def _pending_initial(program_dir, command_id, pattern):
    geometry = _admitted_geometry(program_dir, command_id)

    def initial(descriptor_id):
        row = geometry[descriptor_id]
        size = row["row_bytes"] * row["rows"]
        if pattern is None:
            return bytes(size)
        return bytes((pattern >> (8 * (index % 8))) & 0xFF for index in range(size))

    return initial


def _assert_receiver_and_pending(
    program_dir,
    command_id,
    rows,
    executions,
    *,
    command_state,
    pattern=None,
    resident=False,
    initial_known=True,
):
    """The shared per-commit destination contract, as the test states it.

    ``verified_transfer_snapshots`` owns the rules for both backends; this wrapper
    only supplies the admitted plan this test already read and turns a rejection
    into the assertion failure its negative cases expect."""
    target_core, admitted_allocations = _admitted_destination(program_dir, command_id)
    try:
        verified_transfer_snapshots(
            rows,
            admitted=admitted_descriptors(program_dir, command_id),
            geometry=_admitted_geometry(program_dir, command_id),
            executions={
                descriptor_id: sorted(
                    (row for row in executions
                     if row["descriptor_id"] == descriptor_id),
                    key=lambda row: row["generation"])
                for descriptor_id in {row["descriptor_id"] for row in executions}
            },
            target_core=target_core,
            admitted_allocations=admitted_allocations,
            completed=command_state == "completed",
            initial_bytes=(
                _pending_initial(program_dir, command_id, pattern)
                if initial_known else None
            ),
            resident=resident,
        )
    except ReconciliationError as error:
        raise AssertionError(str(error)) from error


def _command_state(result, instance_id, core_id, command_id, generation=0):
    _, core = instance_frame(result, instance_id, core_id)
    states = [
        row["state"]
        for row in core["terminals"]
        if row["command_id"] == command_id and row["generation"] == generation
    ]
    assert len(states) == 1, (command_id, generation, core["terminals"])
    return states[0]


def _assert_healthy_terminal_state(program_dir, result):
    manifest = json.loads((program_dir / "manifest.json").read_text())
    assert manifest["status"] == "ok", manifest
    for index in range(len(result["cores"])):
        terminal_partition(result, index)
    for core in result["cores"]:
        assert core["commands_errored"] == 0, core
        assert core["commands_cancelled"] == 0, core
        assert core["live_commands"] == 0 and core["allocation_pins"] == 0, core
        assert core["dma_idle"] == 1, core


def _run(tmp_path, program=PROGRAM, depth=None, extra=()):
    args = list(extra)
    if depth is None:
        program_dir = build_program(tmp_path, program)
    else:
        text = ARCH.read_text(encoding="utf-8")
        assert "descriptor_queue_depth: 16" in text
        arch = tmp_path / f"mesh_1x2_depth{depth}.yaml"
        arch.write_text(
            text.replace(
                "descriptor_queue_depth: 16", f"descriptor_queue_depth: {depth}"
            ),
            encoding="utf-8",
        )
        program_dir = build_program_with_arch(tmp_path, program, arch)
        args += ["--arch", str(arch)]
    run = run_mock(tmp_path, "m5out", program, program_dir, tuple(args))
    assert run.returncode == 0, run.stdout + run.stderr
    return program_dir, result_of(program_dir)


def test_p2p_intermediate_commit_snapshot_is_truthful(tmp_path):
    program_dir, result = _run(tmp_path)
    instance_id = result["instances"][0]["instance"]
    rows = _snapshots(result, instance_id)
    push = semantic_command(program_dir, PUSH)
    admitted = admitted_descriptors(program_dir, push)
    useful = {
        row["descriptor_id"]: row["useful_bytes"]
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["command_id"] == push
    }
    executions = descriptor_executions(result, instance_id, CORE_SOURCE, push, 0)
    ticks = [
        row["commit_tick"]
        for row in executions
        if row["committed"]
    ]
    assert [row["commit_tick"] for row in rows] == sorted(ticks)
    assert len(rows) == len(admitted)
    expected_bytes = sum(useful.values())
    spans = sorted(
        (row["logical_start"], row["logical_bytes"]) for row in rows
    )
    cursor = 0
    for start, length in spans:
        assert start == cursor, (start, cursor, spans)
        cursor += length
    assert cursor == expected_bytes, (cursor, expected_bytes)
    committed = 0
    for index, row in enumerate(rows):
        assert row["command_id"] == push and row["generation"] == 0, row
        assert row["descriptor_id"] in admitted, row
        assert row["source_digest"] == row["target_digest"], row
        assert row["target_initial_digest"] != row["target_digest"], row
        committed += useful[row["descriptor_id"]]
        assert row["committed_bytes"] == committed, row
        assert row["expected_bytes"] == expected_bytes, row
        assert row["logical_bytes"] == useful[row["descriptor_id"]], row
        if index + 1 < len(rows):
            assert row["sender_notifications"] == 0, row
            assert row["sender_published"] is False, row
        else:
            assert row["sender_notifications"] == 1, row
            assert row["sender_published"] is True, row
    _assert_receiver_and_pending(
        program_dir,
        push,
        rows,
        executions,
        command_state=_command_state(result, instance_id, CORE_SOURCE, push),
    )
    _assert_healthy_terminal_state(program_dir, result)


def test_p2p_depth_one_second_last_commit_is_not_resident(tmp_path):
    program_dir, result = _run(tmp_path, depth=1)
    instance_id = result["instances"][0]["instance"]
    push = semantic_command(program_dir, PUSH)
    admitted = admitted_descriptors(program_dir, push)
    rows = _snapshots(result, instance_id)
    executions = descriptor_executions(result, instance_id, CORE_SOURCE, push, 0)
    intermediate = [
        row
        for row in rows
        if 0 < row["committed_bytes"] < row["expected_bytes"]
    ]
    assert intermediate, rows
    for row in intermediate:
        assert row["sender_notifications"] == 0, row
        assert row["sender_published"] is False, row
    last = rows[-1]
    assert last["committed_bytes"] == last["expected_bytes"] > 0, last
    assert last["sender_notifications"] == 1 and last["sender_published"] is True, last
    _assert_receiver_and_pending(
        program_dir,
        push,
        rows,
        executions,
        command_state=_command_state(result, instance_id, CORE_SOURCE, push),
    )
    second_last = rows[-2]
    assert second_last["descriptor_id"] == admitted[-2], second_last
    assert second_last["pending_bytes"] == last["logical_bytes"] > 0, second_last


def test_p2p_lost_last_commit_keeps_evidence_before_the_gap(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    push = semantic_command(program_dir, PUSH)
    admitted = admitted_descriptors(program_dir, push)
    run = run_mock(
        tmp_path, "m5out", PROGRAM, program_dir,
        ("--drop-descriptors", str(admitted[-1]), "--watchdog-ticks", "200000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    frames = [
        json.loads(line.strip().split(" ", 1)[1])
        for line in output.splitlines()
        if line.strip().startswith("observation_frame_json ")
    ]
    assert frames, output[-2000:]
    frame = [row for row in frames if row["core_id"] == CORE_SOURCE][0]
    rows = [
        row
        for row in frame["transfer_commits"]
        if row["transfer_id"] == TRANSFER
    ]
    assert sorted(row["descriptor_id"] for row in rows) == admitted[:-1], rows
    committed = 0
    for row in rows:
        assert row["source_digest"] == row["target_digest"], row
        assert row["sender_notifications"] == 0 and row["sender_published"] is False, row
        committed = row["committed_bytes"]
    assert 0 < committed < rows[0]["expected_bytes"], rows[-1]
    _assert_receiver_and_pending(
        program_dir,
        push,
        rows,
        frame["descriptor_executions"],
        command_state="watchdog",
    )


def test_p2p_error_middle_keeps_later_commit_evidence(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    push = semantic_command(program_dir, PUSH)
    admitted = admitted_descriptors(program_dir, push)
    middle = admitted[len(admitted) // 2]
    run = run_mock(
        tmp_path, "m5out", PROGRAM, program_dir,
        ("--error-descriptors", str(middle)),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    rows = _snapshots(result, instance_id)
    executions = descriptor_executions(result, instance_id, CORE_SOURCE, push, 0)
    assert middle not in [row["descriptor_id"] for row in rows], rows
    assert rows[-1]["descriptor_id"] > middle, rows
    assert all(row["sender_notifications"] == 0 for row in rows), rows
    assert all(row["sender_published"] is False for row in rows), rows
    assert all(row["source_digest"] == row["target_digest"] for row in rows), rows
    assert rows[-1]["committed_bytes"] < rows[-1]["expected_bytes"], rows[-1]
    faulted = next(row for row in executions if row["descriptor_id"] == middle)
    assert faulted["committed"] is False, faulted
    assert faulted["status"] == "AXI_WRITE_ERROR", faulted
    assert faulted["landing_digest"] is None, faulted
    assert (
        faulted["fault_target_before_digest"] == faulted["fault_target_after_digest"]
    ), faulted
    _assert_receiver_and_pending(
        program_dir,
        push,
        rows,
        executions,
        command_state=_command_state(result, instance_id, CORE_SOURCE, push),
    )


def test_p2p_snapshots_reset_between_instances(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_mock(
        tmp_path, "m5out", PROGRAM, program_dir, ("--instances", "2")
    )
    assert run.returncode == 0, run.stdout + run.stderr
    result = result_of(program_dir)
    assert [row["instance"] for row in result["instances"]] == [1, 2]
    push = semantic_command(program_dir, PUSH)
    snapshots = {}
    for instance in result["instances"]:
        instance_id = instance["instance"]
        rows = _snapshots(result, instance_id)
        assert sorted(
            row["descriptor_id"] for row in rows
        ) == admitted_descriptors(program_dir, push), rows
        assert rows[0]["committed_bytes"] == rows[0]["logical_bytes"], rows[0]
        assert rows[-1]["committed_bytes"] == rows[-1]["expected_bytes"], rows[-1]
        assert rows[-1]["sender_notifications"] == 1, rows[-1]
        assert rows[-1]["sender_published"] is True, rows[-1]
        assert all(
            row["source_digest"] == row["target_digest"] for row in rows
        ), rows
        snapshots[instance_id] = rows
        _assert_receiver_and_pending(
            program_dir,
            push,
            rows,
            descriptor_executions(result, instance_id, CORE_SOURCE, push, 0),
            command_state=_command_state(result, instance_id, CORE_SOURCE, push),
            initial_known=instance_id == 1,
        )
    assert (
        snapshots[2][-1]["target_initial_digest"] == snapshots[1][-1]["target_digest"]
    ), snapshots[2][-1]
    assert (
        snapshots[2][0]["pending_digest"] != snapshots[1][0]["pending_digest"]
    ), snapshots[2][0]


def test_p2p_cancelled_group_keeps_per_commit_evidence(tmp_path):
    program_dir = build_program(tmp_path, CANCEL_PROGRAM)
    load = semantic_command(program_dir, CANCEL_LOAD)
    descriptor = admitted_descriptors(program_dir, load)[0]
    run = run_mock(
        tmp_path, "m5out", CANCEL_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    assert "MESH_PROGRAM_ERROR_DRAINED" in run.stdout + run.stderr
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    rows = _snapshots(result, instance_id)
    assert rows, rows
    push = semantic_command(program_dir, CANCEL_PUSH)
    assert [row["command_id"] for row in rows] == [push] * len(rows), rows
    assert all(row["sender_notifications"] == 0 for row in rows), rows
    assert all(row["sender_published"] is False for row in rows), rows
    assert all(row["source_digest"] == row["target_digest"] for row in rows), rows
    assert all(
        row["target_initial_digest"] != row["target_digest"] for row in rows
    ), rows
    last = rows[-1]
    assert last["committed_bytes"] == last["expected_bytes"], last
    _assert_receiver_and_pending(
        program_dir,
        push,
        rows,
        descriptor_executions(result, instance_id, CORE_SOURCE, push, 0),
        command_state=_command_state(result, instance_id, CORE_SOURCE, push),
    )


def test_p2p_prefilled_destination_reports_residency_not_completion(tmp_path):
    program_dir, result = _run(tmp_path, program=PREFILLED_PROGRAM)
    instance_id = result["instances"][0]["instance"]
    push = semantic_command(program_dir, PUSH)
    rows = _snapshots(result, instance_id)
    pattern = _fill_pattern(program_dir, semantic_command(program_dir, PREFILL))
    _assert_receiver_and_pending(
        program_dir,
        push,
        rows,
        descriptor_executions(result, instance_id, CORE_SOURCE, push, 0),
        command_state=_command_state(result, instance_id, CORE_SOURCE, push),
        pattern=pattern,
        resident=True,
    )
    mid_flight = [row for row in rows if not row["receiver_transfer_committed"]]
    assert mid_flight, rows
    target_core, admitted_allocations = _admitted_destination(program_dir, push)
    for row in mid_flight:
        assert row["receiver_core"] == target_core, row
        assert row["receiver_allocation_id"] == admitted_allocations[0], row
        assert row["receiver_allocation_valid"] is True, row
        assert row["receiver_notified_allocation_id"] == 0, row
        assert row["receiver_notifications"] == 0, row
        assert row["pending_bytes"] > 0, row
    assert rows[-1]["receiver_transfer_committed"] is True
    assert rows[-1]["receiver_notifications"] == 1
    for row in rows:
        assert row["source_digest"] == row["target_digest"], row
        assert row["target_initial_digest"] != row["target_digest"], row
    _assert_healthy_terminal_state(program_dir, result)


def test_p2p_prefilled_snapshots_reset_between_instances(tmp_path):
    program_dir, result = _run(
        tmp_path, program=PREFILLED_PROGRAM, extra=("--instances", "2")
    )
    assert [row["instance"] for row in result["instances"]] == [1, 2]
    push = semantic_command(program_dir, PUSH)
    pattern = _fill_pattern(program_dir, semantic_command(program_dir, PREFILL))
    for instance in result["instances"]:
        instance_id = instance["instance"]
        rows = _snapshots(result, instance_id)
        assert rows[-1]["receiver_transfer_committed"] is True, rows[-1]
        assert rows[-1]["sender_published"] is True, rows[-1]
        assert all(
            row["source_digest"] == row["target_digest"] for row in rows
        ), rows
        _assert_receiver_and_pending(
            program_dir,
            push,
            rows,
            descriptor_executions(result, instance_id, CORE_SOURCE, push, 0),
            command_state=_command_state(result, instance_id, CORE_SOURCE, push),
            pattern=pattern,
            resident=True,
        )
    _assert_healthy_terminal_state(program_dir, result)


@pytest.fixture(scope="module", params=(None, 1))
def healthy_carrier(request, tmp_path_factory):
    depth = request.param
    tmp_path = tmp_path_factory.mktemp(f"p2p_multi_descriptor_d{depth}")
    program_dir, result = _run(tmp_path, depth=depth)
    instance_id = result["instances"][0]["instance"]
    push = semantic_command(program_dir, PUSH)
    rows = _snapshots(result, instance_id)
    executions = descriptor_executions(result, instance_id, CORE_SOURCE, push, 0)
    state = _command_state(result, instance_id, CORE_SOURCE, push)
    return program_dir, push, rows, executions, state


def test_transfer_snapshot_assertion_accepts_the_healthy_frame(healthy_carrier):
    program_dir, push, rows, executions, state = healthy_carrier
    _assert_receiver_and_pending(
        program_dir, push, rows, executions, command_state=state
    )


def _corrupt(row, corruption):
    if corruption == "receiver-absent":
        row["receiver_present"] = False
    elif corruption == "wrong-receiver-core":
        row["receiver_core"] = 999
    elif corruption == "wrong-allocation":
        row["receiver_allocation_id"] = 999
    elif corruption == "admitted-set-cleared":
        row["receiver_admitted_allocations"] = []
    elif corruption == "missing-notification":
        row["receiver_notified_allocation_id"] = 0
        row["receiver_notifications"] = 0
        row["receiver_notification_tick"] = 0
    elif corruption == "duplicate-notification":
        row["receiver_notifications"] = 2
    elif corruption == "premature-valid":
        row["receiver_allocation_valid"] = True
    elif corruption == "premature-completion":
        row["receiver_transfer_committed"] = True
    elif corruption == "forged-pending":
        row["pending_bytes"] = 123456
        row["pending_digest"] = "corrupted-pending"
    else:
        raise AssertionError(corruption)


@pytest.mark.parametrize(
    "corruption",
    (
        "receiver-absent",
        "wrong-receiver-core",
        "wrong-allocation",
        "admitted-set-cleared",
        "missing-notification",
        "duplicate-notification",
        "premature-valid",
        "premature-completion",
        "forged-pending",
    ),
)
def test_transfer_snapshot_assertion_rejects_corrupted_frames(
    healthy_carrier, corruption
):
    program_dir, push, rows, executions, state = healthy_carrier
    corrupted = copy.deepcopy(rows)
    for row in corrupted:
        _corrupt(row, corruption)
    with pytest.raises(AssertionError):
        _assert_receiver_and_pending(
            program_dir,
            push,
            corrupted,
            copy.deepcopy(executions),
            command_state=state,
        )


def _coherent_receiver(row, complete):
    row.update(
        receiver_transfer_committed=complete,
        receiver_allocation_valid=complete,
        receiver_notifications=1 if complete else 0,
        receiver_notified_allocation_id=row["receiver_allocation_id"] if complete else 0,
        receiver_notification_tick=row["commit_tick"] if complete else 0,
    )


def test_receiver_assertion_rejects_coherent_early_completion(healthy_carrier):
    program_dir, push, rows, executions, state = healthy_carrier
    corrupted = copy.deepcopy(rows)
    for row in corrupted[:-1]:
        _coherent_receiver(row, True)
    with pytest.raises(AssertionError):
        _assert_receiver_and_pending(
            program_dir,
            push,
            corrupted,
            copy.deepcopy(executions),
            command_state=state,
        )


def test_receiver_assertion_rejects_a_coherent_missing_final_notification(
    healthy_carrier,
):
    program_dir, push, rows, executions, state = healthy_carrier
    corrupted = copy.deepcopy(rows)
    _coherent_receiver(corrupted[-1], False)
    with pytest.raises(AssertionError):
        _assert_receiver_and_pending(
            program_dir,
            push,
            corrupted,
            copy.deepcopy(executions),
            command_state=state,
        )


RECEIVER_DELIVERY_FAULTS = ("receive_duplicate", "receive_premature")


@pytest.mark.parametrize("fault", RECEIVER_DELIVERY_FAULTS)
def test_receiver_delivery_defect_is_rejected_by_the_shared_assertion(tmp_path, fault):
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_mock(
        tmp_path, f"m5out-{fault}", PROGRAM, program_dir, ("--receiver-fault", fault)
    )
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    push = semantic_command(program_dir, PUSH)
    rows = _snapshots(result, instance_id)
    state = _command_state(result, instance_id, CORE_SOURCE, push)
    assert state == "completed", state
    with pytest.raises(AssertionError):
        _assert_receiver_and_pending(
            program_dir,
            push,
            rows,
            descriptor_executions(result, instance_id, CORE_SOURCE, push, 0),
            command_state=state,
        )


RECEIVER_ROUTING_FAULTS = ("receive_drop", "receive_redirect")


def _watchdog_frames(output):
    frames = [
        json.loads(line.strip().split(" ", 1)[1])
        for line in output.splitlines()
        if line.strip().startswith("observation_frame_json ")
    ]
    assert frames, output[-2000:]
    return frames


@pytest.mark.parametrize("fault", RECEIVER_ROUTING_FAULTS)
def test_receiver_routing_defect_starves_the_intended_receiver(tmp_path, fault):
    program_dir = build_program(tmp_path, PROGRAM)
    push = semantic_command(program_dir, PUSH)
    recv = semantic_command(program_dir, RECV)
    run = run_mock(
        tmp_path,
        f"m5out-{fault}",
        PROGRAM,
        program_dir,
        ("--receiver-fault", fault, "--watchdog-ticks", "200000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    frames = _watchdog_frames(output)
    sender = [row for row in frames if row["core_id"] == CORE_SOURCE][0]
    receiver = [row for row in frames if row["core_id"] == CORE_TARGET][0]
    rows = [
        row for row in sender["transfer_commits"] if row["transfer_id"] == TRANSFER
    ]
    assert rows[-1]["sender_published"] is True, rows[-1]
    for row in rows:
        assert row["receiver_transfer_committed"] is False, row
        assert row["receiver_notifications"] == 0, row
        assert row["receiver_notification_tick"] == 0, row
        assert row["receiver_allocation_valid"] is False, row
    waiting = [
        row
        for row in receiver["commands"]
        if row["command_id"] == recv and row["generation"] == 0
    ]
    assert waiting and waiting[0]["issued"] and not waiting[0]["terminal"], waiting
    with pytest.raises(AssertionError):
        _assert_receiver_and_pending(
            program_dir,
            push,
            rows,
            sender["descriptor_executions"],
            command_state="completed",
        )

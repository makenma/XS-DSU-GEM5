"""Slice D: staged multi-descriptor P2P commit, error/lost handling and instance reset."""

import pytest

from tests.integration.support.runtime_harness import (
    ARCH,
    admitted_descriptors,
    build_program,
    build_program_with_arch,
    command_observation,
    descriptor_executions,
    instance_frame,
    result_of,
    run_mock,
    schedule,
    semantic_command,
)

PROGRAM = "p2p_multi_descriptor"
CANCEL_PROGRAM = "p2p_cancel"
FILL = "p2p:01:fill"
PUSH = "p2p:03:push"
RECV = "p2p:04:recv"
STORE = "p2p:05:store"
CANCEL_PUSH = "p2p-cancel:03:push"
CANCEL_LOAD = "p2p-cancel:05:load"
TRANSFER = 1
CORE_SOURCE = 0
CORE_TARGET = 1


def _arch_with_descriptor_depth(tmp_path, depth):
    text = ARCH.read_text(encoding="utf-8")
    assert "descriptor_queue_depth: 16" in text
    path = tmp_path / f"mesh_1x2_depth{depth}.yaml"
    path.write_text(
        text.replace("descriptor_queue_depth: 16", f"descriptor_queue_depth: {depth}"),
        encoding="utf-8",
    )
    return path


def _program(tmp_path, depth=None):
    if depth is None:
        return build_program(tmp_path, PROGRAM), None
    arch = _arch_with_descriptor_depth(tmp_path, depth)
    return build_program_with_arch(tmp_path, PROGRAM, arch), arch


def _run(tmp_path, program_dir, extra_args=()):
    run = run_mock(tmp_path, "m5out", PROGRAM, program_dir, extra_args)
    return run


def _successful(tmp_path, extra_args=(), depth=None):
    program_dir, arch = _program(tmp_path, depth)
    args = tuple(extra_args) + (("--arch", str(arch)) if arch is not None else ())
    run = _run(tmp_path, program_dir, args)
    assert run.returncode == 0, run.stdout + run.stderr
    return program_dir, result_of(program_dir), run


def _transfer(result, instance_id, core_id=0):
    _, core = instance_frame(result, instance_id, core_id)
    rows = [
        row
        for row in core["observations"]["transfers"]
        if row["transfer_id"] == TRANSFER
    ]
    assert len(rows) == 1, rows
    return rows[0]


def _push_rows(program_dir, result, instance_id, stable_key=PUSH):
    command = semantic_command(program_dir, stable_key)
    rows = descriptor_executions(result, instance_id, CORE_SOURCE, command, 0)
    return command, rows


def _commits(program_dir, result, instance_id):
    _, core = instance_frame(result, instance_id, CORE_SOURCE)
    assert "transfer_commits" in core["observations"], sorted(core["observations"])
    return core["observations"]["transfer_commits"]


def _physical_commits(program_dir, result, instance_id, stable_key=PUSH):
    push, rows = _push_rows(program_dir, result, instance_id, stable_key)
    useful = {
        row["descriptor_id"]: row["useful_bytes"]
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["command_id"] == push
    }
    committed = [row for row in rows if row["committed"]]
    return (
        sorted(row["descriptor_id"] for row in committed),
        sum(useful[row["descriptor_id"]] for row in committed),
    )


def test_p2p_partial_commit_is_not_transfer_completion(tmp_path):
    program_dir, result, _ = _successful(tmp_path, depth=1)
    instance_id = result["instances"][0]["instance"]
    push, rows = _push_rows(program_dir, result, instance_id)
    admitted = admitted_descriptors(program_dir, push)
    assert len(admitted) == 40, admitted
    assert sorted(row["descriptor_id"] for row in rows) == admitted
    commits = [row["commit_tick"] for row in rows]
    assert all(row["committed"] and row["status"] == "OK" for row in rows)
    assert len(set(commits)) == len(commits), commits
    assert commits == sorted(commits), commits

    transfer = _transfer(result, instance_id)
    assert transfer["sender_published"] is True and transfer["failed"] is False
    assert transfer["expected_descriptors"] == 40
    assert transfer["committed_descriptors"] == 40
    assert transfer["expected_bytes"] == transfer["committed_bytes"] > 0

    store = command_observation(
        result, instance_id, CORE_TARGET, semantic_command(program_dir, STORE), 0
    )
    recv = command_observation(
        result, instance_id, CORE_TARGET, semantic_command(program_dir, RECV), 0
    )
    assert recv["terminal"] and store["terminal"]
    assert min(commits) < store["issue_tick"], (min(commits), store["issue_tick"])
    assert max(commits) <= store["issue_tick"], (max(commits), store["issue_tick"])
    assert recv["terminal_tick"] >= max(commits), (recv["terminal_tick"], max(commits))


def test_p2p_final_commit_releases_once(tmp_path):
    program_dir, result, _ = _successful(tmp_path)
    instance_id = result["instances"][0]["instance"]
    push, rows = _push_rows(program_dir, result, instance_id)
    assert len(admitted_descriptors(program_dir, push)) == 40
    assert all(row["committed"] for row in rows)
    transfer = _transfer(result, instance_id)
    assert transfer == {
        "transfer_id": TRANSFER,
        "expected_descriptors": 40,
        "committed_descriptors": 40,
        "expected_bytes": transfer["committed_bytes"],
        "committed_bytes": transfer["committed_bytes"],
        "failed": False,
        "sender_published": True,
        "sender_notifications": 1,
    }
    frame, core = instance_frame(result, instance_id, CORE_TARGET)
    recv = semantic_command(program_dir, RECV)
    store = semantic_command(program_dir, STORE)
    terminals = [row for row in core["terminals"] if row["command_id"] in (recv, store)]
    assert [row["command_id"] for row in terminals].count(recv) == 1, terminals
    assert [row["command_id"] for row in terminals].count(store) == 1, terminals
    assert frame["finalized"] is True
    assert core["resources"]["live_commands"] == 0
    assert core["resources"]["allocation_pins"] == 0


def test_p2p_lost_final_descriptor_reports_wait_chain(tmp_path):
    program_dir, arch = _program(tmp_path, depth=1)
    push = semantic_command(program_dir, PUSH)
    admitted = admitted_descriptors(program_dir, push)
    last = admitted[-1]
    run = _run(
        tmp_path,
        program_dir,
        ("--arch", str(arch), "--drop-descriptors", str(last), "--watchdog-ticks", "200000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert "MESH_PROGRAM_DONE" not in output, output
    lines = [row for row in output.splitlines() if "transfer=" in row]
    assert lines, output
    line = lines[0]
    assert f"transfer={TRANSFER}" in line, line
    assert "expected_descriptors=40" in line, line
    committed = int(line.split("committed_descriptors=")[1].split()[0])
    assert 0 < committed < 40, line
    assert f"pending=[{last}]" in line, line
    assert "observation_frame" in output, output


def test_p2p_error_drains_without_success(tmp_path):
    program_dir, _ = _program(tmp_path)
    push = semantic_command(program_dir, PUSH)
    admitted = admitted_descriptors(program_dir, push)
    middle = admitted[len(admitted) // 2]
    run = _run(tmp_path, program_dir, ("--error-descriptors", str(middle)))
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    transfer = _transfer(result, instance_id)
    assert transfer["sender_published"] is False
    assert transfer["failed"] is True
    _, rows = _push_rows(program_dir, result, instance_id)
    errored = [row for row in rows if row["descriptor_id"] == middle]
    assert len(errored) == 1 and errored[0]["status"] == "AXI_WRITE_ERROR"
    assert errored[0]["completed"] is True and errored[0]["committed"] is False
    assert errored[0]["commit_tick"] is None, errored[0]
    assert errored[0]["transfer"]["p2p_bytes"] == 0, errored[0]
    assert not errored[0]["transfer"]["payload_digest"], errored[0]
    assert middle not in [
        row["descriptor_id"] for row in _commits(program_dir, result, instance_id)
    ]
    core = result["cores"][CORE_SOURCE]
    assert core["dma_idle"] == 1 and core["live_commands"] == 0
    assert core["allocation_pins"] == 0
    failed = _transfer(result, instance_id)
    assert failed["committed_descriptors"] < failed["expected_descriptors"]
    physical, physical_bytes = _physical_commits(program_dir, result, instance_id)
    assert physical, physical
    assert middle not in physical, physical
    assert failed["committed_descriptors"] == len(physical), (failed, physical)
    assert failed["committed_bytes"] == physical_bytes, (failed, physical_bytes)


def test_p2p_error_on_first_descriptor_counts_later_writes(tmp_path):
    program_dir, _ = _program(tmp_path)
    push = semantic_command(program_dir, PUSH)
    admitted = admitted_descriptors(program_dir, push)
    first = admitted[0]
    run = _run(tmp_path, program_dir, ("--error-descriptors", str(first)))
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    _, rows = _push_rows(program_dir, result, instance_id)
    errored = [row for row in rows if row["descriptor_id"] == first]
    assert [row["status"] for row in errored] == ["AXI_WRITE_ERROR"]
    assert errored[0]["transfer"]["p2p_bytes"] == 0, errored[0]
    assert not errored[0]["transfer"]["payload_digest"], errored[0]
    assert first not in [
        row["descriptor_id"] for row in _commits(program_dir, result, instance_id)
    ]
    transfer = _transfer(result, instance_id)
    assert transfer["sender_published"] is False and transfer["failed"] is True
    physical, physical_bytes = _physical_commits(program_dir, result, instance_id)
    assert first not in physical and physical, physical
    assert transfer["expected_descriptors"] == len(admitted)
    assert transfer["expected_bytes"] > physical_bytes > 0, transfer
    assert transfer["committed_descriptors"] == len(physical), (transfer, physical)
    assert transfer["committed_bytes"] == physical_bytes, (transfer, physical_bytes)
    core = result["cores"][CORE_SOURCE]
    assert core["dma_idle"] == 1 and core["live_commands"] == 0
    assert core["allocation_pins"] == 0


def test_p2p_cancelled_group_never_publishes_late_success(tmp_path):
    program_dir = build_program(tmp_path, CANCEL_PROGRAM)
    load = semantic_command(program_dir, CANCEL_LOAD)
    admitted_load = admitted_descriptors(program_dir, load)
    assert len(admitted_load) == 1, admitted_load
    run = run_mock(
        tmp_path, "m5out", CANCEL_PROGRAM, program_dir,
        ("--error-descriptors", str(admitted_load[0])),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    load_rows = descriptor_executions(result, instance_id, 1, load, 0)
    failed = [row for row in load_rows if row["descriptor_id"] == admitted_load[0]]
    assert len(failed) == 1 and failed[0]["status"] == "AXI_READ_ERROR", load_rows
    assert failed[0]["completed"] is True

    push = semantic_command(program_dir, CANCEL_PUSH)
    admitted = admitted_descriptors(program_dir, push)
    push_rows = descriptor_executions(result, instance_id, CORE_SOURCE, push, 0)
    assert sorted(row["descriptor_id"] for row in push_rows) == admitted
    assert all(row["scheduled"] and row["submit_tick"] > 0 for row in push_rows)
    submit = max(row["submit_tick"] for row in push_rows)
    commit = max(row["commit_tick"] for row in push_rows)
    assert submit < failed[0]["completion_tick"] < commit, (
        submit,
        failed[0]["completion_tick"],
        commit,
    )
    assert all(row["committed"] and row["status"] == "OK" for row in push_rows)

    transfer = _transfer(result, instance_id)
    assert transfer["expected_descriptors"] == len(admitted)
    assert transfer["expected_bytes"] == 4096
    assert transfer["failed"] is True
    assert transfer["sender_published"] is False
    assert transfer["sender_notifications"] == 0
    physical, physical_bytes = _physical_commits(
        program_dir, result, instance_id, CANCEL_PUSH
    )
    assert physical == admitted, (physical, admitted)
    assert transfer["committed_descriptors"] == len(physical)
    assert transfer["committed_bytes"] == physical_bytes == transfer["expected_bytes"]

    for core in result["cores"]:
        assert core["live_commands"] == 0 and core["allocation_pins"] == 0
        assert core["dma_idle"] == 1
        assert core["commands_errored"] + core["commands_cancelled"] > 0


def test_p2p_transfer_state_resets_between_instances(tmp_path):
    program_dir, result, _ = _successful(tmp_path, ("--instances", "2"))
    assert len(result["instances"]) == 2
    bytes_per_instance = []
    for instance in result["instances"]:
        instance_id = instance["instance"]
        transfer = _transfer(result, instance_id)
        assert transfer["sender_published"] is True and transfer["failed"] is False
        assert transfer["committed_descriptors"] == transfer["expected_descriptors"] == 40
        assert transfer["committed_bytes"] == transfer["expected_bytes"] > 0
        bytes_per_instance.append(transfer["committed_bytes"])
        _, rows = _push_rows(program_dir, result, instance_id)
        assert all(row["committed"] and row["status"] == "OK" for row in rows)
    assert bytes_per_instance[0] == bytes_per_instance[1]
    recv = semantic_command(program_dir, RECV)
    for instance in result["instances"]:
        _, core = instance_frame(result, instance["instance"], CORE_TARGET)
        assert [row["command_id"] for row in core["terminals"]].count(recv) == 1
        assert core["resources"]["live_commands"] == 0

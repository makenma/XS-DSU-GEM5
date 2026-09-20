"""Slice E fix: one diagnostic root per failed instance, typed status and valid JSONL."""

import json

from tests.integration.support.runtime_harness import (
    ARCH,
    admitted_descriptors,
    build_program,
    descriptor_executions,
    instance_frame,
    result_of,
    run_mock,
    semantic_command,
)

DUAL = "dual"
DUAL_DESCRIPTORS = "1,4"
REDUCE = "engine_reduce_pressure"
REDUCE_DESCRIPTORS = "3,8"
P2P = "p2p_multi_descriptor"
P2P_PUSH = "p2p:03:push"
P2P_STORE = "p2p:05:store"
CANCEL = "p2p_cancel"
CANCEL_LOAD = "p2p-cancel:05:load"
REQUIRED = {"code", "severity", "stage", "message", "context"}


def _records(program_dir):
    path = program_dir / "runtime_diagnostics.jsonl"
    assert path.is_file(), f"missing runtime diagnostic outlet at {path}"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert rows, "runtime diagnostic outlet is empty"
    for row in rows:
        assert REQUIRED <= set(row), row
    return rows


def _runtime_records(program_dir):
    rows = [row for row in _records(program_dir) if row["stage"] == "runtime"]
    assert rows, "no runtime root record"
    return rows


def _failed_descriptor(program_dir, result, entries):
    earliest = None
    for core_id, command, descriptor in entries:
        for row in descriptor_executions(result, result["instances"][0]["instance"], core_id, command, 0):
            if row["descriptor_id"] != descriptor:
                continue
            assert row["status"] != "OK", row
            if earliest is None or row["completion_tick"] < earliest[0]:
                earliest = (row["completion_tick"], core_id, command, descriptor)
    assert earliest is not None, entries
    return earliest


def test_dual_reports_one_root_for_the_failing_instance(tmp_path):
    program_dir = build_program(tmp_path, DUAL)
    run = run_mock(
        tmp_path, "m5out", DUAL, program_dir,
        ("--error-descriptors", DUAL_DESCRIPTORS),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    rows = _runtime_records(program_dir)
    assert len(rows) == 1, rows
    record = rows[0]
    assert int(record["context"]["instance"]) == instance_id, record
    earliest = _failed_descriptor(
        program_dir, result, [(1, 8, 4), (0, 2, 1)]
    )
    assert int(record["context"]["descriptor_id"]) == earliest[3], (record, earliest)
    assert int(record["context"]["core_id"]) == earliest[1], (record, earliest)
    assert int(record["context"]["command_id"]) == earliest[2], (record, earliest)


def test_engine_reduce_reports_one_root_for_the_failing_instance(tmp_path):
    program_dir = build_program(tmp_path, REDUCE)
    run = run_mock(
        tmp_path, "m5out", REDUCE, program_dir,
        ("--error-descriptors", REDUCE_DESCRIPTORS),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    rows = _runtime_records(program_dir)
    assert len(rows) == 1, rows
    record = rows[0]
    assert int(record["context"]["instance"]) == instance_id, record
    earliest = _failed_descriptor(
        program_dir, result, [(1, 17, 8), (0, 4, 3)]
    )
    assert int(record["context"]["descriptor_id"]) == earliest[3], (record, earliest)
    assert int(record["context"]["core_id"]) == earliest[1], (record, earliest)


def test_second_instance_error_reports_the_second_instance(tmp_path):
    program_dir = build_program(tmp_path, P2P)
    command = semantic_command(program_dir, P2P_PUSH)
    descriptor = admitted_descriptors(program_dir, command)[0]
    run = run_mock(
        tmp_path, "m5out", P2P, program_dir,
        (
            "--instances", "2",
            "--error-descriptors", str(descriptor),
            "--fault-occurrence", "2",
        ),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    assert [row["instance"] for row in result["instances"]] == [1, 2], result["instances"]
    first = result["instances"][0]
    _, core = instance_frame(result, 1, 0)
    assert core["resources"]["live_commands"] == 0
    assert core["terminals"], core
    assert all(row["state"] == "completed" for row in core["terminals"]), core
    rows = _runtime_records(program_dir)
    assert len(rows) == 1, rows
    assert int(rows[0]["context"]["instance"]) == 2, rows[0]
    assert int(rows[0]["context"]["descriptor_id"]) == descriptor, rows[0]


def test_carriage_return_program_path_records_valid_jsonl(tmp_path):
    base = tmp_path / "cr\rbuild"
    base.mkdir()
    program_dir = build_program(base, DUAL)
    image = program_dir / "program.mshb"
    raw = bytearray(image.read_bytes())
    image.write_bytes(b"NOTAMSHB" + bytes(raw[8:]))
    run = run_mock(base, "m5out", DUAL, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    load = [row for row in rows if row["stage"] == "load"]
    assert len(load) == 1, rows
    assert load[0]["code"] == "E_ABI_MAGIC", load[0]
    assert load[0]["context"]["program_file"] == str(image), load[0]


def test_load_and_write_faults_report_their_own_status(tmp_path):
    cancel_dir = build_program(tmp_path, CANCEL)
    load = semantic_command(cancel_dir, CANCEL_LOAD)
    load_descriptor = admitted_descriptors(cancel_dir, load)[0]
    run = run_mock(
        tmp_path, "m5out", CANCEL, cancel_dir,
        ("--error-descriptors", str(load_descriptor)),
    )
    assert "MESH_PROGRAM_ERROR_DRAINED" in run.stdout + run.stderr
    result = result_of(cancel_dir)
    load_rows = descriptor_executions(result, 1, 1, load, 0)
    assert [row["status"] for row in load_rows] == ["AXI_READ_ERROR"], load_rows
    assert _runtime_records(cancel_dir)[0]["context"]["status"] == "AXI_READ_ERROR"

    push_dir = build_program(tmp_path, P2P)
    push = semantic_command(push_dir, P2P_PUSH)
    push_descriptor = admitted_descriptors(push_dir, push)[0]
    run = run_mock(
        tmp_path, "m5out", P2P, push_dir,
        ("--error-descriptors", str(push_descriptor)),
    )
    assert "MESH_PROGRAM_ERROR_DRAINED" in run.stdout + run.stderr
    result = result_of(push_dir)
    push_rows = descriptor_executions(result, 1, 0, push, 0)
    assert [row["status"] for row in push_rows if row["descriptor_id"] == push_descriptor] == [
        "AXI_WRITE_ERROR"
    ], push_rows
    assert _runtime_records(push_dir)[0]["context"]["status"] == "AXI_WRITE_ERROR"

    store = semantic_command(push_dir, P2P_STORE)
    store_descriptor = admitted_descriptors(push_dir, store)[0]
    run = run_mock(
        tmp_path, "m5out.store", P2P, push_dir,
        ("--error-descriptors", str(store_descriptor)),
    )
    assert "MESH_PROGRAM_ERROR_DRAINED" in run.stdout + run.stderr
    result = result_of(push_dir)
    store_rows = descriptor_executions(result, 1, 1, store, 0)
    assert [
        row["status"]
        for row in store_rows
        if row["descriptor_id"] == store_descriptor
    ] == ["AXI_WRITE_ERROR"], store_rows
    assert all(row["status"] == "OK" for row in store_rows if row["descriptor_id"] != store_descriptor)
    assert _runtime_records(push_dir)[0]["context"]["status"] == "AXI_WRITE_ERROR"


def _publish_two_variant_program(base):
    import inspect

    import mesh_ir.golden_programs as golden
    from mesh_ir.architecture import load_arch
    from mesh_ir.publication import publish_authored_program

    source = inspect.getsource(golden._build_single_core_program)
    source = source.replace(
        "def _build_single_core_program(", "def two_variant_program(", 1
    )
    old_tail = """    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()"""
    new_tail = """    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    second = builder.variant("main", "b2_m64n64k64", f"{name}:b2_m64n64k64", records=records, allocations=allocations, external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(3, ExternalSlotBacking(2)), ObjectBacking(6, ExternalSlotBacking(3))), binding_slots=slots)
    second_stream = second.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    second_stream.control_command(RequestBeginAttrs())
    for op_id in tuple(item.op_id for item in effects):
        second_stream.kernel_command(op_id)
    second_stream.control_command(RequestEndAttrs())
    second_stream.control_command(HaltAttrs())
    return builder.build()"""
    assert source.count(old_tail) == 1
    source = source.replace(old_tail, new_tail)
    scope = dict(vars(golden))
    exec(compile(source, "two_variant_program", "exec"), scope)
    arch = load_arch(ARCH)
    program = scope["two_variant_program"](arch, "two_variant")
    directory = base / "two_variant"
    publish_authored_program(directory, program, arch, kind="review")
    return directory


def test_multiple_variant_selection_reaches_the_loader_outlet(tmp_path):
    program_dir = _publish_two_variant_program(tmp_path)
    run = run_mock(tmp_path, "m5out", "single", program_dir)
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    invocation = [row for row in rows if row["stage"] == "invocation"]
    assert len(invocation) == 1, rows
    record = invocation[0]
    assert record["code"] == "E_RELOCATION", record
    assert record["context"]["matching_variants"] == "2", record
    assert record["context"]["install_state"] == "pre_install", record
    assert record["context"]["installed_cores"] == "0", record


def test_invalid_profile_selection_reaches_the_loader_outlet(tmp_path):
    program_dir = build_program(tmp_path, "single")
    run = run_mock(
        tmp_path, "m5out", "single", program_dir,
        ("--profile-id", "65535"),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    invocation = [row for row in rows if row["stage"] == "invocation"]
    assert len(invocation) == 1, rows
    record = invocation[0]
    assert record["code"] == "E_RELOCATION", record
    assert record["context"]["profile_id"] == "65535", record
    assert record["context"]["matching_variants"] == "0", record
    assert record["context"]["install_state"] == "pre_install", record
    assert record["context"]["installed_cores"] == "0", record


def _fatal_lines(output):
    return [line for line in output.splitlines() if "fatal" in line]


def _sink_flush_failure(program_dir):
    path = program_dir / "runtime_diagnostics.jsonl"
    if path.is_symlink() or path.exists():
        path.unlink()
    path.symlink_to("/dev/full")


def _sink_open_failure(program_dir):
    path = program_dir / "runtime_diagnostics.jsonl"
    if path.is_symlink() or path.exists():
        path.unlink()
    path.mkdir()


def test_runtime_flush_failure_keeps_the_business_code(tmp_path):
    program_dir = build_program(tmp_path, CANCEL)
    load = semantic_command(program_dir, CANCEL_LOAD)
    descriptor = admitted_descriptors(program_dir, load)[0]
    _sink_flush_failure(program_dir)
    run = run_mock(
        tmp_path, "m5out", CANCEL, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    fatal = _fatal_lines(output)
    assert any(
        "E_AXI_RESPONSE" in line and "diagnostic sink failure" in line
        for line in fatal
    ), fatal
    assert any("cannot flush" in line for line in fatal), fatal
    assert any("descriptor " + str(descriptor) in line for line in fatal), fatal


def test_watchdog_flush_failure_keeps_the_business_code(tmp_path):
    program_dir = build_program(tmp_path, P2P)
    push = semantic_command(program_dir, P2P_PUSH)
    descriptor = admitted_descriptors(program_dir, push)[-1]
    _sink_flush_failure(program_dir)
    run = run_mock(
        tmp_path, "m5out", P2P, program_dir,
        ("--drop-descriptors", str(descriptor), "--watchdog-ticks", "200000"),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert "diagnostic sink failure" in output and "cannot flush" in output, output


def test_loader_flush_failure_keeps_the_business_code(tmp_path):
    program_dir = build_program(tmp_path, P2P)
    image = program_dir / "program.mshb"
    raw = bytearray(image.read_bytes())
    image.write_bytes(b"NOTAMSHB" + bytes(raw[8:]))
    _sink_flush_failure(program_dir)
    run = run_mock(tmp_path, "m5out", P2P, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    fatal = _fatal_lines(output)
    assert any(
        "E_ABI_MAGIC" in line and "diagnostic sink failure" in line
        for line in fatal
    ), fatal
    assert any("cannot flush" in line for line in fatal), fatal


def test_runtime_sink_open_failure_is_not_a_flush_regression(tmp_path):
    program_dir = build_program(tmp_path, CANCEL)
    load = semantic_command(program_dir, CANCEL_LOAD)
    descriptor = admitted_descriptors(program_dir, load)[0]
    _sink_open_failure(program_dir)
    run = run_mock(
        tmp_path, "m5out", CANCEL, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    fatal = _fatal_lines(output)
    assert any(
        "E_AXI_RESPONSE" in line and "cannot open" in line for line in fatal
    ), fatal
    assert not any("cannot flush" in line for line in fatal), fatal

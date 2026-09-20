"""Slice E: the runtime diagnostic outlet must classify load, runtime and watchdog failures."""

import json
import shutil

import pytest

from tests.integration.support.runtime_harness import (
    ARCH,
    admitted_descriptors,
    build_program,
    result_of,
    run_mock,
    semantic_command,
)

PROGRAM = "p2p_multi_descriptor"
PUSH = "p2p:03:push"
STORE = "p2p:05:store"
REQUIRED = {"code", "severity", "stage", "message", "context"}


def _python_decode_verdict(data):
    from mesh_ir.abi.decoder import decode_program
    from mesh_ir.model import MeshIrError

    try:
        decode_program(bytes(data))
    except MeshIrError as error:
        return error.code
    return "ACCEPTED"


def _records(program_dir):
    path = program_dir / "runtime_diagnostics.jsonl"
    assert path.is_file(), f"missing runtime diagnostic outlet at {path}"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert rows, "runtime diagnostic outlet is empty"
    for row in rows:
        assert REQUIRED <= set(row), row
        assert row["stage"] in ("load", "invocation", "runtime", "watchdog"), row
    return rows


def test_architecture_digest_mismatch_records_load_stage(tmp_path):
    arch_text = ARCH.read_text(encoding="utf-8")
    arch = tmp_path / "mesh_1x2_shifted.yaml"
    arch.write_text(arch_text.replace("clock_hz: 2000000000", "clock_hz: 1000000000"), encoding="utf-8")
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_mock(tmp_path, "m5out", PROGRAM, program_dir, ("--arch", str(arch)))
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    load = [row for row in rows if row["stage"] == "load"]
    assert len(load) == 1, rows
    assert load[0]["code"] == "E_ARCH_DIGEST", load[0]


def test_invocation_rejection_records_invocation_stage(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    bindings = [
        {
            "slot_id": 9999,
            "region_id": 0,
            "owner_core": 0,
            "allocation_offset_bytes": 0,
            "allocation_size_bytes": 4096,
            "allocation_alignment_bytes": 64,
            "access": 2,
        },
    ]
    path = tmp_path / "bad.bindings.json"
    path.write_text(json.dumps(bindings), encoding="utf-8")
    run = run_mock(
        tmp_path, "m5out", PROGRAM, program_dir,
        ("--bindings-file", str(path)),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    invocation = [row for row in rows if row["stage"] == "invocation"]
    assert len(invocation) == 1, rows
    assert invocation[0]["code"] == "E_RELOCATION", invocation[0]


def test_store_error_records_direction(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    store = semantic_command(program_dir, STORE)
    descriptor = admitted_descriptors(program_dir, store)[0]
    run = run_mock(
        tmp_path, "m5out", PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    rows = _records(program_dir)
    runtime = [row for row in rows if row["stage"] == "runtime"]
    assert len(runtime) == 1, rows
    record = runtime[0]
    assert record["code"] == "E_AXI_RESPONSE", record
    assert record["context"]["dma_kind"] == "2", record
    assert int(record["context"]["descriptor_id"]) == descriptor, record


def test_envelope_bad_magic_records_load_stage(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    image = program_dir / "program.mshb"
    raw = bytearray(image.read_bytes())
    assert len(raw) > 32
    bad = b"NOTAMSHB" + bytes(raw[8:])
    assert _python_decode_verdict(bad) == "E_ABI_MAGIC"
    image.write_bytes(bad)
    run = run_mock(tmp_path, "m5out", PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    load = [row for row in rows if row["stage"] == "load"]
    assert len(load) == 1, rows
    assert load[0]["code"] == _python_decode_verdict(bad), load[0]
    assert not (program_dir / "actual_result.json").exists() or not result_of(program_dir)["cores"][0]["commands_issued"]


def test_p2p_error_records_direction(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    push = semantic_command(program_dir, PUSH)
    descriptor = admitted_descriptors(program_dir, push)[0]
    run = run_mock(
        tmp_path, "m5out", PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    rows = _records(program_dir)
    runtime = [row for row in rows if row["stage"] == "runtime"]
    assert len(runtime) == 1, rows
    record = runtime[0]
    assert record["code"] == "E_AXI_RESPONSE", record
    assert record["context"]["dma_kind"] == "3", record
    assert int(record["context"]["descriptor_id"]) == descriptor, record
    assert int(record["context"]["command_id"]) == push, record


def test_healthy_run_records_no_diagnostics(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_mock(tmp_path, "m5out", PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    path = program_dir / "runtime_diagnostics.jsonl"
    assert not path.exists() or not path.read_text().strip(), path.read_text()
    result = result_of(program_dir)
    cores = result["cores"]
    assert cores, result
    assert all(core["commands_issued"] > 0 for core in cores), cores
    assert all(
        core["errored_command_ids"] == [] and core["cancelled_command_ids"] == []
        for core in cores
    ), cores
    assert all(
        core["live_commands"] == 0
        and core["allocation_pins"] == 0
        and core["dma_idle"] == 1
        for core in cores
    ), cores


def test_loader_rejection_records_load_stage(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    image = program_dir / "program.mshb"
    raw = bytearray(image.read_bytes())
    assert len(raw) > 64
    truncated = bytes(raw[: len(raw) // 2])
    image.write_bytes(truncated)
    assert _python_decode_verdict(truncated) != "ACCEPTED"
    run = run_mock(tmp_path, "m5out", PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    load = [row for row in rows if row["stage"] == "load"]
    assert len(load) == 1, rows
    assert load[0]["code"] == _python_decode_verdict(truncated), load[0]
    result = program_dir / "actual_result.json"
    assert not result.exists() or not result_of(program_dir)["cores"][0]["commands_issued"]


def test_runtime_error_records_runtime_stage(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    command = semantic_command(program_dir, PUSH)
    descriptor = admitted_descriptors(program_dir, command)[0]
    run = run_mock(
        tmp_path,
        "m5out",
        PROGRAM,
        program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    rows = _records(program_dir)
    runtime = [row for row in rows if row["stage"] == "runtime"]
    assert len(runtime) == 1, rows
    record = runtime[0]
    assert record["code"] == "E_AXI_RESPONSE", record
    assert int(record["context"]["descriptor_id"]) == descriptor, record
    assert int(record["context"]["command_id"]) == command, record
    assert int(record["context"]["core_id"]) == 0, record


def test_watchdog_records_watchdog_stage(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    command = semantic_command(program_dir, PUSH)
    descriptor = admitted_descriptors(program_dir, command)[-1]
    run = run_mock(
        tmp_path,
        "m5out",
        PROGRAM,
        program_dir,
        ("--drop-descriptors", str(descriptor), "--watchdog-ticks", "200000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    rows = _records(program_dir)
    watchdog = [row for row in rows if row["stage"] == "watchdog"]
    assert len(watchdog) == 1, rows
    assert "E_RUNTIME_DEADLOCK" in json.dumps(watchdog[0]), watchdog[0]


def test_first_business_failure_is_latched(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    command = semantic_command(program_dir, PUSH)
    admitted = admitted_descriptors(program_dir, command)
    run = run_mock(
        tmp_path,
        "m5out",
        PROGRAM,
        program_dir,
        ("--error-descriptors", f"{admitted[0]},{admitted[5]}"),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    rows = _records(program_dir)
    runtime = [row for row in rows if row["stage"] == "runtime"]
    assert len(runtime) == 1, rows
    keys = [(row["context"].get("command_id"), row["context"].get("generation")) for row in runtime]
    assert len(set(keys)) == len(keys), runtime

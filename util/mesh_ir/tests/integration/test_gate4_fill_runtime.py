"""Slice E fix: the LOCAL_FILL healthy carrier must reconcile with the pattern oracle."""

import hashlib

from tests.integration.support.runtime_harness import (
    admitted_descriptors,
    build_program,
    descriptor_executions,
    result_of,
    run_mock,
    semantic_command,
    schedule,
)

PROGRAM = "poison_reduce"
FILL = "timing:00:fill:peer:a"
OFFSET_PROGRAM = "fill_offset"
OFFSET_FILL = "fill:input"


def test_poison_reduce_fault_free_run_is_healthy(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_mock(tmp_path, "m5out", PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    result = result_of(program_dir)
    for core in result["cores"]:
        assert core["commands_errored"] == 0, core
        assert core["commands_cancelled"] == 0, core
        assert core["live_commands"] == 0 and core["allocation_pins"] == 0, core
        assert core["dma_idle"] == 1, core


def test_multi_row_fill_payload_matches_the_admitted_geometry(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    fill = semantic_command(program_dir, FILL)
    row = next(
        item
        for item in schedule(program_dir)["DMA_DESCRIPTORS"]
        if item["command_id"] == fill
    )
    assert row["rows"] > 1, row
    assert row["row_bytes"] % 8 != 0, row
    admitted = admitted_descriptors(program_dir, fill)
    assert admitted, fill
    run = run_mock(tmp_path, "m5out", PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    rows = descriptor_executions(
        result, instance_id, row["dst"]["owner_core"], fill, 0
    )
    assert sorted(item["descriptor_id"] for item in rows) == admitted, rows
    pattern = _fill_pattern(program_dir, fill)
    payload = row["row_bytes"] * row["rows"]
    expected = bytes(
        (pattern >> (8 * (index % 8))) & 0xFF for index in range(payload)
    )
    for item in rows:
        assert item["committed"] and item["status"] == "OK", item
        assert item["transfer"]["fill_bytes"] == payload
        assert item["landing_digest"] == hashlib.sha256(expected).hexdigest(), item
        assert _sentinel_failures(
            item["sentinel_ranges"], required=("right", "row-gap")
        ) == [], item


def _range(kind, before, after, available=True, size=8):
    return {
        "kind": kind,
        "address": 0x1000,
        "size": size,
        "available": available,
        "before_digest": before,
        "after_digest": after,
    }


def _sentinel_failures(ranges, required=("left", "right")):
    failures = []
    verified = {row["kind"] for row in ranges if row["available"]}
    for kind in required:
        if kind not in verified:
            failures.append(("missing", kind))
    for row in ranges:
        if row["available"] and row["before_digest"] != row["after_digest"]:
            failures.append(("changed", row["kind"]))
    return failures


def _legacy_side_digest(before, after):
    return hashlib.sha256(bytes(before) + bytes(after)).hexdigest()


def _fill_pattern(program_dir, command_id):
    sections = schedule(program_dir)
    command = next(
        item for item in sections["COMMANDS"] if item["command_id"] == command_id
    )
    value = sections["OP_ATTRS"][command["attr_index"] - 1]["pattern"]
    return int(value, 16) if isinstance(value, str) else value


def test_fill_lands_at_the_admitted_destination(tmp_path):
    program_dir = build_program(tmp_path, OFFSET_PROGRAM)
    fill = semantic_command(program_dir, OFFSET_FILL)
    admitted = admitted_descriptors(program_dir, fill)
    assert len(admitted) == 1, admitted
    geometry = next(
        item
        for item in schedule(program_dir)["DMA_DESCRIPTORS"]
        if item["command_id"] == fill
    )
    assert geometry["rows"] == 1 and geometry["row_bytes"] == 128, geometry
    pattern = _fill_pattern(program_dir, fill)
    payload = geometry["row_bytes"] * geometry["rows"]
    expected = bytes(
        (pattern >> (8 * (index % 8))) & 0xFF for index in range(payload)
    )
    run = run_mock(tmp_path, "m5out", OFFSET_PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    rows = descriptor_executions(result, instance_id, 0, fill, 0)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["landing_digest"] == hashlib.sha256(expected).hexdigest(), row
    assert _sentinel_failures(row["sentinel_ranges"]) == [], row
    left = next(r for r in row["sentinel_ranges"] if r["kind"] == "left")
    right = next(r for r in row["sentinel_ranges"] if r["kind"] == "right")
    assert left["available"] is True and left["size"] == 8, left
    assert right["available"] is True and right["size"] == 8, right
    assert left["before_digest"] and right["before_digest"], row


def test_injected_error_fill_writes_nothing(tmp_path):
    program_dir = build_program(tmp_path, OFFSET_PROGRAM)
    fill = semantic_command(program_dir, OFFSET_FILL)
    descriptor = admitted_descriptors(program_dir, fill)[0]
    run = run_mock(
        tmp_path, "m5out", OFFSET_PROGRAM, program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    rows = descriptor_executions(result, instance_id, 0, fill, 0)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["completed"] is True, row
    assert row["committed"] is False, row
    assert row["status"] == "AXI_WRITE_ERROR", row
    assert row["commit_tick"] is None, row
    assert row["landing_digest"] is None, row
    assert row["sentinel_ranges"] == [], row
    assert row["transfer"]["fill_bytes"] == 0, row
    assert not row["transfer"]["payload_digest"], row
    assert sum(core["commands_errored"] for core in result["cores"]) == 1, result["cores"]
    for core in result["cores"]:
        assert core["live_commands"] == 0 and core["allocation_pins"] == 0, core
        assert core["dma_idle"] == 1, core


def test_typed_ranges_reject_uniform_and_single_side_corruption():
    intact = [_range("left", "aa", "aa"), _range("right", "bb", "bb")]
    assert _sentinel_failures(intact) == []
    uniform = [_range("left", "00", "ff"), _range("right", "00", "ff")]
    assert ("changed", "left") in _sentinel_failures(uniform)
    assert ("changed", "right") in _sentinel_failures(uniform)
    single = [_range("left", "aa", "aa"), _range("right", "bb", "cc")]
    assert _sentinel_failures(single) == [("changed", "right")]


def test_typed_ranges_reject_row_gap_corruption_and_missing_neighbours():
    gap = [
        _range("left", "aa", "aa"),
        _range("right", "bb", "bb"),
        _range("row-gap", "00", "11"),
    ]
    assert ("changed", "row-gap") in _sentinel_failures(gap)
    unavailable = [
        _range("left", None, None, available=False),
        _range("right", "bb", "bb"),
    ]
    assert ("missing", "left") in _sentinel_failures(
        unavailable, required=("left", "right")
    )


def test_uniform_corruption_would_pass_the_legacy_concatenated_form():
    left_before = _legacy_side_digest(b"\x00" * 8, b"\xff" * 8)
    right_before = _legacy_side_digest(b"\x00" * 8, b"\xff" * 8)
    assert left_before == right_before
    uniform = [_range("left", "00", "ff"), _range("right", "00", "ff")]
    assert _sentinel_failures(uniform) != []

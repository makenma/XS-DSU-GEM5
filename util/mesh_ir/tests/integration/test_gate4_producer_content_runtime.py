"""NORM11-DMA-SHAPES-CHECK: a multi-row transfer's content follows the admitted
source's real last producer, verified row by row."""

import copy
import hashlib
import json

import pytest

from mesh_ir.producer_content import (
    ProducerContentError,
    verify_transfer_producer_content,
)
from mesh_ir.runtime_reconciliation import payload_digest

from tests.integration.support.runtime_harness import (
    build_program,
    result_of,
    run_mock,
    schedule,
)

PROGRAM = "dma_shapes"
FILL_PATTERN_BYTE = 0xA5
TRANSFER_KIND = 3
FILL_KIND = 5


def _frame(result, core_id=0, instance=1):
    instances = [row for row in result["instances"] if row["instance"] == instance]
    assert len(instances) == 1, result["instances"]
    return instances[0]["cores"][str(core_id)]["observations"]


def _execution(result, descriptor_id, core_id=0):
    matches = [
        row
        for row in _frame(result, core_id)["descriptor_executions"]
        if row["descriptor_id"] == descriptor_id
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _producer(result, command_id, allocation_id, core_id=0):
    matches = [
        row
        for row in _frame(result, core_id)["compute_outputs"]
        if row["command_id"] == command_id
        and row["allocation_id"] == allocation_id
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _fill_pattern_digest(size):
    pattern = bytes([FILL_PATTERN_BYTE]) + bytes(7)
    row = pattern * (size // 8)
    return hashlib.sha256(row).hexdigest()


def _transfer_and_fill(program_dir):
    descriptors = schedule(program_dir)["DMA_DESCRIPTORS"]
    transfer = next(row for row in descriptors if row["kind"] == TRANSFER_KIND)
    fill = next(row for row in descriptors if row["kind"] == FILL_KIND)
    return transfer, fill


def _run_dma_shapes(tmp_path, instances=1):
    program_dir = build_program(tmp_path, PROGRAM)
    extra = ("--instances", str(instances)) if instances != 1 else ()
    run = run_mock(
        tmp_path, f"m5out-{instances}", PROGRAM, program_dir, extra
    )
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    return program_dir, result_of(program_dir), output


def _rejects(program_dir, result, descriptor_id, mutate):
    corrupted = copy.deepcopy(result)
    mutate(corrupted)
    with pytest.raises(ProducerContentError):
        verify_transfer_producer_content(program_dir, corrupted, descriptor_id)


def _mutations(transfer, producer_command, allocation_id):
    descriptor_id = transfer["descriptor_id"]

    def drop_last_row(result):
        rows = _execution(result, descriptor_id)["source_rows"]
        _execution(result, descriptor_id)["source_rows"] = rows[:-1]

    def repeat_first_row(result):
        rows = _execution(result, descriptor_id)["source_rows"]
        first = dict(rows[0])
        first["address"] = rows[1]["address"]
        _execution(result, descriptor_id)["source_rows"] = [rows[0], first]

    def stale_fill_content(result):
        digest = _fill_pattern_digest(transfer["row_bytes"])
        for row in _execution(result, descriptor_id)["source_rows"]:
            row["digest"] = digest

    def swap_rows(result):
        rows = _execution(result, descriptor_id)["source_rows"]
        _execution(result, descriptor_id)["source_rows"] = list(reversed(rows))

    def drop_producer(result):
        _frame(result)["compute_outputs"] = [
            row
            for row in _frame(result)["compute_outputs"]
            if not (
                row["command_id"] == producer_command
                and row["allocation_id"] == allocation_id
            )
        ]

    def producer_identity_mismatch(result):
        _producer(result, producer_command, allocation_id)["command_id"] = 999

    def producer_allocation_mismatch(result):
        _producer(result, producer_command, allocation_id)["allocation_id"] = 1

    def drop_producer_row(result):
        _producer(result, producer_command, allocation_id)["rows"] = (
            _producer(result, producer_command, allocation_id)["rows"][:-1]
        )

    return {
        "missing_row": drop_last_row,
        "first_row_only": repeat_first_row,
        "stale_fill_content": stale_fill_content,
        "rows_out_of_order": swap_rows,
        "producer_missing": drop_producer,
        "producer_command_mismatch": producer_identity_mismatch,
        "producer_allocation_mismatch": producer_allocation_mismatch,
        "producer_row_missing": drop_producer_row,
    }


def test_dma_shapes_multi_row_transfer_follows_its_admitted_producer(tmp_path):
    program_dir, result, _ = _run_dma_shapes(tmp_path)
    reconciliation = json.loads(
        (program_dir / "reconciliation.json").read_text()
    )
    assert reconciliation["status"] == "ok", reconciliation
    assert reconciliation["outcome"] == "done", reconciliation
    transfer, fill = _transfer_and_fill(program_dir)
    assert transfer["rows"] > 1, transfer
    assert transfer["useful_bytes"] == transfer["rows"] * transfer["row_bytes"], transfer
    summary = verify_transfer_producer_content(
        program_dir, result, transfer["descriptor_id"]
    )
    assert summary["descriptor_id"] == transfer["descriptor_id"], summary
    assert summary["command_id"] == transfer["command_id"], summary
    assert summary["allocation_id"], summary
    assert summary["source_stride_bytes"] == transfer["src_stride_bytes"], summary
    rows = summary["instances"][0]["rows"]
    assert len(rows) == transfer["rows"], rows
    for index, row in enumerate(rows):
        assert row["address"] == (
            summary["source_address"] + index * transfer["src_stride_bytes"]
        ), row
        assert row["size"] == transfer["row_bytes"], row
        assert row["digest"], row
    producer = _producer(
        result, summary["producer_command_id"], summary["allocation_id"]
    )
    assert len(producer["rows"]) >= len(rows), producer
    fill_digest = payload_digest(
        (bytes([FILL_PATTERN_BYTE]) + bytes(7)) * (fill["row_bytes"] // 8)
    )
    fill_row = next(
        row
        for row in result["transport"]
        if row["descriptor_id"] == fill["descriptor_id"]
    )
    assert fill_row["payload_digest"] == fill_digest, fill_row


def test_dma_shapes_drain_keeps_its_agreed_failure_exit(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    fill_descriptor = next(
        row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["kind"] == FILL_KIND and row["useful_bytes"] > 0
    )
    run = run_mock(
        tmp_path,
        "m5out",
        PROGRAM,
        program_dir,
        ("--error-descriptors", str(fill_descriptor["descriptor_id"])),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    assert run.returncode != 0, output
    assert "producer content" not in output, output
    reconciliation = json.loads((program_dir / "reconciliation.json").read_text())
    assert reconciliation["status"] == "ok", reconciliation
    assert reconciliation["outcome"] == "error_drained", reconciliation


def _instance_producers(result, instance, command_id, allocation_id):
    return [
        row
        for row in _frame(result, instance=instance)["compute_outputs"]
        if row["command_id"] == command_id
        and row["allocation_id"] == allocation_id
    ]


def _instance_mutations(transfer, producer_command, allocation_id):
    descriptor_id = transfer["descriptor_id"]

    def producer_generation(result):
        for instance in (1, 2):
            for row in _instance_producers(
                result, instance, producer_command, allocation_id
            ):
                row["generation"] = 999

    def address_shift(result):
        for instance in (1, 2):
            for row in _instance_producers(
                result, instance, producer_command, allocation_id
            ):
                row["offset"] += 64
                for run in row["rows"]:
                    run["address"] += 64

    def second_instance_rows_cleared(result):
        _instance_producers(result, 2, producer_command, allocation_id)[0][
            "rows"
        ] = []

    def duplicate_then_missing(result):
        first = _instance_producers(result, 1, producer_command, allocation_id)[0]
        _frame(result, instance=1)["compute_outputs"].append(copy.deepcopy(first))
        _frame(result, instance=2)["compute_outputs"] = [
            row
            for row in _frame(result, instance=2)["compute_outputs"]
            if not (
                row["command_id"] == producer_command
                and row["allocation_id"] == allocation_id
            )
        ]

    def second_instance_row_digest_zeroed(result):
        _instance_producers(result, 2, producer_command, allocation_id)[0]["rows"][
            0
        ]["digest"] = hashlib.sha256(bytes(transfer["row_bytes"])).hexdigest()

    def second_instance_execution_dropped(result):
        _frame(result, instance=2)["descriptor_executions"] = [
            row
            for row in _frame(result, instance=2)["descriptor_executions"]
            if row["descriptor_id"] != descriptor_id
        ]

    return {
        "producer_generation": producer_generation,
        "producer_address_shift": address_shift,
        "second_instance_producer_rows_missing": second_instance_rows_cleared,
        "duplicate_producer_then_missing_instance": duplicate_then_missing,
        "second_instance_producer_digest_wrong": second_instance_row_digest_zeroed,
        "second_instance_execution_missing": second_instance_execution_dropped,
    }


def test_dma_shapes_two_instances_verify_each_instance_producer(tmp_path):
    program_dir, result, output = _run_dma_shapes(tmp_path, instances=2)
    assert "MESH_PROGRAM_DONE" in output, output
    reconciliation = json.loads((program_dir / "reconciliation.json").read_text())
    assert reconciliation["status"] == "ok", reconciliation
    assert reconciliation["outcome"] == "done", reconciliation
    transfer, _ = _transfer_and_fill(program_dir)
    summary = verify_transfer_producer_content(
        program_dir, result, transfer["descriptor_id"]
    )
    instances = summary["instances"]
    assert [row["instance"] for row in instances] == [1, 2], instances
    for entry in instances:
        assert len(entry["rows"]) == transfer["rows"], entry
        assert entry["generation"] == entry["producer_generation"], entry
        for index, row in enumerate(entry["rows"]):
            assert row["address"] == (
                summary["source_address"] + index * transfer["src_stride_bytes"]
            ), row
            assert row["size"] == transfer["row_bytes"], row
    assert instances[0]["rows"] == instances[1]["rows"], instances


def test_transfer_producer_content_rejects_instance_and_address_counterexamples(
    tmp_path,
):
    program_dir, result, _ = _run_dma_shapes(tmp_path, instances=2)
    transfer, _ = _transfer_and_fill(program_dir)
    summary = verify_transfer_producer_content(
        program_dir, result, transfer["descriptor_id"]
    )
    mutations = _instance_mutations(
        transfer, summary["producer_command_id"], summary["allocation_id"]
    )
    assert len(mutations) >= 5, mutations
    for name, mutate in mutations.items():
        _rejects(program_dir, result, transfer["descriptor_id"], mutate)


def test_transfer_producer_content_rejects_content_counterexamples(tmp_path):
    program_dir, result, _ = _run_dma_shapes(tmp_path)
    transfer, _ = _transfer_and_fill(program_dir)
    summary = verify_transfer_producer_content(
        program_dir, result, transfer["descriptor_id"]
    )
    mutations = _mutations(
        transfer, summary["producer_command_id"], summary["allocation_id"]
    )
    assert len(mutations) >= 4, mutations
    for name, mutate in mutations.items():
        _rejects(program_dir, result, transfer["descriptor_id"], mutate)

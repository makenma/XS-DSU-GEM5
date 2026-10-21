"""R31-02: a producer's physical byte-run records cover exactly the bytes their
digests cover, for contiguous, non-contiguous and offset layouts."""

import hashlib
import json

from mesh_ir.architecture import load_arch
from mesh_ir.golden_programs import (
    _VIEW_OFFSET_WHOLE_REGION,
    _build_view_offset_compute_program,
    _padding_seed_program,
)
from mesh_ir.producer_content import verify_transfer_producer_content
from mesh_ir.publication import publish_authored_program

from tests.integration.support.runtime_harness import (
    ARCH,
    build_program,
    descriptor_executions,
    result_of,
    run_mock,
    schedule,
    semantic_command,
)

CONTIGUOUS_PROGRAM = "dma_shapes"
OFFSET_PROGRAM = "fill_view_offset_out_pad"
INNER_STRIDE = (16, 2)
INNER_STRIDE_SEEDS = (0x00, 0x5A)


def _frame(result, core_id=0, instance=1):
    instances = [row for row in result["instances"] if row["instance"] == instance]
    assert len(instances) == 1, result["instances"]
    return instances[0]["cores"][str(core_id)]["observations"]


def _producer(result, allocation_id, core_id=0):
    matches = [
        row
        for row in _frame(result, core_id)["compute_outputs"]
        if row["allocation_id"] == allocation_id
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _execution(result, descriptor_id, core_id=0):
    matches = [
        row
        for row in _frame(result, core_id)["descriptor_executions"]
        if row["descriptor_id"] == descriptor_id
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _local_consumer(program_dir):
    return next(
        row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["kind"] in (2, 3)
    )


def _allocation_of(program_dir, shard_id):
    return next(
        row["allocation_id"]
        for row in schedule(program_dir)["SHARDS"]
        if row["shard_id"] == shard_id
    )


def _allocation(program_dir, allocation_id):
    return next(
        row
        for row in schedule(program_dir)["ALLOCATIONS"]
        if row["allocation_id"] == allocation_id
    )


def _absolute_allocation_base(program_dir, descriptor):
    traffic = json.loads(
        (program_dir / "expected_traffic.json").read_text()
    )["descriptors"]
    row = next(
        item
        for item in traffic
        if item["identity"]["descriptor_id"] == descriptor["descriptor_id"]
    )
    return row["src_address"] - descriptor["src"]["offset_bytes"]


def _producer_intervals(producer, allocation_offset, key="rows"):
    return sorted(
        (run["address"] - allocation_offset, run["address"] - allocation_offset + run["size"])
        for run in producer[key]
    )


def _union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _assert_run_partition(program_dir, descriptor, producer, execution, allocation_offset):
    fine = _producer_intervals(producer, allocation_offset)
    merged = _producer_intervals(producer, allocation_offset, key="merged_rows")
    consumer = _consumer_intervals(program_dir, descriptor, execution)
    assert _union(fine) == _union(consumer), (fine, consumer)
    assert fine == consumer or merged == consumer, (fine, merged, consumer)
    return fine, merged, consumer


def _consumer_intervals(program_dir, descriptor, execution):
    base = _absolute_allocation_base(program_dir, descriptor)
    return sorted(
        (row["address"] - base, row["address"] - base + row["size"])
        for row in execution["source_rows"]
    )


def _run_contiguous(tmp_path):
    program_dir = build_program(tmp_path, CONTIGUOUS_PROGRAM)
    run = run_mock(tmp_path, "m5out-contiguous", CONTIGUOUS_PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    return program_dir, result_of(program_dir)


def _run_offset(tmp_path):
    program_dir = build_program(tmp_path, OFFSET_PROGRAM)
    run = run_mock(tmp_path, "m5out-offset", OFFSET_PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    return program_dir, result_of(program_dir)


def _run_inner_stride(tmp_path, seed):
    arch = load_arch(ARCH)
    base = _build_view_offset_compute_program(
        arch,
        "review_inner_stride",
        "inner_stride",
        "review_inner_stride",
        0,
        0,
        output_padding_region=_VIEW_OFFSET_WHOLE_REGION,
        strides=INNER_STRIDE,
    )
    program = _padding_seed_program(
        arch, seed, base=base, variant_label="inner_stride"
    )
    directory = tmp_path / f"inner_stride_{seed:02x}"
    publish_authored_program(directory, program, arch, kind="review")
    run = run_mock(tmp_path, f"m5out-inner-{seed:02x}", OFFSET_PROGRAM, directory)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    return directory, result_of(directory)


def test_contiguous_producer_runs_equal_the_written_bytes(tmp_path):
    program_dir, result = _run_contiguous(tmp_path)
    descriptor = _local_consumer(program_dir)
    allocation = _allocation_of(program_dir, descriptor["src"]["shard_id"])
    producer = _producer(result, allocation)
    execution = _execution(result, descriptor["descriptor_id"])
    assert [run["size"] for run in producer["rows"]] == [
        descriptor["row_bytes"]
    ] * descriptor["rows"], producer["rows"]
    allocation_offset = _allocation(program_dir, allocation)["offset_bytes"]
    _assert_run_partition(
        program_dir, descriptor, producer, execution, allocation_offset
    )
    summary = verify_transfer_producer_content(
        program_dir, result, descriptor["descriptor_id"]
    )
    assert len(summary["instances"][0]["rows"]) == descriptor["rows"], summary


def test_nonzero_view_offset_producer_runs_stay_exact(tmp_path):
    program_dir, result = _run_offset(tmp_path)
    descriptor = _local_consumer(program_dir)
    allocation = _allocation_of(program_dir, descriptor["src"]["shard_id"])
    producer = _producer(result, allocation)
    allocation_offset = _allocation(program_dir, allocation)["offset_bytes"]
    assert descriptor["src"]["offset_bytes"] > 0, descriptor
    assert producer["offset"] - allocation_offset == descriptor["src"][
        "offset_bytes"
    ], producer
    execution = _execution(result, descriptor["descriptor_id"])
    allocation_offset = _allocation(program_dir, allocation)["offset_bytes"]
    _assert_run_partition(
        program_dir, descriptor, producer, execution, allocation_offset
    )
    summary = verify_transfer_producer_content(
        program_dir, result, descriptor["descriptor_id"]
    )
    assert len(summary["instances"][0]["rows"]) == descriptor["rows"], summary


def test_inner_stride_producer_runs_exclude_padding(tmp_path):
    runs = {}
    gaps = {}
    for seed in INNER_STRIDE_SEEDS:
        program_dir, result = _run_inner_stride(tmp_path, seed)
        descriptor = _local_consumer(program_dir)
        allocation = _allocation_of(program_dir, descriptor["src"]["shard_id"])
        producer = _producer(result, allocation)
        execution = _execution(result, descriptor["descriptor_id"])
        assert descriptor["row_bytes"] == 2, descriptor
        assert descriptor["src_stride_bytes"] == 4, descriptor
        assert [run["size"] for run in producer["rows"]] == [2] * 64, producer[
            "rows"
        ]
        allocation_offset = _allocation(program_dir, allocation)["offset_bytes"]
        _assert_run_partition(
            program_dir, descriptor, producer, execution, allocation_offset
        )
        summary = verify_transfer_producer_content(
            program_dir, result, descriptor["descriptor_id"]
        )
        assert len(summary["instances"][0]["rows"]) == descriptor["rows"], summary
        allocation_offset = _allocation(program_dir, allocation)["offset_bytes"]
        written = {
            run["address"] - allocation_offset
            for run in producer["rows"]
        }
        for run in producer["rows"]:
            for offset in range(2, run["size"]):
                assert run["address"] + offset not in written, run
        fill = descriptor_executions(
            result,
            1,
            0,
            semantic_command(program_dir, "fill:output"),
            0,
        )[0]
        padding = {
            span["address"]
            for span in fill["sentinel_ranges"]
            if span["kind"] == "row-gap"
        }
        aperture = next(
            region.base
            for region in load_arch(ARCH).regions
            if region.kind == "CORE_SRAM_APERTURE"
        )
        padding_offsets = {address - aperture - allocation_offset for address in padding}
        assert written.isdisjoint(padding_offsets), (written, padding_offsets)
        runs[seed] = _producer_intervals(producer, allocation_offset)
        gaps[seed] = sorted(
            span["before_digest"]
            for span in fill["sentinel_ranges"]
            if span["kind"] == "row-gap"
        )
    zero_digest = hashlib.sha256(bytes(2)).hexdigest()
    seed_digest = hashlib.sha256(bytes([INNER_STRIDE_SEEDS[1]]) * 2).hexdigest()
    assert runs[INNER_STRIDE_SEEDS[0]] == runs[INNER_STRIDE_SEEDS[1]], runs
    assert set(gaps[INNER_STRIDE_SEEDS[0]]) == {zero_digest}, gaps[
        INNER_STRIDE_SEEDS[0]
    ]
    assert seed_digest in gaps[INNER_STRIDE_SEEDS[1]], gaps[INNER_STRIDE_SEEDS[1]]
    assert gaps[INNER_STRIDE_SEEDS[0]] != gaps[INNER_STRIDE_SEEDS[1]], gaps


def test_inner_stride_padding_is_not_claimed_by_any_run(tmp_path):
    for seed in INNER_STRIDE_SEEDS:
        program_dir, result = _run_inner_stride(tmp_path, seed)
        descriptor = _local_consumer(program_dir)
        allocation = _allocation_of(program_dir, descriptor["src"]["shard_id"])
        producer = _producer(result, allocation)
        allocation_offset = _allocation(program_dir, allocation)["offset_bytes"]
        claimed = set()
        for run in list(producer["rows"]) + list(producer["merged_rows"]):
            start = run["address"] - allocation_offset
            claimed.update(range(start, start + run["size"]))
        written = set()
        for run in producer["rows"]:
            start = run["address"] - allocation_offset
            written.update(range(start, start + run["size"]))
        fill = descriptor_executions(
            result,
            1,
            0,
            semantic_command(program_dir, "fill:output"),
            0,
        )[0]
        aperture = next(
            region.base
            for region in load_arch(ARCH).regions
            if region.kind == "CORE_SRAM_APERTURE"
        )
        padding = set()
        for span in fill["sentinel_ranges"]:
            if span["kind"] != "row-gap":
                continue
            start = span["address"] - aperture - allocation_offset
            padding.update(range(start, start + span["size"]))
        assert padding, fill["sentinel_ranges"]
        assert claimed.isdisjoint(padding), (claimed & padding)
        assert written == claimed, (written, claimed)

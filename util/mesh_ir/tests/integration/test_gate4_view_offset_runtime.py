"""R17-01/R19-03: accepted in-allocation view offsets must compute for real and
be reported at the admitted write address, with padding left untouched."""

import copy
import hashlib

import pytest

from mesh_ir.architecture import load_arch
from tests.integration.support.runtime_harness import (
    ARCH,
    build_program,
    compute_output,
    descriptor_executions,
    expected_traffic,
    result_of,
    run_mock,
    schedule,
    semantic_command,
    terminal_partition,
)

FILL = "fill:input"
GEMM = "gemm"
STORE = "store:output"
OUTPUT_FILL = "fill:output"
FILLED_BYTES = 128
FILL_OFFSET = 64
CORE = 0
CASES = (
    ("fill_view_offset", FILL_OFFSET, 0),
    ("fill_view_offset_out", FILL_OFFSET, FILL_OFFSET),
    ("fill_view_offset_in0", 0, 0),
    ("fill_view_offset_in0_out", 0, FILL_OFFSET),
)
SRAM_APERTURE = next(
    region.base for region in load_arch(ARCH).regions if region.kind == "CORE_SRAM_APERTURE"
)


def _fill_pattern(program_dir, command_id):
    sections = schedule(program_dir)
    command = next(
        item for item in sections["COMMANDS"] if item["command_id"] == command_id
    )
    value = sections["OP_ATTRS"][command["attr_index"] - 1]["pattern"]
    return int(value, 16) if isinstance(value, str) else value


def _descriptor(program_dir, command_id):
    rows = [
        row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["command_id"] == command_id
    ]
    assert len(rows) == 1, (command_id, rows)
    return rows[0]


def _admitted_address(program_dir, command_id, field):
    descriptor = _descriptor(program_dir, command_id)
    rows = [
        row
        for row in expected_traffic(program_dir)
        if row["identity"]["command_id"] == command_id
        and row["identity"]["descriptor_id"] == descriptor["descriptor_id"]
    ]
    assert len(rows) == 1, (command_id, rows)
    return rows[0][field]


def _allocation_of(program_dir, shard_id):
    return next(
        row["allocation_id"]
        for row in schedule(program_dir)["SHARDS"]
        if row["shard_id"] == shard_id
    )


def _allocation_field(program_dir, allocation_id, field):
    return next(
        row[field]
        for row in schedule(program_dir)["ALLOCATIONS"]
        if row["allocation_id"] == allocation_id
    )


def _allocation_base(program_dir, allocation_id):
    return _allocation_field(program_dir, allocation_id, "offset_bytes")


def _run(tmp_path, program):
    program_dir = build_program(tmp_path, program)
    run = run_mock(tmp_path, f"m5out-{program}", program, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    return program_dir, result_of(program_dir)


def _assert_zero_resources(result):
    for core in result["cores"]:
        assert core["commands_errored"] == 0, core
        assert core["commands_cancelled"] == 0, core
        assert core["live_commands"] == 0 and core["allocation_pins"] == 0, core
        assert core["dma_idle"] == 1, core


def test_nonzero_view_offset_compute_runs_end_to_end(tmp_path):
    program_dir, result = _run(tmp_path, "fill_view_offset")
    fill = semantic_command(program_dir, FILL)
    geometry = _descriptor(program_dir, fill)
    assert geometry["dst"]["offset_bytes"] == FILL_OFFSET, geometry
    assert geometry["row_bytes"] * geometry["rows"] == FILLED_BYTES, geometry
    pattern = _fill_pattern(program_dir, fill)
    expected = bytes(
        (pattern >> (8 * (index % 8))) & 0xFF for index in range(FILLED_BYTES)
    )

    instance_id = result["instances"][0]["instance"]
    rows = descriptor_executions(
        result, instance_id, geometry["dst"]["owner_core"], fill, 0
    )
    assert len(rows) == 1, rows
    assert rows[0]["committed"] and rows[0]["status"] == "OK", rows
    assert (
        rows[0]["landing_digest"] == hashlib.sha256(expected).hexdigest()
    ), rows[0]
    assert rows[0]["transfer"]["fill_bytes"] == FILLED_BYTES, rows[0]
    terminal_partition(result, 0)
    _assert_zero_resources(result)


def test_compute_output_observation_tracks_the_admitted_write_address(tmp_path):
    controls = {}
    for program, input_offset, output_offset in CASES:
        program_dir, result = _run(tmp_path, program)
        fill = semantic_command(program_dir, FILL)
        gemm = semantic_command(program_dir, GEMM)
        store = semantic_command(program_dir, STORE)
        fill_descriptor = _descriptor(program_dir, fill)
        store_descriptor = _descriptor(program_dir, store)
        assert fill_descriptor["dst"]["offset_bytes"] == input_offset, fill_descriptor
        assert store_descriptor["src"]["offset_bytes"] == output_offset, store_descriptor
        store_source = _admitted_address(program_dir, store, "src_address")
        expected_allocation = _allocation_of(
            program_dir, store_descriptor["src"]["shard_id"]
        )
        expected_offset = (
            _allocation_base(program_dir, expected_allocation)
            + store_descriptor["src"]["offset_bytes"]
        )
        assert store_source == SRAM_APERTURE + expected_offset, (
            program,
            store_source,
            expected_offset,
        )
        instance_id = result["instances"][0]["instance"]
        observed = compute_output(result, instance_id, CORE, gemm, 0)
        assert observed["allocation_id"] == expected_allocation, (program, observed)
        assert observed["offset"] == expected_offset, (program, observed, expected_offset)
        store_row = descriptor_executions(result, instance_id, CORE, store, 0)[0]
        assert store_row["committed"] and store_row["status"] == "OK", store_row
        controls[program] = (
            tuple(observed["digest_words"]),
            store_row["transfer"]["payload_digest"],
        )
        terminal_partition(result, 0)
        _assert_zero_resources(result)
    assert len(set(controls.values())) == 1, controls


PADDING_PAIR = (
    ("fill_view_offset_padding_zero", 0x00),
    ("fill_view_offset_padding_pattern", 0x5A),
    ("fill_view_offset_row_gap_zero", 0x00),
    ("fill_view_offset_row_gap_pattern", 0x5A),
)


def _seed_spans(program_dir):
    allocations = {
        row["allocation_id"]: row for row in schedule(program_dir)["ALLOCATIONS"]
    }
    return [
        (
            SRAM_APERTURE + allocations[allocation_id]["offset_bytes"],
            allocations[allocation_id]["size_bytes"],
        )
        for allocation_id in (3, 4)
        if allocation_id in allocations
    ]


def _seeded_digest(seed, spans, address, size):
    data = bytes(
        seed
        if any(base <= address + offset < base + length for base, length in spans)
        else 0
        for offset in range(size)
    )
    return hashlib.sha256(data).hexdigest()


def _destination_geometry(program_dir, descriptor):
    allocation = _allocation_of(program_dir, descriptor["dst"]["shard_id"])
    allocation_base = SRAM_APERTURE + _allocation_base(program_dir, allocation)
    allocation_bytes = _allocation_field(program_dir, allocation, "size_bytes")
    base = allocation_base + descriptor["dst"]["offset_bytes"]
    end = (
        base
        + (descriptor["rows"] - 1) * descriptor["dst_stride_bytes"]
        + descriptor["row_bytes"]
    )
    return allocation_base, allocation_bytes, base, end


def _sentinel_ranges(program_dir, descriptor):
    allocation_base, allocation_bytes, base, end = _destination_geometry(
        program_dir, descriptor
    )
    ranges = []
    if base > allocation_base:
        ranges.append(("object-prefix", allocation_base, base - allocation_base))
    if base >= 8:
        ranges.append(("left", base - 8, 8))
    ranges.append(("right", end, 8))
    for row in range(descriptor["rows"] - 1):
        gap_begin = (
            base + row * descriptor["dst_stride_bytes"] + descriptor["row_bytes"]
        )
        gap_end = base + (row + 1) * descriptor["dst_stride_bytes"]
        if gap_end > gap_begin:
            ranges.append(("row-gap", gap_begin, gap_end - gap_begin))
    allocation_end = allocation_base + allocation_bytes
    if allocation_end > end:
        ranges.append(("allocation-tail", end, allocation_end - end))
    return ranges


def _expected_sentinels(program_dir, descriptor, seed):
    spans = _seed_spans(program_dir)
    return [
        {
            "kind": kind,
            "address": address,
            "size": size,
            "available": True,
            "before_digest": _seeded_digest(seed, spans, address, size),
            "after_digest": _seeded_digest(seed, spans, address, size),
        }
        for kind, address, size in _sentinel_ranges(program_dir, descriptor)
    ]


def _contract_padding(program_dir, descriptor):
    allocation_base, allocation_bytes, base, end = _destination_geometry(
        program_dir, descriptor
    )
    intervals = []
    if base > allocation_base:
        intervals.append((allocation_base, base))
    for row in range(descriptor["rows"] - 1):
        gap_begin = (
            base + row * descriptor["dst_stride_bytes"] + descriptor["row_bytes"]
        )
        gap_end = base + (row + 1) * descriptor["dst_stride_bytes"]
        if gap_end > gap_begin:
            intervals.append((gap_begin, gap_end))
    allocation_end = allocation_base + allocation_bytes
    if allocation_end > end:
        intervals.append((end, allocation_end))
    return intervals


def _assert_padding_preserved(program_dir, result, seed):
    instance_id = result["instances"][0]["instance"]
    for command in (
        semantic_command(program_dir, FILL),
        semantic_command(program_dir, OUTPUT_FILL),
    ):
        descriptor = _descriptor(program_dir, command)
        rows = descriptor_executions(result, instance_id, CORE, command, 0)
        assert len(rows) == 1 and rows[0]["committed"], rows
        assert rows[0]["sentinel_ranges"] == _expected_sentinels(
            program_dir, descriptor, seed
        ), (command, rows[0]["sentinel_ranges"])
        sampled = set()
        for span in rows[0]["sentinel_ranges"]:
            if span["available"]:
                sampled.update(
                    range(span["address"], span["address"] + span["size"])
                )
        for start, stop in _contract_padding(program_dir, descriptor):
            assert set(range(start, stop)) <= sampled, (command, start, stop)


def test_view_offset_padding_pair_keeps_the_specified_initial_values(tmp_path):
    controls = {}
    for program, seed in PADDING_PAIR:
        program_dir, result = _run(tmp_path, program)
        store = semantic_command(program_dir, STORE)
        store_descriptor = _descriptor(program_dir, store)
        instance_id = result["instances"][0]["instance"]
        observed = compute_output(
            result, instance_id, CORE, semantic_command(program_dir, GEMM), 0
        )
        expected_offset = (
            _allocation_base(
                program_dir,
                _allocation_of(program_dir, store_descriptor["src"]["shard_id"]),
            )
            + store_descriptor["src"]["offset_bytes"]
        )
        assert observed["offset"] == expected_offset, (program, observed)
        _assert_padding_preserved(program_dir, result, seed)
        store_row = descriptor_executions(result, instance_id, CORE, store, 0)[0]
        controls[program] = (
            tuple(observed["digest_words"]),
            store_row["transfer"]["payload_digest"],
        )
        terminal_partition(result, 0)
        _assert_zero_resources(result)
    assert len(set(controls.values())) == 1, controls


def test_padding_assertion_requires_the_sampled_ranges(tmp_path):
    for index in (1, 3):
        program, seed = PADDING_PAIR[index]
        program_dir, result = _run(tmp_path, program)
        _assert_padding_preserved(program_dir, result, seed)
        for mode in ("removed", "unavailable"):
            corrupted = copy.deepcopy(result)
            for instance in corrupted["instances"]:
                for core in instance["cores"].values():
                    for row in core["observations"]["descriptor_executions"]:
                        if mode == "removed":
                            row["sentinel_ranges"] = []
                        else:
                            for span in row["sentinel_ranges"]:
                                span["available"] = False
                                span["before_digest"] = None
                                span["after_digest"] = None
            with pytest.raises(AssertionError):
                _assert_padding_preserved(program_dir, corrupted, seed)


SPLIT_FILL = "fill:output"
SPLIT_CARRIERS = (
    ("fill_view_offset_split", 0x00),
    ("fill_view_offset_split_zero", 0x00),
    ("fill_view_offset_split_pattern", 0x5A),
)


def _command_descriptors(program_dir, command_id):
    return [
        row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["command_id"] == command_id
    ]


def _allocation_span(program_dir, allocation_id):
    return (
        SRAM_APERTURE + _allocation_base(program_dir, allocation_id),
        _allocation_field(program_dir, allocation_id, "size_bytes"),
    )


def _descriptor_write_bytes(allocation_base, descriptor):
    return {
        allocation_base
        + descriptor["dst"]["offset_bytes"]
        + row * descriptor["dst_stride_bytes"]
        + offset
        for row in range(descriptor["rows"])
        for offset in range(descriptor["row_bytes"])
    }


def _span_addresses(span):
    return set(range(span["address"], span["address"] + span["size"]))


def split_fill_attribution(program_dir, result):
    fill = semantic_command(program_dir, SPLIT_FILL)
    descriptors = _command_descriptors(program_dir, fill)
    allocation = _allocation_of(program_dir, descriptors[0]["dst"]["shard_id"])
    allocation_base, allocation_bytes = _allocation_span(program_dir, allocation)
    allocation_set = set(range(allocation_base, allocation_base + allocation_bytes))
    pieces = {
        row["descriptor_id"]: _descriptor_write_bytes(allocation_base, row)
        for row in descriptors
    }
    access = set().union(*pieces.values())
    view_start = allocation_base + min(row["dst"]["offset_bytes"] for row in descriptors)
    view_padding = set(range(allocation_base, view_start))
    producer_data = allocation_set - access - view_padding
    instance_id = result["instances"][0]["instance"]
    observed = {
        row["descriptor_id"]: row
        for row in descriptor_executions(result, instance_id, CORE, fill, 0)
    }
    report = {
        "program": program_dir.name,
        "fill_command": fill,
        "allocation": allocation,
        "allocation_base": allocation_base,
        "allocation_bytes": allocation_bytes,
        "descriptor_ids": [row["descriptor_id"] for row in descriptors],
        "dst_offsets": [row["dst"]["offset_bytes"] for row in descriptors],
        "access_bytes": len(access),
        "view_padding_bytes": len(view_padding),
        "producer_bytes": len(producer_data),
        "descriptors": [],
    }
    for descriptor in descriptors:
        descriptor_id = descriptor["descriptor_id"]
        row = observed[descriptor_id]
        own_piece = pieces[descriptor_id]
        siblings = access - own_piece
        sampled = set()
        padding_evidence = set()
        for span in row["sentinel_ranges"]:
            addresses = _span_addresses(span)
            sampled |= addresses
            if addresses <= view_padding:
                padding_evidence |= addresses
        report["descriptors"].append(
            {
                "descriptor_id": descriptor_id,
                "dst_offset_bytes": descriptor["dst"]["offset_bytes"],
                "destination_storage": row.get("destination_storage"),
                "spans": [
                    {
                        "kind": span["kind"],
                        "address": span["address"],
                        "size": span["size"],
                        "available": span["available"],
                        "kind_class": _span_class(
                            _span_addresses(span),
                            allocation_set,
                            view_padding,
                            own_piece,
                            siblings,
                            producer_data,
                        ),
                        "before_equals_after": span["before_digest"]
                        == span["after_digest"],
                    }
                    for span in row["sentinel_ranges"]
                ],
                "sampled": sampled,
                "padding_evidence": padding_evidence,
            }
        )
    report["_observed"] = observed
    report["_pieces"] = pieces
    report["_access"] = access
    report["_view_padding"] = view_padding
    report["_producer_data"] = producer_data
    report["_allocation_set"] = allocation_set
    report["_descriptors"] = descriptors
    return report


def _span_class(
    addresses, allocation_set, view_padding, own_piece, siblings, producer_data
):
    if addresses <= view_padding:
        return "view-padding"
    if addresses <= own_piece:
        return "own-piece"
    if addresses <= siblings:
        return "sibling-piece"
    if addresses <= producer_data:
        return "producer-data"
    if not addresses <= allocation_set:
        return "allocation-external"
    return "mixed"


def test_split_fill_samples_stay_inside_the_owning_allocation(tmp_path):
    for program, seed in SPLIT_CARRIERS:
        program_dir, result = _run(tmp_path, program)
        report = split_fill_attribution(program_dir, result)
        assert report["program"] == program, report["program"]
        descriptors = report["_descriptors"]
        assert len(descriptors) == 8, report["descriptor_ids"]
        assert report["dst_offsets"] == [64, 80, 96, 112, 128, 144, 160, 176], report
        assert [row["rows"] for row in descriptors] == [3] * 8, descriptors
        assert [row["dst_stride_bytes"] for row in descriptors] == [4] * 8, descriptors
        assert report["allocation"] == 2, report["allocation"]
        assert (report["allocation_base"], report["allocation_bytes"]) == (
            SRAM_APERTURE + 0x2000,
            192,
        ), report
        assert report["access_bytes"] == 48, report
        assert report["view_padding_bytes"] == 64, report
        assert report["producer_bytes"] == 80, report

        allocation_set = report["_allocation_set"]
        access = report["_access"]
        view_padding = report["_view_padding"]
        producer_data = report["_producer_data"]
        observed = report["_observed"]
        pieces = report["_pieces"]
        assert access.isdisjoint(view_padding)
        assert access.isdisjoint(producer_data)
        assert view_padding.isdisjoint(producer_data)
        assert view_padding | access | producer_data == allocation_set

        seeds = _seed_spans(program_dir)
        neighbourhood = set()
        evidence = set()
        for descriptor in descriptors:
            descriptor_id = descriptor["descriptor_id"]
            row = observed[descriptor_id]
            assert row["committed"] and row["status"] == "OK", row
            assert row["destination_storage"] == {
                "allocation_id": 2,
                "base": report["allocation_base"],
                "bytes": report["allocation_bytes"],
            }, row
            pattern = _fill_pattern(program_dir, report["fill_command"])
            payload = descriptor["row_bytes"] * descriptor["rows"]
            landed = bytes(
                (pattern >> (8 * (index % 8))) & 0xFF for index in range(payload)
            )
            assert row["landing_digest"] == hashlib.sha256(landed).hexdigest(), row
            assert row["transfer"]["fill_bytes"] == payload, row
            assert [
                (span["kind"], span["address"], span["size"], span["available"])
                for span in row["sentinel_ranges"]
            ] == [
                (kind, address, size, True)
                for kind, address, size in _sentinel_ranges(program_dir, descriptor)
            ], row
            own_piece = pieces[descriptor_id]
            siblings = access - own_piece
            sampled = set()
            for span in row["sentinel_ranges"]:
                addresses = _span_addresses(span)
                sampled |= addresses
                assert own_piece.isdisjoint(addresses), span
                assert span["before_digest"] == span["after_digest"], span
                if span["kind"] in ("object-prefix", "row-gap", "allocation-tail"):
                    assert addresses <= allocation_set, span
                if addresses <= (siblings | producer_data):
                    assert span["before_digest"] != _seeded_digest(
                        seed, seeds, span["address"], span["size"]
                    ), span
            assert own_piece.isdisjoint(sampled)
            assert allocation_set - own_piece <= sampled
            assert siblings <= sampled
            assert producer_data <= sampled
            assert view_padding <= sampled
            row_evidence = {
                address
                for span in row["sentinel_ranges"]
                for address in _span_addresses(span)
                if _span_addresses(span) <= view_padding
            }
            for span in row["sentinel_ranges"]:
                addresses = _span_addresses(span)
                if addresses <= view_padding:
                    assert span["before_digest"] == _seeded_digest(
                        seed, seeds, span["address"], span["size"]
                    ), span
            assert row_evidence <= view_padding
            assert row_evidence.isdisjoint(access)
            assert row_evidence.isdisjoint(producer_data)
            evidence |= row_evidence
            sampled_neighbourhood = sampled - allocation_set
            for span in row["sentinel_ranges"]:
                if _span_addresses(span) & sampled_neighbourhood:
                    assert span["kind"] in ("left", "right"), span
            neighbourhood |= sampled_neighbourhood
        assert evidence == view_padding, sorted(view_padding - evidence)
        assert neighbourhood, "the allocation neighbourhood is never sampled"
        assert neighbourhood.isdisjoint(allocation_set)
        first = observed[descriptors[0]["descriptor_id"]]
        prefix = next(
            span for span in first["sentinel_ranges"] if span["kind"] == "object-prefix"
        )
        assert prefix["address"] == report["allocation_base"], prefix
        assert prefix["address"] + prefix["size"] == report["allocation_base"] + 64, prefix
        last = observed[descriptors[-1]["descriptor_id"]]
        tail = next(
            span
            for span in last["sentinel_ranges"]
            if span["kind"] == "allocation-tail"
        )
        assert tail["address"] == report["allocation_base"] + 186, tail
        assert (
            tail["address"] + tail["size"]
            == report["allocation_base"] + report["allocation_bytes"]
        ), tail
        terminal_partition(result, 0)
        _assert_zero_resources(result)

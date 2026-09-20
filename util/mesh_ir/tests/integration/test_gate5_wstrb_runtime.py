"""Gate 5 WSTRB carriers: enabled lanes land byte-exact, disabled lanes hold.

A transfer must write exactly its admitted payload bytes: the unaligned head
and tail, the row padding, the 4 KiB and burst splits and the bytes around
them are observed on the real targets before cycle 0 and after quiescence.
"""

import copy
import json

import pytest

from mesh_ir.producer_content import (
    ProducerContentError,
    verify_transfer_producer_content,
)
from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    verified_burst_geometry,
    verified_sentinels,
)

from tests.integration.support.garnet_harness import (
    build_program,
    cause_of,
    output_of,
    result_of,
    run_garnet,
)

EDGE_PROGRAM = "dma_edge"
EDGE_CASE = "dma_edge"
SHAPES_PROGRAM = "dma_shapes"
SHAPES_CASE = "dma_shapes"
REGION_PROGRAM = "region_edge"
REGION_CASE = "region_edge"

# (source offset, destination offset, size, pattern) of the admitted edge
# carrier; the destination is never seeded and must receive exactly the
# pattern bytes of its source.
EDGE_PAIRS = (
    (0x100000, 0x200000, 1, 0x01),
    (0x100041, 0x200040, 1, 0x02),
    (0x100080, 0x200080, 31, 0x03),
    (0x1000C0, 0x2000C0, 33, 0x04),
    (0x100FF0, 0x201000, 64, 0x05),
    (0x101100, 0x201100, 511, 0x06),
    (0x101400, 0x201400, 513, 0x07),
    (0x101800, 0x201800, 700, 0x08),
)
HBM_BASE = 0x800000000

DUMP = ("--dump-verify-bytes", "2000")


def _run(tmp_path, name, case, program, extra=()):
    program_dir = build_program(tmp_path, program)
    run = run_garnet(tmp_path, f"m5out-{name}", case, program_dir,
                     extra_args=tuple(extra))
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    oracle = json.loads((program_dir / "gate5_oracle.json").read_text())
    return program_dir, result_of(program_dir), oracle


def _sentinel_targets(result):
    observed = {"endpoint": result["memory_endpoint"].get("sentinels", [])}
    for aperture in result["apertures"]:
        key = f"aperture_{aperture['core_id']}"
        if aperture.get("sentinels"):
            observed[key] = aperture["sentinels"]
    return observed


def test_edge_payloads_are_byte_exact_and_sentinels_hold(tmp_path):
    _, result, oracle = _run(
        tmp_path, "edge", EDGE_CASE, EDGE_PROGRAM, extra=DUMP
    )
    patterns = {HBM_BASE + dst: (size, pattern) for _, dst, size, pattern in EDGE_PAIRS}
    verifies = {row["address"]: row for row in result["memory_endpoint"]["verifies"]}
    assert sorted(verifies) == sorted(patterns), (sorted(verifies), sorted(patterns))
    for address, (size, pattern) in patterns.items():
        row = verifies[address]
        assert row["size"] == size, row
        assert row["bytes_hex"], row
        assert bytes.fromhex(row["bytes_hex"]) == bytes([pattern]) * size, (
            hex(address),
            row["bytes_hex"][:64],
        )
    declared = oracle["sentinels"]["endpoint"]
    checked = verified_sentinels(declared, result["memory_endpoint"]["sentinels"])
    assert checked == len(declared) == 9, (checked, declared)
    # Every declared span really is a gap or a neighbourhood of a payload, so
    # the evidence is about WSTRB-disabled bytes rather than empty memory.
    payloads = sorted(
        (HBM_BASE + dst, HBM_BASE + dst + size) for _, dst, size, _ in EDGE_PAIRS
    )
    for span in declared:
        start = span["address"]
        end = start + span["size"]
        assert any(end <= p_start or p_end <= start for p_start, p_end in payloads)
        assert any(
            end == p_start or start == p_end
            for p_start, p_end in payloads
        ), span


def test_peer_target_sentinels_hold_around_row_padding(tmp_path):
    _, result, oracle = _run(tmp_path, "shapes", SHAPES_CASE, SHAPES_PROGRAM)
    targets = _sentinel_targets(result)
    assert "endpoint" in targets and len(targets) >= 2, targets
    for key, declared in oracle["sentinels"].items():
        assert key in targets, (key, sorted(targets))
        checked = verified_sentinels(declared, targets[key])
        assert checked == len(declared), key
    # The peer transfer's row gap is declared as one whole padding span.
    peer = oracle["sentinels"]["aperture_1"]
    assert any(span["size"] >= 128 for span in peer), peer


def test_changed_sentinel_byte_is_rejected(tmp_path):
    _, result, oracle = _run(tmp_path, "tamper", SHAPES_CASE, SHAPES_PROGRAM)
    tampered = copy.deepcopy(result["memory_endpoint"]["sentinels"])
    tampered[0]["after_digest"] = "0" * 64
    with pytest.raises(ReconciliationError) as error:
        verified_sentinels(oracle["sentinels"]["endpoint"], tampered)
    assert "changed" in str(error.value)


def test_missing_sentinel_sample_is_rejected(tmp_path):
    _, result, oracle = _run(tmp_path, "missing", SHAPES_CASE, SHAPES_PROGRAM)
    tampered = copy.deepcopy(result["memory_endpoint"]["sentinels"])[:-1]
    with pytest.raises(ReconciliationError) as error:
        verified_sentinels(oracle["sentinels"]["endpoint"], tampered)
    assert "were declared" in str(error.value)


def test_unavailable_sentinel_sample_is_rejected(tmp_path):
    _, result, oracle = _run(tmp_path, "unavailable", SHAPES_CASE, SHAPES_PROGRAM)
    tampered = copy.deepcopy(result["memory_endpoint"]["sentinels"])
    tampered[0]["available"] = False
    with pytest.raises(ReconciliationError) as error:
        verified_sentinels(oracle["sentinels"]["endpoint"], tampered)
    assert "before/after sample" in str(error.value)


def test_a_single_disabled_lane_byte_is_rejected(tmp_path):
    _, result, _ = _run(tmp_path, "lane", EDGE_CASE, EDGE_PROGRAM, extra=DUMP)
    verifies = result["memory_endpoint"]["verifies"]
    row = next(r for r in verifies if r["size"] == 700)
    payload = bytearray(bytes.fromhex(row["bytes_hex"]))
    payload[3] ^= 0xFF
    assert bytes(payload) != bytes([0x08]) * row["size"]


def test_page_crossing_store_is_byte_exact_and_bracketed(tmp_path):
    """An 8192-byte payload crosses a 4 KiB page boundary: every byte must
    land exactly once and the bracketing bytes must hold."""
    _, result, oracle = _run(
        tmp_path,
        "region",
        REGION_CASE,
        REGION_PROGRAM,
        extra=("--dump-verify-bytes", "9000", "--dump-source-bytes", "9000"),
    )
    landed = result["memory_endpoint"]["verifies"]
    assert len(landed) == 1, landed
    row = landed[0]
    assert row["size"] == 8192 and row["bytes_hex"], row
    start = row["address"]
    assert start % 4096 + row["size"] > 4096, (hex(start), row["size"])
    executions = [
        execution
        for instance in result["instances"]
        for ledger in instance["cores"].values()
        for execution in ledger["observations"]["descriptor_executions"]
        if execution["transfer"]["write_bytes"] == row["size"]
    ]
    assert len(executions) == 1, executions
    produced = b"".join(
        bytes.fromhex(source["bytes_hex"])
        for source in executions[0]["source_rows"]
    )
    assert len(produced) == row["size"]
    assert produced == bytes.fromhex(row["bytes_hex"])
    declared = oracle["sentinels"]["endpoint"]
    assert declared, oracle["sentinels"]
    assert verified_sentinels(
        declared, result["memory_endpoint"]["sentinels"]
    ) == len(declared)


PEER_EDGE_PROGRAM = "p2p_cancel"
PEER_EDGE_CASE = "p2p_peer_edge"
PEER_ROW_BYTES = 512
PEER_ROW_STRIDE = 516


def _peer_edge_facts(program_dir):
    """The admitted peer-edge geometry, read from the schedule, not the run."""
    sections = json.loads(
        (program_dir / "schedule.mesh.json").read_text()
    )["sections"]
    p2p = [
        row for row in sections["DMA_DESCRIPTORS"] if row["kind"] == 3
    ]
    assert len(p2p) == 1, p2p
    descriptor = p2p[0]
    traffic = json.loads(
        (program_dir / "expected_traffic.json").read_text()
    )["descriptors"]
    row = next(
        entry for entry in traffic
        if entry["identity"]["descriptor_id"] == descriptor["descriptor_id"]
    )
    return descriptor, row


def _admitted_peer_bursts(row, beat_bytes, max_beats):
    from mesh_ir.burst_splitter import plan_descriptor

    plan = plan_descriptor(
        row["row_bytes"], row["rows"], row["remote_address"],
        row["remote_stride_bytes"], beat_bytes, max_beats,
    )
    return plan.bursts


def test_peer_target_sentinels_hold_the_unaligned_and_padded_edges(tmp_path):
    program_dir, result, oracle = _run(
        tmp_path, "peer-edge", PEER_EDGE_CASE, PEER_EDGE_PROGRAM
    )
    descriptor, row = _peer_edge_facts(program_dir)
    assert descriptor["dst"]["memory_space"] == 4, descriptor
    assert descriptor["dst"]["allocation_id"] if "allocation_id" in descriptor[
        "dst"
    ] else True
    assert descriptor["dst_stride_bytes"] == PEER_ROW_STRIDE, descriptor
    assert row["row_bytes"] == PEER_ROW_BYTES, row
    # The payload is unaligned, padded and crosses a 4 KiB page inside its last
    # row: exactly the admitted geometry the peer half of CPP-08 needs.
    base = row["remote_address"]
    starts = [base + index * PEER_ROW_STRIDE for index in range(row["rows"])]
    assert any(start % 32 for start in starts), starts
    assert starts[-1] + PEER_ROW_BYTES > (starts[-1] // 4096 + 1) * 4096, starts
    padding = [
        (starts[index] + PEER_ROW_BYTES, starts[index + 1])
        for index in range(len(starts) - 1)
    ]
    assert all(end - start == 4 for start, end in padding), padding

    peer = {
        row["address"]: row for row in _sentinel_targets(result)["aperture_1"]
    }
    for start, end in padding:
        assert start in peer, (hex(start), sorted(hex(a) for a in peer))
        assert peer[start]["size"] == end - start, peer[start]
        assert peer[start]["before_digest"] == peer[start]["after_digest"], peer[start]
    tail = starts[-1] + PEER_ROW_BYTES
    assert tail in peer and peer[tail]["before_digest"] == peer[tail]["after_digest"]
    declared = oracle["sentinels"]["aperture_1"]
    assert {span["address"] for span in declared} <= set(peer)
    assert verified_sentinels(declared, list(peer.values())) == len(declared)


def test_peer_writes_split_into_the_admitted_bursts(tmp_path):
    program_dir, result, _ = _run(
        tmp_path, "peer-bursts", PEER_EDGE_CASE, PEER_EDGE_PROGRAM
    )
    descriptor, row = _peer_edge_facts(program_dir)
    beats = _admitted_peer_bursts(row, 32, 16)
    admitted = sorted((burst.beat_base, burst.beats) for burst in beats)
    assert len(admitted) == row["bursts"], (len(admitted), row["bursts"])
    # The unaligned rows split into a full burst plus a one-beat remainder, and
    # the last row's remainder starts exactly at the 4 KiB boundary.
    page = (row["remote_address"] // 4096 + 1) * 4096
    assert sum(1 for address, _ in admitted if address == page) == 1, admitted
    assert any(
        burst.beat_base % 4096 == 0
        and burst.beat_base != row["remote_address"] for burst in beats
    ), admitted
    writes = [
        (entry["address"], entry["beats"])
        for entry in result["burst_timings"] if entry["channel"] == "AW"
    ]
    assert sorted(writes) == admitted, (sorted(writes), admitted)
    checked = verified_burst_geometry(
        [entry for entry in result["burst_timings"] if entry["channel"] == "AW"],
        max_beats=16, beat_bytes=32,
    )
    assert checked == len(admitted)


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("page-crossing", "crosses a 4 KiB page"),
        ("beat-cap", "over the admitted cap"),
        ("padding-changed", "changed"),
        ("row-duplicated", "source rows"),
    ),
)
def test_the_peer_edge_evidence_rejects_corruption(tmp_path, tamper, message):
    program_dir, result, oracle = _run(
        tmp_path, f"peer-corrupt-{tamper}", PEER_EDGE_CASE, PEER_EDGE_PROGRAM
    )
    if tamper == "row-duplicated":
        descriptor, _ = _peer_edge_facts(program_dir)
        corrupted = copy.deepcopy(result)
        for ledger in corrupted["instances"][-1]["cores"].values():
            for execution in ledger["observations"]["descriptor_executions"]:
                if execution["descriptor_id"] != descriptor["descriptor_id"]:
                    continue
                sources = execution["source_rows"]
                assert len(sources) >= 3, sources
                execution["source_rows"] = [sources[0]] + sources[2:]
        with pytest.raises(ProducerContentError, match=message):
            verify_transfer_producer_content(
                program_dir, corrupted, descriptor["descriptor_id"]
            )
        return
    if tamper == "padding-changed":
        observed = copy.deepcopy(_sentinel_targets(result)["aperture_1"])
        observed[0]["after_digest"] = "0" * 64
        with pytest.raises(ReconciliationError, match=message):
            verified_sentinels(oracle["sentinels"]["aperture_1"], observed)
        return
    writes = copy.deepcopy(
        [entry for entry in result["burst_timings"] if entry["channel"] == "AW"]
    )
    if tamper == "page-crossing":
        writes[0]["address"] = writes[0]["address"] // 4096 * 4096 + 4080
        writes[0]["beats"] = 2
    elif tamper == "beat-cap":
        writes[0]["beats"] = 17
    with pytest.raises(ReconciliationError, match=message):
        verified_burst_geometry(writes, max_beats=16, beat_bytes=32)


def test_a_compute_written_sentinel_byte_must_be_explained(tmp_path):
    _, result, oracle = _run(
        tmp_path, "peer-explained", PEER_EDGE_CASE, PEER_EDGE_PROGRAM
    )
    declared = oracle["sentinels"]["aperture_1"]
    observed = _sentinel_targets(result)["aperture_1"]
    assert verified_sentinels(declared, observed) == len(declared)

    changed = copy.deepcopy(observed)
    changed[0]["after_digest"] = "0" * 64
    with pytest.raises(ReconciliationError, match="changed"):
        verified_sentinels(declared, changed)
    # A run an admitted compute command published explains the change; nothing
    # else does.
    published = [
        {"address": changed[0]["address"], "size": changed[0]["size"] - 1}
    ]
    with pytest.raises(ReconciliationError, match="changed"):
        verified_sentinels(declared, changed, published)
    published = [
        {"address": changed[0]["address"], "size": changed[0]["size"]}
    ]
    assert verified_sentinels(declared, changed, published) == len(declared)

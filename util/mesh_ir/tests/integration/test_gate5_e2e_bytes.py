"""Gate 5 E2E byte contract: raw producer and destination bytes must agree.

The shared entry is enforced on a real two-instance run; every tamper is a
single edit of that archive and must be rejected for the stated reason.  A
missing raw byte image fails closed, so a digest can never stand in for the
bytes it summarises.
"""

import copy
import hashlib

import pytest

from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    payload_digest,
    verified_destination_bytes,
)

from tests.integration.support.garnet_harness import (
    build_program,
    cause_of,
    expected_traffic,
    output_of,
    result_of,
    run_garnet,
    schedule,
)

CASE = "dma_basic"
PROGRAM = "single"
INSTANCES = 2


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    work = tmp_path_factory.mktemp("gate5-bytes")
    program_dir = build_program(work, PROGRAM)
    run = run_garnet(
        work, "m5out", CASE, program_dir,
        extra_args=("--instances", str(INSTANCES)),
    )
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    assert "destination bytes: PASS" in output_of(run), output_of(run)
    return {"program_dir": program_dir, "result": result_of(program_dir)}


def _descriptors(program_dir):
    kinds = {
        row["descriptor_id"]: row["kind"]
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    return {
        row["identity"]["descriptor_id"]: {
            **row,
            **row["identity"],
            "kind": kinds[row["identity"]["descriptor_id"]],
        }
        for row in expected_traffic(program_dir)
    }


def _sources(program_dir):
    descriptors = {
        row["descriptor_id"]: row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    rows = {}
    for descriptor_id, row in _descriptors(program_dir).items():
        if row["kind"] not in (2, 3):
            continue
        descriptor = descriptors[descriptor_id]
        rows[descriptor_id] = [
            (row["src_address"] + index * descriptor["src_stride_bytes"],
             descriptor["row_bytes"])
            for index in range(descriptor["rows"])
        ]
    return rows


def _verify(baseline, frames=None, descriptors=None, sources=None):
    return verified_destination_bytes(
        baseline["result"]["instances"] if frames is None else frames,
        _descriptors(baseline["program_dir"]) if descriptors is None else descriptors,
        sources=_sources(baseline["program_dir"]) if sources is None else sources,
    )


def _writer_execution(frame, kind=2):
    for ledger in frame["cores"].values():
        for execution in ledger["observations"]["descriptor_executions"]:
            if execution["transfer"] and any(
                execution["transfer"].get(field)
                for field in ("write_bytes", "p2p_bytes")
            ) and kind == 2:
                return execution
    raise AssertionError("no writer execution in the frame")


def test_every_instance_destination_holds_its_own_producer_bytes(baseline):
    checked = _verify(baseline)
    assert checked == INSTANCES, checked
    for frame in baseline["result"]["instances"]:
        assert frame["destinations"], frame["instance"]
        for segment in frame["destinations"]:
            assert segment["bytes_hex"], segment
            assert len(segment["bytes_hex"]) == 2 * segment["size"], segment


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("destination-byte-changed", "holds"),
        ("destination-bytes-missing", "lost the raw destination bytes"),
        ("first-instance-source-missing", "lost the raw bytes of its producer"),
        ("destination-address-moved", "the admitted destination row is"),
        ("producer-address-moved", "the admitted source rows are"),
        ("source-row-dropped", "producer rows are"),
        ("raw-and-destination-consistent-but-sha-conflicts",
         "its archived row digest is"),
    ),
)
def test_the_byte_entry_rejects_corruption(baseline, tamper, message):
    frames = copy.deepcopy(baseline["result"]["instances"])
    first, second = frames[0], frames[1]
    if tamper == "destination-byte-changed":
        hexed = first["destinations"][0]["bytes_hex"]
        first["destinations"][0]["bytes_hex"] = (
            "00" if hexed[:2] != "00" else "ff"
        ) + hexed[2:]
    elif tamper == "destination-bytes-missing":
        first["destinations"][0]["bytes_hex"] = ""
    elif tamper == "first-instance-source-missing":
        for ledger in first["cores"].values():
            for execution in ledger["observations"]["descriptor_executions"]:
                for source in execution.get("source_rows") or ():
                    source["bytes_hex"] = None
    elif tamper == "destination-address-moved":
        first["destinations"][0]["address"] += 32
    elif tamper == "producer-address-moved":
        moved = False
        for ledger in first["cores"].values():
            for execution in ledger["observations"]["descriptor_executions"]:
                for source in execution.get("source_rows") or ():
                    source["address"] += 8192
                    moved = True
        assert moved
    elif tamper == "source-row-dropped":
        for ledger in first["cores"].values():
            for execution in ledger["observations"]["descriptor_executions"]:
                rows = execution.get("source_rows") or []
                if rows:
                    execution["source_rows"] = rows[:-1]
    elif tamper == "raw-and-destination-consistent-but-sha-conflicts":
        # The reported counterexample: the raw source bytes, the destination
        # raw bytes, the payload digest and the endpoint digest are all
        # rewritten to one consistent lie, while the row digest that the
        # producer-content chain pins stays untouched.
        execution = _writer_execution(first)
        source = execution["source_rows"][0]
        raw = bytearray(bytes.fromhex(source["bytes_hex"]))
        raw[0] ^= 0xFF
        source["bytes_hex"] = bytes(raw).hex()
        produced = bytes(raw) + b"".join(
            bytes.fromhex(row["bytes_hex"])
            for row in execution["source_rows"][1:]
        )
        execution["transfer"]["payload_digest"] = payload_digest(produced)
        segment = next(
            item for item in first["destinations"]
            if (item["command_id"], item["generation"], item["descriptor_id"])
            == (execution["command_id"], execution["generation"],
                execution["descriptor_id"])
            and item["row"] == 0
        )
        landed = bytearray(bytes.fromhex(segment["bytes_hex"]))
        landed[0] = raw[0]
        segment["bytes_hex"] = bytes(landed).hex()
        verify = next(
            row for row in
            baseline["result"]["memory_endpoint"]["verifies"]
            if row["address"] == segment["address"]
        )
        image = bytearray(bytes.fromhex(verify["bytes_hex"]))
        image[0] = raw[0]
        verify["bytes_hex"] = bytes(image).hex()
        verify["digest"] = hashlib.sha256(bytes(image)).hexdigest()
    with pytest.raises(ReconciliationError, match=message):
        _verify(baseline, frames=frames)

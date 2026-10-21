"""Gate 6 workload producer chain: every local source byte has a producer.

The healthy control is a real compiled ring program executed on the real
2x2 fabric.  Every tamper edits the archived result once and must be
rejected by the shared entry for the stated reason: a consumed source byte
without an admitted producer fact, a producer from the wrong op or
generation, or a borrowed stale producer at a reused address.
"""

import copy
import hashlib
import json
import shutil

import pytest

from mesh_ir.runtime_reconciliation import ReconciliationError

from tests.integration.support.garnet_harness import (
    output_of,
    result_of,
    run_garnet,
)
from tests.integration.support.runtime_harness import REPO

ARCH = REPO / "configs/example/ai_mesh/arch/mesh_2x2.yaml"


@pytest.fixture(scope="module")
def executed_ring(tmp_path_factory):
    from mesh_ir.compile_service import export_and_compile_factory
    work = tmp_path_factory.mktemp("producer-ring")
    program_dir = work / "program"
    export_and_compile_factory(
        "examples.tiny_mlp:create_model",
        REPO / "util/mesh_ir/examples/tiny_mlp_inputs.json",
        None, ARCH,
        REPO / "util/mesh_ir/examples/tiny_mlp_ring_tp4_compile.yaml",
        program_dir,
    )
    run = run_garnet(work, "m5out", "ring_workload", program_dir, arch=ARCH)
    assert run.returncode == 0, output_of(run)
    return program_dir, result_of(program_dir)


@pytest.fixture
def ring(tmp_path, executed_ring):
    source, result = executed_ring
    program_dir = tmp_path / "program"
    shutil.copytree(source, program_dir)
    return program_dir, copy.deepcopy(result)


def test_every_local_source_byte_has_an_admitted_producer(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    summary = verified_workload_producer_chain(program_dir, result, ARCH)
    assert summary["consumed_rows"] > 0, summary
    assert summary["uncovered"] == 0, summary


def _first_consumer_row(result):
    for frame in result["instances"]:
        for core_id, ledger in sorted(frame["cores"].items()):
            for execution in ledger["observations"][
                    "descriptor_executions"]:
                if execution.get("source_rows"):
                    return frame, core_id, execution
    raise AssertionError("no consumer source rows")


def test_a_deleted_producer_row_is_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    # Remove the archived producer run holding a consumed row's content:
    # compute output or landed source row, whichever carries the digest.
    frame, core_id, execution = _first_consumer_row(result)
    victim_digest = execution["source_rows"][0]["digest"]
    removed = False
    for ledger_core, ledger in frame["cores"].items():
        for output in ledger["observations"].get("compute_outputs", []):
            keep = [row for row in output.get("rows", [])
                    if row["digest"] != victim_digest or removed]
            if len(keep) != len(output.get("rows", [])):
                removed = True
            output["rows"] = keep
        for other in ledger["observations"]["descriptor_executions"]:
            source = other.get("source_rows")
            if not removed and source:
                keep = [row for row in source
                        if row["digest"] != victim_digest]
                if len(keep) != len(source):
                    other["source_rows"] = keep
                    removed = True
    assert removed, "tamper did not remove any producer row"
    # Deleting a landed producer row also deletes that execution's own read
    # evidence; whichever guard sees it first, the entry fails closed.
    with pytest.raises(ReconciliationError,
                       match="no admitted producer|archives no source rows"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_compute_command_labels_permuted_are_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    # Swap every core's compute output command IDs cyclically: the ID set
    # is unchanged, but each consumer's admitted wait closure now resolves
    # to runs carrying another command's bytes.
    for frame in result["instances"]:
        for core_id, ledger in frame["cores"].items():
            outputs = ledger["observations"].get("compute_outputs", [])
            ids = [row["command_id"] for row in outputs]
            if len(ids) > 1:
                shifted = ids[1:] + ids[:1]
                for row, new in zip(outputs, shifted):
                    row["command_id"] = new
    with pytest.raises(ReconciliationError,
                       match="no admitted producer|not one of its admitted "
                             "write"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_a_compute_output_offset_drift_is_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    frame = next(frame for frame in result["instances"]
                 if any(ledger["observations"].get("compute_outputs")
                        for ledger in frame["cores"].values()))
    for ledger in frame["cores"].values():
        outputs = ledger["observations"].get("compute_outputs", [])
        if outputs:
            outputs[0]["offset"] += 64
            break
    with pytest.raises(ReconciliationError, match="declares offset"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_cleared_source_rows_cannot_skip_the_check(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    for frame in result["instances"]:
        for ledger in frame["cores"].values():
            for execution in ledger["observations"][
                    "descriptor_executions"]:
                execution["source_rows"] = []
    with pytest.raises(ReconciliationError,
                       match="archives no source rows"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def _same_address_writer_pair(result):
    from mesh_ir.architecture import load_arch
    from mesh_ir.workload_producer import _sram_geometry
    """Two compute commands of one core whose overlapping writes carry
    different bytes at a segment some consumer really reads -- swapping
    their labels must misdescribe the current data version there."""
    for frame in result["instances"]:
        for core, ledger in frame["cores"].items():
            by_command = {}
            for output in ledger["observations"].get("compute_outputs", []):
                for row in output.get("rows", []):
                    raw = row.get("bytes_hex")
                    if isinstance(raw, str) and len(raw) == 2 * row["size"]:
                        by_command.setdefault(
                            output["command_id"], []).append(
                            (row["address"], row["address"] + row["size"],
                             bytes.fromhex(raw)))
            tile = _sram_geometry(load_arch(ARCH))
            base = tile[0] + int(core) * tile[1]
            consumed = [(row["address"] - base,
                         row["address"] - base + row["size"])
                        for execution in ledger["observations"][
                            "descriptor_executions"]
                        for row in execution.get("source_rows") or ()]
            commands = sorted(by_command)
            for index, first in enumerate(commands):
                for second in commands[index + 1:]:
                    for a_begin, a_end, a_data in by_command[first]:
                        for b_begin, b_end, b_data in by_command[second]:
                            lo, hi = max(a_begin, b_begin), min(a_end, b_end)
                            if lo >= hi:
                                continue
                            if a_data[lo - a_begin:hi - a_begin] == \
                                    b_data[lo - b_begin:hi - b_begin]:
                                continue
                            if any(row_lo < hi and lo < row_hi
                                   for row_lo, row_hi in consumed):
                                return core, first, second
    raise AssertionError("no consumed same-address writer pair with "
                         "different content in the carrier")


def test_same_address_writer_labels_swapped_are_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    sections = json.loads(
        (program_dir / "schedule.mesh.json").read_text())["sections"]
    verified_workload_producer_chain(program_dir, result, ARCH)
    core, first, second = _same_address_writer_pair(result)
    outputs = result["instances"][0]["cores"][core][
        "observations"]["compute_outputs"]
    pair = {row["command_id"]: row for row in outputs
            if row["command_id"] in (first, second)}
    pair[first]["command_id"], pair[second]["command_id"] = second, first
    # The swap relabels the current data version as the older writer: the
    # rows the consumers really read no longer match the claimed latest.
    with pytest.raises(ReconciliationError,
                       match="no admitted producer|not one of its admitted "
                             "write"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_a_compute_output_claiming_a_foreign_allocation_is_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    frame = result["instances"][0]
    for ledger in frame["cores"].values():
        outputs = ledger["observations"].get("compute_outputs", [])
        if outputs:
            victim = outputs[0]
            break
    others = {row["allocation_id"] for row in json.loads(
        (program_dir / "schedule.mesh.json").read_text())["sections"]
        ["ALLOCATIONS"]}
    victim["allocation_id"] = next(
        value for value in sorted(others)
        if value != victim["allocation_id"])
    with pytest.raises(ReconciliationError,
                       match="not one of its admitted write"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_a_duplicated_compute_output_record_is_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    frame = result["instances"][0]
    for ledger in frame["cores"].values():
        outputs = ledger["observations"].get("compute_outputs", [])
        if outputs:
            outputs.append(copy.deepcopy(outputs[0]))
            break
    with pytest.raises(ReconciliationError, match="second generation"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_a_borrowed_stale_producer_digest_is_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    # Relabel one consumer source row with a digest archived elsewhere: the
    # row's own bytes no longer hash to the claimed digest.
    frame, core_id, execution = _first_consumer_row(result)
    row = execution["source_rows"][0]
    borrowed = None
    for ledger in frame["cores"].values():
        for output in ledger["observations"].get("compute_outputs", []):
            for other in output.get("rows", []):
                if other["digest"] != row["digest"]:
                    borrowed = other["digest"]
    assert borrowed, "archive carries no second digest to borrow"
    row["digest"] = borrowed
    with pytest.raises(ReconciliationError, match="do not match its digest"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_a_wrong_generation_producer_is_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    frame, core_id, execution = _first_consumer_row(result)
    execution["generation"] = execution.get("generation", 0) + 7
    with pytest.raises(ReconciliationError, match="generation"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_a_shifted_destination_landing_is_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    # Shift the admitted landing address of every peer push: no projected
    # producer run remains where the receivers really read their bytes.
    traffic_path = program_dir / "expected_traffic.json"
    traffic = json.loads(traffic_path.read_text())
    pushed = [row for row in traffic["descriptors"]
              if row["identity"]["direction"] == "P2P"]
    assert pushed, "ring archive carries no peer push"
    for victim in pushed:
        victim["dst_address"] += 64
    traffic_path.write_text(json.dumps(traffic))
    # The landing-extent anchor sees the moved destination before the
    # coverage check does; both are the same lie.
    with pytest.raises(ReconciliationError,
                       match="no admitted producer|outside every admitted "
                             "write allocation"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_source_and_destination_tampered_together_are_rejected(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    # Forge one consumer source row completely: flipped bytes and the
    # matching digest, so the row is self-consistent but no admitted
    # producer ever wrote those bytes.
    frame, core_id, execution = _first_consumer_row(result)
    row = execution["source_rows"][0]
    raw = bytearray(bytes.fromhex(row["bytes_hex"]))
    if not raw:
        pytest.skip("carrier archives no raw consumer bytes")
    raw[0] ^= 0xFF
    row["bytes_hex"] = raw.hex()
    row["digest"] = hashlib.sha256(bytes(raw)).hexdigest()
    with pytest.raises(ReconciliationError, match="no admitted producer"):
        verified_workload_producer_chain(program_dir, result, ARCH)


def test_compute_cannot_claim_allocation_padding_as_a_write(ring):
    from mesh_ir.workload_producer import verified_workload_producer_chain
    program_dir, result = ring
    verified_workload_producer_chain(program_dir, result, ARCH)
    sections = json.loads((program_dir / 'schedule.mesh.json').read_text())['sections']
    allocations = {row['allocation_id']: row for row in sections['ALLOCATIONS']}
    victim = None
    for ledger in result['instances'][0]['cores'].values():
        for output in ledger['observations']['compute_outputs']:
            if not output['rows']:
                continue
            allocation = allocations[output['allocation_id']]
            end = max(row['address'] + row['size'] for row in output['rows'])
            if end < allocation['offset_bytes'] + allocation['size_bytes']:
                victim = output
                break
        if victim is not None:
            break
    assert victim is not None
    end = max(row['address'] + row['size'] for row in victim['rows'])
    victim['rows'].append({'address': end, 'size': 1, 'bytes_hex': '00',
                          'digest': hashlib.sha256(b'\0').hexdigest()})
    with pytest.raises(ReconciliationError, match='admitted write access'):
        verified_workload_producer_chain(program_dir, result, ARCH)

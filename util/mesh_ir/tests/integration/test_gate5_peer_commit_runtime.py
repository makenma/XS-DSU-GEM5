"""Gate 5 real staged P2P: peer coverage, unique publish and replayed commits.

A transfer may only be released by its own distinct accepted transactions
covering every admitted byte.  The replay cases inject real duplicate
observer notifications at the target delivery boundary and assert that
coverage neither grows nor completes earlier.
"""

import copy
import hashlib
import json

import pytest

from mesh_ir.fault_plan import execution_keys, resolve_fault_plan
from mesh_ir.generated import abi as A
from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    admitted_transfer_destination,
    fill_pattern_bytes,
    verified_transfer_publishes,
    verified_transfer_snapshots,
)

from tests.integration.support import garnet_harness
from tests.integration.support.garnet_harness import (
    build_program,
    cause_of,
    expected_traffic,
    output_of,
    result_of,
    run_garnet,
    schedule,
)

SHAPES = "dma_shapes"
REUSE = "p2p_reuse"
PERSIST = "p2p_persist"
PARTIAL = "p2p_partial_abandon"


def _arch():
    return {
        "core_ids": [0, 1],
        "axi_data_bytes": 32,
        "axi_max_burst_beats": 16,
        "region_bases": [0x800000000, 0x100000000, 0x400000000],
        "region_tile_strides": [0, 0, 0x400000],
    }


def _run(tmp_path, name, case, program, extra=()):
    program_dir = build_program(tmp_path, program)
    run = run_garnet(tmp_path, f"m5out-{name}", case, program_dir,
                     extra_args=tuple(extra))
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    return program_dir, result_of(program_dir)


def _admitted_transfer_bursts(program_dir):
    admitted = {
        row["descriptor_id"]: row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    traffic = json.loads((program_dir / "expected_traffic.json").read_text())
    totals = {}
    for row in traffic["descriptors"]:
        descriptor_id = row["identity"]["descriptor_id"]
        descriptor = admitted[descriptor_id]
        if descriptor["kind"] != 3:
            continue
        transfer_id = descriptor["transfer_id"]
        totals[transfer_id] = totals.get(transfer_id, 0) + row["bursts"]
    return totals


def _p2p_first_write_fact(program_dir, descriptor_id):
    facts = execution_keys(program_dir, _arch(), {0: 0, 1: 1})[0]
    matches = [
        fact for fact in facts
        if fact["direction"] == "write" and fact["descriptor_id"] == descriptor_id
    ]
    assert matches, descriptor_id
    return matches[0]


def _p2p_first_write_uid(program_dir, descriptor_id):
    return _p2p_first_write_fact(program_dir, descriptor_id)["uid"]


def _apertures(result, instance_id=None):
    """One archived instance frame's per-core peer coverage."""
    frames = [
        (record["instance"], record["apertures"]) for record in result["instances"]
    ]
    assert frames, "the result archived no instance frame"
    if instance_id is not None:
        frames = [frame for frame in frames if frame[0] == instance_id]
        assert len(frames) == 1, instance_id
    return frames[-1][1]


def _p2p_descriptor(program_dir, kind=3, index=0):
    rows = [
        row for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["kind"] == kind
    ]
    assert len(rows) > index, rows
    return rows[index]


def test_healthy_multi_burst_transfer_is_published_once(tmp_path):
    program_dir, result = _run(tmp_path, "healthy", SHAPES, SHAPES)
    wanted = _admitted_transfer_bursts(program_dir)
    published = verified_transfer_publishes(_apertures(result), wanted)
    assert len(published) == 1
    row = next(r for r in _apertures(result)[1]["transfers"] if r["notified"])
    assert row["expected_bytes"] == 128, row
    assert row["covered_bytes"] == 128, row
    assert row["transactions"] == wanted[row["transfer_id"]] == 2, row
    assert row["duplicate_notifications"] == 0, row


@pytest.mark.parametrize("count", (1, 8))
def test_replayed_commit_notification_cannot_extend_coverage(tmp_path, count):
    # Each run publishes its own program directory, so the two archived
    # results are genuinely independent.
    healthy_dir = tmp_path / f"healthy{count}"
    healthy_dir.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(healthy_dir, SHAPES)
    descriptor = _p2p_descriptor(program_dir)
    uid = _p2p_first_write_uid(program_dir, descriptor["descriptor_id"])
    healthy = run_garnet(healthy_dir, "m5out", SHAPES, program_dir)
    assert healthy.returncode == 0, output_of(healthy)

    replayed_dir = tmp_path / f"replayed{count}"
    replayed_dir.mkdir(parents=True, exist_ok=True)
    replayed_program = build_program(replayed_dir, SHAPES)
    replayed = run_garnet(
        replayed_dir, "m5out", SHAPES, replayed_program,
        extra_args=("--replay-write-commit", f"11:{uid}:{count}"),
    )
    assert replayed.returncode == 0, output_of(replayed)
    assert "p2p unique publish: PASS" in output_of(replayed), output_of(replayed)

    healthy_row = next(
        row for row in _apertures(result_of(program_dir))[1]["transfers"]
        if row["notified"]
    )
    wanted = _admitted_transfer_bursts(replayed_program)
    replayed_result = result_of(replayed_program)
    verified_transfer_publishes(_apertures(replayed_result), wanted)
    replayed_row = next(
        row for row in _apertures(replayed_result)[1]["transfers"]
        if row["notified"]
    )
    assert replayed_row["covered_bytes"] == 128, replayed_row
    assert replayed_row["transactions"] == 2, replayed_row
    assert replayed_row["duplicate_notifications"] == 64 * count, replayed_row
    # The replay must not release the transfer earlier than the healthy run.
    assert replayed_row["commit_tick"] == healthy_row["commit_tick"], (
        healthy_row,
        replayed_row,
    )


def test_reused_range_transfers_each_cover_their_own_bytes(tmp_path):
    program_dir, result = _run(tmp_path, "reuse", REUSE, REUSE)
    wanted = _admitted_transfer_bursts(program_dir)
    assert len(wanted) == 2, wanted
    rows = {row["transfer_id"]: row for row in _apertures(result)[1]["transfers"]}
    assert sorted(rows) == sorted(wanted), rows
    for transfer_id, row in rows.items():
        assert row["notified"] and row["covered_bytes"] == row["expected_bytes"], row
        assert row["transactions"] == 1, row
        assert row["duplicate_notifications"] == 0, row
    published = verified_transfer_publishes(_apertures(result), wanted)
    assert len(set(published.values())) == 2, published


def test_reused_range_transfers_have_disjoint_transaction_identities(tmp_path):
    """G5-R23-01: each reused-range transfer names its own transactions."""
    program_dir, result = _run(tmp_path, "reuse-identity", REUSE, REUSE)
    wanted = _admitted_transfer_bursts(program_dir)
    frames = _apertures(result)
    published = verified_transfer_publishes(frames, wanted, refused_replays={})
    rows = {
        row["transfer_id"]: row
        for aperture in frames if aperture["transfers"]
        for row in aperture["transfers"]
    }
    assert sorted(rows) == sorted(wanted), rows
    seen = set()
    for transfer_id, row in rows.items():
        assert row["transaction_uids"], row
        assert len(row["transaction_uids"]) == row["transactions"] == 1, row
        assert not (seen & set(row["transaction_uids"])), (seen, row)
        seen |= set(row["transaction_uids"])
        assert row["replayed_lanes"] == 0, row
    assert len(published) == 2, published


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("borrowed-identity", "attributed to transfer"),
        ("identity-dropped", "distinct transaction identities"),
        ("identity-duplicated", "distinct transaction identities"),
        ("uninjected-refusal", "injected stale delivery"),
    ),
)
def test_the_transfer_identity_entry_rejects_corruption(tmp_path, tamper, message):
    """G5-R23-01: the shared entry refuses forged transaction ownership."""
    program_dir, result = _run(tmp_path, f"reuse-{tamper}", REUSE, REUSE)
    wanted = _admitted_transfer_bursts(program_dir)
    tampered = copy.deepcopy(_apertures(result))
    transfers = next(
        aperture["transfers"] for aperture in tampered if aperture["transfers"]
    )
    assert len(transfers) == 2, transfers
    if tamper == "borrowed-identity":
        transfers[1]["transaction_uids"] = list(transfers[0]["transaction_uids"])
    elif tamper == "identity-dropped":
        transfers[0]["transaction_uids"] = []
    elif tamper == "identity-duplicated":
        transfers[0]["transaction_uids"] = (
            list(transfers[0]["transaction_uids"]) * 2
        )
    elif tamper == "uninjected-refusal":
        transfers[1]["replayed_lanes"] = 16
    with pytest.raises(ReconciliationError, match=message):
        verified_transfer_publishes(tampered, wanted, refused_replays={})


def test_a_replayed_retired_commit_cannot_release_a_reused_range(tmp_path):
    """G5-R23-01: the owner refuses a stale delivery at the real boundary."""
    program_dir = build_program(tmp_path, REUSE)
    p2p = [
        row for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["kind"] == 3
    ]
    assert len(p2p) == 2, p2p
    stale_transfer = p2p[0]["transfer_id"]
    reused_transfer = p2p[1]["transfer_id"]
    fact = _p2p_first_write_fact(program_dir, p2p[0]["descriptor_id"])
    run = run_garnet(
        tmp_path, "m5out-stale-replay", REUSE, program_dir,
        extra_args=("--replay-peer-commit", "%d:0" % stale_transfer),
    )
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    assert "p2p reuse commits: PASS" in output_of(run), output_of(run)
    result = result_of(program_dir)
    wanted = _admitted_transfer_bursts(program_dir)
    frames = _apertures(result)
    rows = {
        row["transfer_id"]: row
        for aperture in frames if aperture["transfers"]
        for row in aperture["transfers"]
    }
    assert sorted(rows) == sorted(wanted), rows
    stale, reused = rows[stale_transfer], rows[reused_transfer]
    assert stale["replayed_lanes"] == 0, stale
    assert reused["replayed_lanes"] == fact["useful_bytes"], reused
    assert reused["notified"] and reused["covered_bytes"] == reused["expected_bytes"], reused
    assert fact["uid"] not in reused["transaction_uids"], reused
    assert stale["transaction_uids"] == [fact["uid"]], stale
    published = verified_transfer_publishes(
        frames, wanted, refused_replays={reused_transfer: fact["useful_bytes"]}
    )
    assert sorted(published) == sorted(wanted), published


def test_multi_instance_transfer_publishes_once_per_instance(tmp_path):
    program_dir, result = _run(tmp_path, "persist", PERSIST, "dual")
    wanted = _admitted_transfer_bursts(program_dir)
    verified_transfer_publishes(_apertures(result), wanted)
    records = [
        row
        for instance in result["instances"]
        for core_id, ledger in instance["cores"].items()
        for row in ledger["observations"]["transfers"]
    ]
    assert len(records) == len(result["instances"]), records
    for row in records:
        assert row["sender_notifications"] == 1, row
        assert row["committed_bytes"] == row["expected_bytes"], row


def test_uncovered_transfer_publish_is_rejected(tmp_path):
    program_dir, result = _run(tmp_path, "reject", SHAPES, SHAPES)
    wanted = _admitted_transfer_bursts(program_dir)
    tampered = copy.deepcopy(_apertures(result))
    for aperture in tampered:
        for row in aperture["transfers"]:
            if row["notified"]:
                row["covered_bytes"] -= 64
                row["uncovered_bytes"] += 64
    with pytest.raises(ReconciliationError) as error:
        verified_transfer_publishes(tampered, wanted)
    assert "admitted bytes covered" in str(error.value)


def test_unretired_expectation_in_a_retired_aperture_is_rejected(tmp_path):
    program_dir, result = _run(tmp_path, "unretired", REUSE, REUSE)
    wanted = _admitted_transfer_bursts(program_dir)
    tampered = copy.deepcopy(_apertures(result))
    tampered[1]["transfers"][0]["notified"] = False
    # A later aperture makes the tampered one non-final: its expectations must
    # have retired with its instance.
    tampered.append({"core_id": 0, "transfers": []})
    with pytest.raises(ReconciliationError) as error:
        verified_transfer_publishes(tampered, wanted)
    assert "unretired expectation" in str(error.value)


def test_transfer_without_an_admitted_descriptor_is_rejected(tmp_path):
    _, result = _run(tmp_path, "foreign", SHAPES, SHAPES)
    with pytest.raises(ReconciliationError) as error:
        verified_transfer_publishes(_apertures(result), {4242: 1})
    assert "without an admitted descriptor" in str(error.value)


def _staged(tmp_path, name):
    program_dir, result = _run(tmp_path, name, SHAPES, SHAPES)
    rows = [row for row in _apertures(result)[1]["transfers"] if row["notified"]]
    assert len(rows) == 1, rows
    return program_dir, rows[0]


def test_staged_commit_stages_never_publish_early(tmp_path):
    program_dir, row = _staged(tmp_path, "stages")
    wanted = _admitted_transfer_bursts(program_dir)
    stages = row["stages"]
    assert len(stages) == wanted[row["transfer_id"]] == 2, stages
    assert stages[0]["covered_bytes"] < row["expected_bytes"], stages
    assert stages[0]["uncovered_bytes"] > 0, stages
    assert [stage["notified"] for stage in stages] == [False, True], stages
    covered = [stage["covered_bytes"] for stage in stages]
    assert covered == sorted(set(covered)), covered
    assert stages[-1]["covered_bytes"] == row["expected_bytes"], stages


def test_long_transfer_composes_every_stage_before_publishing(tmp_path):
    program_dir, result = _run(tmp_path, "stages-long", "p2p_delayed", "dual")
    wanted = _admitted_transfer_bursts(program_dir)
    rows = [row for row in _apertures(result)[1]["transfers"] if row["notified"]]
    assert len(rows) == 1, rows
    stages = rows[0]["stages"]
    assert len(stages) == wanted[rows[0]["transfer_id"]] == 32, len(stages)
    assert sum(1 for stage in stages if stage["notified"]) == 1, stages
    assert all(not stage["notified"] for stage in stages[:-1]), stages
    assert all(stage["uncovered_bytes"] > 0 for stage in stages[:-1]), stages


def _p2p_basic(tmp_path, name, fault=None):
    work = tmp_path / name
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, "dual")
    extra = ("--receiver-fault", fault) if fault else ()
    extra = (*extra, "--watchdog-ticks", "3000000") if fault else extra
    run = run_garnet(work, "m5out", "p2p_basic", program_dir, extra_args=extra)
    return run, program_dir


def test_duplicate_receiver_notification_is_idempotent(tmp_path):
    healthy_run, healthy_dir = _p2p_basic(tmp_path, "healthy")
    duplicate_run, duplicate_dir = _p2p_basic(
        tmp_path, "duplicate", fault="receive_duplicate"
    )
    assert healthy_run.returncode == 0, output_of(healthy_run)
    assert duplicate_run.returncode == 0, output_of(duplicate_run)
    assert "p2p unique publish: PASS" in output_of(duplicate_run)
    assert "staged commit stages: PASS" in output_of(duplicate_run)
    # The receiver's own delivery record is where a replayed delivery shows up;
    # everything the replay could have corrupted must be byte-identical.
    assert [
        row["receiver_notifications"]
        for row in _receiver_deliveries(result_of(healthy_dir))
    ] == [1]
    assert [
        row["receiver_notifications"]
        for row in _receiver_deliveries(result_of(duplicate_dir))
    ] == [2]
    assert json.dumps(
        _without_receiver_deliveries(result_of(healthy_dir)), sort_keys=True
    ) == json.dumps(
        _without_receiver_deliveries(result_of(duplicate_dir)), sort_keys=True
    )


def _receiver_deliveries(result):
    return [
        row
        for instance in result["instances"]
        for ledger in instance["cores"].values()
        for row in ledger["observations"]["transfer_commits"]
    ]


def _without_receiver_deliveries(result):
    trimmed = copy.deepcopy(result)
    for row in _receiver_deliveries(trimmed):
        row["receiver_notifications"] = 0
        row["receiver_notification_tick"] = 0
    return trimmed


@pytest.mark.parametrize("fault", ("receive_redirect", "receive_drop"))
def test_a_lost_receiver_notification_fails_closed(tmp_path, fault):
    run, program_dir = _p2p_basic(tmp_path, fault, fault=fault)
    output = output_of(run)
    assert run.returncode != 0, output
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert "MESH_PROGRAM_DONE" not in output, output
    assert "MESH_PROGRAM_ERROR_DRAINED" not in output, output
    assert not (program_dir / "actual_result.json").exists()


def _partial(tmp_path, name):
    """Run the real carrier that loses one burst of an admitted P2P plan.

    Returns the archived result together with the partial facts resolved from
    the admitted burst plan, so the expected numbers never come from the run.
    """
    work = tmp_path / name
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, "dual")
    admitted = {
        row["descriptor_id"]: row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    rows = {
        row["identity"]["descriptor_id"]: {
            **row,
            **row["identity"],
            "kind": admitted[row["identity"]["descriptor_id"]]["kind"],
        }
        for row in expected_traffic(program_dir)
    }
    writes = [
        fact
        for fact in execution_keys(program_dir, _arch(), {0: 0, 1: 1})[0]
        if fact["direction"] == "write"
    ]
    (descriptor_id,), _, models = resolve_fault_plan(
        program_dir, _arch(),
        [{"target": 11, "uid": writes[8]["uid"], "resp": "slverr"}],
        rows,
    )
    transfer_id = admitted[descriptor_id]["transfer_id"]
    bursts = models[descriptor_id]["burst_useful_bytes"]
    expected = {
        "covered_bytes": sum(bursts) - bursts[models[descriptor_id]["failed_burst"]],
        "transactions": len(bursts) - 1,
    }
    run = run_garnet(work, "m5out", PARTIAL, program_dir)
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_ERROR_DRAINED"), output_of(run)
    return program_dir, result_of(program_dir), descriptor_id, transfer_id, expected


def _sender_rows(result, transfer_id=None):
    return [
        row
        for instance in result["instances"]
        for ledger in instance["cores"].values()
        for row in ledger["observations"]["transfers"]
        if transfer_id is None or row["transfer_id"] == transfer_id
    ]


def _contributions(result, descriptor_id):
    return [
        execution
        for instance in result["instances"]
        for ledger in instance["cores"].values()
        for execution in ledger["observations"]["descriptor_executions"]
        if execution["descriptor_id"] == descriptor_id
    ]


def _abandoned_row(result, transfer_id):
    rows = [
        row
        for aperture in _apertures(result)
        for row in aperture["transfers"]
        if row["transfer_id"] == transfer_id
    ]
    assert len(rows) == 1, rows
    return rows[0]


def test_a_lost_peer_burst_keeps_the_real_partial_coverage(tmp_path):
    program_dir, result, descriptor_id, transfer_id, expected = _partial(
        tmp_path, "partial"
    )
    row = _abandoned_row(result, transfer_id)
    assert row["abandoned"] is True and row["notified"] is False, row
    assert row["covered_bytes"] == expected["covered_bytes"], row
    assert row["uncovered_bytes"] == row["expected_bytes"] - expected["covered_bytes"], row
    assert 0 < row["covered_bytes"] < row["expected_bytes"], row
    assert row["transactions"] == expected["transactions"], row
    assert row["duplicate_notifications"] == 0, row
    assert row["stages"] and not any(
        stage["notified"] for stage in row["stages"]
    ), row["stages"]

    admitted = _admitted_transfer_bursts(program_dir)
    assert row["expected_bytes"] == admitted[transfer_id] * (
        _arch()["axi_data_bytes"] * _arch()["axi_max_burst_beats"]
    ), row

    contributions = _contributions(result, descriptor_id)
    assert len(contributions) == 1, contributions
    moved = contributions[0]["transfer"]
    assert moved["p2p_bytes"] == expected["covered_bytes"], moved
    assert moved["p2p_bursts"] == expected["transactions"], moved
    assert not any(
        moved[field] for field in ("read_bytes", "write_bytes", "fill_bytes")
    ), moved

    senders = _sender_rows(result, transfer_id)
    assert len(senders) == 1, senders
    sender = senders[0]
    assert sender["failed"] is True and sender["sender_published"] is False, sender
    assert sender["sender_notifications"] == 0, sender
    assert sender["committed_descriptors"] == 0, sender
    assert sender["expected_descriptors"] == 1, sender

    published = verified_transfer_publishes(
        _apertures(result), admitted, abandoned={transfer_id: contributions},
        sender_rows=senders,
    )
    assert transfer_id not in published, published


def test_a_partially_committed_transfer_is_never_released(tmp_path):
    program_dir, result, descriptor_id, transfer_id, _ = _partial(
        tmp_path, "partial-reject"
    )
    wanted = _admitted_transfer_bursts(program_dir)
    senders = _sender_rows(result, transfer_id)
    contributions = _contributions(result, descriptor_id)
    verified_transfer_publishes(
        _apertures(result), wanted, abandoned={transfer_id: contributions},
        sender_rows=senders,
    )

    def run_with(apertures=_apertures(result), mapping=None, rows=senders):
        return verified_transfer_publishes(
            apertures, wanted,
            abandoned={transfer_id: contributions} if mapping is None else mapping,
            sender_rows=rows,
        )

    with pytest.raises(ReconciliationError, match="without an admitted lost burst"):
        run_with(mapping={})

    hidden = copy.deepcopy(_abandoned_row(result, transfer_id))
    hidden["abandoned"] = False
    with pytest.raises(ReconciliationError,
                       match="differ from the ones the admitted plan lost"):
        run_with(apertures=[{"core_id": 0, "transfers": []},
                            {"core_id": 1, "transfers": [hidden]}])

    released = copy.deepcopy(_abandoned_row(result, transfer_id))
    released["notified"] = True
    with pytest.raises(ReconciliationError, match="released anyway"):
        run_with(apertures=[{"core_id": 0, "transfers": []},
                            {"core_id": 1, "transfers": [released]}])

    short = copy.deepcopy(_abandoned_row(result, transfer_id))
    short["covered_bytes"] -= 512
    short["uncovered_bytes"] += 512
    with pytest.raises(ReconciliationError,
                       match="landed .* bytes but the source moved"):
        run_with(apertures=[{"core_id": 0, "transfers": []},
                            {"core_id": 1, "transfers": [short]}])

    short_stage = copy.deepcopy(_abandoned_row(result, transfer_id))
    short_stage["transactions"] -= 1
    # The identity list has to shrink with the count, so the tamper reaches the
    # rule it targets instead of the identity-completeness rule.
    short_stage["transaction_uids"] = short_stage["transaction_uids"][:-1]
    with pytest.raises(ReconciliationError, match="the source moved .* bursts"):
        run_with(apertures=[{"core_id": 0, "transfers": []},
                            {"core_id": 1, "transfers": [short_stage]}])

    lied = copy.deepcopy(contributions)
    lied[0]["transfer"]["p2p_bytes"] += 512
    with pytest.raises(ReconciliationError, match="moved .* of .* admitted bytes"):
        run_with(mapping={transfer_id: lied})

    released_sender = [dict(sender) for sender in senders]
    released_sender[0]["sender_published"] = True
    with pytest.raises(ReconciliationError, match="the sender released"):
        run_with(rows=released_sender)


PREFILLED = "p2p_prefilled_destination"
PREFILLED_HEALTHY = "p2p_prefilled"
PREFILLED_INCOMPLETE = "p2p_prefilled_incomplete"


def _prefilled_facts(program_dir):
    """The admitted destination facts of the prefilled carrier's one transfer."""
    admitted = {
        row["descriptor_id"]: row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    transfers = {}
    for row in admitted.values():
        if row["kind"] == 3:
            transfers.setdefault(row["transfer_id"], []).append(row["descriptor_id"])
    assert len(transfers) == 1, transfers
    transfer_id, descriptor_ids = next(iter(transfers.items()))
    source_core = admitted[descriptor_ids[0]]["src"]["owner_core"]
    target_core, allocations = admitted_transfer_destination(
        schedule(program_dir), descriptor_ids
    )
    prefill = next(
        row for row in admitted.values()
        if row["kind"] == 5 and row["dst"]["owner_core"] == target_core
    )
    attrs_by_index = {
        index + 1: attr
        for index, attr in enumerate(schedule(program_dir)["OP_ATTRS"])
    }
    commands = schedule(program_dir)["COMMANDS"]

    def initial_bytes(descriptor_id):
        row = admitted[descriptor_id]
        return fill_pattern_bytes(
            attrs_by_index, commands, prefill["command_id"],
            row["row_bytes"] * row["rows"],
        )

    from mesh_ir.architecture import load_arch
    from mesh_ir.runtime_reconciliation import admitted_write_identities
    manifest = load_arch(garnet_harness.ARCH)
    arch = {
        "core_ids": list(manifest.core_ids),
        "region_bases": [region.base for region in manifest.regions],
        "region_tile_strides": [
            region.tile_stride if region.tile_stride else 0
            for region in manifest.regions
        ],
        "axi_data_bytes": manifest.axi_data_bytes,
        "axi_max_burst_beats": manifest.axi_max_burst_beats,
    }
    traffic_rows = expected_traffic(program_dir)
    kinds = {
        row["descriptor_id"]: row["kind"]
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    descriptors = {
        row["identity"]["descriptor_id"]: {
            **row, **row["identity"],
            "kind": kinds[row["identity"]["descriptor_id"]],
        }
        for row in traffic_rows
    }
    identities = admitted_write_identities(
        program_dir, descriptors, arch=arch, src_nodes={0: 0, 1: 1},
        instances=1)
    return {
        "transfer_id": transfer_id,
        "descriptor_ids": descriptor_ids,
        "geometry": admitted,
        "command_id": admitted[descriptor_ids[0]]["command_id"],
        "source_core": source_core,
        "target_core": target_core,
        "allocations": allocations,
        "prefill_command_id": prefill["command_id"],
        "initial_bytes": initial_bytes,
        "identities": identities,
    }


def _snapshot_frame(result, facts):
    frame = result["instances"][0]["cores"]
    sender = frame[str(facts["source_core"])]
    receiver = frame[str(facts["target_core"])]
    rows = [
        row for row in sender["observations"]["transfer_commits"]
        if row["transfer_id"] == facts["transfer_id"]
    ]
    assert rows, sender["observations"]["transfer_commits"]
    assert not frame[str(facts["target_core"])]["observations"]["transfer_commits"]
    return rows, sender, receiver


def _landing_facts(result, facts):
    """The transfer's raw landing inputs: aperture row, instance, admitted
    write identities and the frame's bursts, plus each pending descriptor's
    span layout, destination read-back and producer bytes."""
    frame = result["instances"][0]
    sender = frame["cores"][str(facts["source_core"])]["observations"]
    aperture = next(
        entry for entry in frame["apertures"]
        if entry["core_id"] == facts["target_core"]
    )
    transfer = next(
        row for row in aperture["transfers"]
        if row["transfer_id"] == facts["transfer_id"]
    )
    pending = [
        execution["descriptor_id"]
        for execution in sender["descriptor_executions"]
        if execution["committed"] is False
        and execution["descriptor_id"] in facts["descriptor_ids"]
    ]
    spans = {}
    for descriptor_id in pending:
        images = [
            image for image in frame["destinations"]
            if image["descriptor_id"] == descriptor_id
        ]
        layout = []
        destination = b""
        offset = 0
        for image in images:
            destination += bytes.fromhex(image["bytes_hex"] or "")
            layout.append((offset, image["address"], image["size"]))
            offset += image["size"]
        execution = next(
            (entry for entry in sender["descriptor_executions"]
             if entry["descriptor_id"] == descriptor_id), None)
        producer = None
        if execution is not None and execution.get("source_rows"):
            rows = execution["source_rows"]
            if all(row.get("bytes_hex") for row in rows):
                producer = b"".join(
                    bytes.fromhex(row["bytes_hex"]) for row in rows)
        spans[descriptor_id] = {
            "layout": layout, "destination": destination or None,
            "producer": producer,
        }
    return {
        "transfer": transfer,
        "instance": frame["instance"],
        "identities": facts["identities"],
        "bursts": [row for row in frame.get("bursts", ())],
        "spans": spans,
    }


def _verify_snapshots(facts, rows, sender, completed, landing=None):
    executions_by_descriptor = {}
    for row in sender["observations"]["descriptor_executions"]:
        executions_by_descriptor.setdefault(row["descriptor_id"], []).append(
            row)
    for rows_of in executions_by_descriptor.values():
        rows_of.sort(key=lambda row: row["generation"])
    return verified_transfer_snapshots(
        rows,
        admitted=facts["descriptor_ids"],
        geometry=facts["geometry"],
        executions=executions_by_descriptor,
        target_core=facts["target_core"],
        admitted_allocations=facts["allocations"],
        completed=completed,
        initial_bytes=facts["initial_bytes"],
        resident=True,
        landing=landing,
    )


def _prefilled_run(tmp_path, name, case):
    work = tmp_path / name
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, PREFILLED)
    run = run_garnet(work, "m5out", case, program_dir)
    assert run.returncode == 0, output_of(run)
    return program_dir, run, result_of(program_dir)


def test_a_resident_destination_is_not_the_transfers_completion(tmp_path):
    program_dir, run, result = _prefilled_run(
        tmp_path, "prefilled", PREFILLED_HEALTHY
    )
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    facts = _prefilled_facts(program_dir)
    rows, sender, _ = _snapshot_frame(result, facts)
    assert len(rows) == len(facts["descriptor_ids"]), rows
    _verify_snapshots(
        facts, rows, sender, completed=True,
        landing=_landing_facts(result, facts),
    )

    pending = []
    for row in rows:
        assert row["receiver_present"] is True, row
        assert row["receiver_core"] == facts["target_core"], row
        assert row["receiver_admitted_allocations"] == facts["allocations"], row
        assert row["receiver_allocation_valid"] is True, row
        assert row["source_digest"] == row["target_digest"] != "", row
        assert row["target_digest"] != hashlib.sha256(
            facts["initial_bytes"](row["descriptor_id"])
        ).hexdigest(), row
        pending.append(row["pending_bytes"])
    # A resident destination is only completed by this transfer's own commits.
    assert pending[-1] == 0 and pending[0] > 0, pending
    assert pending == sorted(pending, reverse=True) and len(set(pending)) == len(pending), pending
    assert not any(row["receiver_transfer_committed"] for row in rows[:-1]), rows
    assert not any(row["sender_published"] for row in rows[:-1]), rows
    assert not any(row["receiver_notifications"] for row in rows[:-1]), rows
    assert not any(row["receiver_notified_allocation_id"] for row in rows[:-1]), rows
    assert rows[-1]["receiver_transfer_committed"] is True, rows[-1]
    assert rows[-1]["receiver_notifications"] == 1, rows[-1]
    assert rows[-1]["sender_published"] is True, rows[-1]

    # The resident fact has a real cause: the target's own prefill retired
    # before this transfer's first commit.
    prefill = [
        execution
        for execution in _snapshot_frame(result, facts)[2][
            "observations"
        ]["descriptor_executions"]
        if facts["geometry"][execution["descriptor_id"]]["command_id"]
        == facts["prefill_command_id"]
    ]
    assert prefill and all(row["committed"] for row in prefill), prefill
    assert max(row["commit_tick"] for row in prefill) < rows[0]["commit_tick"], (
        prefill,
        rows[0],
    )


def test_an_incomplete_transfer_on_a_resident_destination_stays_blocked(tmp_path):
    program_dir, run, result = _prefilled_run(
        tmp_path, "prefilled-incomplete", PREFILLED_INCOMPLETE
    )
    assert cause_of(run).startswith("MESH_PROGRAM_ERROR_DRAINED"), output_of(run)
    facts = _prefilled_facts(program_dir)
    rows, sender, receiver = _snapshot_frame(result, facts)
    _verify_snapshots(
        facts, rows, sender, completed=False,
        landing=_landing_facts(result, facts),
    )

    committed = {row["descriptor_id"] for row in rows}
    assert 0 < len(committed) < len(facts["descriptor_ids"]), committed
    assert not any(row["sender_published"] for row in rows), rows
    assert not any(row["receiver_transfer_committed"] for row in rows), rows
    assert not any(row["receiver_notifications"] for row in rows), rows
    # Every row still reports the destination the earlier producer made
    # resident, and the pending set is exactly the descriptors that did not
    # commit, holding their admitted initial content.
    last = rows[-1]
    assert last["receiver_allocation_valid"] is True, last
    expected_pending = [
        descriptor_id for descriptor_id in facts["descriptor_ids"]
        if descriptor_id not in committed
    ]
    assert [span["descriptor_id"] for span in last["pending_spans"]] == expected_pending
    assert last["pending_bytes"] == sum(
        facts["geometry"][descriptor_id]["row_bytes"]
        * facts["geometry"][descriptor_id]["rows"]
        for descriptor_id in expected_pending
    ), last
    cut_short = {
        execution["descriptor_id"]
        for execution in sender["observations"]["descriptor_executions"]
        if not execution["committed"]
    }
    assert cut_short and cut_short <= set(expected_pending), cut_short
    for span in last["pending_spans"]:
        initial = hashlib.sha256(
            facts["initial_bytes"](span["descriptor_id"])
        ).hexdigest()
        if span["descriptor_id"] in cut_short:
            assert span["digest"] != initial, span
        else:
            assert span["digest"] == initial, span

    recv = next(
        command for command in schedule(program_dir)["COMMANDS"]
        if command["core_id"] == facts["target_core"]
        and command["opcode"] == int(A.OPCODE.RECV_WAIT)
    )
    receiver_terminals = {
        row["command_id"]: row["state"] for row in receiver["terminals"]
    }
    assert receiver_terminals[recv["command_id"]] == "cancelled", receiver_terminals


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("failed-span-zeroed", "had reached by then"),
        ("landed-byte-changed", "landed producer byte"),
        ("unwritten-byte-changed", "admitted initial byte"),
        ("range-sample-missing", "destination read-back"),
        ("producer-missing", "has no producer bytes"),
        ("early-snapshot-borrows-final-landing", "had reached by then"),
        ("middle-snapshot-borrows-later-burst", "had reached by then"),
    ),
)
def test_a_partial_failure_destination_is_split_and_checked(
    tmp_path, tamper, message
):
    """G5-R23-04: the cut-short destination is split into what really landed and
    what still holds the admitted initial content; neither is exempt, and a
    snapshot may only name the landing state its events had reached by its own
    tick."""
    program_dir, _, result = _prefilled_run(
        tmp_path, f"partial-corrupt-{tamper}", PREFILLED_INCOMPLETE
    )
    facts = _prefilled_facts(program_dir)
    rows, sender, _ = _snapshot_frame(result, facts)
    corrupted_rows = copy.deepcopy(rows)
    landing = _landing_facts(result, facts)
    transfer = landing["transfer"]
    pending = sorted(landing["spans"])
    assert pending, landing["spans"]

    def span_events(descriptor_id, before=None):
        projected = []
        for event in transfer["landing_events"]:
            if before is not None and event["tick"] > before:
                continue
            ranges = []
            for run in event["ranges"]:
                for offset, address, size in landing["spans"][descriptor_id][
                        "layout"]:
                    begin = max(run["address"], address)
                    finish = min(run["address"] + run["size"],
                                 address + size)
                    if begin < finish:
                        ranges.append((offset + begin - address,
                                       finish - begin))
            if ranges:
                projected.append((event["tick"], tuple(ranges)))
        return projected

    def covered_span(descriptor_id, before=None):
        covered = set()
        for _tick, ranges in span_events(descriptor_id, before):
            for offset, size in ranges:
                covered.update(range(offset, offset + size))
        return covered

    if tamper == "unwritten-byte-changed":
        entry = next(
            descriptor_id for descriptor_id in pending
            if len(covered_span(descriptor_id))
            < len(facts["initial_bytes"](descriptor_id))
        )
    else:
        entry = pending[0]
    frame = result["instances"][0]
    middle = corrupted_rows[1]
    span = next(
        item for item in middle["pending_spans"]
        if item["descriptor_id"] == entry
    )
    image = next(
        item for item in frame["destinations"]
        if item["descriptor_id"] == entry
    )
    if tamper == "failed-span-zeroed":
        span["digest"] = "0" * 64
        middle["pending_digest"] = "0" * 64
    elif tamper in ("landed-byte-changed", "unwritten-byte-changed"):
        landed = [
            range_pair
            for _tick, ranges in span_events(entry)
            for range_pair in ranges
        ]
        assert landed, entry
        covered = covered_span(entry)
        if tamper == "landed-byte-changed":
            index = sorted(covered)[0]
        else:
            index = next(
                index
                for index in range(len(facts["initial_bytes"](entry)))
                if index not in covered
            )
        # locate the row image that holds this span offset
        target = image
        seen = 0
        for candidate in frame["destinations"]:
            if candidate["descriptor_id"] != entry:
                continue
            width = candidate["size"]
            if seen <= index < seen + width:
                target = candidate
                break
            seen += width
        local = index - seen
        hexed = target["bytes_hex"]
        replacement = "00" if hexed[2 * local:2 * local + 2] != "00" else "ff"
        target["bytes_hex"] = (
            hexed[:2 * local] + replacement + hexed[2 * local + 2:]
        )
    elif tamper == "range-sample-missing":
        frame["destinations"].remove(image)
    elif tamper == "producer-missing":
        for execution in sender["observations"]["descriptor_executions"]:
            if execution["descriptor_id"] == entry:
                execution["source_rows"] = []
    elif tamper in ("early-snapshot-borrows-final-landing",
                    "middle-snapshot-borrows-later-burst"):
        # The reported counterexample family: a snapshot names a landing state
        # its own events had not reached at the row's tick.  The final row's
        # span digest is the final partial landing; the earliest row may only
        # name the state reached by its own tick.
        events = span_events(entry)
        assert events, entry
        if tamper == "early-snapshot-borrows-final-landing":
            victim_row = next(
                row for row in corrupted_rows
                if row["commit_tick"] < min(tick for tick, _r in events)
                and any(item["descriptor_id"] == entry
                        for item in row["pending_spans"])
            )
        else:
            ticks = sorted({tick for tick, _r in events})
            victim_row = next(
                row for row in corrupted_rows
                if row["commit_tick"] < ticks[-1]
                and covered_span(entry, before=row["commit_tick"])
                != covered_span(entry)
                and any(item["descriptor_id"] == entry
                        for item in row["pending_spans"])
            )
        last = corrupted_rows[-1]
        final_digest = next(
            item["digest"] for item in last["pending_spans"]
            if item["descriptor_id"] == entry
        )
        victim_span = next(
            item for item in victim_row["pending_spans"]
            if item["descriptor_id"] == entry
        )
        original = victim_span["digest"]
        victim_span["digest"] = final_digest
        assert original != final_digest

        def span_state_at(descriptor_id, tick):
            initial = facts["initial_bytes"](descriptor_id)
            producer = landing["spans"][descriptor_id]["producer"]
            state = bytearray(initial)
            for event_tick, ranges in span_events(descriptor_id, tick):
                for offset, size in ranges:
                    state[offset:offset + size] = \
                        producer[offset:offset + size]
            return bytes(state)

        content = b""
        for item in victim_row["pending_spans"]:
            if item["descriptor_id"] not in landing["spans"]:
                content += facts["initial_bytes"](item["descriptor_id"])
            elif item is victim_span:
                final_tick = max(tick for tick, _r in span_events(entry))
                content += span_state_at(entry, final_tick)
            else:
                content += span_state_at(
                    item["descriptor_id"], victim_row["commit_tick"])
        victim_row["pending_digest"] = hashlib.sha256(content).hexdigest()
    with pytest.raises(ReconciliationError, match=message):
        _verify_snapshots(
            facts, corrupted_rows, sender, completed=False,
            landing=_landing_facts(result, facts),
        )


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("foreign-uid", "but its ranges are the landing of"),
        ("all-uids-zeroed", "but its ranges are the landing of"),
        ("duplicated-uid", "coverage stages"),
        ("event-before-own-request", "before its request handshake"),
        ("completed-uids-swapped", "but its ranges are the landing of"),
        ("completed-before-own-request", "before its request handshake"),
        ("completed-event-deleted", "coverage stages"),
        ("completed-event-ranges-emptied", "with no ranges"),
        ("completed-event-range-shortened", "landing events account for"),
        ("completed-landing-after-own-response",
         "after its own write response retired"),
        ("completed-landing-after-own-but-before-later-response",
         "after its own write response retired"),
    ),
)
def test_landing_events_carry_their_transaction_identity(
        tmp_path, tamper, message):
    """G5-R23-04/M18-03/04: a landing event -- of a pending or of a completed
    descriptor alike -- is only explained by the single-use transaction whose
    accepted burst carries its bytes.  Every tamper mutates the raw result and
    replays it through the same product entry."""
    program_dir, _, result = _prefilled_run(
        tmp_path, "event-identity-%s" % tamper, PREFILLED_INCOMPLETE
    )
    facts = _prefilled_facts(program_dir)
    rows, sender, _ = _snapshot_frame(result, facts)
    landing = _landing_facts(result, facts)
    transfer = landing["transfer"]

    def owner_burst_of(event):
        for descriptor_id in facts["descriptor_ids"]:
            for owner in facts["identities"].get(
                    (descriptor_id, landing["instance"]), ()):
                span = (owner["address"],
                        owner["address"] + owner["useful_bytes"])
                if all(span[0] <= run["address"]
                       and run["address"] + run["size"] <= span[1]
                       for run in event["ranges"]):
                    return next(
                        row for row in landing["bursts"]
                        if row["channel"] == "AW"
                        and row["descriptor_id"] == descriptor_id
                        and row["burst_index"] == owner["burst_index"])
        raise AssertionError("no owner for %r" % event)

    def owner_aw_of(event):
        return owner_burst_of(event)["ar_aw_tick"]

    events = sorted(transfer["landing_events"],
                    key=lambda row: (row["tick"], row["txn_uid"]))
    assert events
    pending_spans = landing["spans"]

    def event_on_pending_span(event):
        for span in pending_spans.values():
            for _offset, address, size in span["layout"]:
                for run in event["ranges"]:
                    if max(run["address"], address) < min(
                            run["address"] + run["size"], address + size):
                        return True
        return False

    if tamper == "foreign-uid":
        event = next(row for row in events if event_on_pending_span(row))
        event["txn_uid"] = 0xDEADBEEF
    elif tamper == "all-uids-zeroed":
        for event in events:
            event["txn_uid"] = 0
    elif tamper == "duplicated-uid":
        transfer["landing_events"] = [
            *transfer["landing_events"], copy.deepcopy(events[0])]
    elif tamper == "event-before-own-request":
        event = next(row for row in events if event_on_pending_span(row))
        event["tick"] = owner_aw_of(event) - 1
        transfer["stages"][
            transfer["landing_events"].index(event)]["tick"] = event["tick"]
    elif tamper == "completed-uids-swapped":
        completed = [row for row in events if not event_on_pending_span(row)]
        first, second = completed[0], completed[1]
        first["txn_uid"], second["txn_uid"] = \
            second["txn_uid"], first["txn_uid"]
    elif tamper == "completed-event-deleted":
        event = next(row for row in events if not event_on_pending_span(row))
        transfer["landing_events"].remove(event)
    elif tamper == "completed-event-ranges-emptied":
        event = next(row for row in events if not event_on_pending_span(row))
        event["ranges"] = []
    elif tamper == "completed-event-range-shortened":
        event = next(row for row in events if not event_on_pending_span(row))
        event["ranges"][0]["size"] -= 1
    elif tamper in ("completed-landing-after-own-response",
                    "completed-landing-after-own-but-before-later-response"):
        completed = [
            row for row in events if not event_on_pending_span(row)]
        responses = {
            owner_burst_of(row)["response_tick"] for row in completed}
        if tamper == "completed-landing-after-own-response":
            event = max(completed,
                        key=lambda row: owner_burst_of(row)["response_tick"])
        else:
            event = min(completed,
                        key=lambda row: owner_burst_of(row)["response_tick"])
            assert owner_burst_of(event)["response_tick"] < max(responses), \
                completed
        burst = owner_burst_of(event)
        assert burst["response_tick"] > 0
        forged = burst["response_tick"] + 1
        event_index = transfer["landing_events"].index(event)
        stage = transfer["stages"][event_index]
        event["tick"] = forged
        stage["tick"] = forged
    else:
        completed = [row for row in events if not event_on_pending_span(row)]
        event = completed[0]
        event["tick"] = owner_aw_of(event) - 1
        transfer["stages"][
            transfer["landing_events"].index(event)]["tick"] = event["tick"]
    with pytest.raises(ReconciliationError, match=message):
        _verify_snapshots(
            facts, rows, sender, completed=False, landing=landing)


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("early-completion", "reports the receiver transfer committed"),
        ("resident-cleared", "destination valid=False"),
        ("pending-span-dropped", "pending spans"),
        ("pending-shrunk", "pending bytes"),
        ("sender-released", "sender_published=True"),
        ("initial-content-changed", "does not hold its admitted initial content"),
    ),
)
def test_the_resident_destination_contract_rejects_corruption(
    tmp_path, tamper, message
):
    program_dir, _, result = _prefilled_run(
        tmp_path, f"prefilled-corrupt-{tamper}", PREFILLED_INCOMPLETE
    )
    facts = _prefilled_facts(program_dir)
    rows, sender, _ = _snapshot_frame(result, facts)
    corrupted = copy.deepcopy(rows)
    middle = corrupted[1]
    if tamper == "early-completion":
        middle.update(receiver_transfer_committed=True, receiver_notifications=1)
    elif tamper == "resident-cleared":
        middle["receiver_allocation_valid"] = False
    elif tamper == "pending-span-dropped":
        middle["pending_spans"] = middle["pending_spans"][1:]
    elif tamper == "pending-shrunk":
        middle["pending_bytes"] -= 4
    elif tamper == "sender-released":
        middle["sender_published"] = True
    elif tamper == "initial-content-changed":
        untouched = next(
            span for span in middle["pending_spans"]
            if span["descriptor_id"]
            not in {
                row["descriptor_id"] for row in sender["observations"][
                    "descriptor_executions"
                ] if not row["committed"]
            }
        )
        untouched["digest"] = "0" * 64
    with pytest.raises(ReconciliationError, match=message):
        _verify_snapshots(
            facts, corrupted, sender, completed=False,
            landing=_landing_facts(result, facts),
        )

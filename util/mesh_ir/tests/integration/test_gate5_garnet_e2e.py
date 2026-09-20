"""Gate 5 real AXI/Garnet E2E: admitted programs run end to end over the fabric.

Every assertion reads the archived instance frames of the real run; the shared
reconciliation entry is invoked by the runner itself, so these tests check the
per-frame facts the runner cannot summarise away.
"""

import copy
import hashlib
import json

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.compute_timing import (
    ComputeTimingError,
    admitted_engine_plan,
    compute_cycles,
    verify_engine_timing,
)
from mesh_ir.fault_plan import execution_keys
from mesh_ir.producer_content import (
    ProducerContentError,
    verify_transfer_producer_content,
)
from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    verified_store_destinations,
    verified_transfer_publishes,
)

from tests.integration.support.garnet_harness import (
    ARCH,
    build_program,
    expected_traffic,
    cause_of,
    output_of,
    result_of,
    run_garnet,
    schedule,
)

PROGRAM = "single"
CASE = "dma_basic"
EDGE_CASE = "region_edge"
EDGE_PROGRAM = "region_edge"

TERMINAL_STATES = ("completed", "errored", "cancelled")


def _run(tmp_path, instances=1, extra=()):
    program_dir = build_program(tmp_path, PROGRAM)
    arguments = ["--dump-verify-bytes", "9000", "--dump-source-bytes", "9000"]
    if instances != 1:
        arguments += ["--instances", str(instances)]
    run = run_garnet(
        tmp_path, f"m5out-{instances}", CASE, program_dir, extra_args=(*arguments, *extra)
    )
    assert run.returncode == 0, output_of(run)
    assert "MESH_PROGRAM_DONE" in output_of(run), output_of(run)
    return program_dir, result_of(program_dir)


def _frame(result, instance):
    frames = [row for row in result["instances"] if row["instance"] == instance]
    assert len(frames) == 1, [row["instance"] for row in result["instances"]]
    return frames[0]


def _executions(frame, core_id=0):
    return frame["cores"][str(core_id)]["observations"]["descriptor_executions"]


def _admitted_descriptors(program_dir):
    return {
        row["descriptor_id"]: row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }


def _terminal_partition(frame, core_id, plan):
    ledger = frame["cores"][str(core_id)]
    seen = {}
    for record in ledger["terminals"]:
        assert record["state"] in TERMINAL_STATES, record
        key = (record["command_id"], record["generation"])
        assert key not in seen, (frame["instance"], key)
        seen[key] = record["state"]
    assert set(seen) == plan, (frame["instance"], sorted(plan - set(seen)))
    return seen


@pytest.mark.parametrize("instances", (1, 2, 3))
def test_e2e1_every_instance_frame_carries_its_own_executions(tmp_path, instances):
    program_dir, result = _run(tmp_path, instances)
    admitted = _admitted_descriptors(program_dir)

    for expected_instance in range(1, instances + 1):
        frame = _frame(result, expected_instance)
        core = frame["cores"]["0"]
        plan = {
            (command_id, generation)
            for command_id, generation in result["cores"][0]["dispatch_plan"]
        }
        _terminal_partition(frame, 0, plan)

        executions = _executions(frame)
        keys = [
            (row["command_id"], row["generation"], row["descriptor_id"])
            for row in executions
        ]
        assert len(set(keys)) == len(keys), keys
        executing_commands = {
            (row["command_id"], row["generation"]) for row in executions
        }
        assert executing_commands <= plan, executing_commands - plan
        for row in executions:
            descriptor = admitted[row["descriptor_id"]]
            assert row["command_id"] == descriptor["command_id"], row
            assert row["completed"] and row["committed"], row
            assert row["status"] == "OK", row
            assert row["commit_tick"] >= row["submit_tick"], row
            assert row["completion_tick"] >= row["commit_tick"], row
        # The frame's command observations own the plan independently of the
        # execution records.
        observed = {
            (row["command_id"], row["generation"])
            for row in core["observations"]["commands"]
        }
        assert observed == plan, observed ^ plan


def test_e2e1_source_bytes_reach_the_destination_byte_for_byte(tmp_path):
    _, result = _run(tmp_path)
    frame = _frame(result, 1)
    executions = {row["descriptor_id"]: row for row in _executions(frame)}
    store = next(row for row in executions.values() if row["descriptor_id"] == 3)

    source_rows = store["source_rows"]
    assert source_rows, store
    verifies = result["memory_endpoint"]["verifies"]
    assert len(verifies) == 1, verifies
    destination = verifies[0]
    assert destination["bytes_hex"], destination

    rebuilt = bytearray()
    for row in source_rows:
        assert row["bytes_hex"], row
        payload = bytes.fromhex(row["bytes_hex"])
        assert len(payload) == row["size"], row
        assert hashlib.sha256(payload).hexdigest() == row["digest"], row
        rebuilt += payload
    landed = bytes.fromhex(destination["bytes_hex"])
    assert len(landed) == destination["size"] == len(rebuilt)
    assert landed == bytes(rebuilt), "the STORE destination differs from the "
    "bytes the DMA read from the producer allocation"


def test_e2e1_store_bytes_are_identical_across_instances(tmp_path):
    _, result = _run(tmp_path, instances=3)
    digests = []
    for instance in (1, 2, 3):
        frame = _frame(result, instance)
        store = next(
            row for row in _executions(frame) if row["descriptor_id"] == 3
        )
        digests.append(
            hashlib.sha256(
                b"".join(bytes.fromhex(row["bytes_hex"]) for row in store["source_rows"])
            ).hexdigest()
        )
    assert len(set(digests)) == 1, digests
    assert result["memory_endpoint"]["verifies"][0]["digest"]


def test_e2e1_region_edge_round_trip_reconciles(tmp_path):
    program_dir = build_program(tmp_path, EDGE_PROGRAM)
    run = run_garnet(
        tmp_path,
        "m5out-edge",
        EDGE_CASE,
        program_dir,
        extra_args=("--dump-verify-bytes", "9000", "--dump-source-bytes", "9000"),
    )
    assert run.returncode == 0, output_of(run)
    assert "MESH_PROGRAM_DONE" in cause_of(run), output_of(run)
    result = result_of(program_dir)
    frame = _frame(result, 1)
    executions = _executions(frame)
    assert executions, "no descriptor executions were archived"
    for row in executions:
        assert row["committed"] and row["completion_tick"] is not None, row
    verifies = result["memory_endpoint"]["verifies"]
    assert len(verifies) == 1, verifies
    landed = verifies[0]
    assert landed["size"] == 8192 and landed["bytes_hex"], landed
    stores = [
        row for row in executions if row["transfer"]["write_bytes"] == landed["size"]
    ]
    assert len(stores) == 1, stores
    read = b"".join(
        bytes.fromhex(row["bytes_hex"]) for row in stores[0]["source_rows"]
    )
    assert read == bytes.fromhex(landed["bytes_hex"])


DUAL_PROGRAM = "dual"
FRAMES_CASE = "p2p_frames"
CORE_SOURCE = 0
CORE_TARGET = 1


def _load_program(program_dir):
    import json as _json

    sections = _json.loads(
        (program_dir / "schedule.mesh.json").read_text()
    )["sections"]
    return sections, decode_program((program_dir / "program.mshb").read_bytes())


def _arch():
    manifest = load_arch(ARCH)
    return {
        "core_ids": list(manifest.core_ids),
        "axi_data_bytes": manifest.axi_data_bytes,
        "axi_max_burst_beats": manifest.axi_max_burst_beats,
        "region_bases": [region.base for region in manifest.regions],
        "region_tile_strides": [
            region.tile_stride if region.tile_stride else 0
            for region in manifest.regions
        ],
    }


def _transfer_bursts(program_dir):
    """transfer_id -> bursts the admitted P2P descriptors really accept."""
    admitted = _admitted_descriptors(program_dir)
    totals = {}
    for fact in execution_keys(program_dir, _arch(), {0: 0, 1: 1})[0]:
        descriptor = admitted[fact["descriptor_id"]]
        if fact["direction"] != "write" or descriptor["kind"] != 3:
            continue
        transfer_id = descriptor["transfer_id"]
        totals[transfer_id] = totals.get(transfer_id, 0) + 1
    return totals


def _run_frames(tmp_path, instances):
    program_dir = build_program(tmp_path, DUAL_PROGRAM)
    arguments = ["--dump-verify-bytes", "9000", "--dump-source-bytes", "9000"]
    if instances != 1:
        arguments += ["--instances", str(instances)]
    run = run_garnet(
        tmp_path, f"m5out-frames-{instances}", FRAMES_CASE, program_dir,
        extra_args=tuple(arguments),
    )
    assert run.returncode == 0, output_of(run)
    assert "MESH_PROGRAM_DONE" in output_of(run), output_of(run)
    return program_dir, result_of(program_dir)


def _frame_target(apertures):
    return next(row for row in apertures if row["core_id"] == CORE_TARGET)


@pytest.mark.parametrize("instances", (1, 2, 3))
def test_e2e2_every_frame_carries_its_own_peer_evidence(tmp_path, instances):
    program_dir, result = _run_frames(tmp_path, instances)
    wanted = _transfer_bursts(program_dir)
    assert wanted, "no admitted P2P transfer"
    frames = result["instances"]
    assert [row["instance"] for row in frames] == list(range(1, instances + 1))
    for frame in frames:
        apertures = frame["apertures"]
        assert [row["core_id"] for row in apertures] == [CORE_SOURCE, CORE_TARGET]
        coverage = {
            row["transfer_id"]: row for row in _frame_target(apertures)["transfers"]
        }
        assert sorted(coverage) == sorted(wanted), coverage
        for transfer_id, row in coverage.items():
            assert row["notified"] and not row["abandoned"], row
            assert row["covered_bytes"] == row["expected_bytes"], row
            assert row["uncovered_bytes"] == 0, row
            assert row["duplicate_notifications"] == 0, row
            assert row["transactions"] == wanted[transfer_id], row
            assert len(row["stages"]) == wanted[transfer_id], row
            assert [stage["notified"] for stage in row["stages"]] == (
                [False] * (len(row["stages"]) - 1) + [True]
            ), row["stages"]
            assert row["commit_tick"] > 0, row
        # The frame's own sender record and the shared publish contract.
        rows = frame["cores"][str(CORE_SOURCE)]["observations"]["transfer_commits"]
        assert rows, frame["instance"]
        for row in rows:
            assert row["sender_published"] is True, row
            assert row["sender_notifications"] == 1, row
            assert row["committed_bytes"] == row["expected_bytes"], row
            assert row["source_digest"] == row["target_digest"] != "", row
        published = verified_transfer_publishes(apertures, wanted)
        assert sorted(published) == sorted(wanted), published


@pytest.mark.parametrize("instances", (1, 2, 3))
def test_e2e2_each_frame_chains_its_producer_to_the_destination(tmp_path, instances):
    program_dir, result = _run_frames(tmp_path, instances)
    admitted = _admitted_descriptors(program_dir)
    local_source = sorted(
        descriptor_id for descriptor_id, row in admitted.items()
        if row["kind"] in (2, 3)
    )
    assert local_source, "no admitted local-source descriptor"
    for descriptor_id in local_source:
        # The shared entry checks every instance frame on its own.
        verify_transfer_producer_content(program_dir, result, descriptor_id)
        for frame in result["instances"]:
            summary = verify_transfer_producer_content(
                program_dir, result, descriptor_id,
                instance_id=frame["instance"],
            )
            assert summary["instances"], summary
    p2p = [row for row in admitted.values() if row["kind"] == 3]
    for frame in result["instances"]:
        for descriptor in p2p:
            executions = [
                row for row in _executions(frame, CORE_SOURCE)
                if row["descriptor_id"] == descriptor["descriptor_id"]
            ]
            assert len(executions) == 1, executions
            assert executions[0]["source_rows"], executions[0]
            assert executions[0]["transfer"]["p2p_bytes"] > 0, executions[0]


def test_e2e2_frame_evidence_is_independent_between_instances(tmp_path):
    program_dir, result = _run_frames(tmp_path, 2)
    wanted = _transfer_bursts(program_dir)
    first, second = result["instances"]
    verified_transfer_publishes(first["apertures"], wanted)
    verified_transfer_publishes(second["apertures"], wanted)

    # A frame that lost coverage is rejected on its own, whatever the later
    # frame observed: the evidence is per frame, never cumulative.
    corrupted = copy.deepcopy(first)
    for row in _frame_target(corrupted["apertures"])["transfers"]:
        row["covered_bytes"] -= 512
        row["uncovered_bytes"] += 512
    with pytest.raises(ReconciliationError, match="admitted bytes covered"):
        verified_transfer_publishes(corrupted["apertures"], wanted)
    verified_transfer_publishes(second["apertures"], wanted)

    # A frame whose peer record kept an expectation is rejected too.
    unretired = copy.deepcopy(second)
    _frame_target(unretired["apertures"])["transfers"][0]["notified"] = False
    with pytest.raises(ReconciliationError, match="unretired expectation"):
        verified_transfer_publishes(unretired["apertures"], wanted)

    # The second frame's source evidence is its own: pairing it with the first
    # frame's producer content is refused by the shared entry.
    tempered = copy.deepcopy(result)
    rows = tempered["instances"][1]["cores"][str(CORE_SOURCE)][
        "observations"
    ]["descriptor_executions"]
    for row in rows:
        if row["descriptor_id"] == sorted(
            did for did, admitted in _admitted_descriptors(program_dir).items()
            if admitted["kind"] == 3
        )[0]:
            row["source_rows"][0]["digest"] = "0" * 64
    with pytest.raises(ProducerContentError):
        verify_transfer_producer_content(
            program_dir, tempered,
            [did for did, admitted in _admitted_descriptors(program_dir).items()
             if admitted["kind"] == 3][0],
            instance_id=2,
        )


COMPUTE_CASES = {"dma_basic": "single", "p2p_frames": "dual"}


def _compute_run(tmp_path, case, instances):
    work = tmp_path / f"{case}-compute-{instances}"
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, COMPUTE_CASES[case])
    arguments = ["--instances", str(instances)] if instances != 1 else []
    run = run_garnet(work, "m5out", case, program_dir, extra_args=tuple(arguments))
    assert run.returncode == 0, output_of(run)
    assert "MESH_PROGRAM_DONE" in output_of(run), output_of(run)
    return program_dir, result_of(program_dir)


@pytest.mark.parametrize("case", sorted(COMPUTE_CASES))
@pytest.mark.parametrize("instances", (1, 2, 3))
def test_admitted_compute_cycles_are_the_archived_engine_cycles(
    tmp_path, case, instances
):
    program_dir, result = _compute_run(tmp_path, case, instances)
    summary = verify_engine_timing(
        program_dir, load_arch(ARCH), result, instances
    )
    assert summary["commands"] == instances * sum(
        len(commands) for commands in admitted_engine_plan(
            program_dir, load_arch(ARCH)
        ).values()
    ), summary
    # Every archived plan carries the admitted analytic cycle count, and the
    # per-core cumulative engine statistics are its admitted sum.
    for frame in result["instances"]:
        for ledger in frame["cores"].values():
            for row in ledger["observations"]["engine_executions"]:
                assert row["cycles"] > 0, row
                assert row["ended"] and row["end_tick"] >= row["begin_tick"], row
    for core in result["cores"]:
        assert core["gemm_cycles"] % instances == 0, core


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("cycles-changed", "the admitted .* work is"),
        ("end-tick-moved", "admitted plan is"),
        ("engine-swapped", "instead of the admitted"),
        ("never-ended", "never ended its engine execution"),
        ("plan-dropped", "admitted engine plans"),
        ("statistics-changed", "the admitted plan over"),
    ),
)
def test_the_compute_timing_oracle_rejects_a_tampered_engine_plan(
    tmp_path, tamper, message
):
    program_dir, result = _compute_run(tmp_path, "p2p_frames", 2)
    corrupted = copy.deepcopy(result)
    frames = corrupted["instances"]
    if tamper == "plan-dropped":
        ledger = frames[0]["cores"]["0"]
        ledger["observations"]["engine_executions"] = [
            row for row in ledger["observations"]["engine_executions"]
            if row["command_id"] != 4
        ]
    else:
        for frame in frames:
            for ledger in frame["cores"].values():
                for row in ledger["observations"]["engine_executions"]:
                    if tamper == "cycles-changed":
                        row["cycles"] += 1
                    elif tamper == "end-tick-moved":
                        row["end_tick"] += 1
                    elif tamper == "engine-swapped":
                        row["engine"] = "vector" if row["engine"] != "vector" else "reduce"
                    elif tamper == "never-ended":
                        row["ended"] = False
    if tamper == "statistics-changed":
        corrupted["cores"][0]["gemm_cycles"] += 1
    with pytest.raises(ComputeTimingError, match=message):
        verify_engine_timing(program_dir, load_arch(ARCH), corrupted, 2)


def test_the_compute_model_is_sensitive_to_the_admitted_work(tmp_path):
    program_dir, _ = _compute_run(tmp_path, "dma_basic", 1)
    _, program = _load_program(program_dir)
    arch = load_arch(ARCH)
    gemm = next(
        command for command in program.commands
        if command.command_id == 4
    )
    attr = program.op_attrs[gemm.attr_index - 1]
    cycles = compute_cycles(arch, "GEMM", attr)
    assert cycles == 1036, cycles
    # Halving the admitted efficiency doubles the engine's own work while the
    # setup and flush stay put: the model reads the attribute, not a constant.
    halved = type(attr)(
        attr.kind, attr.reserved,
        tuple(65536 // 2 if field == "efficiency_q16" else value
              for field, value in zip(attr.payload_fields, attr.payload)),
        attr.payload_fields,
    )
    assert compute_cycles(arch, "GEMM", halved) == (
        arch.tensor_setup_cycles + 2 * (cycles - arch.tensor_setup_cycles
                                        - arch.tensor_pipeline_flush_cycles)
        + arch.tensor_pipeline_flush_cycles
    )


def _store_rows(program_dir):
    kinds = {
        row["descriptor_id"]: row["kind"]
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }
    return [
        {**row, **row["identity"], "kind": kinds[row["identity"]["descriptor_id"]]}
        for row in expected_traffic(program_dir)
        if kinds[row["identity"]["descriptor_id"]] == 2
    ]


def test_store_destination_must_hold_its_own_payload(tmp_path):
    program_dir, result = _run(tmp_path, 1)
    stores = _store_rows(program_dir)
    assert stores, "no admitted STORE descriptor"
    assert verified_store_destinations(result, stores) == len(stores)
    # The landing is the digest of what the execution itself wrote, so a healthy
    # run pins the destination to its own payload.
    row = stores[0]
    verify = {
        entry["address"]: entry
        for entry in result["memory_endpoint"]["verifies"]
    }[row["dst_address"]]
    execution = next(
        execution
        for ledger in result["instances"][-1]["cores"].values()
        for execution in ledger["observations"]["descriptor_executions"]
        if execution["descriptor_id"] == row["descriptor_id"]
    )
    assert verify["digest"] == execution["transfer"]["payload_digest"], verify


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("destination-digest", "but its own execution wrote"),
        ("span-size", "admitted destination"),
        ("uncommitted", "did not commit"),
        ("missing-execution", "executions in the last frame"),
        ("strided", "strided destination"),
        ("destination-missing", "was not verified"),
    ),
)
def test_the_store_destination_oracle_rejects_corruption(tmp_path, tamper, message):
    program_dir, result = _run(tmp_path, 1)
    stores = _store_rows(program_dir)
    corrupted = copy.deepcopy(result)
    descriptor_id = stores[0]["descriptor_id"]
    if tamper == "destination-digest":
        corrupted["memory_endpoint"]["verifies"][0]["digest"] = "0" * 16 + "-" + "0" * 16
    elif tamper == "span-size":
        corrupted["memory_endpoint"]["verifies"][0]["size"] += 1
    elif tamper == "destination-missing":
        corrupted["memory_endpoint"]["verifies"] = []
    elif tamper in ("uncommitted", "missing-execution"):
        for ledger in corrupted["instances"][-1]["cores"].values():
            executions = ledger["observations"]["descriptor_executions"]
            if tamper == "uncommitted":
                for execution in executions:
                    if execution["descriptor_id"] == descriptor_id:
                        execution["committed"] = False
            else:
                ledger["observations"]["descriptor_executions"] = [
                    execution for execution in executions
                    if execution["descriptor_id"] != descriptor_id
                ]
    elif tamper == "strided":
        stores = [{**stores[0], "remote_stride_bytes": stores[0]["row_bytes"] + 1}]
    with pytest.raises(ReconciliationError, match=message):
        verified_store_destinations(corrupted, stores)

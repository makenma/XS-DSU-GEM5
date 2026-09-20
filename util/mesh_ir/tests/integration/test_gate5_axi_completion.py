"""Gate 5 real AXI completion and error drain over the fabric.

A target fault is planned per transaction UID; these tests assert that every
planned UID resolves to an admitted execution through the same entry the
runner uses, that the drained carriers reconcile through the shared entry, and
that a healthy run is not classified as drained.
"""

import copy
import json

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.fault_plan import execution_keys, predict, resolve_fault_plan
from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    payload_digest,
    reconcile,
    verified_post_commit_landing,
)

from tests.integration.support.garnet_harness import (
    build_program,
    cause_of,
    output_of,
    result_of,
    run_garnet,
    schedule,
)

HBM_BASE = 0x800000000

ERROR_CARRIERS = (
    ("read_error", "dma_error", 1),
    ("read_error_middle", "single", 1),
    ("write_error", "dma_write_error", 1),
    ("cross_error", "cross_fault", 1),
    ("cross_error_first_instance", "cross_fault", 2),
    ("repeat_error", "repeat_error", 1),
)


def _arch(tmp_path):
    return json.loads((tmp_path / "m5out" / "config.json").read_text())["system"]


def _admitted(program_dir):
    return {
        row["descriptor_id"]: row
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
    }


def _expected_rows(program_dir):
    admitted = _admitted(program_dir)
    traffic = json.loads((program_dir / "expected_traffic.json").read_text())
    return {
        row["identity"]["descriptor_id"]: {
            **row,
            **row["identity"],
            "kind": admitted[row["identity"]["descriptor_id"]]["kind"],
        }
        for row in traffic["descriptors"]
    }


def _runner_arch(tmp_path):
    return {
        "core_ids": [0, 1],
        "axi_data_bytes": 32,
        "axi_max_burst_beats": 16,
        "region_bases": [
            0x800000000,
            0x100000000,
            0x400000000,
        ],
        "region_tile_strides": [0, 0, 0x400000],
    }


@pytest.mark.parametrize(
    "case,program,instances",
    (
        ("read_error", "dma_error", 1),
        ("write_error", "dma_write_error", 1),
        ("repeat_error", "repeat_error", 1),
    ),
)
def test_planned_fault_resolves_to_an_admitted_execution(
    tmp_path, case, program, instances
):
    program_dir = build_program(tmp_path, program)
    arch = _runner_arch(tmp_path)
    facts = execution_keys(program_dir, arch, {0: 0, 1: 1}, instances=instances)
    expected = _expected_rows(program_dir)
    for direction in ("read", "write"):
        uids = predict(program_dir, arch, {0: 0, 1: 1},
                       instances=instances)[0][direction]
        if not uids:
            continue
        resolved = resolve_fault_plan(
            program_dir, arch,
            [{"target": 12, "uid": uids[0], "resp": "slverr"}],
            expected, instances)
        descriptors, occurrence, prefix = resolved
        assert len(descriptors) == 1 and occurrence >= 1, resolved
        assert descriptors[0] in expected, resolved
        assert prefix[descriptors[0]], resolved
        # The resolved UID names a burst that exists in the admitted walk.
        matches = [
            fact for fact in facts[0]
            if fact["uid"] == uids[0] and fact["direction"] == direction
        ]
        assert matches, (direction, hex(uids[0]))
        fact = matches[0]
        assert fact["descriptor_id"] == descriptors[0]
        assert fact["burst_index"] < len(prefix[fact["descriptor_id"]]), fact


def test_fault_model_names_the_admitted_burst_geometry(tmp_path):
    program_dir = build_program(tmp_path, "dma_write_error")
    arch = _runner_arch(tmp_path)
    uids = predict(program_dir, arch, {0: 0, 1: 1})[0]["write"]
    descriptors, _, models = resolve_fault_plan(
        program_dir, arch,
        [{"target": 12, "uid": uids[0], "resp": "slverr"}],
        _expected_rows(program_dir), 1)
    # 1024 bytes over 512-byte bursts: the model names which burst fails and
    # the admitted per-burst bytes, so every other burst still commits.
    assert models[descriptors[0]] == {
        "burst_useful_bytes": [512, 512], "failed_burst": 0,
    }, models
    second = execute_fault_model(
        program_dir, arch, uids[1], _expected_rows(program_dir)
    )
    assert second["failed_burst"] == 1, second


def execute_fault_model(program_dir, arch, uid, expected_rows):
    _, _, models = resolve_fault_plan(
        program_dir, arch,
        [{"target": 12, "uid": uid, "resp": "slverr"}], expected_rows, 1)
    return next(iter(models.values()))


def test_planned_uid_without_an_admitted_transaction_is_rejected(tmp_path):
    program_dir = build_program(tmp_path, "dma_write_error")
    arch = _runner_arch(tmp_path)
    with pytest.raises(MeshIrError) as error:
        resolve_fault_plan(
            program_dir, arch,
            [{"target": 12, "uid": 0xDEAD, "resp": "slverr"}],
            _expected_rows(program_dir), 1)
    assert "no admitted transaction" in str(error.value)


def test_no_planned_fault_resolves_to_none(tmp_path):
    program_dir = build_program(tmp_path, "dma_error")
    arch = _runner_arch(tmp_path)
    assert resolve_fault_plan(
        program_dir, arch, [], _expected_rows(program_dir), 1) is None
    assert resolve_fault_plan(
        program_dir, arch,
        [{"target": 12, "uid": 0, "resp": "okay"}],
        _expected_rows(program_dir), 1) is None


@pytest.mark.parametrize("case,program,instances", ERROR_CARRIERS)
def test_error_carrier_drains_and_passes_the_shared_entry(
    tmp_path, case, program, instances
):
    program_dir = build_program(tmp_path, program)
    extra = ["--instances", str(instances)] if instances != 1 else []
    run = run_garnet(
        tmp_path, f"m5out-{case}", case, program_dir, extra_args=tuple(extra)
    )
    assert run.returncode == 0, output_of(run)
    cause = cause_of(run)
    assert cause.startswith("MESH_PROGRAM_ERROR_DRAINED"), cause
    passed = output_of(run)
    assert "reconciliation: PASS" in passed, passed
    result = result_of(program_dir)
    assert result["error_drained"] == 1, result["error_drained"]
    assert result["watchdog_fired"] == 0, result["watchdog_fired"]
    for instance in result["instances"]:
        for ledger in instance["cores"].values():
            executions = ledger["observations"]["descriptor_executions"]
            for row in executions:
                assert row["completed"], row
                if row["status"] != "OK":
                    assert row["committed"] is False, row
                    assert row["commit_tick"] is None, row


def test_healthy_run_is_not_classified_as_drained(tmp_path):
    program_dir = build_program(tmp_path, "single")
    run = run_garnet(tmp_path, "m5out-healthy", "dma_basic", program_dir)
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), cause_of(run)
    result = result_of(program_dir)
    assert result["error_drained"] == 0, result["error_drained"]
    assert "reconciliation: PASS" in output_of(run)


def test_drained_run_keeps_the_committed_burst_prefix(tmp_path):
    program_dir = build_program(tmp_path, "dma_write_error")
    run = run_garnet(tmp_path, "m5out-prefix", "write_error", program_dir)
    assert run.returncode == 0, output_of(run)
    result = result_of(program_dir)
    rows = {
        row["descriptor_id"]: row
        for row in result["instances"][0]["cores"]["0"]["observations"][
            "descriptor_executions"
        ]
    }
    faulted = [row for row in rows.values() if row["status"] != "OK"]
    assert len(faulted) == 1, faulted
    assert faulted[0]["committed"] is False and faulted[0]["commit_tick"] is None
    assert faulted[0]["transfer"]["write_bytes"] == 512, faulted[0]
    assert result["memory_endpoint"]["committed_valid_bytes"] == 512, result[
        "memory_endpoint"
    ]


READ_WINDOW_PROGRAM = "read_window"
BURST_BYTES = 256
BURSTS = 24
SEED_BASE = 0x800100000


def _run_read_window(tmp_path, name, case):
    # Each run publishes its own program directory: publication is
    # no-replace, and a second build into the same path is refused.
    work = tmp_path / name
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, READ_WINDOW_PROGRAM)
    run = run_garnet(work, f"m5out-{name}", case, program_dir)
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    return program_dir, result_of(program_dir), run


def _read_bursts(result):
    return [row for row in result["burst_timings"] if row["channel"] == "AR"]


def _inversions(reads):
    issue = sorted(reads, key=lambda row: (row["ar_aw_tick"], row["ordinal"]))
    response = sorted(reads, key=lambda row: (row["response_tick"], row["ordinal"]))
    return sum(
        1 for index, row in enumerate(response)
        if row["address"] != issue[index]["address"]
    )


def test_multi_burst_load_completes_out_of_issue_order(tmp_path):
    _, result, run = _run_read_window(tmp_path, "reorder", "read_reorder")
    reads = _read_bursts(result)
    assert len(reads) == BURSTS, len(reads)
    issue = sorted(reads, key=lambda row: (row["ar_aw_tick"], row["ordinal"]))
    assert [row["address"] for row in issue] == sorted(
        row["address"] for row in reads
    )
    assert len({row["axi_id"] for row in reads}) == 4, reads
    assert _inversions(reads) > 0, reads
    assert "reconciliation: PASS" in output_of(run), output_of(run)
    assert "R completion order: PASS" in output_of(run), output_of(run)


def test_delay_is_what_inverts_the_completion_order(tmp_path):
    _, baseline, _ = _run_read_window(tmp_path, "no-delay", "read_outstanding_window")
    _, delayed, _ = _run_read_window(tmp_path, "delay", "read_reorder")
    assert _inversions(_read_bursts(baseline)) == 0, _read_bursts(baseline)
    assert _inversions(_read_bursts(delayed)) > 0, _read_bursts(delayed)


def test_axi_id_is_only_reused_after_its_last_read_beat(tmp_path):
    _, result, _ = _run_read_window(tmp_path, "reuse", "read_reorder")
    reads = _read_bursts(result)
    per_id = {}
    for row in sorted(reads, key=lambda row: row["ar_aw_tick"]):
        previous = per_id.get(row["axi_id"])
        if previous is not None:
            assert row["ar_aw_tick"] >= previous["response_tick"], (previous, row)
        per_id[row["axi_id"]] = row
    per_id = {
        identifier: [row for row in reads if row["axi_id"] == identifier]
        for identifier in {row["axi_id"] for row in reads}
    }
    reused = {identifier: rows for identifier, rows in per_id.items()
              if len(rows) >= 2}
    assert reused, per_id
    assert len(reads) - len(per_id) >= 2, per_id


def test_position_sensitive_payload_rejects_a_swapped_burst(tmp_path):
    program_dir, result, _ = _run_read_window(tmp_path, "position", "read_reorder")
    oracle = json.loads((program_dir / "gate5_oracle.json").read_text())
    patterns = [
        (SEED_BASE + index * BURST_BYTES, BURST_BYTES, 0x40 + index)
        for index in range(BURSTS)
    ]
    ordered = b"".join(bytes([pattern]) * size for _, size, pattern in patterns)
    accepted = reconcile(
        cause="MESH_PROGRAM_DONE: reorder",
        result=result,
        schedule=json.loads((program_dir / "schedule.mesh.json").read_text()),
        expected_rows=list(_expected_rows(program_dir).values()),
        error_descriptors=(),
        fault_occurrence=0,
        instances=1,
        traffic_multiplier=1,
        read_payload_digests={1: payload_digest(ordered)},
    )
    assert accepted["status"] == "ok"
    swapped = bytearray(ordered)
    swapped[:BURST_BYTES], swapped[BURST_BYTES:2 * BURST_BYTES] = (
        swapped[BURST_BYTES:2 * BURST_BYTES],
        swapped[:BURST_BYTES],
    )
    with pytest.raises(ReconciliationError) as error:
        reconcile(
            cause="MESH_PROGRAM_DONE: reorder",
            result=result,
            schedule=json.loads((program_dir / "schedule.mesh.json").read_text()),
            expected_rows=list(_expected_rows(program_dir).values()),
            error_descriptors=(),
            fault_occurrence=0,
            instances=1,
            traffic_multiplier=1,
            read_payload_digests={1: payload_digest(bytes(swapped))},
        )
    assert "payload does not match the oracle" in str(error.value)


ADMITTED_BURST_BYTES = 16 * 32


@pytest.fixture(scope="module")
def post_commit(tmp_path_factory):
    work = tmp_path_factory.mktemp("gate5-post-commit")
    program_dir = build_program(work, "single")
    run = run_garnet(work, "m5out", "write_error_post_commit", program_dir)
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_ERROR_DRAINED"), output_of(run)
    assert "post-commit landing: PASS" in output_of(run), output_of(run)
    return {
        "program_dir": program_dir,
        "result": result_of(program_dir),
        "oracle": json.loads((program_dir / "gate5_oracle.json").read_text()),
    }


def _errored_execution(result):
    return next(
        row
        for frame in result["instances"]
        for ledger in frame["cores"].values()
        for row in ledger["observations"]["descriptor_executions"]
        if row["status"] != "OK"
    )


def _post_commit_reconcile(post_commit, result):
    oracle = post_commit["oracle"]
    return reconcile(
        cause="MESH_PROGRAM_ERROR_DRAINED: post_commit",
        result=result,
        schedule=json.loads(
            (post_commit["program_dir"] / "schedule.mesh.json").read_text()
        ),
        expected_rows=list(_expected_rows(post_commit["program_dir"]).values()),
        error_descriptors=tuple(int(key) for key in oracle["fault_models"]),
        fault_occurrence=oracle["fault_occurrence"],
        instances=1,
        traffic_multiplier=1,
        read_payload_digests={
            int(key): value
            for key, value in oracle["read_payload_digests"].items()
        },
        fault_models={
            int(key): value for key, value in oracle["fault_models"].items()
        },
    )


def test_post_commit_b_error_lands_the_bytes_and_fails_the_source(post_commit):
    result = post_commit["result"]
    result_summary = verified_post_commit_landing(
        result, admitted_burst_bytes=ADMITTED_BURST_BYTES
    )
    assert result_summary["failed_bytes"] == ADMITTED_BURST_BYTES, result_summary
    endpoint = result["memory_endpoint"]
    errored = [
        row
        for instance in result["instances"]
        for ledger in instance["cores"].values()
        for row in ledger["observations"]["descriptor_executions"]
        if row["status"] != "OK"
    ]
    assert len(errored) == 1, errored
    assert errored[0]["status"] == "AXI_WRITE_ERROR", errored[0]
    assert errored[0]["committed"] is False, errored[0]
    assert errored[0]["commit_tick"] is None, errored[0]
    produced = b"".join(
        bytes.fromhex(source["bytes_hex"])
        for source in errored[0]["source_rows"]
    )
    assert len(produced) == 8192, len(produced)
    assert endpoint["committed_valid_bytes"] == len(produced)
    landed = bytes.fromhex(endpoint["verifies"][0]["bytes_hex"])
    assert landed == produced


POST_COMMIT_CORRUPTIONS = (
    ("landing-changed", "landing differs"),
    ("target-count-zeroed", "committed bytes"),
    ("whole-transfer-failed", "failing span"),
    ("claimed-commit", "claimed a source commit"),
)


@pytest.mark.parametrize("tamper, message", POST_COMMIT_CORRUPTIONS)
def test_the_post_commit_landing_entry_rejects_corruption(
    post_commit, tamper, message
):
    result = copy.deepcopy(post_commit["result"])
    row = _errored_execution(result)
    endpoint = result["memory_endpoint"]
    if tamper == "landing-changed":
        hexed = endpoint["verifies"][0]["bytes_hex"]
        endpoint["verifies"][0]["bytes_hex"] = (
            "00" if hexed[:2] != "00" else "ff"
        ) + hexed[2:]
    elif tamper == "target-count-zeroed":
        endpoint["committed_valid_bytes"] = 0
    elif tamper == "whole-transfer-failed":
        row["transfer"]["write_bytes"] = 0
    elif tamper == "claimed-commit":
        row["committed"] = True
        row["commit_tick"] = row["completion_tick"]
    with pytest.raises(ReconciliationError, match=message):
        verified_post_commit_landing(
            result, admitted_burst_bytes=ADMITTED_BURST_BYTES
        )


def test_a_post_commit_fault_cannot_claim_a_source_commit(post_commit):
    result = copy.deepcopy(post_commit["result"])
    for row in result["instances"][0]["cores"]["0"]["observations"][
        "descriptor_executions"
    ]:
        if row["status"] != "OK":
            row["committed"] = True
            row["commit_tick"] = row["completion_tick"]
    with pytest.raises(ReconciliationError) as error:
        _post_commit_reconcile(post_commit, result)
    assert "recorded a commit" in str(error.value)


def test_lost_response_is_reported_as_a_deadlock(tmp_path):
    """G5-16: a response that never arrives must be reported with its pending
    identities and must never be classified as a completion."""
    work = tmp_path / "lost"
    work.mkdir(parents=True, exist_ok=True)
    program_dir = build_program(work, "dma_error")
    run = run_garnet(work, "m5out", "lost_response", program_dir)
    output = output_of(run)
    assert run.returncode != 0, output
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert "MESH_PROGRAM_DONE" not in output, output
    assert "MESH_PROGRAM_ERROR_DRAINED" not in output, output
    # The pending identity is reported, not just a timeout.
    assert "dma cmd=2 generation=0" in output, output
    assert "pending=1" in output, output
    assert "descriptors_completed=0" in output, output
    assert "wait-event cmd=3 generation=0 event=2 producer=cmd2" in output, output
    assert not (program_dir / "actual_result.json").exists()
    records = [
        json.loads(line)
        for line in (program_dir / "runtime_diagnostics.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    deadlocks = [row for row in records if row["code"] == "E_RUNTIME_DEADLOCK"]
    assert deadlocks, records
    assert deadlocks[0]["stage"] == "watchdog", deadlocks[0]
    assert deadlocks[0]["context"]["instance"] == "1", deadlocks[0]


MIDDLE_ERROR_UID = 16
HBM_NODE = 12


def test_a_mid_plan_read_error_keeps_the_later_bursts(tmp_path):
    """G5-12: an R error in the middle of a LOAD plan keeps every burst that
    already landed, refuses the bad one and drains the response."""
    program_dir = build_program(tmp_path, "single")
    run = run_garnet(tmp_path, "m5out-middle", "read_error_middle", program_dir)
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_ERROR_DRAINED"), output_of(run)
    assert "reconciliation: PASS" in output_of(run), output_of(run)
    result = result_of(program_dir)
    rows = {
        row["descriptor_id"]: row
        for row in result["instances"][0]["cores"]["0"]["observations"][
            "descriptor_executions"
        ]
    }
    faulted = [row for row in rows.values() if row["status"] != "OK"]
    assert len(faulted) == 1, faulted
    row = faulted[0]
    assert row["status"] == "AXI_READ_ERROR", row
    assert row["committed"] is False and row["commit_tick"] is None, row

    # The surviving bursts are the admitted plan minus exactly the failed one.
    arch = _runner_arch(tmp_path)
    uids = predict(program_dir, arch, {0: 0, 1: 1})[0]["read"]
    (descriptor_id,), _, models = resolve_fault_plan(
        program_dir, arch,
        [{"target": HBM_NODE, "uid": uids[MIDDLE_ERROR_UID], "resp": "slverr"}],
        _expected_rows(program_dir), 1,
    )
    assert descriptor_id == row["descriptor_id"], (descriptor_id, row)
    bursts = models[descriptor_id]["burst_useful_bytes"]
    failed = models[descriptor_id]["failed_burst"]
    assert row["transfer"]["read_bytes"] == sum(bursts) - bursts[failed], row
    assert row["transfer"]["read_bursts"] == len(bursts) - 1, row
    transport = {t["descriptor_id"]: t for t in result["transport"]}
    assert transport[descriptor_id]["read_discarded_bytes"] == bursts[failed], transport
    assert transport[descriptor_id]["error_code"] == 1, transport
    # A local commit exists exactly because bursts survived the mid-plan error.
    timing = {
        t["descriptor_id"]: t for t in result["dma_timings"]
    }[descriptor_id]
    assert timing["local_commit_tick"] > 0, timing
    assert timing["last_r_tick"] <= timing["done_tick"], timing
    # The earlier descriptor of the same load command is untouched.
    healthy = [
        entry for entry in rows.values()
        if entry["status"] == "OK" and entry["transfer"]["read_bytes"] > 0
    ]
    assert healthy and all(entry["committed"] for entry in healthy), healthy
    # Claiming the faulted execution landed nothing is refused by the shared
    # entry the runner itself uses.
    oracle = json.loads((program_dir / "gate5_oracle.json").read_text())
    tampered = copy.deepcopy(result)
    for instance in tampered["instances"]:
        for ledger in instance["cores"].values():
            for entry in ledger["observations"]["descriptor_executions"]:
                if entry["descriptor_id"] == descriptor_id:
                    entry["transfer"]["read_bytes"] = 0
                    entry["transfer"]["read_bursts"] = 0
    with pytest.raises(ReconciliationError) as error:
        reconcile(
            cause="MESH_PROGRAM_ERROR_DRAINED: middle_read_error",
            result=tampered,
            schedule=json.loads(
                (program_dir / "schedule.mesh.json").read_text()
            ),
            expected_rows=list(_expected_rows(program_dir).values()),
            error_descriptors=(descriptor_id,),
            fault_occurrence=oracle["fault_occurrence"],
            instances=1,
            traffic_multiplier=1,
            read_payload_digests={
                int(key): value
                for key, value in oracle["read_payload_digests"].items()
            },
            fault_models={
                int(key): value
                for key, value in oracle["fault_models"].items()
            },
        )
    assert "committed" in str(error.value)

"""Gate 5 per-execution burst identity and retirement facts.

Every real burst must name the admitted execution it belongs to and retire
exactly once before its descriptor's terminal tick.  The healthy control is one
real two-instance run; each further case applies a single tampering to that
archive and asserts that the shared entry rejects it for the stated reason.
"""

import copy

import pytest

from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    verified_burst_attribution,
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
BEAT_BYTES = 32
MAX_BEATS = 16
BURSTS = 96


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    work = tmp_path_factory.mktemp("gate5-bursts")
    program_dir = build_program(work, PROGRAM)
    run = run_garnet(
        work, "m5out", CASE, program_dir,
        extra_args=("--instances", str(INSTANCES)),
    )
    assert run.returncode == 0, output_of(run)
    assert cause_of(run).startswith("MESH_PROGRAM_DONE"), output_of(run)
    assert "burst attribution: PASS" in output_of(run), output_of(run)
    return {
        "program_dir": program_dir,
        "result": result_of(program_dir),
        "run": run,
    }


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


def _attribution(baseline, frames=None, descriptors=None, bridges=None):
    return verified_burst_attribution(
        baseline["result"]["instances"] if frames is None else frames,
        _descriptors(baseline["program_dir"]) if descriptors is None else descriptors,
        max_beats=MAX_BEATS, beat_bytes=BEAT_BYTES,
        bridges=baseline["result"]["bridges"] if bridges is None else bridges,
    )


def _read_burst(frame, channel="AR", index=0):
    return [
        row for row in frame["bursts"] if row["channel"] == channel
    ][index]


def test_healthy_two_instance_bursts_name_their_executions(baseline):
    summary = _attribution(baseline)
    assert summary == {"frames": INSTANCES, "bursts": BURSTS}, summary
    ordinals = {}
    for frame in baseline["result"]["instances"]:
        assert frame["bursts"], frame["instance"]
        assert all(
            row["instance"] == frame["instance"] for row in frame["bursts"]
        ), frame["instance"]
        for row in frame["bursts"]:
            assert row["command_id"] and row["descriptor_id"], row
            assert row["retire_tick"] == row["response_tick"] > 0, row
            assert row["retire_tick"] <= row["done_tick"], row
            assert row["ordinal"] not in ordinals, row
            ordinals[row["ordinal"]] = row["descriptor_id"]
    assert len(ordinals) == BURSTS, len(ordinals)


def test_a_frame_boundary_anchors_the_execution_ledger(baseline):
    """The fully coherent ledger lie: every burst of one execution, the
    execution's own terminal and the terminal row move together into the
    next instance's window.  Only the instance boundary can catch it."""
    frames = copy.deepcopy(baseline["result"]["instances"])
    first, second = frames[0], frames[1]
    key = (first["bursts"][0]["command_id"],
           first["bursts"][0]["generation"],
           first["bursts"][0]["descriptor_id"])
    siblings = [row for row in first["bursts"]
                if (row["command_id"], row["generation"],
                    row["descriptor_id"]) == key]
    execution = next(
        row for ledger in first["cores"].values()
        for row in ledger["observations"]["descriptor_executions"]
        if (row["command_id"], row["generation"], row["descriptor_id"])
        == key)
    next_issue = min(row["issue_tick"] for row in second["bursts"])
    shift = next_issue + 10_000_000
    for row in siblings:
        for field in ("retire_tick", "response_tick", "done_tick",
                      "ar_aw_tick", "issue_tick", "local_commit_tick"):
            if row[field]:
                row[field] = row[field] + shift
    execution["completion_tick"] = max(r["done_tick"] for r in siblings)
    execution["commit_tick"] = execution["commit_tick"] + shift
    for terminal in first["cores"]["0"].get("terminals", []):
        if terminal["command_id"] == execution["command_id"]:
            terminal["terminal_tick"] = execution["completion_tick"]
    with pytest.raises(ReconciliationError, match="before frame"):
        _attribution(baseline, frames=frames)


def test_a_response_may_not_precede_its_request(baseline):
    """G5-M18-01: a response retiring before the request was accepted, and a
    burst without a recorded handshake, are both impossible lifecycles."""
    frames = copy.deepcopy(baseline["result"]["instances"])
    row = _read_burst(frames[0], "AW", 0)
    row["response_tick"] = row["retire_tick"] = 1
    with pytest.raises(ReconciliationError, match="request handshake"):
        _attribution(baseline, frames=frames)

    frames = copy.deepcopy(baseline["result"]["instances"])
    row = _read_burst(frames[0], "AW", 0)
    row["ar_aw_tick"] = 0
    with pytest.raises(ReconciliationError, match="request handshake"):
        _attribution(baseline, frames=frames)


def test_a_response_at_the_request_tick_is_legal(baseline):
    frames = copy.deepcopy(baseline["result"]["instances"])
    row = _read_burst(frames[0], "AW", 0)
    row["response_tick"] = row["retire_tick"] = row["ar_aw_tick"]
    _attribution(baseline, frames=frames)


def test_dropping_the_faulted_burst_breaks_the_error_closure(tmp_path):
    """G5-M18-02: the review's combined counterexample.  Deleting the
    faulted trailing burst of an errored execution and lowering the bridge's
    burst counters leaves a plan-consistent prefix; the shared entry must
    still reject it from the execution's own error declaration and the
    bridge's error classification, without any case-specific B count."""
    program_dir = build_program(tmp_path, "p2p_prefilled_destination")
    run = run_garnet(tmp_path, "m5out-closure",
                     "p2p_prefilled_incomplete", program_dir)
    assert run.returncode == 0, output_of(run)
    result = result_of(program_dir)
    descriptors = _descriptors(program_dir)
    verified_burst_attribution(
        result["instances"], descriptors,
        max_beats=MAX_BEATS, beat_bytes=BEAT_BYTES,
        bridges=result["bridges"],
    )
    frames = copy.deepcopy(result["instances"])
    faulted = next(row for row in frames[0]["bursts"]
                   if row["descriptor_id"] == 11 and row["errored"])
    frames[0]["bursts"].remove(faulted)
    bridge = next(b for b in result["bridges"] if b["core_id"] == 0)
    bridge["aw_accepted"] -= 1
    bridge["write_bursts_submitted"] -= 1
    bridge["write_bursts_completed"] -= 1
    bridge["b_consumed"] -= 1
    with pytest.raises(ReconciliationError, match="archives no errored burst"):
        verified_burst_attribution(
            frames, descriptors,
            max_beats=MAX_BEATS, beat_bytes=BEAT_BYTES,
            bridges=result["bridges"],
        )


def test_dropping_a_successful_burst_of_an_errored_execution(tmp_path):
    """G5-M18-02: an errored execution's own transfer facts state how many
    bursts succeeded; dropping one of them is caught by the shared entry
    alone, even when the bridge counters move with the lie."""
    program_dir = build_program(tmp_path, "dma_write_error")
    run = run_garnet(tmp_path, "m5out-ok-drop", "write_error", program_dir)
    assert run.returncode == 0, output_of(run)
    result = result_of(program_dir)
    descriptors = _descriptors(program_dir)
    verified_burst_attribution(
        result["instances"], descriptors,
        max_beats=MAX_BEATS, beat_bytes=BEAT_BYTES,
        bridges=result["bridges"],
    )
    frames = copy.deepcopy(result["instances"])
    errored_execution = next(
        row for row in frames[0]["bursts"] if row["errored"])
    siblings = [
        row for row in frames[0]["bursts"]
        if (row["command_id"], row["generation"], row["descriptor_id"])
        == (errored_execution["command_id"], errored_execution["generation"],
            errored_execution["descriptor_id"])
    ]
    frames[0]["bursts"].remove(max(siblings, key=lambda r: r["burst_index"]))
    bridge = next(b for b in result["bridges"] if b["core_id"] == 0)
    for field in ("aw_accepted", "write_bursts_submitted",
                  "write_bursts_completed", "b_consumed"):
        bridge[field] -= 1
    with pytest.raises(ReconciliationError, match="non-errored ones"):
        verified_burst_attribution(
            frames, descriptors,
            max_beats=MAX_BEATS, beat_bytes=BEAT_BYTES,
            bridges=result["bridges"],
        )


def test_the_bridge_error_classification_cannot_drift(baseline):
    """G5-M18-02: the bridge's errored-B classification is tied to the
    archived errored bursts, so a run without failures cannot claim one."""
    bridges = copy.deepcopy(baseline["result"]["bridges"])
    bridges[0]["b_error_count"] = 1
    with pytest.raises(ReconciliationError, match="errored B responses"):
        verified_burst_attribution(
            baseline["result"]["instances"],
            _descriptors(baseline["program_dir"]),
            max_beats=MAX_BEATS, beat_bytes=BEAT_BYTES,
            bridges=bridges,
        )


@pytest.mark.parametrize(
    "tamper, message",
    (
        ("late-b", "after its descriptor terminal tick"),
        ("missing-b", "request handshake"),
        ("duplicated-burst", "archives burst indices"),
        ("cleared-read-commit", "never reached its local SRAM commit"),
        ("shifted-address", "the admitted burst is"),
        ("borrowed-instance", "archives a burst of instance"),
        ("borrowed-ordinal", "before frame"),
        ("early-retire", "request handshake"),
        ("self-raised-done", "the execution completed at"),
        ("foreign-core", "has no execution"),
        ("fake-error", "reports an error"),
    ),
)
def test_the_burst_entry_rejects_corruption(baseline, tamper, message):
    frames = copy.deepcopy(baseline["result"]["instances"])
    first, second = frames[0], frames[1]
    if tamper == "late-b":
        # The reported counterexample: a B later than every descriptor terminal.
        row = _read_burst(first, "AW", 0)
        row["retire_tick"] = row["response_tick"] = row["done_tick"] + 100000
    elif tamper == "missing-b":
        row = _read_burst(first, "AW", 1)
        row["retire_tick"] = 0
        row["response_tick"] = 0
    elif tamper == "duplicated-burst":
        row = _read_burst(first, "AW", 1)
        row["burst_index"] = row["burst_index"] - 1
    elif tamper == "cleared-read-commit":
        row = next(
            row for row in first["bursts"]
            if row["channel"] == "AR" and not row["errored"]
        )
        row["local_commit_tick"] = 0
    elif tamper == "shifted-address":
        row = _read_burst(first, "AR", 0)
        row["address"] += BEAT_BYTES
    elif tamper == "borrowed-instance":
        second["bursts"][0]["instance"] = first["instance"]
    elif tamper == "borrowed-ordinal":
        borrowed = copy.deepcopy(_read_burst(first, "AW", 0))
        borrowed["instance"] = second["instance"]
        second["bursts"].append(borrowed)
    elif tamper == "early-retire":
        row = _read_burst(first, "AW", 2)
        row["retire_tick"] = row["response_tick"] = 0
    elif tamper == "self-raised-done":
        # The reported counterexample: the late B raises its own terminal bound
        # with it, so the row stays internally ordered while the execution's
        # real completion is untouched.
        row = _read_burst(first, "AW", 0)
        row["retire_tick"] = row["response_tick"] = row["done_tick"] + 100000
        row["done_tick"] = row["retire_tick"] + 1
    elif tamper == "foreign-core":
        row = _read_burst(first, "AW", 0)
        row["core_id"] = 99
    elif tamper == "fake-error":
        row = next(
            row for row in first["bursts"]
            if row["channel"] == "AR" and not row["errored"]
        )
        row["errored"] = True
        row["local_commit_tick"] = 0
    with pytest.raises(ReconciliationError, match=message):
        _attribution(baseline, frames=frames)


def test_a_committed_execution_cannot_drop_an_accepted_burst(baseline):
    frames = copy.deepcopy(baseline["result"]["instances"])
    first = frames[0]
    victim = next(
        row for row in first["bursts"]
        if row["channel"] == "AR"
        and row["descriptor_id"] == first["bursts"][0]["descriptor_id"]
    )
    first["bursts"].remove(victim)
    with pytest.raises(ReconciliationError, match="archives burst indices"):
        _attribution(baseline, frames=frames)


def test_a_b_ejected_after_the_target_commit_still_bounds_the_descriptor(tmp_path):
    """G5-R23-02: the B ejection delay lands after the target committed it."""
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_garnet(tmp_path, "m5out-ejection", "delayed_b_ejection", program_dir)
    assert run.returncode == 0, output_of(run)
    assert "b reorder: PASS" in output_of(run), output_of(run)
    result = result_of(program_dir)
    summary = verified_burst_attribution(
        result["instances"], _descriptors(program_dir),
        max_beats=MAX_BEATS, beat_bytes=BEAT_BYTES,
        bridges=result["bridges"],
    )
    assert summary["bursts"] == 48, summary
    writes = sorted(
        (row for frame in result["instances"] for row in frame["bursts"]
         if row["channel"] == "AW"),
        key=lambda row: row["retire_tick"],
    )
    retired = [row["ordinal"] for row in writes]
    assert retired != sorted(retired), retired
    delayed = writes[-1]
    assert delayed["retire_tick"] == max(row["done_tick"] for row in writes), delayed
    assert all(row["retire_tick"] <= row["done_tick"] for row in writes), writes


def test_the_delayed_b_carrier_retires_out_of_order_but_never_early(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_garnet(tmp_path, "m5out-delayed", "delayed_b", program_dir)
    assert run.returncode == 0, output_of(run)
    assert "b reorder: PASS" in output_of(run), output_of(run)
    result = result_of(program_dir)
    summary = verified_burst_attribution(
        result["instances"], _descriptors(program_dir),
        max_beats=MAX_BEATS, beat_bytes=BEAT_BYTES,
        bridges=result["bridges"],
    )
    assert summary["bursts"] == 48, summary
    writes = sorted(
        (row for frame in result["instances"] for row in frame["bursts"]
         if row["channel"] == "AW"),
        key=lambda row: row["retire_tick"],
    )
    retired = [row["ordinal"] for row in writes]
    assert retired != sorted(retired), retired
    assert all(row["retire_tick"] <= row["done_tick"] for row in writes), writes

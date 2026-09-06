import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
CONFIG = REPO / "configs" / "example" / "ai_mesh" / "run_gate3_protocol.py"
sys.path.insert(0, str(CONFIG.parent))

from gate3_acceptance import _fatal_error, _fatal_snapshot, _ledger, parse_gate3_facts


def _run(profile, output, expected_exit=0, sim_tick_limit=5000000, config=CONFIG):
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("AI_MESH_")
    }
    run = subprocess.run(
        [
            str(GEM5),
            f"--outdir={output}",
            str(config),
            "--profile",
            profile,
            "--sim-tick-limit",
            str(sim_tick_limit),
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == expected_exit, run.stdout + run.stderr
    return parse_gate3_facts(output / "gate3_facts.tsv")


@pytest.mark.parametrize(
    ("profile", "parameter_bytes"),
    (("PARAMETER_ENVELOPE_LONG", 320), ("PARAMETER_ENVELOPE_SHORT", 192)),
)
def test_parameter_envelope_mismatch_is_recoverable_before_execution(
    tmp_path, profile, parameter_bytes
):
    events, _, _, _ = _run(profile, tmp_path)
    parameter_r_bytes = sum(
        int(event["bytes"])
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "PARAMETER"
        and event["channel"] == "R"
    )
    assert parameter_r_bytes == parameter_bytes
    assert not any(event["kind"] == "CORE_START" for event in events)
    assert not any(
        event["kind"] == "AXI_ACCEPT" and event["control"] == "OUTPUT"
        for event in events
    )
    rejects = [
        event
        for event in events
        if event["kind"] == "PARAMETER_REJECT"
    ]
    assert [event["status"] for event in rejects] == [
        "E_PARAMETER_LENGTH_MISMATCH"
    ]


def test_parameter_read_splits_at_4k_boundary(tmp_path):
    events, _, metrics, fatal = _run("PARAMETER_CROSS_4K", tmp_path)
    assert fatal is None
    reads = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "PARAMETER"
    ]
    assert [event["bytes"] for event in reads if event["channel"] == "R"] == [
        64,
        64,
        64,
        64,
    ]
    assert len([event for event in reads if event["channel"] == "AR"]) == 2
    assert metrics["parameter_read_segments"] == 2
    assert metrics["parameter_first_segment_bytes"] == 64
    assert metrics["core_starts"] == 1


def test_parameter_read_extracts_all_narrow_beat_lanes(tmp_path):
    events, _, metrics, fatal = _run("PARAMETER_NARROW_BEATS", tmp_path)
    assert fatal is None
    parameter_r = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "PARAMETER"
        and event["channel"] == "R"
    ]
    assert [event["bytes"] for event in parameter_r] == [32] * 8
    assert metrics["parameter_read_segments"] == 1
    assert metrics["parameter_first_segment_bytes"] == 256
    assert metrics["core_starts"] == 1


@pytest.mark.parametrize(
    "profile",
    (
        "PARAMETER_BINDING_OOB",
        "PARAMETER_BINDING_HEADER_OVERLAP",
        "PARAMETER_BINDING_EXTENSION_OVERLAP",
        "PARAMETER_BINDING_RECORD_SIZE",
    ),
)
def test_parameter_binding_structure_is_rejected_before_execution(
    tmp_path, profile
):
    events, _, metrics, fatal = _run(profile, tmp_path)
    assert fatal is None
    assert [
        event["status"]
        for event in events
        if event["kind"] == "PARAMETER_REJECT"
    ] == ["E_REQUEST_BINDING"]
    assert [
        event["status"]
        for event in events
        if event["kind"] == "CQ_DETAIL"
    ] == ["E_REQUEST_BINDING"]
    assert not any(event["control"] == "PROMPT" for event in events)
    assert not any(event["kind"] == "CORE_START" for event in events)
    assert not any(event["control"] == "OUTPUT" for event in events)
    assert metrics["cq_assignments"] == 1


def test_parameter_binding_at_end_of_block_is_accepted(tmp_path):
    events, final, metrics, fatal = _run("PARAMETER_BINDING_END", tmp_path)
    assert fatal is None
    assert not any(event["kind"] == "PARAMETER_REJECT" for event in events)
    assert metrics["core_starts"] == 1
    assert final["npu_cq_ack_seq"] == 1


def test_ring_slots_use_abi_strides_on_real_axi_transactions(tmp_path):
    events, final, _, fatal = _run("DEPTH2_WRAP", tmp_path)
    assert fatal is None
    sq_reads = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["object"] == "SQ_ENTRY"
        and event["channel"] == "AR"
    ]
    assert len(sq_reads) == 3
    assert [event["address"] for event in sq_reads] == [
        sq_reads[0]["address"],
        sq_reads[0]["address"] + 64,
        sq_reads[0]["address"],
    ]
    assert [event["size"] for event in sq_reads] == [6, 6, 6]
    cq_writes = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["object"] == "CQ_ENTRY"
        and event["channel"] == "W"
    ]
    assert len(cq_writes) == 3
    assert [event["address"] for event in cq_writes] == [
        cq_writes[0]["address"],
        cq_writes[0]["address"] + 32,
        cq_writes[0]["address"],
    ]
    assert [event["size"] for event in cq_writes] == [5, 5, 5]
    assert [event["wstrb"] for event in cq_writes] == [
        "ffffffff",
        "ffffffff00000000",
        "ffffffff",
    ]
    assert final["npu_cq_ack_seq"] == 3


@pytest.mark.parametrize(
    ("profile", "metadata_bytes"),
    (("NORMAL", 128), ("METADATA_TLV_TAIL", 200)),
)
def test_metadata_valid_cq_reads_and_validates_complete_object(
    tmp_path, profile, metadata_bytes
):
    events, final, metrics, fatal = _run(profile, tmp_path)
    assert fatal is None
    assert metrics["metadata_reads_validated"] == 1
    assert metrics["metadata_read_bytes"] == metadata_bytes
    reads = [
        event
        for event in events
        if event["kind"] == "LOCAL_READ"
        and event["object"] == "METADATA_READ"
    ]
    assert [(event["bytes"], event["status"]) for event in reads] == (
        [(128, "HEADER")]
        if metadata_bytes == 128
        else [(128, "HEADER"), (72, "TAIL")]
    )
    if metadata_bytes > 128:
        assert reads[0]["tick"] < reads[1]["tick"]
        ack = next(
            event for event in events
            if event["kind"] == "AXI_ACCEPT"
            and event["control"] == "CQ_HEAD_ACK"
            and event["channel"] == "AW"
        )
        assert reads[1]["tick"] < ack["tick"]
    assert final["npu_cq_ack_seq"] == 1


def test_metadata_identity_mismatch_blocks_ack(tmp_path):
    events, _, _, fatal = _run(
        "METADATA_CQ_MISMATCH", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    assert not any(
        event["kind"] == "AXI_ACCEPT"
        and event["control"] == "CQ_HEAD_ACK"
        for event in events
    )


@pytest.mark.parametrize(
    "profile",
    (
        "METADATA_SESSION_MISMATCH",
        "METADATA_USER_MISMATCH",
        "METADATA_TASK_MISMATCH",
        "METADATA_ROUND_MISMATCH",
        "METADATA_FLAGS_UNKNOWN",
        "METADATA_TLV_SIZE_MISMATCH",
    ),
)
def test_metadata_semantic_mismatch_blocks_ack(tmp_path, profile):
    events, _, _, fatal = _run(profile, tmp_path, expected_exit=20)
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    reads = [
        event
        for event in events
        if event["kind"] == "LOCAL_READ"
        and event["object"] == "METADATA_READ"
    ]
    if profile == "METADATA_TLV_SIZE_MISMATCH":
        assert [event["status"] for event in reads] == ["HEADER", "TAIL"]
    else:
        assert [event["status"] for event in reads] == ["HEADER"]
    assert not any(event["control"] == "CQ_HEAD_ACK" for event in events)


def test_three_msi_responses_complete_in_reverse_order(tmp_path):
    events, final, metrics, fatal = _run(
        "MSI_REVERSE_B", tmp_path, sim_tick_limit=15000000
    )
    assert fatal is None
    msi_aw = [
        event for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "MSI"
        and event["channel"] == "AW"
    ]
    msi_b = [
        event for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "MSI"
        and event["channel"] == "B"
    ]
    assert [event["absolute_seq"] for event in msi_aw] == [1, 2, 3]
    assert [event["absolute_seq"] for event in msi_b] == [3, 2, 1]
    assert max(event["tick"] for event in msi_aw) < min(
        event["tick"] for event in msi_b
    )
    assert metrics["npu_cq_msi_issued_seq"] == 3
    assert metrics["npu_cq_notified_seq"] == 3
    assert metrics["npu_cq_ack_seq"] == 3
    retirements = [
        event for event in events
        if event["kind"] == "CQ_OBLIGATION_RETIRE"
    ]
    terminal_tick = max(
        max(event["tick"] for event in msi_b),
        max(
            event["tick"] for event in events
            if event["kind"] == "CQ_ACK_TARGET_COMMIT"
        ),
    )
    expected_retire_tick = (terminal_tick // 1000 + 1) * 1000
    assert [event["tick"] for event in retirements] == [
        expected_retire_tick,
        expected_retire_tick,
        expected_retire_tick,
    ]
    assert final["msi_rob_entries"] == 0


@pytest.mark.parametrize(
    "profile",
    (
        "MSI_POSTCOMMIT_ACK_OK",
        "MSI_POSTCOMMIT_ACK_ERROR",
        "MSI_POSTCOMMIT_ACK_DELAYED",
    ),
)
def test_fatal_drain_finishes_after_last_control_response(tmp_path, profile):
    events, final, metrics, fatal = _run(
        profile, tmp_path, expected_exit=20
    )
    assert fatal == {"symbol": "E_INTERRUPT", "value": 0x00020006}
    assert final["fatal"]
    assert metrics["fatal_records"] == 1
    assert any(
        event["control"] == "MSI"
        and event["channel"] == "B"
        and event["response"] == "SLVERR"
        for event in events
    )
    assert any(
        event["control"] == "CQ_HEAD_ACK" and event["channel"] == "B"
        for event in events
    )


def test_same_tick_completion_faults_are_reduced_together(tmp_path):
    events, _, metrics, fatal = _run(
        "MSI_POSTCOMMIT_ACK_ERROR", tmp_path, expected_exit=20
    )
    assert fatal == {"symbol": "E_INTERRUPT", "value": 0x00020006}
    responses = [
        event for event in events
        if event["channel"] == "B"
        and event["control"] in {"MSI", "CQ_HEAD_ACK"}
        and event["response"] == "SLVERR"
    ]
    assert len(responses) == 2
    assert len({event["tick"] for event in responses}) == 1
    candidates = [
        bytes.fromhex(wire) for wire in metrics["_fatal_candidates"]
    ]
    assert metrics["_fatal_state"]["candidate_count"] == 2
    assert {int.from_bytes(wire[8:10], "little") for wire in candidates} == {
        4, 5,
    }
    assert {int.from_bytes(wire[:8], "little") for wire in candidates} == {
        responses[0]["tick"],
    }


def test_normal_sq_resolution_is_discarded_at_same_tick_fatal_cut(tmp_path):
    events, final, metrics, fatal = _run(
        "NORMAL_SQ_AT_FATAL_CUT", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    responses = [
        event for event in events
        if event["channel"] in {"B", "R"}
        and event["control"] in {"SQ_DOORBELL", "SQ_ENTRY"}
    ]
    assert [(event["control"], event["response"]) for event in responses] == [
        ("SQ_ENTRY", "OKAY"),
        ("SQ_DOORBELL", "SLVERR"),
    ]
    assert len({event["tick"] for event in responses}) == 1
    assert not any(event["kind"] == "SQ_CONSUME" for event in events)
    assert metrics["sq_intakes_released"] == 0
    assert metrics["fatal_sq_intakes"] == 1
    assert final["npu_sq_consumer_seq"] == 0
    assert metrics["_fatal_state"]["candidate_count"] == 1
    assert metrics["_fatal_state"]["source_class"] == 1
    assert metrics["_fatal_sq_intakes"][0]["terminal_evidence"] == "R_OK"


def test_pending_fatal_still_collects_same_tick_sq_error(tmp_path):
    events, final, metrics, fatal = _run(
        "SQ_ERROR_AT_FATAL_CUT", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    responses = [
        event for event in events
        if event["channel"] in {"B", "R"}
        and event["control"] in {"SQ_DOORBELL", "SQ_ENTRY"}
    ]
    assert [(event["control"], event["response"]) for event in responses] == [
        ("SQ_DOORBELL", "SLVERR"),
        ("SQ_ENTRY", "SLVERR"),
    ]
    assert len({event["tick"] for event in responses}) == 1
    candidates = [
        bytes.fromhex(wire) for wire in metrics["_fatal_candidates"]
    ]
    assert metrics["_fatal_state"]["candidate_count"] == 2
    assert {int.from_bytes(wire[8:10], "little") for wire in candidates} == {
        0, 1,
    }
    assert metrics["_fatal_state"]["source_class"] == 0
    assert metrics["sq_intakes_released"] == 0
    assert metrics["fatal_sq_intakes"] == 1
    assert final["npu_sq_consumer_seq"] == 0
    assert metrics["_fatal_sq_intakes"][0]["first_error"] == (
        "E_AGENT_PROTOCOL_FATAL"
    )


def test_driver_publication_commit_is_discarded_at_same_tick_fatal_cut(
    tmp_path,
):
    events, final, metrics, fatal = _run(
        "DRIVER_COMMIT_AT_FATAL_CUT", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    responses = [
        event for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["channel"] in {"B", "R"}
        and event["control"] in {"SQ_DOORBELL", "SQ_ENTRY"}
    ]
    assert {(event["control"], event["response"])
            for event in responses} == {
        ("SQ_DOORBELL", "OKAY"), ("SQ_ENTRY", "SLVERR")
    }
    assert len({event["tick"] for event in responses}) == 1
    assert not any(event["kind"] == "PUBLICATION_COMMIT" for event in events)
    assert final["sq_committed_producer_seq"] == 0
    assert final["live_submissions"] == 1
    assert metrics["fatal_publications"] == 1
    assert len(metrics["_fatal_publications"]) == 1
    publication = metrics["_fatal_publications"][0]
    assert publication["terminal_evidence"] == "B_OK"
    assert not publication["ambiguous"]


def test_serial_msi_uses_lowest_free_id(tmp_path):
    events, _, _, fatal = _run("DEPTH2_WRAP", tmp_path)
    assert fatal is None
    msi_aw = [
        event for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "MSI"
        and event["channel"] == "AW"
    ]
    assert [event["axi_id"] for event in msi_aw] == [32, 32, 32]


def test_sq_intake_id_is_shared_by_candidate_and_fatal_record(tmp_path):
    _, _, metrics, fatal = _run(
        "SECOND_SQ_READ_ERROR", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    assert metrics["sq_intakes_created"] == 2
    assert metrics["sq_intakes_released"] == 1
    assert len(metrics["_fatal_sq_intakes"]) == 1
    intake = metrics["_fatal_sq_intakes"][0]
    candidate = bytes.fromhex(metrics["_fatal_state"]["candidate_key_wire"])
    key_bytes = int.from_bytes(candidate[26:28], "little")
    assert key_bytes == 16
    assert int.from_bytes(candidate[28:36], "little") == intake["intake_id"]
    assert int.from_bytes(candidate[36:44], "little") == 1
    assert intake["intake_id"] == 2
    assert intake["expected_sq_seq"] == 1


def test_cross_component_fatal_transfers_live_sq_intake(tmp_path):
    events, _, metrics, fatal = _run(
        "AMBIGUOUS_B", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    assert metrics["sq_intakes_created"] == 1
    assert metrics["sq_intakes_released"] == 0
    assert metrics["live_sq_intakes"] == 0
    assert len(metrics["_fatal_sq_intakes"]) == 1
    intake = metrics["_fatal_sq_intakes"][0]
    assert intake["intake_id"] == 1
    assert intake["expected_sq_seq"] == 0
    assert intake["read_tag"] != 0
    assert intake["state_at_cut"] == "WAIT_R"
    assert intake["terminal_evidence"] == "R_OK"
    assert any(
        event["control"] == "SQ_ENTRY"
        and event["channel"] == "R"
        and event["response"] == "OKAY"
        for event in events
    )


def test_pre_ar_fatal_transfers_reserved_sq_intake(tmp_path):
    events, _, metrics, fatal = _run(
        "PRE_AR_INTAKE_FATAL", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    assert not any(
        event["control"] == "SQ_ENTRY" and event["channel"] == "AR"
        for event in events
    )
    assert metrics["sq_intakes_created"] == 1
    assert metrics["sq_intakes_released"] == 0
    assert metrics["live_sq_intakes"] == 0
    intake = metrics["_fatal_sq_intakes"]
    assert len(intake) == 1
    assert intake[0]["intake_id"] == 1
    assert intake[0]["expected_sq_seq"] == 0
    assert intake[0]["read_tag"] == 0
    assert intake[0]["state_at_cut"] == "PRE_AR"
    assert intake[0]["terminal_evidence"] == "NONE"


def test_same_edge_profile_has_three_callbacks_and_ordered_ack_prefix(tmp_path):
    events, final, _, fatal = _run("SAME_EDGE_CALLBACKS", tmp_path)
    assert fatal is None
    irq = [
        event for event in events
        if event["kind"] == "IRQ_DELIVER" and event["object"] == "MSI"
    ]
    commits = [
        event for event in events
        if event["kind"] == "MSI_TARGET_COMMIT"
        and event["object"] == "MSI"
    ]
    assert [event["absolute_seq"] for event in commits] == [1, 2, 3]
    assert len({event["tick"] for event in commits}) == 1
    assert [event["absolute_seq"] for event in irq] == [1, 2, 3]
    assert len({event["tick"] for event in irq}) == 1
    assert commits[0]["tick"] < irq[0]["tick"]
    cq = [event for event in events if event["kind"] == "CQ_CONSUME"]
    assert [event["absolute_seq"] for event in cq] == [0, 1, 2]
    ack = [
        event["absolute_seq"] for event in events
        if event["kind"] == "CQ_ACK_TARGET_COMMIT"
    ]
    assert ack == [1, 2, 3]
    assert final["npu_cq_ack_seq"] == 3


def test_runtime_callback_hole_holds_visible_prefix(tmp_path):
    events, final, _, fatal = _run("MSI_CALLBACK_HOLE", tmp_path)
    assert fatal is None
    commits = [
        event for event in events
        if event["kind"] == "MSI_TARGET_COMMIT"
    ]
    irq = [event for event in events if event["kind"] == "IRQ_DELIVER"]
    assert [event["absolute_seq"] for event in commits] == [2, 3, 1]
    assert [event["absolute_seq"] for event in irq] == [1, 2, 3]
    assert irq[0]["tick"] >= commits[-1]["tick"]
    assert final["cq_notified_seq"] == 3


def test_runtime_duplicate_callback_is_idempotent(tmp_path):
    events, final, metrics, fatal = _run(
        "MSI_CALLBACK_DUPLICATE", tmp_path
    )
    assert fatal is None
    assert metrics["duplicate_msi_callbacks"] == 1
    assert [
        event["absolute_seq"]
        for event in events if event["kind"] == "IRQ_DELIVER"
    ] == [1, 2, 3]
    assert final["cq_notified_seq"] == 3


@pytest.mark.parametrize(
    ("profile", "permutation"),
    (
        ("MSI_CALLBACK_PERM_123", [1, 2, 3]),
        ("MSI_CALLBACK_PERM_132", [1, 3, 2]),
        ("MSI_CALLBACK_PERM_213", [2, 1, 3]),
        ("MSI_CALLBACK_PERM_231", [2, 3, 1]),
        ("MSI_CALLBACK_PERM_312", [3, 1, 2]),
        ("MSI_CALLBACK_PERM_321", [3, 2, 1]),
    ),
)
def test_runtime_callback_permutations_preserve_visible_prefix(
    tmp_path, profile, permutation
):
    events, final, _, fatal = _run(profile, tmp_path)
    assert fatal is None
    commits = [
        event for event in events if event["kind"] == "MSI_TARGET_COMMIT"
    ]
    assert [event["absolute_seq"] for event in commits] == permutation
    assert len({event["tick"] for event in commits}) == 1
    assert [
        event["absolute_seq"] for event in events
        if event["kind"] == "IRQ_DELIVER"
    ] == [1, 2, 3]
    assert [
        event["absolute_seq"] for event in events
        if event["kind"] == "CQ_ACK_TARGET_COMMIT"
    ] == [1, 2, 3]
    assert final["cq_notified_seq"] == 3


def test_cq_full_backpressures_admission_at_reserved_capacity(tmp_path):
    events, _, metrics, fatal = _run("CQ_FULL", tmp_path)
    assert fatal is None
    assert metrics["peak_live_cq_obligations"] == 2
    assert metrics["cq_backpressure"] == 1
    assert metrics["core_starts"] == 3
    reserves = [
        event for event in events
        if event["kind"] == "CQ_OBLIGATION_RESERVE"
    ]
    retirements = [
        event for event in events
        if event["kind"] == "CQ_OBLIGATION_RETIRE"
    ]
    assert reserves[2]["tick"] >= retirements[0]["tick"]


def test_request_mismatch_changes_observed_cq_before_rejection(tmp_path):
    events, final, metrics, fatal = _run(
        "CQ_REQUEST_MISMATCH", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    injected = [
        event for event in events if event["kind"] == "FAULT_INJECT"
    ]
    rejected = [
        event for event in events if event["kind"] == "CQ_REJECT"
    ]
    consumed = [
        event for event in events if event["kind"] == "CQ_CONSUME"
    ]
    assert [event["request_id"] for event in injected] == [2]
    assert [event["request_id"] for event in rejected] == [2]
    assert consumed == []
    assert metrics["cq_request_mismatch"] == 1
    assert metrics.get("cq_identity_retries", 0) == 0
    assert not any(event["control"] == "CQ_HEAD_ACK" for event in events)
    assert final["npu_cq_ack_seq"] == 0
    assert final["live_cq_obligations"] == 0
    assert final["fatal_cq_obligations"] == 1


@pytest.mark.parametrize(
    ("profile", "metric", "field", "expected"),
    (
        ("STALE_CQ_SEQ", "stale_cq_entries", "absolute_seq", 1),
        ("CQ_COOKIE_MISMATCH", "cq_cookie_mismatch", "cookie", 0),
    ),
)
def test_other_cq_identity_faults_use_injected_bytes_without_retry(
    tmp_path, profile, metric, field, expected
):
    events, final, metrics, fatal = _run(
        profile, tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    injected = [event for event in events if event["kind"] == "FAULT_INJECT"]
    rejected = [event for event in events if event["kind"] == "CQ_REJECT"]
    assert len(injected) == len(rejected) == 1
    assert injected[0][field] == rejected[0][field] == expected
    assert metrics[metric] == 1
    assert not any(event["kind"] == "CQ_CONSUME" for event in events)
    assert not any(event["control"] == "CQ_HEAD_ACK" for event in events)
    assert final["fatal_cq_obligations"] == 1


def test_terminal_ready_tick_orders_later_high_qos_request_first(tmp_path):
    events, final, _, fatal = _run("QOS_OUT_OF_ORDER", tmp_path)
    assert fatal is None
    ready = [event for event in events if event["kind"] == "TERMINAL_READY"]
    assert [event["request_id"] for event in ready] == [2, 1]
    assert ready[0]["tick"] < ready[1]["tick"]
    assigned = [event for event in events if event["kind"] == "CQ_ASSIGN"]
    assert [event["absolute_seq"] for event in assigned] == [0, 1]
    assert [event["request_id"] for event in assigned] == [2, 1]
    consumed = [event for event in events if event["kind"] == "CQ_CONSUME"]
    assert [event["request_id"] for event in consumed] == [2, 1]
    assert final["npu_cq_ack_seq"] == 2


def test_same_tick_normal_and_sequence_only_use_typed_tie_break(tmp_path):
    events, final, _, fatal = _run("SAME_TICK_SEQ_ONLY", tmp_path)
    assert fatal is None
    ready = [event for event in events if event["kind"] == "TERMINAL_READY"]
    assert len(ready) == 2
    assert ready[0]["tick"] == ready[1]["tick"]
    assert {(event["absolute_seq"], event["status"]) for event in ready} == {
        (0, "SUCCESS"),
        (1, "SQ_SEQ_ONLY_ERROR"),
    }
    assigned = [event for event in events if event["kind"] == "CQ_ASSIGN"]
    assert [event["absolute_seq"] for event in assigned] == [0, 1]
    assert [event["request_id"] for event in assigned] == [0, 1]
    assert [event["status"] for event in assigned] == ["ERROR", "SUCCESS"]
    consumed = [event for event in events if event["kind"] == "CQ_CONSUME"]
    assert [event["request_id"] for event in consumed] == [0, 1]
    assert final["npu_cq_ack_seq"] == 2


def test_acceptance_conflict_is_observed_on_real_doorbell_transaction(tmp_path):
    events, final, metrics, fatal = _run(
        "SAME_EDGE_ACCEPTANCE_CONFLICT", tmp_path, expected_exit=20
    )
    assert fatal == {
        "symbol": "E_AGENT_PROTOCOL_FATAL",
        "value": 0x00020002,
    }
    doorbell = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "SQ_DOORBELL"
    ]
    assert [event["channel"] for event in doorbell] == ["AW", "W", "B"]
    assert doorbell[-1]["response"] == "SLVERR"
    assert any(event["kind"] == "DOORBELL_TARGET_COMMIT" for event in events)
    assert metrics["fatal_publications"] == 1
    assert metrics["ambiguous_publications"] == 0
    assert len(metrics["_fatal_publications"]) == 1
    publication = metrics["_fatal_publications"][0]
    assert {
        key: value
        for key, value in publication.items()
        if not key.endswith("_token_wire")
    } == {
        "publication_id": 1,
        "doorbell_issue_ordinal": 1,
        "base_seq": 0,
        "pending_tail": 1,
        "request_ids": [1],
        "state_at_cut": "AW_W_ACCEPTED",
        "terminal_evidence": "B_ERROR",
        "target_commit_evidence": "YES",
        "ambiguous": False,
    }
    assert len(publication["transaction_token_wire"]) == 64
    assert len(publication["response_token_wire"]) == 64
    assert final["fatal"]


@pytest.mark.parametrize(
    ("profile", "symbol", "value", "site_id", "fatal_cq_count"),
    (
        ("SQ_SEQUENCE_MISMATCH", "E_AGENT_PROTOCOL_FATAL", 0x00020002, 1, 0),
        ("MSI_B_ERROR", "E_INTERRUPT", 0x00020006, 14, 1),
        (
            "ACK_NO_TARGET_COMMIT",
            "E_COMPLETION_PATH_AXI",
            0x00020003,
            11,
            1,
        ),
    ),
)
def test_fatal_cut_has_typed_error_and_unique_cq_ownership(
    tmp_path, profile, symbol, value, site_id, fatal_cq_count
):
    _, final, metrics, fatal = _run(profile, tmp_path, expected_exit=20)
    assert fatal == {"symbol": symbol, "value": value}
    assert metrics["_fatal_state"]["site_id"] == site_id
    assert final["live_cq_obligations"] == 0
    assert metrics["fatal_cq_obligations"] == fatal_cq_count
    assert len(metrics["_fatal_cq_obligations"]) == fatal_cq_count
    assert all(
        record["state_at_cut"] in {
            "PRETERMINAL",
            "TERMINAL_PENDING",
            "POSTED_UNACKED",
            "EARLY_ACK_WAIT_MSI_B",
        }
        for record in metrics["_fatal_cq_obligations"]
    )


@pytest.mark.parametrize(
    ("profile", "record_key", "terminal", "target_commit"),
    (
        ("MSI_B_ERROR", "_fatal_msi_records", "B_ERROR", "NO"),
        (
            "ACK_NO_TARGET_COMMIT",
            "_fatal_ack_records",
            "B_ERROR",
            "NO",
        ),
        ("CQ_REQUEST_MISMATCH", "_fatal_msi_records", "B_OK", "YES"),
    ),
)
def test_fatal_control_records_are_runtime_owned(
    tmp_path, profile, record_key, terminal, target_commit
):
    _, _, metrics, _ = _run(profile, tmp_path, expected_exit=20)
    records = metrics[record_key]
    assert len(records) == 1
    assert records[0]["issue_ordinal"] > 0
    assert records[0]["axi_id"] > 0
    assert records[0]["state_at_cut"] in {"WAIT_B", "ISSUED"}
    assert records[0]["terminal_evidence"] == terminal
    assert records[0]["target_commit_evidence"] == target_commit
    assert len(records[0]["transaction_token_wire"]) == 64
    assert (records[0]["response_token_wire"] is None) == (
        terminal == "NONE"
    )


@pytest.mark.parametrize(
    ("suffix", "msi_tick", "notified", "rob"),
    (("BEFORE", 300001, 1, 0), ("SAME", 321001, 0, 1),
     ("AFTER", 322001, 0, 1), ("LATE", 3300001, 0, 1)),
)
def test_msi_response_preserves_fatal_cut_state(
    tmp_path, suffix, msi_tick, notified, rob
):
    events, final, metrics, fatal = _run(
        "ACK_ERROR_MSI_" + suffix, tmp_path, expected_exit=20
    )
    assert fatal["symbol"] == "E_COMPLETION_PATH_AXI"
    assert [e["tick"] for e in events if e["kind"] == "FATAL"] == [321001]
    assert [(e["tick"], e["response"]) for e in events
            if e["control"] == "MSI" and e["channel"] == "B"] == [(msi_tick, "OKAY")]
    assert final["cq_notified_seq"] == metrics["npu_cq_notified_seq"] == notified
    assert final["msi_rob_entries"] == metrics["msi_rob_entries"] == rob
    snapshot = _fatal_snapshot(
        "PROTO-30", "ack_error_msi_" + suffix.lower(), "0" * 64,
        _fatal_error(metrics, fatal), final, metrics, _ledger(final, metrics),
    )
    assert snapshot["retained_sequence_slot_snapshot"]["cq_notified_seq"] == notified
    assert snapshot["ledger_summary"]["msi_rob_entries"] == rob
    assert final["cq_msi_issued_seq"] == 1
    assert final["live_cq_obligations"] == 0
    assert metrics["fatal_cq_obligations"] == 1
    assert len(metrics["_fatal_cq_obligations"]) == 1
    assert metrics["_fatal_msi_records"][0]["terminal_evidence"] == "B_OK"
    assert not any(e["kind"] == "CQ_OBLIGATION_RETIRE" for e in events)


@pytest.mark.parametrize(("profile", "ack", "owned"), (
    ("ACK_NO_TARGET_COMMIT", 0, 1), ("ACK_POSTCOMMIT_ERROR", 1, 0),
))
def test_fatal_ack_snapshot_uses_npu_target_watermark(
    tmp_path, profile, ack, owned
):
    events, final, metrics, fatal = _run(profile, tmp_path, expected_exit=20)
    assert fatal["symbol"] == "E_COMPLETION_PATH_AXI"
    assert metrics.get("npu_cq_ack_seq", 0) == ack
    assert final["npu_cq_ack_seq"] == ack
    snapshot = _fatal_snapshot(
        "PROTO-29", profile.lower(), "0" * 64, _fatal_error(metrics, fatal),
        final, metrics, _ledger(final, metrics),
    )
    assert snapshot["retained_sequence_slot_snapshot"]["cq_ack_received_seq"] == ack
    assert metrics["driver_cq_ack_seq"] == 0
    assert metrics["fatal_cq_obligations"] == owned
    assert len(metrics["_fatal_cq_obligations"]) == owned
    retires = [e for e in events if e["kind"] == "CQ_OBLIGATION_RETIRE"]
    assert len(retires) == ack
    if ack:
        assert retires[0]["tick"] < metrics["_fatal_state"]["observed_tick"]


@pytest.mark.parametrize(("driver_period", "retired"), ((3, False), (7, True)))
def test_retirement_at_fatal_edge_preserves_tick_start_ownership(
    tmp_path, driver_period, retired
):
    config = tmp_path / "retirement_edge.py"
    config.write_text(
        f"""import runpy
import sys
import m5
from m5.objects import Root, SrcClockDomain
sys.path.insert(0, {str(CONFIG.parent)!r})
import gate3_profiles

original_plans = gate3_profiles.target_plans
original_instantiate = m5.instantiate

def plans(profile):
    result = original_plans(profile)
    result["planned_b_ejection"].extend((
        gate3_profiles._b_delay(3, gate3_profiles._src1_write(5), 21),
        gate3_profiles._b_delay(2, 1, 2),
    ))
    return result

def instantiate(*args, **kwargs):
    system = Root.getInstance().system
    system.gate3_frontend.clk_domain = SrcClockDomain(
        clock="1ps", voltage_domain=system.voltage_domain)
    system.gate3_driver.clk_domain = SrcClockDomain(
        clock="{driver_period}ps", voltage_domain=system.voltage_domain)
    return original_instantiate(*args, **kwargs)

gate3_profiles.target_plans = plans
m5.instantiate = instantiate
runpy.run_path({str(CONFIG)!r}, run_name="__main__")
"""
    )
    events, final, metrics, fatal = _run(
        "ACK_POSTCOMMIT_ERROR", tmp_path / "sim", expected_exit=20,
        config=config,
    )
    assert fatal == {"symbol": "E_COMPLETION_PATH_AXI", "value": 0x00020003}
    msi = [event for event in events
           if event["control"] == "MSI" and event["channel"] == "B"]
    assert [(event["tick"], event["response"]) for event in msi] == [
        (317001, "OKAY"),
    ]
    ack = [event for event in events
           if event["control"] == "CQ_HEAD_ACK" and event["channel"] == "B"]
    fatal_tick = 317003 if retired else 317002
    assert [(event["tick"], event["response"]) for event in ack] == [
        (fatal_tick, "SLVERR"),
    ]
    assert [event["tick"] for event in events if event["kind"] == "FATAL"] == [
        fatal_tick,
    ]
    assert [event["tick"] for event in events
            if event["kind"] == "CQ_OBLIGATION_RETIRE"] == (
        [317002] if retired else []
    )
    assert final["npu_cq_ack_seq"] == final["cq_notified_seq"] == 1
    assert final["msi_rob_entries"] == 0
    assert final["live_cq_obligations"] == 0
    assert metrics["fatal_cq_obligations"] == int(not retired)
    assert len(metrics.get("_fatal_cq_obligations", [])) == int(not retired)
    assert metrics["_fatal_state"]["candidate_count"] == 1

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mesh_ir.gate3_oracle import (
    Gate3OracleError,
    canonical_observation_bytes,
    load_observation,
    validate_observation,
)


NON_MSI_IDS = {
    "SQ_DOORBELL": 1,
    "SQ_HEAD_UPDATE": 2,
    "CQ_TAIL_UPDATE": 3,
    "CQ_HEAD_ACK": 4,
}

EVENT_PHASES = {
    "LOCAL_VISIBLE": 0,
    "RELEASE_FENCE_DONE": 0,
    "AXI_ACCEPT": 0,
    "DOORBELL_TARGET_COMMIT": 1,
    "CQ_ACK_TARGET_COMMIT": 1,
    "MSI_TARGET_COMMIT": 1,
    "FATAL": 2,
    "PUBLICATION_COMMIT": 3,
    "PUBLICATION_ROLLBACK": 3,
    "SQ_CONSUME": 3,
    "CQ_OBLIGATION_RESERVE": 3,
    "CAPACITY_ACCEPT": 3,
    "CQ_ASSIGN": 3,
    "CQ_CONSUME": 3,
    "CQ_OBLIGATION_RETIRE": 3,
    "SLOT_REUSE": 3,
    "CORE_START": 4,
    "IRQ_DELIVER": 4,
}


def event(kind, tick, **values):
    row = {
        "ordinal": 0,
        "tick": tick,
        "phase": EVENT_PHASES[kind],
        "kind": kind,
        "object": None,
        "absolute_seq": None,
        "request_id": None,
        "cookie": None,
        "slot": None,
        "generation": None,
        "txn": None,
        "channel": None,
        "direction": None,
        "control": None,
        "axi_id": None,
        "response": None,
        "bytes": 0,
        "wstrb": None,
        "status": None,
    }
    row.update(values)
    return row


def axi(tick, txn, channel, direction, control, axi_id, **values):
    return event(
        "AXI_ACCEPT",
        tick,
        txn=txn,
        channel=channel,
        direction=direction,
        control=control,
        axi_id=axi_id,
        **values,
    )


def write_transaction(events, tick, txn, direction, control, axi_id, **identity):
    events.extend(
        [
            axi(tick, txn, "AW", direction, control, axi_id, **identity),
            axi(
                tick + 1,
                txn,
                "W",
                direction,
                control,
                axi_id,
                bytes=8,
                wstrb="ff",
                **identity,
            ),
            axi(
                tick + 2,
                txn,
                "B",
                "NPU_TO_DRIVER" if direction == "DRIVER_TO_NPU" else "DRIVER_TO_NPU",
                control,
                axi_id,
                response="OKAY",
                **identity,
            ),
        ]
    )


def read_transaction(events, tick, txn, control, axi_id, byte_count, **identity):
    events.extend(
        [
            axi(
                tick,
                txn,
                "AR",
                "NPU_TO_DRIVER",
                control,
                axi_id,
                **identity,
            ),
            axi(
                tick + 1,
                txn,
                "R",
                "DRIVER_TO_NPU",
                control,
                axi_id,
                response="OKAY",
                bytes=byte_count,
                **identity,
            ),
        ]
    )


def complete_observation():
    events = [
        event("LOCAL_VISIBLE", 1, object="PROMPT", request_id=10),
        event("LOCAL_VISIBLE", 2, object="PARAMETER", request_id=10),
        event(
            "LOCAL_VISIBLE",
            3,
            object="SQ_ENTRY",
            absolute_seq=0,
            slot=0,
            generation=0,
            request_id=10,
            cookie=90,
        ),
        event("RELEASE_FENCE_DONE", 4, request_id=10),
    ]
    write_transaction(
        events,
        5,
        1,
        "DRIVER_TO_NPU",
        "SQ_DOORBELL",
        1,
        absolute_seq=1,
        request_id=10,
    )
    events.append(
        event(
            "DOORBELL_TARGET_COMMIT",
            7,
            object="DOORBELL",
            absolute_seq=1,
            request_id=10,
        )
    )
    read_transaction(
        events,
        9,
        2,
        "SQ_ENTRY",
        11,
        64,
        absolute_seq=0,
        request_id=10,
        cookie=90,
    )
    events.extend(
        [
            event(
                "CQ_OBLIGATION_RESERVE",
                11,
                object="CQ_ENTRY",
                request_id=10,
                cookie=90,
            ),
            event(
                "SQ_CONSUME",
                12,
                object="SQ_ENTRY",
                absolute_seq=0,
                slot=0,
                generation=0,
                request_id=10,
                cookie=90,
            ),
        ]
    )
    write_transaction(
        events,
        13,
        3,
        "NPU_TO_DRIVER",
        "SQ_HEAD_UPDATE",
        2,
        absolute_seq=1,
        request_id=10,
    )
    events.append(
        event(
            "PUBLICATION_COMMIT",
            16,
            object="DOORBELL",
            absolute_seq=1,
            request_id=10,
        )
    )
    read_transaction(events, 17, 4, "PARAMETER", 12, 160, request_id=10)
    read_transaction(events, 19, 5, "PROMPT", 13, 128, request_id=10)
    events.extend(
        [
            event("CAPACITY_ACCEPT", 21, object="CONTEXT", request_id=10),
            event("CORE_START", 22, object="CONTEXT", request_id=10),
        ]
    )
    write_transaction(
        events,
        23,
        6,
        "NPU_TO_DRIVER",
        "OUTPUT",
        14,
        request_id=10,
        cookie=90,
    )
    write_transaction(
        events,
        26,
        7,
        "NPU_TO_DRIVER",
        "METADATA",
        15,
        request_id=10,
        cookie=90,
    )
    events.append(
        event(
            "CQ_ASSIGN",
            29,
            object="CQ_ENTRY",
            absolute_seq=0,
            slot=0,
            generation=0,
            request_id=10,
            cookie=90,
            status="SUCCESS",
        )
    )
    write_transaction(
        events,
        30,
        8,
        "NPU_TO_DRIVER",
        "CQ_ENTRY",
        16,
        absolute_seq=0,
        request_id=10,
        cookie=90,
        status="SUCCESS",
    )
    write_transaction(
        events,
        33,
        9,
        "NPU_TO_DRIVER",
        "CQ_TAIL_UPDATE",
        3,
        absolute_seq=1,
        request_id=10,
    )
    write_transaction(
        events,
        36,
        10,
        "NPU_TO_DRIVER",
        "MSI",
        17,
        absolute_seq=1,
        request_id=10,
    )
    events.extend(
        [
            event("MSI_TARGET_COMMIT", 38, object="MSI", absolute_seq=1),
            event("IRQ_DELIVER", 38, object="MSI", absolute_seq=1),
            event(
                "CQ_CONSUME",
                39,
                object="CQ_ENTRY",
                absolute_seq=0,
                slot=0,
                generation=0,
                request_id=10,
                cookie=90,
                status="SUCCESS",
            ),
        ]
    )
    write_transaction(
        events,
        40,
        11,
        "DRIVER_TO_NPU",
        "CQ_HEAD_ACK",
        4,
        absolute_seq=1,
        request_id=10,
    )
    events.extend(
        [
            event(
                "CQ_ACK_TARGET_COMMIT",
                42,
                object="CQ_ACK",
                absolute_seq=1,
                request_id=10,
            ),
            event(
                "SLOT_REUSE",
                43,
                object="CQ_ENTRY",
                absolute_seq=0,
                slot=0,
                generation=0,
                request_id=10,
            ),
            event(
                "CQ_OBLIGATION_RETIRE",
                44,
                object="CQ_ENTRY",
                absolute_seq=0,
                slot=0,
                generation=0,
                request_id=10,
                cookie=90,
            ),
        ]
    )
    for ordinal, row in enumerate(events):
        row["ordinal"] = ordinal
    return {
        "schema": "ai_mesh_gate3_observation_v1",
        "version": 1,
        "id": "PROTO-14",
        "subcase": "directional_traffic",
        "configuration": {
            "sq_depth": 2,
            "cq_depth": 2,
            "control_bytes": 8,
            "non_msi_axi_ids": dict(NON_MSI_IDS),
        },
        "events": events,
        "final": {
            "sq_tentative_producer_seq": 1,
            "sq_committed_producer_seq": 1,
            "sq_observed_head_seq": 1,
            "sq_reusable_head_seq": 1,
            "npu_sq_consumer_seq": 1,
            "npu_cq_producer_seq": 1,
            "cq_msi_issued_seq": 1,
            "cq_notified_seq": 1,
            "driver_cq_consumer_seq": 1,
            "npu_cq_ack_seq": 1,
            "live_submissions": 0,
            "live_contexts": 0,
            "live_cq_obligations": 0,
            "msi_rob_entries": 0,
            "ack_wait_b": 0,
            "fatal": False,
            "core_starts": 1,
            "cq_assignments": 1,
            "irq_deliveries": 1,
        },
    }


def event_index(observation, kind, control=None, channel=None):
    return next(
        index
        for index, row in enumerate(observation["events"])
        if row["kind"] == kind
        and (control is None or row["control"] == control)
        and (channel is None or row["channel"] == channel)
    )


def resequence(observation):
    for ordinal, row in enumerate(observation["events"]):
        row["ordinal"] = ordinal


def test_complete_protocol_lifecycle_is_accepted():
    result = validate_observation(complete_observation())
    assert result == (
        "event_order",
        "ring_slot_generation",
        "axi_direction",
        "axi_transaction_lifecycle",
        "control_update_contract",
        "submission_publication",
        "core_start_order",
        "completion_publication",
        "cq_identity",
        "cq_slot_lifetime",
        "absolute_sequence_state",
        "terminal_ownership",
    )


def test_schema_rejects_extra_event_field():
    observation = complete_observation()
    observation["events"][0]["extra"] = 1
    with pytest.raises(Gate3OracleError, match="schema"):
        validate_observation(observation)


def test_u64_json_rejects_noncanonical_small_hex():
    observation = complete_observation()
    observation["events"][0]["tick"] = "0x0000000000000001"
    with pytest.raises(Gate3OracleError, match="u64-json"):
        validate_observation(observation)


def test_event_order_rejects_tick_regression():
    observation = complete_observation()
    observation["events"][1]["tick"] = 0
    with pytest.raises(Gate3OracleError, match="event order"):
        validate_observation(observation)


def test_event_order_rejects_noncanonical_same_edge_phase():
    observation = complete_observation()
    index = event_index(observation, "PUBLICATION_COMMIT")
    observation["events"][index]["phase"] = 0
    with pytest.raises(Gate3OracleError, match="canonical edge phase"):
        validate_observation(observation)


def test_ring_slot_and_generation_are_derived_from_absolute_sequence():
    observation = complete_observation()
    index = event_index(observation, "CQ_ASSIGN")
    observation["events"][index]["slot"] = 1
    with pytest.raises(Gate3OracleError, match="ring slot"):
        validate_observation(observation)
    observation = complete_observation()
    index = event_index(observation, "SQ_CONSUME")
    observation["events"][index]["generation"] = 1
    with pytest.raises(Gate3OracleError, match="ring generation"):
        validate_observation(observation)


def test_doorbell_requires_local_visibility_and_release_fence():
    observation = complete_observation()
    index = event_index(observation, "RELEASE_FENCE_DONE")
    observation["events"].pop(index)
    resequence(observation)
    with pytest.raises(Gate3OracleError, match="release fence"):
        validate_observation(observation)


def test_protocol_window_can_observe_an_unbound_doorbell():
    observation = complete_observation()
    observation["events"] = [
        row
        for row in observation["events"]
        if row["kind"] == "AXI_ACCEPT"
        and row["control"] == "SQ_DOORBELL"
    ]
    for row in observation["events"]:
        row["request_id"] = None
    resequence(observation)
    for name in observation["final"]:
        if name == "fatal":
            observation["final"][name] = False
        else:
            observation["final"][name] = 0
    validate_observation(observation)


def test_committed_producer_waits_for_okay_doorbell_b():
    observation = complete_observation()
    index = event_index(observation, "AXI_ACCEPT", "SQ_DOORBELL", "B")
    observation["events"][index]["response"] = "SLVERR"
    with pytest.raises(Gate3OracleError, match="publication commit"):
        validate_observation(observation)


def test_core_start_waits_for_prompt_and_capacity():
    observation = complete_observation()
    index = event_index(observation, "CAPACITY_ACCEPT")
    observation["events"].pop(index)
    resequence(observation)
    with pytest.raises(Gate3OracleError, match="capacity"):
        validate_observation(observation)


def test_success_cq_waits_for_output_and_metadata_b():
    observation = complete_observation()
    index = event_index(observation, "AXI_ACCEPT", "METADATA", "B")
    observation["events"][index]["response"] = "SLVERR"
    with pytest.raises(Gate3OracleError, match="metadata"):
        validate_observation(observation)


def test_irq_waits_for_cq_commit_and_msi_target_commit():
    observation = complete_observation()
    index = event_index(observation, "IRQ_DELIVER")
    irq = observation["events"].pop(index)
    irq["tick"] = 31
    cq_b = event_index(observation, "AXI_ACCEPT", "CQ_ENTRY", "B")
    observation["events"].insert(cq_b, irq)
    resequence(observation)
    with pytest.raises(Gate3OracleError, match="IRQ"):
        validate_observation(observation)


def test_irq_requires_explicit_msi_target_commit():
    observation = complete_observation()
    index = event_index(observation, "MSI_TARGET_COMMIT")
    observation["events"].pop(index)
    resequence(observation)
    with pytest.raises(Gate3OracleError, match="MSI target commit"):
        validate_observation(observation)


def test_cq_consumer_requires_exact_identity():
    observation = complete_observation()
    index = event_index(observation, "CQ_CONSUME")
    observation["events"][index]["cookie"] = 91
    with pytest.raises(Gate3OracleError, match="CQ identity"):
        validate_observation(observation)


def test_cq_slot_reuse_waits_for_ack_target_commit():
    observation = complete_observation()
    reuse = event_index(observation, "SLOT_REUSE")
    row = observation["events"].pop(reuse)
    row["tick"] = 41
    response = event_index(observation, "AXI_ACCEPT", "CQ_HEAD_ACK", "B")
    observation["events"].insert(response, row)
    resequence(observation)
    with pytest.raises(Gate3OracleError, match="slot reuse"):
        validate_observation(observation)


def test_cq_ack_target_commit_is_an_absolute_sequence_update():
    observation = complete_observation()
    index = event_index(observation, "CQ_ACK_TARGET_COMMIT")
    observation["events"][index]["request_id"] = None
    validate_observation(observation)


def test_axi_direction_is_derived_from_channel_and_control():
    observation = complete_observation()
    index = event_index(observation, "AXI_ACCEPT", "SQ_ENTRY", "R")
    observation["events"][index]["direction"] = "NPU_TO_DRIVER"
    with pytest.raises(Gate3OracleError, match="direction"):
        validate_observation(observation)


def test_axi_channel_metadata_has_exact_shape():
    observation = complete_observation()
    index = event_index(observation, "AXI_ACCEPT", "OUTPUT", "W")
    observation["events"][index]["wstrb"] = None
    with pytest.raises(Gate3OracleError, match="W transfer has no WSTRB"):
        validate_observation(observation)
    observation = complete_observation()
    index = event_index(observation, "AXI_ACCEPT", "SQ_ENTRY", "AR")
    observation["events"][index]["bytes"] = 1
    with pytest.raises(Gate3OracleError, match="request channel carries data bytes"):
        validate_observation(observation)


def test_control_write_requires_full_wstrb_and_fixed_id():
    observation = complete_observation()
    index = event_index(observation, "AXI_ACCEPT", "CQ_HEAD_ACK", "W")
    observation["events"][index]["wstrb"] = "0f"
    with pytest.raises(Gate3OracleError, match="WSTRB"):
        validate_observation(observation)
    observation = complete_observation()
    for row in observation["events"]:
        if row["kind"] == "AXI_ACCEPT" and row["txn"] == 9:
            row["axi_id"] = 9
    with pytest.raises(Gate3OracleError, match="fixed AXI ID"):
        validate_observation(observation)


def test_non_msi_control_allows_only_one_transaction_in_flight():
    observation = complete_observation()
    first_b = event_index(observation, "AXI_ACCEPT", "SQ_DOORBELL", "B")
    overlap = [
        axi(
            6,
            99,
            "AW",
            "DRIVER_TO_NPU",
            "SQ_DOORBELL",
            1,
            absolute_seq=2,
            request_id=11,
        ),
        axi(
            6,
            99,
            "W",
            "DRIVER_TO_NPU",
            "SQ_DOORBELL",
            1,
            bytes=8,
            wstrb="ff",
            absolute_seq=2,
            request_id=11,
        ),
        axi(
            8,
            99,
            "B",
            "NPU_TO_DRIVER",
            "SQ_DOORBELL",
            1,
            response="OKAY",
            absolute_seq=2,
            request_id=11,
        ),
    ]
    observation["events"][first_b:first_b] = overlap[:2]
    target_commit = event_index(observation, "DOORBELL_TARGET_COMMIT")
    observation["events"].insert(target_commit + 1, overlap[2])
    resequence(observation)
    with pytest.raises(Gate3OracleError, match="more than one transaction in flight"):
        validate_observation(observation)


def test_fatal_cut_forbids_new_axi_issue():
    observation = complete_observation()
    fatal = event("FATAL", 22, object="CONTEXT")
    observation["events"].insert(event_index(observation, "CORE_START"), fatal)
    observation["final"]["fatal"] = True
    resequence(observation)
    with pytest.raises(Gate3OracleError, match="fatal cut"):
        validate_observation(observation)


def test_absolute_sequence_final_state_is_checked():
    observation = complete_observation()
    for name in (
        "npu_cq_producer_seq",
        "cq_msi_issued_seq",
        "cq_notified_seq",
        "driver_cq_consumer_seq",
    ):
        observation["final"][name] = 2
    with pytest.raises(Gate3OracleError, match="CQ consumer sequence"):
        validate_observation(observation)


def test_cq_obligation_retirement_requires_the_same_owner_identity():
    observation = complete_observation()
    index = event_index(observation, "CQ_OBLIGATION_RETIRE")
    observation["events"][index]["request_id"] = 11
    with pytest.raises(Gate3OracleError, match="without ownership"):
        validate_observation(observation)


def test_observation_input_is_not_mutated():
    observation = complete_observation()
    before = copy.deepcopy(observation)
    validate_observation(observation)
    assert observation == before


def test_file_loader_requires_canonical_unique_json(tmp_path):
    observation = complete_observation()
    path = tmp_path / "observation.json"
    path.write_bytes(canonical_observation_bytes(observation))
    assert load_observation(path) == observation
    path.write_text('{"schema":1,"schema":2}\n', encoding="utf-8")
    with pytest.raises(Gate3OracleError, match="duplicate JSON field"):
        load_observation(path)


def test_observation_cli_returns_machine_readable_result(tmp_path):
    path = tmp_path / "observation.json"
    path.write_bytes(canonical_observation_bytes(complete_observation()))
    repo = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [
            str(Path(sys.executable).resolve()),
            str(repo / "tests/gem5/ai_mesh/gate3/validate_observation.py"),
            str(path),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["status"] == "PASS"
    assert tuple(document["checks"]) == validate_observation(complete_observation())

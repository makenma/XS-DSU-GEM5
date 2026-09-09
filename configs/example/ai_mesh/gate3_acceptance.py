from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
MESH_IR_ROOT = REPO / "util" / "mesh_ir"
if str(MESH_IR_ROOT) not in sys.path:
    sys.path.insert(0, str(MESH_IR_ROOT))

from mesh_ir.acceptance import (
    ARTIFACT_BASENAMES,
    ContractError,
    atomic_write_json,
    canonical_digest,
    read_json_artifact,
    validate_schema,
)
from mesh_ir.gate3_contract import GATE3_CASES
from mesh_ir.gate3_oracle import Gate3OracleError, validate_observation
from mesh_ir.generated import agent_abi as ABI

from gate3_profiles import Gate3Profile, is_expected_fatal


FINAL_FIELDS = (
    "sq_tentative_producer_seq",
    "sq_committed_producer_seq",
    "sq_observed_head_seq",
    "sq_reusable_head_seq",
    "npu_sq_consumer_seq",
    "npu_cq_producer_seq",
    "cq_msi_issued_seq",
    "cq_notified_seq",
    "driver_cq_consumer_seq",
    "npu_cq_ack_seq",
    "live_submissions",
    "live_contexts",
    "live_cq_obligations",
    "msi_rob_entries",
    "ack_wait_b",
    "fatal",
    "core_starts",
    "cq_assignments",
    "irq_deliveries",
)

TRAFFIC_CLASSES = (
    "AGENT_TO_NPU_CONTROL",
    "NPU_TO_AGENT_CONTROL",
    "NPU_READ_AGENT_MEMORY",
    "NPU_WRITE_AGENT_MEMORY",
    "NPU_LOCAL_MEMORY",
    "NPU_P2P",
)

CONTROL_IDS = {
    "SQ_DOORBELL": 16,
    "SQ_HEAD_UPDATE": 18,
    "CQ_TAIL_UPDATE": 19,
    "CQ_HEAD_ACK": 17,
}

FULL_STAGE = {
    "agent_control_bytes": 16,
    "agent_control_packets": 2,
    "npu_control_bytes": 24,
    "npu_control_packets": 3,
    "npu_read_bytes": 288,
    "npu_read_packets": 3,
    "npu_write_bytes": 224,
    "npu_write_packets": 3,
}

CQ_IDENTITY_FAULT_PROFILES = frozenset({
    "STALE_CQ_SEQ",
    "CQ_REQUEST_MISMATCH",
    "CQ_COOKIE_MISMATCH",
})


def _optional(value):
    return None if value == "-" else int(value)


def parse_gate3_facts(path: Path):
    events = []
    metrics = {}
    fatal = None
    final = None
    fatal_sq_intakes = []
    fatal_cq_obligations = []
    fatal_ack_records = []
    fatal_msi_records = []
    fatal_publications = []
    fatal_state = None
    fatal_candidates = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("|")
        if not fields or fields[0] == "":
            continue
        if fields[0] == "EVENT":
            if len(fields) != 21:
                raise ContractError("Gate3 facts EVENT field count mismatch")
            events.append(
                {
                    "source_ordinal": len(events),
                    "tick": int(fields[1]),
                    "phase": int(fields[2]),
                    "kind": fields[3],
                    "object": None if fields[4] == "-" else fields[4],
                    "absolute_seq": _optional(fields[5]),
                    "request_id": _optional(fields[6]),
                    "cookie": _optional(fields[7]),
                    "slot": _optional(fields[8]),
                    "generation": _optional(fields[9]),
                    "txn": _optional(fields[10]),
                    "channel": None if fields[11] == "-" else fields[11],
                    "direction": None if fields[12] == "-" else fields[12],
                    "control": None if fields[13] == "-" else fields[13],
                    "axi_id": _optional(fields[14]),
                    "address": _optional(fields[15]),
                    "size": _optional(fields[16]),
                    "response": None if fields[17] == "-" else fields[17],
                    "bytes": int(fields[18]),
                    "wstrb": None if fields[19] == "-" else fields[19],
                    "status": None if fields[20] == "-" else fields[20],
                }
            )
        elif fields[0] == "FINAL":
            if len(fields) != 20:
                raise ContractError("Gate3 facts FINAL field count mismatch")
            values = [int(value) for value in fields[1:]]
            final = dict(zip(FINAL_FIELDS, values))
            final["fatal"] = bool(final["fatal"])
        elif fields[0] == "METRIC":
            if len(fields) != 3:
                raise ContractError("Gate3 facts METRIC field count mismatch")
            metrics[fields[1]] = int(fields[2])
        elif fields[0] == "FATAL":
            if len(fields) != 3:
                raise ContractError("Gate3 facts FATAL field count mismatch")
            fatal = {"symbol": fields[1], "value": int(fields[2])}
        elif fields[0] == "SQ_INTAKE":
            if len(fields) != 7:
                raise ContractError("Gate3 facts SQ_INTAKE field count mismatch")
            fatal_sq_intakes.append(
                {
                    "intake_id": int(fields[1]),
                    "expected_sq_seq": int(fields[2]),
                    "read_tag": int(fields[3]),
                    "first_error": fields[4],
                    "state_at_cut": fields[5],
                    "terminal_evidence": fields[6],
                }
            )
        elif fields[0] == "FATAL_CQ":
            if len(fields) != 7:
                raise ContractError("Gate3 facts FATAL_CQ field count mismatch")
            fatal_cq_obligations.append({
                "cq_obligation_id": int(fields[1]),
                "absolute_sq_seq": int(fields[2]),
                "request_id": int(fields[3]),
                "cq_seq": _optional(fields[4]),
                "slot_id": _optional(fields[5]),
                "state_at_cut": fields[6],
            })
        elif fields[0] in {"FATAL_ACK", "FATAL_MSI"}:
            if len(fields) != 9:
                raise ContractError(
                    f"Gate3 facts {fields[0]} field count mismatch"
                )
            record = {
                "issue_ordinal": int(fields[1]),
                "ack_seq" if fields[0] == "FATAL_ACK" else "tail":
                    int(fields[2]),
                "axi_id": int(fields[3]),
                "state_at_cut": fields[4],
                "terminal_evidence": fields[5],
                "target_commit_evidence": fields[6],
                "transaction_token_wire": fields[7],
                "response_token_wire": None if fields[8] == "-" else
                    fields[8],
            }
            target = fatal_ack_records if fields[0] == "FATAL_ACK" else \
                fatal_msi_records
            target.append(record)
        elif fields[0] == "FATAL_PUBLICATION":
            if len(fields) != 12:
                raise ContractError(
                    "Gate3 facts FATAL_PUBLICATION field count mismatch"
                )
            fatal_publications.append({
                "publication_id": int(fields[1]),
                "doorbell_issue_ordinal": int(fields[2]),
                "base_seq": int(fields[3]),
                "pending_tail": int(fields[4]),
                "request_ids": [
                    int(value) for value in fields[5].split(",") if value
                ],
                "state_at_cut": fields[6],
                "terminal_evidence": fields[7],
                "target_commit_evidence": fields[8],
                "ambiguous": bool(int(fields[9])),
                "transaction_token_wire": fields[10],
                "response_token_wire": fields[11],
            })
        elif fields[0] == "FATAL_STATE":
            if len(fields) != 14:
                raise ContractError("Gate3 facts FATAL_STATE field count mismatch")
            fatal_state = {
                "candidate_count": int(fields[1]),
                "observed_tick": int(fields[2]),
                "source_class": int(fields[3]),
                "site_domain": int(fields[4]),
                "site_id": int(fields[5]),
                "component_kind": int(fields[6]),
                "component_local_id": int(fields[7]),
                "endpoint_id": int(fields[8]),
                "object_kind": int(fields[9]),
                "issue_ordinal": int(fields[10]),
                "error_code": int(fields[11]),
                "candidate_key_wire": fields[12],
                "physical_source_token_wire": fields[13],
            }
        elif fields[0] == "FATAL_CANDIDATE":
            if len(fields) != 2:
                raise ContractError(
                    "Gate3 facts FATAL_CANDIDATE field count mismatch"
                )
            fatal_candidates.append(fields[1])
        else:
            raise ContractError(f"unknown Gate3 facts record {fields[0]}")
    if final is None:
        raise ContractError("Gate3 facts have no final state")
    if final["fatal"] != (fatal is not None):
        raise ContractError("Gate3 facts fatal state is inconsistent")
    if fatal is not None:
        if fatal_state is None or not fatal_candidates:
            raise ContractError("Gate3 facts have no authoritative fatal state")
        if fatal_state["error_code"] != fatal["value"]:
            raise ContractError("Gate3 fatal state detail code mismatch")
        if fatal_state["candidate_count"] != len(fatal_candidates):
            raise ContractError("Gate3 fatal candidate count mismatch")
        if fatal_state["candidate_key_wire"] not in fatal_candidates:
            raise ContractError("Gate3 first fatal is absent from candidates")
        if metrics.get("fatal_cq_obligations", 0) != len(
            fatal_cq_obligations
        ):
            raise ContractError("Gate3 fatal CQ ownership count mismatch")
        if metrics.get("fatal_publications", 0) != len(
            fatal_publications
        ):
            raise ContractError("Gate3 fatal publication count mismatch")
        if metrics.get("ambiguous_publications", 0) != sum(
            record["ambiguous"] for record in fatal_publications
        ):
            raise ContractError("Gate3 ambiguous publication count mismatch")
        for record in fatal_ack_records + fatal_msi_records:
            if (record["terminal_evidence"] == "NONE") != (
                record["response_token_wire"] is None
            ):
                raise ContractError(
                    "Gate3 fatal control terminal/token mismatch"
                )
        metrics["_fatal_state"] = fatal_state
        metrics["_fatal_candidates"] = fatal_candidates
        metrics["_fatal_cq_obligations"] = fatal_cq_obligations
        metrics["_fatal_ack_records"] = fatal_ack_records
        metrics["_fatal_msi_records"] = fatal_msi_records
        metrics["_fatal_publications"] = fatal_publications
        metrics["_fatal_sq_intakes"] = fatal_sq_intakes
    final["fatal_cq_obligations"] = len(fatal_cq_obligations)
    events.sort(key=lambda row: (row["tick"], row["phase"], row["source_ordinal"]))
    for ordinal, event in enumerate(events):
        event.pop("source_ordinal")
        event["ordinal"] = ordinal
    return events, final, metrics, fatal


def _events(events, **fields):
    return [
        event for event in events
        if all(event.get(name) == value for name, value in fields.items())
    ]


def _before(left, right):
    return (left["tick"], left["phase"], left["ordinal"]) < (
        right["tick"], right["phase"], right["ordinal"]
    )


def _last_before(events, target, **fields):
    matches = [event for event in _events(events, **fields) if _before(event, target)]
    return matches[-1] if matches else None


def _stage_for(profile: Gate3Profile):
    if profile.name == "EMPTY_DOORBELL":
        return "none"
    if profile.name in {"SQ_READ_ERROR", "SQ_SEQUENCE_MISMATCH"}:
        return "sq_read"
    if profile.name in {"SQ_CRC", "EARLY_SEQ_ONLY_CQ", "SQ_CRC_IDENTITY"}:
        return "sq_crc"
    if profile.name in {
        "AMBIGUOUS_B", "SAME_EDGE_ACCEPTANCE_CONFLICT",
        "NORMAL_SQ_AT_FATAL_CUT", "SQ_ERROR_AT_FATAL_CUT",
        "DRIVER_COMMIT_AT_FATAL_CUT",
    }:
        return "ambiguous"
    if profile.name == "PRE_AR_INTAKE_FATAL":
        return "doorbell"
    if profile.name == "SQ_HEAD_B_ERROR":
        return "sq_head"
    if profile.name in {
        "PARAMETER_READ_ERROR", "PARAMETER_CRC", "PARAMETER_BOUNDS",
        "PARAMETER_ERROR_IDENTITY", "CAPACITY_OUTPUT", "CAPACITY_METADATA",
        "PARAMETER_BINDING_OOB", "PARAMETER_BINDING_HEADER_OVERLAP",
        "PARAMETER_BINDING_EXTENSION_OVERLAP",
        "PARAMETER_BINDING_RECORD_SIZE",
    }:
        return "parameter"
    if profile.name == "CQ_ENTRY_B_ERROR":
        return "cq_entry"
    if profile.name == "CQ_TAIL_B_ERROR":
        return "cq_tail"
    if profile.name in {"MSI_B_ERROR", "MSI_ERROR_HOLE"}:
        return "msi"
    if profile.name in {"ACK_NO_TARGET_COMMIT", "ACK_POSTCOMMIT_ERROR"}:
        return "ack"
    return "full"


def _stage_traffic(stage: str, prefix: bool):
    values = {name: 0 for name in FULL_STAGE}
    if stage == "none":
        return values
    values["agent_control_bytes"] = 8
    values["agent_control_packets"] = 1
    if stage == "doorbell":
        return values
    values["npu_read_bytes"] = 64
    values["npu_read_packets"] = 1
    if stage in {"sq_read", "ambiguous"}:
        return values
    values["npu_control_bytes"] = 8
    values["npu_control_packets"] = 1
    if stage == "sq_head":
        return values
    if stage == "sq_crc":
        values["npu_control_bytes"] += 16
        values["npu_control_packets"] += 2
        values["npu_write_bytes"] = 32
        values["npu_write_packets"] = 1
        values["agent_control_bytes"] += 8
        values["agent_control_packets"] += 1
        return values
    values["npu_read_bytes"] += 256
    values["npu_read_packets"] += 1
    if stage == "parameter":
        values["npu_control_bytes"] += 16
        values["npu_control_packets"] += 2
        values["npu_write_bytes"] = 32
        values["npu_write_packets"] = 1
        values["agent_control_bytes"] += 8
        values["agent_control_packets"] += 1
        return values
    values["npu_read_bytes"] += 64
    values["npu_read_packets"] += 1
    if stage == "cq_entry":
        values["npu_write_bytes"] = 224
        values["npu_write_packets"] = 3
        return values
    values["npu_control_bytes"] += 8
    values["npu_control_packets"] += 1
    if stage == "cq_tail":
        values["npu_write_bytes"] = 224
        values["npu_write_packets"] = 3
        return values
    values["npu_control_bytes"] += 8
    values["npu_control_packets"] += 1
    values["npu_write_bytes"] = 224
    values["npu_write_packets"] = 3
    if stage == "msi":
        return values
    values["agent_control_bytes"] += 8
    values["agent_control_packets"] += 1
    return values


def _expected_traffic(profile: Gate3Profile):
    stage = _stage_for(profile)
    prefix = profile.name == "E2E_D_PREFIX"
    owners = []
    if profile.name == "PROVEN_NO_EFFECT_B_ERROR":
        owners.extend([(1, "doorbell"), (2, "full")])
    elif stage == "none":
        owners = []
    elif profile.name == "SAME_TICK_SEQ_ONLY":
        owners = [(1, "full"), (2, "sq_crc")]
    else:
        owners = [(index, stage) for index in range(1, profile.request_count + 1)]
    expected = {name: 0 for name in TRAFFIC_CLASSES}
    ownership = {}
    for request_id, owner_stage in owners:
        values = _stage_traffic(owner_stage, prefix and owner_stage == "full")
        expected["AGENT_TO_NPU_CONTROL"] += values["agent_control_bytes"]
        expected["NPU_TO_AGENT_CONTROL"] += values["npu_control_bytes"]
        expected["NPU_READ_AGENT_MEMORY"] += values["npu_read_bytes"]
        expected["NPU_WRITE_AGENT_MEMORY"] += values["npu_write_bytes"]
        ownership[request_id] = sum(values[name] for name in (
            "agent_control_bytes",
            "npu_control_bytes",
            "npu_read_bytes",
            "npu_write_bytes",
        ))
    if profile.name in {"EMPTY_DOORBELL", "STALE_DOORBELL", "DUPLICATE_DOORBELL"}:
        expected["AGENT_TO_NPU_CONTROL"] += 8
        ownership[0] = 8
    if profile.name == "ACK_BEYOND_ISSUED":
        expected["AGENT_TO_NPU_CONTROL"] += 8
        ownership[1] += 8
    packets = {
        "AGENT_TO_NPU_CONTROL": 0,
        "NPU_TO_AGENT_CONTROL": 0,
        "NPU_READ_AGENT_MEMORY": 0,
        "NPU_WRITE_AGENT_MEMORY": 0,
    }
    for request_id, owner_stage in owners:
        values = _stage_traffic(owner_stage, prefix and owner_stage == "full")
        packets["AGENT_TO_NPU_CONTROL"] += values["agent_control_packets"]
        packets["NPU_TO_AGENT_CONTROL"] += values["npu_control_packets"]
        packets["NPU_READ_AGENT_MEMORY"] += values["npu_read_packets"]
        packets["NPU_WRITE_AGENT_MEMORY"] += values["npu_write_packets"]
    if profile.request_count > 1 and profile.request_count <= profile.sq_depth:
        duplicate_doorbells = profile.request_count - 1
        expected["AGENT_TO_NPU_CONTROL"] -= duplicate_doorbells * 8
        packets["AGENT_TO_NPU_CONTROL"] -= duplicate_doorbells
        for request_id, _ in owners:
            ownership[request_id] -= 8
        ownership[0] = 8
    if profile.name in CQ_IDENTITY_FAULT_PROFILES:
        expected["AGENT_TO_NPU_CONTROL"] -= 8
        packets["AGENT_TO_NPU_CONTROL"] -= 1
        ownership[1] -= 8
    if profile.name in {"EMPTY_DOORBELL", "STALE_DOORBELL", "DUPLICATE_DOORBELL"}:
        packets["AGENT_TO_NPU_CONTROL"] += 1
    if profile.name == "ACK_BEYOND_ISSUED":
        packets["AGENT_TO_NPU_CONTROL"] += 1
    if profile.name in {
        "PARAMETER_BINDING_END", "PARAMETER_BINDING_OOB",
        "PARAMETER_BINDING_HEADER_OVERLAP",
        "PARAMETER_BINDING_EXTENSION_OVERLAP",
        "PARAMETER_BINDING_RECORD_SIZE",
    }:
        expected["NPU_READ_AGENT_MEMORY"] += 24
        packets["NPU_READ_AGENT_MEMORY"] += 2
        ownership[1] += 24
    return expected, packets, ownership


def _traffic_class(event):
    if event["kind"] != "AXI_ACCEPT" or event["channel"] not in ("W", "R"):
        return None
    if event["channel"] == "W":
        if event["control"] in {"SQ_DOORBELL", "CQ_HEAD_ACK"}:
            return "AGENT_TO_NPU_CONTROL"
        if event["control"] in {"SQ_HEAD_UPDATE", "CQ_TAIL_UPDATE", "MSI"}:
            return "NPU_TO_AGENT_CONTROL"
        if event["control"] in {"OUTPUT", "METADATA", "CQ_ENTRY"}:
            return "NPU_WRITE_AGENT_MEMORY"
    if event["channel"] == "R" and event["control"] in {
        "SQ_ENTRY", "PARAMETER", "PROMPT", "CQ_ENTRY_READ", "METADATA_READ"
    }:
        return "NPU_READ_AGENT_MEMORY"
    return None


def _actual_traffic(events):
    actual_bytes = defaultdict(int)
    actual_packets = defaultdict(set)
    owner_bytes = defaultdict(int)
    unattributed = 0
    sq_owners = {
        event["absolute_seq"]: event["request_id"]
        for event in _events(events, kind="LOCAL_VISIBLE", object="SQ_ENTRY")
        if event["absolute_seq"] is not None and event["request_id"] is not None
    }
    cumulative_doorbell = len(_events(
        events, kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="AW"
    )) < len(sq_owners)
    active_sq_owner = None
    for event in events:
        traffic_class = _traffic_class(event)
        if traffic_class is None:
            if event["kind"] == "AXI_ACCEPT" and event["channel"] in ("W", "R"):
                unattributed += event["bytes"]
            continue
        actual_bytes[traffic_class] += event["bytes"]
        if event["channel"] in ("W", "R"):
            actual_packets[traffic_class].add(event["txn"])
        if event["control"] == "SQ_ENTRY" and event["channel"] == "R":
            active_sq_owner = sq_owners.get(event["absolute_seq"])
        owner = event["request_id"]
        if event["control"] == "SQ_DOORBELL" and cumulative_doorbell:
            owner = 0
        elif event["control"] == "SQ_ENTRY" and event["channel"] == "R":
            owner = active_sq_owner
        elif event["control"] == "CQ_HEAD_ACK" and owner == 0:
            owner = sq_owners.get(event["cookie"], 0)
        elif owner in (None, 0) and event["direction"] == "NPU_TO_DRIVER":
            owner = active_sq_owner
        owner = 0 if owner is None else owner
        owner_bytes[owner] += event["bytes"]
    return actual_bytes, {name: len(actual_packets[name]) for name in TRAFFIC_CLASSES}, owner_bytes, unattributed


def _traffic_artifact(case_id, subcase, profile, events):
    expected, expected_packets, expected_owners = _expected_traffic(profile)
    actual, actual_packets, actual_owners, unattributed = _actual_traffic(events)
    classes = []
    for name in TRAFFIC_CLASSES:
        classes.append(
            {
                "traffic_class": name,
                "expected_bytes": expected.get(name, 0),
                "actual_bytes": actual.get(name, 0),
                "expected_packets": expected_packets.get(name, 0),
                "actual_packets": actual_packets.get(name, 0),
            }
        )
    owner_ids = sorted(set(expected_owners) | set(actual_owners))
    ownership = [
        {
            "owner_key_wire": format(owner, "x"),
            "expected_bytes": expected_owners.get(owner, 0),
            "actual_bytes": actual_owners.get(owner, 0),
        }
        for owner in owner_ids
    ]
    expected_projection = {
        "classes": [
            [row["traffic_class"], row["expected_bytes"], row["expected_packets"]]
            for row in classes
        ],
        "ownership": [[row["owner_key_wire"], row["expected_bytes"]] for row in ownership],
    }
    actual_projection = {
        "classes": [
            [row["traffic_class"], row["actual_bytes"], row["actual_packets"]]
            for row in classes
        ],
        "ownership": [[row["owner_key_wire"], row["actual_bytes"]] for row in ownership],
    }
    matched = (
        canonical_digest(expected_projection) == canonical_digest(actual_projection)
        and unattributed == 0
        and all(
            row["expected_bytes"] == row["actual_bytes"]
            and row["expected_packets"] == row["actual_packets"]
            for row in classes
        )
        and all(row["expected_bytes"] == row["actual_bytes"] for row in ownership)
    )
    return {
        "schema": "ai_mesh_traffic_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "status": "PASS" if matched else "FAIL",
        "oracle_digest": canonical_digest(expected_projection),
        "actual_digest": canonical_digest(actual_projection),
        "classes": classes,
        "ownership": ownership,
        "unattributed_bytes": unattributed,
    }


def _sequence_window(events, control):
    return [
        event["absolute_seq"]
        for event in _events(events, kind="AXI_ACCEPT", control=control, channel="AW")
        if event["absolute_seq"] is not None
    ]


def _valid_control_order(events, control):
    live = None
    for event in _events(events, kind="AXI_ACCEPT", control=control):
        if event["channel"] == "AW":
            if live is not None:
                return False
            live = event["txn"]
        elif event["channel"] == "B":
            if live != event["txn"]:
                return False
            live = None
    return live is None


def _evidence(name, profile, events, final, metrics, traffic, scenario):
    sq_consumes = _events(events, kind="SQ_CONSUME", object="SQ_ENTRY")
    cq_assigns = _events(events, kind="CQ_ASSIGN", object="CQ_ENTRY")
    cq_consumes = _events(events, kind="CQ_CONSUME", object="CQ_ENTRY")
    doorbell_aw = _events(events, kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="AW")
    local = _events(events, kind="LOCAL_VISIBLE")
    if name in {"sq_depth1_wrap", "sq_depth2_wrap"}:
        expected_depth = 1 if name.endswith("1_wrap") else 2
        return profile.sq_depth == expected_depth and any(
            event["generation"] >= 1 for event in sq_consumes
        )
    if name in {"cq_depth1_wrap", "cq_depth2_wrap"}:
        expected_depth = 1 if name.endswith("1_wrap") else 2
        return profile.cq_depth == expected_depth and any(
            event["generation"] >= 1 for event in cq_assigns
        )
    if name == "sq_backpressure":
        return metrics.get("sq_backpressure", 0) > 0
    if name == "sq_no_overwrite":
        keys = [(event["absolute_seq"], event["slot"], event["generation"]) for event in sq_consumes]
        return len(keys) == len(set(keys))
    if name in {"cq_obligation_retained", "context_retained"}:
        if profile.name == "CQ_FULL":
            reserves = _events(events, kind="CQ_OBLIGATION_RESERVE")
            retires = _events(events, kind="CQ_OBLIGATION_RETIRE")
            return (
                metrics.get("peak_live_cq_obligations") == profile.cq_depth
                and metrics.get("cq_backpressure", 0) > 0
                and len(reserves) == profile.request_count
                and bool(retires)
                and reserves[-1]["tick"] >= retires[0]["tick"]
            )
        return bool(_events(events, kind="CQ_OBLIGATION_RESERVE"))
    if name == "doorbell_stale_noop":
        return metrics.get("stale_doorbells", 0) > 0 and final["core_starts"] == 1
    if name == "doorbell_duplicate_noop":
        return metrics.get("duplicate_doorbells", 0) > 0 and final["core_starts"] == 1
    if name == "doorbell_empty_noop":
        return metrics.get("empty_doorbells", 0) > 0
    if name == "single_execution":
        return final["core_starts"] == 1
    if name == "zero_execution":
        return final["core_starts"] == 0
    if name == "sq_head_unchanged":
        return final["sq_observed_head_seq"] == 0
    if name == "zero_cq":
        return final["cq_assignments"] == 0
    if name == "seq_only_error_cq":
        return any(event["status"] == "ERROR" for event in cq_assigns)
    if name == "sq_head_advanced_once":
        return final["sq_observed_head_seq"] == 1 and len(_sequence_window(events, "SQ_HEAD_UPDATE")) == 1
    if name == "minimal_error_cq":
        return len(cq_assigns) == 1 and cq_assigns[0]["status"] == "ERROR"
    if name == "parameter_binding_detail":
        rejected = _events(events, kind="PARAMETER_REJECT")
        details = _events(events, kind="CQ_DETAIL", object="CQ_ENTRY")
        return (
            [event["status"] for event in rejected] ==
                ["E_REQUEST_BINDING"]
            and [event["status"] for event in details] ==
                ["E_REQUEST_BINDING"]
        )
    if name in {
        "trusted_sq_identity", "seq_only_error_identity",
        "trusted_parameter_error_identity",
    }:
        return bool(cq_assigns) and all(
            event["request_id"] is not None and event["cookie"] is not None
            for event in cq_assigns
        )
    if name == "doorbell_held_before_prompt_visibility":
        return all(_last_before(local, row, kind="LOCAL_VISIBLE", object="PROMPT") for row in doorbell_aw)
    if name == "doorbell_held_before_parameter_visibility":
        return all(_last_before(local, row, kind="LOCAL_VISIBLE", object="PARAMETER") for row in doorbell_aw)
    if name == "doorbell_held_before_sq_visibility":
        return all(_last_before(local, row, kind="LOCAL_VISIBLE", object="SQ_ENTRY") for row in doorbell_aw)
    if name == "doorbell_held_before_release_fence":
        return all(_last_before(events, row, kind="RELEASE_FENCE_DONE") for row in doorbell_aw)
    if name == "producer_held_before_doorbell_b":
        return all(
            _last_before(events, row, kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="B")
            for row in _events(events, kind="PUBLICATION_COMMIT")
        )
    if name == "publication_rollback":
        return bool(_events(events, kind="PUBLICATION_ROLLBACK"))
    if name == "absolute_slot_reused":
        return len({event["request_id"] for event in local if event["object"] == "SQ_ENTRY"}) > 1
    if name == "request_id_not_reused":
        ids = [event["request_id"] for event in local if event["object"] == "SQ_ENTRY"]
        return len(ids) == len(set(ids))
    if name == "acceptance_evidence_priority":
        transaction = _events(
            events, kind="AXI_ACCEPT", control="SQ_DOORBELL"
        )
        return (
            [event["channel"] for event in transaction] == ["AW", "W", "B"]
            and transaction[-1]["response"] != "OKAY"
            and bool(_events(events, kind="DOORBELL_TARGET_COMMIT"))
            and bool(_events(events, kind="FATAL"))
            and not bool(_events(events, kind="PUBLICATION_COMMIT"))
        )
    if name == "ambiguous_publication_retained":
        return bool(_events(events, kind="DOORBELL_TARGET_COMMIT")) and not bool(_events(events, kind="PUBLICATION_COMMIT"))
    if name == "fatal_intake_conservation":
        fatal_intakes = metrics.get("_fatal_sq_intakes", [])
        ids = [intake["intake_id"] for intake in fatal_intakes]
        return (
            metrics.get("sq_intakes_created", 0) ==
                metrics.get("sq_intakes_released", 0) +
                metrics.get("live_sq_intakes", 0) + len(fatal_intakes)
            and metrics.get("live_sq_intakes", 0) == 0
            and len(ids) == len(set(ids))
            and all(identifier > 0 for identifier in ids)
        )
    if name == "post_cut_sq_r_retained":
        intakes = metrics.get("_fatal_sq_intakes", [])
        reads = _events(
            events, kind="AXI_ACCEPT", control="SQ_ENTRY", channel="R"
        )
        return (
            len(intakes) == 1
            and intakes[0]["read_tag"] > 0
            and intakes[0]["state_at_cut"] == "WAIT_R"
            and intakes[0]["terminal_evidence"] == "R_OK"
            and any(event["response"] == "OKAY" for event in reads)
        )
    if name == "pre_ar_intake_retained":
        intakes = metrics.get("_fatal_sq_intakes", [])
        reads = _events(
            events, kind="AXI_ACCEPT", control="SQ_ENTRY", channel="AR"
        )
        return (
            not reads
            and len(intakes) == 1
            and intakes[0]["read_tag"] == 0
            and intakes[0]["state_at_cut"] == "PRE_AR"
            and intakes[0]["terminal_evidence"] == "NONE"
        )
    if name == "same_tick_normal_sq_discarded":
        responses = [
            event for event in events
            if event["kind"] == "AXI_ACCEPT"
            and event["channel"] in {"B", "R"}
            and event["control"] in {"SQ_DOORBELL", "SQ_ENTRY"}
        ]
        intakes = metrics.get("_fatal_sq_intakes", [])
        return (
            [(event["control"], event["response"])
             for event in responses] == [
                ("SQ_ENTRY", "OKAY"), ("SQ_DOORBELL", "SLVERR")
            ]
            and len({event["tick"] for event in responses}) == 1
            and not _events(events, kind="SQ_CONSUME")
            and metrics.get("sq_intakes_released", 0) == 0
            and final["npu_sq_consumer_seq"] == 0
            and len(intakes) == 1
            and intakes[0]["terminal_evidence"] == "R_OK"
            and metrics.get("_fatal_state", {}).get("candidate_count") == 1
            and metrics.get("_fatal_state", {}).get("source_class") == 1
        )
    if name == "same_tick_sq_error_collected":
        responses = [
            event for event in events
            if event["kind"] == "AXI_ACCEPT"
            and event["channel"] in {"B", "R"}
            and event["control"] in {"SQ_DOORBELL", "SQ_ENTRY"}
        ]
        candidates = [
            bytes.fromhex(wire)
            for wire in metrics.get("_fatal_candidates", [])
        ]
        intakes = metrics.get("_fatal_sq_intakes", [])
        return (
            [(event["control"], event["response"])
             for event in responses] == [
                ("SQ_DOORBELL", "SLVERR"), ("SQ_ENTRY", "SLVERR")
            ]
            and len({event["tick"] for event in responses}) == 1
            and metrics.get("_fatal_state", {}).get("candidate_count") == 2
            and {int.from_bytes(wire[8:10], "little")
                 for wire in candidates} == {0, 1}
            and metrics.get("_fatal_state", {}).get("source_class") == 0
            and metrics.get("sq_intakes_released", 0) == 0
            and final["npu_sq_consumer_seq"] == 0
            and len(intakes) == 1
            and intakes[0]["first_error"] == "E_AGENT_PROTOCOL_FATAL"
        )
    if name == "same_tick_driver_commit_discarded":
        responses = [
            event for event in events
            if event["kind"] == "AXI_ACCEPT"
            and event["channel"] in {"B", "R"}
            and event["control"] in {"SQ_DOORBELL", "SQ_ENTRY"}
        ]
        publications = metrics.get("_fatal_publications", [])
        return (
            {(event["control"], event["response"])
             for event in responses} == {
                ("SQ_DOORBELL", "OKAY"), ("SQ_ENTRY", "SLVERR")
            }
            and len({event["tick"] for event in responses}) == 1
            and not _events(events, kind="PUBLICATION_COMMIT")
            and final["sq_committed_producer_seq"] == 0
            and final["live_submissions"] == 1
            and len(publications) == 1
            and publications[0]["terminal_evidence"] == "B_OK"
            and not publications[0]["ambiguous"]
        )
    if name == "core_held_until_prompt_r":
        return all(
            _last_before(events, row, kind="AXI_ACCEPT", control="PROMPT", channel="R")
            for row in _events(events, kind="CORE_START")
        )
    if name == "cq_held_until_output_b":
        return all(
            _last_before(events, row, kind="AXI_ACCEPT", control="OUTPUT", channel="B")
            for row in cq_assigns
        )
    if name == "msi_held_until_cq_b":
        return all(
            _last_before(events, row, kind="AXI_ACCEPT", control="CQ_ENTRY", channel="B")
            for row in _events(events, kind="AXI_ACCEPT", control="MSI", channel="AW")
        )
    if name == "irq_held_until_cq_b":
        return all(
            _last_before(events, row, kind="AXI_ACCEPT", control="CQ_ENTRY", channel="B")
            for row in _events(events, kind="IRQ_DELIVER")
        )
    if name == "stale_cq_rejected":
        injected = _events(events, kind="FAULT_INJECT", object="CQ_ENTRY")
        rejected = _events(events, kind="CQ_REJECT", object="CQ_ENTRY")
        return (
            metrics.get("stale_cq_entries", 0) == 1
            and len(injected) == len(rejected) == 1
            and injected[0]["absolute_seq"] == rejected[0]["absolute_seq"]
            and rejected[0]["absolute_seq"] != 0
            and not cq_consumes
            and final["fatal"]
            and final["cq_notified_seq"] == 0
            and final["msi_rob_entries"] == 1
            and any(row["terminal_evidence"] == "B_OK"
                    for row in metrics.get("_fatal_msi_records", []))
            and not _events(events, control="CQ_HEAD_ACK")
        )
    if name == "cq_request_rejected":
        injected = _events(events, kind="FAULT_INJECT", object="CQ_ENTRY")
        rejected = _events(events, kind="CQ_REJECT", object="CQ_ENTRY")
        consumed = _events(events, kind="CQ_CONSUME", object="CQ_ENTRY")
        return (
            metrics.get("cq_request_mismatch", 0) == 1
            and metrics.get("cq_identity_retries", 0) == 0
            and len(injected) == len(rejected) == 1
            and not consumed
            and injected[0]["request_id"] == rejected[0]["request_id"]
            and final["fatal"]
            and final["cq_notified_seq"] == 0
            and final["msi_rob_entries"] == 1
            and any(row["terminal_evidence"] == "B_OK"
                    for row in metrics.get("_fatal_msi_records", []))
            and not _events(events, control="CQ_HEAD_ACK")
        )
    if name == "cq_cookie_rejected":
        injected = _events(events, kind="FAULT_INJECT", object="CQ_ENTRY")
        rejected = _events(events, kind="CQ_REJECT", object="CQ_ENTRY")
        return (
            metrics.get("cq_cookie_mismatch", 0) == 1
            and len(injected) == len(rejected) == 1
            and injected[0]["cookie"] == rejected[0]["cookie"]
            and rejected[0]["cookie"] != 1
            and not cq_consumes
            and final["fatal"]
            and final["cq_notified_seq"] == 0
            and final["msi_rob_entries"] == 1
            and any(row["terminal_evidence"] == "B_OK"
                    for row in metrics.get("_fatal_msi_records", []))
            and not _events(events, control="CQ_HEAD_ACK")
        )
    if name == "cq_slot_held_until_ack_commit":
        return all(
            _last_before(events, row, kind="CQ_ACK_TARGET_COMMIT")
            for row in _events(events, kind="SLOT_REUSE", object="CQ_ENTRY")
        )
    if name == "control_forward_progress":
        return final["cq_assignments"] == profile.request_count
    if name == "bounded_control_latency":
        return all(event["tick"] >= 0 for event in doorbell_aw)
    if name in {"exact_directional_bytes", "exact_directional_packets"}:
        return traffic["status"] == "PASS"
    if name.endswith("_full_wstrb"):
        control = {
            "doorbell_full_wstrb": "SQ_DOORBELL",
            "sq_head_full_wstrb": "SQ_HEAD_UPDATE",
            "cq_tail_full_wstrb": "CQ_TAIL_UPDATE",
            "cq_ack_full_wstrb": "CQ_HEAD_ACK",
            "msi_full_wstrb": "MSI",
        }[name]
        def _full_strobe(event):
            strobe = int(event["wstrb"], 16)
            return (
                event["size"] == 3
                and strobe == 0xff << (event["address"] % 64)
            )
        writes = _events(
            events, kind="AXI_ACCEPT", control=control, channel="W"
        )
        return bool(writes) and all(_full_strobe(event) for event in writes)
    if name.endswith("_sequence_window"):
        control = {
            "doorbell_sequence_window": "SQ_DOORBELL",
            "sq_head_sequence_window": "SQ_HEAD_UPDATE",
            "cq_tail_sequence_window": "CQ_TAIL_UPDATE",
            "cq_ack_sequence_window": "CQ_HEAD_ACK",
        }[name]
        values = _sequence_window(events, control)
        return values == list(range(1, len(values) + 1))
    if name == "msi_contiguous_retirement":
        values = _sequence_window(events, "MSI")
        return values == list(range(1, len(values) + 1))
    if name == "fixed_control_ids":
        return all(
            event["axi_id"] == CONTROL_IDS[event["control"]]
            for event in _events(events, kind="AXI_ACCEPT")
            if event["control"] in CONTROL_IDS
        )
    if name in {"one_control_inflight", "bad_b_blocks_later_update"}:
        return all(_valid_control_order(events, control) for control in CONTROL_IDS)
    if name in {"capacity_error_before_core", "single_error_cq"}:
        return final["core_starts"] == 0 and len(cq_assigns) == 1 and cq_assigns[0]["status"] == "ERROR"
    if name == "cq_held_until_metadata_b":
        return all(
            _last_before(events, row, kind="AXI_ACCEPT", control="METADATA", channel="B")
            for row in cq_assigns
        )
    if name == "accepted_conservation":
        return final["driver_cq_consumer_seq"] == final["npu_cq_ack_seq"]
    if name == "qos_terminal_arbitration":
        ready = _events(events, kind="TERMINAL_READY")
        return (
            len(ready) == len(cq_assigns) == 2
            and [event["request_id"] for event in ready] == [2, 1]
            and ready[0]["tick"] < ready[1]["tick"]
            and [event["absolute_seq"] for event in cq_assigns] == [0, 1]
            and [event["request_id"] for event in cq_assigns] == [2, 1]
        )
    if name == "same_tick_terminal_arbitration":
        ready = _events(events, kind="TERMINAL_READY")
        return (
            len(ready) == len(cq_assigns) == 2
            and len({event["tick"] for event in ready}) == 1
            and {(event["absolute_seq"], event["status"])
                 for event in ready} == {
                     (0, "SUCCESS"), (1, "SQ_SEQ_ONLY_ERROR")
                 }
            and [event["absolute_seq"] for event in cq_assigns] == [0, 1]
            and [event["request_id"] for event in cq_assigns] == [0, 1]
        )
    if name in {"continuous_cq_assignment", "ordered_driver_consume"}:
        values = [event["absolute_seq"] for event in cq_assigns or cq_consumes]
        return values == list(range(len(values)))
    if name == "completion_fatal_no_duplicate":
        return bool(_events(events, kind="FATAL")) and len(cq_assigns) <= 1
    if name in {"ack_slot_retained", "ack_obligation_retained"}:
        return (
            final["live_cq_obligations"] +
            len(metrics.get("_fatal_cq_obligations", []))
        ) > 0
    if name in {"ack_history_retained", "slot_not_rolled_back"}:
        return (
            bool(_events(events, kind="CQ_ACK_TARGET_COMMIT"))
            and bool(_events(events, kind="FATAL"))
            and final["npu_cq_ack_seq"] == metrics.get("npu_cq_ack_seq", 0)
            and final["npu_cq_ack_seq"] > 0
        )
    if name == "slot_not_resurrected":
        return final["live_cq_obligations"] == 0 and bool(
            _events(events, kind="CQ_ACK_TARGET_COMMIT")
        ) and bool(_events(events, kind="FATAL"))
    if name == "zero_doorbell_before_local_visibility":
        return all(_before(local_event, bell) for bell in doorbell_aw for local_event in _events(events, kind="LOCAL_VISIBLE"))
    if name == "zero_doorbell_before_release_fence":
        return all(_before(_last_before(events, bell, kind="RELEASE_FENCE_DONE"), bell) for bell in doorbell_aw)
    if name in {"zero_local_store_axi", "zero_local_store_garnet"}:
        return not any(
            event["kind"] == "AXI_ACCEPT" and
            event["channel"] in {"AW", "W", "B"} and
            event["object"] in {"PROMPT", "PARAMETER", "SQ_ENTRY"}
            for event in events
        )
    if name == "slot_reuse_after_ack_target_commit":
        return _evidence("cq_slot_held_until_ack_commit", profile, events, final, metrics, traffic, scenario)
    if name == "ack_response_ledger_independent":
        return bool(_events(events, kind="CQ_ACK_TARGET_COMMIT")) and bool(_events(events, kind="AXI_ACCEPT", control="CQ_HEAD_ACK", channel="B"))
    if name == "all_control_sequence_windows":
        return all(_evidence(item, profile, events, final, metrics, traffic, scenario) for item in (
            "doorbell_sequence_window",
            "sq_head_sequence_window",
            "cq_tail_sequence_window",
            "cq_ack_sequence_window",
        ))
    if name == "early_ack_held_until_msi_b":
        ack_commits = _events(events, kind="CQ_ACK_TARGET_COMMIT")
        msi_bs = _events(events, kind="AXI_ACCEPT", control="MSI", channel="B")
        if not ack_commits or not msi_bs:
            return False
        early = any(
            ack["tick"] < min((b["tick"] for b in msi_bs), default=ack["tick"] + 1)
            for ack in ack_commits
        )
        last_retire = _events(events, kind="CQ_OBLIGATION_RETIRE")
        held = all(
            r["tick"] >= max((b["tick"] for b in msi_bs), default=0)
            for r in last_retire
        )
        return early and held
    if name == "early_ack_committed_once":
        return len(_events(events, kind="CQ_ACK_TARGET_COMMIT")) == profile.request_count
    if name == "msi_contiguous_ok_prefix":
        aw = _events(events, kind="AXI_ACCEPT", control="MSI", channel="AW")
        responses = _events(
            events, kind="AXI_ACCEPT", control="MSI", channel="B"
        )
        retires = _events(events, kind="CQ_OBLIGATION_RETIRE")
        return (
            len(aw) >= 3
            and len(responses) == len(aw)
            and [event["absolute_seq"] for event in responses] ==
                list(reversed([event["absolute_seq"] for event in aw]))
            and max(event["tick"] for event in aw) <
                min(event["tick"] for event in responses)
            and len(retires) == len(aw)
            and final["cq_notified_seq"] == len(aw)
        )
    if name == "msi_error_holds_prefix":
        return final["fatal"] and final["cq_msi_issued_seq"] <= final["npu_cq_producer_seq"]
    if name == "future_ack_rejected":
        return metrics.get("future_ack_rejected", 0) > 0
    if name == "single_npu_fabric":
        return len(scenario.get("endpoint_to_router", {}).get("targets", [])) == 2
    if name == "zero_cpu_core":
        return scenario.get("cpu_core_count") == 0
    if name == "zero_cpu_garnet":
        return scenario.get("cpu_garnet_ports") == 0
    if name == "zero_ucie":
        return scenario.get("ucie_links") == 0
    if name == "shaper_is_driver_helper":
        return scenario.get("traffic_shaper") == "AgentAxiDriver"
    if name == "zero_execution":
        return final["core_starts"] == 0
    if name == "committed_prefix_split":
        head = _events(events, kind="AXI_ACCEPT", control="SQ_HEAD_UPDATE",
                       channel="B")
        doorbell_b = _events(
            events, kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="B"
        )
        return (
            len(head) == len(doorbell_b) == 1
            and head[0]["tick"] < doorbell_b[0]["tick"]
            and final["sq_committed_producer_seq"] <=
                final["sq_tentative_producer_seq"]
        )
    if name == "pending_suffix_split":
        commits = _events(events, kind="PUBLICATION_COMMIT", object="DOORBELL")
        doorbell_b = _events(
            events, kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="B"
        )
        return (
            len(commits) == len(doorbell_b) == 1
            and commits[0]["tick"] >= doorbell_b[0]["tick"]
            and final["sq_tentative_producer_seq"] >=
                final["sq_committed_producer_seq"]
        )
    if name == "early_suffix_cached":
        doorbell_b = _events(
            events, kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="B"
        )
        irq = _events(events, kind="IRQ_DELIVER", object="MSI")
        return (
            len(doorbell_b) == len(irq) == 1
            and irq[0]["tick"] < doorbell_b[0]["tick"]
            and len(cq_consumes) == 1
            and doorbell_b[0]["tick"] < cq_consumes[0]["tick"]
        )
    if name == "early_seq_only_cached":
        return (
            _evidence(
                "early_suffix_cached", profile, events, final, metrics,
                traffic, scenario
            )
            and len(cq_consumes) == 1
            and cq_consumes[0]["status"] == "ERROR"
            and cq_consumes[0]["request_id"] == 0
        )
    if name == "cq_slot_not_reused":
        return not bool(_events(events, kind="SLOT_REUSE", object="CQ_ENTRY"))
    if name == "same_edge_deterministic_prefix":
        irq = _events(events, kind="IRQ_DELIVER", object="MSI")
        commits = _events(events, kind="MSI_TARGET_COMMIT", object="MSI")
        msi_b = _events(events, kind="AXI_ACCEPT", control="MSI", channel="B")
        ack = _events(events, kind="CQ_ACK_TARGET_COMMIT", object="CQ_ACK")
        irq_ticks = [event["tick"] for event in irq]
        return (
            not final["fatal"]
            and profile.request_count >= 3
            and len(commits) == len(irq) == len(msi_b) == profile.request_count
            and len({event["tick"] for event in commits}) == 1
            and [event["absolute_seq"] for event in commits] == [1, 2, 3]
            and len(set(irq_ticks)) == 1
            and [event["absolute_seq"] for event in irq] == [1, 2, 3]
            and [event["absolute_seq"] for event in cq_assigns] == [0, 1, 2]
            and [event["absolute_seq"] for event in cq_consumes] == [0, 1, 2]
            and [event["absolute_seq"] for event in ack] == [1, 2, 3]
            and all(irq[0]["tick"] <= event["tick"] for event in msi_b)
        )
    if name == "callback_hole_holds_prefix":
        commits = _events(events, kind="MSI_TARGET_COMMIT", object="MSI")
        irq = _events(events, kind="IRQ_DELIVER", object="MSI")
        return (
            [event["absolute_seq"] for event in commits] == [2, 3, 1]
            and [event["absolute_seq"] for event in irq] == [1, 2, 3]
            and irq[0]["tick"] >= commits[-1]["tick"]
            and final["cq_notified_seq"] == 3
        )
    if name == "duplicate_callback_idempotent":
        irq = _events(events, kind="IRQ_DELIVER", object="MSI")
        return (
            metrics.get("duplicate_msi_callbacks", 0) == 1
            and [event["absolute_seq"] for event in irq] == [1, 2, 3]
            and final["cq_notified_seq"] == 3
        )
    if name == "same_tick_fatal_reduction":
        responses = [
            event for event in _events(
                events, kind="AXI_ACCEPT", channel="B"
            )
            if event["control"] in {"MSI", "CQ_HEAD_ACK"}
            and event["response"] == "SLVERR"
        ]
        candidates = [
            bytes.fromhex(wire)
            for wire in metrics.get("_fatal_candidates", [])
        ]
        return (
            len(responses) == 2
            and len({event["tick"] for event in responses}) == 1
            and metrics.get("_fatal_state", {}).get("candidate_count") == 2
            and {int.from_bytes(wire[8:10], "little")
                 for wire in candidates} == {4, 5}
            and {int.from_bytes(wire[:8], "little")
                 for wire in candidates} == {responses[0]["tick"]}
        )
    if name == "early_suffix_consumed_once":
        return final["driver_cq_consumer_seq"] == len(cq_consumes)
    return False


def _invariants(case_id, subcase, profile, events, final, metrics, traffic, scenario):
    requirements = {
        requirement.name: requirement
        for requirement in GATE3_CASES[case_id]
    }
    requirement = requirements[subcase]
    try:
        validate_observation({
            "schema": "ai_mesh_gate3_observation_v1",
            "version": 1,
            "id": case_id,
            "subcase": subcase,
            "configuration": {
                "sq_depth": profile.sq_depth,
                "cq_depth": profile.cq_depth,
                "control_bytes": 8,
                "data_bus_bytes": 64,
                "non_msi_axi_ids": CONTROL_IDS,
            },
            "events": events,
            "final": final,
        })
        oracle_ok = True
    except Gate3OracleError:
        oracle_ok = False
    checks = []
    for name in requirement.invariants:
        observed = oracle_ok if name in requirement.invariants[:12] else _evidence(
            name, profile, events, final, metrics, traffic, scenario
        )
        checks.append({
            "name": name,
            "status": "PASS" if observed else "FAIL",
            "expected": {"kind": "BOOL", "value": True},
            "observed": {"kind": "BOOL", "value": observed},
        })
    failures = [check["name"] for check in checks if check["status"] == "FAIL"]
    return {
        "schema": "ai_mesh_invariants_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "status": "FAIL" if failures else "PASS",
        "registry_digest": canonical_digest([check["name"] for check in checks]),
        "checks": checks,
        "first_failure": failures[0] if failures else None,
    }


def _ledger(final, metrics):
    fatal = final["fatal"]
    ack_records = metrics.get("_fatal_ack_records", [])
    return {
        "live_cq_obligations": final["live_cq_obligations"],
        "fatal_cq_obligations": len(
            metrics.get("_fatal_cq_obligations", [])
        ),
        "fatal_sq_intakes": len(
            metrics.get("_fatal_sq_intakes", [])
        ) if fatal else 0,
        "fatal_publications": len(
            metrics.get("_fatal_publications", [])
        ) if fatal else 0,
        "ambiguous_publications": sum(
            record["ambiguous"]
            for record in metrics.get("_fatal_publications", [])
        ) if fatal else 0,
        "host_ack_wait_b": sum(
            record["terminal_evidence"] == "NONE"
            for record in ack_records
        ) if fatal else final["ack_wait_b"],
        "host_ack_b_error": sum(
            record["terminal_evidence"] == "B_ERROR"
            for record in ack_records
        ) if fatal else metrics.get("host_ack_b_error", 0),
        "msi_rob_entries": final["msi_rob_entries"],
        "fatal_records": metrics.get("fatal_records", 0),
    }


def _enum_name(enum_type, value):
    for name, enum_value in vars(enum_type).items():
        if not name.startswith("_") and enum_value == value:
            return name
    raise ContractError(f"unknown generated enum value {value}")


def _fatal_error(metrics, fatal):
    state = metrics["_fatal_state"]
    candidates = metrics["_fatal_candidates"]
    modulus = 1 << 256
    digest_sum = sum(
        int.from_bytes(
            hashlib.sha256(
                b"FATAL_CANDIDATE_V1\0" + bytes.fromhex(wire)
            ).digest(),
            "big",
        )
        for wire in candidates
    ) % modulus
    return {
        "fatal_id": 1,
        "error": fatal,
        "observed_tick": state["observed_tick"],
        "site_domain": _enum_name(
            ABI.FATAL_SITE_DOMAIN_V1, state["site_domain"]
        ),
        "site_id": state["site_id"],
        "component_kind": _enum_name(
            ABI.FATAL_COMPONENT_KIND_V1, state["component_kind"]
        ),
        "component_local_id": state["component_local_id"],
        "endpoint_id": None if state["endpoint_id"] == 0xFFFFFFFF else
            state["endpoint_id"],
        "candidate_key_wire": state["candidate_key_wire"],
        "candidate_count": state["candidate_count"],
        "candidate_multiset_digest": format(digest_sum, "064x"),
        "physical_source_token_wire": state["physical_source_token_wire"],
    }


def _fatal_snapshot(case_id, subcase, run_manifest_digest, fatal_error,
                    final, metrics, ledger_summary):
    fatal_cq_obligations = metrics["_fatal_cq_obligations"]
    fatal_publications = metrics["_fatal_publications"]
    return {
        "schema": "ai_mesh_fatal_snapshot_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "run_manifest_digest": run_manifest_digest,
        "first_fatal": fatal_error,
        "secondary": {
            "candidate_count": fatal_error["candidate_count"],
            "multiset_digest": fatal_error["candidate_multiset_digest"],
        },
        "ledger_summary": ledger_summary,
        "fatal_cq_obligations": fatal_cq_obligations,
        "fatal_sq_intakes": metrics["_fatal_sq_intakes"],
        "fatal_publications": fatal_publications,
        "host_ack_records": metrics["_fatal_ack_records"],
        "msi_records": metrics["_fatal_msi_records"],
        "retained_sequence_slot_snapshot": {
            "sq_consumer_seq": final["npu_sq_consumer_seq"],
            "cq_producer_seq": final["npu_cq_producer_seq"],
            "cq_consumer_seq": final["driver_cq_consumer_seq"],
            "cq_notified_seq": final["cq_notified_seq"],
            "cq_ack_received_seq": final["npu_cq_ack_seq"],
            "cq_msi_issued_seq": final["cq_msi_issued_seq"],
            "occupied_cq_slots": [
                {
                    "slot_id": obligation["slot_id"],
                    "cq_obligation_id": obligation["cq_obligation_id"],
                }
                for obligation in fatal_cq_obligations
                if obligation["slot_id"] is not None
            ],
            "early_ack_records": [
                {
                    "ack_seq": record["ack_seq"],
                    "axi_id": record["axi_id"],
                }
                for record in metrics["_fatal_ack_records"]
                if record["target_commit_evidence"] == "YES"
                and record["ack_seq"] > final["cq_notified_seq"]
            ],
        },
    }


def _load_scenario(artifact_dir):
    path = artifact_dir / "gate3_scenario.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_gate3_artifacts(profile: Gate3Profile, exit_code: int, facts_path: Path,
                          artifact_dir: Path, case_id: str, subcase: str):
    events, final, metrics, fatal = parse_gate3_facts(facts_path)
    if exit_code not in (0, 20):
        raise ContractError(f"unexpected Gate3 exit code {exit_code}")
    if final["fatal"] != (exit_code == 20):
        raise ContractError("Gate3 exit code and final fatal state differ")
    observation = {
        "schema": "ai_mesh_gate3_observation_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "configuration": {
            "sq_depth": profile.sq_depth,
            "cq_depth": profile.cq_depth,
            "control_bytes": 8,
            "data_bus_bytes": 64,
            "non_msi_axi_ids": CONTROL_IDS,
        },
        "events": events,
        "final": final,
    }
    validate_observation(observation)
    atomic_write_json(artifact_dir / ARTIFACT_BASENAMES["GATE3_OBSERVATION_JSON"], observation)
    traffic = _traffic_artifact(case_id, subcase, profile, events)
    validate_schema("traffic_v1.schema.json", traffic)
    atomic_write_json(artifact_dir / ARTIFACT_BASENAMES["TRAFFIC_JSON"], traffic)
    scenario = _load_scenario(artifact_dir)
    invariants = _invariants(case_id, subcase, profile, events, final, metrics, traffic, scenario)
    validate_schema("invariants_v1.schema.json", invariants)
    atomic_write_json(artifact_dir / ARTIFACT_BASENAMES["INVARIANTS_JSON"], invariants)
    manifest_path = artifact_dir / ARTIFACT_BASENAMES["RUN_MANIFEST"]
    manifest = read_json_artifact(manifest_path, artifact_dir, "run_manifest_v1.schema.json")
    run_manifest_digest = canonical_digest(manifest)
    ledger_summary = _ledger(final, metrics)
    first_fatal = _fatal_error(metrics, fatal) if fatal else None
    child_report = {
        "schema": "ai_mesh_child_scenario_report_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "run_exit_reason": "INFRA_FATAL" if final["fatal"] else "QUIESCENT_SUCCESS",
        "first_fatal": first_fatal["error"] if first_fatal else None,
        "watchdog_fired": False,
        "ledger_summary": ledger_summary,
        "global_quiescence": not final["fatal"],
        "run_manifest_digest": run_manifest_digest,
    }
    validate_schema("child_scenario_report_v1.schema.json", child_report)
    atomic_write_json(artifact_dir / "child_report.json", child_report)
    if fatal:
        snapshot = _fatal_snapshot(
            case_id, subcase, run_manifest_digest, first_fatal, final,
            metrics, ledger_summary
        )
        validate_schema("fatal_snapshot_v1.schema.json", snapshot)
        atomic_write_json(
            artifact_dir / ARTIFACT_BASENAMES["FATAL_SNAPSHOT"], snapshot
        )

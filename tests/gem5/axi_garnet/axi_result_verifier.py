"""Strict, protocol-aware verification for AXI-over-Garnet artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from axi_test_lib import (
    TRACE_FIELDS,
    canonical_json_bytes,
    constants,
    load_jsonl,
    payload_digest,
    validate_trace_events,
)


RESULT_SCHEMA_VERSION = 1
CREDIT_FIELDS = {
    "current", "depth", "initial", "link_id", "owner_id", "owner_kind",
    "port_id", "returned", "sent", "vc", "vnet",
}
QUIESCENCE_FIELDS = {
    "ni_queued_flits", "ni_queued_messages", "router_buffered_flits",
    "non_idle_input_vcs", "non_idle_output_vcs", "data_link_pending_flits",
    "credit_link_pending_credits", "bridge_pending_items", "credit_deficit",
}
EXIT_ZERO_FIELDS = {
    "outstanding_at_exit", "orphan_w_at_exit", "rob_entries_at_exit",
    "message_buffers_at_exit", "local_delivery_at_exit",
    "adapter_ingress_at_exit", "response_obligations_at_exit",
    "business_events_at_exit", "router_vc_flits_at_exit",
}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_constant(value):
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def load_json_strict(path):
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source, parse_constant=_reject_constant)


def _require_keys(value, keys, label):
    missing = set(keys) - set(value)
    if missing:
        raise ValueError(f"{label} missing fields: {sorted(missing)}")


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _count_vector(value, length, label):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{label} must contain exactly {length} counters")
    return [_count(item, f"{label}[{index}]")
            for index, item in enumerate(value)]


def validate_credit_ledger(path, expected_links=None, expected_vcs=None,
                           require_restored=True):
    ledger = load_json_strict(path)
    _require_keys(
        ledger, {"schema_version", "directed_links", "vcs_per_link", "entries"},
        "credit ledger",
    )
    if ledger["schema_version"] != RESULT_SCHEMA_VERSION:
        raise ValueError("unknown credit-ledger schema_version")
    links = _count(ledger["directed_links"], "directed_links")
    vcs = _count(ledger["vcs_per_link"], "vcs_per_link")
    if links == 0 or vcs == 0:
        raise ValueError("credit ledger dimensions must be positive")
    if expected_links is not None and links != expected_links:
        raise ValueError(
            f"directed link count mismatch: expected {expected_links}, got {links}"
        )
    if expected_vcs is not None and vcs != expected_vcs:
        raise ValueError(
            f"VCs/link mismatch: expected {expected_vcs}, got {vcs}"
        )
    entries = ledger["entries"]
    if not isinstance(entries, list) or len(entries) != links * vcs:
        raise ValueError("credit ledger is not rectangular")

    by_link = defaultdict(set)
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != CREDIT_FIELDS:
            raise ValueError(f"credit entry {index} field set mismatch")
        for field in CREDIT_FIELDS - {"owner_kind"}:
            _count(entry[field], f"credit entry {index}.{field}")
        if entry["owner_kind"] not in ("ni", "router"):
            raise ValueError(f"credit entry {index} has unknown owner_kind")
        link = entry["link_id"]
        vc = entry["vc"]
        if vc >= vcs or vc in by_link[link]:
            raise ValueError("duplicate or out-of-range credit VC entry")
        by_link[link].add(vc)
        if entry["initial"] != entry["depth"]:
            raise ValueError("initial credit does not equal configured depth")
        if entry["current"] > entry["depth"]:
            raise ValueError("current credit exceeds configured depth")
        if entry["initial"] + entry["returned"] != \
                entry["sent"] + entry["current"]:
            raise ValueError("credit conservation equation failed")
        if require_restored and entry["current"] != entry["initial"]:
            raise ValueError("credit was not restored at exit")
    if len(by_link) != links or any(len(items) != vcs for items in by_link.values()):
        raise ValueError("credit ledger link/VC shape mismatch")
    return ledger


def _canonical_jsonl(path):
    path = Path(path)
    values = load_jsonl(path)
    raw = path.read_bytes()
    expected = b"".join(canonical_json_bytes(value) for value in values)
    if raw != expected:
        raise ValueError(f"{path.name} is not canonical JSONL")
    return values


def _expected_write_digest(transaction, beat_index, data_bus_bytes):
    seed = int(transaction.get("dataSeed", 0))
    data = bytes(
        (seed + beat_index * 17 + lane) & 0xFF
        for lane in range(data_bus_bytes)
    )
    return payload_digest(data)


def _response_domain(transaction):
    return (
        transaction["srcNode"], transaction["srcPort"],
        transaction["axiId"], transaction["kind"],
    )


def _target_domain(transaction):
    return _response_domain(transaction) + (transaction["dstNode"],)


def validate_event_trace(trace_path, workload_path, data_bus_bytes=64):
    events = _canonical_jsonl(trace_path)
    workload = _canonical_jsonl(workload_path)
    if not workload:
        raise ValueError("workload trace is empty")
    validate_trace_events(events)
    if not events or events[-1]["eventEnum"] != 30:
        raise ValueError("event trace does not end with quiescence")
    if sum(event["eventEnum"] == 30 for event in events) != 1:
        raise ValueError("event trace must contain exactly one quiescence event")

    by_uid = {}
    by_plan = {}
    for transaction in workload:
        _require_keys(transaction, {
            "expectedTxnUid", "expectedTargetSeq", "expectedResponseSeq",
            "expectedWriteOrdinal", "planTxnIndex", "srcNode", "srcPort",
            "dstNode", "axiId", "kind", "beatCount", "size", "address",
            "expectedResponse", "arrivalTick", "dataSeed", "strobe",
        }, "workload transaction")
        uid = transaction["expectedTxnUid"]
        plan = transaction["planTxnIndex"]
        if uid in by_uid or plan in by_plan:
            raise ValueError("duplicate workload UID or plan index")
        by_uid[uid] = transaction
        by_plan[plan] = transaction

    observations = defaultdict(list)
    target_order = defaultdict(list)
    response_order = defaultdict(list)
    shadow = {}
    for event in events:
        if tuple(sorted(event)) != TRACE_FIELDS:
            raise ValueError("event trace field set mismatch")
        if event["eventEnum"] == 30:
            nullable = set(TRACE_FIELDS) - {
                "schemaVersion", "eventSeq", "tick", "phaseEnum", "eventEnum",
                "occupancy",
            }
            if any(event[field] is not None for field in nullable):
                raise ValueError("quiescence event contains transaction metadata")
            continue
        uid = event["txnUid"]
        if uid not in by_uid:
            raise ValueError("event references an unknown txnUid")
        transaction = by_uid[uid]
        observations[uid].append(event)
        expected_fields = {
            "srcNode": transaction["srcNode"],
            "srcPort": transaction["srcPort"],
            "dstNode": transaction["dstNode"],
            "axiId": transaction["axiId"],
            "targetSeq": transaction["expectedTargetSeq"],
            "responseSeq": transaction["expectedResponseSeq"],
        }
        for field, expected in expected_fields.items():
            if event[field] != expected:
                raise ValueError(f"event {field} disagrees with workload oracle")
        expected_ordinal = transaction["expectedWriteOrdinal"]
        if event["writeOrdinal"] != expected_ordinal:
            raise ValueError("event writeOrdinal disagrees with workload oracle")
        if event["tick"] < transaction["arrivalTick"]:
            raise ValueError("transaction was accepted before its arrival tick")
        if event["occupancy"] < 0:
            raise ValueError("event occupancy is negative")

        kind = transaction["kind"]
        beat_count = transaction["beatCount"]
        event_kind = event["eventEnum"]
        if event_kind == 10:
            expected_channel = "aw" if kind == "write" else "ar"
            if event["phaseEnum"] != 10 or event["channel"] != expected_channel:
                raise ValueError("address event has the wrong phase/channel")
            if event["beatCount"] != beat_count or event["beatIndex"] is not None:
                raise ValueError("address event beat metadata is invalid")
            target_order[_target_domain(transaction)].append(
                transaction["expectedTargetSeq"]
            )
        elif event_kind == 11:
            if kind != "write" or event["phaseEnum"] != 10 or \
                    event["channel"] != "w":
                raise ValueError("W event has the wrong transaction/phase/channel")
            beat = event["beatIndex"]
            if not isinstance(beat, int) or not 0 <= beat < beat_count or \
                    event["beatCount"] != beat_count:
                raise ValueError("W beat index/count is invalid")
            if event["payloadDigest"] != _expected_write_digest(
                    transaction, beat, data_bus_bytes):
                raise ValueError("W payload digest disagrees with workload")
        elif event_kind in (20, 21):
            expected_event = 20 if kind == "write" else 21
            expected_channel = "b" if kind == "write" else "r"
            if event_kind != expected_event or event["phaseEnum"] != 20 or \
                    event["channel"] != expected_channel:
                raise ValueError("response event has the wrong type/phase/channel")
            if event["resp"] != transaction["expectedResponse"]:
                raise ValueError("response code disagrees with workload oracle")
            if kind == "write":
                response_order[_response_domain(transaction)].append(
                    transaction["expectedResponseSeq"]
                )
                if event["beatIndex"] is not None or \
                        event["beatCount"] is not None:
                    raise ValueError("B event unexpectedly carries beat metadata")
                if event["resp"] == "okay":
                    size = 1 << transaction["size"]
                    for beat in range(beat_count):
                        address = transaction["address"] + beat * size
                        bus_base = address // data_bus_bytes * data_bus_bytes
                        lane_base = address - bus_base
                        for lane in range(lane_base, lane_base + size):
                            if transaction["strobe"] == "full" or lane % 2 == 0:
                                shadow[bus_base + lane] = (
                                    transaction["dataSeed"] + beat * 17 + lane
                                ) & 0xFF
            else:
                beat = event["beatIndex"]
                if not isinstance(beat, int) or not 0 <= beat < beat_count or \
                        event["beatCount"] != beat_count:
                    raise ValueError("R beat index/count is invalid")
                if beat + 1 == beat_count:
                    response_order[_response_domain(transaction)].append(
                        transaction["expectedResponseSeq"]
                    )
                data = bytearray(data_bus_bytes)
                if event["resp"] == "okay":
                    size = 1 << transaction["size"]
                    address = transaction["address"] + beat * size
                    bus_base = address // data_bus_bytes * data_bus_bytes
                    lane_base = address - bus_base
                    for lane in range(lane_base, lane_base + size):
                        data[lane] = shadow.get(bus_base + lane, 0)
                if event["payloadDigest"] != payload_digest(data):
                    raise ValueError("R payload digest disagrees with shadow memory")
        else:
            raise ValueError("unknown trace event enum")

    for uid, transaction in by_uid.items():
        txn_events = observations[uid]
        addresses = [event for event in txn_events if event["eventEnum"] == 10]
        if len(addresses) != 1:
            raise ValueError("transaction does not have exactly one address event")
        if transaction["kind"] == "write":
            beats = [event["beatIndex"] for event in txn_events
                     if event["eventEnum"] == 11]
            responses = [event for event in txn_events if event["eventEnum"] == 20]
        else:
            beats = [event["beatIndex"] for event in txn_events
                     if event["eventEnum"] == 21]
            responses = [event for event in txn_events if event["eventEnum"] == 21
                         and event["beatIndex"] + 1 == transaction["beatCount"]]
        if beats != list(range(transaction["beatCount"])):
            raise ValueError("transaction has duplicate, missing, or reordered beats")
        if len(responses) != 1:
            raise ValueError("transaction does not have exactly one completion")

    for sequences in target_order.values():
        if sequences != list(range(len(sequences))):
            raise ValueError("targetSeq acceptance order is invalid")
    for sequences in response_order.values():
        if sequences != list(range(len(sequences))):
            raise ValueError("responseSeq retirement order is invalid")
    return events


def validate_final_consistency_trace(trace_path):
    events = _canonical_jsonl(trace_path)
    validate_trace_events(events)
    if len(events) != 4 or events[-1]["eventEnum"] != 30:
        raise ValueError("final-consistency trace must contain 3 W beats and drain")
    for index, event in enumerate(events[:-1]):
        if event["eventEnum"] != 11 or event["channel"] != "w" or \
                event["beatIndex"] != index or event["txnUid"] is not None or \
                event["dstNode"] is not None:
            raise ValueError("final-consistency W residual trace is malformed")
    return events


def _expected_channel_counts(workload):
    writes = [item for item in workload if item["kind"] == "write"]
    reads = [item for item in workload if item["kind"] == "read"]
    return [
        len(writes), sum(item["beatCount"] for item in writes), len(writes),
        len(reads), sum(item["beatCount"] for item in reads),
    ]


def validate_axi_result(result_path, case, workload_path, trace_path,
                        credit_path, config_hash, git_sha, expected_links,
                        expected_vcs, link_width_bits=128,
                        data_width_bits=512,
                        wire_header_bytes=(24, 16, 8, 24, 16)):
    result = load_json_strict(result_path)
    required = {
        "schema_version", "case", "status", "git_sha", "config_hash", "seed",
        "sim_ticks", "sim_network_cycles", "transactions_issued",
        "admission_attempts", "retry_cycles", "transactions_accepted",
        "transactions_completed", "transactions_error", "packets_injected",
        "packets_ejected", "flits_injected", "flits_ejected", "per_vnet",
        "measurement_window", "queue_high_water", "stall_events",
        "completion_order", "qos_transactions",
        "credit_ledger",
        "credit_mismatches_by_link_vc", "outstanding_at_exit",
        "orphan_w_at_exit", "rob_entries_at_exit", "message_buffers_at_exit",
        "local_delivery_at_exit", "adapter_ingress_at_exit",
        "response_obligations_at_exit", "business_events_at_exit",
        "router_vc_flits_at_exit", "quiescence_snapshot_at_exit",
        "quiescent_consecutive_cycles", "protocol_errors", "workload_sha256",
        "trace_sha256", "w_beats", "r_beats",
    }
    _require_keys(result, required, "AXI result")
    if result["schema_version"] != RESULT_SCHEMA_VERSION:
        raise ValueError("unknown AXI result schema_version")
    if result["case"] != case["name"] or result["status"] != "pass":
        raise ValueError("AXI result case/status mismatch")
    if result["git_sha"] != git_sha or result["config_hash"] != config_hash:
        raise ValueError("AXI result provenance mismatch")

    workload = _canonical_jsonl(workload_path)
    expected_channels = _expected_channel_counts(workload)
    expected_transactions = len(workload)
    for field in (
        "sim_ticks", "sim_network_cycles", "transactions_issued",
        "admission_attempts", "retry_cycles", "transactions_accepted",
        "transactions_completed", "transactions_error", "packets_injected",
        "packets_ejected", "flits_injected", "flits_ejected", "protocol_errors",
        "quiescent_consecutive_cycles", "w_beats", "r_beats",
    ):
        _count(result[field], field)
    if result["transactions_issued"] != expected_transactions or \
            result["transactions_accepted"] != expected_transactions or \
            result["transactions_completed"] != expected_transactions:
        raise ValueError("transaction issue/accept/complete conservation failed")
    if result["admission_attempts"] < result["transactions_accepted"]:
        raise ValueError("admission attempts are below accepted transactions")
    if result["transactions_error"] != case.get(
            "expected_error_transactions", 0):
        raise ValueError("architected error transaction count mismatch")
    if result["protocol_errors"] != 0:
        raise ValueError("successful result contains protocol errors")
    if result["w_beats"] != expected_channels[1] or \
            result["r_beats"] != expected_channels[4]:
        raise ValueError("AXI beat conservation failed")

    expected_qos = [0] * 16
    for index, transaction in enumerate(workload):
        qos = transaction.get("qos")
        if isinstance(qos, bool) or not isinstance(qos, int) or \
                not 0 <= qos < len(expected_qos):
            raise ValueError(f"workload transaction {index} has invalid AxQOS")
        expected_qos[qos] += 1
    if _count_vector(result["qos_transactions"], 16,
                     "qos_transactions") != expected_qos:
        raise ValueError("target AxQOS histogram disagrees with workload")

    completion_order = result["completion_order"]
    if not isinstance(completion_order, list) or \
            any(isinstance(index, bool) or not isinstance(index, int)
                for index in completion_order) or \
            sorted(completion_order) != list(range(expected_transactions)):
        raise ValueError("completion_order is not a transaction permutation")
    if case["name"] == "cross_id_reorder":
        by_axi_id = {
            transaction["axiId"]: transaction["planTxnIndex"]
            for transaction in workload
            if transaction["srcNode"] == 0 and
            transaction["srcPort"] == 0 and
            transaction["kind"] == "write" and
            transaction["axiId"] in (0, 1)
        }
        if set(by_axi_id) != {0, 1} or \
                completion_order.index(by_axi_id[1]) >= \
                completion_order.index(by_axi_id[0]):
            raise ValueError("fast AXI ID=1 did not complete before slow ID=0")

    per_vnet = result["per_vnet"]
    _require_keys(per_vnet, {
        "packets_injected", "packets_ejected", "flits_injected",
        "flits_ejected", "wire_bytes_injected", "wire_bytes_ejected",
        "input_vc_occupancy_flit_cycles", "input_vc_full_vc_cycles",
        "input_vc_full_events", "input_vc_max_occupancy",
        "credit_stall_vc_cycles", "vc_alloc_stall_vc_cycles",
        "ni_credit_stall_vc_cycles", "ni_vc_busy_cycles",
    }, "per_vnet")
    vectors = {key: _count_vector(value, 5, f"per_vnet.{key}")
               for key, value in per_vnet.items()}
    if vectors["packets_injected"] != expected_channels or \
            vectors["packets_ejected"] != expected_channels:
        raise ValueError("per-vnet packet conservation/workload mapping failed")

    data_bytes = data_width_bits // 8
    link_bytes = link_width_bits // 8
    wire_per_packet = [
        wire_header_bytes[index] + (data_bytes if index in (1, 4) else 0)
        for index in range(5)
    ]
    expected_wire = [expected_channels[index] * wire_per_packet[index]
                     for index in range(5)]
    expected_flits = [
        expected_channels[index] * math.ceil(wire_per_packet[index] / link_bytes)
        for index in range(5)
    ]
    if case["name"].startswith("buffer_depth_credit_"):
        depth = int(case["name"].rsplit("d", 1)[1])
        largest_packet_flits = max(
            math.ceil(wire_bytes / link_bytes) for wire_bytes in wire_per_packet
        )
        if largest_packet_flits <= depth:
            raise ValueError("I7 packet is not larger than the target VC depth")
    if vectors["wire_bytes_injected"] != expected_wire or \
            vectors["wire_bytes_ejected"] != expected_wire:
        raise ValueError("wire-byte accounting mismatch")
    if vectors["flits_injected"] != expected_flits or \
            vectors["flits_ejected"] != expected_flits:
        raise ValueError("dynamic packetization/flit accounting mismatch")
    if result["packets_injected"] != sum(expected_channels) or \
            result["packets_ejected"] != sum(expected_channels) or \
            result["flits_injected"] != sum(expected_flits) or \
            result["flits_ejected"] != sum(expected_flits):
        raise ValueError("top-level packet/flit conservation failed")

    measurement = result["measurement_window"]
    measurement_fields = (
        "packets_injected", "flits_injected", "wire_bytes_injected",
        "input_vc_occupancy_flit_cycles", "input_vc_full_vc_cycles",
        "input_vc_full_events", "input_vc_max_occupancy",
        "credit_stall_vc_cycles", "vc_alloc_stall_vc_cycles",
        "ni_credit_stall_vc_cycles", "ni_vc_busy_cycles",
    )
    _require_keys(measurement, {
        "enabled", "start_cycle", "end_cycle", "start_tick", "end_tick",
        "traffic_vnet", *measurement_fields,
    }, "measurement_window")
    if not isinstance(measurement["enabled"], bool):
        raise ValueError("measurement_window.enabled must be a boolean")
    for field in ("start_cycle", "end_cycle", "start_tick", "end_tick"):
        _count(measurement[field], f"measurement_window.{field}")
    measured_vectors = {
        field: _count_vector(
            measurement[field], 5, f"measurement_window.{field}"
        )
        for field in measurement_fields
    }
    matrix_match = re.fullmatch(
        r"buffer_matrix_(11111|24224|48448|8168816)_vc(1|2|4)_"
        r"(aw|w|b|ar|r|all)", case["name"],
    )
    is_matrix = case.get("generator") == "matrix"
    if is_matrix != bool(matrix_match):
        raise ValueError("buffer-matrix generator/name mismatch")
    if is_matrix:
        profile, _, traffic = matrix_match.groups()
        expected_vnet = -1 if traffic == "all" else \
            constants()["channel"][traffic]
        expected_window = constants()["buffer_matrix_window_cycles"]
        if measurement["enabled"] is not True or \
                measurement["start_cycle"] != expected_window["start"] or \
                measurement["end_cycle"] != expected_window["end"] or \
                measurement["traffic_vnet"] != expected_vnet or \
                measurement["start_tick"] >= measurement["end_tick"]:
            raise ValueError("buffer-matrix measurement boundary mismatch")
        for vnet, packets in enumerate(
                measured_vectors["packets_injected"]):
            selected = expected_vnet == -1 or expected_vnet == vnet
            if selected != (packets > 0):
                raise ValueError(
                    f"measurement traffic isolation failed for vnet {vnet}"
                )
        depths = constants()["buffer_profiles"][profile]
        if any(observed > depth for observed, depth in zip(
                measured_vectors["input_vc_max_occupancy"], depths)):
            raise ValueError("measurement VC high-water exceeded profile")
        for field in measurement_fields[3:]:
            if vectors[field] != measured_vectors[field]:
                raise ValueError(
                    f"per_vnet.{field} is not the measurement-window delta"
                )
    else:
        if measurement["enabled"] is not False or \
                measurement["traffic_vnet"] != -2 or \
                any(measurement[field] != 0 for field in (
                    "start_cycle", "end_cycle", "start_tick", "end_tick"
                )) or any(any(values) for values in measured_vectors.values()):
            raise ValueError("non-matrix case has an active measurement window")

    _require_keys(result["queue_high_water"], {
        "local_fifo", "message_buffer", "router_vc", "adapter_ingress",
    }, "queue_high_water")
    for group in ("local_fifo", "message_buffer", "router_vc", "adapter_ingress"):
        _count_vector(result["queue_high_water"][group], 5,
                      f"queue_high_water.{group}")
    if is_matrix and result["queue_high_water"]["router_vc"] != \
            measured_vectors["input_vc_max_occupancy"]:
        raise ValueError("router VC high-water is not window-scoped")
    _require_keys(result["stall_events"], {
        "local_fifo_full", "message_buffer_or_ni", "router_credit",
        "vc_allocation",
    }, "stall_events")
    for field in ("local_fifo_full", "message_buffer_or_ni",
                  "router_credit", "vc_allocation"):
        _count(result["stall_events"][field], f"stall_events.{field}")
    if is_matrix and (
            result["stall_events"]["router_credit"] !=
            sum(measured_vectors["credit_stall_vc_cycles"]) or
            result["stall_events"]["vc_allocation"] !=
            sum(measured_vectors["vc_alloc_stall_vc_cycles"])):
        raise ValueError("router stall summary is not window-scoped")
    for mask in case.get("expected_stall_mask", []):
        observed = {
            "local_fifo": result["stall_events"]["local_fifo_full"],
            "message_buffer_or_ni": result["stall_events"][
                "message_buffer_or_ni"
            ],
            "router_credit": result["stall_events"]["router_credit"],
            "orphan_or_quota": result.get("orphan_or_quota_stall", 0),
        }.get(mask)
        if observed is None or observed <= 0:
            raise ValueError(f"required stall class was not observed: {mask}")

    for field in EXIT_ZERO_FIELDS:
        if _count(result[field], field) != 0:
            raise ValueError(f"nonzero residual state: {field}")
    snapshot = result["quiescence_snapshot_at_exit"]
    _require_keys(snapshot, QUIESCENCE_FIELDS, "quiescence snapshot")
    if any(_count(snapshot[field], f"quiescence.{field}") != 0
           for field in QUIESCENCE_FIELDS):
        raise ValueError("Garnet quiescence snapshot is nonzero")
    if result["quiescent_consecutive_cycles"] < 2:
        raise ValueError("quiescence was not stable for two cycles")

    ledger = validate_credit_ledger(
        credit_path, expected_links=expected_links, expected_vcs=expected_vcs,
        require_restored=True,
    )
    credit = result["credit_ledger"]
    if credit["path"] != Path(credit_path).name or \
            credit["sha256"] != sha256_file(credit_path) or \
            credit["directed_links"] != ledger["directed_links"] or \
            credit["vcs_per_link"] != ledger["vcs_per_link"] or \
            credit["all_restored"] is not True or \
            result["credit_mismatches_by_link_vc"] != 0:
        raise ValueError("credit-ledger result summary/hash mismatch")
    if result["workload_sha256"] != sha256_file(workload_path) or \
            result["trace_sha256"] != sha256_file(trace_path):
        raise ValueError("workload/event trace SHA256 mismatch")
    validate_event_trace(trace_path, workload_path, data_bus_bytes=data_bytes)
    return result

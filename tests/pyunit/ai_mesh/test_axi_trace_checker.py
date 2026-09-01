from __future__ import annotations

import json

import pytest

from axi_result_verifier import validate_credit_ledger, validate_event_trace
from axi_test_lib import (
    build_negative_scenario,
    canonical_json_bytes,
    load_jsonl,
    payload_digest,
    workload_jsonl,
)


def _event(sequence, tick, phase, kind, channel=None, transaction=None,
           beat_index=None, payload=None, response=None):
    event = {
        "axiId": None,
        "beatCount": None,
        "beatIndex": beat_index,
        "channel": channel,
        "dstNode": None,
        "eventEnum": kind,
        "eventSeq": sequence,
        "occupancy": 0,
        "payloadDigest": payload,
        "phaseEnum": phase,
        "resp": response,
        "responseSeq": None,
        "schemaVersion": 1,
        "semanticBytes": None,
        "srcNode": None,
        "srcPort": None,
        "targetSeq": None,
        "tick": tick,
        "txnUid": None,
        "wireBytes": None,
        "writeOrdinal": None,
    }
    if transaction is not None:
        event.update({
            "axiId": transaction["axiId"],
            "dstNode": transaction["dstNode"],
            "responseSeq": transaction["expectedResponseSeq"],
            "srcNode": transaction["srcNode"],
            "srcPort": transaction["srcPort"],
            "targetSeq": transaction["expectedTargetSeq"],
            "txnUid": transaction["expectedTxnUid"],
            "writeOrdinal": transaction["expectedWriteOrdinal"],
        })
        if channel in ("aw", "ar", "w", "r"):
            event["beatCount"] = transaction["beatCount"]
        semantic = {"aw": 24, "w": 64, "b": 8, "ar": 24, "r": 64}
        wire = {"aw": 24, "w": 80, "b": 8, "ar": 24, "r": 80}
        event["semanticBytes"] = semantic[channel]
        event["wireBytes"] = wire[channel]
    return event


def _fixture(tmp_path):
    scenario = build_negative_scenario("n3a_unaligned_decerr")
    scenario["name"] = "trace_fixture"
    scenario["stable_scenario_id"] = "trace_fixture"
    scenario["transactions"] = [
        {
            "kind": "write", "source_index": 0, "dst_node": 1,
            "axi_id": 3, "address": "0x1000", "beat_count": 2, "size": 6,
            "data_seed": 7, "strobe": "full",
        },
        {
            "kind": "read", "source_index": 0, "dst_node": 1,
            "axi_id": 3, "address": "0x1000", "beat_count": 2, "size": 6,
            "data_seed": 0, "strobe": "full",
        },
    ]
    workload_path = tmp_path / "workload.jsonl"
    workload_path.write_bytes(workload_jsonl(scenario, "trace_fixture", 42))
    write, read = load_jsonl(workload_path)
    write_digests = [
        payload_digest(bytes((7 + beat * 17 + lane) & 0xFF
                             for lane in range(64)))
        for beat in range(2)
    ]
    events = [
        _event(0, 1000, 10, 10, "aw", write),
        _event(1, 2000, 10, 11, "w", write, 0, write_digests[0]),
        _event(2, 3000, 10, 11, "w", write, 1, write_digests[1]),
        _event(3, 4000, 20, 20, "b", write, response="okay"),
        _event(4, 5000, 10, 10, "ar", read),
        _event(5, 6000, 20, 21, "r", read, 0, write_digests[0], "okay"),
        _event(6, 7000, 20, 21, "r", read, 1, write_digests[1], "okay"),
        _event(7, 8000, 30, 30),
    ]
    trace_path = tmp_path / "event_trace.jsonl"
    _write_events(trace_path, events)
    return workload_path, trace_path, events


def _write_events(path, events):
    path.write_bytes(b"".join(canonical_json_bytes(event) for event in events))


def test_accepts_canonical_valid_trace(tmp_path):
    workload, trace, _ = _fixture(tmp_path)
    assert len(validate_event_trace(trace, workload)) == 8


def test_rejects_duplicate_or_missing_beats(tmp_path):
    workload, trace, events = _fixture(tmp_path)
    events[2]["beatIndex"] = 0
    events[2]["payloadDigest"] = events[1]["payloadDigest"]
    _write_events(trace, events)
    with pytest.raises(ValueError, match="duplicate|missing|reordered"):
        validate_event_trace(trace, workload)


def test_rejects_bad_last_or_response_order(tmp_path):
    workload, trace, events = _fixture(tmp_path)
    events[6]["beatCount"] = 1
    _write_events(trace, events)
    with pytest.raises(ValueError, match="beat index/count"):
        validate_event_trace(trace, workload)
    _, _, events = _fixture(tmp_path)
    events[6]["responseSeq"] = 1
    _write_events(trace, events)
    with pytest.raises(ValueError, match="responseSeq"):
        validate_event_trace(trace, workload)


def test_rejects_bad_memory_data(tmp_path):
    workload, trace, events = _fixture(tmp_path)
    events[5]["payloadDigest"] ^= 1
    _write_events(trace, events)
    with pytest.raises(ValueError, match="shadow memory"):
        validate_event_trace(trace, workload)


def test_rejects_bad_credit_ledger(tmp_path):
    entries = []
    for link in range(2):
        for vc in range(2):
            entries.append({
                "current": 4, "depth": 4, "initial": 4,
                "link_id": link, "owner_id": link, "owner_kind": "router",
                "port_id": 0, "returned": 2, "sent": 2,
                "vc": vc, "vnet": vc,
            })
    path = tmp_path / "credit_ledger.json"
    path.write_text(json.dumps({
        "schema_version": 1, "directed_links": 2,
        "vcs_per_link": 2, "entries": entries,
    }), encoding="utf-8")
    assert validate_credit_ledger(path, 2, 2)
    entries[0]["returned"] = 1
    path.write_text(json.dumps({
        "schema_version": 1, "directed_links": 2,
        "vcs_per_link": 2, "entries": entries,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="conservation"):
        validate_credit_ledger(path, 2, 2)

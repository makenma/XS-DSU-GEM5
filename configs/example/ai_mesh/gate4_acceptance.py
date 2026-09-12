from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MESH_IR_ROOT = REPO / "util" / "mesh_ir"
if str(MESH_IR_ROOT) not in sys.path:
    sys.path.insert(0, str(MESH_IR_ROOT))

import jsonschema

from mesh_ir.acceptance import (
    ARTIFACT_BASENAMES,
    atomic_write_json,
    canonical_digest,
    read_json_artifact,
    validate_schema,
)
from mesh_ir.gate4_oracle import (
    Gate4Execution,
    Gate4RunOracle,
    ORACLE_CHECKS,
)


GATE4_OBSERVATION_BASENAME = "gate4_observation.json"


def _events(events, **fields):
    return [
        event
        for event in events
        if all(event.get(name) == value for name, value in fields.items())
    ]


def _observation_schema():
    return json.loads(
        (REPO / "schemas/ai_mesh/gate4_observation_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )


def _validate_observation(document):
    validator = jsonschema.Draft202012Validator(_observation_schema())
    errors = sorted(
        validator.iter_errors(document), key=lambda item: list(item.path)
    )
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path)
        raise ValueError(f"gate4 observation {location}: {error.message}")


def _before(left, right):
    return (left["tick"], left["phase"], left["ordinal"]) < (
        right["tick"], right["phase"], right["ordinal"]
    )


def _last_before(events, target, **fields):
    matches = [
        event for event in _events(events, **fields) if _before(event, target)
    ]
    return matches[-1] if matches else None


def _stage_sequence(events):
    starts = _events(events, kind="HOST_STAGE_START")
    return [event["object"] for event in starts]


def _user_of(request_id):
    return request_id >> 32


def _evidence(name, events, final, metrics):
    terminals = _events(events, kind="TASK_TERMINAL")
    terminal_status = [event["status"] for event in terminals]
    starts = _stage_sequence(events)
    if name == "single_business_done_terminal":
        return terminal_status == ["BUSINESS_DONE"] and metrics.get(
            "completed_tasks"
        ) == 1
    if name == "no_core_start_surrogate_only":
        return metrics.get("core_starts", 0) == 0
    if name == "raw_log_parse_repair_generate_order":
        return (
            starts == ["COMPILE", "LOG_PARSE", "COMPILE", "TEST"]
            and final["npu_cq_ack_seq"] == 2
        )
    if name == "test_fail_returns_through_npu":
        return (
            starts == ["COMPILE", "TEST", "LOG_PARSE", "COMPILE", "TEST"]
            and final["npu_cq_ack_seq"] == 2
        )
    if name == "cap_round_no_extra_parse_or_id":
        return (
            "LOG_PARSE" not in starts
            and final["npu_cq_ack_seq"] == 1
            and metrics.get("failed_tasks") == 1
        )
    if name == "queue_wait_observed":
        enqueue = {}
        begin = {}
        for event in events:
            key = (event["object"], event["request_id"])
            if event["kind"] == "HOST_STAGE_ENQUEUE":
                enqueue[key] = event["tick"]
            elif event["kind"] == "HOST_STAGE_START":
                begin[key] = event["tick"]
        waits = [
            begin[key] - enqueue[key] for key in begin if key in enqueue
        ]
        return any(wait > 0 for wait in waits)
    if name == "no_starvation_all_terminal":
        return len(terminals) == 12 and all(
            status == "BUSINESS_DONE" for status in terminal_status
        )
    if name == "all_twelve_terminal":
        return _evidence("no_starvation_all_terminal", events, final, metrics)
    if name in (
        "concurrent_accepts_before_first_completion",
        "peak_outstanding_generates",
    ):
        def order(event):
            return (event["tick"], event["phase"], event["ordinal"])

        controls = {
            event["request_id"]
            for event in _events(events, kind="CONTROL_SUBMIT")
        }
        generates = {
            event["request_id"]
            for event in _events(events, kind="SQ_CONSUME")
            if event["request_id"] is not None
        } - controls
        accepts = [
            event
            for event in _events(events, kind="SQ_CONSUME")
            if event["request_id"] in generates
        ]
        consumes = [
            event
            for event in _events(events, kind="CQ_CONSUME")
            if event["request_id"] in generates
        ]
        if not accepts or not consumes:
            return False
        first_consume = min(consumes, key=order)
        if name == "concurrent_accepts_before_first_completion":
            return sum(1 for event in accepts if order(event) < order(first_consume)) >= 3
        stream = sorted(
            [(order(event), 1) for event in accepts]
            + [(order(event), -1) for event in consumes]
        )
        outstanding = 0
        peak = 0
        for _, delta in stream:
            outstanding += delta
            peak = max(peak, outstanding)
        return peak >= 3
    if name == "repair_input_excerpt_share":
        return (
            metrics.get("repair_input_excerpt_bytes") == 4096
            and metrics.get("excerpt_bytes") == 4096
            and metrics.get("raw_log_bytes") == 32768
        )
    if name == "fabric_raw_log_zero":
        return metrics.get("npu_fabric_raw_log_bytes", 0) == 0 and metrics.get(
            "agent_live_objects"
        ) == 0
    if name == "frozen_outcome_replay":
        return terminal_status == ["BUSINESS_DONE"] and metrics.get(
            "completed_tasks"
        ) == 1
    if name == "three_distinct_paths":
        per_user = {}
        for event in _events(events, kind="HOST_STAGE_START"):
            per_user.setdefault(_user_of(event["request_id"]), []).append(
                event["object"]
            )
        parse_users = {
            user for user, kinds in per_user.items() if "LOG_PARSE" in kinds
        }
        plain_users = {
            user
            for user, kinds in per_user.items()
            if "LOG_PARSE" not in kinds
        }
        return (
            set(per_user) == {0, 1, 2}
            and len(parse_users) == 2
            and plain_users == {1}
            and all(
                status == "BUSINESS_DONE" for status in terminal_status
            )
        )
    if name == "msi_per_completion":
        consumed = _events(events, kind="CQ_CONSUME")
        irq = _events(events, kind="IRQ_DELIVER")
        return len(irq) == len(consumed) == 5 and metrics.get(
            "irq_deliveries"
        ) == 5
    if name == "suppressed_tasks_no_ghost_submission":
        suppressed = {
            event["request_id"]
            for event in _events(events, kind="TASK_SUPPRESSED")
        }
        doorbells = {
            event["request_id"]
            for event in _events(
                events, kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="AW"
            )
        }
        consumed = {
            event["request_id"]
            for event in _events(events, kind="CQ_CONSUME")
        }
        finals = (
            metrics.get("completed_tasks", 0)
            + metrics.get("failed_tasks", 0)
            + metrics.get("infra_failed_tasks", 0)
        )
        return not (suppressed & (doorbells | consumed)) and finals == len(
            terminals
        )
    if name == "suppressed_after_stop_tick":
        stop_tick = max(
            event["tick"]
            for event in _events(events, kind="TASK_SUPPRESSED")
        )
        think = _events(events, kind="TASK_THINK_READY")
        suppressed = _events(events, kind="TASK_SUPPRESSED")
        return bool(suppressed) and all(
            event["tick"] < stop_tick for event in think
        )
    if name == "aging_reservation_observed":
        return metrics.get("aging_reservations", 0) >= 1
    if name == "zero_local_stage_axi":
        return not any(
            event["kind"] == "AXI_ACCEPT"
            and event["object"] in ("RAW_LOG", "EXCERPT")
            for event in events
        )
    if name == "cancel_suppresses_target_stages":
        target = (1 << 32) | 1
        stages = _events(
            events,
            kind="HOST_STAGE_START",
        )
        return not any(
            event["request_id"] == target for event in stages
        ) and len(_events(events, kind="CANCEL_JOIN_RESOLVED")) == 1
    if name == "business_after_control_resolve":
        resolve = _events(events, kind="CONTROL_RESOLVE")
        business = [
            event
            for event in events
            if event["kind"] in ("HOST_STAGE_ENQUEUE", "TASK_TERMINAL")
            and _user_of(event["request_id"]) == 1
        ]
        if not resolve or not business:
            return False
        return (
            min(event["tick"] for event in business) >= resolve[0]["tick"]
        )
    if name == "target_business_continues":
        stages = [
            event["object"]
            for event in _events(events, kind="HOST_STAGE_START")
            if _user_of(event["request_id"]) == 2
        ]
        return stages == ["COMPILE", "TEST", "LOG_PARSE", "COMPILE", "TEST"]
    if name == "standalone_waiter_only":
        waiters = _events(events, kind="CONTROL_TERMINAL")
        return (
            len(waiters) == 1
            and waiters[0]["status"]
            in ("NOT_FOUND", "ALREADY_TERMINAL")
            and metrics.get("completed_tasks") == 3
        )
    if name == "single_error_cq_partial_prefix":
        errors = [
            event
            for event in _events(events, kind="CQ_CONSUME")
            if event["status"] == "ERROR"
        ]
        return (
            len(errors) == 1
            and metrics.get("output_b_error_requests") == 1
            and metrics.get("output_b_error_prefix_bytes") == 0
        )
    if name == "object_produce_infra_drain":
        faults = _events(events, kind="HOST_FAULT")
        return (
            [(event["object"], event["status"]) for event in faults]
            == [("OBJECT_PRODUCE", "E_HOST_LOCAL_OBJECT")]
            and terminal_status == ["INFRA_FAILED"]
            and metrics.get("raw_log_bytes") == 0
        )
    if name == "object_read_infra_drain":
        faults = _events(events, kind="HOST_FAULT")
        return (
            [(event["object"], event["status"]) for event in faults]
            == [("OBJECT_READ", "E_HOST_LOCAL_OBJECT")]
            and terminal_status == ["INFRA_FAILED"]
        )
    if name == "big_log_local_io_dominates":
        def duration(kind):
            starts = [
                event
                for event in events
                if event["kind"] == "HOST_STAGE_START" and event["object"] == kind
            ]
            dones = [
                event
                for event in events
                if event["kind"] == "HOST_STAGE_DONE" and event["object"] == kind
            ]
            return dones[0]["tick"] - starts[0]["tick"] if starts and dones else -1

        repair_pull = sum(
            event["bytes"]
            for event in events
            if event["kind"] == "AXI_ACCEPT"
            and event["channel"] == "R"
            and event["control"] == "PROMPT"
            and event["request_id"] == 2
        )
        return (
            metrics.get("raw_log_bytes") == 104857600
            and metrics.get("excerpt_bytes") == 16384
            and metrics.get("repair_input_excerpt_bytes") == 16384
            and metrics.get("npu_fabric_raw_log_bytes", 0) == 0
            and duration("LOG_PARSE") >= 1639656000 - 1
            and duration("COMPILE") >= 1639408000 - 1
            and repair_pull == 4608
        )
    if name == "standalone_after_target_error_cq":
        waiters = _events(events, kind="CONTROL_TERMINAL")
        user_two_starts = [
            event
            for event in _events(events, kind="HOST_STAGE_START")
            if _user_of(event["request_id"]) == 2
        ]
        return (
            sorted(event["status"] for event in terminals)
            == ["BUSINESS_DONE", "BUSINESS_DONE", "BUSINESS_FAILED"]
            and len(waiters) == 1
            and waiters[0]["status"] == "ALREADY_TERMINAL"
            and not user_two_starts
        )
    if name == "join_target_success_wins":
        joins = _events(events, kind="CANCEL_JOIN_RESOLVED")
        user_one_starts = [
            event["object"]
            for event in _events(events, kind="HOST_STAGE_START")
            if _user_of(event["request_id"]) == 1
        ]
        return (
            [event["status"] for event in joins] == ["TARGET_SUCCESS_WINS"]
            and not _events(events, kind="CONTROL_TERMINAL")
            and sorted(terminal_status) == ["BUSINESS_DONE"] * 3
            and user_one_starts == ["COMPILE", "TEST"]
            and metrics.get("failed_tasks") == 0
        )
    if name == "join_target_error_wins":
        joins = _events(events, kind="CANCEL_JOIN_RESOLVED")
        errors = [
            event
            for event in _events(events, kind="CQ_CONSUME")
            if event["status"] == "ERROR"
        ]
        return (
            [event["status"] for event in joins] == ["TARGET_ERROR_WINS"]
            and not _events(events, kind="CONTROL_TERMINAL")
            and sorted(terminal_status)
            == ["BUSINESS_DONE", "BUSINESS_DONE", "BUSINESS_FAILED"]
            and len(errors) == 1
            and _user_of(errors[0]["request_id"]) == 1
            and metrics.get("failed_tasks") == 1
        )
    if name == "control_cq_first_order" or name == "target_cq_first_order":
        submit = _events(events, kind="CONTROL_SUBMIT")
        if not submit:
            return False
        command_id = submit[0]["request_id"]
        assigns = {
            event["request_id"]: event
            for event in _events(events, kind="CQ_ASSIGN")
        }
        command = assigns.get(command_id)
        target = assigns.get((1 << 32) | 1)
        if command is None or target is None:
            return False
        command_first = command["absolute_seq"] < target["absolute_seq"]
        return command_first if name == "control_cq_first_order" else (
            target["absolute_seq"] < command["absolute_seq"]
        )
    if name == "same_edge_cancel_before_intent":
        submit = _events(events, kind="CONTROL_SUBMIT")
        waiters = _events(events, kind="CONTROL_TERMINAL")
        if not submit or not waiters:
            return False
        target_consume = _last_before(
            events,
            submit[0],
            kind="CQ_CONSUME",
            request_id=(2 << 32) | 1,
        )
        ready = _events(events, kind="CONTROL_READY")
        return (
            target_consume is not None
            and bool(ready)
            and target_consume["tick"] <= ready[0]["tick"]
            and ready[0]["tick"] <= submit[0]["tick"]
            and waiters[0]["status"] == "ALREADY_TERMINAL"
            and not _events(events, kind="CANCEL_JOIN_RESOLVED")
        )
    if name == "command_cq_before_doorbell_b":
        submit = _events(events, kind="CONTROL_SUBMIT")
        if not submit:
            return False
        command_id = submit[0]["request_id"]
        commits = _events(
            events, kind="PUBLICATION_COMMIT", request_id=command_id
        )
        cq_writes = [
            event
            for event in events
            if event["kind"] == "AXI_ACCEPT"
            and event["channel"] == "W"
            and event["control"] == "CQ_ENTRY"
            and event["request_id"] == command_id
        ]
        joins = _events(events, kind="CANCEL_JOIN_RESOLVED")
        if not commits or not cq_writes or not joins:
            return False
        return (
            max(event["tick"] for event in cq_writes) < commits[0]["tick"]
            and joins[0]["tick"] >= commits[0]["tick"]
        )
    if name == "control_local_rollback":
        submit = _events(events, kind="CONTROL_SUBMIT")
        if not submit:
            return False
        command_id = submit[0]["request_id"]
        consumed = {
            event["request_id"] for event in _events(events, kind="CQ_CONSUME")
        }
        rollbacks = _events(events, kind="PUBLICATION_ROLLBACK")
        waiters = _events(events, kind="CONTROL_TERMINAL")
        return (
            len(rollbacks) == 1
            and rollbacks[0]["request_id"] == command_id
            and command_id not in consumed
            and len(waiters) == 1
            and waiters[0]["status"] == "LOCAL_SUBMIT_FAILED"
            and not _events(events, kind="CANCEL_JOIN_RESOLVED")
            and all(
                status == "BUSINESS_DONE" for status in terminal_status
            )
        )
    if name == "illegal_dual_leg_fatal":
        fatal_events = _events(events, kind="FATAL")
        injected = _events(events, kind="FAULT_INJECT", object="CQ_ENTRY")
        premature = [
            event
            for event in events
            if event["kind"] in (
                "HOST_STAGE_ENQUEUE",
                "HOST_STAGE_START",
                "HOST_STAGE_DONE",
                "TASK_TERMINAL",
            )
            and _user_of(event["request_id"]) == 1
        ]
        return (
            bool(fatal_events)
            and metrics.get("fatal_records", 0) == 1
            and bool(injected)
            and not premature
            and final["fatal"]
        )
    if name == "output_drain_split_prefix":
        errors = [
            event
            for event in _events(events, kind="CQ_CONSUME")
            if event["status"] == "ERROR"
        ]
        return (
            len(errors) == 1
            and metrics.get("output_b_error_requests") == 1
            and metrics.get("output_b_error_prefix_bytes") == 8192
        )
    if name == "cancel_output_partial_prefix":
        target = 1
        command = 2
        output_aw = _events(
            events, kind="AXI_ACCEPT", control="OUTPUT", channel="AW"
        )
        output_b = _events(
            events, kind="AXI_ACCEPT", control="OUTPUT", channel="B"
        )
        resolve = _events(events, kind="CONTROL_RESOLVE")
        ready = _events(events, kind="CONTROL_READY")
        joins = _events(events, kind="CANCEL_JOIN_RESOLVED")
        consume_status = {
            event["request_id"]: event["status"]
            for event in _events(events, kind="CQ_CONSUME")
        }
        terminal = _events(events, kind="TASK_TERMINAL")
        return (
            len(resolve) == 1
            and resolve[0]["request_id"] == command
            and resolve[0]["status"] == "SUCCESS"
            and len(joins) == 1
            and joins[0]["request_id"] == command
            and joins[0]["status"] == "CANCEL_WINS"
            and consume_status.get(target) == "CANCELLED"
            and consume_status.get(command) == "SUCCESS"
            and metrics.get("output_cancel_requests") == 1
            and metrics.get("output_cancel_prefix_bytes") == 8192
            and len(output_aw) == 2
            and len(output_b) == 2
            and all(event["tick"] <= resolve[0]["tick"] for event in output_aw)
            and bool(output_b)
            and bool(ready)
            and output_b[0]["tick"] <= ready[0]["tick"]
            and ready[0]["tick"] <= output_aw[-1]["tick"]
            and [event["status"] for event in terminal]
            == ["BUSINESS_FAILED"]
            and terminal[0]["request_id"] == target
            and terminal[0]["tick"] >= joins[0]["tick"]
            and metrics.get("failed_tasks") == 1
            and not _events(events, kind="HOST_STAGE_START")
        )
    if name == "no_early_host_visible_move":
        for consume in _events(events, kind="CQ_CONSUME"):
            tail = _last_before(
                events,
                consume,
                kind="LOCAL_READ",
                object="METADATA_READ",
                status="TAIL",
                request_id=consume["request_id"],
            )
            if tail is None:
                return False
        for enqueue in _events(events, kind="HOST_STAGE_ENQUEUE"):
            consume = _last_before(
                events,
                enqueue,
                kind="CQ_CONSUME",
                request_id=enqueue["request_id"],
            )
            if consume is None:
                return False
        return True
    return False


def _plans_digests(plans):
    return {
        "runtime_config": plans["runtime_config"],
        "workload_plan": plans["workload_plan"],
        "control_plan": plans["control_plan"],
        "command_identity": plans["command_identity"],
        "host_task_identity": plans["host_task_identity"],
        "host_arena_object_plan": plans["host_arena_object_plan"],
        "capacity_plan": plans["capacity_plan"],
        "surrogate_profiles": plans["surrogate_profiles"],
        "plan_image": plans["plan_image"],
    }


def write_gate4_artifacts(
    plans: dict,
    execution: Gate4Execution,
    exit_code: int,
    facts_path: Path,
    artifact_dir: Path,
    case_id: str,
    subcase: str,
    hardware: dict,
):
    if exit_code not in (0, 20):
        raise ValueError(f"unexpected Gate4 exit code {exit_code}")
    oracle = Gate4RunOracle(execution, facts_path)
    events, final, metrics = oracle.events, oracle.final, oracle.metrics
    if final["fatal"] != (exit_code == 20):
        raise ValueError("Gate4 exit code and final fatal state differ")
    facts_digest = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    observation = {
        "schema": "ai_mesh_gate4_observation_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "configuration": {
            "sq_depth": hardware["sq_depth"],
            "cq_depth": hardware["cq_depth"],
            "control_bytes": 8,
            "data_bus_bytes": 64,
            "non_msi_axi_ids": {
                "SQ_DOORBELL": hardware["doorbell_axi_id"],
                "SQ_HEAD_UPDATE": hardware["sq_head_axi_id"],
                "CQ_TAIL_UPDATE": hardware["cq_tail_axi_id"],
                "CQ_HEAD_ACK": hardware["ack_axi_id"],
            },
        },
        "plans": _plans_digests(plans),
        "facts_digest": facts_digest,
        "metrics": {
            name: value
            for name, value in sorted(metrics.items())
            if not name.startswith("_")
        },
        "events": events,
        "final": final,
    }
    _validate_observation(observation)
    atomic_write_json(
        artifact_dir / GATE4_OBSERVATION_BASENAME, observation
    )
    rows, ownership, unattributed, matched = oracle.traffic_artifact_rows()
    expected_projection = {
        "classes": [
            [row["traffic_class"], row["expected_bytes"], row["expected_packets"]]
            for row in rows
        ],
        "ownership": [
            [row["owner_key_wire"], row["expected_bytes"]] for row in ownership
        ],
    }
    actual_projection = {
        "classes": [
            [row["traffic_class"], row["actual_bytes"], row["actual_packets"]]
            for row in rows
        ],
        "ownership": [
            [row["owner_key_wire"], row["actual_bytes"]] for row in ownership
        ],
    }
    traffic = {
        "schema": "ai_mesh_traffic_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "status": "PASS" if matched else "FAIL",
        "oracle_digest": canonical_digest(expected_projection),
        "actual_digest": canonical_digest(actual_projection),
        "classes": rows,
        "ownership": ownership,
        "unattributed_bytes": unattributed,
        "fatal_cut": bool(final["fatal"]),
    }
    validate_schema("traffic_v1.schema.json", traffic)
    atomic_write_json(artifact_dir / ARTIFACT_BASENAMES["TRAFFIC_JSON"], traffic)
    oracle_results = oracle.run()
    checks = []
    for check in ORACLE_CHECKS:
        observed = oracle_results[check]
        checks.append(
            {
                "name": check,
                "status": "PASS" if observed else "FAIL",
                "expected": {"kind": "BOOL", "value": True},
                "observed": {"kind": "BOOL", "value": observed},
            }
        )
    for name in _subcase_evidence(case_id, subcase):
        observed = _evidence(name, events, final, metrics)
        checks.append(
            {
                "name": name,
                "status": "PASS" if observed else "FAIL",
                "expected": {"kind": "BOOL", "value": True},
                "observed": {"kind": "BOOL", "value": observed},
            }
        )
    failures = [check["name"] for check in checks if check["status"] == "FAIL"]
    invariants = {
        "schema": "ai_mesh_invariants_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "status": "FAIL" if failures else "PASS",
        "registry_digest": canonical_digest(
            [check["name"] for check in checks]
        ),
        "checks": checks,
        "first_failure": failures[0] if failures else None,
    }
    validate_schema("invariants_v1.schema.json", invariants)
    atomic_write_json(
        artifact_dir / ARTIFACT_BASENAMES["INVARIANTS_JSON"], invariants
    )
    manifest = read_json_artifact(
        artifact_dir / ARTIFACT_BASENAMES["RUN_MANIFEST"],
        artifact_dir,
        "run_manifest_v1.schema.json",
    )
    run_manifest_digest = canonical_digest(manifest)
    ledger = {
        "live_cq_obligations": final["live_cq_obligations"],
        "fatal_cq_obligations": final["fatal_cq_obligations"],
        "fatal_sq_intakes": metrics.get("fatal_sq_intakes", 0),
        "fatal_publications": metrics.get("fatal_publications", 0),
        "ambiguous_publications": metrics.get("ambiguous_publications", 0),
        "host_ack_wait_b": final["ack_wait_b"],
        "host_ack_b_error": metrics.get("host_ack_b_error", 0),
        "msi_rob_entries": final["msi_rob_entries"],
        "fatal_records": metrics.get("fatal_records", 0),
    }
    child_report = {
        "schema": "ai_mesh_child_scenario_report_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "run_exit_reason": "INFRA_FATAL" if final["fatal"] else "QUIESCENT_SUCCESS",
        "first_fatal": (
            {"symbol": oracle.fatal["symbol"], "value": oracle.fatal["value"]}
            if oracle.fatal
            else None
        ),
        "watchdog_fired": False,
        "ledger_summary": ledger,
        "global_quiescence": not final["fatal"],
        "run_manifest_digest": run_manifest_digest,
    }
    validate_schema("child_scenario_report_v1.schema.json", child_report)
    atomic_write_json(artifact_dir / "child_report.json", child_report)
    if final["fatal"]:
        from gate3_acceptance import _fatal_error, _fatal_snapshot

        fatal_error = _fatal_error(metrics, oracle.fatal)
        snapshot = _fatal_snapshot(
            case_id,
            subcase,
            run_manifest_digest,
            fatal_error,
            final,
            metrics,
            ledger,
        )
        validate_schema("fatal_snapshot_v1.schema.json", snapshot)
        atomic_write_json(
            artifact_dir / ARTIFACT_BASENAMES["FATAL_SNAPSHOT"], snapshot
        )
    return oracle


def _subcase_evidence(case_id, subcase):
    from mesh_ir.gate4_contract import GATE4_CASES

    for requirement in GATE4_CASES[case_id]:
        if requirement.name == subcase:
            return requirement.evidence
    return ()

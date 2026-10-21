"""Gate 6 R3 acceptance artifacts for the real serving run (E2E-D).

Every check is derived from the immutable fixture program plus the runtime
artifacts (``mesh_result.json``, ``gate6_facts.tsv``).  The run's own labels are
never trusted: the descriptor closure, the per-phase KV read window and the
terminal ledger are recomputed by :mod:`mesh_ir.serving_e2e_oracle`, the phase
serialization is recomputed from the core issue ticks, and the traffic artifact
is rebuilt from ``program.expected_traffic``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mesh_ir.acceptance import (
    ARTIFACT_BASENAMES,
    atomic_write_json,
    build_dma_traffic,
    canonical_digest,
    read_json_artifact,
    validate_run_manifest,
    validate_schema,
    validate_traffic,
)
from mesh_ir.generated import abi as A
from mesh_ir.execution_view import build_execution_views
from mesh_ir.kv_layout import kv_layout_projection
from mesh_ir.scheduled.addressing import descriptor_endpoint_span
from mesh_ir.scheduled.model import EndpointSide
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.serving_contract_digest import frozen_expectation
from mesh_ir.serving_output_bytes import (
    dual_fnv_digest,
    surrogate_output_bytes,
)
from mesh_ir.model import MeshIrError
from mesh_ir.serving_e2e_oracle import verify_serving_run

CHAIN_CODES = ("E_BINDING_ROLE", "E_TRAFFIC_MISMATCH", "E_KV_TOKEN_MISMATCH")
CQ_CODES = ("E_KV_STATE",)

LEDGER_METRICS = (
    ("live_cq_obligations", "live_cq_obligations", 0),
    ("fatal_cq_obligations", "fatal_cq_obligations", 0),
    ("fatal_sq_intakes", "fatal_sq_intakes", 0),
    ("fatal_publications", "fatal_publications", 0),
    ("ambiguous_publications", "ambiguous_publications", 0),
    ("host_ack_wait_b", "ack_wait_b", 0),
    ("host_ack_b_error", "host_ack_b_error", 0),
    ("msi_rob_entries", "msi_rob_entries", 0),
    ("fatal_records", "fatal_records", 0),
)

CQ_COUNTERS = (
    ("cq_assignments", "cq_assignments", 1),
    ("core_starts", "core_starts", 1),
    ("live_contexts", "live_contexts", 0),
)

HOST_COUNTERS = (
    ("completed_tasks", "completed_tasks", 1),
    ("failed_tasks", "failed_tasks", 0),
    ("infra_failed_tasks", "infra_failed_tasks", 0),
    ("metadata_reads_validated", "metadata_reads_validated", 1),
    ("frontend_drained", "frontend_drained", 1),
    ("kv_session_records", "kv_session_records", 1),
    ("kv_session_tombstones", "kv_session_tombstones", 0),
    ("generated_code_reads_validated", "generated_code_reads_validated", 1),
    ("live_submissions", "live_submissions", 0),
    ("live_sq_intakes", "live_sq_intakes", 0),
    ("agent_live_objects", "agent_live_objects", 0),
)

FINAL_METRICS = (
    ("core_starts", "core_starts"),
    ("cq_assignments", "cq_assignments"),
    ("live_contexts", "live_contexts"),
    ("live_cq_obligations", "live_cq_obligations"),
    ("live_submissions", "live_submissions"),
    ("msi_rob_entries", "msi_rob_entries"),
    ("ack_wait_b", "ack_wait_b"),
    ("irq_deliveries", "irq_deliveries"),
    ("driver_cq_consumer_seq", "driver_cq_consumer_seq"),
    ("npu_cq_ack_seq", "npu_cq_ack_seq"),
)


def _u64_check(name, observed, expected):
    return {
        "name": name,
        "status": "PASS" if observed == expected else "FAIL",
        "expected": {"kind": "U64", "value": expected},
        "observed": {"kind": "U64", "value": observed},
    }


def _instances_match_views(result, views):
    instances = result["instances"]
    if [entry["instance"] for entry in instances] != list(range(1, len(views) + 1)):
        return False
    return all(
        sum(core["completed"] for core in frame["cores"].values()) == len(view.command_ids) and
        sum(core["errored"] for core in frame["cores"].values()) == 0
        for view, frame in zip(views, instances, strict=True))


def _serialization_violations(views, frames):
    if len(frames) != len(views):
        return 1
    windows = []
    for view, frame in zip(views, frames, strict=True):
        issued = [row for core in frame["cores"].values() for row in core["observations"]["commands"] if row["issued"]]
        if len(issued) != len(view.command_ids) or {row["command_id"] for row in issued} != view.command_ids:
            return 1
        windows.append((min(row["issue_tick"] for row in issued), max(row["issue_tick"] for row in issued)))
    return sum(before[1] >= after[0] for before, after in zip(windows, windows[1:]))


def _counter_deviations(rows, metrics):
    return sum(metrics.get(key) != expected for _, key, expected in rows)


def _final_matches(metrics, final):
    return all(metric in metrics and final.get(field) == metrics[metric]
               for field, metric in FINAL_METRICS)


def _published_stores(program):
    view = build_execution_views(program).views[-1]
    return {descriptor.descriptor_id
            for descriptor in program.dma_descriptors
            if descriptor.kind == A.DMA_KIND.STORE and
            descriptor.descriptor_id in view.descriptor_ids}


def _expected_appends(program):
    kv_tensors = {row["tensor"]["tensor_id"] for row in kv_layout_projection(program)["bindings"]}
    return {
        view.profile_id: sum(
            descriptor.descriptor_id in view.descriptor_ids and
            descriptor.kind == A.DMA_KIND.STORE and
            descriptor.dst.tensor_id in kv_tensors
            for descriptor in program.dma_descriptors)
        for view in build_execution_views(program).views
    }


def _phase_order_violations(serving_phases, core_start_tick):
    violations = 0
    previous = 0
    for phase in serving_phases:
        commit = phase["commit_tick"]
        if commit <= previous:
            violations += 1
        previous = commit
        if core_start_tick == 0 or phase["core_drain_tick"] < core_start_tick or \
                (phase["append_terminal_tick"] != 0 and
                 phase["append_terminal_tick"] < core_start_tick):
            violations += 1
    return violations


def _core_start_tick(events):
    for event in events:
        if event["kind"] == "CORE_START":
            return event["tick"]
    return 0


def _phase_descriptor_violations(program, serving_phases):
    expected = _expected_appends(program)
    return sum(phase["accepted_descriptors"] !=
               expected.get(phase["mesh_profile_id"], -1)
               for phase in serving_phases)


def _expected_host_digest(span, frozen=None):
    digest = None
    if frozen is not None:
        return frozen["host_digest"]
    elif span and span.get("semantic_digest"):
        digest = bytes.fromhex(span["semantic_digest"])
    if digest is None or len(digest) != 32:
        return None
    return dual_fnv_digest(surrogate_output_bytes(digest, span["bytes"]))


def _published_stores_in_order(program):
    command_ids = build_execution_views(program).views[-1].command_ids
    stores = [descriptor for descriptor in program.dma_descriptors
              if descriptor.kind == A.DMA_KIND.STORE and
              descriptor.command_id in command_ids]
    stores.sort(key=lambda descriptor: descriptor.dst.offset_bytes)
    return stores


def _payload_violations(result, span, program, frozen=None):
    digest = None
    if frozen is not None:
        digest = bytes.fromhex(frozen["semantic_digest"])
    elif span.get("semantic_digest"):
        digest = bytes.fromhex(span["semantic_digest"])
    if digest is None or len(digest) != 32:
        return 1
    content = frozen["content"] if frozen is not None else \
        surrogate_output_bytes(digest, span["bytes"])
    stores = _published_stores_in_order(program)
    base = stores[0].dst.offset_bytes
    rows = {row["descriptor_id"]: row["payload_digest"]
            for row in result["transport"]}
    violations = 0
    for descriptor in stores:
        start = descriptor.dst.offset_bytes - base
        expected = dual_fnv_digest(
            content[start:start + descriptor.useful_bytes])
        if rows.get(descriptor.descriptor_id) != expected:
            violations += 1
    return violations


def _kv_payload_violations(program, result, frozen):
    if frozen is None:
        return 1
    kv_tensors = {row["tensor"]["tensor_id"] for row in kv_layout_projection(program)["bindings"]}
    uses = {(use.descriptor_id, use.side): use for use in program.semantics.endpoint_uses}
    actual = {row["descriptor_id"]: row["payload_digest"]
              for row in result["transport"]}
    content = frozen["kv_content"]
    violations = 0
    for descriptor in program.dma_descriptors:
        if descriptor.src.tensor_id not in kv_tensors and descriptor.dst.tensor_id not in kv_tensors:
            continue
        if descriptor.kind in (A.DMA_KIND.LOAD, A.DMA_KIND.PREFETCH):
            side, stride = EndpointSide.SRC, descriptor.src_stride_bytes
        elif descriptor.kind in (A.DMA_KIND.STORE, A.DMA_KIND.LOCAL_FILL):
            side, stride = EndpointSide.DST, descriptor.dst_stride_bytes
        else:
            violations += 1
            continue
        _, _, _, _, _, begin, _ = descriptor_endpoint_span(program.semantics, uses[(descriptor.descriptor_id, side)])
        payload = bytearray()
        for row in range(descriptor.rows):
            start = begin + row * stride
            end = start + descriptor.row_bytes
            if start < 0 or end > len(content):
                violations += 1
                break
            payload.extend(content[start:end])
        if len(payload) != descriptor.useful_bytes or \
                actual.get(descriptor.descriptor_id) != dual_fnv_digest(payload):
            violations += 1
    return violations


def _phase_precondition_violations(program, result, serving_phases):
    violations = 0
    timings = {}
    for row in result["dma_timings"]:
        timings.setdefault(row["descriptor_id"], []).append(row)
    kv_tensors = {row["tensor"]["tensor_id"] for row in kv_layout_projection(program)["bindings"]}
    views = build_execution_views(program)
    for index, phase in enumerate(serving_phases):
        profile = phase["mesh_profile_id"]
        command_ids = views.for_profile(profile).command_ids
        kv = [descriptor for descriptor in program.dma_descriptors
              if descriptor.command_id in command_ids and
              descriptor.kind == A.DMA_KIND.STORE and
              descriptor.dst.tensor_id in kv_tensors]
        for descriptor in kv:
            matching = timings.get(descriptor.descriptor_id, [])
            if len(matching) != 1:
                violations += 1
                continue
            row = matching[0]
            if not (0 < row["first_aw_tick"] <= row["first_b_tick"] <=
                    row["done_tick"] <= phase["append_terminal_tick"]):
                violations += 1
        if index + 1 < len(serving_phases):
            next_profile = serving_phases[index + 1]["mesh_profile_id"]
            next_ids = views.for_profile(next_profile).command_ids
            ticks = []
            for core in result["instances"][index + 1]["cores"].values():
                ticks.extend(row["issue_tick"] for row in core["observations"]["commands"]
                             if row["issued"] and row["command_id"] in next_ids)
            if ticks and phase["commit_tick"] >= min(ticks):
                violations += 1
    return violations


def _expected_read_identity(events):
    for event in events:
        if event["kind"] == "CQ_ASSIGN":
            return event["request_id"], event["cookie"]
    return None, None


def _backing_violations(result, metrics, events, program, frozen=None):
    span = result.get("output_span")
    violations = 0
    if span and span["digest_before"] and span["digest_after"]:
        violations += 1 if span["digest_before"] == span["digest_after"] else 0
    if frozen is not None and (span is None or
                               span.get("semantic_digest") !=
                               frozen["semantic_digest"] or
                               span.get("bytes") != frozen["output_bytes"]):
        violations += 1
    if metrics.get("generated_code_reads_validated", 0) != 1:
        violations += 1
    if span and metrics.get("generated_code_read_bytes", 0) != span["bytes"]:
        violations += 1
    reads = [event for event in events
             if event["kind"] == "LOCAL_READ" and
             event["control"] == "GENERATED_CODE_READ"]
    if len(reads) != 1:
        return violations + 1
    if not span or span["base"] == 0 or reads[0]["address"] != span["base"] or \
            reads[0]["bytes"] != span["bytes"]:
        violations += 1
    publishes = [row["done_tick"] for row in result["dma_timings"]
                 if row["descriptor_id"] in _published_stores(program)]
    if not publishes or reads[0]["tick"] <= max(publishes):
        violations += 1
    host = "%016x-%016x" % (metrics.get("generated_code_digest_lo", 0),
                            metrics.get("generated_code_digest_hi", 0))
    if host == "0000000000000000-0000000000000000":
        violations += 1
    expected = _expected_host_digest(span, frozen)
    if expected is None or host != expected:
        violations += 1
    request_id, cookie = _expected_read_identity(events)
    if request_id is None or reads[0]["request_id"] != request_id or \
            reads[0]["cookie"] != cookie:
        violations += 1
    violations += _payload_violations(result, span, program, frozen)
    if span and span["digest_after"] and host != span["digest_after"]:
        violations += 1
    return violations


def _deviations(rows, metrics):
    return sum(metrics.get(key, 0) != expected for _, key, expected in rows)


def _argv_path(argv, option):
    if option in argv:
        return Path(argv[argv.index(option) + 1])
    return None


def _planned_output_bytes(program):
    published = _published_stores(program)
    return sum(descriptor.useful_bytes for descriptor in program.dma_descriptors
               if descriptor.descriptor_id in published)


def _join_order_violations(serving_phases):
    violations = 0
    for phase in serving_phases:
        commit = phase["commit_tick"]
        drain = phase["core_drain_tick"]
        append = phase["append_terminal_tick"]
        if commit == 0 or drain == 0 or drain > commit:
            violations += 1
            continue
        if phase["append_tokens"] == 0:
            violations += 1 if append != 0 else 0
        elif append == 0 or append > commit:
            violations += 1
    return violations


def derive_gate6_evidence(program, result, final, metrics, serving_phases,
                          events, case_id, subcase, frozen=None):
    failed = None
    try:
        verify_serving_run(program, result["transport"], metrics,
                           serving_phases)
    except MeshIrError as error:
        if error.code not in CHAIN_CODES + CQ_CODES:
            raise
        failed = error.code
    views = build_execution_views(program).views
    quiescence = (
        (0 if result["terminal"] == "DONE" else 1) +
        (0 if result["error_drained"] == 0 else 1) +
        (1 if result["watchdog_fired"] else 0) +
        (1 if final["fatal"] else 0)
    )
    chain = (
        (0 if _instances_match_views(result, views) else 1) +
        _phase_descriptor_violations(program, serving_phases) +
        _kv_payload_violations(program, result, frozen) +
        (1 if failed in CHAIN_CODES else 0)
    )
    unique_cq = (
        (1 if failed in CQ_CODES else 0) +
        _deviations(LEDGER_METRICS, metrics) +
        _deviations(CQ_COUNTERS, metrics)
    )
    host_compile = (
        _backing_violations(result, metrics, events, program,
                            frozen) +
        _counter_deviations(HOST_COUNTERS, metrics) +
        (0 if _final_matches(metrics, final) else 1)
    )
    checks = [
        _u64_check("quiescence", quiescence, 0),
        _u64_check(
            "completion_timing",
            _serialization_violations(views, result["instances"]) +
            _join_order_violations(serving_phases) +
            _phase_order_violations(serving_phases,
                                    _core_start_tick(events)) +
            _phase_precondition_violations(program, result, serving_phases),
            0),
        _u64_check("serving_phase_chain", chain, 0),
        _u64_check("serving_unique_cq", unique_cq, 0),
        _u64_check("serving_host_compile", host_compile, 0),
    ]
    executed_profiles = {range_.profile_id
                         for range_ in program.profile_stream_ranges}
    expected_rows = [
        {
            "descriptor_id": row.descriptor_id,
            "kind": row.kind,
            "useful_bytes": row.useful_bytes,
            "bursts": row.bursts,
        }
        for row in program.expected_traffic
        if row.profile_id in executed_profiles
    ]
    traffic = build_dma_traffic(case_id, subcase, expected_rows,
                                result["transport"], 1)
    return checks, traffic


def write_gate6_artifacts(program, result_path, facts_path, artifact_dir,
                          case_id, subcase):
    artifact_dir = Path(artifact_dir)
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    facts = read_facts_document(Path(facts_path))
    if facts.fatal is not None:
        raise ValueError("serving facts recorded a fatal record")
    if not facts.serving_phases:
        raise ValueError("serving facts recorded no phase rows")
    manifest = read_json_artifact(
        artifact_dir / ARTIFACT_BASENAMES["RUN_MANIFEST"], artifact_dir,
        "run_manifest_v1.schema.json")
    validate_run_manifest(manifest)
    if manifest["id"] != case_id or manifest["subcase"] != subcase:
        raise ValueError("run manifest identity differs from the case")
    argv = manifest["execution"]["final_argv"]
    runtime_config = _argv_path(argv, "--runtime-config")
    program_path = _argv_path(argv, "--serving-program")
    if runtime_config is None or program_path is None:
        raise ValueError("serving manifest has no frozen input paths")
    import yaml
    from mesh_ir.agent_workload import load_workload_plan

    document = yaml.safe_load(runtime_config.read_text("utf-8"))
    workload_plan = runtime_config.parent / document["agent"]["workload_plan"]
    declared = manifest["scenario"]
    if hashlib.sha256(program_path.read_bytes()).hexdigest() != \
            declared["mesh_programs"][0]["file_sha256"]:
        raise ValueError("serving program differs from the manifest")
    if load_workload_plan(workload_plan).digest != declared["digests"]["workload_plan"]:
        raise ValueError("workload plan differs from the manifest")
    frozen = frozen_expectation(
        workload_plan, program, program.agent_request_profiles[0].profile_id,
        _planned_output_bytes(program))
    checks, traffic = derive_gate6_evidence(
        program, result, facts.final, facts.metrics, facts.serving_phases,
        facts.events, case_id, subcase, frozen)
    failures = [check["name"] for check in checks
                if check["status"] == "FAIL"]
    names = [check["name"] for check in checks]
    if len(set(names)) != len(names):
        raise ValueError("serving invariant registry has duplicate names")
    invariants = {
        "schema": "ai_mesh_invariants_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "status": "FAIL" if failures else "PASS",
        "registry_digest": canonical_digest(names),
        "checks": checks,
        "first_failure": failures[0] if failures else None,
    }
    validate_schema("invariants_v1.schema.json", invariants)
    atomic_write_json(artifact_dir / ARTIFACT_BASENAMES["INVARIANTS_JSON"],
                      invariants)
    validate_schema("traffic_v1.schema.json", traffic)
    validate_traffic(traffic, case_id, subcase)
    atomic_write_json(artifact_dir / ARTIFACT_BASENAMES["TRAFFIC_JSON"],
                      traffic)
    quiescent = (invariants["status"] == "PASS" and
                 result["terminal"] == "DONE" and
                 not result["watchdog_fired"])
    child_report = {
        "schema": "ai_mesh_child_scenario_report_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "run_exit_reason": (
            "QUIESCENT_SUCCESS" if quiescent else "INFRA_FATAL"),
        "first_fatal": None,
        "watchdog_fired": bool(result["watchdog_fired"]),
        "ledger_summary": {field: facts.metrics.get(key, 0)
                           for field, key, _ in LEDGER_METRICS},
        "global_quiescence": quiescent,
        "run_manifest_digest": canonical_digest(manifest),
    }
    validate_schema("child_scenario_report_v1.schema.json", child_report)
    atomic_write_json(artifact_dir / "child_report.json", child_report)
    return {"invariants": invariants, "traffic": traffic,
            "child_report": child_report}

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "configs" /
                   "example" / "ai_mesh"))

from gate6_acceptance import (  # noqa: E402
    derive_gate6_evidence,
    write_gate6_artifacts,
)
from mesh_ir.abi.decoder import decode_program  # noqa: E402
from mesh_ir.acceptance import canonical_digest  # noqa: E402
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.generated import abi as A
from mesh_ir.gate3_oracle import SERVING_PHASE_FIELDS
from mesh_ir.gate3_oracle import read_facts_document  # noqa: E402
from mesh_ir.serving_output_bytes import dual_fnv_digest, surrogate_output_bytes
from mesh_ir.serving_contract_digest import frozen_expectation

FIXTURE = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures" / \
    "gate6"
EVIDENCE = FIXTURE / "run_evidence"
CASE_ID = "E2E-D"
SUBCASE = "default"
DIGEST_FIELDS = (
    "configuration",
    "base_architecture",
    "effective_architecture",
    "program_weight_registry",
    "model_weight_image",
    "workload_plan",
    "control_plan",
    "command_identity",
    "host_arena_object_plan",
    "capacity_plan",
    "endpoint_map",
    "host_task_identity",
)


def write_run_manifest(artifact_dir):
    child_environment = sorted(
        (
            {"name": "LC_ALL", "value": "C"},
            {"name": "TZ", "value": "UTC"},
            {"name": "PYTHONHASHSEED", "value": "0"},
            {"name": "OMP_NUM_THREADS", "value": "1"},
            {"name": "AI_MESH_CASE_ID", "value": CASE_ID},
            {"name": "AI_MESH_SUBCASE", "value": SUBCASE},
            {"name": "AI_MESH_ARTIFACT_DIR", "value": str(artifact_dir)},
            {"name": "AI_MESH_CHILD_REPORT",
             "value": str(artifact_dir / "child_report.json")},
        ),
        key=lambda row: row["name"].encode("utf-8"),
    )
    final_argv = ["/tmp/gem5.opt", "--runtime-config",
                  str(FIXTURE / "serving_runtime_config.yaml"),
                  "--serving-program",
                  str(FIXTURE / "serving_two_tokens.mshb")]
    manifest = {
        "schema": "ai_mesh_run_manifest_v1",
        "version": 1,
        "id": CASE_ID,
        "subcase": SUBCASE,
        "manifest_digest": "d" * 64,
        "git": {"base_sha": "e" * 40, "dirty": False},
        "execution": {
            "runner": "GEM5",
            "normalized_execution_digest": canonical_digest({
                "runner": "GEM5",
                "resolved_executable": final_argv[0],
                "final_argv": final_argv,
                "child_environment": child_environment,
                "inherited_environment_digest": canonical_digest([]),
            }),
            "final_argv": final_argv,
            "child_environment": child_environment,
            "absolute_output_dir": str(artifact_dir),
        },
        "provenance": {
            "resolved_executable_sha256": "a" * 64,
            "config_or_test_registry_sha256": "b" * 64,
            "harness_sha256": "c" * 64,
            "build_mode": "OPT",
            "inherited_environment": [],
        },
        "scenario": {
            "kind": "GEM5",
            "master_seed": 1,
            "data_mode": "FUNCTIONAL_BYTES",
            "strict_replay_serial_batches": False,
            "digests": {name: "f" * 64 for name in DIGEST_FIELDS},
            "mesh_programs": [{
                "program_id": 1,
                "semantic_digest": "1" * 64,
                "file_sha256": "2" * 64,
            }],
            "provider_profiles": [],
            "identity_counters": None,
            "physical_source_counters": [],
            "tick_projection": {
                "host_clock_period_ticks": 1000,
                "npu_clock_period_ticks": 1000,
                "core_clock_period_ticks": 1000,
                "host_tasks": [],
            },
            "endpoint_map": None,
            "host_arena_object_plan": None,
            "capacity_plan": None,
            "host_task_identity_plan": None,
            "approximation": {
                "reference_compute": False,
                "numeric_compute": False,
                "cpu_instruction_simulation": False,
                "cpu_mesh_simulation": False,
                "ucie_protocol_simulation": False,
                "remote_link_is_analytic_proxy": True,
                "synthetic_weight_bytes": True,
                "synthetic_output_bytes": True,
            },
        },
    }
    manifest["scenario"]["digests"]["workload_plan"] = load_workload_plan(
        FIXTURE / "serving_workload_plan.json").digest
    manifest["scenario"]["mesh_programs"][0]["file_sha256"] = hashlib.sha256(
        (FIXTURE / "serving_two_tokens.mshb").read_bytes()).hexdigest()
    (artifact_dir / "run_manifest.json").write_text(json.dumps(manifest),
                                                    encoding="utf-8")
    return canonical_digest(manifest)


def program():
    return decode_program((FIXTURE / "serving_two_tokens.mshb").read_bytes())


def evidence():
    result = json.loads((EVIDENCE / "mesh_result.json").read_text("utf-8"))
    facts = read_facts_document(EVIDENCE / "gate6_facts.tsv")
    assert facts.fatal is None
    return result, facts


def derive(result=None, metrics=None, phases=None):
    stored = evidence()
    facts = stored[1]
    decoded = program()
    profile = decoded.agent_request_profiles[0]
    return derive_gate6_evidence(
        decoded,
        stored[0] if result is None else result,
        facts.final,
        facts.metrics if metrics is None else metrics,
        facts.serving_phases if phases is None else phases,
        facts.events,
        CASE_ID,
        SUBCASE,
        frozen_expectation(FIXTURE / "serving_workload_plan.json", decoded,
                           profile.profile_id, profile.host_output_bytes),
    )


def status_of(checks, name):
    return next(check for check in checks if check["name"] == name)["status"]


def test_real_run_evidence_passes_every_check():
    checks, traffic = derive()
    assert [check["name"] for check in checks] == [
        "quiescence",
        "completion_timing",
        "serving_phase_chain",
        "serving_unique_cq",
        "serving_host_compile",
    ]
    assert [check["status"] for check in checks] == ["PASS"] * len(checks)
    assert [check["observed"] for check in checks] == [
        {"kind": "U64", "value": 0}] * len(checks)
    assert traffic["status"] == "PASS"
    assert traffic["oracle_digest"] == traffic["actual_digest"]
    assert [row["traffic_class"] for row in traffic["classes"]] == \
        ["NPU_LOCAL_MEMORY"]
    assert traffic["unattributed_bytes"] == 0


def test_committed_and_expected_bytes_agree_per_ownership_key():
    _, traffic = derive()
    assert len(traffic["ownership"]) == 15
    for row in traffic["ownership"]:
        assert row["expected_bytes"] == row["actual_bytes"]
        assert row["expected_bytes"] > 0


def test_mutated_descriptor_bytes_fail_the_closure_check():
    result, _ = evidence()
    mutated = copy.deepcopy(result)
    mutated["transport"][0]["read_bytes"] += 32
    checks, traffic = derive(result=mutated)
    assert status_of(checks, "serving_phase_chain") == "FAIL"
    assert status_of(checks, "serving_unique_cq") == "PASS"
    assert traffic["status"] == "FAIL"


def test_missing_descriptor_row_fails_the_closure_check():
    result, _ = evidence()
    mutated = copy.deepcopy(result)
    mutated["transport"] = mutated["transport"][:-1]
    checks, _ = derive(result=mutated)
    assert status_of(checks, "serving_phase_chain") == "FAIL"


def test_ledger_drift_fails_the_unique_cq_check():
    result, _ = evidence()
    metrics = dict(evidence()[1].metrics)
    metrics["fatal_cq_obligations"] = 1
    checks, _ = derive(result=result, metrics=metrics)
    assert status_of(checks, "serving_unique_cq") == "FAIL"
    assert status_of(checks, "serving_host_compile") == "PASS"


def test_cq_counter_drift_breaks_the_final_metric_cross_check():
    result, _ = evidence()
    metrics = dict(evidence()[1].metrics)
    metrics["cq_assignments"] = 2
    checks, _ = derive(result=result, metrics=metrics)
    assert status_of(checks, "serving_unique_cq") == "FAIL"
    assert status_of(checks, "serving_host_compile") == "FAIL"


def test_fatal_record_fails_the_ledger_checks():
    result, _ = evidence()
    metrics = dict(evidence()[1].metrics)
    metrics["fatal_records"] = 1
    checks, _ = derive(result=result, metrics=metrics)
    assert status_of(checks, "serving_unique_cq") == "FAIL"
    assert status_of(checks, "quiescence") == "PASS"


def test_phase_token_accounting_drift_fails_the_chain_check():
    result, facts = evidence()
    phases = [dict(row) for row in facts.serving_phases]
    phases[1]["kv_tokens_before"] = 9
    checks, _ = derive(result=result, phases=phases)
    assert status_of(checks, "serving_phase_chain") == "FAIL"
    assert status_of(checks, "serving_unique_cq") == "PASS"


def test_phase_commit_after_drain_is_required_by_completion_timing():
    result, facts = evidence()
    phases = [dict(row) for row in facts.serving_phases]
    phases[0]["commit_tick"] = phases[0]["core_drain_tick"] - 1
    checks, _ = derive(result=result, phases=phases)
    assert status_of(checks, "completion_timing") == "FAIL"
    assert status_of(checks, "serving_phase_chain") == "PASS"


def test_missing_append_terminal_tick_fails_completion_timing():
    result, facts = evidence()
    phases = [dict(row) for row in facts.serving_phases]
    phases[1]["append_terminal_tick"] = 0
    checks, _ = derive(result=result, phases=phases)
    assert status_of(checks, "completion_timing") == "FAIL"


def test_a_backing_that_never_changed_fails_the_host_compile_check():
    result, facts = evidence()
    mutated = copy.deepcopy(result)
    if not mutated["output_span"]["digest_before"]:
        mutated["output_span"]["digest_before"] = "0000000000000001-0000000000000002"
        mutated["output_span"]["digest_after"] = \
            mutated["output_span"]["digest_before"]
    else:
        mutated["output_span"]["digest_after"] = \
            mutated["output_span"]["digest_before"]
    checks, _ = derive(result=mutated)
    assert status_of(checks, "serving_host_compile") == "FAIL"


def test_a_host_read_of_other_bytes_fails_the_host_compile_check():
    result, _ = evidence()
    metrics = dict(evidence()[1].metrics)
    metrics["generated_code_digest_lo"] = 0
    checks, _ = derive(result=result, metrics=metrics)
    assert status_of(checks, "serving_host_compile") == "FAIL"
    metrics = dict(evidence()[1].metrics)
    metrics["generated_code_reads_validated"] = 0
    checks, _ = derive(result=result, metrics=metrics)
    assert status_of(checks, "serving_host_compile") == "FAIL"


def test_the_real_run_host_read_matches_the_contract_output_bytes():
    from mesh_ir.serving_output_bytes import (
        dual_fnv_digest,
        surrogate_output_bytes,
    )

    result, facts = evidence()
    span = result["output_span"]
    assert span["bytes"] == facts.metrics["generated_code_read_bytes"]
    assert span["semantic_digest"]
    expected = dual_fnv_digest(
        surrogate_output_bytes(bytes.fromhex(span["semantic_digest"]),
                               span["bytes"]))
    observed = "%016x-%016x" % (facts.metrics["generated_code_digest_lo"],
                                facts.metrics["generated_code_digest_hi"])
    assert observed == expected


def test_interleaved_phase_windows_fail_the_serialization_check():
    result, _ = evidence()
    mutated = copy.deepcopy(result)
    issue = mutated["cores"][0]["command_issue_ticks"]
    issue["1"], issue["9"] = issue["9"], issue["1"]
    checks, _ = derive(result=mutated)
    assert status_of(checks, "completion_timing") == "FAIL"
    assert status_of(checks, "serving_phase_chain") == "PASS"


def test_instance_completion_drift_fails_the_range_coverage_check():
    result, _ = evidence()
    mutated = copy.deepcopy(result)
    mutated["instances"][0]["cores"]["0"]["completed"] = 7
    checks, _ = derive(result=mutated)
    assert status_of(checks, "serving_phase_chain") == "FAIL"


def test_artifacts_are_written_from_the_real_evidence(tmp_path):
    digest = write_run_manifest(tmp_path)
    written = write_gate6_artifacts(
        program(),
        EVIDENCE / "mesh_result.json",
        EVIDENCE / "gate6_facts.tsv",
        tmp_path,
        CASE_ID,
        SUBCASE,
    )
    for name in ("invariants.json", "traffic.json", "child_report.json"):
        assert (tmp_path / name).is_file()
    assert written["invariants"]["status"] == "PASS"
    assert written["invariants"]["first_failure"] is None
    assert written["child_report"]["run_exit_reason"] == "QUIESCENT_SUCCESS"
    assert written["child_report"]["global_quiescence"] is True
    assert written["child_report"]["run_manifest_digest"] == digest
    metrics = evidence()[1].metrics
    assert written["child_report"]["ledger_summary"] == {
        name: metrics.get(key, 0) for name, key in (
            ("live_cq_obligations", "live_cq_obligations"),
            ("fatal_cq_obligations", "fatal_cq_obligations"),
            ("fatal_sq_intakes", "fatal_sq_intakes"),
            ("fatal_publications", "fatal_publications"),
            ("ambiguous_publications", "ambiguous_publications"),
            ("host_ack_wait_b", "ack_wait_b"),
            ("host_ack_b_error", "host_ack_b_error"),
            ("msi_rob_entries", "msi_rob_entries"),
            ("fatal_records", "fatal_records"),
        )}
    assert written["traffic"]["status"] == "PASS"


def test_artifacts_report_failure_when_evidence_is_tampered(tmp_path):
    write_run_manifest(tmp_path)
    result = json.loads((EVIDENCE / "mesh_result.json").read_text("utf-8"))
    result["transport"][0]["read_bytes"] += 8
    tampered = tmp_path / "mesh_result.json"
    tampered.write_text(json.dumps(result), encoding="utf-8")
    written = write_gate6_artifacts(
        program(),
        tampered,
        EVIDENCE / "gate6_facts.tsv",
        tmp_path,
        CASE_ID,
        SUBCASE,
    )
    assert written["invariants"]["status"] == "FAIL"
    assert written["invariants"]["first_failure"] == "serving_phase_chain"
    assert written["child_report"]["run_exit_reason"] == "INFRA_FATAL"
    assert written["child_report"]["global_quiescence"] is False


@pytest.mark.parametrize("mutation", [
    "descriptor_counts_99", "future_commit", "missing_agent_live_objects",
    "wrong_output_base", "invented_matching_output_digest",
    "append_terminal_before_instance", "missing_generated_code_read_fact",
    "wrong_publish_payload_digest", "wrong_host_read_request",
    "decode_append_before_its_accept", "commit_after_next_phase_start",
    "forged_semantic_and_all_output_evidence", "append_accepted_without_b",
    "append_before_last_b", "missing_kv_terminal", "wrong_kv_payload",
])
def test_runner_rejects_review_counterexamples(tmp_path, mutation):
    write_run_manifest(tmp_path)
    baseline = write_gate6_artifacts(
        program(), EVIDENCE / "mesh_result.json", EVIDENCE / "gate6_facts.tsv",
        tmp_path, CASE_ID, SUBCASE)
    assert baseline["invariants"]["status"] == "PASS"
    result, facts = evidence()
    rows = [line.split("|") for line in
            (EVIDENCE / "gate6_facts.tsv").read_text().splitlines()]
    phases = [row for row in rows if row[0] == "SERVING_PHASE"]
    fields = {name: index + 1
              for index, name in enumerate(SERVING_PHASE_FIELDS)}
    read = next(row for row in rows
                if row[0] == "EVENT" and row[13] == "GENERATED_CODE_READ")
    metrics = {row[1]: row for row in rows if row[0] == "METRIC"}
    decoded = program()
    publish_range = decoded.profile_stream_ranges[-1]
    publish_commands = {command.command_id for command in decoded.commands[
        publish_range.command_begin:
        publish_range.command_begin + publish_range.command_count]}
    stores = sorted((descriptor for descriptor in decoded.dma_descriptors
                     if descriptor.command_id in publish_commands and
                     descriptor.kind == A.DMA_KIND.STORE),
                    key=lambda descriptor: descriptor.dst.offset_bytes)
    payloads = {row["descriptor_id"]: row for row in result["transport"]}
    if mutation == "descriptor_counts_99":
        phases[0][fields["accepted_descriptors"]] = "99"
        phases[0][fields["terminal_descriptors"]] = "99"
    elif mutation == "future_commit":
        phases[0][fields["commit_tick"]] = str(
            facts.serving_phases[-1]["commit_tick"] + 1)
    elif mutation == "missing_agent_live_objects":
        rows.remove(metrics["agent_live_objects"])
    elif mutation == "wrong_output_base":
        result["output_span"]["base"] = 0
    elif mutation == "invented_matching_output_digest":
        result["output_span"]["digest_after"] = \
            "0000000000000001-0000000000000002"
        metrics["generated_code_digest_lo"][2] = "1"
        metrics["generated_code_digest_hi"][2] = "2"
    elif mutation == "append_terminal_before_instance":
        phases[0][fields["append_terminal_tick"]] = "1"
    elif mutation == "missing_generated_code_read_fact":
        rows.remove(read)
    elif mutation == "wrong_publish_payload_digest":
        payloads[stores[0].descriptor_id]["payload_digest"] = \
            "b4417644988ceb25-1ad43fc0a9090c31"
    elif mutation == "wrong_host_read_request":
        read[6] = read[7] = "999"
    elif mutation == "decode_append_before_its_accept":
        phases[1][fields["append_terminal_tick"]] = str(
            facts.serving_phases[0]["commit_tick"] + 1)
    elif mutation == "commit_after_next_phase_start":
        next_range = decoded.profile_stream_ranges[1]
        command = decoded.commands[next_range.command_begin].command_id
        phases[0][fields["commit_tick"]] = str(
            result["cores"][0]["command_issue_ticks"][str(command)] + 1)
    elif mutation == "forged_semantic_and_all_output_evidence":
        seed = b"\x11" * 32
        content = surrogate_output_bytes(seed, result["output_span"]["bytes"])
        digest = dual_fnv_digest(content)
        result["output_span"]["semantic_digest"] = seed.hex()
        result["output_span"]["digest_after"] = digest
        lo, hi = digest.split("-")
        metrics["generated_code_digest_lo"][2] = str(int(lo, 16))
        metrics["generated_code_digest_hi"][2] = str(int(hi, 16))
        read[20] = digest
        for descriptor in decoded.dma_descriptors:
            if descriptor.command_id in publish_commands and \
                    descriptor.kind == A.DMA_KIND.LOCAL_FILL:
                payloads[descriptor.descriptor_id]["payload_digest"] = digest
        for descriptor in stores:
            start = descriptor.dst.offset_bytes - stores[0].dst.offset_bytes
            payloads[descriptor.descriptor_id]["payload_digest"] = \
                dual_fnv_digest(content[start:start + descriptor.useful_bytes])
    else:
        kv_symbol = decoded.agent_request_profiles[0].primary_kv_symbol_id
        kv_tensor = next(relocation.tensor_id for relocation in
                         decoded.relocations if relocation.symbol_sid == kv_symbol)
        first_range = decoded.profile_stream_ranges[0]
        commands = {command.command_id for command in decoded.commands[
            first_range.command_begin:
            first_range.command_begin + first_range.command_count]}
        descriptor = next(descriptor for descriptor in decoded.dma_descriptors
                          if descriptor.command_id in commands and
                          descriptor.kind == A.DMA_KIND.STORE and
                          descriptor.dst.tensor_id == kv_tensor)
        timing = next(row for row in result["dma_timings"]
                      if row["descriptor_id"] == descriptor.descriptor_id)
        assert 0 < timing["first_aw_tick"] < timing["first_b_tick"]
        if mutation == "wrong_kv_payload":
            payloads[descriptor.descriptor_id]["payload_digest"] = \
                "0000000000000001-0000000000000002"
        elif mutation == "append_accepted_without_b":
            timing["first_b_tick"] = timing["done_tick"] = 0
        elif mutation == "missing_kv_terminal":
            result["dma_timings"].remove(timing)
        else:
            timing["done_tick"] = facts.serving_phases[0]["commit_tick"] + 1
    result_path = tmp_path / "mesh_result.json"
    facts_path = tmp_path / "gate6_facts.tsv"
    result_path.write_text(json.dumps(result))
    facts_path.write_text("\n".join("|".join(row) for row in rows) + "\n")
    written = write_gate6_artifacts(
        decoded, result_path, facts_path, tmp_path, CASE_ID, SUBCASE)
    assert written["invariants"]["status"] == "FAIL", mutation
    assert written["child_report"]["run_exit_reason"] == "INFRA_FATAL"


@pytest.mark.parametrize("input_name", ["program", "workload", "missing_paths"])
def test_runner_requires_manifest_bound_frozen_inputs(tmp_path, input_name):
    write_run_manifest(tmp_path)
    baseline = write_gate6_artifacts(
        program(), EVIDENCE / "mesh_result.json", EVIDENCE / "gate6_facts.tsv",
        tmp_path, CASE_ID, SUBCASE)
    assert baseline["invariants"]["status"] == "PASS"
    path = tmp_path / "run_manifest.json"
    manifest = json.loads(path.read_text())
    if input_name == "program":
        manifest["scenario"]["mesh_programs"][0]["file_sha256"] = "f" * 64
    elif input_name == "workload":
        manifest["scenario"]["digests"]["workload_plan"] = "f" * 64
    else:
        manifest["execution"]["final_argv"] = ["/tmp/gem5.opt"]
        execution = manifest["execution"]
        execution["normalized_execution_digest"] = canonical_digest({
            "runner": "GEM5", "resolved_executable": "/tmp/gem5.opt",
            "final_argv": execution["final_argv"],
            "child_environment": execution["child_environment"],
            "inherited_environment_digest": canonical_digest([]),
        })
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        write_gate6_artifacts(
            program(), EVIDENCE / "mesh_result.json", EVIDENCE / "gate6_facts.tsv",
            tmp_path, CASE_ID, SUBCASE)

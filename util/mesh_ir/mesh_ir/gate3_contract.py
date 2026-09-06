from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from mesh_ir.acceptance import GATE_OF
from mesh_ir.gate3_oracle import CHECKS


@dataclass(frozen=True)
class SubcaseRequirement:
    case_id: str
    name: str
    evidence: tuple[str, ...]
    terminal_class: str = "QUIESCENT_SUCCESS"
    runner: str = "GEM5"
    target: str | None = None

    @property
    def backend_case(self):
        number = self.case_id.removeprefix("PROTO-")
        return f"proto_{number}_{self.name}"

    @property
    def invariants(self):
        if self.runner != "GEM5":
            return ("unit_test_passed",)
        return (*CHECKS, *self.evidence)


def _s(case_id, name, *evidence, terminal="QUIESCENT_SUCCESS"):
    return SubcaseRequirement(case_id, name, tuple(evidence), terminal)


def _py(case_id, name, target):
    return SubcaseRequirement(case_id, name, (), runner="PYTEST", target=target)


def _cc(case_id, name, target):
    return SubcaseRequirement(case_id, name, (), runner="GTEST", target=target)


GATE3_CASES = {
    "PROTO-1": (
        _s("PROTO-1", "depth1_wrap", "sq_depth1_wrap", "cq_depth1_wrap"),
        _s("PROTO-1", "depth2_wrap", "sq_depth2_wrap", "cq_depth2_wrap"),
    ),
    "PROTO-2": (
        _s("PROTO-2", "sq_full", "sq_backpressure", "sq_no_overwrite"),
        _s("PROTO-2", "cq_full", "cq_obligation_retained", "context_retained"),
    ),
    "PROTO-3": (
        _s("PROTO-3", "stale_doorbell", "doorbell_stale_noop", "single_execution"),
        _s("PROTO-3", "duplicate_doorbell", "doorbell_duplicate_noop", "single_execution"),
        _s("PROTO-3", "empty_doorbell", "doorbell_empty_noop", "zero_execution"),
    ),
    "PROTO-4": (
        _s("PROTO-4", "sq_read_error", "sq_head_unchanged", "zero_cq", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-4", "sq_sequence_mismatch", "sq_head_unchanged", "zero_cq", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-4", "sq_crc", "seq_only_error_cq", "sq_head_advanced_once"),
        _s("PROTO-4", "parameter_read_error", "minimal_error_cq", "trusted_sq_identity"),
        _s("PROTO-4", "parameter_crc", "minimal_error_cq", "trusted_sq_identity"),
        _s("PROTO-4", "parameter_bounds", "minimal_error_cq", "trusted_sq_identity"),
        _s("PROTO-4", "parameter_binding_oob", "parameter_binding_detail", "zero_execution"),
    ),
    "PROTO-5": (
        _s("PROTO-5", "prompt_not_visible", "doorbell_held_before_prompt_visibility"),
        _s("PROTO-5", "parameter_not_visible", "doorbell_held_before_parameter_visibility"),
        _s("PROTO-5", "sq_not_visible", "doorbell_held_before_sq_visibility"),
        _s("PROTO-5", "release_fence_pending", "doorbell_held_before_release_fence"),
        _s("PROTO-5", "producer_commit", "producer_held_before_doorbell_b"),
    ),
    "PROTO-6": (
        _s("PROTO-6", "early_normal_cq", "early_suffix_cached", "early_suffix_consumed_once"),
        _s("PROTO-6", "early_seq_only_cq", "early_seq_only_cached", "early_suffix_consumed_once"),
        _s("PROTO-6", "early_cumulative_head", "committed_prefix_split", "pending_suffix_split"),
        _s("PROTO-6", "proven_no_effect_b_error", "publication_rollback", "absolute_slot_reused", "request_id_not_reused"),
        _s("PROTO-6", "same_edge_acceptance_conflict", "acceptance_evidence_priority", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-6", "ambiguous_b", "ambiguous_publication_retained", "fatal_intake_conservation", "post_cut_sq_r_retained", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-6", "pre_ar_intake_fatal", "ambiguous_publication_retained", "fatal_intake_conservation", "pre_ar_intake_retained", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-6", "normal_sq_at_fatal_cut", "fatal_intake_conservation", "same_tick_normal_sq_discarded", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-6", "sq_error_at_fatal_cut", "fatal_intake_conservation", "same_tick_sq_error_collected", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-6", "driver_commit_at_fatal_cut", "fatal_intake_conservation", "same_tick_driver_commit_discarded", terminal="EXPECTED_INFRA_FATAL"),
    ),
    "PROTO-7": (
        _s("PROTO-7", "delayed_prompt_r", "core_held_until_prompt_r"),
    ),
    "PROTO-8": (
        _s("PROTO-8", "delayed_output_b", "cq_held_until_output_b"),
    ),
    "PROTO-9": (
        _s("PROTO-9", "delayed_cq_b", "msi_held_until_cq_b", "irq_held_until_cq_b"),
    ),
    "PROTO-10": (
        _s(
            "PROTO-10", "stale_cq_seq", "stale_cq_rejected",
            terminal="EXPECTED_INFRA_FATAL",
        ),
        _s(
            "PROTO-10", "request_mismatch", "cq_request_rejected",
            terminal="EXPECTED_INFRA_FATAL",
        ),
        _s(
            "PROTO-10", "cookie_mismatch", "cq_cookie_rejected",
            terminal="EXPECTED_INFRA_FATAL",
        ),
    ),
    "PROTO-11": (
        _s("PROTO-11", "ack_before_target_commit", "cq_slot_held_until_ack_commit"),
    ),
    "PROTO-13": (
        _s("PROTO-13", "bulk_saturation", "control_forward_progress", "bounded_control_latency"),
    ),
    "PROTO-14": (
        _s("PROTO-14", "directional_traffic", "exact_directional_bytes", "exact_directional_packets"),
    ),
    "PROTO-15": (
        _s("PROTO-15", "doorbell_sequence", "doorbell_sequence_window", "doorbell_full_wstrb"),
        _s("PROTO-15", "sq_head_sequence", "sq_head_sequence_window", "sq_head_full_wstrb"),
        _s("PROTO-15", "cq_tail_sequence", "cq_tail_sequence_window", "cq_tail_full_wstrb"),
        _s("PROTO-15", "cq_ack_sequence", "cq_ack_sequence_window", "cq_ack_full_wstrb"),
        _s("PROTO-15", "msi_sequence", "msi_contiguous_retirement", "msi_full_wstrb"),
        _s("PROTO-15", "bad_response_order", "fixed_control_ids", "one_control_inflight", "bad_b_blocks_later_update"),
    ),
    "PROTO-16": (
        _s("PROTO-16", "sq_crc_identity", "seq_only_error_identity"),
        _s("PROTO-16", "parameter_error_identity", "trusted_parameter_error_identity"),
    ),
    "PROTO-17": (
        _py("PROTO-17", "py_crc32c", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_crc32c_reference_vector"),
        _py("PROTO-17", "py_sq_round_trip", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_sq_round_trip_with_crc"),
        _py("PROTO-17", "py_sq_crc", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_sq_crc_corruption_rejected"),
        _py("PROTO-17", "py_cq_round_trip", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_cq_round_trip"),
        _py("PROTO-17", "py_parameter_round_trip", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_parameter_round_trip"),
        _py("PROTO-17", "py_binding_round_trip", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_binding_round_trip"),
        _py("PROTO-17", "py_tlv", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_tlv_encode_decode_and_ordering"),
        _py("PROTO-17", "py_metadata", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_metadata_round_trip_with_tlv_tail"),
        _py("PROTO-17", "py_ring_helpers", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_ring_helpers"),
        _py("PROTO-17", "py_detail_codes", "util/mesh_ir/tests/unit/test_agent_protocol.py::test_detail_code_registry_closed"),
        _cc("PROTO-17", "cpp_crc32c", "AgentCrc32cTest.ReferenceVector"),
        _cc("PROTO-17", "cpp_sq", "AgentRecordGoldenTest.SqDecodesAndCrcVerifies"),
        _cc("PROTO-17", "cpp_cq", "AgentRecordGoldenTest.CqDecodes"),
        _cc("PROTO-17", "cpp_metadata", "AgentRecordGoldenTest.MetadataCrcVerifies"),
    ),
    "PROTO-18": (
        _s("PROTO-18", "delayed_metadata_b", "cq_held_until_metadata_b"),
    ),
    "PROTO-19": (
        _s("PROTO-19", "output_capacity", "capacity_error_before_core", "single_error_cq"),
        _s("PROTO-19", "metadata_capacity", "capacity_error_before_core", "single_error_cq"),
    ),
    "PROTO-20": (
        _s("PROTO-20", "cq_not_acked", "cq_obligation_retained", "context_retained", "cq_slot_not_reused", "accepted_conservation"),
    ),
    "PROTO-24": (
        _s("PROTO-24", "qos_out_of_order", "qos_terminal_arbitration", "continuous_cq_assignment", "ordered_driver_consume"),
        _s("PROTO-24", "same_tick_seq_only", "same_tick_terminal_arbitration", "continuous_cq_assignment"),
    ),
    "PROTO-26": (
        _s("PROTO-26", "cq_entry_b_error", "completion_fatal_no_duplicate", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-26", "sq_head_b_error", "completion_fatal_no_duplicate", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-26", "cq_tail_b_error", "completion_fatal_no_duplicate", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-26", "msi_b_error", "completion_fatal_no_duplicate", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-26", "ack_no_target_commit", "ack_slot_retained", "ack_obligation_retained", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-26", "ack_after_target_commit", "ack_history_retained", "slot_not_resurrected", terminal="EXPECTED_INFRA_FATAL"),
    ),
    "PROTO-28": (
        _s("PROTO-28", "local_store_visibility", "zero_doorbell_before_local_visibility"),
        _s("PROTO-28", "release_fence", "zero_doorbell_before_release_fence"),
        _s("PROTO-28", "local_store_no_fabric", "zero_local_store_axi", "zero_local_store_garnet"),
    ),
    "PROTO-29": (
        _s("PROTO-29", "ack_commit_before_b", "slot_reuse_after_ack_target_commit", "ack_response_ledger_independent"),
        _s("PROTO-29", "ack_no_target_commit_error", "ack_obligation_retained", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-29", "ack_postcommit_error", "ack_history_retained", "slot_not_rolled_back", terminal="EXPECTED_INFRA_FATAL"),
    ),
    "PROTO-30": (
        _s("PROTO-30", "control_windows", "all_control_sequence_windows"),
        _s("PROTO-30", "early_ack", "early_ack_held_until_msi_b", "early_ack_committed_once"),
        _s("PROTO-30", "msi_reverse_b", "msi_contiguous_ok_prefix"),
        _s("PROTO-30", "msi_error_hole", "msi_error_holds_prefix", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-30", "same_edge_callbacks", "same_edge_deterministic_prefix"),
        _s("PROTO-30", "msi_callback_hole", "callback_hole_holds_prefix"),
        _s("PROTO-30", "msi_callback_duplicate", "duplicate_callback_idempotent"),
        _s("PROTO-30", "same_tick_completion_faults", "same_tick_fatal_reduction", terminal="EXPECTED_INFRA_FATAL"),
        _s("PROTO-30", "ack_beyond_issued", "future_ack_rejected"),
        _s("PROTO-30", "topology", "single_npu_fabric", "zero_cpu_core", "zero_cpu_garnet", "zero_ucie", "shaper_is_driver_helper"),
    ),
}

GATE3_IDS = tuple(case_id for case_id, gate in GATE_OF.items() if gate == 3)


def _load_registry(path):
    spec = importlib.util.spec_from_file_location("gate3_runtime_registry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gate3_readiness_failures(manifest, registry_path: Path):
    failures = []
    if tuple(GATE3_CASES) != GATE3_IDS:
        failures.append("Gate 3 contract ID order differs from the acceptance gate map")
    cases = {row["id"]: row for row in manifest.get("cases", [])}
    registry = _load_registry(registry_path)
    for case_id, requirements in GATE3_CASES.items():
        case = cases.get(case_id)
        if case is None:
            failures.append(f"{case_id}: missing manifest case")
            continue
        expected_names = [row.name for row in requirements]
        actual_names = [row["name"] for row in case["subcases"]]
        if actual_names != expected_names:
            failures.append(
                f"{case_id}: subcases mismatch: expected {expected_names}, got {actual_names}"
            )
            continue
        for subcase, requirement in zip(case["subcases"], requirements):
            label = f"{case_id}/{requirement.name}"
            if subcase["terminal_class"] != requirement.terminal_class:
                failures.append(f"{label}: terminal class mismatch")
            execution = subcase["execution"]
            if execution["runner"] != requirement.runner:
                failures.append(f"{label}: runner mismatch")
                continue
            if requirement.runner == "GEM5":
                if execution["case_name"] != requirement.backend_case:
                    failures.append(f"{label}: backend case mismatch")
                    continue
                if requirement.backend_case not in registry.CASES:
                    failures.append(f"{label}: backend case is not registered")
                    continue
                actual = tuple(registry.invariant_registry(
                    requirement.backend_case, execution["args"]
                ))
                if actual != requirement.invariants:
                    failures.append(f"{label}: invariant registry mismatch")
            elif requirement.runner == "PYTEST":
                if execution["node_id"] != requirement.target:
                    failures.append(f"{label}: pytest target mismatch")
            elif execution["gtest_filter"] != requirement.target:
                failures.append(f"{label}: gtest target mismatch")
    return tuple(failures)

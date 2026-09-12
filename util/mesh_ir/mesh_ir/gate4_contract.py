from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from mesh_ir.acceptance import CUMULATIVE, GATE_OF
from mesh_ir.gate4_oracle import ORACLE_CHECKS


@dataclass(frozen=True)
class Gate4Subcase:
    case_id: str
    name: str
    evidence: tuple[str, ...] = ()
    terminal_class: str = "QUIESCENT_SUCCESS"
    runner: str = "GEM5"
    target: str | None = None
    scenario: str | None = None
    control: str | None = None
    config: str | None = None
    args: tuple[str, ...] = ()
    partial_commands: bool = False

    @property
    def backend_case(self) -> str:
        prefix, number = self.case_id.split("-", 1)
        return f"{prefix.lower()}_{number}_{self.name}"

    @property
    def runtime_config(self) -> str:
        stem = self.config or self.control or self.scenario
        return (
            "tests/gem5/ai_mesh/fixtures/gate4/"
            f"agent_runtime_config_{stem}.yaml"
        )

    @property
    def manifest_args(self) -> tuple[str, ...]:
        fixtures = "tests/gem5/ai_mesh/fixtures/gate4"
        return (
            "--runtime-config",
            self.runtime_config,
            "--surrogate-profiles",
            f"{fixtures}/agent_surrogate_profiles_{self.scenario}.json",
            *self.args,
        )

    @property
    def invariants(self) -> tuple[str, ...]:
        if self.runner != "GEM5":
            return ("unit_test_passed",)
        return (*ORACLE_CHECKS, *self.evidence)


def _gem(
    case_id,
    name,
    scenario,
    *evidence,
    control=None,
    config=None,
    args=(),
    partial=False,
    fatal=False,
):
    return Gate4Subcase(
        case_id,
        name,
        tuple(evidence),
        terminal_class="EXPECTED_INFRA_FATAL" if fatal else "QUIESCENT_SUCCESS",
        scenario=scenario,
        control=control,
        config=config,
        args=tuple(args),
        partial_commands=partial or fatal,
    )


def _py(case_id, name, target):
    return Gate4Subcase(case_id, name, runner="PYTEST", target=target)


def _cc(case_id, name, target):
    return Gate4Subcase(case_id, name, runner="GTEST", target=target)


GATE4_CASES = {
    "PROTO-21": (
        _gem(
            "PROTO-21",
            "cancel_wins",
            "three_user",
            "cancel_suppresses_target_stages",
            "business_after_control_resolve",
            control="cancel_live",
        ),
        _gem(
            "PROTO-21",
            "standalone_after_target_cq",
            "three_user",
            "target_business_continues",
            control="cancel_late",
        ),
        _gem(
            "PROTO-21",
            "standalone_after_target_error_cq",
            "three_user",
            "standalone_after_target_error_cq",
            control="cancel_late",
            args=("--inject-output-b-error", "8589934593"),
        ),
        _gem(
            "PROTO-21",
            "target_success_wins_late_anchor",
            "three_user",
            "join_target_success_wins",
            control="cancel_late_anchor",
        ),
        _gem(
            "PROTO-21",
            "target_error_wins_late_anchor",
            "three_user",
            "join_target_error_wins",
            control="cancel_late_anchor",
            args=("--inject-output-b-error", "4294967297"),
        ),
        _gem(
            "PROTO-21",
            "cq_order_control_first",
            "three_user",
            "control_cq_first_order",
            control="cancel_live",
            args=("--control-cq-first",),
        ),
        _gem(
            "PROTO-21",
            "cq_order_target_first",
            "three_user",
            "target_cq_first_order",
            "business_after_control_resolve",
            control="cancel_live",
        ),
        _gem(
            "PROTO-21",
            "same_edge_cq_before_intent",
            "three_user",
            "same_edge_cancel_before_intent",
            control="cancel_same_edge",
        ),
        _gem(
            "PROTO-21",
            "command_cq_before_b",
            "three_user",
            "command_cq_before_doorbell_b",
            control="cancel_live",
            args=("--early-command-cq",),
        ),
        _gem(
            "PROTO-21",
            "control_rollback",
            "three_user",
            "control_local_rollback",
            control="cancel_live",
            args=("--inject-control-doorbell-b-error", "1"),
        ),
        _gem(
            "PROTO-21",
            "illegal_dual_leg",
            "three_user",
            "illegal_dual_leg_fatal",
            control="cancel_live",
            args=("--mutate-cancel-command-status", "1:ALREADY_TERMINAL"),
            partial=True,
            fatal=True,
        ),
        _gem(
            "PROTO-21",
            "concurrent_cancel_joins",
            "three_user",
            control="multi_cancel",
        ),
        _gem(
            "PROTO-21",
            "concurrent_cancel_illegal",
            "three_user",
            control="multi_cancel",
            args=("--mutate-cancel-command-status", "1:ALREADY_TERMINAL"),
            partial=True,
            fatal=True,
        ),
    ),
    "PROTO-22": (
        _gem(
            "PROTO-22",
            "release_notfound",
            "three_user",
            "standalone_waiter_only",
            control="release_notfound",
        ),
        _gem(
            "PROTO-22",
            "cancel_notfound_before_target",
            "three_user",
            "standalone_waiter_only",
            control="cancel_before_target",
        ),
        _gem(
            "PROTO-22",
            "cancel_notfound_future_round",
            "three_user",
            "standalone_waiter_only",
            control="cancel_future_round",
        ),
        _gem(
            "PROTO-22",
            "cancel_already_terminal_past_round",
            "three_user",
            "standalone_waiter_only",
            control="cancel_past_round",
        ),
        _gem(
            "PROTO-22",
            "cancel_already_terminal_cross_past_round",
            "three_user",
            "standalone_waiter_only",
            control="cancel_cross_past_round",
        ),
        _gem(
            "PROTO-22",
            "output_drain_split",
            "su_output_drain",
            "output_drain_split_prefix",
            args=("--inject-output-b-error-at", "1:2"),
        ),
        _gem(
            "PROTO-22",
            "cancel_output_chunk",
            "su_output_cancel",
            "cancel_output_partial_prefix",
            control="cancel_output",
            args=("--cancel-output-prefix-bytes", "8192"),
        ),
        _py(
            "PROTO-22",
            "self_target_rejected",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[control_cancel_self_target]",
        ),
    ),
    "PROTO-27": (
        _gem(
            "PROTO-27",
            "host_visible_carrier",
            "three_user",
            "no_early_host_visible_move",
        ),
        _py(
            "PROTO-27",
            "read_delay_variation",
            "util/mesh_ir/tests/integration/test_gate4_determinism.py::"
            "test_cq_metadata_read_delay_moves_host_visible_later_only",
        ),
    ),
    "HOST-1": (
        _gem(
            "HOST-1",
            "first_pass",
            "su_first_pass",
            "single_business_done_terminal",
            "no_core_start_surrogate_only",
        ),
    ),
    "HOST-2": (
        _gem(
            "HOST-2",
            "compile_repair",
            "su_compile_repair",
            "raw_log_parse_repair_generate_order",
        ),
    ),
    "HOST-3": (
        _gem(
            "HOST-3",
            "test_repair",
            "su_test_repair",
            "test_fail_returns_through_npu",
        ),
    ),
    "HOST-4": (
        _py(
            "HOST-4",
            "repair_edge_compile_fail_with_test",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[compile_fail_with_test]",
        ),
        _py(
            "HOST-4",
            "repair_edge_cap_with_parse",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[fail_at_cap_with_parse]",
        ),
        _py(
            "HOST-4",
            "repair_edge_success_with_parse",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[success_with_log_parse]",
        ),
    ),
    "HOST-5": (
        _gem(
            "HOST-5",
            "repair_limit",
            "su_repair_limit",
            "cap_round_no_extra_parse_or_id",
        ),
        _py(
            "HOST-5",
            "round_beyond_cap_rejected",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[round_beyond_cap]",
        ),
    ),
    "HOST-6": (
        _cc(
            "HOST-6",
            "slot_token_atomic",
            "HostResourceManagerTest.SlotAndTokenAcquisitionIsAtomic",
        ),
        _cc(
            "HOST-6",
            "scores_frozen_when_blocked",
            "HostResourceManagerTest.ScoresFreezeWhenNothingIsEligible",
        ),
    ),
    "HOST-7": (
        _cc(
            "HOST-7",
            "release_exactly_once",
            "HostResourceManagerTest.ReleaseReturnsResourcesExactlyOnce",
        ),
    ),
    "HOST-8": (
        _cc(
            "HOST-8",
            "actual_done_timer_cases",
            "AgentWorkloadManager.ActualDoneTakesMaxOfNominalAndLocalIo",
        ),
    ),
    "HOST-9": (
        _cc(
            "HOST-9",
            "swrr_golden",
            "HostResourceManagerTest.SwrrRoundRobinsAcrossKindsWithWeights",
        ),
        _gem(
            "HOST-9",
            "twelve_user_queue_pressure",
            "twelve_user",
            "queue_wait_observed",
            "no_starvation_all_terminal",
            args=(
                "--host-compute-tokens",
                "6",
                "--host-aging-threshold-ns",
                "5000000",
            ),
        ),
    ),
    "HOST-10": (
        _gem(
            "HOST-10",
            "repair_full_context_bytes",
            "su_compile_repair",
            "repair_input_excerpt_share",
        ),
        _gem(
            "HOST-10",
            "big_log_full_context",
            "su_big_log",
            "big_log_local_io_dominates",
        ),
    ),
    "HOST-11": (
        _gem(
            "HOST-11",
            "raw_log_fabric_zero",
            "su_compile_repair",
            "fabric_raw_log_zero",
        ),
        _py(
            "HOST-11",
            "oracle_tamper_negatives",
            "util/mesh_ir/tests/integration/test_gate4_oracle_tamper.py::"
            "test_oracle_rejects_tampered_owner_and_byte_facts",
        ),
    ),
    "HOST-12": (
        _py(
            "HOST-12",
            "digest_canonical_formula",
            "util/mesh_ir/tests/unit/test_agent_workload.py::"
            "test_digest_is_canonical_json_sha256_of_document",
        ),
        _gem(
            "HOST-12",
            "outcome_invariance",
            "su_first_pass",
            "frozen_outcome_replay",
        ),
        _py(
            "HOST-12",
            "three_repeat_single_user",
            "util/mesh_ir/tests/integration/test_gate4_determinism.py::"
            "test_single_user_three_repeat_is_byte_identical",
        ),
    ),
    "HOST-13": (
        _gem(
            "HOST-13",
            "three_user_paths",
            "three_user",
            "three_distinct_paths",
        ),
        _py(
            "HOST-13",
            "three_repeat_three_user",
            "util/mesh_ir/tests/integration/test_gate4_determinism.py::"
            "test_three_user_three_repeat_is_byte_identical",
        ),
    ),
    "HOST-14": (
        _gem(
            "HOST-14",
            "twelve_user_all_terminal",
            "twelve_user",
            "all_twelve_terminal",
        ),
        _gem(
            "HOST-14",
            "concurrent_generate_acceptance",
            "twelve_user",
            "concurrent_accepts_before_first_completion",
            "peak_outstanding_generates",
        ),
    ),
    "HOST-16": (
        _gem(
            "HOST-16",
            "plan_mode_multi_msi",
            "two_user",
            "msi_per_completion",
            config="plan_mode_two_user",
        ),
        _py(
            "HOST-16",
            "polling_completion_rejected",
            "util/mesh_ir/tests/unit/test_agent_config.py::"
            "test_polling_completion_mode_rejected",
        ),
    ),
    "HOST-17": (
        _gem(
            "HOST-17",
            "stop_after_completed_tasks",
            "twelve_user",
            "suppressed_tasks_no_ghost_submission",
            args=("--stop-after-completed-tasks", "4"),
            partial=True,
        ),
        _gem(
            "HOST-17",
            "stop_accepting_at_tick",
            "twelve_user",
            "suppressed_after_stop_tick",
            args=("--stop-accepting-at-tick", "350000000"),
            partial=True,
        ),
        _py(
            "HOST-17",
            "cutoff_suppresses_anchored_control",
            "util/mesh_ir/tests/integration/test_gate4_matrix.py::"
            "test_gate4_cutoff_after_completion_suppresses_anchored_control",
        ),
        _py(
            "HOST-17",
            "zero_request_cutoff_quiesces",
            "util/mesh_ir/tests/integration/test_gate4_matrix.py::"
            "test_gate4_zero_request_cutoff_quiesces",
        ),
    ),
    "HOST-18": (
        _py(
            "HOST-18",
            "positive_fixture_frozen_digest",
            "util/mesh_ir/tests/unit/test_agent_workload.py::"
            "test_minimal_fixture_loads_with_frozen_digest",
        ),
        _py(
            "HOST-18",
            "identity_gap_rejected",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[user_gap]",
        ),
        _py(
            "HOST-18",
            "stage_kind_rejected",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[stage_kind_mismatch]",
        ),
        _py(
            "HOST-18",
            "token_equation_rejected",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[token_equation]",
        ),
        _py(
            "HOST-18",
            "output_capacity_rejected",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_workload_and_control_rejections[output_capacity_below_generated]",
        ),
        _py(
            "HOST-18",
            "duplicate_key_rejected",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_strict_parser_rejections[duplicate_json_key]",
        ),
        _py(
            "HOST-18",
            "non_nfc_rejected",
            "util/mesh_ir/tests/negative/test_agent_workload_negative.py::"
            "test_strict_parser_rejections[non_nfc_string]",
        ),
        _py(
            "HOST-18",
            "config_user_count_rejected",
            "util/mesh_ir/tests/unit/test_agent_config.py::"
            "test_user_count_mismatch_rejected",
        ),
        _py(
            "HOST-18",
            "config_repair_cap_rejected",
            "util/mesh_ir/tests/unit/test_agent_config.py::"
            "test_repair_cap_mismatch_rejected",
        ),
        _py(
            "HOST-18",
            "config_chunk_mismatch_rejected",
            "util/mesh_ir/tests/unit/test_agent_config.py::"
            "test_chunk_mismatch_rejected",
        ),
    ),
    "HOST-19": (
        _gem(
            "HOST-19",
            "aging_completion_bound",
            "twelve_user",
            "aging_reservation_observed",
            "all_twelve_terminal",
            args=(
                "--host-compute-tokens",
                "6",
                "--host-aging-threshold-ns",
                "5000000",
            ),
        ),
    ),
    "HOST-20": (
        _cc(
            "HOST-20",
            "reservation_pauses_allocation",
            "HostResourceManagerTest."
            "AgingReservationPausesAllocationUntilTargetRuns",
        ),
        _cc(
            "HOST-20",
            "reservation_target_completes",
            "AgentWorkloadManager.AgingReservationStartsTargetAfterRelease",
        ),
    ),
    "HOST-21": (
        _cc(
            "HOST-21",
            "non_divisible_timing",
            "AgentWorkloadManager.ActualDoneTakesMaxOfNominalAndLocalIo",
        ),
        _gem(
            "HOST-21",
            "zero_host_local_axi",
            "su_compile_repair",
            "zero_local_stage_axi",
        ),
    ),
    "HOST-22": (
        _cc(
            "HOST-22",
            "host_object_commit_same_object",
            "AgentObjectTable.HostObjectCommitsAtomically",
        ),
        _cc(
            "HOST-22",
            "refs_only_on_committed",
            "AgentObjectTable.ConsumerRefsOnlyOnCommittedObjects",
        ),
    ),
    "HOST-23": (
        _cc(
            "HOST-23",
            "queue_full_arrival_order",
            "HostResourceManagerTest."
            "QueueFullWaitersAreAdmittedInArrivalOrder",
        ),
        _cc(
            "HOST-23",
            "waiter_admitted_on_release",
            "AgentWorkloadManager.QueueFullWaiterAdmittedOnRelease",
        ),
    ),
    "HOST-24": (
        _py(
            "HOST-24",
            "arena_first_command_bases",
            "util/mesh_ir/tests/unit/test_agent_arena.py::"
            "test_arena_plan_first_command_bases_and_validity",
        ),
        _py(
            "HOST-24",
            "arena_monotonic_aligned",
            "util/mesh_ir/tests/unit/test_agent_arena.py::"
            "test_arena_allocations_are_monotonic_and_aligned",
        ),
        _py(
            "HOST-24",
            "image_digest_frozen",
            "util/mesh_ir/tests/unit/test_agent_plan_image.py::"
            "test_image_digest_is_frozen_and_self_consistent",
        ),
        _py(
            "HOST-24",
            "fixture_matches_rebuild",
            "util/mesh_ir/tests/unit/test_agent_plan_image.py::"
            "test_checked_in_fixture_matches_rebuild",
        ),
    ),
    "HOST-26": (
        _gem(
            "HOST-26",
            "output_b_error_domain",
            "three_user",
            "single_error_cq_partial_prefix",
            args=("--inject-output-b-error", "4294967297"),
        ),
        _gem(
            "HOST-26",
            "host_produce_drain",
            "su_compile_repair",
            "object_produce_infra_drain",
            args=(
                "--host-fault-site",
                "object_produce",
                "--host-fault-task",
                "0",
                "--host-fault-round",
                "0",
            ),
        ),
        _gem(
            "HOST-26",
            "host_read_drain",
            "su_first_pass",
            "object_read_infra_drain",
            args=(
                "--host-fault-site",
                "object_read",
                "--host-fault-task",
                "0",
                "--host-fault-round",
                "0",
            ),
        ),
    ),
}

GATE4_IDS = tuple(case_id for case_id, gate in GATE_OF.items() if gate == 4)


GATE4_COVERAGE_GAPS = {}


def gate4_coverage_gaps():
    return dict(GATE4_COVERAGE_GAPS)


def e2e_e_agent_only_subset() -> dict:
    return {
        "schema": "ai_mesh_e2e_e_agent_only_subset_v1",
        "version": 1,
        "subcase_ids": [
            "HOST-1/first_pass",
            "HOST-2/compile_repair",
            "HOST-3/test_repair",
            "HOST-14/twelve_user_all_terminal",
        ],
        "execution_backend": "FULL_CONTEXT_SURROGATE",
        "kv_reuse": False,
        "note": (
            "Agent-only subset reported beside Gate 4; it does not occupy "
            "the full E2E-E PASS"
        ),
    }


def _load_registry(path):
    spec = importlib.util.spec_from_file_location("gate4_runtime_registry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gate4_readiness_failures(manifest, registry_path: Path):
    failures = []
    if tuple(GATE4_CASES) != GATE4_IDS:
        failures.append("Gate 4 contract ID order differs from the acceptance gate map")
    cases = {row["id"]: row for row in manifest.get("cases", [])}
    registry = _load_registry(registry_path)
    for case_id, requirements in GATE4_CASES.items():
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
                if tuple(execution["args"]) != requirement.manifest_args:
                    failures.append(f"{label}: execution args mismatch")
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
    gate4_total = sum(
        case["earliest_gate"] <= 4 for case in manifest.get("cases", [])
    )
    if gate4_total != CUMULATIVE[4]:
        failures.append(
            f"gate 4 cumulative count is {gate4_total}, expected {CUMULATIVE[4]}"
        )
    return tuple(failures)

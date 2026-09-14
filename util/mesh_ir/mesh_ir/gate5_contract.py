"""Gate 5 subcase registration and capability gaps (coding spec 10, 11, 12).

The contract is the single source for subcase names, runners and targets; the
manifest is verified against it instead of keeping a second list.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from mesh_ir.acceptance import CUMULATIVE, GATE_OF

UNIT = "util/mesh_ir/tests/unit"
INTEGRATION = "util/mesh_ir/tests/integration"
FIXTURE_PYTEST = (
    "tests/gem5/ai_mesh/fixtures/gate5/build_gate5_moe_images.py"
)
E2E_SUBCASE = "moe_dual_basic"
STREAMED_OVERLAY = (
    "tests/gem5/ai_mesh/fixtures/gate5/moe_dual_overlay_objects.bin"
)
CACHED_OVERLAY = (
    "tests/gem5/ai_mesh/fixtures/gate5/moe_dual_overlay_cached.bin"
)
DROP_OVERLAY = (
    "tests/gem5/ai_mesh/fixtures/gate5/moe_dual_drop_overlay_objects.bin"
)
DROP_PROGRAM = "$GOLDEN/moe_dual_drop"
COPY_OVERLAY = (
    "tests/gem5/ai_mesh/fixtures/gate5/moe_dual_copy_overlay_objects.bin"
)
COPY_PROGRAM = "$GOLDEN/moe_dual_copy"
QUAD_OVERLAY = (
    "tests/gem5/ai_mesh/fixtures/gate5/moe_quad_overlay_objects.bin"
)
QUAD_REPLAY_OVERLAY = (
    "tests/gem5/ai_mesh/fixtures/gate5/moe_quad_replay_overlay_objects.bin"
)
QUAD_HOTSPOT_OVERLAY = (
    "tests/gem5/ai_mesh/fixtures/gate5/moe_quad_hotspot_overlay_objects.bin"
)
HOTSPOT_EVIDENCE = (
    "quiescence",
    "completion_timing",
    "moe_overlay_traffic",
    "moe_canonical_projection",
    "moe_oracle_recompute",
    "moe_drain_state",
    "moe_hotspot_load",
    "moe_gate_release",
    "moe_overlay_execution",
)
MULTI_PROGRAM = "$GOLDEN/moe_multi"
MULTI_CACHED_OVERLAYS = (
    "tests/gem5/ai_mesh/fixtures/gate5/moe_multi_l1_overlay_cached.bin,"
    "tests/gem5/ai_mesh/fixtures/gate5/moe_multi_l2_overlay_cached.bin"
)
E2E_PROGRAM = "$GOLDEN/moe_dual"
E2E_ARCH = "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"
QUAD_PROGRAM = "$GOLDEN/moe_quad"
QUAD_ARCH = "configs/example/ai_mesh/arch/mesh_4x4_moe.yaml"


@dataclass(frozen=True)
class Gate5Subcase:
    name: str
    runner: str = "PYTEST"
    target: str | None = None
    evidence: tuple[str, ...] = ()
    terminal_class: str = "QUIESCENT_SUCCESS"
    args: tuple[str, ...] = ()
    program: str = E2E_PROGRAM
    arch: str = E2E_ARCH
    overlay_image: str = STREAMED_OVERLAY

    @property
    def manifest_args(self) -> tuple[str, ...]:
        return ("--mesh-program-dir", self.program, "--arch", self.arch,
                "--overlay-image", self.overlay_image, *self.args)

    @property
    def node_id(self) -> str:
        return f"{self.directory}/test_gate5_{self.target}.py::{self.node}"

    node: str = ""
    directory: str = UNIT

    @property
    def invariants(self) -> tuple[str, ...]:
        if self.runner != "GEM5":
            return ("unit_test_passed",)
        return self.evidence


def _unit(name, module, node):
    return Gate5Subcase(name=name, runner="PYTEST", target=module, node=node)


def _integration(name, node, module="e2e_determinism"):
    return Gate5Subcase(name=name, runner="PYTEST", target=module, node=node,
                        directory=INTEGRATION)


def _gem5(name, invariants, target=E2E_SUBCASE, args=(),
          overlay_image=STREAMED_OVERLAY, program=E2E_PROGRAM,
          arch=E2E_ARCH):
    return Gate5Subcase(name=name, runner="GEM5", target=target,
                        evidence=tuple(invariants), args=tuple(args),
                        overlay_image=overlay_image, program=program,
                        arch=arch)


def _identity(*entries):
    return tuple(_unit(name, "moe_identity", node) for name, node in entries)


def _providers(*entries):
    return tuple(_unit(name, "moe_providers", node) for name, node in entries)


def _capacity(*entries):
    return tuple(_unit(name, "moe_capacity", node) for name, node in entries)


def _abi(*entries):
    return tuple(_unit(name, "moe_abi", node) for name, node in entries)


def _materializer(*entries):
    return tuple(_unit(name, "moe_materializer", node) for name, node in entries)


def _overlay(*entries):
    return tuple(_unit(name, "moe_overlay", node) for name, node in entries)


def _runtime(*entries):
    return tuple(_unit(name, "moe_overlay_runtime", node)
                 for name, node in entries)


GATE5_CASES = {
    "MOE-1": _identity(
        ("uid_wire_and_comparator", "test_gate5_uid_wire_is_frozen_little_endian"),
        ("uid_numeric_order", "test_gate5_uid_comparator_is_numeric_not_memcmp"),
        ("uid_illegal_input", "test_gate5_uid_rejects_reserved_and_ordinal_"
                              "violations"),
    ),
    "MOE-2": _identity(
        ("rng_keyed_golden", "test_gate5_rng_key_and_draws_match_the_checked_in_"
                             "golden"),
        ("splitmix_reference", "test_gate5_splitmix_matches_published_reference_"
                               "sequence"),
    ),
    "MOE-5": _providers(
        ("uniform_token_local", "test_gate5_uniform_provider_is_token_local_and_"
                                "frozen"),
        ("replay_projection", "test_gate5_route_replay_provider_projects_the_"
                              "population"),
        ("replay_negative", "test_gate5_route_replay_rejects_digest_and_layer_"
                            "mismatch"),
    ),
    "MOE-6": _providers(
        ("histogram_exact_counts", "test_gate5_histogram_provider_matches_counts_"
                                   "exactly"),
        ("histogram_minimal_assignment", "test_gate5_histogram_uses_"
                                         "lexicographically_minimal_assignment"),
        ("histogram_negative", "test_gate5_histogram_rejects_unreachable_and_"
                               "mismatched_tables"),
    ),
    "MOE-7": _providers(
        ("correlated_windowed", "test_gate5_correlated_provider_is_token_local_"
                                "and_windowed"),
        ("correlated_table_validation", "test_gate5_correlated_provider_validates_"
                                        "its_tables"),
        ("freeze_validator_rejects_duplicates",
         "test_gate5_freeze_validator_rejects_duplicate_expert"),
    ),
    "MOE-8": _identity(
        ("without_replacement_golden", "test_gate5_uniform_without_replacement_"
                                       "matches_the_golden"),
        ("without_replacement_negative", "test_gate5_without_replacement_rejects_"
                                         "topk_above_expert_count"),
    ),
    "MOE-9": _capacity(
        ("q16_capacity_ceiling", "test_gate5_expert_capacity_uses_checked_q16_"
                                 "ceiling"),
        ("layer_view_bounds_formula", "test_gate5_layer_view_bounds_follow_the_"
                                      "frozen_formula"),
    ),
    "MOE-10": _capacity(
        ("instance_bounds_sum", "test_gate5_instance_bounds_sum_every_active_"
                                "region"),
        ("capacity_requirements_match", "test_gate5_capacity_requirements_match_"
                                        "arch_and_registry"),
    ),
    "MOE-11": _materializer(
        ("pad_fill_deterministic", "test_gate5_pad_fill_pattern_is_deterministic_"
                                   "and_tail_exact"),
        ("expert_rows_pad_after_real", "test_gate5_expert_rows_put_padding_after_"
                                       "real_rows"),
    ),
    "MOE-12": _providers(
        ("overflow_policy_follows", "test_gate5_capacity_application_follows_the_"
                                    "overflow_policy"),
    ) + _materializer(
        ("data_plane_drop_fill", "test_gate5_drop_fill_binds_the_drop_projection"),
        ("fill_modes_agree", "test_gate5_fill_modes_agree_on_shape_and_bytes"),
        ("member_rows_match_layer", "test_gate5_member_rows_must_match_the_layer_"
                                    "token_rows"),
    ) + (_gem5(
        "dual_dropped_token_fill",
        ("quiescence", "completion_timing", "moe_overlay_traffic",
         "moe_canonical_projection", "moe_oracle_recompute",
         "moe_drop_fill", "moe_drain_state",
         "moe_gate_release", "moe_overlay_execution"),
        target="moe_dual_drop", overlay_image=DROP_OVERLAY,
        program=DROP_PROGRAM),),
    "MOE-13": _materializer(
        ("local_rows_no_transfer", "test_gate5_local_rows_produce_no_transfer"),
    ) + _runtime(
        ("local_only_placement", "test_gate5_local_only_placement_has_no_"
                                 "transfers"),
    ),
    "MOE-14": (_gem5(
        "copy_through_fan_in_one",
        ("quiescence", "completion_timing", "moe_overlay_traffic",
         "moe_canonical_projection", "moe_oracle_recompute",
         "moe_copy_through", "moe_drain_state",
         "moe_gate_release", "moe_overlay_execution"),
        target="moe_dual_copy", overlay_image=COPY_OVERLAY,
        program=COPY_PROGRAM),) + _materializer(
        ("dispatch_chunks_capped", "test_gate5_dispatch_chunks_are_contiguous_and_"
                                   "capped"),
        ("fan_in_decides_combine", "test_gate5_fan_in_decides_the_combine_"
                                   "command"),
    ) + _overlay(

        ("group_transfers_in_region_zero", "test_gate5_group_transfers_live_in_"
                                           "region_zero"),
    ),
    "MOE-15": _providers(
        ("zero_token_population", "test_gate5_zero_token_population_stays_empty"),
    ) + _materializer(

        ("data_plane_empty_population", "test_gate5_data_plane_handles_empty_"
                                        "population"),
    ),
    "MOE-16": _abi(
        ("feature_bits_and_sections", "test_gate5_fixture_header_and_feature_"
                                      "bits"),
        ("conditional_sections_required", "test_gate5_feature_requires_every_"
                                          "conditional_section"),
        ("sections_without_bit", "test_gate5_sections_are_rejected_without_the_"
                                 "feature_bit"),
        ("writer_rejects_sections_without_bit", "test_gate5_writer_rejects_"
                                                "feature_sections_without_the_"
                                                "bit"),
    ),
    "MOE-17": _providers(
        ("missing_policy_fallback", "test_gate5_missing_policy_fails_or_falls_"
                                    "back"),
        ("selection_digest_membership", "test_gate5_selection_digests_are_"
                                        "membership_sensitive"),
    ),
    "MOE-18": _runtime(
        ("every_dma_descriptor_resolves", "test_gate5_every_dma_descriptor_"
                                          "resolves_both_endpoints"),
        ("materialization_digest_placement", "test_gate5_materialization_digest_"
                                             "is_stable_and_placement_sensitive"),
    ),
    "MOE-20": _capacity(
        ("exact_configuration_accepted", "test_gate5_capacity_exact_configuration_"
                                         "is_accepted"),
        ("capacity_minus_one_no_side_effect", "test_gate5_capacity_minus_one_has_"
                                              "no_side_effect"),
        ("capacity_needs_partitioned_arch", "test_gate5_capacity_needs_a_"
                                            "partitioned_architecture"),
    ),
    "MOE-21": _identity(
        ("rng_keyed_golden", "test_gate5_rng_key_and_draws_match_the_checked_in_"
                             "golden"),
        ("q16_rounding_and_profiles", "test_gate5_q16_rounding_and_profile_"
                                      "digests"),
    ),
    "MOE-22": _identity(
        ("uid_semantic_ordinals", "test_gate5_uid_semantic_and_route_ordinals_"
                                  "follow_phase"),
        ("without_replacement_negative", "test_gate5_without_replacement_rejects_"
                                         "topk_above_expert_count"),
    ),
    "MOE-23": _overlay(
        ("ordinals_dense_per_region", "test_gate5_ordinals_are_dense_per_region_"
                                      "and_kind"),
        ("canonical_key_collision", "test_gate5_ordinals_reject_identical_"
                                    "canonical_keys"),
        ("overlay_enums_frozen", "test_gate5_overlay_enums_are_frozen_by_the_"
                                 "contract"),
    ),
    "MOE-26": _overlay(
        ("structural_verifier_accepts", "test_gate5_structural_verifier_accepts_a_"
                                        "minimal_overlay"),
        ("duplicate_producer_rejected", "test_gate5_structural_verifier_rejects_"
                                        "duplicate_producers"),
        ("missing_producer_and_cycles", "test_gate5_structural_verifier_rejects_"
                                        "missing_producer_and_cycles"),
    ) + _abi(

        ("region_gate_inside_lifecycle", "test_gate5_region_gate_must_sit_inside_"
                                         "the_request_body"),
        ("cross_core_gate_prerequisite", "test_gate5_dual_core_region_gate_needs_"
                                         "a_cross_core_prerequisite"),
        ("independent_core_rejected", "test_gate5_dual_core_region_gate_rejects_"
                                      "an_independent_core"),
    ),
    "MOE-27": _overlay(
        ("scratch_allocates_in_kind_order", "test_gate5_scratch_bump_allocates_in_"
                                            "kind_order"),
        ("scratch_exhaustion", "test_gate5_scratch_rejects_exhaustion_and_unknown_"
                               "region"),
        ("views_reject_escape", "test_gate5_views_reject_unknown_backing_and_"
                                "escape"),
    ) + _runtime(
        ("overlay_bounds_enforced", "test_gate5_overlay_bounds_are_enforced"),
        ("member_slice_shard_binding", "test_gate5_member_slice_binds_only_a_"
                                       "fitting_output_shard"),
        ("member_view_owner_core", "test_gate5_member_view_must_stay_on_its_own_"
                                   "core"),
        ("overlay_image_view_refs_bound", "test_gate5_overlay_image_rejects_too_"
                                          "many_view_refs"),
    ) + _overlay(
        ("bitmap_validity", "test_gate5_row_bitmap_validity_is_exact"),
    ),
    "MOE-29": _abi(
        ("checksum_mutation_layer_policy",
         "test_gate5_recomputed_checksum_mutations_are_rejected"
         "[layer_overflow_policy]"),
        ("checksum_mutation_expert_reserved",
         "test_gate5_recomputed_checksum_mutations_are_rejected"
         "[expert_reserved_core]"),
        ("checksum_mutation_region_reserved",
         "test_gate5_recomputed_checksum_mutations_are_rejected"
         "[region_reserved]"),
        ("semantic_violation_gate_adjacency",
         "test_gate5_verifier_rejects_semantic_violations[region_gate_adjacency]"),
        ("semantic_violation_kernel_opcode",
         "test_gate5_verifier_rejects_semantic_violations[kernel_opcode]"),
        ("semantic_violation_digest_object",
         "test_gate5_verifier_rejects_semantic_violations[digest_object]"),
        ("reencode_byte_identical", "test_gate5_fixture_reencodes_byte_"
                                    "identically"),
    ),
    "MOE-30": _runtime(
        ("image_scratch_addresses", "test_gate5_overlay_image_carries_resolved_"
                                    "scratch_addresses"),
        ("view_backing_bounds", "test_gate5_every_view_resolves_inside_its_"
                                "backing_allocation"),
        ("member_output_static_backing", "test_gate5_dropped_tokens_fill_a_bound_"
                                         "member_output"),
        ("unbound_member_output_rejected", "test_gate5_unbound_member_output_"
                                           "rejects_a_dropped_fill"),
        ("overlay_image_carries_views", "test_gate5_overlay_image_carries_the_"
                                        "command_views"),
        ("fill_modes_service_geometry", "test_gate5_fill_modes_share_the_service_"
                                        "geometry"),
    ) + _materializer(

        ("route_buffer_canonical", "test_gate5_route_buffer_records_are_64b_and_"
                                   "canonical"),
    ),
}

CACHED_SUBCASE = "moe_dual_cached"
REUSE_SUBCASE = "moe_dual_cached_reuse"
TIMING_SUBCASE = "moe_dual_timing"
CACHE_RUNTIME_EVIDENCE = (
    "quiescence",
    "completion_timing",
    "moe_cache_fill_traffic",
    "moe_canonical_projection",
    "moe_oracle_recompute",
    "moe_drain_state",
    "moe_gate_release",
    "moe_overlay_execution",
)
MOE_RUNTIME_EVIDENCE = (
    "quiescence",
    "completion_timing",
    "moe_overlay_traffic",
    "moe_canonical_projection",
    "moe_oracle_recompute",
    "moe_drain_state",
    "moe_gate_release",
    "moe_overlay_execution",
)
REUSE_RUNTIME_EVIDENCE = (
    "quiescence",
    "completion_timing",
    "moe_cache_reuse",
    "moe_canonical_projection",
    "moe_oracle_recompute",
    "moe_drain_state",
    "moe_gate_release",
    "moe_overlay_execution",
)

E2E_CASE = "E2E-C"

GATE5_CASES["MOE-19"] = (
    _gem5("cached_cold_fill", CACHE_RUNTIME_EVIDENCE,
          target=CACHED_SUBCASE, overlay_image=CACHED_OVERLAY,
          args=("--weight-policy", "cached")),
) + tuple(
    _unit(name, "weight_cache", node) for name, node in (
        ("cold_fill_then_hit", "test_gate5_cold_fill_then_hit_reuses_the_same_"
                               "slot"),
        ("attach_shares_one_fill", "test_gate5_attach_shares_one_physical_"
                                   "fill"),
        ("ab_eviction_new_incarnation",
         "test_gate5_ab_eviction_allocates_a_new_incarnation"),
        ("transition_golden", "test_gate5_cache_scenario_matches_the_frozen_"
                              "transition_golden"),
        ("drain_state_persistent_line",
         "test_gate5_cache_drain_state_keeps_the_persistent_line"),
    ))

GATE5_CASES["MOE-3"] = (
    _gem5("timing_buffer_ab", MOE_RUNTIME_EVIDENCE, target=TIMING_SUBCASE),
) + _providers(
    ("member_rank_timing_ab",
     "test_gate5_token_local_member_rank_projection_ab"),
    ("comparable_digest_scope",
     "test_gate5_selection_digest_scope_is_token_and_rank_only"),
)

GATE5_CASES["MOE-25"] = _providers(
    ("batch_composition_token_local",
     "test_gate5_token_local_selection_survives_batch_split"),
    ("histogram_population_equality",
     "test_gate5_histogram_population_equality_is_assignment_stable"),
    ("histogram_reassignment_exact",
     "test_gate5_histogram_reassignment_stays_exact"),
)

GATE5_CASES["MOE-23"] = GATE5_CASES["MOE-23"] + (
    _unit("oracle_fixture_projection_parity", "moe_oracle",
          "test_gate5_oracle_accepts_every_materialized_fixture_projection"),
    _unit("oracle_recomputes_dual_traffic", "moe_oracle",
          "test_gate5_oracle_recomputes_the_dual_fixture_traffic"),
)

GATE5_CASES["MOE-29"] = GATE5_CASES["MOE-29"] + (
    _integration("oracle_runtime_trace_parity",
                 "test_gate5_oracle_verifies_a_real_cached_runtime_trace"),
) + tuple(
    _unit(name, "moe_oracle", node) for name, node in (
        ("oracle_rejects_every_tamper_class",
         "test_gate5_oracle_rejects_every_tamper_class"),
        ("oracle_unknown_tamper_rejected",
         "test_gate5_oracle_rejects_an_unknown_worker_tamper"),
        ("oracle_duplicate_fill_rejected",
         "test_gate5_oracle_reports_duplicate_fill_identities"),
        ("oracle_short_core_rejected",
         "test_gate5_oracle_command_comparison_flags_a_short_core"),
    ))

GATE5_CASES["MOE-4"] = tuple(
    _unit(name, "strict_replay", node) for name, node in (
        ("two_layer_shared_tag_cold_single_fill",
         "test_gate5_strict_replay_cold_arm_shares_one_fill_across_layers"),
        ("two_layer_shared_tag_warm_hit",
         "test_gate5_strict_replay_warm_snapshot_hits_every_layer_"
         "occurrence"),
        ("fast_slow_fill_same_projection",
         "test_gate5_strict_replay_fast_and_slow_fill_agree_on_the_"
         "projection"),
        ("strict_capacity_tick_zero",
         "test_gate5_strict_replay_proves_the_worst_case_cold_union_at_"
         "tick_zero"),
        ("strict_serializes_batches",
         "test_gate5_strict_replay_serializes_batches"),
        ("normal_mode_timing_attribution",
         "test_gate5_normal_mode_timing_changes_the_outcome_with_"
         "attribution"),
        ("materialization_digest_carries_bindings",
         "test_gate5_strict_replay_bindings_enter_the_materialization_"
         "digest"),
        ("binding_consistency_rejected",
         "test_gate5_weight_bindings_reject_inconsistent_fill_references"),
        ("selection_digest_ignores_cache",
         "test_gate5_selection_digest_ignores_the_cache_outcome"),
        ("cache_state_replay_installs_decision_state",
         "test_gate5_cache_state_replay_installs_the_same_decision_state"),
        ("cache_state_replay_carries_tombstones",
         "test_gate5_cache_state_replay_carries_failure_tombstones"),
        ("cache_state_replay_rejects_foreign_snapshot",
         "test_gate5_cache_state_replay_rejects_foreign_snapshots"),
    ))

PRESTART_EVIDENCE = (
    "quiescence",
    "moe_prestart_terminal",
)

GATE5_CASES["MOE-28"] = (
    _gem5("cached_reuse_prestart_failure", PRESTART_EVIDENCE,
          target="moe_dual_cached_reuse_fault",
          args=("--weight-policy", "cached"),
          overlay_image=CACHED_OVERLAY),
    _gem5("cache_line_reuse_across_instances", REUSE_RUNTIME_EVIDENCE,
          target=REUSE_SUBCASE,
          args=("--weight-policy", "cached"),
          overlay_image=CACHED_OVERLAY),
    _gem5("same_core_multi_layer_reservation", CACHE_RUNTIME_EVIDENCE,
          target="moe_multi_cached",
          args=("--weight-policy", "cached"),
          overlay_image=MULTI_CACHED_OVERLAYS, program=MULTI_PROGRAM),
) + tuple(
    _unit(name, "moe_failure_fanout", node) for name, node in (
        ("cross_core_all_or_none",
         "test_gate5_batch_cross_cores_commits_all_or_none"),
        ("cross_core_tombstone_no_side_effect",
         "test_gate5_batch_cross_cores_reports_a_tombstone_without_side_"
         "effects"),
        ("full_cache_hit_protected_from_batch_miss",
         "test_gate5_full_cache_protects_a_hit_from_the_same_batch_miss"),
        ("exact_alias_single_subscriber",
         "test_gate5_exact_alias_shares_one_subscriber_until_the_last_"
         "drain"),
        ("cross_layer_alias_occurrences",
         "test_gate5_cross_layer_alias_keeps_one_fill_per_occurrence"),
        ("ab_eviction_new_incarnation",
         "test_gate5_ab_eviction_spends_two_incarnations_and_two_fills"),
        ("member_cancel_keeps_batch_subscriber",
         "test_gate5_member_cancel_keeps_the_batch_subscriber_woken"),
        ("instance_fault_tombstones_own_subscriber",
         "test_gate5_instance_fault_tombstones_only_its_own_subscriber"),
        ("abort_releases_only_its_own_occurrence",
         "test_gate5_mixed_batch_survives_another_batch_fault"),
        ("fill_error_fans_out_once",
         "test_gate5_fill_error_fans_out_once_and_leaves_one_tombstone"),
        ("no_retry_in_the_same_generation",
         "test_gate5_failed_fill_never_retries_in_the_same_generation"),
        ("cancel_loses_to_error_on_the_same_edge",
         "test_gate5_cancel_and_error_on_the_same_edge_keep_the_first_"
         "error"),
        ("background_fill_settles_tombstoned_subscriber",
         "test_gate5_background_fill_settles_a_tombstoned_subscriber"),
        ("batch_set_abort_releases_every_core",
         "test_gate5_batch_set_abort_releases_every_core"),
        ("batch_set_cancel_and_release",
         "test_gate5_batch_set_cancel_and_release_all_members"),
        ("unknown_core_demand_rejected",
         "test_gate5_batch_set_rejects_an_unknown_core_demand"),
    )) + (
    _unit("drain_state_persistent_tombstone", "weight_cache",
          "test_gate5_cache_drain_state_keeps_the_failure_tombstone"),
)

GATE5_CASES["E2E-C"] = (
    _gem5("dual_baseline", MOE_RUNTIME_EVIDENCE),
    _gem5("balanced_4x4", MOE_RUNTIME_EVIDENCE, target="moe_quad",
          overlay_image=QUAD_OVERLAY, program=QUAD_PROGRAM, arch=QUAD_ARCH),
    _gem5("replay_4x4", MOE_RUNTIME_EVIDENCE, target="moe_quad",
          overlay_image=QUAD_REPLAY_OVERLAY, program=QUAD_PROGRAM,
          arch=QUAD_ARCH),
    _gem5("hotspot_4x4", HOTSPOT_EVIDENCE, target="moe_quad_hotspot",
          overlay_image=QUAD_HOTSPOT_OVERLAY, program=QUAD_PROGRAM,
          arch=QUAD_ARCH),
    _integration("determinism_three_repeat",
                 "test_gate5_e2e_c_four_by_four_is_deterministic"),
)

PRESTART_TOP_FIELDS = ("terminal", "error_drained", "cores", "instances",
                        "moe", "cache_reservation")
PRESTART_CORE_FIELDS = ("core_id", "live_commands", "instance_error",
                        "dma_idle")
PRESTART_CACHE_FIELDS = ("core_id", "live_tokens", "live_obligations",
                         "pending_fills", "pending_subscribers")
PRESTART_RESERVATION_FIELDS = ("waiting", "attempts", "identity_reassignments",
                               "commits", "prestart_failures", "starts",
                               "pending")
PRESTART_REGION_FIELDS = ("layer_id", "region_id", "issued", "completed")
PRESTART_LEDGER_FIELDS = ("completed", "errored", "cancelled")


def _prestart_count(value, minimum: int = 0) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= minimum)


def prestart_terminal_gaps(result, expected_cores=(), expected_regions=(),
                           expected_instances: int = 0) -> tuple:
    """Required evidence first: every lifecycle identity, field and type is
    checked before any value, and a missing entry is never read as zero.
    Expected core/region identities come from the frozen program inputs."""
    gaps = []
    if not isinstance(result, dict):
        return ("prestart terminal document is not an object",)
    for field in PRESTART_TOP_FIELDS:
        if field not in result:
            gaps.append("missing required field %r" % field)
    if result.get("terminal") != "PRESTART_FAILED":
        gaps.append("terminal is %r, expected PRESTART_FAILED"
                    % (result.get("terminal"),))
    if not _prestart_count(result.get("error_drained")) or \
            result["error_drained"] != 1:
        gaps.append("error_drained is %r, expected the integer 1"
                    % (result.get("error_drained"),))
    cores = result.get("cores")
    core_ids = []
    if not isinstance(cores, list) or not cores:
        gaps.append("cores must be a non-empty list")
    else:
        for core in cores:
            if not isinstance(core, dict):
                gaps.append("core entry is not an object")
                continue
            for field in PRESTART_CORE_FIELDS:
                if field not in core:
                    gaps.append("core entry is missing %r" % field)
            identifier = core.get("core_id")
            if not _prestart_count(identifier):
                gaps.append("core id is %r, expected an integer" % (identifier,))
            else:
                core_ids.append(identifier)
            for field in ("live_commands", "instance_error"):
                if field in core and not _prestart_count(core[field]):
                    gaps.append("core %s %s is %r, expected a non-negative "
                                "integer" % (identifier, field, core[field]))
                elif field in core and core[field] != 0:
                    gaps.append("core %s reports %s=%r after the undo"
                                % (identifier, field, core[field]))
            if "dma_idle" in core and not _prestart_count(core["dma_idle"]):
                gaps.append("core %s dma_idle is %r, expected a non-negative "
                            "integer" % (identifier, core["dma_idle"]))
            elif "dma_idle" in core and core["dma_idle"] != 1:
                gaps.append("core %s DMA engine is not idle (%r)"
                            % (identifier, core["dma_idle"]))
        if len(set(core_ids)) != len(core_ids):
            gaps.append("duplicate core identities: %s" % (core_ids,))
        if expected_cores and sorted(core_ids) != sorted(expected_cores):
            gaps.append("core identities are %s, expected %s"
                        % (sorted(core_ids), sorted(expected_cores)))
    moe = result.get("moe")
    cache_state = moe.get("cache_state") if isinstance(moe, dict) else None
    cache_ids = []
    if not isinstance(cache_state, list) or not cache_state:
        gaps.append("moe.cache_state must be a non-empty list")
    else:
        for state in cache_state:
            if not isinstance(state, dict):
                gaps.append("cache state entry is not an object")
                continue
            for field in PRESTART_CACHE_FIELDS:
                if field not in state:
                    gaps.append("cache state entry is missing %r" % field)
            identifier = state.get("core_id")
            if not _prestart_count(identifier):
                gaps.append("cache state core id is %r, expected an integer"
                            % (identifier,))
            else:
                cache_ids.append(identifier)
            for field in ("live_tokens", "live_obligations", "pending_fills",
                          "pending_subscribers"):
                if field in state and not _prestart_count(state[field]):
                    gaps.append("core %s cache %s is %r, expected an integer"
                                % (identifier, field, state[field]))
                elif field in state and state[field] != 0:
                    gaps.append("core %s cache %s=%r after the undo"
                                % (identifier, field, state[field]))
        if len(set(cache_ids)) != len(cache_ids):
            gaps.append("duplicate cache owner identities: %s" % (cache_ids,))
        if core_ids and sorted(cache_ids) != sorted(core_ids):
            gaps.append("cache owners are %s but cores are %s"
                        % (sorted(cache_ids), sorted(core_ids)))
        if expected_cores and sorted(cache_ids) != sorted(expected_cores):
            gaps.append("cache owners are %s, expected %s"
                        % (sorted(cache_ids), sorted(expected_cores)))
    regions = moe.get("regions") if isinstance(moe, dict) else None
    region_ids = []
    if not isinstance(regions, list) or not regions:
        gaps.append("moe.regions must be a non-empty list")
    else:
        for region in regions:
            if not isinstance(region, dict):
                gaps.append("region entry is not an object")
                continue
            for field in PRESTART_REGION_FIELDS:
                if field not in region:
                    gaps.append("region entry is missing %r" % field)
            identity = (region.get("layer_id"), region.get("region_id"))
            if not all(_prestart_count(part) for part in identity):
                gaps.append("region identity %r is not a pair of integers"
                            % (identity,))
            else:
                region_ids.append(identity)
            for field in ("issued", "completed"):
                if field in region and not _prestart_count(region[field]):
                    gaps.append("region %s %s is %r, expected an integer"
                                % (identity, field, region[field]))
                elif field in region and region[field] != 0:
                    gaps.append("undone instance region %s issued %s=%r"
                                % (identity, field, region[field]))
        if len(set(region_ids)) != len(region_ids):
            gaps.append("duplicate region identities: %s" % (region_ids,))
        if expected_regions and sorted(region_ids) != sorted(expected_regions):
            gaps.append("region identities are %s, expected %s"
                        % (sorted(region_ids), sorted(expected_regions)))
    reservation = result.get("cache_reservation")
    if not isinstance(reservation, dict):
        gaps.append("cache reservation ledger is missing or not an object")
    else:
        for field in PRESTART_RESERVATION_FIELDS:
            if field not in reservation:
                gaps.append("cache reservation is missing %r" % field)
        for field in ("waiting", "attempts", "identity_reassignments",
                      "commits", "prestart_failures", "starts"):
            if field in reservation and not _prestart_count(
                    reservation[field]):
                gaps.append("cache reservation %s is %r, expected an integer"
                            % (field, reservation[field]))
        if not isinstance(reservation.get("pending"), list):
            if "pending" in reservation:
                gaps.append("cache reservation pending is not a list")
        elif reservation["pending"]:
            gaps.append("a failed batch still waits for cache resources")
        if reservation.get("waiting") != 0:
            gaps.append("waiting is %r, expected 0" % (reservation.get("waiting"),))
        if reservation.get("identity_reassignments") != 0:
            gaps.append("reservation identities were reassigned")
        if reservation.get("prestart_failures") != 1:
            gaps.append("prestart_failures is %r, expected 1"
                        % (reservation.get("prestart_failures"),))
        starts = reservation.get("starts")
        if not _prestart_count(starts) or starts < 1:
            gaps.append("starts is %r, expected a positive count" % (starts,))
        elif cache_ids:
            expected_commits = starts * len(cache_ids)
            if reservation.get("commits") != expected_commits:
                gaps.append("commits is %r, expected %d for %d started "
                            "instance(s)"
                            % (reservation.get("commits"), expected_commits,
                               starts))
    instances = result.get("instances")
    instance_ids = []
    if not isinstance(instances, list) or not instances:
        gaps.append("instances must be a non-empty list of ledgers")
    else:
        for record in instances:
            if not isinstance(record, dict):
                gaps.append("instance ledger is not an object")
                continue
            identity = record.get("instance")
            if not _prestart_count(identity, 1):
                gaps.append("instance ledger identity is %r, expected a "
                            "positive integer" % (identity,))
            else:
                instance_ids.append(identity)
            ledger_cores = record.get("cores")
            if not isinstance(ledger_cores, dict) or not ledger_cores:
                gaps.append("instance ledger has no per-core entries")
                continue
            if core_ids and sorted(ledger_cores) != sorted(
                    str(core) for core in core_ids):
                gaps.append("instance ledger cores are %s, expected %s"
                            % (sorted(ledger_cores), sorted(core_ids)))
            for core_id, ledger in ledger_cores.items():
                if not isinstance(ledger, dict):
                    gaps.append("instance ledger core %s is not an object"
                                % core_id)
                    continue
                for field in PRESTART_LEDGER_FIELDS:
                    if field not in ledger:
                        gaps.append("instance ledger core %s is missing %r"
                                    % (core_id, field))
                    elif not _prestart_count(ledger[field]):
                        gaps.append("instance ledger core %s %s is %r, "
                                    "expected a non-negative integer"
                                    % (core_id, field, ledger[field]))
        if len(set(instance_ids)) != len(instance_ids):
            gaps.append("duplicate instance identities: %s" % (instance_ids,))
        if instance_ids and instance_ids != \
                list(range(1, len(instance_ids) + 1)):
            gaps.append("instance identities are %s, expected the ordered "
                        "sequence 1..%d" % (instance_ids, len(instance_ids)))
        if expected_instances and len(instance_ids) > expected_instances:
            gaps.append("%d instance ledgers exceed the %d configured "
                        "instance(s)" % (len(instance_ids),
                                         expected_instances))
        starts = reservation.get("starts") if isinstance(reservation, dict) \
            else None
        if _prestart_count(starts) and len(instances) != starts + 1:
            gaps.append("instances has %d ledgers, expected %d started plus "
                        "the undone one" % (len(instances), starts))
        # The undone batch is identified by its terminal identity (starts + 1)
        # and must be the last ledger; the array position is never trusted.
        if _prestart_count(starts, 1):
            undone_identity = starts + 1
            undone_index = next(
                (index for index, record in enumerate(instances)
                 if isinstance(record, dict) and
                 record.get("instance") == undone_identity), None)
            if undone_index is None:
                gaps.append("no ledger for the undone instance %d"
                            % undone_identity)
            else:
                if undone_index != len(instances) - 1:
                    gaps.append("the undone instance %d is ledger %d, not the "
                                "last one" % (undone_identity, undone_index))
                undone_cores = instances[undone_index].get("cores")
                if isinstance(undone_cores, dict):
                    for core_id, ledger in undone_cores.items():
                        if not isinstance(ledger, dict):
                            continue
                        for field in PRESTART_LEDGER_FIELDS:
                            if _prestart_count(ledger.get(field)) and \
                                    ledger[field]:
                                gaps.append("undone instance core %s issued "
                                            "%s=%r"
                                            % (core_id, field, ledger[field]))
    return tuple(gaps)


GATE5_OPEN_GAPS = (
    ("MOE-28", "wait_then_release_production_arm",
     "one batch commits every reachable core/layer demand in a single "
     "shadow, an uncommitted reservation now blocks arm/start, a waiting "
     "item keeps its frozen request_id, and a tombstoned demand ends in a "
     "prestart-failure terminal with zero start; the remaining production "
     "arm is wait -> resource release -> exactly one commit/start, which "
     "needs two crossing batches and lands with the Gate 6 async dispatcher "
     "(Gate 6 R4)"),
)
GATE5_PLACEHOLDER_IDS = ()


def manifest_subcase(subcase: Gate5Subcase) -> dict:
    if subcase.runner == "GEM5":
        execution = {
            "runner": "GEM5",
            "config_script": "configs/example/ai_mesh/run_dummy_core_agent.py",
            "case_name": subcase.target,
            "args": list(subcase.manifest_args),
        }
    else:
        execution = {"runner": "PYTEST", "node_id": subcase.node_id}
    return {
        "name": subcase.name,
        "terminal_class": subcase.terminal_class,
        "execution": execution,
        "invariants": list(subcase.invariants),
    }


def gate5_readiness_failures(manifest, registry_path: Path):
    failures = []
    cases = {row["id"]: row for row in manifest.get("cases", [])}
    spec = importlib.util.spec_from_file_location("gate5_runtime_registry",
                                                 registry_path)
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)
    for case_id, requirements in GATE5_CASES.items():
        case = cases.get(case_id)
        if case is None:
            failures.append(f"{case_id}: missing manifest case")
            continue
        expected_names = [row.name for row in requirements]
        actual_names = [row["name"] for row in case["subcases"]]
        if actual_names != expected_names:
            failures.append(
                f"{case_id}: subcases mismatch: expected {expected_names}, "
                f"got {actual_names}")
            continue
        for subcase, requirement in zip(case["subcases"], requirements):
            label = f"{case_id}/{requirement.name}"
            if subcase["terminal_class"] != requirement.terminal_class:
                failures.append(f"{label}: terminal class mismatch")
            execution = subcase["execution"]
            if execution["runner"] != requirement.runner:
                failures.append(f"{label}: runner mismatch")
                continue
            if requirement.runner == "PYTEST":
                if execution["node_id"] != requirement.node_id:
                    failures.append(f"{label}: pytest node mismatch")
                continue
            if execution["case_name"] != requirement.target:
                failures.append(f"{label}: backend case mismatch")
                continue
            if requirement.target not in registry.CASES:
                failures.append(f"{label}: backend case is not registered")
                continue
            if tuple(execution["args"]) != requirement.manifest_args:
                failures.append(f"{label}: execution args mismatch")
            actual = tuple(registry.invariant_registry(requirement.target,
                                                       execution["args"]))
            if actual != requirement.invariants:
                failures.append(f"{label}: invariant registry mismatch")
    gate5_total = sum(case["earliest_gate"] <= 5
                      for case in manifest.get("cases", []))
    if gate5_total != CUMULATIVE[5]:
        failures.append(f"gate 5 cumulative count is {gate5_total}, "
                        f"expected {CUMULATIVE[5]}")
    return tuple(failures)


def coverage_gaps(manifest):
    gaps = []
    rows = manifest.get("cases", [])
    if not rows:
        gaps.append("manifest: no cases registered")
    cases = {row["id"]: row for row in rows}
    for case_id, subcases in sorted(GATE5_CASES.items()):
        case = cases.get(case_id)
        if case is None:
            gaps.append(f"{case_id}: missing from the manifest")
            continue
        listed = {subcase["name"] for subcase in case.get("subcases", [])}
        for subcase in subcases:
            if subcase.name not in listed:
                gaps.append(f"{case_id}/{subcase.name}: missing subcase")
        if not case.get("subcases"):
            gaps.append(f"{case_id}: registered without subcases")
    for case_id, name, detail in GATE5_OPEN_GAPS:
        if case_id not in GATE5_CASES:
            gaps.append(f"{case_id}: open gap for an unregistered id")
            continue
        gaps.append(f"{case_id}/{name}: {detail}")
    return tuple(gaps)

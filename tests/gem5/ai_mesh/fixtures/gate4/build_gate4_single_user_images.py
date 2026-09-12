import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "util/mesh_ir"))

from mesh_ir.agent_planning import ArenaRegion, capacity_counts
from mesh_ir.agent_plan_image import build_agent_plan_image
from mesh_ir.agent_surrogate import load_surrogate_profiles
from mesh_ir.agent_workload import load_control_plan, load_workload_plan

FIXTURES = Path(__file__).resolve().parent
SINGLE_USER_IMAGES = (
    "first_pass",
    "compile_repair",
    "test_repair",
    "repair_limit",
    "big_log",
    "output_drain",
)
CONTROL_SCENARIOS = (
    "cancel_live",
    "cancel_before_target",
    "cancel_future_round",
    "cancel_past_round",
    "cancel_cross_past_round",
    "cancel_late_anchor",
    "multi_cancel",
    "cancel_late",
    "cancel_same_edge",
    "release_notfound",
)


def regions():
    return [
        ArenaRegion("INPUT", 0x0000000101000000, 67108864, 32),
        ArenaRegion("PARAMETER", 0x0000000100100000, 8388608, 8),
        ArenaRegion("OUTPUT", 0x0000000105000000, 67108864, 32),
        ArenaRegion("METADATA", 0x0000000109000000, 8388608, 8),
    ]


def build_image(name: str) -> bytes:
    workload = load_workload_plan(
        FIXTURES / f"agent_workload_plan_su_{name}.json"
    )
    registry = load_surrogate_profiles(
        FIXTURES / f"agent_surrogate_profiles_su_{name}.json"
    )
    image, _ = build_agent_plan_image(
        workload, None, "EXPLICIT_ONLY", regions(), registry
    )
    return image


def build_su_output_cancel_image() -> bytes:
    workload = load_workload_plan(
        FIXTURES / "agent_workload_plan_su_output_drain.json"
    )
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_su_output_cancel.json"
    )
    control = load_control_plan(
        FIXTURES / "agent_control_plan_cancel_output.json", workload
    )
    image, _ = build_agent_plan_image(
        workload, control, "EXPLICIT_ONLY", regions(), registry
    )
    return image


def build_two_user_image() -> bytes:
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_two_user.json"
    )
    image, _ = build_agent_plan_image(
        workload, None, "EXPLICIT_ONLY", regions(), registry
    )
    return image


def build_three_user_image(control_name: str | None = None) -> bytes:
    workload = load_workload_plan(
        FIXTURES / "agent_workload_plan_three_user.json"
    )
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_three_user.json"
    )
    control = (
        load_control_plan(
            FIXTURES / f"agent_control_plan_{control_name}.json", workload
        )
        if control_name is not None
        else None
    )
    image, _ = build_agent_plan_image(
        workload, control, "EXPLICIT_ONLY", regions(), registry
    )
    return image


def build_twelve_user_image() -> bytes:
    workload = load_workload_plan(
        FIXTURES / "agent_workload_plan_twelve_user.json"
    )
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_twelve_user.json"
    )
    image, _ = build_agent_plan_image(
        workload, None, "EXPLICIT_ONLY", regions(), registry
    )
    return image


def runtime_config_document(
    workload_name: str,
    control_name: str | None,
    stop_after: int,
    aging_threshold_ns: int = 50000000,
    profile_name: str | None = None,
) -> dict:
    workload = load_workload_plan(
        FIXTURES / f"agent_workload_plan_{workload_name}.json"
    )
    control = (
        load_control_plan(
            FIXTURES / f"agent_control_plan_{control_name}.json", workload
        )
        if control_name is not None
        else None
    )
    registry = load_surrogate_profiles(
        FIXTURES
        / f"agent_surrogate_profiles_{profile_name or workload_name}.json"
    )
    counts = capacity_counts(workload, control, "EXPLICIT_ONLY")
    users = counts["U"]
    commands = counts["Q"]
    controls = counts["C"] + counts["L"]
    context = counts["live_context_bound"]
    stage_counts = {"COMPILE": 0, "TEST": 0, "LOG_PARSE": 0}
    for user in workload.users:
        for task in user.tasks:
            for round_ in task.rounds:
                for stage in (round_.compile, round_.test, round_.log_parse):
                    if stage is not None:
                        stage_counts[stage.kind] += 1
    service_queue_depth = max(
        [8] + [min(count, len(workload.users)) for count in stage_counts.values()]
    )
    return {
        "schema": "agent_runtime_config_v1",
        "version": 1,
        "runtime": {
            "mode": "FULL_TIMING",
            "master_seed": 20260909,
            "stop_after_completed_tasks": stop_after or 0,
            "stop_accepting_new_tasks_at_tick": None,
            "require_global_drain": True,
        },
        "agent": {
            "users": len(workload.users),
            "mode": "replay_plan",
            "workload_plan": f"agent_workload_plan_{workload_name}.json",
            "workload_output_oracle": None,
            "control_plan": (
                f"agent_control_plan_{control_name}.json"
                if control_name is not None
                else None
            ),
            "max_repair_rounds": workload.max_repair_rounds,
            "release_session_on_task_terminal": False,
            "completion_mode": "interrupt",
        },
        "serving": {
            "output_chunk_bytes": registry.profiles[0].publish_chunk_bytes,
            "sq_entries": 8,
            "cq_entries": 8,
            "max_inflight_msi_writes": 4,
            "max_active_sequences": 1,
            "max_request_contexts": max(8, context),
            "sq_intake_slots": 8,
            "cq_obligation_entries": max(8, context),
            "terminal_result_queue_depth": max(8, context),
            "driver_submission_table_entries": 4,
            "early_completion_cache_entries": 4,
            "max_pending_sq_publications": 1,
            "max_sq_entries_per_publication": 1,
            "host_ack_response_entries": 1,
            "early_cq_ack_entries": 4,
            "seen_request_id_entries": max(16, commands),
            "host_issued_request_id_entries": max(16, commands),
            "batch_failure_record_entries": 4,
            "session_release_waiter_entries": 4,
            "kv_session_record_entries": max(8, counts["S"]),
            "kv_session_tombstone_entries": 4,
            "kv_admission_wait_entries": max(8, min(counts["G"], users)),
            "cache_reservation_queue_entries": 64,
            "instance_view_buffer_depth": 4096,
            "instance_view_ref_buffer_depth": 4096,
            "address_map": {
                "npu_control_page_base": 0x0000003000000000,
                "host_control_page_base": 0x0000000100000000,
                "msi_address": 0x0000000100000100,
                "sq_ring_base": 0x0000000100010000,
                "cq_ring_base": 0x0000000100020000,
                "parameter_arena": {
                    "base": 0x0000000100100000,
                    "bytes": 8388608,
                },
                "input_arena": {
                    "base": 0x0000000101000000,
                    "bytes": 67108864,
                },
                "output_arena": {
                    "base": 0x0000000105000000,
                    "bytes": 67108864,
                },
                "metadata_arena": {
                    "base": 0x0000000109000000,
                    "bytes": 8388608,
                },
                "arena_alignment_bytes": 32,
            },
            "control_axi_ids": {
                "sq_doorbell": 16,
                "cq_head_ack": 17,
                "sq_head_update": 18,
                "cq_tail_update": 19,
            },
            "msi_axi_id_base": 32,
            "msi_axi_id_count": 16,
        },
        "agent_axi_driver": {
            "mode": "REMOTE_HOST_PROXY",
            "clock": "1GHz",
            "remote_host_memory": {
                "base": 0x0000000100000000,
                "bytes": 4294967296,
                "target_request_queue_depth": 64,
                "target_response_queue_depth": 64,
                "local_store_queue_depth": 64,
            },
            "remote_link_shaper": {
                "enabled": False,
                "to_npu_latency_cycles": 0,
                "to_agent_latency_cycles": 0,
                "bytes_per_cycle_per_direction": 0,
                "to_npu_queue_depth": 0,
                "to_agent_queue_depth": 0,
            },
            "synthetic_host_services": {
                "compute_tokens": 24,
                "host_available_fraction_q16": 52428,
                "compile_slots": 4,
                "test_slots": 6,
                "log_parse_slots": 4,
                "service_queue_depth": service_queue_depth,
                "sq_submit_ready_queue_entries": max(16, commands),
                "control_trigger_queue_entries": max(8, controls),
                "irq_queue_entries": 16,
                "completion_read_queue_entries": 16,
                "cancel_join_entries": max(4, counts["C"]),
                "standalone_control_waiter_entries": max(8, controls),
                "scheduler_weights": {"compile": 4, "test": 3, "log_parse": 2},
                "aging_threshold_ns": aging_threshold_ns,
                "host_admission_registry_entries": max(
                    8, min(counts["H"], users)
                ),
                "release_waiter_entries": 4,
                "agent_object_table_entries": max(16, counts["O"]),
                "local_io_model": {
                    "enabled": True,
                    "fixed_latency_ns": 1000,
                    "bytes_per_ns": 64,
                },
            },
        },
    }


RUNTIME_CONFIGS = (
    ("su_first_pass", "su_first_pass", None, 0),
    ("su_compile_repair", "su_compile_repair", None, 0),
    ("su_test_repair", "su_test_repair", None, 0),
    ("su_repair_limit", "su_repair_limit", None, 0),
    ("su_big_log", "su_big_log", None, 0),
    ("su_output_drain", "su_output_drain", None, 0),
    ("three_user", "three_user", None, 0),
    ("twelve_user", "twelve_user", None, 0),
    ("plan_mode_two_user", "two_user", None, 0),
    ("cancel_live", "three_user", "cancel_live", 0),
    ("cancel_late", "three_user", "cancel_late", 0),
    ("cancel_same_edge", "three_user", "cancel_same_edge", 0),
    ("release_notfound", "three_user", "release_notfound", 0),
    ("cancel_output", "su_output_drain", "cancel_output", 0, "su_output_cancel"),
    ("two_user", "two_user", "two_user", 3),
    ("multi_cancel", "three_user", "multi_cancel", 0),
    ("cancel_before_target", "three_user", "cancel_before_target", 0),
    ("cancel_future_round", "three_user", "cancel_future_round", 0),
    ("cancel_past_round", "three_user", "cancel_past_round", 0),
    ("cancel_cross_past_round", "three_user", "cancel_cross_past_round", 0),
    ("cancel_late_anchor", "three_user", "cancel_late_anchor", 0),
)


def write_runtime_configs() -> int:
    for config in RUNTIME_CONFIGS:
        stem, workload_name, control_name, stop_after = config[:4]
        profile_name = config[4] if len(config) > 4 else None
        document = runtime_config_document(
            workload_name,
            control_name,
            stop_after,
            profile_name=profile_name,
        )
        target = FIXTURES / f"agent_runtime_config_{stem}.yaml"
        target.write_text(
            yaml.safe_dump(document, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        print(target)
    return 0


def main() -> int:
    for name in SINGLE_USER_IMAGES:
        target = FIXTURES / f"agent_plan_image_su_{name}.bin"
        target.write_bytes(build_image(name))
        print(target)
    two_user = FIXTURES / "agent_plan_image_two_user.bin"
    two_user.write_bytes(build_two_user_image())
    print(two_user)
    three_user = FIXTURES / "agent_plan_image_three_user.bin"
    three_user.write_bytes(build_three_user_image())
    print(three_user)
    multi_cancel = FIXTURES / "agent_plan_image_multi_cancel.bin"
    multi_cancel.write_bytes(build_three_user_image("multi_cancel"))
    print(multi_cancel)
    twelve_user = FIXTURES / "agent_plan_image_twelve_user.bin"
    twelve_user.write_bytes(build_twelve_user_image())
    print(twelve_user)
    for name in CONTROL_SCENARIOS:
        target = FIXTURES / f"agent_plan_image_ctrl_{name}.bin"
        target.write_bytes(build_three_user_image(name))
        print(target)
    su_output_cancel = FIXTURES / "agent_plan_image_su_output_cancel.bin"
    su_output_cancel.write_bytes(build_su_output_cancel_image())
    print(su_output_cancel)
    return write_runtime_configs()


if __name__ == "__main__":
    raise SystemExit(main())

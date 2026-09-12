from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from mesh_ir.acceptance import read_yaml_document
from mesh_ir.agent_planning import capacity_counts
from mesh_ir.agent_surrogate import SurrogateProfileRegistry
from mesh_ir.agent_workload import (
    ControlPlan,
    PlanError,
    WorkloadPlan,
    null_control_plan_digest,
    validate_against_schema,
)
from mesh_ir.model import canonical_json_bytes

_HOST_WINDOW_CONTROL_BYTES = 4096
_MSI_REGISTER_BYTES = 8
_RING_ENTRY_BYTES = {"sq": 64, "cq": 32}
_PROXY_CONTROL_OFFSET = 0x100000


def agent_proxy_control_base(address_map) -> int:
    return address_map["npu_control_page_base"] + _PROXY_CONTROL_OFFSET


@dataclass(frozen=True)
class AgentRuntimeConfig:
    document: dict
    digest: str


def load_agent_runtime_config(path) -> AgentRuntimeConfig:
    document = read_yaml_document(Path(path))
    validate_against_schema("agent_runtime_config_v1.schema.json", document)
    return AgentRuntimeConfig(
        document=document,
        digest=hashlib.sha256(canonical_json_bytes(document)).hexdigest(),
    )


def release_policy_of(config: AgentRuntimeConfig) -> str:
    if config.document["agent"]["release_session_on_task_terminal"]:
        return "AUTO_PER_TASK"
    return "EXPLICIT_ONLY"


def _capacity_error(key: str, reason: str) -> PlanError:
    return PlanError("E_CAPACITY_PLAN", key, reason)


def _host_ranges(address_map, serving) -> list[tuple[int, int, str]]:
    return [
        (
            address_map["host_control_page_base"],
            address_map["host_control_page_base"] + _HOST_WINDOW_CONTROL_BYTES,
            "host_control_page_base",
        ),
        (
            address_map["sq_ring_base"],
            address_map["sq_ring_base"] + _RING_ENTRY_BYTES["sq"] * serving["sq_entries"],
            "sq_ring_base",
        ),
        (
            address_map["cq_ring_base"],
            address_map["cq_ring_base"] + _RING_ENTRY_BYTES["cq"] * serving["cq_entries"],
            "cq_ring_base",
        ),
        (
            address_map["parameter_arena"]["base"],
            address_map["parameter_arena"]["base"] + address_map["parameter_arena"]["bytes"],
            "parameter_arena",
        ),
        (
            address_map["input_arena"]["base"],
            address_map["input_arena"]["base"] + address_map["input_arena"]["bytes"],
            "input_arena",
        ),
        (
            address_map["output_arena"]["base"],
            address_map["output_arena"]["base"] + address_map["output_arena"]["bytes"],
            "output_arena",
        ),
        (
            address_map["metadata_arena"]["base"],
            address_map["metadata_arena"]["base"] + address_map["metadata_arena"]["bytes"],
            "metadata_arena",
        ),
    ]


def validate_runtime_config(
    config: AgentRuntimeConfig,
    workload: WorkloadPlan,
    surrogate_registry: SurrogateProfileRegistry | None = None,
) -> None:
    document = config.document
    agent = document["agent"]
    serving = document["serving"]
    if document["agent_axi_driver"]["remote_link_shaper"]["enabled"]:
        raise PlanError(
            "E_AGENT_PLAN",
            "agent_axi_driver/remote_link_shaper",
            "remote link shaping is not implemented in Gate 4",
        )
    if agent["users"] != len(workload.users):
        raise PlanError(
            "E_AGENT_PLAN", "agent/users", "user count differs from the workload plan"
        )
    if agent["max_repair_rounds"] != workload.max_repair_rounds:
        raise PlanError(
            "E_AGENT_PLAN",
            "agent/max_repair_rounds",
            "repair cap differs from the workload plan header",
        )
    if surrogate_registry is not None:
        chunk = surrogate_registry.profiles[0].publish_chunk_bytes
        if serving["output_chunk_bytes"] != chunk:
            raise PlanError(
                "E_AGENT_PLAN",
                "serving/output_chunk_bytes",
                f"must equal the surrogate publish chunk {chunk}",
            )
    address_map = serving["address_map"]
    memory = document["agent_axi_driver"]["remote_host_memory"]
    window_end = memory["base"] + memory["bytes"]
    if not address_map["host_control_page_base"] % _HOST_WINDOW_CONTROL_BYTES == 0:
        raise PlanError(
            "E_ADDRESS_PLAN", "host_control_page_base", "control page must be 4 KiB aligned"
        )
    for base_field in ("sq_ring_base", "cq_ring_base"):
        if address_map[base_field] % _HOST_WINDOW_CONTROL_BYTES != 0:
            raise PlanError(
                "E_ADDRESS_PLAN", base_field, "rings must be 4 KiB aligned"
            )
    alignment = address_map["arena_alignment_bytes"]
    for arena_field in ("parameter_arena", "input_arena", "output_arena", "metadata_arena"):
        if address_map[arena_field]["base"] % alignment != 0:
            raise PlanError(
                "E_ADDRESS_PLAN", arena_field, f"arena base must be {alignment}-byte aligned"
            )
    msi_start = address_map["msi_address"]
    msi_end = msi_start + _MSI_REGISTER_BYTES
    control_start = address_map["host_control_page_base"]
    if not (control_start <= msi_start and msi_end <= control_start + _HOST_WINDOW_CONTROL_BYTES):
        raise PlanError(
            "E_ADDRESS_PLAN", "msi_address", "MSI register must sit in the host control page"
        )
    ranges = _host_ranges(address_map, serving)
    for start, end, label in ranges:
        if start < memory["base"] or end > window_end:
            raise PlanError(
                "E_ADDRESS_PLAN",
                label,
                "host range must stay inside the remote host memory window",
            )
    ordered = sorted(ranges)
    for (_, previous_end, previous_label), (next_start, _, next_label) in zip(
        ordered, ordered[1:]
    ):
        if next_start < previous_end:
            raise PlanError(
                "E_ADDRESS_PLAN",
                f"{previous_label}/{next_label}",
                "host ranges must not overlap",
            )
    if (
        memory["base"] <= address_map["npu_control_page_base"]
        and address_map["npu_control_page_base"] < window_end
    ):
        raise PlanError(
            "E_ADDRESS_PLAN",
            "npu_control_page_base",
            "NPU control page must not fall into the host memory window",
        )
    proxy_base = agent_proxy_control_base(address_map)
    if memory["base"] <= proxy_base and proxy_base < window_end:
        raise PlanError(
            "E_ADDRESS_PLAN",
            "npu_control_page_base",
            "agent proxy control page must not fall into the host memory "
            "window",
        )
    control_ids = serving["control_axi_ids"]
    values = list(control_ids.values())
    if len(set(values)) != len(values):
        raise PlanError(
            "E_AGENT_PLAN", "serving/control_axi_ids", "control AXI IDs must be unique"
        )
    msi_base = serving["msi_axi_id_base"]
    if any(msi_base <= value < msi_base + serving["msi_axi_id_count"] for value in values):
        raise PlanError(
            "E_AGENT_PLAN",
            "serving/msi_axi_id_base",
            "control AXI IDs must not overlap the MSI ID range",
        )
    if serving["msi_axi_id_count"] < serving["max_inflight_msi_writes"]:
        raise PlanError(
            "E_AGENT_PLAN",
            "serving/msi_axi_id_count",
            "must cover max_inflight_msi_writes",
        )
    services = document["agent_axi_driver"]["synthetic_host_services"]
    tokens = _global_host_tokens(services)
    if tokens == 0:
        raise _capacity_error(
            "global_host_tokens", "configured token pool evaluates to zero"
        )


def _global_host_tokens(services) -> int:
    return (
        services["compute_tokens"] * services["host_available_fraction_q16"] // 65536
    )


def _stage_statistics(workload: WorkloadPlan):
    counts = {"COMPILE": 0, "TEST": 0, "LOG_PARSE": 0}
    max_tokens = 0
    for user in workload.users:
        for task in user.tasks:
            for round_ in task.rounds:
                for stage in (round_.compile, round_.test, round_.log_parse):
                    if stage is None:
                        continue
                    counts[stage.kind] += 1
                    max_tokens = max(max_tokens, stage.host_tokens_required)
    return counts, max_tokens


def _required_global(counts, stage_tokens, serving, release_policy, shaper_enabled):
    context = counts["live_context_bound"]
    publication_entries = (
        serving["max_pending_sq_publications"] * serving["max_sq_entries_per_publication"]
    )
    release_waiters = min(counts["A"] + counts["L"], counts["U"] + counts["L"])
    return {
        "sq_entries": 1,
        "cq_entries": 1,
        "max_inflight_msi_writes": 1,
        "max_active_sequences": 1,
        "max_request_contexts": context,
        "sq_intake_slots": min(serving["sq_entries"], context),
        "cq_obligation_entries": context,
        "terminal_result_queue_depth": context,
        "driver_submission_table_entries": publication_entries,
        "early_completion_cache_entries": publication_entries,
        "max_pending_sq_publications": 1,
        "max_sq_entries_per_publication": 1,
        "host_ack_response_entries": 1,
        "early_cq_ack_entries": serving["max_inflight_msi_writes"],
        "sq_submit_ready_queue_entries": counts["Q"],
        "control_trigger_queue_entries": counts["C"] + counts["L"],
        "irq_queue_entries": serving["max_inflight_msi_writes"],
        "completion_read_queue_entries": serving["cq_entries"],
        "cancel_join_entries": min(counts["C"], counts["U"]),
        "standalone_control_waiter_entries": counts["C"] + counts["L"],
        "seen_request_id_entries": counts["Q"],
        "host_issued_request_id_entries": counts["Q"],
        "batch_failure_record_entries": min(counts["G"], serving["max_active_sequences"]),
        "global_host_tokens": stage_tokens,
        "kv_session_record_entries": (
            min(counts["S"], counts["U"])
            if release_policy == "AUTO_PER_TASK"
            else counts["S"]
        ),
        "kv_session_tombstone_entries": counts["T"],
        "kv_admission_wait_entries": min(counts["G"], counts["U"]),
        "cache_reservation_queue_entries": serving["max_active_sequences"] * counts["ML"],
        "instance_view_buffer_depth": 0,
        "instance_view_ref_buffer_depth": 0,
        "npu_session_release_waiter_entries": release_waiters,
        "host_release_waiter_entries": release_waiters,
        "host_admission_registry_entries": min(counts["H"], counts["U"]),
        "agent_object_table_entries": counts["O"],
        "agent_local_store_queue_entries": 1,
        "agent_target_request_queue_entries": 1,
        "agent_target_response_queue_entries": 1,
        "agent_proxy_to_npu_queue_entries": 1 if shaper_enabled else 0,
        "agent_proxy_to_agent_queue_entries": 1 if shaper_enabled else 0,
    }


def _configured_global(document):
    serving = document["serving"]
    driver = document["agent_axi_driver"]
    services = driver["synthetic_host_services"]
    memory = driver["remote_host_memory"]
    shaper = driver["remote_link_shaper"]
    return {
        "sq_entries": serving["sq_entries"],
        "cq_entries": serving["cq_entries"],
        "max_inflight_msi_writes": serving["max_inflight_msi_writes"],
        "max_active_sequences": serving["max_active_sequences"],
        "max_request_contexts": serving["max_request_contexts"],
        "sq_intake_slots": serving["sq_intake_slots"],
        "cq_obligation_entries": serving["cq_obligation_entries"],
        "terminal_result_queue_depth": serving["terminal_result_queue_depth"],
        "driver_submission_table_entries": serving["driver_submission_table_entries"],
        "early_completion_cache_entries": serving["early_completion_cache_entries"],
        "max_pending_sq_publications": serving["max_pending_sq_publications"],
        "max_sq_entries_per_publication": serving["max_sq_entries_per_publication"],
        "host_ack_response_entries": serving["host_ack_response_entries"],
        "early_cq_ack_entries": serving["early_cq_ack_entries"],
        "sq_submit_ready_queue_entries": services["sq_submit_ready_queue_entries"],
        "control_trigger_queue_entries": services["control_trigger_queue_entries"],
        "irq_queue_entries": services["irq_queue_entries"],
        "completion_read_queue_entries": services["completion_read_queue_entries"],
        "cancel_join_entries": services["cancel_join_entries"],
        "standalone_control_waiter_entries": services["standalone_control_waiter_entries"],
        "seen_request_id_entries": serving["seen_request_id_entries"],
        "host_issued_request_id_entries": serving["host_issued_request_id_entries"],
        "batch_failure_record_entries": serving["batch_failure_record_entries"],
        "global_host_tokens": _global_host_tokens(services),
        "kv_session_record_entries": serving["kv_session_record_entries"],
        "kv_session_tombstone_entries": serving["kv_session_tombstone_entries"],
        "kv_admission_wait_entries": serving["kv_admission_wait_entries"],
        "cache_reservation_queue_entries": serving["cache_reservation_queue_entries"],
        "instance_view_buffer_depth": serving["instance_view_buffer_depth"],
        "instance_view_ref_buffer_depth": serving["instance_view_ref_buffer_depth"],
        "npu_session_release_waiter_entries": serving["session_release_waiter_entries"],
        "host_release_waiter_entries": services["release_waiter_entries"],
        "host_admission_registry_entries": services["host_admission_registry_entries"],
        "agent_object_table_entries": services["agent_object_table_entries"],
        "agent_local_store_queue_entries": memory["local_store_queue_depth"],
        "agent_target_request_queue_entries": memory["target_request_queue_depth"],
        "agent_target_response_queue_entries": memory["target_response_queue_depth"],
        "agent_proxy_to_npu_queue_entries": (
            shaper["to_npu_queue_depth"] if shaper["enabled"] else 0
        ),
        "agent_proxy_to_agent_queue_entries": (
            shaper["to_agent_queue_depth"] if shaper["enabled"] else 0
        ),
    }


def _service_pools(counts, stage_counts, services):
    required = {}
    configured = {}
    for kind, slot_field in (
        ("COMPILE", "compile_slots"),
        ("TEST", "test_slots"),
        ("LOG_PARSE", "log_parse_slots"),
    ):
        reachable = stage_counts[kind] > 0
        required[kind] = {
            "slots": 1 if reachable else 0,
            "queue_depth": min(stage_counts[kind], counts["U"]),
        }
        configured[kind] = {
            "slots": services[slot_field],
            "queue_depth": services["service_queue_depth"],
        }
    return required, configured


def build_capacity_plan(
    workload: WorkloadPlan,
    control: ControlPlan | None,
    config: AgentRuntimeConfig,
) -> dict:
    document = config.document
    serving = document["serving"]
    services = document["agent_axi_driver"]["synthetic_host_services"]
    shaper_enabled = document["agent_axi_driver"]["remote_link_shaper"]["enabled"]
    release_policy = release_policy_of(config)
    counts = capacity_counts(workload, control, release_policy)
    stage_counts, stage_tokens = _stage_statistics(workload)
    required_global = _required_global(
        counts, stage_tokens, serving, release_policy, shaper_enabled
    )
    configured_global = _configured_global(document)
    pools_required, pools_configured = _service_pools(counts, stage_counts, services)
    for key in sorted(required_global):
        if configured_global[key] < required_global[key]:
            raise _capacity_error(
                f"configured/{key}",
                f"configured {configured_global[key]} is below required {required_global[key]}",
            )
    for kind in sorted(pools_required):
        pool_required = pools_required[kind]
        pool_configured = pools_configured[kind]
        for field in sorted(pool_required):
            if pool_configured[field] < pool_required[field]:
                raise _capacity_error(
                    f"configured/service_pools/{kind}/{field}",
                    f"configured {pool_configured[field]} is below required {pool_required[field]}",
                )
    headroom_global = {
        key: configured_global[key] - required_global[key]
        for key in sorted(required_global)
    }
    headroom_pools = {
        kind: {
            field: pools_configured[kind][field] - pools_required[kind][field]
            for field in sorted(pools_required[kind])
        }
        for kind in sorted(pools_required)
    }
    capacity_document = {
        "schema": "capacity_plan_v1",
        "version": 1,
        "workload_plan_digest": workload.digest,
        "control_plan_digest": (
            control.digest
            if control is not None
            else null_control_plan_digest(workload.digest)
        ),
        "release_policy": release_policy,
        "counts": counts,
        "required": {
            "global": required_global,
            "service_pools": pools_required,
            "per_core": [],
            "per_instance_profile": [],
        },
        "configured": {
            "global": configured_global,
            "service_pools": pools_configured,
            "per_core": [],
            "per_instance_profile": [],
        },
        "headroom": {
            "global": headroom_global,
            "service_pools": headroom_pools,
            "per_core": [],
            "per_instance_profile": [],
        },
    }
    capacity_document["capacity_plan_digest"] = hashlib.sha256(
        canonical_json_bytes(capacity_document)
    ).hexdigest()
    validate_against_schema("capacity_plan_v1.schema.json", capacity_document)
    return capacity_document

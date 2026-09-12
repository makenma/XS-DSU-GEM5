from __future__ import annotations

import hashlib
import struct

from mesh_ir.agent_planning import (
    ArenaRegion,
    build_command_identity_plan,
    build_host_arena_object_plan,
    build_host_task_identity_plan,
)
from mesh_ir.agent_surrogate import SurrogateProfileRegistry
from mesh_ir.agent_workload import ControlPlan, WorkloadPlan

MAGIC = b"AGPI"
VERSION = 1

_SECTION_WORKLOAD = 1
_SECTION_COMMAND_IDENTITY = 2
_SECTION_HOST_TASK_IDENTITY = 3
_SECTION_ARENA = 4
_SECTION_SURROGATE = 5
_SECTION_CONTROL = 6

_KV_POLICY = {"INITIAL": 0, "REQUIRE_REUSE": 1, "ALLOW_REPREFILL": 2}
_OUTCOME = {"SUCCESS": 0, "FAIL": 1, "NONE": 2}
_COMMAND_KIND = {"GENERATE": 0, "RELEASE_SESSION": 1, "CANCEL": 2}
_TRIGGER_KIND = {
    "SCENARIO_START": 0,
    "AFTER_SQ_ACCEPT": 1,
    "AFTER_FIRST_OUTPUT_CHUNK": 2,
    "SAME_EDGE_AS_TERMINAL": 3,
    "AFTER_GENERATE_TERMINAL": 4,
    "AFTER_SESSION_ADMISSION": 5,
    "AFTER_BATCH_FREEZE": 6,
    "AFTER_RELEASE_TERMINAL": 7,
}


def _u8(value: int) -> bytes:
    return struct.pack("<B", value)


def _u16(value: int) -> bytes:
    return struct.pack("<H", value)


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _u64(value: int) -> bytes:
    return struct.pack("<Q", value)


def _stage(stage) -> bytes:
    return (
        _u64(stage.nominal_ns)
        + _u16(stage.host_tokens_required)
        + _u8(_OUTCOME[stage.outcome])
        + _u64(stage.local_io.read_bytes)
        + _u64(stage.local_io.write_bytes)
        + _u64(stage.raw_log_bytes or 0)
        + _u64(stage.excerpt_bytes or 0)
        + _u32(stage.excerpt_tokens or 0)
    )


def _round(round_) -> bytes:
    data = (
        _u32(round_.workload_plan_item_id)
        + _u16(round_.repair_round)
        + _u8(_KV_POLICY[round_.kv_policy])
        + _u8(round_.deadline_tick is not None)
        + _u64(round_.deadline_tick or 0)
        + _u32(round_.full_context_tokens)
        + _u64(round_.full_context_bytes)
        + _u32(round_.expected_cached_tokens)
        + _u64(round_.expected_cached_context_bytes)
        + _u32(round_.output_tokens)
        + _u64(round_.generated_code_bytes)
        + _u64(round_.output_capacity_bytes)
        + _u32(round_.output_metadata_capacity_bytes)
        + _u16(round_.program_id)
        + _u16(round_.profile_id)
        + _u64(round_.requested_profile_key)
        + _u8(round_.qos)
        + bytes.fromhex(round_.input_content_digest)
        + _stage(round_.compile)
        + _u8(round_.test is not None)
        + (_stage(round_.test) if round_.test is not None else b"")
        + _u8(round_.log_parse is not None)
        + (_stage(round_.log_parse) if round_.log_parse is not None else b"")
        + _u8(round_.prompt_tokens is not None)
        + _u32(round_.prompt_tokens or 0)
        + _u64(round_.prompt_bytes or 0)
        + _u8(round_.delta_prompt_tokens is not None)
        + _u32(round_.delta_prompt_tokens or 0)
        + _u64(round_.delta_prompt_bytes or 0)
    )
    return data


def _workload_section(workload: WorkloadPlan) -> bytes:
    data = _u32(len(workload.users))
    for user in workload.users:
        data += _u32(user.user_id) + _u32(len(user.tasks))
        for task in user.tasks:
            data += (
                _u32(task.task_seq)
                + _u64(task.think_time_ns)
                + _u64(task.session_id)
                + _u64(task.kv_handle)
                + _u32(task.initial_kv_generation)
                + _u16(task.effective_max_repair_rounds)
                + _u32(len(task.rounds))
            )
            for round_ in task.rounds:
                data += _round(round_)
    return data


def _command_section(document: dict) -> bytes:
    data = _u32(len(document["records"]))
    for record in document["records"]:
        data += (
            _u32(record["user_id"])
            + _u32(record["per_user_command_seq"])
            + _u64(record["request_id"])
            + _u8(_COMMAND_KIND[record["command_kind"]])
            + _u32(record["task_seq"])
            + _u16(record["repair_round_or_ffff"])
            + _u32(record["control_ordinal_or_zero"])
            + _u64(record["target_request_id_or_zero"])
            + _u64(record["session_id_or_zero"])
            + _u64(record["kv_handle_or_zero"])
            + _u32(record["generation_or_zero"])
        )
    return data


def _host_task_section(document: dict) -> bytes:
    data = _u32(len(document["records"]))
    for record in document["records"]:
        stage_kind = {"COMPILE": 0, "TEST": 1, "LOG_PARSE": 2}
        data += (
            _u32(record["user_id"])
            + _u32(record["task_seq"])
            + _u16(record["repair_round"])
            + _u8(stage_kind[record["stage_kind"]])
            + _u64(record["host_task_id"])
        )
    return data


def _arena_section(document: dict) -> bytes:
    arena_kind = {"INPUT": 0, "PARAMETER": 1, "OUTPUT": 2, "METADATA": 3}
    data = _u32(len(document["records"]))
    for record in document["records"]:
        data += (
            _u32(record["user_id"])
            + _u32(record["task_seq"])
            + _u16(record["repair_round_or_ffff"])
            + _u8(_COMMAND_KIND[record["command_kind"]])
            + _u8(arena_kind[record["arena_kind"]])
            + _u32(record["per_user_command_seq"])
            + _u64(record["request_id"])
            + _u64(record["base"])
            + _u64(record["allocation_bytes"])
            + _u64(record["initial_valid_bytes"])
            + _u32(record["alignment"])
        )
    return data


def _surrogate_section(registry: SurrogateProfileRegistry) -> bytes:
    data = bytes.fromhex(registry.digest) + _u32(len(registry.profiles))
    for profile in registry.profiles:
        data += (
            _u16(profile.program_id)
            + _u16(profile.profile_id)
            + _u64(profile.profile_key)
            + _u32(profile.input_tokens)
            + _u64(profile.input_bytes)
            + _u32(profile.output_tokens)
            + _u64(profile.output_bytes)
            + _u64(profile.service_ns)
            + _u32(profile.publish_chunk_bytes)
        )
    return data


def _control_section(control: ControlPlan) -> bytes:
    data = _u32(len(control.actions))
    for action in control.actions:
        trigger = action.trigger
        kind = trigger["kind"]
        anchored = kind in {
            "AFTER_SQ_ACCEPT",
            "AFTER_SESSION_ADMISSION",
            "AFTER_BATCH_FREEZE",
            "AFTER_FIRST_OUTPUT_CHUNK",
            "SAME_EDGE_AS_TERMINAL",
            "AFTER_GENERATE_TERMINAL",
        }
        after = kind == "AFTER_RELEASE_TERMINAL"
        data += (
            _u32(action.control_ordinal)
            + _u8(_COMMAND_KIND[action.opcode])
            + _u8(_TRIGGER_KIND[kind])
            + _u8(1 if anchored else 0)
            + _u32(trigger.get("event_user_id", 0))
            + _u32(trigger.get("event_task_seq", 0))
            + _u16(trigger.get("event_repair_round", 0))
            + _u8(1 if after else 0)
            + _u32(trigger.get("after_control_ordinal", 0))
            + _u32(action.issuer_user_id)
            + _u32(action.issuer_task_seq)
            + _u16(action.target_user_id or 0)
            + _u32(action.target_task_seq or 0)
            + _u16(action.target_repair_round or 0)
            + _u64(action.target_session_id or 0)
            + _u64(action.target_kv_handle or 0)
            + _u32(action.target_generation or 0)
        )
    return data


def build_agent_plan_image(
    workload: WorkloadPlan,
    control: ControlPlan | None,
    release_policy: str,
    regions,
    surrogate_registry: SurrogateProfileRegistry | None = None,
) -> tuple[bytes, str]:
    identity = build_command_identity_plan(workload, control, release_policy)
    host_tasks = build_host_task_identity_plan(workload)
    arena = build_host_arena_object_plan(workload, control, release_policy, regions)
    sections = [
        (_SECTION_WORKLOAD, _workload_section(workload)),
        (_SECTION_COMMAND_IDENTITY, _command_section(identity)),
        (_SECTION_HOST_TASK_IDENTITY, _host_task_section(host_tasks)),
        (_SECTION_ARENA, _arena_section(arena)),
    ]
    if surrogate_registry is not None:
        sections.append((_SECTION_SURROGATE, _surrogate_section(surrogate_registry)))
    if control is not None:
        sections.append((_SECTION_CONTROL, _control_section(control)))
    from mesh_ir.agent_workload import null_control_plan_digest

    body = (
        MAGIC
        + _u32(VERSION)
        + _u32(len(sections))
        + bytes.fromhex(workload.digest)
        + bytes.fromhex(
            control.digest
            if control is not None
            else null_control_plan_digest(workload.digest)
        )
    )
    for section_type, payload in sections:
        body += _u16(section_type) + _u64(len(payload)) + payload
    digest = hashlib.sha256(body).digest()
    return body + digest, digest.hex()

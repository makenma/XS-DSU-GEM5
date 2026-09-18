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
from mesh_ir.agent_workload import ControlPlan, PlanError, WorkloadPlan
from mesh_ir.host_bindings import request_binding_counts

MAGIC = b"AGPI"
VERSION = 1

_SECTION_WORKLOAD = 1
_SECTION_COMMAND_IDENTITY = 2
_SECTION_HOST_TASK_IDENTITY = 3
_SECTION_ARENA = 4
_SECTION_SURROGATE = 5
_SECTION_CONTROL = 6
_SECTION_REQUEST_BINDINGS = 7

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


def _read(data: bytes, offset: int, width: int) -> tuple[int, int]:
    formats = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}
    value = struct.unpack_from(formats[width], data, offset)[0]
    return value, offset + width


def _decode_stage(data: bytes, offset: int) -> tuple[dict, int]:
    value = {}
    value["nominal_ns"], offset = _read(data, offset, 8)
    value["host_tokens_required"], offset = _read(data, offset, 2)
    value["outcome"], offset = _read(data, offset, 1)
    value["read_bytes"], offset = _read(data, offset, 8)
    value["write_bytes"], offset = _read(data, offset, 8)
    value["raw_log_bytes"], offset = _read(data, offset, 8)
    value["excerpt_bytes"], offset = _read(data, offset, 8)
    value["excerpt_tokens"], offset = _read(data, offset, 4)
    return value, offset


def _decode_round(data: bytes, offset: int) -> tuple[dict, int]:
    value = {}
    value["workload_plan_item_id"], offset = _read(data, offset, 4)
    value["repair_round"], offset = _read(data, offset, 2)
    value["kv_policy"], offset = _read(data, offset, 1)
    value["has_deadline"], offset = _read(data, offset, 1)
    value["deadline_tick"], offset = _read(data, offset, 8)
    value["full_context_tokens"], offset = _read(data, offset, 4)
    value["full_context_bytes"], offset = _read(data, offset, 8)
    value["expected_cached_tokens"], offset = _read(data, offset, 4)
    value["expected_cached_context_bytes"], offset = _read(data, offset, 8)
    value["output_tokens"], offset = _read(data, offset, 4)
    value["generated_code_bytes"], offset = _read(data, offset, 8)
    value["output_capacity_bytes"], offset = _read(data, offset, 8)
    value["output_metadata_capacity_bytes"], offset = _read(data, offset, 4)
    value["program_id"], offset = _read(data, offset, 2)
    value["profile_id"], offset = _read(data, offset, 2)
    value["requested_profile_key"], offset = _read(data, offset, 8)
    value["qos"], offset = _read(data, offset, 1)
    value["input_content_digest"] = data[offset:offset + 32].hex()
    offset += 32
    value["compile"], offset = _decode_stage(data, offset)
    value["has_test"], offset = _read(data, offset, 1)
    if value["has_test"]:
        value["test"], offset = _decode_stage(data, offset)
    value["has_parse"], offset = _read(data, offset, 1)
    if value["has_parse"]:
        value["log_parse"], offset = _decode_stage(data, offset)
    value["has_prompt"], offset = _read(data, offset, 1)
    value["prompt_tokens"], offset = _read(data, offset, 4)
    value["prompt_bytes"], offset = _read(data, offset, 8)
    value["has_delta_prompt"], offset = _read(data, offset, 1)
    value["delta_prompt_tokens"], offset = _read(data, offset, 4)
    value["delta_prompt_bytes"], offset = _read(data, offset, 8)
    return value, offset


def _failed(path: str, reason: str) -> PlanError:
    return PlanError("E_AGENT_PLAN", path, reason)


def _compare(path: str, observed, expected) -> None:
    if observed != expected:
        raise _failed(path, "plan projection differs from the frozen workload: "
                            "%r != %r" % (observed, expected))


def _compare_stage(path: str, observed: dict, stage) -> None:
    _compare(path + ".nominal_ns", observed["nominal_ns"], stage.nominal_ns)
    _compare(path + ".host_tokens_required",
             observed["host_tokens_required"], stage.host_tokens_required)
    _compare(path + ".outcome", observed["outcome"], _OUTCOME[stage.outcome])
    _compare(path + ".read_bytes", observed["read_bytes"],
             stage.local_io.read_bytes)
    _compare(path + ".write_bytes", observed["write_bytes"],
             stage.local_io.write_bytes)
    _compare(path + ".raw_log_bytes", observed["raw_log_bytes"],
             stage.raw_log_bytes or 0)
    _compare(path + ".excerpt_bytes", observed["excerpt_bytes"],
             stage.excerpt_bytes or 0)
    _compare(path + ".excerpt_tokens", observed["excerpt_tokens"],
             stage.excerpt_tokens or 0)


def verify_workload_projection(workload: WorkloadPlan, blob: bytes) -> None:
    if len(blob) < 76 or blob[:4] != MAGIC:
        raise _failed("$", "plan image header is malformed")
    section_count = struct.unpack_from("<I", blob, 8)[0]
    offset = 76
    payload = None
    for _ in range(section_count):
        if offset + 10 > len(blob):
            raise _failed("$", "plan image section table is truncated")
        section_type = struct.unpack_from("<H", blob, offset)[0]
        length = struct.unpack_from("<Q", blob, offset + 2)[0]
        offset += 10
        if offset + length > len(blob):
            raise _failed("$", "plan image section escapes the image")
        if section_type == _SECTION_WORKLOAD:
            payload = blob[offset:offset + length]
        offset += length
    if payload is None:
        raise _failed("$", "plan image has no workload section")
    cursor = 0
    user_count, cursor = _read(payload, cursor, 4)
    _compare("$.users", user_count, len(workload.users))
    for user in workload.users:
        user_id, cursor = _read(payload, cursor, 4)
        _compare("$.users.user_id", user_id, user.user_id)
        task_count, cursor = _read(payload, cursor, 4)
        _compare("$.users.tasks", task_count, len(user.tasks))
        for task in user.tasks:
            task_seq, cursor = _read(payload, cursor, 4)
            _compare("$.tasks.task_seq", task_seq, task.task_seq)
            think_time, cursor = _read(payload, cursor, 8)
            _compare("$.tasks.think_time_ns", think_time, task.think_time_ns)
            session_id, cursor = _read(payload, cursor, 8)
            _compare("$.tasks.session_id", session_id, task.session_id)
            kv_handle, cursor = _read(payload, cursor, 8)
            _compare("$.tasks.kv_handle", kv_handle, task.kv_handle)
            generation, cursor = _read(payload, cursor, 4)
            _compare("$.tasks.initial_kv_generation", generation,
                     task.initial_kv_generation)
            repairs, cursor = _read(payload, cursor, 2)
            _compare("$.tasks.effective_max_repair_rounds", repairs,
                     task.effective_max_repair_rounds)
            round_count, cursor = _read(payload, cursor, 4)
            _compare("$.tasks.rounds", round_count, len(task.rounds))
            for index, round_ in enumerate(task.rounds):
                decoded, cursor = _decode_round(payload, cursor)
                prefix = "$.tasks.rounds[%d]" % index
                _compare(prefix + ".item", decoded["workload_plan_item_id"],
                         round_.workload_plan_item_id)
                _compare(prefix + ".repair_round", decoded["repair_round"],
                         round_.repair_round)
                _compare(prefix + ".kv_policy", decoded["kv_policy"],
                         _KV_POLICY[round_.kv_policy])
                _compare(prefix + ".has_deadline", decoded["has_deadline"],
                         1 if round_.deadline_tick is not None else 0)
                _compare(prefix + ".deadline_tick", decoded["deadline_tick"],
                         round_.deadline_tick or 0)
                _compare(prefix + ".full_context_tokens",
                         decoded["full_context_tokens"],
                         round_.full_context_tokens)
                _compare(prefix + ".full_context_bytes",
                         decoded["full_context_bytes"],
                         round_.full_context_bytes)
                _compare(prefix + ".cached_tokens",
                         decoded["expected_cached_tokens"],
                         round_.expected_cached_tokens)
                _compare(prefix + ".cached_context_bytes",
                         decoded["expected_cached_context_bytes"],
                         round_.expected_cached_context_bytes)
                _compare(prefix + ".output_tokens", decoded["output_tokens"],
                         round_.output_tokens)
                _compare(prefix + ".generated_code_bytes",
                         decoded["generated_code_bytes"],
                         round_.generated_code_bytes)
                _compare(prefix + ".output_capacity_bytes",
                         decoded["output_capacity_bytes"],
                         round_.output_capacity_bytes)
                _compare(prefix + ".output_metadata_capacity_bytes",
                         decoded["output_metadata_capacity_bytes"],
                         round_.output_metadata_capacity_bytes)
                _compare(prefix + ".program_id", decoded["program_id"],
                         round_.program_id)
                _compare(prefix + ".profile_id", decoded["profile_id"],
                         round_.profile_id)
                _compare(prefix + ".requested_profile_key",
                         decoded["requested_profile_key"],
                         round_.requested_profile_key)
                _compare(prefix + ".qos", decoded["qos"], round_.qos)
                _compare(prefix + ".input_content_digest",
                         decoded["input_content_digest"],
                         round_.input_content_digest)
                _compare_stage(prefix + ".compile", decoded["compile"],
                               round_.compile)
                _compare(prefix + ".has_test", decoded["has_test"],
                         1 if round_.test is not None else 0)
                if round_.test is not None:
                    _compare_stage(prefix + ".test", decoded["test"],
                                   round_.test)
                _compare(prefix + ".has_parse", decoded["has_parse"],
                         1 if round_.log_parse is not None else 0)
                if round_.log_parse is not None:
                    _compare_stage(prefix + ".log_parse",
                                   decoded["log_parse"], round_.log_parse)
                _compare(prefix + ".has_prompt", decoded["has_prompt"],
                         1 if round_.prompt_tokens is not None else 0)
                _compare(prefix + ".prompt_tokens", decoded["prompt_tokens"],
                         round_.prompt_tokens or 0)
                _compare(prefix + ".prompt_bytes", decoded["prompt_bytes"],
                         round_.prompt_bytes or 0)
                _compare(prefix + ".has_delta_prompt",
                         decoded["has_delta_prompt"],
                         1 if round_.delta_prompt_tokens is not None else 0)
                _compare(prefix + ".delta_prompt_tokens",
                         decoded["delta_prompt_tokens"],
                         round_.delta_prompt_tokens or 0)
                _compare(prefix + ".delta_prompt_bytes",
                         decoded["delta_prompt_bytes"],
                         round_.delta_prompt_bytes or 0)
    if cursor != len(payload):
        raise _failed("$.tasks.rounds", "plan projection has trailing bytes")


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


def encode_request_binding_section(kv_session_slot_bytes, plans) -> bytes:
    data = _u64(kv_session_slot_bytes) + _u32(len(plans))
    for plan in plans:
        data += (
            _u16(plan.program_id)
            + _u16(plan.profile_id)
            + _u32(plan.primary_input_symbol_id)
            + _u32(plan.primary_output_symbol_id)
            + _u32(plan.primary_kv_symbol_id)
            + _u32(len(plan.requirements))
            + _u32(plan.instance_count)
        )
        for requirement in plan.requirements:
            data += (
                _u32(requirement.symbol_id)
                + _u16(requirement.kind)
                + _u16(requirement.flags)
                + _u64(requirement.platform_address)
                + _u64(requirement.platform_bytes)
            )
    return data


def decode_request_binding_section(blob: bytes) -> dict:
    if len(blob) < 12:
        raise ValueError("request binding section is truncated")
    kv_session_slot_bytes = struct.unpack_from("<Q", blob, 0)[0]
    count = struct.unpack_from("<I", blob, 8)[0]
    offset = 12
    plans = []
    for _ in range(count):
        program_id, profile_id, primary_input, primary_output, primary_kv, \
            requirement_count, instance_count = struct.unpack_from(
                "<HHIIIII", blob, offset)
        offset += 24
        requirements = []
        for _ in range(requirement_count):
            symbol_id, kind, flags, address, bytes_ = struct.unpack_from(
                "<IHHQQ", blob, offset)
            offset += 24
            requirements.append({
                "symbol_id": symbol_id, "kind": kind, "flags": flags,
                "platform_address": address, "platform_bytes": bytes_,
            })
        plans.append({
            "program_id": program_id, "profile_id": profile_id,
            "primary_input_symbol_id": primary_input,
            "primary_output_symbol_id": primary_output,
            "primary_kv_symbol_id": primary_kv,
            "instance_count": instance_count,
            "requirements": requirements,
        })
    if offset != len(blob):
        raise ValueError("request binding section has trailing bytes")
    return {"kv_session_slot_bytes": kv_session_slot_bytes, "plans": plans}


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
    binding_plans=None,
    kv_session_slot_bytes: int = 0,
) -> tuple[bytes, str]:
    identity = build_command_identity_plan(workload, control, release_policy)
    host_tasks = build_host_task_identity_plan(workload)
    binding_counts = None
    if binding_plans:
        binding_counts = {
            (plan.program_id, plan.profile_id): len(plan.requirements)
            for plan in binding_plans
        }
    arena = build_host_arena_object_plan(workload, control, release_policy,
                                         regions, binding_counts)
    sections = [
        (_SECTION_WORKLOAD, _workload_section(workload)),
        (_SECTION_COMMAND_IDENTITY, _command_section(identity)),
        (_SECTION_HOST_TASK_IDENTITY, _host_task_section(host_tasks)),
        (_SECTION_ARENA, _arena_section(arena)),
    ]
    if binding_plans:
        sections.append((_SECTION_REQUEST_BINDINGS,
                         encode_request_binding_section(
                             kv_session_slot_bytes, binding_plans)))
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
    image = body + digest
    verify_workload_projection(workload, image)
    return image, digest.hex()

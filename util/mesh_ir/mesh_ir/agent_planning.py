from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mesh_ir.agent_workload import (
    ControlPlan,
    PlanError,
    WorkloadPlan,
    null_control_plan_digest,
    validate_against_schema,
)
from mesh_ir.model import canonical_json_bytes

_STAGE_KIND_ORDER = {"COMPILE": 0, "TEST": 1, "LOG_PARSE": 2}
_ARENA_KIND_ORDER = {"INPUT": 0, "PARAMETER": 1, "OUTPUT": 2, "METADATA": 3}
_PARAMETER_HEADER_BYTES = 160
_PARAMETER_TLV_BYTES = {
    "INPUT_DIGEST": 40,
    "WORKLOAD_ID_DIGEST": 40,
    "OUTPUT_CHUNK_BYTES": 16,
    "DEADLINE": 16,
}
_CONTROL_PARAMETER_BYTES = 160


def reachable_generate_keys(workload: WorkloadPlan) -> list[tuple[int, int, int]]:
    return [
        (user.user_id, task.task_seq, round_.repair_round)
        for user in workload.users
        for task in user.tasks
        for round_ in task.rounds
    ]


def reachable_stage_keys(workload: WorkloadPlan) -> list[tuple[int, int, int, str]]:
    keys = []
    for user in workload.users:
        for task in user.tasks:
            for round_ in task.rounds:
                keys.append((user.user_id, task.task_seq, round_.repair_round, "COMPILE"))
                if round_.test is not None:
                    keys.append((user.user_id, task.task_seq, round_.repair_round, "TEST"))
                if round_.log_parse is not None:
                    keys.append(
                        (user.user_id, task.task_seq, round_.repair_round, "LOG_PARSE")
                    )
    keys.sort(key=lambda key: (key[0], key[1], key[2], _STAGE_KIND_ORDER[key[3]]))
    return keys


def capacity_counts(
    workload: WorkloadPlan, control: ControlPlan | None, release_policy: str
) -> dict:
    if release_policy not in ("AUTO_PER_TASK", "EXPLICIT_ONLY"):
        raise PlanError("E_AGENT_PLAN", "release_policy", "unknown release policy")
    users = len(workload.users)
    generates = len(reachable_generate_keys(workload))
    task_tuples = {
        (task.session_id, task.kv_handle, task.initial_kv_generation)
        for user in workload.users
        for task in user.tasks
    }
    tasks_total = sum(len(user.tasks) for user in workload.users)
    cancel_actions = 0
    release_actions = 0
    release_tuples = set()
    if control is not None:
        for action in control.actions:
            if action.opcode == "CANCEL":
                cancel_actions += 1
            else:
                release_actions += 1
                release_tuples.add(
                    (action.target_session_id, action.target_kv_handle, action.target_generation)
                )
    auto_releases = tasks_total if release_policy == "AUTO_PER_TASK" else 0
    commands = generates + cancel_actions + release_actions + auto_releases
    raw_logs = 0
    excerpts = 0
    for user in workload.users:
        for task in user.tasks:
            for round_ in task.rounds:
                if round_.compile.outcome == "FAIL":
                    raw_logs += 1
                if round_.test is not None and round_.test.outcome == "FAIL":
                    raw_logs += 1
                if round_.log_parse is not None:
                    excerpts += 1
    return {
        "U": users,
        "G": generates,
        "C": cancel_actions,
        "L": release_actions,
        "A": auto_releases,
        "Q": commands,
        "S": len(task_tuples),
        "T": len(release_tuples | (task_tuples if auto_releases else set())),
        "O": generates + raw_logs + excerpts,
        "H": len(reachable_stage_keys(workload)),
        "K": 0,
        "ML": 0,
        "live_context_bound": min(commands, users + cancel_actions + release_actions),
    }


def _request_id(user_id: int, seq: int) -> int:
    if user_id > 0xFFFFFFFF or seq > 0xFFFFFFFF:
        raise PlanError("E_AGENT_PLAN", "$", "request_id bitfield overflow")
    return (user_id << 32) | seq


def _record(
    user_id: int,
    seq: int,
    kind: str,
    task_seq: int,
    repair_round_or_ffff: int,
    control_ordinal: int,
    target_request_id: int,
    session_id: int,
    kv_handle: int,
    generation: int,
) -> dict:
    return {
        "user_id": user_id,
        "per_user_command_seq": seq,
        "request_id": _request_id(user_id, seq),
        "command_kind": kind,
        "task_seq": task_seq,
        "repair_round_or_ffff": repair_round_or_ffff,
        "control_ordinal_or_zero": control_ordinal,
        "target_request_id_or_zero": target_request_id,
        "session_id_or_zero": session_id,
        "kv_handle_or_zero": kv_handle,
        "generation_or_zero": generation,
    }


def build_command_identity_plan(
    workload: WorkloadPlan, control: ControlPlan | None, release_policy: str
) -> dict:
    if release_policy not in ("AUTO_PER_TASK", "EXPLICIT_ONLY"):
        raise PlanError("E_AGENT_PLAN", "release_policy", "unknown release policy")
    generate_ids = {}
    for user in workload.users:
        seq = 0
        for task in user.tasks:
            for round_ in task.rounds:
                seq += 1
                generate_ids[
                    (user.user_id, task.task_seq, round_.repair_round)
                ] = _request_id(user.user_id, seq)
    control_by_user = {}
    if control is not None:
        for action in control.actions:
            control_by_user.setdefault(action.issuer_user_id, []).append(action)
    records = []
    for user in workload.users:
        seq = 0
        for task in user.tasks:
            for round_ in task.rounds:
                seq += 1
                records.append(
                    _record(
                        user.user_id,
                        seq,
                        "GENERATE",
                        task.task_seq,
                        round_.repair_round,
                        0,
                        0,
                        task.session_id,
                        task.kv_handle,
                        task.initial_kv_generation,
                    )
                )
        if release_policy == "AUTO_PER_TASK":
            for task in user.tasks:
                seq += 1
                records.append(
                    _record(
                        user.user_id,
                        seq,
                        "RELEASE_SESSION",
                        task.task_seq,
                        0xFFFF,
                        0,
                        0,
                        task.session_id,
                        task.kv_handle,
                        task.initial_kv_generation,
                    )
                )
        for action in sorted(
            control_by_user.get(user.user_id, ()), key=lambda item: item.control_ordinal
        ):
            seq += 1
            if action.opcode == "CANCEL":
                target = generate_ids.get(
                    (action.target_user_id, action.target_task_seq, action.target_repair_round)
                )
                if target is None:
                    raise PlanError(
                        "E_AGENT_PLAN",
                        "$",
                        "CANCEL target does not resolve to a planned GENERATE",
                    )
                records.append(
                    _record(
                        user.user_id,
                        seq,
                        "CANCEL",
                        action.issuer_task_seq,
                        action.target_repair_round,
                        action.control_ordinal,
                        target,
                        0,
                        0,
                        0,
                    )
                )
            else:
                records.append(
                    _record(
                        user.user_id,
                        seq,
                        "RELEASE_SESSION",
                        action.issuer_task_seq,
                        0xFFFF,
                        action.control_ordinal,
                        0,
                        action.target_session_id,
                        action.target_kv_handle,
                        action.target_generation,
                    )
                )
    document = {
        "schema": "command_identity_plan_v1",
        "version": 1,
        "workload_plan_digest": workload.digest,
        "control_plan_digest": (
            control.digest
            if control is not None
            else null_control_plan_digest(workload.digest)
        ),
        "release_policy": release_policy,
        "records": records,
    }
    document["command_identity_digest"] = hashlib.sha256(
        canonical_json_bytes(document)
    ).hexdigest()
    return document


def validate_command_identity_document(document) -> None:
    validate_against_schema("command_identity_plan_v1.schema.json", document)
    expected_seq = {}
    previous_user = None
    for index, record in enumerate(document["records"]):
        user_id = record["user_id"]
        seq = record["per_user_command_seq"]
        if seq != expected_seq.get(user_id, 0) + 1:
            raise PlanError(
                "E_AGENT_PLAN",
                f"records/{index}/per_user_command_seq",
                "must be dense from 1 per user",
            )
        expected_seq[user_id] = seq
        if previous_user is not None and user_id < previous_user:
            raise PlanError(
                "E_AGENT_PLAN",
                f"records/{index}/user_id",
                "records must be sorted by user then sequence",
            )
        previous_user = user_id
        if record["request_id"] != _request_id(user_id, seq):
            raise PlanError(
                "E_AGENT_PLAN",
                f"records/{index}/request_id",
                "must equal (user_id << 32) | per_user_command_seq",
            )
    body = {
        key: value for key, value in document.items() if key != "command_identity_digest"
    }
    digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    if document["command_identity_digest"] != digest:
        raise PlanError(
            "E_AGENT_PLAN", "command_identity_digest", "does not match canonical body"
        )


def build_host_task_identity_plan(workload: WorkloadPlan) -> dict:
    records = [
        {
            "user_id": user_id,
            "task_seq": task_seq,
            "repair_round": repair_round,
            "stage_kind": stage_kind,
            "host_task_id": ordinal,
        }
        for ordinal, (user_id, task_seq, repair_round, stage_kind) in enumerate(
            reachable_stage_keys(workload), start=1
        )
    ]
    document = {
        "schema": "host_task_identity_plan_v1",
        "version": 1,
        "workload_plan_digest": workload.digest,
        "records": records,
    }
    document["host_task_identity_digest"] = hashlib.sha256(
        canonical_json_bytes(document)
    ).hexdigest()
    return document


def parameter_block_bytes(round_) -> int:
    total = (
        _PARAMETER_HEADER_BYTES
        + _PARAMETER_TLV_BYTES["INPUT_DIGEST"]
        + _PARAMETER_TLV_BYTES["WORKLOAD_ID_DIGEST"]
        + _PARAMETER_TLV_BYTES["OUTPUT_CHUNK_BYTES"]
    )
    if round_.deadline_tick is not None:
        total += _PARAMETER_TLV_BYTES["DEADLINE"]
    return total


def _align_up(value: int, alignment: int) -> int:
    if alignment <= 0:
        raise PlanError("E_ADDRESS_PLAN", "alignment", "alignment must be positive")
    remainder = value % alignment
    return value if remainder == 0 else value + alignment - remainder


class ArenaAllocator:
    def __init__(self, base: int, size: int, alignment: int, label: str):
        self.base = base
        self.limit = base + size
        self.alignment = alignment
        self.label = label
        self.cursor = base

    def allocate(self, size: int) -> tuple[int, int]:
        base = _align_up(self.cursor, self.alignment)
        allocation = _align_up(size, self.alignment)
        if base < self.cursor or base + allocation < base:
            raise PlanError("E_ADDRESS_PLAN", self.label, "address arithmetic overflow")
        if base + allocation > self.limit:
            raise PlanError(
                "E_ADDRESS_PLAN",
                self.label,
                f"allocation of {allocation} bytes exceeds arena limit {self.limit:#x}",
            )
        self.cursor = base + allocation
        return base, allocation


@dataclass(frozen=True)
class ArenaRegion:
    kind: str
    base: int
    bytes: int
    alignment: int


def _check_disjoint(regions) -> None:
    intervals = sorted(
        (region.base, region.base + region.bytes, region.kind) for region in regions
    )
    for (_, previous_end, kind), (next_start, _, next_kind) in zip(
        intervals, intervals[1:]
    ):
        if next_start < previous_end:
            raise PlanError(
                "E_ADDRESS_PLAN",
                f"{kind}/{next_kind}",
                "arena regions must not overlap",
            )


def build_host_arena_object_plan(
    workload: WorkloadPlan,
    control: ControlPlan | None,
    release_policy: str,
    regions,
) -> dict:
    if {region.kind for region in regions} != set(_ARENA_KIND_ORDER):
        raise PlanError("E_ADDRESS_PLAN", "regions", "exactly four arena kinds required")
    _check_disjoint(regions)
    allocators = {
        region.kind: ArenaAllocator(region.base, region.bytes, region.alignment, region.kind)
        for region in regions
    }
    rounds_by_key = {
        (user.user_id, task.task_seq, round_.repair_round): round_
        for user in workload.users
        for task in user.tasks
        for round_ in task.rounds
    }
    identity = build_command_identity_plan(workload, control, release_policy)
    records = []
    for record in identity["records"]:
        key = (record["user_id"], record["task_seq"], record["repair_round_or_ffff"])
        round_ = rounds_by_key.get(key)
        if record["command_kind"] == "GENERATE":
            demands = (
                ("INPUT", round_.full_context_bytes, round_.full_context_bytes),
                ("PARAMETER", parameter_block_bytes(round_), parameter_block_bytes(round_)),
                ("OUTPUT", round_.output_capacity_bytes, 0),
                ("METADATA", round_.output_metadata_capacity_bytes, 0),
            )
        else:
            demands = (("PARAMETER", _CONTROL_PARAMETER_BYTES, _CONTROL_PARAMETER_BYTES),)
        for kind, size, initial_valid in sorted(
            demands, key=lambda demand: _ARENA_KIND_ORDER[demand[0]]
        ):
            base, allocation = allocators[kind].allocate(size)
            records.append(
                {
                    "user_id": record["user_id"],
                    "task_seq": record["task_seq"],
                    "repair_round_or_ffff": record["repair_round_or_ffff"],
                    "command_kind": record["command_kind"],
                    "arena_kind": kind,
                    "per_user_command_seq": record["per_user_command_seq"],
                    "request_id": record["request_id"],
                    "base": base,
                    "allocation_bytes": allocation,
                    "initial_valid_bytes": initial_valid,
                    "alignment": allocators[kind].alignment,
                }
            )
    document = {
        "schema": "host_arena_object_plan_v1",
        "version": 1,
        "workload_plan_digest": workload.digest,
        "control_plan_digest": identity["control_plan_digest"],
        "records": records,
    }
    document["host_arena_object_digest"] = hashlib.sha256(
        canonical_json_bytes(document)
    ).hexdigest()
    return document

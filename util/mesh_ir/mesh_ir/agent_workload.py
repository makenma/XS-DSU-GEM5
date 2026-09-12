from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from mesh_ir.acceptance import ContractError, schema_root
from mesh_ir.model import canonical_json_bytes

_U64_MAX = (1 << 53) - 1
_U64_HEX = re.compile(r"^0x([0-9a-f]{16})$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NULL_CONTROL_DOMAIN = b"AI_MESH_NULL_CONTROL_PLAN_V1\x00"
_TERMINAL_MANDATORY_TLV = ({"TIMING_BREAKDOWN"}, {"DIAGNOSTIC"})
_OUTPUT_TLV_PAYLOAD_BYTES = {
    "TIMING_BREAKDOWN": 64,
    "DIAGNOSTIC": 64,
    "ROUTE_DIGESTS": 64,
}
_ANCHORED_TRIGGERS = {
    "AFTER_SQ_ACCEPT",
    "AFTER_SESSION_ADMISSION",
    "AFTER_BATCH_FREEZE",
    "AFTER_FIRST_OUTPUT_CHUNK",
    "SAME_EDGE_AS_TERMINAL",
    "AFTER_GENERATE_TERMINAL",
}


class PlanError(ContractError):
    def __init__(self, code: str, path: str, reason: str):
        super().__init__(f"{code} {path}: {reason}")
        self.code = code
        self.path = path


def _error(path: str, reason: str) -> PlanError:
    return PlanError("E_AGENT_PLAN", path, reason)


def _pairs_without_duplicates(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise _error("$", f"duplicate JSON key {key!r}")
        document[key] = value
    return document


def _reject_constant(name):
    def reject(_value):
        raise _error("$", f"{name} is not valid JSON")

    return reject


def _walk_nfc(value, path):
    if isinstance(value, str):
        if value != unicodedata.normalize("NFC", value):
            raise _error(path, "string is not NFC normalized")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_nfc(item, f"{path}/{index}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk_nfc(item, f"{path}/{key}")


def _walk_u64_json(value, path):
    if isinstance(value, bool) or isinstance(value, int):
        if not isinstance(value, bool) and value > _U64_MAX:
            raise _error(path, "integer exceeds the exact JSON integer range")
        return
    if isinstance(value, str):
        match = _U64_HEX.match(value)
        if match and int(match.group(1), 16) <= _U64_MAX:
            raise _error(path, "u64-json at or below 2^53-1 must be a JSON integer")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_u64_json(item, f"{path}/{index}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk_u64_json(item, f"{path}/{key}")


def load_strict_json(path) -> object:
    try:
        text = Path(path).read_bytes().decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant("non-finite number"),
        )
    except PlanError:
        raise
    except UnicodeDecodeError as error:
        raise _error("$", f"invalid UTF-8: {error}") from error
    except json.JSONDecodeError as error:
        raise _error("$", f"malformed JSON: {error}") from error
    _walk_nfc(document, "$")
    _walk_u64_json(document, "$")
    return document


def u64_value(value) -> int:
    if isinstance(value, bool):
        raise _error("$", "boolean is not u64-json")
    if isinstance(value, int):
        if 0 <= value <= _U64_MAX:
            return value
        raise _error("$", "integer exceeds the exact JSON integer range")
    match = _U64_HEX.match(value) if isinstance(value, str) else None
    if match:
        return int(match.group(1), 16)
    raise _error("$", "non-canonical u64-json value")


def validate_against_schema(filename: str, document) -> None:
    schema = json.loads((schema_root() / filename).read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        path = "/".join(str(part) for part in first.absolute_path)
        raise PlanError("E_AGENT_PLAN", path or "$", first.message)


def worst_metadata_bytes() -> int:
    return 128 + max(
        sum(8 + _OUTPUT_TLV_PAYLOAD_BYTES[name] for name in terminal)
        for terminal in _TERMINAL_MANDATORY_TLV
    )


def workload_plan_digest(document) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def control_plan_digest_of(document) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def null_control_plan_digest(workload_digest: str) -> str:
    return hashlib.sha256(
        _NULL_CONTROL_DOMAIN + bytes.fromhex(workload_digest)
    ).hexdigest()


def _check_round_equations(base: str, round_, previous) -> None:
    repair = round_["repair_round"]
    full_tokens = round_["full_context_tokens"]
    full_bytes = u64_value(round_["full_context_bytes"])
    cached_tokens = round_["expected_cached_tokens"]
    cached_bytes = u64_value(round_["expected_cached_context_bytes"])
    if repair == 0:
        if round_["kv_policy"] != "INITIAL":
            raise _error(f"{base}/kv_policy", "round 0 must use INITIAL")
        if cached_tokens != 0:
            raise _error(f"{base}/expected_cached_tokens", "round 0 must not expect cached tokens")
        if cached_bytes != 0:
            raise _error(f"{base}/expected_cached_context_bytes", "round 0 must not expect cached bytes")
        if full_tokens != round_["prompt_tokens"]:
            raise _error(f"{base}/full_context_tokens", "round 0 full context must equal prompt tokens")
        if full_bytes != u64_value(round_["prompt_bytes"]):
            raise _error(f"{base}/full_context_bytes", "round 0 full context must equal prompt bytes")
    else:
        if round_["kv_policy"] == "INITIAL":
            raise _error(f"{base}/kv_policy", "repair round must not use INITIAL")
        if full_tokens != cached_tokens + round_["delta_prompt_tokens"]:
            raise _error(f"{base}/full_context_tokens", "full context tokens must equal cached plus delta")
        if full_bytes != cached_bytes + u64_value(round_["delta_prompt_bytes"]):
            raise _error(f"{base}/full_context_bytes", "full context bytes must equal cached plus delta")
        if cached_tokens != previous["kv_required_tokens_after_round"]:
            raise _error(
                f"{base}/expected_cached_tokens",
                "must equal previous round kv_required_tokens_after_round",
            )
        if cached_bytes != u64_value(previous["full_context_bytes"]):
            raise _error(
                f"{base}/expected_cached_context_bytes",
                "must equal previous round full_context_bytes",
            )
    if round_["kv_required_tokens_after_round"] != full_tokens + round_["output_tokens"]:
        raise _error(
            f"{base}/kv_required_tokens_after_round",
            "must equal full context tokens plus output tokens",
        )
    if u64_value(round_["output_capacity_bytes"]) < u64_value(round_["generated_code_bytes"]):
        raise _error(f"{base}/output_capacity_bytes", "must cover generated_code_bytes")
    worst = worst_metadata_bytes()
    if round_["output_metadata_capacity_bytes"] < worst:
        raise _error(
            f"{base}/output_metadata_capacity_bytes",
            f"must cover worst terminal metadata bytes {worst}",
        )


def _check_stage_paths(base: str, round_, cap: int, is_last: bool) -> None:
    compile_stage = round_["compile"]
    test = round_.get("test")
    parse = round_.get("log_parse")
    at_cap = round_["repair_round"] == cap
    if compile_stage["outcome"] == "FAIL":
        if test is not None:
            raise _error(f"{base}/test", "forbidden when compile outcome is FAIL")
    elif test is None:
        raise _error(f"{base}/test", "required when compile succeeds")
    failing = compile_stage["outcome"] == "FAIL" or (
        test is not None and test["outcome"] == "FAIL"
    )
    if failing and not at_cap:
        if parse is None:
            raise _error(f"{base}/log_parse", "required while repair budget remains")
        if is_last:
            raise _error(
                base,
                f"failing repair_round {round_['repair_round']} must be followed by a repair round",
            )
    else:
        if parse is not None:
            raise _error(f"{base}/log_parse", "forbidden on terminal or at-cap round")
        if not failing and not is_last:
            raise _error(
                base,
                f"successful repair_round {round_['repair_round']} must be the last round",
            )


def _check_stage_local_io(base: str, round_) -> None:
    compile_stage = round_["compile"]
    generated = u64_value(round_["generated_code_bytes"])
    if u64_value(compile_stage["local_io"]["read_bytes"]) < generated:
        raise _error(f"{base}/compile/local_io/read_bytes", "must cover generated_code_bytes")
    if compile_stage["outcome"] == "FAIL" and u64_value(
        compile_stage["local_io"]["write_bytes"]
    ) < u64_value(compile_stage["raw_log_bytes"]):
        raise _error(f"{base}/compile/local_io/write_bytes", "must cover raw_log_bytes")
    test = round_.get("test")
    if (
        test is not None
        and test["outcome"] == "FAIL"
        and u64_value(test["local_io"]["write_bytes"]) < u64_value(test["raw_log_bytes"])
    ):
        raise _error(f"{base}/test/local_io/write_bytes", "must cover raw_log_bytes")
    parse = round_.get("log_parse")
    if parse is not None:
        if u64_value(parse["local_io"]["read_bytes"]) < u64_value(parse["raw_log_bytes"]):
            raise _error(f"{base}/log_parse/local_io/read_bytes", "must cover raw_log_bytes")
        if u64_value(parse["local_io"]["write_bytes"]) < u64_value(parse["excerpt_bytes"]):
            raise _error(f"{base}/log_parse/local_io/write_bytes", "must cover excerpt_bytes")


def _semantic_workload(document) -> None:
    if not document["plan_id"].strip():
        raise _error("plan_id", "blank after whitespace normalization")
    item_ids = set()
    session_ids = {}
    kv_handles = {}
    for user_index, user in enumerate(document["users"]):
        if user["user_id"] != user_index:
            raise _error(f"users/{user_index}/user_id", "users must be dense from 0")
        for task_index, task in enumerate(user["tasks"]):
            task_base = f"users/{user_index}/tasks/{task_index}"
            if task["task_seq"] != task_index:
                raise _error(f"{task_base}/task_seq", "tasks must be dense from 0")
            if not task["task_class"].strip():
                raise _error(f"{task_base}/task_class", "blank after whitespace normalization")
            for field, seen in (("session_id", session_ids), ("kv_handle", kv_handles)):
                value = u64_value(task[field])
                if value == 0:
                    raise _error(f"{task_base}/{field}", "must be nonzero")
                if value in seen:
                    raise _error(f"{task_base}/{field}", f"duplicates {seen[value]}")
                seen[value] = task_base
            cap = task.get("max_repair_rounds", document["max_repair_rounds"])
            rounds = task["rounds"]
            if len(rounds) > cap + 1:
                raise _error(
                    f"{task_base}/rounds",
                    f"repair_round {cap + 1} exceeds max_repair_rounds {cap}",
                )
            program_id = rounds[0]["program_id"]
            for round_index, round_ in enumerate(rounds):
                base = f"{task_base}/rounds/{round_index}"
                if round_["repair_round"] != round_index:
                    raise _error(f"{base}/repair_round", "rounds must be dense from 0")
                item = round_["workload_plan_item_id"]
                if item in item_ids:
                    raise _error(f"{base}/workload_plan_item_id", f"duplicates item {item}")
                item_ids.add(item)
                if round_["program_id"] != program_id:
                    raise _error(f"{base}/program_id", "must be identical across task rounds")
                previous = rounds[round_index - 1] if round_index else None
                _check_round_equations(base, round_, previous)
                _check_stage_paths(base, round_, cap, round_index == len(rounds) - 1)
                _check_stage_local_io(base, round_)


def _semantic_control(document, workload) -> None:
    if document["workload_plan_digest"] != workload.digest:
        raise _error("workload_plan_digest", "does not match the loaded workload plan")
    user_ids = {user.user_id for user in workload.users}
    tasks = {
        (user.user_id, task.task_seq) for user in workload.users for task in user.tasks
    }
    generates = {
        (user.user_id, task.task_seq, round_.repair_round)
        for user in workload.users
        for task in user.tasks
        for round_ in task.rounds
    }
    actions = document["actions"]
    by_ordinal = {action["control_ordinal"]: action for action in actions}
    for index, action in enumerate(actions):
        if action["control_ordinal"] != index + 1:
            raise _error(f"actions/{index}/control_ordinal", "ordinals must be dense from 1")
    cancel_targets = {}
    for index, action in enumerate(actions):
        base = f"actions/{index}"
        if action["issuer_user_id"] not in user_ids:
            raise _error(f"{base}/issuer_user_id", "issuer user not in workload plan")
        if (action["issuer_user_id"], action["issuer_task_seq"]) not in tasks:
            raise _error(f"{base}/issuer_task_seq", "issuer task not in workload plan")
        trigger = action["trigger"]
        if trigger["kind"] in _ANCHORED_TRIGGERS:
            anchor_user = trigger["event_user_id"]
            if anchor_user not in user_ids:
                raise _error(f"{base}/trigger/event_user_id", "anchor user not in workload plan")
            if (anchor_user, trigger["event_task_seq"]) not in tasks:
                raise _error(f"{base}/trigger/event_task_seq", "anchor task not in workload plan")
            if (
                anchor_user,
                trigger["event_task_seq"],
                trigger["event_repair_round"],
            ) not in generates:
                raise _error(
                    f"{base}/trigger/event_repair_round",
                    "anchor does not resolve to a reachable GENERATE",
                )
        if trigger["kind"] == "AFTER_RELEASE_TERMINAL":
            referenced = by_ordinal.get(trigger["after_control_ordinal"])
            if (
                referenced is None
                or referenced["control_ordinal"] >= action["control_ordinal"]
                or referenced["opcode"] != "RELEASE_SESSION"
            ):
                raise _error(
                    f"{base}/trigger/after_control_ordinal",
                    "must reference an earlier RELEASE_SESSION action",
                )
        if action["opcode"] == "CANCEL":
            if action["target_user_id"] not in user_ids:
                raise _error(f"{base}/target_user_id", "CANCEL target user not in workload plan")
            if (action["target_user_id"], action["target_task_seq"]) not in tasks:
                raise _error(f"{base}/target_task_seq", "CANCEL target task not in workload plan")
            target = (
                action["target_user_id"],
                action["target_task_seq"],
                action["target_repair_round"],
            )
            if target not in generates:
                raise _error(
                    f"{base}/target_repair_round",
                    "CANCEL target does not resolve to a reachable GENERATE",
                )
            if target in cancel_targets:
                raise _error(
                    f"{base}/target_task_seq",
                    f"duplicate CANCEL target already claimed by ordinal {cancel_targets[target]}",
                )
            cancel_targets[target] = action["control_ordinal"]


@dataclass(frozen=True)
class HostLocalIoPlan:
    read_bytes: int
    write_bytes: int


@dataclass(frozen=True)
class HostStagePlan:
    kind: str
    nominal_ns: int
    host_tokens_required: int
    outcome: str
    local_io: HostLocalIoPlan
    raw_log_bytes: int | None = None
    excerpt_bytes: int | None = None
    excerpt_tokens: int | None = None


@dataclass(frozen=True)
class RoundPlan:
    workload_plan_item_id: int
    repair_round: int
    batch_replay: bool
    logical_source_rank: int
    full_context_tokens: int
    full_context_bytes: int
    expected_cached_tokens: int
    expected_cached_context_bytes: int
    input_content_digest: str
    output_tokens: int
    generated_code_bytes: int
    output_capacity_bytes: int
    output_metadata_capacity_bytes: int
    kv_required_tokens_after_round: int
    kv_policy: str
    program_id: int
    profile_id: int
    requested_profile_key: int
    qos: int
    compile: HostStagePlan
    test: HostStagePlan | None
    log_parse: HostStagePlan | None
    deadline_tick: int | None = None
    prompt_tokens: int | None = None
    prompt_bytes: int | None = None
    delta_prompt_tokens: int | None = None
    delta_prompt_bytes: int | None = None


@dataclass(frozen=True)
class TaskPlan:
    task_seq: int
    task_class: str
    think_time_ns: int
    session_id: int
    kv_handle: int
    initial_kv_generation: int
    max_repair_rounds: int
    rounds: tuple[RoundPlan, ...]

    @property
    def effective_max_repair_rounds(self) -> int:
        return self.max_repair_rounds


@dataclass(frozen=True)
class UserPlan:
    user_id: int
    tasks: tuple[TaskPlan, ...]


@dataclass(frozen=True)
class WorkloadPlan:
    plan_id: str
    max_repair_rounds: int
    users: tuple[UserPlan, ...]
    digest: str
    document: dict


@dataclass(frozen=True)
class ControlAction:
    control_ordinal: int
    opcode: str
    trigger: dict
    issuer_user_id: int
    issuer_task_seq: int
    target_user_id: int | None = None
    target_task_seq: int | None = None
    target_repair_round: int | None = None
    target_session_id: int | None = None
    target_kv_handle: int | None = None
    target_generation: int | None = None


@dataclass(frozen=True)
class ControlPlan:
    workload_plan_digest: str
    actions: tuple[ControlAction, ...]
    digest: str
    document: dict


def _stage(document_stage) -> HostStagePlan:
    local = document_stage["local_io"]
    return HostStagePlan(
        kind=document_stage["kind"],
        nominal_ns=u64_value(document_stage["nominal_ns"]),
        host_tokens_required=document_stage["host_tokens_required"],
        outcome=document_stage["outcome"],
        local_io=HostLocalIoPlan(
            read_bytes=u64_value(local["read_bytes"]),
            write_bytes=u64_value(local["write_bytes"]),
        ),
        raw_log_bytes=(
            u64_value(document_stage["raw_log_bytes"])
            if "raw_log_bytes" in document_stage
            else None
        ),
        excerpt_bytes=(
            u64_value(document_stage["excerpt_bytes"])
            if "excerpt_bytes" in document_stage
            else None
        ),
        excerpt_tokens=document_stage.get("excerpt_tokens"),
    )


def _round(document_round) -> RoundPlan:
    return RoundPlan(
        workload_plan_item_id=document_round["workload_plan_item_id"],
        repair_round=document_round["repair_round"],
        batch_replay=document_round["batch_replay"],
        logical_source_rank=document_round["logical_source_rank"],
        full_context_tokens=document_round["full_context_tokens"],
        full_context_bytes=u64_value(document_round["full_context_bytes"]),
        expected_cached_tokens=document_round["expected_cached_tokens"],
        expected_cached_context_bytes=u64_value(
            document_round["expected_cached_context_bytes"]
        ),
        input_content_digest=document_round["input_content_digest"],
        output_tokens=document_round["output_tokens"],
        generated_code_bytes=u64_value(document_round["generated_code_bytes"]),
        output_capacity_bytes=u64_value(document_round["output_capacity_bytes"]),
        output_metadata_capacity_bytes=document_round["output_metadata_capacity_bytes"],
        kv_required_tokens_after_round=document_round[
            "kv_required_tokens_after_round"
        ],
        kv_policy=document_round["kv_policy"],
        program_id=document_round["program_id"],
        profile_id=document_round["profile_id"],
        requested_profile_key=u64_value(document_round["requested_profile_key"]),
        qos=document_round["qos"],
        compile=_stage(document_round["compile"]),
        test=_stage(document_round["test"]) if "test" in document_round else None,
        log_parse=(
            _stage(document_round["log_parse"])
            if "log_parse" in document_round
            else None
        ),
        deadline_tick=(
            u64_value(document_round["deadline_tick"])
            if "deadline_tick" in document_round
            else None
        ),
        prompt_tokens=document_round.get("prompt_tokens"),
        prompt_bytes=(
            u64_value(document_round["prompt_bytes"])
            if "prompt_bytes" in document_round
            else None
        ),
        delta_prompt_tokens=document_round.get("delta_prompt_tokens"),
        delta_prompt_bytes=(
            u64_value(document_round["delta_prompt_bytes"])
            if "delta_prompt_bytes" in document_round
            else None
        ),
    )


def _task(document_task, header_cap: int) -> TaskPlan:
    return TaskPlan(
        task_seq=document_task["task_seq"],
        task_class=document_task["task_class"],
        think_time_ns=u64_value(document_task["think_time_ns"]),
        session_id=u64_value(document_task["session_id"]),
        kv_handle=u64_value(document_task["kv_handle"]),
        initial_kv_generation=document_task["initial_kv_generation"],
        max_repair_rounds=document_task.get("max_repair_rounds", header_cap),
        rounds=tuple(_round(round_) for round_ in document_task["rounds"]),
    )


def _build_workload(document) -> WorkloadPlan:
    return WorkloadPlan(
        plan_id=document["plan_id"],
        max_repair_rounds=document["max_repair_rounds"],
        users=tuple(
            UserPlan(
                user_id=user["user_id"],
                tasks=tuple(_task(task, document["max_repair_rounds"]) for task in user["tasks"]),
            )
            for user in document["users"]
        ),
        digest=workload_plan_digest(document),
        document=document,
    )


def _action(document_action) -> ControlAction:
    return ControlAction(
        control_ordinal=document_action["control_ordinal"],
        opcode=document_action["opcode"],
        trigger=dict(document_action["trigger"]),
        issuer_user_id=document_action["issuer_user_id"],
        issuer_task_seq=document_action["issuer_task_seq"],
        target_user_id=document_action.get("target_user_id"),
        target_task_seq=document_action.get("target_task_seq"),
        target_repair_round=document_action.get("target_repair_round"),
        target_session_id=(
            u64_value(document_action["target_session_id"])
            if "target_session_id" in document_action
            else None
        ),
        target_kv_handle=(
            u64_value(document_action["target_kv_handle"])
            if "target_kv_handle" in document_action
            else None
        ),
        target_generation=document_action.get("target_generation"),
    )


def validate_workload_document(document) -> None:
    validate_against_schema("agent_workload_plan_v1.schema.json", document)
    _semantic_workload(document)


def validate_control_document(document, workload: WorkloadPlan) -> None:
    validate_against_schema("agent_control_plan_v1.schema.json", document)
    _semantic_control(document, workload)


def load_workload_plan(path) -> WorkloadPlan:
    document = load_strict_json(path)
    validate_workload_document(document)
    return _build_workload(document)


def load_control_plan(path, workload: WorkloadPlan) -> ControlPlan:
    document = load_strict_json(path)
    validate_control_document(document, workload)
    return ControlPlan(
        workload_plan_digest=document["workload_plan_digest"],
        actions=tuple(_action(action) for action in document["actions"]),
        digest=control_plan_digest_of(document),
        document=document,
    )

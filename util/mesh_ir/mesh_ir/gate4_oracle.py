from __future__ import annotations

from dataclasses import dataclass, field

from mesh_ir.agent_planning import (
    ArenaRegion,
    build_command_identity_plan,
    build_host_arena_object_plan,
)
from mesh_ir.agent_surrogate import SurrogateProfileRegistry
from mesh_ir.agent_workload import (
    ControlPlan,
    WorkloadPlan,
    worst_metadata_bytes,
)
from mesh_ir.gate3_oracle import parse_facts_tsv


class Gate4OracleError(ValueError):
    pass


ORACLE_CHECKS = (
    "task_outcome_closure",
    "command_set_closure",
    "stage_order",
    "stage_duration_bound",
    "byte_conservation",
    "cq_conservation",
    "control_outcome_legality",
    "irq_before_cq_consume",
    "traffic_conservation",
)

TICKS_PER_NS = 1000
CONTROL_BYTES = 8
SQ_ENTRY_BYTES = 64
CQ_ENTRY_BYTES = 32
CONTROL_PARAMETER_BYTES = 160
DATA_BUS_BYTES = 64
MAX_BURST_BEATS = 256

CQ_STATUS_NAMES = {
    0: "SUCCESS",
    1: "CANCELLED",
    2: "NOT_FOUND",
    3: "ALREADY_TERMINAL",
    4: "PARAM_ERROR",
    5: "STALE_GENERATION",
}


@dataclass(frozen=True)
class Gate4Faults:
    output_b_error_request: int = 0
    output_b_error_segment: int = 0
    control_doorbell_error_ordinal: int = 0
    mutate_cancel_command_ordinal: int = 0
    mutate_cancel_command_status: int = 0
    host_fault_site: str = ""
    host_fault_task: int = -1
    host_fault_round: int = -1

    def expect_fatal(self) -> bool:
        return self.mutate_cancel_command_ordinal != 0

TRAFFIC_CLASSES = (
    "AGENT_TO_NPU_CONTROL",
    "NPU_TO_AGENT_CONTROL",
    "NPU_READ_AGENT_MEMORY",
    "NPU_WRITE_AGENT_MEMORY",
    "NPU_LOCAL_MEMORY",
    "NPU_P2P",
)


@dataclass(frozen=True)
class Gate4Execution:
    workload: WorkloadPlan
    control: ControlPlan | None
    surrogate: SurrogateProfileRegistry
    regions: tuple[ArenaRegion, ...]
    faults: Gate4Faults = field(default_factory=Gate4Faults)
    partial_commands: bool = False
    cancel_output_prefix_bytes: int = 0


@dataclass
class PlannedCommand:
    request_id: int
    user_id: int
    task_seq: int
    repair_round: int
    kind: str
    cq_status: str


@dataclass
class PlannedStage:
    request_id: int
    kind: str
    nominal_ns: int
    ran_to_done: bool


@dataclass
class PlannedTask:
    user_id: int
    task_seq: int
    terminal_request_id: int
    outcome: str
    commands: list
    stages: list


ARENA_FIELDS = (
    ("INPUT", "input_arena"),
    ("PARAMETER", "parameter_arena"),
    ("OUTPUT", "output_arena"),
    ("METADATA", "metadata_arena"),
)


def arena_regions(address_map) -> tuple[ArenaRegion, ...]:
    return tuple(
        ArenaRegion(
            kind,
            address_map[field]["base"],
            address_map[field]["bytes"],
            address_map["arena_alignment_bytes"],
        )
        for kind, field in ARENA_FIELDS
    )


def plan_burst_sizes(address: int, length: int) -> list[int]:
    remaining = length
    current = address
    sizes = []
    while remaining != 0:
        beat = 1
        while (
            beat <= DATA_BUS_BYTES // 2
            and beat <= remaining // 2
            and current % (beat * 2) == 0
        ):
            beat *= 2
        page_beats = (4096 - (current & 0xFFF)) // beat
        beats = min(MAX_BURST_BEATS, page_beats, remaining // beat)
        if beat < DATA_BUS_BYTES and current % (beat * 2) != 0:
            beats = min(beats, 1)
        if beats == 0:
            raise Gate4OracleError("AXI plan made no progress")
        sizes.append(beats * beat)
        current += sizes[-1]
        remaining -= sizes[-1]
    return sizes


def plan_bursts(address: int, length: int) -> int:
    return len(plan_burst_sizes(address, length))


def _cancel_join_winner(command_status: str, target_status: str):
    if command_status == "SUCCESS" and target_status == "CANCELLED":
        return "CANCEL_WINS"
    if command_status == "ALREADY_TERMINAL":
        if target_status == "SUCCESS":
            return "TARGET_SUCCESS_WINS"
        if target_status not in ("SUCCESS", "CANCELLED"):
            return "TARGET_ERROR_WINS"
    return None


FACT_NEVER = 1 << 62


class FactTimeline:
    """First observation tick of each (kind, request) pair.

    Comparisons are tick-only: inside one Host edge the driver handles CQ
    visibility before a new cancel intent, and the NPU resolves a CANCEL only
    after accepting its SQ entry, so equal ticks count as "already happened"
    for the target leg and as "already accepted" for the command leg.
    """

    def __init__(self, events):
        self._first = {}
        for event in events or ():
            request_id = event["request_id"]
            if request_id is None:
                continue
            key = (event["kind"], request_id)
            tick = event["tick"]
            if key not in self._first or tick < self._first[key]:
                self._first[key] = tick

    def at(self, kind: str, request_id: int):
        return self._first.get((kind, request_id), FACT_NEVER)


@dataclass(frozen=True)
class CancelLegs:
    join: bool
    command_status: str
    winner: str | None


def _planned_terminal_status(faults: Gate4Faults, request_id: int) -> str:
    if faults.output_b_error_request == request_id:
        return "ERROR"
    return "SUCCESS"


def _cancel_legs(timeline, target_request_id, command_request_id,
                 local_submit_failed, cut, target_status):
    accepted = timeline.at("SQ_CONSUME", command_request_id)
    if local_submit_failed:
        if accepted != FACT_NEVER:
            raise Gate4OracleError(
                "cancel command reached the NPU although its local submit rollback "
                "was configured"
            )
        return CancelLegs(False, "LOCAL_SUBMIT_FAILED", None)
    if accepted == FACT_NEVER:
        if cut:
            return None
        raise Gate4OracleError(
            "cancel command never accepted by the NPU in facts"
        )
    prepared = timeline.at("LOCAL_VISIBLE", command_request_id)
    if prepared == FACT_NEVER:
        raise Gate4OracleError(
            "cancel command has no local commit evidence in facts"
        )
    if accepted < prepared:
        raise Gate4OracleError(
            "cancel command was accepted by the NPU before its local commit in facts"
        )
    latched = timeline.at("GENERATE_TERMINAL_LATCHED", target_request_id)
    published = timeline.at("PUBLICATION_COMMIT", target_request_id) <= prepared
    consumed = timeline.at("CQ_CONSUME", target_request_id) <= prepared
    join = published and not consumed
    if timeline.at("SQ_CONSUME", target_request_id) > accepted:
        command_status = "NOT_FOUND"
    elif latched <= accepted:
        command_status = "ALREADY_TERMINAL"
    else:
        command_status = "SUCCESS"
    if join and command_status == "NOT_FOUND":
        raise Gate4OracleError(
            "published cancel target is unknown to the NPU when the command is "
            "accepted"
        )
    if not join:
        if command_status == "SUCCESS":
            raise Gate4OracleError(
                "cancel command wins against a target outside the cancel window"
            )
        return CancelLegs(False, command_status, None)
    if command_status == "SUCCESS":
        return CancelLegs(True, "SUCCESS", "CANCEL_WINS")
    winner = _cancel_join_winner(command_status, target_status)
    if winner is None:
        raise Gate4OracleError("cancel join legs have no legal winner")
    return CancelLegs(True, command_status, winner)


class PlannedWalk:
    def __init__(self, execution: Gate4Execution, command_filter=None,
                 facts_events=None, cut=False):
        self.execution = execution
        self.workload = execution.workload
        self.faults = execution.faults
        self.command_filter = command_filter
        self.timeline = FactTimeline(facts_events)
        self.cut = cut
        self.identity = build_command_identity_plan(
            execution.workload, execution.control, "EXPLICIT_ONLY"
        )
        self.arena = build_host_arena_object_plan(
            execution.workload,
            execution.control,
            "EXPLICIT_ONLY",
            execution.regions,
        )
        self.records = {
            record["request_id"]: record
            for record in self.identity["records"]
        }
        self.rounds_by_key = {
            (user.user_id, task.task_seq, round_.repair_round): round_
            for user in self.workload.users
            for task in user.tasks
            for round_ in task.rounds
        }
        self.arena_by_request = {}
        for record in self.arena["records"]:
            self.arena_by_request.setdefault(record["request_id"], {})[
                record["arena_kind"]
            ] = record
        self.tasks: list[PlannedTask] = []
        self.controls: list[dict] = []
        self._require_generate_lifetimes()
        self._walk()
        self.fatalCommandId = next(
            (
                entry["request_id"]
                for entry in self.controls
                if entry.get("fatal_illegal")
            ),
            None,
        )

    def _require_generate_lifetimes(self) -> None:
        for record in self.identity["records"]:
            if record["command_kind"] != "GENERATE":
                continue
            request_id = record["request_id"]
            assigned = self.timeline.at("CQ_ASSIGN", request_id)
            consumed = self.timeline.at("CQ_CONSUME", request_id)
            if assigned == FACT_NEVER and consumed == FACT_NEVER:
                continue
            latched = self.timeline.at("GENERATE_TERMINAL_LATCHED", request_id)
            if latched == FACT_NEVER:
                raise Gate4OracleError(
                    "generate reached a CQ without its terminal latch in facts"
                )
            if assigned == FACT_NEVER:
                raise Gate4OracleError(
                    "generate CQ was consumed without an assignment in facts"
                )
            if not latched <= assigned <= consumed:
                raise Gate4OracleError(
                    "generate terminal latch, CQ assignment and CQ consume are out "
                    "of causal order in facts"
                )

    def _generate_id(self, user_id: int, task_seq: int, repair_round: int) -> int:
        for record in self.identity["records"]:
            if (
                record["user_id"] == user_id
                and record["task_seq"] == task_seq
                and record["repair_round_or_ffff"] == repair_round
                and record["command_kind"] == "GENERATE"
            ):
                return record["request_id"]
        raise Gate4OracleError("planned generate is absent from the identity plan")

    def _host_fault_at(self, user_id: int, task_seq: int, repair_round: int) -> bool:
        return (
            self.faults.host_fault_site != ""
            and self.faults.host_fault_task == task_seq
            and self.faults.host_fault_round == repair_round
            and user_id == 0
        )

    def _walk(self) -> None:
        cancel_targets = {}
        for action in (
            self.execution.control.actions if self.execution.control else ()
        ):
            entry = {
                "action": action,
                "request_id": None,
                "status": None,
                "winner": None,
                "target_request_id": None,
            }
            record = next(
                (
                    record
                    for record in self.identity["records"]
                    if record["command_kind"] != "GENERATE"
                    and record["control_ordinal_or_zero"] == action.control_ordinal
                ),
                None,
            )
            if record is None:
                raise Gate4OracleError("control action is absent from identity plan")
            entry["request_id"] = record["request_id"]
            if action.opcode != "CANCEL":
                entry["status"] = "NOT_FOUND"
                entry["join"] = False
                self.controls.append(entry)
                continue
            entry["target_request_id"] = self._generate_id(
                action.target_user_id,
                action.target_task_seq,
                action.target_repair_round,
            )
            legs = _cancel_legs(
                self.timeline,
                entry["target_request_id"],
                record["request_id"],
                self.faults.control_doorbell_error_ordinal
                == action.control_ordinal,
                self.cut,
                _planned_terminal_status(self.faults, entry["target_request_id"]),
            )
            if legs is None:
                continue
            entry["status"] = legs.command_status
            entry["winner"] = legs.winner
            entry["join"] = legs.join
            if (
                self.faults.mutate_cancel_command_ordinal
                == action.control_ordinal
            ):
                entry["mutated_status"] = CQ_STATUS_NAMES.get(
                    self.faults.mutate_cancel_command_status, "SUCCESS"
                )
                target_status = (
                    "CANCELLED"
                    if legs.winner == "CANCEL_WINS"
                    else _planned_terminal_status(
                        self.faults, entry["target_request_id"]
                    )
                )
                entry["status"] = entry["mutated_status"]
                entry["fatal_illegal"] = (
                    _cancel_join_winner(entry["mutated_status"], target_status)
                    is None
                )
            self.controls.append(entry)
            if entry["winner"] == "CANCEL_WINS":
                cancel_targets[(action.target_user_id, action.target_task_seq)] = (
                    action.target_repair_round,
                    record["request_id"],
                )
        for user in self.workload.users:
            for task in user.tasks:
                self._walk_task(user.user_id, task, cancel_targets)

    def _walk_task(self, user_id: int, task, cancel_targets) -> None:
        commands: list[PlannedCommand] = []
        stages: list[PlannedStage] = []
        outcome = None
        terminal_request = None
        cancelled_round = None
        if (user_id, task.task_seq) in cancel_targets:
            cancelled_round, _ = cancel_targets[(user_id, task.task_seq)]
        for round_ in task.rounds:
            request_id = self._generate_id(user_id, task.task_seq, round_.repair_round)
            terminal_request = request_id
            commands.append(
                PlannedCommand(
                    request_id,
                    user_id,
                    task.task_seq,
                    round_.repair_round,
                    "GENERATE",
                    "SUCCESS",
                )
            )
            if (
                self.faults.output_b_error_request
                and request_id == self.faults.output_b_error_request
            ):
                commands[-1].cq_status = "ERROR"
                outcome = "BUSINESS_FAILED"
                break
            if (
                cancelled_round is not None
                and round_.repair_round == cancelled_round
            ):
                commands[-1].cq_status = "CANCELLED"
                outcome = "BUSINESS_FAILED"
                break
            compile_stage = round_.compile
            if self._host_fault_at(user_id, task.task_seq, round_.repair_round):
                if self.faults.host_fault_site == "object_read":
                    stages.append(
                        PlannedStage(request_id, compile_stage.kind,
                                     compile_stage.nominal_ns, False)
                    )
                else:
                    stages.append(
                        PlannedStage(request_id, compile_stage.kind,
                                     compile_stage.nominal_ns, True)
                    )
                outcome = "INFRA_FAILED"
                break
            stages.append(
                PlannedStage(request_id, compile_stage.kind,
                             compile_stage.nominal_ns, True)
            )
            if compile_stage.outcome == "SUCCESS":
                if round_.test is not None:
                    stages.append(
                        PlannedStage(request_id, round_.test.kind,
                                     round_.test.nominal_ns, True)
                    )
                    if round_.test.outcome == "FAIL":
                        if (
                            round_.repair_round < task.effective_max_repair_rounds
                            and round_.log_parse is not None
                        ):
                            self._append_repair(round_, request_id, stages)
                            continue
                        outcome = "BUSINESS_FAILED"
                        break
                outcome = "BUSINESS_DONE"
                break
            if (
                round_.repair_round < task.effective_max_repair_rounds
                and round_.log_parse is not None
            ):
                self._append_repair(round_, request_id, stages)
                continue
            outcome = "BUSINESS_FAILED"
            break
        if outcome is None:
            outcome = "BUSINESS_DONE"
        self.tasks.append(
            PlannedTask(
                user_id,
                task.task_seq,
                terminal_request,
                outcome,
                commands,
                stages,
            )
        )

    def _append_repair(self, round_, request_id, stages) -> None:
        if round_.log_parse is not None:
            stages.append(
                PlannedStage(
                    request_id,
                    round_.log_parse.kind,
                    round_.log_parse.nominal_ns,
                    True,
                )
            )

    def all_commands(self) -> list[PlannedCommand]:
        commands = [command for task in self.tasks for command in task.commands]
        controls = [
            PlannedCommand(
                entry["request_id"],
                next(
                    record["user_id"]
                    for record in self.identity["records"]
                    if record["request_id"] == entry["request_id"]
                ),
                entry["action"].issuer_task_seq,
                0xFFFF,
                entry["action"].opcode,
                entry["status"],
            )
            for entry in self.controls
        ]
        rows = sorted(commands + controls, key=lambda row: row.request_id)
        if self.command_filter is not None:
            rows = [
                row for row in rows if row.request_id in self.command_filter
            ]
        return rows

    def consumed_commands(self) -> list[PlannedCommand]:
        return [
            command
            for command in self.all_commands()
            if command.cq_status != "LOCAL_SUBMIT_FAILED"
        ]

    def byte_ledger(self, tasks=None) -> dict:
        raw_log = 0
        scanned = 0
        excerpt = 0
        excerpt_tokens = 0
        last_excerpt = 0
        for task in (self.tasks if tasks is None else tasks):
            for round_ in self._task_rounds(task):
                generate = next(
                    (
                        command
                        for command in task.commands
                        if command.repair_round == round_.repair_round
                    ),
                    None,
                )
                if generate is None or generate.cq_status != "SUCCESS":
                    break
                failed = self._round_failed(round_)
                if failed is None:
                    break
                parse_ran = any(
                    stage.kind == "LOG_PARSE" and stage.request_id == generate.request_id
                    for stage in task.stages
                )
                if not parse_ran:
                    break
                raw_log += failed
                scanned += round_.log_parse.local_io.read_bytes
                excerpt += round_.log_parse.excerpt_bytes
                excerpt_tokens += round_.log_parse.excerpt_tokens
                last_excerpt = round_.log_parse.excerpt_bytes
        return {
            "raw_log_bytes": raw_log,
            "scanned_log_bytes": scanned,
            "excerpt_bytes": excerpt,
            "excerpt_tokens": excerpt_tokens,
            "repair_input_excerpt_bytes": last_excerpt,
        }

    def _task_rounds(self, task):
        for user in self.workload.users:
            if user.user_id != task.user_id:
                continue
            for user_task in user.tasks:
                if user_task.task_seq == task.task_seq:
                    return user_task.rounds
        raise Gate4OracleError("planned task is absent from the workload")

    def _round_failed(self, round_):
        if round_.compile.outcome == "FAIL":
            return round_.compile.raw_log_bytes
        if round_.test is not None and round_.test.outcome == "FAIL":
            return round_.test.raw_log_bytes
        return None

    def expected_traffic(self) -> tuple[dict, dict]:
        classes = {name: {"bytes": 0, "packets": 0} for name in TRAFFIC_CLASSES}
        owners = {}
        for command in self.all_commands():
            owner = owners.setdefault(
                command.request_id, {"bytes": 0, "packets": 0}
            )

            def add(class_name, bytes_count, packets):
                classes[class_name]["bytes"] += bytes_count
                classes[class_name]["packets"] += packets
                owner["bytes"] += bytes_count
                owner["packets"] += packets

            if command.cq_status == "LOCAL_SUBMIT_FAILED":
                add("AGENT_TO_NPU_CONTROL", CONTROL_BYTES, 1)
                continue
            add("AGENT_TO_NPU_CONTROL", CONTROL_BYTES, 1)
            if command.request_id != self.fatalCommandId:
                add("AGENT_TO_NPU_CONTROL", CONTROL_BYTES, 1)
            add("NPU_TO_AGENT_CONTROL", CONTROL_BYTES, 1)
            add("NPU_TO_AGENT_CONTROL", CONTROL_BYTES, 1)
            add("NPU_TO_AGENT_CONTROL", CONTROL_BYTES, 1)
            add("NPU_WRITE_AGENT_MEMORY", CQ_ENTRY_BYTES, 1)
            add("NPU_READ_AGENT_MEMORY", SQ_ENTRY_BYTES, 1)
            arena = self.arena_by_request[command.request_id]
            parameter = arena["PARAMETER"]
            add(
                "NPU_READ_AGENT_MEMORY",
                parameter["allocation_bytes"],
                plan_bursts(parameter["base"], parameter["allocation_bytes"]),
            )
            if command.kind != "GENERATE":
                continue
            round_ = self.rounds_by_key[
                (command.user_id, command.task_seq, command.repair_round)
            ]
            input_record = arena["INPUT"]
            add(
                "NPU_READ_AGENT_MEMORY",
                input_record["allocation_bytes"],
                plan_bursts(
                    input_record["base"], input_record["allocation_bytes"]
                ),
            )
            profile = self.execution.surrogate.find(
                round_.program_id, round_.profile_id
            )
            if profile is None:
                raise Gate4OracleError(
                    "surrogate profile is absent from registry"
                )
            if command.cq_status != "CANCELLED":
                committed = profile.output_bytes
            else:
                committed = self.execution.cancel_output_prefix_bytes
            if committed:
                output = arena["OUTPUT"]
                written = 0
                segment_sizes = []
                while written < committed:
                    chunk = min(profile.publish_chunk_bytes,
                                committed - written)
                    segment_sizes.extend(
                        plan_burst_sizes(output["base"] + written, chunk)
                    )
                    written += chunk
                if (
                    self.faults.output_b_error_request
                    and command.request_id
                    == self.faults.output_b_error_request
                ):
                    failed = self.faults.output_b_error_segment
                    if failed >= len(segment_sizes):
                        raise Gate4OracleError(
                            "output B error segment exceeds the transfer plan"
                        )
                    issued = segment_sizes[: failed + 1]
                    add(
                        "NPU_WRITE_AGENT_MEMORY",
                        sum(issued),
                        len(issued),
                    )
                else:
                    add(
                        "NPU_WRITE_AGENT_MEMORY",
                        committed,
                        len(segment_sizes),
                    )
            if command.cq_status == "SUCCESS":
                metadata = arena["METADATA"]
                metadata_bytes = worst_metadata_bytes()
                add(
                    "NPU_WRITE_AGENT_MEMORY",
                    metadata_bytes,
                    plan_bursts(metadata["base"], metadata_bytes),
                )
        return classes, owners


def _actual_traffic(events):
    classes = {name: {"bytes": 0, "packets": set()} for name in TRAFFIC_CLASSES}
    owners = {}
    unattributed = 0
    control_packets = {name: 0 for name in TRAFFIC_CLASSES}

    def class_of(event):
        if event["channel"] == "W":
            if event["control"] in ("SQ_DOORBELL", "CQ_HEAD_ACK"):
                return "AGENT_TO_NPU_CONTROL"
            if event["control"] in ("SQ_HEAD_UPDATE", "CQ_TAIL_UPDATE", "MSI"):
                return "NPU_TO_AGENT_CONTROL"
            if event["control"] in ("OUTPUT", "METADATA", "CQ_ENTRY"):
                return "NPU_WRITE_AGENT_MEMORY"
        if event["channel"] == "R" and event["control"] in (
            "SQ_ENTRY", "PARAMETER", "PROMPT"
        ):
            return "NPU_READ_AGENT_MEMORY"
        return None

    sq_owners = {
        event["absolute_seq"]: event["request_id"]
        for event in events
        if event["kind"] == "SQ_CONSUME"
        and event["absolute_seq"] is not None
        and event["request_id"] is not None
    }
    for event in events:
        if event["kind"] != "AXI_ACCEPT" or event["channel"] not in ("W", "R"):
            continue
        traffic_class = class_of(event)
        if traffic_class is None:
            unattributed += event["bytes"]
            continue
        classes[traffic_class]["bytes"] += event["bytes"]
        classes[traffic_class]["packets"].add(event["txn"])
        owner_id = event["request_id"]
        if event["control"] == "SQ_ENTRY":
            owner_id = sq_owners.get(event["absolute_seq"])
        if owner_id is None:
            unattributed += event["bytes"]
            continue
        owner = owners.setdefault(owner_id, {"bytes": 0, "packets": set()})
        owner["bytes"] += event["bytes"]
        owner["packets"].add(event["txn"])
    return classes, owners, unattributed


class Gate4RunOracle:
    def __init__(self, execution: Gate4Execution, facts_path):
        self.events, self.final, self.metrics, self.fatal = parse_facts_tsv(
            facts_path
        )
        self.execution = execution
        self.cut = (
            execution.partial_commands or execution.faults.expect_fatal()
        )
        consumed_ids = {
            event["request_id"]
            for event in self.events
            if event["kind"] == "CQ_CONSUME"
        }
        command_filter = consumed_ids if self.cut else None
        self.walk = PlannedWalk(execution, command_filter, self.events, self.cut)
        self.planned_stage_keys = {
            (stage.kind, stage.request_id)
            for task in self.walk.tasks
            for stage in task.stages
        }
        if execution.faults.expect_fatal():
            terminal_ids = {
                event["request_id"]
                for event in self.events
                if event["kind"] == "TASK_TERMINAL"
            }
            self.terminal_tasks = [
                task
                for task in self.walk.tasks
                if task.terminal_request_id in terminal_ids
            ]
            self.walk.controls = [
                entry
                for entry in self.walk.controls
                if entry["request_id"] in consumed_ids
            ]
        elif execution.partial_commands:
            self.walk.tasks = [
                task
                for task in self.walk.tasks
                if any(
                    command.request_id in consumed_ids
                    for command in task.commands
                )
            ]
            self.walk.controls = [
                entry
                for entry in self.walk.controls
                if entry["request_id"] in consumed_ids
            ]
            self.terminal_tasks = self.walk.tasks
        else:
            self.terminal_tasks = self.walk.tasks

    def _events(self, **fields):
        return [
            event
            for event in self.events
            if all(event.get(name) == value for name, value in fields.items())
        ]

    def check_task_outcome_closure(self) -> bool:
        terminals = self._events(kind="TASK_TERMINAL")
        expected = {
            task.terminal_request_id: task.outcome
            for task in self.terminal_tasks
        }
        observed = {event["request_id"]: event["status"] for event in terminals}
        if observed != expected:
            return False
        completed = sum(
            task.outcome == "BUSINESS_DONE" for task in self.terminal_tasks
        )
        failed = sum(
            task.outcome == "BUSINESS_FAILED" for task in self.terminal_tasks
        )
        infra = sum(
            task.outcome == "INFRA_FAILED" for task in self.terminal_tasks
        )
        return (
            self.metrics.get("completed_tasks", 0) == completed
            and self.metrics.get("failed_tasks", 0) == failed
            and self.metrics.get("infra_failed_tasks", 0) == infra
        )

    def check_command_set_closure(self) -> bool:
        consumed = [event["request_id"] for event in self._events(kind="CQ_CONSUME")]
        expected = [
            command.request_id
            for command in self.walk.consumed_commands()
        ]
        if not self.cut:
            return sorted(consumed) == sorted(expected) and len(consumed) == len(
                set(consumed)
            )
        planned = set(expected)
        if any(request not in planned for request in consumed):
            return False
        per_user = {}
        for command in self.walk.consumed_commands():
            per_user.setdefault(command.user_id, []).append(command.request_id)
        seen_per_user = {}
        for request in consumed:
            record = self.walk.records[request]
            seen_per_user.setdefault(record["user_id"], []).append(request)
        for user_id, requests in seen_per_user.items():
            planned_seq = sorted(request & 0xFFFFFFFF for request in per_user[user_id])
            observed_seq = sorted(request & 0xFFFFFFFF for request in requests)
            if observed_seq != planned_seq[: len(observed_seq)]:
                return False
        return True

    def check_stage_order(self) -> bool:
        for task in self.terminal_tasks:
            for stage in task.stages:
                enqueue = self._events(
                    kind="HOST_STAGE_ENQUEUE",
                    object=stage.kind,
                    request_id=stage.request_id,
                )
                starts = self._events(
                    kind="HOST_STAGE_START",
                    object=stage.kind,
                    request_id=stage.request_id,
                )
                dones = self._events(
                    kind="HOST_STAGE_DONE",
                    object=stage.kind,
                    request_id=stage.request_id,
                )
                if len(enqueue) != 1:
                    return False
                if stage.ran_to_done:
                    if len(starts) != 1 or len(dones) != 1:
                        return False
                    if not (
                        enqueue[0]["tick"]
                        <= starts[0]["tick"]
                        <= dones[0]["tick"]
                    ):
                        return False
                elif starts or dones:
                    return False
        unexpected = [
            event
            for event in self.events
            if event["kind"] in ("HOST_STAGE_ENQUEUE", "HOST_STAGE_START", "HOST_STAGE_DONE")
            and (event["object"], event["request_id"]) not in self.planned_stage_keys
        ]
        return not unexpected

    def check_stage_duration_bound(self) -> bool:
        for task in self.terminal_tasks:
            for stage in task.stages:
                starts = self._events(
                    kind="HOST_STAGE_START",
                    object=stage.kind,
                    request_id=stage.request_id,
                )
                dones = self._events(
                    kind="HOST_STAGE_DONE",
                    object=stage.kind,
                    request_id=stage.request_id,
                )
                if not stage.ran_to_done:
                    continue
                if not starts or not dones:
                    return False
                if (
                    dones[0]["tick"] - starts[0]["tick"]
                    < stage.nominal_ns * TICKS_PER_NS - 1
                ):
                    return False
        return True

    def check_byte_conservation(self) -> bool:
        lower = self.walk.byte_ledger(self.terminal_tasks)
        upper = (
            self.walk.byte_ledger(self.walk.tasks)
            if self.execution.faults.expect_fatal()
            else lower
        )
        if self.metrics.get("npu_fabric_raw_log_bytes", 0) != 0:
            return False
        if not self.execution.faults.expect_fatal() and (
            self.metrics.get("agent_live_objects", None) != 0
        ):
            return False
        for name, value in lower.items():
            observed = self.metrics.get(name, None)
            if observed is None:
                return False
            if not value <= observed <= upper[name]:
                return False
        return True

    def check_cq_conservation(self) -> bool:
        consumed = self._events(kind="CQ_CONSUME")
        doorbells = self._events(
            kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="AW"
        )
        rollbacks = self._events(kind="PUBLICATION_ROLLBACK")
        published = len(doorbells) - len(rollbacks)
        if len(consumed) != published and not (
            self.cut and len(consumed) <= published
        ):
            return False
        consumed_ids = [event["request_id"] for event in consumed]
        if len(consumed_ids) != len(set(consumed_ids)):
            return False
        expected_status = {
            command.request_id: command.cq_status
            for command in self.walk.consumed_commands()
        }
        observed_status = {
            event["request_id"]: event["status"] for event in consumed
        }
        if not self.cut:
            return observed_status == expected_status
        return all(
            expected_status.get(request) == status
            for request, status in observed_status.items()
        )

    def check_control_outcome_legality(self) -> bool:
        joins = self._events(kind="CANCEL_JOIN_RESOLVED")
        waiters = self._events(kind="CONTROL_TERMINAL")
        expected_joins = []
        expected_waiters = []
        for entry in self.walk.controls:
            if entry.get("fatal_illegal"):
                continue
            if entry["join"]:
                expected_joins.append((entry["request_id"], entry["winner"]))
            else:
                expected_waiters.append(
                    (entry["request_id"], entry["status"])
                )
        observed_joins = [
            (event["request_id"], event["status"]) for event in joins
        ]
        observed_waiters = [
            (event["request_id"], event["status"]) for event in waiters
        ]
        if sorted(observed_joins) != sorted(expected_joins):
            return False
        if sorted(observed_waiters) != sorted(expected_waiters):
            return False
        status_of = {
            event["request_id"]: event["status"]
            for event in self._events(kind="CQ_CONSUME")
        }
        for entry in self.walk.controls:
            if entry["winner"] != "CANCEL_WINS" or entry.get("fatal_illegal"):
                continue
            action = entry["action"]
            target_id = self.walk._generate_id(
                action.target_user_id,
                action.target_task_seq,
                action.target_repair_round,
            )
            if status_of.get(entry["request_id"]) != "SUCCESS":
                return False
            if status_of.get(target_id) != "CANCELLED":
                return False
        return True

    def check_irq_before_cq_consume(self) -> bool:
        delivered = {}
        for event in self.events:
            if event["kind"] == "IRQ_DELIVER" and event["absolute_seq"] is not None:
                delivered[event["absolute_seq"]] = min(
                    event["tick"],
                    delivered.get(
                        event["absolute_seq"], event["tick"]
                    ),
                )
        for event in self.events:
            if event["kind"] != "CQ_CONSUME":
                continue
            sequence = event["absolute_seq"]
            if sequence is None or delivered.get(sequence + 1) is None:
                return False
            if delivered[sequence + 1] > event["tick"]:
                return False
        return True

    def traffic_artifact_rows(self):
        expected_classes, expected_owners = self.walk.expected_traffic()
        actual_classes, actual_owners, unattributed = _actual_traffic(self.events)

        def consistent(expected_value, actual_value):
            if not self.cut:
                return expected_value == actual_value
            return expected_value <= actual_value

        rows = []
        matched = unattributed == 0
        for name in TRAFFIC_CLASSES:
            expected = expected_classes[name]
            actual = actual_classes[name]
            expected_packets = expected["packets"]
            actual_packets = len(actual["packets"])
            matched = matched and consistent(
                expected["bytes"], actual["bytes"]
            ) and consistent(expected_packets, actual_packets)
            rows.append(
                {
                    "traffic_class": name,
                    "expected_bytes": expected["bytes"],
                    "actual_bytes": actual["bytes"],
                    "expected_packets": expected_packets,
                    "actual_packets": actual_packets,
                }
            )
        owner_ids = sorted(
            set(expected_owners) | set(actual_owners),
            key=lambda owner: format(owner, "x"),
        )
        ownership = []
        for owner_id in owner_ids:
            expected = expected_owners.get(owner_id, {"bytes": 0})
            actual = actual_owners.get(owner_id, {"bytes": 0})
            matched = matched and consistent(
                expected["bytes"], actual["bytes"]
            )
            ownership.append(
                {
                    "owner_key_wire": format(owner_id, "x"),
                    "expected_bytes": expected["bytes"],
                    "actual_bytes": actual["bytes"],
                }
            )
        return rows, ownership, unattributed, matched

    def check_traffic_conservation(self) -> bool:
        _, _, _, matched = self.traffic_artifact_rows()
        return matched

    def run(self) -> dict:
        results = {}
        for check in ORACLE_CHECKS:
            results[check] = bool(getattr(self, f"check_{check}")())
        return results

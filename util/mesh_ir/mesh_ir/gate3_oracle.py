from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import jsonschema


class Gate3OracleError(ValueError):
    pass


CHECKS = (
    "event_order",
    "ring_slot_generation",
    "axi_direction",
    "axi_transaction_lifecycle",
    "control_update_contract",
    "submission_publication",
    "core_start_order",
    "completion_publication",
    "cq_identity",
    "cq_slot_lifetime",
    "absolute_sequence_state",
    "terminal_ownership",
)

WRITE_DIRECTIONS = {
    "SQ_DOORBELL": "DRIVER_TO_NPU",
    "SQ_HEAD_UPDATE": "NPU_TO_DRIVER",
    "OUTPUT": "NPU_TO_DRIVER",
    "METADATA": "NPU_TO_DRIVER",
    "CQ_ENTRY": "NPU_TO_DRIVER",
    "CQ_TAIL_UPDATE": "NPU_TO_DRIVER",
    "MSI": "NPU_TO_DRIVER",
    "CQ_HEAD_ACK": "DRIVER_TO_NPU",
}

READ_DIRECTIONS = {
    "SQ_ENTRY": "NPU_TO_DRIVER",
    "PARAMETER": "NPU_TO_DRIVER",
    "PROMPT": "NPU_TO_DRIVER",
}

CONTROL_UPDATES = {
    "SQ_DOORBELL",
    "SQ_HEAD_UPDATE",
    "CQ_TAIL_UPDATE",
    "MSI",
    "CQ_HEAD_ACK",
}

EVENT_PHASES = {
    "LOCAL_VISIBLE": 0,
    "RELEASE_FENCE_DONE": 0,
    "AXI_ACCEPT": 0,
    "DOORBELL_TARGET_COMMIT": 1,
    "CQ_ACK_TARGET_COMMIT": 1,
    "MSI_TARGET_COMMIT": 1,
    "FATAL": 2,
    "PUBLICATION_COMMIT": 3,
    "PUBLICATION_ROLLBACK": 3,
    "SQ_CONSUME": 3,
    "CQ_OBLIGATION_RESERVE": 3,
    "CAPACITY_ACCEPT": 3,
    "CQ_ASSIGN": 3,
    "CQ_CONSUME": 3,
    "CQ_OBLIGATION_RETIRE": 3,
    "SLOT_REUSE": 3,
    "CORE_START": 4,
    "IRQ_DELIVER": 4,
}


def _schema():
    root = Path(__file__).resolve().parents[3]
    return json.loads(
        (root / "schemas/ai_mesh/gate3_observation_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise Gate3OracleError(f"duplicate JSON field {name}")
        result[name] = value
    return result


def _reject_constant(value):
    raise Gate3OracleError(f"non-finite JSON number {value}")


def canonical_observation_bytes(document):
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def load_observation(path):
    raw = Path(path).read_bytes()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Gate3OracleError(f"invalid observation JSON: {error}") from error
    if raw != canonical_observation_bytes(document):
        raise Gate3OracleError("observation JSON is not canonical")
    return document


def _u64(value, label):
    if isinstance(value, bool):
        raise Gate3OracleError(f"{label} is not u64-json")
    if isinstance(value, int):
        if 0 <= value <= (1 << 53) - 1:
            return value
        raise Gate3OracleError(f"{label} is outside exact JSON range")
    if isinstance(value, str) and len(value) == 18 and value.startswith("0x"):
        try:
            decoded = int(value[2:], 16)
        except ValueError as error:
            raise Gate3OracleError(f"{label} is not u64-json") from error
        if decoded > (1 << 53) - 1:
            return decoded
    raise Gate3OracleError(f"{label} is not u64-json")


def _value(row, name):
    value = row[name]
    return None if value is None else _u64(value, name)


def _key(row):
    return (_u64(row["tick"], "tick"), row["phase"], _u64(row["ordinal"], "ordinal"))


def _before(left, right):
    return _key(left) < _key(right)


def _matching(events, **fields):
    return [
        row
        for row in events
        if all(row[name] == value for name, value in fields.items())
    ]


def _prior(events, target, **fields):
    return [row for row in _matching(events, **fields) if _before(row, target)]


def _require_prior(events, target, message, **fields):
    matches = _prior(events, target, **fields)
    if not matches:
        raise Gate3OracleError(message)
    return matches[-1]


def _validate_schema(document):
    validator = jsonschema.Draft202012Validator(_schema())
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path)
        raise Gate3OracleError(f"schema {location}: {error.message}")


def _validate_event_order(events):
    previous = None
    for expected_ordinal, row in enumerate(events):
        if _u64(row["ordinal"], "ordinal") != expected_ordinal:
            raise Gate3OracleError("event order does not use dense ordinals")
        current = _key(row)
        if previous is not None and current < previous:
            raise Gate3OracleError("event order regresses in tick or phase")
        if row["phase"] != EVENT_PHASES[row["kind"]]:
            raise Gate3OracleError("event order uses the wrong canonical edge phase")
        previous = current


def _validate_ring_identity(document):
    depths = {
        "SQ_ENTRY": document["configuration"]["sq_depth"],
        "CQ_ENTRY": document["configuration"]["cq_depth"],
    }
    for row in document["events"]:
        depth = depths.get(row["object"])
        seq = _value(row, "absolute_seq")
        slot = _value(row, "slot")
        generation = _value(row, "generation")
        if depth is None or seq is None:
            if slot is not None or generation is not None:
                raise Gate3OracleError("ring identity appears without an absolute sequence")
            continue
        if slot != seq % depth:
            raise Gate3OracleError("ring slot disagrees with absolute sequence")
        if generation != seq // depth:
            raise Gate3OracleError("ring generation disagrees with absolute sequence")


def _expected_direction(control, channel):
    if control in WRITE_DIRECTIONS:
        forward = WRITE_DIRECTIONS[control]
        return forward if channel in ("AW", "W") else (
            "NPU_TO_DRIVER" if forward == "DRIVER_TO_NPU" else "DRIVER_TO_NPU"
        )
    if control in READ_DIRECTIONS:
        forward = READ_DIRECTIONS[control]
        return forward if channel == "AR" else (
            "NPU_TO_DRIVER" if forward == "DRIVER_TO_NPU" else "DRIVER_TO_NPU"
        )
    raise Gate3OracleError(f"unknown AXI control class {control}")


def _validate_axi_direction(events):
    for row in _matching(events, kind="AXI_ACCEPT"):
        expected = _expected_direction(row["control"], row["channel"])
        if row["direction"] != expected:
            raise Gate3OracleError(
                f"AXI direction mismatch for {row['control']}/{row['channel']}"
            )


def _validate_axi_lifecycle(events, fatal):
    groups = defaultdict(list)
    for row in _matching(events, kind="AXI_ACCEPT"):
        groups[_value(row, "txn")].append(row)
    if None in groups:
        raise Gate3OracleError("AXI transaction has no identity")
    for txn, rows in groups.items():
        channels = [row["channel"] for row in rows]
        if channels[0] == "AW":
            expected = ["AW", "W", "B"]
        elif channels[0] == "AR":
            expected = ["AR", "R"]
        else:
            raise Gate3OracleError(f"AXI transaction {txn} starts on a response channel")
        if channels != expected and not (fatal and channels == expected[:len(channels)]):
            raise Gate3OracleError(
                f"AXI transaction lifecycle mismatch for transaction {txn}"
            )
        if len({row["control"] for row in rows}) != 1 or len(
            {row["axi_id"] for row in rows}
        ) != 1:
            raise Gate3OracleError(f"AXI transaction {txn} changes identity")
        for row in rows:
            byte_count = _u64(row["bytes"], "bytes")
            if row["channel"] in ("AW", "AR", "B") and byte_count != 0:
                raise Gate3OracleError("AXI request channel carries data bytes")
            if row["channel"] in ("W", "R") and byte_count == 0:
                raise Gate3OracleError("AXI data channel carries zero bytes")
            if row["channel"] == "W":
                if row["wstrb"] is None:
                    raise Gate3OracleError("AXI W transfer has no WSTRB")
                if int(row["wstrb"], 16) >= 1 << byte_count:
                    raise Gate3OracleError("AXI WSTRB exceeds the transfer byte lanes")
            elif row["wstrb"] is not None:
                raise Gate3OracleError("WSTRB appears outside a W transfer")
            if row["channel"] in ("B", "R"):
                if row["response"] is None:
                    raise Gate3OracleError("AXI response has no response code")
            elif row["response"] is not None:
                raise Gate3OracleError("AXI request carries a response code")


def _validate_control_contract(document):
    events = document["events"]
    configuration = document["configuration"]
    fixed = configuration["non_msi_axi_ids"]
    control_bytes = configuration["control_bytes"]
    full_wstrb = f"{(1 << control_bytes) - 1:x}"
    for row in _matching(events, kind="AXI_ACCEPT"):
        control = row["control"]
        if control in fixed and row["axi_id"] != fixed[control]:
            raise Gate3OracleError(f"{control} does not use its fixed AXI ID")
        if row["channel"] == "W" and control in CONTROL_UPDATES:
            if _u64(row["bytes"], "bytes") != control_bytes or row["wstrb"] != full_wstrb:
                raise Gate3OracleError(f"{control} does not use full WSTRB")
    for control in fixed:
        live = None
        for row in _matching(events, kind="AXI_ACCEPT", control=control):
            if row["channel"] == "AW":
                if live is not None:
                    raise Gate3OracleError(f"{control} has more than one transaction in flight")
                live = _value(row, "txn")
            elif row["channel"] == "B":
                if live != _value(row, "txn"):
                    raise Gate3OracleError(f"{control} B does not match the live transaction")
                live = None


def _validate_submission(document):
    events = document["events"]
    doorbells = _matching(
        events, kind="AXI_ACCEPT", control="SQ_DOORBELL", channel="AW"
    )
    for row in doorbells:
        request = row["request_id"]
        if request is None:
            continue
        for object_name in ("PROMPT", "PARAMETER", "SQ_ENTRY"):
            _require_prior(
                events,
                row,
                f"doorbell precedes {object_name} local visibility",
                kind="LOCAL_VISIBLE",
                object=object_name,
                request_id=request,
            )
        _require_prior(
            events,
            row,
            "doorbell precedes release fence",
            kind="RELEASE_FENCE_DONE",
            request_id=request,
        )
    commits = _matching(events, kind="PUBLICATION_COMMIT")
    for commit in commits:
        candidates = [
            row
            for row in _prior(
                events,
                commit,
                kind="AXI_ACCEPT",
                control="SQ_DOORBELL",
                channel="B",
                request_id=commit["request_id"],
            )
            if row["response"] == "OKAY"
            and row["absolute_seq"] == commit["absolute_seq"]
        ]
        if not candidates:
            raise Gate3OracleError("publication commit has no prior OKAY doorbell B")
    committed = max((_value(row, "absolute_seq") for row in commits), default=0)
    if _u64(document["final"]["sq_committed_producer_seq"], "sq committed") != committed:
        raise Gate3OracleError("committed producer sequence disagrees with publication commits")


def _validate_core_start(events):
    for row in _matching(events, kind="CORE_START"):
        request = row["request_id"]
        prompt = [
            candidate
            for candidate in _prior(
                events,
                row,
                kind="AXI_ACCEPT",
                control="PROMPT",
                channel="R",
                request_id=request,
            )
            if candidate["response"] == "OKAY"
        ]
        if not prompt:
            raise Gate3OracleError("core start precedes final prompt R")
        _require_prior(
            events,
            row,
            "core start precedes capacity acceptance",
            kind="CAPACITY_ACCEPT",
            request_id=request,
        )


def _okay_b_before(events, target, control, request):
    return any(
        row["response"] == "OKAY"
        for row in _prior(
            events,
            target,
            kind="AXI_ACCEPT",
            control=control,
            channel="B",
            request_id=request,
        )
    )


def _validate_completion(events):
    for row in _matching(events, kind="CQ_ASSIGN"):
        if row["status"] == "SUCCESS":
            request = row["request_id"]
            if not _okay_b_before(events, row, "OUTPUT", request):
                raise Gate3OracleError("success CQ precedes final output B")
            if not _okay_b_before(events, row, "METADATA", request):
                raise Gate3OracleError("success CQ precedes final metadata B")
    for row in _matching(events, kind="AXI_ACCEPT", control="MSI", channel="AW"):
        request = row["request_id"]
        if not _okay_b_before(events, row, "CQ_ENTRY", request):
            raise Gate3OracleError("MSI issue precedes CQ B")
    for row in _matching(events, kind="IRQ_DELIVER"):
        target_commits = _prior(
            events,
            row,
            kind="MSI_TARGET_COMMIT",
            absolute_seq=row["absolute_seq"],
        )
        if not target_commits:
            raise Gate3OracleError("IRQ precedes MSI target commit")
        msi_writes = _prior(
            events,
            target_commits[-1],
            kind="AXI_ACCEPT",
            control="MSI",
            channel="W",
            absolute_seq=row["absolute_seq"],
        )
        if not msi_writes:
            raise Gate3OracleError("MSI target commit has no accepted data")
        request = msi_writes[-1]["request_id"]
        if not _okay_b_before(events, row, "CQ_ENTRY", request):
            raise Gate3OracleError("IRQ precedes CQ B")


def _validate_cq_identity(events):
    assignments = {
        _value(row, "absolute_seq"): row
        for row in _matching(events, kind="CQ_ASSIGN")
    }
    if len(assignments) != len(_matching(events, kind="CQ_ASSIGN")):
        raise Gate3OracleError("duplicate CQ sequence assignment")
    for row in _matching(events, kind="CQ_CONSUME"):
        seq = _value(row, "absolute_seq")
        assigned = assignments.get(seq)
        if assigned is None or any(
            row[name] != assigned[name]
            for name in ("request_id", "cookie", "status")
        ):
            raise Gate3OracleError("CQ identity does not match its assignment")


def _validate_cq_slot_lifetime(document):
    events = document["events"]
    for row in _matching(events, kind="SLOT_REUSE", object="CQ_ENTRY"):
        seq = _value(row, "absolute_seq")
        if row["request_id"] is None or seq is None:
            raise Gate3OracleError("CQ slot reuse lacks identity")
        commits = [
            candidate
            for candidate in _prior(
                events,
                row,
                kind="CQ_ACK_TARGET_COMMIT",
            )
            if _value(candidate, "absolute_seq") is not None
            and _value(candidate, "absolute_seq") >= seq + 1
        ]
        if not commits:
            raise Gate3OracleError("CQ slot reuse precedes ACK target commit")


def _validate_sequence_state(document):
    events = document["events"]
    final = {name: _u64(value, name) if not isinstance(value, bool) else value
             for name, value in document["final"].items()}
    if not final["sq_reusable_head_seq"] <= final["sq_observed_head_seq"]:
        raise Gate3OracleError("reusable SQ head exceeds observed head")
    if not final["sq_reusable_head_seq"] <= final["sq_committed_producer_seq"]:
        raise Gate3OracleError("reusable SQ head exceeds committed producer")
    if not final["npu_sq_consumer_seq"] <= final["sq_tentative_producer_seq"]:
        raise Gate3OracleError("NPU SQ consumer exceeds tentative producer")
    if not final["npu_sq_consumer_seq"] <= final["sq_committed_producer_seq"]:
        raise Gate3OracleError("NPU SQ consumer exceeds committed producer")
    if not (
        final["npu_cq_ack_seq"]
        <= final["driver_cq_consumer_seq"]
        <= final["cq_notified_seq"]
        <= final["cq_msi_issued_seq"]
        <= final["npu_cq_producer_seq"]
    ):
        raise Gate3OracleError("CQ absolute sequence counters are not ordered")
    consumed = [_value(row, "absolute_seq") for row in _matching(events, kind="CQ_CONSUME")]
    if consumed != list(range(len(consumed))):
        raise Gate3OracleError("CQ consumer does not consume a continuous prefix")
    if final["driver_cq_consumer_seq"] != len(consumed):
        raise Gate3OracleError("CQ consumer sequence disagrees with consumed entries")
    assigned = [_value(row, "absolute_seq") for row in _matching(events, kind="CQ_ASSIGN")]
    if assigned != list(range(len(assigned))):
        raise Gate3OracleError("CQ producer does not assign a continuous prefix")
    if final["npu_cq_producer_seq"] != len(assigned):
        raise Gate3OracleError("CQ producer sequence disagrees with assignments")


def _validate_terminal_ownership(document):
    events = document["events"]
    final = document["final"]
    fatal_events = _matching(events, kind="FATAL")
    if bool(fatal_events) != final["fatal"]:
        raise Gate3OracleError("fatal event and final fatal state differ")
    if fatal_events:
        cut = fatal_events[0]
        for row in events:
            if _before(cut, row) and (
                row["kind"] in ("CORE_START", "CQ_ASSIGN")
                or row["kind"] == "AXI_ACCEPT" and row["channel"] in ("AW", "AR")
            ):
                raise Gate3OracleError("new work was issued after the fatal cut")
    if final["core_starts"] != len(_matching(events, kind="CORE_START")):
        raise Gate3OracleError("core start accounting mismatch")
    if final["cq_assignments"] != len(_matching(events, kind="CQ_ASSIGN")):
        raise Gate3OracleError("CQ assignment accounting mismatch")
    if final["irq_deliveries"] != len(_matching(events, kind="IRQ_DELIVER")):
        raise Gate3OracleError("IRQ accounting mismatch")
    reserves = defaultdict(int)
    retires = defaultdict(int)
    for row in _matching(events, kind="CQ_OBLIGATION_RESERVE"):
        reserves[(row["request_id"], row["cookie"])] += 1
    for row in _matching(events, kind="CQ_OBLIGATION_RETIRE"):
        retires[(row["request_id"], row["cookie"])] += 1
    owner_keys = set(reserves) | set(retires)
    if any(retires[key] > reserves[key] for key in owner_keys):
        raise Gate3OracleError("CQ obligation retired without ownership")
    live = sum(reserves.values()) - sum(retires.values())
    if live != final["live_cq_obligations"]:
        raise Gate3OracleError("CQ obligation ownership accounting mismatch")
    if not final["fatal"] and any(
        final[name] != 0
        for name in (
            "live_submissions",
            "live_contexts",
            "live_cq_obligations",
            "msi_rob_entries",
            "ack_wait_b",
        )
    ):
        raise Gate3OracleError("successful terminal state retains live ownership")


def validate_observation(document):
    _validate_schema(document)
    events = document["events"]
    _validate_event_order(events)
    _validate_ring_identity(document)
    _validate_axi_direction(events)
    _validate_axi_lifecycle(events, document["final"]["fatal"])
    _validate_control_contract(document)
    _validate_submission(document)
    _validate_core_start(events)
    _validate_completion(events)
    _validate_cq_identity(events)
    _validate_cq_slot_lifetime(document)
    _validate_sequence_state(document)
    _validate_terminal_ownership(document)
    return CHECKS

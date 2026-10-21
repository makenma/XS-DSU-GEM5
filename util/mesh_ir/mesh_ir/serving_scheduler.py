"""Canonical serving batch scheduler decision model (contract section 10.3).

The model is pure: it decides one quantum from the snapshot of requests that
were READY before this edge. Callers own snapshot timing, phase transitions
and ``phase_ready_tick``/``kv_admission_ready_tick`` freezing; the C++
``mesh_serving_scheduler`` twin must produce identical decisions from the
same inputs.
"""

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

MAX_TICK = 2 ** 64 - 1
POLICY_V1 = "PUBLISH_THEN_DECODE_FIRST_AGED_PREFILL"
PHASES = ("PREFILL", "DECODE", "PUBLISH")


@dataclass(frozen=True)
class SchedulerMember:
    request_id: int
    phase: str
    phase_ready_tick: int
    deadline_tick: int | None
    qos: int
    program_id: int
    request_profile_id: int
    path_kind: str
    valid_tokens_per_member: int
    kv_tokens_before: int
    decode_chunk_tokens: int
    logical_source_rank: int


@dataclass(frozen=True)
class SchedulerLimits:
    max_active_sequences: int
    max_batch_tokens: int
    max_prefill_tokens_per_batch: int
    max_decode_sequences_per_batch: int
    prefill_starvation_cycles: int
    batch_wait_cycles: int
    prefill_decode_policy: str = POLICY_V1


@dataclass(frozen=True)
class SchedulerDecision:
    action: str
    phase: str | None
    members: tuple
    member_count: int
    batch_effective_qos: int | None
    wait_until: int | None


IDLE = SchedulerDecision("IDLE", None, (), 0, None, None)


def base_key(member: SchedulerMember):
    return (member.program_id, member.request_profile_id, member.path_kind,
            member.phase, member.valid_tokens_per_member,
            member.kv_tokens_before, member.decode_chunk_tokens)


def order_key(member: SchedulerMember):
    return (MAX_TICK if member.deadline_tick is None
            else member.deadline_tick,
            255 - member.qos, member.phase_ready_tick, member.request_id)


def checked_add(left: int, right: int) -> int:
    total = left + right
    return total if total <= MAX_TICK else MAX_TICK


def _feasible_count(member: SchedulerMember, count: int,
                    limits: SchedulerLimits) -> bool:
    if count * member.valid_tokens_per_member > limits.max_batch_tokens:
        return False
    if member.phase == "PREFILL":
        return (count * member.valid_tokens_per_member
                <= limits.max_prefill_tokens_per_batch)
    if member.phase == "DECODE":
        return count <= limits.max_decode_sequences_per_batch
    if member.phase == "PUBLISH":
        return count <= limits.max_active_sequences
    return False


def _select_phase(members, limits, now, clock_period_ticks):
    phases = {member.phase for member in members}
    if "PUBLISH" in phases:
        return "PUBLISH"
    if any(member.phase == "PREFILL" and
           now - member.phase_ready_tick >= min(
               MAX_TICK, limits.prefill_starvation_cycles * clock_period_ticks)
           for member in members):
        return "PREFILL"
    if "DECODE" in phases:
        return "DECODE"
    if "PREFILL" in phases:
        return "PREFILL"
    return None


def decide(members: Iterable[SchedulerMember], limits: SchedulerLimits,
           now: int,
           profiles: Mapping | Callable, *,
           clock_period_ticks: int = 1) -> SchedulerDecision:
    if (isinstance(clock_period_ticks, bool) or
            not isinstance(clock_period_ticks, int) or clock_period_ticks <= 0):
        raise ValueError("scheduler clock period must be a positive integer")
    if limits.prefill_decode_policy != POLICY_V1:
        raise ValueError("unsupported prefill_decode_policy")
    member_list = list(members)
    for member in member_list:
        if member.phase not in PHASES:
            raise ValueError("unknown phase " + member.phase)
    phase = _select_phase(member_list, limits, now, clock_period_ticks)
    if phase is None:
        return IDLE
    candidates = sorted(
        (member for member in member_list if member.phase == phase),
        key=order_key)
    head = candidates[0]
    key = base_key(head)
    prefix = [head]
    for member in candidates[1:]:
        if base_key(member) != key:
            break
        prefix.append(member)
    compiled = frozenset(profiles(key)) if not callable(profiles) \
        else frozenset(profiles(key))
    feasible = {count for count in compiled
                if _feasible_count(head, count, limits)}
    runnable = sorted(count for count in feasible
                      if count <= len(prefix))
    if phase == "PUBLISH":
        if not runnable:
            return SchedulerDecision("FAIL_HEAD", phase, (head.request_id,),
                                     1, None, None)
        return _run(prefix, runnable[-1], phase)
    deadline = checked_add(head.phase_ready_tick, min(
        MAX_TICK, limits.batch_wait_cycles * clock_period_ticks))
    if not runnable:
        if now < deadline:
            return SchedulerDecision("WAIT", phase, (), 0, None, deadline)
        return SchedulerDecision("FAIL_HEAD", phase, (head.request_id,), 1,
                                 None, None)
    if any(count > len(prefix) for count in feasible) and now < deadline:
        return SchedulerDecision("WAIT", phase, (), 0, None, deadline)
    return _run(prefix, runnable[-1], phase)


def _run(prefix, count, phase):
    frozen = prefix[:count]
    return SchedulerDecision(
        "RUN", phase, tuple(member.request_id for member in frozen), count,
        max(member.qos for member in frozen), None)

import pytest

from mesh_ir.serving_scheduler import (SchedulerLimits, SchedulerMember,
                                       decide, MAX_TICK)


def member(request_id, phase, *, ready=1000, deadline=None, qos=0,
           program=1, profile=1, path="INITIAL", tokens=8, kv_before=0,
           chunk=0, rank=0):
    return SchedulerMember(
        request_id=request_id, phase=phase, phase_ready_tick=ready,
        deadline_tick=deadline, qos=qos, program_id=program,
        request_profile_id=profile, path_kind=path,
        valid_tokens_per_member=tokens, kv_tokens_before=kv_before,
        decode_chunk_tokens=chunk, logical_source_rank=rank)


def limits(**overrides):
    base = dict(max_active_sequences=4, max_batch_tokens=64,
                max_prefill_tokens_per_batch=32,
                max_decode_sequences_per_batch=4,
                prefill_starvation_cycles=5000, batch_wait_cycles=2000)
    base.update(overrides)
    return SchedulerLimits(**base)


def counts(*values):
    return frozenset(values)


def test_publish_outranks_everything_even_aged_prefill():
    decision = decide(
        [member(1, "PREFILL", ready=0), member(2, "PUBLISH"),
         member(3, "DECODE")],
        limits(), now=10 ** 9, profiles=lambda key: counts(1))
    assert decision.action == "RUN"
    assert decision.phase == "PUBLISH"
    assert decision.members == (2,)


def test_aged_prefill_beats_decode():
    decision = decide(
        [member(1, "DECODE", ready=990_000), member(2, "PREFILL", ready=0)],
        limits(), now=1_000_000, profiles=lambda key: counts(1))
    assert decision.phase == "PREFILL"
    assert decision.members == (2,)


def test_young_prefill_loses_to_decode():
    decision = decide(
        [member(1, "DECODE", ready=990_000), member(2, "PREFILL", ready=998_000)],
        limits(), now=1_000_000, profiles=lambda key: counts(1))
    assert decision.phase == "DECODE"
    assert decision.members == (1,)


def test_prefill_used_when_no_decode():
    decision = decide([member(1, "PREFILL", ready=900_000)], limits(),
                      now=1_000_000, profiles=lambda key: counts(1))
    assert decision.phase == "PREFILL"
    assert decision.members == (1,)


def test_empty_snapshot_idles():
    assert decide([], limits(), now=5, profiles=lambda key: counts(1)).action \
        == "IDLE"


def test_ordering_is_deadline_then_qos_then_age_then_id():
    members = [
        member(7, "PUBLISH", ready=500, qos=1),
        member(3, "PUBLISH", ready=400, qos=1),
        member(9, "PUBLISH", ready=100, qos=2, deadline=50),
        member(4, "PUBLISH", ready=300, qos=1, deadline=90),
        member(5, "PUBLISH", ready=200, qos=1, deadline=90),
    ]
    decision = decide(members, limits(), now=1000,
                      profiles=lambda key: counts(1, 2))
    assert decision.members == (9, 5)
    single = decide(members, limits(), now=1000,
                    profiles=lambda key: counts(1))
    assert single.members == (9,)


def test_prefix_stops_at_incompatible_key_and_never_skips_head():
    members = [
        member(1, "DECODE", tokens=8, kv_before=10, chunk=2),
        member(2, "DECODE", tokens=8, kv_before=10, chunk=2),
        member(3, "DECODE", tokens=8, kv_before=12, chunk=2),
        member(4, "DECODE", tokens=8, kv_before=10, chunk=2),
    ]
    decision = decide(members, limits(), now=10 ** 9,
                      profiles=lambda key: counts(1, 2, 4))
    assert decision.members == (1, 2)


def test_picks_largest_feasible_count_not_prefix_size():
    members = [member(i, "DECODE", kv_before=10, chunk=2) for i in (1, 2, 3)]
    decision = decide(members, limits(), now=10 ** 9,
                      profiles=lambda key: counts(1, 2))
    assert decision.members == (1, 2)


def test_capacity_limits_shape_the_feasible_set():
    members = [member(i, "PREFILL", tokens=8) for i in (1, 2, 3, 4)]
    decision = decide(members, limits(max_prefill_tokens_per_batch=16),
                      now=10 ** 9, profiles=lambda key: counts(1, 2, 3, 4))
    assert decision.members == (1, 2)


def test_waits_before_deadline_when_larger_profile_exists():
    members = [member(1, "DECODE", ready=10_000, kv_before=5, chunk=2)]
    decision = decide(members, limits(batch_wait_cycles=2000), now=11_000,
                      profiles=lambda key: counts(1, 2))
    assert decision.action == "WAIT"
    assert decision.wait_until == 12_000


def test_runs_smaller_batch_at_deadline_even_if_larger_profile_exists():
    members = [
        member(1, "DECODE", ready=10_000, kv_before=5, chunk=2),
        member(2, "DECODE", ready=10_500, kv_before=5, chunk=2),
    ]
    decision = decide(members, limits(batch_wait_cycles=2000), now=12_000,
                      profiles=lambda key: counts(1, 2))
    assert decision.action == "RUN"
    assert decision.members == (1, 2)


def test_head_fails_at_deadline_with_no_feasible_count():
    members = [member(1, "DECODE", ready=10_000, kv_before=5, chunk=2)]
    decision = decide(members, limits(batch_wait_cycles=2000), now=12_001,
                      profiles=lambda key: counts(9))
    assert decision.action == "FAIL_HEAD"
    assert decision.members == (1,)


def test_publish_never_waits():
    members = [member(1, "PUBLISH", ready=10_000)]
    decision = decide(members, limits(batch_wait_cycles=2000), now=10_001,
                      profiles=lambda key: counts(1, 2))
    assert decision.action == "RUN"
    assert decision.members == (1,)


def test_batch_effective_qos_is_max_over_frozen_members():
    members = [
        member(1, "PUBLISH", ready=100, qos=1),
        member(2, "PUBLISH", ready=200, qos=3, deadline=MAX_TICK),
        member(3, "PUBLISH", ready=150, qos=2, deadline=MAX_TICK),
    ]
    decision = decide(members, limits(), now=1000,
                      profiles=lambda key: counts(1, 3))
    assert decision.members == (2, 3, 1)[:decision.member_count]
    assert decision.member_count == 3
    assert decision.batch_effective_qos == 3


def test_clock_period_scales_wait_but_not_ready_tick():
    members = [member(1, "DECODE", ready=100)]
    for now, expected in [(600, "WAIT"), (10099, "WAIT"), (10100, "RUN")]:
        decision = decide(members, limits(batch_wait_cycles=20), now,
                          lambda key: counts(1, 2), clock_period_ticks=500)
        assert decision.action == expected
        if expected == "WAIT":
            assert decision.wait_until == 10100


def test_clock_period_scales_prefill_starvation():
    members = [member(1, "PREFILL", ready=100),
               member(2, "DECODE", ready=100)]
    for now, phase in [(10099, "DECODE"), (10100, "PREFILL")]:
        decision = decide(members, limits(prefill_starvation_cycles=20),
                          now, lambda key: counts(1), clock_period_ticks=500)
        assert decision.phase == phase


def test_clock_scaled_wait_saturates_without_wrap():
    decision = decide([member(1, "DECODE", ready=100)],
                      limits(batch_wait_cycles=MAX_TICK), 600,
                      lambda key: counts(1, 2), clock_period_ticks=500)
    assert decision.action == "WAIT"
    assert decision.wait_until == MAX_TICK

from dataclasses import astuple, replace
from pathlib import Path

from mesh_ir.serving_scheduler import MAX_TICK, SchedulerLimits, SchedulerMember, decide

GOLDEN = Path(__file__).resolve().parents[4] / 'tests/gem5/ai_mesh/fixtures/gate6/scheduler.golden'
PHASES = ('PREFILL', 'DECODE', 'PUBLISH')
ACTIONS = ('RUN', 'WAIT', 'IDLE', 'FAIL_HEAD')


def scheduler_golden():
    limits = SchedulerLimits(4, 64, 32, 4, 20, 20)
    prefill = SchedulerMember(1, 'PREFILL', 100, None, 1, 1, 1, 'INITIAL', 8, 0, 0, 0)
    decode = replace(prefill, request_id=2, phase='DECODE', kv_tokens_before=8,
                     decode_chunk_tokens=1)
    publish = replace(prefill, request_id=3, phase='PUBLISH')
    cases = [
        (101, [prefill, decode, publish], (1,)),
        (119, [prefill, decode], (1,)),
        (120, [prefill, decode], (1,)),
        (121, [prefill, decode], (1,)),
        (119, [decode], (1, 2)),
        (120, [decode], (1, 2)),
        (121, [decode], (1, 2)),
        (119, [decode], (2,)),
        (120, [decode], (2,)),
        (121, [decode], (2,)),
        (101, [publish], (1, 2)),
        (101, [replace(publish, deadline_tick=102),
               replace(publish, request_id=4, qos=255)], (2,)),
        (121, [decode, replace(decode, request_id=4, request_profile_id=2),
               replace(decode, request_id=5)], (1, 2, 3)),
        (121, [], (1,)),
    ]
    lines = [str(len(cases))]
    for now, members, counts in cases:
        fields = [now, 1, *astuple(limits)[:-1], len(counts), *counts, len(members)]
        for member in members:
            fields.extend((member.request_id, PHASES.index(member.phase),
                           member.phase_ready_tick,
                           member.deadline_tick if member.deadline_tick is not None else MAX_TICK,
                           member.qos, member.program_id, member.request_profile_id, 0,
                           member.valid_tokens_per_member, member.kv_tokens_before,
                           member.decode_chunk_tokens, member.logical_source_rank))
        decision = decide(members, limits, now, lambda key: frozenset(counts))
        fields.extend((ACTIONS.index(decision.action),
                       PHASES.index(decision.phase) if decision.phase else 0,
                       decision.member_count, decision.batch_effective_qos or 0,
                       decision.wait_until if decision.wait_until is not None else MAX_TICK,
                       len(decision.members), *decision.members))
        lines.append(' '.join(map(str, fields)))
    return '\n'.join(lines) + '\n'


def test_scheduler_golden_matches_independent_decisions():
    assert GOLDEN.read_text() == scheduler_golden()

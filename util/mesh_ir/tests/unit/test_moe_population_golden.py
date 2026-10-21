from pathlib import Path
from types import SimpleNamespace

from mesh_ir.generated import abi as A
from mesh_ir.moe_provider import FrozenToken, apply_capacity, uniform_select
from mesh_ir.moe_uid import SemanticTokenUid

GOLDEN = Path(__file__).resolve().parents[4] / 'tests/gem5/ai_mesh/fixtures/gate6/moe_population.golden'


def population_golden():
    layer = SimpleNamespace(layer_id=7, expert_count=4, top_k=2,
                            capacity_factor_q16=65536,
                            overflow_policy=A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY)
    tokens = [FrozenToken(SemanticTokenUid(member + 1, member, 0, 0, 1, 0, ordinal),
                          str(member), member, ordinal)
              for member in range(2) for ordinal in range(4)]
    plan = uniform_select(layer, tokens, bytes(32), 17)
    capacity = apply_capacity(plan, layer, len(tokens))
    lines = ['16 4']
    for route in plan.routes:
        lines.append(' '.join(map(str, (route.uid.workload_plan_item_id,
                     route.uid.token_ordinal, route.topk_slot,
                     route.selected_expert_id, int(route in capacity.accepted)))))
    lines.append(' '.join(map(str, capacity.expert_loads)))
    lines.append(' '.join(map(str, capacity.padded_slots_by_expert)))
    return '\n'.join(lines) + '\n'


def test_population_golden_matches_independent_provider():
    assert GOLDEN.read_text() == population_golden()

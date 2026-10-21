import hashlib
import json

from mesh_ir.architecture import load_arch
from mesh_ir.model import canonical_json_bytes
from mesh_ir.moe_provider import population_digest
from mesh_ir.moe_uid import SemanticTokenUid
from mesh_ir.serving_moe_provider import ServingMoeProvider
from mesh_ir.serving_programs import moe_full_view_serving_program


def test_runtime_histogram_adapter_partitions_real_tokens_by_route_window(tmp_path):
    program = moe_full_view_serving_program(load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml'))
    population = [(SemanticTokenUid(member + 1, member, 0, 0, 1, 0, ordinal), member)
                  for member in range(2) for ordinal in range(8)]
    document = dict(schema='moe_histogram_replay_v1', version=1,
        workload_plan_digest='aa' * 32, mesh_program_digest=program.semantic_sha256,
        route_window_tokens=4,
        layers=[dict(layer_id=layer.layer_id, expert_count=layer.expert_count, top_k=layer.top_k)
                for layer in program.moe_layer_specs], records=[])
    for window in range(2):
        tokens = [(uid, 0) for uid, _ in population if uid.token_ordinal // 4 == window]
        document['records'].append(dict(layer_id=2, phase=1, window_ordinal=window,
            population_digest=population_digest(2, 1, window, tokens),
            source_token_counts=[8], source_expert_counts=[[8, 8]]))
    document['route_profile_digest'] = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(document))
    provider = ServingMoeProvider(program, 'aa' * 32, 'histogram_replay', path, tmp_path)
    rows = [[uid.workload_plan_item_id, uid.user_id, uid.task_seq, uid.repair_round,
             uid.phase, uid.sequence_ordinal, uid.token_ordinal, member, 0]
            for uid, member in population]
    assert provider(2, rows) == [0, 1] * 16
    assert len(list(tmp_path.glob('provider_selection_*.json'))) == 2

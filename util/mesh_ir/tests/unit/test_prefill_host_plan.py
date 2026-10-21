import json

import pytest

from mesh_ir.agent_plan_image import build_agent_plan_image, decode_request_binding_section, encode_request_binding_section
from mesh_ir.agent_planning import ArenaRegion, build_command_identity_plan, capacity_counts
from mesh_ir.agent_workload import PlanError, load_workload_plan
from mesh_ir.host_bindings import build_host_binding_plans
from tests.integration.support.workspace_program import build_workspace_program, workspace_workload_document
from tests.unit.test_agent_arena import regions


def test_prefill_host_plan_carries_publish_contract_without_surrogate(tmp_path):
    program, arch = build_workspace_program()
    source = tmp_path / 'workload.json'
    source.write_text(json.dumps(workspace_workload_document(program)))
    workload = load_workload_plan(source)
    plans = build_host_binding_plans(program, [row.base for row in arch.regions])
    assert plans[0].publish_chunk_bytes == program.agent_request_profiles[0].publish_chunk_bytes
    wire = decode_request_binding_section(encode_request_binding_section(0, plans))
    assert wire['plans'][0]['publish_chunk_bytes'] == 64
    identity = build_command_identity_plan(workload, None, 'AUTO_PER_TASK')
    assert [row['command_kind'] for row in identity['records']] == ['GENERATE']
    assert identity['records'][0]['kv_handle_or_zero'] == 0
    assert capacity_counts(workload, None, 'AUTO_PER_TASK')['A'] == 0
    image, _ = build_agent_plan_image(workload, None, 'AUTO_PER_TASK',
        regions() + [ArenaRegion('WORKSPACE', 0x800200000, 4096, 64)], binding_plans=plans)
    assert image[:4] == b'AGPI'


@pytest.mark.parametrize('field,value', [('initial_kv_generation', 1), ('output_tokens', 1),
    ('kv_required_tokens_after_round', 4), ('expected_cached_tokens', 1)])
def test_prefill_workload_rejects_kv_state(tmp_path, field, value):
    program, _ = build_workspace_program()
    document = workspace_workload_document(program)
    task = document['users'][0]['tasks'][0]
    (task if field == 'initial_kv_generation' else task['rounds'][0])[field] = value
    path = tmp_path / 'workload.json'
    path.write_text(json.dumps(document))
    with pytest.raises(PlanError):
        load_workload_plan(path)

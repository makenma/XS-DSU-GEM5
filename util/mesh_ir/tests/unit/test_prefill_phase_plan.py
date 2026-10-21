from dataclasses import replace

import pytest

from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.model import MeshIrError
from mesh_ir.serving_oracle import generate_phase_plan
from mesh_ir.serving_profiles import planned_instance_sequence, verify_path_closure


@pytest.fixture(scope='module')
def profile_tables():
    from tests.integration.support.workspace_program import build_workspace_program
    return build_workspace_program(output_rows=8)[0]


def test_prefill_phase_tables_preserve_input_work_and_zero_kv_cursor(profile_tables):
    request = profile_tables.agent_request_profiles[0]
    verify_path_closure(profile_tables, request)
    selected = planned_instance_sequence(profile_tables, request, A.PATH_KIND.INITIAL_PREFILL)
    plan = generate_phase_plan(profile_tables, request, A.PATH_KIND.INITIAL_PREFILL)
    assert [row.phase for row in selected] == [A.PHASE.PREFILL, A.PHASE.PUBLISH]
    assert [row.instance_profile_id for row in selected] == [row.instance_profile_id for row in plan]
    assert [row.valid_tokens_per_member for row in plan] == [8, 0]
    assert [row.cached_tokens_after for row in plan] == [0, 0]
    assert all(row.kv_tokens_before == row.kv_read_bytes_per_member == row.kv_write_bytes_per_member == 0 for row in plan)
    assert plan[0].host_input_dma_bytes_per_member == 128
    assert plan[-1].host_output_dma_bytes_per_member == 128


@pytest.mark.parametrize('field,value', [
    ('flags', 2), ('flags', A.AGENT_REQUEST_FLAGS.HAS_KV),
    ('output_tokens', 1), ('primary_kv_symbol_id', 1),
    ('kv_bytes_per_token', 16), ('expected_cached_tokens', 1),
    ('path_mask', 3),
])
@pytest.mark.parametrize('planner', [verify_path_closure, planned_instance_sequence, generate_phase_plan])
def test_prefill_phase_tables_reject_generation_state(profile_tables, field, value, planner):
    request = replace(profile_tables.agent_request_profiles[0], **{field: value})
    with pytest.raises(MeshIrError, match='E_REQUEST_PROFILE'):
        if planner is verify_path_closure:
            planner(profile_tables, request)
        else:
            planner(profile_tables, request, A.PATH_KIND.INITIAL_PREFILL)


@pytest.mark.parametrize('planner', [verify_path_closure, planned_instance_sequence, generate_phase_plan])
@pytest.mark.parametrize('mutation', ['missing-publish', 'publish-cursor', 'hidden-decode', 'kv-primary', 'kv-read', 'kv-member', 'kv-tensor', 'kv-requirement'])
def test_prefill_phase_tables_reject_broken_closure(profile_tables, planner, mutation):
    instances = list(profile_tables.agent_instance_profiles)
    if mutation == 'missing-publish':
        instances.pop()
    elif mutation == 'publish-cursor':
        instances[-1] = replace(instances[-1], kv_tokens_before=8)
    elif mutation == 'hidden-decode':
        instances.append(replace(instances[0], instance_profile_id=100,
            phase=A.PHASE.DECODE, decode_chunk_tokens=1))
    elif mutation == 'kv-primary':
        instances[0] = replace(instances[0], primary_kv_symbol_id=9)
    elif mutation == 'kv-read':
        instances[0] = replace(instances[0], kv_read_bytes_per_member=16)
    program = replace(profile_tables, agent_instance_profiles=tuple(instances))
    if mutation == 'kv-member':
        first, *rest = program.agent_instance_member_bindings
        program = replace(program, agent_instance_member_bindings=(replace(first, static_kv_symbol_id=9), *rest))
    elif mutation == 'kv-tensor':
        first, *rest = program.agent_instance_tensor_bindings
        program = replace(program, agent_instance_tensor_bindings=(replace(first, binding_kind=AG.BINDING_KIND.KV_EXTERNAL), *rest))
    elif mutation == 'kv-requirement':
        first, *rest = program.agent_request_binding_requirements
        program = replace(program, agent_request_binding_requirements=(replace(first, binding_kind=AG.BINDING_KIND.KV_EXTERNAL), *rest))
    request = program.agent_request_profiles[0]
    with pytest.raises(MeshIrError, match='E_REQUEST_PROFILE'):
        if planner is verify_path_closure:
            planner(program, request)
        else:
            planner(program, request, A.PATH_KIND.INITIAL_PREFILL)

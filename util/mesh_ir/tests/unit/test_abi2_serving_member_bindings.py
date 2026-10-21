from dataclasses import replace

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.model import AgentInstanceMemberBinding, AgentInstanceProfile, AgentRequestBindingRequirement, AgentRequestProfile, AgentSourceCoreMap, StringEntry
from mesh_ir.serving_programs import project_member_tensor_bindings
from mesh_ir.serving_profiles import HOST_LOAD, HOST_STORE, descriptor_attribution, instance_io_intervals, member_intervals, verify_member_ownership
from tests.unit.test_abi2_binding_catalog import shared_symbols


from tests.golden.support.records import record


@pytest.fixture(scope='module')
def member_program(shared_symbols):
    program = shared_symbols
    strings = program.strings + (StringEntry('request.input'), StringEntry('request.output'))
    input_id, output_id = len(strings) - 1, len(strings)
    symbols = {item.value: index for index, item in enumerate(strings, 1)}
    request = record(AgentRequestProfile, program_id=1, profile_id=1, input_binding_bytes=16,
                     full_input_dma_bytes=16, delta_input_dma_bytes=16, host_output_bytes=16,
                     primary_input_symbol_id=input_id, primary_output_symbol_id=output_id,
                     source_rank_count=1)
    members, instances = [], []
    for ordinal, variant in enumerate(program.semantics.variants):
        instances.append(record(AgentInstanceProfile, instance_profile_id=ordinal + 1,
                                request_program_id=1, request_profile_id=1, member_count=1,
                                mesh_entrypoint_id=variant.entrypoint_id, mesh_profile_id=variant.profile_id,
                                member_binding_first=ordinal, member_binding_count=1,
                                host_input_dma_bytes_per_member=16, host_output_dma_bytes_per_member=16,
                                primary_input_symbol_id=input_id, primary_output_symbol_id=output_id))
        members.append(AgentInstanceMemberBinding(ordinal + 1, 0, 0, symbols['input'], symbols['output'], 0, 0))
    requirements = (AgentRequestBindingRequirement(1, 1, AG.BINDING_KIND.HOST_INPUT, AG.BINDING_FLAGS.READ, input_id),
                    AgentRequestBindingRequirement(1, 1, AG.BINDING_KIND.HOST_OUTPUT, AG.BINDING_FLAGS.WRITE, output_id))
    program = replace(program, strings=strings, agent_request_profiles=(request,),
                   agent_instance_profiles=tuple(instances), agent_instance_member_bindings=tuple(members),
                   agent_request_binding_requirements=requirements, agent_source_core_map=(AgentSourceCoreMap(3),))
    return replace(program, agent_instance_tensor_bindings=project_member_tensor_bindings(program))


@pytest.mark.parametrize('index', [0, 1])
def test_member_binding_uses_variant_slot_and_object_relative_offsets(member_program, index):
    program = member_program
    request, = program.agent_request_profiles
    instance = program.agent_instance_profiles[index]
    windows = member_intervals(program, request, instance, 0)
    assert windows[HOST_LOAD][0][1:] == (0, 16)
    assert windows[HOST_STORE][0][1:] == (0, 16)
    io = instance_io_intervals(program, request, instance)
    assert io['intervals'][HOST_LOAD] == [(0, 16)]
    assert io['intervals'][HOST_STORE] == [(0, 16)]
    attribution = descriptor_attribution(program, request, instance)
    owned = program.semantics.variants[index].membership.descriptors
    assert attribution == {(owned.first_id, HOST_LOAD): 0, (owned.first_id + 1, HOST_STORE): 0}


@pytest.mark.parametrize('mutation', ['role-swap', 'missing-variant', 'missing-requirement', 'foreign-primary'])
def test_member_binding_rejects_role_or_scope_substitution(member_program, mutation):
    program = member_program
    request, = program.agent_request_profiles
    instance = program.agent_instance_profiles[1]
    if mutation == 'role-swap':
        first, second = program.agent_instance_member_bindings
        program = replace(program, agent_instance_member_bindings=(first, replace(second, static_input_symbol_id=second.static_output_symbol_id)))
    elif mutation == 'missing-variant':
        instance = replace(instance, mesh_profile_id=99)
    elif mutation == 'missing-requirement':
        program = replace(program, agent_request_binding_requirements=program.agent_request_binding_requirements[1:])
    else:
        instance = replace(instance, primary_input_symbol_id=request.primary_output_symbol_id)
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE|E_RELOCATION'):
        instance_io_intervals(program, request, instance)


def test_member_windows_cannot_claim_past_admitted_object(member_program):
    request, = member_program.agent_request_profiles
    instance = replace(member_program.agent_instance_profiles[0], host_input_dma_bytes_per_member=17)
    program = replace(member_program, agent_instance_profiles=(instance, *member_program.agent_instance_profiles[1:]))
    with pytest.raises(MeshIrError, match='E_HOST_IO_SIZE_MISMATCH'):
        from mesh_ir.serving_profiles import verify_instance_io
        verify_instance_io(program, request, instance)


def test_same_core_members_cannot_alias_reference_slot(member_program):
    request, = member_program.agent_request_profiles
    instance = replace(member_program.agent_instance_profiles[0], member_count=2, member_binding_count=2)
    first = member_program.agent_instance_member_bindings[0]
    second = replace(first, member_ordinal=1)
    program = replace(member_program, agent_instance_profiles=(instance,), agent_instance_member_bindings=(first, second))
    program = replace(program, agent_instance_tensor_bindings=project_member_tensor_bindings(program))
    with pytest.raises(MeshIrError, match='member tensor slots alias'):
        verify_member_ownership(program, request, instance)

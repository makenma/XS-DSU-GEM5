from dataclasses import replace

import pytest

from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.host_bindings import build_host_binding_plans
from mesh_ir.ir.common import TensorRole
from mesh_ir.model import AgentInstanceMemberBinding, AgentInstanceProfile, AgentRequestBindingRequirement, AgentRequestProfile
from mesh_ir.scheduled.verify import verify_program
from tests.unit.test_abi2_moe_bindings import moe_profiles
from tests.golden.support.records import record


@pytest.fixture(scope='module')
def host_program(moe_profiles):
    program, arch = moe_profiles
    tensors = list(program.tensors)
    tensors[2] = replace(tensors[2], content_sha256=tensors[0].content_sha256)
    logical = list(program.semantics.kernel_tensors)
    logical[2] = replace(logical[2], content_sha256=logical[0].content_sha256)
    digests = list(program.content_digests)
    digests[1] = replace(digests[1], digest=digests[0].digest)
    program = replace(program, tensors=tuple(tensors), content_digests=tuple(digests),
                      semantics=replace(program.semantics, kernel_tensors=tuple(logical)))
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    assert verify_program(program, arch).program is program
    symbol = program.moe_expert_specs[0].weight_symbol_id
    request = record(AgentRequestProfile, program_id=1, profile_id=1, path_mask=1,
                     full_input_tokens=1, output_tokens=1, source_rank_count=1,
                     flags=A.AGENT_REQUEST_FLAGS.HAS_KV, kv_bytes_per_token=16,
                     primary_kv_symbol_id=1)
    instances, members = [], []
    for ordinal, phase in enumerate((A.PHASE.PREFILL, A.PHASE.DECODE, A.PHASE.PUBLISH)):
        instances.append(record(AgentInstanceProfile, instance_profile_id=ordinal + 1,
                                request_program_id=1, request_profile_id=1, phase=phase,
                                member_count=1, valid_tokens_per_member=1 if phase != A.PHASE.PUBLISH else 0,
                                kv_tokens_before=ordinal,
                                decode_chunk_tokens=1 if phase == A.PHASE.DECODE else 0,
                                mesh_entrypoint_id=1, mesh_profile_id=1 + ordinal % 2,
                                member_binding_first=ordinal, member_binding_count=1))
        members.append(AgentInstanceMemberBinding(ordinal + 1, 0, 0, 0, 0, 0, 0))
    requirement = AgentRequestBindingRequirement(1, 1, AG.BINDING_KIND.WEIGHT_EXTERNAL,
                    AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.PERSISTENT, symbol)
    return replace(program, agent_request_profiles=(request,), agent_instance_profiles=tuple(instances),
                   agent_instance_member_bindings=tuple(members), agent_request_binding_requirements=(requirement,)), arch


def test_host_weight_plan_uses_scoped_slot_capacity(host_program):
    program, arch = host_program
    plan, = build_host_binding_plans(program, [region.base for region in arch.regions])
    weight, = plan.requirements
    slot = program.semantics.binding_slots[0].reference_binding
    assert weight.platform_address == arch.regions[slot.region_id].base + slot.allocation_offset_bytes
    assert weight.required_bytes == slot.allocation_size_bytes
    assert plan.instance_counts == (3,)


@pytest.mark.parametrize('mutation', ['range', 'capacity', 'content', 'role', 'unknown-variant'])
def test_host_weight_plan_rejects_incoherent_variant_owner(host_program, mutation):
    program, arch = host_program
    if mutation in ('range', 'capacity'):
        slots = list(program.semantics.binding_slots)
        reference = slots[2].reference_binding
        reference = replace(reference, **({'allocation_offset_bytes': 128} if mutation == 'range' else {'allocation_size_bytes': 64}))
        slots[2] = replace(slots[2], reference_binding=reference)
        program = replace(program, semantics=replace(program.semantics, binding_slots=tuple(slots)))
    elif mutation == 'unknown-variant':
        instances = list(program.agent_instance_profiles)
        instances[1] = replace(instances[1], mesh_profile_id=99)
        program = replace(program, agent_instance_profiles=tuple(instances))
    else:
        tensors = list(program.semantics.kernel_tensors)
        tensors[2] = replace(tensors[2], **({'content_sha256': 'ab' * 32} if mutation == 'content' else {'role': TensorRole.INPUT}))
        program = replace(program, semantics=replace(program.semantics, kernel_tensors=tuple(tensors)))
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE|E_RELOCATION'):
        build_host_binding_plans(program, [region.base for region in arch.regions])

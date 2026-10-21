from dataclasses import replace
import subprocess

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.execution_view import semantic_profile_ranges
from mesh_ir.ir.common import Access, TensorRole
from mesh_ir.kv_layout import kv_layout_digest, kv_layout_projection
from mesh_ir.model import AgentInstanceMemberBinding, AgentInstanceProfile, AgentRequestProfile, StringEntry
from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.scheduled.verify import verify_program
from tests.unit.test_abi2_codec import binary_probe
from tests.golden.support.records import record
from tests.unit.test_gate2_authored_facade import _arch, _inputs


@pytest.fixture(scope='module')
def kv_profiles():
    arch = _arch()
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('abi2', 'kv-profiles', 1))
    for name in ('first', 'second'):
        records, allocations, backings, slots = _inputs(arch)
        slots = (replace(slots[0], access=Access.READ_WRITE, reference_binding=replace(slots[0].reference_binding, access=Access.READ_WRITE)), slots[1])
        records = replace(records, tensors=(replace(records.tensors[0], role=TensorRole.KV_CACHE), *records.tensors[1:]))
        variant = builder.variant('forward', name, name, records=records, allocations=allocations, external_backings=backings, binding_slots=slots)
        stream = variant.stream(3, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
        stream.control_command(RequestBeginAttrs())
        for operation in (9, 10, 11):
            stream.kernel_command(operation)
        stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    program = builder.build()
    assert verify_program(program, arch).program is program
    symbol = next(index for index, item in enumerate(program.strings, 1) if item.value == 'input')
    primary = len(program.strings) + 1
    request = record(AgentRequestProfile, program_id=1, profile_id=1, primary_kv_symbol_id=primary,
                     kv_bytes_per_token=4, full_input_tokens=3, output_tokens=1)
    instances, members = [], []
    for index, variant in enumerate(program.semantics.variants):
        instances.append(record(AgentInstanceProfile, instance_profile_id=index + 1,
                         request_program_id=1, request_profile_id=1, primary_kv_symbol_id=primary, phase=A.PHASE.PREFILL,
                         mesh_entrypoint_id=variant.entrypoint_id, mesh_profile_id=variant.profile_id,
                         member_count=1, member_binding_first=index, member_binding_count=1))
        members.append(AgentInstanceMemberBinding(index + 1, 0, 0, 0, 0, symbol, 0))
    program = replace(program, required_features=13, profile_stream_ranges=semantic_profile_ranges(program.semantics),
                      strings=program.strings + (StringEntry('request.kv'),),
                      agent_request_profiles=(request,), agent_instance_profiles=tuple(instances),
                      agent_instance_member_bindings=tuple(members))
    return program


@pytest.mark.parametrize('large_value', [16, (1 << 63) + 1])
def test_cpp_kv_layout_digest_matches_python_canonical_fields(binary_probe, tmp_path, kv_profiles, large_value):
    program = kv_profiles
    relocations = tuple(replace(row, offset_bytes=large_value) if row.tensor_id in (1, 3) else row for row in program.relocations)
    slots = tuple(replace(slot, reference_binding=replace(slot.reference_binding, allocation_offset_bytes=large_value)) if slot.slot_id in (1, 3) else slot for slot in program.semantics.binding_slots)
    program = replace(program, relocations=relocations, semantics=replace(program.semantics, binding_slots=slots))
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    projection = kv_layout_projection(program)
    assert len(projection['bindings']) == 2
    assert len(projection['descriptors']) == 2
    source = tmp_path / 'input.mshb'
    source.write_bytes(encode_program(program))
    result = subprocess.run([str(binary_probe), str(source), str(tmp_path / 'out.mshb'), str(tmp_path / 'out.json'), 'kv-layout'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == kv_layout_digest(program).hex()


@pytest.mark.parametrize('mutation', ['foreign-primary', 'unknown-variant', 'foreign-member', 'non-kv-slot', 'missing-member', 'missing-request'])
def test_kv_layout_rejects_broken_request_instance_member_chain(binary_probe, tmp_path, kv_profiles, mutation):
    program = kv_profiles
    first, second = program.agent_instance_profiles
    if mutation == 'foreign-primary':
        program = replace(program, agent_instance_profiles=(first, replace(second, primary_kv_symbol_id=1)))
    elif mutation == 'unknown-variant':
        program = replace(program, agent_instance_profiles=(first, replace(second, mesh_profile_id=99)))
    elif mutation == 'foreign-member':
        members = list(program.agent_instance_member_bindings)
        members[1] = replace(members[1], instance_profile_id=1)
        program = replace(program, agent_instance_member_bindings=tuple(members))
    elif mutation == 'non-kv-slot':
        members = list(program.agent_instance_member_bindings)
        symbol = next(index for index, item in enumerate(program.strings, 1) if item.value == 'output')
        members[1] = replace(members[1], static_kv_symbol_id=symbol)
        program = replace(program, agent_instance_member_bindings=tuple(members))
    elif mutation == 'missing-member':
        program = replace(program, agent_instance_member_bindings=program.agent_instance_member_bindings[:1])
    else:
        program = replace(program, agent_request_profiles=())
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    with pytest.raises(MeshIrError, match='E_KV_CONTRACT_MISMATCH'):
        kv_layout_digest(program)
    source = tmp_path / 'input.mshb'
    source.write_bytes(encode_program(program))
    result = subprocess.run([str(binary_probe), str(source), str(tmp_path / 'out.mshb'), str(tmp_path / 'out.json'), 'kv-layout'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'E_KV_CONTRACT_MISMATCH' in result.stdout

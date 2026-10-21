from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.moe_programs import moe_min_program
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.verify import verify_program


def test_minimum_moe_uses_scheduled_weight_and_gate_owners():
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    program = moe_min_program(arch)
    assert verify_program(program, arch).program is program
    assert program.required_features == A.SCHEDULED_SEMANTICS | A.DYNAMIC_MOE_V1
    variant, = program.semantics.variants
    weight = ProgramBindingCatalog(program).binding(variant.entrypoint_id, variant.profile_id, 'weight')
    assert weight.tensor.content_sha256 is not None
    assert len(program.moe_expert_specs) == 2
    for expert in program.moe_expert_specs:
        assert expert.weight_region_offset + expert.weight_bytes <= weight.object.footprint_bytes
        digest = program.content_digests[expert.weight_digest_index]
        assert digest.object_id == weight.tensor.tensor_id
        assert digest.digest.hex() == weight.tensor.content_sha256
    gate, = program.moe_dynamic_regions
    insert = next(row for row in program.commands if row.command_id == gate.insert_after_command_id)
    resume = next(row for row in program.commands if row.command_id == gate.resume_before_command_id)
    assert insert.opcode == A.OPCODE.GEMM
    assert resume.opcode == A.OPCODE.DMA_STORE
    assert insert.signal_event == gate.entry_event_id
    assert decode_program(encode_program(program)) == program


import subprocess
import pytest
from tests.unit.test_abi2_admission import admission_probe


@pytest.mark.parametrize('admission_probe', ['serving'], indirect=True)
def test_cpp_admits_the_minimum_scheduled_moe(admission_probe, tmp_path):
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    path = tmp_path / 'moe.mshb'
    path.write_bytes(encode_program(moe_min_program(arch)))
    result = subprocess.run([str(admission_probe), str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'ACCEPTED'


@pytest.mark.parametrize('name,arch_name,cores,experts', [
    ('moe_dual', 'mesh_1x2_moe', 2, 2),
    ('moe_dual_drop', 'mesh_1x2_moe', 2, 2),
    ('moe_dual_copy', 'mesh_1x2_moe', 2, 2),
    ('moe_quad', 'mesh_4x4_moe', 16, 16),
    ('moe_multi', 'mesh_1x2_moe', 1, 6),
])
def test_standalone_moe_has_one_admitted_static_and_dynamic_contract(name, arch_name, cores, experts):
    from mesh_ir import moe_programs
    from mesh_ir.abi.moe_verifier import verify_moe_v1

    arch = load_arch(f'configs/example/ai_mesh/arch/{arch_name}.yaml')
    program = getattr(moe_programs, name + '_program')(arch)
    assert verify_program(program, arch).program is program
    verify_moe_v1(program, arch)
    assert program.required_features == A.SCHEDULED_SEMANTICS | A.DYNAMIC_MOE_V1
    assert len({row.core_id for row in program.commands}) == cores
    assert len(program.moe_expert_specs) == experts
    assert decode_program(encode_program(program)) == program
    catalog = ProgramBindingCatalog(program)
    variant, = program.semantics.variants
    weight = catalog.binding(variant.entrypoint_id, variant.profile_id, 'weight')
    assert all(row.weight_region_offset + row.weight_bytes <= weight.object.footprint_bytes for row in program.moe_expert_specs)
    commands = {row.command_id: row for row in program.commands}
    for region in program.moe_dynamic_regions:
        insert, resume = (commands[key] for key in (region.insert_after_command_id, region.resume_before_command_id))
        assert insert.core_id == resume.core_id == region.core_id
        assert insert.opcode == A.OPCODE.GEMM
        assert insert.signal_event == region.entry_event_id
    if cores > 1:
        pushes = [row for row in program.dma_descriptors if row.kind == A.DMA_KIND.P2P_PUSH]
        assert len(pushes) == cores // 2
        assert all(row.useful_bytes == 128 for row in pushes)
        assert {(row.src.owner_core, row.dst.owner_core) for row in pushes} == {(core + 1, core) for core in range(0, cores, 2)}


@pytest.mark.parametrize('admission_probe,name,arch_name', [
    ('serving', 'moe_dual', 'mesh_1x2_moe'),
    ('serving', 'moe_dual_drop', 'mesh_1x2_moe'),
    ('serving', 'moe_dual_copy', 'mesh_1x2_moe'),
    ('serving', 'moe_multi', 'mesh_1x2_moe'),
    ('quad', 'moe_quad', 'mesh_4x4_moe'),
], indirect=['admission_probe'])
def test_cpp_admits_each_standalone_moe(admission_probe, name, arch_name, tmp_path):
    from mesh_ir import moe_programs

    arch = load_arch(f'configs/example/ai_mesh/arch/{arch_name}.yaml')
    program = getattr(moe_programs, name + '_program')(arch)
    path = tmp_path / 'program.mshb'
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'ACCEPTED'


@pytest.mark.parametrize('name', ['moe_dual', 'moe_multi'])
def test_member_slices_resolve_the_layer_gate_local_operands(name):
    from mesh_ir import moe_programs
    from mesh_ir.moe_materializer import member_slice

    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    program = getattr(moe_programs, name + '_program')(arch)
    commands = {row.command_id: row for row in program.commands}
    shards = {row.shard_id: row for row in program.shards}
    tensors = {row.tensor_id: row for row in program.tensors}
    for layer in program.moe_layer_specs:
        for region in program.moe_dynamic_regions[layer.dynamic_region_first:layer.dynamic_region_first + layer.dynamic_region_count]:
            member = member_slice(program, layer, 'm', 1, region.core_id)
            gate = commands[region.insert_after_command_id]
            operands = program.command_operands[gate.operand_begin:gate.operand_begin + gate.operand_count]
            source, = [row for row in operands if row.access == A.ACCESS_KIND.READ_ONLY and tensors[row.tensor_id].role == A.TENSOR_ROLE.INPUT]
            assert member.input_allocation == source.allocation_id
            assert member.input_offset == shards[source.shard_id].allocation_offset
            destination, = [row for row in operands if row.access == A.ACCESS_KIND.READ_WRITE]
            assert member.output_allocation == destination.allocation_id
            assert member.output_offset == shards[destination.shard_id].allocation_offset
    if name == 'moe_multi':
        slices = [member_slice(program, layer, 'm', 1, 0) for layer in program.moe_layer_specs]
        assert len({row.input_allocation for row in slices}) == 2
        assert len({row.output_allocation for row in slices}) == 2

import pytest
from tests.unit.test_abi2_admission import admission_probe

from mesh_ir.architecture import load_arch
from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.generated import abi as A
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.serving_profiles import apply_request_profile_keys
from mesh_ir.serving_programs import full_view_serving_program, repair_serving_program, serving_program, split_kv_serving_program


@pytest.mark.parametrize('factory,args', [(serving_program, ()), (serving_program, (2,)),
    (full_view_serving_program, (2,)), (repair_serving_program, ()), (split_kv_serving_program, ())])
def test_serving_factory_uses_admitted_scheduled_variants(factory, args):
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2.yaml')
    program = apply_request_profile_keys(factory(arch, *args))
    assert verify_program(program, arch).program is program
    assert program.required_features & A.SCHEDULED_SEMANTICS
    assert len(program.semantics.variants) == len(program.agent_instance_profiles)
    catalog = ProgramBindingCatalog(program)
    for instance in program.agent_instance_profiles:
        bindings = catalog.for_variant(instance.mesh_entrypoint_id, instance.mesh_profile_id)
        members = program.agent_instance_member_bindings[instance.member_binding_first:instance.member_binding_first + instance.member_binding_count]
        for member in members:
            for field in ('static_input_symbol_id', 'static_output_symbol_id', 'static_kv_symbol_id'):
                symbol = getattr(member, field)
                if symbol:
                    assert program.strings[symbol - 1].value in bindings
    blob = encode_program(program)
    assert encode_program(decode_program(blob)) == blob


def test_serving_moe_layers_own_scheduled_prefill_gates():
    from mesh_ir.serving_programs import moe_full_view_serving_program

    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    program = apply_request_profile_keys(moe_full_view_serving_program(arch))
    assert verify_program(program, arch).program is program
    assert len(program.moe_layer_specs) == 2
    owners = {command: variant for variant in program.semantics.variants
        for command in range(variant.membership.commands.first_id, variant.membership.commands.first_id + variant.membership.commands.count)}
    commands = {item.command_id: item for item in program.commands}
    for region in program.moe_dynamic_regions:
        insert = commands[region.insert_after_command_id]
        resume = commands[region.resume_before_command_id]
        assert owners[insert.command_id] is owners[resume.command_id]
        assert resume.opcode == A.OPCODE.GEMM
        assert insert.signal_event == region.entry_event_id


def test_serving_moe_gate_waits_for_every_member_load():
    from mesh_ir.execution_view import build_execution_views
    from mesh_ir.serving_programs import moe_full_view_serving_program

    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    program = moe_full_view_serving_program(arch)
    views = build_execution_views(program)
    commands = {item.command_id: item for item in program.commands}
    producers = {item.signal_event: item.command_id for item in program.commands if item.signal_event}
    for instance in program.agent_instance_profiles:
        if instance.phase != A.PHASE.PREFILL:
            continue
        view = views.for_instance(instance.mesh_entrypoint_id, instance.mesh_profile_id)
        ids = {program.commands[index].command_id for index in view.command_indices}
        region = next(item for item in program.moe_dynamic_regions if item.insert_after_command_id in ids)
        reached, pending = set(), [region.insert_after_command_id]
        while pending:
            command_id = pending.pop()
            if command_id in reached:
                continue
            reached.add(command_id)
            command = commands[command_id]
            pending.extend(producers[item.event_id] for item in program.command_waits[command.wait_begin:command.wait_begin + command.wait_count])
        loads = {item.command_id for item in program.dma_descriptors if item.command_id in ids and item.kind == A.DMA_KIND.LOAD}
        assert loads <= reached, (loads, reached)


@pytest.mark.parametrize('admission_probe', ['serving'], indirect=True)
def test_cpp_admits_authored_serving_profiles(admission_probe, tmp_path):
    import subprocess
    from mesh_ir.serving_programs import moe_full_view_serving_program

    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    for factory in (serving_program, full_view_serving_program, repair_serving_program, moe_full_view_serving_program):
        program = apply_request_profile_keys(factory(arch))
        assert verify_program(program, arch).program is program
        path = tmp_path / (factory.__name__ + '.mshb')
        path.write_bytes(encode_program(program))
        result = subprocess.run([str(admission_probe), str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == 'ACCEPTED'


@pytest.fixture(scope='module')
def full_serving():
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    return apply_request_profile_keys(full_view_serving_program(arch)), arch


@pytest.mark.parametrize('admission_probe', ['serving'], indirect=True)
@pytest.mark.parametrize('mutation', ['missing-slot', 'foreign-phase-slot', 'aliased-member', 'foreign-publish-allocation'])
def test_serving_binding_mutations_fail_python_and_cpp(admission_probe, full_serving, tmp_path, mutation):
    from dataclasses import replace
    import subprocess
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.model import StringEntry

    program, arch = full_serving
    members = list(program.agent_instance_member_bindings)
    if mutation == 'missing-slot':
        program = replace(program, strings=(*program.strings, StringEntry('undeclared-serving-slot')))
        members[0] = replace(members[0], static_input_symbol_id=len(program.strings))
    elif mutation == 'foreign-phase-slot':
        publish = next(instance for instance in program.agent_instance_profiles if instance.phase == A.PHASE.PUBLISH)
        members[publish.member_binding_first] = replace(members[publish.member_binding_first], static_output_symbol_id=members[0].static_input_symbol_id)
    elif mutation == 'aliased-member':
        dual = next(instance for instance in program.agent_instance_profiles if instance.member_count == 2 and instance.phase == A.PHASE.PREFILL)
        first = members[dual.member_binding_first]
        members[dual.member_binding_first + 1] = replace(members[dual.member_binding_first + 1], static_input_symbol_id=first.static_input_symbol_id)
    else:
        publish, *rest = program.agent_publish_surrogate_bindings
        program = replace(program, agent_publish_surrogate_bindings=(replace(publish, allocation_id=program.allocations[0].allocation_id), *rest))
    program = apply_request_profile_keys(replace(program, agent_instance_member_bindings=tuple(members)))
    with pytest.raises(MeshIrError):
        verify_program(program, arch)
    path = tmp_path / (mutation + '.mshb')
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.startswith(('E_BINDING_ROLE:', 'E_HOST_IO_SIZE_MISMATCH:')), result.stdout


def test_serving_host_operands_declare_the_runtime_host_region(full_serving):
    from mesh_ir.ir.common import MemorySpace, TensorRole

    program, arch = full_serving
    catalog = ProgramBindingCatalog(program)
    for variant in program.semantics.variants:
        for binding in catalog.for_variant(variant.entrypoint_id, variant.profile_id).values():
            if binding.tensor.role in (TensorRole.INPUT, TensorRole.OUTPUT):
                assert binding.object.memory_space is MemorySpace.HOST_SHARED
                assert arch.regions[binding.slot.region_id].kind == 'HOST_SHARED'
            elif binding.tensor.role is TensorRole.KV_CACHE:
                assert binding.object.memory_space is MemorySpace.HBM


def test_default_kv_pool_uses_admitted_member_slots(full_serving):
    from mesh_ir.kv_layout import reference_kv_pool_base
    from mesh_ir.serving_programs import KV_BASE

    program, arch = full_serving
    hbm = next(region for region in arch.regions if region.kind == 'HBM')
    assert reference_kv_pool_base(program, [region.base for region in arch.regions]) == hbm.base + KV_BASE
    request = program.agent_request_profiles[0]
    assert not any(row.symbol_sid == request.primary_kv_symbol_id for row in program.relocations)


@pytest.mark.parametrize('ranks', [(0, 1), (1, 0), (0, 0)])
def test_ranked_member_variants_keep_admitted_memory(ranks):
    from mesh_ir.serving_programs import dual_member_ranks_program
    from mesh_ir.serving_profiles import member_rank_vector

    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    program = apply_request_profile_keys(dual_member_ranks_program(arch, ranks))
    assert verify_program(program, arch).program is program
    assert all(member_rank_vector(program, row) == ranks for row in program.agent_instance_profiles if row.member_count == 2)
    assert {member_rank_vector(program, row) for row in program.agent_instance_profiles if row.member_count == 1} == {(0,), (1,)}
    assert decode_program(encode_program(program)) == program


def test_full_view_alias_carrier_reaches_binding_validation():
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.serving_programs import full_view_alias_program

    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    program = apply_request_profile_keys(full_view_alias_program(full_view_serving_program(arch)))
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        verify_program(program, arch)


def test_repair_initial_input_does_not_require_the_later_context_capacity():
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2.yaml')
    program = repair_serving_program(arch)
    catalog = ProgramBindingCatalog(program)
    requests = {(row.program_id, row.profile_id): row for row in program.agent_request_profiles}
    for instance in program.agent_instance_profiles:
        if instance.phase != A.PHASE.PREFILL:
            continue
        request = requests[(instance.request_program_id, instance.request_profile_id)]
        member = program.agent_instance_member_bindings[instance.member_binding_first]
        symbol = program.strings[member.static_input_symbol_id - 1].value
        binding = catalog.binding(instance.mesh_entrypoint_id, instance.mesh_profile_id, symbol)
        assert binding.object.footprint_bytes == request.input_binding_bytes

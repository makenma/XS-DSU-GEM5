from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.abi import decode_program, encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.serving_programs import serving_program
from mesh_ir.serving_tensor_bindings import ServingTensorBindingCatalog


@pytest.fixture(scope='module')
def program():
    root = Path(__file__).resolve().parents[4]
    return serving_program(load_arch(root / 'configs/example/ai_mesh/arch/mesh_1x2.yaml'))


@pytest.fixture(scope='module')
def three_input_catalog_program():
    from mesh_ir.authoring import AuthoredMemory
    from mesh_ir.builder import ProgramBuilder
    from mesh_ir.generated import agent_abi as AG
    from mesh_ir.ir.common import DType, MemorySpace, TensorRole
    from mesh_ir.model import AgentInstanceMemberBinding, AgentInstanceProfile, AgentInstanceTensorBinding, AgentRequestBindingRequirement, AgentRequestProfile, AgentSourceCoreMap, StringEntry
    from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
    from mesh_ir.scheduled.verify import verify_program
    from tests.golden.support.records import record

    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')
    memory = AuthoredMemory(arch, arch.core_ids[0])
    handles = []
    for ordinal, (name, dtype, shape) in enumerate((('ids', DType.INT32, (1, 16)),
            ('mask', DType.FP16, (1, 1, 16, 16)), ('positions', DType.INT32, (1, 16)))):
        source = memory.buffer(name, TensorRole.INPUT, dtype, shape, MemorySpace.HOST_SHARED, ordinal * 4096)
        handles.append((source, memory.stage(source, name + ':stage', ordinal * 4096)))
    for ordinal, (source, destination) in enumerate(handles):
        memory.copy(str(ordinal), source, destination)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('serving', 'tensor-map-catalog', 1))
    variant = builder.variant('forward', 'input', 'input', records=memory.records(),
        allocations=memory.allocations, external_backings=memory.external_backings, binding_slots=memory.binding_slots)
    stream = variant.stream(arch.core_ids[0], 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    for operation in memory.effects:
        stream.kernel_command(operation.op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    program = builder.build()
    verify_program(program, arch)
    strings = program.strings + (StringEntry('request.inputs'),)
    symbols = {item.value: index for index, item in enumerate(strings, 1)}
    root_symbol = len(strings)
    rows = []
    offset = 0
    catalog = ProgramBindingCatalog(program).for_variant(1, 1)
    for name in ('ids', 'mask', 'positions'):
        size = catalog[name].slot.required_allocation_bytes
        rows.append(AgentInstanceTensorBinding(1, 0, AG.BINDING_KIND.HOST_INPUT,
            symbols[name], root_symbol, offset, size))
        offset += size
    request = record(AgentRequestProfile, program_id=1, profile_id=1,
        primary_input_symbol_id=root_symbol, input_binding_bytes=offset,
        full_input_dma_bytes=offset, delta_input_dma_bytes=offset, source_rank_count=1)
    instance = record(AgentInstanceProfile, instance_profile_id=1, request_program_id=1, request_profile_id=1,
        phase=A.PHASE.PREFILL, member_count=1, member_binding_count=1,
        mesh_entrypoint_id=1, mesh_profile_id=1, primary_input_symbol_id=root_symbol,
        host_input_dma_bytes_per_member=offset)
    return replace(program, strings=strings, agent_request_profiles=(request,), agent_instance_profiles=(instance,),
        agent_instance_member_bindings=(AgentInstanceMemberBinding(1, 0, 0, min(row.static_symbol_id for row in rows), 0, 0, 0),),
        agent_instance_tensor_bindings=tuple(sorted(rows, key=lambda row: row.static_symbol_id)),
        agent_request_binding_requirements=(AgentRequestBindingRequirement(1, 1, AG.BINDING_KIND.HOST_INPUT, AG.BINDING_FLAGS.READ, root_symbol),),
        agent_source_core_map=(AgentSourceCoreMap(arch.core_ids[0], 0),))


def test_phase_io_covers_every_mapped_input_in_request_coordinates(three_input_catalog_program):
    from mesh_ir.serving_profiles import HOST_LOAD, descriptor_attribution, instance_io_intervals, verify_instance_io

    program = three_input_catalog_program
    request, = program.agent_request_profiles
    instance, = program.agent_instance_profiles
    verify_instance_io(program, request, instance)
    assert sorted(instance_io_intervals(program, request, instance)['intervals'][HOST_LOAD]) == [(0, 64), (64, 576), (576, 640)]
    assert len(descriptor_attribution(program, request, instance)) == 3


def test_native_admission_accepts_multiple_computation_inputs(driver, tmp_path):
    from mesh_ir.scheduled.verify import verify_program
    from mesh_ir.serving_profiles import HOST_LOAD, apply_request_profile_keys, instance_io_intervals
    from tests.golden.test_mutation_corpus import _cpp_verdict

    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')
    program = apply_request_profile_keys(serving_program(arch, input_parts=2))
    verify_program(program, arch)
    request, = program.agent_request_profiles
    instance = next(row for row in program.agent_instance_profiles if row.phase == A.PHASE.PREFILL)
    assert sorted(instance_io_intervals(program, request, instance)['intervals'][HOST_LOAD]) == [(0, 64), (64, 128)]
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == 'ACCEPTED'


def test_catalog_maps_three_typed_inputs_to_distinct_request_subranges(three_input_catalog_program):
    rows = ServingTensorBindingCatalog(three_input_catalog_program).for_instance(1)
    assert {row.binding.tensor.name: (row.mapping.offset_bytes, row.mapping.span_bytes) for row in rows} == {
        'ids': (0, 64), 'mask': (64, 512), 'positions': (576, 64)}
    assert {row.binding.tensor.dtype.name for row in rows} == {'INT32', 'FP16'}
    assert len({row.mapping.request_symbol_id for row in rows}) == 1


@pytest.mark.parametrize('mutation', ['overlap', 'outside', 'missing'])
def test_catalog_rejects_false_three_input_subranges(three_input_catalog_program, mutation):
    program = three_input_catalog_program
    rows = list(program.agent_instance_tensor_bindings)
    if mutation == 'missing':
        rows.pop()
    else:
        rows[-1] = replace(rows[-1], offset_bytes=0 if mutation == 'overlap' else 640)
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        ServingTensorBindingCatalog(replace(program, agent_instance_tensor_bindings=tuple(rows)))


def test_tensor_mapping_is_generated_mandatory_and_round_trips(program):
    assert A.AGENT_INSTANCE_TENSOR_BINDINGS_BYTES == 32
    assert A.SECTION_TYPE.AGENT_INSTANCE_TENSOR_BINDINGS in A.conditional_required_sections(program.required_features)
    decoded = decode_program(encode_program(program))
    assert decoded.agent_instance_tensor_bindings == program.agent_instance_tensor_bindings
    catalog = ServingTensorBindingCatalog(decoded)
    for instance in decoded.agent_instance_profiles:
        rows = catalog.for_instance(instance.instance_profile_id)
        assert rows
        assert len({item.binding.slot.slot_id for item in rows}) == len(rows)
        for item in rows:
            assert item.mapping.span_bytes == item.binding.slot.required_allocation_bytes
            assert item.mapping.offset_bytes == 0


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'instance', 'ordinal', 'symbol', 'root', 'kind', 'offset', 'size', 'overflow', 'summary'])
def test_tensor_mapping_rejects_incomplete_or_false_ownership(program, mutation):
    rows = list(program.agent_instance_tensor_bindings)
    if mutation == 'missing':
        rows.pop(0)
    elif mutation == 'duplicate':
        rows.insert(0, rows[0])
    elif mutation == 'summary':
        first, *rest = program.agent_instance_member_bindings
        program = replace(program, agent_instance_member_bindings=(replace(first, static_input_symbol_id=0), *rest))
    else:
        field, value = dict(instance=('instance_profile_id', 9999), ordinal=('member_ordinal', 9999),
            symbol=('static_symbol_id', len(program.strings) + 1), root=('request_symbol_id', 0),
            kind=('binding_kind', 999), offset=('offset_bytes', 1), size=('span_bytes', 1),
            overflow=('offset_bytes', (1 << 64) - 1))[mutation]
        rows[0] = replace(rows[0], **{field: value})
    program = replace(program, agent_instance_tensor_bindings=tuple(rows))
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE|E_ABI_ORDER|E_ABI_DUPLICATE|E_ABI_OVERFLOW'):
        ServingTensorBindingCatalog(program)


@pytest.mark.parametrize('mutation', [None, 'empty', 'missing', 'duplicate', 'offset', 'overflow', 'root', 'kind', 'foreign', 'host-count'])
def test_native_admission_mirrors_tensor_mapping_contract(program, driver, tmp_path, mutation):
    from mesh_ir.scheduled.verify import verify_program
    from mesh_ir.serving_profiles import apply_request_profile_keys
    from tests.golden.test_mutation_corpus import _cpp_verdict

    rows = list(program.agent_instance_tensor_bindings)
    if mutation == 'host-count':
        first, *rest = program.agent_instance_profiles
        program = replace(program, agent_instance_profiles=(replace(first,
            host_input_dma_bytes_per_member=first.host_input_dma_bytes_per_member + 1), *rest))
    elif mutation == 'empty':
        rows.clear()
    elif mutation == 'missing':
        rows.pop(0)
    elif mutation == 'duplicate':
        rows.insert(0, rows[0])
    elif mutation:
        field, value = dict(offset=('offset_bytes', 1), overflow=('offset_bytes', (1 << 64) - 1),
                            root=('request_symbol_id', 0), kind=('binding_kind', 999),
                            foreign=('static_symbol_id', program.agent_request_profiles[0].primary_input_symbol_id))[mutation]
        rows[0] = replace(rows[0], **{field: value})
    changed = apply_request_profile_keys(replace(program, agent_instance_tensor_bindings=tuple(rows)))
    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')
    if mutation:
        with pytest.raises(MeshIrError) as error:
            verify_program(changed, arch)
        if mutation == 'host-count':
            assert error.value.code == 'E_HOST_IO_SIZE_MISMATCH'
        expected = 'VERIFY:' + error.value.code
    else:
        verify_program(changed, arch)
        expected = 'ACCEPTED'
    assert _cpp_verdict(encode_program(changed), driver, tmp_path, arch) == expected

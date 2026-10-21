from dataclasses import replace

import pytest

from mesh_ir.agent_planning import ArenaRegion, build_host_arena_object_plan
from mesh_ir.agent_workload import PlanError, load_workload_plan
from mesh_ir.generated import agent_abi as AG
from mesh_ir.host_bindings import HostBindingPlan, HostBindingRequirement, resolve_host_binding_records
from tests.unit.test_agent_arena import FIXTURES, regions
from tests.integration.support.workspace_program import build_workspace_program


def workspace_plan(size=4861952):
    return HostBindingPlan(1, 1, 1, 2, 0,
        (HostBindingRequirement(1, AG.BINDING_KIND.HOST_INPUT, AG.BINDING_FLAGS.READ),
         HostBindingRequirement(2, AG.BINDING_KIND.HOST_OUTPUT, AG.BINDING_FLAGS.WRITE),
         HostBindingRequirement(3, AG.BINDING_KIND.REQUEST_WORKSPACE,
             AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.WRITE, required_bytes=size)), (2,), 64)


def test_workspace_binding_has_request_address_and_derived_capacity():
    plan = workspace_plan()
    records = resolve_host_binding_records(plan, 0x100000000, 640, 0x110000000,
        4861952, 0, workspace_address=0x800000000)
    assert records[-1] == (3, AG.BINDING_KIND.REQUEST_WORKSPACE,
        AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.WRITE, 0x800000000, 4861952)
    assert records[-1][2] & (AG.BINDING_FLAGS.PERSISTENT | AG.BINDING_FLAGS.RESOLVE_BY_HANDLE) == 0


@pytest.mark.parametrize('address', [0, (1 << 64) - 64])
def test_workspace_binding_rejects_absent_or_overflowed_address(address):
    from mesh_ir.diagnostics import MeshIrError
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        resolve_host_binding_records(workspace_plan(), 0x100000000, 640,
            0x110000000, 4861952, 0, workspace_address=address)


def test_workspace_arena_is_disjoint_per_request_and_starts_uninitialized():
    workload = load_workload_plan(FIXTURES / 'agent_workload_plan_two_user.json')
    plan = workspace_plan(4096)
    keys = {(round_.program_id, round_.profile_id) for user in workload.users
            for task in user.tasks for round_ in task.rounds}
    plans = tuple(replace(plan, program_id=program, profile_id=profile) for program, profile in sorted(keys))
    document = build_host_arena_object_plan(workload, None, 'EXPLICIT_ONLY',
        regions() + [ArenaRegion('WORKSPACE', 0x800000000, 65536, 64)], binding_plans=plans)
    rows = [row for row in document['records'] if row['arena_kind'] == 'WORKSPACE']
    assert len(rows) == 5
    assert len({row['request_id'] for row in rows}) == 5
    assert [row['base'] for row in rows] == [0x800000000 + index * 4096 for index in range(5)]
    assert all(row['initial_valid_bytes'] == 0 and row['allocation_bytes'] == 4096 for row in rows)


@pytest.mark.parametrize('case', ['missing', 'capacity', 'overlap', 'duplicate'])
def test_workspace_arena_rejects_invalid_reservations(case):
    workload = load_workload_plan(FIXTURES / 'agent_workload_plan_two_user.json')
    selected = regions()
    if case != 'missing':
        selected.append(ArenaRegion('WORKSPACE', 0x800000000 if case != 'overlap' else selected[0].base,
            1 if case == 'capacity' else 65536, 64))
    if case == 'duplicate':
        selected.append(ArenaRegion('WORKSPACE', 0x900000000, 65536, 64))
    with pytest.raises(PlanError, match='E_ADDRESS_PLAN'):
        build_host_arena_object_plan(workload, None, 'EXPLICIT_ONLY', selected, binding_plans=(workspace_plan(),))


@pytest.fixture(scope='module')
def workspace_program():
    return build_workspace_program()



def test_workspace_capacity_is_derived_from_admitted_tensor_mappings(workspace_program):
    from mesh_ir.host_bindings import build_host_binding_plans
    from mesh_ir.serving_tensor_bindings import ServingTensorBindingCatalog
    program, arch = workspace_program
    ServingTensorBindingCatalog(program)
    plan, = build_host_binding_plans(program, [region.base for region in arch.regions])
    assert plan.workspace_bytes == 64
    assert plan.instance_counts == (2,)


def test_workspace_capacity_counts_one_request_root_per_member(workspace_program):
    from mesh_ir.serving_profiles import serving_capacity_required
    program, _ = workspace_program
    capacity = serving_capacity_required(program)
    assert capacity.batch_interval_entries_per_context == 5


def test_workspace_phase_io_uses_mapping_kind_instead_of_tensor_role(workspace_program):
    from mesh_ir.serving_profiles import ServingInstanceIo, verify_instance_io
    program, _ = workspace_program
    request, = program.agent_request_profiles
    prefill, publish = program.agent_instance_profiles
    for instance in (prefill, publish):
        verify_instance_io(program, request, instance)
    saved = ServingInstanceIo(program, request, prefill).member_io(0)['intervals']
    restored = ServingInstanceIo(program, request, publish).member_io(0)['intervals']
    assert saved['workspace_write'] == [(0, 64)]
    assert saved['host_output'] == []
    assert restored['workspace_read'] == [(0, 64)]
    assert restored['host_output'] == [(0, 64)]


def test_computed_workspace_publication_proves_every_output_byte(workspace_program):
    from mesh_ir.serving_workspace import verify_workspace_publication
    program, _ = workspace_program
    verify_workspace_publication(program, program.agent_request_profiles[0])


@pytest.mark.parametrize('output_rows,publish_rows', [(4, 2), (6, 4), (12, 8)])
def test_workspace_publication_reuses_a_bounded_staging_window(output_rows, publish_rows):
    from mesh_ir.generated import abi as A
    from mesh_ir.ir.common import MemorySpace
    from mesh_ir.scheduled.verify import verify_program
    from mesh_ir.serving_profiles import ServingInstanceIo
    program, arch = build_workspace_program(output_rows=output_rows, publish_rows=publish_rows)
    verify_program(program, arch)
    request, = program.agent_request_profiles
    publish = program.agent_instance_profiles[-1]
    variant = program.semantics.variants[-1]
    objects = program.semantics.objects[variant.membership.objects.first_id - 1:
        variant.membership.objects.first_id - 1 + variant.membership.objects.count]
    local = [obj for obj in objects if obj.memory_space is MemorySpace.CORE_SRAM]
    assert len(local) == (output_rows + publish_rows - 1) // publish_rows
    assert max(obj.footprint_bytes for obj in local) == publish_rows * 16
    allocations = program.allocations[variant.membership.allocations.first_id - 1:
        variant.membership.allocations.first_id - 1 + variant.membership.allocations.count]
    assert {row.offset_bytes for row in allocations} == {0}
    io = ServingInstanceIo(program, request, publish)
    expected = [(offset * 16, min(offset + publish_rows, output_rows) * 16)
                for offset in range(0, output_rows, publish_rows)]
    assert io.member_io(0)['intervals']['host_output'] == expected
    assert io.member_io(0)['intervals']['workspace_read'] == expected
    stores = [row for row in io.descriptors if row.kind == A.DMA_KIND.STORE]
    assert sum(row.rows * row.row_bytes for row in stores) == output_rows * 16
    assert len(stores) == len(expected)


@pytest.mark.parametrize('phase', ['prefill', 'publish'])
@pytest.mark.parametrize('partial', [False, True])
def test_workspace_publication_rejects_fill_overwriting_computed_bytes(phase, partial):
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.serving_workspace import verify_workspace_publication
    program, _ = build_workspace_program(overwrite_phase=phase, partial=partial)
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        verify_workspace_publication(program, program.agent_request_profiles[0])


def test_workspace_publication_rejects_unwritten_request_subrange(workspace_program):
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.serving_workspace import verify_workspace_publication
    program, _ = workspace_program
    rows = tuple(replace(row, offset_bytes=64) if row.instance_profile_id == 2
        and row.binding_kind == AG.BINDING_KIND.REQUEST_WORKSPACE else row for row in program.agent_instance_tensor_bindings)
    program = replace(program, agent_instance_tensor_bindings=rows)
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        verify_workspace_publication(program, program.agent_request_profiles[0])


@pytest.mark.parametrize('phase,partial', [(None, False), ('prefill', False), ('prefill', True), ('publish', False), ('publish', True)])
def test_native_admission_mirrors_computed_workspace_publication(driver, tmp_path, phase, partial):
    from mesh_ir.abi import encode_program
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.generated import abi as A
    from mesh_ir.scheduled.verify import verify_program
    from mesh_ir.serving_profiles import apply_request_profile_keys
    from tests.golden.test_mutation_corpus import _cpp_verdict
    program, arch = build_workspace_program(overwrite_phase=phase, partial=partial)
    program = apply_request_profile_keys(replace(program, required_features=program.required_features | A.AGENT_SERVING_V1 | A.PROFILE_SCOPED_EXECUTION_V1))
    if phase is None:
        verify_program(program, arch)
        expected = 'ACCEPTED'
    else:
        with pytest.raises(MeshIrError) as error:
            verify_program(program, arch)
        assert error.value.code == 'E_BINDING_ROLE'
        expected = 'VERIFY:' + error.value.code
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == expected


@pytest.mark.parametrize('mutation', ['missing', 'capacity', 'overflow', 'persistent', 'host-storage', 'readonly-slot', 'readonly-reference'])
def test_workspace_mapping_rejects_invalid_storage_contract(workspace_program, mutation):
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.ir.common import Access, MemorySpace
    from mesh_ir.serving_tensor_bindings import ServingTensorBindingCatalog
    program, _ = workspace_program
    rows = list(program.agent_instance_tensor_bindings)
    index = next(index for index, row in enumerate(rows) if row.binding_kind == AG.BINDING_KIND.REQUEST_WORKSPACE)
    if mutation == 'missing':
        rows.pop(index)
    elif mutation in ('capacity', 'overflow'):
        rows[index] = replace(rows[index], **({'span_bytes': 1} if mutation == 'capacity' else {'offset_bytes': (1 << 64) - 32}))
    elif mutation == 'persistent':
        requirements = tuple(replace(row, binding_flags=row.binding_flags | AG.BINDING_FLAGS.PERSISTENT)
            if row.binding_kind == AG.BINDING_KIND.REQUEST_WORKSPACE else row for row in program.agent_request_binding_requirements)
        program = replace(program, agent_request_binding_requirements=requirements)
    else:
        binding = next(row.binding for row in ServingTensorBindingCatalog(program).for_instance(rows[index].instance_profile_id)
            if row.mapping == rows[index])
        if mutation == 'host-storage':
            objects = tuple(replace(row, memory_space=MemorySpace.HOST_SHARED) if row.object_id == binding.object.object_id else row
                for row in program.semantics.objects)
            program = replace(program, semantics=replace(program.semantics, objects=objects))
        else:
            fields = {'access': Access.READ_ONLY} if mutation == 'readonly-slot' else {
                'reference_binding': replace(binding.slot.reference_binding, access=Access.READ_ONLY)}
            slots = tuple(replace(row, **fields) if row.slot_id == binding.slot.slot_id else row
                for row in program.semantics.binding_slots)
            program = replace(program, semantics=replace(program.semantics, binding_slots=slots))
    program = replace(program, agent_instance_tensor_bindings=tuple(rows))
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE|E_ABI_OVERFLOW|E_RELOCATION'):
        ServingTensorBindingCatalog(program)


@pytest.mark.parametrize('space, storage', [('CORE_SRAM', 'CORE_SRAM'), ('HBM', 'CORE_SRAM')])
def test_imported_output_requires_external_backing(workspace_program, space, storage):
    from mesh_ir.authoring import AuthoredMemory
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.ir.common import DType, MemorySpace, StorageClass, TensorRole
    from mesh_ir.ir.kernel_ir import StateOrigin
    _, arch = workspace_program
    memory = AuthoredMemory(arch, arch.core_ids[0])
    with pytest.raises(MeshIrError, match='E_TENSOR_NOT_RESIDENT'):
        memory.buffer('imported', TensorRole.OUTPUT, DType.FP16, (4, 8), MemorySpace[space], 0,
            storage_class=StorageClass[storage], initial_state=StateOrigin.EXTERNAL)

from dataclasses import replace

import pytest

from mesh_ir.builder import ProgramBuilder
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.scheduled.verify import verify_program
from tests.unit.test_gate2_authored_facade import _arch, _inputs


@pytest.fixture(scope='module')
def shared_symbols():
    arch = _arch()
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('abi2', 'binding-catalog', 1))
    for name in ('first', 'second'):
        records, allocations, backings, slots = _inputs(arch)
        variant = builder.variant('forward', name, name, records=records, allocations=allocations, external_backings=backings, binding_slots=slots)
        stream = variant.stream(3, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
        stream.control_command(RequestBeginAttrs())
        for operation in (9, 10, 11):
            stream.kernel_command(operation)
        stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    program = builder.build()
    assert verify_program(program, arch).program is program
    return program


def test_binding_symbols_resolve_only_inside_selected_variant(shared_symbols):
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog

    catalog = ProgramBindingCatalog(shared_symbols)
    for variant in shared_symbols.semantics.variants:
        item = catalog.binding(variant.entrypoint_id, variant.profile_id, 'input')
        assert item.slot.slot_id == variant.membership.binding_slots.first_id
        assert item.object.object_id == variant.membership.objects.first_id
        assert item.tensor.tensor_id == variant.membership.kernel_tensors.first_id
        assert item.slot.reference_binding.allocation_offset_bytes == 0
    assert catalog.binding(1, 1, 'input').slot.slot_id != catalog.binding(1, 2, 'input').slot.slot_id


@pytest.mark.parametrize('selector', [(1, 99, 'input'), (99, 1, 'input'), (1, 1, 'missing')])
def test_binding_catalog_rejects_missing_owner_without_global_fallback(shared_symbols, selector):
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog

    with pytest.raises(MeshIrError, match='E_RELOCATION'):
        ProgramBindingCatalog(shared_symbols).binding(*selector)


def test_binding_catalog_rejects_ambiguous_symbol_in_one_variant(shared_symbols):
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog

    slots = list(shared_symbols.semantics.binding_slots)
    slots[1] = replace(slots[1], symbol=slots[0].symbol)
    program = replace(shared_symbols, semantics=replace(shared_symbols.semantics, binding_slots=tuple(slots)))
    with pytest.raises(MeshIrError, match='E_RELOCATION'):
        ProgramBindingCatalog(program)

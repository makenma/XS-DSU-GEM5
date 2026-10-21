import dataclasses
from pathlib import Path

import pytest

from mesh_ir.analysis.sram import SramAllocation
from mesh_ir.architecture import load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, INVALID_CORE_ID, MemorySpace
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ExternalSlotBacking, HaltAttrs, ObjectBacking, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.traffic import Binding, BindingSlot
from tests.unit.test_gate2_kernel_ir import load_compute_store_kernel


ROOT = Path(__file__).resolve().parents[4]


def _arch():
    return load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")


def _inputs(arch, suffix=""):
    records = load_compute_store_kernel().memory_records()
    allocations = (
        SramAllocation(1, 2, 3, 0, 32, arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 3, arch.sram_base_alignment_bytes, 32, arch.sram_base_alignment_bytes),
    )
    region_id = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    input_binding = Binding(1, region_id, INVALID_CORE_ID, 0, 32, 32, Access.READ_ONLY)
    output_binding = Binding(2, region_id, INVALID_CORE_ID, 64, 32, 32, Access.READ_WRITE)
    slots = (
        BindingSlot(1, f"input{suffix}", MemorySpace.HBM, region_id, INVALID_CORE_ID, 16, 32, Access.READ_ONLY, input_binding),
        BindingSlot(2, f"output{suffix}", MemorySpace.HBM, region_id, INVALID_CORE_ID, 16, 32, Access.READ_WRITE, output_binding),
    )
    backings = (
        ObjectBacking(1, ExternalSlotBacking(1)),
        ObjectBacking(4, ExternalSlotBacking(2)),
    )
    return records, allocations, backings, slots


def _declare_variant(builder, arch, entrypoint="forward", profile_id="p4", identity="forward:p4"):
    records, allocations, backings, slots = _inputs(arch, f":{identity}")
    variant = builder.variant(
        entrypoint,
        profile_id,
        identity,
        records=records,
        allocations=allocations,
        external_backings=backings,
        binding_slots=slots,
    )
    stream = variant.stream(3, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(9)
    stream.kernel_command(10)
    stream.kernel_command(11)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return records, allocations, backings, slots


def test_authored_facade_builds_real_load_compute_store_deterministically():
    arch = _arch()
    origin = AuthoredProgramOrigin("unit", "load-compute-store", 1)
    builder = ProgramBuilder(arch, origin)
    inputs = _declare_variant(builder, arch)
    before = tuple(dataclasses.asdict(group) if dataclasses.is_dataclass(group) else group for group in inputs)

    first = builder.build()
    second = builder.build()

    assert verify_program(first, arch).program is first
    assert first == second
    assert first.semantics.origin == origin
    assert tuple(item.source.kernel_op_id for item in first.semantics.command_semantics if hasattr(item.source, "kernel_op_id")) == (9, 10, 11)
    assert tuple(dataclasses.asdict(group) if dataclasses.is_dataclass(group) else group for group in inputs) == before


def test_authored_facade_keeps_two_variant_local_id_spaces_disjoint():
    arch = _arch()
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "two-profiles", 1))
    _declare_variant(builder, arch, profile_id="small", identity="forward:small")
    _declare_variant(builder, arch, profile_id="large", identity="forward:large")

    program = builder.build()

    assert [(item.entrypoint_id, item.profile_id) for item in program.semantics.variants] == [(1, 1), (1, 2)]
    first, second = program.semantics.variants
    assert first.membership.objects.first_id == 1
    assert second.membership.objects.first_id == first.membership.objects.first_id + first.membership.objects.count
    assert first.lineage.authoring_variant_id == "forward:small"
    assert second.lineage.authoring_variant_id == "forward:large"


def test_authored_facade_rejects_duplicate_variants_streams_and_effects():
    arch = _arch()
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "duplicates", 1))
    records, allocations, backings, slots = _inputs(arch)
    first = builder.variant("forward", "p4", "forward:p4", records=records, allocations=allocations, external_backings=backings, binding_slots=slots)
    with pytest.raises(MeshIrError):
        builder.variant("forward", "p4", "other", records=records, allocations=allocations, external_backings=backings, binding_slots=slots)
    stream = first.stream(3, 0)
    with pytest.raises(MeshIrError):
        first.stream(3, 0)
    stream.kernel_command(9)
    with pytest.raises(MeshIrError):
        stream.kernel_command(9)


@pytest.mark.parametrize(
    "operation",
    (
        lambda builder, arch: builder.variant("", "p4", "v", records=_inputs(arch)[0], allocations=_inputs(arch)[1]),
        lambda builder, arch: builder.variant("forward", "p4", "v", records=_inputs(arch)[0], allocations=[]),
        lambda builder, arch: builder.variant("forward", "p4", "v", records=_inputs(arch)[0], allocations=_inputs(arch)[1]).stream(True, 0),
    ),
)
def test_authored_facade_rejects_malformed_public_inputs(operation):
    arch = _arch()
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "invalid", 1))

    with pytest.raises(MeshIrError):
        operation(builder, arch)

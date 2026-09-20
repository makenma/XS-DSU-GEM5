from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.analysis.sram import SramAllocation
from mesh_ir.architecture import load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.golden_programs import build_fill_program, build_zero_dma_program
from mesh_ir.ir.common import DType, DmaKind
from mesh_ir.ir.kernel_ir import ElementRegion, KernelMemoryRecords, KernelOpcode
from mesh_ir.passes.addresses import bind_addresses_and_relocations_stage
from mesh_ir.passes.hazards import insert_hazard_dependencies_stage
from mesh_ir.passes.schedule import schedule_per_core_streams_stage
from mesh_ir.passes.segments import lower_dma_to_segments_stage
from mesh_ir.passes.static_sram import _StaticSramState
from mesh_ir.scheduled.addressing import _derive_descriptor_execution_set, derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ControlCommandSource, ExternalSlotBacking, KernelCommandSource, LocalAllocationBacking, RepeatCommandAttrs
from mesh_ir.scheduled.projection import empty_dma_row_geometry
from mesh_ir.scheduled.verify import verify_pretraffic_state, verify_program
from mesh_ir.traffic import calculate_traffic


ROOT = Path(__file__).resolve().parents[4]


class _IntSubclass(int):
    pass


def _records(program, ops=None):
    semantics = program.semantics
    return KernelMemoryRecords(
        semantics.kernel_tensors,
        semantics.computations,
        semantics.placements,
        semantics.logical_shards,
        semantics.partial_sums,
        semantics.objects,
        semantics.views,
        semantics.states,
        semantics.tokens,
        semantics.kernel_ops if ops is None else ops,
    )


def _reauthor(arch, source, records, variant_count=1, repeat_after_op_id=None):
    semantics = source.semantics
    local_backings = {item.backing.allocation_id: item.object_id for item in semantics.object_backings if type(item.backing) is LocalAllocationBacking}
    allocations = tuple(SramAllocation(item.allocation_id, local_backings[item.allocation_id], item.owner_core, item.offset_bytes, item.size_bytes, item.alignment_bytes) for item in source.allocations)
    external_backings = tuple(item for item in semantics.object_backings if type(item.backing) is ExternalSlotBacking)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "empty-dma-geometry", 1))
    for ordinal in range(variant_count):
        variant = builder.variant("main", f"empty-{ordinal}", f"empty-dma-geometry:{ordinal}", records=records, allocations=allocations, external_backings=external_backings, binding_slots=semantics.binding_slots)
        for stream in semantics.streams:
            target = variant.stream(stream.core_id, stream.physical_stream_id, stream.flags)
            command_ids = semantics.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]
            for command_id in command_ids:
                command = semantics.command_semantics[command_id - 1]
                if type(command.source) is KernelCommandSource:
                    target.kernel_command(command.source.kernel_op_id)
                    if command.source.kernel_op_id == repeat_after_op_id:
                        target.control_command(RepeatCommandAttrs(len(target._sources) - 1, 1, 3))
                elif type(command.source) is ControlCommandSource:
                    target.control_command(command.source.attrs)
                else:
                    raise AssertionError("unexpected authored command source")
    return builder


def _pretraffic(builder, arch):
    static = _StaticSramState(builder.origin, tuple(item._freeze() for item in builder._variants))
    hazards = insert_hazard_dependencies_stage(static)
    schedule = schedule_per_core_streams_stage(hazards, arch)
    segments = lower_dma_to_segments_stage(schedule)
    return bind_addresses_and_relocations_stage(segments, arch)


@pytest.mark.parametrize(
    "shape,expected",
    (
        ((0, 32), (0, 64)),
        ((1, 0), (1, 0)),
        ((2, 3, 0), (6, 0)),
        ((0,), (1, 0)),
    ),
)
def test_empty_dma_row_geometry_preserves_source_shape(shape, expected):
    region = ElementRegion((0,) * len(shape), shape, (1,) * len(shape))

    assert empty_dma_row_geometry(region, DType.FP16) == expected


@pytest.mark.parametrize(
    "region,dtype",
    (
        (ElementRegion((), (), ()), DType.FP16),
        (ElementRegion((0, 0), (1, 1), (1, 1)), DType.FP16),
        (ElementRegion((0, 0), (1, 0), (1, 0)), DType.FP16),
        (ElementRegion((0, False), (1, 0), (1, 1)), DType.FP16),
        (ElementRegion((0, 0), (_IntSubclass(1), 0), (1, 1)), DType.FP16),
        (ElementRegion((0, 0), (1, 0), (1, 1)), int(DType.FP16)),
        (ElementRegion((0, 0, 0), (1 << 63, 3, 0), (1, 1, 1)), DType.FP16),
    ),
)
def test_empty_dma_row_geometry_rejects_noncanonical_sources(region, dtype):
    with pytest.raises(MeshIrError):
        empty_dma_row_geometry(region, dtype)


def test_authored_empty_dma_programs_preserve_distinct_geometry_and_completion():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    fill = build_fill_program(arch)
    zero = build_zero_dma_program(arch)

    assert verify_program(fill, arch).program is fill
    assert verify_program(zero, arch).program is zero
    assert tuple((item.kind, item.rows, item.row_bytes, item.src_stride_bytes, item.dst_stride_bytes) for item in fill.dma_descriptors) == (
        (int(DmaKind.LOCAL_FILL), 1, 128, 128, 128),
        (int(DmaKind.LOAD), 1, 0, 0, 0),
        (int(DmaKind.STORE), 1, 128, 128, 128),
    )
    assert tuple((item.kind, item.rows, item.row_bytes, item.src_stride_bytes, item.dst_stride_bytes) for item in zero.dma_descriptors) == (
        (int(DmaKind.LOAD), 0, 64, 64, 64),
        (int(DmaKind.LOAD), 1, 0, 0, 0),
        (int(DmaKind.LOCAL_FILL), 0, 64, 64, 64),
        (int(DmaKind.LOCAL_FILL), 1, 0, 0, 0),
        (int(DmaKind.P2P_PUSH), 0, 64, 64, 64),
    )
    assert all(item.completion_event > 0 for item in zero.dma_descriptors)
    assert all((item.useful_bytes, item.physical_beat_bytes, item.bursts, item.packets, item.flits) == (0, 0, 0, 0, 0) for item in zero.semantics.intrinsic_traffic.descriptors)


def test_empty_prefetch_crosses_actual_authored_pipeline_and_variants():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    zero = build_zero_dma_program(arch)
    zero_ops = list(zero.semantics.kernel_ops)
    prefetch_index, prefetch = next((index, op) for index, op in enumerate(zero_ops) if op.stable_key == "load:zero_bytes")
    zero_ops[prefetch_index] = replace(prefetch, attrs=replace(prefetch.attrs, kind=DmaKind.PREFETCH, max_burst_beats=8))
    prefetch_builder = _reauthor(arch, zero, _records(zero, tuple(zero_ops)), variant_count=2)
    prefetch_state = _pretraffic(prefetch_builder, arch)

    assert verify_pretraffic_state(prefetch_state, arch).state is prefetch_state
    prefetch_program = prefetch_builder.build()
    assert verify_program(prefetch_program, arch).program is prefetch_program
    assert len(prefetch_program.semantics.variants) == 2
    prefetch_descriptors = tuple(item for item in prefetch_program.dma_descriptors if item.kind == int(DmaKind.PREFETCH))
    assert tuple((item.rows, item.row_bytes, item.src_stride_bytes, item.dst_stride_bytes, item.max_burst_beats, item.src.offset_bytes, item.dst.offset_bytes) for item in prefetch_descriptors) == (
        (1, 0, 0, 0, 8, 64, 0),
        (1, 0, 0, 0, 8, 64, 0),
    )
    selected, _ = derive_descriptor_execution_set(prefetch_program, arch, selected_variant_id=2)
    assert selected
    assert all(item.descriptor.identity.profile_id == prefetch_program.semantics.variants[1].profile_id for item in selected)
    assert all(item.useful_bytes == 0 for item in prefetch_program.semantics.intrinsic_traffic.descriptors)


def test_empty_store_crosses_actual_authored_pipeline_and_completion():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    fill = build_fill_program(arch)
    fill_ops = list(fill.semantics.kernel_ops)
    store_index, store = next((index, op) for index, op in enumerate(fill_ops) if op.opcode is KernelOpcode.DMA and op.attrs.kind is DmaKind.STORE)
    zero_columns = ElementRegion((0, 0), (1, 0), (1, 1))
    fill_ops[store_index] = replace(store, reads=(replace(store.reads[0], region=zero_columns),), writes=(replace(store.writes[0], region=zero_columns),))
    store_builder = _reauthor(arch, fill, _records(fill, tuple(fill_ops)))
    store_state = _pretraffic(store_builder, arch)

    assert verify_pretraffic_state(store_state, arch).state is store_state
    store_program = store_builder.build()
    empty_store = next(item for item in store_program.dma_descriptors if item.kind == int(DmaKind.STORE))
    assert (empty_store.rows, empty_store.row_bytes, empty_store.src_stride_bytes, empty_store.dst_stride_bytes, empty_store.useful_bytes, empty_store.physical_storage_bytes) == (1, 0, 0, 0, 0, 0)
    assert store_program.commands[empty_store.command_id - 1].signal_event == empty_store.completion_event
    assert next(item for item in store_program.semantics.intrinsic_traffic.descriptors if item.identity.descriptor_id == empty_store.descriptor_id).useful_bytes == 0


def test_empty_dma_repeat_preserves_multiplicity_completion_and_zero_traffic():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    source = build_zero_dma_program(arch)
    repeated_op = next(op for op in source.semantics.kernel_ops if op.stable_key == "load:zero_rows")
    program = _reauthor(arch, source, _records(source), repeat_after_op_id=repeated_op.op_id).build()
    descriptor = next(item for item in program.dma_descriptors if item.command_id == next(item.command_id for item in program.semantics.command_semantics if type(item.source) is KernelCommandSource and item.source.kernel_op_id == repeated_op.op_id))
    traffic = next(item for item in program.semantics.intrinsic_traffic.descriptors if item.identity.descriptor_id == descriptor.descriptor_id)

    assert verify_program(program, arch).program is program
    assert (descriptor.rows, descriptor.row_bytes, descriptor.src_stride_bytes, descriptor.dst_stride_bytes, descriptor.useful_bytes, descriptor.physical_storage_bytes) == (0, 64, 64, 64, 0, 0)
    assert traffic.execution_count == 3
    assert (traffic.useful_bytes, traffic.physical_beat_bytes, traffic.segments, traffic.bursts) == (0, 0, 0, 0)
    assert program.commands[descriptor.command_id - 1].signal_event == descriptor.completion_event


@pytest.mark.parametrize("change", ("geometry", "completion"))
def test_pretraffic_verifier_rejects_empty_descriptor_corruption(change):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    source = build_zero_dma_program(arch)
    builder = _reauthor(arch, source, _records(source))
    state = _pretraffic(builder, arch)
    descriptor = state.transport.dma_descriptors[0]
    semantics = state.semantics
    if change == "geometry":
        descriptor = replace(descriptor, rows=0, row_bytes=0, src_stride_bytes=0, dst_stride_bytes=0)
    else:
        descriptor = replace(descriptor, completion_event=state.transport.dma_descriptors[1].completion_event)
        groups = tuple(replace(item, completion_event_id=descriptor.completion_event) if descriptor.descriptor_id in item.descriptor_ids else item for item in semantics.descriptor_groups)
        semantics = replace(semantics, descriptor_groups=groups)
    transport = replace(state.transport, dma_descriptors=(descriptor,) + state.transport.dma_descriptors[1:])
    executions, identity = _derive_descriptor_execution_set(transport, semantics, arch)
    forged = replace(state, transport=transport, semantics=replace(semantics, executions=executions, reference_binding_identity_sha256=identity))

    with pytest.raises(MeshIrError) as caught:
        verify_pretraffic_state(forged, arch)
    assert caught.value.code == "E_DMA_RANGE"


@pytest.mark.parametrize(
    "descriptor_index,geometry",
    (
        (0, (0, 0, 0, 0)),
        (1, (0, 0, 0, 0)),
    ),
)
def test_public_verifier_rejects_fresh_zero_traffic_with_wrong_empty_geometry(descriptor_index, geometry):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    program = build_zero_dma_program(arch)
    rows, row_bytes, source_stride, destination_stride = geometry
    descriptor = replace(program.dma_descriptors[descriptor_index], rows=rows, row_bytes=row_bytes, src_stride_bytes=source_stride, dst_stride_bytes=destination_stride)
    descriptors = program.dma_descriptors[:descriptor_index] + (descriptor,) + program.dma_descriptors[descriptor_index + 1:]
    provisional = replace(program, dma_descriptors=descriptors, expected_traffic=(), semantic_sha256="")
    executions, identity = derive_descriptor_execution_set(provisional, arch)
    report = calculate_traffic(arch, identity, executions)
    semantics = replace(program.semantics, reference_binding_identity_sha256=identity, intrinsic_traffic=report)
    provisional = replace(provisional, semantics=semantics, expected_traffic=_expected_traffic(report, executions, arch))
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)
    assert caught.value.code == "E_DMA_RANGE"

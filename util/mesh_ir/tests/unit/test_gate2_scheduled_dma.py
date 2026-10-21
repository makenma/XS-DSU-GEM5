from dataclasses import replace
from pathlib import Path

import pytest

import mesh_ir.passes.addresses as address_pass
import mesh_ir.passes.segments as segment_pass
from mesh_ir.analysis.regions import compact_ordered_affine_axes, ordered_region_interval
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, DType, DmaKind, Engine, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import AllocAttrs, BufferObject, BufferView, ControlToken, DistributionKind, DmaAttrs, ElementRegion, KernelBundle, KernelMemoryRecords, KernelModule, KernelOp, KernelOpcode, KernelTensor, KernelTensorLineage, OperandAccess, OperandAccessMode, Placement, StateOrigin, StateTransition, TensorShard, TensorState, ViewDeclarationAttrs
from mesh_ir.model import Allocation, Command, CommandOperand, CommandWait, DmaDescriptor, DmaEndpoint, Entrypoint, Event, Profile, Relocation, Shard, Stream, StringEntry, Tensor
from mesh_ir.scheduled.assemble import _PreTrafficSemantics, _PreTrafficState, _TransportSections, _expected_traffic, assemble_program
from mesh_ir.scheduled.addressing import bind_invocation, derive_descriptor_execution_set, derive_descriptor_executions
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AuthoredVariantLineage, CommandSemantics, ControlCommandSource, ControlExecution, DescriptorEndpointUse, DescriptorGroup, DescriptorSource, DmaExecution, EndpointSide, ExternalSlotBacking, HaltAttrs, IdSpan, KernelCommandSource, LifecycleSource, LocalAllocationBacking, ObjectBacking, ProgramVariant, ReadAccessUse, RequestBeginAttrs, RequestEndAttrs, ResidentView, ScheduledDependency, ScheduledDependencyKind, ScheduledStream, VariantMembership, WriteAccessUse
from mesh_ir.scheduled.verify import verify_pretraffic_state, verify_program
from mesh_ir.passes.static_sram import plan_static_sram_stage
from mesh_ir.passes.hazards import insert_hazard_dependencies_stage
from mesh_ir.passes.schedule import schedule_per_core_streams_stage
from mesh_ir.passes.segments import _paired_geometry, lower_dma_to_segments_stage
from mesh_ir.passes.schedule import schedule_per_core_streams_stage
from mesh_ir.traffic import AddressRef, Binding, BindingSlot, DescriptorExecution, DescriptorIdentity, DirectAddress, ResolvedDescriptor, SlotAddress, TrafficDirection, calculate_traffic, resolve_addresses


ROOT = Path(__file__).resolve().parents[4]


def test_ordered_region_analysis_preserves_parent_lattice_and_ignores_singleton_steps():
    parent = ElementRegion((0, 0), (2, 3), (2, 3))

    assert ordered_region_interval(parent, ElementRegion((2, 0), (1, 3), (7, 3))) == (3, 3)
    assert ordered_region_interval(parent, ElementRegion((2, 3), (1, 1), (7, 11))) == (4, 1)
    assert compact_ordered_affine_axes((2, 1, 3, 4), (12, 999, 4, 1)) == ((24, 1),)

    with pytest.raises(MeshIrError):
        ordered_region_interval(parent, ElementRegion((1, 0), (1, 3), (1, 3)))
    with pytest.raises(MeshIrError):
        ordered_region_interval(parent, ElementRegion((0, 0), (2, 2), (2, 6)))


def _affine_records(shape, source_strides, destination_strides):
    elements = 1
    for extent in shape:
        elements *= extent
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "source", TensorRole.INPUT, DType.FP32, shape, source_strides, StorageClass.EXTERNAL, Access.READ_ONLY, elements * 4, elements * 4, None),
        KernelTensor(2, 0, None, 2, 0, "destination", TensorRole.OUTPUT, DType.FP32, shape, destination_strides, StorageClass.EXTERNAL, Access.READ_WRITE, elements * 4, elements * 4, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,) * len(shape), shape, shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0,) * len(shape), shape, shape, 0),
    )
    views = (
        BufferView(1, 1, 1, (0,) * len(shape), shape, shape, 0, source_strides, None, None, 0),
        BufferView(2, 2, 2, (0,) * len(shape), shape, shape, 0, destination_strides, None, None, 0),
    )
    return KernelMemoryRecords(tensors, (), (Placement(1, (0,)),), shards, (), (), views, (), (), ())


def _reshaped_affine_records(source_shape, destination_shape, source_strides, destination_strides):
    source_elements = 1
    for extent in source_shape:
        source_elements *= extent
    destination_elements = 1
    for extent in destination_shape:
        destination_elements *= extent
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "source", TensorRole.INPUT, DType.FP32, source_shape, source_strides, StorageClass.EXTERNAL, Access.READ_ONLY, source_elements * 4, source_elements * 4, None),
        KernelTensor(2, 0, None, 2, 0, "destination", TensorRole.OUTPUT, DType.FP32, destination_shape, destination_strides, StorageClass.EXTERNAL, Access.READ_WRITE, destination_elements * 4, destination_elements * 4, None),
    )
    shards = (
        TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,) * len(source_shape), source_shape, source_shape, 0),
        TensorShard(2, 2, 1, 0, DistributionKind.PARTITIONED, (0,) * len(destination_shape), destination_shape, destination_shape, 0),
    )
    views = (
        BufferView(1, 1, 1, (0,) * len(source_shape), source_shape, source_shape, 0, source_strides, None, None, 0),
        BufferView(2, 2, 2, (0,) * len(destination_shape), destination_shape, destination_shape, 0, destination_strides, None, None, 0),
    )
    return KernelMemoryRecords(tensors, (), (Placement(1, (0,)),), shards, (), (), views, (), (), ())


def test_affine_projection_is_compact_for_dense_strided_and_reshaped_movements(monkeypatch):
    monkeypatch.setattr(segment_pass, "_coordinates", lambda _: (_ for _ in ()).throw(AssertionError("element enumeration")), raising=False)
    dense = _affine_records((2, 3, 4), (12, 4, 1), (12, 4, 1))
    region = ElementRegion((0, 0, 0), (2, 3, 4), (1, 1, 1))
    chunks = _paired_geometry(dense, OperandAccess(1, 1, region, OperandAccessMode.READ), StateTransition(1, 2, 2, region))
    assert tuple(item[2:6] for item in chunks) == ((1, 96, 96, 96),)

    strided = _affine_records((1024, 4), (8, 1), (8, 1))
    region = ElementRegion((0, 0), (1024, 4), (1, 1))
    chunks = _paired_geometry(strided, OperandAccess(1, 1, region, OperandAccessMode.READ), StateTransition(1, 2, 2, region))
    assert tuple(item[2:6] for item in chunks) == ((1024, 16, 32, 32),)

    reshaped = _reshaped_affine_records((2, 3), (6,), (3, 1), (1,))
    source_region = ElementRegion((0, 0), (2, 3), (1, 1))
    destination_region = ElementRegion((0,), (6,), (1,))
    chunks = _paired_geometry(reshaped, OperandAccess(1, 1, source_region, OperandAccessMode.READ), StateTransition(1, 2, 2, destination_region))
    assert tuple(item[2:6] for item in chunks) == ((1, 24, 24, 24),)

    singleton = _affine_records((1024, 1), (1, 99), (1, 777))
    region = ElementRegion((0, 0), (1024, 1), (1, 1))
    chunks = _paired_geometry(singleton, OperandAccess(1, 1, region, OperandAccessMode.READ), StateTransition(1, 2, 2, region))
    assert tuple(item[2:6] for item in chunks) == ((1, 4096, 4096, 4096),)

    middle_singleton = _affine_records((2, 1, 3), (3, 99, 1), (3, 777, 1))
    region = ElementRegion((0, 0, 0), (2, 1, 3), (1, 1, 1))
    chunks = _paired_geometry(middle_singleton, OperandAccess(1, 1, region, OperandAccessMode.READ), StateTransition(1, 2, 2, region))
    assert tuple(item[2:6] for item in chunks) == ((1, 24, 24, 24),)


def test_pass19_endpoint_offset_does_not_enumerate_region_spans(monkeypatch):
    records = _affine_records((1_000_000, 4), (8, 1), (8, 1))
    region = ElementRegion((999_999, 0), (1, 4), (1, 1))
    access = OperandAccess(1, 1, region, OperandAccessMode.READ)
    monkeypatch.setattr(address_pass, "region_byte_spans", lambda *_: (_ for _ in ()).throw(AssertionError("span enumeration")), raising=False)

    assert address_pass._access_offset(records, access, region) == 999_999 * 32


def test_affine_segment_projection_splits_transpose_and_nonuniform_outer_rows_in_order():
    transpose = _affine_records((2, 3), (1, 2), (3, 1))
    region = ElementRegion((0, 0), (2, 3), (1, 1))
    source = OperandAccess(1, 1, region, OperandAccessMode.READ)
    destination = StateTransition(1, 2, 2, region)

    chunks = _paired_geometry(transpose, source, destination)

    assert tuple((item[0].origin, item[1].origin, item[2:6]) for item in chunks) == (
        ((0, 0), (0, 0), (3, 4, 8, 4)),
        ((1, 0), (1, 0), (3, 4, 8, 4)),
    )

    nonuniform = _affine_records((2, 2, 3), (20, 4, 1), (6, 3, 1))
    region = ElementRegion((0, 0, 0), (2, 2, 3), (1, 1, 1))
    source = OperandAccess(1, 1, region, OperandAccessMode.READ)
    destination = StateTransition(1, 2, 2, region)
    chunks = _paired_geometry(nonuniform, source, destination)

    assert tuple((item[0].origin, item[1].origin, item[2:6]) for item in chunks) == (
        ((0, 0, 0), (0, 0, 0), (1, 12, 12, 12)),
        ((0, 1, 0), (0, 1, 0), (1, 12, 12, 12)),
        ((1, 0, 0), (1, 0, 0), (1, 12, 12, 12)),
        ((1, 1, 0), (1, 1, 0), (1, 12, 12, 12)),
    )


def _state(arch, external_access=Access.READ_ONLY):
    tensor = KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP32, (4,), (1,), StorageClass.EXTERNAL, Access.READ_ONLY, 16, 16, None)
    shard = TensorShard(1, 1, 1, 0, DistributionKind.PARTITIONED, (0,), (4,), (4,), 0)
    objects = (BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (4,), (1,), 16, 16, False, 0), BufferObject(2, 1, 0, MemorySpace.CORE_SRAM, (4,), (1,), 16, 16, False, 0))
    views = (BufferView(1, 1, 1, (0,), (4,), (4,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0), BufferView(2, 2, 1, (0,), (4,), (4,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0))
    states = (TensorState(1, 1, 0, StateOrigin.EXTERNAL), TensorState(2, 2, 0, StateOrigin.EMPTY), TensorState(3, 2, 1, StateOrigin.PRODUCED))
    region = ElementRegion((0,), (4,), (1,))
    ops = (
        KernelOp(1, 0, "alloc:external", KernelOpcode.ALLOC, INVALID_CORE_ID, 0, (), (), AllocAttrs(1), (), None),
        KernelOp(2, 0, "alloc:local", KernelOpcode.ALLOC, 0, 0, (), (), AllocAttrs(2), (), None),
        KernelOp(3, 0, "view:external", KernelOpcode.VIEW, INVALID_CORE_ID, 0, (), (), ViewDeclarationAttrs(1), (), None),
        KernelOp(4, 0, "view:local", KernelOpcode.VIEW, 0, 0, (), (), ViewDeclarationAttrs(2), (), None),
        KernelOp(5, 0, "load", KernelOpcode.DMA, 0, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, 0, INVALID_CORE_ID, 0, 0, b""), (), 1),
    )
    binding = Binding(1, 0, INVALID_CORE_ID, 0, 32, 32, external_access)
    slot = BindingSlot(1, "input", MemorySpace.HBM, 0, INVALID_CORE_ID, 16, 32, external_access, binding)
    src_ref = AddressRef(1, MemorySpace.HBM, 0, INVALID_CORE_ID, SlotAddress(1), 0, 16, 16, 1, Access.READ_ONLY)
    dst_ref = AddressRef(2, MemorySpace.CORE_SRAM, 2, 0, DirectAddress(0, 0, 32, 32, Access.READ_WRITE), 0, 16, 16, 1, Access.READ_WRITE)
    resolved = resolve_addresses(arch, (slot,), (binding,), (src_ref, dst_ref), issuing_core=0)
    identity = DescriptorIdentity(1, 1, 2, 1, 0, INVALID_CORE_ID, 1, TensorRole.INPUT, TrafficDirection.READ)
    descriptor = ResolvedDescriptor(identity, DmaKind.LOAD, resolved.addresses[0], resolved.addresses[1], 1, 16, 16, 16, 16, 16, arch.axi_max_burst_beats)
    execution = DescriptorExecution(descriptor, 1)
    membership = VariantMembership(IdSpan(1, 1), IdSpan(1, 2), IdSpan(1, 1), IdSpan(1, 2), IdSpan(1, 4), IdSpan(1, 3), IdSpan(1, 1), IdSpan(1, 1), IdSpan(1, 1), IdSpan(1, 0), IdSpan(1, 1), IdSpan(1, 1), IdSpan(1, 0), IdSpan(1, 2), IdSpan(1, 2), IdSpan(1, 3), IdSpan(1, 1), IdSpan(1, 5), IdSpan(1, 2), IdSpan(1, 4), IdSpan(1, 0), IdSpan(1, 3), IdSpan(1, 2), IdSpan(1, 1))
    transport = _TransportSections(
        (StringEntry("main"), StringEntry("default"), StringEntry("input")),
        (Entrypoint(1, 1, 0, 1, 0, 0),), (Profile(1, 1, 2, 0),),
        (Tensor(1, 3, A.TENSOR_ROLE.INPUT, A.DTYPE.FP32, A.STORAGE_CLASS.EXTERNAL, A.ACCESS_KIND.READ_ONLY, 1, A.LAYOUT_KIND.CONTIGUOUS_ROW_MAJOR, dims=(4, 0, 0, 0, 0, 0, 0, 0)),),
        (Shard(1, 1, 0, INVALID_CORE_ID, 1, global_origin=(0,) * 8, local_shape=(4, 0, 0, 0, 0, 0, 0, 0), valid_shape=(4, 0, 0, 0, 0, 0, 0, 0), span_bytes=16), Shard(2, 1, 0, 0, 1, global_origin=(0,) * 8, local_shape=(4, 0, 0, 0, 0, 0, 0, 0), valid_shape=(4, 0, 0, 0, 0, 0, 0, 0), allocation_id=1, span_bytes=16)),
        (Allocation(1, 0, A.MEMORY_SPACE.CORE_SRAM, 0, 32, 32),),
        (Stream(0, 0, 0, 3, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL), Stream(0, 1, 3, 1)),
        (Command(1, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_BEGIN, signal_event=1), Command(2, 5, 0, 1, A.ENGINE.DMA_READ, A.OPCODE.DMA_LOAD, 0, 1, 2, 0, signal_event=2), Command(3, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_END, 1, 1, 0, 2, signal_event=3), Command(4, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.HALT, 2, 1, 0, 2)),
        (CommandWait(1), CommandWait(2), CommandWait(3)),
        (CommandOperand(1, 1, 0, A.ACCESS_KIND.READ_ONLY), CommandOperand(1, 2, 1, A.ACCESS_KIND.READ_WRITE)),
        (Event(1, A.EVENT_KIND.NORMAL, producer_command_id=1), Event(2, A.EVENT_KIND.NORMAL, producer_command_id=2), Event(3, A.EVENT_KIND.NORMAL, producer_command_id=3)),
        (DmaDescriptor(1, 2, 0, 0, A.DMA_KIND.LOAD, DmaEndpoint(A.MEMORY_SPACE.HBM, 0, INVALID_CORE_ID, 1, 1, offset_bytes=0), DmaEndpoint(A.MEMORY_SPACE.CORE_SRAM, 2, 0, 1, 2, offset_bytes=0), 1, 16, 16, 16, 16, 16, 0, 0, 0, arch.axi_max_burst_beats, 0, 2),), (),
        (Relocation(1, 3, A.RELOCATION_KIND.TENSOR_BASE, 0, 1, 0, 0, 0),),
    )
    semantics = _PreTrafficSemantics(
        AuthoredProgramOrigin("unit", "load", 1), (ProgramVariant(1, 1, 1, AuthoredVariantLineage("load"), 1, membership),), (tensor,), (), (Placement(1, (0,)),), (shard,), (), objects, views, states, (ControlToken(1),), ops,
        (ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(2, LocalAllocationBacking(1))), (ResidentView(1, 1), ResidentView(2, 2)),
        (CommandSemantics(1, ControlCommandSource(RequestBeginAttrs()), ControlExecution()), CommandSemantics(2, KernelCommandSource(5), DmaExecution(1)), CommandSemantics(3, ControlCommandSource(RequestEndAttrs()), ControlExecution()), CommandSemantics(4, ControlCommandSource(HaltAttrs()), ControlExecution())), (),
        (ScheduledDependency(1, 1, 2, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1)), ScheduledDependency(2, 2, 3, ScheduledDependencyKind.DMA_COMPLETION, DescriptorSource(1)), ScheduledDependency(3, 3, 4, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1))),
        (ScheduledStream(1, 0, 0, 0, 3, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL), ScheduledStream(2, 0, 1, 3, 1, 0)), (1, 3, 4, 2), (DescriptorGroup(1, 2, 5, (1,), 2),), (DescriptorEndpointUse(1, 1, EndpointSide.SRC, ReadAccessUse(5, 0, region)), DescriptorEndpointUse(2, 1, EndpointSide.DST, WriteAccessUse(5, 0, region))), (slot,), semantic_sha256(((1, 0, resolved.identity_sha256),)), (execution,))
    return _PreTrafficState(A.ABI_MAJOR, A.ABI_MINOR, arch.digest(), transport, semantics)


def test_readonly_external_input_loads_into_owned_writable_local_backing():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)

    verified = verify_program(program, arch)

    assert verified.program is program
    assert program.semantics.intrinsic_traffic.descriptors[0].bursts == 1
    assert program.expected_traffic[0].useful_bytes == 16


@pytest.mark.parametrize("change", ("missing", "dangling"))
def test_public_verifier_validates_backing_admission_before_address_lookup(change):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    if change == "missing":
        membership = replace(program.semantics.variants[0].membership, object_backings=IdSpan(1, 1))
        variant = replace(program.semantics.variants[0], membership=membership)
        semantics = replace(program.semantics, variants=(variant,), object_backings=program.semantics.object_backings[1:])
    else:
        backing = replace(program.semantics.object_backings[1], backing=LocalAllocationBacking(2))
        semantics = replace(program.semantics, object_backings=(program.semantics.object_backings[0], backing))
    provisional = replace(program, semantics=semantics, semantic_sha256="")
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_ABI_BOUNDS"


def _with_descriptor_pieces(program, arch, destination_origins):
    descriptor = program.dma_descriptors[0]
    descriptors = tuple(replace(descriptor, descriptor_id=index + 1, src=replace(descriptor.src, offset_bytes=index * 8), dst=replace(descriptor.dst, offset_bytes=destination_origins[index] * 4), row_bytes=8, src_stride_bytes=8, dst_stride_bytes=8, useful_bytes=8, physical_storage_bytes=8) for index in range(2))
    regions = tuple(ElementRegion((index * 2,), (2,), (1,)) for index in range(2))
    destination_regions = tuple(ElementRegion((origin,), (2,), (1,)) for origin in destination_origins)
    uses = tuple(item for index in range(2) for item in (DescriptorEndpointUse(index * 2 + 1, index + 1, EndpointSide.SRC, ReadAccessUse(5, 0, regions[index])), DescriptorEndpointUse(index * 2 + 2, index + 1, EndpointSide.DST, WriteAccessUse(5, 0, destination_regions[index]))))
    dependencies = program.semantics.dependencies + (ScheduledDependency(4, 2, 3, ScheduledDependencyKind.DMA_COMPLETION, DescriptorSource(2)),)
    membership = replace(program.semantics.variants[0].membership, descriptors=IdSpan(1, 2), dependencies=IdSpan(1, 4), endpoint_uses=IdSpan(1, 4))
    variant = replace(program.semantics.variants[0], membership=membership)
    semantics = replace(program.semantics, variants=(variant,), descriptor_groups=(replace(program.semantics.descriptor_groups[0], descriptor_ids=(1, 2)),), endpoint_uses=uses, dependencies=dependencies, intrinsic_traffic=calculate_traffic(arch, semantic_sha256(()), ()), reference_binding_identity_sha256=semantic_sha256(()))
    provisional = replace(program, dma_descriptors=descriptors, expected_traffic=(), semantics=semantics, semantic_sha256="")
    executions, identity = derive_descriptor_execution_set(provisional, arch)
    report = calculate_traffic(arch, identity, executions)
    semantics = replace(semantics, reference_binding_identity_sha256=identity, intrinsic_traffic=report)
    provisional = replace(provisional, semantics=semantics, expected_traffic=_expected_traffic(report, executions, arch))
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def test_public_verifier_accepts_equivalent_ordered_descriptor_piece_coverage():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    split = _with_descriptor_pieces(program, arch, (0, 2))

    assert verify_program(split, arch).program is split


@pytest.mark.parametrize("destination_origins", ((2, 0), (0, 0)))
def test_public_verifier_rejects_permuted_or_duplicated_descriptor_piece_coverage(destination_origins):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    forged = _with_descriptor_pieces(program, arch, destination_origins)

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_DMA_RANGE"


@pytest.mark.parametrize("row_bytes", (8, 0))
def test_public_verifier_rejects_truncated_descriptor_coverage_with_matching_traffic(row_bytes):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    descriptor = replace(program.dma_descriptors[0], row_bytes=row_bytes, src_stride_bytes=row_bytes, dst_stride_bytes=row_bytes, useful_bytes=row_bytes, physical_storage_bytes=row_bytes)
    provisional = replace(program, dma_descriptors=(descriptor,), expected_traffic=(), semantic_sha256="")
    executions, identity = derive_descriptor_execution_set(provisional, arch)
    report = calculate_traffic(arch, identity, executions)
    semantics = replace(program.semantics, reference_binding_identity_sha256=identity, intrinsic_traffic=report)
    provisional = replace(provisional, semantics=semantics, expected_traffic=_expected_traffic(report, executions, arch))
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_DMA_RANGE"


def test_fresh_hash_cannot_upgrade_external_slot_permission():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    slot = replace(program.semantics.binding_slots[0], access=Access.READ_WRITE)
    semantics = replace(program.semantics, binding_slots=(slot,))
    provisional = replace(program, semantics=semantics, semantic_sha256="")
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError):
        verify_program(forged, arch)


def test_fresh_hash_cannot_forge_abi_traffic_projection():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    rows = (replace(program.expected_traffic[0], bursts=2),)
    provisional = replace(program, expected_traffic=rows, semantic_sha256="")
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_TRAFFIC_MISMATCH"


def test_binding_recomputes_selected_traffic_without_mutating_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    before = program.canonical_bytes()

    invocation = bind_invocation(program, arch, 1, 1, (program.semantics.binding_slots[0].reference_binding,))

    assert invocation.program_semantic_sha256 == program.semantic_sha256
    assert invocation.bindings == (program.semantics.binding_slots[0].reference_binding,)
    assert invocation.traffic.descriptors[0].bursts == 1
    assert program.canonical_bytes() == before


def test_fresh_report_hash_cannot_replace_reference_binding_identity():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    forged_identity = "f" * 64
    report = calculate_traffic(arch, forged_identity, derive_descriptor_executions(program, arch))
    semantics = replace(program.semantics, reference_binding_identity_sha256=forged_identity, intrinsic_traffic=report)
    provisional = replace(program, semantics=semantics, semantic_sha256="")
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_RELOCATION"


def test_pass15_preserves_kernel_and_uses_actual_dma_reserved_extent():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    state = _state(arch)
    semantics = state.semantics
    module = KernelModule.create(arch.digest().hex(), "a" * 64, "main", "default", semantics.kernel_tensors, (), (KernelTensorLineage(1, 1),), (), semantics.placements, semantics.logical_shards, (), semantics.objects, semantics.views, semantics.states, semantics.tokens, semantics.kernel_ops)
    bundle = KernelBundle.create(arch.digest().hex(), "b" * 64, (module,))

    result = plan_static_sram_stage(bundle, arch)

    assert result.origin.kernel_bundle_semantic_sha256 == bundle.semantic_sha256
    assert result.variants[0].source.lineage.kernel_module_semantic_sha256 == module.semantic_sha256
    assert result.variants[0].source.records == module.memory_records()
    assert result.variants[0].plan.allocations[0].size_bytes == arch.axi_data_bytes
    assert result.variants[0].plan.allocations[0].alignment_bytes == max(arch.axi_data_bytes, arch.sram_base_alignment_bytes)
    assert module.semantic_sha256 == bundle.modules[0].semantic_sha256

    hazards = insert_hazard_dependencies_stage(result)

    assert hazards.static is result
    assert hazards.semantic_sha256 != result.semantic_sha256

    schedule = schedule_per_core_streams_stage(hazards, arch)
    segments = lower_dma_to_segments_stage(schedule)

    command = next(item for item in schedule.variants[0].commands if type(item.source) is KernelCommandSource)
    assert command.engine is Engine.DMA_READ
    assert command.phases == ()
    assert segments.segments[0].rows == 1
    assert segments.segments[0].row_bytes == 16

    scheduled = schedule_per_core_streams_stage(hazards, arch)

    command = next(item for item in scheduled.variants[0].commands if type(item.source) is KernelCommandSource)
    assert command.source.kernel_op_id == 5
    assert command.engine is Engine.DMA_READ
    assert command.phases == ()

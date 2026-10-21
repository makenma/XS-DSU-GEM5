from dataclasses import dataclass, replace

from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.architecture import ArchManifest
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_dma_pin_program, build_repeat_program
from mesh_ir.ir.common import Access, DType, DmaKind, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import AllocAttrs, BufferObject, BufferView, ControlToken, DistributionKind, DmaAttrs, ElementRegion, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, Placement, StateOrigin, StateTransition, TensorShard, TensorState, ViewDeclarationAttrs
from mesh_ir.model import Program
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ExternalSlotBacking, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.traffic import calculate_traffic
from tests.golden.support.stage2_sram_reuse_fixture import build_sram_reuse_program


@dataclass(frozen=True)
class Stage4LifetimeCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None
    expected_message: str | None


def _refreshed(program: Program, **changes) -> Program:
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def _with_allocation_offset(
    program: Program, arch: ArchManifest, object_id: int, offset_bytes: int
) -> Program:
    allocation_id = next(
        item.backing.allocation_id
        for item in program.semantics.object_backings
        if item.object_id == object_id
    )
    candidate = _refreshed(
        program,
        allocations=tuple(
            replace(item, offset_bytes=offset_bytes)
            if item.allocation_id == allocation_id
            else item
            for item in program.allocations
        ),
    )
    if not candidate.dma_descriptors:
        return candidate
    executions, binding_identity = derive_descriptor_execution_set(candidate, arch)
    traffic = calculate_traffic(arch, binding_identity, executions)
    return _refreshed(
        candidate,
        expected_traffic=_expected_traffic(traffic, executions, arch),
        semantics=replace(
            candidate.semantics,
            reference_binding_identity_sha256=binding_identity,
            intrinsic_traffic=traffic,
        ),
    )


def _unused_local_records(arch: ArchManifest) -> KernelMemoryRecords:
    owner = arch.core_ids[0]
    sizes = (16, 16, 0, 16)
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "entry-a", TensorRole.WEIGHT, DType.INT8, (16,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 16, 16, "1" * 64),
        KernelTensor(2, 0, None, 2, 0, "entry-b", TensorRole.WEIGHT, DType.INT8, (16,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 16, 16, "2" * 64),
        KernelTensor(3, 0, None, 3, 0, "entry-zero", TensorRole.WEIGHT, DType.INT8, (0,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 0, 0, "3" * 64),
        KernelTensor(4, 0, None, 4, 0, "ordinary-empty", TensorRole.ACTIVATION, DType.INT8, (16,), (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, 16, 16, None),
    )
    shards = tuple(
        TensorShard(index, index, 1, owner, DistributionKind.PARTITIONED, (0,), (size,), (size,), 0)
        for index, size in enumerate(sizes, 1)
    )
    objects = tuple(
        BufferObject(index, index, owner, MemorySpace.CORE_SRAM, (size,), (1,), size, arch.sram_base_alignment_bytes, False, index - 1)
        for index, size in enumerate(sizes, 1)
    )
    views = tuple(
        BufferView(index, index, index, (0,), (size,), (size,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0)
        for index, size in enumerate(sizes, 1)
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT, 0),
        TensorState(2, 2, 0, StateOrigin.PRE_RESIDENT, 0),
        TensorState(3, 3, 0, StateOrigin.PRE_RESIDENT, 0),
        TensorState(4, 4, 0, StateOrigin.EMPTY, 0),
    )
    ops = (
        *(KernelOp(index, 0, f"unused:alloc:{index}", KernelOpcode.ALLOC, owner, 0, (), (), AllocAttrs(index), (), None) for index in range(1, 5)),
        *(KernelOp(index + 4, 0, f"unused:view:{index}", KernelOpcode.VIEW, owner, 0, (), (), ViewDeclarationAttrs(index), (), None) for index in range(1, 5)),
    )
    return KernelMemoryRecords(tensors, (), (Placement(1, (owner,)),), shards, (), objects, views, states, (), ops)


def build_unused_local_lifetime_program(arch: ArchManifest) -> Program:
    records = _unused_local_records(arch)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage4-unused-local", 1))
    variant = builder.variant(
        "main",
        "unused-local",
        "unused-local",
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
    )
    stream = variant.stream(
        arch.core_ids[0],
        0,
        A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    stream.control_command(RequestBeginAttrs())
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def build_dma_reuse_program(
    arch: ArchManifest,
    replacement_after_tokens: tuple[int, ...],
    replacement_region: ElementRegion,
) -> Program:
    seed = build_dma_pin_program(arch)
    owner = arch.core_ids[0]
    replacement = KernelOp(0, 0, "pin:reuse:fill", KernelOpcode.DMA, owner, 0, (), (StateTransition(7, 8, 4, replacement_region),), DmaAttrs(DmaKind.LOCAL_FILL, owner, owner, owner, 0, b"\x00"), replacement_after_tokens, 4)
    if replacement_after_tokens:
        effects = (
            replace(seed.semantics.kernel_ops[6], op_id=9),
            replace(seed.semantics.kernel_ops[7], op_id=10),
            replace(replacement, op_id=11),
            replace(seed.semantics.kernel_ops[8], op_id=12),
        )
    else:
        effects = (
            replace(seed.semantics.kernel_ops[6], op_id=9),
            replace(replacement, op_id=10),
            replace(seed.semantics.kernel_ops[7], op_id=11),
            replace(seed.semantics.kernel_ops[8], op_id=12),
        )
    records = KernelMemoryRecords(
        (*seed.semantics.kernel_tensors, KernelTensor(4, 0, None, 4, 0, "reuse", TensorRole.ACTIVATION, DType.INT8, (128,), (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, 128, 128, None)),
        seed.semantics.computations,
        seed.semantics.placements,
        (*seed.semantics.logical_shards, TensorShard(4, 4, 1, owner, DistributionKind.PARTITIONED, (0,), (128,), (128,), 0)),
        seed.semantics.partial_sums,
        (*seed.semantics.objects, BufferObject(4, 4, owner, MemorySpace.CORE_SRAM, (128,), (1,), 128, arch.sram_base_alignment_bytes, False, 1)),
        (*seed.semantics.views, BufferView(4, 4, 4, (0,), (128,), (128,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 0)),
        (*seed.semantics.states, TensorState(7, 4, 0, StateOrigin.EMPTY, 0), TensorState(8, 4, 1, StateOrigin.PRODUCED, 0)),
        (*seed.semantics.tokens, ControlToken(4)),
        (
            *seed.semantics.kernel_ops[:3],
            KernelOp(4, 0, "pin:reuse:alloc", KernelOpcode.ALLOC, owner, 0, (), (), AllocAttrs(4), (), None),
            *(replace(item, op_id=item.op_id + 1) for item in seed.semantics.kernel_ops[3:6]),
            KernelOp(8, 0, "pin:reuse:view", KernelOpcode.VIEW, owner, 0, (), (), ViewDeclarationAttrs(4), (), None),
            *effects,
        ),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage4-outstanding-dma", 1))
    variant = builder.variant(
        "main",
        "outstanding-dma",
        "outstanding-dma",
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
        external_backings=tuple(
            item
            for item in seed.semantics.object_backings
            if type(item.backing) is ExternalSlotBacking
        ),
        binding_slots=seed.semantics.binding_slots,
    )
    primary = variant.stream(owner, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    secondary = variant.stream(owner, 1)
    primary.control_command(RequestBeginAttrs())
    primary.kernel_command(9)
    primary.kernel_command(10 if replacement_after_tokens else 11)
    primary.control_command(RequestEndAttrs())
    primary.control_command(HaltAttrs())
    secondary.kernel_command(11 if replacement_after_tokens else 10)
    secondary.kernel_command(12)
    return builder.build()


def build_stage4_lifetime_cases(arch: ArchManifest) -> tuple[Stage4LifetimeCase, ...]:
    ordered_reuse = build_sram_reuse_program(arch)
    old_object = next(item for item in ordered_reuse.semantics.objects if item.object_id == 1)
    old_allocation_id = next(
        item.backing.allocation_id
        for item in ordered_reuse.semantics.object_backings
        if item.object_id == old_object.object_id
    )
    persistent_reuse = _refreshed(
        ordered_reuse,
        allocations=tuple(
            replace(item, flags=1)
            if item.allocation_id == old_allocation_id
            else item
            for item in ordered_reuse.allocations
        ),
        semantics=replace(
            ordered_reuse.semantics,
            objects=tuple(
                replace(item, persistent=True)
                if item.object_id == old_object.object_id
                else item
                for item in ordered_reuse.semantics.objects
            ),
        ),
    )
    unused_planned = build_unused_local_lifetime_program(arch)
    entry_offset = next(
        item.offset_bytes
        for item in unused_planned.allocations
        if item.allocation_id
        == next(
            backing.backing.allocation_id
            for backing in unused_planned.semantics.object_backings
            if backing.object_id == 1
        )
    )
    unused = _with_allocation_offset(
        unused_planned,
        arch,
        4,
        entry_offset + 2 * arch.sram_base_alignment_bytes,
    )
    full_region = ElementRegion((0,), (128,), (1,))
    outstanding = build_dma_reuse_program(arch, (), full_region)
    partial_frontier = build_dma_reuse_program(arch, (2,), full_region)
    zero_byte_access = build_dma_reuse_program(arch, (), ElementRegion((0,), (0,), (1,)))
    outstanding_offset = next(
        item.offset_bytes
        for item in outstanding.allocations
        if item.allocation_id
        == next(
            backing.backing.allocation_id
            for backing in outstanding.semantics.object_backings
            if backing.object_id == 1
        )
    )
    repeat = build_repeat_program(arch)
    return (
        Stage4LifetimeCase("ordered_sram_reuse_positive", arch, ordered_reuse, ordered_reuse, None, None),
        Stage4LifetimeCase("persistent_old_object_blocks_reuse", arch, ordered_reuse, persistent_reuse, "E_SRAM_OOM", "overlapping local allocations have conflicting lifetimes"),
        Stage4LifetimeCase("unused_pre_resident_overlap", arch, unused, _with_allocation_offset(unused, arch, 2, entry_offset), "E_SRAM_OOM", "overlapping local allocations have conflicting lifetimes"),
        Stage4LifetimeCase("unused_zero_extent_overlap_positive", arch, unused, _with_allocation_offset(unused, arch, 3, entry_offset + arch.sram_base_alignment_bytes), None, None),
        Stage4LifetimeCase("unused_empty_overlap_positive", arch, unused, _with_allocation_offset(unused, arch, 4, entry_offset), None, None),
        Stage4LifetimeCase("outstanding_dma_reader_blocks_reuse", arch, outstanding, _with_allocation_offset(outstanding, arch, 4, outstanding_offset), "E_SRAM_OOM", "overlapping local allocations have conflicting lifetimes"),
        Stage4LifetimeCase("completed_first_dma_reader_still_blocks_reuse", arch, partial_frontier, _with_allocation_offset(partial_frontier, arch, 4, outstanding_offset), "E_SRAM_OOM", "overlapping local allocations have conflicting lifetimes"),
        Stage4LifetimeCase("zero_byte_dma_replacement_blocks_reuse", arch, zero_byte_access, _with_allocation_offset(zero_byte_access, arch, 4, outstanding_offset), "E_SRAM_OOM", "overlapping local allocations have conflicting lifetimes"),
        Stage4LifetimeCase("repeat_static_frontier_positive", arch, repeat, repeat, None, None),
    )

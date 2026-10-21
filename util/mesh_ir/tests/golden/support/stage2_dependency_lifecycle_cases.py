from dataclasses import dataclass, replace

from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import (
    build_dma_fence_program,
    build_dma_pin_program,
    build_fence_scopes_program,
)
from mesh_ir.ir.common import (
    Access,
    DType,
    DmaKind,
    INVALID_CORE_ID,
    Layout,
    MemorySpace,
    StorageClass,
    TensorRole,
)
from mesh_ir.ir.kernel_ir import (
    AllocAttrs,
    BufferObject,
    BufferView,
    ControlToken,
    DistributionKind,
    DmaAttrs,
    ElementRegion,
    KernelMemoryRecords,
    KernelOp,
    KernelOpcode,
    KernelTensor,
    OperandAccess,
    OperandAccessMode,
    Placement,
    RecvWaitAttrs,
    StateOrigin,
    StateTransition,
    TensorShard,
    TensorState,
    ViewDeclarationAttrs,
)
from mesh_ir.model import CommandWait, Program
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin,
    DescriptorSource,
    ExternalSlotBacking,
    HaltAttrs,
    IdSpan,
    LifecycleSource,
    ObjectSource,
    RequestBeginAttrs,
    RequestEndAttrs,
    ScheduledDependency,
    ScheduledDependencyKind,
    StateSource,
    StreamOrderSource,
)
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage2_sram_reuse_fixture import build_sram_reuse_program
from tests.unit.test_gate2_dependency_sram import weight_scratch_kernel


@dataclass(frozen=True)
class Stage2DependencyLifecycleCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None


def _refreshed(program: Program, **changes) -> Program:
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def _with_dependency(program: Program, predicate, source) -> Program:
    dependencies = tuple(
        replace(item, source=source) if predicate(item) else item
        for item in program.semantics.dependencies
    )
    return _refreshed(
        program,
        semantics=replace(program.semantics, dependencies=dependencies),
    )


def _with_op(program: Program, op_id: int, **changes) -> Program:
    operations = tuple(
        replace(item, **changes) if item.op_id == op_id else item
        for item in program.semantics.kernel_ops
    )
    return _refreshed(
        program,
        semantics=replace(program.semantics, kernel_ops=operations),
    )


def _variant_with_count(program: Program, field: str, count: int) -> Program:
    variant = program.semantics.variants[0]
    membership = replace(
        variant.membership,
        **{field: replace(getattr(variant.membership, field), count=count)},
    )
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            variants=(replace(variant, membership=membership),),
        ),
    )


def _grouped_p2p_program(arch: ArchManifest) -> Program:
    shape = (3, 5)
    strides = (5, 1)
    tensor = KernelTensor(
        1,
        0,
        None,
        1,
        0,
        "partial",
        TensorRole.WEIGHT,
        DType.FP32,
        shape,
        strides,
        StorageClass.PRE_RESIDENT,
        Access.READ_ONLY,
        60,
        60,
        "a" * 64,
    )
    shards = (
        TensorShard(1, 1, 1, 3, DistributionKind.REPLICATED, (0, 0), shape, shape),
        TensorShard(2, 1, 1, 7, DistributionKind.REPLICATED, (0, 0), shape, shape),
    )
    objects = (
        BufferObject(1, 1, 3, MemorySpace.CORE_SRAM, shape, strides, 60, 4, False, 0),
        BufferObject(2, 1, 7, MemorySpace.CORE_SRAM, (3, 8), (8, 1), 96, 4, False, 0),
    )
    views = (
        BufferView(1, 1, 1, (0, 0), shape, shape, 0, strides, None, None, 0),
        BufferView(2, 2, 2, (0, 0), shape, shape, 0, (8, 1), None, None, 0),
    )
    states = (
        TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
    )
    first = ElementRegion((0, 4), (1, 1), (1, 1))
    second = ElementRegion((1, 0), (1, 3), (1, 1))
    records = KernelMemoryRecords(
        (tensor,),
        (),
        (Placement(1, (3, 7)),),
        shards,
        (),
        objects,
        views,
        states,
        (ControlToken(1), ControlToken(2)),
        (
            KernelOp(1, 0, "alloc:1", KernelOpcode.ALLOC, 3, 0, (), (), AllocAttrs(1), (), None),
            KernelOp(2, 0, "alloc:2", KernelOpcode.ALLOC, 7, 0, (), (), AllocAttrs(2), (), None),
            KernelOp(3, 0, "view:1", KernelOpcode.VIEW, 3, 0, (), (), ViewDeclarationAttrs(1), (), None),
            KernelOp(4, 0, "view:2", KernelOpcode.VIEW, 7, 0, (), (), ViewDeclarationAttrs(2), (), None),
            KernelOp(
                5,
                0,
                "p2p:41",
                KernelOpcode.DMA,
                3,
                0,
                (
                    OperandAccess(1, 1, first, OperandAccessMode.READ),
                    OperandAccess(1, 1, second, OperandAccessMode.READ),
                ),
                (
                    StateTransition(2, 3, 2, first),
                    StateTransition(2, 3, 2, second),
                ),
                DmaAttrs(DmaKind.P2P_PUSH, 3, 3, 7, 41, b""),
                (),
                1,
            ),
            KernelOp(
                6,
                0,
                "recv:41",
                KernelOpcode.RECV_WAIT,
                7,
                0,
                (),
                (),
                RecvWaitAttrs(41, 3, 7, 16),
                (1,),
                2,
            ),
        ),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage2-grouped-p2p", 1))
    variant = builder.variant(
        "main",
        "stage2-grouped-p2p",
        "stage2-grouped-p2p",
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
    )
    sender = variant.stream(3, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    receiver = variant.stream(7, 0)
    sender.control_command(RequestBeginAttrs())
    sender.kernel_command(5)
    sender.control_command(RequestEndAttrs())
    sender.control_command(HaltAttrs())
    receiver.kernel_command(6)
    receiver.control_command(HaltAttrs())
    return builder.build()


def _war_program(arch: ArchManifest) -> Program:
    seed = build_dma_pin_program(arch)
    semantics = seed.semantics
    records = KernelMemoryRecords(
        semantics.kernel_tensors,
        semantics.computations,
        semantics.placements,
        semantics.logical_shards,
        semantics.partial_sums,
        semantics.objects,
        semantics.views,
        (*semantics.states, TensorState(7, 1, 2, StateOrigin.PRODUCED)),
        (*semantics.tokens, ControlToken(4)),
        (
            *semantics.kernel_ops,
            KernelOp(
                10,
                0,
                "war:overwrite",
                KernelOpcode.DMA,
                0,
                0,
                (),
                (
                    StateTransition(
                        2,
                        7,
                        1,
                        next(item for item in semantics.kernel_ops if item.op_id == 8).reads[0].region,
                    ),
                ),
                DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x00"),
                (2, 3),
                4,
            ),
        ),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage2-war", 1))
    variant = builder.variant(
        "main",
        "stage2-war",
        "stage2-war",
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
        external_backings=tuple(
            item for item in semantics.object_backings if type(item.backing) is ExternalSlotBacking
        ),
        binding_slots=semantics.binding_slots,
    )
    primary = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    secondary = variant.stream(0, 1)
    primary.control_command(RequestBeginAttrs())
    primary.kernel_command(7)
    primary.kernel_command(8)
    primary.kernel_command(10)
    primary.control_command(RequestEndAttrs())
    primary.control_command(HaltAttrs())
    secondary.kernel_command(9)
    return builder.build()


def build_two_variant_program(arch: ArchManifest) -> Program:
    records = weight_scratch_kernel(arch, False).memory_records()
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage2-two-variant", 1))
    for profile in ("stage2-one", "stage2-two"):
        variant = builder.variant(
            "main",
            profile,
            profile,
            records=records,
            allocations=plan_memory_sram(records, arch).allocations,
        )
        stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
        stream.control_command(RequestBeginAttrs())
        stream.kernel_command(7)
        stream.kernel_command(8)
        stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    return builder.build()


def _two_variant_initial_program(arch: ArchManifest) -> Program:
    source = weight_scratch_kernel(arch, False).memory_records()
    records = replace(
        source,
        tokens=(
            ControlToken(1, initial=True),
            ControlToken(2, initial=True),
            ControlToken(3),
            ControlToken(4),
            ControlToken(5, initial=True),
            ControlToken(6, initial=True),
            ControlToken(7, initial=True),
            ControlToken(8, initial=True),
        ),
        ops=tuple(
            replace(item, stable_key="a", after_tokens=(1,), done_token=3)
            if item.op_id == 7
            else replace(item, stable_key="b", after_tokens=(2,), done_token=4)
            if item.op_id == 8
            else item
            for item in source.ops
        ),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage2-two-variant-initial", 1))
    for profile in ("stage2-initial-one", "stage2-initial-two"):
        variant = builder.variant(
            "main",
            profile,
            profile,
            records=records,
            allocations=plan_memory_sram(records, arch).allocations,
        )
        stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
        stream.control_command(RequestBeginAttrs())
        stream.kernel_command(7)
        stream.kernel_command(8)
        stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    return builder.build()


def _append_dma_pin(program: Program) -> Program:
    dependency = ScheduledDependency(
        len(program.semantics.dependencies) + 1,
        2,
        5,
        ScheduledDependencyKind.DMA_PIN,
        DescriptorSource(1),
    )
    variant = program.semantics.variants[0]
    membership = replace(
        variant.membership,
        dependencies=IdSpan(1, len(program.semantics.dependencies) + 1),
    )
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            dependencies=(*program.semantics.dependencies, dependency),
            variants=(replace(variant, membership=membership),),
        ),
    )


def _duplicate_state_producer(program: Program) -> Program:
    first_writer = next(item for item in program.semantics.kernel_ops if item.op_id == 7)
    overwrite = next(item for item in program.semantics.kernel_ops if item.op_id == 10)
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            kernel_ops=tuple(
                replace(overwrite, writes=first_writer.writes)
                if item.op_id == overwrite.op_id
                else item
                for item in program.semantics.kernel_ops
            ),
        ),
    )


def _append_token(program: Program, initial: bool) -> Program:
    variant = program.semantics.variants[0]
    membership = replace(variant.membership, tokens=IdSpan(1, 3))
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            tokens=(*program.semantics.tokens, ControlToken(3, initial=initial)),
            variants=(replace(variant, membership=membership),),
        ),
    )


def _missing_completion_wait(program: Program) -> Program:
    dependencies = list(program.semantics.dependencies)
    index = next(
        index
        for index, item in enumerate(dependencies)
        if item.kind is ScheduledDependencyKind.STREAM_ORDER
        and item.source_command_id == 1
        and item.target_command_id == 2
    )
    dependencies[index] = replace(
        dependencies[index],
        source_command_id=1,
        target_command_id=3,
        kind=ScheduledDependencyKind.LIFECYCLE,
        source=LifecycleSource(1),
    )
    waits = list(program.command_waits)
    waits[1] = CommandWait(1)
    return _refreshed(
        program,
        command_waits=tuple(waits),
        semantics=replace(program.semantics, dependencies=tuple(dependencies)),
    )


def _missing_lifecycle_completion(program: Program) -> Program:
    dependencies = list(program.semantics.dependencies)
    index = next(
        index
        for index, item in enumerate(dependencies)
        if item.kind is ScheduledDependencyKind.LIFECYCLE
        and item.source_command_id == 3
        and item.target_command_id == 4
    )
    dependencies[index] = replace(
        dependencies[index], source_command_id=1, target_command_id=4)
    dependencies = [
        item
        for item in dependencies
        if not (
            item.kind is ScheduledDependencyKind.DMA_COMPLETION
            and item.source_command_id == 3
            and item.target_command_id == 4
        )
    ]
    variant = program.semantics.variants[0]
    membership = replace(variant.membership, dependencies=IdSpan(1, len(dependencies)))
    waits = list(program.command_waits)
    waits[2] = CommandWait(1)
    return _refreshed(
        program,
        command_waits=tuple(waits),
        semantics=replace(
            program.semantics,
            dependencies=tuple(
                replace(item, dependency_id=index)
                for index, item in enumerate(dependencies, 1)
            ),
            variants=(replace(variant, membership=membership),),
        ),
    )


def _without_dependency(program: Program, predicate) -> Program:
    removed = False
    dependencies = []
    for item in program.semantics.dependencies:
        if not removed and predicate(item):
            removed = True
            continue
        dependencies.append(item)
    dependencies = tuple(
        replace(item, dependency_id=index)
        for index, item in enumerate(dependencies, 1)
    )
    variant = program.semantics.variants[0]
    membership = replace(
        variant.membership,
        dependencies=IdSpan(1, len(dependencies)),
    )
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            dependencies=dependencies,
            variants=(replace(variant, membership=membership),),
        ),
    )


def build_stage2_dependency_lifecycle_cases() -> tuple[Stage2DependencyLifecycleCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    sram = build_sram_reuse_program(arch)
    initial = _append_token(sram, True)
    initial_after = _with_op(initial, 7, after_tokens=(3,))
    missing_token = _append_token(sram, False)
    duplicate_dependency = _variant_with_count(sram, "dependencies", len(sram.semantics.dependencies) + 1)
    duplicate_dependency = _refreshed(
        duplicate_dependency,
        semantics=replace(
            duplicate_dependency.semantics,
            dependencies=(
                *duplicate_dependency.semantics.dependencies,
                replace(
                    duplicate_dependency.semantics.dependencies[0],
                    dependency_id=len(duplicate_dependency.semantics.dependencies) + 1,
                ),
            ),
        ),
    )
    dma_pin = build_dma_pin_program(arch)
    war = _war_program(arch)
    grouped_arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    grouped = _grouped_p2p_program(grouped_arch)
    grouped_dma_pin = _append_dma_pin(grouped)
    two_variant = build_two_variant_program(arch)
    two_variant_initial = _two_variant_initial_program(arch)
    fence = build_fence_scopes_program(arch)
    second_dependency = next(
        item
        for item in two_variant.semantics.dependencies
        if item.source_command_id == 7 and item.target_command_id == 8
    )
    cross_variant = _refreshed(
        two_variant,
        semantics=replace(
            two_variant.semantics,
            dependencies=tuple(
                replace(item, target_command_id=2)
                if item.dependency_id == second_dependency.dependency_id
                else item
                for item in two_variant.semantics.dependencies
            ),
        ),
    )
    lifecycle_other_variant = _with_dependency(
        two_variant,
        lambda item: item.kind is ScheduledDependencyKind.LIFECYCLE
        and item.source_command_id == 1,
        LifecycleSource(2),
    )
    return (
        Stage2DependencyLifecycleCase("dependency_facts_positive", arch, dma_pin, dma_pin, None),
        Stage2DependencyLifecycleCase("war_waw_positive", arch, war, war, None),
        Stage2DependencyLifecycleCase("grouped_state_definition_positive", grouped_arch, grouped, grouped, None),
        Stage2DependencyLifecycleCase("dma_pin_positive", grouped_arch, grouped_dma_pin, grouped_dma_pin, None),
        Stage2DependencyLifecycleCase("intrinsic_initial_token_positive", arch, sram, initial_after, None),
        Stage2DependencyLifecycleCase("two_variant_canonical_topology_positive", arch, two_variant, two_variant, None),
        Stage2DependencyLifecycleCase("two_variant_initial_token_canonical_topology_positive", arch, two_variant_initial, two_variant_initial, None),
        Stage2DependencyLifecycleCase("fence_scopes_shared_root_slots_positive", arch, fence, fence, None),
        Stage2DependencyLifecycleCase("intrinsic_cycle", arch, sram, _with_op(sram, 7, after_tokens=(2,)), "E_DEPENDENCY_CYCLE"),
        Stage2DependencyLifecycleCase("intrinsic_duplicate_token_producer", arch, sram, _with_op(sram, 8, done_token=1), "E_EVENT_MULTIPLE_PRODUCERS"),
        Stage2DependencyLifecycleCase("intrinsic_missing_token_producer", arch, sram, missing_token, "E_EVENT_NO_PRODUCER"),
        Stage2DependencyLifecycleCase("typed_dependency_duplicate", arch, sram, duplicate_dependency, "E_ABI_DUPLICATE"),
        Stage2DependencyLifecycleCase("kernel_state_source", arch, dma_pin, _with_dependency(dma_pin, lambda item: item.kind is ScheduledDependencyKind.KERNEL_STATE and item.dependency_id == 2, StateSource(999)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("kernel_state_wrong_existing_source", arch, war, _with_dependency(war, lambda item: item.kind is ScheduledDependencyKind.KERNEL_STATE and item.dependency_id == 2, StateSource(1)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("raw_source", arch, dma_pin, _with_dependency(dma_pin, lambda item: item.kind is ScheduledDependencyKind.RAW and item.dependency_id == 3, ObjectSource(999)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("raw_wrong_existing_source", arch, war, _with_dependency(war, lambda item: item.kind is ScheduledDependencyKind.RAW and item.dependency_id == 3, ObjectSource(2)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("war_source", arch, war, _with_dependency(war, lambda item: item.kind is ScheduledDependencyKind.WAR and item.source_command_id == 3, ObjectSource(999)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("war_wrong_existing_source", arch, war, _with_dependency(war, lambda item: item.kind is ScheduledDependencyKind.WAR and item.dependency_id == 10, ObjectSource(2)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("waw_source", arch, war, _with_dependency(war, lambda item: item.kind is ScheduledDependencyKind.WAW, ObjectSource(999)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("waw_wrong_existing_source", arch, war, _with_dependency(war, lambda item: item.kind is ScheduledDependencyKind.WAW, ObjectSource(2)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("dma_completion_source", arch, dma_pin, _with_dependency(dma_pin, lambda item: item.kind is ScheduledDependencyKind.DMA_COMPLETION and item.dependency_id == 15, DescriptorSource(999)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("dma_completion_wrong_existing_source", arch, war, _with_dependency(war, lambda item: item.kind is ScheduledDependencyKind.DMA_COMPLETION and item.dependency_id == 21, DescriptorSource(2)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("dma_pin_source", grouped_arch, grouped_dma_pin, _with_dependency(grouped_dma_pin, lambda item: item.kind is ScheduledDependencyKind.DMA_PIN, DescriptorSource(99)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("stream_order_source", arch, dma_pin, _with_dependency(dma_pin, lambda item: item.kind is ScheduledDependencyKind.STREAM_ORDER and item.dependency_id == 11, StreamOrderSource(999)), "E_STREAM_CONTRACT"),
        Stage2DependencyLifecycleCase("lifecycle_source", arch, dma_pin, _with_dependency(dma_pin, lambda item: item.kind is ScheduledDependencyKind.LIFECYCLE and item.dependency_id == 7, LifecycleSource(999)), "E_LIFECYCLE"),
        Stage2DependencyLifecycleCase("lifecycle_wrong_existing_variant", arch, two_variant, lifecycle_other_variant, "E_LIFECYCLE"),
        Stage2DependencyLifecycleCase("typed_dependency_wrong_source_union", arch, war, _with_dependency(war, lambda item: item.kind is ScheduledDependencyKind.KERNEL_STATE and item.dependency_id == 2, ObjectSource(1)), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("missing_kernel_control_dependency", arch, war, _without_dependency(war, lambda item: item.kind is ScheduledDependencyKind.KERNEL_CONTROL), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("missing_kernel_state_dependency", arch, war, _without_dependency(war, lambda item: item.kind is ScheduledDependencyKind.KERNEL_STATE), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("missing_dma_completion_dependency", arch, war, _without_dependency(war, lambda item: item.kind is ScheduledDependencyKind.DMA_COMPLETION), "E_ABI_BOUNDS"),
        Stage2DependencyLifecycleCase("completion_requires_event_wait", arch, sram, _missing_completion_wait(sram), "E_EVENT_NO_PRODUCER"),
        Stage2DependencyLifecycleCase("lifecycle_requires_completion", arch, sram, _missing_lifecycle_completion(sram), "E_LIFECYCLE"),
        Stage2DependencyLifecycleCase("grouped_state_cross_operation_producer", arch, war, _duplicate_state_producer(war), "E_ABI_DUPLICATE"),
        Stage2DependencyLifecycleCase("cross_variant_typed_dependency", arch, two_variant, cross_variant, "E_ABI_BOUNDS"),
    )

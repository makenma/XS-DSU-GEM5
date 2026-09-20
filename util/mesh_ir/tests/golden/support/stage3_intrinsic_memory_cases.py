from dataclasses import dataclass, replace

from mesh_ir.analysis.kernel_work import _kernel_op_work_phases_verified
from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import (
    build_dma_pin_program,
    build_dma_shapes_program,
    build_p2p_reuse_program,
)
from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.ir.common import Access, DmaKind
from mesh_ir.ir.kernel_ir import (
    AllocAttrs,
    ControlToken,
    DmaAttrs,
    ElementRegion,
    KernelMemoryRecords,
    KernelOp,
    KernelOpcode,
    LocalCopyAttrs,
    OperandAccess,
    OperandAccessMode,
    StateOrigin,
    StateTransition,
    TensorState,
    ViewDeclarationAttrs,
)
from mesh_ir.model import CommandOperand, Program
from mesh_ir.scheduled.projection import abi_attr_for_kernel_op
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin,
    ComputeExecution,
    ExternalSlotBacking,
    HaltAttrs,
    KernelTokenSource,
    LocalAllocationBacking,
    ProgramSemantics,
    RequestBeginAttrs,
    RequestEndAttrs,
    ScheduledDependency,
    ScheduledDependencyKind,
)
from mesh_ir.scheduled.assemble import _TransportSections
from mesh_ir.scheduled.verify import _local_memory_records, _verify_dependencies_and_lifecycle
from tests.golden.support.stage2_control_fixture import ROOT


@dataclass(frozen=True)
class Stage3IntrinsicMemoryCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None


def _refreshed(program: Program, **changes) -> Program:
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def verify_complete_lifecycle(program: Program) -> None:
    _verify_dependencies_and_lifecycle(
        program.semantics,
        _TransportSections(
            program.strings,
            program.entrypoints,
            program.profiles,
            program.tensors,
            program.shards,
            program.allocations,
            program.streams,
            program.commands,
            program.command_waits,
            program.command_operands,
            program.events,
            program.dma_descriptors,
            program.op_attrs,
            program.relocations,
        ),
    )


def _records(program: Program) -> KernelMemoryRecords:
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
        semantics.kernel_ops,
    )


def _with_projected_operands(
    program: Program, semantics: ProgramSemantics
) -> Program:
    kernel_ops = semantics.kernel_ops
    source_op_by_command = {
        item.command_id: item.source.kernel_op_id
        for item in semantics.command_semantics
        if hasattr(item.source, "kernel_op_id")
    }
    views = {item.view_id: item for item in semantics.views}
    shards = {item.shard_id: item for item in semantics.logical_shards}
    resident_by_view = {
        item.view_id: item.runtime_shard_id for item in semantics.resident_views
    }
    allocation_by_object = {
        item.object_id: item.backing.allocation_id
        for item in semantics.object_backings
        if type(item.backing) is LocalAllocationBacking
    }
    operands = []
    commands = []
    for command, semantic in zip(program.commands, semantics.command_semantics):
        operand_begin = len(operands)
        source_op_id = source_op_by_command.get(semantic.command_id)
        if source_op_id:
            op = kernel_ops[source_op_id - 1]
            for access, permission in (
                *((item, Access.READ_ONLY) for item in op.reads),
                *((item, Access.READ_WRITE) for item in op.writes),
            ):
                view = views[access.view_id]
                operands.append(
                    CommandOperand(
                        shards[view.shard_id].tensor_id,
                        resident_by_view[view.view_id],
                        allocation_by_object.get(view.object_id, 0),
                        int(permission),
                    )
                )
        commands.append(
            replace(
                command,
                operand_begin=operand_begin,
                operand_count=len(operands) - operand_begin,
            )
        )
    return _refreshed(
        program,
        commands=tuple(commands),
        command_operands=tuple(operands),
        semantics=semantics,
    )


def _with_projected_kernel_ops(
    program: Program, kernel_ops: tuple[KernelOp, ...]
) -> Program:
    semantics = replace(program.semantics, kernel_ops=kernel_ops)
    source_op_by_command = {
        item.command_id: item.source.kernel_op_id
        for item in semantics.command_semantics
        if hasattr(item.source, "kernel_op_id")
    }
    local_by_global_op = {}
    for variant in semantics.variants:
        records = _local_memory_records(semantics, variant)
        for op in records.ops:
            if op.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW):
                global_op_id = variant.membership.kernel_ops.first_id + op.op_id - 1
                local_by_global_op[global_op_id] = (op, records)
    updated_semantics = tuple(
        replace(
            item,
            execution=ComputeExecution(
                _kernel_op_work_phases_verified(
                    local_by_global_op[source_op_by_command[item.command_id]][1],
                    local_by_global_op[source_op_by_command[item.command_id]][0].op_id,
                )
            ),
        )
        if source_op_by_command.get(item.command_id) in local_by_global_op
        and local_by_global_op[source_op_by_command[item.command_id]][0].opcode
        not in (KernelOpcode.DMA, KernelOpcode.RECV_WAIT, KernelOpcode.BARRIER)
        else item
        for item in semantics.command_semantics
    )
    attrs = list(program.op_attrs)
    for command, semantic in zip(program.commands, updated_semantics):
        source_op_id = source_op_by_command.get(semantic.command_id)
        if command.attr_index and source_op_id in local_by_global_op:
            op, records = local_by_global_op[source_op_id]
            attr = abi_attr_for_kernel_op(op, records)
            if attr is not None:
                attrs[command.attr_index - 1] = attr
    return _with_projected_operands(
        replace(program, op_attrs=tuple(attrs)),
        replace(semantics, command_semantics=updated_semantics),
    )


def _with_canonical_dependencies(
    program: Program, dependencies: tuple[ScheduledDependency, ...]
) -> Program:
    canonical = tuple(
        replace(item, dependency_id=index)
        for index, item in enumerate(dependencies, start=1)
    )
    variants = tuple(
        replace(
            variant,
            membership=replace(
                variant.membership,
                dependencies=replace(
                    variant.membership.dependencies,
                    count=len(canonical),
                ),
            ),
        )
        for variant in program.semantics.variants
    )
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            variants=variants,
            dependencies=canonical,
        ),
    )


def _fragment_replacement_program(arch: ArchManifest) -> Program:
    seed = build_dma_shapes_program(arch)
    records = _records(seed)
    left = ElementRegion((0, 0), (2, 8), (1, 1))
    right = ElementRegion((0, 8), (2, 8), (1, 1))
    reduction = next(item for item in records.ops if item.op_id == 31)
    store = next(item for item in records.ops if item.op_id == 32)
    snapshot = replace(records.objects[9], object_id=12)
    snapshot_view = replace(records.views[9], view_id=12, object_id=12)
    declarations = (
        *tuple(item for item in records.ops if item.op_id <= 11),
        KernelOp(12, 0, "alloc:12", KernelOpcode.ALLOC, 1, 0, (), (), AllocAttrs(12), (), None),
        *tuple(
            replace(item, op_id=item.op_id + 1)
            for item in records.ops
            if 12 <= item.op_id <= 22
        ),
        KernelOp(24, 0, "view:12", KernelOpcode.VIEW, 1, 0, (), (), ViewDeclarationAttrs(12), (), None),
    )
    effects = tuple(
        replace(item, op_id=item.op_id + 2)
        for item in records.ops
        if 23 <= item.op_id <= 30
    )
    snapshot_copy = KernelOp(
        33,
        0,
        "shapes:partial-snapshot",
        KernelOpcode.LOCAL_COPY,
        1,
        0,
        (OperandAccess(18, 10, ElementRegion((0, 0), (2, 16), (1, 1)), OperandAccessMode.READ),),
        (StateTransition(21, 22, 12, ElementRegion((0, 0), (2, 16), (1, 1))),),
        LocalCopyAttrs(),
        (8,),
        9,
    )
    overwrite = KernelOp(
        34,
        0,
        "shapes:partial-overwrite",
        KernelOpcode.LOCAL_COPY,
        1,
        0,
        (OperandAccess(13, 8, left, OperandAccessMode.READ),),
        (StateTransition(18, 23, 10, left),),
        LocalCopyAttrs(),
        (6, 8, 9),
        10,
    )
    replaced_reduction = replace(
        reduction,
        op_id=35,
        reads=(
            OperandAccess(23, 10, left, OperandAccessMode.READ),
            OperandAccess(22, 12, left, OperandAccessMode.READ),
            OperandAccess(23, 10, right, OperandAccessMode.READ),
            OperandAccess(13, 8, right, OperandAccessMode.READ),
        ),
        writes=(
            StateTransition(13, 14, 8, left),
            StateTransition(13, 14, 8, right),
        ),
        after_tokens=(6, 9, 10),
        done_token=11,
    )
    replaced_store = replace(store, op_id=36, after_tokens=(11,), done_token=12)
    records = replace(
        records,
        objects=(*records.objects, snapshot),
        views=(*records.views, snapshot_view),
        states=(
            *records.states,
            TensorState(21, 12, 0, StateOrigin.EMPTY),
            TensorState(22, 12, 1, StateOrigin.PRODUCED, 1),
            TensorState(23, 10, 2, StateOrigin.PRODUCED, 1),
        ),
        tokens=(*records.tokens, ControlToken(11), ControlToken(12)),
        ops=declarations + effects + (snapshot_copy, overwrite, replaced_reduction, replaced_store),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage3-partial-fragments", 1))
    variant = builder.variant(
        "main",
        "stage3-partial-fragments",
        "stage3-partial-fragments",
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
        external_backings=tuple(
            item
            for item in seed.semantics.object_backings
            if type(item.backing) is ExternalSlotBacking
        ),
        binding_slots=seed.semantics.binding_slots,
    )
    stream0 = variant.stream(
        0,
        0,
        A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    stream1 = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream0.control_command(RequestBeginAttrs())
    for op_id in (30, 26, 28, 29):
        stream0.kernel_command(op_id)
    for op_id in (25, 27, 31, 32, 33, 34, 35, 36):
        stream1.kernel_command(op_id)
    stream0.control_command(RequestEndAttrs())
    stream0.control_command(HaltAttrs())
    stream1.control_command(HaltAttrs())
    return builder.build()


def _repeated_partial_contributor(program: Program) -> Program:
    projected = _with_projected_kernel_ops(
        program,
        tuple(
            replace(
                item,
                reads=(
                    OperandAccess(13, 8, item.reads[0].region, OperandAccessMode.READ),
                    item.reads[1],
                ),
            )
            if item.op_id == 31
            else item
            for item in program.semantics.kernel_ops
        ),
    )
    return _with_canonical_dependencies(
        projected,
        tuple(
            item
            for item in projected.semantics.dependencies
            if item.dependency_id not in (24, 25)
        ),
    )


def _duplicate_partial_contributor(program: Program) -> Program:
    reduce = next(item for item in program.semantics.kernel_ops if item.op_id == 31)
    duplicate = OperandAccess(
        13,
        8,
        reduce.reads[1].region,
        OperandAccessMode.READ,
    )
    projected = _with_projected_kernel_ops(
        program,
        tuple(
                replace(
                    item,
                    reads=(*item.reads, duplicate),
                    attrs=replace(
                        item.attrs,
                        cost=replace(
                            item.attrs.cost,
                            logical_input_bytes=384,
                            local_storage_bytes=512,
                            reduction_ops=64,
                        ),
                    ),
                )
                if item.op_id == reduce.op_id
                else item
                for item in program.semantics.kernel_ops
        ),
    )
    return projected


def _same_object_hazard_program(arch: ArchManifest, synchronized: bool) -> Program:
    seed = build_dma_pin_program(arch)
    records = _records(seed)
    region = next(item for item in records.ops if item.op_id == 8).reads[0].region
    overwrite = KernelOp(
        10,
        0,
        "pin:z-hazard:overwrite",
        KernelOpcode.DMA,
        0,
        0,
        (),
        (StateTransition(2, 7, 1, region),),
        DmaAttrs(DmaKind.LOCAL_FILL, 0, 0, 0, 0, b"\x00"),
        (2, 3),
        4,
    )
    records = replace(
        records,
        states=(*records.states, TensorState(7, 1, 2, StateOrigin.PRODUCED)),
        tokens=(*records.tokens, ControlToken(4)),
        ops=(*records.ops, overwrite),
    )
    name = "stage3-hazard-synchronized" if synchronized else "stage3-hazard-unordered"
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", name, 1))
    variant = builder.variant(
        "main",
        name,
        name,
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
        external_backings=tuple(
            item
            for item in seed.semantics.object_backings
            if type(item.backing) is ExternalSlotBacking
        ),
        binding_slots=seed.semantics.binding_slots,
    )
    primary = variant.stream(
        0,
        0,
        A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    secondary = variant.stream(0, 1)
    primary.control_command(seed.semantics.command_semantics[0].source.attrs)
    primary.kernel_command(7)
    primary.kernel_command(8)
    primary.kernel_command(10)
    primary.control_command(seed.semantics.command_semantics[3].source.attrs)
    primary.control_command(seed.semantics.command_semantics[4].source.attrs)
    secondary.kernel_command(9)
    program = builder.build()
    if synchronized:
        return program
    unordered = _refreshed(
        program,
        semantics=replace(
            program.semantics,
            kernel_ops=tuple(
                replace(item, after_tokens=(2,)) if item.op_id == 10 else item
                for item in program.semantics.kernel_ops
            ),
        ),
    )
    return _with_canonical_dependencies(
        unordered,
        tuple(
            item
            for item in unordered.semantics.dependencies
            if not (
                item.source_command_id == 7
                and item.target_command_id == 4
                and item.kind is ScheduledDependencyKind.KERNEL_CONTROL
            )
        ),
    )


def _p2p_visibility_without_send_completion(program: Program) -> Program:
    receive = next(
        item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.RECV_WAIT
    )
    dependencies = tuple(
        replace(
            item,
            source_command_id=2,
            source=KernelTokenSource(1),
        )
        if item.source_command_id == 3
        and item.target_command_id == 7
        and item.kind.name == "KERNEL_CONTROL"
        else item
        for item in program.semantics.dependencies
    )
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            kernel_ops=tuple(
                replace(item, after_tokens=(1,))
                if item.op_id == receive.op_id
                else item
                for item in program.semantics.kernel_ops
            ),
            dependencies=dependencies,
        ),
    )


def build_stage3_intrinsic_memory_cases() -> tuple[Stage3IntrinsicMemoryCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    fragments = _fragment_replacement_program(arch)
    partial_sum = build_dma_shapes_program(arch)
    repeated = _repeated_partial_contributor(partial_sum)
    duplicate = _duplicate_partial_contributor(partial_sum)
    synchronized = _same_object_hazard_program(arch, True)
    unordered = _same_object_hazard_program(arch, False)
    p2p = build_p2p_reuse_program(arch)
    p2p_visibility = _p2p_visibility_without_send_completion(p2p)
    return (
        Stage3IntrinsicMemoryCase(
            "partial_sum_fragment_replacement_positive",
            arch,
            fragments,
            fragments,
            None,
        ),
        Stage3IntrinsicMemoryCase(
            "partial_sum_repeated_contributor",
            arch,
            partial_sum,
            repeated,
            "E_TENSOR_NOT_RESIDENT",
        ),
        Stage3IntrinsicMemoryCase(
            "partial_sum_duplicate_contributor",
            arch,
            partial_sum,
            duplicate,
            "E_TENSOR_NOT_RESIDENT",
        ),
        Stage3IntrinsicMemoryCase(
            "same_object_completion_synchronized_positive",
            arch,
            synchronized,
            synchronized,
            None,
        ),
        Stage3IntrinsicMemoryCase(
            "same_object_stream_order_is_not_completion",
            arch,
            synchronized,
            unordered,
            "E_TENSOR_NOT_RESIDENT",
        ),
        Stage3IntrinsicMemoryCase("p2p_visibility_positive", arch, p2p, p2p, None),
        Stage3IntrinsicMemoryCase(
            "p2p_source_completion_missing",
            arch,
            p2p,
            p2p_visibility,
            "E_P2P_UNMATCHED",
        ),
    )

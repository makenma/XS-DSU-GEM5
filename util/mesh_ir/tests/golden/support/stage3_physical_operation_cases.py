from dataclasses import dataclass, replace

from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import (
    build_compute_timing_program,
    build_dual_core_program,
    build_p2p_reuse_program,
)
from mesh_ir.ir.common import DType, Layout
from mesh_ir.ir.graph_ir import MatmulAttrs, OpCode
from mesh_ir.ir.kernel_ir import (
    BarrierAttrs,
    BlockedMnkLayout,
    ControlToken,
    GemmKernelAttrs,
    KernelMemoryRecords,
    KernelCost,
    KernelOp,
    KernelOpcode,
    KernelTile,
    MatrixPhase,
    ViewDeclarationAttrs,
)
from mesh_ir.model import Program
from mesh_ir.passes.addresses import _access_offset
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic
from mesh_ir.scheduled.model import (
    ObjectSource,
    ReadAccessUse,
    ScheduledDependency,
    ScheduledDependencyKind,
    StateSource,
)
from mesh_ir.scheduled.projection import abi_attr_for_kernel_op
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin,
    HaltAttrs,
    RequestBeginAttrs,
    RequestEndAttrs,
)
from mesh_ir.scheduled.verify import _local_memory_records
from mesh_ir.traffic import calculate_traffic
from tests.golden.support.compiler_fixtures import (
    compile_matrix_program,
    compile_unary_chain_program,
)
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage3_intrinsic_memory_cases import (
    _with_canonical_dependencies,
    _with_projected_kernel_ops,
)
from tests.golden.support.stage3_intrinsic_cases import build_complete_program
from tests.unit.test_gate2_kernel_ir import semantic_operation_kernel


@dataclass(frozen=True)
class Stage3PhysicalOperationCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None
    expected_message: str = ""


def _refreshed(program: Program, **changes) -> Program:
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(
        provisional,
        semantic_sha256=semantic_sha256(provisional.semantic_dict()),
    )


def _with_unworked_kernel_op(program: Program, operation) -> Program:
    semantics = replace(
        program.semantics,
        kernel_ops=tuple(
            operation if item.op_id == operation.op_id else item
            for item in program.semantics.kernel_ops
        ),
    )
    attrs_table = list(program.op_attrs)
    for command, semantic in zip(program.commands, semantics.command_semantics):
        if getattr(semantic.source, "kernel_op_id", None) != operation.op_id:
            continue
        attrs_table[command.attr_index - 1] = abi_attr_for_kernel_op(
            operation,
            _local_memory_records(
                semantics,
                next(
                    variant
                    for variant in semantics.variants
                    if variant.membership.kernel_ops.first_id
                    <= operation.op_id
                    < variant.membership.kernel_ops.first_id
                    + variant.membership.kernel_ops.count
                ),
            ),
        )
        break
    else:
        raise AssertionError(operation.op_id)
    return _refreshed(
        program,
        semantics=semantics,
        op_attrs=tuple(attrs_table),
    )


def _direct_tail(program: Program) -> Program:
    op = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.GEMM and item.attrs.phase is MatrixPhase.DIRECT
    )
    reads = (
        replace(op.reads[0], region=replace(op.reads[0].region, shape=(1, 2, 1))),
        replace(op.reads[1], region=replace(op.reads[1].region, shape=(1, 1, 2))),
    )
    attrs = replace(
        op.attrs,
        tile=replace(op.attrs.tile, k_extent=1, valid_k=1),
        cost=KernelCost(8, 8, 16, 4, 0, 0),
    )
    return _with_projected_kernel_ops(
        program,
        tuple(
            replace(item, reads=reads, attrs=attrs) if item.op_id == op.op_id else item
            for item in program.semantics.kernel_ops
        ),
    )


def _matrix_tile(program: Program) -> Program:
    op = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.GEMM and item.attrs.phase is MatrixPhase.DIRECT
    )
    attrs = replace(
        op.attrs,
        tile=replace(op.attrs.tile, m_extent=1, valid_m=1),
        cost=KernelCost(16, 8, 24, 4, 0, 0),
    )
    return _with_projected_kernel_ops(
        program,
        tuple(
            replace(item, attrs=attrs) if item.op_id == op.op_id else item
            for item in program.semantics.kernel_ops
        ),
    )


def _matrix_accumulator_gap(program: Program) -> Program:
    op = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.GEMM
        and item.attrs.phase is MatrixPhase.ACCUMULATE_FIRST
    )
    reads = tuple(
        replace(item, region=replace(item.region, shape=(2, 1)))
        for item in op.reads
    )
    attrs = replace(
        op.attrs,
        tile=replace(op.attrs.tile, k_extent=1, valid_k=1),
        cost=KernelCost(8, 16, 24, 4, 0, 0),
    )
    return _with_projected_kernel_ops(
        program,
        tuple(
            replace(item, reads=reads, attrs=attrs) if item.op_id == op.op_id else item
            for item in program.semantics.kernel_ops
        ),
    )


def _matrix_epilogue_tile(program: Program) -> Program:
    op = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.MATRIX_EPILOGUE
    )
    attrs = replace(
        op.attrs,
        tile=replace(op.attrs.tile, m_extent=1, valid_m=1),
        cost=KernelCost(16, 8, 24, 0, 4, 0),
    )
    return _with_projected_kernel_ops(
        program,
        tuple(
            replace(item, attrs=attrs) if item.op_id == op.op_id else item
            for item in program.semantics.kernel_ops
        ),
    )


def _matrix_duplicate_epilogue(program: Program) -> Program:
    epilogues = tuple(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.MATRIX_EPILOGUE
    )
    existing, duplicate = epilogues
    return _with_projected_kernel_ops(
        program,
        tuple(
            replace(
                item,
                writes=(
                    replace(
                        duplicate.writes[0],
                        view_id=existing.writes[0].view_id,
                        region=existing.writes[0].region,
                    ),
                ),
            )
            if item.op_id == duplicate.op_id
            else item
            for item in program.semantics.kernel_ops
        ),
    )


def _matrix_accumulator_overlap(program: Program, arch: ArchManifest) -> Program:
    final = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.GEMM
        and item.attrs.phase is MatrixPhase.ACCUMULATE_FINAL
        and item.attrs.tile.n_origin == 0
    )
    input_view_ids = tuple(item.view_id for item in final.reads[:2])
    shifted = _refreshed(
        program,
        semantics=replace(
            program.semantics,
            views=tuple(
                replace(view, shard_origin=(*view.shard_origin[:-1], 1))
                if view.view_id in input_view_ids
                else view
                for view in program.semantics.views
            ),
        ),
    )
    final = next(item for item in shifted.semantics.kernel_ops if item.op_id == final.op_id)
    source_ops = {
        item.op_id
        for item in shifted.semantics.kernel_ops
        if item.opcode is KernelOpcode.DMA
        and item.writes
        and item.writes[0].view_id in input_view_ids
    }
    projected = _with_projected_kernel_ops(
        shifted,
        tuple(
            replace(
                item,
                attrs=replace(
                    item.attrs,
                    tile=replace(item.attrs.tile, k_origin=1),
                ),
            )
            if item.op_id == final.op_id
            else replace(
                item,
                reads=(
                    replace(
                        item.reads[0],
                        region=replace(
                            item.reads[0].region,
                            origin=(
                                *item.reads[0].region.origin[:-1],
                                item.reads[0].region.origin[-1] - 1,
                            ),
                        ),
                    ),
                ),
            )
            if item.op_id in source_ops
            else item
            for item in shifted.semantics.kernel_ops
        ),
    )
    semantics = projected.semantics
    records = _local_memory_records(semantics, semantics.variants[0])
    endpoint_uses = tuple(
        replace(
            endpoint_use,
            use=replace(
                endpoint_use.use,
                region=next(
                    operation
                    for operation in records.ops
                    if operation.op_id == endpoint_use.use.kernel_op_id
                ).reads[endpoint_use.use.access_index].region,
            ),
        )
        if type(endpoint_use.use) is ReadAccessUse
        and endpoint_use.use.kernel_op_id in source_ops
        else endpoint_use
        for endpoint_use in semantics.endpoint_uses
    )
    use_by_descriptor = {
        item.descriptor_id: item.use
        for item in endpoint_uses
        if type(item.use) is ReadAccessUse and item.use.kernel_op_id in source_ops
    }
    descriptors = tuple(
        replace(
            descriptor,
            src=replace(
                descriptor.src,
                offset_bytes=_access_offset(
                    records,
                    next(
                        operation
                        for operation in records.ops
                        if operation.op_id == use_by_descriptor[descriptor.descriptor_id].kernel_op_id
                    ).reads[use_by_descriptor[descriptor.descriptor_id].access_index],
                    use_by_descriptor[descriptor.descriptor_id].region,
                ),
            ),
        )
        if descriptor.descriptor_id in use_by_descriptor
        else descriptor
        for descriptor in projected.dma_descriptors
    )
    provisional = replace(
        projected,
        dma_descriptors=descriptors,
        expected_traffic=(),
        semantic_sha256="",
        semantics=replace(semantics, endpoint_uses=endpoint_uses),
    )
    executions, identity = derive_descriptor_execution_set(provisional, arch)
    traffic = calculate_traffic(arch, identity, executions)
    return _refreshed(
        provisional,
        expected_traffic=_expected_traffic(traffic, executions, arch),
        semantics=replace(
            provisional.semantics,
            reference_binding_identity_sha256=identity,
            intrinsic_traffic=traffic,
        ),
    )


def _byte_cost(program: Program, field: str) -> Program:
    op = next(
        item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.VECTOR
    )
    cost = replace(op.attrs.cost, **{field: getattr(op.attrs.cost, field) + 1})
    return _with_unworked_kernel_op(
        program,
        replace(op, attrs=replace(op.attrs, cost=cost)),
    )


def _unary_region(program: Program, opcode: KernelOpcode) -> Program:
    op = next(item for item in program.semantics.kernel_ops if item.opcode is opcode)
    return _with_unworked_kernel_op(
        program,
        replace(
            op,
            reads=(
                replace(op.reads[0], region=replace(op.reads[0].region, shape=(1, 4))),
            ),
        ),
    )


def _vector_arity(program: Program) -> Program:
    operation = next(
        item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.VECTOR
    )
    return _with_projected_kernel_ops(
        program,
        tuple(
            replace(item, reads=(*operation.reads, operation.reads[0]))
            if item.op_id == operation.op_id
            else item
            for item in program.semantics.kernel_ops
        ),
    )


def _matrix_arity(program: Program) -> Program:
    operation = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.GEMM and item.attrs.phase is MatrixPhase.DIRECT
    )
    return _with_projected_kernel_ops(
        program,
        tuple(
            replace(item, reads=(*operation.reads, operation.reads[1]))
            if item.op_id == operation.op_id
            else item
            for item in program.semantics.kernel_ops
        ),
    )


def _matrix_epilogue_arity(program: Program) -> Program:
    operation = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.MATRIX_EPILOGUE
    )
    return _with_projected_kernel_ops(
        program,
        tuple(
            replace(item, reads=(*operation.reads, operation.reads[0]))
            if item.op_id == operation.op_id
            else item
            for item in program.semantics.kernel_ops
        ),
    )


def _synchronization_data_operand(program: Program, opcode: KernelOpcode) -> Program:
    target = next(
        item for item in program.semantics.kernel_ops if item.opcode is opcode
    )
    source = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.DMA and item.reads
    )
    projected = _with_projected_kernel_ops(
        program,
        tuple(
            replace(item, reads=(source.reads[0],)) if item.op_id == target.op_id else item
            for item in program.semantics.kernel_ops
        ),
    )
    producer_op_id = next(
        operation.op_id
        for operation in projected.semantics.kernel_ops
        if any(
            transition.new_state_id == source.reads[0].state_id
            for transition in operation.writes
        )
    )
    source_command = next(
        item.command_id
        for item in projected.semantics.command_semantics
        if getattr(item.source, "kernel_op_id", None) == producer_op_id
    )
    target_command = next(
        item.command_id
        for item in projected.semantics.command_semantics
        if getattr(item.source, "kernel_op_id", None) == target.op_id
    )
    source_object_id = next(
        view.object_id
        for view in projected.semantics.views
        if view.view_id == source.reads[0].view_id
    )
    return _with_canonical_dependencies(
        projected,
        (
            *projected.semantics.dependencies,
            ScheduledDependency(
                0,
                source_command,
                target_command,
                ScheduledDependencyKind.KERNEL_STATE,
                StateSource(source.reads[0].state_id),
            ),
            ScheduledDependency(
                0,
                source_command,
                target_command,
                ScheduledDependencyKind.RAW,
                ObjectSource(source_object_id),
            ),
        ),
    )


def _recv_wait_data_operand(program: Program) -> Program:
    return _synchronization_data_operand(program, KernelOpcode.RECV_WAIT)


def _barrier_data_operand(program: Program) -> Program:
    return _synchronization_data_operand(program, KernelOpcode.BARRIER)


def build_p2p_barrier_program(arch: ArchManifest) -> Program:
    seed = build_p2p_reuse_program(arch)
    variant = seed.semantics.variants[0]
    records = _local_memory_records(seed.semantics, variant)
    first_barrier_id = records.ops[-1].op_id + 1
    barriers = (
        KernelOp(
            first_barrier_id,
            0,
            "stage3:p2p-barrier:0",
            KernelOpcode.BARRIER,
            0,
            0,
            (),
            (),
            BarrierAttrs((0, 1)),
            (records.tokens[0].token_id,),
            records.tokens[-1].token_id + 1,
        ),
        KernelOp(
            first_barrier_id + 1,
            0,
            "stage3:p2p-barrier:1",
            KernelOpcode.BARRIER,
            1,
            0,
            (),
            (),
            BarrierAttrs((0, 1)),
            (records.tokens[0].token_id,),
            records.tokens[-1].token_id + 2,
        ),
    )
    records = KernelMemoryRecords(
        records.tensors,
        records.computations,
        records.placements,
        records.shards,
        records.partial_sums,
        records.objects,
        records.views,
        records.states,
        (*records.tokens, ControlToken(barriers[0].done_token), ControlToken(barriers[1].done_token)),
        (*records.ops, *barriers),
    )
    builder = ProgramBuilder(
        arch,
        AuthoredProgramOrigin("unit", "stage3-p2p-barrier", 1),
    )
    scope = builder.variant(
        "main",
        "stage3-p2p-barrier",
        "stage3-p2p-barrier",
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
    )
    first = scope.stream(
        0,
        0,
        A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    second = scope.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    first.control_command(RequestBeginAttrs())
    first.kernel_command(5)
    first.kernel_command(6)
    second.kernel_command(7)
    first.kernel_command(8)
    second.kernel_command(9)
    first.kernel_command(first_barrier_id)
    second.kernel_command(first_barrier_id + 1)
    first.control_command(RequestEndAttrs())
    first.control_command(HaltAttrs())
    second.control_command(HaltAttrs())
    return builder.build()


def _nonlocal_compute_sram(program: Program) -> Program:
    operation = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.GEMM and item.owner_core == 0
    )
    projected = _with_projected_kernel_ops(
        program,
        tuple(
            replace(
                item,
                reads=(replace(operation.reads[0], state_id=1, view_id=1), *operation.reads[1:]),
            )
            if item.op_id == operation.op_id
            else item
            for item in program.semantics.kernel_ops
        ),
    )
    return _with_canonical_dependencies(
        projected,
        tuple(
            item
            for item in projected.semantics.dependencies
            if not (
                (
                    item.kind is ScheduledDependencyKind.KERNEL_STATE
                    and type(item.source) is StateSource
                    and item.source.state_id == operation.reads[0].state_id
                )
                or (
                    item.kind is ScheduledDependencyKind.RAW
                    and type(item.source) is ObjectSource
                    and item.source.object_id
                    == next(
                        view.object_id
                        for view in program.semantics.views
                        if view.view_id == operation.reads[0].view_id
                    )
                )
            )
        ),
    )


def _local_reduce_command(program: Program):
    reduction = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.LOCAL_REDUCE
    )
    semantic = next(
        item
        for item in program.semantics.command_semantics
        if getattr(item.source, "kernel_op_id", None) == reduction.op_id
    )
    wire = next(
        item
        for item, command_semantic in zip(
            program.commands, program.semantics.command_semantics
        )
        if command_semantic.command_id == semantic.command_id
    )
    return reduction, semantic, wire


def _without_local_reduce_input_dependencies(
    program: Program,
    reduction: KernelOp,
    target_command_id: int,
):
    old_object_id = next(
        view.object_id
        for view in program.semantics.views
        if view.view_id == reduction.reads[0].view_id
    )
    return tuple(
        item
        for item in program.semantics.dependencies
        if not (
            item.target_command_id == target_command_id
            and (
                item.kind is ScheduledDependencyKind.KERNEL_STATE
                and type(item.source) is StateSource
                and item.source.state_id == reduction.reads[0].state_id
                or item.kind is ScheduledDependencyKind.RAW
                and type(item.source) is ObjectSource
                and item.source.object_id == old_object_id
            )
        )
    )


def _local_reduce_nonpartial_input(program: Program) -> Program:
    reduction, target_semantic, target_wire = _local_reduce_command(program)
    source_operation = next(
        item
        for item in program.semantics.kernel_ops
        if item.opcode is KernelOpcode.GEMM and item.owner_core == reduction.owner_core
    )
    source = source_operation.reads[0]
    semantics = replace(
        program.semantics,
        kernel_ops=tuple(
            replace(item, reads=(source, *item.reads[1:]))
            if item.op_id == reduction.op_id
            else item
            for item in program.semantics.kernel_ops
        ),
    )
    source_semantic = next(
        item
        for item in semantics.command_semantics
        if getattr(item.source, "kernel_op_id", None) == source_operation.op_id
    )
    source_wire = next(
        item
        for item, command_semantic in zip(program.commands, program.semantics.command_semantics)
        if command_semantic.command_id == source_semantic.command_id
    )
    producer_op_id = next(
        item.op_id
        for item in semantics.kernel_ops
        if any(transition.new_state_id == source.state_id for transition in item.writes)
    )
    producer_semantic = next(
        item
        for item in semantics.command_semantics
        if getattr(item.source, "kernel_op_id", None) == producer_op_id
    )
    source_object_id = next(
        view.object_id
        for view in semantics.views
        if view.view_id == source.view_id
    )
    attrs = list(program.op_attrs)
    attrs[target_wire.attr_index - 1] = replace(
        attrs[target_wire.attr_index - 1],
        payload=(
            attrs[target_wire.attr_index - 1].payload[0],
            int(DType.FP16),
            int(DType.FP16),
            *attrs[target_wire.attr_index - 1].payload[3:],
        ),
    )
    operands = list(program.command_operands)
    operands[target_wire.operand_begin] = operands[source_wire.operand_begin]
    projected = _refreshed(
        program,
        op_attrs=tuple(attrs),
        command_operands=tuple(operands),
        semantics=semantics,
    )
    return _with_canonical_dependencies(
        projected,
        (
            *_without_local_reduce_input_dependencies(
                program, reduction, target_semantic.command_id
            ),
            ScheduledDependency(
                0,
                producer_semantic.command_id,
                target_semantic.command_id,
                ScheduledDependencyKind.KERNEL_STATE,
                StateSource(source.state_id),
            ),
            ScheduledDependency(
                0,
                producer_semantic.command_id,
                target_semantic.command_id,
                ScheduledDependencyKind.RAW,
                ObjectSource(source_object_id),
            ),
        ),
    )


def _local_reduce_missing_contributor(program: Program) -> Program:
    reduction, target_semantic, target_wire = _local_reduce_command(program)
    semantics = replace(
        program.semantics,
        kernel_ops=tuple(
            replace(item, reads=item.reads[1:])
            if item.op_id == reduction.op_id
            else item
            for item in program.semantics.kernel_ops
        ),
    )
    attrs = list(program.op_attrs)
    attrs[target_wire.attr_index - 1] = replace(
        attrs[target_wire.attr_index - 1],
        payload=(*attrs[target_wire.attr_index - 1].payload[:-1], 1),
    )
    operands = (
        *program.command_operands[:target_wire.operand_begin],
        *program.command_operands[target_wire.operand_begin + 1 :],
    )
    commands = tuple(
        replace(item, operand_count=item.operand_count - 1)
        if item.command_id == target_wire.command_id
        else replace(item, operand_begin=item.operand_begin - 1)
        if item.operand_begin > target_wire.operand_begin
        else item
        for item in program.commands
    )
    return _with_canonical_dependencies(
        _refreshed(
            program,
            commands=commands,
            command_operands=operands,
            op_attrs=tuple(attrs),
            semantics=semantics,
        ),
        _without_local_reduce_input_dependencies(
            program, reduction, target_semantic.command_id
        ),
    )


def _missing_consumer_layout(program: Program) -> Program:
    vector = next(
        item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.VECTOR
    )
    view_id = vector.reads[0].view_id
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            views=tuple(
                replace(view, layout=None) if view.view_id == view_id else view
                for view in program.semantics.views
            ),
        ),
    )


def _bmm_batch_region(program: Program) -> Program:
    op = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.BMM)
    return _with_unworked_kernel_op(
        program,
        replace(
            op,
            reads=(
                replace(
                    op.reads[0],
                    region=replace(
                        op.reads[0].region,
                        shape=(1, *op.reads[0].region.shape[1:]),
                    ),
                ),
                *op.reads[1:],
            ),
        ),
    )


def build_nonunit_bmm_program(arch: ArchManifest) -> Program:
    return build_complete_program(
        arch,
        semantic_operation_kernel(
            KernelOpcode.BMM,
            GemmKernelAttrs(
                OpCode.BMM,
                MatmulAttrs(batch_axes=(0,)),
                KernelTile(0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2),
                KernelCost(64, 32, 96, 16, 0, 0),
                MatrixPhase.DIRECT,
                0,
            ),
            (((2, 2, 2), DType.FP32), ((2, 2, 2), DType.FP32)),
            ((2, 2, 2), DType.FP32),
        ).memory_records(),
        "stage3-nonunit-bmm",
    )


def build_blocked_vector_program(arch: ArchManifest) -> Program:
    baseline = build_compute_timing_program(arch)
    variant = baseline.semantics.variants[0]
    records = _local_memory_records(baseline.semantics, variant)
    read_view = replace(
        records.views[7],
        view_id=len(records.views) + 1,
        object_strides=(4, 0, 1),
        layout=Layout.BLOCKED_MNK,
        blocked_layout=BlockedMnkLayout(1, 1, 1, (0, 1, 2)),
    )
    declaration_count = len(records.objects) + len(records.views)
    declarations = records.ops[:declaration_count]
    effects = []
    for operation in records.ops[declaration_count:]:
        operation = replace(operation, op_id=operation.op_id + 1)
        if operation.opcode is KernelOpcode.VECTOR:
            operation = replace(
                operation,
                reads=(replace(operation.reads[0], view_id=read_view.view_id),),
                attrs=replace(
                    operation.attrs,
                    cost=replace(
                        operation.attrs.cost,
                        logical_input_bytes=4,
                        local_storage_bytes=12,
                    ),
                ),
            )
        effects.append(operation)
    view_declaration = KernelOp(
        declaration_count + 1,
        0,
        f"view:{read_view.view_id}",
        KernelOpcode.VIEW,
        records.objects[read_view.object_id - 1].owner_core,
        0,
        (),
        (),
        ViewDeclarationAttrs(read_view.view_id),
        (),
        None,
    )
    records = replace(
        records,
        views=(*records.views, read_view),
        ops=(*declarations, view_declaration, *effects),
    )
    builder = ProgramBuilder(
        arch,
        AuthoredProgramOrigin("unit", "stage3-blocked-vector", 1),
    )
    scope = builder.variant(
        "main",
        "stage3-blocked-vector",
        "stage3-blocked-vector",
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
    )
    first = scope.stream(
        0,
        0,
        A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    second = scope.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    first.control_command(RequestBeginAttrs())
    for operation_id in range(declaration_count + 2, declaration_count + 6):
        first.kernel_command(operation_id)
    for operation_id in range(declaration_count + 6, len(records.ops) + 1):
        second.kernel_command(operation_id)
    first.control_command(RequestEndAttrs())
    first.control_command(HaltAttrs())
    second.control_command(HaltAttrs())
    return builder.build()


def _logical_count_cost(program: Program) -> Program:
    op = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.VECTOR)
    return _with_unworked_kernel_op(
        program,
        replace(
            op,
            attrs=replace(
                op.attrs,
                cost=replace(op.attrs.cost, logical_input_bytes=8, local_storage_bytes=16),
            ),
        ),
    )


def build_stage3_physical_operation_cases() -> tuple[Stage3PhysicalOperationCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    intrinsic_arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    timing = build_compute_timing_program(arch)
    matrix = compile_matrix_program(arch)
    unary = compile_unary_chain_program(arch)
    blocked = build_blocked_vector_program(arch)
    bmm = build_nonunit_bmm_program(intrinsic_arch)
    p2p = build_p2p_reuse_program(arch)
    p2p_barrier = build_p2p_barrier_program(arch)
    dual = build_dual_core_program(arch)
    return (
        Stage3PhysicalOperationCase(
            "physical_direct_matrix_k_tail",
            arch,
            timing,
            _direct_tail(timing),
            "E_EXPORT_LAYOUT",
            "direct matrix kernel does not cover its contraction domain",
        ),
        Stage3PhysicalOperationCase(
            "physical_matrix_tile_tuple",
            arch,
            timing,
            _matrix_tile(timing),
            "E_EXPORT_LAYOUT",
            "matrix tile does not match operand regions",
        ),
        Stage3PhysicalOperationCase(
            "physical_kernel_logical_input_byte_cost",
            arch,
            timing,
            _byte_cost(timing, "logical_input_bytes"),
            "E_EXPORT_LAYOUT",
            "kernel byte cost is inconsistent",
        ),
        Stage3PhysicalOperationCase(
            "physical_kernel_logical_output_byte_cost",
            arch,
            timing,
            _byte_cost(timing, "logical_output_bytes"),
            "E_EXPORT_LAYOUT",
            "kernel byte cost is inconsistent",
        ),
        Stage3PhysicalOperationCase(
            "physical_kernel_local_storage_byte_cost",
            arch,
            timing,
            _byte_cost(timing, "local_storage_bytes"),
            "E_EXPORT_LAYOUT",
            "kernel byte cost is inconsistent",
        ),
        Stage3PhysicalOperationCase(
            "physical_matrix_accumulator_k_gap",
            arch,
            matrix,
            _matrix_accumulator_gap(matrix),
            "E_EXPORT_LAYOUT",
            "matrix accumulator K regions are not an exact cover",
        ),
        Stage3PhysicalOperationCase(
            "physical_matrix_epilogue_tile_tuple",
            arch,
            matrix,
            _matrix_epilogue_tile(matrix),
            "E_EXPORT_LAYOUT",
            "matrix epilogue tile differs from its result region",
        ),
        Stage3PhysicalOperationCase(
            "physical_matrix_result_duplicate_epilogue",
            arch,
            matrix,
            _matrix_duplicate_epilogue(matrix),
            "E_EXPORT_LAYOUT",
            "matrix result region has multiple epilogues",
        ),
        Stage3PhysicalOperationCase(
            "physical_matrix_accumulator_k_overlap",
            arch,
            matrix,
            _matrix_accumulator_overlap(matrix, arch),
            "E_EXPORT_LAYOUT",
            "matrix accumulator K regions are not an exact cover",
        ),
        Stage3PhysicalOperationCase(
            "physical_vector_input_region",
            arch,
            unary,
            _unary_region(unary, KernelOpcode.VECTOR),
            "E_EXPORT_LAYOUT",
            "elementwise operand regions do not match output shape",
        ),
        Stage3PhysicalOperationCase(
            "physical_vector_arity",
            arch,
            timing,
            _vector_arity(timing),
            "E_EXPORT_UNSUPPORTED_OP",
            "vector kernel has invalid semantic opcode or arity",
        ),
        Stage3PhysicalOperationCase(
            "physical_matrix_arity",
            arch,
            timing,
            _matrix_arity(timing),
            "E_EXPORT_UNSUPPORTED_OP",
            "matrix kernel has invalid arity",
        ),
        Stage3PhysicalOperationCase(
            "physical_matrix_epilogue_arity",
            arch,
            matrix,
            _matrix_epilogue_arity(matrix),
            "E_EXPORT_UNSUPPORTED_OP",
            "matrix epilogue has invalid physical operands",
        ),
        Stage3PhysicalOperationCase(
            "physical_recv_wait_carries_data",
            arch,
            p2p,
            _recv_wait_data_operand(p2p),
            "E_EXPORT_UNSUPPORTED_OP",
            "synchronization operation carries data operands",
        ),
        Stage3PhysicalOperationCase(
            "physical_barrier_carries_data",
            arch,
            p2p_barrier,
            _barrier_data_operand(p2p_barrier),
            "E_EXPORT_UNSUPPORTED_OP",
            "synchronization operation carries data operands",
        ),
        Stage3PhysicalOperationCase(
            "physical_compute_nonlocal_sram",
            arch,
            dual,
            _nonlocal_compute_sram(dual),
            "E_TENSOR_NOT_RESIDENT",
            "compute operation does not use owner-local SRAM",
        ),
        Stage3PhysicalOperationCase(
            "physical_local_reduce_nonpartial_input",
            arch,
            timing,
            _local_reduce_nonpartial_input(timing),
            "E_TENSOR_NOT_RESIDENT",
            "reduction operands do not share the declared partial-SUM identity",
        ),
        Stage3PhysicalOperationCase(
            "physical_local_reduce_missing_contributor",
            arch,
            timing,
            _local_reduce_missing_contributor(timing),
            "E_TENSOR_NOT_RESIDENT",
            "partial-SUM completion has missing or repeated contributors",
        ),
        Stage3PhysicalOperationCase(
            "physical_compute_missing_consumer_layout",
            arch,
            timing,
            _missing_consumer_layout(timing),
            "E_EXPORT_LAYOUT",
            "compute operation uses a view without a consumer layout",
        ),
        Stage3PhysicalOperationCase(
            "physical_softmax_input_region",
            arch,
            unary,
            _unary_region(unary, KernelOpcode.SOFTMAX),
            "E_EXPORT_LAYOUT",
            "softmax input and output regions differ",
        ),
        Stage3PhysicalOperationCase(
            "physical_norm_input_region",
            arch,
            unary,
            _unary_region(unary, KernelOpcode.NORM),
            "E_EXPORT_LAYOUT",
            "normalization input and output regions differ",
        ),
        Stage3PhysicalOperationCase(
            "physical_blocked_noninjective_read_positive",
            arch,
            timing,
            blocked,
            None,
        ),
        Stage3PhysicalOperationCase(
            "physical_blocked_noninjective_read_logical_count_cost",
            arch,
            blocked,
            _logical_count_cost(blocked),
            "E_EXPORT_LAYOUT",
            "kernel byte cost is inconsistent",
        ),
        Stage3PhysicalOperationCase(
            "physical_nonunit_bmm_batch_region",
            intrinsic_arch,
            bmm,
            _bmm_batch_region(bmm),
            "E_EXPORT_LAYOUT",
            "batched matrix operands have inconsistent batch shapes",
        ),
    )

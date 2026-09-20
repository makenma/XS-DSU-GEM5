from dataclasses import dataclass, replace

from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.ir.common import Access, Const, DType, INVALID_CORE_ID, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, OpCode, ViewAttrs
from mesh_ir.ir.kernel_ir import (
    ControlToken,
    DistributionKind,
    KernelCost,
    KernelComputation,
    KernelMemoryRecords,
    KernelOp,
    KernelOpcode,
    KernelTensor,
    KernelTile,
    LocalCopyAttrs,
    MatrixPhase,
    OperandAccess,
    OperandAccessMode,
    StateOrigin,
    StateTransition,
    SynthesizedTensorPurpose,
    TensorState,
    TensorShard,
    VectorAlgorithm,
    VectorKernelAttrs,
)
from mesh_ir.model import Program
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin,
    ExternalSlotBacking,
    HaltAttrs,
    ObjectBacking,
    RequestBeginAttrs,
    RequestEndAttrs,
    ScheduledDependencyKind,
    StateSource,
)
from mesh_ir.traffic import Binding, BindingSlot, calculate_traffic
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage2_dependency_lifecycle_cases import (
    build_two_variant_program,
)
from tests.golden.support.compiler_fixtures import (
    compile_matrix_program,
    compile_softmax_program,
)
from tests.unit.test_gate2_kernel_ir import (
    load_compute_store_kernel,
    local_copy_kernel,
    partial_write_kernel,
    semantic_operation_kernel,
)


@dataclass(frozen=True)
class Stage3IntrinsicCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None


def _refreshed(program: Program, **changes) -> Program:
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def build_complete_program(
    arch: ArchManifest, records: KernelMemoryRecords, name: str
) -> Program:
    slots = []
    backings = []
    for object_ in records.objects:
        if object_.memory_space is MemorySpace.CORE_SRAM:
            continue
        slot_id = len(slots) + 1
        region_id = next(
            index
            for index, region in enumerate(arch.regions)
            if region.kind == object_.memory_space.name
        )
        tensor = records.tensors[object_.storage_tensor_id - 1]
        binding = Binding(
            slot_id,
            region_id,
            INVALID_CORE_ID,
            (slot_id - 1) * 4096,
            4096,
            64,
            tensor.access,
        )
        slots.append(
            BindingSlot(
                slot_id,
                f"object:{object_.object_id}",
                object_.memory_space,
                region_id,
                INVALID_CORE_ID,
                object_.footprint_bytes,
                max(object_.alignment_bytes, 64),
                tensor.access,
                binding,
            )
        )
        backings.append(ObjectBacking(object_.object_id, ExternalSlotBacking(slot_id)))
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", name, 1))
    variant = builder.variant(
        "main",
        name,
        name,
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
        external_backings=tuple(backings),
        binding_slots=tuple(slots),
    )
    operations = tuple(
        operation
        for operation in records.ops
        if operation.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW)
    )
    owners = {item.owner_core for item in operations}
    for ordinal, owner in enumerate(
        core_id for core_id in arch.core_ids if core_id in owners
    ):
        flags = (
            A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL
            if ordinal == 0
            else 0
        )
        stream = variant.stream(owner, 0, flags)
        if ordinal == 0:
            stream.control_command(RequestBeginAttrs())
        for operation in operations:
            if operation.owner_core == owner:
                stream.kernel_command(operation.op_id)
        if ordinal == 0:
            stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    return builder.build()


def _with_read_state(program: Program, stable_key: str, state_id: int) -> Program:
    target = next(
        item for item in program.semantics.kernel_ops if item.stable_key == stable_key
    )
    definition = next(
        item
        for item in program.semantics.kernel_ops
        if any(write.new_state_id == state_id for write in item.writes)
    )
    target_command_id = next(
        item.command_id
        for item in program.semantics.command_semantics
        if getattr(item.source, "kernel_op_id", None) == target.op_id
    )
    source_command_id = next(
        item.command_id
        for item in program.semantics.command_semantics
        if getattr(item.source, "kernel_op_id", None) == definition.op_id
    )
    dependency = next(
        item
        for item in program.semantics.dependencies
        if item.target_command_id == target_command_id
        and item.kind is ScheduledDependencyKind.KERNEL_STATE
        and type(item.source) is StateSource
    )
    return _refreshed(
        program,
        semantics=replace(
            program.semantics,
            kernel_ops=tuple(
                replace(item, reads=(replace(target.reads[0], state_id=state_id),))
                if item.op_id == target.op_id
                else item
                for item in program.semantics.kernel_ops
            ),
            dependencies=tuple(
                replace(
                    item,
                    source_command_id=source_command_id,
                    source=StateSource(state_id),
                )
                if item.dependency_id == dependency.dependency_id
                else item
                for item in program.semantics.dependencies
            ),
        ),
    )


def _metadata_tensor(
    tensor_id: int,
    producer_computation_id: int,
    synthesized_purpose: SynthesizedTensorPurpose | None,
    alias_root_tensor_id: int,
    name: str,
    shape: tuple[int, ...],
    strides: tuple[int, ...],
) -> KernelTensor:
    elements = 1
    storage_elements = 1
    for dimension in shape:
        elements *= dimension
    for dimension, stride in zip(shape, strides):
        storage_elements += (dimension - 1) * stride
    return KernelTensor(
        tensor_id,
        producer_computation_id,
        synthesized_purpose,
        alias_root_tensor_id,
        0,
        name,
        TensorRole.ACTIVATION,
        DType.FP16,
        shape,
        strides,
        StorageClass.CORE_SRAM,
        Access.READ_WRITE,
        elements * DType.FP16.byte_width,
        storage_elements * DType.FP16.byte_width,
        None,
    )


def _metadata_shard(shard_id: int, tensor_id: int, shape: tuple[int, ...]) -> TensorShard:
    return TensorShard(
        shard_id,
        tensor_id,
        1,
        3,
        DistributionKind.PARTITIONED,
        (0,) * len(shape),
        shape,
        shape,
    )


def build_metadata_contract_program(arch: ArchManifest) -> Program:
    source = load_compute_store_kernel().memory_records()
    records = replace(
        source,
        tensors=(
            *source.tensors,
            _metadata_tensor(3, 0, None, 3, "transpose-source", (2, 3), (3, 1)),
            _metadata_tensor(4, 2, None, 3, "transpose-result", (3, 2), (1, 3)),
            _metadata_tensor(5, 0, None, 5, "reshape-source", (2, 3), (3, 1)),
            _metadata_tensor(6, 3, None, 5, "reshape-result", (6,), (1,)),
            _metadata_tensor(
                7,
                2,
                SynthesizedTensorPurpose.ACCUMULATION,
                7,
                "synthesized",
                (1,),
                (1,),
            ),
        ),
        computations=(
            *source.computations,
            KernelComputation(
                2,
                OpCode.TRANSPOSE_VIEW,
                (3,),
                4,
                ViewAttrs(permutation=(1, 0)),
            ),
            KernelComputation(
                3,
                OpCode.RESHAPE_VIEW,
                (5,),
                6,
                ViewAttrs(shape=(Const(6),)),
            ),
        ),
        shards=(
            *source.shards,
            _metadata_shard(3, 3, (2, 3)),
            _metadata_shard(4, 4, (3, 2)),
            _metadata_shard(5, 5, (2, 3)),
            _metadata_shard(6, 6, (6,)),
            _metadata_shard(7, 7, (1,)),
        ),
    )
    return build_complete_program(arch, records, "stage3-metadata")


def build_stage3_intrinsic_cases() -> tuple[Stage3IntrinsicCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    intrinsic_arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    baseline = build_single_core_program(arch)
    two_variant = build_two_variant_program(arch)
    metadata = build_metadata_contract_program(intrinsic_arch)
    metadata_rank_mismatch = _refreshed(
        metadata,
        semantics=replace(
            metadata.semantics,
            kernel_tensors=tuple(
                replace(item, shape=(6,), strides=(1,))
                if item.tensor_id == 4
                else item
                for item in metadata.semantics.kernel_tensors
            ),
            logical_shards=tuple(
                replace(item, padded_local_shape=(6,), valid_shape=(6,))
                if item.shard_id == 4
                else item
                for item in metadata.semantics.logical_shards
            ),
        ),
    )
    metadata_reshape_overlapping = _refreshed(
        metadata,
        semantics=replace(
            metadata.semantics,
            kernel_tensors=tuple(
                replace(item, shape=(2, 3), strides=(1, 1), storage_extent_bytes=8)
                if item.tensor_id == 6
                else item
                for item in metadata.semantics.kernel_tensors
            ),
            logical_shards=tuple(
                replace(item, padded_local_shape=(2, 3), valid_shape=(2, 3))
                if item.shard_id == 6
                else item
                for item in metadata.semantics.logical_shards
            ),
        ),
    )
    metadata_synthesized_alias = _refreshed(
        metadata,
        semantics=replace(
            metadata.semantics,
            kernel_tensors=tuple(
                replace(item, alias_root_tensor_id=3)
                if item.tensor_id == 7
                else item
                for item in metadata.semantics.kernel_tensors
            ),
        ),
    )
    load = build_complete_program(
        intrinsic_arch,
        load_compute_store_kernel().memory_records(),
        "stage3-load",
    )
    partial = build_complete_program(
        intrinsic_arch,
        partial_write_kernel().memory_records(),
        "stage3-partial",
    )
    partial_fill = next(
        item
        for item in partial.semantics.kernel_ops
        if item.stable_key == "fill:1"
    )
    partial_hole_region = replace(partial_fill.writes[0].region, shape=(1,))
    partial_hole_semantics = replace(
        partial.semantics,
        kernel_ops=tuple(
            replace(
                item,
                writes=(replace(partial_fill.writes[0], region=partial_hole_region),),
            )
            if item.op_id == partial_fill.op_id
            else item
            for item in partial.semantics.kernel_ops
        ),
        endpoint_uses=tuple(
            replace(item, use=replace(item.use, region=partial_hole_region))
            if item.descriptor_id == 2
            else item
            for item in partial.semantics.endpoint_uses
        ),
    )
    partial_hole_descriptors = tuple(
        replace(
            item,
            row_bytes=4,
            src_stride_bytes=4,
            dst_stride_bytes=4,
            useful_bytes=4,
            physical_storage_bytes=4,
        )
        if item.descriptor_id == 2
        else item
        for item in partial.dma_descriptors
    )
    partial_hole_provisional = replace(
        partial,
        dma_descriptors=partial_hole_descriptors,
        expected_traffic=(),
        semantics=partial_hole_semantics,
        semantic_sha256="",
    )
    partial_hole_executions, partial_hole_identity = derive_descriptor_execution_set(
        partial_hole_provisional,
        intrinsic_arch,
    )
    partial_hole_traffic = calculate_traffic(
        intrinsic_arch,
        partial_hole_identity,
        partial_hole_executions,
    )
    partial_hole = _refreshed(
        partial_hole_provisional,
        expected_traffic=_expected_traffic(
            partial_hole_traffic,
            partial_hole_executions,
            intrinsic_arch,
        ),
        semantics=replace(
            partial_hole_semantics,
            reference_binding_identity_sha256=partial_hole_identity,
            intrinsic_traffic=partial_hole_traffic,
        ),
    )
    local_copy = build_complete_program(
        intrinsic_arch,
        local_copy_kernel().memory_records(),
        "stage3-copy",
    )
    local_copy_source = local_copy_kernel().memory_records()
    local_copy_operation = local_copy_source.ops[-1]
    local_copy_region = local_copy_operation.reads[0].region
    stale_records = replace(
        local_copy_source,
        states=(
            *local_copy_source.states,
            TensorState(4, 2, 2, StateOrigin.PRODUCED),
            TensorState(5, 1, 1, StateOrigin.PRODUCED),
        ),
        tokens=(*local_copy_source.tokens, ControlToken(2), ControlToken(3)),
        ops=(
            *local_copy_source.ops,
            KernelOp(
                6,
                0,
                "local-copy:overwrite",
                KernelOpcode.LOCAL_COPY,
                3,
                0,
                (
                    OperandAccess(
                        1,
                        1,
                        local_copy_region,
                        OperandAccessMode.READ,
                    ),
                ),
                (StateTransition(3, 4, 2, local_copy_region),),
                LocalCopyAttrs(),
                (1,),
                2,
            ),
            KernelOp(
                7,
                0,
                "local-copy:current",
                KernelOpcode.LOCAL_COPY,
                3,
                0,
                (
                    OperandAccess(
                        4,
                        2,
                        local_copy_region,
                        OperandAccessMode.READ,
                    ),
                ),
                (StateTransition(1, 5, 1, local_copy_region),),
                LocalCopyAttrs(),
                (2,),
                3,
            ),
        ),
    )
    stale_current = build_complete_program(
        intrinsic_arch,
        stale_records,
        "stage3-local-copy-stale",
    )
    stale_read = _with_read_state(stale_current, "local-copy:current", 3)
    first_half = replace(local_copy_region, shape=(2,))
    second_half = replace(local_copy_region, origin=(2,), shape=(2,))
    disjoint_records = replace(
        stale_records,
        ops=tuple(
            replace(
                item,
                reads=(replace(item.reads[0], region=first_half),),
                writes=(replace(item.writes[0], region=first_half),),
            )
            if item.stable_key == "local-copy:overwrite"
            else replace(
                item,
                reads=(replace(item.reads[0], region=second_half),),
                writes=(replace(item.writes[0], region=second_half),),
            )
            if item.stable_key == "local-copy:current"
            else item
            for item in stale_records.ops
        ),
    )
    disjoint_current = build_complete_program(
        intrinsic_arch,
        disjoint_records,
        "stage3-local-copy-disjoint",
    )
    disjoint_old_read = _with_read_state(
        disjoint_current,
        "local-copy:current",
        3,
    )
    scalar = build_complete_program(
        intrinsic_arch,
        semantic_operation_kernel(
            KernelOpcode.VECTOR,
            VectorKernelAttrs(
                OpCode.RELU,
                ElementwiseAttrs(),
                KernelTile(0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1),
                KernelCost(4, 4, 8, 0, 1, 0),
                VectorAlgorithm.ELEMENTWISE,
            ),
            (((), DType.FP32),),
            ((), DType.FP32),
        ).memory_records(),
        "stage3-scalar",
    )
    strided_source = load_compute_store_kernel().memory_records()
    store = next(item for item in strided_source.ops if item.stable_key == "store")
    strided = build_complete_program(
        intrinsic_arch,
        replace(
            strided_source,
            ops=tuple(
                replace(
                    operation,
                    reads=(
                        replace(
                            operation.reads[0],
                            region=replace(
                                operation.reads[0].region,
                                shape=(2,),
                                steps=(2,),
                            ),
                        ),
                    ),
                    writes=(
                        replace(
                            operation.writes[0],
                            region=replace(
                                operation.writes[0].region,
                                shape=(2,),
                                steps=(2,),
                            ),
                        ),
                    ),
                )
                if operation.op_id == store.op_id
                else operation
                for operation in strided_source.ops
            ),
        ),
        "stage3-strided",
    )
    resident_state = next(
        item
        for item in local_copy.semantics.states
        if item.origin is StateOrigin.PRE_RESIDENT
    )
    local_copy_missing_initialization = _refreshed(
        local_copy,
        semantics=replace(
            local_copy.semantics,
            states=tuple(
                replace(item, origin=StateOrigin.EMPTY)
                if item.state_id == resident_state.state_id
                else item
                for item in local_copy.semantics.states
            ),
        ),
    )
    compute = next(
        item for item in load.semantics.kernel_ops if item.stable_key == "relu"
    )
    noninjective_write = _refreshed(
        load,
        semantics=replace(
            load.semantics,
            views=tuple(
                replace(view, object_strides=(0,), layout=None)
                if view.view_id == compute.writes[0].view_id
                else view
                for view in load.semantics.views
            ),
        ),
    )
    semantics = baseline.semantics
    computation = semantics.computations[0]
    operation_index, operation = next(
        (index, item)
        for index, item in enumerate(semantics.kernel_ops)
        if item.opcode is KernelOpcode.GEMM
    )
    changed_operand_identity = _refreshed(
        baseline,
        semantics=replace(
            semantics,
            computations=(
                replace(
                    computation,
                    operand_tensor_ids=tuple(reversed(computation.operand_tensor_ids)),
                ),
                *semantics.computations[1:],
            ),
        ),
    )
    changed_result_producer = _refreshed(
        baseline,
        semantics=replace(
            semantics,
            computations=(
                replace(
                    computation,
                    result_tensor_id=computation.operand_tensor_ids[0],
                ),
                *semantics.computations[1:],
            ),
        ),
    )
    changed_result_shard = _refreshed(
        baseline,
        semantics=replace(
            semantics,
            kernel_ops=(
                *semantics.kernel_ops[:operation_index],
                replace(operation, result_shard_id=1),
                *semantics.kernel_ops[operation_index + 1:],
            ),
        ),
    )
    changed_k_origin = _refreshed(
        baseline,
        semantics=replace(
            semantics,
            kernel_ops=(
                *semantics.kernel_ops[:operation_index],
                replace(
                    operation,
                    attrs=replace(
                        operation.attrs,
                        tile=replace(
                            operation.attrs.tile,
                            k_origin=operation.attrs.tile.k_origin + 1,
                        ),
                    ),
                ),
                *semantics.kernel_ops[operation_index + 1:],
            ),
        ),
    )
    changed_stable_key = _refreshed(
        baseline,
        semantics=replace(
            semantics,
            kernel_ops=(
                *semantics.kernel_ops[:operation_index],
                replace(operation, stable_key=semantics.kernel_ops[0].stable_key),
                *semantics.kernel_ops[operation_index + 1:],
            ),
        ),
    )
    compiler_matrix = compile_matrix_program(arch)
    matrix_epilogue = next(
        item
        for item in compiler_matrix.semantics.kernel_ops
        if item.opcode is KernelOpcode.MATRIX_EPILOGUE
    )
    incomplete_accumulator = next(
        item
        for item in compiler_matrix.semantics.kernel_ops
        if item.opcode is KernelOpcode.GEMM
        and item.computation_id == matrix_epilogue.computation_id
        and item.attrs.phase is MatrixPhase.ACCUMULATE_FIRST
    )
    matrix_epilogue_incomplete_accumulator = _with_read_state(
        compiler_matrix,
        matrix_epilogue.stable_key,
        incomplete_accumulator.writes[0].new_state_id,
    )
    empty_softmax = compile_softmax_program(arch, 0)
    empty_softmax_op = next(
        item
        for item in empty_softmax.semantics.kernel_ops
        if item.opcode is KernelOpcode.SOFTMAX
    )
    empty_softmax_missing_result_shard = _refreshed(
        empty_softmax,
        semantics=replace(
            empty_softmax.semantics,
            kernel_ops=tuple(
                replace(item, result_shard_id=0)
                if item.op_id == empty_softmax_op.op_id
                else item
                for item in empty_softmax.semantics.kernel_ops
            ),
        ),
    )
    return (
        Stage3IntrinsicCase("logical_contract_positive", arch, baseline, baseline, None),
        Stage3IntrinsicCase("logical_operand_identity", arch, baseline, changed_operand_identity, "E_ABI_CHECKSUM"),
        Stage3IntrinsicCase("logical_result_producer", arch, baseline, changed_result_producer, "E_ABI_BOUNDS"),
        Stage3IntrinsicCase("physical_result_shard", arch, baseline, changed_result_shard, "E_EXPORT_LAYOUT"),
        Stage3IntrinsicCase("matrix_k_origin", arch, baseline, changed_k_origin, "E_EXPORT_LAYOUT"),
        Stage3IntrinsicCase("logical_duplicate_stable_key", arch, baseline, changed_stable_key, "E_ABI_ORDER"),
        Stage3IntrinsicCase("logical_stable_key_per_variant_positive", arch, two_variant, two_variant, None),
        Stage3IntrinsicCase("metadata_contract_positive", intrinsic_arch, metadata, metadata, None),
        Stage3IntrinsicCase("metadata_transpose_result_rank_mismatch", intrinsic_arch, metadata, metadata_rank_mismatch, "E_EXPORT_LAYOUT"),
        Stage3IntrinsicCase("metadata_reshape_overlapping_identical", intrinsic_arch, metadata, metadata_reshape_overlapping, "E_EXPORT_LAYOUT"),
        Stage3IntrinsicCase("metadata_synthesized_tensor_alias_root", intrinsic_arch, metadata, metadata_synthesized_alias, "E_EXPORT_LAYOUT"),
        Stage3IntrinsicCase("compiler_matrix_epilogue_positive", arch, compiler_matrix, compiler_matrix, None),
        Stage3IntrinsicCase("compiler_matrix_epilogue_incomplete_accumulator", arch, compiler_matrix, matrix_epilogue_incomplete_accumulator, "E_EXPORT_LAYOUT"),
        Stage3IntrinsicCase("compiler_empty_softmax_positive", arch, empty_softmax, empty_softmax, None),
        Stage3IntrinsicCase("compiler_empty_softmax_missing_result_shard", arch, empty_softmax, empty_softmax_missing_result_shard, "E_ABI_BOUNDS"),
        Stage3IntrinsicCase("intrinsic_load_compute_store_positive", intrinsic_arch, load, load, None),
        Stage3IntrinsicCase("intrinsic_partial_write_disjoint_positive", intrinsic_arch, partial, partial, None),
        Stage3IntrinsicCase("intrinsic_local_copy_positive", intrinsic_arch, local_copy, local_copy, None),
        Stage3IntrinsicCase("intrinsic_stale_local_copy_positive", intrinsic_arch, stale_current, stale_current, None),
        Stage3IntrinsicCase("intrinsic_stale_local_copy_read", intrinsic_arch, stale_current, stale_read, "E_TENSOR_NOT_RESIDENT"),
        Stage3IntrinsicCase("intrinsic_disjoint_old_state_positive", intrinsic_arch, disjoint_current, disjoint_old_read, None),
        Stage3IntrinsicCase("intrinsic_scalar_positive", intrinsic_arch, scalar, scalar, None),
        Stage3IntrinsicCase("intrinsic_strided_store_positive", intrinsic_arch, strided, strided, None),
        Stage3IntrinsicCase("intrinsic_local_copy_missing_initialization", intrinsic_arch, local_copy, local_copy_missing_initialization, "E_TENSOR_NOT_RESIDENT"),
        Stage3IntrinsicCase("intrinsic_partial_hole", intrinsic_arch, partial, partial_hole, "E_TENSOR_NOT_RESIDENT"),
        Stage3IntrinsicCase("intrinsic_noninjective_write", intrinsic_arch, load, noninjective_write, "E_EXPORT_LAYOUT"),
    )

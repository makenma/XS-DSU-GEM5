from dataclasses import replace
from math import prod

from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated.semantic_enums import Access
from mesh_ir.golden_programs import build_compute_timing_program
from mesh_ir.ir.kernel_ir import (
    CollectiveAlgorithm,
    CollectiveAttrs,
    CollectiveKind,
    KernelOpcode,
    OperandAccess,
    OperandAccessMode,
    ReduceKind,
    StateTransition,
)
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage3_physical_operation_cases import (
    Stage3PhysicalOperationCase,
)


def build_stage3_collective_capability_cases() -> tuple[Stage3PhysicalOperationCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    baseline = build_compute_timing_program(arch)
    local_reduce = next(
        operation
        for operation in baseline.semantics.kernel_ops
        if operation.opcode is KernelOpcode.LOCAL_REDUCE
    )
    epilogue = next(
        operation
        for operation in baseline.semantics.kernel_ops
        if operation.opcode is KernelOpcode.MATRIX_EPILOGUE
    )
    views = {item.view_id: item for item in baseline.semantics.views}
    shards = {item.shard_id: item for item in baseline.semantics.logical_shards}
    tensors = {item.tensor_id: item for item in baseline.semantics.kernel_tensors}
    partial = next(
        item
        for item in baseline.semantics.partial_sums
        if item.partial_sum_id == local_reduce.attrs.partial_sum_id
    )
    placement = next(
        item
        for item in baseline.semantics.placements
        if item.placement_id == partial.placement_id
    )
    output_view = views[local_reduce.writes[0].view_id]
    output_shard = shards[output_view.shard_id]
    output_tensor = tensors[output_shard.tensor_id]
    completed_state = next(
        item
        for item in baseline.semantics.states
        if item.state_id == local_reduce.writes[0].new_state_id
    )
    replaced_state = next(
        item
        for item in baseline.semantics.states
        if item.state_id == epilogue.writes[0].new_state_id
    )
    epilogue_command = next(
        command
        for command, semantic in zip(
            baseline.commands, baseline.semantics.command_semantics
        )
        if getattr(semantic.source, "kernel_op_id", None) == epilogue.op_id
    )
    collective_operands = (
        baseline.command_operands[epilogue_command.operand_begin],
        replace(
            baseline.command_operands[epilogue_command.operand_begin],
            access=int(Access.READ_WRITE),
        ),
    )
    collective = replace(
        epilogue,
        stable_key="timing:11:collective:selected",
        opcode=KernelOpcode.COLLECTIVE,
        result_shard_id=local_reduce.result_shard_id,
        reads=(
            OperandAccess(
                completed_state.state_id,
                output_view.view_id,
                local_reduce.writes[0].region,
                OperandAccessMode.READ,
            ),
        ),
        writes=(
            StateTransition(
                completed_state.state_id,
                replaced_state.state_id,
                output_view.view_id,
                local_reduce.writes[0].region,
            ),
        ),
        attrs=CollectiveAttrs(
            CollectiveKind.ALL_REDUCE,
            placement.core_ids,
            CollectiveAlgorithm.RING,
            prod(local_reduce.writes[0].region.shape) * output_tensor.dtype.byte_width,
            ReduceKind.SUM,
            partial.partial_sum_id,
        ),
    )
    semantics = replace(
        baseline.semantics,
        states=tuple(
            replace(
                state,
                object_id=(
                    completed_state.object_id
                    if state.state_id == replaced_state.state_id
                    else state.object_id
                ),
                version=(
                    completed_state.version + 1
                    if state.state_id == replaced_state.state_id
                    else state.version
                ),
                partial_sum_id=(
                    partial.partial_sum_id
                    if state.state_id in (
                        completed_state.state_id,
                        replaced_state.state_id,
                    )
                    else state.partial_sum_id
                ),
            )
            for state in baseline.semantics.states
        ),
        kernel_ops=tuple(
            collective if operation.op_id == collective.op_id else operation
            for operation in baseline.semantics.kernel_ops
        ),
    )
    command_operands = list(baseline.command_operands)
    command_operands[
        epilogue_command.operand_begin : epilogue_command.operand_begin
        + epilogue_command.operand_count
    ] = collective_operands
    provisional = replace(
        baseline,
        semantics=semantics,
        command_operands=tuple(command_operands),
        semantic_sha256="",
    )
    program = replace(
        provisional,
        semantic_sha256=semantic_sha256(provisional.semantic_dict()),
    )
    return (
        Stage3PhysicalOperationCase(
            "abstract_collective_has_no_scheduled_execution",
            arch,
            baseline,
            program,
            "E_CAPABILITY_MISMATCH",
            "abstract collective has no physical execution work",
        ),
    )

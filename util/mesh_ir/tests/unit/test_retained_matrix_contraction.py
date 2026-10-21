from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import Collectives, Tiling
from mesh_ir.ir.common import Const, DType, TensorRole, contiguous_strides
from mesh_ir.ir.graph_ir import ElementwiseAttrs, GraphFunction, GraphModule, GraphOp, GraphValue, MatmulAttrs, OpCode, PytreeSpec
from mesh_ir.ir.kernel_ir import KernelOpcode
from mesh_ir.passes.bufferize import bufferize_and_alias
from mesh_ir.passes.collectives import lower_collectives
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor
from mesh_ir.passes.movement import insert_data_movement
from mesh_ir.passes.placement import LoweringDecisions, PlacementCandidate, PlacementDecision, PlacementScore
from mesh_ir.passes.placement_geometry import OperationPlacementGeometry, PlacementValues, ShardingStrategy, advance_placement_ledger, create_placement_ledger, derive_operation_geometry
from mesh_ir.passes.planning_prefix import OperationIdentity, PlanningResult, PlanningState
from mesh_ir.passes.sharding import shard_and_pad
from mesh_ir.passes.tiling import tile_kernels


@pytest.mark.parametrize('contraction', [8, 13])
@pytest.mark.parametrize('overlapping_cores', [False, True])
@pytest.mark.parametrize('form', ['matmul', 'bmm', 'transposed'])
def test_replicated_retained_inputs_contract_over_required_k_shards(contraction, overlapping_cores, form):
    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / 'configs/example/ai_mesh/arch/mesh_5x5.yaml')
    target_cores = (0, 5, 6, 10)
    producer_cores = (5, 10, 11, 15) if overlapping_cores else target_cores
    lhs_shape = (contraction, 3) if form == 'transposed' else (3, contraction)
    rhs_shape = (5, contraction) if form == 'transposed' else (contraction, 5)
    leading = (2,) if form == 'bmm' else ()
    shapes = tuple(leading + shape for shape in (lhs_shape, rhs_shape, lhs_shape, rhs_shape, (3, 5)))
    roles = (TensorRole.INPUT, TensorRole.INPUT, TensorRole.ACTIVATION, TensorRole.ACTIVATION, TensorRole.OUTPUT)
    values = tuple(
        GraphValue(index, f'value{index}', role, DType.FP32,
            tuple(Const(extent) for extent in shape), contiguous_strides(tuple(Const(extent) for extent in shape)),
            0, index, content_sha256=str(index) * 64 if role is TensorRole.INPUT else None)
        for index, (shape, role) in enumerate(zip(shapes, roles), 1)
    )
    ops = (
        GraphOp(1, OpCode.RELU, (1,), (3,), ElementwiseAttrs(), 'lhs:produce'),
        GraphOp(2, OpCode.RELU, (2,), (4,), ElementwiseAttrs(), 'rhs:produce'),
        GraphOp(3, OpCode.BMM if form == 'bmm' else OpCode.MATMUL, (3, 4), (5,),
            MatmulAttrs(batch_axes=(0,) if form == 'bmm' else (),
                lhs_transpose=form == 'transposed', rhs_transpose=form == 'transposed'), 'matrix:contract'),
    )
    function = GraphFunction(1, 'forward', (1, 2), ops, (5,),
        PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), 'a' * 64, 'forward', 'static', values, (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    ledger = create_placement_ledger(function, values)
    candidates = []
    decisions = []
    for op, strategy in zip(ops, (ShardingStrategy.REPLICATE, ShardingStrategy.REPLICATE, ShardingStrategy.MATRIX_K_SUM)):
        cores = target_cores if op.opcode in (OpCode.MATMUL, OpCode.BMM) else producer_cores
        geometry = derive_operation_geometry(op, PlacementValues(values), strategy, cores, ledger,
            Tiling(2, 3, 2, True), Collectives('ring', 32), arch)
        assert isinstance(geometry, OperationPlacementGeometry)
        identity = OperationIdentity('forward', 'static', 1, op.op_id)
        candidates.append(PlacementCandidate(identity, 0, strategy, cores, PlacementScore(0, 0, 0), None, geometry))
        decisions.append(PlacementDecision(identity, strategy, cores, len(candidates) - 1))
        ledger = advance_placement_ledger(ledger, geometry)
    selected = LoweringDecisions(tuple(candidates), tuple(decisions))
    tiled = tile_kernels(planning, shard_and_pad(planning, selected))
    buffers = bufferize_and_alias(planning, tiled)
    with PassExecutor(1) as executor:
        kernel = insert_data_movement(planning, lower_collectives(planning, buffers), executor)[0]
    kernel.verify(arch)
    geometry = candidates[-1].geometry
    for op in kernel.ops:
        if op.opcode not in (KernelOpcode.GEMM, KernelOpcode.BMM) or not op.reads:
            continue
        for operand_index, read in enumerate(op.reads[:2]):
            view = kernel.views[read.view_id - 1]
            shard = kernel.shards[view.shard_id - 1]
            required = next(shard for shard in geometry.operand_distributions[operand_index].required_distribution.shards
                if shard.owner_core == op.owner_core)
            assert shard.global_origin == required.global_origin
            assert shard.valid_shape == required.valid_shape

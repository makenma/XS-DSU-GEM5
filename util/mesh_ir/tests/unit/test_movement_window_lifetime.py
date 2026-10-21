from mesh_ir.analysis.dependency import build_kernel_dependency_graph
from mesh_ir.analysis.sram import plan_static_sram
from mesh_ir.compile_config import Collectives, Tiling
from mesh_ir.ir.common import Const, DType, MemorySpace, TensorRole, contiguous_strides
from mesh_ir.ir.graph_ir import GraphFunction, GraphModule, GraphOp, GraphValue, MatmulAttrs, OpCode, PytreeSpec
from mesh_ir.passes.bufferize import bufferize_and_alias
from mesh_ir.passes.collectives import lower_collectives
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor
from mesh_ir.passes.movement import insert_data_movement
from mesh_ir.passes.placement import LoweringDecisions, PlacementCandidate, PlacementDecision, PlacementScore
from mesh_ir.passes.placement_geometry import PlacementValues, ShardingStrategy, advance_placement_ledger, create_placement_ledger, derive_operation_geometry
from mesh_ir.passes.planning_prefix import OperationIdentity, PlanningResult, PlanningState
from mesh_ir.passes.sharding import shard_and_pad
from mesh_ir.passes.tiling import tile_kernels
from tests.unit.test_gate2_dependency_sram import local_arch


def test_sequential_matrix_windows_are_released_before_the_next_weight_prefetch():
    arch = local_arch(sram_bytes=512, axi_data_bytes=8)
    owner = arch.core_ids[0]
    shapes = ((1, 16), (16, 16), (16, 16), (16, 16), (1, 16), (1, 16), (1, 16))
    roles = (TensorRole.INPUT, TensorRole.WEIGHT, TensorRole.WEIGHT, TensorRole.WEIGHT,
        TensorRole.ACTIVATION, TensorRole.ACTIVATION, TensorRole.OUTPUT)
    values = tuple(GraphValue(identity, f'value:{identity}', role, DType.FP32,
        tuple(Const(extent) for extent in shape), contiguous_strides(tuple(Const(extent) for extent in shape)),
        0, identity, content_sha256=f'{identity:064x}' if identity <= 4 else None)
        for identity, (shape, role) in enumerate(zip(shapes, roles), 1))
    ops = tuple(GraphOp(identity, OpCode.MATMUL, (1 if identity == 1 else identity + 3, identity + 1),
        (identity + 4,), MatmulAttrs(), f'matrix:{identity}') for identity in range(1, 4))
    function = GraphFunction(1, 'forward', (1,), ops, (7,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), 'a' * 64, 'forward', 'static', values, (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    ledger = create_placement_ledger(function, values)
    candidates = []
    decisions = []
    for op in ops:
        geometry = derive_operation_geometry(op, PlacementValues(values), ShardingStrategy.SINGLE,
            (owner,), ledger, Tiling(1, 4, 4, True), Collectives('ring', 32), arch)
        identity = OperationIdentity('forward', 'static', 1, op.op_id)
        candidates.append(PlacementCandidate(identity, 0, ShardingStrategy.SINGLE, (owner,), PlacementScore(0, 0, 0), None, geometry))
        decisions.append(PlacementDecision(identity, ShardingStrategy.SINGLE, (owner,), len(candidates) - 1))
        ledger = advance_placement_ledger(ledger, geometry)
    tiled = tile_kernels(planning, shard_and_pad(planning, LoweringDecisions(tuple(candidates), tuple(decisions))))
    buffers = bufferize_and_alias(planning, tiled)
    with PassExecutor(1) as executor:
        kernel = insert_data_movement(planning, lower_collectives(planning, buffers), executor)[0]
    dependencies = build_kernel_dependency_graph(kernel)
    for previous, following in ((2, 3), (3, 4)):
        previous_objects = {obj.object_id for obj in kernel.objects
            if obj.storage_tensor_id == previous and obj.memory_space is MemorySpace.CORE_SRAM}
        following_objects = {obj.object_id for obj in kernel.objects
            if obj.storage_tensor_id == following and obj.memory_space is MemorySpace.CORE_SRAM}
        previous_ops = [op for op in kernel.ops if any(kernel.views[access.view_id - 1].object_id in previous_objects
            for access in (*op.reads, *op.writes))]
        following_first = min((op for op in kernel.ops if any(kernel.views[access.view_id - 1].object_id in following_objects
            for access in op.writes)), key=lambda op: op.op_id)
        assert all(dependencies.graph.happens_before(dependencies.token_node_by_id[op.done_token - 1],
            dependencies.op_node_by_id[following_first.op_id - 1]) for op in previous_ops)
    plan = plan_static_sram(kernel, arch)
    assert max(report.peak_bytes for report in plan.reports) <= arch.sram_bytes
    assert len({allocation.offset_bytes for allocation in plan.allocations}) < len(plan.allocations)

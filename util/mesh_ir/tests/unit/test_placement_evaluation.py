import itertools
from dataclasses import replace

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import Collectives, Tiling
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DType, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, GraphFunction, GraphOp, MatmulAttrs, OpCode, PytreeSpec, ViewAttrs
from mesh_ir.passes import placement_evaluation as evaluation
from mesh_ir.passes.placement_geometry import (
    PlacementRejection, PlacementValues, ShardingStrategy, advance_placement_ledger,
    create_placement_ledger, derive_operation_geometry, estimate_operation_geometry,
)
from tests.unit.test_gate2_placement_geometry import ARCH_PATH, _value


@pytest.mark.parametrize('branch', [False, True])
def test_metadata_consumers_constrain_producer_distribution(branch):
    from mesh_ir.ir.kernel_ir import DistributionKind
    from mesh_ir.passes.placement_geometry import RankShardGeometry, ValueDistribution

    values = PlacementValues((
        _value(1, 'produced', TensorRole.ACTIVATION, DType.FP16, (2, 4, 8)),
        _value(2, 'view', TensorRole.ACTIVATION, DType.FP16, (2, 32), alias_root=1),
        _value(3, 'view_again', TensorRole.OUTPUT, DType.FP16, (64,), alias_root=1)))
    first = GraphOp(1, OpCode.RESHAPE_VIEW, (1,), (2,),
        ViewAttrs(shape=values.values[1].shape), 'reshape:first')
    second = GraphOp(2, OpCode.RESHAPE_VIEW, (1 if branch else 2,), (3,),
        ViewAttrs(shape=values.values[2].shape), 'reshape:second')
    function = GraphFunction(1, 'forward', (1,), (first, second), (3,),
        PytreeSpec.leaf(), PytreeSpec.leaf())
    constraints = evaluation.ViewDistributionConstraints(function.ops, values)
    inner = ValueDistribution(1, DistributionKind.PARTITIONED, 2, (0, 1), tuple(
        RankShardGeometry(i, i, (0, 0, 4 * i), (2, 4, 4), (2, 4, 4)) for i in range(2)))
    assert isinstance(constraints.check(inner), PlacementRejection)
    outer = ValueDistribution(1, DistributionKind.PARTITIONED, 0, (0, 1), tuple(
        RankShardGeometry(i, i, (i, 0, 0), (1, 4, 8), (1, 4, 8)) for i in range(2)))
    assert constraints.check(outer) is None
    middle = ValueDistribution(1, DistributionKind.PARTITIONED, 1, (0, 1), tuple(
        RankShardGeometry(i, i, (0, i * 2, 0), (2, 2, 8), (2, 2, 8)) for i in range(2)))
    assert isinstance(constraints.check(middle), PlacementRejection)


@pytest.mark.parametrize('collective', ['ring', 'tree'])
@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('sram_bytes', [2097152, 128])
@pytest.mark.parametrize('rows', [1, 3])
def test_equivalent_candidates_have_exact_direct_scores(monkeypatch, collective, retained, sram_bytes, rows):
    arch = load_arch(ARCH_PATH)
    values = (
        _value(1, 'input', TensorRole.INPUT, DType.FP32, (rows, 16)),
        _value(2, 'weight', TensorRole.WEIGHT, DType.FP32, (16, 24), content='b' * 64),
        _value(3, 'activation', TensorRole.ACTIVATION, DType.FP32, (rows, 16)),
        _value(4, 'output', TensorRole.OUTPUT, DType.FP32, (rows, 24)),
    )
    first = GraphOp(1, OpCode.RELU, (1,), (3,), ElementwiseAttrs(), 'relu:pin')
    op = GraphOp(2, OpCode.MATMUL, (3 if retained else 1, 2), (4,), MatmulAttrs(), 'matmul:score')
    function = GraphFunction(1, 'forward', (1,), (first, op) if retained else (op,), (4,),
                             PytreeSpec.leaf(), PytreeSpec.leaf())
    ledger = create_placement_ledger(function, values)
    values = PlacementValues(values)
    tiling = Tiling(2, 8, 8, True)
    collectives = Collectives(collective, 32)
    if retained:
        geometry = derive_operation_geometry(first, values, ShardingStrategy.OUTER_DIM,
                                             arch.core_ids[:2], ledger, tiling, collectives, arch)
        ledger = advance_placement_ledger(ledger, geometry)
    arch.sram_bytes = sram_bytes
    evaluator = evaluation.PlacementGeometryEvaluator(op, values, ledger, tiling, collectives, arch)
    calls = []
    direct = evaluation.derive_operation_geometry

    def observed(*args):
        calls.append(args[3])
        return direct(*args)

    monkeypatch.setattr(evaluation, 'derive_operation_geometry', observed)
    count = 0
    for cores in itertools.combinations(arch.core_ids, 2):
        for strategy in (ShardingStrategy.MATRIX_M, ShardingStrategy.MATRIX_N, ShardingStrategy.MATRIX_K_SUM):
            geometry = derive_operation_geometry(op, values, strategy, cores, ledger, tiling, collectives, arch)
            expected = geometry if isinstance(geometry, PlacementRejection) else estimate_operation_geometry(geometry, ledger, arch)
            assert evaluator.estimate(strategy, cores) == expected
            count += 1
    assert len(calls) < count


def test_cached_geometry_recomputes_physical_routes():
    arch = load_arch(ARCH_PATH)
    values = PlacementValues((_value(1, 'input', TensorRole.INPUT, DType.FP32, (8,)),
                              _value(2, 'output', TensorRole.OUTPUT, DType.FP32, (8,))))
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), 'relu:routes')
    function = GraphFunction(1, 'forward', (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    ledger = create_placement_ledger(function, values.values)
    evaluator = evaluation.PlacementGeometryEvaluator(op, values, ledger, Tiling(2, 8, 8, True), Collectives('tree', 32), arch)
    estimates = [evaluator.estimate(ShardingStrategy.SINGLE, (core,)) for core in arch.core_ids]
    assert len({row.manhattan_hop_bytes_lower_bound for row in estimates}) > 1
    assert len({row.communication_bytes for row in estimates}) == 1
    memory = arch.fabric.targets[0]
    arch.fabric = replace(arch.fabric, targets=(replace(memory, ranges=tuple(
        replace(row, access=Access.READ_ONLY) for row in memory.ranges)), *arch.fabric.targets[1:]))
    assert isinstance(evaluator.estimate(ShardingStrategy.SINGLE, (arch.core_ids[0],)), PlacementRejection)
    for invalid in ((True,), (65534,), ()):
        with pytest.raises(MeshIrError):
            evaluator.estimate(ShardingStrategy.SINGLE, invalid)


@pytest.mark.parametrize('limited', [False, True])
def test_unrelated_live_storage_does_not_split_geometry_templates(monkeypatch, limited):
    arch = load_arch(ARCH_PATH)
    values = PlacementValues((
        _value(1, 'first_input', TensorRole.INPUT, DType.FP32, (8, 16)),
        _value(2, 'second_input', TensorRole.INPUT, DType.FP32, (3, 16)),
        _value(3, 'retained', TensorRole.ACTIVATION, DType.FP32, (8, 16)),
        _value(4, 'weight', TensorRole.WEIGHT, DType.FP32, (16, 24), content='b' * 64),
        _value(5, 'output', TensorRole.OUTPUT, DType.FP32, (3, 24)),
        _value(6, 'last_output', TensorRole.OUTPUT, DType.FP32, (8, 16))))
    first = GraphOp(1, OpCode.RELU, (1,), (3,), ElementwiseAttrs(), 'retain:unrelated')
    op = GraphOp(2, OpCode.MATMUL, (2, 4), (5,), MatmulAttrs(), 'matmul:independent')
    last = GraphOp(3, OpCode.RELU, (3,), (6,), ElementwiseAttrs(), 'consume:retained')
    function = GraphFunction(1, 'forward', (1, 2), (first, op, last), (5, 6),
                             PytreeSpec.leaf(), PytreeSpec.leaf())
    tiling, collectives = Tiling(2, 8, 8, True), Collectives('ring', 32)
    ledger = create_placement_ledger(function, values.values)
    geometry = derive_operation_geometry(first, values, ShardingStrategy.OUTER_DIM,
        arch.core_ids[:2], ledger, tiling, collectives, arch)
    ledger = advance_placement_ledger(ledger, geometry)
    strategies = (ShardingStrategy.MATRIX_M, ShardingStrategy.MATRIX_N, ShardingStrategy.MATRIX_K_SUM)
    direct_scores = {}
    for cores in itertools.combinations(arch.core_ids, 2):
        for strategy in strategies:
            geometry = derive_operation_geometry(op, values, strategy, cores, ledger, tiling, collectives, arch)
            direct_scores[strategy, cores] = estimate_operation_geometry(geometry, ledger, arch)
            windows = tuple(row.window for row in ledger.retained_backings) + geometry.resident_windows
            peak = max(sum(window.required_extent_bytes for window in windows
                           if window.owner_core == core and window.first_event <= event <= window.last_event)
                       for event in {edge for window in windows for edge in (window.first_event, window.last_event)}
                       for core in arch.core_ids)
            assert direct_scores[strategy, cores].peak_sram_bytes == peak
    peaks = {row.peak_sram_bytes for row in direct_scores.values()}
    assert len(peaks) > 1
    if limited:
        arch.sram_bytes = min(peaks)
    evaluator = evaluation.PlacementGeometryEvaluator(op, values, ledger, tiling, collectives, arch)
    calls = []
    derive = evaluation.derive_operation_geometry

    def observed(*args):
        calls.append((args[2], args[3]))
        return derive(*args)

    monkeypatch.setattr(evaluation, 'derive_operation_geometry', observed)
    accepted = set()
    for (strategy, cores), score in direct_scores.items():
        geometry = derive_operation_geometry(op, values, strategy, cores, ledger, tiling, collectives, arch)
        expected = estimate_operation_geometry(geometry, ledger, arch)
        actual = evaluator.estimate(strategy, cores)
        assert actual == expected
        accepted.add(not isinstance(actual, PlacementRejection))
        if not limited:
            assert actual == score
    assert accepted == ({False, True} if limited else {True})
    assert len(calls) == len(strategies)


def test_route_rejection_precedes_resident_sum_overflow():
    from mesh_ir.canonical import U64_MAX

    arch = load_arch(ARCH_PATH)
    values = PlacementValues((_value(1, 'input', TensorRole.INPUT, DType.FP32, (8,)),
                              _value(2, 'output', TensorRole.OUTPUT, DType.FP32, (8,))))
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), 'relu:overflow')
    function = GraphFunction(1, 'forward', (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    ledger = create_placement_ledger(function, values.values)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, arch.core_ids[:1],
                 ledger, Tiling(2, 8, 8, True), Collectives('tree', 32), arch)
    geometry = replace(geometry, resident_windows=tuple(
        replace(row, required_extent_bytes=U64_MAX - 63) for row in geometry.resident_windows))
    memory = arch.fabric.targets[0]
    arch.fabric = replace(arch.fabric, targets=(replace(memory, ranges=tuple(
        replace(row, access=Access.READ_ONLY) for row in memory.ranges)), *arch.fabric.targets[1:]))
    result = estimate_operation_geometry(geometry, ledger, arch)
    assert isinstance(result, PlacementRejection)
    assert result.message == 'placement transfer has no legal target range'


def test_planner_evaluates_all_candidates_and_retains_selected_core_alternatives(monkeypatch, caplog):
    import yaml
    from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
    from mesh_ir.ir.graph_ir import GraphModule
    from mesh_ir.passes import placement
    from mesh_ir.passes.execution import PassExecutor
    from mesh_ir.passes.planning_prefix import plan_graphs_with_executor

    arch = load_arch(ARCH_PATH)
    values = (_value(1, 'input', TensorRole.INPUT, DType.FP32, (3, 16)),
              _value(2, 'weight', TensorRole.WEIGHT, DType.FP32, (16, 24), content='b' * 64),
              _value(3, 'activation', TensorRole.ACTIVATION, DType.FP32, (3, 16)),
              _value(4, 'output', TensorRole.OUTPUT, DType.FP32, (3, 24)))
    ops = (GraphOp(1, OpCode.RELU, (1,), (3,), ElementwiseAttrs(), 'relu:pin'),
           GraphOp(2, OpCode.MATMUL, (3, 2), (4,), MatmulAttrs(), 'matmul:score'))
    function = GraphFunction(1, 'forward', (1,), ops, (4,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), 'd' * 64, 'forward', 'p1', values, (function,))
    raw = dict(schema_version='mesh-compile-v1', entrypoints=['forward'],
               shape_profiles=dict(forward=[dict(profile_id='p1')]), symbol_bindings={},
               parallelism=dict(tensor_parallel=2, pipeline_parallel=1, expert_parallel=1, data_parallel=1),
               placement=dict(core_order='row_major_yx', allowed_cores=list(arch.core_ids), reserve_cores=[]),
               tiling=dict(gemm_m=2, gemm_n=8, gemm_k=8, double_buffer=True),
               collectives=dict(all_reduce_algorithm='ring', chunk_bytes=32),
               runtime_model=dict(mode='FULL_TIMING', tensor_data='DIGEST_ONLY'))
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(raw), arch), arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
    with caplog.at_level('INFO', logger='mesh_ir.passes.placement'):
        actual = placement.place_operations(planning, arch, effective)
    messages = [row.getMessage() for row in caplog.records if row.name == 'mesh_ir.passes.placement']
    assert len(messages) == 4
    assert 'start' in messages[0] and 'op=1/2' in messages[0] and 'op_id=1' in messages[0]
    assert 'complete' in messages[-1] and 'op=2/2' in messages[-1] and 'candidates=18' in messages[-1]

    class DirectEvaluator(evaluation.PlacementGeometryEvaluator):
        def estimate(self, strategy, core_ids):
            geometry = derive_operation_geometry(self.op, self.values, strategy, core_ids,
                self.ledger, self.tiling, self.collectives, self.arch)
            return (geometry if type(geometry) is PlacementRejection else
                    estimate_operation_geometry(geometry, self.ledger, self.arch))

    monkeypatch.setattr(placement, 'PlacementGeometryEvaluator', DirectEvaluator)
    expected = placement.place_operations(planning, arch, effective)
    assert actual == expected
    assert actual.candidates_evaluated == 2 * 6 * 3
    assert len(actual.candidates) == 2 * 3
    assert all(candidate.core_ids == decision.core_ids for decision in actual.placements
               for candidate in actual.candidates if candidate.identity == decision.identity)


@pytest.mark.parametrize('view', [False, True])
def test_preserve_and_view_rejections_remain_bound_to_operand_owners(view):
    arch = load_arch(ARCH_PATH)
    values = PlacementValues((_value(1, 'input', TensorRole.INPUT, DType.FP32, (4, 8)),
              _value(2, 'activation', TensorRole.ACTIVATION, DType.FP32, (4, 8)),
              _value(3, 'output', TensorRole.OUTPUT, DType.FP32, (4, 8), alias_root=2 if view else None)))
    first = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), 'relu:pin')
    op = GraphOp(2, OpCode.RESHAPE_VIEW if view else OpCode.RELU, (2,), (3,),
                 ViewAttrs(shape=values.values[2].shape) if view else ElementwiseAttrs(), 'next')
    function = GraphFunction(1, 'forward', (1,), (first, op), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    tiling, collectives = Tiling(2, 8, 8, True), Collectives('tree', 32)
    ledger = create_placement_ledger(function, values.values)
    geometry = derive_operation_geometry(first, values, ShardingStrategy.OUTER_DIM, arch.core_ids[:2],
                                         ledger, tiling, collectives, arch)
    ledger = advance_placement_ledger(ledger, geometry)
    evaluator = evaluation.PlacementGeometryEvaluator(op, values, ledger, tiling, collectives, arch)
    for cores in itertools.combinations(arch.core_ids, 2):
        for strategy in (ShardingStrategy.PRESERVE, ShardingStrategy.OUTER_DIM, ShardingStrategy.REPLICATE):
            geometry = derive_operation_geometry(op, values, strategy, cores, ledger, tiling, collectives, arch)
            expected = geometry if type(geometry) is PlacementRejection else estimate_operation_geometry(geometry, ledger, arch)
            assert evaluator.estimate(strategy, cores) == expected

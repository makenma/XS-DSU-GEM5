import itertools
import os
from pathlib import Path

import pytest
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import Collectives, Tiling, load_compile_config_text, resolve_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DType, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, GraphFunction, GraphModule, GraphOp, MatmulAttrs, OpCode, PytreeSpec, ViewAttrs
from mesh_ir.passes.execution import PassExecutor
from mesh_ir.passes.placement import place_operations
from mesh_ir.passes.placement_evaluation import PlacementGeometryEvaluator
from mesh_ir.passes.placement_geometry import PlacementValues, ShardingStrategy, advance_placement_ledger, create_placement_ledger, derive_operation_geometry
from mesh_ir.passes.planning_prefix import plan_graphs_with_executor
from tests.unit.test_gate2_placement_geometry import _value


ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize('workers', [2, 32])
@pytest.mark.parametrize('limited', [False, True])
def test_parallel_templates_preserve_all_candidate_estimates_and_compute_each_template_once(workers, limited):
    arch = load_arch(ROOT / 'configs/example/ai_mesh/arch/mesh_5x5.yaml')
    values = PlacementValues((
        _value(1, 'input', TensorRole.INPUT, DType.FP32, (3, 16)),
        _value(2, 'weight', TensorRole.WEIGHT, DType.FP32, (16, 24), content='b' * 64),
        _value(3, 'activation', TensorRole.ACTIVATION, DType.FP32, (3, 16)),
        _value(4, 'output', TensorRole.OUTPUT, DType.FP32, (3, 24))))
    first = GraphOp(1, OpCode.RELU, (1,), (3,), ElementwiseAttrs(), 'relu:pin')
    op = GraphOp(2, OpCode.MATMUL, (3, 2), (4,), MatmulAttrs(), 'matmul:search')
    function = GraphFunction(1, 'forward', (1,), (first, op), (4,), PytreeSpec.leaf(), PytreeSpec.leaf())
    tiling, collectives = Tiling(2, 8, 8, True), Collectives('ring', 32)
    ledger = create_placement_ledger(function, values.values)
    geometry = derive_operation_geometry(first, values, ShardingStrategy.OUTER_DIM,
        arch.core_ids[:4], ledger, tiling, collectives, arch)
    ledger = advance_placement_ledger(ledger, geometry)
    if limited:
        arch.sram_bytes = 128
    cores = tuple(itertools.combinations(arch.core_ids, 4))
    strategies = (ShardingStrategy.MATRIX_M, ShardingStrategy.MATRIX_N, ShardingStrategy.MATRIX_K_SUM)
    serial = PlacementGeometryEvaluator(op, values, ledger, tiling, collectives, arch)
    expected = tuple(serial.estimate(strategy, group) for group in cores for strategy in strategies)
    parallel = PlacementGeometryEvaluator(op, values, ledger, tiling, collectives, arch)
    with PassExecutor(workers) as executor:
        parallel.prepare(strategies, cores, executor)
        diagnostics = executor.diagnostics
    assert tuple(parallel.estimate(strategy, group) for group in cores for strategy in strategies) == expected
    assert parallel.templates == serial.templates
    assert diagnostics.workers == workers
    assert diagnostics.submitted_tasks == diagnostics.completed_tasks == min(workers, len(serial.templates))
    assert all(item.pass_name == 'PlaceOpsAndTensors' and item.worker_pid != os.getpid()
        for item in diagnostics.tasks)
    assert len({item.worker_pid for item in diagnostics.tasks}) > 1


@pytest.mark.parametrize('workers', [2, 32])
@pytest.mark.parametrize('limited', [False, True])
def test_parallel_placement_matches_serial_decisions_and_precise_rejection_counts(workers, limited):
    arch = load_arch(ROOT / 'configs/example/ai_mesh/arch/mesh_2x2.yaml')
    if limited:
        arch.sram_bytes = 32
    values = (
        _value(1, 'input', TensorRole.INPUT, DType.FP32, (2, 4, 8)),
        _value(2, 'activation', TensorRole.ACTIVATION, DType.FP32, (2, 4, 8)),
        _value(3, 'reshaped', TensorRole.OUTPUT, DType.FP32, (2, 32), alias_root=2))
    ops = (GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), 'relu'),
        GraphOp(2, OpCode.RESHAPE_VIEW, (2,), (3,), ViewAttrs(shape=values[2].shape), 'reshape'))
    function = GraphFunction(1, 'forward', (1,), ops, (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), 'd' * 64, 'forward', 'p1', values, (function,))
    raw = dict(schema_version='mesh-compile-v1', entrypoints=['forward'],
        shape_profiles=dict(forward=[dict(profile_id='p1')]), symbol_bindings={},
        parallelism=dict(tensor_parallel=2, pipeline_parallel=1, expert_parallel=1, data_parallel=1),
        placement=dict(core_order='row_major_yx', allowed_cores=list(arch.core_ids), reserve_cores=[]),
        tiling=dict(gemm_m=2, gemm_n=8, gemm_k=8, double_buffer=True),
        collectives=dict(all_reduce_algorithm='ring', chunk_bytes=32),
        runtime_model=dict(mode='FULL_TIMING', tensor_data='DIGEST_ONLY'))
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(raw), arch), arch)
    with PassExecutor(workers) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        if limited:
            with pytest.raises(MeshIrError) as serial:
                place_operations(planning, arch, effective)
            with pytest.raises(MeshIrError) as parallel:
                place_operations(planning, arch, effective, executor=executor)
            assert (parallel.value.code, parallel.value.message, parallel.value.context) == (
                serial.value.code, serial.value.message, serial.value.context)
        else:
            assert place_operations(planning, arch, effective, executor=executor) == place_operations(planning, arch, effective)

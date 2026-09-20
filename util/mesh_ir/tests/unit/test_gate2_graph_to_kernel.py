from __future__ import annotations

import dataclasses
import math
import os
from pathlib import Path

import mesh_ir.ir.kernel_ir as kernel_ir
import mesh_ir.passes.sharding as sharding_ir
import mesh_ir.passes.tiling as tiling_ir
import mesh_ir.passes.collectives as collective_ir
import pytest

from mesh_ir.analysis.cost import RowWorkDomain
from mesh_ir.analysis.kernel_work import kernel_local_reduction_domain
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256, to_canonical
from mesh_ir.compile_config import Collectives, Tiling, load_compile_config_text, resolve_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Const, DType, DmaKind, FixedStride, Layout, MemorySpace, StorageClass, TensorRole, contiguous_strides
from mesh_ir.ir.graph_ir import ElementwiseAttrs, GraphFunction, GraphModule, GraphOp, GraphValue, MatmulAttrs, NormAttrs, OpCode, PytreeSpec, ViewAttrs
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor
from mesh_ir.passes.graph_to_kernel import _logical_regions_overlap, _regions_exactly_cover, lower_to_kernel, verify_kernel_correspondence
from mesh_ir.passes.planning_prefix import OperationIdentity, PlanningResult, PlanningState, graph_set_sha256, plan_graphs_with_executor
from mesh_ir.passes.placement import LoweringDecisions, PlacementCandidate, PlacementDecision, PlacementScore, ShardingStrategy, place_operations
from mesh_ir.passes.collective_geometry import TiledLogicalRegion
from mesh_ir.passes.placement_geometry import ComputeTileGeometry, OperandDistribution, OperationPlacementGeometry, RankShardGeometry, ResidentWindowRole, ValueDistribution, create_placement_ledger, derive_operation_geometry
from mesh_ir.passes.sharding import shard_and_pad
from mesh_ir.passes.tiling import tile_kernels
from mesh_ir.passes.bufferize import bufferize_and_alias
from mesh_ir.passes.collectives import lower_collectives
from mesh_ir.passes.movement import insert_data_movement
from mesh_ir.ir.kernel_verify import verify_scheduled_ready_kernel
from tests.golden.support.compiler_fixtures import build_transposed_bmm_graph


ROOT = Path(__file__).resolve().parents[4]
COMPILE = """
schema_version: mesh-compile-v1
entrypoints: [forward]
shape_profiles:
  forward:
    - {profile_id: p1}
symbol_bindings: {}
parallelism: {tensor_parallel: 1, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}
placement: {core_order: row_major_yx, allowed_cores: [7, 2], reserve_cores: []}
tiling: {gemm_m: 2, gemm_n: 2, gemm_k: 2, double_buffer: true}
collectives: {all_reduce_algorithm: ring, chunk_bytes: 7}
runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}
"""


def test_output_region_cover_accepts_strided_partitions_without_enumeration():
    extent = 1 << 40
    even = kernel_ir.ElementRegion((0,), (extent // 2,), (2,))
    odd = kernel_ir.ElementRegion((1,), (extent // 2,), (2,))
    assert not _logical_regions_overlap(even, odd)
    assert _regions_exactly_cover((extent,), (even, odd))
    assert _regions_exactly_cover((), (kernel_ir.ElementRegion((), (), ()),))
    assert _regions_exactly_cover((0,), ())


def test_output_region_overlap_uses_finite_progression_intersections():
    left = kernel_ir.ElementRegion((0,), (2,), (4,))
    disjoint = kernel_ir.ElementRegion((2,), (2,), (3,))
    intersecting = kernel_ir.ElementRegion((1,), (2,), (3,))
    assert not _logical_regions_overlap(left, disjoint)
    assert _logical_regions_overlap(left, intersecting)
    assert not _regions_exactly_cover((6,), (kernel_ir.ElementRegion((0,), (4,), (1,)), kernel_ir.ElementRegion((2,), (2,), (1,))))
    assert not _regions_exactly_cover((6,), (kernel_ir.ElementRegion((0,), (2,), (1,)), kernel_ir.ElementRegion((4,), (2,), (1,))))


def test_logical_tensor_owns_graph_alias_semantics_not_consumer_layout():
    tensor = kernel_ir.KernelTensor(
        tensor_id=2,
        producer_computation_id=0,
        synthesized_purpose=None,
        alias_root_tensor_id=1,
        storage_offset_elements=0,
        name="reshaped",
        role=TensorRole.ACTIVATION,
        dtype=DType.FP16,
        shape=(6, 4),
        strides=(4, 1),
        storage_class=StorageClass.CORE_SRAM,
        access=Access.READ_ONLY,
        logical_extent_bytes=48,
        storage_extent_bytes=48,
        content_sha256=None,
    )
    assert tensor.alias_root_tensor_id == 1
    assert not hasattr(tensor, "layout")


def test_kernel_memory_records_are_source_neutral_and_lineage_is_an_envelope():
    tensor_fields = tuple(item.name for item in kernel_ir.dataclasses.fields(kernel_ir.KernelTensor))
    op_fields = tuple(item.name for item in kernel_ir.dataclasses.fields(kernel_ir.KernelOp))
    partial_fields = tuple(item.name for item in kernel_ir.dataclasses.fields(kernel_ir.PartialSumDefinition))
    assert "producer_computation_id" in tensor_fields
    assert not {"source_value_id", "source_op_id", "provenance"} & set(tensor_fields)
    assert op_fields == (
        "op_id",
        "computation_id",
        "stable_key",
        "opcode",
        "owner_core",
        "result_shard_id",
        "reads",
        "writes",
        "attrs",
        "after_tokens",
        "done_token",
    )
    assert not {"source_op_id", "source_stable_id"} & set(op_fields)
    assert "computation_id" in partial_fields and "source_op_id" not in partial_fields
    assert tuple(item.name for item in kernel_ir.dataclasses.fields(kernel_ir.KernelComputation)) == (
        "computation_id",
        "opcode",
        "operand_tensor_ids",
        "result_tensor_id",
        "attrs",
    )
    assert tuple(item.name for item in kernel_ir.dataclasses.fields(kernel_ir.KernelTensorLineage)) == ("tensor_id", "source_value_id")
    assert tuple(item.name for item in kernel_ir.dataclasses.fields(kernel_ir.KernelComputationLineage)) == ("computation_id", "source_op_id", "source_node_id")


def test_two_physical_slots_own_three_generation_windows():
    objects = (
        kernel_ir.BufferObject(1, 1, 7, MemorySpace.CORE_SRAM, (4, 4), (4, 1), 32, 64, False, 0),
        kernel_ir.BufferObject(2, 1, 7, MemorySpace.CORE_SRAM, (4, 4), (4, 1), 32, 64, False, 1),
    )
    views = (
        kernel_ir.BufferView(1, 1, 1, (0, 0), (4, 4), (4, 4), 0, (4, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 0),
        kernel_ir.BufferView(2, 2, 1, (0, 4), (4, 4), (4, 4), 0, (4, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 1),
        kernel_ir.BufferView(3, 1, 1, (0, 8), (4, 4), (4, 2), 0, (4, 1), Layout.CONTIGUOUS_ROW_MAJOR, None, 2),
    )
    assert tuple(item.buffer_index for item in objects) == (0, 1)
    assert tuple(item.generation for item in views) == (0, 1, 2)
    assert views[0].object_id == views[2].object_id


def test_view_domain_region_does_not_restate_physical_stride():
    region = kernel_ir.ElementRegion(origin=(1, 0), shape=(2, 3), steps=(1, 1))
    assert region.origin == (1, 0)
    assert not hasattr(region, "offset_elements")
    assert not hasattr(region, "strides")


def test_matrix_micro_phases_and_epilogue_are_closed_records():
    assert tuple(item.name for item in kernel_ir.MatrixPhase) == (
        "DIRECT",
        "ACCUMULATE_ONLY",
        "ACCUMULATE_FIRST",
        "ACCUMULATE_CONTINUE",
        "ACCUMULATE_FINAL",
    )
    assert kernel_ir.KernelOpcode.MATRIX_EPILOGUE.value == "MATRIX_EPILOGUE"


def test_sharding_stage_owns_distribution_instance_and_occurrence_id_maps():
    assert tuple(item.name for item in dataclasses.fields(sharding_ir.PlannedValueDistribution)) == (
        "placement_id",
        "shard_ids",
        "geometry",
    )
    assert tuple(item.name for item in dataclasses.fields(sharding_ir.PlannedOperationSharding)) == (
        "identity",
        "operand_placement_ids",
        "established_operand_placement_ids",
        "result_placement_id",
        "partial_sum_id",
    )
    assert tuple(item.name for item in dataclasses.fields(sharding_ir.VariantSharding)) == (
        "entrypoint",
        "profile_id",
        "distributions",
        "operations",
    )
    assert tuple(item.name for item in dataclasses.fields(tiling_ir.PlannedKernelTile)) == (
        "tile_id",
        "identity",
        "geometry",
    )
    assert tuple(item.name for item in dataclasses.fields(collective_ir.CollectiveChunk)) == ("chunk_id", "partial_sum_id", "geometry")
    assert tuple(item.name for item in dataclasses.fields(collective_ir.CollectiveTransfer)) == ("transfer_id", "partial_sum_id", "chunk_id", "geometry")


def test_sharding_projects_selected_resharding_instances_without_rederiving_geometry():
    shape = (Const(4),)
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1, content_sha256="a" * 64)
    intermediate = GraphValue(2, "first", TensorRole.ACTIVATION, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    output = GraphValue(3, "second", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 3)
    ops = (
        GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:1"),
        GraphOp(2, OpCode.RELU, (2,), (3,), ElementwiseAttrs(), "relu:2"),
    )
    function = GraphFunction(1, "forward", (1,), ops, (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create("1" * 64, "2" * 64, "forward", "p1", (source, intermediate, output), (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    cores = (7, 2)

    def replicated(value_id):
        return ValueDistribution(
            value_id,
            kernel_ir.DistributionKind.REPLICATED,
            None,
            cores,
            tuple(RankShardGeometry(rank, core, (0,), (4,), (4,)) for rank, core in enumerate(cores)),
        )

    def partitioned(value_id):
        return ValueDistribution(
            value_id,
            kernel_ir.DistributionKind.PARTITIONED,
            0,
            cores,
            tuple(RankShardGeometry(rank, core, (rank * 2,), (2,), (2,)) for rank, core in enumerate(cores)),
        )

    input_distribution = replicated(1)
    first_result = partitioned(2)
    second_operand = replicated(2)
    second_result = replicated(3)
    geometries = (
        OperationPlacementGeometry(0, (OperandDistribution(0, 1, None, input_distribution, ()),), (), first_result, (), (), (), (), (), ()),
        OperationPlacementGeometry(1, (OperandDistribution(0, 2, first_result, second_operand, ()),), (), second_result, (), (), (), (), (), ()),
    )
    identities = tuple(OperationIdentity("forward", "p1", 1, op.op_id) for op in ops)
    candidates = tuple(
        PlacementCandidate(identity, 0, strategy, cores, PlacementScore(0, 0, 0), None, geometry)
        for identity, strategy, geometry in zip(identities, (ShardingStrategy.OUTER_DIM, ShardingStrategy.REPLICATE), geometries)
    )
    decisions = LoweringDecisions(
        candidates,
        tuple(PlacementDecision(identity, candidate.strategy, cores, index) for index, (identity, candidate) in enumerate(zip(identities, candidates))),
    )
    state = shard_and_pad(planning, decisions)
    variant = state.variants[0]
    assert tuple((item.placement_id, item.shard_ids, item.geometry) for item in variant.distributions) == (
        (1, (1, 2), input_distribution),
        (2, (3, 4), first_result),
        (3, (5, 6), second_operand),
        (4, (7, 8), second_result),
    )
    assert tuple((item.operand_placement_ids, item.established_operand_placement_ids, item.result_placement_id, item.partial_sum_id) for item in variant.operations) == (
        ((1,), (0,), 2, 0),
        ((3,), (2,), 4, 0),
    )
    assert state.semantic_bytes() == shard_and_pad(planning, decisions).semantic_bytes()


def test_sharding_rejects_selected_nonuniform_partition_geometry():
    shape = (Const(4),)
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1, content_sha256="a" * 64)
    output = GraphValue(2, "relu", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:1")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create("1" * 64, "2" * 64, "forward", "p1", (source, output), (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    cores = (7, 2)
    valid = ValueDistribution(1, kernel_ir.DistributionKind.REPLICATED, None, cores, (RankShardGeometry(0, 7, (0,), (4,), (4,)), RankShardGeometry(1, 2, (0,), (4,), (4,))))
    invalid = ValueDistribution(2, kernel_ir.DistributionKind.PARTITIONED, 0, cores, (RankShardGeometry(0, 7, (0,), (3,), (3,)), RankShardGeometry(1, 2, (3,), (3,), (1,))))
    geometry = OperationPlacementGeometry(0, (OperandDistribution(0, 1, None, valid, ()),), (), invalid, (), (), (), (), (), ())
    identity = OperationIdentity("forward", "p1", 1, 1)
    candidate = PlacementCandidate(identity, 0, ShardingStrategy.OUTER_DIM, cores, PlacementScore(0, 0, 0), None, geometry)
    decisions = LoweringDecisions((candidate,), (PlacementDecision(identity, ShardingStrategy.OUTER_DIM, cores, 0),))
    with pytest.raises(MeshIrError, match="exact uniform logical cover"):
        shard_and_pad(planning, decisions)


def test_sharding_preserves_duplicate_operand_occurrences():
    shape = (Const(4),)
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1, content_sha256="a" * 64)
    output = GraphValue(2, "sum", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    op = GraphOp(1, OpCode.ADD, (1, 1), (2,), ElementwiseAttrs(), "add:1")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create("1" * 64, "2" * 64, "forward", "p1", (source, output), (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    source_distribution = ValueDistribution(1, kernel_ir.DistributionKind.PARTITIONED, None, (7,), (RankShardGeometry(0, 7, (0,), (4,), (4,)),))
    result_distribution = ValueDistribution(2, kernel_ir.DistributionKind.PARTITIONED, None, (7,), (RankShardGeometry(0, 7, (0,), (4,), (4,)),))
    operands = (
        OperandDistribution(0, 1, None, source_distribution, ()),
        OperandDistribution(1, 1, None, source_distribution, ()),
    )
    geometry = OperationPlacementGeometry(0, operands, (), result_distribution, (), (), (), (), (), ())
    identity = OperationIdentity("forward", "p1", 1, 1)
    candidate = PlacementCandidate(identity, 0, ShardingStrategy.SINGLE, (7,), PlacementScore(0, 0, 0), None, geometry)
    decisions = LoweringDecisions((candidate,), (PlacementDecision(identity, ShardingStrategy.SINGLE, (7,), 0),))
    operation = shard_and_pad(planning, decisions).variants[0].operations[0]
    assert operation.operand_placement_ids == (1, 1)
    assert operation.established_operand_placement_ids == (0, 0)


def test_sharding_keeps_k_sum_partial_obligation_off_semantic_distribution():
    lhs_shape = (Const(2), Const(4))
    rhs_shape = (Const(4), Const(3))
    result_shape = (Const(2), Const(3))
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1, content_sha256="a" * 64)
    rhs = GraphValue(2, "rhs", TensorRole.INPUT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 2, content_sha256="b" * 64)
    result = GraphValue(3, "result", TensorRole.OUTPUT, DType.FP32, result_shape, contiguous_strides(result_shape), 0, 3)
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:1")
    function = GraphFunction(1, "forward", (1, 2), (op,), (3,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create("1" * 64, "2" * 64, "forward", "p1", (lhs, rhs, result), (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    cores = (7, 2)
    lhs_distribution = ValueDistribution(1, kernel_ir.DistributionKind.PARTITIONED, 1, cores, (RankShardGeometry(0, 7, (0, 0), (2, 2), (2, 2)), RankShardGeometry(1, 2, (0, 2), (2, 2), (2, 2))))
    rhs_distribution = ValueDistribution(2, kernel_ir.DistributionKind.PARTITIONED, 0, cores, (RankShardGeometry(0, 7, (0, 0), (2, 3), (2, 3)), RankShardGeometry(1, 2, (2, 0), (2, 3), (2, 3))))
    result_distribution = ValueDistribution(3, kernel_ir.DistributionKind.REPLICATED, None, cores, (RankShardGeometry(0, 7, (0, 0), (2, 3), (2, 3)), RankShardGeometry(1, 2, (0, 0), (2, 3), (2, 3))))
    output_tile = TiledLogicalRegion((0, 0), (2, 3), (2, 3))
    compute_tiles = (
        ComputeTileGeometry(0, 7, 0, kernel_ir.KernelTile(0, 0, 0, 0, 1, 2, 3, 2, 1, 2, 3, 2), kernel_ir.MatrixPhase.ACCUMULATE_ONLY),
        ComputeTileGeometry(1, 2, 0, kernel_ir.KernelTile(0, 0, 0, 2, 1, 2, 3, 2, 1, 2, 3, 2), kernel_ir.MatrixPhase.ACCUMULATE_ONLY),
    )
    geometry = OperationPlacementGeometry(0, (OperandDistribution(0, 1, None, lhs_distribution, ()), OperandDistribution(1, 2, None, rhs_distribution, ())), (), result_distribution, (output_tile,), compute_tiles, (), (), (), ())
    identity = OperationIdentity("forward", "p1", 1, 1)
    candidate = PlacementCandidate(identity, 0, ShardingStrategy.MATRIX_K_SUM, cores, PlacementScore(0, 0, 0), None, geometry)
    decisions = LoweringDecisions((candidate,), (PlacementDecision(identity, ShardingStrategy.MATRIX_K_SUM, cores, 0),))
    variant = shard_and_pad(planning, decisions).variants[0]
    assert variant.operations[0].partial_sum_id == 1
    assert all(item.geometry.distribution is not kernel_ir.DistributionKind.PARTIAL_SUM for item in variant.distributions)
    tiled = tile_kernels(planning, sharding_ir.ShardingState(decisions, (variant,)))
    assert tuple((item.tile_id, item.identity, item.geometry) for item in tiled.tiles) == (
        (1, identity, compute_tiles[0]),
        (2, identity, compute_tiles[1]),
    )


def test_tiling_omits_metadata_compute_and_preserves_empty_completion_tile():
    source_shape = (Const(0), Const(1))
    flat_shape = (Const(0),)
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 1, content_sha256="a" * 64)
    flat = GraphValue(2, "flat", TensorRole.ACTIVATION, DType.FP32, flat_shape, contiguous_strides(flat_shape), 0, 1)
    output = GraphValue(3, "relu", TensorRole.OUTPUT, DType.FP32, flat_shape, contiguous_strides(flat_shape), 0, 3)
    ops = (
        GraphOp(1, OpCode.RESHAPE_VIEW, (1,), (2,), ViewAttrs(shape=flat_shape), "reshape:1"),
        GraphOp(2, OpCode.RELU, (2,), (3,), ElementwiseAttrs(), "relu:2"),
    )
    function = GraphFunction(1, "forward", (1,), ops, (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create("1" * 64, "2" * 64, "forward", "p1", (source, flat, output), (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    distributions = (
        ValueDistribution(1, kernel_ir.DistributionKind.PARTITIONED, None, (7,), (RankShardGeometry(0, 7, (0, 0), (0, 1), (0, 1)),)),
        ValueDistribution(2, kernel_ir.DistributionKind.PARTITIONED, None, (7,), (RankShardGeometry(0, 7, (0,), (0,), (0,)),)),
        ValueDistribution(3, kernel_ir.DistributionKind.PARTITIONED, None, (7,), (RankShardGeometry(0, 7, (0,), (0,), (0,)),)),
    )
    empty_tile = ComputeTileGeometry(0, 7, 0, kernel_ir.KernelTile(0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0), None)
    geometries = (
        OperationPlacementGeometry(0, (OperandDistribution(0, 1, None, distributions[0], ()),), (), distributions[1], (), (), (), (), (), ()),
        OperationPlacementGeometry(1, (OperandDistribution(0, 2, distributions[1], distributions[1], ()),), (), distributions[2], (TiledLogicalRegion((0,), (0,), (0,)),), (empty_tile,), (), (), (), ()),
    )
    identities = tuple(OperationIdentity("forward", "p1", 1, op.op_id) for op in ops)
    candidates = tuple(PlacementCandidate(identity, 0, ShardingStrategy.SINGLE, (7,), PlacementScore(0, 0, 0), None, geometry) for identity, geometry in zip(identities, geometries))
    decisions = LoweringDecisions(candidates, tuple(PlacementDecision(identity, ShardingStrategy.SINGLE, (7,), index) for index, identity in enumerate(identities)))
    shards = shard_and_pad(planning, decisions)
    tiled = tile_kernels(planning, shards)
    assert tuple((item.tile_id, item.identity, item.geometry) for item in tiled.tiles) == ((1, identities[1], empty_tile),)


def test_bufferization_uses_selected_two_slots_for_reloaded_generations():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    lhs_shape = (Const(1), Const(3))
    rhs_shape = (Const(3), Const(2))
    result_shape = (Const(1), Const(2))
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1, content_sha256="a" * 64)
    rhs = GraphValue(2, "rhs", TensorRole.WEIGHT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 2, content_sha256="b" * 64)
    result = GraphValue(3, "result", TensorRole.OUTPUT, DType.FP32, result_shape, contiguous_strides(result_shape), 0, 3)
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:stream")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "2" * 64, "forward", "p1", (lhs, rhs, result), (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    geometry = derive_operation_geometry(op, graph.values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, graph.values), Tiling(1, 1, 1, True), Collectives("ring", 16), arch)
    assert isinstance(geometry, OperationPlacementGeometry)
    identity = OperationIdentity("forward", "p1", 1, 1)
    candidate = PlacementCandidate(identity, 0, ShardingStrategy.SINGLE, (7,), PlacementScore(0, 0, 0), None, geometry)
    decisions = LoweringDecisions((candidate,), (PlacementDecision(identity, ShardingStrategy.SINGLE, (7,), 0),))
    tiled = tile_kernels(planning, shard_and_pad(planning, decisions))
    buffers = bufferize_and_alias(planning, tiled)
    variant = buffers.variants[0]
    lhs_window_ids = next(item.window_ids for item in geometry.operand_windows if item.operand_index == 0)
    lhs_objects = tuple(item for item in variant.objects if item.window_id in lhs_window_ids)
    assert len(lhs_objects) == 2
    assert tuple(item.buffer_index for item in lhs_objects) == (0, 1)
    uses = tuple(item for item in variant.window_uses if item.identity == identity and item.operand_index == 0)
    assert tuple(item.use_index for item in uses) == tuple(range(6))
    assert tuple(variant.objects[variant.views[item.access.view_id - 1].object_id - 1].window_id for item in uses) == tuple(item.window_id for item in geometry.operand_distributions[0].mappings[0].window_uses)
    assert len({item.access.view_id for item in uses}) == 6
    assert tuple(variant.views[item.access.view_id - 1].generation for item in uses) == (0, 0, 1, 1, 2, 2)
    accumulation = variant.tensors[-1]
    assert accumulation.tensor_id == 4
    assert accumulation.synthesized_purpose is kernel_ir.SynthesizedTensorPurpose.ACCUMULATION
    assert accumulation.producer_computation_id == 1
    assert variant.tensor_lineage[-1] == kernel_ir.KernelTensorLineage(4, 0)
    assert variant.computations == (kernel_ir.KernelComputation(1, OpCode.MATMUL, (1, 2), 3, op.attrs),)
    assert all(item.partial_sum_id == 0 for item in variant.shards)
    assert all(item.storage_tensor_id == accumulation.tensor_id for item in variant.objects if item.window_id and next(window for window in geometry.resident_windows if window.window_id == item.window_id).role is ResidentWindowRole.ACCUMULATOR)
    assert tuple(item.tile_id for item in variant.tile_buffers) == tuple(range(1, 7))
    assert all(len(item.operand_accesses) == 2 for item in variant.tile_buffers)
    assert tuple(variant.views[item.result_access.view_id - 1].generation for item in variant.tile_buffers) == (0, 0, 0, 1, 1, 1)
    assert all(variant.shards[item.result_shard_id - 1].tensor_id == accumulation.tensor_id for item in variant.tile_buffers)
    result_views = tuple(item for item in variant.result_views if item.role is ResidentWindowRole.SEMANTIC_RESULT)
    accumulator_views = tuple(item for item in variant.result_views if item.role is ResidentWindowRole.ACCUMULATOR)
    assert tuple((item.output_tile_index, item.owner_core, variant.views[item.access.view_id - 1].generation) for item in result_views) == ((0, 7, 0), (1, 7, 1))
    assert tuple((item.output_tile_index, item.owner_core, variant.views[item.access.view_id - 1].generation) for item in accumulator_views) == ((0, 7, 0), (1, 7, 1))


def test_bufferization_materializes_only_actual_double_buffer_generations_per_rank():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    shape = (Const(4), Const(2))
    source = GraphValue(1, "source", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1, content_sha256="a" * 64)
    result = GraphValue(2, "result", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:rank-slots")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "2" * 64, "forward", "p1", (source, result), (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    geometry = derive_operation_geometry(op, graph.values, ShardingStrategy.OUTER_DIM, (7, 2), create_placement_ledger(function, graph.values), Tiling(1, 1, 1, True), Collectives("ring", 16), arch)
    assert isinstance(geometry, OperationPlacementGeometry)
    identity = OperationIdentity("forward", "p1", 1, 1)
    candidate = PlacementCandidate(identity, 0, ShardingStrategy.OUTER_DIM, (7, 2), PlacementScore(0, 0, 0), None, geometry)
    decisions = LoweringDecisions((candidate,), (PlacementDecision(identity, ShardingStrategy.OUTER_DIM, (7, 2), 0),))
    buffers = bufferize_and_alias(planning, tile_kernels(planning, shard_and_pad(planning, decisions))).variants[0]
    assert all(len(item.window_ids) == 1 for item in geometry.operand_windows)
    used_objects = {item.object_id for item in buffers.views}
    assert all(item.object_id in used_objects for item in buffers.objects)


def test_empty_tp4_layernorm_uses_its_zero_extent_owner_shard_for_work():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    source_shape = (Const(2), Const(4), Const(8))
    parameter_shape = (Const(8),)
    source = GraphValue(1, "source", TensorRole.INPUT, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 1)
    weight = GraphValue(2, "weight", TensorRole.WEIGHT, DType.FP32, parameter_shape, contiguous_strides(parameter_shape), 0, 2, content_sha256="a" * 64)
    bias = GraphValue(3, "bias", TensorRole.WEIGHT, DType.FP32, parameter_shape, contiguous_strides(parameter_shape), 0, 3, content_sha256="b" * 64)
    result = GraphValue(4, "result", TensorRole.OUTPUT, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 4)
    op = GraphOp(1, OpCode.LAYERNORM, (1, 2, 3), (4,), NormAttrs((2,), 1e-5, True, True), "layernorm:empty-ranks")
    function = GraphFunction(1, "forward", (1,), (op,), (4,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "2" * 64, "forward", "p1", (source, weight, bias, result), (function,))
    text = COMPILE.replace("tensor_parallel: 1", "tensor_parallel: 4").replace("allowed_cores: [7, 2]", "allowed_cores: [7, 2, 9, 13]")
    effective = resolve_compile_config(load_compile_config_text(text, arch), arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        kernel = lower_to_kernel(planning, arch, effective, executor).bundle.modules[0]
    empty = tuple(item for item in kernel.ops if item.opcode is kernel_ir.KernelOpcode.NORM and item.owner_core in (9, 13))
    assert len(empty) == 2
    assert all(item.attrs.cost == kernel_ir.KernelCost(0, 0, 0, 0, 0, 0) for item in empty)
    kernel.verify(arch)


def test_bufferization_projects_shared_physical_generations_for_alias_occurrences():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    source_shape = (Const(1), Const(3))
    output_shape = (Const(1), Const(1))
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 1, content_sha256="a" * 64)
    output = GraphValue(2, "out", TensorRole.OUTPUT, DType.FP32, output_shape, contiguous_strides(output_shape), 0, 2)
    op = GraphOp(1, OpCode.MATMUL, (1, 1), (2,), MatmulAttrs(rhs_transpose=True), "matmul:1")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "2" * 64, "forward", "p1", (source, output), (function,))
    config = load_compile_config_text(COMPILE.replace("gemm_m: 2, gemm_n: 2, gemm_k: 2", "gemm_m: 1, gemm_n: 1, gemm_k: 1"), arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        placements = place_operations(planning, arch, effective)
        tiled = tile_kernels(planning, shard_and_pad(planning, placements))
        buffers = bufferize_and_alias(planning, tiled)
    variant = buffers.variants[0]
    identity = OperationIdentity("forward", "p1", 1, 1)
    first = tuple(item for item in variant.window_uses if item.identity == identity and item.operand_index == 0)
    second = tuple(item for item in variant.window_uses if item.identity == identity and item.operand_index == 1)
    assert len(first) == len(second) == 3
    assert tuple(variant.views[item.access.view_id - 1].object_id for item in first) == tuple(variant.views[item.access.view_id - 1].object_id for item in second)
    assert tuple(variant.views[item.access.view_id - 1].generation for item in first) == tuple(variant.views[item.access.view_id - 1].generation for item in second)
    assert tuple(item.access for item in first) == tuple(item.access for item in second)


def test_bufferization_maps_retained_producer_storage_subregions():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    lhs_shape = (Const(2), Const(3))
    rhs_shape = (Const(3), Const(2))
    result_shape = (Const(2), Const(2))
    source = GraphValue(1, "source", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1, content_sha256="a" * 64)
    retained = GraphValue(2, "retained", TensorRole.ACTIVATION, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 2)
    weight = GraphValue(3, "weight", TensorRole.INPUT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 3, content_sha256="b" * 64)
    result = GraphValue(4, "result", TensorRole.OUTPUT, DType.FP32, result_shape, contiguous_strides(result_shape), 0, 4)
    ops = (GraphOp(1, OpCode.ADD, (1, 1), (2,), ElementwiseAttrs(), "add:1"), GraphOp(2, OpCode.MATMUL, (2, 3), (4,), MatmulAttrs(), "matmul:2"))
    function = GraphFunction(1, "forward", (1, 3), ops, (4,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "2" * 64, "forward", "p1", (source, retained, weight, result), (function,))
    config = load_compile_config_text(COMPILE.replace("gemm_m: 2, gemm_n: 2, gemm_k: 2", "gemm_m: 1, gemm_n: 1, gemm_k: 1"), arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        decisions = place_operations(planning, arch, effective)
        buffers = bufferize_and_alias(planning, tile_kernels(planning, shard_and_pad(planning, decisions)))
    variant = buffers.variants[0]
    producer_geometry = decisions.candidates[decisions.placements[0].candidate_index].geometry
    retained_window = next(item for item in producer_geometry.resident_windows if item.role is ResidentWindowRole.SEMANTIC_RESULT)
    producer_view = next(item for item in variant.result_views if item.identity.op_id == 1 and item.role is ResidentWindowRole.SEMANTIC_RESULT)
    uses = tuple(item for item in variant.window_uses if item.identity.op_id == 2 and item.operand_index == 0)
    assert uses
    projected = tuple(variant.views[item.access.view_id - 1] for item in uses)
    producer_backing = variant.views[producer_view.access.view_id - 1]
    assert all(item.object_id == producer_backing.object_id for item in projected)
    assert tuple(item.object_offset_elements + sum(origin * stride for origin, stride in zip(use.access.region.origin, item.object_strides)) for use, item in zip(uses, projected)) == tuple(use.access.region.origin[0] * 3 + use.access.region.origin[1] for use in uses)
    assert all(item.object_strides == (3, 1) and item.generation == producer_backing.generation for item in projected)
    assert variant.objects[projected[0].object_id - 1].window_id == retained_window.window_id


def test_bufferization_maps_retained_result_tiles_and_alias_output_backing():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    lhs_shape = (Const(2), Const(3))
    rhs_shape = (Const(3), Const(2))
    result_shape = (Const(2), Const(2))
    flat_shape = (Const(4),)
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1, content_sha256="a" * 64)
    rhs = GraphValue(2, "rhs", TensorRole.INPUT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 2, content_sha256="b" * 64)
    retained = GraphValue(3, "retained", TensorRole.ACTIVATION, DType.FP32, result_shape, contiguous_strides(result_shape), 0, 3)
    activation = GraphValue(4, "activation", TensorRole.ACTIVATION, DType.FP32, result_shape, contiguous_strides(result_shape), 0, 4)
    output = GraphValue(5, "output", TensorRole.OUTPUT, DType.FP32, flat_shape, contiguous_strides(flat_shape), 0, 4)
    ops = (GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:1"), GraphOp(2, OpCode.RELU, (3,), (4,), ElementwiseAttrs(), "relu:2"), GraphOp(3, OpCode.RESHAPE_VIEW, (4,), (5,), ViewAttrs(shape=flat_shape), "reshape:3"))
    function = GraphFunction(1, "forward", (1, 2), ops, (5,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "2" * 64, "forward", "p1", (lhs, rhs, retained, activation, output), (function,))
    config = load_compile_config_text(COMPILE.replace("gemm_m: 2, gemm_n: 2, gemm_k: 2", "gemm_m: 1, gemm_n: 1, gemm_k: 1"), arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        decisions = place_operations(planning, arch, effective)
        buffers = bufferize_and_alias(planning, tile_kernels(planning, shard_and_pad(planning, decisions)))
    variant = buffers.variants[0]
    result_views = tuple(item for item in variant.result_views if item.identity.op_id == 1 and item.role is ResidentWindowRole.SEMANTIC_RESULT)
    assert tuple(variant.views[item.access.view_id - 1].object_offset_elements + sum(origin * stride for origin, stride in zip(item.access.region.origin, variant.views[item.access.view_id - 1].object_strides)) for item in result_views) == tuple(range(4))
    output_tensor_id = next(item.tensor_id for item in variant.tensor_lineage if item.source_value_id == 5)
    external = tuple(item for item in variant.views if item.tensor_id == output_tensor_id and variant.objects[item.object_id - 1].memory_space is MemorySpace.HBM)
    assert external and all(variant.objects[item.object_id - 1].storage_tensor_id == 4 for item in external)


def test_single_core_relu_runs_all_six_real_lowering_passes():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    shape = (Const(2), Const(4))
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1, content_sha256="c" * 64)
    output = GraphValue(2, "relu", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:1")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (source, output), (function,))
    config = load_compile_config_text(COMPILE, arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        result = lower_to_kernel(planning, arch, effective, executor)
        sharding = shard_and_pad(planning, result.decisions)
        collectives = lower_collectives(planning, bufferize_and_alias(planning, tile_kernels(planning, sharding)))
        decision = result.decisions.placements[0]
        invalid_decisions = dataclasses.replace(result.decisions, placements=(dataclasses.replace(decision, candidate_index=True),))
        invalid_sharding = dataclasses.replace(collectives.buffers.tiling.shards, decisions=invalid_decisions)
        invalid_tiling = dataclasses.replace(collectives.buffers.tiling, shards=invalid_sharding)
        invalid_buffers = dataclasses.replace(collectives.buffers, tiling=invalid_tiling)
        invalid_collectives = dataclasses.replace(collectives, buffers=invalid_buffers)
        submitted = executor.diagnostics.submitted_tasks
        with pytest.raises(MeshIrError, match="movement placement decision is invalid"):
            insert_data_movement(planning, invalid_collectives, executor)
        assert executor.diagnostics.submitted_tasks == submitted
    assert tuple(item.name for item in result.passes) == (
        "PlaceOpsAndTensors",
        "ShardAndPadTensors",
        "TileKernels",
        "BufferizeAndAlias",
        "LowerCollectives",
        "InsertDataMovement",
    )
    assert result.bundle.source_graph_set_sha256 == graph_set_sha256((graph,))
    assert result.decisions.placements[0].strategy is ShardingStrategy.SINGLE
    assert result.execution.submitted_tasks == result.execution.completed_tasks == planning.execution.completed_tasks + 1
    assert result.execution.tasks[-1].pass_name == "InsertDataMovement"
    assert result.execution.tasks[-1].worker_pid is not None and result.execution.tasks[-1].worker_pid != os.getpid()
    module = result.bundle.modules[0]
    module.verify(arch)
    assert tuple(op.opcode for op in module.ops if op.computation_id == 1)[-1] is kernel_ir.KernelOpcode.VECTOR
    assert any(op.opcode is kernel_ir.KernelOpcode.DMA for op in module.ops)
    verify_kernel_correspondence(graph, module)
    changed_tensor = dataclasses.replace(module.tensors[0], content_sha256="d" * 64)
    changed_content = dataclasses.replace(module, tensors=(changed_tensor, *module.tensors[1:]))
    changed_content = dataclasses.replace(changed_content, semantic_sha256=semantic_sha256(changed_content.semantic_dict()))
    with pytest.raises(MeshIrError, match="Kernel logical tensor differs from Graph value"):
        verify_kernel_correspondence(graph, changed_content)
    changed_lineage = dataclasses.replace(module.computation_lineage[0], source_node_id="relu:other")
    changed_source = dataclasses.replace(module, computation_lineage=(changed_lineage,))
    changed_source = dataclasses.replace(changed_source, semantic_sha256=semantic_sha256(changed_source.semantic_dict()))
    with pytest.raises(MeshIrError, match="Kernel computation differs from Graph operation"):
        verify_kernel_correspondence(graph, changed_source)
    store = next(op for op in module.ops if op.opcode is kernel_ir.KernelOpcode.DMA and op.attrs.kind is DmaKind.STORE)
    for truncated_shape in ((1, 4), (0, 4)):
        reads = (dataclasses.replace(store.reads[0], region=dataclasses.replace(store.reads[0].region, shape=truncated_shape)),)
        writes = (dataclasses.replace(store.writes[0], region=dataclasses.replace(store.writes[0].region, shape=truncated_shape)),)
        truncated_store = dataclasses.replace(store, reads=reads, writes=writes)
        changed_ops = (*module.ops[: store.op_id - 1], truncated_store, *module.ops[store.op_id:])
        truncated = dataclasses.replace(module, ops=changed_ops)
        truncated = dataclasses.replace(truncated, semantic_sha256=semantic_sha256(truncated.semantic_dict()))
        with pytest.raises(MeshIrError, match="output store regions"):
            verify_kernel_correspondence(graph, truncated)


def test_intermediate_hash_boundary_preserves_complete_dataclass_records():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    shape = (Const(4), Const(4))
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1)
    rhs = GraphValue(2, "rhs", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    hidden = GraphValue(3, "hidden", TensorRole.ACTIVATION, DType.FP32, shape, contiguous_strides(shape), 0, 3)
    output = GraphValue(4, "output", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 4)
    ops = (
        GraphOp(1, OpCode.ADD, (1, 2), (3,), ElementwiseAttrs(), "add:1"),
        GraphOp(2, OpCode.RELU, (3,), (4,), ElementwiseAttrs(), "relu:2"),
    )
    function = GraphFunction(
        1,
        "forward",
        (1, 2),
        ops,
        (4,),
        PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())),
        PytreeSpec.leaf(),
    )
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (lhs, rhs, hidden, output), (function,))
    config = load_compile_config_text(
        COMPILE.replace("tensor_parallel: 1", "tensor_parallel: 2").replace(
            "allowed_cores: [7, 2]", "allowed_cores: [7, 2, 9, 13]"
        ),
        arch,
    )
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        result = lower_to_kernel(planning, arch, effective, executor)
        decisions = result.decisions
        shards = shard_and_pad(planning, decisions)
        tiling = tile_kernels(planning, shards)
        buffers = bufferize_and_alias(planning, tiling)
        collectives = lower_collectives(planning, buffers)
        recreated = kernel_ir.KernelBundle.create(
            arch.digest().hex(),
            graph_set_sha256((graph,)),
            insert_data_movement(planning, collectives, executor),
        )
    complete = to_canonical(dataclasses.asdict(decisions))
    direct = to_canonical(decisions)
    rejected_index = next(index for index, item in enumerate(decisions.candidates) if item.rejection is not None)
    accepted_index = next(index for index, item in enumerate(decisions.candidates) if item.rejection is None)
    assert complete["candidates"][rejected_index]["geometry"] is None
    assert "geometry" not in direct["candidates"][rejected_index]
    assert complete["candidates"][accepted_index]["rejection"] is None
    assert "rejection" not in direct["candidates"][accepted_index]
    assert complete["candidates"][accepted_index]["geometry"]["operand_distributions"][0]["established_distribution"] is None
    states = (decisions, shards, tiling, buffers, collectives)
    state_hashes = tuple(semantic_sha256(dataclasses.asdict(state)) for state in states)
    assert tuple(item.output_hash for item in result.passes[:5]) == state_hashes
    assert tuple(item.input_hash for item in result.passes[1:6]) == tuple(item.output_hash for item in result.passes[:5])
    assert result.passes[-1].output_hash == result.bundle.semantic_sha256 == recreated.semantic_sha256


@pytest.mark.parametrize("shape", ((), (Const(0),)))
def test_scalar_and_empty_outputs_have_exact_store_coverage(shape):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1, content_sha256="c" * 64)
    output = GraphValue(2, "relu", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:1")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (source, output), (function,))
    effective = resolve_compile_config(load_compile_config_text(COMPILE, arch), arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        module = lower_to_kernel(planning, arch, effective, executor).bundle.modules[0]
    stores = tuple(op for op in module.ops if op.opcode is kernel_ir.KernelOpcode.DMA and op.attrs.kind is DmaKind.STORE)
    if shape:
        assert not stores or all(not math.prod(transition.region.shape) for store in stores for transition in store.writes)
    else:
        assert len(stores) == 1
        assert stores[0].writes[0].region.shape == ()
    verify_kernel_correspondence(graph, module)


def test_metadata_alias_output_store_covers_the_selected_alias_domain():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    source_shape = (Const(2), Const(4))
    output_shape = (Const(4), Const(2))
    left = GraphValue(1, "left", TensorRole.INPUT, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 1, content_sha256="c" * 64)
    right = GraphValue(2, "right", TensorRole.INPUT, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 2, content_sha256="d" * 64)
    summed = GraphValue(3, "summed", TensorRole.ACTIVATION, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 3)
    output = GraphValue(4, "reshaped", TensorRole.OUTPUT, DType.FP32, output_shape, contiguous_strides(output_shape), 0, 3)
    add = GraphOp(1, OpCode.ADD, (1, 2), (3,), ElementwiseAttrs(), "add:1")
    reshape = GraphOp(2, OpCode.RESHAPE_VIEW, (3,), (4,), ViewAttrs(shape=output_shape), "reshape:2")
    function = GraphFunction(1, "forward", (1, 2), (add, reshape), (4,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (left, right, summed, output), (function,))
    effective = resolve_compile_config(load_compile_config_text(COMPILE, arch), arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        module = lower_to_kernel(planning, arch, effective, executor).bundle.modules[0]
    assert {op.computation_id for op in module.ops if op.computation_id} == {1}
    assert tuple(op for op in module.ops if op.opcode is kernel_ir.KernelOpcode.DMA and op.attrs.kind is DmaKind.STORE)
    verify_kernel_correspondence(graph, module)


@pytest.fixture(scope="module")
def metadata_chain_lowering():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    flat_shape = (Const(4),)
    matrix_shape = (Const(2), Const(2))
    left = GraphValue(1, "left", TensorRole.INPUT, DType.FP32, flat_shape, contiguous_strides(flat_shape), 0, 1, content_sha256="c" * 64)
    right = GraphValue(2, "right", TensorRole.INPUT, DType.FP32, flat_shape, contiguous_strides(flat_shape), 0, 2, content_sha256="d" * 64)
    summed = GraphValue(3, "summed", TensorRole.ACTIVATION, DType.FP32, flat_shape, contiguous_strides(flat_shape), 0, 3)
    reshaped = GraphValue(4, "reshaped", TensorRole.ACTIVATION, DType.FP32, matrix_shape, contiguous_strides(matrix_shape), 0, 3)
    transposed = GraphValue(5, "transposed", TensorRole.ACTIVATION, DType.FP32, matrix_shape, (FixedStride(1), FixedStride(2)), 0, 3)
    output = GraphValue(6, "output", TensorRole.OUTPUT, DType.FP32, matrix_shape, contiguous_strides(matrix_shape), 0, 6)
    ops = (
        GraphOp(1, OpCode.ADD, (1, 2), (3,), ElementwiseAttrs(), "add:1"),
        GraphOp(2, OpCode.RESHAPE_VIEW, (3,), (4,), ViewAttrs(shape=matrix_shape), "reshape:2"),
        GraphOp(3, OpCode.TRANSPOSE_VIEW, (4,), (5,), ViewAttrs(permutation=(1, 0)), "transpose:3"),
        GraphOp(4, OpCode.RELU, (5,), (6,), ElementwiseAttrs(), "relu:4"),
    )
    function = GraphFunction(1, "forward", (1, 2), ops, (6,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (left, right, summed, reshaped, transposed, output), (function,))
    effective = resolve_compile_config(load_compile_config_text(COMPILE, arch), arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        module = lower_to_kernel(planning, arch, effective, executor).bundle.modules[0]
    return arch, graph, module


def test_internal_metadata_value_can_remain_unmapped_at_kernel_correspondence(metadata_chain_lowering):
    _, graph, module = metadata_chain_lowering
    lineage = {item.tensor_id: item.source_value_id for item in module.tensor_lineage}
    mapped = {
        lineage[module.shards[item.shard_id - 1].tensor_id]
        for item in module.views
        if lineage[module.shards[item.shard_id - 1].tensor_id]
    }
    assert {1, 2, 3, 5, 6} <= mapped
    assert 4 not in mapped
    verify_kernel_correspondence(graph, module)


@pytest.mark.parametrize("boundary", ("function_output", "materializing_operand"))
def test_unmapped_metadata_value_is_rejected_when_it_becomes_a_physical_boundary(metadata_chain_lowering, boundary):
    arch, graph, module = metadata_chain_lowering
    function = graph.functions[0]
    if boundary == "function_output":
        changed_function = dataclasses.replace(function, outputs=(4,))
    else:
        changed_function = dataclasses.replace(function, ops=(*function.ops[:3], dataclasses.replace(function.ops[3], operands=(4,))))
    changed_graph = GraphModule.create(
        graph.arch_digest,
        graph.source_semantic_hash,
        graph.entrypoint,
        graph.profile_id,
        graph.values,
        (changed_function,),
        graph.symbols,
        graph.profile_bindings,
        graph.decomposition_digest,
        graph.debug_locations,
    )
    changed_module = dataclasses.replace(module, source_semantic_hash=changed_graph.semantic_sha256)
    changed_module = dataclasses.replace(changed_module, semantic_sha256=semantic_sha256(changed_module.semantic_dict()))
    changed_graph.verify()
    changed_module.verify(arch)
    assert changed_graph.semantic_sha256 != graph.semantic_sha256
    assert changed_module.semantic_sha256 != module.semantic_sha256
    with pytest.raises(MeshIrError) as caught:
        verify_kernel_correspondence(changed_graph, changed_module)
    assert caught.value.code == "E_EXPORT_LAYOUT"
    assert caught.value.context["value_id"] == 4


def test_existing_metadata_view_affine_mapping_remains_strict(metadata_chain_lowering):
    arch, graph, module = metadata_chain_lowering
    lineage = {item.tensor_id: item.source_value_id for item in module.tensor_lineage}
    target = next(
        item
        for item in module.views
        if lineage[module.shards[item.shard_id - 1].tensor_id] == 5
        and module.objects[item.object_id - 1].memory_space is MemorySpace.CORE_SRAM
    )
    changed_view = dataclasses.replace(
        target,
        view_id=len(module.views) + 1,
        shard_origin=(0, 1),
        padded_shape=(2, 1),
        valid_shape=(2, 1),
    )
    declaration_boundary = len(module.objects) + len(module.views)
    source_declaration = module.ops[len(module.objects) + target.view_id - 1]
    changed_declaration = dataclasses.replace(
        source_declaration,
        op_id=declaration_boundary + 1,
        stable_key=f"view:{changed_view.view_id:08d}",
        attrs=dataclasses.replace(source_declaration.attrs, view_id=changed_view.view_id),
    )
    changed_effects = tuple(dataclasses.replace(item, op_id=item.op_id + 1) for item in module.ops[declaration_boundary:])
    changed_module = dataclasses.replace(
        module,
        views=(*module.views, changed_view),
        ops=(*module.ops[:declaration_boundary], changed_declaration, *changed_effects),
    )
    changed_module = dataclasses.replace(changed_module, semantic_sha256=semantic_sha256(changed_module.semantic_dict()))
    changed_module.verify(arch)
    with pytest.raises(MeshIrError) as caught:
        verify_kernel_correspondence(graph, changed_module)
    assert caught.value.code == "E_EXPORT_LAYOUT"
    assert caught.value.context["value_id"] == 5


def test_matrix_tiling_uses_requested_m_n_k_and_canonical_phase_order():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    lhs_shape = (Const(3), Const(4))
    rhs_shape = (Const(4), Const(5))
    out_shape = (Const(3), Const(5))
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1)
    rhs = GraphValue(2, "rhs", TensorRole.INPUT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 2)
    out = GraphValue(3, "out", TensorRole.OUTPUT, DType.FP32, out_shape, contiguous_strides(out_shape), 0, 3)
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:1")
    function = GraphFunction(1, "forward", (1, 2), (op,), (3,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (lhs, rhs, out), (function,))
    config = load_compile_config_text(COMPILE, arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        placements = place_operations(planning, arch, effective)
        sharding = shard_and_pad(planning, placements)
        tiling = tile_kernels(planning, sharding)
    assert len(tiling.tiles) == 12
    assert tuple((item.geometry.tile.m_origin, item.geometry.tile.n_origin, item.geometry.tile.k_origin) for item in tiling.tiles[:4]) == ((0, 0, 0), (0, 0, 2), (0, 2, 0), (0, 2, 2))
    assert tuple(item.geometry.matrix_phase for item in tiling.tiles[:2]) == (kernel_ir.MatrixPhase.ACCUMULATE_FIRST, kernel_ir.MatrixPhase.ACCUMULATE_FINAL)
    assert tiling.tiles[-1].geometry.tile.valid_m == 1
    assert tiling.tiles[-1].geometry.tile.valid_n == 1


def test_multi_k_matrix_lowers_accumulation_chain_and_one_epilogue_per_output_tile():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    lhs_shape = (Const(3), Const(4))
    rhs_shape = (Const(4), Const(5))
    out_shape = (Const(3), Const(5))
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1, content_sha256="a" * 64)
    rhs = GraphValue(2, "rhs", TensorRole.INPUT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 2, content_sha256="b" * 64)
    out = GraphValue(3, "out", TensorRole.OUTPUT, DType.FP32, out_shape, contiguous_strides(out_shape), 0, 3)
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:1")
    function = GraphFunction(1, "forward", (1, 2), (op,), (3,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (lhs, rhs, out), (function,))
    effective = resolve_compile_config(load_compile_config_text(COMPILE, arch), arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        lowered = lower_to_kernel(planning, arch, effective, executor)
    module = lowered.bundle.modules[0]
    matrix = tuple(item for item in module.ops if item.opcode is kernel_ir.KernelOpcode.GEMM)
    epilogues = tuple(item for item in module.ops if item.opcode is kernel_ir.KernelOpcode.MATRIX_EPILOGUE)
    assert len(matrix) == 12
    assert len(epilogues) == 6
    groups = {}
    for item in matrix:
        tile = item.attrs.tile
        groups.setdefault((tile.m_origin, tile.n_origin), []).append(item.attrs.phase)
    assert all(phases == [kernel_ir.MatrixPhase.ACCUMULATE_FIRST, kernel_ir.MatrixPhase.ACCUMULATE_FINAL] for phases in groups.values())
    assert all(item.result_shard_id != matrix[0].result_shard_id for item in epilogues)


def test_reshape_to_matrix_preserves_one_affine_storage_root():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    input_shape = (Const(2), Const(3), Const(4))
    matrix_shape = (Const(6), Const(4))
    weight_shape = (Const(4), Const(5))
    output_shape = (Const(6), Const(5))
    source = GraphValue(1, "source", TensorRole.INPUT, DType.FP32, input_shape, contiguous_strides(input_shape), 0, 1)
    matrix = GraphValue(2, "matrix", TensorRole.ACTIVATION, DType.FP32, matrix_shape, contiguous_strides(matrix_shape), 0, 1)
    weight = GraphValue(3, "weight", TensorRole.INPUT, DType.FP32, weight_shape, contiguous_strides(weight_shape), 0, 3)
    output = GraphValue(4, "output", TensorRole.OUTPUT, DType.FP32, output_shape, contiguous_strides(output_shape), 0, 4)
    reshape = GraphOp(1, OpCode.RESHAPE_VIEW, (1,), (2,), ViewAttrs(shape=matrix_shape), "reshape:1")
    matmul = GraphOp(2, OpCode.MATMUL, (2, 3), (4,), MatmulAttrs(), "matmul:2")
    function = GraphFunction(1, "forward", (1, 3), (reshape, matmul), (4,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (source, matrix, weight, output), (function,))
    config = load_compile_config_text(COMPILE.replace("gemm_m: 2, gemm_n: 2, gemm_k: 2", "gemm_m: 8, gemm_n: 8, gemm_k: 8"), arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        result = lower_to_kernel(planning, arch, effective, executor)
    kernel = result.bundle.modules[0]
    local = {kernel.shards[item.shard_id - 1].tensor_id: item for item in kernel.views if kernel.objects[item.object_id - 1].memory_space is MemorySpace.CORE_SRAM}
    assert local[2].object_strides == (4, 1)
    assert kernel.objects[local[2].object_id - 1].storage_tensor_id == 1
    assert tuple(item.computation_id for item in kernel.ops if item.opcode is kernel_ir.KernelOpcode.GEMM) == (2,)


def test_transpose_view_to_bmm_proves_per_batch_matrix_layout():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    graph = build_transposed_bmm_graph(arch)
    config = load_compile_config_text(COMPILE.replace("gemm_m: 2, gemm_n: 2, gemm_k: 2", "gemm_m: 8, gemm_n: 8, gemm_k: 8"), arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        placements = place_operations(planning, arch, effective)
        sharding = shard_and_pad(planning, placements)
        tiling = tile_kernels(planning, sharding)
        buffers = bufferize_and_alias(planning, tiling)
    variant = buffers.variants[0]
    tensor_id = next(item.tensor_id for item in variant.tensor_lineage if item.source_value_id == 2)
    local = next(item for item in variant.views if item.tensor_id == tensor_id and variant.objects[item.object_id - 1].memory_space is MemorySpace.CORE_SRAM)
    assert local.layout is Layout.TRANSPOSED_2D_VIEW
    assert local.object_strides == (12, 1, 4)
    assert variant.objects[local.object_id - 1].storage_tensor_id == 1


def test_actual_multi_output_matrix_selects_k_sharding_and_consumes_each_generation_before_reuse():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    lhs_shape = (Const(2), Const(16))
    rhs_shape = (Const(16), Const(32))
    out_shape = (Const(2), Const(32))
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1)
    rhs = GraphValue(2, "rhs", TensorRole.WEIGHT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 2, content_sha256="b" * 64)
    out = GraphValue(3, "out", TensorRole.OUTPUT, DType.FP32, out_shape, contiguous_strides(out_shape), 0, 3)
    bias = GraphValue(4, "bias", TensorRole.WEIGHT, DType.FP32, (Const(32),), (1,), 0, 4, content_sha256="c" * 64)
    op = GraphOp(1, OpCode.LINEAR_BIAS, (1, 2, 4), (3,), MatmulAttrs(), "linear:k")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (lhs, rhs, out, bias), (function,))
    text = COMPILE.replace("tensor_parallel: 1", "tensor_parallel: 2").replace("gemm_m: 2, gemm_n: 2, gemm_k: 2", "gemm_m: 3, gemm_n: 5, gemm_k: 7")
    config = load_compile_config_text(text, arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        placements = place_operations(planning, arch, effective)
        tiling = tile_kernels(planning, shard_and_pad(planning, placements))
        buffers = bufferize_and_alias(planning, tiling)
        kernel = insert_data_movement(planning, lower_collectives(planning, buffers), executor)[0]
    selected = placements.placements[0]
    assert selected.strategy is ShardingStrategy.MATRIX_K_SUM
    scores = {item.strategy: item.score.communication_bytes for item in placements.candidates if item.core_ids == selected.core_ids}
    assert scores[ShardingStrategy.MATRIX_K_SUM] < scores[ShardingStrategy.MATRIX_M]
    assert scores[ShardingStrategy.MATRIX_K_SUM] < scores[ShardingStrategy.MATRIX_N]
    generations = {}
    for item in kernel.views:
        if kernel.objects[item.object_id - 1].memory_space is MemorySpace.CORE_SRAM:
            generations.setdefault(item.object_id, set()).add(item.generation)
    for object_id, object_generations in generations.items():
        if len(object_generations) < 2:
            continue
        for generation in sorted(object_generations)[:-1]:
            readers = tuple(item.op_id for item in kernel.ops for access in item.reads if kernel.views[access.view_id - 1].object_id == object_id and kernel.views[access.view_id - 1].generation == generation)
            later_writers = tuple(item.op_id for item in kernel.ops for access in item.writes if kernel.views[access.view_id - 1].object_id == object_id and kernel.views[access.view_id - 1].generation > generation)
            if readers and later_writers:
                assert max(readers) < min(later_writers)
    kernel.verify(arch)


def test_k_sharded_nondivisible_chunks_use_real_shards_and_one_ring_traversal():
    arch = dataclasses.replace(load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml"), sram_bytes=1280)
    lhs_shape = (Const(4), Const(128))
    rhs_shape = (Const(128), Const(4))
    out_shape = (Const(4), Const(4))
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1)
    rhs = GraphValue(2, "rhs", TensorRole.WEIGHT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 2, content_sha256="b" * 64)
    out = GraphValue(3, "out", TensorRole.OUTPUT, DType.FP32, out_shape, contiguous_strides(out_shape), 0, 3)
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:ring")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (lhs, rhs, out), (function,))
    text = COMPILE.replace("tensor_parallel: 1", "tensor_parallel: 4").replace("allowed_cores: [7, 2]", "allowed_cores: [7, 2, 9, 13]").replace("gemm_m: 2, gemm_n: 2, gemm_k: 2", "gemm_m: 4, gemm_n: 4, gemm_k: 32").replace("chunk_bytes: 7", "chunk_bytes: 28")
    config = load_compile_config_text(text, arch)
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        placements = place_operations(planning, arch, effective)
        assert placements.placements[0].strategy is ShardingStrategy.MATRIX_K_SUM
        sharding = shard_and_pad(planning, placements)
        tiling = tile_kernels(planning, sharding)
        assert tuple(dict.fromkeys(item.geometry.owner_core for item in tiling.tiles)) == placements.placements[0].core_ids
        lhs_distribution = sharding.variants[0].distributions[0]
        for owner, shard in zip(placements.placements[0].core_ids, lhs_distribution.geometry.shards):
            owner_tiles = tuple(item for item in tiling.tiles if item.geometry.owner_core == owner)
            covered_k = {index for item in owner_tiles for index in range(item.geometry.tile.k_origin, item.geometry.tile.k_origin + item.geometry.tile.valid_k)}
            assert covered_k == set(range(shard.global_origin[-1], shard.global_origin[-1] + shard.valid_shape[-1]))
        buffers = bufferize_and_alias(planning, tiling)
        collectives = lower_collectives(planning, buffers)
    variant = buffers.variants[0]
    out_tensor_id = next(item.tensor_id for item in variant.tensor_lineage if item.source_value_id == 3)
    local_out = tuple(item for item in variant.views if item.tensor_id == out_tensor_id and variant.objects[item.object_id - 1].memory_space is MemorySpace.CORE_SRAM)
    assert tuple(item.shard_id for item in local_out) == (9, 10, 11, 12)
    collective = collectives.variants[0]
    nonempty = tuple(item for item in collective.chunks if item.geometry.element_count)
    for output_tile in dict.fromkeys(item.geometry.output_tile for item in nonempty):
        chunks = tuple(item for item in nonempty if item.geometry.output_tile == output_tile)
        covered = tuple(index for item in chunks for index in range(item.geometry.tile_flat_offset, item.geometry.tile_flat_offset + item.geometry.element_count))
        assert covered == tuple(range(math.prod(output_tile.valid_shape)))
    assert all(item.geometry.element_count <= 7 for item in nonempty)
    assert tuple(item.geometry.tile_flat_offset for item in collective.chunks) == tuple(sorted(item.geometry.tile_flat_offset for item in collective.chunks))
    for chunk in nonempty:
        transfers = tuple(item for item in collective.transfers if item.chunk_id == chunk.chunk_id)
        assert len(transfers) == 6
        assert sum(item.geometry.phase.value == "REDUCE" for item in transfers) == 3


def _selected_k_sum_kernel(algorithm="ring"):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    lhs_shape = (Const(3), Const(8))
    rhs_shape = (Const(8), Const(5))
    out_shape = (Const(3), Const(5))
    lhs = GraphValue(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_shape, contiguous_strides(lhs_shape), 0, 1)
    rhs = GraphValue(2, "rhs", TensorRole.WEIGHT, DType.FP32, rhs_shape, contiguous_strides(rhs_shape), 0, 2, content_sha256="b" * 64)
    out = GraphValue(3, "out", TensorRole.OUTPUT, DType.FP32, out_shape, contiguous_strides(out_shape), 0, 3)
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:ring")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (lhs, rhs, out), (function,))
    planning = PlanningResult((graph,), PlanningState((graph,), (), ()), (), ExecutionDiagnostics(1, 0, 0, ()))
    cores = (7, 2)
    geometry = derive_operation_geometry(op, graph.values, ShardingStrategy.MATRIX_K_SUM, cores, create_placement_ledger(function, graph.values), Tiling(3, 5, 2, True), Collectives(algorithm, 16), arch)
    assert type(geometry) is OperationPlacementGeometry
    identity = OperationIdentity("forward", "p1", 1, 1)
    candidate = PlacementCandidate(identity, 0, ShardingStrategy.MATRIX_K_SUM, cores, PlacementScore(0, 0, 0), None, geometry)
    decisions = LoweringDecisions((candidate,), (PlacementDecision(identity, ShardingStrategy.MATRIX_K_SUM, cores, 0),))
    sharding = shard_and_pad(planning, decisions)
    tiling = tile_kernels(planning, sharding)
    buffers = bufferize_and_alias(planning, tiling)
    collectives = lower_collectives(planning, buffers)
    with PassExecutor(1) as executor:
        kernel = insert_data_movement(planning, collectives, executor)[0]
    return arch, graph, kernel, collectives


@pytest.mark.parametrize("algorithm", ("ring", "tree"))
def test_selected_k_sum_emits_physical_reductions_and_completed_results(algorithm):
    arch, graph, kernel, collectives = _selected_k_sum_kernel(algorithm)
    sends = tuple(item for item in kernel.ops if item.opcode is kernel_ir.KernelOpcode.DMA and item.attrs.kind.name == "P2P_PUSH")
    reductions = tuple(item for item in kernel.ops if item.opcode is kernel_ir.KernelOpcode.LOCAL_REDUCE)
    waits = tuple(item for item in kernel.ops if item.opcode is kernel_ir.KernelOpcode.RECV_WAIT)
    assert len(sends) == len(collectives.variants[0].transfers)
    assert len(waits) == len(sends)
    send_by_id = {item.attrs.transfer_id: item for item in sends}
    wait_by_id = {item.attrs.transfer_id: item for item in waits}
    for transfer in collectives.variants[0].transfers:
        if transfer.geometry.phase.name != "PROPAGATE":
            continue
        send = send_by_id[transfer.transfer_id]
        wait = wait_by_id[transfer.transfer_id]
        targets = {
            (kernel.views[access.view_id - 1].object_id, kernel.views[access.view_id - 1].generation)
            for access in send.writes
        }
        consumers = tuple(
            item
            for item in kernel.ops[wait.op_id:]
            if any((kernel.views[access.view_id - 1].object_id, kernel.views[access.view_id - 1].generation) in targets for access in item.reads)
        )
        assert consumers
        assert all(wait.done_token in item.after_tokens for item in consumers)
    assert reductions
    cross_row = next(item for item in sends if len(item.reads) == 2)
    assert sum(math.prod(access.region.shape) for access in cross_row.reads) == 4
    assert len(cross_row.writes) == 2
    local = next(item for item in reductions if len(item.writes) == 2)
    assert len(local.reads) == 4
    assert kernel_local_reduction_domain(kernel.memory_records(), local.op_id) == RowWorkDomain(4, 2)
    phases = tuple(item.attrs.phase for item in kernel.ops if item.opcode is kernel_ir.KernelOpcode.GEMM)
    assert kernel_ir.MatrixPhase.ACCUMULATE_FIRST in phases
    assert kernel_ir.MatrixPhase.ACCUMULATE_FINAL in phases
    kernel.verify(arch)
    verify_kernel_correspondence(graph, kernel)
    verify_scheduled_ready_kernel(kernel, arch)


def test_selected_k_sum_rejects_repeated_contributor_with_missing_peer():
    arch, _, kernel, _ = _selected_k_sum_kernel()
    local = next(item for item in kernel.ops if item.opcode is kernel_ir.KernelOpcode.LOCAL_REDUCE)
    reads = (local.reads[0], local.reads[0], *local.reads[2:])
    changed_op = dataclasses.replace(local, reads=reads)
    changed = dataclasses.replace(kernel, ops=(*kernel.ops[: local.op_id - 1], changed_op, *kernel.ops[local.op_id:]))
    changed = dataclasses.replace(changed, semantic_sha256=semantic_sha256(changed.semantic_dict()))
    with pytest.raises(MeshIrError) as error:
        changed.verify(arch)
    assert error.value.code == "E_TENSOR_NOT_RESIDENT"
    assert "contributor" in error.value.message

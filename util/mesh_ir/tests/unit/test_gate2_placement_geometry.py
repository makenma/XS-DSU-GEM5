from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import Collectives, Tiling, load_compile_config_text, resolve_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Const, DType, TensorRole, contiguous_strides
from mesh_ir.ir.graph_ir import ElementwiseAttrs, GraphFunction, GraphModule, GraphOp, GraphValue, MatmulAttrs, OpCode, PytreeSpec, ViewAttrs
from mesh_ir.ir.kernel_ir import CollectiveAlgorithm, DistributionKind, KernelTile, MatrixPhase
from mesh_ir.passes.collective_geometry import CollectivePhase, TiledLogicalRegion, collective_transfer_geometry
from mesh_ir.passes.execution import PassExecutor
from mesh_ir.passes.placement import ShardingStrategy, place_operations
from mesh_ir.passes.placement_geometry import (
    DenseLogicalRegion,
    ExternalBindingKind,
    GeometryPhase,
    PlacementRejection,
    PlacementTransferKind,
    RegionSourceKind,
    ResidentWindowRole,
    WindowUseGeometry,
    advance_placement_ledger,
    create_placement_ledger,
    derive_operation_geometry,
    estimate_operation_geometry,
    operand_window_use_regions,
)
from mesh_ir.passes.planning_prefix import plan_graphs_with_executor


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml"
TILING = Tiling(3, 5, 7, True)
COLLECTIVES = Collectives("tree", 28)


def _value(value_id, name, role, dtype, extents, *, alias_root=None, content=None, access=Access.READ_ONLY):
    shape = tuple(Const(item) for item in extents)
    return GraphValue(
        value_id,
        name,
        role,
        dtype,
        shape,
        contiguous_strides(shape),
        0,
        value_id if alias_root is None else alias_root,
        access,
        content_sha256=content,
    )


def test_collective_geometry_preserves_chunks_tails_phases_and_empty_completion():
    tile = TiledLogicalRegion((0, 0), (2, 5), (2, 5))
    geometry = collective_transfer_geometry(3, 7, tile, DType.FP32, (7, 2, 9, 13), CollectiveAlgorithm.RING, 8)
    assert len(geometry.chunks) == 8
    assert tuple((item.tile_flat_offset, item.element_count) for item in geometry.chunks) == (
        (0, 2),
        (2, 1),
        (3, 2),
        (5, 1),
        (6, 2),
        (8, 1),
        (9, 1),
        (10, 0),
    )
    assert all(item.accumulation_dtype is DType.FP32 and item.output_tile is tile for item in geometry.chunks)
    assert len(geometry.transfers) == 42
    assert sum(item.phase is CollectivePhase.REDUCE for item in geometry.transfers) == 21
    assert all(item.chunk in geometry.chunks and item.chunk.element_count for item in geometry.transfers)

    empty = collective_transfer_geometry(
        3,
        7,
        TiledLogicalRegion((0, 0), (2, 5), (0, 5)),
        DType.FP32,
        (7, 2, 9, 13),
        CollectiveAlgorithm.TREE,
        8,
    )
    assert len(empty.chunks) == 4
    assert all(item.element_count == 0 for item in empty.chunks)
    assert empty.transfers == ()


def test_ring_collective_nondivisible_geometry_has_one_shared_240_byte_transfer_set():
    geometry = collective_transfer_geometry(3, 7, TiledLogicalRegion((0, 0), (2, 5), (2, 5)), DType.FP32, (7, 2, 9, 13), CollectiveAlgorithm.RING, 8)
    assert sum(item.chunk.element_count * item.chunk.accumulation_dtype.byte_width for item in geometry.transfers) == 240
    assert tuple((item.source_core, item.destination_core) for item in geometry.transfers[:3]) == ((7, 2), (2, 9), (9, 13))


def test_collective_geometry_requires_resolved_algorithm_and_one_element_budget():
    tile = TiledLogicalRegion((0, 0), (1, 1), (1, 1))
    with pytest.raises(MeshIrError, match="resolved"):
        collective_transfer_geometry(1, 1, tile, DType.FP32, (7, 2), CollectiveAlgorithm.AUTO, 8)
    with pytest.raises(MeshIrError, match="one accumulation element"):
        collective_transfer_geometry(1, 1, tile, DType.FP32, (7, 2), CollectiveAlgorithm.RING, 2)


def test_tree_collective_orders_reduce_by_descending_depth_then_rank():
    geometry = collective_transfer_geometry(1, 1, TiledLogicalRegion((0,), (1,), (1,)), DType.FP32, (7, 2, 9, 13), CollectiveAlgorithm.TREE, 16)
    assert tuple((item.source_core, item.destination_core, item.phase) for item in geometry.transfers) == (
        (13, 2, CollectivePhase.REDUCE),
        (2, 7, CollectivePhase.REDUCE),
        (9, 7, CollectivePhase.REDUCE),
        (7, 2, CollectivePhase.PROPAGATE),
        (7, 9, CollectivePhase.PROPAGATE),
        (2, 13, CollectivePhase.PROPAGATE),
    )


def test_ledger_recognizes_lifted_sources_and_retains_real_result_window():
    x = _value(1, "x", TensorRole.INPUT, DType.FP32, (4, 4))
    weight = _value(2, "weight", TensorRole.WEIGHT, DType.FP32, (4, 4), content="a" * 64)
    hidden = _value(3, "hidden", TensorRole.ACTIVATION, DType.FP32, (4, 4))
    output = _value(4, "output", TensorRole.OUTPUT, DType.FP32, (4, 4))
    add = GraphOp(1, OpCode.ADD, (1, 2), (3,), ElementwiseAttrs(), "add:1")
    relu = GraphOp(2, OpCode.RELU, (3,), (4,), ElementwiseAttrs(), "relu:2")
    function = GraphFunction(1, "forward", (1,), (add, relu), (4,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (x, weight, hidden, output)
    ledger = create_placement_ledger(function, values)
    assert {(item.value_id, item.kind) for item in ledger.external_bindings} == {
        (1, ExternalBindingKind.SOURCE),
        (2, ExternalBindingKind.SOURCE),
        (4, ExternalBindingKind.DESTINATION),
    }
    arch = load_arch(ARCH_PATH)
    geometry = derive_operation_geometry(add, values, ShardingStrategy.OUTER_DIM, (7, 2), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(geometry, PlacementRejection)
    assert tuple(item.operand_index for item in geometry.operand_distributions) == (0, 1)
    assert all(tuple(item.padded_shape for item in operand.required_distribution.shards) == ((2, 4), (2, 4)) for operand in geometry.operand_distributions)
    advanced = advance_placement_ledger(ledger, geometry)
    assert len(advanced.retained_backings) == 2
    assert all(item.window in geometry.resident_windows for item in advanced.retained_backings)
    assert advanced.next_window_id == max(item.window_id for item in geometry.resident_windows) + 1


def test_repeated_operand_occurrences_remain_distinct_but_share_exact_external_load():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (4, 4))
    output = _value(2, "output", TensorRole.OUTPUT, DType.FP32, (4, 4))
    add = GraphOp(1, OpCode.ADD, (1, 1), (2,), ElementwiseAttrs(), "add:1")
    function = GraphFunction(1, "forward", (1,), (add,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, output)
    geometry = derive_operation_geometry(
        add,
        values,
        ShardingStrategy.OUTER_DIM,
        (7, 2),
        create_placement_ledger(function, values),
        TILING,
        COLLECTIVES,
        load_arch(ARCH_PATH),
    )
    assert not isinstance(geometry, PlacementRejection)
    assert tuple(item.operand_index for item in geometry.operand_distributions) == (0, 1)
    loads = tuple(item for item in geometry.value_transfers if item.kind is PlacementTransferKind.EXTERNAL_LOAD)
    assert len(loads) == 2
    assert all(len(item.mappings) == 2 for item in geometry.operand_distributions)
    assert all(tuple(source.transfer_indices for source in item.mappings[0].sources) == ((0,),) for item in geometry.operand_distributions)
    operand_windows = tuple(item for item in geometry.resident_windows if item.role is ResidentWindowRole.OPERAND)
    assert len(operand_windows) == 4
    assert all(item.dma_visible for item in operand_windows)
    bindings = {(item.operand_index, item.logical_rank): item.window_ids for item in geometry.operand_windows}
    assert bindings[(0, 0)] == bindings[(1, 0)]
    assert bindings[(0, 1)] == bindings[(1, 1)]
    for rank in range(2):
        first_use = geometry.operand_distributions[0].mappings[rank].window_uses
        second_use = geometry.operand_distributions[1].mappings[rank].window_uses
        assert first_use == second_use
        assert len(first_use) == 1


def test_selected_backing_is_reused_without_a_duplicate_operand_window_and_preserve_checks_cores():
    x = _value(1, "x", TensorRole.INPUT, DType.FP32, (4, 4))
    y = _value(2, "y", TensorRole.INPUT, DType.FP32, (4, 4))
    hidden = _value(3, "hidden", TensorRole.ACTIVATION, DType.FP32, (4, 4))
    output = _value(4, "output", TensorRole.OUTPUT, DType.FP32, (4, 4))
    add = GraphOp(1, OpCode.ADD, (1, 2), (3,), ElementwiseAttrs(), "add:1")
    relu = GraphOp(2, OpCode.RELU, (3,), (4,), ElementwiseAttrs(), "relu:2")
    function = GraphFunction(1, "forward", (1, 2), (add, relu), (4,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    values = (x, y, hidden, output)
    arch = load_arch(ARCH_PATH)
    ledger = create_placement_ledger(function, values)
    first = derive_operation_geometry(add, values, ShardingStrategy.OUTER_DIM, (7, 2), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(first, PlacementRejection)
    ledger = advance_placement_ledger(ledger, first)
    preserved = derive_operation_geometry(relu, values, ShardingStrategy.PRESERVE, (7, 2), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(preserved, PlacementRejection)
    assert all(item.kind is PlacementTransferKind.OUTPUT_STORE for item in preserved.value_transfers)
    assert all(item.role is not ResidentWindowRole.OPERAND for item in preserved.resident_windows)
    retained_ids = {item.window.window_id for item in ledger.retained_backings}
    assert {window_id for item in preserved.operand_windows for window_id in item.window_ids} == retained_ids
    assert all(mapping.window_uses for mapping in preserved.operand_distributions[0].mappings)
    assert all(use.window_id in retained_ids for mapping in preserved.operand_distributions[0].mappings for use in mapping.window_uses)
    rejected = derive_operation_geometry(relu, values, ShardingStrategy.PRESERVE, (9, 13), ledger, TILING, COLLECTIVES, arch)
    assert isinstance(rejected, PlacementRejection)


def test_changed_group_uses_partial_local_overlap_and_explicit_peer_regions():
    x = _value(1, "x", TensorRole.INPUT, DType.FP32, (6, 4))
    y = _value(2, "y", TensorRole.INPUT, DType.FP32, (6, 4))
    hidden = _value(3, "hidden", TensorRole.ACTIVATION, DType.FP32, (6, 4))
    output = _value(4, "output", TensorRole.OUTPUT, DType.FP32, (6, 4))
    add = GraphOp(1, OpCode.ADD, (1, 2), (3,), ElementwiseAttrs(), "add:1")
    relu = GraphOp(2, OpCode.RELU, (3,), (4,), ElementwiseAttrs(), "relu:2")
    function = GraphFunction(1, "forward", (1, 2), (add, relu), (4,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    values = (x, y, hidden, output)
    arch = load_arch(ARCH_PATH)
    ledger = create_placement_ledger(function, values)
    first = derive_operation_geometry(add, values, ShardingStrategy.OUTER_DIM, (7, 2), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(first, PlacementRejection)
    ledger = advance_placement_ledger(ledger, first)
    changed = derive_operation_geometry(relu, values, ShardingStrategy.OUTER_DIM, (7, 2, 9, 13), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(changed, PlacementRejection)
    second_rank = changed.operand_distributions[0].mappings[1]
    assert {item.kind for item in second_rank.sources} == {RegionSourceKind.LOCAL, RegionSourceKind.PEER}


def test_metadata_view_transforms_selected_distribution_without_semantic_backing():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (2, 3, 4))
    viewed = GraphValue(2, "viewed", TensorRole.ACTIVATION, DType.FP32, (Const(2), Const(4), Const(3)), (12, 1, 4), 0, 1)
    op = GraphOp(1, OpCode.TRANSPOSE_VIEW, (1,), (2,), ViewAttrs(permutation=(0, 2, 1)), "transpose:1")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    geometry = derive_operation_geometry(op, (source, viewed), ShardingStrategy.REPLICATE, (7, 2), create_placement_ledger(function, (source, viewed)), TILING, COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    assert tuple(item.kind for item in geometry.value_transfers) == (PlacementTransferKind.EXTERNAL_LOAD, PlacementTransferKind.OUTPUT_STORE)
    assert all(item.role is ResidentWindowRole.OPERAND for item in geometry.resident_windows)
    assert geometry.retained_results == ()
    assert geometry.result_distribution.distribution is DistributionKind.REPLICATED


def test_internal_contiguous_reshape_preserves_rectangular_root_backing():
    x = _value(1, "x", TensorRole.INPUT, DType.FP32, (2, 3, 4))
    y = _value(2, "y", TensorRole.INPUT, DType.FP32, (2, 3, 4))
    hidden = _value(3, "hidden", TensorRole.ACTIVATION, DType.FP32, (2, 3, 4))
    viewed = GraphValue(4, "viewed", TensorRole.ACTIVATION, DType.FP32, (Const(6), Const(4)), (4, 1), 0, 3)
    output = _value(5, "output", TensorRole.OUTPUT, DType.FP32, (6, 4))
    add = GraphOp(1, OpCode.ADD, (1, 2), (3,), ElementwiseAttrs(), "add:1")
    reshape = GraphOp(2, OpCode.RESHAPE_VIEW, (3,), (4,), ViewAttrs(shape=viewed.shape), "reshape:2")
    relu = GraphOp(3, OpCode.RELU, (4,), (5,), ElementwiseAttrs(), "relu:3")
    function = GraphFunction(1, "forward", (1, 2), (add, reshape, relu), (5,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    values = (x, y, hidden, viewed, output)
    arch = load_arch(ARCH_PATH)
    ledger = create_placement_ledger(function, values)
    first = derive_operation_geometry(add, values, ShardingStrategy.OUTER_DIM, (7, 2), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(first, PlacementRejection)
    ledger = advance_placement_ledger(ledger, first)
    geometry = derive_operation_geometry(reshape, values, ShardingStrategy.PRESERVE, (7, 2), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(geometry, PlacementRejection)
    assert geometry.result_distribution.partition_axis == 0
    assert geometry.value_transfers == ()
    assert geometry.resident_windows == ()
    assert geometry.retained_results == ()
    assert all(source.kind is RegionSourceKind.LOCAL for mapping in geometry.operand_distributions[0].mappings for source in mapping.sources)
    assert all(item.window_ids for item in geometry.operand_windows)


def test_metadata_graph_output_stores_through_exact_retained_root_window():
    x = _value(1, "x", TensorRole.INPUT, DType.FP32, (2, 3))
    y = _value(2, "y", TensorRole.INPUT, DType.FP32, (2, 3))
    hidden = _value(3, "hidden", TensorRole.ACTIVATION, DType.FP32, (2, 3))
    viewed = GraphValue(4, "viewed", TensorRole.OUTPUT, DType.FP32, (Const(6),), (1,), 0, 3)
    add = GraphOp(1, OpCode.ADD, (1, 2), (3,), ElementwiseAttrs(), "add:retained-root")
    reshape = GraphOp(2, OpCode.RESHAPE_VIEW, (3,), (4,), ViewAttrs(shape=viewed.shape), "reshape:graph-output")
    function = GraphFunction(1, "forward", (1, 2), (add, reshape), (4,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    values = (x, y, hidden, viewed)
    arch = load_arch(ARCH_PATH)
    ledger = create_placement_ledger(function, values)
    first = derive_operation_geometry(add, values, ShardingStrategy.SINGLE, (7,), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(first, PlacementRejection)
    ledger = advance_placement_ledger(ledger, first)
    retained_id = ledger.retained_backings[0].window.window_id
    geometry = derive_operation_geometry(reshape, values, ShardingStrategy.PRESERVE, (7,), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(geometry, PlacementRejection)
    assert geometry.resident_windows == ()
    assert geometry.compute_tiles == ()
    assert len(geometry.value_transfers) == 1
    store = geometry.value_transfers[0]
    assert store.kind is PlacementTransferKind.OUTPUT_STORE
    assert store.useful_bytes == 24
    assert store.window_use.window_id == retained_id
    with pytest.raises(MeshIrError, match="selected result window"):
        replace(geometry, value_transfers=(replace(store, window_use=replace(store.window_use, window_id=retained_id + 1000)),))


def test_matrix_geometry_owns_per_occurrence_padding_and_tile_local_collectives():
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP16, (5, 9))
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP16, (9, 6), content="b" * 64)
    output = _value(3, "output", TensorRole.OUTPUT, DType.FP16, (5, 6))
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(accum_dtype=DType.FP32), "matmul:1")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (lhs, rhs, output)
    ledger = create_placement_ledger(function, values)
    arch = load_arch(ARCH_PATH)
    m_geometry = derive_operation_geometry(op, values, ShardingStrategy.MATRIX_M, (7, 2, 9, 13), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(m_geometry, PlacementRejection)
    assert tuple(item.padded_shape for item in m_geometry.operand_distributions[0].required_distribution.shards) == ((2, 9),) * 4
    assert all(item.distribution is DistributionKind.REPLICATED for item in (m_geometry.operand_distributions[1].required_distribution,))
    assert m_geometry.collective_transfers == ()

    k_geometry = derive_operation_geometry(op, values, ShardingStrategy.MATRIX_K_SUM, (7, 2, 9, 13), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(k_geometry, PlacementRejection)
    assert tuple(item.padded_shape for item in k_geometry.operand_distributions[0].required_distribution.shards) == ((5, 3),) * 4
    assert k_geometry.collective_transfers
    assert len(k_geometry.collectives) == len(k_geometry.output_tiles)
    assert tuple(dict.fromkeys(item.owner_core for item in k_geometry.compute_tiles)) == (7, 2, 9, 13)
    assert all(sum(item.owner_core == owner for item in k_geometry.compute_tiles) == len(k_geometry.output_tiles) for owner in (7, 2, 9, 13))
    chunks = {item.chunk for item in k_geometry.collective_transfers}
    assert all(item.accumulation_dtype is DType.FP32 for item in chunks)
    assert {item.output_tile for item in chunks} == set(k_geometry.output_tiles)


def test_matrix_streaming_uses_two_small_input_windows_and_not_whole_operands():
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP32, (1, 1024))
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP32, (1024, 1), content="b" * 64)
    output = _value(3, "output", TensorRole.OUTPUT, DType.FP32, (1, 1))
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:stream")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (lhs, rhs, output)
    arch = load_arch(ARCH_PATH)
    arch.sram_bytes = 512
    tiling = Tiling(1, 1, 8, True)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.MATRIX_K_SUM, (7, 2), create_placement_ledger(function, values), tiling, Collectives("ring", 32), arch)
    assert not isinstance(geometry, PlacementRejection)
    ingress = tuple(item for item in geometry.resident_windows if item.role is ResidentWindowRole.OPERAND)
    assert len(ingress) == 8
    assert all(item.required_extent_bytes == 32 for item in ingress)
    assert {item.buffer_index for item in ingress} == {0, 1}
    assert all(len(item.window_ids) == 2 for item in geometry.operand_windows)
    estimate = estimate_operation_geometry(geometry, create_placement_ledger(function, values), arch)
    assert not isinstance(estimate, PlacementRejection)
    assert estimate.peak_sram_bytes <= 512


def test_matrix_geometry_publishes_exact_compute_tiles_window_uses_and_reload_traffic():
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP32, (1, 3))
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP32, (3, 2), content="e" * 64)
    output = _value(3, "output", TensorRole.OUTPUT, DType.FP32, (1, 2))
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:reload")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (lhs, rhs, output)
    geometry = derive_operation_geometry(
        op,
        values,
        ShardingStrategy.SINGLE,
        (7,),
        create_placement_ledger(function, values),
        Tiling(1, 1, 1, True),
        COLLECTIVES,
        load_arch(ARCH_PATH),
    )
    assert not isinstance(geometry, PlacementRejection)
    assert tuple(item.tile_ordinal for item in geometry.compute_tiles) == tuple(range(6))
    assert tuple(item.output_tile_index for item in geometry.compute_tiles) == (0, 0, 0, 1, 1, 1)
    assert tuple(item.owner_core for item in geometry.compute_tiles) == (7,) * 6
    assert tuple(item.matrix_phase for item in geometry.compute_tiles) == (
        MatrixPhase.ACCUMULATE_FIRST,
        MatrixPhase.ACCUMULATE_CONTINUE,
        MatrixPhase.ACCUMULATE_FINAL,
        MatrixPhase.ACCUMULATE_FIRST,
        MatrixPhase.ACCUMULATE_CONTINUE,
        MatrixPhase.ACCUMULATE_FINAL,
    )
    assert tuple(item.tile for item in geometry.compute_tiles) == tuple(
        KernelTile(0, 0, n, k, 1, 1, 1, 1, 1, 1, 1, 1)
        for n in range(2)
        for k in range(3)
    )
    for operand in geometry.operand_distributions:
        uses = operand.mappings[0].window_uses
        assert tuple(item.compute_tile_ordinal for item in uses) == tuple(range(6))
        assert tuple(item.output_tile_index for item in uses) == (0, 0, 0, 1, 1, 1)
        assert all(item.phase is GeometryPhase.COMPUTE for item in uses)
        binding = next(item for item in geometry.operand_windows if item.operand_index == operand.operand_index)
        assert set(item.window_id for item in uses) == set(binding.window_ids)
        assert tuple(item.window_id for item in uses) == (binding.window_ids[0], binding.window_ids[1]) * 3
    loads = tuple(item for item in geometry.value_transfers if item.kind is PlacementTransferKind.EXTERNAL_LOAD)
    stores = tuple(item for item in geometry.value_transfers if item.kind is PlacementTransferKind.OUTPUT_STORE)
    assert sum(item.useful_bytes for item in loads if item.operand_index == 0) == 24
    assert sum(item.useful_bytes for item in loads if item.operand_index == 1) == 24
    assert sum(item.useful_bytes for item in stores) == 8
    assert sum(item.useful_bytes for item in geometry.value_transfers) == 56
    assert all(item.window_use in geometry.operand_distributions[item.operand_index].mappings[0].window_uses for item in loads)
    assert tuple(item.window_use.output_tile_index for item in stores) == (0, 1)
    assert all(item.window_use.compute_tile_ordinal is None and item.window_use.phase is GeometryPhase.STORE for item in stores)
    referenced = {
        transfer_index
        for operand in geometry.operand_distributions
        for mapping in operand.mappings
        for source in mapping.sources
        for transfer_index in source.transfer_indices
    }
    assert referenced == {index for index, item in enumerate(geometry.value_transfers) if item.kind is not PlacementTransferKind.OUTPUT_STORE}


def test_empty_matrix_rank_retains_completion_tile_without_access_or_traffic():
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP32, (1, 1))
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP32, (1, 1), content="f" * 64)
    output = _value(3, "output", TensorRole.OUTPUT, DType.FP32, (1, 1))
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:empty-rank")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (lhs, rhs, output)
    geometry = derive_operation_geometry(
        op,
        values,
        ShardingStrategy.MATRIX_M,
        (7, 2),
        create_placement_ledger(function, values),
        Tiling(1, 1, 1, True),
        COLLECTIVES,
        load_arch(ARCH_PATH),
    )
    assert not isinstance(geometry, PlacementRejection)
    empty = tuple(item for item in geometry.compute_tiles if item.owner_core == 2)
    assert len(empty) == 1
    assert empty[0].tile.valid_batch == empty[0].tile.valid_m == empty[0].tile.valid_n == empty[0].tile.valid_k == 0
    assert all(not mapping.window_uses for operand in geometry.operand_distributions for mapping in operand.mappings if mapping.required_shard.owner_core == 2)
    assert all(item.request_core != 2 for item in geometry.value_transfers)


def test_matrix_bias_uses_epilogue_slots_and_reuses_invariant_broadcast_content():
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP32, (1, 3))
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP32, (3, 2), content="1" * 64)
    bias = _value(3, "bias", TensorRole.WEIGHT, DType.FP32, (1,), content="2" * 64)
    output = _value(4, "output", TensorRole.OUTPUT, DType.FP32, (1, 2))
    op = GraphOp(1, OpCode.LINEAR_BIAS, (1, 2, 3), (4,), MatmulAttrs(beta=0.25), "linear:bias-use")
    function = GraphFunction(1, "forward", (1,), (op,), (4,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (lhs, rhs, bias, output)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, values), Tiling(1, 1, 1, True), COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    bias_mapping = geometry.operand_distributions[2].mappings[0]
    assert len(bias_mapping.window_uses) == 2
    assert tuple(item.output_tile_index for item in bias_mapping.window_uses) == (0, 1)
    assert all(item.compute_tile_ordinal is None and item.phase is GeometryPhase.EPILOGUE for item in bias_mapping.window_uses)
    assert len({item.window_id for item in bias_mapping.window_uses}) == 1
    bias_loads = tuple(item for item in geometry.value_transfers if item.kind is PlacementTransferKind.EXTERNAL_LOAD and item.operand_index == 2)
    assert len(bias_loads) == 1
    assert bias_loads[0].useful_bytes == 4
    assert bias_loads[0].window_use == bias_mapping.window_uses[0]


def test_same_matrix_value_with_different_lhs_rhs_access_sequences_keeps_distinct_slots():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (2, 2))
    output = _value(2, "output", TensorRole.OUTPUT, DType.FP32, (2, 2))
    op = GraphOp(1, OpCode.MATMUL, (1, 1), (2,), MatmulAttrs(), "matmul:two-uses")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, output)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, values), Tiling(1, 1, 1, True), COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    lhs_binding, rhs_binding = geometry.operand_windows
    assert lhs_binding.window_ids != rhs_binding.window_ids
    lhs_uses = geometry.operand_distributions[0].mappings[0].window_uses
    rhs_uses = geometry.operand_distributions[1].mappings[0].window_uses
    assert tuple(item.compute_tile_ordinal for item in lhs_uses) == tuple(range(8))
    assert tuple(item.compute_tile_ordinal for item in rhs_uses) == tuple(range(8))
    assert tuple(item.window_id for item in lhs_uses) != tuple(item.window_id for item in rhs_uses)


def test_identical_multik_operand_occurrences_share_one_physical_generation_sequence():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (1, 3))
    output = _value(2, "output", TensorRole.OUTPUT, DType.FP32, (1, 1))
    op = GraphOp(1, OpCode.MATMUL, (1, 1), (2,), MatmulAttrs(rhs_transpose=True), "matmul:identical-uses")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, output)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, values), Tiling(1, 1, 1, True), COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    lhs_binding, rhs_binding = geometry.operand_windows
    assert lhs_binding.window_ids == rhs_binding.window_ids
    lhs_mapping = geometry.operand_distributions[0].mappings[0]
    rhs_mapping = geometry.operand_distributions[1].mappings[0]
    assert lhs_mapping.window_uses == rhs_mapping.window_uses
    assert tuple(item.compute_tile_ordinal for item in lhs_mapping.window_uses) == (0, 1, 2)
    assert tuple(item.window_id for item in lhs_mapping.window_uses) == (lhs_binding.window_ids[0], lhs_binding.window_ids[1], lhs_binding.window_ids[0])
    loads = tuple(item for item in geometry.value_transfers if item.kind is PlacementTransferKind.EXTERNAL_LOAD)
    assert len(loads) == 3
    assert sum(item.useful_bytes for item in loads) == 12
    assert tuple(source.transfer_indices for source in lhs_mapping.sources) == tuple(source.transfer_indices for source in rhs_mapping.sources)
    assert sum(item.useful_bytes for item in geometry.value_transfers) == 16


def test_operand_window_use_regions_preserve_occurrence_transpose_and_k_tail():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (2, 3))
    output = _value(2, "output", TensorRole.OUTPUT, DType.FP32, (2, 2))
    op = GraphOp(1, OpCode.MATMUL, (1, 1), (2,), MatmulAttrs(rhs_transpose=True), "matmul:projected-regions")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, output)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, values), Tiling(1, 1, 2, True), COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    lhs_mapping = geometry.operand_distributions[0].mappings[0]
    rhs_mapping = geometry.operand_distributions[1].mappings[0]
    lhs = operand_window_use_regions(op, 0, source, lhs_mapping, geometry)
    rhs = operand_window_use_regions(op, 1, source, rhs_mapping, geometry)
    assert tuple((item.origin, item.shape) for item in lhs) == (
        ((0, 0), (1, 2)),
        ((0, 2), (1, 1)),
        ((0, 0), (1, 2)),
        ((0, 2), (1, 1)),
        ((1, 0), (1, 2)),
        ((1, 2), (1, 1)),
        ((1, 0), (1, 2)),
        ((1, 2), (1, 1)),
    )
    assert tuple((item.origin, item.shape) for item in rhs) == (
        ((0, 0), (1, 2)),
        ((0, 2), (1, 1)),
        ((1, 0), (1, 2)),
        ((1, 2), (1, 1)),
        ((0, 0), (1, 2)),
        ((0, 2), (1, 1)),
        ((1, 0), (1, 2)),
        ((1, 2), (1, 1)),
    )
    assert lhs_mapping.window_uses != rhs_mapping.window_uses


def test_operand_window_use_regions_include_bias_cache_hits_and_local_access():
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP32, (1, 3))
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP32, (3, 2), content="1" * 64)
    bias = _value(3, "bias", TensorRole.WEIGHT, DType.FP32, (1,), content="2" * 64)
    output = _value(4, "output", TensorRole.OUTPUT, DType.FP32, (1, 2))
    op = GraphOp(1, OpCode.LINEAR_BIAS, (1, 2, 3), (4,), MatmulAttrs(beta=0.25), "linear:projected-bias")
    function = GraphFunction(1, "forward", (1,), (op,), (4,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (lhs, rhs, bias, output)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, values), Tiling(1, 1, 1, True), COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    bias_mapping = geometry.operand_distributions[2].mappings[0]
    regions = operand_window_use_regions(op, 2, bias, bias_mapping, geometry)
    assert regions == (DenseLogicalRegion((0,), (1,)), DenseLogicalRegion((0,), (1,)))
    assert len(tuple(item for item in geometry.value_transfers if item.operand_index == 2)) == 1

    x = _value(5, "x", TensorRole.INPUT, DType.FP32, (2, 2))
    y = _value(6, "y", TensorRole.INPUT, DType.FP32, (2, 2))
    hidden = _value(7, "hidden", TensorRole.ACTIVATION, DType.FP32, (2, 2))
    final = _value(8, "final", TensorRole.OUTPUT, DType.FP32, (2, 2))
    add = GraphOp(5, OpCode.ADD, (5, 6), (7,), ElementwiseAttrs(), "add:retained")
    relu = GraphOp(6, OpCode.RELU, (7,), (8,), ElementwiseAttrs(), "relu:local")
    local_function = GraphFunction(2, "forward", (5, 6), (add, relu), (8,), PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())), PytreeSpec.leaf())
    local_values = (x, y, hidden, final)
    ledger = create_placement_ledger(local_function, local_values)
    first = derive_operation_geometry(add, local_values, ShardingStrategy.OUTER_DIM, (7, 2), ledger, TILING, COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(first, PlacementRejection)
    ledger = advance_placement_ledger(ledger, first)
    local = derive_operation_geometry(relu, local_values, ShardingStrategy.PRESERVE, (7, 2), ledger, TILING, COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(local, PlacementRejection)
    local_mapping = local.operand_distributions[0].mappings[0]
    assert operand_window_use_regions(relu, 0, hidden, local_mapping, local) == (DenseLogicalRegion((0, 0), (1, 2)),)
    assert not tuple(item for item in local.value_transfers if item.operand_index == 0)


def test_operand_window_use_regions_map_metadata_source_and_result_coordinate_domains_by_rank():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (2, 3))
    viewed = GraphValue(2, "viewed", TensorRole.OUTPUT, DType.FP32, (Const(6),), (1,), 0, 1)
    op = GraphOp(1, OpCode.RESHAPE_VIEW, (1,), (2,), ViewAttrs(shape=viewed.shape), "reshape:projected-region")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, viewed)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, values), TILING, COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    assert geometry.compute_tiles == ()
    assert len(geometry.resident_windows) == 1
    assert tuple(item.useful_bytes for item in geometry.value_transfers) == (24, 24)
    load, store = geometry.value_transfers
    assert load.window_use.window_id == store.window_use.window_id
    mapping = geometry.operand_distributions[0].mappings[0]
    assert operand_window_use_regions(op, 0, source, mapping, geometry) == (DenseLogicalRegion((0, 0), (2, 3)),)
    assert geometry.output_tiles == (TiledLogicalRegion((0,), (6,), (6,)),)


def test_operand_window_use_regions_return_empty_for_access_free_mapping():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (1, 1))
    output = _value(2, "output", TensorRole.OUTPUT, DType.FP32, (1, 1))
    op = GraphOp(1, OpCode.MATMUL, (1, 1), (2,), MatmulAttrs(), "matmul:empty-projection")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, output)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.MATRIX_M, (7, 2), create_placement_ledger(function, values), Tiling(1, 1, 1, True), COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    mapping = geometry.operand_distributions[0].mappings[1]
    assert mapping.window_uses == ()
    assert operand_window_use_regions(op, 0, source, mapping, geometry) == ()

    viewed = GraphValue(3, "viewed", TensorRole.ACTIVATION, DType.FP32, (Const(1),), (1,), 0, 1)
    final = _value(4, "final", TensorRole.OUTPUT, DType.FP32, (1,))
    reshape = GraphOp(2, OpCode.RESHAPE_VIEW, (1,), (3,), ViewAttrs(shape=viewed.shape), "reshape:access-free")
    relu = GraphOp(3, OpCode.RELU, (3,), (4,), ElementwiseAttrs(), "relu:after-view")
    view_function = GraphFunction(2, "forward", (1,), (reshape, relu), (4,), PytreeSpec.leaf(), PytreeSpec.leaf())
    view_values = (source, viewed, final)
    view_geometry = derive_operation_geometry(reshape, view_values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(view_function, view_values), TILING, COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(view_geometry, PlacementRejection)
    view_mapping = view_geometry.operand_distributions[0].mappings[0]
    assert view_geometry.compute_tiles == view_geometry.resident_windows == view_geometry.value_transfers == ()
    assert view_mapping.window_uses == ()
    assert operand_window_use_regions(reshape, 0, source, view_mapping, view_geometry) == ()


def test_operand_window_use_regions_reject_invalid_occurrence_membership_and_use_identity():
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP32, (2, 2))
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP32, (2, 2), content="4" * 64)
    output = _value(3, "output", TensorRole.OUTPUT, DType.FP32, (2, 2))
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:projection-errors")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (lhs, rhs, output)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, values), Tiling(1, 1, 1, True), COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    mapping = geometry.operand_distributions[0].mappings[0]
    assert operand_window_use_regions(op, 0, lhs, replace(mapping), geometry) == operand_window_use_regions(op, 0, lhs, mapping, geometry)
    with pytest.raises(MeshIrError, match="operand occurrence"):
        operand_window_use_regions(op, 1, lhs, mapping, geometry)
    with pytest.raises(MeshIrError, match="selected operand mapping"):
        operand_window_use_regions(op, 0, lhs, replace(mapping, required_region=DenseLogicalRegion((0, 0), (1, 2))), geometry)
    mismatched = replace(op, opcode=OpCode.RESHAPE_VIEW, attrs=ViewAttrs(shape=lhs.shape))
    with pytest.raises(MeshIrError, match="window use identity"):
        operand_window_use_regions(mismatched, 0, lhs, mapping, geometry)
    with pytest.raises(MeshIrError, match="invalid record types"):
        operand_window_use_regions(op, True, lhs, mapping, geometry)


def test_metadata_external_alias_has_window_uses_but_no_compute_tile():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (2, 3, 4))
    viewed = GraphValue(2, "viewed", TensorRole.OUTPUT, DType.FP32, (Const(2), Const(4), Const(3)), (12, 1, 4), 0, 1)
    op = GraphOp(1, OpCode.TRANSPOSE_VIEW, (1,), (2,), ViewAttrs(permutation=(0, 2, 1)), "transpose:window-use")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, viewed)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), create_placement_ledger(function, values), TILING, COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    assert geometry.compute_tiles == ()
    assert len(geometry.operand_distributions[0].mappings[0].window_uses) == 1
    load, store = geometry.value_transfers
    assert load.window_use.compute_tile_ordinal is None
    assert load.window_use.phase is GeometryPhase.PREFETCH
    assert store.window_use.compute_tile_ordinal is None
    assert store.window_use.phase is GeometryPhase.STORE
    assert load.window_use.window_id == store.window_use.window_id


def test_selected_geometry_records_reject_invalid_use_phase_and_cross_owner_reference():
    with pytest.raises(MeshIrError, match="compute identity and phase"):
        WindowUseGeometry(0, None, 0, 1, GeometryPhase.COMPUTE)
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (4, 4))
    output = _value(2, "output", TensorRole.OUTPUT, DType.FP32, (4, 4))
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:owner")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, output)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.OUTER_DIM, (7, 2), create_placement_ledger(function, values), TILING, COLLECTIVES, load_arch(ARCH_PATH))
    assert not isinstance(geometry, PlacementRejection)
    mapping = geometry.operand_distributions[0].mappings[0]
    wrong_use = replace(mapping.window_uses[0], compute_tile_ordinal=1, output_tile_index=1)
    wrong_mapping = replace(mapping, window_uses=(wrong_use,))
    wrong_operand = replace(geometry.operand_distributions[0], mappings=(wrong_mapping, *geometry.operand_distributions[0].mappings[1:]))
    with pytest.raises(MeshIrError, match="differs from its compute tile"):
        replace(geometry, operand_distributions=(wrong_operand,))


def test_geometry_type_and_fabric_permission_failures_are_precise():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (2, 2))
    output = _value(2, "output", TensorRole.OUTPUT, DType.FP32, (2, 2))
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:fabric")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, output)
    arch = load_arch(ARCH_PATH)
    ledger = create_placement_ledger(function, values)
    with pytest.raises(MeshIrError) as invalid_core:
        derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (True,), ledger, TILING, COLLECTIVES, arch)
    assert invalid_core.value.code == "E_PLACEMENT_INFEASIBLE"
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, (7,), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(geometry, PlacementRejection)
    memory = arch.fabric.targets[0]
    read_only_memory = replace(memory, ranges=tuple(replace(item, access=Access.READ_ONLY) for item in memory.ranges))
    arch.fabric = replace(arch.fabric, targets=(read_only_memory, *arch.fabric.targets[1:]))
    rejected = estimate_operation_geometry(geometry, ledger, arch)
    assert isinstance(rejected, PlacementRejection)
    assert rejected.code == "E_PLACEMENT_INFEASIBLE"
    assert rejected.message == "placement transfer has no legal target range"


def test_geometry_uses_requester_router_and_exact_external_hop_bytes():
    source = _value(1, "source", TensorRole.INPUT, DType.FP32, (4, 2))
    output = _value(2, "output", TensorRole.OUTPUT, DType.FP32, (4, 2))
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:routing")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    values = (source, output)
    arch = load_arch(ARCH_PATH)
    ledger = create_placement_ledger(function, values)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.OUTER_DIM, (7, 2), ledger, TILING, COLLECTIVES, arch)
    assert not isinstance(geometry, PlacementRejection)
    estimate = estimate_operation_geometry(geometry, ledger, arch)
    assert not isinstance(estimate, PlacementRejection)
    assert estimate.communication_bytes == 64
    assert estimate.manhattan_hop_bytes_lower_bound == 32


@pytest.mark.parametrize(
    ("lhs_extents", "rhs_extents", "output_extents", "expected"),
        (
            ((1, 1024), (1024, 1), (1, 1), ShardingStrategy.MATRIX_M),
            ((1024, 1), (1, 1), (1024, 1), ShardingStrategy.MATRIX_N),
    ),
)
def test_place_operations_publishes_selected_geometry_and_natural_matrix_choice(lhs_extents, rhs_extents, output_extents, expected):
    arch = load_arch(ARCH_PATH)
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP32, lhs_extents)
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP32, rhs_extents, content="c" * 64)
    output = _value(3, "output", TensorRole.OUTPUT, DType.FP32, output_extents)
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:k")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "d" * 64, "forward", "p1", (lhs, rhs, output), (function,))
    config = load_compile_config_text(
        """
schema_version: mesh-compile-v1
entrypoints: [forward]
shape_profiles: {forward: [{profile_id: p1}]}
symbol_bindings: {}
parallelism: {tensor_parallel: 2, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}
placement: {core_order: row_major_yx, allowed_cores: [7, 2], reserve_cores: []}
tiling: {gemm_m: 3, gemm_n: 5, gemm_k: 7, double_buffer: true}
collectives: {all_reduce_algorithm: tree, chunk_bytes: 28}
runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}
""",
        arch,
    )
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        result = place_operations(planning, arch, effective)
    selected = result.placements[0]
    candidate = result.candidates[selected.candidate_index]
    assert selected.strategy is expected
    assert candidate.geometry is not None
    assert candidate.rejection is None
    if lhs_extents == (1, 1024):
        scores = {item.strategy: item.score.communication_bytes for item in result.candidates if item.core_ids == selected.core_ids and item.rejection is None}
        assert scores[ShardingStrategy.MATRIX_M] == 8196
        assert scores[ShardingStrategy.MATRIX_K_SUM] == 8204
        empty = tuple(item for item in candidate.geometry.compute_tiles if item.owner_core == 2)
        assert len(empty) == 1
        assert empty[0].tile.valid_batch == empty[0].tile.valid_m == empty[0].tile.valid_n == empty[0].tile.valid_k == 0
        assert all(item.request_core != 2 for item in candidate.geometry.value_transfers)
    else:
        scores = {item.strategy: item.score.communication_bytes for item in result.candidates if item.core_ids == selected.core_ids and item.rejection is None}
        assert scores[ShardingStrategy.MATRIX_N] == 8196
        assert scores[ShardingStrategy.MATRIX_M] == 8200
        empty = tuple(item for item in candidate.geometry.compute_tiles if item.owner_core == 2)
        assert len(empty) == 1
        assert empty[0].tile.valid_batch == empty[0].tile.valid_m == empty[0].tile.valid_n == empty[0].tile.valid_k == 0
        assert all(item.request_core != 2 for item in candidate.geometry.value_transfers)


def test_real_graph_placement_naturally_selects_k_sum_from_exact_generation_costs():
    arch = load_arch(ARCH_PATH)
    lhs = _value(1, "lhs", TensorRole.INPUT, DType.FP32, (4, 4))
    rhs = _value(2, "rhs", TensorRole.WEIGHT, DType.FP32, (4, 4), content="3" * 64)
    output = _value(3, "output", TensorRole.OUTPUT, DType.FP32, (4, 4))
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), "matmul:natural-k")
    function = GraphFunction(1, "forward", (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "4" * 64, "forward", "p1", (lhs, rhs, output), (function,))
    config = load_compile_config_text(
        """
schema_version: mesh-compile-v1
entrypoints: [forward]
shape_profiles: {forward: [{profile_id: p1}]}
symbol_bindings: {}
parallelism: {tensor_parallel: 2, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}
placement: {core_order: row_major_yx, allowed_cores: [7, 2], reserve_cores: []}
tiling: {gemm_m: 1, gemm_n: 1, gemm_k: 1, double_buffer: true}
collectives: {all_reduce_algorithm: tree, chunk_bytes: 28}
runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}
""",
        arch,
    )
    effective = resolve_compile_config(config, arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        result = place_operations(planning, arch, effective)
    selected = result.placements[0]
    assert selected.strategy is ShardingStrategy.MATRIX_K_SUM
    scores = {item.strategy: item.score.communication_bytes for item in result.candidates if item.core_ids == selected.core_ids and item.rejection is None}
    assert scores[ShardingStrategy.MATRIX_M] == 576
    assert scores[ShardingStrategy.MATRIX_N] == 576
    assert scores[ShardingStrategy.MATRIX_K_SUM] == 512

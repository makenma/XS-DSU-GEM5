import dataclasses
import os
import pickle
import subprocess
import sys
from pathlib import Path

import networkx as nx
import pytest
import rustworkx as rx

from mesh_ir.analysis.dependency import DependencyEdge, DependencyKind, DirectedGraph, NodeOrderKey, build_kernel_dependency_graph
from mesh_ir.analysis.lifetime import LifetimeConflictReason, analyze_lifetimes, analyze_memory_lifetimes
from mesh_ir.analysis.regions import ByteSpan, region_byte_spans
from mesh_ir.analysis.sram import plan_memory_sram, plan_static_sram
from mesh_ir.architecture import load_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DType, DmaKind, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, OpCode
from mesh_ir.ir.kernel_ir import BufferObject, ControlToken, DistributionKind, DmaAttrs, ElementRegion, KernelComputation, KernelCost, KernelOp, KernelOpcode, KernelTensor, KernelTile, OperandAccess, OperandAccessMode, Placement, StateOrigin, StateTransition, TensorShard, TensorState, VectorAlgorithm, VectorKernelAttrs
from mesh_ir.traffic import AddressRef, DirectAddress, resolve_addresses
from tests.unit.test_gate2_kernel_ir import create_kernel, declarations, load_compute_store_kernel, refreshed


ROOT = Path(__file__).resolve().parents[4]


def order_keys(count):
    return tuple(NodeOrderKey(f"node:{index}", "TEST", (index,)) for index in range(1, count + 1))


def test_directed_graph_is_strict_cached_private_and_reduces_the_full_union(monkeypatch):
    native_topological_sort = rx.topological_sort
    native_lexicographical_topological_sort = rx.lexicographical_topological_sort
    native_descendants = rx.descendants
    native_transitive_reduction = rx.transitive_reduction
    calls = []

    def observed_topological_sort(native):
        calls.append(("topological_sort", id(native)))
        return native_topological_sort(native)

    def observed_lexicographical_topological_sort(native, **kwargs):
        calls.append(("lexicographical_topological_sort", id(native)))
        return native_lexicographical_topological_sort(native, **kwargs)

    def observed_descendants(native, source):
        calls.append(("descendants", id(native)))
        return native_descendants(native, source)

    def observed_transitive_reduction(native):
        calls.append(("transitive_reduction", id(native)))
        return native_transitive_reduction(native)

    monkeypatch.setattr(rx, "topological_sort", observed_topological_sort)
    monkeypatch.setattr(rx, "lexicographical_topological_sort", observed_lexicographical_topological_sort)
    monkeypatch.setattr(rx, "descendants", observed_descendants)
    monkeypatch.setattr(rx, "transitive_reduction", observed_transitive_reduction)
    graph = DirectedGraph(
        (1, 2, 3, 4),
        (
            DependencyEdge(1, 2, DependencyKind.STATE, 1),
            DependencyEdge(1, 3, DependencyKind.STATE, 2),
            DependencyEdge(2, 3, DependencyKind.CONTROL, 3),
            DependencyEdge(3, 4, DependencyKind.OP_COMPLETION, 4),
        ),
        order_keys(4),
    )
    before = dataclasses.asdict(graph)
    assert graph.canonical_topological_order() == (1, 2, 3, 4)
    assert graph.reachable(1) == frozenset((2, 3, 4))
    assert graph.happens_before(1, 4)
    assert graph.transitive_reduction().edges == (
        DependencyEdge(1, 2, DependencyKind.STATE, 1),
        DependencyEdge(2, 3, DependencyKind.CONTROL, 3),
        DependencyEdge(3, 4, DependencyKind.OP_COMPLETION, 4),
    )
    assert [name for name, _ in calls] == ["topological_sort", "lexicographical_topological_sort", "descendants", "transitive_reduction"]
    assert len({native_id for _, native_id in calls}) == 1
    assert dataclasses.asdict(graph) == before
    assert not any(isinstance(getattr(graph, name), rx.PyDiGraph) for name in dir(graph) if not name.startswith("_"))
    assert "_DirectedGraph__graph" in vars(graph) and "_graph" not in vars(graph)
    with pytest.raises(dataclasses.FrozenInstanceError):
        graph.nodes = ()
    restored = pickle.loads(pickle.dumps(graph))
    assert dataclasses.asdict(restored) == before
    assert restored.reachable(1) == frozenset((2, 3, 4))


@pytest.mark.parametrize(
    "factory",
    (
        lambda: DirectedGraph((True,), (), order_keys(1)),
        lambda: DirectedGraph((1.0,), (), order_keys(1)),
        lambda: DirectedGraph((1, 2), (DependencyEdge(1, 2, "CONTROL"),), order_keys(2)),
        lambda: DirectedGraph((1,), (), (NodeOrderKey("node", "TEST", (True,)),)),
    ),
)
def test_directed_graph_rejects_json_coercible_nested_types(factory):
    with pytest.raises(MeshIrError):
        factory()


def test_directed_graph_queries_reject_bool_and_float_nodes_with_stable_error():
    graph = DirectedGraph((1,), (), order_keys(1))
    for query in (lambda: graph.reachable(True), lambda: graph.happens_before(1.0, 1)):
        with pytest.raises(MeshIrError) as error:
            query()
        assert error.value.code == "E_ABI_BOUNDS"


@pytest.mark.parametrize(
    "edges",
    (
        (
            DependencyEdge(1, 2, DependencyKind.STATE, 1),
            DependencyEdge(1, 3, DependencyKind.CONTROL, 2),
            DependencyEdge(2, 4, DependencyKind.STATE, 3),
            DependencyEdge(3, 4, DependencyKind.OP_COMPLETION, 4),
        ),
        (
            DependencyEdge(1, 2, DependencyKind.STATE, 1),
            DependencyEdge(2, 3, DependencyKind.CONTROL, 2),
            DependencyEdge(3, 1, DependencyKind.OP_COMPLETION, 3),
            DependencyEdge(4, 4, DependencyKind.STATE, 4),
        ),
    ),
)
def test_directed_graph_ancestors_are_native_immutable_and_graph_local(edges, monkeypatch):
    graph = DirectedGraph((1, 2, 3, 4, 5), edges, order_keys(5))
    oracle = nx.DiGraph()
    oracle.add_nodes_from(graph.nodes)
    oracle.add_edges_from((edge.source, edge.target) for edge in edges)
    native_ancestors = rx.ancestors
    calls = []

    def observed_ancestors(native, target):
        calls.append((id(native), native[target]))
        return native_ancestors(native, target)

    monkeypatch.setattr(rx, "ancestors", observed_ancestors)
    before = dataclasses.asdict(graph)
    for target in graph.nodes:
        actual = graph.ancestors(target)
        assert type(actual) is frozenset
        assert actual == frozenset(nx.ancestors(oracle, target))
        assert target not in actual
    assert [target for _, target in calls] == list(graph.nodes)
    native_ids = {native_id for native_id, _ in calls}
    assert len(native_ids) == 1
    assert dataclasses.asdict(graph) == before
    fresh = dataclasses.replace(graph, edges=())
    assert all(fresh.ancestors(target) == frozenset() for target in fresh.nodes)
    assert len({native_id for native_id, _ in calls}) == 2


@pytest.mark.parametrize("target", (0, 3, -1, True, 1.0, "1", None))
def test_directed_graph_ancestor_target_admission_is_exact_after_forward_query(target):
    graph = DirectedGraph((1, 2), (DependencyEdge(1, 2, DependencyKind.STATE, 1),), order_keys(2))
    assert graph.reachable(1) == frozenset((2,))
    with pytest.raises(MeshIrError) as error:
        graph.ancestors(target)
    assert error.value.code == "E_ABI_BOUNDS"


@pytest.mark.parametrize("cyclic", (False, True))
def test_direct_edges_prove_order_before_descendant_traversal(cyclic, monkeypatch):
    edges = [
        DependencyEdge(1, 2, DependencyKind.STATE, 1),
        DependencyEdge(2, 3, DependencyKind.CONTROL, 2),
    ]
    if cyclic:
        edges.append(DependencyEdge(3, 1, DependencyKind.OP_COMPLETION, 3))
    graph = DirectedGraph((1, 2, 3), tuple(edges), order_keys(3))
    native_descendants = rx.descendants
    descendant_calls = []

    def counted_descendants(native, source):
        descendant_calls.append((id(native), native[source]))
        return native_descendants(native, source)

    monkeypatch.setattr(rx, "descendants", counted_descendants)
    assert graph.happens_before(1, 2) is True
    assert descendant_calls == []
    assert graph.reachable(1) == frozenset((2, 3))
    assert len(descendant_calls) == 1 and descendant_calls[0][1] == 1
    assert graph.happens_before(1, 3) is True
    assert len(descendant_calls) == 1


@pytest.mark.parametrize("source,target", ((True, 2), (1, 2.0), (0, 2), (1, 4)))
def test_direct_edge_proof_preserves_endpoint_and_self_admission(source, target):
    graph = DirectedGraph(
        (1, 2, 3),
        (
            DependencyEdge(1, 1, DependencyKind.CONTROL, 1),
            DependencyEdge(1, 2, DependencyKind.STATE, 2),
        ),
        order_keys(3),
    )
    assert graph.happens_before(1, 1) is False
    with pytest.raises(MeshIrError) as error:
        graph.happens_before(source, target)
    assert error.value.code == "E_ABI_BOUNDS"


def test_directed_graph_queries_share_one_descendant_traversal_per_source(monkeypatch):
    dag = DirectedGraph(
        (1, 2, 3, 4),
        (
            DependencyEdge(1, 2, DependencyKind.STATE, 1),
            DependencyEdge(3, 1, DependencyKind.CONTROL, 2),
        ),
        order_keys(4),
    )
    before = dataclasses.asdict(dag)
    native_descendants = rx.descendants
    descendant_calls = []

    def counted_descendants(graph, source):
        descendant_calls.append((id(graph), graph[source]))
        return native_descendants(graph, source)

    monkeypatch.setattr(rx, "descendants", counted_descendants)
    assert dag.happens_before(1, 3) is False
    assert dag.happens_before(2, 1) is False
    assert descendant_calls == []
    assert dag.happens_before(3, 2) is True
    assert dag.happens_before(3, 4) is False
    assert dag.happens_before(3, 2) is True
    assert dag.reachable(3) == frozenset((1, 2))
    assert len(descendant_calls) == 1 and descendant_calls[0][1] == 3
    assert dataclasses.asdict(dag) == before
    assert "_topological_positions" not in dataclasses.asdict(dag)
    cycle = DirectedGraph(
        (1, 2),
        (
            DependencyEdge(1, 2, DependencyKind.STATE, 1),
            DependencyEdge(2, 1, DependencyKind.CONTROL, 2),
        ),
        order_keys(2),
    )
    assert cycle.happens_before(1, 2) is True
    assert cycle.reachable(1) == frozenset((2,))
    assert descendant_calls[-1][1] == 1 and descendant_calls[-1][0] != descendant_calls[0][0]
    assert cycle.happens_before(1, 1) is False
    with pytest.raises(MeshIrError):
        cycle.happens_before(True, 2)


@pytest.mark.parametrize(
    "edges",
    (
        ((1, 2, DependencyKind.STATE, 1), (1, 3, DependencyKind.CONTROL, 2), (2, 4, DependencyKind.OP_COMPLETION, 3), (3, 4, DependencyKind.STATE, 4)),
        ((1, 2, DependencyKind.STATE, 1), (1, 2, DependencyKind.CONTROL, 2), (3, 4, DependencyKind.STATE, 3)),
        ((1, 2, DependencyKind.STATE, 1), (2, 3, DependencyKind.CONTROL, 2), (3, 1, DependencyKind.OP_COMPLETION, 3)),
    ),
)
def test_directed_graph_reachability_matches_networkx_for_every_node_pair(edges):
    graph = DirectedGraph((1, 2, 3, 4), tuple(sorted(DependencyEdge(*edge) for edge in edges)), order_keys(4))
    oracle = nx.DiGraph()
    oracle.add_nodes_from(graph.nodes)
    oracle.add_edges_from((edge.source, edge.target) for edge in graph.edges)
    before = dataclasses.asdict(graph)
    for source in graph.nodes:
        assert graph.reachable(source) == frozenset(nx.descendants(oracle, source))
        for target in graph.nodes:
            assert graph.happens_before(source, target) is (source != target and nx.has_path(oracle, source, target))
    assert dataclasses.asdict(graph) == before


def test_empty_directed_graph_preserves_all_empty_results():
    graph = DirectedGraph((), (), ())
    assert graph.canonical_topological_order() == ()
    assert graph.cycle_witness() is None
    assert graph.transitive_reduction() == graph
    for query in (lambda: graph.reachable(1), lambda: graph.ancestors(1), lambda: graph.happens_before(1, 1)):
        with pytest.raises(MeshIrError) as error:
            query()
        assert error.value.code == "E_ABI_BOUNDS"


def test_cycle_witness_uses_topological_proof_for_dag_and_native_witness_for_cycle(monkeypatch):
    native_topological_sort = rx.topological_sort
    native_cycle = rx.digraph_find_cycle
    topological_calls = []
    cycle_calls = []

    def counted_topological_sort(graph):
        topological_calls.append(id(graph))
        return native_topological_sort(graph)

    def counted_cycle(graph, *args, **kwargs):
        cycle_calls.append((id(graph), args, kwargs))
        return native_cycle(graph, *args, **kwargs)

    monkeypatch.setattr(rx, "topological_sort", counted_topological_sort)
    monkeypatch.setattr(rx, "digraph_find_cycle", counted_cycle)
    dag = DirectedGraph((1, 2, 3), (DependencyEdge(1, 2, DependencyKind.STATE, 1), DependencyEdge(2, 3, DependencyKind.CONTROL, 2)), order_keys(3))
    assert dag.cycle_witness() is None
    assert dag.canonical_topological_order() == (1, 2, 3)
    assert cycle_calls == []
    cycle = DirectedGraph((1, 2, 3), (DependencyEdge(1, 2, DependencyKind.STATE, 1), DependencyEdge(2, 3, DependencyKind.CONTROL, 2), DependencyEdge(3, 1, DependencyKind.STATE, 3)), order_keys(3))
    assert cycle.cycle_witness() == (1, 2, 3, 1)
    assert len(topological_calls) == 2 and topological_calls[0] != topological_calls[1]
    assert cycle_calls == [(topological_calls[1], (), {})]


def test_cycle_and_canonical_topology_are_stable_across_fresh_hashseeds():
    script = """
from mesh_ir.analysis.dependency import DependencyEdge, DependencyKind, DirectedGraph, NodeOrderKey
cycle_pairs = ((1, 2), (2, 3), (2, 4), (3, 1), (4, 2), (5, 6), (6, 5), (7, 7))
cycle = DirectedGraph(tuple(range(1, 9)), tuple(DependencyEdge(source, target, DependencyKind.STATE, source) for source, target in cycle_pairs), tuple(NodeOrderKey(str(node), "TEST", ()) for node in range(1, 9)))
dag_pairs = ((1, 4), (2, 4), (2, 5), (3, 5), (4, 6), (5, 6), (6, 8), (7, 8))
keys = (("张", "B", (10,)), ("a\\x00", "A", (1, 0)), ("a2", "B", ()), ("a", "A", (2,)), ("a10", "B", (1,)), ("a", "A", (2,)), ("", "A", ()), ("张", "B", (10,)))
dag = DirectedGraph(tuple(range(1, 9)), tuple(DependencyEdge(source, target, DependencyKind.STATE, source) for source, target in dag_pairs), tuple(NodeOrderKey(*key) for key in keys))
print((cycle.cycle_witness(), dag.canonical_topological_order()))
"""
    results = []
    for hashseed in ("1", "77", "31337"):
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = hashseed
        completed = subprocess.run((sys.executable, "-c", script), check=True, capture_output=True, text=True, env=environment)
        results.append(completed.stdout)
    assert len(set(results)) == 1


def test_region_analysis_compacts_strided_rows_and_broadcast_duplicates_exactly():
    rows = region_byte_spans(0, (1024, 4096), (8192, 1), 2)
    assert len(rows) == 1024
    assert rows[0] == ByteSpan(0, 8192)
    assert rows[-1] == ByteSpan(1023 * 16384, 1023 * 16384 + 8192)
    assert region_byte_spans(0, (1024, 4096), (0, 1), 2) == (ByteSpan(0, 8192),)


def test_lifetime_analysis_uses_issue_to_completion_and_real_access_frontiers():
    kernel = load_compute_store_kernel()
    dependencies = build_kernel_dependency_graph(kernel)
    analysis = analyze_lifetimes(kernel)
    by_object = {item.object_id: item for item in analysis.objects}
    assert dependencies.invocation_begin_node == len(dependencies.graph.nodes)
    assert dependencies.graph.order_keys[-1] == NodeOrderKey("", "INVOCATION_BEGIN", ())
    assert all(dependencies.graph.happens_before(dependencies.invocation_begin_node, node) for node in dependencies.op_node_by_id if node)
    assert set(by_object) == {2, 3}
    assert by_object[2].minimal_start_nodes == (1,)
    assert by_object[2].maximal_completion_nodes == (5,)
    assert by_object[2].intervals[0].dma_pinned
    assert not by_object[2].intervals[-1].dma_pinned
    assert analysis.conflicts[0].reason is LifetimeConflictReason.UNORDERED


def test_source_neutral_memory_analysis_matches_module_wrappers():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    kernel = refreshed(dataclasses.replace(load_compute_store_kernel(), arch_digest=arch.digest().hex()))
    records = kernel.memory_records()
    assert analyze_memory_lifetimes(records) == analyze_lifetimes(kernel)
    assert plan_memory_sram(records, arch) == plan_static_sram(kernel, arch)


def test_dependency_graph_derives_one_invocation_boundary_for_effects_and_initial_tokens():
    kernel = load_compute_store_kernel()
    kernel = refreshed(dataclasses.replace(kernel, tokens=(*kernel.tokens, ControlToken(4, True))))
    dependencies = build_kernel_dependency_graph(kernel)
    begin = dependencies.invocation_begin_node
    assert begin == len(dependencies.graph.nodes)
    assert dependencies.graph.order_keys[begin - 1] == NodeOrderKey("", "INVOCATION_BEGIN", ())
    effect_nodes = tuple(node for node in dependencies.op_node_by_id if node)
    assert all(dependencies.graph.happens_before(begin, node) for node in effect_nodes)
    assert dependencies.graph.happens_before(begin, dependencies.token_node_by_id[3])


def test_unused_nonempty_initial_contents_are_entry_live_but_zero_extent_is_not():
    arch = local_arch()
    owner = arch.core_ids[0]
    sizes = (16, 16, 0)
    tensors = tuple(KernelTensor(index, 0, None, index, 0, f"weight:{index}", TensorRole.WEIGHT, DType.INT8, (size,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, size, size, f"{index:064x}") for index, size in enumerate(sizes, 1))
    shards = tuple(TensorShard(index, index, 1, owner, DistributionKind.PARTITIONED, (0,), (size,), (size,), 0) for index, size in enumerate(sizes, 1))
    objects = tuple(BufferObject(index, index, owner, MemorySpace.CORE_SRAM, (size,), (1,), size, 8, False, 0) for index, size in enumerate(sizes, 1))
    views, declarations_ = declarations(objects)
    states = tuple(TensorState(index, index, 0, StateOrigin.PRE_RESIDENT, 0) for index in (1, 2, 3))
    kernel = create_kernel(arch.digest().hex(), "b" * 64, "forward", "unused", tensors, (), (Placement(1, (owner,)),), shards, (), objects, views, states, (), declarations_)
    analysis = analyze_lifetimes(kernel)
    begin = build_kernel_dependency_graph(kernel).invocation_begin_node
    by_object = {item.object_id: item for item in analysis.objects}
    assert by_object[1].minimal_start_nodes == by_object[1].maximal_completion_nodes == (begin,)
    assert by_object[2].minimal_start_nodes == by_object[2].maximal_completion_nodes == (begin,)
    assert by_object[3].minimal_start_nodes == by_object[3].maximal_completion_nodes == ()
    assert [(item.first_object_id, item.second_object_id) for item in analysis.conflicts] == [(1, 2)]


def local_arch(**changes):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    values = {"sram_bytes": 256, "sram_banks": 3, "sram_bank_interleave_bytes": 8, "sram_read_bytes_per_cycle_per_bank": 5, "sram_write_bytes_per_cycle_per_bank": 11, "sram_base_alignment_bytes": 1}
    values.update(changes)
    return dataclasses.replace(arch, **values)


def compute_allocation_kernel(arch, specs, sequential=False):
    owner = arch.core_ids[0]
    tensors = tuple(KernelTensor(index, index, None, index, 0, f"buffer:{index}", TensorRole.WEIGHT, DType.INT8, (size,), (1,), StorageClass.PRE_RESIDENT, Access.READ_WRITE, size, size, f"{index:064x}") for index, (size, _, _) in enumerate(specs, 1))
    shards = tuple(TensorShard(index, index, 1, owner, DistributionKind.PARTITIONED, (0,), (size,), (size,), 0) for index, (size, _, _) in enumerate(specs, 1))
    objects = tuple(BufferObject(index, index, owner, MemorySpace.CORE_SRAM, (size,), (1,), size, alignment, persistent, index - 1) for index, (size, alignment, persistent) in enumerate(specs, 1))
    views, declarations_ = declarations(objects)
    count = len(objects)
    states = tuple(TensorState(index, index, 0, StateOrigin.PRE_RESIDENT, 0) for index in range(1, count + 1)) + tuple(TensorState(count + index, index, 1, StateOrigin.PRODUCED, 0) for index in range(1, count + 1))
    tokens = tuple(ControlToken(index) for index in range(1, count + 1))
    effects = []
    for index, (size, _, _) in enumerate(specs, 1):
        region = ElementRegion((0,), (size,), (1,))
        tile = KernelTile(0, 0, 0, 0, 1, size, 1, 1, 1, size, 1, 1)
        attrs = VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile, KernelCost(size, size, size * 2, 0, size, 0), VectorAlgorithm.ELEMENTWISE)
        after = (index - 1,) if sequential and index > 1 else ()
        effects.append(KernelOp(count * 2 + index, index, f"compute:{index:02d}", KernelOpcode.VECTOR, owner, index, (OperandAccess(index, index, region, OperandAccessMode.READ),), (StateTransition(index, count + index, index, region),), attrs, after, index))
    computations = tuple(KernelComputation(index, OpCode.RELU, (index,), index, ElementwiseAttrs()) for index in range(1, count + 1))
    return create_kernel(arch.digest().hex(), "b" * 64, "forward", "alloc", tensors, computations, (Placement(1, (owner,)),), shards, (), objects, views, states, tokens, declarations_ + tuple(effects))


def load_allocation_kernel(arch, shape):
    owner = arch.core_ids[0]
    elements = 1 if not shape else shape[0]
    size = elements * DType.FP32.byte_width
    strides = () if not shape else (1,)
    tensor = KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.FP32, shape, strides, StorageClass.EXTERNAL, Access.READ_ONLY, size, size, None)
    shard = TensorShard(1, 1, 1, owner, DistributionKind.PARTITIONED, (0,) * len(shape), shape, shape, 0)
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, shape, strides, size, 4, False, 0),
        BufferObject(2, 1, owner, MemorySpace.CORE_SRAM, shape, strides, size, 4, False, 0),
    )
    views, declarations_ = declarations(objects)
    states = (TensorState(1, 1, 0, StateOrigin.EXTERNAL, 0), TensorState(2, 2, 0, StateOrigin.EMPTY, 0), TensorState(3, 2, 1, StateOrigin.PRODUCED, 0))
    region = ElementRegion((0,) * len(shape), shape, (1,) * len(shape))
    load = KernelOp(5, 0, "load", KernelOpcode.DMA, owner, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, region),), DmaAttrs(DmaKind.LOAD, owner, INVALID_CORE_ID, owner, 0, b""), (), 1)
    return create_kernel(arch.digest().hex(), "b" * 64, "forward", "dma", (tensor,), (), (Placement(1, (owner,)),), (shard,), (), objects, views, states, (ControlToken(1),), declarations_ + (load,))


def concurrent_load_kernel(arch):
    owner = arch.core_ids[0]
    tensors = tuple(KernelTensor(index, 0, None, index, 0, f"input:{index}", TensorRole.INPUT, DType.INT8, (16,), (1,), StorageClass.EXTERNAL, Access.READ_ONLY, 16, 16, None) for index in (1, 2))
    shards = tuple(TensorShard(index, index, 1, owner, DistributionKind.PARTITIONED, (0,), (16,), (16,), 0) for index in (1, 2))
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (16,), (1,), 16, 8, False, 0),
        BufferObject(2, 1, owner, MemorySpace.CORE_SRAM, (16,), (1,), 16, 8, False, 0),
        BufferObject(3, 2, INVALID_CORE_ID, MemorySpace.HBM, (16,), (1,), 16, 8, False, 0),
        BufferObject(4, 2, owner, MemorySpace.CORE_SRAM, (16,), (1,), 16, 8, False, 1),
    )
    views, declarations_ = declarations(objects)
    states = (TensorState(1, 1, 0, StateOrigin.EXTERNAL, 0), TensorState(2, 2, 0, StateOrigin.EMPTY, 0), TensorState(3, 3, 0, StateOrigin.EXTERNAL, 0), TensorState(4, 4, 0, StateOrigin.EMPTY, 0), TensorState(5, 2, 1, StateOrigin.PRODUCED, 0), TensorState(6, 4, 1, StateOrigin.PRODUCED, 0))
    region = ElementRegion((0,), (16,), (1,))
    loads = (
        KernelOp(9, 0, "load:01", KernelOpcode.DMA, owner, 0, (OperandAccess(1, 1, region, OperandAccessMode.READ),), (StateTransition(2, 5, 2, region),), DmaAttrs(DmaKind.LOAD, owner, INVALID_CORE_ID, owner, 0, b""), (), 1),
        KernelOp(10, 0, "load:02", KernelOpcode.DMA, owner, 0, (OperandAccess(3, 3, region, OperandAccessMode.READ),), (StateTransition(4, 6, 4, region),), DmaAttrs(DmaKind.LOAD, owner, INVALID_CORE_ID, owner, 0, b""), (), 2),
    )
    return create_kernel(arch.digest().hex(), "b" * 64, "forward", "pins", tensors, (), (Placement(1, (owner,)),), shards, (), objects, views, states, (ControlToken(1), ControlToken(2)), declarations_ + loads)


def weight_scratch_kernel(arch, scratch_first):
    owner = arch.core_ids[0]
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "weight", TensorRole.WEIGHT, DType.FP32, (4,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 16, 16, "1" * 64),
        KernelTensor(2, 1, None, 2, 0, "result", TensorRole.ACTIVATION, DType.FP32, (4,), (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, 16, 16, None),
        KernelTensor(3, 0, None, 3, 0, "scratch", TensorRole.ACTIVATION, DType.FP32, (4,), (1,), StorageClass.CORE_SRAM, Access.READ_WRITE, 16, 16, None),
    )
    shards = tuple(TensorShard(index, index, 1, owner, DistributionKind.PARTITIONED, (0,), (4,), (4,), 0) for index in (1, 2, 3))
    objects = tuple(BufferObject(index, index, owner, MemorySpace.CORE_SRAM, (4,), (1,), 16, 8, False, 0) for index in (1, 2, 3))
    views, declarations_ = declarations(objects)
    states = (
        TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT, 0),
        TensorState(2, 2, 0, StateOrigin.EMPTY, 0),
        TensorState(3, 2, 1, StateOrigin.PRODUCED, 0),
        TensorState(4, 3, 0, StateOrigin.EMPTY, 0),
        TensorState(5, 3, 1, StateOrigin.PRODUCED, 0),
    )
    full = ElementRegion((0,), (4,), (1,))
    vector = KernelOp(7 if not scratch_first else 8, 1, "vector", KernelOpcode.VECTOR, owner, 2, (OperandAccess(1, 1, full, OperandAccessMode.READ),), (StateTransition(2, 3, 2, full),), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), KernelTile(0, 0, 0, 0, 1, 4, 1, 1, 1, 4, 1, 1), KernelCost(16, 16, 32, 0, 4, 0), VectorAlgorithm.ELEMENTWISE), (1,) if scratch_first else (), 2 if scratch_first else 1)
    fill = KernelOp(7 if scratch_first else 8, 0, "fill", KernelOpcode.DMA, owner, 0, (), (StateTransition(4, 5, 3, full),), DmaAttrs(DmaKind.LOCAL_FILL, owner, owner, owner, 0, b"\x00"), () if scratch_first else (1,), 1 if scratch_first else 2)
    effects = (fill, vector) if scratch_first else (vector, fill)
    computations = (KernelComputation(1, OpCode.RELU, (1,), 2, ElementwiseAttrs()),)
    return create_kernel(arch.digest().hex(), "b" * 64, "forward", "birth", tensors, computations, (Placement(1, (owner,)),), shards, (), objects, views, states, (ControlToken(1), ControlToken(2)), declarations_ + effects)


def double_buffer_kernel(arch):
    owner = arch.core_ids[0]
    tensors = (
        KernelTensor(1, 0, None, 1, 0, "input", TensorRole.INPUT, DType.INT8, (16,), (1,), StorageClass.EXTERNAL, Access.READ_ONLY, 16, 16, None),
        KernelTensor(2, 1, None, 2, 0, "output", TensorRole.OUTPUT, DType.INT8, (16,), (1,), StorageClass.EXTERNAL, Access.READ_WRITE, 16, 16, None),
    )
    shards = tuple(TensorShard(index, index, 1, owner, DistributionKind.PARTITIONED, (0,), (16,), (16,), 0) for index in (1, 2))
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (16,), (1,), 16, 8, False, 0),
        BufferObject(2, 1, owner, MemorySpace.CORE_SRAM, (16,), (1,), 16, 8, False, 0),
        BufferObject(3, 1, owner, MemorySpace.CORE_SRAM, (16,), (1,), 16, 8, False, 1),
        BufferObject(4, 1, owner, MemorySpace.CORE_SRAM, (16,), (1,), 16, 8, False, 0),
        BufferObject(5, 2, owner, MemorySpace.CORE_SRAM, (16,), (1,), 16, 8, False, 0),
        BufferObject(6, 2, INVALID_CORE_ID, MemorySpace.HBM, (16,), (1,), 16, 8, False, 0),
    )
    views, declarations_ = declarations(objects)
    views = tuple(dataclasses.replace(view, generation=generation) for view, generation in zip(views, (0, 0, 1, 2, 0, 0)))
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL, 0),
        TensorState(2, 2, 0, StateOrigin.EMPTY, 0),
        TensorState(3, 2, 1, StateOrigin.PRODUCED, 0),
        TensorState(4, 3, 0, StateOrigin.EMPTY, 0),
        TensorState(5, 3, 1, StateOrigin.PRODUCED, 0),
        TensorState(6, 4, 0, StateOrigin.EMPTY, 0),
        TensorState(7, 4, 1, StateOrigin.PRODUCED, 0),
        TensorState(8, 5, 0, StateOrigin.EMPTY, 0),
        TensorState(9, 5, 1, StateOrigin.PRODUCED, 0),
        TensorState(10, 5, 2, StateOrigin.PRODUCED, 0),
        TensorState(11, 5, 3, StateOrigin.PRODUCED, 0),
        TensorState(12, 6, 0, StateOrigin.EMPTY, 0),
        TensorState(13, 6, 1, StateOrigin.PRODUCED, 0),
        TensorState(14, 6, 2, StateOrigin.PRODUCED, 0),
        TensorState(15, 6, 3, StateOrigin.PRODUCED, 0),
    )
    tokens = tuple(ControlToken(index) for index in range(1, 10))
    full = ElementRegion((0,), (16,), (1,))
    vector_attrs = VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), KernelTile(0, 0, 0, 0, 1, 16, 1, 1, 1, 16, 1, 1), KernelCost(16, 16, 32, 0, 16, 0), VectorAlgorithm.ELEMENTWISE)
    effects = (
        KernelOp(13, 0, "01:prefetch:0", KernelOpcode.DMA, owner, 0, (OperandAccess(1, 1, full, OperandAccessMode.READ),), (StateTransition(2, 3, 2, full),), DmaAttrs(DmaKind.PREFETCH, owner, INVALID_CORE_ID, owner, 0, b""), (), 1),
        KernelOp(14, 0, "02:prefetch:1", KernelOpcode.DMA, owner, 0, (OperandAccess(1, 1, full, OperandAccessMode.READ),), (StateTransition(4, 5, 3, full),), DmaAttrs(DmaKind.PREFETCH, owner, INVALID_CORE_ID, owner, 0, b""), (), 2),
        KernelOp(15, 1, "03:compute:0", KernelOpcode.VECTOR, owner, 2, (OperandAccess(3, 2, full, OperandAccessMode.READ),), (StateTransition(8, 9, 5, full),), vector_attrs, (1,), 3),
        KernelOp(16, 0, "04:store:0", KernelOpcode.DMA, owner, 0, (OperandAccess(9, 5, full, OperandAccessMode.READ),), (StateTransition(12, 13, 6, full),), DmaAttrs(DmaKind.STORE, owner, owner, INVALID_CORE_ID, 0, b""), (3,), 4),
        KernelOp(17, 0, "05:prefetch:2", KernelOpcode.DMA, owner, 0, (OperandAccess(1, 1, full, OperandAccessMode.READ),), (StateTransition(6, 7, 4, full),), DmaAttrs(DmaKind.PREFETCH, owner, INVALID_CORE_ID, owner, 0, b""), (3,), 5),
        KernelOp(18, 1, "06:compute:1", KernelOpcode.VECTOR, owner, 2, (OperandAccess(5, 3, full, OperandAccessMode.READ),), (StateTransition(9, 10, 5, full),), vector_attrs, (2, 4), 6),
        KernelOp(19, 0, "07:store:1", KernelOpcode.DMA, owner, 0, (OperandAccess(10, 5, full, OperandAccessMode.READ),), (StateTransition(13, 14, 6, full),), DmaAttrs(DmaKind.STORE, owner, owner, INVALID_CORE_ID, 0, b""), (6,), 7),
        KernelOp(20, 1, "08:compute:2", KernelOpcode.VECTOR, owner, 2, (OperandAccess(7, 4, full, OperandAccessMode.READ),), (StateTransition(10, 11, 5, full),), vector_attrs, (5, 7), 8),
        KernelOp(21, 0, "09:store:2", KernelOpcode.DMA, owner, 0, (OperandAccess(11, 5, full, OperandAccessMode.READ),), (StateTransition(14, 15, 6, full),), DmaAttrs(DmaKind.STORE, owner, owner, INVALID_CORE_ID, 0, b""), (8,), 9),
    )
    computations = (KernelComputation(1, OpCode.RELU, (1,), 2, ElementwiseAttrs()),)
    return create_kernel(arch.digest().hex(), "b" * 64, "forward", "double-buffer", tensors, computations, (Placement(1, (owner,)),), shards, (), objects, views, states, tokens, declarations_ + effects)


def test_static_sram_priority_first_fit_alignment_hole_and_bank_mapping_are_deterministic():
    arch = local_arch()
    kernel = compute_allocation_kernel(arch, ((17, 16, True), (9, 8, False)))
    plan = plan_static_sram(kernel, arch)
    assert plan == plan_static_sram(kernel, arch)
    assert [(item.object_id, item.offset_bytes) for item in plan.allocations] == [(1, 0), (2, 24)]
    report = plan.reports[0]
    assert report.peak_bytes == 33
    assert report.padding_bytes == 0
    assert report.fragmentation_bytes == 7
    assert tuple(item.bank_id for item in report.bank_spans) == (0, 1, 2)
    assert report.bank_spans[0].spans == (ByteSpan(0, 8), ByteSpan(24, 32))


def test_sequential_reads_of_distinct_pre_resident_contents_cannot_reuse_storage():
    arch = local_arch()
    kernel = compute_allocation_kernel(arch, ((16, 8, False), (16, 8, False)), sequential=True)
    analysis = analyze_lifetimes(kernel)
    plan = plan_static_sram(kernel, arch)
    assert [(item.first_object_id, item.second_object_id) for item in analysis.conflicts] == [(1, 2)]
    assert [(item.object_id, item.offset_bytes) for item in plan.allocations] == [(1, 0), (2, 16)]
    assert plan.reports[0].peak_bytes == 32


def test_entry_live_weight_cannot_reuse_earlier_scratch_but_can_release_after_its_final_read():
    arch = local_arch()
    scratch_first = plan_static_sram(weight_scratch_kernel(arch, True), arch)
    weight_first = plan_static_sram(weight_scratch_kernel(arch, False), arch)
    scratch_first_offsets = {item.object_id: item.offset_bytes for item in scratch_first.allocations}
    weight_first_offsets = {item.object_id: item.offset_bytes for item in weight_first.allocations}
    assert scratch_first_offsets[1] != scratch_first_offsets[3]
    assert weight_first_offsets[1] == weight_first_offsets[3]


def test_real_double_buffering_overlaps_prefetch_and_compute_then_reuses_completed_generation():
    arch = local_arch(axi_data_bytes=16)
    kernel = double_buffer_kernel(arch)
    dependencies = build_kernel_dependency_graph(kernel)
    analysis = analyze_lifetimes(kernel)
    lifetimes = {item.object_id: item for item in analysis.objects}
    conflict_pairs = {(item.first_object_id, item.second_object_id) for item in analysis.conflicts}
    assert not dependencies.graph.happens_before(lifetimes[2].maximal_completion_nodes[0], lifetimes[3].minimal_start_nodes[0])
    assert not dependencies.graph.happens_before(lifetimes[3].maximal_completion_nodes[0], lifetimes[2].minimal_start_nodes[0])
    assert (2, 3) in conflict_pairs
    assert (3, 4) in conflict_pairs
    assert (2, 4) not in conflict_pairs
    assert dependencies.graph.happens_before(lifetimes[2].maximal_completion_nodes[0], lifetimes[4].minimal_start_nodes[0])
    plan = plan_static_sram(kernel, arch)
    offsets = {item.object_id: item.offset_bytes for item in plan.allocations}
    assert offsets[2] == offsets[4]
    assert offsets[2] != offsets[3]
    assert kernel.objects[1].buffer_index == kernel.objects[3].buffer_index == 0
    assert kernel.views[1].generation == 0
    assert kernel.views[3].generation == 2


def test_concurrent_dma_pins_are_a_real_conflict_and_cannot_reuse_bytes():
    arch = local_arch(axi_data_bytes=32)
    kernel = concurrent_load_kernel(arch)
    analysis = analyze_lifetimes(kernel)
    assert [(item.first_object_id, item.second_object_id) for item in analysis.conflicts] == [(2, 4)]
    assert all(interval.dma_pinned for lifetime in analysis.objects for interval in lifetime.intervals)
    plan = plan_static_sram(kernel, arch)
    assert [(item.object_id, item.offset_bytes, item.size_bytes) for item in plan.allocations] == [(2, 0, 32), (4, 32, 32)]


def test_static_sram_capacity_plus_one_fails_without_a_partial_plan():
    arch = local_arch()
    kernel = compute_allocation_kernel(arch, ((257, 1, False),))
    with pytest.raises(MeshIrError) as error:
        plan_static_sram(kernel, arch)
    assert error.value.code == "E_SRAM_OOM"
    assert error.value.context["required_end"] == 257
    assert error.value.context["limiting_object_ids"] == (1,)


@pytest.mark.parametrize(("shape", "logical_bytes"), (((), 4), ((6,), 24)))
def test_dma_visible_small_objects_reserve_a_complete_axi_beat(shape, logical_bytes):
    arch = local_arch(axi_data_bytes=32)
    kernel = load_allocation_kernel(arch, shape)
    plan = plan_static_sram(kernel, arch)
    assert plan.allocations[0].object_id == 2
    assert plan.allocations[0].size_bytes == 32
    assert plan.allocations[0].alignment_bytes == 32
    assert plan.reports[0].padding_bytes == 32 - logical_bytes
    allocation = plan.allocations[0]
    obj = kernel.objects[allocation.object_id - 1]
    tensor = kernel.tensors[obj.storage_tensor_id - 1]
    region_id = next(index for index, item in enumerate(arch.regions) if item.kind == "CORE_SRAM_APERTURE")
    reference = AddressRef(1, MemorySpace.CORE_SRAM, region_id, allocation.owner_core, DirectAddress(allocation.offset_bytes, allocation.offset_bytes, allocation.size_bytes, allocation.alignment_bytes, Access.READ_WRITE), 0, tensor.logical_extent_bytes, obj.footprint_bytes, obj.alignment_bytes, Access.READ_WRITE)
    resolved = resolve_addresses(arch, (), (), (reference,), issuing_core=allocation.owner_core)
    assert resolved.addresses[0].physical_span_bytes == 32

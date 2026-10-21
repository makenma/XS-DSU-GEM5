import random

import networkx as nx
import pytest
import rustworkx as rx

from mesh_ir.analysis.dependency import DependencyEdge, DependencyKind, DirectedGraph, NodeOrderKey


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('acyclic', [False, True])
def test_dependency_boolean_search_matches_independent_graph_oracle(seed, acyclic, monkeypatch):
    randomizer = random.Random(seed)
    count = 32
    pairs = tuple((source, target) for source in range(1, count + 1) for target in range(1, count + 1)
        if source != target and (not acyclic or source < target) and randomizer.random() < 0.045)
    graph = DirectedGraph(tuple(range(1, count + 1)),
        tuple(DependencyEdge(source, target, DependencyKind.CONTROL) for source, target in pairs),
        tuple(NodeOrderKey(str(node), 'TEST', ()) for node in range(1, count + 1)))
    reference = nx.DiGraph()
    reference.add_nodes_from(graph.nodes)
    reference.add_edges_from(pairs)

    def reject_path_materialization(*args, **kwargs):
        pytest.fail('Boolean dependency queries must not materialize Dijkstra paths')

    monkeypatch.setattr(rx, 'has_path', reject_path_materialization)
    for source in graph.nodes:
        for target in graph.nodes:
            assert graph.happens_before(source, target) == (source != target and nx.has_path(reference, source, target))


def test_long_parallel_pipeline_queries_use_linear_memory_labels(monkeypatch):
    width, stages = 4, 128
    count = width * stages
    graph = DirectedGraph(tuple(range(1, count + 1)),
        tuple(DependencyEdge(stage * width + left + 1, (stage + 1) * width + right + 1,
            DependencyKind.CONTROL) for stage in range(stages - 1) for left in range(width) for right in range(width)),
        tuple(NodeOrderKey(str(node), 'TEST', ()) for node in range(1, count + 1)))
    native_search = rx.digraph_dfs_search
    calls = []

    def observed(*args, **kwargs):
        calls.append(1)
        return native_search(*args, **kwargs)

    monkeypatch.setattr(rx, 'digraph_dfs_search', observed)
    for source in range(1, width + 1):
        for target in range(count - width + 1, count + 1):
            assert graph.happens_before(source, target)
    assert calls == []

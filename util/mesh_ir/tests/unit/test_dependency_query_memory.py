import tracemalloc

from mesh_ir.analysis.dependency import DependencyEdge, DependencyKind, DirectedGraph, NodeOrderKey


def test_dependency_path_queries_keep_memory_bounded_on_a_long_chain():
    count = 4096
    graph = DirectedGraph(tuple(range(1, count + 1)),
        tuple(DependencyEdge(node, node + 1, DependencyKind.CONTROL) for node in range(1, count)),
        tuple(NodeOrderKey(str(node), 'CONTROL', ()) for node in range(1, count + 1)))
    assert graph.happens_before(1, 2)
    assert not graph.happens_before(count, 1)
    tracemalloc.start()
    try:
        for source in range(1, count, 16):
            assert graph.happens_before(source, count)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    assert graph.reachable(count - 2) == frozenset((count - 1, count))

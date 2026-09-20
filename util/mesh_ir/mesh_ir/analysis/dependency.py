from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cached_property

import rustworkx as rx

from mesh_ir.diagnostics import MeshIrError


class DependencyKind(str, Enum):
    CONTROL = "CONTROL"
    STATE = "STATE"
    OP_COMPLETION = "OP_COMPLETION"
    INVOCATION_BEGIN = "INVOCATION_BEGIN"


@dataclass(frozen=True, order=True)
class DependencyEdge:
    source: int
    target: int
    kind: DependencyKind
    identity: int = 0


@dataclass(frozen=True, order=True)
class NodeOrderKey:
    stable_key: str
    opcode: str
    operand_ids: tuple[int, ...]


@dataclass(frozen=True)
class DirectedGraph:
    nodes: tuple[int, ...]
    edges: tuple[DependencyEdge, ...]
    order_keys: tuple[NodeOrderKey, ...]

    def __post_init__(self):
        if type(self.nodes) is not tuple or any(type(node) is not int for node in self.nodes) or self.nodes != tuple(range(1, len(self.nodes) + 1)):
            raise MeshIrError("E_ABI_ORDER", "dependency nodes must be dense and ordered")
        if type(self.order_keys) is not tuple or len(self.order_keys) != len(self.nodes) or any(type(item) is not NodeOrderKey or type(item.stable_key) is not str or type(item.opcode) is not str or type(item.operand_ids) is not tuple or any(type(operand) is not int or operand < 0 for operand in item.operand_ids) for item in self.order_keys):
            raise MeshIrError("E_ABI_BOUNDS", "dependency order keys do not match nodes")
        if type(self.edges) is not tuple or any(type(item) is not DependencyEdge or type(item.source) is not int or type(item.target) is not int or type(item.kind) is not DependencyKind or type(item.identity) is not int or item.identity < 0 for item in self.edges):
            raise MeshIrError("E_ABI_BOUNDS", "dependency edges must be immutable typed records")
        if self.edges != tuple(sorted(set(self.edges))):
            raise MeshIrError("E_ABI_ORDER", "dependency edges must be unique and canonical")
        node_set = set(self.nodes)
        if any(edge.source not in node_set or edge.target not in node_set for edge in self.edges):
            raise MeshIrError("E_ABI_BOUNDS", "dependency edge references an unknown node")

    @cached_property
    def __graph(self) -> rx.PyDiGraph:
        graph = rx.PyDiGraph(multigraph=False, node_count_hint=len(self.nodes), edge_count_hint=len(self.edges))
        graph.add_nodes_from(self.nodes)
        graph.add_edges_from_no_data((edge.source - 1, edge.target - 1) for edge in self.edges)
        return graph

    @cached_property
    def _topological_positions(self) -> tuple[int, ...] | None:
        try:
            order = tuple(rx.topological_sort(self.__graph))
        except rx.DAGHasCycle:
            return None
        positions = [0] * len(order)
        for position, index in enumerate(order):
            positions[index] = position
        return tuple(positions)

    @cached_property
    def _descendants_by_source(self) -> dict[int, frozenset[int]]:
        return {}

    def canonical_topological_order(self) -> tuple[int, ...]:
        witness = self.cycle_witness()
        if witness is not None:
            raise MeshIrError("E_DEPENDENCY_CYCLE", "dependency graph contains a cycle", nodes=witness)
        ranks = [0] * len(self.nodes)
        for rank, node in enumerate(sorted(self.nodes, key=lambda node: (self.order_keys[node - 1], node))):
            ranks[node - 1] = rank
        width = len(str(max(len(self.nodes) - 1, 0)))
        return tuple(rx.lexicographical_topological_sort(self.__graph, key=lambda node: f"{ranks[node - 1]:0{width}d}"))

    def reachable(self, source: int) -> frozenset[int]:
        if type(source) is not int or not 1 <= source <= len(self.nodes):
            raise MeshIrError("E_ABI_BOUNDS", "dependency source node is unknown", node=source)
        descendants = self._descendants_by_source.get(source)
        if descendants is None:
            descendants = frozenset(self.nodes[index] for index in rx.descendants(self.__graph, source - 1))
            self._descendants_by_source[source] = descendants
        return descendants

    def ancestors(self, target: int) -> frozenset[int]:
        if type(target) is not int or not 1 <= target <= len(self.nodes):
            raise MeshIrError("E_ABI_BOUNDS", "dependency target node is unknown", node=target)
        return frozenset(self.nodes[index] for index in rx.ancestors(self.__graph, target - 1))

    def happens_before(self, source: int, target: int) -> bool:
        if type(source) is not int or type(target) is not int or not 1 <= source <= len(self.nodes) or not 1 <= target <= len(self.nodes):
            raise MeshIrError("E_ABI_BOUNDS", "dependency query references an unknown node", source=source, target=target)
        if source == target:
            return False
        if self.__graph.has_edge(source - 1, target - 1):
            return True
        positions = self._topological_positions
        if positions is not None and positions[source - 1] >= positions[target - 1]:
            return False
        return target in self.reachable(source)

    def cycle_witness(self) -> tuple[int, ...] | None:
        if self._topological_positions is not None:
            return None
        edges = rx.digraph_find_cycle(self.__graph)
        if not edges:
            return None
        cycle = tuple(self.nodes[edge[0]] for edge in edges)
        rotations = tuple(cycle[index:] + cycle[:index] for index in range(len(cycle)))
        canonical = min(rotations)
        return canonical + (canonical[0],)

    def transitive_reduction(self) -> "DirectedGraph":
        witness = self.cycle_witness()
        if witness is not None:
            raise MeshIrError("E_DEPENDENCY_CYCLE", "dependency graph contains a cycle", nodes=witness)
        reduced, _ = rx.transitive_reduction(self.__graph)
        reduced_pairs = {(reduced[source], reduced[target]) for source, target in reduced.edge_list()}
        edges = tuple(
            edge
            for edge in self.edges
            if (edge.source, edge.target) in reduced_pairs
        )
        return DirectedGraph(self.nodes, edges, self.order_keys)


@dataclass(frozen=True)
class KernelDependencyGraph:
    graph: DirectedGraph
    op_node_by_id: tuple[int, ...]
    token_node_by_id: tuple[int, ...]
    invocation_begin_node: int

    def __post_init__(self):
        if type(self.graph) is not DirectedGraph or type(self.op_node_by_id) is not tuple or type(self.token_node_by_id) is not tuple:
            raise MeshIrError("E_ABI_BOUNDS", "Kernel dependency graph has invalid field types")
        if any(type(node) is not int or node < 0 or node not in self.graph.nodes and node != 0 for node in self.op_node_by_id) or any(type(node) is not int or node not in self.graph.nodes for node in self.token_node_by_id):
            raise MeshIrError("E_ABI_BOUNDS", "Kernel dependency mappings reference invalid nodes")
        if type(self.invocation_begin_node) is not int or self.invocation_begin_node not in self.graph.nodes:
            raise MeshIrError("E_ABI_BOUNDS", "Kernel invocation boundary references an invalid node")


def build_kernel_dependency_graph(kernel) -> KernelDependencyGraph:
    from mesh_ir.ir.kernel_ir import KernelOpcode

    effectful = tuple(op for op in kernel.ops if op.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW))
    op_nodes = {op.op_id: index for index, op in enumerate(effectful, 1)}
    token_nodes = {token.token_id: len(effectful) + index for index, token in enumerate(kernel.tokens, 1)}
    producers: dict[int, int] = {}
    for op in effectful:
        if op.done_token in producers:
            raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "completion token has multiple producers", token_id=op.done_token, first_op_id=producers[op.done_token], second_op_id=op.op_id)
        producers[op.done_token] = op.op_id
    for token in kernel.tokens:
        if token.initial and token.token_id in producers:
            raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "initial token also has an operation producer", token_id=token.token_id)
        if not token.initial and token.token_id not in producers:
            raise MeshIrError("E_EVENT_NO_PRODUCER", "completion token has no producer", token_id=token.token_id)
    state_producers = {
        transition.new_state_id: token_nodes[op.done_token]
        for op in effectful
        for transition in op.writes
    }
    edges: set[DependencyEdge] = set()
    keys: list[NodeOrderKey] = []
    for op in effectful:
        operand_ids = tuple(access.state_id for access in op.reads) + tuple(item.old_state_id for item in op.writes)
        keys.append(NodeOrderKey(op.stable_key, op.opcode.value, operand_ids))
        op_node = op_nodes[op.op_id]
        done_node = token_nodes[op.done_token]
        edges.add(DependencyEdge(op_node, done_node, DependencyKind.OP_COMPLETION, op.done_token))
        for token_id in op.after_tokens:
            edges.add(DependencyEdge(token_nodes[token_id], op_node, DependencyKind.CONTROL, token_id))
        for state_id in operand_ids:
            if state_id in state_producers:
                edges.add(DependencyEdge(state_producers[state_id], op_node, DependencyKind.STATE, state_id))
    for token in kernel.tokens:
        producer = producers.get(token.token_id)
        stable = kernel.ops[producer - 1].stable_key if producer is not None else f"initial:{token.token_id}"
        keys.append(NodeOrderKey(stable, "TOKEN", (token.token_id,)))
    invocation_begin_node = len(keys) + 1
    keys.append(NodeOrderKey("", "INVOCATION_BEGIN", ()))
    for op in effectful:
        edges.add(DependencyEdge(invocation_begin_node, op_nodes[op.op_id], DependencyKind.INVOCATION_BEGIN, 0))
    for token in kernel.tokens:
        if token.initial:
            edges.add(DependencyEdge(invocation_begin_node, token_nodes[token.token_id], DependencyKind.INVOCATION_BEGIN, 0))
    graph = DirectedGraph(tuple(range(1, len(keys) + 1)), tuple(sorted(edges)), tuple(keys))
    witness = graph.cycle_witness()
    if witness is not None:
        raise MeshIrError("E_DEPENDENCY_CYCLE", "dependency graph contains a cycle", nodes=witness)
    op_map = tuple(op_nodes.get(op_id, 0) for op_id in range(1, len(kernel.ops) + 1))
    token_map = tuple(token_nodes[token_id] for token_id in range(1, len(kernel.tokens) + 1))
    return KernelDependencyGraph(graph, op_map, token_map, invocation_begin_node)

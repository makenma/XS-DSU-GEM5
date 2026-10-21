import random

import networkx as nx
import pytest

from mesh_ir.analysis.access import AccessInterval, conflicting_access_frontier
from mesh_ir.analysis.regions import ByteSpan
from mesh_ir.ir.kernel_ir import OperandAccessMode


@pytest.mark.parametrize('seed', range(12))
def test_byte_access_frontiers_preserve_pairwise_conflict_reachability(seed):
    randomizer = random.Random(seed)
    accesses = []
    for op_id in range(1, 81):
        for _ in range(randomizer.randint(1, 3)):
            begin = randomizer.randrange(16)
            end = randomizer.randrange(begin + 1, 18)
            mode = randomizer.choice(tuple(OperandAccessMode))
            accesses.append(AccessInterval(randomizer.randrange(1, 5), op_id, op_id * 2,
                op_id * 2 + 1, mode, (ByteSpan(begin, end),), False))
    reference = nx.DiGraph()
    reduced = nx.DiGraph()
    reference.add_nodes_from(range(1, 81))
    reduced.add_nodes_from(reference)
    for index, first in enumerate(accesses):
        for second in accesses[index + 1:]:
            overlaps = any(left.begin < right.end and right.begin < left.end
                for left in first.region_spans for right in second.region_spans)
            if first.op_id != second.op_id and first.object_id == second.object_id and overlaps and (
                first.mode is OperandAccessMode.WRITE or second.mode is OperandAccessMode.WRITE):
                reference.add_edge(first.op_id, second.op_id)
    for first, second in conflicting_access_frontier(tuple(accesses)):
        assert reference.has_edge(first.op_id, second.op_id)
        reduced.add_edge(first.op_id, second.op_id)
    for node in reference:
        assert nx.descendants(reduced, node) == nx.descendants(reference, node)


def test_repeated_full_buffer_writes_have_a_linear_conflict_frontier():
    accesses = tuple(AccessInterval(1, identity, identity * 2, identity * 2 + 1,
        OperandAccessMode.WRITE, (ByteSpan(0, 4096),), True) for identity in range(1, 4097))
    edges = tuple(conflicting_access_frontier(accesses))
    assert len(edges) == 4095
    assert {(first.op_id, second.op_id) for first, second in edges} == {
        (identity, identity + 1) for identity in range(1, 4096)}


def test_partial_writes_preserve_every_reader_until_its_bytes_are_overwritten():
    accesses = (
        AccessInterval(1, 1, 2, 3, OperandAccessMode.WRITE, (ByteSpan(0, 16),), False),
        AccessInterval(1, 2, 4, 5, OperandAccessMode.READ, (ByteSpan(0, 16),), False),
        AccessInterval(1, 3, 6, 7, OperandAccessMode.WRITE, (ByteSpan(0, 8),), False),
        AccessInterval(1, 4, 8, 9, OperandAccessMode.WRITE, (ByteSpan(8, 16),), False),
    )
    edges = {(first.op_id, second.op_id) for first, second in conflicting_access_frontier(accesses)}
    assert (2, 3) in edges and (2, 4) in edges
    assert (3, 4) not in edges


def test_disjoint_and_read_only_objects_create_no_conflict_edges():
    accesses = tuple(AccessInterval(identity, identity, identity * 2, identity * 2 + 1,
        OperandAccessMode.WRITE, (ByteSpan(0, 16),), False) for identity in range(1, 4097))
    assert tuple(conflicting_access_frontier(accesses)) == ()

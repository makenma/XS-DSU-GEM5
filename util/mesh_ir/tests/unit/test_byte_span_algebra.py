from random import Random

import pytest

from mesh_ir.analysis.regions import ByteSpan, intersect_spans, subtract_spans, spans_contain, spans_overlap
from mesh_ir.analysis.regions import DisjointByteSpanIndex
from mesh_ir.diagnostics import MeshIrError


def test_span_algebra_matches_independent_byte_sets():
    random = Random(7183)
    for _ in range(400):
        operands = []
        for side in range(2):
            spans = []
            for index in range(random.randrange(12)):
                first, last = sorted((random.randrange(40), random.randrange(40)))
                spans.append(ByteSpan(first, last))
            operands.append(tuple(spans))
        left, right = operands
        left_bytes = {byte for span in left for byte in range(span.begin, span.end)}
        right_bytes = {byte for span in right for byte in range(span.begin, span.end)}
        for actual, expected in ((intersect_spans(left, right), left_bytes & right_bytes),
                                 (subtract_spans(left, right), left_bytes - right_bytes)):
            assert {byte for span in actual for byte in range(span.begin, span.end)} == expected
            assert all(span.begin < span.end for span in actual)
            assert all(a.end < b.begin for a, b in zip(actual, actual[1:]))
        assert spans_contain(right, left) == (left_bytes <= right_bytes)
        assert spans_overlap(left, right) == bool(left_bytes & right_bytes)


def test_large_strided_panel_set_operations_preserve_holes():
    required = tuple(ByteSpan(row * 8192, row * 8192 + 1024) for row in range(4096))
    available = tuple(ByteSpan(row * 8192 + 512, row * 8192 + 1536) for row in range(4096))
    assert intersect_spans(required, available) == tuple(
        ByteSpan(row * 8192 + 512, row * 8192 + 1024) for row in range(4096))
    assert subtract_spans(required, available) == tuple(
        ByteSpan(row * 8192, row * 8192 + 512) for row in range(4096))


def test_source_index_matches_independent_owner_byte_sets():
    random = Random(8461)
    for _ in range(400):
        owners = [random.randrange(-1, 5) for _ in range(64)]
        groups = tuple(tuple(ByteSpan(byte, byte + 1) for byte, value in enumerate(owners)
                             if value == owner) for owner in range(5))
        index = DisjointByteSpanIndex(groups)
        required = tuple(ByteSpan(*sorted((random.randrange(70), random.randrange(70))))
                         for _ in range(random.randrange(8)))
        wanted = {byte for row in required for byte in range(row.begin, row.end)}
        actual = index.intersections(required)
        assert [owner for owner, _ in actual] == sorted(owner for owner, _ in actual)
        assert {owner: {byte for row in spans for byte in range(row.begin, row.end)}
                for owner, spans in actual} == {
                    owner: selected for owner in range(5)
                    if (selected := {byte for byte, value in enumerate(owners)
                                     if value == owner and byte in wanted})}
        assert all(left.end < right.begin for _, spans in actual
                   for left, right in zip(spans, spans[1:]))


def test_source_index_rejects_cross_owner_overlap_and_preserves_boundaries():
    with pytest.raises(MeshIrError, match='overlap'):
        DisjointByteSpanIndex(((ByteSpan(0, 5),), (ByteSpan(4, 8),)))
    index = DisjointByteSpanIndex(((ByteSpan(4, 8),), (ByteSpan(0, 4),), ()))
    assert index.intersections((ByteSpan(3, 5),)) == (
        (0, (ByteSpan(4, 5),)), (1, (ByteSpan(3, 4),)))
    assert index.intersections((ByteSpan(8, 12),)) == ()
    assert index.intersections(()) == ()

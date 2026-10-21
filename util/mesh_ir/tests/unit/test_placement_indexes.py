from dataclasses import replace

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DType, TensorRole
from mesh_ir.passes.placement_geometry import (
    ExternalBinding, ExternalBindingKind, MappedStorageRegion, PlacementLedger,
    PlacementValues,
)
from tests.unit.test_gate2_placement_geometry import _value


def test_graph_value_index_is_immutable_and_rejects_duplicate_identity():
    value = _value(1, 'input', TensorRole.INPUT, DType.FP32, (2, 4))
    index = PlacementValues((value,))
    assert index.by_id[1] is value
    with pytest.raises(TypeError):
        index.by_id[2] = value
    with pytest.raises(MeshIrError, match='duplicated'):
        PlacementValues((value, value))


def test_ledger_indexes_follow_their_immutable_snapshot():
    read = ExternalBinding(1, ExternalBindingKind.SOURCE, Access.READ_ONLY)
    write = ExternalBinding(2, ExternalBindingKind.DESTINATION, Access.READ_WRITE)
    ledger = PlacementLedger(0, 1, (1, 2), (read, write), (), (), ((1, 1),))
    assert ledger.source_bindings[1] == read
    assert ledger.destination_bindings[2] == write
    assert ledger.last_uses[1] == 1
    advanced = replace(ledger, last_use_by_alias_root=((1, 3),))
    assert advanced.last_uses[1] == 3 and ledger.last_uses[1] == 1
    with pytest.raises(TypeError):
        ledger.last_uses[1] = 3


def test_cached_byte_spans_change_with_the_new_region_not_its_source():
    region = MappedStorageRegion(1, 1, DType.FP32, 0, (2, 3), (5, 1))
    spans = region.byte_spans
    assert region.byte_spans is spans
    shifted = replace(region, element_offset=4)
    assert [(s.begin, s.end) for s in spans] == [(0, 12), (20, 32)]
    assert [(s.begin, s.end) for s in shifted.byte_spans] == [(16, 28), (36, 48)]
    assert region.useful_bytes == shifted.useful_bytes == 24


@pytest.mark.parametrize('transpose', [False, True])
@pytest.mark.parametrize('strategy', ['MATRIX_M', 'MATRIX_N', 'MATRIX_K_SUM'])
def test_indexed_source_coverage_preserves_complete_matrix_geometry(monkeypatch, strategy, transpose):
    from mesh_ir.analysis.regions import intersect_spans, subtract_spans
    from mesh_ir.architecture import load_arch
    from mesh_ir.compile_config import Collectives, Tiling
    from mesh_ir.ir.graph_ir import GraphFunction, GraphOp, MatmulAttrs, OpCode, PytreeSpec
    from mesh_ir.passes import placement_geometry as geometry
    from tests.unit.test_gate2_placement_geometry import ARCH_PATH

    values = (_value(1, 'lhs', TensorRole.INPUT, DType.FP32, (8, 64)),
              _value(2, 'rhs', TensorRole.WEIGHT, DType.FP32,
                     (24, 64) if transpose else (64, 24), content='a' * 64),
              _value(3, 'out', TensorRole.OUTPUT, DType.FP32, (8, 24)))
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(rhs_transpose=transpose), 'indexed:matrix')
    function = GraphFunction(1, 'forward', (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    args = (op, PlacementValues(values), geometry.ShardingStrategy[strategy], (7, 2),
            geometry.create_placement_ledger(function, values), Tiling(4, 8, 16, True),
            Collectives('ring', 32), load_arch(ARCH_PATH))
    actual = geometry.derive_operation_geometry(*args)
    weight_loads = [row for row in actual.value_transfers if row.operand_index == 1]
    assert weight_loads
    assert all(len(row.region.mapped_region.shape) == 2 for row in weight_loads)
    route_calls = []
    minimum_hops = geometry._minimum_target_hops

    def observed_hops(arch, source, kind, destination):
        route_calls.append((source, kind, destination))
        return minimum_hops(arch, source, kind, destination)

    monkeypatch.setattr(geometry, '_minimum_target_hops', observed_hops)
    score = geometry.estimate_operation_geometry(actual, args[4], args[-1])
    assert not isinstance(score, geometry.PlacementRejection)
    assert len(route_calls) == len(set(route_calls))
    template = geometry.GeometryCostTemplate.from_geometry(actual, args[4])
    route_bytes = {}
    for transfer in actual.value_transfers:
        key = (transfer.request_core, transfer.kind, transfer.destination.core_id)
        route_bytes[key] = route_bytes.get(key, 0) + transfer.useful_bytes
    for transfer in actual.collective_transfers:
        key = (transfer.source_core, None, transfer.destination_core)
        route_bytes[key] = route_bytes.get(key, 0) + transfer.chunk.element_count * transfer.chunk.accumulation_dtype.byte_width
    assert len(template.transfers) == len(route_bytes)
    assert {(source, kind, destination): size for size, source, kind, destination in template.transfers} == route_bytes
    assert score.communication_bytes == sum(route_bytes.values())
    assert score.manhattan_hop_bytes_lower_bound == sum(size * minimum_hops(args[-1], *route)
                                                     for route, size in route_bytes.items())

    class SequentialSourceCoverage:
        def __init__(self, groups):
            self.groups = groups

        def intersections(self, required):
            result = []
            for owner, spans in enumerate(self.groups):
                selected = intersect_spans(required, spans)
                required = subtract_spans(required, spans)
                if selected:
                    result.append((owner, selected))
            return tuple(result)

    monkeypatch.setattr(geometry, 'DisjointByteSpanIndex', SequentialSourceCoverage)
    assert not isinstance(actual, geometry.PlacementRejection)
    assert geometry.derive_operation_geometry(*args) == actual
    monkeypatch.setattr(geometry, '_mapped_pieces', lambda value, parent, spans:
                        tuple(geometry._mapped_span(value, span) for span in spans))
    expanded = geometry.derive_operation_geometry(*args)
    assert geometry.estimate_operation_geometry(expanded, args[4], args[-1]) == score
    assert actual.resident_windows == expanded.resident_windows
    assert actual.compute_tiles == expanded.compute_tiles
    assert actual.collectives == expanded.collectives
    coverage = []
    for variant in (actual, expanded):
        by_transfer = {}
        for row in variant.value_transfers:
            key = (row.kind, row.value_id, row.operand_index, row.request_core,
                   row.source, row.destination, row.window_use)
            spans = by_transfer.setdefault(key, set())
            for span in row.region.mapped_region.byte_spans:
                occupied = set(range(span.begin, span.end))
                assert not occupied & spans
                spans.update(occupied)
        coverage.append(by_transfer)
    assert coverage[0] == coverage[1]


def test_transfer_membership_validation_scales_with_window_uses(monkeypatch):
    from mesh_ir.architecture import load_arch
    from mesh_ir.compile_config import Collectives, Tiling
    from mesh_ir.ir.graph_ir import GraphFunction, GraphOp, MatmulAttrs, OpCode, PytreeSpec
    from mesh_ir.passes import placement_geometry as geometry
    from tests.unit.test_gate2_placement_geometry import ARCH_PATH

    values = (_value(1, 'lhs', TensorRole.INPUT, DType.FP32, (16, 64)),
              _value(2, 'rhs', TensorRole.WEIGHT, DType.FP32, (64, 32), content='a' * 64),
              _value(3, 'out', TensorRole.OUTPUT, DType.FP32, (16, 32)))
    op = GraphOp(1, OpCode.MATMUL, (1, 2), (3,), MatmulAttrs(), 'indexed:validation')
    function = GraphFunction(1, 'forward', (1,), (op,), (3,), PytreeSpec.leaf(), PytreeSpec.leaf())
    selected = geometry.derive_operation_geometry(op, PlacementValues(values),
        geometry.ShardingStrategy.MATRIX_N, (7, 2),
        geometry.create_placement_ledger(function, values), Tiling(2, 4, 8, True),
        Collectives('ring', 32), load_arch(ARCH_PATH))
    comparisons = 0
    equal = geometry.WindowUseGeometry.__eq__

    def counted_equal(left, right):
        nonlocal comparisons
        comparisons += 1
        return equal(left, right)

    monkeypatch.setattr(geometry.WindowUseGeometry, '__eq__', counted_equal)
    assert replace(selected) == selected
    uses = sum(len(mapping.window_uses) for operand in selected.operand_distributions
               for mapping in operand.mappings)
    assert comparisons <= 8 * (uses + len(selected.value_transfers))
    transfer = next(row for row in selected.value_transfers if row.operand_index is not None)
    for invalid in (replace(transfer, operand_index=17),
                    replace(transfer, window_use=replace(transfer.window_use, window_id=99999)),
                    replace(transfer, request_core=13, destination=replace(transfer.destination, core_id=13))):
        with pytest.raises(MeshIrError, match='not associated'):
            replace(selected, value_transfers=tuple(invalid if row is transfer else row
                                                    for row in selected.value_transfers))

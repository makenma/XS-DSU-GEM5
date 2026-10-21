from collections import Counter

from mesh_ir.passes.collective_geometry import CollectivePhase
from mesh_ir.passes.placement_geometry import GeometryPhase, PlacementTransferKind, RegionSourceKind, ResidentWindowRole, operand_window_use_regions
from mesh_ir.passes.placement_geometry import OperationPlacementGeometry, PlacementValues
from mesh_ir.passes.region_projection import project_mapped_region_pieces
from mesh_ir.ir.kernel_ir import MatrixPhase
from mesh_ir.ir.graph_ir import GraphOp


def reserve_geometry_commands(op: GraphOp, values: PlacementValues, geometry: OperationPlacementGeometry) -> tuple[tuple[int, int], ...]:
    counts = Counter(tile.owner_core for tile in geometry.compute_tiles)
    epilogues = {(tile.owner_core, tile.output_tile_index) for tile in geometry.compute_tiles
        if tile.matrix_phase is not None and tile.matrix_phase is not MatrixPhase.DIRECT}
    counts.update(core for core, _ in epilogues)
    for transfer in geometry.value_transfers:
        counts[transfer.request_core] += 1
        if transfer.kind is PlacementTransferKind.PEER_RESHARD:
            counts[transfer.destination.core_id] += 1
    for transfer in geometry.collective_transfers:
        counts[transfer.source_core] += 1
        counts[transfer.destination_core] += 2 if transfer.phase is CollectivePhase.REDUCE else 1
    staged_windows = {window.window_id for window in geometry.resident_windows
        if window.role in (ResidentWindowRole.OPERAND, ResidentWindowRole.BIAS)}
    for operand in geometry.operand_distributions:
        value = values.by_id[operand.value_id]
        for mapping in operand.mappings:
            local = tuple(source for source in mapping.sources if source.kind is RegionSourceKind.LOCAL)
            if not local or not any(use.window_id in staged_windows for use in mapping.window_uses):
                continue
            regions = operand_window_use_regions(op, operand.operand_index, value, mapping, geometry)
            for use, region in zip(mapping.window_uses, regions):
                if use.window_id in staged_windows and use.phase is GeometryPhase.COMPUTE:
                    counts[mapping.required_shard.owner_core] += sum(
                        len(project_mapped_region_pieces(value, region, source.mapped_region)) for source in local)
    return tuple(sorted(counts.items()))

from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.canonical import canonical_json_bytes
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import GraphModule, METADATA_VIEW_OPCODES, OpCode
from mesh_ir.ir.kernel_ir import KernelTile, MatrixPhase
from mesh_ir.passes.placement import PlacementCandidate, PlacementDecision
from mesh_ir.passes.placement_geometry import ComputeTileGeometry, OperationPlacementGeometry
from mesh_ir.passes.planning_prefix import OperationIdentity, PlanningResult
from mesh_ir.passes.sharding import PlannedOperationSharding, PlannedValueDistribution, ShardingState, VariantSharding


@dataclass(frozen=True)
class PlannedKernelTile:
    tile_id: int
    identity: OperationIdentity
    geometry: ComputeTileGeometry


@dataclass(frozen=True)
class TilingState:
    shards: ShardingState
    tiles: tuple[PlannedKernelTile, ...]

    def semantic_bytes(self) -> bytes:
        return canonical_json_bytes(self)


_MATRIX = frozenset((OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS))


def tile_kernels(planning: PlanningResult, shards: ShardingState) -> TilingState:
    if type(planning) is not PlanningResult or type(shards) is not ShardingState:
        raise MeshIrError("E_CONFIG", "tiling inputs have invalid record types")
    if type(planning.state.variants) is not tuple or any(type(item) is not GraphModule for item in planning.state.variants):
        raise MeshIrError("E_CONFIG", "tiling Graph variants have invalid record types")
    if len(shards.variants) != len(planning.state.variants):
        raise MeshIrError("E_ABI_ORDER", "tiling shard variants differ from planning variants")
    result = []
    decision_index = 0
    tile_id = 1
    for variant_ordinal, (graph, variant) in enumerate(zip(planning.state.variants, shards.variants)):
        graph.verify()
        if type(variant) is not VariantSharding or (variant.entrypoint, variant.profile_id) != (graph.entrypoint, graph.profile_id) or len(graph.functions) != 1:
            raise MeshIrError("E_ABI_ORDER", "tiling shard variant identity differs from planning")
        if type(variant.distributions) is not tuple or any(type(item) is not PlannedValueDistribution for item in variant.distributions):
            raise MeshIrError("E_CONFIG", "tiling distribution records have invalid types")
        if tuple(item.placement_id for item in variant.distributions) != tuple(range(1, len(variant.distributions) + 1)):
            raise MeshIrError("E_ABI_ORDER", "tiling placement identities are not dense")
        by_placement = {item.placement_id: item.geometry for item in variant.distributions}
        function = graph.functions[0]
        if len(variant.operations) != len(function.ops):
            raise MeshIrError("E_ABI_ORDER", "tiling operation assignments differ from source operations")
        for operation_ordinal, (op, assignment) in enumerate(zip(function.ops, variant.operations)):
            identity = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
            if type(assignment) is not PlannedOperationSharding or type(assignment.identity) is not OperationIdentity or any(type(item) is not int for item in (assignment.identity.function_id, assignment.identity.op_id)) or assignment.identity != identity:
                raise MeshIrError("E_ABI_ORDER", "tiling operation assignment differs from source order", op_id=op.op_id)
            if type(assignment.operand_placement_ids) is not tuple or type(assignment.established_operand_placement_ids) is not tuple:
                raise MeshIrError("E_CONFIG", "tiling operation placement references have invalid types", op_id=op.op_id)
            references = (*assignment.operand_placement_ids, *(item for item in assignment.established_operand_placement_ids if item), assignment.result_placement_id)
            all_references = (*assignment.operand_placement_ids, *assignment.established_operand_placement_ids, assignment.result_placement_id, assignment.partial_sum_id)
            if len(assignment.operand_placement_ids) != len(op.operands) or len(assignment.established_operand_placement_ids) != len(op.operands) or any(type(item) is not int or item < 0 for item in all_references) or any(item not in by_placement for item in references):
                raise MeshIrError("E_ABI_BOUNDS", "tiling operation references an absent placement", op_id=op.op_id)
            if decision_index >= len(shards.decisions.placements):
                raise MeshIrError("E_ABI_ORDER", "tiling placement decisions omit a source operation", op_id=op.op_id)
            decision = shards.decisions.placements[decision_index]
            decision_index += 1
            if type(decision) is not PlacementDecision or decision.identity != identity or type(decision.candidate_index) is not int or not 0 <= decision.candidate_index < len(shards.decisions.candidates):
                raise MeshIrError("E_ABI_ORDER", "tiling placement decision is invalid", op_id=op.op_id)
            candidate = shards.decisions.candidates[decision.candidate_index]
            if type(candidate) is not PlacementCandidate or candidate.identity != identity or candidate.variant_ordinal != variant_ordinal or candidate.strategy is not decision.strategy or candidate.core_ids != decision.core_ids or candidate.rejection is not None or type(candidate.geometry) is not OperationPlacementGeometry:
                raise MeshIrError("E_ABI_ORDER", "tiling selected candidate differs from its decision", op_id=op.op_id)
            geometry = candidate.geometry
            if type(geometry.operation_ordinal) is not int or geometry.operation_ordinal != operation_ordinal or type(geometry.compute_tiles) is not tuple or any(type(item) is not ComputeTileGeometry for item in geometry.compute_tiles):
                raise MeshIrError("E_ABI_ORDER", "tiling selected compute geometry is invalid", op_id=op.op_id)
            if tuple(by_placement[item] for item in assignment.operand_placement_ids) != tuple(item.required_distribution for item in geometry.operand_distributions):
                raise MeshIrError("E_ABI_ORDER", "tiling required operand placements differ from selected geometry", op_id=op.op_id)
            established = tuple(None if item == 0 else by_placement[item] for item in assignment.established_operand_placement_ids)
            if established != tuple(item.established_distribution for item in geometry.operand_distributions) or by_placement[assignment.result_placement_id] != geometry.result_distribution:
                raise MeshIrError("E_ABI_ORDER", "tiling established or result placements differ from selected geometry", op_id=op.op_id)
            if op.opcode in METADATA_VIEW_OPCODES:
                if geometry.compute_tiles:
                    raise MeshIrError("E_EXPORT_LAYOUT", "metadata operation cannot own a compute tile", op_id=op.op_id)
                continue
            if not geometry.compute_tiles:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "physical operation has no selected compute tile", op_id=op.op_id)
            for tile_ordinal, selected in enumerate(geometry.compute_tiles):
                if type(selected.tile_ordinal) is not int or type(selected.owner_core) is not int or type(selected.output_tile_index) is not int or type(selected.tile) is not KernelTile or selected.tile_ordinal != tile_ordinal or selected.owner_core not in decision.core_ids or not 0 <= selected.output_tile_index < len(geometry.output_tiles):
                    raise MeshIrError("E_ABI_ORDER", "selected compute tile identity is invalid", op_id=op.op_id, tile_ordinal=tile_ordinal)
                if (op.opcode in _MATRIX) != (type(selected.matrix_phase) is MatrixPhase):
                    raise MeshIrError("E_CONFIG", "selected compute tile phase differs from source operation", op_id=op.op_id, tile_ordinal=tile_ordinal)
                result.append(PlannedKernelTile(tile_id, identity, selected))
                tile_id += 1
    if decision_index != len(shards.decisions.placements):
        raise MeshIrError("E_ABI_ORDER", "tiling placement decisions contain absent source operations")
    return TilingState(shards, tuple(result))


__all__ = ["PlannedKernelTile", "TilingState", "tile_kernels"]

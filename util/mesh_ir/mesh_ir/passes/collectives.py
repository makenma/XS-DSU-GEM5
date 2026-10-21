from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.canonical import canonical_json_bytes
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import GraphModule
from mesh_ir.passes.bufferize import BufferizationState, VariantBuffers
from mesh_ir.passes.collective_geometry import CollectiveChunkGeometry, CollectiveGeometry, CollectiveTransferGeometry
from mesh_ir.passes.placement import PlacementCandidate, PlacementDecision
from mesh_ir.passes.placement_geometry import OperationPlacementGeometry
from mesh_ir.passes.planning_prefix import OperationIdentity, PlanningResult
from mesh_ir.passes.sharding import PlannedOperationSharding


@dataclass(frozen=True)
class CollectiveChunk:
    chunk_id: int
    partial_sum_id: int
    geometry: CollectiveChunkGeometry


@dataclass(frozen=True)
class CollectiveTransfer:
    transfer_id: int
    partial_sum_id: int
    chunk_id: int
    geometry: CollectiveTransferGeometry


@dataclass(frozen=True)
class VariantCollectives:
    entrypoint: str
    profile_id: str
    chunks: tuple[CollectiveChunk, ...]
    transfers: tuple[CollectiveTransfer, ...]


@dataclass(frozen=True)
class CollectiveState:
    buffers: BufferizationState
    variants: tuple[VariantCollectives, ...]

    def semantic_bytes(self) -> bytes:
        return canonical_json_bytes(self)


def lower_collectives(planning: PlanningResult, buffers: BufferizationState) -> CollectiveState:
    if type(planning) is not PlanningResult or type(buffers) is not BufferizationState or len(planning.state.variants) != len(buffers.variants):
        raise MeshIrError("E_CONFIG", "collective lowering inputs are invalid")
    variants = []
    decision_index = 0
    for variant_ordinal, (graph, sharding, buffered) in enumerate(zip(planning.state.variants, buffers.tiling.shards.variants, buffers.variants)):
        if type(graph) is not GraphModule or type(buffered) is not VariantBuffers or (buffered.entrypoint, buffered.profile_id) != (graph.entrypoint, graph.profile_id) or len(graph.functions) != 1:
            raise MeshIrError("E_ABI_ORDER", "collective lowering variant identity differs from planning")
        function = graph.functions[0]
        if len(sharding.operations) != len(function.ops):
            raise MeshIrError("E_ABI_ORDER", "collective lowering operation assignments differ from source")
        chunks = []
        transfers = []
        for operation_ordinal, (op, assignment) in enumerate(zip(function.ops, sharding.operations)):
            identity = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
            if type(assignment) is not PlannedOperationSharding or assignment.identity != identity or decision_index >= len(buffers.tiling.shards.decisions.placements):
                raise MeshIrError("E_ABI_ORDER", "collective lowering operation order is invalid", op_id=op.op_id)
            decision = buffers.tiling.shards.decisions.placements[decision_index]
            decision_index += 1
            if type(decision) is not PlacementDecision or decision.identity != identity or type(decision.candidate_index) is not int or not 0 <= decision.candidate_index < len(buffers.tiling.shards.decisions.candidates):
                raise MeshIrError("E_ABI_ORDER", "collective lowering placement decision is invalid", op_id=op.op_id)
            candidate = buffers.tiling.shards.decisions.candidates[decision.candidate_index]
            if type(candidate) is not PlacementCandidate or candidate.identity != identity or candidate.variant_ordinal != variant_ordinal or candidate.rejection is not None or type(candidate.geometry) is not OperationPlacementGeometry:
                raise MeshIrError("E_ABI_ORDER", "collective lowering selected geometry is invalid", op_id=op.op_id)
            geometry = candidate.geometry
            if geometry.operation_ordinal != operation_ordinal:
                raise MeshIrError("E_ABI_ORDER", "collective lowering selected operation ordinal is invalid", op_id=op.op_id)
            if bool(assignment.partial_sum_id) != bool(geometry.collectives):
                raise MeshIrError("E_ABI_ORDER", "partial-SUM assignment and selected collectives differ", op_id=op.op_id)
            for collective in geometry.collectives:
                if type(collective) is not CollectiveGeometry:
                    raise MeshIrError("E_CONFIG", "selected collective geometry has an invalid type", op_id=op.op_id)
                local_chunks = {}
                for selected in collective.chunks:
                    if type(selected) is not CollectiveChunkGeometry or selected.computation_id != op.op_id or selected.semantic_result_value_id != op.results[0]:
                        raise MeshIrError("E_ABI_CHECKSUM", "selected collective chunk differs from its source operation", op_id=op.op_id)
                    chunk = CollectiveChunk(len(chunks) + 1, assignment.partial_sum_id, selected)
                    chunks.append(chunk)
                    local_chunks[id(selected)] = chunk.chunk_id
                for selected in collective.transfers:
                    if type(selected) is not CollectiveTransferGeometry or id(selected.chunk) not in local_chunks:
                        raise MeshIrError("E_ABI_ORDER", "selected collective transfer references an absent chunk", op_id=op.op_id)
                    transfers.append(CollectiveTransfer(len(transfers) + 1, assignment.partial_sum_id, local_chunks[id(selected.chunk)], selected))
        variants.append(VariantCollectives(graph.entrypoint, graph.profile_id, tuple(chunks), tuple(transfers)))
    if decision_index != len(buffers.tiling.shards.decisions.placements):
        raise MeshIrError("E_ABI_ORDER", "collective lowering placement decisions contain absent operations")
    return CollectiveState(buffers, tuple(variants))


__all__ = ["CollectiveChunk", "CollectiveState", "CollectiveTransfer", "VariantCollectives", "lower_collectives"]

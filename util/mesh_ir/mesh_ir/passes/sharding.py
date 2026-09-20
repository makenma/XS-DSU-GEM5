from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.canonical import canonical_json_bytes, checked_mul_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Const, INVALID_CORE_ID
from mesh_ir.ir.graph_ir import GraphModule, OpCode
from mesh_ir.ir.kernel_ir import DistributionKind
from mesh_ir.passes.placement import LoweringDecisions, PlacementCandidate, PlacementDecision, ShardingStrategy
from mesh_ir.passes.placement_geometry import OperandDistribution, OperationPlacementGeometry, RankShardGeometry, ValueDistribution
from mesh_ir.passes.planning_prefix import OperationIdentity, PlanningResult


_MATRIX = frozenset((OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS))


@dataclass(frozen=True)
class PlannedValueDistribution:
    placement_id: int
    shard_ids: tuple[int, ...]
    geometry: ValueDistribution


@dataclass(frozen=True)
class PlannedOperationSharding:
    identity: OperationIdentity
    operand_placement_ids: tuple[int, ...]
    established_operand_placement_ids: tuple[int, ...]
    result_placement_id: int
    partial_sum_id: int


@dataclass(frozen=True)
class VariantSharding:
    entrypoint: str
    profile_id: str
    distributions: tuple[PlannedValueDistribution, ...]
    operations: tuple[PlannedOperationSharding, ...]


@dataclass(frozen=True)
class ShardingState:
    decisions: LoweringDecisions
    variants: tuple[VariantSharding, ...]

    def semantic_bytes(self) -> bytes:
        return canonical_json_bytes(self)


def _concrete_shape(graph: GraphModule, value_id: int) -> tuple[int, ...]:
    if type(value_id) is not int or value_id < 1:
        raise MeshIrError("E_ABI_BOUNDS", "selected distribution value identity is invalid", value_id=value_id)
    values = {item.value_id: item for item in graph.values}
    if value_id not in values:
        raise MeshIrError("E_ABI_BOUNDS", "selected distribution references an absent Graph value", value_id=value_id)
    shape = values[value_id].shape
    if any(type(item) is not Const for item in shape):
        raise MeshIrError("E_SHAPE_UNBOUND", "sharding requires concrete Graph dimensions", value_id=value_id)
    return tuple(item.value for item in shape)


def _verify_distribution(graph: GraphModule, distribution: ValueDistribution) -> None:
    if type(distribution) is not ValueDistribution:
        raise MeshIrError("E_CONFIG", "selected value distribution type is invalid")
    shape = _concrete_shape(graph, distribution.value_id)
    if type(distribution.distribution) is not DistributionKind or distribution.distribution not in (DistributionKind.PARTITIONED, DistributionKind.REPLICATED):
        raise MeshIrError("E_CONFIG", "selected semantic value distribution is invalid", value_id=distribution.value_id)
    if type(distribution.core_ids) is not tuple or not distribution.core_ids:
        raise MeshIrError("E_CONFIG", "selected distribution core order is invalid", value_id=distribution.value_id)
    if any(type(core) is not int or not 0 <= core < INVALID_CORE_ID for core in distribution.core_ids) or len(set(distribution.core_ids)) != len(distribution.core_ids):
        raise MeshIrError("E_CONFIG", "selected distribution core order is invalid", value_id=distribution.value_id)
    if type(distribution.shards) is not tuple or len(distribution.shards) != len(distribution.core_ids) or any(type(item) is not RankShardGeometry for item in distribution.shards):
        raise MeshIrError("E_CONFIG", "selected distribution shard records are invalid", value_id=distribution.value_id)
    if any(type(item.logical_rank) is not int or type(item.owner_core) is not int for item in distribution.shards):
        raise MeshIrError("E_CONFIG", "selected distribution shard identities are invalid", value_id=distribution.value_id)
    if tuple(item.logical_rank for item in distribution.shards) != tuple(range(len(distribution.shards))) or tuple(item.owner_core for item in distribution.shards) != distribution.core_ids:
        raise MeshIrError("E_ABI_ORDER", "selected distribution shard order differs from core rank order", value_id=distribution.value_id)
    rank = len(shape)
    for shard in distribution.shards:
        fields = (shard.global_origin, shard.padded_shape, shard.valid_shape)
        if any(type(field) is not tuple or len(field) != rank or any(type(item) is not int or item < 0 for item in field) for field in fields):
            raise MeshIrError("E_CONFIG", "selected distribution shard geometry is invalid", value_id=distribution.value_id, logical_rank=shard.logical_rank)
        if any(valid > padded or origin + valid > extent for origin, padded, valid, extent in zip(shard.global_origin, shard.padded_shape, shard.valid_shape, shape)):
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "selected shard exceeds its logical value", value_id=distribution.value_id, logical_rank=shard.logical_rank)
    if distribution.distribution is DistributionKind.REPLICATED:
        if distribution.partition_axis is not None or any(shard.global_origin != (0,) * rank or shard.padded_shape != shape or shard.valid_shape != shape for shard in distribution.shards):
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "replicated distribution does not reproduce the complete logical value", value_id=distribution.value_id)
        return
    if distribution.partition_axis is None:
        if len(distribution.core_ids) != 1 or distribution.shards[0].global_origin != (0,) * rank or distribution.shards[0].padded_shape != shape or distribution.shards[0].valid_shape != shape:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "unpartitioned distribution does not reproduce the complete logical value", value_id=distribution.value_id)
        return
    axis = distribution.partition_axis
    if type(axis) is not int or not 0 <= axis < rank:
        raise MeshIrError("E_EXPORT_LAYOUT", "selected partition axis is invalid", value_id=distribution.value_id)
    extent = shape[axis]
    padded_extent = 0 if extent == 0 else (extent + len(distribution.core_ids) - 1) // len(distribution.core_ids)
    for logical_rank, shard in enumerate(distribution.shards):
        origin_extent = min(checked_mul_u64(logical_rank, padded_extent, "partition origin"), extent)
        valid_extent = min(padded_extent, extent - origin_extent)
        expected_origin = tuple(origin_extent if index == axis else 0 for index in range(rank))
        expected_padded = tuple(padded_extent if index == axis else value for index, value in enumerate(shape))
        expected_valid = tuple(valid_extent if index == axis else value for index, value in enumerate(shape))
        if shard.global_origin != expected_origin or shard.padded_shape != expected_padded or shard.valid_shape != expected_valid:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "partitioned distribution is not the exact uniform logical cover", value_id=distribution.value_id, logical_rank=logical_rank)


def _selected_candidate(decisions: LoweringDecisions, decision: PlacementDecision, variant_ordinal: int) -> PlacementCandidate:
    if type(decision) is not PlacementDecision or type(decision.candidate_index) is not int or not 0 <= decision.candidate_index < len(decisions.candidates):
        raise MeshIrError("E_ABI_BOUNDS", "placement decision candidate identity is invalid")
    candidate = decisions.candidates[decision.candidate_index]
    if type(candidate) is not PlacementCandidate:
        raise MeshIrError("E_CONFIG", "selected placement candidate type is invalid")
    if type(candidate.identity) is not OperationIdentity or type(candidate.identity.entrypoint) is not str or type(candidate.identity.profile_id) is not str or any(type(item) is not int for item in (candidate.identity.function_id, candidate.identity.op_id)) or type(candidate.variant_ordinal) is not int or type(candidate.strategy) is not ShardingStrategy or type(candidate.core_ids) is not tuple or any(type(item) is not int for item in candidate.core_ids):
        raise MeshIrError("E_CONFIG", "selected placement candidate fields are invalid")
    if candidate.identity != decision.identity or candidate.strategy is not decision.strategy or candidate.core_ids != decision.core_ids or candidate.variant_ordinal != variant_ordinal:
        raise MeshIrError("E_ABI_ORDER", "placement decision differs from its selected candidate", op_id=decision.identity.op_id)
    if candidate.rejection is not None or type(candidate.geometry) is not OperationPlacementGeometry:
        raise MeshIrError("E_PLACEMENT_INFEASIBLE", "selected placement candidate has no legal geometry", op_id=decision.identity.op_id)
    return candidate


def shard_and_pad(planning: PlanningResult, decisions: LoweringDecisions) -> ShardingState:
    if type(planning) is not PlanningResult or type(decisions) is not LoweringDecisions:
        raise MeshIrError("E_CONFIG", "sharding inputs have invalid record types")
    if type(planning.state.variants) is not tuple or any(type(item) is not GraphModule for item in planning.state.variants):
        raise MeshIrError("E_CONFIG", "sharding Graph variants have invalid record types")
    if type(decisions.candidates) is not tuple or type(decisions.placements) is not tuple or any(type(item) is not PlacementCandidate for item in decisions.candidates):
        raise MeshIrError("E_CONFIG", "placement decision collections have invalid record types")
    variants = []
    decision_index = 0
    for variant_ordinal, graph in enumerate(planning.state.variants):
        graph.verify()
        if len(graph.functions) != 1:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "Kernel module requires one concrete entrypoint function")
        function = graph.functions[0]
        distributions: list[PlannedValueDistribution] = []
        operations = []
        next_shard_id = 1
        next_partial_sum_id = 1

        def register(geometry: ValueDistribution) -> int:
            nonlocal next_shard_id
            _verify_distribution(graph, geometry)
            for existing in distributions:
                if existing.geometry == geometry:
                    return existing.placement_id
            placement_id = len(distributions) + 1
            shard_ids = tuple(range(next_shard_id, next_shard_id + len(geometry.shards)))
            next_shard_id += len(shard_ids)
            distributions.append(PlannedValueDistribution(placement_id, shard_ids, geometry))
            return placement_id

        for operation_ordinal, op in enumerate(function.ops):
            if decision_index >= len(decisions.placements):
                raise MeshIrError("E_ABI_ORDER", "placement decisions omit a source operation", op_id=op.op_id)
            decision = decisions.placements[decision_index]
            decision_index += 1
            identity = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
            if type(decision) is not PlacementDecision or type(decision.identity) is not OperationIdentity or type(decision.identity.entrypoint) is not str or type(decision.identity.profile_id) is not str or any(type(item) is not int for item in (decision.identity.function_id, decision.identity.op_id)) or decision.identity != identity or type(decision.strategy) is not ShardingStrategy or type(decision.core_ids) is not tuple or any(type(item) is not int for item in decision.core_ids):
                raise MeshIrError("E_ABI_ORDER", "placement decisions differ from source operation order", op_id=op.op_id)
            candidate = _selected_candidate(decisions, decision, variant_ordinal)
            geometry = candidate.geometry
            allowed_strategies = (ShardingStrategy.SINGLE,) if len(decision.core_ids) == 1 else (ShardingStrategy.MATRIX_M, ShardingStrategy.MATRIX_N, ShardingStrategy.MATRIX_K_SUM) if op.opcode in _MATRIX else (ShardingStrategy.PRESERVE, ShardingStrategy.OUTER_DIM, ShardingStrategy.REPLICATE)
            if decision.strategy not in allowed_strategies:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "selected sharding strategy does not apply to the source operation", op_id=op.op_id)
            if type(geometry.operation_ordinal) is not int or geometry.operation_ordinal != operation_ordinal or type(geometry.operand_distributions) is not tuple:
                raise MeshIrError("E_ABI_ORDER", "selected geometry operation ordinal is invalid", op_id=op.op_id)
            if any(type(item) is not OperandDistribution or type(item.operand_index) is not int for item in geometry.operand_distributions) or tuple(item.operand_index for item in geometry.operand_distributions) != tuple(range(len(op.operands))):
                raise MeshIrError("E_ABI_ORDER", "selected operand distributions differ from source operand occurrences", op_id=op.op_id)
            operand_placement_ids = []
            established_placement_ids = []
            for operand_index, operand in enumerate(geometry.operand_distributions):
                if type(operand.value_id) is not int or operand.value_id != op.operands[operand_index]:
                    raise MeshIrError("E_ABI_ORDER", "selected operand distribution differs from source operand identity", op_id=op.op_id, operand_index=operand_index)
                if type(operand.required_distribution) is not ValueDistribution or operand.required_distribution.core_ids != decision.core_ids:
                    raise MeshIrError("E_ABI_ORDER", "required operand distribution differs from selected core order", op_id=op.op_id, operand_index=operand_index)
                established_placement_ids.append(0 if operand.established_distribution is None else register(operand.established_distribution))
                operand_placement_ids.append(register(operand.required_distribution))
            if type(geometry.result_distribution) is not ValueDistribution or len(op.results) != 1 or geometry.result_distribution.value_id != op.results[0] or geometry.result_distribution.core_ids != decision.core_ids:
                raise MeshIrError("E_ABI_ORDER", "selected result distribution differs from source result identity", op_id=op.op_id)
            result_placement_id = register(geometry.result_distribution)
            partial_sum_id = 0
            if decision.strategy is ShardingStrategy.MATRIX_K_SUM:
                partial_sum_id = next_partial_sum_id
                next_partial_sum_id += 1
            operations.append(
                PlannedOperationSharding(
                    identity,
                    tuple(operand_placement_ids),
                    tuple(established_placement_ids),
                    result_placement_id,
                    partial_sum_id,
                )
            )
        variants.append(VariantSharding(graph.entrypoint, graph.profile_id, tuple(distributions), tuple(operations)))
    if decision_index != len(decisions.placements):
        raise MeshIrError("E_ABI_ORDER", "placement decisions contain operations outside the planned variants")
    return ShardingState(decisions, tuple(variants))


__all__ = [
    "PlannedOperationSharding",
    "PlannedValueDistribution",
    "ShardingState",
    "VariantSharding",
    "shard_and_pad",
]

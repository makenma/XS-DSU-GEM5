from __future__ import annotations

import itertools
from dataclasses import dataclass

from mesh_ir.analysis.cost import OperationCost
from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.compile_config import EffectiveCompileConfig, validate_effective_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import GraphModule, OpCode
from mesh_ir.passes.placement_geometry import (
    OperationPlacementGeometry,
    PlacementRejection,
    ShardingStrategy,
    advance_placement_ledger,
    create_placement_ledger,
    derive_operation_geometry,
    estimate_operation_geometry,
)
from mesh_ir.passes.planning_prefix import FunctionIdentity, LogicalParticipant, OperationCostRecord, OperationIdentity, ParallelGroup, PlanningResult, PlanningState


@dataclass(frozen=True)
class PlacementScore:
    communication_bytes: int
    peak_sram_bytes: int
    manhattan_hop_bytes_lower_bound: int


@dataclass(frozen=True)
class PlacementCandidate:
    identity: OperationIdentity
    variant_ordinal: int
    strategy: ShardingStrategy
    core_ids: tuple[int, ...]
    score: PlacementScore
    rejection: PlacementRejection | None
    geometry: OperationPlacementGeometry | None


@dataclass(frozen=True)
class PlacementDecision:
    identity: OperationIdentity
    strategy: ShardingStrategy
    core_ids: tuple[int, ...]
    candidate_index: int


@dataclass(frozen=True)
class LoweringDecisions:
    candidates: tuple[PlacementCandidate, ...]
    placements: tuple[PlacementDecision, ...]


_MATRIX = frozenset((OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS))


def _strategies(opcode: OpCode, degree: int) -> tuple[ShardingStrategy, ...]:
    if degree == 1:
        return (ShardingStrategy.SINGLE,)
    if opcode in _MATRIX:
        return (ShardingStrategy.MATRIX_M, ShardingStrategy.MATRIX_N, ShardingStrategy.MATRIX_K_SUM)
    return (ShardingStrategy.PRESERVE, ShardingStrategy.OUTER_DIM, ShardingStrategy.REPLICATE)


def _validate_inputs(planning: PlanningResult, arch: ArchManifest, effective: EffectiveCompileConfig) -> None:
    if type(planning) is not PlanningResult or type(arch) is not ArchManifest or type(effective) is not EffectiveCompileConfig:
        raise MeshIrError("E_CONFIG", "placement inputs have invalid record types")
    validate_arch(arch)
    validate_effective_compile_config(effective, arch)
    if type(planning.state) is not PlanningState:
        raise MeshIrError("E_CONFIG", "placement planning state type is invalid")
    if type(planning.state.variants) is not tuple or not planning.state.variants:
        raise MeshIrError("E_CONFIG", "placement requires concrete Graph variants")
    if any(type(item) is not GraphModule for item in planning.state.variants):
        raise MeshIrError("E_CONFIG", "placement Graph variants have invalid record types")
    functions = []
    operations = []
    for graph in planning.state.variants:
        graph.verify()
        for function in graph.functions:
            function_identity = FunctionIdentity(graph.entrypoint, graph.profile_id, function.function_id)
            functions.append(function_identity)
            operations.extend(OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id) for op in function.ops)
    if type(planning.state.parallel_groups) is not tuple or any(type(item) is not ParallelGroup for item in planning.state.parallel_groups):
        raise MeshIrError("E_CONFIG", "placement parallel groups have invalid record types")
    if type(planning.state.operation_costs) is not tuple or any(type(item) is not OperationCostRecord for item in planning.state.operation_costs):
        raise MeshIrError("E_CONFIG", "placement operation costs have invalid record types")
    if any(type(item.identity) is not OperationIdentity or type(item.cost) is not OperationCost for item in planning.state.operation_costs):
        raise MeshIrError("E_CONFIG", "placement operation cost contents have invalid record types")
    group_functions = tuple(item.function for item in planning.state.parallel_groups)
    if group_functions != tuple(functions) or tuple(item.identity for item in planning.state.operation_costs) != tuple(operations):
        raise MeshIrError("E_ABI_ORDER", "planning identities do not match Graph order")
    configured_degree = effective.config.parallelism.tensor_parallel
    arch_cores = set(arch.core_ids)
    allowed = set(effective.config.placement.allowed_cores)
    reserved = set(effective.config.placement.reserve_cores)
    expected_admissible = tuple(core for core in arch.core_ids if core in allowed and core not in reserved)
    for expected_id, group in enumerate(planning.state.parallel_groups, 1):
        if type(group.function) is not FunctionIdentity or type(group.participants) is not tuple or any(type(item) is not LogicalParticipant or type(item.logical_rank) is not int for item in group.participants):
            raise MeshIrError("E_CONFIG", "parallel group record types are invalid", group_id=group.group_id)
        if type(group.group_id) is not int or group.group_id != expected_id or tuple(item.logical_rank for item in group.participants) != tuple(range(configured_degree)):
            raise MeshIrError("E_CONFIG", "parallel group identities or logical ranks are invalid", group_id=group.group_id)
        if type(group.admissible_core_ids) is not tuple or len(set(group.admissible_core_ids)) != len(group.admissible_core_ids) or any(type(core) is not int or core not in arch_cores for core in group.admissible_core_ids) or group.admissible_core_ids != expected_admissible:
            raise MeshIrError("E_CONFIG", "parallel group admissible cores are invalid", group_id=group.group_id)
        if type(group.all_reduce_algorithm) is not str or type(group.chunk_bytes) is not int or group.all_reduce_algorithm != effective.config.collectives.all_reduce_algorithm or group.chunk_bytes != effective.config.collectives.chunk_bytes:
            raise MeshIrError("E_CONFIG", "parallel group collective policy differs from effective configuration", group_id=group.group_id)
        if len(group.admissible_core_ids) < configured_degree:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "parallel group has fewer admissible cores than participants", group_id=group.group_id)


def place_operations(planning: PlanningResult, arch: ArchManifest, effective: EffectiveCompileConfig) -> LoweringDecisions:
    _validate_inputs(planning, arch, effective)
    groups = {item.function: item for item in planning.state.parallel_groups}
    candidates = []
    decisions = []
    for variant_ordinal, graph in enumerate(planning.state.variants):
        for function in graph.functions:
            identity = FunctionIdentity(graph.entrypoint, graph.profile_id, function.function_id)
            group = groups[identity]
            degree = len(group.participants)
            core_sets = tuple(itertools.combinations(group.admissible_core_ids, degree))
            if not core_sets:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "parallel group has no physical placement", group_id=group.group_id)
            ledger = create_placement_ledger(function, graph.values)
            for op in function.ops:
                operation = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
                first = len(candidates)
                for core_ids in core_sets:
                    for strategy in _strategies(op.opcode, degree):
                        try:
                            geometry = derive_operation_geometry(
                                op,
                                graph.values,
                                strategy,
                                core_ids,
                                ledger,
                                effective.config.tiling,
                                effective.config.collectives,
                                arch,
                            )
                            if type(geometry) is PlacementRejection:
                                score = PlacementScore(0, 0, 0)
                                rejection = geometry
                                geometry = None
                            else:
                                estimate = estimate_operation_geometry(geometry, ledger, arch)
                                if type(estimate) is PlacementRejection:
                                    score = PlacementScore(0, 0, 0)
                                    rejection = estimate
                                    geometry = None
                                else:
                                    score = PlacementScore(estimate.communication_bytes, estimate.peak_sram_bytes, estimate.manhattan_hop_bytes_lower_bound)
                                    rejection = None
                        except MeshIrError as error:
                            if error.code != "E_ABI_OVERFLOW":
                                raise
                            score = PlacementScore(0, 0, 0)
                            rejection = PlacementRejection(error.code, error.message)
                            geometry = None
                        candidates.append(PlacementCandidate(operation, variant_ordinal, strategy, core_ids, score, rejection, geometry))
                eligible = tuple(index for index in range(first, len(candidates)) if candidates[index].rejection is None)
                if not eligible:
                    raise MeshIrError("E_PLACEMENT_INFEASIBLE", "operation has no legal placement candidate", op_id=op.op_id)
                selected_index = min(
                    eligible,
                    key=lambda index: (
                        candidates[index].score.communication_bytes,
                        candidates[index].score.peak_sram_bytes,
                        candidates[index].score.manhattan_hop_bytes_lower_bound,
                        candidates[index].core_ids,
                        (variant_ordinal, function.function_id, op.op_id),
                        int(candidates[index].strategy),
                    ),
                )
                selected = candidates[selected_index]
                if selected.geometry is None:
                    raise MeshIrError("E_PLACEMENT_INFEASIBLE", "selected placement candidate has no geometry", op_id=op.op_id)
                decisions.append(PlacementDecision(operation, selected.strategy, selected.core_ids, selected_index))
                ledger = advance_placement_ledger(ledger, selected.geometry)
    return LoweringDecisions(tuple(candidates), tuple(decisions))


__all__ = [
    "LoweringDecisions",
    "PlacementCandidate",
    "PlacementDecision",
    "PlacementRejection",
    "PlacementScore",
    "ShardingStrategy",
    "place_operations",
]

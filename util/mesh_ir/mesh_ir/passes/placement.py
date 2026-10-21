from __future__ import annotations

import itertools
import logging
from collections import Counter
from dataclasses import dataclass, replace

from mesh_ir.analysis.cost import OperationCost
from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.compile_config import EffectiveCompileConfig, validate_effective_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import GraphModule, OpCode
from mesh_ir.passes.execution import PassExecutor
from mesh_ir.passes.placement_geometry import (
    OperationPlacementGeometry,
    PlacementRejection,
    PlacementValues,
    ShardingStrategy,
    advance_placement_ledger,
    create_placement_ledger,
    derive_operation_geometry,
)
from mesh_ir.passes.placement_evaluation import PlacementGeometryEvaluator, ViewDistributionConstraints
from mesh_ir.passes.planning_prefix import FunctionIdentity, LogicalParticipant, OperationCostRecord, OperationIdentity, ParallelGroup, PlanningResult, PlanningState


@dataclass(frozen=True)
class PlacementScore:
    communication_bytes: int
    peak_sram_bytes: int
    manhattan_hop_bytes_lower_bound: int
    peak_command_entries: int = 0

    @property
    def ordering_key(self) -> tuple[int, int, int, int]:
        return (self.peak_command_entries, self.communication_bytes,
            self.peak_sram_bytes, self.manhattan_hop_bytes_lower_bound)


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
    candidates_evaluated: int = 0


_MATRIX = frozenset((OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS))
_LOG = logging.getLogger(__name__)


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


def place_operations(planning: PlanningResult, arch: ArchManifest, effective: EffectiveCompileConfig,
                     *, executor: PassExecutor | None = None) -> LoweringDecisions:
    _validate_inputs(planning, arch, effective)
    groups = {item.function: item for item in planning.state.parallel_groups}
    candidates = []
    decisions = []
    evaluated = 0
    for variant_ordinal, graph in enumerate(planning.state.variants):
        values = PlacementValues(graph.values)
        for function in graph.functions:
            identity = FunctionIdentity(graph.entrypoint, graph.profile_id, function.function_id)
            group = groups[identity]
            degree = len(group.participants)
            core_sets = tuple(itertools.combinations(group.admissible_core_ids, degree))
            if not core_sets:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "parallel group has no physical placement", group_id=group.group_id)
            ledger = create_placement_ledger(function, graph.values)
            view_constraints = ViewDistributionConstraints(function.ops, values)
            for ordinal, op in enumerate(function.ops, 1):
                _LOG.info('placement start entrypoint=%s profile=%s function=%s op=%s/%s op_id=%s opcode=%s',
                    graph.entrypoint, graph.profile_id, function.function_id, ordinal, len(function.ops), op.op_id, op.opcode.value)
                operation = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
                first = len(candidates)
                evaluated_before = evaluated
                reasons = Counter()
                finalists = []
                selected_index = None
                selected_key = None
                evaluator = PlacementGeometryEvaluator(op, values, ledger,
                    effective.config.tiling, effective.config.collectives, arch, view_constraints)
                if executor is not None and executor.workers > 1:
                    evaluator.prepare(_strategies(op.opcode, degree), core_sets, executor)
                for core_ordinal, core_ids in enumerate(core_sets, 1):
                    core_candidates = []
                    strategy_index = None
                    strategy_key = None
                    for strategy in _strategies(op.opcode, degree):
                        evaluated += 1
                        try:
                            estimate = evaluator.estimate(strategy, core_ids)
                            if type(estimate) is PlacementRejection:
                                score = PlacementScore(0, 0, 0)
                                rejection = estimate
                            else:
                                score = PlacementScore(estimate.communication_bytes, estimate.peak_sram_bytes, estimate.manhattan_hop_bytes_lower_bound, estimate.peak_command_entries)
                                rejection = None
                        except MeshIrError as error:
                            if error.code != "E_ABI_OVERFLOW":
                                raise
                            score = PlacementScore(0, 0, 0)
                            rejection = PlacementRejection(error.code, error.message)
                        key = (score.communication_bytes, score.peak_sram_bytes,
                            score.manhattan_hop_bytes_lower_bound, int(strategy))
                        if rejection is None and (strategy_key is None or key < strategy_key):
                            strategy_index = len(core_candidates)
                            strategy_key = key
                        if rejection is not None:
                            reasons[rejection.message] += 1
                        core_candidates.append(PlacementCandidate(operation, variant_ordinal, strategy, core_ids, score, rejection, None))
                    if strategy_index is not None:
                        candidate = core_candidates[strategy_index]
                        key = (*candidate.score.ordering_key, core_ids, int(candidate.strategy))
                        if selected_key is None or key < selected_key:
                            selected_index = first + strategy_index
                            selected_key = key
                            finalists = core_candidates
                    if core_ordinal % 256 == 0:
                        _LOG.info('placement progress entrypoint=%s profile=%s function=%s op=%s/%s op_id=%s core_groups=%s/%s candidates=%s',
                            graph.entrypoint, graph.profile_id, function.function_id, ordinal, len(function.ops), op.op_id,
                            core_ordinal, len(core_sets), evaluated - evaluated_before)
                if selected_index is None:
                    raise MeshIrError("E_PLACEMENT_INFEASIBLE", "operation has no legal placement candidate", op_id=op.op_id, opcode=op.opcode.value, rejection_reasons=dict(reasons))
                candidates.extend(finalists)
                selected = candidates[selected_index]
                geometry = derive_operation_geometry(op, values, selected.strategy, selected.core_ids,
                    ledger, effective.config.tiling, effective.config.collectives, arch)
                if type(geometry) is PlacementRejection:
                    raise MeshIrError("E_PLACEMENT_INFEASIBLE", "selected placement candidate has no geometry", op_id=op.op_id)
                selected = replace(selected, geometry=geometry)
                candidates[selected_index] = selected
                decisions.append(PlacementDecision(operation, selected.strategy, selected.core_ids, selected_index))
                ledger = advance_placement_ledger(ledger, selected.geometry)
                _LOG.info('placement complete entrypoint=%s profile=%s function=%s op=%s/%s op_id=%s candidates=%s cores=%s strategy=%s rom_reserved_peak=%s rom_capacity=%s',
                    graph.entrypoint, graph.profile_id, function.function_id, ordinal, len(function.ops), op.op_id,
                    evaluated - evaluated_before, selected.core_ids, selected.strategy.name,
                    selected.score.peak_command_entries, arch.command_rom_entries)
    return LoweringDecisions(tuple(candidates), tuple(decisions), evaluated)


__all__ = [
    "LoweringDecisions",
    "PlacementCandidate",
    "PlacementDecision",
    "PlacementRejection",
    "PlacementScore",
    "ShardingStrategy",
    "place_operations",
]

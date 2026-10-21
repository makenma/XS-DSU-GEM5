from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass

from mesh_ir.analysis.cost import OperationCost, estimate_operation_cost
from mesh_ir.architecture import ArchManifest
from mesh_ir.canonical import canonical_json_bytes, checked_add_u64
from mesh_ir.compile_config import CompileConfig, EffectiveCompileConfig, validate_effective_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import GraphModule, GraphOp, GraphValue
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor, PassRecord, PassTask, record_pass, verify_pass_chain
from mesh_ir.passes.shape_specialize import root_symbol_bindings_from_graph, specialize_profiles


@dataclass(frozen=True)
class OperationIdentity:
    entrypoint: str
    profile_id: str
    function_id: int
    op_id: int


@dataclass(frozen=True)
class FunctionIdentity:
    entrypoint: str
    profile_id: str
    function_id: int


@dataclass(frozen=True)
class OperationCostRecord:
    identity: OperationIdentity
    cost: OperationCost


@dataclass(frozen=True)
class LogicalParticipant:
    logical_rank: int


@dataclass(frozen=True)
class ParallelGroup:
    group_id: int
    function: FunctionIdentity
    participants: tuple[LogicalParticipant, ...]
    admissible_core_ids: tuple[int, ...]
    all_reduce_algorithm: str
    chunk_bytes: int


@dataclass(frozen=True)
class PlanningState:
    variants: tuple[GraphModule, ...]
    operation_costs: tuple[OperationCostRecord, ...]
    parallel_groups: tuple[ParallelGroup, ...]

    def semantic_dict(self) -> dict[str, object]:
        return {
            "variant_semantic_sha256": [item.semantic_sha256 for item in self.variants],
            "operation_costs": tuple(asdict(item) for item in self.operation_costs),
            "parallel_groups": tuple(asdict(item) for item in self.parallel_groups),
        }

    def semantic_bytes(self) -> bytes:
        return canonical_json_bytes(self.semantic_dict())

    def semantic_sha256(self) -> str:
        return hashlib.sha256(self.semantic_bytes()).hexdigest()


@dataclass(frozen=True)
class PlanningResult:
    source_graphs: tuple[GraphModule, ...]
    state: PlanningState
    passes: tuple[PassRecord, ...]
    execution: ExecutionDiagnostics


@dataclass(frozen=True)
class _CostTask:
    identity: OperationIdentity
    op: GraphOp
    operands: tuple[GraphValue, ...]
    result: GraphValue


@dataclass(frozen=True)
class _GroupTask:
    group_id: int
    function: FunctionIdentity
    tensor_parallel: int
    admissible_core_ids: tuple[int, ...]
    all_reduce_algorithm: str
    chunk_bytes: int


def graph_set_sha256(variants: tuple[GraphModule, ...]) -> str:
    if type(variants) is not tuple or any(type(item) is not GraphModule for item in variants):
        raise MeshIrError("E_CONFIG", "Graph set must be an immutable tuple of Graph modules")
    for graph in variants:
        graph.verify()
    return hashlib.sha256(canonical_json_bytes([item.semantic_sha256 for item in variants])).hexdigest()


def _expected_variants(source: GraphModule, config: CompileConfig) -> tuple[tuple[GraphModule, ...], ...]:
    profiles = config.profiles_for(source.entrypoint)
    roots = tuple((item.logical_name, item.input_name, item.axis) for item in config.bindings_for(source.entrypoint))
    requested = tuple((item.profile_id, item.bindings) for item in profiles)
    specialized = specialize_profiles(source, roots, root_symbol_bindings_from_graph(source), requested)
    if not source.symbols and not source.profile_bindings and len(profiles) == 1 and not profiles[0].bindings and source.profile_id == profiles[0].profile_id:
        return ((source, specialized[0]),)
    return tuple((item,) for item in specialized)


def _semantic_graph_bytes(graph: GraphModule) -> bytes:
    return canonical_json_bytes(graph.semantic_dict())


def _validate_planning_inputs(
    variants: tuple[GraphModule, ...],
    source_graphs: tuple[GraphModule, ...],
    arch: ArchManifest,
    effective: EffectiveCompileConfig,
) -> None:
    if type(variants) is not tuple or type(source_graphs) is not tuple:
        raise MeshIrError("E_CONFIG", "planning Graph collections must be immutable tuples")
    validate_effective_compile_config(effective, arch)
    config = effective.config
    if len(source_graphs) != len(config.entrypoints):
        raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "source Graph membership does not match configured entrypoints")
    expected = []
    for index, entrypoint in enumerate(config.entrypoints):
        source = source_graphs[index]
        if type(source) is not GraphModule:
            raise MeshIrError("E_CONFIG", "source Graph record type is invalid", entrypoint=entrypoint)
        source.verify()
        if source.entrypoint != entrypoint or source.arch_digest != effective.arch_digest or source.profile_bindings:
            code = "E_ARCH_DIGEST" if source.arch_digest != effective.arch_digest else "E_SHAPE_PROFILE_MISMATCH"
            raise MeshIrError(code, "source Graph identity does not match planning configuration", entrypoint=entrypoint)
        expected.extend(_expected_variants(source, config))
    if len(variants) != len(expected):
        raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "concrete Graph membership does not match configured profiles")
    seen = set()
    for index, (actual, alternatives) in enumerate(zip(variants, expected)):
        if type(actual) is not GraphModule:
            raise MeshIrError("E_CONFIG", "concrete Graph record type is invalid", variant=index)
        actual.verify()
        identity = (actual.entrypoint, actual.profile_id)
        if identity in seen:
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "concrete Graph identity is duplicated", entrypoint=identity[0], profile_id=identity[1])
        seen.add(identity)
        if actual.arch_digest != effective.arch_digest:
            raise MeshIrError("E_ARCH_DIGEST", "concrete Graph architecture digest does not match", entrypoint=actual.entrypoint, profile_id=actual.profile_id)
        if not any(identity == (wanted.entrypoint, wanted.profile_id) and _semantic_graph_bytes(actual) == _semantic_graph_bytes(wanted) for wanted in alternatives):
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "concrete Graph does not match source specialization", entrypoint=actual.entrypoint, profile_id=actual.profile_id)


def _cost_task(payload: _CostTask) -> OperationCostRecord:
    return OperationCostRecord(payload.identity, estimate_operation_cost(payload.op, payload.operands, payload.result))


def _group_task(payload: _GroupTask) -> ParallelGroup:
    if len(payload.admissible_core_ids) < payload.tensor_parallel:
        raise MeshIrError(
            "E_PLACEMENT_INFEASIBLE",
            "tensor parallel degree exceeds admissible cores",
            tensor_parallel=payload.tensor_parallel,
            admissible_cores=len(payload.admissible_core_ids),
        )
    return ParallelGroup(
        payload.group_id,
        payload.function,
        tuple(LogicalParticipant(rank) for rank in range(payload.tensor_parallel)),
        payload.admissible_core_ids,
        payload.all_reduce_algorithm,
        payload.chunk_bytes,
    )


def _cost_tasks(variants: tuple[GraphModule, ...]) -> tuple[PassTask[_CostTask], ...]:
    tasks = []
    for variant_index, graph in enumerate(variants):
        values = {item.value_id: item for item in graph.values}
        for function in graph.functions:
            for op in function.ops:
                identity = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
                payload = _CostTask(identity, op, tuple(values[item] for item in op.operands), values[op.results[0]])
                tasks.append(PassTask(f"{variant_index}:{function.function_id}:{op.op_id}", payload))
    return tuple(tasks)


def _group_tasks(
    variants: tuple[GraphModule, ...],
    arch: ArchManifest,
    effective: EffectiveCompileConfig,
) -> tuple[PassTask[_GroupTask], ...]:
    config = effective.config
    allowed = set(config.placement.allowed_cores)
    reserved = set(config.placement.reserve_cores)
    admissible = tuple(core_id for core_id in arch.core_ids if core_id in allowed and core_id not in reserved)
    tasks = []
    group_id = 1
    for variant_index, graph in enumerate(variants):
        for function in graph.functions:
            identity = FunctionIdentity(graph.entrypoint, graph.profile_id, function.function_id)
            payload = _GroupTask(
                group_id,
                identity,
                config.parallelism.tensor_parallel,
                admissible,
                config.collectives.all_reduce_algorithm,
                config.collectives.chunk_bytes,
            )
            tasks.append(PassTask(f"{variant_index}:{function.function_id}", payload))
            group_id += 1
    return tuple(tasks)


def _sum_cost(costs: tuple[OperationCostRecord, ...], field: str) -> int:
    total = 0
    for record in costs:
        total = checked_add_u64(total, getattr(record.cost, field), field)
    return total


def _sum_participants(groups: tuple[ParallelGroup, ...]) -> int:
    total = 0
    for group in groups:
        total = checked_add_u64(total, len(group.participants), "parallel participants")
    return total


def _execution_slice(before: ExecutionDiagnostics, after: ExecutionDiagnostics) -> ExecutionDiagnostics:
    tasks = after.tasks[len(before.tasks):]
    return ExecutionDiagnostics(after.workers, after.submitted_tasks - before.submitted_tasks, after.completed_tasks - before.completed_tasks, tasks)


def plan_graphs_with_executor(
    variants: tuple[GraphModule, ...],
    arch: ArchManifest,
    effective: EffectiveCompileConfig,
    *,
    source_graphs: tuple[GraphModule, ...],
    executor: PassExecutor,
) -> PlanningResult:
    if type(executor) is not PassExecutor:
        raise MeshIrError("E_CONFIG", "planning requires a live PassExecutor")
    _validate_planning_inputs(variants, source_graphs, arch, effective)
    diagnostics_before = executor.diagnostics
    graph_hash = graph_set_sha256(variants)
    started = time.perf_counter_ns()
    records = [record_pass("FuseVerifiedPatterns", graph_hash, graph_hash, time.perf_counter_ns() - started, (("fusions", 0),))]
    started = time.perf_counter_ns()
    costs = tuple(item.value for item in executor.run("AnnotateCostAndBytes", _cost_task, _cost_tasks(variants)))
    cost_state = PlanningState(variants, costs, ())
    cost_hash = cost_state.semantic_sha256()
    cost_statistics = (
        ("allocated_storage_bytes", _sum_cost(costs, "allocated_storage_bytes")),
        ("logical_input_bytes", _sum_cost(costs, "logical_input_bytes")),
        ("logical_output_bytes", _sum_cost(costs, "logical_output_bytes")),
        ("macs", _sum_cost(costs, "macs")),
        ("operations", len(costs)),
        ("reduction_ops", _sum_cost(costs, "reduction_ops")),
        ("variants", len(variants)),
        ("vector_ops", _sum_cost(costs, "vector_ops")),
    )
    records.append(record_pass("AnnotateCostAndBytes", graph_hash, cost_hash, time.perf_counter_ns() - started, cost_statistics))
    started = time.perf_counter_ns()
    groups = tuple(item.value for item in executor.run("ChooseParallelPlan", _group_task, _group_tasks(variants, arch, effective)))
    state = PlanningState(variants, costs, groups)
    final_hash = state.semantic_sha256()
    group_statistics = (
        ("admissible_cores", len(groups[0].admissible_core_ids) if groups else 0),
        ("groups", len(groups)),
        ("participants", _sum_participants(groups)),
    )
    records.append(record_pass("ChooseParallelPlan", cost_hash, final_hash, time.perf_counter_ns() - started, group_statistics))
    passes = tuple(records)
    verify_pass_chain(passes, graph_hash, final_hash, ("FuseVerifiedPatterns", "AnnotateCostAndBytes", "ChooseParallelPlan"))
    return PlanningResult(source_graphs, state, passes, _execution_slice(diagnostics_before, executor.diagnostics))


def plan_graphs(
    variants: tuple[GraphModule, ...],
    arch: ArchManifest,
    effective: EffectiveCompileConfig,
    *,
    source_graphs: tuple[GraphModule, ...],
    workers: int = 1,
) -> PlanningResult:
    with PassExecutor(workers) as executor:
        return plan_graphs_with_executor(variants, arch, effective, source_graphs=source_graphs, executor=executor)


__all__ = [
    "FunctionIdentity",
    "LogicalParticipant",
    "OperationIdentity",
    "OperationCostRecord",
    "ParallelGroup",
    "PlanningResult",
    "PlanningState",
    "graph_set_sha256",
    "plan_graphs",
    "plan_graphs_with_executor",
]

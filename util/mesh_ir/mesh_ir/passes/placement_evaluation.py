import logging
from dataclasses import dataclass, replace

from mesh_ir.architecture import ArchManifest
from mesh_ir.compile_config import Collectives, Tiling
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import GraphOp, GraphValue, METADATA_VIEW_OPCODES
from mesh_ir.passes.execution import PassExecutor, PassTask
from mesh_ir.passes.placement_geometry import (
    GeometryCostTemplate, GeometryEstimate, PlacementLedger, PlacementRejection,
    PlacementValues, ShardingStrategy, derive_operation_geometry, validate_placement_candidate,
    ValueDistribution, project_view_distribution,
)


TemplateKey = tuple[ShardingStrategy, tuple[int | None, ...]]
PlacementTemplate = tuple[tuple[int, ...], GeometryCostTemplate | PlacementRejection]


@dataclass(frozen=True)
class _TemplateBatch:
    op: GraphOp
    values: tuple[GraphValue, ...]
    ledger: PlacementLedger
    tiling: Tiling
    collectives: Collectives
    arch: ArchManifest
    view_operations: tuple[GraphOp, ...]
    candidates: tuple[tuple[ShardingStrategy, tuple[int, ...]], ...]


class ViewDistributionConstraints:
    def __init__(self, operations: tuple[GraphOp, ...], values: PlacementValues):
        self.values = values
        self.consumers: dict[int, list[GraphOp]] = {}
        for op in operations:
            if op.opcode in METADATA_VIEW_OPCODES:
                self.consumers.setdefault(op.operands[0], []).append(op)

    def check(self, distribution: ValueDistribution) -> PlacementRejection | None:
        pending = [distribution]
        while pending:
            source = pending.pop()
            for op in self.consumers.get(source.value_id, ()):
                transformed = project_view_distribution(op,
                    self.values.by_id[source.value_id], source,
                    self.values.by_id[op.results[0]])
                if type(transformed) is PlacementRejection:
                    return PlacementRejection(transformed.code,
                        f'consumer view {op.op_id} ({op.opcode.value}): {transformed.message}')
                pending.append(transformed)
        return None


class PlacementGeometryEvaluator:
    def __init__(self, op: GraphOp, values: PlacementValues, ledger: PlacementLedger,
                 tiling: Tiling, collectives: Collectives, arch: ArchManifest,
                 view_constraints: ViewDistributionConstraints | None = None):
        self.op = op
        self.values = values
        self.ledger = ledger
        self.tiling = tiling
        self.collectives = collectives
        self.arch = arch
        self.view_constraints = view_constraints
        operand_roots = {values.by_id[value].alias_root for value in op.operands}
        self.fixed_cores = frozenset(
            [row.window.owner_core for row in ledger.retained_backings
             if row.mapped_region.alias_root in operand_roots] +
            [core for value in op.operands if value in ledger.distributions
             for core in ledger.distributions[value].core_ids])
        self.templates: dict[TemplateKey, PlacementTemplate] = {}

    def template_key(self, strategy: ShardingStrategy, core_ids: tuple[int, ...]) -> TemplateKey:
        return strategy, tuple(core if core in self.fixed_cores else None for core in core_ids)

    def template(self, strategy: ShardingStrategy, core_ids: tuple[int, ...]) -> PlacementTemplate:
        validate_placement_candidate(strategy, core_ids, self.arch, self.op.op_id)
        key = self.template_key(strategy, core_ids)
        if key not in self.templates:
            try:
                geometry = derive_operation_geometry(self.op, self.values, strategy, core_ids,
                    self.ledger, self.tiling, self.collectives, self.arch)
                if type(geometry) is not PlacementRejection and self.view_constraints is not None:
                    rejection = self.view_constraints.check(geometry.result_distribution)
                    if rejection is not None:
                        geometry = rejection
                template = (geometry if type(geometry) is PlacementRejection else
                            GeometryCostTemplate.from_geometry(geometry, self.ledger))
            except MeshIrError as error:
                if error.code != 'E_ABI_OVERFLOW':
                    raise
                template = PlacementRejection(error.code, error.message)
            self.templates[key] = (core_ids, template)
        return self.templates[key]

    def prepare(self, strategies: tuple[ShardingStrategy, ...], core_sets: tuple[tuple[int, ...], ...],
                executor: PassExecutor) -> None:
        representatives = {}
        for core_ids in core_sets:
            for strategy in strategies:
                key = self.template_key(strategy, core_ids)
                if key not in self.templates:
                    representatives.setdefault(key, (strategy, core_ids))
        if not representatives:
            return
        view_operations = []
        pending = list(self.op.results)
        value_ids = set(self.op.operands + self.op.results)
        value_ids.update(item.window.value_id for item in self.ledger.retained_backings)
        while pending and self.view_constraints is not None:
            for op in self.view_constraints.consumers.get(pending.pop(), ()):
                view_operations.append(op)
                value_ids.update(op.operands + op.results)
                pending.extend(op.results)
        context = _TemplateBatch(self.op, tuple(self.values.by_id[value] for value in sorted(value_ids)),
            replace(self.ledger), self.tiling, self.collectives, self.arch, tuple(view_operations), ())
        batches = [[] for _ in range(min(executor.workers, len(representatives)))]
        for index, candidate in enumerate(representatives.values()):
            batches[index % len(batches)].append(candidate)
        tasks = tuple(PassTask(f'op-{self.op.op_id}:geometry-{index}',
            replace(context, candidates=tuple(candidates))) for index, candidates in enumerate(batches))
        logging.getLogger(__name__).info('placement geometry op_id=%s templates=%s batches=%s workers=%s',
            self.op.op_id, len(representatives), len(tasks), executor.workers)
        for completed in executor.run('PlaceOpsAndTensors', _build_templates, tasks):
            self.templates.update(completed.value)

    def estimate(self, strategy: ShardingStrategy, core_ids: tuple[int, ...]) -> GeometryEstimate | PlacementRejection:
        representative, template = self.template(strategy, core_ids)
        if type(template) is PlacementRejection:
            return template
        return template.estimate(self.arch, dict(zip(representative, core_ids)))


def _build_templates(task: _TemplateBatch) -> tuple[tuple[TemplateKey, PlacementTemplate], ...]:
    values = PlacementValues(task.values)
    evaluator = PlacementGeometryEvaluator(task.op, values, task.ledger, task.tiling, task.collectives,
        task.arch, ViewDistributionConstraints(task.view_operations, values))
    return tuple((evaluator.template_key(strategy, cores), evaluator.template(strategy, cores))
        for strategy, cores in task.candidates)

from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.architecture import ArchManifest
from mesh_ir.compile_config import EffectiveCompileConfig
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import GraphModule
from mesh_ir.ir.kernel_ir import KernelBundle
from mesh_ir.model import Program
from mesh_ir.passes.execution import ExecutionDiagnostics, PASS_REGISTRY, PassExecutor, PassRecord, verify_pass_chain
from mesh_ir.passes.graph_to_kernel import KernelLoweringResult, lower_to_kernel, verify_kernel_bundle_correspondence
from mesh_ir.passes.planning_prefix import PlanningResult, graph_set_sha256, plan_graphs_with_executor
from mesh_ir.passes.scheduled import ScheduledLoweringResult, lower_to_program
from mesh_ir.scheduled.verify import verify_program, verify_program_kernel_correspondence
from mesh_ir.traffic import TrafficReport


_REGISTERED_NAMES = tuple(item.name for item in PASS_REGISTRY)
_BACKEND_PASS_NAMES = _REGISTERED_NAMES[
    _REGISTERED_NAMES.index("FuseVerifiedPatterns"):_REGISTERED_NAMES.index("ComputeExpectedTraffic") + 1
]


@dataclass(frozen=True)
class BackendCompilationResult:
    planning: PlanningResult
    kernel: KernelLoweringResult
    scheduled: ScheduledLoweringResult

    def __post_init__(self) -> None:
        if type(self.planning) is not PlanningResult or type(self.kernel) is not KernelLoweringResult or type(self.scheduled) is not ScheduledLoweringResult:
            raise MeshIrError("E_CONFIG", "backend compilation stage result types are invalid")
        graph_hash = graph_set_sha256(self.planning.state.variants)
        planning_hash = self.planning.state.semantic_sha256()
        kernel_hash = self.kernel.bundle.semantic_sha256
        program_hash = self.scheduled.program.semantic_sha256
        verify_pass_chain(self.planning.passes, graph_hash, planning_hash, _BACKEND_PASS_NAMES[:3])
        verify_pass_chain(self.kernel.passes, planning_hash, kernel_hash, _BACKEND_PASS_NAMES[3:9])
        verify_pass_chain(self.scheduled.passes, kernel_hash, program_hash, _BACKEND_PASS_NAMES[9:])
        verify_pass_chain(self.passes, graph_hash, program_hash, _BACKEND_PASS_NAMES)
        verify_kernel_bundle_correspondence(self.planning.state.variants, self.kernel.bundle)
        verify_program_kernel_correspondence(self.scheduled.program, self.kernel.bundle)

    @property
    def source_graphs(self) -> tuple[GraphModule, ...]:
        return self.planning.source_graphs

    @property
    def variants(self) -> tuple[GraphModule, ...]:
        return self.planning.state.variants

    @property
    def bundle(self) -> KernelBundle:
        return self.kernel.bundle

    @property
    def program(self) -> Program:
        return self.scheduled.program

    @property
    def passes(self) -> tuple[PassRecord, ...]:
        return self.planning.passes + self.kernel.passes + self.scheduled.passes

    @property
    def execution(self) -> ExecutionDiagnostics:
        return self.scheduled.execution

    @property
    def intrinsic_traffic(self) -> TrafficReport:
        return self.scheduled.program.semantics.intrinsic_traffic


def compile_backend(
    variants: tuple[GraphModule, ...],
    arch: ArchManifest,
    effective: EffectiveCompileConfig,
    *,
    source_graphs: tuple[GraphModule, ...],
    workers: int = 1,
) -> BackendCompilationResult:
    with PassExecutor(workers) as executor:
        planning = plan_graphs_with_executor(variants, arch, effective, source_graphs=source_graphs, executor=executor)
        kernel = lower_to_kernel(planning, arch, effective, executor)
        scheduled = lower_to_program(kernel, arch, effective, executor)
        result = BackendCompilationResult(planning, kernel, scheduled)
        verify_program(result.program, arch)
        return result


__all__ = ["BackendCompilationResult", "compile_backend"]

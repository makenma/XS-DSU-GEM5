from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mesh_ir.architecture import ArchManifest
from mesh_ir.compile_config import EffectiveCompileConfig
from mesh_ir.ir.graph_ir import GraphModule
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor
from mesh_ir.passes.graph_to_kernel import KernelLoweringResult, lower_to_kernel
from mesh_ir.passes.planning_prefix import PlanningResult, plan_graphs_with_executor


@dataclass(frozen=True)
class KernelPipelineInputs:
    variants: tuple[GraphModule, ...]
    source_graphs: tuple[GraphModule, ...]
    arch: ArchManifest
    effective: EffectiveCompileConfig


@dataclass(frozen=True)
class KernelPipelineRun:
    planning: PlanningResult
    lowering: KernelLoweringResult
    prefix_execution: ExecutionDiagnostics


def run_kernel_pipeline(inputs: KernelPipelineInputs, workers: int) -> KernelPipelineRun:
    with PassExecutor(workers) as executor:
        planning = plan_graphs_with_executor(
            inputs.variants,
            inputs.arch,
            inputs.effective,
            source_graphs=inputs.source_graphs,
            executor=executor,
        )
        prefix_execution = executor.diagnostics
        lowering = lower_to_kernel(planning, inputs.arch, inputs.effective, executor)
    return KernelPipelineRun(planning, lowering, prefix_execution)


def _torch_unavailable(_: int) -> tuple[int, bool, bool]:
    return os.getpid(), importlib.util.find_spec("torch") is None, "torch" not in sys.modules


def _semantic_result(run: KernelPipelineRun) -> dict[str, object]:
    passes = run.planning.passes + run.lowering.passes
    return {
        "bundle": run.lowering.bundle.canonical_bytes().hex(),
        "planning_state": run.planning.state.semantic_bytes().hex(),
        "passes": [
            {
                "name": item.name,
                "version": item.version,
                "input_hash": item.input_hash,
                "output_hash": item.output_hash,
                "statistics": item.statistics,
            }
            for item in passes
        ],
    }


def main() -> None:
    payload_path = Path(sys.argv[1])
    workers = int(sys.argv[2])
    driver_pid = os.getpid()
    require_no_torch = len(sys.argv) == 4 and sys.argv[3] == "--require-no-torch"
    probe = ()
    if require_no_torch:
        if importlib.util.find_spec("torch") is not None or "torch" in sys.modules:
            raise RuntimeError("Torch is available in the backend process")
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            probe = tuple(pool.map(_torch_unavailable, range(workers)))
        if not probe or not all(item[0] != driver_pid and item[1] and item[2] for item in probe):
            raise RuntimeError("Torch is available in a spawned backend worker")
    payload = pickle.loads(payload_path.read_bytes())
    if type(payload) is not tuple or len(payload) != 4:
        raise TypeError("kernel pipeline input has invalid type")
    variants, source_graphs, arch, effective = payload
    if (
        type(variants) is not tuple
        or not all(type(item) is GraphModule for item in variants)
        or type(source_graphs) is not tuple
        or not all(type(item) is GraphModule for item in source_graphs)
        or type(arch) is not ArchManifest
        or type(effective) is not EffectiveCompileConfig
    ):
        raise TypeError("kernel pipeline input has invalid type")
    inputs = KernelPipelineInputs(variants, source_graphs, arch, effective)
    run = run_kernel_pipeline(inputs, workers)
    if require_no_torch and (importlib.util.find_spec("torch") is not None or "torch" in sys.modules):
        raise RuntimeError("Torch became available in the backend process")
    diagnostics = run.lowering.execution
    print(
        json.dumps(
            {
                "semantic": _semantic_result(run),
                "observed": {
                    "workers": workers,
                    "driver_pid": driver_pid,
                    "submitted": diagnostics.submitted_tasks,
                    "completed": diagnostics.completed_tasks,
                    "task_count": len(diagnostics.tasks),
                    "pipeline_worker_pids": sorted({item.worker_pid for item in diagnostics.tasks if item.worker_pid is not None}),
                    "environment_probe_pids": sorted({item[0] for item in probe}),
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import multiprocessing
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from mesh_ir.architecture import ArchManifest
from mesh_ir.compile_config import EffectiveCompileConfig
from mesh_ir.compiler import compile_backend
from mesh_ir.ir.graph_ir import GraphModule


def backend_golden_identity(result, variants, source_graphs, arch, effective):
    config = effective.config
    key = f"{config.collectives.all_reduce_algorithm}-tp{config.parallelism.tensor_parallel}"
    return {
        "schema_version": "mesh-backend-golden-v1",
        "architecture_digest": arch.digest().hex(),
        "source_graphs": [
            {
                "entrypoint": item.entrypoint,
                "profile_id": item.profile_id,
                "semantic_sha256": item.semantic_sha256,
            }
            for item in source_graphs
        ],
        "variants": [
            {
                "entrypoint": item.entrypoint,
                "profile_id": item.profile_id,
                "semantic_sha256": item.semantic_sha256,
            }
            for item in variants
        ],
        "configuration": {
            "key": key,
            "effective_config_sha256": hashlib.sha256(effective.canonical_bytes()).hexdigest(),
            "kernels": [
                {
                    "entrypoint": item.entrypoint,
                    "profile_id": item.profile_id,
                    "semantic_sha256": item.semantic_sha256,
                }
                for item in result.bundle.modules
            ],
            "program_semantic_sha256": result.program.semantic_sha256,
            "intrinsic_traffic_semantic_sha256": result.program.semantics.intrinsic_traffic.semantic_sha256,
        },
    }


def write_backend_golden_candidate(actual, path):
    path = path.resolve()
    root = Path(__file__).resolve().parents[4]
    docs = (root / ".tmp/docs").resolve()
    if not path.is_relative_to(docs) or path == docs:
        raise ValueError(f"golden candidate must be a file under {docs}")
    configuration = actual["configuration"]
    candidate = {
        "schema_version": actual["schema_version"],
        "architecture_digest": actual["architecture_digest"],
        "source_graphs": actual["source_graphs"],
        "variants": actual["variants"],
        "configurations": {configuration["key"]: {key: value for key, value in configuration.items() if key != "key"}},
    }
    if path.exists():
        current = json.loads(path.read_text())
        for key in ("schema_version", "architecture_digest", "source_graphs", "variants"):
            if current[key] != candidate[key]:
                raise RuntimeError(f"golden candidate common identity changed at {key}")
        current["configurations"].update(candidate["configurations"])
        candidate = current
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(candidate, sort_keys=True, indent=2) + "\n")


def _torch_unavailable(_: int) -> tuple[int, bool, bool]:
    return os.getpid(), importlib.util.find_spec("torch") is None, "torch" not in sys.modules


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("workers", type=int)
    parser.add_argument("--require-no-torch", action="store_true")
    parser.add_argument("--golden-candidate", type=Path)
    parser.add_argument("--mshb-output", type=Path)
    arguments = parser.parse_args()
    payload_path = arguments.payload
    workers = arguments.workers
    driver_pid = os.getpid()
    require_no_torch = arguments.require_no_torch
    probe = ()
    if require_no_torch:
        if importlib.util.find_spec("torch") is not None or "torch" in sys.modules:
            raise RuntimeError("Torch is available in the backend process")
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            probe = tuple(pool.map(_torch_unavailable, range(workers)))
        if not probe or not all(item[0] != driver_pid and item[1] and item[2] for item in probe):
            raise RuntimeError("Torch is available in a spawned environment probe")
    payload = pickle.loads(payload_path.read_bytes())
    if type(payload) is not tuple or len(payload) != 4:
        raise TypeError("backend compiler input has invalid type")
    variants, source_graphs, arch, effective = payload
    if (
        type(variants) is not tuple
        or any(type(item) is not GraphModule for item in variants)
        or type(source_graphs) is not tuple
        or any(type(item) is not GraphModule for item in source_graphs)
        or type(arch) is not ArchManifest
        or type(effective) is not EffectiveCompileConfig
    ):
        raise TypeError("backend compiler input has invalid type")
    result = compile_backend(variants, arch, effective, source_graphs=source_graphs, workers=workers)
    if require_no_torch and (importlib.util.find_spec("torch") is not None or "torch" in sys.modules):
        raise RuntimeError("Torch became available in the backend process")
    diagnostics = result.execution
    golden = backend_golden_identity(result, variants, source_graphs, arch, effective)
    if arguments.golden_candidate is not None:
        write_backend_golden_candidate(golden, arguments.golden_candidate)
    if arguments.mshb_output is not None:
        from mesh_ir.abi import encode_program

        binary = encode_program(result.program)
        with arguments.mshb_output.open("xb") as output:
            output.write(binary)
    document = {
        "golden": golden,
        "semantic": {
            "planning": result.planning.state.semantic_bytes().hex(),
            "kernel": result.bundle.canonical_bytes().hex(),
            "program": result.program.canonical_bytes().hex(),
            "passes": [
                {
                    "name": item.name,
                    "version": item.version,
                    "input_hash": item.input_hash,
                    "output_hash": item.output_hash,
                    "statistics": item.statistics,
                }
                for item in result.passes
            ],
        },
        "observed": {
            "workers": workers,
            "driver_pid": driver_pid,
            "submitted": diagnostics.submitted_tasks,
            "completed": diagnostics.completed_tasks,
            "task_count": len(diagnostics.tasks),
            "pipeline_worker_pids": sorted({item.worker_pid for item in diagnostics.tasks if item.worker_pid is not None}),
            "environment_probe_pids": sorted({item[0] for item in probe}),
        },
    }
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

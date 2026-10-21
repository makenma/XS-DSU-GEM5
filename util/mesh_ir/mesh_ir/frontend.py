from __future__ import annotations

import hashlib
import inspect
import platform
import re
import subprocess
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, metadata
from importlib.resources import files
from pathlib import Path

from mesh_ir.canonical import canonical_json_bytes, to_canonical
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import GraphModule
from mesh_ir.passes.canonicalize import canonicalize_graph
from mesh_ir.passes.decompose import DecompositionManifest, decompose
from mesh_ir.passes.import_export import import_export
from mesh_ir.passes.execution import PassRecord, record_pass
from mesh_ir.passes.planning_prefix import graph_set_sha256
from mesh_ir.passes.shape_specialize import specialize_profiles
from mesh_ir.schema import validate_schema
from mesh_ir.torch_compat import PINNED_TORCH_VERSION, export_program, load_exported_program, summarize_export_inputs


@dataclass(frozen=True)
class FrontendRequest:
    entrypoint: str
    profile_id: str
    arch_digest: str
    staging_dir: Path
    symbol_bindings: tuple[tuple[str, str, int], ...] = ()
    shape_profiles: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = ()
    source_project_root: Path | None = None


@dataclass(frozen=True)
class SourceProvenance:
    python_version: str
    torch_version: str
    source_file: str | None
    source_sha256: str | None
    export_args: tuple[tuple[str, object], ...]
    source_opset: tuple[tuple[str, int], ...]
    source_schema: tuple[int, int]
    archive_sha256: str
    archive_semantic_sha256: str
    compiler_mode: str
    compiler_git_sha: str
    compiler_package_sha256: str
    requirements_lock_sha256: str


@dataclass(frozen=True)
class FrontendResult:
    graph: GraphModule
    variants: tuple[GraphModule, ...]
    decomposition_manifest: DecompositionManifest
    passes: tuple[PassRecord, ...]
    provenance: SourceProvenance


def _digest_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(factory) -> tuple[str | None, str | None]:
    name = inspect.getsourcefile(factory)
    if name is None:
        return None, None
    path = Path(name).resolve()
    try:
        stable = path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        stable = path.name
    return stable, _digest_file(path)


def _record(name: str, started: int, before: str, after: str, **statistics: int) -> PassRecord:
    return record_pass(name, before, after, time.perf_counter_ns() - started, tuple(sorted(statistics.items())))


def _package_digest(root) -> str:
    entries = []

    def visit(node, relative: str) -> None:
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            child_relative = f"{relative}/{child.name}" if relative else child.name
            if child.name == "__pycache__" or child.name == "requirements-lock.txt":
                continue
            if child.is_dir():
                visit(child, child_relative)
            elif child.name.endswith((".py", ".json", ".yaml")):
                entries.append((child_relative, hashlib.sha256(child.read_bytes()).hexdigest()))

    visit(root, "")
    return hashlib.sha256(canonical_json_bytes(entries)).hexdigest()


def _compiler_identity(request: FrontendRequest) -> tuple[str, str, str, str]:
    if request.source_project_root is not None:
        project = Path(request.source_project_root).resolve()
        package = project / "mesh_ir"
        if package.resolve() != Path(__file__).resolve().parent:
            raise MeshIrError("E_EXPORT_VERSION", "source project root does not own the imported compiler package", project_root=str(project))
        lock = project / "requirements-lock.txt"
        try:
            lock_bytes = lock.read_bytes()
            completed = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise MeshIrError("E_EXPORT_VERSION", "source compiler identity is unavailable", detail=str(error)) from error
        git_sha = completed.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
            raise MeshIrError("E_EXPORT_VERSION", "source compiler git identity is malformed", git_sha=git_sha)
        return "source", git_sha, _package_digest(package), hashlib.sha256(lock_bytes).hexdigest()
    package = files("mesh_ir")
    try:
        lock_bytes = package.joinpath("requirements-lock.txt").read_bytes()
        project_metadata = metadata("mesh-ir")
    except (FileNotFoundError, OSError, PackageNotFoundError) as error:
        raise MeshIrError("E_EXPORT_VERSION", "installed compiler identity is unavailable", detail=str(error)) from error
    candidates = []
    for item in project_metadata.get_all("Project-URL") or ():
        label, separator, value = item.partition(",")
        if separator and label.strip() == "Compiler Source":
            match = re.search(r"([0-9a-f]{40})$", value.strip())
            if match:
                candidates.append(match.group(1))
    if len(candidates) != 1:
        raise MeshIrError("E_EXPORT_VERSION", "installed compiler metadata has no unique source commit")
    return "installed", candidates[0], _package_digest(package), hashlib.sha256(lock_bytes).hexdigest()


def _provenance(handle, request: FrontendRequest, source_file: str | None, source_sha: str | None, export_args: tuple[tuple[str, object], ...]) -> SourceProvenance:
    mode, git_sha, package_sha, lock_sha = _compiler_identity(request)
    return SourceProvenance(
        platform.python_version(), PINNED_TORCH_VERSION, source_file, source_sha, export_args,
        handle.source_opset, handle.source_schema, handle.archive_sha256, handle.source_semantic_sha256, mode, git_sha, package_sha, lock_sha,
    )


def _run(handle, request: FrontendRequest, provenance: SourceProvenance, validation_elapsed_ns: int) -> FrontendResult:
    records = []
    source_identity = handle.source_semantic_sha256
    records.append(record_pass("LoadAndValidateExport", source_identity, source_identity, validation_elapsed_ns))
    started = time.perf_counter_ns()
    functional, decomposition_manifest = decompose(handle)
    decomposition_elapsed = time.perf_counter_ns() - started
    started = time.perf_counter_ns()
    dto = import_export(functional)
    dto_hash = dto.semantic_hash()
    records.append(record_pass("DecomposeToPinnedCoreAten", source_identity, dto_hash, decomposition_elapsed, (("operators", sum(count for _, count in decomposition_manifest.after_histogram)),)))
    records.append(_record("ImportGraphIR", started, dto_hash, dto_hash, values=len(dto.values), nodes=len(dto.nodes)))
    started = time.perf_counter_ns()
    graph = canonicalize_graph(dto, request.arch_digest, request.entrypoint, request.profile_id)
    validate_schema("mesh_graph_v1.schema.json", to_canonical(graph.canonical_dict()), "Graph IR")
    records.append(_record("CanonicalizeFunctionalOps", started, dto_hash, graph.semantic_sha256, values=len(graph.values), ops=len(graph.functions[0].ops)))
    started = time.perf_counter_ns()
    if request.shape_profiles:
        variants = specialize_profiles(graph, request.symbol_bindings, dto.root_symbol_bindings, request.shape_profiles)
    elif graph.symbols:
        raise MeshIrError("E_SHAPE_UNBOUND", "dynamic export requires concrete shape profiles")
    else:
        variants = (graph,)
    for variant in variants:
        validate_schema("mesh_graph_v1.schema.json", to_canonical(variant.canonical_dict()), "Graph IR")
    variant_hash = graph_set_sha256(variants)
    records.append(_record("SpecializeShapeProfiles", started, graph.semantic_sha256, variant_hash, variants=len(variants)))
    staging = Path(request.staging_dir)
    try:
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "graph_ir.json").write_bytes(graph.canonical_bytes())
        (staging / "decomposition_manifest.json").write_bytes(canonical_json_bytes(decomposition_manifest))
        (staging / "frontend_provenance.json").write_bytes(canonical_json_bytes(provenance))
        (staging / "frontend_passes.json").write_bytes(canonical_json_bytes(records))
    except OSError as error:
        raise MeshIrError("E_CONFIG", "cannot write private frontend artifacts", path=str(staging), detail=str(error)) from error
    return FrontendResult(graph, variants, decomposition_manifest, tuple(records), provenance)


def export_graph(factory, args: tuple[object, ...], request: FrontendRequest, dynamic_shapes=None) -> FrontendResult:
    if not callable(factory):
        raise MeshIrError("E_CONFIG", "module factory must be callable")
    staging = Path(request.staging_dir)
    try:
        staging.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise MeshIrError("E_CONFIG", "cannot create private frontend staging", path=str(staging), detail=str(error)) from error
    source_file, source_sha = _source(factory)
    started = time.perf_counter_ns()
    handle = export_program(factory, args, staging / "exported_program.pt2", dynamic_shapes)
    validation_elapsed_ns = time.perf_counter_ns() - started
    provenance = _provenance(handle, request, source_file, source_sha, summarize_export_inputs(args, dynamic_shapes))
    return _run(handle, request, provenance, validation_elapsed_ns)


def load_graph(path: str | Path, request: FrontendRequest) -> FrontendResult:
    source = Path(path)
    started = time.perf_counter_ns()
    handle = load_exported_program(source)
    validation_elapsed_ns = time.perf_counter_ns() - started
    provenance = _provenance(handle, request, source.name, _digest_file(source), ())
    return _run(handle, request, provenance, validation_elapsed_ns)

from __future__ import annotations

import hashlib
import importlib
import inspect
from pathlib import Path

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import CompileOverrides, load_compile_config, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.frontend import FrontendRequest, export_graph, load_graph
from mesh_ir.publication import ArtifactTransaction, PublicationResult, publish_compilation


def _source_project_root() -> Path | None:
    package = Path(__file__).resolve().parent
    candidate = package.parent
    if (candidate / "pyproject.toml").is_file() and (candidate / "requirements-lock.txt").is_file() and (candidate / "mesh_ir").resolve() == package:
        return candidate
    return None


def _entrypoint(config, requested: str | None) -> str:
    if len(config.entrypoints) != 1:
        raise MeshIrError("E_CONFIG", "one CLI source requires exactly one configured entrypoint", entrypoints=list(config.entrypoints))
    configured = config.entrypoints[0]
    if requested is not None and requested != configured:
        raise MeshIrError("E_CONFIG", "CLI entrypoint differs from compile configuration", requested=requested, configured=configured)
    return configured


def _request(config, entrypoint: str, arch_digest: str, staging: Path) -> FrontendRequest:
    profiles = config.profiles_for(entrypoint)
    shape_profiles = tuple((item.profile_id, item.bindings) for item in profiles)
    bindings = tuple((item.logical_name, item.input_name, item.axis) for item in config.bindings_for(entrypoint))
    specialize = len(profiles) != 1 or bool(profiles[0].bindings) or bool(bindings)
    return FrontendRequest(
        entrypoint,
        "symbolic" if specialize else profiles[0].profile_id,
        arch_digest,
        staging,
        bindings,
        shape_profiles if specialize else (),
        _source_project_root(),
    )


def resolve_module_factory(value: str):
    if type(value) is not str or value.count(":") != 1:
        raise MeshIrError("E_CONFIG", "module factory must use MODULE:FACTORY syntax", module=value)
    module_name, factory_name = value.split(":")
    if not module_name or not factory_name or "." in factory_name:
        raise MeshIrError("E_CONFIG", "module factory name is invalid", module=value)
    try:
        module = importlib.import_module(module_name)
    except (ImportError, ValueError) as error:
        raise MeshIrError("E_CONFIG", "cannot import module factory", module=module_name, detail=str(error)) from error
    try:
        factory = getattr(module, factory_name)
    except AttributeError as error:
        raise MeshIrError("E_CONFIG", "module factory does not exist", module=module_name, factory=factory_name) from error
    if not callable(factory):
        raise MeshIrError("E_CONFIG", "module factory must be callable", module=module_name, factory=factory_name)
    return factory


def compile_exported_program(
    exported_program: str | Path,
    arch_path: str | Path,
    config_path: str | Path,
    output: str | Path,
    *,
    workers: int = 1,
    entrypoint: str | None = None,
    overrides: CompileOverrides = CompileOverrides(),
) -> PublicationResult:
    protected = (Path(exported_program), Path(arch_path), Path(config_path))
    with ArtifactTransaction(output, protected) as transaction:
        arch = load_arch(arch_path)
        config = load_compile_config(config_path, arch)
        selected = _entrypoint(config, entrypoint)
        effective = resolve_compile_config(config, arch, overrides)
        frontend = load_graph(
            exported_program,
            _request(config, selected, arch.digest().hex(), transaction.working_directory / "frontend"),
        )
        backend = compile_backend(
            frontend.variants,
            arch,
            effective,
            source_graphs=(frontend.graph,),
            workers=workers,
        )
        return publish_compilation(transaction, (frontend,), backend, arch, effective)


def export_and_compile_factory(
    module_factory: str,
    inputs_path: str | Path,
    dynamic_shapes_path: str | Path | None,
    arch_path: str | Path,
    config_path: str | Path,
    output: str | Path,
    *,
    workers: int = 1,
    entrypoint: str | None = None,
    overrides: CompileOverrides = CompileOverrides(),
) -> PublicationResult:
    factory = resolve_module_factory(module_factory)
    try:
        source_name = inspect.getsourcefile(factory)
    except TypeError as error:
        raise MeshIrError("E_CONFIG", "module factory source is unavailable", module=module_factory) from error
    protected = tuple(Path(item) for item in (inputs_path, dynamic_shapes_path, arch_path, config_path, source_name) if item is not None)
    with ArtifactTransaction(output, protected) as transaction:
        from mesh_ir.export_inputs import load_export_input_documents

        arch = load_arch(arch_path)
        config = load_compile_config(config_path, arch)
        selected = _entrypoint(config, entrypoint)
        effective = resolve_compile_config(config, arch, overrides)
        prepared = load_export_input_documents(inputs_path, dynamic_shapes_path)
        frontend_dir = transaction.working_directory / "frontend"
        frontend = export_graph(
            factory,
            prepared.args,
            _request(config, selected, arch.digest().hex(), frontend_dir),
            dynamic_shapes=prepared.dynamic_shapes,
        )
        exported = frontend_dir / "exported_program.pt2"
        try:
            exported_bytes = exported.read_bytes()
        except OSError as error:
            raise MeshIrError("E_EXPORT_VERSION", "exported program artifact is unavailable", detail=str(error)) from error
        if hashlib.sha256(exported_bytes).hexdigest() != frontend.provenance.archive_sha256:
            raise MeshIrError("E_ABI_CHECKSUM", "saved export bytes differ from frontend provenance")
        backend = compile_backend(
            frontend.variants,
            arch,
            effective,
            source_graphs=(frontend.graph,),
            workers=workers,
        )
        return publish_compilation(
            transaction,
            (frontend,),
            backend,
            arch,
            effective,
            exported_program_bytes=exported_bytes,
            input_documents=prepared.document_identities,
        )


__all__ = ["compile_exported_program", "export_and_compile_factory", "resolve_module_factory"]

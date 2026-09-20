from __future__ import annotations

import dataclasses
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.canonical import canonical_json_bytes
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.schema import load_yaml_mapping, validate_schema


@dataclass(frozen=True)
class ShapeProfile:
    profile_id: str
    bindings: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class SymbolBinding:
    logical_name: str
    input_name: str
    axis: int


@dataclass(frozen=True)
class Parallelism:
    tensor_parallel: int
    pipeline_parallel: int
    expert_parallel: int
    data_parallel: int


@dataclass(frozen=True)
class Placement:
    core_order: str
    allowed_cores: tuple[int, ...]
    reserve_cores: tuple[int, ...]


@dataclass(frozen=True)
class Tiling:
    gemm_m: int
    gemm_n: int
    gemm_k: int
    double_buffer: bool


@dataclass(frozen=True)
class Collectives:
    all_reduce_algorithm: str
    chunk_bytes: int


@dataclass(frozen=True)
class RuntimeModel:
    mode: str
    tensor_data: str


@dataclass(frozen=True)
class CompileConfig:
    schema_version: str
    entrypoints: tuple[str, ...]
    shape_profiles: tuple[tuple[str, tuple[ShapeProfile, ...]], ...]
    symbol_bindings: tuple[tuple[str, tuple[SymbolBinding, ...]], ...]
    parallelism: Parallelism
    placement: Placement
    tiling: Tiling
    collectives: Collectives
    runtime_model: RuntimeModel
    defaulted_paths: tuple[str, ...] = ()

    def profiles_for(self, entrypoint: str) -> tuple[ShapeProfile, ...]:
        return dict(self.shape_profiles)[entrypoint]

    def bindings_for(self, entrypoint: str) -> tuple[SymbolBinding, ...]:
        return dict(self.symbol_bindings).get(entrypoint, ())


@dataclass(frozen=True)
class CompileOverrides:
    gemm_m: int | None = None
    gemm_n: int | None = None
    gemm_k: int | None = None
    chunk_bytes: int | None = None
    tensor_parallel: int | None = None


@dataclass(frozen=True)
class ProvenanceEntry:
    path: str
    source: str
    value: object


@dataclass(frozen=True)
class EffectiveCompileConfig:
    config: CompileConfig
    arch_digest: str
    provenance: tuple[ProvenanceEntry, ...]

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


_CLI_PATHS = (
    ("tiling.gemm_m", "gemm_m"),
    ("tiling.gemm_n", "gemm_n"),
    ("tiling.gemm_k", "gemm_k"),
    ("collectives.chunk_bytes", "chunk_bytes"),
    ("parallelism.tensor_parallel", "tensor_parallel"),
)


def _validate_schema(raw: dict[str, Any]) -> None:
    validate_schema("mesh_compile_v1.schema.json", raw, "compile", apply_defaults=True)


def _config_raw(config: CompileConfig) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "entrypoints": list(config.entrypoints),
        "shape_profiles": {entrypoint: [{"profile_id": profile.profile_id, **dict(profile.bindings)} for profile in profiles] for entrypoint, profiles in config.shape_profiles},
        "symbol_bindings": {entrypoint: {binding.logical_name: {"input": binding.input_name, "axis": binding.axis} for binding in bindings} for entrypoint, bindings in config.symbol_bindings},
        "parallelism": dataclasses.asdict(config.parallelism),
        "placement": {"core_order": config.placement.core_order, "allowed_cores": list(config.placement.allowed_cores), "reserve_cores": list(config.placement.reserve_cores)},
        "tiling": dataclasses.asdict(config.tiling),
        "collectives": dataclasses.asdict(config.collectives),
        "runtime_model": dataclasses.asdict(config.runtime_model),
    }


def _from_raw(raw: dict[str, Any], arch: ArchManifest, defaulted_paths: tuple[str, ...] = ()) -> CompileConfig:
    profile_keys = set(raw["shape_profiles"])
    if profile_keys != set(raw["entrypoints"]):
        raise MeshIrError("E_CONFIG", "shape profile entrypoints must exactly match entrypoints")
    binding_keys = set(raw["symbol_bindings"])
    if not binding_keys <= set(raw["entrypoints"]):
        raise MeshIrError("E_CONFIG", "symbol binding references unknown entrypoint")
    profiles = []
    seen_profile_ids = set()
    for entrypoint in raw["entrypoints"]:
        items = []
        for item in raw["shape_profiles"][entrypoint]:
            profile_id = item["profile_id"]
            if profile_id in seen_profile_ids:
                raise MeshIrError("E_CONFIG", "profile id must be globally unique", profile_id=profile_id)
            seen_profile_ids.add(profile_id)
            items.append(ShapeProfile(profile_id, tuple(sorted((name, value) for name, value in item.items() if name != "profile_id"))))
        profiles.append((entrypoint, tuple(items)))
    bindings = []
    for entrypoint in raw["entrypoints"]:
        items = tuple(
            SymbolBinding(name, record["input"], record["axis"])
            for name, record in sorted(raw["symbol_bindings"].get(entrypoint, {}).items())
        )
        bindings.append((entrypoint, items))
    parallel = Parallelism(**raw["parallelism"])
    if parallel.pipeline_parallel != 1 or parallel.expert_parallel != 1 or parallel.data_parallel != 1:
        raise MeshIrError("E_PLACEMENT_INFEASIBLE", "pipeline, expert, and data parallelism are unsupported in this compiler version")
    placement = Placement(raw["placement"]["core_order"], tuple(raw["placement"]["allowed_cores"]), tuple(raw["placement"]["reserve_cores"]))
    known = set(arch.core_ids)
    if not set(placement.allowed_cores) <= known or not set(placement.reserve_cores) <= known:
        raise MeshIrError("E_PLACEMENT_INFEASIBLE", "placement contains a core absent from the architecture")
    if set(placement.allowed_cores) & set(placement.reserve_cores):
        raise MeshIrError("E_PLACEMENT_INFEASIBLE", "allowed and reserved cores overlap")
    if parallel.tensor_parallel > len(placement.allowed_cores):
        raise MeshIrError("E_PLACEMENT_INFEASIBLE", "tensor parallel degree exceeds allowed cores")
    return CompileConfig(
        raw["schema_version"], tuple(raw["entrypoints"]), tuple(profiles), tuple(bindings), parallel, placement,
        Tiling(**raw["tiling"]), Collectives(**raw["collectives"]), RuntimeModel(**raw["runtime_model"]), defaulted_paths,
    )


def load_compile_config_text(text: str, arch: ArchManifest) -> CompileConfig:
    raw = load_yaml_mapping(text, "compile configuration")
    defaulted_paths = validate_schema("mesh_compile_v1.schema.json", raw, "compile", apply_defaults=True)
    return _from_raw(raw, arch, defaulted_paths)


def load_compile_config(path: str | Path, arch: ArchManifest) -> CompileConfig:
    try:
        return load_compile_config_text(Path(path).read_text(encoding="utf-8"), arch)
    except OSError as error:
        raise MeshIrError("E_CONFIG", "cannot read compile configuration", path=str(path), detail=str(error)) from error


def resolve_compile_config(config: CompileConfig, arch: ArchManifest, overrides: CompileOverrides = CompileOverrides()) -> EffectiveCompileConfig:
    provenance = [
        ProvenanceEntry("architecture.digest", "architecture", arch.digest().hex()),
        ProvenanceEntry("compile", "workload", config),
    ]
    provenance.extend(ProvenanceEntry(path, "schema-default", _path_value(_config_raw(config), path)) for path in config.defaulted_paths)
    tiling = config.tiling
    collectives = config.collectives
    parallelism = config.parallelism
    for path, field in _CLI_PATHS:
        value = getattr(overrides, field)
        if value is None:
            continue
        section = path.split(".", 1)[0]
        if section == "tiling":
            tiling = dataclasses.replace(tiling, **{field: value})
        elif section == "collectives":
            collectives = dataclasses.replace(collectives, **{field: value})
        else:
            parallelism = dataclasses.replace(parallelism, **{field: value})
        provenance.append(ProvenanceEntry(path, "cli", value))
    candidate = dataclasses.replace(config, tiling=tiling, collectives=collectives, parallelism=parallelism)
    raw = _config_raw(candidate)
    _validate_schema(raw)
    resolved = _from_raw(raw, arch, config.defaulted_paths)
    return EffectiveCompileConfig(resolved, arch.digest().hex(), tuple(provenance))


def _require_exact(value: object, expected: type, field: str) -> None:
    if type(value) is not expected:
        raise MeshIrError("E_CONFIG", "compile configuration field has an invalid type", field=field, type=type(value).__name__)


def _validate_compile_config_types(config: object) -> CompileConfig:
    _require_exact(config, CompileConfig, "compile")
    _require_exact(config.schema_version, str, "schema_version")
    _require_exact(config.entrypoints, tuple, "entrypoints")
    if any(type(item) is not str for item in config.entrypoints):
        raise MeshIrError("E_CONFIG", "compile entrypoint has an invalid type")
    _require_exact(config.shape_profiles, tuple, "shape_profiles")
    shape_entrypoints = []
    for entry in config.shape_profiles:
        if type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not str or type(entry[1]) is not tuple:
            raise MeshIrError("E_CONFIG", "shape profile collection has an invalid type")
        entrypoint, profiles = entry
        shape_entrypoints.append(entrypoint)
        profile_ids = []
        for profile in profiles:
            _require_exact(profile, ShapeProfile, "shape_profile")
            _require_exact(profile.profile_id, str, "shape_profile.profile_id")
            _require_exact(profile.bindings, tuple, "shape_profile.bindings")
            names = []
            for binding in profile.bindings:
                if type(binding) is not tuple or len(binding) != 2 or type(binding[0]) is not str or type(binding[1]) is not int:
                    raise MeshIrError("E_CONFIG", "shape profile binding has an invalid type")
                names.append(binding[0])
            if len(names) != len(set(names)):
                raise MeshIrError("E_CONFIG", "shape profile bindings must be unique", profile_id=profile.profile_id)
            profile_ids.append(profile.profile_id)
        if len(profile_ids) != len(set(profile_ids)):
            raise MeshIrError("E_CONFIG", "shape profile ids must be unique", entrypoint=entrypoint)
    if len(shape_entrypoints) != len(set(shape_entrypoints)):
        raise MeshIrError("E_CONFIG", "shape profile entrypoints must be unique")
    _require_exact(config.symbol_bindings, tuple, "symbol_bindings")
    symbol_entrypoints = []
    for entry in config.symbol_bindings:
        if type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not str or type(entry[1]) is not tuple:
            raise MeshIrError("E_CONFIG", "symbol binding collection has an invalid type")
        entrypoint, bindings = entry
        symbol_entrypoints.append(entrypoint)
        names = []
        for binding in bindings:
            _require_exact(binding, SymbolBinding, "symbol_binding")
            _require_exact(binding.logical_name, str, "symbol_binding.logical_name")
            _require_exact(binding.input_name, str, "symbol_binding.input_name")
            _require_exact(binding.axis, int, "symbol_binding.axis")
            names.append(binding.logical_name)
        if len(names) != len(set(names)):
            raise MeshIrError("E_CONFIG", "logical symbol bindings must be unique", entrypoint=entrypoint)
    if len(symbol_entrypoints) != len(set(symbol_entrypoints)):
        raise MeshIrError("E_CONFIG", "symbol binding entrypoints must be unique")
    _require_exact(config.parallelism, Parallelism, "parallelism")
    for field in ("tensor_parallel", "pipeline_parallel", "expert_parallel", "data_parallel"):
        _require_exact(getattr(config.parallelism, field), int, f"parallelism.{field}")
    _require_exact(config.placement, Placement, "placement")
    _require_exact(config.placement.core_order, str, "placement.core_order")
    for field in ("allowed_cores", "reserve_cores"):
        value = getattr(config.placement, field)
        _require_exact(value, tuple, f"placement.{field}")
        if any(type(item) is not int for item in value):
            raise MeshIrError("E_CONFIG", "placement core identity has an invalid type", field=field)
    _require_exact(config.tiling, Tiling, "tiling")
    for field in ("gemm_m", "gemm_n", "gemm_k"):
        _require_exact(getattr(config.tiling, field), int, f"tiling.{field}")
    _require_exact(config.tiling.double_buffer, bool, "tiling.double_buffer")
    _require_exact(config.collectives, Collectives, "collectives")
    _require_exact(config.collectives.all_reduce_algorithm, str, "collectives.all_reduce_algorithm")
    _require_exact(config.collectives.chunk_bytes, int, "collectives.chunk_bytes")
    _require_exact(config.runtime_model, RuntimeModel, "runtime_model")
    _require_exact(config.runtime_model.mode, str, "runtime_model.mode")
    _require_exact(config.runtime_model.tensor_data, str, "runtime_model.tensor_data")
    _require_exact(config.defaulted_paths, tuple, "defaulted_paths")
    if any(type(item) is not str or not item for item in config.defaulted_paths) or tuple(sorted(set(config.defaulted_paths))) != config.defaulted_paths:
        raise MeshIrError("E_CONFIG", "schema-default paths must be ordered unique strings")
    return config


def _remove_defaulted_path(value: object, path: str) -> None:
    parts = path.split(".")
    current = value
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise MeshIrError("E_CONFIG", "schema-default provenance path is invalid", path=path)
    leaf = parts[-1]
    if isinstance(current, dict) and leaf in current:
        del current[leaf]
    elif isinstance(current, list) and leaf.isdecimal() and int(leaf) < len(current):
        del current[int(leaf)]
    else:
        raise MeshIrError("E_CONFIG", "schema-default provenance path is invalid", path=path)


def _validate_workload_config(config: object, arch: ArchManifest) -> CompileConfig:
    typed = _validate_compile_config_types(config)
    expected_raw = _config_raw(typed)
    explicit_raw = copy.deepcopy(expected_raw)
    for path in reversed(typed.defaulted_paths):
        _remove_defaulted_path(explicit_raw, path)
    actual_defaults = validate_schema("mesh_compile_v1.schema.json", explicit_raw, "compile", apply_defaults=True)
    if actual_defaults != typed.defaulted_paths:
        raise MeshIrError("E_CONFIG", "schema-default provenance does not reconstruct the workload")
    reconstructed = _from_raw(explicit_raw, arch, actual_defaults)
    if reconstructed != typed:
        raise MeshIrError("E_CONFIG", "compile workload does not match canonical resolution")
    return typed


def validate_effective_compile_config(effective: EffectiveCompileConfig, arch: ArchManifest) -> None:
    _require_exact(effective, EffectiveCompileConfig, "effective")
    _require_exact(arch, ArchManifest, "architecture")
    validate_arch(arch)
    _validate_compile_config_types(effective.config)
    _require_exact(effective.arch_digest, str, "arch_digest")
    digest = arch.digest().hex()
    if effective.arch_digest != digest:
        raise MeshIrError("E_ARCH_DIGEST", "effective compile configuration architecture digest does not match", expected=digest, actual=effective.arch_digest)
    _require_exact(effective.provenance, tuple, "provenance")
    for entry in effective.provenance:
        _require_exact(entry, ProvenanceEntry, "provenance.entry")
        _require_exact(entry.path, str, "provenance.path")
        _require_exact(entry.source, str, "provenance.source")
    if len(effective.provenance) < 2:
        raise MeshIrError("E_CONFIG", "effective compile configuration provenance is incomplete")
    architecture_entry, workload_entry = effective.provenance[:2]
    if architecture_entry != ProvenanceEntry("architecture.digest", "architecture", digest):
        raise MeshIrError("E_CONFIG", "effective architecture provenance is invalid")
    if workload_entry.path != "compile" or workload_entry.source != "workload":
        raise MeshIrError("E_CONFIG", "effective workload provenance is invalid")
    workload = _validate_workload_config(workload_entry.value, arch)
    baseline = resolve_compile_config(workload, arch)
    prefix = baseline.provenance
    if effective.provenance[:len(prefix)] != prefix:
        raise MeshIrError("E_CONFIG", "effective schema-default provenance is invalid")
    cli_entries = effective.provenance[len(prefix):]
    path_to_field = dict(_CLI_PATHS)
    path_positions = {path: index for index, (path, _) in enumerate(_CLI_PATHS)}
    seen = set()
    previous = -1
    override_values: dict[str, int] = {}
    for entry in cli_entries:
        if entry.source != "cli" or entry.path not in path_to_field or entry.path in seen:
            raise MeshIrError("E_CONFIG", "effective CLI provenance is invalid", path=entry.path, source=entry.source)
        position = path_positions[entry.path]
        if position <= previous or type(entry.value) is not int:
            raise MeshIrError("E_CONFIG", "effective CLI provenance is out of order or has an invalid value", path=entry.path)
        previous = position
        seen.add(entry.path)
        override_values[path_to_field[entry.path]] = entry.value
    reconstructed = resolve_compile_config(workload, arch, CompileOverrides(**override_values))
    if reconstructed != effective:
        raise MeshIrError("E_CONFIG", "effective compile configuration does not match its provenance")


def _path_value(value: object, path: str) -> object:
    current = value
    for part in path.split("."):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current

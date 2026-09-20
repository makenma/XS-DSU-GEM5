from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.canonical import canonical_json_bytes, checked_u64, semantic_sha256, to_canonical
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.model import Program
from mesh_ir.passes.execution import PASS_REGISTRY, PassRecord, record_pass, verify_pass_chain
from mesh_ir.passes.planning_prefix import graph_set_sha256
from mesh_ir.scheduled.model import ExternalSlotBacking, LocalAllocationBacking
from mesh_ir.scheduled.verify import verify_program, verify_program_kernel_correspondence

if TYPE_CHECKING:
    from mesh_ir.compile_config import EffectiveCompileConfig
    from mesh_ir.compiler import BackendCompilationResult
    from mesh_ir.frontend import FrontendResult, SourceProvenance


_DIGEST = re.compile(r"[0-9a-f]{64}")
_GIT_DIGEST = re.compile(r"[0-9a-f]{40}")
_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)
_RENAMEAT2 = getattr(_LIBC, "renameat2", None)
if _RENAMEAT2 is not None:
    _RENAMEAT2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    _RENAMEAT2.restype = ctypes.c_int
_RESERVED_ARTIFACTS = frozenset(("manifest.json", "checksums.sha256"))
_COMPILER_ARTIFACTS = frozenset((
    "graph.mesh.json",
    "kernel.mesh.json",
    "schedule.mesh.json",
    "program.mshb",
    "effective_config.json",
    "decomposition_manifest.json",
    "memory_map.json",
    "expected_traffic.json",
    "compile_report.json",
    "diagnostics.jsonl",
))


@dataclass(frozen=True)
class Artifact:
    name: str
    data: bytes


@dataclass(frozen=True)
class VariantIdentity:
    variant_id: int
    entrypoint_id: int
    entrypoint: str
    profile_id: int
    profile: str


@dataclass(frozen=True)
class PublicationManifest:
    kind: str
    abi_major: int
    abi_minor: int
    min_reader_minor: int
    required_features: int
    abi_schema_sha256: str
    variants: tuple[VariantIdentity, ...]
    arch_digest: str
    program_semantic_sha256: str
    mshb_sha256: str
    mshb_bytes: int
    graph_semantic_sha256: str | None = None
    kernel_semantic_sha256: str | None = None
    effective_config_sha256: str | None = None
    identity: tuple[tuple[str, object], ...] = ()

    def document(self, artifacts: tuple[dict[str, object], ...]) -> dict[str, object]:
        result = {
            "schema_version": "mesh-artifact-manifest-v1",
            "status": "ok",
            "kind": self.kind,
            "abi": {
                "major": self.abi_major,
                "minor": self.abi_minor,
                "min_reader_minor": self.min_reader_minor,
                "required_features": self.required_features,
                "schema_sha256": self.abi_schema_sha256,
            },
            "variants": [to_canonical(item) for item in self.variants],
            "arch_digest": self.arch_digest,
            "program_semantic_sha256": self.program_semantic_sha256,
            "mshb_sha256": self.mshb_sha256,
            "mshb_bytes": self.mshb_bytes,
            "artifacts": list(artifacts),
        }
        if self.graph_semantic_sha256 is not None:
            result["graph_semantic_sha256"] = self.graph_semantic_sha256
        if self.kernel_semantic_sha256 is not None:
            result["kernel_semantic_sha256"] = self.kernel_semantic_sha256
        if self.effective_config_sha256 is not None:
            result["effective_config_sha256"] = self.effective_config_sha256
        if self.identity:
            result["identity"] = {name: to_canonical(value) for name, value in self.identity}
        return result


@dataclass(frozen=True)
class ArtifactPlan:
    artifacts: tuple[Artifact, ...]
    manifest: PublicationManifest
    required_names: frozenset[str]


@dataclass(frozen=True)
class PublicationResult:
    output: Path
    manifest_sha256: str
    program_semantic_sha256: str
    mshb_sha256: str
    artifacts: tuple[str, ...]

    def canonical_dict(self) -> dict[str, object]:
        return {
            "status": "ok",
            "output": str(self.output),
            "manifest_sha256": self.manifest_sha256,
            "program_semantic_sha256": self.program_semantic_sha256,
            "mshb_sha256": self.mshb_sha256,
            "artifacts": list(self.artifacts),
        }


@dataclass(frozen=True)
class CompileReport:
    sources: tuple[tuple[str, "SourceProvenance"], ...]
    member_frontend_passes: tuple[tuple[str, tuple[PassRecord, ...]], ...]
    passes: tuple[PassRecord, ...]
    execution: object
    input_documents: tuple[tuple[str, str], ...]

    def canonical_dict(self) -> dict[str, object]:
        return {
            "schema_version": "mesh-compile-report-v1",
            "sources": [
                {"entrypoint": entrypoint, "provenance": to_canonical(provenance)}
                for entrypoint, provenance in self.sources
            ],
            "member_frontend_passes": [
                {"entrypoint": entrypoint, "passes": [to_canonical(item) for item in records]}
                for entrypoint, records in self.member_frontend_passes
            ],
            "passes": [to_canonical(item) for item in self.passes],
            "execution": to_canonical(self.execution),
            "input_documents": [
                {"kind": kind, "sha256": digest}
                for kind, digest in self.input_documents
            ],
        }


@dataclass(frozen=True)
class _ProtectedInput:
    supplied: Path
    resolved: Path
    sha256: str


def _rename_noreplace(source: Path, destination: Path) -> None:
    if _RENAMEAT2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable", str(destination))
    if _RENAMEAT2(_AT_FDCWD, os.fsencode(source), _AT_FDCWD, os.fsencode(destination), _RENAME_NOREPLACE) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


class ArtifactTransaction:
    def __init__(self, output: str | Path, protected_inputs: tuple[str | Path, ...]):
        if type(protected_inputs) is not tuple:
            raise MeshIrError("E_CONFIG", "protected input paths must be an immutable tuple")
        supplied_output = Path(output)
        if supplied_output.is_symlink():
            raise MeshIrError("E_CONFIG", "publication output already exists", path=str(supplied_output))
        self.output = supplied_output.resolve(strict=False)
        self.failed_output = self.output.with_name(f"{self.output.name}.failed")
        protected = tuple(self._protect(Path(item)) for item in protected_inputs)
        if any(self.output == item.resolved or self.output in item.resolved.parents for item in protected):
            raise MeshIrError("E_CONFIG", "output directory aliases or contains a protected input", output=str(self.output))
        self._reject_success_output()
        try:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self._temporary = Path(tempfile.mkdtemp(prefix=f".{self.output.name}.", dir=self.output.parent))
            self._verify_publication_capability()
            self.working_directory = self._temporary / "work"
            self.working_directory.mkdir()
        except MeshIrError:
            self.close()
            raise
        except OSError as error:
            self.close()
            raise MeshIrError("E_CONFIG", "cannot create private publication directory", output=str(self.output), detail=str(error)) from error
        self._protected = protected
        self._published = False
        self._failure_published = False

    @staticmethod
    def _protect(path: Path) -> _ProtectedInput:
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_file():
                raise OSError("protected input is not a regular file")
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError as error:
            raise MeshIrError("E_CONFIG", "cannot protect compiler input", path=str(path), detail=str(error)) from error
        return _ProtectedInput(path, resolved, digest)

    def _reject_success_output(self) -> None:
        if self.output.exists() or self.output.is_symlink():
            raise MeshIrError("E_CONFIG", "publication output already exists", path=str(self.output))

    def _reject_failure_output(self) -> None:
        if self.failed_output.exists() or self.failed_output.is_symlink():
            raise MeshIrError("E_CONFIG", "publication failure output already exists", path=str(self.failed_output))

    def _verify_publication_capability(self) -> None:
        source = self._temporary / "rename-probe-source"
        destination = self._temporary / "rename-probe-destination"
        unsupported = frozenset((errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP))
        try:
            source.mkdir()
            destination.mkdir()
            try:
                _rename_noreplace(source, destination)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    if error.errno in unsupported:
                        raise MeshIrError(
                            "E_CONFIG",
                            "output filesystem does not support atomic no-replace directory publication",
                            output=str(self.output),
                            detail=str(error),
                        ) from error
                    raise MeshIrError(
                        "E_CONFIG",
                        "cannot verify atomic publication support on output filesystem",
                        output=str(self.output),
                        detail=str(error),
                    ) from error
            else:
                raise MeshIrError(
                    "E_CONFIG",
                    "output filesystem does not preserve no-replace directory publication",
                    output=str(self.output),
                )
            destination.rmdir()
            try:
                _rename_noreplace(source, destination)
            except OSError as error:
                if error.errno in unsupported:
                    raise MeshIrError(
                        "E_CONFIG",
                        "output filesystem does not support atomic no-replace directory publication",
                        output=str(self.output),
                        detail=str(error),
                    ) from error
                raise MeshIrError(
                    "E_CONFIG",
                    "cannot verify atomic publication support on output filesystem",
                    output=str(self.output),
                    detail=str(error),
                ) from error
            if source.exists() or not destination.is_dir():
                raise MeshIrError(
                    "E_CONFIG",
                    "output filesystem does not preserve atomic directory publication",
                    output=str(self.output),
                )
        except MeshIrError:
            raise
        except OSError as error:
            raise MeshIrError(
                "E_CONFIG",
                "cannot verify atomic publication support on output filesystem",
                output=str(self.output),
                detail=str(error),
            ) from error
        finally:
            shutil.rmtree(source, ignore_errors=True)
            shutil.rmtree(destination, ignore_errors=True)

    def _verify_protected(self) -> None:
        for item in self._protected:
            try:
                resolved = item.supplied.resolve(strict=True)
                digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except OSError as error:
                raise MeshIrError("E_CONFIG", "protected compiler input became unreadable", path=str(item.supplied), detail=str(error)) from error
            if resolved != item.resolved or digest != item.sha256:
                raise MeshIrError("E_CONFIG", "protected compiler input changed during compilation", path=str(item.supplied))

    @staticmethod
    def _validate_artifact(artifact: Artifact) -> None:
        if type(artifact) is not Artifact or type(artifact.name) is not str or type(artifact.data) is not bytes:
            raise MeshIrError("E_CONFIG", "artifact plan record has an invalid type")
        path = PurePosixPath(artifact.name)
        if (
            path.is_absolute()
            or len(path.parts) != 1
            or path.name != artifact.name
            or _ARTIFACT_NAME.fullmatch(artifact.name) is None
            or artifact.name in _RESERVED_ARTIFACTS
        ):
            raise MeshIrError("E_CONFIG", "artifact plan contains an invalid path", path=artifact.name)

    @classmethod
    def _validate_plan(cls, plan: ArtifactPlan) -> None:
        if type(plan) is not ArtifactPlan or type(plan.manifest) is not PublicationManifest or type(plan.artifacts) is not tuple or type(plan.required_names) is not frozenset:
            raise MeshIrError("E_CONFIG", "artifact plan has an invalid type")
        for artifact in plan.artifacts:
            cls._validate_artifact(artifact)
        names = tuple(item.name for item in plan.artifacts)
        if len(names) != len(set(names)) or frozenset(names) != plan.required_names:
            raise MeshIrError("E_CONFIG", "artifact plan membership is incomplete or duplicated")
        if tuple(sorted(names)) != names:
            raise MeshIrError("E_CONFIG", "artifact plan paths are not canonical")
        manifest = plan.manifest
        if type(manifest.kind) is not str or not manifest.kind:
            raise MeshIrError("E_CONFIG", "publication kind is invalid")
        if any(
            type(value) is not int or not 0 <= value <= 0xFFFF
            for value in (manifest.abi_major, manifest.abi_minor, manifest.min_reader_minor)
        ):
            raise MeshIrError("E_ABI_VERSION", "publication ABI version is invalid")
        if manifest.min_reader_minor > manifest.abi_minor:
            raise MeshIrError("E_ABI_VERSION", "publication minimum reader minor exceeds ABI minor")
        checked_u64(manifest.required_features, "required_features")
        checked_u64(manifest.mshb_bytes, "mshb_bytes")
        for field in ("abi_schema_sha256", "arch_digest", "program_semantic_sha256", "mshb_sha256"):
            _digest(getattr(manifest, field), field)
        for field in ("graph_semantic_sha256", "kernel_semantic_sha256", "effective_config_sha256"):
            value = getattr(manifest, field)
            if value is not None:
                _digest(value, field)
        if type(manifest.variants) is not tuple or any(type(item) is not VariantIdentity for item in manifest.variants):
            raise MeshIrError("E_CONFIG", "publication variant identities have invalid types")
        variant_ids = []
        for variant in manifest.variants:
            for field in ("variant_id", "entrypoint_id", "profile_id"):
                value = getattr(variant, field)
                checked_u64(value, field)
                if value == 0:
                    raise MeshIrError("E_ABI_BOUNDS", "publication variant identity is zero", field=field)
            if type(variant.entrypoint) is not str or not variant.entrypoint or type(variant.profile) is not str or not variant.profile:
                raise MeshIrError("E_CONFIG", "publication variant name is invalid", variant_id=variant.variant_id)
            variant_ids.append(variant.variant_id)
        if len(variant_ids) != len(set(variant_ids)):
            raise MeshIrError("E_CONFIG", "publication variant identities are duplicated")
        if type(manifest.identity) is not tuple or any(type(item) is not tuple or len(item) != 2 for item in manifest.identity):
            raise MeshIrError("E_CONFIG", "publication identity fields have invalid types")
        identity_names = tuple(name for name, _ in manifest.identity)
        if any(type(name) is not str or not name for name in identity_names) or len(identity_names) != len(set(identity_names)):
            raise MeshIrError("E_CONFIG", "publication identity fields are invalid")
        images = tuple(item for item in plan.artifacts if item.name == "program.mshb")
        if (
            len(images) != 1
            or len(images[0].data) != manifest.mshb_bytes
            or hashlib.sha256(images[0].data).hexdigest() != manifest.mshb_sha256
        ):
            raise MeshIrError("E_ABI_CHECKSUM", "publication manifest does not identify its Program image")
        canonical_json_bytes(manifest.document(()))

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        try:
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise MeshIrError("E_CONFIG", "cannot write publication artifact", path=path.name, detail=str(error)) from error

    @staticmethod
    def _sync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise MeshIrError("E_CONFIG", "cannot synchronize publication directory", path=str(path), detail=str(error)) from error

    def publish(self, plan: ArtifactPlan) -> PublicationResult:
        if self._published:
            raise MeshIrError("E_CONFIG", "artifact transaction has already published")
        self._validate_plan(plan)
        self._verify_protected()
        publication = self._temporary / "publication"
        try:
            publication.mkdir()
        except OSError as error:
            raise MeshIrError("E_CONFIG", "cannot create staged artifact directory", detail=str(error)) from error
        for artifact in plan.artifacts:
            self._write(publication / artifact.name, artifact.data)
        rows = tuple(
            {
                "path": artifact.name,
                "sha256": hashlib.sha256(artifact.data).hexdigest(),
                "bytes": len(artifact.data),
            }
            for artifact in plan.artifacts
        )
        manifest_bytes = canonical_json_bytes(plan.manifest.document(rows))
        self._write(publication / "manifest.json", manifest_bytes)
        checksummed = tuple((*plan.artifacts, Artifact("manifest.json", manifest_bytes)))
        checksums = "".join(
            f"{hashlib.sha256(item.data).hexdigest()}  {item.name}\n"
            for item in sorted(checksummed, key=lambda item: item.name)
        ).encode("utf-8")
        self._write(publication / "checksums.sha256", checksums)
        expected_names = plan.required_names | _RESERVED_ARTIFACTS
        try:
            actual_names = frozenset(item.name for item in publication.iterdir())
            matches_plan = all((publication / item.name).read_bytes() == item.data for item in checksummed)
            matches_checksums = (publication / "checksums.sha256").read_bytes() == checksums
        except OSError as error:
            raise MeshIrError("E_CONFIG", "cannot validate staged publication artifacts", detail=str(error)) from error
        if actual_names != expected_names or not matches_plan or not matches_checksums:
            raise MeshIrError("E_ABI_CHECKSUM", "staged artifact set differs from its immutable plan")
        self._sync_directory(publication)
        self._verify_protected()
        self._reject_success_output()
        self._sync_directory(self.output.parent)
        result = PublicationResult(
            self.output,
            hashlib.sha256(manifest_bytes).hexdigest(),
            plan.manifest.program_semantic_sha256,
            plan.manifest.mshb_sha256,
            tuple(sorted(expected_names)),
        )
        try:
            _rename_noreplace(publication, self.output)
        except OSError as error:
            raise MeshIrError("E_CONFIG", "cannot atomically publish artifact directory", output=str(self.output), detail=str(error)) from error
        self._published = True
        return result

    def publish_failure(self, error: MeshIrError) -> Path:
        if type(error) is not MeshIrError:
            raise MeshIrError("E_CONFIG", "failure publication requires a catalog diagnostic")
        if self._published:
            raise MeshIrError("E_CONFIG", "successful artifact transaction cannot publish failure evidence")
        if self._failure_published:
            raise MeshIrError("E_CONFIG", "artifact transaction has already published failure evidence")
        self._verify_protected()
        self._reject_success_output()
        self._reject_failure_output()
        failure = self._temporary / "failure"
        try:
            failure.mkdir()
        except OSError as io_error:
            raise MeshIrError("E_CONFIG", "cannot create failed artifact directory", detail=str(io_error)) from io_error
        diagnostic = (error.to_jsonl() + "\n").encode("utf-8")
        manifest = canonical_json_bytes({
            "schema_version": "mesh-artifact-manifest-v1",
            "status": "failed",
            "diagnostic": error.as_dict(),
        })
        self._write(failure / "diagnostics.jsonl", diagnostic)
        self._write(failure / "manifest.json", manifest)
        self._sync_directory(failure)
        self._verify_protected()
        self._reject_success_output()
        self._reject_failure_output()
        self._sync_directory(self.failed_output.parent)
        try:
            _rename_noreplace(failure, self.failed_output)
        except OSError as io_error:
            raise MeshIrError("E_CONFIG", "cannot atomically publish failure evidence", output=str(self.failed_output), detail=str(io_error)) from io_error
        self._failure_published = True
        return self.failed_output

    def close(self) -> None:
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)

    def __enter__(self) -> "ArtifactTransaction":
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        try:
            if isinstance(exception, MeshIrError) and not self._published and not self._failure_published:
                try:
                    self.publish_failure(exception)
                except MeshIrError:
                    pass
        finally:
            self.close()


def _digest(value: str, field: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise MeshIrError("E_ABI_CHECKSUM", "publication identity digest is malformed", field=field)


def _variant_identities(program: Program) -> tuple[VariantIdentity, ...]:
    strings = tuple(item.value for item in program.strings)

    def name(string_id: int) -> str:
        if type(string_id) is not int or not 1 <= string_id <= len(strings):
            raise MeshIrError("E_ABI_BOUNDS", "artifact identity references an invalid string", string_id=string_id)
        return strings[string_id - 1]

    entrypoints = {item.entrypoint_id: name(item.name_sid) for item in program.entrypoints}
    profiles = {item.profile_id: (item.entrypoint_id, name(item.name_sid)) for item in program.profiles}
    result = []
    for variant in program.semantics.variants:
        if variant.entrypoint_id not in entrypoints or variant.profile_id not in profiles or profiles[variant.profile_id][0] != variant.entrypoint_id:
            raise MeshIrError("E_ABI_BOUNDS", "program variant identity is invalid", variant_id=variant.variant_id)
        result.append(VariantIdentity(
            variant.variant_id,
            variant.entrypoint_id,
            entrypoints[variant.entrypoint_id],
            variant.profile_id,
            profiles[variant.profile_id][1],
        ))
    return tuple(result)


def _verified_program_artifacts(program: Program, arch: ArchManifest) -> tuple[bytes, tuple[Artifact, ...], int]:
    from mesh_ir.abi.decoder import decode_program
    from mesh_ir.abi.encoder import encode_program

    if type(program) is not Program:
        raise MeshIrError("E_ABI_BOUNDS", "publication requires a complete typed Program")
    validate_arch(arch)
    verify_program(program, arch)
    started = time.perf_counter_ns()
    blob = encode_program(program)
    encoding_elapsed_ns = time.perf_counter_ns() - started
    decoded = decode_program(blob)
    verify_program(decoded, arch)
    if decoded.canonical_bytes() != program.canonical_bytes():
        raise MeshIrError("E_ABI_CHECKSUM", "binary round trip changed the canonical Program")
    return blob, (
        Artifact("diagnostics.jsonl", b""),
        Artifact("expected_traffic.json", canonical_json_bytes(program.semantics.intrinsic_traffic.canonical_dict())),
        Artifact("program.mshb", blob),
        Artifact("schedule.mesh.json", program.canonical_bytes()),
    ), encoding_elapsed_ns


def _program_manifest(
    program: Program,
    arch: ArchManifest,
    blob: bytes,
    *,
    kind: str,
    identity: tuple[tuple[str, object], ...] = (),
    graph_semantic_sha256: str | None = None,
    kernel_semantic_sha256: str | None = None,
    effective_config_sha256: str | None = None,
) -> PublicationManifest:
    if type(kind) is not str or not kind:
        raise MeshIrError("E_CONFIG", "publication kind is invalid")
    _digest(program.semantic_sha256, "program_semantic_sha256")
    return PublicationManifest(
        kind,
        program.abi_major,
        program.abi_minor,
        program.min_reader_minor,
        program.required_features,
        A.SCHEMA_SHA256,
        _variant_identities(program),
        arch.digest().hex(),
        program.semantic_sha256,
        hashlib.sha256(blob).hexdigest(),
        len(blob),
        graph_semantic_sha256,
        kernel_semantic_sha256,
        effective_config_sha256,
        identity,
    )


def publish_authored_program(
    output: str | Path,
    program: Program,
    arch: ArchManifest,
    *,
    kind: str,
    identity: tuple[tuple[str, object], ...] = (),
    extra_documents: tuple[tuple[str, object], ...] = (),
    protected_inputs: tuple[str | Path, ...] = (),
) -> PublicationResult:
    with ArtifactTransaction(output, protected_inputs) as transaction:
        blob, common, _ = _verified_program_artifacts(program, arch)
        extras = tuple(Artifact(name, canonical_json_bytes(document)) for name, document in extra_documents)
        artifacts = tuple(sorted((*common, *extras), key=lambda item: item.name))
        plan = ArtifactPlan(
            artifacts,
            _program_manifest(program, arch, blob, kind=kind, identity=identity),
            frozenset(item.name for item in artifacts),
        )
        return transaction.publish(plan)


def _aggregate_frontend_passes(frontends: tuple["FrontendResult", ...], final_graph_hash: str) -> tuple[PassRecord, ...]:
    names = tuple(item.name for item in PASS_REGISTRY[:5])
    for frontend in frontends:
        verify_pass_chain(
            frontend.passes,
            frontend.provenance.archive_semantic_sha256,
            graph_set_sha256(frontend.variants),
            names,
        )
    if len(frontends) == 1:
        return frontends[0].passes
    current = semantic_sha256([
        (frontend.graph.entrypoint, frontend.provenance.archive_semantic_sha256)
        for frontend in frontends
    ])
    records = []
    for index, name in enumerate(names):
        member_records = tuple(frontend.passes[index] for frontend in frontends)
        output = final_graph_hash if index == len(names) - 1 else semantic_sha256([
            (frontend.graph.entrypoint, item.output_hash)
            for frontend, item in zip(frontends, member_records)
        ])
        statistics = {}
        for record in member_records:
            for key, value in record.statistics:
                statistics[key] = statistics.get(key, 0) + value
        records.append(record_pass(
            name,
            current,
            output,
            sum(item.elapsed_ns for item in member_records),
            tuple(statistics.items()),
        ))
        current = output
    return tuple(records)


def _validate_compiler_inputs(
    frontends: tuple["FrontendResult", ...],
    backend: "BackendCompilationResult",
    arch: ArchManifest,
    effective: "EffectiveCompileConfig",
) -> None:
    from mesh_ir.compile_config import EffectiveCompileConfig, validate_effective_compile_config
    from mesh_ir.compiler import BackendCompilationResult
    from mesh_ir.frontend import FrontendResult, SourceProvenance

    if type(frontends) is not tuple or not frontends or any(type(item) is not FrontendResult for item in frontends):
        raise MeshIrError("E_CONFIG", "compiler publication requires immutable frontend results")
    if type(backend) is not BackendCompilationResult or type(effective) is not EffectiveCompileConfig:
        raise MeshIrError("E_CONFIG", "compiler publication result types are invalid")
    validate_arch(arch)
    validate_effective_compile_config(effective, arch)
    expected_entrypoints = effective.config.entrypoints
    actual_entrypoints = tuple(item.graph.entrypoint for item in frontends)
    if actual_entrypoints != expected_entrypoints or len(set(actual_entrypoints)) != len(actual_entrypoints):
        raise MeshIrError("E_CONFIG", "frontend entrypoints differ from effective configuration")
    for frontend in frontends:
        if type(frontend.provenance) is not SourceProvenance:
            raise MeshIrError("E_CONFIG", "frontend provenance type is invalid", entrypoint=frontend.graph.entrypoint)
        for field in ("archive_sha256", "archive_semantic_sha256", "compiler_package_sha256", "requirements_lock_sha256"):
            _digest(getattr(frontend.provenance, field), field)
        if type(frontend.provenance.compiler_git_sha) is not str or _GIT_DIGEST.fullmatch(frontend.provenance.compiler_git_sha) is None:
            raise MeshIrError("E_ABI_CHECKSUM", "compiler Git identity digest is malformed")
        if frontend.provenance.source_sha256 is not None:
            _digest(frontend.provenance.source_sha256, "source_sha256")
        expected_profiles = tuple(profile.profile_id for profile in effective.config.profiles_for(frontend.graph.entrypoint))
        actual_profiles = tuple(item.profile_id for item in frontend.variants)
        if actual_profiles != expected_profiles or any(item.entrypoint != frontend.graph.entrypoint for item in frontend.variants):
            raise MeshIrError("E_CONFIG", "frontend variants differ from effective configuration", entrypoint=frontend.graph.entrypoint)
    source_graphs = tuple(item.graph for item in frontends)
    variants = tuple(variant for item in frontends for variant in item.variants)
    if backend.source_graphs != source_graphs or backend.variants != variants:
        raise MeshIrError("E_ABI_CHECKSUM", "backend graph membership differs from frontend results")
    backend.bundle.verify()
    verify_program(backend.program, arch)
    verify_program_kernel_correspondence(backend.program, backend.bundle)


def _graph_set_document(frontends: tuple["FrontendResult", ...]) -> tuple[dict[str, object], str]:
    source_graphs = tuple(item.graph for item in frontends)
    variants = tuple(variant for item in frontends for variant in item.variants)
    payload = {
        "schema_version": "mesh-graph-set-v1",
        "source_graphs": [item.canonical_dict() for item in source_graphs],
        "variants": [item.canonical_dict() for item in variants],
    }
    identity = semantic_sha256(payload)
    return {**payload, "semantic_sha256": identity}, identity


def _memory_map_document(program: Program) -> dict[str, object]:
    backings = []
    for item in program.semantics.object_backings:
        backing = item.backing
        if type(backing) is LocalAllocationBacking:
            value = {"kind": "LOCAL_ALLOCATION", "allocation_id": backing.allocation_id}
        elif type(backing) is ExternalSlotBacking:
            value = {"kind": "EXTERNAL_SLOT", "slot_id": backing.slot_id}
        else:
            raise MeshIrError("E_ABI_BOUNDS", "memory map contains an unknown backing type", object_id=item.object_id)
        backings.append({"object_id": item.object_id, "backing": value})
    return {
        "schema_version": "mesh-memory-map-v1",
        "program_semantic_sha256": program.semantic_sha256,
        "variants": [
            {
                "variant_id": item.variant_id,
                "entrypoint_id": item.entrypoint_id,
                "profile_id": item.profile_id,
                "membership": to_canonical(item.membership),
            }
            for item in program.semantics.variants
        ],
        "allocations": [to_canonical(item) for item in program.allocations],
        "object_backings": backings,
        "resident_views": [to_canonical(item) for item in program.semantics.resident_views],
    }


def publish_compilation(
    transaction: ArtifactTransaction,
    frontends: tuple["FrontendResult", ...],
    backend: "BackendCompilationResult",
    arch: ArchManifest,
    effective: "EffectiveCompileConfig",
    *,
    exported_program_bytes: bytes | None = None,
    input_documents: tuple[tuple[str, str], ...] = (),
) -> PublicationResult:
    _validate_compiler_inputs(frontends, backend, arch, effective)
    if exported_program_bytes is not None and type(exported_program_bytes) is not bytes:
        raise MeshIrError("E_CONFIG", "exported program artifact must be immutable bytes")
    if type(input_documents) is not tuple or any(type(item) is not tuple or len(item) != 2 for item in input_documents):
        raise MeshIrError("E_CONFIG", "input document identities are invalid")
    for kind, digest in input_documents:
        if type(kind) is not str or not kind:
            raise MeshIrError("E_CONFIG", "input document identity kind is invalid")
        _digest(digest, kind)
    if len({kind for kind, _ in input_documents}) != len(input_documents):
        raise MeshIrError("E_CONFIG", "input document identity kinds are duplicated")
    graph_document, graph_identity = _graph_set_document(frontends)
    frontend_passes = _aggregate_frontend_passes(frontends, graph_set_sha256(backend.variants))
    pre_encode_passes = frontend_passes + backend.passes
    verify_pass_chain(
        pre_encode_passes,
        frontend_passes[0].input_hash,
        backend.program.semantic_sha256,
        tuple(item.name for item in PASS_REGISTRY[:-1]),
    )
    blob, common, encoding_elapsed_ns = _verified_program_artifacts(backend.program, arch)
    effective_bytes = effective.canonical_bytes()
    artifacts = [
        *common,
        Artifact("decomposition_manifest.json", canonical_json_bytes({
            "schema_version": "mesh-decomposition-set-v1",
            "entries": [
                {"entrypoint": item.graph.entrypoint, "manifest": to_canonical(item.decomposition_manifest)}
                for item in frontends
            ],
        })),
        Artifact("effective_config.json", effective_bytes),
        Artifact("graph.mesh.json", canonical_json_bytes(graph_document)),
        Artifact("kernel.mesh.json", backend.bundle.canonical_bytes()),
        Artifact("memory_map.json", canonical_json_bytes(_memory_map_document(backend.program))),
    ]
    if exported_program_bytes is not None:
        artifacts.append(Artifact("exported_program.pt2", exported_program_bytes))
    artifact_count = len(_COMPILER_ARTIFACTS | _RESERVED_ARTIFACTS) + (1 if exported_program_bytes is not None else 0)
    p22 = record_pass(
        "EncodeArtifacts",
        backend.program.semantic_sha256,
        hashlib.sha256(blob).hexdigest(),
        encoding_elapsed_ns,
        (("artifact_count", artifact_count), ("image_bytes", len(blob))),
    )
    passes = pre_encode_passes + (p22,)
    verify_pass_chain(
        passes,
        frontend_passes[0].input_hash,
        p22.output_hash,
        tuple(item.name for item in PASS_REGISTRY),
    )
    report = CompileReport(
        tuple((item.graph.entrypoint, item.provenance) for item in frontends),
        tuple((item.graph.entrypoint, item.passes) for item in frontends),
        passes,
        backend.execution,
        input_documents,
    )
    artifacts.append(Artifact("compile_report.json", canonical_json_bytes(report.canonical_dict())))
    artifacts = sorted(artifacts, key=lambda item: item.name)
    required = _COMPILER_ARTIFACTS | ({"exported_program.pt2"} if exported_program_bytes is not None else set())
    plan = ArtifactPlan(
        tuple(artifacts),
        _program_manifest(
            backend.program,
            arch,
            blob,
            kind="compiler",
            graph_semantic_sha256=graph_identity,
            kernel_semantic_sha256=backend.bundle.semantic_sha256,
            effective_config_sha256=hashlib.sha256(effective_bytes).hexdigest(),
        ),
        frozenset(required),
    )
    return transaction.publish(plan)


__all__ = [
    "Artifact",
    "ArtifactPlan",
    "ArtifactTransaction",
    "CompileReport",
    "PublicationManifest",
    "PublicationResult",
    "VariantIdentity",
    "publish_authored_program",
    "publish_compilation",
]

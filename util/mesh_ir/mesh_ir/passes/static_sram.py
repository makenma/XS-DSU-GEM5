from __future__ import annotations

import re
from dataclasses import dataclass

from mesh_ir.analysis.lifetime import LifetimeConflict
from mesh_ir.analysis.sram import SramAllocation, SramCoreReport, SramPlan, plan_static_sram
from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import KernelBundle, KernelMemoryRecords
from mesh_ir.ir.kernel_verify import verify_scheduled_ready_kernel
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AuthoredVariantLineage, CompiledProgramOrigin, CompiledVariantLineage, ControlCommandSource, KernelCommandSource, ObjectBacking, ProgramOrigin
from mesh_ir.traffic import BindingSlot


_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class _CompiledVariantSource:
    entrypoint: str
    profile_id: str
    lineage: CompiledVariantLineage
    records: KernelMemoryRecords


@dataclass(frozen=True)
class _AuthoredStreamPlan:
    core_id: int
    physical_stream_id: int
    flags: int
    sources: tuple[KernelCommandSource | ControlCommandSource, ...]


@dataclass(frozen=True)
class _AuthoredVariantSource:
    entrypoint: str
    profile_id: str
    lineage: AuthoredVariantLineage
    records: KernelMemoryRecords
    streams: tuple[_AuthoredStreamPlan, ...]
    external_backings: tuple[ObjectBacking, ...]
    binding_slots: tuple[BindingSlot, ...]


@dataclass(frozen=True)
class _VariantSramPlan:
    source: _CompiledVariantSource | _AuthoredVariantSource
    plan: SramPlan


@dataclass(frozen=True)
class _StaticSramState:
    origin: ProgramOrigin
    variants: tuple[_VariantSramPlan, ...]

    @property
    def semantic_sha256(self) -> str:
        return semantic_sha256(self)


def verify_static_sram_state(state: _StaticSramState) -> None:
    if type(state) is not _StaticSramState or type(state.variants) is not tuple or not state.variants or any(type(item) is not _VariantSramPlan or type(item.plan) is not SramPlan for item in state.variants):
        raise MeshIrError("E_ABI_BOUNDS", "static SRAM state is not an exact nonempty variant tuple")
    pairs = []
    authored_variant_ids = []
    for ordinal, variant in enumerate(state.variants):
        source = variant.source
        if type(source) not in (_CompiledVariantSource, _AuthoredVariantSource) or type(source.entrypoint) is not str or not source.entrypoint or type(source.profile_id) is not str or not source.profile_id or type(source.records) is not KernelMemoryRecords:
            raise MeshIrError("E_ABI_BOUNDS", "static SRAM variant source is invalid", variant_ordinal=ordinal)
        if type(variant.plan.allocations) is not tuple or any(type(item) is not SramAllocation for item in variant.plan.allocations) or type(variant.plan.reports) is not tuple or any(type(item) is not SramCoreReport for item in variant.plan.reports) or type(variant.plan.conflict_witnesses) is not tuple or any(type(item) is not LifetimeConflict for item in variant.plan.conflict_witnesses):
            raise MeshIrError("E_ABI_BOUNDS", "static SRAM plan collections are invalid", variant_ordinal=ordinal)
        if type(state.origin) is CompiledProgramOrigin:
            lineage = source.lineage
            if type(source) is not _CompiledVariantSource or type(lineage) is not CompiledVariantLineage or type(lineage.kernel_module_ordinal) is not int or lineage.kernel_module_ordinal != ordinal or type(lineage.kernel_module_semantic_sha256) is not str or _DIGEST.fullmatch(lineage.kernel_module_semantic_sha256) is None:
                raise MeshIrError("E_ABI_CHECKSUM", "compiled static SRAM lineage is invalid", variant_ordinal=ordinal)
        elif type(state.origin) is AuthoredProgramOrigin:
            if type(state.origin.namespace) is not str or not state.origin.namespace or type(state.origin.name) is not str or not state.origin.name or type(state.origin.version) is not int or state.origin.version < 1 or type(source) is not _AuthoredVariantSource or type(source.lineage) is not AuthoredVariantLineage or type(source.lineage.authoring_variant_id) is not str or not source.lineage.authoring_variant_id:
                raise MeshIrError("E_ABI_BOUNDS", "authored static SRAM lineage is invalid", variant_ordinal=ordinal)
            authored_variant_ids.append(source.lineage.authoring_variant_id)
            if type(source.streams) is not tuple or any(type(item) is not _AuthoredStreamPlan for item in source.streams) or type(source.external_backings) is not tuple or any(type(item) is not ObjectBacking for item in source.external_backings) or type(source.binding_slots) is not tuple or any(type(item) is not BindingSlot for item in source.binding_slots):
                raise MeshIrError("E_ABI_BOUNDS", "authored static SRAM source collections are invalid", variant_ordinal=ordinal)
            stream_keys = []
            for stream in source.streams:
                if type(stream.core_id) is not int or type(stream.physical_stream_id) is not int or type(stream.flags) is not int or not 0 <= stream.physical_stream_id <= 0xFFFFFFFF or stream.flags < 0 or type(stream.sources) is not tuple or not stream.sources or any(type(item) not in (KernelCommandSource, ControlCommandSource) for item in stream.sources):
                    raise MeshIrError("E_STREAM_CONTRACT", "authored stream record is invalid", variant_ordinal=ordinal)
                for item in stream.sources:
                    if type(item) is KernelCommandSource and (type(item.kernel_op_id) is not int or item.kernel_op_id < 1):
                        raise MeshIrError("E_STREAM_CONTRACT", "authored Kernel command identity is invalid", variant_ordinal=ordinal)
                stream_keys.append((stream.core_id, stream.physical_stream_id))
            if len(stream_keys) != len(set(stream_keys)):
                raise MeshIrError("E_ABI_DUPLICATE", "authored physical stream is repeated", variant_ordinal=ordinal)
        else:
            raise MeshIrError("E_ABI_BOUNDS", "static SRAM origin is invalid")
        pairs.append((source.entrypoint, source.profile_id))
    if len(pairs) != len(set(pairs)):
        raise MeshIrError("E_ABI_DUPLICATE", "static SRAM variants have duplicate selection identities")
    if len(authored_variant_ids) != len(set(authored_variant_ids)):
        raise MeshIrError("E_ABI_DUPLICATE", "authored static SRAM variants have duplicate lineage identities")


def plan_static_sram_stage(bundle: KernelBundle, arch: ArchManifest) -> _StaticSramState:
    if type(bundle) is not KernelBundle:
        raise MeshIrError("E_ABI_BOUNDS", "pass 15 requires an exact Kernel bundle")
    validate_arch(arch)
    bundle.verify()
    variants = []
    for ordinal, module in enumerate(bundle.modules):
        verify_scheduled_ready_kernel(module, arch)
        source = _CompiledVariantSource(module.entrypoint, module.profile_id, CompiledVariantLineage(ordinal, module.semantic_sha256), module.memory_records())
        variants.append(_VariantSramPlan(source, plan_static_sram(module, arch)))
    state = _StaticSramState(CompiledProgramOrigin(bundle.semantic_sha256), tuple(variants))
    verify_static_sram_state(state)
    return state

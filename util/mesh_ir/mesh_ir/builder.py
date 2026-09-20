from __future__ import annotations

from dataclasses import dataclass, field

from mesh_ir.analysis.sram import SramAllocation, SramPlan, plan_memory_sram
from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import KernelMemoryRecords, KernelOpcode
from mesh_ir.model import Program
from mesh_ir.passes.static_sram import _AuthoredStreamPlan, _AuthoredVariantSource, _StaticSramState, _VariantSramPlan
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AuthoredVariantLineage, AxiFenceAttrs, ControlCommandAttrs, ControlCommandSource, EventSignalAttrs, EventWaitAttrs, FenceScope, HaltAttrs, KernelCommandSource, ObjectBacking, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.traffic import BindingSlot


_CONTROL_ATTR_TYPES = (RequestBeginAttrs, RequestEndAttrs, HaltAttrs, EventWaitAttrs, EventSignalAttrs, RepeatCommandAttrs, AxiFenceAttrs)
_DECLARATION_OPCODES = (KernelOpcode.ALLOC, KernelOpcode.VIEW)


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value:
        raise MeshIrError("E_ABI_BOUNDS", "authored identity must be a nonempty string", field=field_name)
    return value


def _exact_nonnegative_int(value: object, field_name: str, maximum: int = (1 << 32) - 1) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise MeshIrError("E_ABI_BOUNDS", "authored integer field is out of range", field=field_name, value=value)
    return value


def _validate_control_attrs(attrs: ControlCommandAttrs) -> None:
    if type(attrs) not in _CONTROL_ATTR_TYPES:
        raise MeshIrError("E_ABI_BOUNDS", "authored control command has an invalid attribute type")
    if type(attrs) in (EventWaitAttrs, EventSignalAttrs):
        if type(attrs.event_id) is not int or attrs.event_id < 1:
            raise MeshIrError("E_ABI_BOUNDS", "authored event identity must be positive")
    elif type(attrs) is RepeatCommandAttrs:
        _exact_nonnegative_int(attrs.subrange_begin_stream_ordinal, "subrange_begin_stream_ordinal")
        if type(attrs.subrange_command_count) is not int or not 1 <= attrs.subrange_command_count <= (1 << 32) - 1:
            raise MeshIrError("E_ABI_BOUNDS", "authored repeat range must be nonempty")
        if type(attrs.repeat_count) is not int or not 1 <= attrs.repeat_count <= (1 << 32) - 1:
            raise MeshIrError("E_ABI_BOUNDS", "authored repeat count must be positive")
    elif type(attrs) is AxiFenceAttrs and type(attrs.scope) is not FenceScope:
        raise MeshIrError("E_ABI_BOUNDS", "authored fence scope is invalid")


@dataclass
class StreamScope:
    _variant: "VariantScope"
    core_id: int
    physical_stream_id: int
    flags: int
    _sources: list[KernelCommandSource | ControlCommandSource] = field(default_factory=list)

    def kernel_command(self, kernel_op_id: int) -> None:
        if type(kernel_op_id) is not int or kernel_op_id < 1:
            raise MeshIrError("E_ABI_BOUNDS", "authored Kernel operation identity must be positive", kernel_op_id=kernel_op_id)
        op = self._variant._op_by_id.get(kernel_op_id)
        if op is None or op.opcode in _DECLARATION_OPCODES:
            raise MeshIrError("E_ABI_BOUNDS", "authored stream references an unknown or declarative Kernel operation", kernel_op_id=kernel_op_id)
        if op.owner_core != self.core_id:
            raise MeshIrError("E_ABI_BOUNDS", "authored Kernel operation is assigned to the wrong owner stream", kernel_op_id=kernel_op_id, expected_owner=op.owner_core, actual_owner=self.core_id)
        if kernel_op_id in self._variant._assigned_effects:
            raise MeshIrError("E_ABI_DUPLICATE", "authored Kernel operation appears more than once", kernel_op_id=kernel_op_id)
        self._variant._assigned_effects.add(kernel_op_id)
        self._sources.append(KernelCommandSource(kernel_op_id))

    def control_command(self, attrs: ControlCommandAttrs) -> None:
        _validate_control_attrs(attrs)
        self._sources.append(ControlCommandSource(attrs))

    def _freeze(self) -> _AuthoredStreamPlan:
        if not self._sources:
            raise MeshIrError("E_STREAM_CONTRACT", "authored stream must contain at least one command", core_id=self.core_id, physical_stream_id=self.physical_stream_id)
        return _AuthoredStreamPlan(self.core_id, self.physical_stream_id, self.flags, tuple(self._sources))


@dataclass
class VariantScope:
    _builder: "ProgramBuilder"
    entrypoint: str
    profile_id: str
    authoring_variant_id: str
    records: KernelMemoryRecords
    plan: SramPlan
    external_backings: tuple[ObjectBacking, ...]
    binding_slots: tuple[BindingSlot, ...]
    _streams: list[StreamScope] = field(default_factory=list)
    _stream_keys: set[tuple[int, int]] = field(default_factory=set)
    _assigned_effects: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._op_by_id = {item.op_id: item for item in self.records.ops}

    def stream(self, core_id: int, physical_stream_id: int, flags: int = 0) -> StreamScope:
        _exact_nonnegative_int(core_id, "core_id", (1 << 16) - 2)
        _exact_nonnegative_int(physical_stream_id, "physical_stream_id", (1 << 16) - 1)
        _exact_nonnegative_int(flags, "flags")
        if core_id not in self._builder.arch.core_ids:
            raise MeshIrError("E_ABI_BOUNDS", "authored stream owner is not an architecture core", core_id=core_id)
        key = (core_id, physical_stream_id)
        if key in self._stream_keys:
            raise MeshIrError("E_ABI_DUPLICATE", "authored physical stream is repeated", core_id=core_id, physical_stream_id=physical_stream_id)
        scope = StreamScope(self, core_id, physical_stream_id, flags)
        self._stream_keys.add(key)
        self._streams.append(scope)
        return scope

    def _freeze(self) -> _VariantSramPlan:
        required = {item.op_id for item in self.records.ops if item.opcode not in _DECLARATION_OPCODES}
        if self._assigned_effects != required:
            raise MeshIrError("E_STREAM_CONTRACT", "authored streams must cover every effectful Kernel operation exactly once", missing_op_ids=tuple(sorted(required - self._assigned_effects)), unexpected_op_ids=tuple(sorted(self._assigned_effects - required)))
        streams = tuple(item._freeze() for item in self._streams)
        source = _AuthoredVariantSource(self.entrypoint, self.profile_id, AuthoredVariantLineage(self.authoring_variant_id), self.records, streams, self.external_backings, self.binding_slots)
        return _VariantSramPlan(source, self.plan)


class ProgramBuilder:
    def __init__(self, arch: ArchManifest, origin: AuthoredProgramOrigin):
        if type(arch) is not ArchManifest or type(origin) is not AuthoredProgramOrigin:
            raise MeshIrError("E_ABI_BOUNDS", "ProgramBuilder requires exact architecture and authored origin records")
        validate_arch(arch)
        _text(origin.namespace, "origin.namespace")
        _text(origin.name, "origin.name")
        if type(origin.version) is not int or origin.version < 1:
            raise MeshIrError("E_ABI_BOUNDS", "authored origin version must be a positive integer")
        self.arch = arch
        self.origin = origin
        self._variants: list[VariantScope] = []
        self._selection_keys: set[tuple[str, str]] = set()
        self._variant_ids: set[str] = set()

    def variant(
        self,
        entrypoint: str,
        profile_id: str,
        authoring_variant_id: str,
        *,
        records: KernelMemoryRecords,
        allocations: tuple[SramAllocation, ...],
        external_backings: tuple[ObjectBacking, ...] = (),
        binding_slots: tuple[BindingSlot, ...] = (),
    ) -> VariantScope:
        entrypoint = _text(entrypoint, "entrypoint")
        profile_id = _text(profile_id, "profile_id")
        authoring_variant_id = _text(authoring_variant_id, "authoring_variant_id")
        if type(records) is not KernelMemoryRecords or type(allocations) is not tuple or type(external_backings) is not tuple or type(binding_slots) is not tuple:
            raise MeshIrError("E_ABI_BOUNDS", "authored variant inputs have invalid record types")
        if any(type(item) is not ObjectBacking for item in external_backings) or any(type(item) is not BindingSlot for item in binding_slots):
            raise MeshIrError("E_ABI_BOUNDS", "authored backing inputs have invalid record types")
        selection = (entrypoint, profile_id)
        if selection in self._selection_keys or authoring_variant_id in self._variant_ids:
            raise MeshIrError("E_ABI_DUPLICATE", "authored variant identity is repeated", entrypoint=entrypoint, profile_id=profile_id, authoring_variant_id=authoring_variant_id)
        plan = plan_memory_sram(records, self.arch, allocations=allocations)
        scope = VariantScope(self, entrypoint, profile_id, authoring_variant_id, records, plan, external_backings, binding_slots)
        self._selection_keys.add(selection)
        self._variant_ids.add(authoring_variant_id)
        self._variants.append(scope)
        return scope

    def build(self) -> Program:
        if not self._variants:
            raise MeshIrError("E_ABI_BOUNDS", "authored Program requires at least one variant")
        from mesh_ir.passes.scheduled import lower_authored_to_program

        return lower_authored_to_program(_StaticSramState(self.origin, tuple(item._freeze() for item in self._variants)), self.arch)


__all__ = ["ProgramBuilder", "StreamScope", "VariantScope"]

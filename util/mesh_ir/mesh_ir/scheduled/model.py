from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from mesh_ir.analysis.cost import ExecutionWorkPhase
from mesh_ir.ir.kernel_ir import BufferObject, BufferView, ControlToken, ElementRegion, KernelComputation, KernelOp, KernelTensor, PartialSumDefinition, Placement, TensorShard, TensorState
from mesh_ir.traffic import Binding, BindingSlot, TrafficReport
from mesh_ir.generated.semantic_enums import EndpointSide, FenceScope, ScheduledDependencyKind

@dataclass(frozen=True)
class IdSpan:
    first_id: int
    count: int


@dataclass(frozen=True)
class CompiledProgramOrigin:
    kernel_bundle_semantic_sha256: str


@dataclass(frozen=True)
class AuthoredProgramOrigin:
    namespace: str
    name: str
    version: int


ProgramOrigin: TypeAlias = CompiledProgramOrigin | AuthoredProgramOrigin


@dataclass(frozen=True)
class CompiledVariantLineage:
    kernel_module_ordinal: int
    kernel_module_semantic_sha256: str


@dataclass(frozen=True)
class AuthoredVariantLineage:
    authoring_variant_id: str


VariantLineage: TypeAlias = CompiledVariantLineage | AuthoredVariantLineage


@dataclass(frozen=True)
class VariantMembership:
    abi_tensors: IdSpan
    runtime_shards: IdSpan
    allocations: IdSpan
    streams: IdSpan
    commands: IdSpan
    events: IdSpan
    descriptors: IdSpan
    relocations: IdSpan
    kernel_tensors: IdSpan
    computations: IdSpan
    placements: IdSpan
    logical_shards: IdSpan
    partial_sums: IdSpan
    objects: IdSpan
    views: IdSpan
    states: IdSpan
    tokens: IdSpan
    kernel_ops: IdSpan
    object_backings: IdSpan
    command_semantics: IdSpan
    barrier_groups: IdSpan
    dependencies: IdSpan
    endpoint_uses: IdSpan
    binding_slots: IdSpan


@dataclass(frozen=True)
class ProgramVariant:
    variant_id: int
    entrypoint_id: int
    profile_id: int
    lineage: VariantLineage
    lifecycle_stream_id: int
    membership: VariantMembership


@dataclass(frozen=True)
class LocalAllocationBacking:
    allocation_id: int


@dataclass(frozen=True)
class ExternalSlotBacking:
    slot_id: int


Backing: TypeAlias = LocalAllocationBacking | ExternalSlotBacking


@dataclass(frozen=True)
class ObjectBacking:
    object_id: int
    backing: Backing


@dataclass(frozen=True)
class ResidentView:
    runtime_shard_id: int
    view_id: int


@dataclass(frozen=True)
class KernelCommandSource:
    kernel_op_id: int


@dataclass(frozen=True)
class RequestBeginAttrs:
    pass


@dataclass(frozen=True)
class RequestEndAttrs:
    pass


@dataclass(frozen=True)
class HaltAttrs:
    pass


@dataclass(frozen=True)
class EventWaitAttrs:
    event_id: int


@dataclass(frozen=True)
class EventSignalAttrs:
    event_id: int


@dataclass(frozen=True)
class RepeatCommandAttrs:
    subrange_begin_stream_ordinal: int
    subrange_command_count: int
    repeat_count: int


@dataclass(frozen=True)
class AxiFenceAttrs:
    scope: FenceScope


ControlCommandAttrs: TypeAlias = RequestBeginAttrs | RequestEndAttrs | HaltAttrs | EventWaitAttrs | EventSignalAttrs | RepeatCommandAttrs | AxiFenceAttrs


@dataclass(frozen=True)
class ControlCommandSource:
    attrs: ControlCommandAttrs


CommandSource: TypeAlias = KernelCommandSource | ControlCommandSource


@dataclass(frozen=True)
class ComputeExecution:
    phases: tuple[ExecutionWorkPhase, ...]


@dataclass(frozen=True)
class DmaExecution:
    descriptor_group_id: int


@dataclass(frozen=True)
class RecvWaitExecution:
    transfer_id: int


@dataclass(frozen=True)
class BarrierExecution:
    barrier_group_id: int


@dataclass(frozen=True)
class ControlExecution:
    pass


CommandExecution: TypeAlias = ComputeExecution | DmaExecution | RecvWaitExecution | BarrierExecution | ControlExecution


@dataclass(frozen=True)
class CommandSemantics:
    command_id: int
    source: CommandSource
    execution: CommandExecution


@dataclass(frozen=True)
class KernelTokenSource:
    token_id: int


@dataclass(frozen=True)
class StateSource:
    state_id: int


@dataclass(frozen=True)
class ObjectSource:
    object_id: int


@dataclass(frozen=True)
class DescriptorSource:
    descriptor_id: int


@dataclass(frozen=True)
class StreamOrderSource:
    stream_id: int


@dataclass(frozen=True)
class LifecycleSource:
    variant_id: int


ScheduledDependencySource: TypeAlias = KernelTokenSource | StateSource | ObjectSource | DescriptorSource | StreamOrderSource | LifecycleSource


@dataclass(frozen=True)
class ScheduledDependency:
    dependency_id: int
    source_command_id: int
    target_command_id: int
    kind: ScheduledDependencyKind
    source: ScheduledDependencySource


@dataclass(frozen=True)
class BarrierArrival:
    participant_core: int
    kernel_op_id: int
    command_id: int
    done_token_id: int


@dataclass(frozen=True)
class BarrierGroup:
    barrier_group_id: int
    participants: tuple[int, ...]
    arrivals: tuple[BarrierArrival, ...]
    completion_event_id: int


@dataclass(frozen=True)
class ScheduledStream:
    stream_id: int
    core_id: int
    physical_stream_id: int
    command_begin: int
    command_count: int
    flags: int


@dataclass(frozen=True)
class DescriptorGroup:
    group_id: int
    command_id: int
    kernel_op_id: int
    descriptor_ids: tuple[int, ...]
    completion_event_id: int


@dataclass(frozen=True)
class ReadAccessUse:
    kernel_op_id: int
    access_index: int
    region: ElementRegion


@dataclass(frozen=True)
class WriteAccessUse:
    kernel_op_id: int
    access_index: int
    region: ElementRegion


EndpointAccessUse: TypeAlias = ReadAccessUse | WriteAccessUse


@dataclass(frozen=True)
class DescriptorEndpointUse:
    ref_id: int
    descriptor_id: int
    side: EndpointSide
    use: EndpointAccessUse


@dataclass(frozen=True)
class _ProgramFacts:
    origin: ProgramOrigin
    variants: tuple[ProgramVariant, ...]
    kernel_tensors: tuple[KernelTensor, ...]
    computations: tuple[KernelComputation, ...]
    placements: tuple[Placement, ...]
    logical_shards: tuple[TensorShard, ...]
    partial_sums: tuple[PartialSumDefinition, ...]
    objects: tuple[BufferObject, ...]
    views: tuple[BufferView, ...]
    states: tuple[TensorState, ...]
    tokens: tuple[ControlToken, ...]
    kernel_ops: tuple[KernelOp, ...]
    object_backings: tuple[ObjectBacking, ...]
    resident_views: tuple[ResidentView, ...]
    command_semantics: tuple[CommandSemantics, ...]
    barrier_groups: tuple[BarrierGroup, ...]
    dependencies: tuple[ScheduledDependency, ...]
    streams: tuple[ScheduledStream, ...]
    stream_command_ids: tuple[int, ...]
    descriptor_groups: tuple[DescriptorGroup, ...]
    endpoint_uses: tuple[DescriptorEndpointUse, ...]
    binding_slots: tuple[BindingSlot, ...]


@dataclass(frozen=True)
class ProgramSemantics(_ProgramFacts):
    reference_binding_identity_sha256: str
    intrinsic_traffic: TrafficReport


@dataclass(frozen=True)
class BoundInvocation:
    program_semantic_sha256: str
    arch_digest: bytes
    variant_id: int
    entrypoint_id: int
    profile_id: int
    bindings: tuple[Binding, ...]
    bound_identity_sha256: str
    traffic: TrafficReport


__all__ = [name for name in tuple(globals()) if not name.startswith("_")]

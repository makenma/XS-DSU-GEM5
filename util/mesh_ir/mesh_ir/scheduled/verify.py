from __future__ import annotations

import re
from dataclasses import dataclass, replace
from types import MappingProxyType

from mesh_ir.analysis.dependency import DependencyEdge, DependencyKind, DirectedGraph, NodeOrderKey
from mesh_ir.analysis.kernel_work import _kernel_op_work_phases_verified
from mesh_ir.analysis.lifetime import _analyze_verified_lifetimes
from mesh_ir.analysis.regions import compact_ordered_affine_axes, ordered_region_interval
from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.abi.rules import check_record_rules
from mesh_ir.canonical import canonical_json_bytes, checked_add_u64, checked_mul_u64, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, DmaKind, Engine, MemorySpace
from mesh_ir.ir.kernel_ir import AllocAttrs, BarrierAttrs, BufferObject, BufferView, CollectiveAttrs, ControlToken, DmaAttrs, GemmKernelAttrs, KernelBundle, KernelComputation, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, LocalReduceAttrs, PartialSumDefinition, Placement, RecvWaitAttrs, StateOrigin, TensorShard, TensorState, VerifiedKernelMemory, ViewDeclarationAttrs
from mesh_ir.ir.kernel_verify import verify_kernel_memory
from mesh_ir.model import Allocation, Command, CommandOperand, CommandWait, DmaDescriptor, DmaEndpoint, Entrypoint, Event, ExpectedTrafficRow, OpAttr, Profile, Program, Relocation, Shard, Stream, StringEntry, Tensor
from mesh_ir.scheduled.assemble import _PreTrafficSemantics, _PreTrafficState, _TransportSections, _VerifiedPreTrafficState
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AuthoredVariantLineage, AxiFenceAttrs, BarrierExecution, BarrierGroup, CommandSemantics, CompiledProgramOrigin, CompiledVariantLineage, ComputeExecution, ControlCommandSource, ControlExecution, DescriptorEndpointUse, DescriptorGroup, DescriptorSource, DmaExecution, EndpointSide, EventSignalAttrs, EventWaitAttrs, ExternalSlotBacking, HaltAttrs, IdSpan, KernelCommandSource, KernelTokenSource, LifecycleSource, LocalAllocationBacking, ObjectBacking, ObjectSource, ProgramSemantics, ProgramVariant, ReadAccessUse, RecvWaitExecution, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs, ResidentView, ScheduledDependency, ScheduledDependencyKind, ScheduledStream, StateSource, StreamOrderSource, VariantMembership, WriteAccessUse
from mesh_ir.scheduled.projection import abi_attr_for_kernel_op, abi_opcode_for_kernel_op, abi_projection_for_control, abi_projection_for_reference_relocation, effective_dma_burst_beats, empty_dma_row_geometry
from mesh_ir.traffic import BindingSlot, DescriptorExecution, TrafficReport, resolve_addresses, verify_traffic_report


_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class VerifiedProgram:
    program: Program
    arch: ArchManifest
    commands_by_id: MappingProxyType
    variants_by_pair: MappingProxyType


def _positive_u32(value: int, field: str) -> None:
    if type(value) is not int or not 1 <= value <= 0xFFFFFFFF:
        raise MeshIrError("E_ABI_BOUNDS", "identity is outside positive u32", field=field, value=value)


def _exact_tuple(value: object, cls: type, field: str) -> None:
    if type(value) is not tuple or any(type(item) is not cls for item in value):
        raise MeshIrError("E_ABI_BOUNDS", "Program collection is not an immutable exact record tuple", field=field)


def _verify_command_engine(command: Command, expected: Engine) -> None:
    if command.engine != int(expected):
        raise MeshIrError("E_ENGINE_MISMATCH", "command engine does not match semantic projection", command_id=command.command_id, expected_engine=int(expected), actual_engine=command.engine)


def _validate_origin(origin: object) -> None:
    if type(origin) is CompiledProgramOrigin:
        if type(origin.kernel_bundle_semantic_sha256) is not str or _DIGEST.fullmatch(origin.kernel_bundle_semantic_sha256) is None:
            raise MeshIrError("E_ABI_CHECKSUM", "compiled Program origin digest is malformed")
    elif type(origin) is AuthoredProgramOrigin:
        if type(origin.namespace) is not str or not origin.namespace or type(origin.name) is not str or not origin.name or type(origin.version) is not int or origin.version < 1:
            raise MeshIrError("E_ABI_BOUNDS", "authored Program origin is malformed")
    else:
        raise MeshIrError("E_ABI_BOUNDS", "Program origin type is invalid")


def _verify_span_partition(variants: tuple[ProgramVariant, ...], field: str, total: int) -> None:
    expected = 1
    for variant in variants:
        span = getattr(variant.membership, field)
        if type(span) is not IdSpan or type(span.first_id) is not int or type(span.count) is not int or span.first_id != expected or span.count < 0:
            raise MeshIrError("E_ABI_ORDER", "variant membership span is not canonical", field=field, variant_id=variant.variant_id)
        expected += span.count
    if expected != total + 1:
        raise MeshIrError("E_ABI_BOUNDS", "variant membership does not cover its collection", field=field, expected=total, actual=expected - 1)


def _verify_variants(semantics: ProgramSemantics | _PreTrafficSemantics, transport: _TransportSections) -> None:
    if not semantics.variants:
        raise MeshIrError("E_ABI_BOUNDS", "Program must contain at least one variant")
    if any(type(item.variant_id) is not int for item in semantics.variants) or tuple(item.variant_id for item in semantics.variants) != tuple(range(1, len(semantics.variants) + 1)):
        raise MeshIrError("E_ABI_ORDER", "Program variant IDs must be dense and canonical")
    pairs = []
    authored_variant_ids = []
    for variant in semantics.variants:
        if type(variant) is not ProgramVariant or type(variant.membership) is not VariantMembership:
            raise MeshIrError("E_ABI_BOUNDS", "Program variant record type is invalid")
        _positive_u32(variant.entrypoint_id, "entrypoint_id")
        _positive_u32(variant.profile_id, "profile_id")
        _positive_u32(variant.lifecycle_stream_id, "lifecycle_stream_id")
        if type(semantics.origin) is CompiledProgramOrigin:
            if type(variant.lineage) is not CompiledVariantLineage or type(variant.lineage.kernel_module_ordinal) is not int or variant.lineage.kernel_module_ordinal < 0 or type(variant.lineage.kernel_module_semantic_sha256) is not str or _DIGEST.fullmatch(variant.lineage.kernel_module_semantic_sha256) is None:
                raise MeshIrError("E_ABI_CHECKSUM", "compiled variant lineage is malformed", variant_id=variant.variant_id)
        elif type(variant.lineage) is not AuthoredVariantLineage or type(variant.lineage.authoring_variant_id) is not str or not variant.lineage.authoring_variant_id:
            raise MeshIrError("E_ABI_BOUNDS", "authored variant lineage is malformed", variant_id=variant.variant_id)
        else:
            authored_variant_ids.append(variant.lineage.authoring_variant_id)
        pairs.append((variant.entrypoint_id, variant.profile_id))
    if len(pairs) != len(set(pairs)):
        raise MeshIrError("E_ABI_DUPLICATE", "entrypoint/profile pair has multiple Program variants")
    if len(authored_variant_ids) != len(set(authored_variant_ids)):
        raise MeshIrError("E_ABI_DUPLICATE", "authored Program variants have duplicate lineage identities")
    if any(
        type(profile) is not Profile or type(profile.rank) is not int or
        not 0 <= profile.rank <= 8
        for profile in transport.profiles
    ):
        raise MeshIrError("E_ABI_BOUNDS", "ABI profile rank is invalid")
    transport_pairs = tuple(
        (item.entrypoint_id, item.profile_id) for item in transport.profiles
    )
    if len(transport_pairs) != len(set(transport_pairs)):
        raise MeshIrError("E_ABI_DUPLICATE", "ABI profiles have duplicate entrypoint/profile pairs")
    if set(pairs) != set(transport_pairs):
        raise MeshIrError("E_ABI_BOUNDS", "variant membership differs from ABI profiles")
    for field, source, program_field in A.VARIANT_MEMBERSHIP_TARGETS:
        target = transport if source == "transport" else semantics
        _verify_span_partition(
            semantics.variants, field, len(getattr(target, program_field))
        )


def _verify_streams(semantics: ProgramSemantics | _PreTrafficSemantics, transport: _TransportSections, arch: ArchManifest) -> None:
    if any(type(item.stream_id) is not int for item in semantics.streams) or tuple(item.stream_id for item in semantics.streams) != tuple(range(1, len(semantics.streams) + 1)):
        raise MeshIrError("E_ABI_ORDER", "Scheduled stream IDs must be dense and canonical")
    if type(semantics.stream_command_ids) is not tuple or any(type(item) is not int for item in semantics.stream_command_ids):
        raise MeshIrError("E_STREAM_CONTRACT", "stream command vector is not an immutable integer tuple")
    command_ids = tuple(item.command_id for item in transport.commands)
    if sorted(semantics.stream_command_ids) != sorted(command_ids) or len(set(semantics.stream_command_ids)) != len(command_ids):
        raise MeshIrError("E_STREAM_CONTRACT", "stream command vector must exactly partition commands")
    if len(semantics.streams) != len(transport.streams):
        raise MeshIrError("E_STREAM_CONTRACT", "semantic and ABI stream tables differ")
    commands = {item.command_id: item for item in transport.commands}
    for semantic, abi in zip(semantics.streams, transport.streams):
        if type(semantic) is not ScheduledStream or type(semantic.stream_id) is not int or type(semantic.core_id) is not int or semantic.core_id not in arch.core_ids or type(semantic.physical_stream_id) is not int or not 0 <= semantic.physical_stream_id <= 0xFFFFFFFF or type(semantic.flags) is not int:
            raise MeshIrError("E_STREAM_CONTRACT", "Scheduled stream type or core is invalid")
        if (
            type(abi.command_begin) is not int or
            type(abi.command_count) is not int or
            not 0 <= abi.command_begin <= 0xFFFFFFFF or
            not 0 <= abi.command_count <= 0xFFFFFFFF or
            abi.command_begin > len(semantics.stream_command_ids) or
            abi.command_count > len(semantics.stream_command_ids) - abi.command_begin
        ):
            raise MeshIrError("E_ABI_BOUNDS", "ABI stream command range is invalid")
        if (abi.core_id, abi.stream_id, abi.command_begin, abi.command_count, abi.flags) != (semantic.core_id, semantic.physical_stream_id, semantic.command_begin, semantic.command_count, semantic.flags):
            raise MeshIrError("E_STREAM_CONTRACT", "ABI stream is not the semantic stream projection", stream_id=semantic.stream_id)
        end = semantic.command_begin + semantic.command_count
        if type(semantic.command_begin) is not int or type(semantic.command_count) is not int or semantic.command_begin < 0 or end > len(semantics.stream_command_ids):
            raise MeshIrError("E_STREAM_CONTRACT", "Scheduled stream range is invalid", stream_id=semantic.stream_id)
        selected = tuple(commands[item] for item in semantics.stream_command_ids[semantic.command_begin:end])
        if any((item.core_id, item.stream_id) != (semantic.core_id, semantic.physical_stream_id) for item in selected):
            raise MeshIrError("E_STREAM_CONTRACT", "command is assigned to the wrong semantic stream", stream_id=semantic.stream_id)
    streams = {item.stream_id: item for item in semantics.streams}
    entrypoints = {item.entrypoint_id: item for item in transport.entrypoints}
    locations = {}
    for variant in semantics.variants:
        owned_streams = semantics.streams[variant.membership.streams.first_id - 1:variant.membership.streams.first_id - 1 + variant.membership.streams.count]
        keys = tuple((arch.core_ids.index(item.core_id), item.physical_stream_id) for item in owned_streams)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise MeshIrError("E_STREAM_CONTRACT", "variant streams are not canonical unique physical streams", variant_id=variant.variant_id)
        stream = streams.get(variant.lifecycle_stream_id)
        span = variant.membership.streams
        if stream is None or not span.first_id <= stream.stream_id < span.first_id + span.count:
            raise MeshIrError("E_STREAM_CONTRACT", "variant lifecycle stream is outside its membership", variant_id=variant.variant_id)
        location = (stream.core_id, stream.physical_stream_id)
        locations.setdefault(variant.entrypoint_id, set()).add(location)
        entrypoint = entrypoints.get(variant.entrypoint_id)
        if entrypoint is None or (entrypoint.lifecycle_core_id, entrypoint.lifecycle_stream_id) != location:
            raise MeshIrError("E_STREAM_CONTRACT", "entrypoint lifecycle projection differs from its variant", variant_id=variant.variant_id)
    if any(len(items) != 1 for items in locations.values()):
        raise MeshIrError("E_STREAM_CONTRACT", "entrypoint profiles use different lifecycle locations")


def _verify_commands(semantics: ProgramSemantics | _PreTrafficSemantics, transport: _TransportSections, verified_memories: tuple[VerifiedKernelMemory, ...], arch: ArchManifest) -> None:
    if any(type(item.command_id) is not int for item in semantics.command_semantics) or tuple(item.command_id for item in semantics.command_semantics) != tuple(item.command_id for item in transport.commands):
        raise MeshIrError("E_ABI_ORDER", "command identities must be dense and aligned")
    for command in transport.commands:
        fields = (command.wait_begin, command.wait_count, command.signal_event)
        if any(type(value) is not int for value in fields) or command.wait_begin < 0 or command.wait_count < 0 or command.signal_event < 0 or command.wait_begin + command.wait_count > len(transport.command_waits):
            raise MeshIrError("E_ABI_BOUNDS", "command synchronization span is invalid", command_id=command.command_id)
        expected_kind = {
            A.OPCODE.GEMM: A.ATTR_KIND.GEMM_V1,
            A.OPCODE.BMM: A.ATTR_KIND.BMM_V1,
        }.get(command.opcode)
        if expected_kind is None or not 1 <= command.attr_index <= len(transport.op_attrs):
            continue
        attr = transport.op_attrs[command.attr_index - 1]
        if attr.kind != expected_kind:
            continue
        values = dict(zip(attr.payload_fields, attr.payload))
        if not all(field in values for field in ("batch", "m", "n", "k")):
            continue
        work = 1
        for field in ("batch", "m", "n", "k"):
            work = checked_mul_u64(work, values[field], "GEMM workload")
            if work >= 1 << 63:
                raise MeshIrError("E_ABI_OVERFLOW", "GEMM workload exceeds the schedulable range", command_id=command.command_id)
    for semantic, command in zip(semantics.command_semantics, transport.commands):
        if type(semantic) is not CommandSemantics:
            raise MeshIrError("E_ABI_BOUNDS", "command semantics record type is invalid")
        if type(command.engine) is not int or type(command.opcode) is not int:
            raise MeshIrError("E_ABI_ENUM", "command engine or opcode is not an exact integer", command_id=command.command_id)
        if type(semantic.source) is ControlCommandSource:
            expected_opcode, expected_attr = abi_projection_for_control(semantic.source.attrs)
            if command.opcode != expected_opcode or command.source_op_id != 0 or type(semantic.execution) is not ControlExecution:
                raise MeshIrError("E_ABI_ENUM", "control command semantic projection is invalid", command_id=command.command_id)
            _verify_command_engine(command, Engine.CONTROL)
            if expected_attr is None:
                if command.attr_index != 0:
                    raise MeshIrError("E_ABI_ENUM", "control command has an unexpected ABI attribute", command_id=command.command_id)
            elif type(command.attr_index) is not int or not 1 <= command.attr_index <= len(transport.op_attrs) or canonical_json_bytes(transport.op_attrs[command.attr_index - 1]) != canonical_json_bytes(expected_attr):
                raise MeshIrError("E_ABI_ENUM", "control command ABI attribute projection is invalid", command_id=command.command_id)
            if type(semantic.source.attrs) is EventSignalAttrs and command.signal_event != semantic.source.attrs.event_id:
                raise MeshIrError("E_EVENT_NO_PRODUCER", "event signal command does not produce its typed event", command_id=command.command_id)
            if type(semantic.source.attrs) is EventWaitAttrs and semantic.source.attrs.event_id not in {item.event_id for item in transport.command_waits[command.wait_begin:command.wait_begin + command.wait_count]}:
                raise MeshIrError("E_EVENT_NO_PRODUCER", "event wait command does not wait on its typed event", command_id=command.command_id)
    command_by_op = {item.source.kernel_op_id: (item, transport.commands[item.command_id - 1]) for item in semantics.command_semantics if type(item.source) is KernelCommandSource}
    expected_ops = {item.op_id for item in semantics.kernel_ops if item.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW)}
    if set(command_by_op) != expected_ops:
        raise MeshIrError("E_ABI_BOUNDS", "Kernel effect operations and commands do not correspond exactly")
    groups_by_command = {item.command_id: item for item in semantics.descriptor_groups}
    barriers_by_command = {arrival.command_id: item for item in semantics.barrier_groups for arrival in item.arrivals}
    throughput = {
        Engine.TENSOR: arch.tensor_macs_per_cycle,
        Engine.VECTOR: arch.vector_elements_per_cycle,
        Engine.REDUCE: arch.reduce_ops_per_cycle,
    }
    for variant, verified_memory in zip(semantics.variants, verified_memories):
        records = verified_memory.records
        work_phases = tuple(_kernel_op_work_phases_verified(records, op.op_id) for op in records.ops)
        for op, phases in zip(records.ops, work_phases):
            for phase in phases:
                for work in phase.work:
                    if work.dtype.name.lower() not in throughput[work.engine]:
                        raise MeshIrError("E_CAPABILITY_MISMATCH", "architecture lacks required Program compute dtype throughput", op_id=op.op_id, engine=work.engine.name, dtype=work.dtype.name)
        command_span = variant.membership.commands
        event_span = variant.membership.events
        for command in transport.commands[command_span.first_id - 1:command_span.first_id - 1 + command_span.count]:
            selected_waits = transport.command_waits[command.wait_begin:command.wait_begin + command.wait_count]
            if command.signal_event and not event_span.first_id <= command.signal_event < event_span.first_id + event_span.count or any(not event_span.first_id <= item.event_id < event_span.first_id + event_span.count for item in selected_waits):
                raise MeshIrError("E_ABI_BOUNDS", "command synchronization crosses its variant", command_id=command.command_id)
        for local_op in records.ops:
            if local_op.opcode in (KernelOpcode.ALLOC, KernelOpcode.VIEW):
                continue
            global_id = variant.membership.kernel_ops.first_id + local_op.op_id - 1
            semantic, command = command_by_op[global_id]
            stream = next((item for item in semantics.streams if command.command_id in semantics.stream_command_ids[item.command_begin:item.command_begin + item.command_count]), None)
            if stream is None or command.source_op_id != global_id or command.core_id != local_op.owner_core:
                raise MeshIrError("E_ABI_BOUNDS", "Kernel command ownership projection is invalid", command_id=command.command_id)
            expected_attr = abi_attr_for_kernel_op(local_op, records)
            if expected_attr is None:
                if command.attr_index != 0:
                    raise MeshIrError("E_ABI_ENUM", "Kernel command has an unexpected ABI attribute", command_id=command.command_id)
            elif type(command.attr_index) is not int or not 1 <= command.attr_index <= len(transport.op_attrs) or canonical_json_bytes(transport.op_attrs[command.attr_index - 1]) != canonical_json_bytes(expected_attr):
                raise MeshIrError("E_ABI_ENUM", "Kernel command ABI attribute projection is invalid", command_id=command.command_id)
            if local_op.opcode is KernelOpcode.DMA:
                if type(local_op.attrs) is not DmaAttrs or command.opcode != abi_opcode_for_kernel_op(local_op) or type(semantic.execution) is not DmaExecution:
                    raise MeshIrError("E_ABI_ENUM", "DMA command projection is invalid", command_id=command.command_id)
                group = groups_by_command.get(command.command_id)
                expected_engine = Engine.DMA_READ if local_op.attrs.kind in (DmaKind.LOAD, DmaKind.PREFETCH) else Engine.DMA_WRITE
                _verify_command_engine(command, expected_engine)
                if group is None or semantic.execution.descriptor_group_id != group.group_id or group.kernel_op_id != global_id:
                    raise MeshIrError("E_DMA_RANGE", "DMA command descriptor group is invalid", command_id=command.command_id)
            elif local_op.opcode is KernelOpcode.RECV_WAIT:
                if type(local_op.attrs) is not RecvWaitAttrs or command.opcode != A.OPCODE.RECV_WAIT or type(semantic.execution) is not RecvWaitExecution or semantic.execution.transfer_id != local_op.attrs.transfer_id:
                    raise MeshIrError("E_ABI_ENUM", "receive-wait command projection is invalid", command_id=command.command_id)
                _verify_command_engine(command, Engine.CONTROL)
            elif local_op.opcode is KernelOpcode.BARRIER:
                group = barriers_by_command.get(command.command_id)
                if command.opcode != A.OPCODE.BARRIER or type(semantic.execution) is not BarrierExecution or group is None or semantic.execution.barrier_group_id != group.barrier_group_id:
                    raise MeshIrError("E_ABI_ENUM", "barrier command projection is invalid", command_id=command.command_id)
                _verify_command_engine(command, Engine.CONTROL)
            else:
                phases = work_phases[local_op.op_id - 1]
                expected_engine = phases[0].engine if phases else Engine.CONTROL
                if command.opcode != abi_opcode_for_kernel_op(local_op) or type(semantic.execution) is not ComputeExecution or canonical_json_bytes(semantic.execution.phases) != canonical_json_bytes(phases):
                    raise MeshIrError("E_ABI_ENUM", "compute command execution projection is invalid", command_id=command.command_id)
                _verify_command_engine(command, expected_engine)
    if any(type(item.event_id) is not int for item in transport.events) or tuple(item.event_id for item in transport.events) != tuple(range(1, len(transport.events) + 1)):
        raise MeshIrError("E_ABI_ORDER", "event identities must be dense exact integers")
    event_ids = {item.event_id for item in transport.events}
    command_ids = {item.command_id for item in transport.commands}
    command_signals = {}
    for command in transport.commands:
        if command.signal_event and command.signal_event not in event_ids:
            raise MeshIrError("E_ABI_BOUNDS", "command signals an unknown event", command_id=command.command_id)
        if command.signal_event:
            command_signals.setdefault(command.signal_event, []).append(command.command_id)
        if any(type(item.event_id) is not int or item.event_id not in event_ids for item in transport.command_waits[command.wait_begin:command.wait_begin + command.wait_count]):
            raise MeshIrError("E_ABI_BOUNDS", "command waits on an unknown event", command_id=command.command_id)
    for event in transport.events:
        if type(event.kind) is not int or type(event.producer_command_id) is not int or type(event.expected_arrivals) is not int:
            raise MeshIrError("E_ABI_BOUNDS", "event fields are not exact integers", event_id=event.event_id)
        producers = tuple(command_signals.get(event.event_id, ()))
        if event.kind == A.EVENT_KIND.NORMAL:
            if len(producers) > 1:
                raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "normal event has multiple signaling commands", event_id=event.event_id)
            if event.expected_arrivals != 0:
                raise MeshIrError("E_ABI_BOUNDS", "normal event expected arrivals must be zero", event_id=event.event_id)
            if event.producer_command_id not in command_ids or producers != (event.producer_command_id,):
                raise MeshIrError("E_EVENT_NO_PRODUCER", "normal event producer projection is invalid", event_id=event.event_id)
        elif event.kind == A.EVENT_KIND.BARRIER:
            if any(transport.commands[command_id - 1].opcode != A.OPCODE.BARRIER for command_id in producers):
                raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "barrier event has a non-barrier signaling command", event_id=event.event_id)
        else:
            raise MeshIrError("E_ABI_ENUM", "event kind is invalid", event_id=event.event_id)


def _dependency_graph_kind(kind: ScheduledDependencyKind) -> DependencyKind:
    if kind is ScheduledDependencyKind.KERNEL_STATE or kind in (ScheduledDependencyKind.RAW, ScheduledDependencyKind.WAR, ScheduledDependencyKind.WAW, ScheduledDependencyKind.SRAM_REUSE):
        return DependencyKind.STATE
    if kind in (ScheduledDependencyKind.DMA_PIN, ScheduledDependencyKind.DMA_COMPLETION):
        return DependencyKind.OP_COMPLETION
    if kind is ScheduledDependencyKind.LIFECYCLE:
        return DependencyKind.INVOCATION_BEGIN
    return DependencyKind.CONTROL


def _dependency_source_identity(source: object) -> int:
    if type(source) is KernelTokenSource:
        return source.token_id
    if type(source) is StateSource:
        return source.state_id
    if type(source) is ObjectSource:
        return source.object_id
    if type(source) is DescriptorSource:
        return source.descriptor_id
    if type(source) is StreamOrderSource:
        return source.stream_id
    if type(source) is LifecycleSource:
        return source.variant_id
    raise MeshIrError("E_ABI_BOUNDS", "Scheduled dependency source type is invalid")


def _verify_dependencies_and_lifecycle(semantics: ProgramSemantics | _PreTrafficSemantics, transport: _TransportSections) -> None:
    if any(type(item.dependency_id) is not int for item in semantics.dependencies) or tuple(item.dependency_id for item in semantics.dependencies) != tuple(range(1, len(semantics.dependencies) + 1)):
        raise MeshIrError("E_ABI_ORDER", "dependency IDs must be dense and canonical")
    commands = {item.command_id: item for item in transport.commands}
    command_ids = set(commands)
    semantic_by_command = {item.command_id: item for item in semantics.command_semantics}
    ops = {item.op_id: item for item in semantics.kernel_ops}
    if any(type(item.source) is KernelCommandSource and type(item.source.kernel_op_id) is not int for item in semantics.command_semantics):
        raise MeshIrError("E_ABI_BOUNDS", "Kernel command source identity is not an exact integer")
    command_by_op = {item.source.kernel_op_id: item.command_id for item in semantics.command_semantics if type(item.source) is KernelCommandSource}
    if len(command_by_op) != sum(type(item.source) is KernelCommandSource for item in semantics.command_semantics):
        raise MeshIrError("E_ABI_DUPLICATE", "Kernel operation is associated with multiple commands")
    variant_by_command = {}
    variant_by_op = {}
    for variant in semantics.variants:
        for command_id in range(variant.membership.commands.first_id, variant.membership.commands.first_id + variant.membership.commands.count):
            variant_by_command[command_id] = variant
        for op_id in range(variant.membership.kernel_ops.first_id, variant.membership.kernel_ops.first_id + variant.membership.kernel_ops.count):
            variant_by_op[op_id] = variant
    for item in semantics.command_semantics:
        if type(item.source) is KernelCommandSource and variant_by_op.get(item.source.kernel_op_id) is not variant_by_command.get(item.command_id):
            raise MeshIrError("E_ABI_BOUNDS", "Kernel command source is outside its command variant", command_id=item.command_id)
    streams_by_command = {}
    stream_position = {}
    for stream in semantics.streams:
        for position, command_id in enumerate(semantics.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]):
            streams_by_command[command_id] = stream
            stream_position[command_id] = position
    views = {item.view_id: item for item in semantics.views}
    producer_by_token = {item.done_token: item.op_id for item in semantics.kernel_ops if item.done_token is not None}
    producer_by_state = {transition.new_state_id: item.op_id for item in semantics.kernel_ops for transition in item.writes}
    descriptor_groups = {descriptor_id: group for group in semantics.descriptor_groups for descriptor_id in group.descriptor_ids}
    expected_source_types = {
        ScheduledDependencyKind.KERNEL_CONTROL: KernelTokenSource,
        ScheduledDependencyKind.KERNEL_STATE: StateSource,
        ScheduledDependencyKind.RAW: ObjectSource,
        ScheduledDependencyKind.WAR: ObjectSource,
        ScheduledDependencyKind.WAW: ObjectSource,
        ScheduledDependencyKind.DMA_PIN: DescriptorSource,
        ScheduledDependencyKind.DMA_COMPLETION: DescriptorSource,
        ScheduledDependencyKind.SRAM_REUSE: ObjectSource,
        ScheduledDependencyKind.STREAM_ORDER: StreamOrderSource,
        ScheduledDependencyKind.LIFECYCLE: LifecycleSource,
    }
    edge_pairs = set()
    graph_edges = set()
    typed_edges = set()
    for item in semantics.dependencies:
        if type(item) is not ScheduledDependency or type(item.source_command_id) is not int or type(item.target_command_id) is not int or type(item.kind) is not ScheduledDependencyKind or item.source_command_id not in command_ids or item.target_command_id not in command_ids or item.source_command_id == item.target_command_id:
            raise MeshIrError("E_ABI_BOUNDS", "Scheduled dependency is invalid")
        variant = variant_by_command.get(item.source_command_id)
        if variant is None or variant_by_command.get(item.target_command_id) is not variant or type(item.source) is not expected_source_types[item.kind]:
            raise MeshIrError("E_ABI_BOUNDS", "Scheduled dependency crosses a variant or has the wrong typed source", dependency_id=item.dependency_id)
        pair = (item.source_command_id, item.target_command_id)
        edge_pairs.add(pair)
        typed = (item.source_command_id, item.target_command_id, item.kind, item.source)
        if typed in typed_edges:
            raise MeshIrError("E_ABI_DUPLICATE", "Scheduled typed dependency is duplicated", dependency_id=item.dependency_id)
        typed_edges.add(typed)
        identity = _dependency_source_identity(item.source)
        if type(item.source) is not StreamOrderSource:
            _positive_u32(identity, "dependency_source_identity")
        source_semantic = semantic_by_command[item.source_command_id]
        target_semantic = semantic_by_command[item.target_command_id]
        source_op = ops.get(source_semantic.source.kernel_op_id) if type(source_semantic.source) is KernelCommandSource else None
        target_op = ops.get(target_semantic.source.kernel_op_id) if type(target_semantic.source) is KernelCommandSource else None
        if source_op is not None and variant_by_op.get(source_op.op_id) is not variant or target_op is not None and variant_by_op.get(target_op.op_id) is not variant:
            raise MeshIrError("E_ABI_BOUNDS", "command dependency uses a Kernel operation from another variant", dependency_id=item.dependency_id)
        if type(item.source) is LifecycleSource:
            if item.source.variant_id != variant.variant_id:
                raise MeshIrError("E_LIFECYCLE", "lifecycle dependency names the wrong variant", dependency_id=item.dependency_id)
        elif type(item.source) is KernelTokenSource:
            if source_op is None or source_op.done_token != item.source.token_id or target_op is not None and item.source.token_id not in target_op.after_tokens:
                raise MeshIrError("E_ABI_BOUNDS", "Kernel token dependency contradicts command operations", dependency_id=item.dependency_id)
        elif type(item.source) is StateSource:
            source_id = producer_by_state.get(item.source.state_id)
            target_states = () if target_op is None else tuple(access.state_id for access in target_op.reads) + tuple(access.old_state_id for access in target_op.writes)
            if source_op is None or source_id != source_op.op_id or item.source.state_id not in target_states:
                raise MeshIrError("E_ABI_BOUNDS", "Kernel state dependency contradicts command operations", dependency_id=item.dependency_id)
        elif type(item.source) is DescriptorSource:
            group = descriptor_groups.get(item.source.descriptor_id)
            if group is None or group.command_id != item.source_command_id or variant_by_command.get(group.command_id) is not variant:
                raise MeshIrError("E_ABI_BOUNDS", "descriptor dependency contradicts its owning command", dependency_id=item.dependency_id)
        elif type(item.source) is ObjectSource:
            if not variant.membership.objects.first_id <= item.source.object_id < variant.membership.objects.first_id + variant.membership.objects.count or source_op is None or target_op is None:
                raise MeshIrError("E_ABI_BOUNDS", "object dependency is outside its variant", dependency_id=item.dependency_id)
            if item.kind is ScheduledDependencyKind.SRAM_REUSE:
                source_objects = {views[access.view_id].object_id for access in (*source_op.reads, *source_op.writes)}
                if item.source.object_id not in source_objects:
                    raise MeshIrError("E_ABI_BOUNDS", "SRAM reuse dependency contradicts source operation access", dependency_id=item.dependency_id)
            elif item.kind in (ScheduledDependencyKind.RAW, ScheduledDependencyKind.WAR, ScheduledDependencyKind.WAW):
                source_reads = {views[access.view_id].object_id for access in source_op.reads}
                source_writes = {views[access.view_id].object_id for access in source_op.writes}
                target_reads = {views[access.view_id].object_id for access in target_op.reads}
                target_writes = {views[access.view_id].object_id for access in target_op.writes}
                required = (source_writes, target_reads) if item.kind is ScheduledDependencyKind.RAW else (source_reads, target_writes) if item.kind is ScheduledDependencyKind.WAR else (source_writes, target_writes)
                if item.source.object_id not in required[0] or item.source.object_id not in required[1]:
                    raise MeshIrError("E_ABI_BOUNDS", "memory hazard dependency contradicts operation accesses", dependency_id=item.dependency_id)
        elif type(item.source) is StreamOrderSource:
            source_stream = streams_by_command.get(item.source_command_id)
            target_stream = streams_by_command.get(item.target_command_id)
            if type(item.source.stream_id) is not int or not 1 <= item.source.stream_id <= 0xFFFFFFFF or source_stream is None or target_stream is not source_stream or source_stream.stream_id != item.source.stream_id:
                raise MeshIrError("E_STREAM_CONTRACT", "stream dependency contradicts stream ownership", dependency_id=item.dependency_id)
        graph_edges.add(DependencyEdge(item.source_command_id, item.target_command_id, _dependency_graph_kind(item.kind), identity))
    for op in semantics.kernel_ops:
        target_command = command_by_op.get(op.op_id)
        if target_command is None:
            continue
        for token_id in op.after_tokens:
            source_op_id = producer_by_token.get(token_id)
            source_command = command_by_op.get(source_op_id)
            required = (source_command, target_command, ScheduledDependencyKind.KERNEL_CONTROL, KernelTokenSource(token_id))
            if source_command is not None and required not in typed_edges:
                raise MeshIrError("E_ABI_BOUNDS", "required Kernel token dependency is missing", kernel_op_id=op.op_id, token_id=token_id)
        for state_id in tuple(access.state_id for access in op.reads) + tuple(access.old_state_id for access in op.writes):
            source_op_id = producer_by_state.get(state_id)
            source_command = command_by_op.get(source_op_id)
            required = (source_command, target_command, ScheduledDependencyKind.KERNEL_STATE, StateSource(state_id))
            if source_command is not None and required not in typed_edges:
                raise MeshIrError("E_ABI_BOUNDS", "required Kernel state dependency is missing", kernel_op_id=op.op_id, state_id=state_id)
    events = {item.event_id: item for item in transport.events}
    barrier_producers = {group.completion_event_id: tuple(item.command_id for item in group.arrivals) for group in semantics.barrier_groups}
    descriptor_group_by_completion = {}
    for group in semantics.descriptor_groups:
        descriptor_group_by_completion.setdefault((group.command_id, group.completion_event_id), group)
    factual_completion_kinds = {ScheduledDependencyKind.KERNEL_CONTROL, ScheduledDependencyKind.KERNEL_STATE, ScheduledDependencyKind.RAW, ScheduledDependencyKind.WAR, ScheduledDependencyKind.WAW, ScheduledDependencyKind.SRAM_REUSE}
    factual_completion_pairs = {(left, right) for left, right, kind, _ in typed_edges if kind in factual_completion_kinds}
    execution_edges = set()
    for target in transport.commands:
        for wait in transport.command_waits[target.wait_begin:target.wait_begin + target.wait_count]:
            event = events[wait.event_id]
            producers = barrier_producers.get(wait.event_id, (event.producer_command_id,) if event.producer_command_id else ())
            if not producers:
                raise MeshIrError("E_EVENT_NO_PRODUCER", "command wait event has no typed producer", command_id=target.command_id, event_id=wait.event_id)
            for source_command in producers:
                pair = (source_command, target.command_id)
                execution_edges.add(DependencyEdge(source_command, target.command_id, DependencyKind.OP_COMPLETION, wait.event_id))
                if pair not in edge_pairs:
                    raise MeshIrError("E_ABI_BOUNDS", "command wait is not backed by a Scheduled dependency", command_id=target.command_id, event_id=wait.event_id)
                group = descriptor_group_by_completion.get((source_command, wait.event_id))
                if group is not None:
                    for descriptor_id in group.descriptor_ids:
                        if (source_command, target.command_id, ScheduledDependencyKind.DMA_COMPLETION, DescriptorSource(descriptor_id)) not in typed_edges:
                            raise MeshIrError("E_ABI_BOUNDS", "descriptor completion dependency is missing", descriptor_id=descriptor_id)
                else:
                    producer_semantic = semantic_by_command[source_command]
                    if type(producer_semantic.source) is KernelCommandSource:
                        if pair not in factual_completion_pairs:
                            raise MeshIrError("E_ABI_BOUNDS", "Kernel completion event lacks a factual typed dependency", command_id=source_command)
                    elif (source_command, target.command_id, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(variant_by_command[source_command].variant_id)) not in typed_edges:
                        raise MeshIrError("E_LIFECYCLE", "control completion event lacks its lifecycle dependency", command_id=source_command)
    execution_graph = DirectedGraph(
        tuple(range(1, len(transport.commands) + 1)),
        tuple(sorted(execution_edges)),
        tuple(NodeOrderKey(f"command:{item.command_id:010d}", str(item.opcode), ()) for item in transport.commands),
    )
    for item in semantics.dependencies:
        if item.kind is ScheduledDependencyKind.STREAM_ORDER:
            if streams_by_command.get(item.source_command_id) is not streams_by_command.get(item.target_command_id) or stream_position[item.source_command_id] >= stream_position[item.target_command_id]:
                raise MeshIrError("E_STREAM_CONTRACT", "stream dependency is not realized by stream admission order", dependency_id=item.dependency_id)
        elif not execution_graph.happens_before(item.source_command_id, item.target_command_id):
            raise MeshIrError("E_EVENT_NO_PRODUCER", "Scheduled completion dependency is not realized by event synchronization", dependency_id=item.dependency_id)
    graph = DirectedGraph(
        tuple(range(1, len(transport.commands) + 1)),
        tuple(sorted(graph_edges)),
        tuple(NodeOrderKey(f"command:{item.command_id:010d}", str(item.opcode), ()) for item in transport.commands),
    )
    graph.canonical_topological_order()
    if any(streams_by_command.get(source) is streams_by_command.get(target) and stream_position[source] >= stream_position[target] for source, target in edge_pairs):
        raise MeshIrError("E_STREAM_CONTRACT", "same-stream dependency order is violated")
    for variant in semantics.variants:
        span = variant.membership.commands
        owned = set(range(span.first_id, span.first_id + span.count))
        begin = [item.command_id for item in transport.commands if item.command_id in owned and item.opcode == A.OPCODE.REQUEST_BEGIN]
        end = [item.command_id for item in transport.commands if item.command_id in owned and item.opcode == A.OPCODE.REQUEST_END]
        halt = [item.command_id for item in transport.commands if item.command_id in owned and item.opcode == A.OPCODE.HALT]
        if len(begin) != 1 or len(end) != 1 or not halt:
            raise MeshIrError("E_LIFECYCLE", "variant lifecycle command set is incomplete", variant_id=variant.variant_id)
        reachable_begin = graph.reachable(begin[0])
        reachable_end = graph.reachable(end[0])
        if not owned - {begin[0]} <= reachable_begin or end[0] not in reachable_begin or any(item not in reachable_end for item in halt):
            raise MeshIrError("E_LIFECYCLE", "variant lifecycle reachability is incomplete", variant_id=variant.variant_id)
        executed_after_begin = execution_graph.reachable(begin[0])
        executed_before_end = owned & execution_graph.ancestors(end[0])
        work = owned - set(begin) - set(end) - set(halt)
        if not work <= executed_after_begin or not work <= executed_before_end or any(item not in execution_graph.reachable(end[0]) for item in halt):
            raise MeshIrError("E_LIFECYCLE", "variant work is not fully synchronized between lifecycle boundaries", variant_id=variant.variant_id)
        lifecycle = [item for item in semantics.dependencies if item.kind is ScheduledDependencyKind.LIFECYCLE and type(item.source) is LifecycleSource and item.source.variant_id == variant.variant_id]
        if not lifecycle:
            raise MeshIrError("E_LIFECYCLE", "variant has no typed lifecycle dependencies", variant_id=variant.variant_id)


def _local_id(value: int, span: IdSpan, field: str, optional: bool = False) -> int:
    if type(value) is not int:
        raise MeshIrError("E_ABI_BOUNDS", "cross-variant or unknown semantic identity", field=field, value=value)
    if optional and value == 0:
        return 0
    if not span.first_id <= value < span.first_id + span.count:
        raise MeshIrError("E_ABI_BOUNDS", "cross-variant or unknown semantic identity", field=field, value=value)
    return value - span.first_id + 1


def _slice(items: tuple, span: IdSpan) -> tuple:
    return items[span.first_id - 1:span.first_id - 1 + span.count]


def _local_memory_records(semantics: ProgramSemantics | _PreTrafficSemantics, variant: ProgramVariant) -> KernelMemoryRecords:
    membership = variant.membership
    tensors = tuple(replace(item, tensor_id=_local_id(item.tensor_id, membership.kernel_tensors, "tensor_id"), producer_computation_id=_local_id(item.producer_computation_id, membership.computations, "producer_computation_id", True), alias_root_tensor_id=_local_id(item.alias_root_tensor_id, membership.kernel_tensors, "alias_root_tensor_id")) for item in _slice(semantics.kernel_tensors, membership.kernel_tensors))
    computations = tuple(replace(item, computation_id=_local_id(item.computation_id, membership.computations, "computation_id"), operand_tensor_ids=tuple(_local_id(value, membership.kernel_tensors, "operand_tensor_id") for value in item.operand_tensor_ids), result_tensor_id=_local_id(item.result_tensor_id, membership.kernel_tensors, "result_tensor_id")) for item in _slice(semantics.computations, membership.computations))
    placements = tuple(replace(item, placement_id=_local_id(item.placement_id, membership.placements, "placement_id")) for item in _slice(semantics.placements, membership.placements))
    shards = tuple(replace(item, shard_id=_local_id(item.shard_id, membership.logical_shards, "logical_shard_id"), tensor_id=_local_id(item.tensor_id, membership.kernel_tensors, "shard_tensor_id"), placement_id=_local_id(item.placement_id, membership.placements, "shard_placement_id"), partial_sum_id=_local_id(item.partial_sum_id, membership.partial_sums, "shard_partial_sum_id", True)) for item in _slice(semantics.logical_shards, membership.logical_shards))
    partials = tuple(replace(item, partial_sum_id=_local_id(item.partial_sum_id, membership.partial_sums, "partial_sum_id"), computation_id=_local_id(item.computation_id, membership.computations, "partial_computation_id"), semantic_result_tensor_id=_local_id(item.semantic_result_tensor_id, membership.kernel_tensors, "partial_result_tensor_id"), accumulator_tensor_id=_local_id(item.accumulator_tensor_id, membership.kernel_tensors, "partial_accumulator_tensor_id"), placement_id=_local_id(item.placement_id, membership.placements, "partial_placement_id")) for item in _slice(semantics.partial_sums, membership.partial_sums))
    objects = tuple(replace(item, object_id=_local_id(item.object_id, membership.objects, "object_id"), storage_tensor_id=_local_id(item.storage_tensor_id, membership.kernel_tensors, "storage_tensor_id")) for item in _slice(semantics.objects, membership.objects))
    views = tuple(replace(item, view_id=_local_id(item.view_id, membership.views, "view_id"), object_id=_local_id(item.object_id, membership.objects, "view_object_id"), shard_id=_local_id(item.shard_id, membership.logical_shards, "view_shard_id")) for item in _slice(semantics.views, membership.views))
    states = tuple(replace(item, state_id=_local_id(item.state_id, membership.states, "state_id"), object_id=_local_id(item.object_id, membership.objects, "state_object_id"), partial_sum_id=_local_id(item.partial_sum_id, membership.partial_sums, "state_partial_sum_id", True)) for item in _slice(semantics.states, membership.states))
    tokens = tuple(replace(item, token_id=_local_id(item.token_id, membership.tokens, "token_id")) for item in _slice(semantics.tokens, membership.tokens))
    ops = []
    for item in _slice(semantics.kernel_ops, membership.kernel_ops):
        attrs = item.attrs
        if type(attrs) is AllocAttrs:
            attrs = replace(attrs, object_id=_local_id(attrs.object_id, membership.objects, "allocation_object_id"))
        elif type(attrs) is ViewDeclarationAttrs:
            attrs = replace(attrs, view_id=_local_id(attrs.view_id, membership.views, "declaration_view_id"))
        elif type(attrs) in (GemmKernelAttrs, CollectiveAttrs, LocalReduceAttrs):
            attrs = replace(attrs, partial_sum_id=_local_id(attrs.partial_sum_id, membership.partial_sums, "operation_partial_sum_id", True))
        reads = tuple(replace(access, state_id=_local_id(access.state_id, membership.states, "read_state_id"), view_id=_local_id(access.view_id, membership.views, "read_view_id")) for access in item.reads)
        writes = tuple(replace(access, old_state_id=_local_id(access.old_state_id, membership.states, "old_state_id"), new_state_id=_local_id(access.new_state_id, membership.states, "new_state_id"), view_id=_local_id(access.view_id, membership.views, "write_view_id")) for access in item.writes)
        ops.append(replace(item, op_id=_local_id(item.op_id, membership.kernel_ops, "kernel_op_id"), computation_id=_local_id(item.computation_id, membership.computations, "operation_computation_id", True), result_shard_id=_local_id(item.result_shard_id, membership.logical_shards, "result_shard_id", True), reads=reads, writes=writes, attrs=attrs, after_tokens=tuple(_local_id(value, membership.tokens, "after_token_id") for value in item.after_tokens), done_token=None if item.done_token is None else _local_id(item.done_token, membership.tokens, "done_token_id")))
    return KernelMemoryRecords(tensors, computations, placements, shards, partials, objects, views, states, tokens, tuple(ops))


def _verify_descriptor_coverage(semantics: ProgramSemantics | _PreTrafficSemantics, transport: _TransportSections, arch: ArchManifest) -> None:
    if any(type(item.descriptor_id) is not int for item in transport.dma_descriptors) or tuple(item.descriptor_id for item in transport.dma_descriptors) != tuple(range(1, len(transport.dma_descriptors) + 1)):
        raise MeshIrError("E_ABI_ORDER", "DMA descriptor IDs must be dense exact integers")
    descriptors = {item.descriptor_id: item for item in transport.dma_descriptors}
    groups_by_op = {}
    descriptor_owners = {}
    for group in semantics.descriptor_groups:
        if type(group.group_id) is not int or type(group.command_id) is not int or type(group.kernel_op_id) is not int or type(group.completion_event_id) is not int or group.completion_event_id < 1 or type(group.descriptor_ids) is not tuple or not group.descriptor_ids or any(type(item) is not int for item in group.descriptor_ids):
            raise MeshIrError("E_DMA_RANGE", "descriptor group fields are invalid")
        if group.kernel_op_id in groups_by_op or any(descriptor_id in descriptor_owners for descriptor_id in group.descriptor_ids):
            raise MeshIrError("E_ABI_DUPLICATE", "DMA operation or descriptor belongs to multiple groups")
        groups_by_op[group.kernel_op_id] = group
        for descriptor_id in group.descriptor_ids:
            descriptor_owners[descriptor_id] = group.group_id
    uses_by_descriptor = {}
    for endpoint in semantics.endpoint_uses:
        if type(endpoint.ref_id) is not int or type(endpoint.descriptor_id) is not int or type(endpoint.side) is not EndpointSide or type(endpoint.use) not in (ReadAccessUse, WriteAccessUse):
            raise MeshIrError("E_DMA_RANGE", "descriptor endpoint use is invalid")
        selected = uses_by_descriptor.setdefault(endpoint.descriptor_id, {})
        if endpoint.side in selected:
            raise MeshIrError("E_ABI_DUPLICATE", "descriptor endpoint side is duplicated", descriptor_id=endpoint.descriptor_id)
        selected[endpoint.side] = endpoint
    seen_descriptors = set()
    seen_refs = set()
    for variant in semantics.variants:
        records = _local_memory_records(semantics, variant)
        op_base = variant.membership.kernel_ops.first_id - 1
        descriptor_begin = variant.membership.descriptors.first_id
        descriptor_end = descriptor_begin + variant.membership.descriptors.count
        for op in records.ops:
            if op.opcode is not KernelOpcode.DMA:
                continue
            global_op_id = op.op_id + op_base
            group = groups_by_op.get(global_op_id)
            if group is None:
                raise MeshIrError("E_DMA_RANGE", "DMA operation has no descriptor group", kernel_op_id=global_op_id)
            if not all(descriptor_begin <= item < descriptor_end for item in group.descriptor_ids):
                raise MeshIrError("E_DMA_RANGE", "descriptor group crosses its variant", group_id=group.group_id)
            if op.reads and len(op.reads) != len(op.writes) or not op.reads and len(op.writes) != 1:
                raise MeshIrError("E_DMA_RANGE", "DMA accesses do not form the required ordered pairs", kernel_op_id=global_op_id)
            pairs = ((op.writes[0], op.writes[0], 0, 0),) if not op.reads else tuple((source, destination, index, index) for index, (source, destination) in enumerate(zip(op.reads, op.writes)))
            cursors = [0] * len(pairs)
            active_pair = 0
            views = {item.view_id: item for item in records.views}
            shards = {item.shard_id: item for item in records.shards}
            tensors = {item.tensor_id: item for item in records.tensors}
            for descriptor_id in group.descriptor_ids:
                if active_pair >= len(pairs):
                    raise MeshIrError("E_DMA_RANGE", "descriptor group contains pieces after complete access coverage", group_id=group.group_id)
                descriptor = descriptors.get(descriptor_id)
                endpoints = uses_by_descriptor.get(descriptor_id)
                if descriptor is None or endpoints is None or set(endpoints) != {EndpointSide.SRC, EndpointSide.DST}:
                    raise MeshIrError("E_DMA_RANGE", "descriptor or endpoint pair is missing", descriptor_id=descriptor_id)
                command = transport.commands[group.command_id - 1]
                if type(descriptor.command_id) is not int or type(descriptor.completion_event) is not int or descriptor.completion_event < 1 or descriptor.command_id != group.command_id or descriptor.completion_event != group.completion_event_id or descriptor.completion_event != command.signal_event:
                    raise MeshIrError("E_DMA_RANGE", "descriptor completion differs from its owning command", descriptor_id=descriptor_id)
                if (
                    type(op.attrs) is not DmaAttrs or
                    descriptor.kind != int(op.attrs.kind) or
                    command.opcode != abi_opcode_for_kernel_op(op)
                ):
                    raise MeshIrError("E_ABI_ENUM", "descriptor kind differs from its DMA projection", descriptor_id=descriptor_id)
                expected_burst_limit = effective_dma_burst_beats(op.attrs, arch)
                if type(descriptor.max_burst_beats) is not int or descriptor.max_burst_beats != expected_burst_limit:
                    raise MeshIrError("E_DMA_RANGE", "descriptor burst limit differs from its Kernel source", descriptor_id=descriptor_id)
                src_use = endpoints[EndpointSide.SRC].use
                dst_use = endpoints[EndpointSide.DST].use
                expected_source_type = ReadAccessUse if op.reads else WriteAccessUse
                if type(src_use) is not expected_source_type or type(dst_use) is not WriteAccessUse or type(src_use.kernel_op_id) is not int or type(dst_use.kernel_op_id) is not int or type(src_use.access_index) is not int or type(dst_use.access_index) is not int or src_use.kernel_op_id != global_op_id or dst_use.kernel_op_id != global_op_id or src_use.access_index != dst_use.access_index or src_use.access_index != active_pair:
                    raise MeshIrError("E_DMA_RANGE", "descriptor endpoints are not the ordered access-piece projection", descriptor_id=descriptor_id)
                source, destination, source_index, destination_index = pairs[active_pair]
                if src_use.access_index != source_index or dst_use.access_index != destination_index:
                    raise MeshIrError("E_DMA_RANGE", "descriptor endpoint access indices are not paired", descriptor_id=descriptor_id)
                source_start, source_count = ordered_region_interval(source.region, src_use.region)
                destination_start, destination_count = ordered_region_interval(destination.region, dst_use.region)
                if source_start != cursors[active_pair] or destination_start != cursors[active_pair] or source_count != destination_count:
                    raise MeshIrError("E_DMA_RANGE", "descriptor pieces do not preserve exact source-to-destination order", descriptor_id=descriptor_id)
                source_view = views[source.view_id]
                destination_view = views[destination.view_id]
                source_tensor = tensors[shards[source_view.shard_id].tensor_id]
                destination_tensor = tensors[shards[destination_view.shard_id].tensor_id]
                if source_tensor.dtype.byte_width != destination_tensor.dtype.byte_width:
                    raise MeshIrError("E_DMA_RANGE", "descriptor endpoint element widths differ", descriptor_id=descriptor_id)
                width = source_tensor.dtype.byte_width
                source_strides = tuple(checked_mul_u64(checked_mul_u64(step, stride, "descriptor source element stride"), width, "descriptor source byte stride") for step, stride in zip(src_use.region.steps, source_view.object_strides))
                destination_strides = tuple(checked_mul_u64(checked_mul_u64(step, stride, "descriptor destination element stride"), width, "descriptor destination destination byte stride") for step, stride in zip(dst_use.region.steps, destination_view.object_strides))
                geometry = (descriptor.rows, descriptor.row_bytes, descriptor.src_stride_bytes, descriptor.dst_stride_bytes, descriptor.useful_bytes, descriptor.physical_storage_bytes)
                if any(type(value) is not int or value < 0 for value in geometry):
                    raise MeshIrError("E_DMA_RANGE", "descriptor geometry fields are invalid", descriptor_id=descriptor_id)
                useful = checked_mul_u64(source_count, width, "descriptor piece useful bytes")
                if source_count == 0:
                    source_geometry = empty_dma_row_geometry(src_use.region, source_tensor.dtype)
                    destination_geometry = empty_dma_row_geometry(dst_use.region, destination_tensor.dtype)
                    rows, row_bytes = source_geometry
                    if source_geometry != destination_geometry or geometry != (rows, row_bytes, row_bytes, row_bytes, 0, 0):
                        raise MeshIrError("E_DMA_RANGE", "empty descriptor piece geometry is invalid", descriptor_id=descriptor_id)
                else:
                    if descriptor.rows < 1 or descriptor.row_bytes < 1 or checked_mul_u64(descriptor.rows, descriptor.row_bytes, "descriptor traversal bytes") != useful:
                        raise MeshIrError("E_DMA_RANGE", "descriptor traversal size differs from its access piece", descriptor_id=descriptor_id)
                    source_axes = compact_ordered_affine_axes(src_use.region.shape + (width,), source_strides + (1,))
                    destination_axes = compact_ordered_affine_axes(dst_use.region.shape + (width,), destination_strides + (1,))
                    descriptor_source_axes = compact_ordered_affine_axes((descriptor.rows, descriptor.row_bytes), (descriptor.src_stride_bytes, 1))
                    descriptor_destination_axes = compact_ordered_affine_axes((descriptor.rows, descriptor.row_bytes), (descriptor.dst_stride_bytes, 1))
                    source_span = width
                    for extent, stride in zip(src_use.region.shape, source_strides):
                        source_span = checked_add_u64(source_span, checked_mul_u64(extent - 1, stride, "descriptor source physical span"), "descriptor source physical span")
                    destination_span = width
                    for extent, stride in zip(dst_use.region.shape, destination_strides):
                        destination_span = checked_add_u64(destination_span, checked_mul_u64(extent - 1, stride, "descriptor destination physical span"), "descriptor destination physical span")
                    if source_axes != descriptor_source_axes or destination_axes != descriptor_destination_axes or descriptor.useful_bytes != useful or descriptor.physical_storage_bytes != max(source_span, destination_span):
                        raise MeshIrError("E_DMA_RANGE", "descriptor geometry is not its ordered access-piece traversal", descriptor_id=descriptor_id)
                cursors[active_pair] = checked_add_u64(cursors[active_pair], source_count, "descriptor ordered coverage")
                _, pair_count = ordered_region_interval(source.region, source.region)
                if cursors[active_pair] == pair_count:
                    active_pair += 1
                elif cursors[active_pair] > pair_count:
                    raise MeshIrError("E_DMA_RANGE", "descriptor pieces exceed their access pair", descriptor_id=descriptor_id)
                seen_descriptors.add(descriptor_id)
                seen_refs.update((endpoints[EndpointSide.SRC].ref_id, endpoints[EndpointSide.DST].ref_id))
            if active_pair != len(pairs):
                raise MeshIrError("E_DMA_RANGE", "descriptor group does not exactly cover every ordered access pair", group_id=group.group_id)
    if seen_descriptors != set(descriptors) or seen_descriptors != set(descriptor_owners) or len(seen_refs) != len(semantics.endpoint_uses):
        raise MeshIrError("E_DMA_RANGE", "descriptor groups and endpoint uses do not exactly cover DMA descriptors")


def _reference_relocations_by_id(relocations: tuple[Relocation, ...]) -> dict[int, Relocation]:
    identities = tuple(item.relocation_id for item in relocations)
    if any(type(item) is not int or not 1 <= item <= 0xFFFFFFFF for item in identities):
        raise MeshIrError("E_RELOCATION", "relocation identities must be positive u32 values")
    if len(identities) != len(set(identities)):
        raise MeshIrError("E_ABI_DUPLICATE", "relocation identities are duplicated")
    check_record_rules("RELOCATIONS", relocations)
    return {item.relocation_id: item for item in relocations}


def _verify_physical_projection(semantics: ProgramSemantics | _PreTrafficSemantics, transport: _TransportSections, arch: ArchManifest, verified_memories: tuple[VerifiedKernelMemory, ...]) -> None:
    if any(type(item.allocation_id) is not int for item in transport.allocations) or tuple(item.allocation_id for item in transport.allocations) != tuple(range(1, len(transport.allocations) + 1)):
        raise MeshIrError("E_ABI_ORDER", "allocation IDs must be dense and canonical")
    tensor_ids = {item.tensor_id for item in transport.tensors}
    for shard in transport.shards:
        if shard.tensor_id not in tensor_ids:
            raise MeshIrError("E_ABI_BOUNDS", "shard tensor unknown")
    for relocation in transport.relocations:
        if relocation.tensor_id not in tensor_ids:
            raise MeshIrError("E_RELOCATION", "relocation tensor unknown")
    if type(semantics.resident_views) is not tuple or any(type(item) is not ResidentView for item in semantics.resident_views):
        raise MeshIrError("E_ABI_BOUNDS", "resident views are not an immutable exact record tuple")
    if any(type(item.runtime_shard_id) is not int or type(item.view_id) is not int for item in semantics.resident_views) or tuple(item.runtime_shard_id for item in semantics.resident_views) != tuple(range(1, len(transport.shards) + 1)) or len(semantics.resident_views) != len(transport.shards):
        raise MeshIrError("E_ABI_ORDER", "resident views and runtime shards are not one-to-one")
    if {item.view_id for item in semantics.resident_views} != {item.view_id for item in semantics.views} or len(semantics.resident_views) != len(semantics.views):
        raise MeshIrError("E_ABI_BOUNDS", "resident views do not exactly cover semantic views")
    operand_cursor = 0
    resident_by_runtime = {item.runtime_shard_id: item for item in semantics.resident_views}
    relocations_by_id = _reference_relocations_by_id(transport.relocations)
    for variant, verified_memory in zip(semantics.variants, verified_memories):
        membership = variant.membership
        records = verified_memory.records
        objects = {item.object_id: item for item in _slice(semantics.objects, membership.objects)}
        views = {item.view_id: item for item in _slice(semantics.views, membership.views)}
        logical_shards = {item.shard_id: item for item in _slice(semantics.logical_shards, membership.logical_shards)}
        tensors = {item.tensor_id: item for item in _slice(semantics.kernel_tensors, membership.kernel_tensors)}
        ops = {item.op_id: item for item in _slice(semantics.kernel_ops, membership.kernel_ops)}
        allocations = {item.allocation_id: item for item in _slice(transport.allocations, membership.allocations)}
        runtime_shards = {item.shard_id: item for item in _slice(transport.shards, membership.runtime_shards)}
        command_semantics = {item.command_id: item for item in _slice(semantics.command_semantics, membership.command_semantics)}
        backing_records = _slice(semantics.object_backings, membership.object_backings)
        if any(type(item.object_id) is not int for item in backing_records):
            raise MeshIrError("E_ABI_BOUNDS", "object backing identity is not an exact integer", variant_id=variant.variant_id)
        backings = {item.object_id: item.backing for item in backing_records}
        if len(backings) != len(backing_records) or set(backings) != set(objects):
            raise MeshIrError("E_ABI_BOUNDS", "object backing associations are incomplete, duplicated, or cross-variant", variant_id=variant.variant_id)
        allocation_by_object = {}
        slot_by_object = {}
        for object_id, backing in backings.items():
            obj = objects[object_id]
            if type(backing) is LocalAllocationBacking:
                if obj.memory_space is not MemorySpace.CORE_SRAM or type(backing.allocation_id) is not int or backing.allocation_id not in allocations:
                    raise MeshIrError("E_ABI_BOUNDS", "local object backing association is invalid", object_id=object_id)
                allocation = allocations[backing.allocation_id]
                fields = (allocation.allocation_id, allocation.owner_core, allocation.memory_space, allocation.offset_bytes, allocation.size_bytes, allocation.alignment_bytes, allocation.flags)
                if any(type(value) is not int for value in fields) or allocation.owner_core != obj.owner_core or allocation.memory_space != int(MemorySpace.CORE_SRAM) or allocation.size_bytes < obj.footprint_bytes or allocation.alignment_bytes < obj.alignment_bytes or allocation.alignment_bytes < 1 or allocation.alignment_bytes & (allocation.alignment_bytes - 1) or allocation.offset_bytes % allocation.alignment_bytes or checked_add_u64(allocation.offset_bytes, allocation.size_bytes, "allocation end") > arch.sram_bytes or allocation.flags != int(obj.persistent):
                    raise MeshIrError("E_ABI_BOUNDS", "local allocation is not the object's exact physical backing", object_id=object_id, allocation_id=allocation.allocation_id)
                allocation_by_object[object_id] = allocation.allocation_id
            elif type(backing) is ExternalSlotBacking:
                if obj.memory_space not in (MemorySpace.HBM, MemorySpace.HOST_SHARED) or type(backing.slot_id) is not int:
                    raise MeshIrError("E_ABI_BOUNDS", "external object backing association is invalid", object_id=object_id)
                slot_by_object[object_id] = backing.slot_id
            else:
                raise MeshIrError("E_ABI_BOUNDS", "object backing type is invalid", object_id=object_id)
        if set(allocation_by_object.values()) != set(allocations) or len(allocation_by_object) != len(allocations):
            raise MeshIrError("E_ABI_BOUNDS", "local allocations do not map one-to-one inside their variant", variant_id=variant.variant_id)
        slot_records = _slice(semantics.binding_slots, membership.binding_slots)
        slots = {item.slot_id: item for item in slot_records}
        if len(slots) != len(slot_records) or set(slot_by_object.values()) != set(slots) or len(slot_by_object) != len(slots):
            raise MeshIrError("E_ABI_BOUNDS", "external slots do not map one-to-one inside their variant", variant_id=variant.variant_id)
        if slot_records:
            resolve_addresses(arch, slot_records, tuple(item.reference_binding for item in slot_records), (), issuing_core=arch.core_ids[0])
        written_objects = {views[item.view_id].object_id for op in ops.values() for item in op.writes}
        for object_id, slot_id in slot_by_object.items():
            obj = objects[object_id]
            slot = slots[slot_id]
            if slot.memory_space is not obj.memory_space or slot.owner_core != obj.owner_core or slot.required_allocation_bytes < obj.footprint_bytes or slot.required_allocation_alignment_bytes < obj.alignment_bytes or object_id in written_objects and slot.access is not Access.READ_WRITE:
                raise MeshIrError("E_ABI_BOUNDS", "external slot is not the object's exact physical backing", object_id=object_id, slot_id=slot_id)
        relocation_records = _slice(transport.relocations, membership.relocations)
        relocation_ids = {item.relocation_id for item in relocation_records}
        if len(relocation_records) != len(slot_by_object) or relocation_ids != set(slot_by_object.values()):
            raise MeshIrError("E_RELOCATION", "relocations do not exactly cover external slots", variant_id=variant.variant_id)
        for object_id, slot_id in slot_by_object.items():
            relocation = relocations_by_id[slot_id]
            if not 1 <= relocation.symbol_sid <= len(transport.strings):
                raise MeshIrError("E_RELOCATION", "relocation symbol is outside the string table", relocation_id=relocation.relocation_id)
            symbol = transport.strings[relocation.symbol_sid - 1]
            if type(symbol.value) is not str or symbol.value != slots[slot_id].symbol:
                raise MeshIrError("E_RELOCATION", "relocation symbol is not the external slot projection", relocation_id=relocation.relocation_id)
            expected = abi_projection_for_reference_relocation(
                objects[object_id],
                slots[slot_id],
                relocation.symbol_sid,
            )
            if relocation != expected:
                raise MeshIrError("E_RELOCATION", "relocation is not the external slot projection", relocation_id=relocation.relocation_id)
        lifetime = _analyze_verified_lifetimes(records, verified_memory.dependencies)
        conflicts = {tuple(sorted((item.first_object_id, item.second_object_id))) for item in lifetime.conflicts}
        allocation_items = tuple(allocations.values())
        object_by_allocation = {allocation_id: object_id for object_id, allocation_id in allocation_by_object.items()}
        for index, left in enumerate(allocation_items):
            left_end = checked_add_u64(left.offset_bytes, left.size_bytes, "allocation overlap end")
            for right in allocation_items[index + 1:]:
                right_end = checked_add_u64(right.offset_bytes, right.size_bytes, "allocation overlap end")
                if left.owner_core != right.owner_core or max(left.offset_bytes, right.offset_bytes) >= min(left_end, right_end):
                    continue
                left_object = object_by_allocation[left.allocation_id] - membership.objects.first_id + 1
                right_object = object_by_allocation[right.allocation_id] - membership.objects.first_id + 1
                if tuple(sorted((left_object, right_object))) in conflicts:
                    raise MeshIrError("E_SRAM_OOM", "overlapping local allocations have conflicting lifetimes", first_allocation_id=left.allocation_id, second_allocation_id=right.allocation_id)
        resident_by_view = {}
        for runtime_id in range(membership.runtime_shards.first_id, membership.runtime_shards.first_id + membership.runtime_shards.count):
            resident = resident_by_runtime.get(runtime_id)
            abi = runtime_shards.get(runtime_id)
            if resident is None or abi is None or resident.view_id not in views:
                raise MeshIrError("E_ABI_BOUNDS", "resident view is missing or crosses its variant", runtime_shard_id=runtime_id)
            resident_by_view[resident.view_id] = runtime_id
            view = views[resident.view_id]
            obj = objects.get(view.object_id)
            shard = logical_shards.get(view.shard_id)
            if obj is None or shard is None or shard.tensor_id not in tensors:
                raise MeshIrError("E_ABI_BOUNDS", "resident view source is outside its variant", runtime_shard_id=runtime_id)
            tensor = tensors[shard.tensor_id]
            expected = (
                shard.tensor_id,
                obj.owner_core,
                len(shard.padded_local_shape),
                tuple(shard.global_origin) + (0,) * (8 - len(shard.global_origin)),
                tuple(view.padded_shape) + (0,) * (8 - len(view.padded_shape)),
                tuple(view.valid_shape) + (0,) * (8 - len(view.valid_shape)),
                allocation_by_object.get(obj.object_id, 0),
                checked_mul_u64(view.object_offset_elements, tensor.dtype.byte_width, "resident view byte offset"),
                obj.footprint_bytes,
            )
            actual_fields = (abi.tensor_id, abi.owner_core, abi.rank, abi.global_origin, abi.local_shape, abi.valid_shape, abi.allocation_id, abi.allocation_offset, abi.span_bytes)
            if any(type(value) is not int for value in (abi.tensor_id, abi.owner_core, abi.rank, abi.allocation_id, abi.allocation_offset, abi.span_bytes)) or type(abi.global_origin) is not tuple or type(abi.local_shape) is not tuple or type(abi.valid_shape) is not tuple or any(type(value) is not int for values in (abi.global_origin, abi.local_shape, abi.valid_shape) for value in values) or actual_fields != expected:
                raise MeshIrError("E_ABI_BOUNDS", "runtime shard is not the resident view projection", runtime_shard_id=runtime_id)
        if set(resident_by_view) != set(views):
            raise MeshIrError("E_ABI_BOUNDS", "resident views do not exactly cover variant views", variant_id=variant.variant_id)
        for command in _slice(transport.commands, membership.commands):
            fields = (command.operand_begin, command.operand_count, command.source_op_id)
            if any(type(value) is not int for value in fields) or command.operand_begin != operand_cursor or command.operand_count < 0 or checked_add_u64(command.operand_begin, command.operand_count, "command operand span") > len(transport.command_operands):
                raise MeshIrError("E_ABI_BOUNDS", "command operand span is not canonical", command_id=command.command_id)
            semantic = command_semantics.get(command.command_id)
            if semantic is None:
                raise MeshIrError("E_ABI_BOUNDS", "command semantics are outside their variant", command_id=command.command_id)
            expected_operands = []
            if type(semantic.source) is KernelCommandSource:
                if type(semantic.source.kernel_op_id) is not int or semantic.source.kernel_op_id not in ops or command.source_op_id != semantic.source.kernel_op_id:
                    raise MeshIrError("E_ABI_BOUNDS", "command Kernel source is outside its variant", command_id=command.command_id)
                op = ops[semantic.source.kernel_op_id]
                for access, permission in tuple((item, Access.READ_ONLY) for item in op.reads) + tuple((item, Access.READ_WRITE) for item in op.writes):
                    view = views.get(access.view_id)
                    if view is None or view.object_id not in objects or view.shard_id not in logical_shards or view.view_id not in resident_by_view:
                        raise MeshIrError("E_ABI_BOUNDS", "command access projection crosses its variant", command_id=command.command_id)
                    obj = objects[view.object_id]
                    expected_operands.append(CommandOperand(logical_shards[view.shard_id].tensor_id, resident_by_view[view.view_id], allocation_by_object.get(obj.object_id, 0), int(permission)))
            elif command.source_op_id != 0:
                raise MeshIrError("E_ABI_BOUNDS", "control command has a Kernel source operation", command_id=command.command_id)
            actual_operands = transport.command_operands[command.operand_begin:command.operand_begin + command.operand_count]
            if any(type(item) is not CommandOperand or type(item.tensor_id) is not int or type(item.shard_id) is not int or type(item.allocation_id) is not int or type(item.access) is not int or type(item.reserved) is not int for item in actual_operands) or actual_operands != tuple(expected_operands):
                raise MeshIrError("E_ABI_BOUNDS", "command operands are not the exact ordered access projection", command_id=command.command_id)
            operand_cursor += command.operand_count
    if operand_cursor != len(transport.command_operands):
        raise MeshIrError("E_ABI_BOUNDS", "command operand spans do not cover the operand table")


def _verify_repeats(semantics: ProgramSemantics | _PreTrafficSemantics, transport: _TransportSections) -> None:
    commands = {item.command_id: item for item in transport.commands}
    semantic_by_id = {item.command_id: item for item in semantics.command_semantics}
    ops = {item.op_id: item for item in semantics.kernel_ops}
    states = {item.state_id: item for item in semantics.states}
    command_stream = {}
    command_ordinal = {}
    bodies = set()
    forbidden = {A.OPCODE.HALT, A.OPCODE.REQUEST_BEGIN, A.OPCODE.REQUEST_END, A.OPCODE.BARRIER, A.OPCODE.DMA_P2P_PUSH, A.OPCODE.RECV_WAIT, A.OPCODE.REPEAT}
    for stream in semantics.streams:
        members = semantics.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]
        for member_ordinal, command_id in enumerate(members):
            command_stream[command_id] = stream.stream_id
            command_ordinal[command_id] = member_ordinal
        for ordinal, command_id in enumerate(members):
            semantic = semantic_by_id[command_id]
            attrs = semantic.source.attrs if type(semantic.source) is ControlCommandSource else None
            if type(attrs) is not RepeatCommandAttrs:
                if commands[command_id].opcode == A.OPCODE.REPEAT:
                    raise MeshIrError("E_ABI_ENUM", "REPEAT command lacks typed repeat semantics", command_id=command_id)
                continue
            values = (attrs.subrange_begin_stream_ordinal, attrs.subrange_command_count, attrs.repeat_count)
            if any(type(value) is not int for value in values) or not 0 <= values[0] <= 0xFFFFFFFF or not 1 <= values[1] <= 0xFFFFFFFF or not 1 <= values[2] <= 0xFFFFFFFF:
                raise MeshIrError("E_ABI_BOUNDS", "REPEAT fields must be exact bounded integers", command_id=command_id)
            if attrs.subrange_begin_stream_ordinal + attrs.subrange_command_count != ordinal:
                raise MeshIrError("E_ABI_BOUNDS", "REPEAT body must immediately precede its command", command_id=command_id)
            body = members[attrs.subrange_begin_stream_ordinal:ordinal]
            if any(commands[item].opcode in forbidden for item in body) or any(item in bodies for item in body):
                raise MeshIrError("E_ABI_BOUNDS", "REPEAT body contains a forbidden or overlapping command", command_id=command_id)
            checked_mul_u64(attrs.subrange_command_count, attrs.repeat_count, "REPEAT logical command executions")
            bodies.update(body)
            body_set = set(body)
            body_ops = tuple(ops[semantic_by_id[item].source.kernel_op_id] for item in body if type(semantic_by_id[item].source) is KernelCommandSource)
            produced_states = {write.new_state_id for op in body_ops for write in op.writes}
            written_objects = {states[write.new_state_id].object_id for op in body_ops for write in op.writes}
            for object_id in written_objects:
                transitions = tuple(write for op in body_ops for write in op.writes if states[write.new_state_id].object_id == object_id)
                roots = {write.old_state_id for write in transitions if write.old_state_id not in produced_states}
                if len(roots) != 1:
                    raise MeshIrError("E_ABI_BOUNDS", "REPEAT writable object lacks one generation-local root", command_id=command_id, object_id=object_id)
                root = states[next(iter(roots))]
                if root.object_id != object_id or root.version != 0 or root.origin is not StateOrigin.EMPTY:
                    raise MeshIrError("E_ABI_BOUNDS", "REPEAT writable object root is not an empty version-zero state", command_id=command_id, object_id=object_id)
                if any(read.state_id not in produced_states for op in body_ops for read in op.reads if states[read.state_id].object_id == object_id):
                    raise MeshIrError("E_ABI_BOUNDS", "REPEAT reads mutable state from outside its generation", command_id=command_id, object_id=object_id)
            for op in body_ops:
                for read in op.reads:
                    state = states[read.state_id]
                    if state.object_id not in written_objects and state.origin not in (StateOrigin.EXTERNAL, StateOrigin.PRE_RESIDENT):
                        raise MeshIrError("E_ABI_BOUNDS", "REPEAT reuses a mutable external state", command_id=command_id, state_id=state.state_id)
            for other_semantic in semantics.command_semantics:
                if other_semantic.command_id in body_set or type(other_semantic.source) is not KernelCommandSource:
                    continue
                other_op = ops[other_semantic.source.kernel_op_id]
                used_states = {read.state_id for read in other_op.reads} | {write.old_state_id for write in other_op.writes}
                used_tokens = set(other_op.after_tokens)
                body_tokens = {op.done_token for op in body_ops if op.done_token is not None}
                if used_states & produced_states or used_tokens & body_tokens:
                    if command_stream.get(other_semantic.command_id) != stream.stream_id or command_ordinal[other_semantic.command_id] <= ordinal:
                        raise MeshIrError("E_ABI_BOUNDS", "REPEAT body result is not consumed as a final-generation same-stream value", command_id=command_id, consumer_command_id=other_semantic.command_id)
            signaled = {commands[item].signal_event for item in body if commands[item].signal_event}
            for other in transport.commands:
                if other.command_id in body:
                    continue
                waits = transport.command_waits[other.wait_begin:other.wait_begin + other.wait_count]
                if not any(wait.event_id in signaled for wait in waits):
                    continue
                if command_stream.get(other.command_id) != stream.stream_id:
                    raise MeshIrError("E_ABI_BOUNDS", "REPEAT body event escapes to another stream", command_id=command_id)
                if other.command_id != command_id and command_ordinal[other.command_id] <= ordinal:
                    raise MeshIrError("E_ABI_BOUNDS", "REPEAT body event is not consumed as a final-generation same-stream event", command_id=command_id, consumer_command_id=other.command_id)
            repeat_waits = {item.event_id for item in transport.command_waits[commands[command_id].wait_begin:commands[command_id].wait_begin + commands[command_id].wait_count]}
            body_waits = {
                wait.event_id
                for item in body
                for wait in transport.command_waits[commands[item].wait_begin:commands[item].wait_begin + commands[item].wait_count]
            }
            maximal_events = {
                commands[item].signal_event
                for item in body
                if commands[item].signal_event and commands[item].signal_event not in body_waits
            }
            if any(not commands[item].signal_event for item in body if commands[item].signal_event not in body_waits) or not maximal_events <= repeat_waits or not commands[command_id].signal_event:
                raise MeshIrError("E_ABI_BOUNDS", "REPEAT does not drain every generation before completion", command_id=command_id)


def _verify_barriers(semantics: ProgramSemantics | _PreTrafficSemantics, transport: _TransportSections, arch: ArchManifest) -> None:
    if tuple(item.barrier_group_id for item in semantics.barrier_groups) != tuple(range(1, len(semantics.barrier_groups) + 1)):
        raise MeshIrError("E_ABI_ORDER", "barrier group IDs must be dense and canonical")
    if len({item.completion_event_id for item in semantics.barrier_groups}) != len(semantics.barrier_groups):
        raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "barrier groups must own distinct completion events")
    events = {item.event_id: item for item in transport.events}
    commands = {item.command_id: item for item in transport.commands}
    ops = {item.op_id: item for item in semantics.kernel_ops}
    command_semantics = {item.command_id: item for item in semantics.command_semantics}
    ranks = {core_id: index for index, core_id in enumerate(arch.core_ids)}
    seen_commands = set()
    seen_ops = set()
    seen_tokens = set()
    for group in semantics.barrier_groups:
        if type(group) is not BarrierGroup or type(group.participants) is not tuple or not group.participants or any(type(core) is not int or core not in ranks for core in group.participants) or group.participants != tuple(sorted(set(group.participants), key=ranks.__getitem__)):
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "barrier participants are not canonical hardware cores", barrier_group_id=group.barrier_group_id)
        event = events.get(group.completion_event_id)
        if event is None or event.kind != A.EVENT_KIND.BARRIER or event.producer_command_id != 0:
            raise MeshIrError("E_EVENT_NO_PRODUCER", "barrier completion event projection is invalid", barrier_group_id=group.barrier_group_id)
        if event.expected_arrivals != len(group.participants):
            raise MeshIrError("E_ABI_BOUNDS", "barrier completion event arrival count is invalid", barrier_group_id=group.barrier_group_id)
        if type(group.arrivals) is not tuple or tuple(item.participant_core for item in group.arrivals) != group.participants:
            raise MeshIrError("E_ABI_BOUNDS", "barrier arrivals do not exactly cover participants", barrier_group_id=group.barrier_group_id)
        for arrival in group.arrivals:
            if type(arrival.participant_core) is not int or arrival.command_id in seen_commands or arrival.kernel_op_id in seen_ops or arrival.done_token_id in seen_tokens:
                raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "barrier arrival identity is duplicated", barrier_group_id=group.barrier_group_id)
            command = commands.get(arrival.command_id)
            op = ops.get(arrival.kernel_op_id)
            semantic = command_semantics.get(arrival.command_id)
            if command is None or command.opcode != A.OPCODE.BARRIER or command.core_id != arrival.participant_core or command.signal_event != group.completion_event_id:
                raise MeshIrError("E_ABI_BOUNDS", "barrier command projection is invalid", barrier_group_id=group.barrier_group_id)
            if op is None or op.opcode is not KernelOpcode.BARRIER or op.owner_core != arrival.participant_core or op.done_token != arrival.done_token_id or type(op.attrs) is not BarrierAttrs or op.attrs.participants != group.participants:
                raise MeshIrError("E_ABI_BOUNDS", "barrier Kernel operation association is invalid", barrier_group_id=group.barrier_group_id)
            if type(semantic.source) is not KernelCommandSource or semantic.source.kernel_op_id != arrival.kernel_op_id or type(semantic.execution) is not BarrierExecution or semantic.execution.barrier_group_id != group.barrier_group_id:
                raise MeshIrError("E_ABI_BOUNDS", "barrier command semantics association is invalid", barrier_group_id=group.barrier_group_id)
            seen_commands.add(arrival.command_id)
            seen_ops.add(arrival.kernel_op_id)
            seen_tokens.add(arrival.done_token_id)
    barrier_commands = {item.command_id for item in transport.commands if item.opcode == A.OPCODE.BARRIER}
    barrier_ops = {item.op_id for item in semantics.kernel_ops if item.opcode is KernelOpcode.BARRIER}
    if seen_commands != barrier_commands or seen_ops != barrier_ops:
        raise MeshIrError("E_ABI_BOUNDS", "barrier group associations are incomplete")


def _verify_state(state: _PreTrafficState, arch: ArchManifest) -> None:
    validate_arch(arch)
    if type(state) is not _PreTrafficState or type(state.transport) is not _TransportSections or type(state.semantics) is not _PreTrafficSemantics:
        raise MeshIrError("E_ABI_BOUNDS", "pre-traffic state type is invalid")
    if type(state.abi_major) is not int or type(state.abi_minor) is not int or type(state.arch_digest) is not bytes or state.arch_digest != arch.digest():
        raise MeshIrError("E_ARCH_DIGEST", "pre-traffic architecture identity is invalid")
    transport_types = (("strings", StringEntry), ("entrypoints", Entrypoint), ("profiles", Profile), ("tensors", Tensor), ("shards", Shard), ("allocations", Allocation), ("streams", Stream), ("commands", Command), ("command_waits", CommandWait), ("command_operands", CommandOperand), ("events", Event), ("dma_descriptors", DmaDescriptor), ("op_attrs", OpAttr), ("relocations", Relocation))
    for field, cls in transport_types:
        _exact_tuple(getattr(state.transport, field), cls, field)
    command_ids = tuple(item.command_id for item in state.transport.commands)
    if any(type(item) is not int for item in command_ids):
        raise MeshIrError("E_ABI_ORDER", "command identities must be exact integers")
    if len(set(command_ids)) != len(command_ids):
        raise MeshIrError("E_ABI_DUPLICATE", "command identities must be unique")
    if command_ids != tuple(range(1, len(command_ids) + 1)):
        raise MeshIrError("E_ABI_ORDER", "command identities must be dense and canonical")
    semantic_types = (("variants", ProgramVariant), ("kernel_tensors", KernelTensor), ("computations", KernelComputation), ("placements", Placement), ("logical_shards", TensorShard), ("partial_sums", PartialSumDefinition), ("objects", BufferObject), ("views", BufferView), ("states", TensorState), ("tokens", ControlToken), ("kernel_ops", KernelOp), ("object_backings", ObjectBacking), ("command_semantics", CommandSemantics), ("barrier_groups", BarrierGroup), ("dependencies", ScheduledDependency), ("streams", ScheduledStream), ("descriptor_groups", DescriptorGroup), ("endpoint_uses", DescriptorEndpointUse), ("binding_slots", BindingSlot), ("executions", DescriptorExecution))
    for field, cls in semantic_types:
        _exact_tuple(getattr(state.semantics, field), cls, field)
    _validate_origin(state.semantics.origin)
    _verify_variants(state.semantics, state.transport)
    _verify_streams(state.semantics, state.transport, arch)
    verified_memories = tuple(verify_kernel_memory(_local_memory_records(state.semantics, variant), arch) for variant in state.semantics.variants)
    _verify_commands(state.semantics, state.transport, verified_memories, arch)
    _verify_descriptor_coverage(state.semantics, state.transport, arch)
    _verify_physical_projection(state.semantics, state.transport, arch, verified_memories)
    _verify_barriers(state.semantics, state.transport, arch)
    _verify_dependencies_and_lifecycle(state.semantics, state.transport)
    _verify_repeats(state.semantics, state.transport)


def verify_pretraffic_state(state: _PreTrafficState, arch: ArchManifest) -> _VerifiedPreTrafficState:
    _verify_state(state, arch)
    return _VerifiedPreTrafficState(state)


def verify_compiled_pretraffic_state(state: _PreTrafficState, arch: ArchManifest, lowering, effective) -> _VerifiedPreTrafficState:
    from mesh_ir.compile_config import EffectiveCompileConfig, validate_effective_compile_config
    from mesh_ir.passes.addresses import bind_addresses_and_relocations_stage
    from mesh_ir.passes.graph_to_kernel import KernelLoweringResult
    from mesh_ir.passes.hazards import insert_hazard_dependencies_stage
    from mesh_ir.passes.schedule import schedule_per_core_streams_stage
    from mesh_ir.passes.segments import lower_dma_to_segments_stage
    from mesh_ir.passes.static_sram import plan_static_sram_stage

    if type(lowering) is not KernelLoweringResult or type(effective) is not EffectiveCompileConfig:
        raise MeshIrError("E_CONFIG", "compiled pre-traffic verification context is invalid")
    validate_effective_compile_config(effective, arch)
    static = plan_static_sram_stage(lowering.bundle, arch)
    hazards = insert_hazard_dependencies_stage(static)
    schedule = schedule_per_core_streams_stage(hazards, arch)
    segments = lower_dma_to_segments_stage(schedule)
    expected = bind_addresses_and_relocations_stage(segments, arch)
    if canonical_json_bytes(state) != canonical_json_bytes(expected):
        raise MeshIrError("E_ABI_CHECKSUM", "pre-traffic state differs from the compiled stage derivation")
    return verify_pretraffic_state(state, arch)


def verify_program(program: Program, arch: ArchManifest) -> VerifiedProgram:
    if type(program) is not Program or type(program.semantics) is not ProgramSemantics:
        raise MeshIrError("E_ABI_BOUNDS", "complete Program type is invalid")
    if type(program.semantic_sha256) is not str or _DIGEST.fullmatch(program.semantic_sha256) is None or program.semantic_sha256 != semantic_sha256(program.semantic_dict()):
        raise MeshIrError("E_ABI_CHECKSUM", "Program semantic checksum is stale")
    semantics = program.semantics
    transport = _TransportSections(program.strings, program.entrypoints, program.profiles, program.tensors, program.shards, program.allocations, program.streams, program.commands, program.command_waits, program.command_operands, program.events, program.dma_descriptors, program.op_attrs, program.relocations)
    state = _PreTrafficState(program.abi_major, program.abi_minor, program.arch_digest, transport, _PreTrafficSemantics(semantics.origin, semantics.variants, semantics.kernel_tensors, semantics.computations, semantics.placements, semantics.logical_shards, semantics.partial_sums, semantics.objects, semantics.views, semantics.states, semantics.tokens, semantics.kernel_ops, semantics.object_backings, semantics.resident_views, semantics.command_semantics, semantics.barrier_groups, semantics.dependencies, semantics.streams, semantics.stream_command_ids, semantics.descriptor_groups, semantics.endpoint_uses, semantics.binding_slots, semantics.reference_binding_identity_sha256, ()))
    _verify_state(state, arch)
    executions: tuple[DescriptorExecution, ...]
    if program.dma_descriptors:
        from mesh_ir.scheduled.addressing import derive_descriptor_executions
        executions = derive_descriptor_executions(program, arch)
    else:
        executions = ()
    if type(semantics.intrinsic_traffic) is not TrafficReport:
        raise MeshIrError("E_TRAFFIC_MISMATCH", "Program intrinsic traffic type is invalid")
    verify_traffic_report(arch, semantics.reference_binding_identity_sha256, executions, semantics.intrinsic_traffic)
    if type(program.expected_traffic) is not tuple or any(type(item) is not ExpectedTrafficRow for item in program.expected_traffic):
        raise MeshIrError("E_TRAFFIC_MISMATCH", "ABI traffic projection is invalid")
    from mesh_ir.scheduled.assemble import _expected_traffic
    expected_traffic = _expected_traffic(semantics.intrinsic_traffic, executions, arch)
    if canonical_json_bytes(program.expected_traffic) != canonical_json_bytes(expected_traffic):
        raise MeshIrError("E_TRAFFIC_MISMATCH", "ABI traffic rows differ from intrinsic traffic")
    return VerifiedProgram(program, arch, MappingProxyType({item.command_id: item for item in program.commands}), MappingProxyType({(item.entrypoint_id, item.profile_id): item for item in semantics.variants}))


def verify_program_kernel_correspondence(program: Program, bundle: KernelBundle) -> None:
    if type(program) is not Program or type(program.semantics) is not ProgramSemantics or type(bundle) is not KernelBundle:
        raise MeshIrError("E_ABI_BOUNDS", "compiled correspondence inputs have invalid record types")
    if type(program.semantic_sha256) is not str or _DIGEST.fullmatch(program.semantic_sha256) is None or program.semantic_sha256 != semantic_sha256(program.semantic_dict()):
        raise MeshIrError("E_ABI_CHECKSUM", "Program semantic checksum is stale")
    bundle.verify()
    semantics = program.semantics
    if type(semantics.origin) is not CompiledProgramOrigin or semantics.origin.kernel_bundle_semantic_sha256 != bundle.semantic_sha256 or len(semantics.variants) != len(bundle.modules):
        raise MeshIrError("E_ABI_CHECKSUM", "Program compiled origin differs from Kernel bundle")
    ordinals = []
    for variant in semantics.variants:
        lineage = variant.lineage
        if type(lineage) is not CompiledVariantLineage or type(lineage.kernel_module_ordinal) is not int or not 0 <= lineage.kernel_module_ordinal < len(bundle.modules):
            raise MeshIrError("E_ABI_CHECKSUM", "Program variant lineage is invalid", variant_id=variant.variant_id)
        module = bundle.modules[lineage.kernel_module_ordinal]
        if lineage.kernel_module_semantic_sha256 != module.semantic_sha256 or canonical_json_bytes(_local_memory_records(semantics, variant)) != canonical_json_bytes(module.memory_records()):
            raise MeshIrError("E_ABI_CHECKSUM", "Program variant differs from its Kernel module", variant_id=variant.variant_id)
        ordinals.append(lineage.kernel_module_ordinal)
    if tuple(ordinals) != tuple(range(len(bundle.modules))):
        raise MeshIrError("E_ABI_CHECKSUM", "Program variant lineage order differs from Kernel bundle")


__all__ = ["VerifiedProgram", "verify_compiled_pretraffic_state", "verify_pretraffic_state", "verify_program", "verify_program_kernel_correspondence"]

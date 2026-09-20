from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.analysis.cost import ExecutionWorkPhase
from mesh_ir.analysis.kernel_work import _kernel_op_work_phases_verified
from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import DmaKind, Engine
from mesh_ir.ir.kernel_ir import KernelOpcode
from mesh_ir.ir.kernel_verify import verify_kernel_memory
from mesh_ir.passes.hazards import _HazardState
from mesh_ir.passes.static_sram import _AuthoredVariantSource
from mesh_ir.scheduled.model import AxiFenceAttrs, ControlCommandSource, EventSignalAttrs, EventWaitAttrs, FenceScope, HaltAttrs, KernelCommandSource, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs


@dataclass(frozen=True)
class _ScheduledCommand:
    command_ordinal: int
    source: KernelCommandSource | ControlCommandSource
    owner_core: int
    engine: Engine
    phases: tuple[ExecutionWorkPhase, ...]


@dataclass(frozen=True)
class _ScheduledStream:
    owner_core: int
    physical_stream_id: int
    flags: int
    command_ordinals: tuple[int, ...]


@dataclass(frozen=True)
class _ScheduledVariant:
    variant_ordinal: int
    commands: tuple[_ScheduledCommand, ...]
    streams: tuple[_ScheduledStream, ...]


@dataclass(frozen=True)
class _ScheduleState:
    hazards: _HazardState
    variants: tuple[_ScheduledVariant, ...]

    @property
    def semantic_sha256(self) -> str:
        return semantic_sha256(self)


def _engine(op, phases) -> Engine:
    if op.opcode is KernelOpcode.DMA:
        return Engine.DMA_READ if op.attrs.kind in (DmaKind.LOAD, DmaKind.PREFETCH) else Engine.DMA_WRITE
    if op.opcode in (KernelOpcode.RECV_WAIT, KernelOpcode.BARRIER):
        return Engine.CONTROL
    return phases[0].engine if phases else Engine.CONTROL


def schedule_per_core_streams_stage(hazards: _HazardState, arch: ArchManifest) -> _ScheduleState:
    if type(hazards) is not _HazardState:
        raise MeshIrError("E_ABI_BOUNDS", "pass 17 requires the exact hazard state")
    validate_arch(arch)
    variants = []
    for ordinal, variant in enumerate(hazards.static.variants):
        source = variant.source
        verified_memory = verify_kernel_memory(source.records, arch)
        records = verified_memory.records
        dependency = verified_memory.dependencies
        work_phases = tuple(_kernel_op_work_phases_verified(records, op.op_id) for op in records.ops)
        order = dependency.graph.canonical_topological_order()
        op_by_node = {node: op_id for op_id, node in enumerate(dependency.op_node_by_id, 1) if node}
        ordered_ops = tuple(op_by_node[node] for node in order if node in op_by_node)
        effect_ops = {op_id: records.ops[op_id - 1] for op_id in ordered_ops}
        if type(source) is _AuthoredVariantSource:
            commands = []
            streams = []
            seen_ops = set()
            event_producers = set()
            event_waits = set()
            ordered_streams = sorted(source.streams, key=lambda item: (arch.core_ids.index(item.core_id), item.physical_stream_id))
            for stream in ordered_streams:
                members = []
                for item in stream.sources:
                    if type(item) is KernelCommandSource:
                        op = effect_ops.get(item.kernel_op_id)
                        if op is None or op.owner_core != stream.core_id or item.kernel_op_id in seen_ops:
                            raise MeshIrError("E_STREAM_CONTRACT", "authored Kernel command is absent, duplicated, or assigned to the wrong stream", kernel_op_id=item.kernel_op_id)
                        seen_ops.add(item.kernel_op_id)
                        phases = work_phases[item.kernel_op_id - 1]
                        engine = _engine(op, phases)
                    elif type(item) is ControlCommandSource and type(item.attrs) in (RequestBeginAttrs, RequestEndAttrs, HaltAttrs, RepeatCommandAttrs, AxiFenceAttrs, EventWaitAttrs, EventSignalAttrs):
                        phases = ()
                        engine = Engine.CONTROL
                        if type(item.attrs) is RepeatCommandAttrs:
                            values = (item.attrs.subrange_begin_stream_ordinal, item.attrs.subrange_command_count, item.attrs.repeat_count)
                            if any(type(value) is not int for value in values) or not 0 <= values[0] <= 0xFFFFFFFF or not 1 <= values[1] <= 0xFFFFFFFF or not 1 <= values[2] <= 0xFFFFFFFF:
                                raise MeshIrError("E_ABI_BOUNDS", "authored REPEAT fields are invalid")
                        elif type(item.attrs) is AxiFenceAttrs and type(item.attrs.scope) is not FenceScope:
                            raise MeshIrError("E_ABI_ENUM", "authored AXI fence scope is invalid")
                        if type(item.attrs) in (EventWaitAttrs, EventSignalAttrs):
                            if type(item.attrs.event_id) is not int or not 1 <= item.attrs.event_id <= 0xFFFFFFFF:
                                raise MeshIrError("E_EVENT_NO_PRODUCER", "authored event symbol is outside positive u32")
                            if type(item.attrs) is EventSignalAttrs:
                                if item.attrs.event_id in event_producers:
                                    raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "authored event symbol has multiple producers", event_id=item.attrs.event_id)
                                event_producers.add(item.attrs.event_id)
                            else:
                                event_waits.add(item.attrs.event_id)
                    else:
                        raise MeshIrError("E_STREAM_CONTRACT", "authored command source is invalid")
                    command = _ScheduledCommand(len(commands) + 1, item, stream.core_id, engine, phases)
                    commands.append(command)
                    members.append(command.command_ordinal)
                streams.append(_ScheduledStream(stream.core_id, stream.physical_stream_id, stream.flags, tuple(members)))
            if seen_ops != set(effect_ops):
                raise MeshIrError("E_STREAM_CONTRACT", "authored streams do not exactly cover effectful Kernel operations")
            if not event_waits <= event_producers:
                raise MeshIrError("E_EVENT_NO_PRODUCER", "authored event wait has no signal source", event_ids=tuple(sorted(event_waits - event_producers)))
            lifecycle = tuple(item for item in streams if item.flags & A.STREAM_FLAGS.IS_LIFECYCLE)
            if len(lifecycle) != 1 or any(type(item.flags) is not int or item.flags < 0 or item.flags & ~(A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL) for item in streams):
                raise MeshIrError("E_STREAM_CONTRACT", "authored variant requires one exact lifecycle stream")
            command_by_ordinal = {item.command_ordinal: item for item in commands}
            begin = tuple(item for item in commands if type(item.source) is ControlCommandSource and type(item.source.attrs) is RequestBeginAttrs)
            end = tuple(item for item in commands if type(item.source) is ControlCommandSource and type(item.source.attrs) is RequestEndAttrs)
            halts = tuple(item for item in commands if type(item.source) is ControlCommandSource and type(item.source.attrs) is HaltAttrs)
            lifecycle_commands = tuple(command_by_ordinal[item] for item in lifecycle[0].command_ordinals)
            participant_cores = {item.owner_core for item in streams}
            end_position = lifecycle_commands.index(end[0]) if len(end) == 1 and end[0] in lifecycle_commands else -1
            if len(begin) != 1 or len(end) != 1 or not lifecycle_commands or lifecycle_commands[0] is not begin[0] or end_position < 1 or any(type(item.source) is not ControlCommandSource or type(item.source.attrs) is not HaltAttrs for item in lifecycle_commands[end_position + 1:]) or len(halts) != len(participant_cores) or {item.owner_core for item in halts} != participant_cores:
                raise MeshIrError("E_LIFECYCLE", "authored lifecycle commands are incomplete or misplaced")
            for halt in halts:
                owner_streams = tuple(item for item in streams if halt.command_ordinal in item.command_ordinals)
                if len(owner_streams) != 1 or owner_streams[0].command_ordinals[-1] != halt.command_ordinal:
                    raise MeshIrError("E_LIFECYCLE", "authored HALT must terminate its physical stream", owner_core=halt.owner_core)
        else:
            participants = tuple(sorted({item.owner_core for item in effect_ops.values()}, key=arch.core_ids.index)) or (arch.core_ids[0],)
            commands = [_ScheduledCommand(1, ControlCommandSource(RequestBeginAttrs()), participants[0], Engine.CONTROL, ())]
            work_ordinal_by_op = {}
            for op_id in ordered_ops:
                op = effect_ops[op_id]
                phases = work_phases[op_id - 1]
                command = _ScheduledCommand(len(commands) + 1, KernelCommandSource(op_id), op.owner_core, _engine(op, phases), phases)
                commands.append(command)
                work_ordinal_by_op[op_id] = command.command_ordinal
            end = _ScheduledCommand(len(commands) + 1, ControlCommandSource(RequestEndAttrs()), participants[0], Engine.CONTROL, ())
            commands.append(end)
            halt_ordinals = {}
            for core in participants:
                halt = _ScheduledCommand(len(commands) + 1, ControlCommandSource(HaltAttrs()), core, Engine.CONTROL, ())
                commands.append(halt)
                halt_ordinals[core] = halt.command_ordinal
            lifecycle_flags = A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL
            streams = [_ScheduledStream(participants[0], 0, lifecycle_flags, (1, end.command_ordinal, halt_ordinals[participants[0]]))]
            streams.extend(_ScheduledStream(core, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL, (halt_ordinals[core],)) for core in participants[1:])
            grouped = {}
            for op_id in ordered_ops:
                command = commands[work_ordinal_by_op[op_id] - 1]
                grouped.setdefault((command.owner_core, command.engine), []).append(command.command_ordinal)
            next_physical = {core: 1 for core in participants}
            for (core, engine), members in sorted(grouped.items(), key=lambda item: (arch.core_ids.index(item[0][0]), int(item[0][1]))):
                streams.append(_ScheduledStream(core, next_physical[core], 0, tuple(members)))
                next_physical[core] += 1
            streams.sort(key=lambda item: (arch.core_ids.index(item.owner_core), item.physical_stream_id))
        variants.append(_ScheduledVariant(ordinal, tuple(commands), tuple(streams)))
    return _ScheduleState(hazards, tuple(variants))

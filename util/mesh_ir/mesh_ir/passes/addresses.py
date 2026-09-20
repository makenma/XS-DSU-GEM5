from __future__ import annotations

from dataclasses import replace

from mesh_ir.architecture import ArchManifest
from mesh_ir.canonical import checked_add_u64, checked_mul_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, Engine, INVALID_CORE_ID, Layout, MemorySpace
from mesh_ir.ir.kernel_ir import BarrierAttrs, DmaAttrs, KernelMemoryRecords, KernelOpcode, RecvWaitAttrs
from mesh_ir.model import Allocation, Command, CommandOperand, CommandWait, DmaDescriptor, DmaEndpoint, Entrypoint, Event, OpAttr, Profile, Shard, Stream, StringEntry, Tensor
from mesh_ir.passes.segments import _SegmentState
from mesh_ir.passes.static_sram import _AuthoredVariantSource
from mesh_ir.scheduled.assemble import _PreTrafficSemantics, _PreTrafficState, _TransportSections
from mesh_ir.scheduled.model import AxiFenceAttrs, BarrierArrival, BarrierExecution, BarrierGroup, CommandSemantics, ComputeExecution, ControlCommandSource, ControlExecution, DescriptorEndpointUse, DescriptorGroup, DescriptorSource, DmaExecution, EndpointSide, EventSignalAttrs, EventWaitAttrs, ExternalSlotBacking, HaltAttrs, IdSpan, KernelCommandSource, KernelTokenSource, LifecycleSource, LocalAllocationBacking, ObjectBacking, ObjectSource, ProgramVariant, ReadAccessUse, RecvWaitExecution, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs, ResidentView, ScheduledDependency, ScheduledDependencyKind, ScheduledStream, StateSource, StreamOrderSource, VariantMembership, WriteAccessUse, _ProgramFacts
from mesh_ir.scheduled.projection import abi_attr_for_kernel_op, abi_opcode_for_kernel_op, abi_projection_for_control, abi_projection_for_reference_relocation, effective_dma_burst_beats
from mesh_ir.scheduled.records import MemoryIdBases, globalize_memory_records
from mesh_ir.traffic import Binding, BindingSlot


def _padded(values: tuple[int, ...]) -> tuple[int, ...]:
    return values + (0,) * (8 - len(values))


def _align_up(value: int, alignment: int) -> int:
    return checked_add_u64(value, alignment - 1, "reference binding alignment") // alignment * alignment


def _region_id(arch: ArchManifest, space: MemorySpace) -> int:
    kind = "HBM" if space is MemorySpace.HBM else "HOST_SHARED" if space is MemorySpace.HOST_SHARED else "CORE_SRAM_APERTURE"
    matches = tuple(index for index, item in enumerate(arch.regions) if item.kind == kind)
    if len(matches) != 1:
        raise MeshIrError("E_CONFIG", "memory space has no unique architecture region", memory_space=space.name)
    return matches[0]


def _source(kind: ScheduledDependencyKind, identity: int):
    if kind is ScheduledDependencyKind.KERNEL_CONTROL:
        return KernelTokenSource(identity)
    if kind is ScheduledDependencyKind.KERNEL_STATE:
        return StateSource(identity)
    if kind in (ScheduledDependencyKind.RAW, ScheduledDependencyKind.WAR, ScheduledDependencyKind.WAW, ScheduledDependencyKind.SRAM_REUSE):
        return ObjectSource(identity)
    if kind in (ScheduledDependencyKind.DMA_PIN, ScheduledDependencyKind.DMA_COMPLETION):
        return DescriptorSource(identity)
    raise MeshIrError("E_ABI_BOUNDS", "dependency kind has no stage source", kind=kind.value)


def _access_offset(records: KernelMemoryRecords, access, region) -> int:
    views = {item.view_id: item for item in records.views}
    shards = {item.shard_id: item for item in records.shards}
    tensors = {item.tensor_id: item for item in records.tensors}
    view = views[access.view_id]
    shard = shards[view.shard_id]
    tensor = tensors[shard.tensor_id]
    element = view.object_offset_elements
    for origin, stride in zip(region.origin, view.object_strides):
        element = checked_add_u64(element, checked_mul_u64(origin, stride, "descriptor endpoint offset"), "descriptor endpoint offset")
    return checked_mul_u64(element, tensor.dtype.byte_width, "descriptor endpoint byte offset")


def bind_addresses_and_relocations_stage(segments: _SegmentState, arch: ArchManifest) -> _PreTrafficState:
    if type(segments) is not _SegmentState:
        raise MeshIrError("E_ABI_BOUNDS", "pass 19 requires the exact segment state")
    static = segments.schedule.hazards.static
    sources = tuple(item.source for item in static.variants)
    strings = []
    string_ids = {}

    def sid(value: str) -> int:
        if value not in string_ids:
            strings.append(StringEntry(value))
            string_ids[value] = len(strings)
        return string_ids[value]

    entrypoint_names = []
    for source in sources:
        if source.entrypoint not in entrypoint_names:
            entrypoint_names.append(source.entrypoint)
    entrypoint_ids = {name: index for index, name in enumerate(entrypoint_names, 1)}
    profiles = tuple(Profile(index, entrypoint_ids[source.entrypoint], sid(source.profile_id), 0) for index, source in enumerate(sources, 1))
    entrypoints = []
    for name in entrypoint_names:
        owned = tuple(item for item in profiles if item.entrypoint_id == entrypoint_ids[name])
        records = sources[owned[0].profile_id - 1].records
        entrypoints.append(Entrypoint(entrypoint_ids[name], sid(name), owned[0].profile_id - 1, len(owned), records.ops[-1].owner_core if records.ops else arch.core_ids[0], 0))
    tensors = []
    runtime_shards = []
    allocations = []
    abi_streams = []
    commands = []
    waits = []
    operands = []
    events = []
    descriptors = []
    op_attrs: list[OpAttr] = []
    relocations = []
    variants = []
    all_records = KernelMemoryRecords((), (), (), (), (), (), (), (), (), ())
    object_backings = []
    resident_views = []
    command_semantics = []
    barrier_groups = []
    dependencies = []
    semantic_streams = []
    stream_command_ids = []
    descriptor_groups = []
    endpoint_uses = []
    binding_slots = []
    bases = MemoryIdBases(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    for ordinal, variant_plan in enumerate(static.variants):
        variant_source = variant_plan.source
        records = variant_source.records
        global_records = globalize_memory_records(records, bases)
        starts = {
            "abi_tensors": len(tensors) + 1,
            "runtime_shards": len(runtime_shards) + 1,
            "allocations": len(allocations) + 1,
            "streams": len(semantic_streams) + 1,
            "commands": len(commands) + 1,
            "events": len(events) + 1,
            "descriptors": len(descriptors) + 1,
            "relocations": len(relocations) + 1,
            "kernel_tensors": len(all_records.tensors) + 1,
            "computations": len(all_records.computations) + 1,
            "placements": len(all_records.placements) + 1,
            "logical_shards": len(all_records.shards) + 1,
            "partial_sums": len(all_records.partial_sums) + 1,
            "objects": len(all_records.objects) + 1,
            "views": len(all_records.views) + 1,
            "states": len(all_records.states) + 1,
            "tokens": len(all_records.tokens) + 1,
            "kernel_ops": len(all_records.ops) + 1,
            "object_backings": len(object_backings) + 1,
            "command_semantics": len(command_semantics) + 1,
            "barrier_groups": len(barrier_groups) + 1,
            "dependencies": len(dependencies) + 1,
            "endpoint_uses": len(endpoint_uses) + 1,
            "binding_slots": len(binding_slots) + 1,
        }
        all_records = KernelMemoryRecords(all_records.tensors + global_records.tensors, all_records.computations + global_records.computations, all_records.placements + global_records.placements, all_records.shards + global_records.shards, all_records.partial_sums + global_records.partial_sums, all_records.objects + global_records.objects, all_records.views + global_records.views, all_records.states + global_records.states, all_records.tokens + global_records.tokens, all_records.ops + global_records.ops)
        objects_by_id = {item.object_id: item for item in global_records.objects}
        shard_by_tensor = {}
        for shard in global_records.shards:
            shard_by_tensor.setdefault(shard.tensor_id, shard)
        for tensor in global_records.tensors:
            shard = shard_by_tensor[tensor.tensor_id]
            content = bytes.fromhex(tensor.content_sha256) if tensor.content_sha256 is not None else bytes(32)
            tensors.append(Tensor(tensor.tensor_id, sid(tensor.name), int(tensor.role), int(tensor.dtype), int(tensor.storage_class), int(tensor.access), len(tensor.shape), int(Layout.CONTIGUOUS_ROW_MAJOR), placement_id=shard.placement_id, dims=_padded(tensor.shape), content_sha256=content))
        plan = variant_plan.plan
        allocation_by_object = {}
        for item in plan.allocations:
            allocation_id = len(allocations) + 1
            object_id = item.object_id + bases.objects
            allocation_by_object[object_id] = allocation_id
            allocations.append(Allocation(allocation_id, item.owner_core, int(MemorySpace.CORE_SRAM), item.offset_bytes, item.size_bytes, item.alignment_bytes, int(objects_by_id[object_id].persistent)))
        written_objects = {global_records.views[item.view_id - 1 - bases.views].object_id for op in global_records.ops for item in op.writes}
        authored_backings = {item.object_id: item.backing for item in variant_source.external_backings} if type(variant_source) is _AuthoredVariantSource else {}
        authored_slots = {item.slot_id: item for item in variant_source.binding_slots} if type(variant_source) is _AuthoredVariantSource else {}
        authored_global_slots = {}
        reference_cursor = {}
        for obj in global_records.objects:
            if obj.memory_space is MemorySpace.CORE_SRAM:
                object_backings.append(ObjectBacking(obj.object_id, LocalAllocationBacking(allocation_by_object[obj.object_id])))
                continue
            local_object_id = obj.object_id - bases.objects
            if type(variant_source) is _AuthoredVariantSource:
                backing = authored_backings.get(local_object_id)
                if type(backing) is not ExternalSlotBacking or backing.slot_id not in authored_slots:
                    raise MeshIrError("E_RELOCATION", "authored external object lacks its exact slot backing", object_id=local_object_id)
                original = authored_slots[backing.slot_id]
                slot_id = len(binding_slots) + 1
                reference = replace(original.reference_binding, slot_id=slot_id)
                slot = replace(original, slot_id=slot_id, reference_binding=reference)
                binding_slots.append(slot)
                authored_global_slots[backing.slot_id] = slot_id
                object_backings.append(ObjectBacking(obj.object_id, ExternalSlotBacking(slot_id)))
                relocations.append(abi_projection_for_reference_relocation(obj, slot, sid(slot.symbol)))
                continue
            region_id = _region_id(arch, obj.memory_space)
            owner = obj.owner_core
            access = Access.READ_WRITE if obj.object_id in written_objects else Access.READ_ONLY
            alignment = max(obj.alignment_bytes, arch.axi_data_bytes)
            required = _align_up(obj.footprint_bytes, arch.axi_data_bytes)
            key = (region_id, owner)
            cursor = reference_cursor.get(key, 0)
            candidates = []
            for target in arch.fabric.targets:
                for item in target.ranges:
                    if item.region_id != region_id or item.owner_core != owner or int(item.access) < int(access):
                        continue
                    candidate = _align_up(max(cursor, item.offset_bytes), alignment)
                    if checked_add_u64(candidate, required, "reference binding end") <= checked_add_u64(item.offset_bytes, item.size_bytes, "target range end"):
                        candidates.append(candidate)
            if not candidates:
                raise MeshIrError("E_RELOCATION", "external object has no legal nonoverlapping reference binding", object_id=obj.object_id)
            candidate = min(candidates)
            reference_cursor[key] = checked_add_u64(candidate, required, "reference binding cursor")
            slot_id = len(binding_slots) + 1
            binding = Binding(slot_id, region_id, owner, candidate, required, alignment, access)
            slot = BindingSlot(slot_id, f"variant:{ordinal + 1}:object:{obj.object_id}", obj.memory_space, region_id, owner, required, alignment, access, binding)
            binding_slots.append(slot)
            object_backings.append(ObjectBacking(obj.object_id, ExternalSlotBacking(slot_id)))
            relocations.append(abi_projection_for_reference_relocation(obj, slot, sid(slot.symbol)))
        if type(variant_source) is _AuthoredVariantSource and (set(authored_backings) != {item.object_id - bases.objects for item in global_records.objects if item.memory_space is not MemorySpace.CORE_SRAM} or set(authored_global_slots) != set(authored_slots)):
            raise MeshIrError("E_RELOCATION", "authored external backings and slots do not exactly cover the variant", variant_ordinal=ordinal)
        resident_by_view = {}
        shards_by_id = {item.shard_id: item for item in global_records.shards}
        for view in global_records.views:
            runtime_id = len(runtime_shards) + 1
            resident_by_view[view.view_id] = runtime_id
            resident_views.append(ResidentView(runtime_id, view.view_id))
            obj = objects_by_id[view.object_id]
            shard = shards_by_id[view.shard_id]
            allocation_id = allocation_by_object.get(obj.object_id, 0)
            runtime_shards.append(Shard(runtime_id, shard.tensor_id, 0, obj.owner_core, len(shard.padded_local_shape), global_origin=_padded(shard.global_origin), local_shape=_padded(view.padded_shape), valid_shape=_padded(view.valid_shape), allocation_id=allocation_id, allocation_offset=checked_mul_u64(view.object_offset_elements, global_records.tensors[shard.tensor_id - 1 - bases.tensors].dtype.byte_width, "resident view offset"), span_bytes=obj.footprint_bytes))
        scheduled = segments.schedule.variants[ordinal]
        command_base = len(commands)
        command_id_by_ordinal = {item.command_ordinal: command_base + item.command_ordinal for item in scheduled.commands}
        scheduled_by_command = {command_id_by_ordinal[item.command_ordinal]: item for item in scheduled.commands}
        work_command_by_op = {item.source.kernel_op_id: command_id_by_ordinal[item.command_ordinal] for item in scheduled.commands if type(item.source) is KernelCommandSource}
        begin_ids = tuple(command_id_by_ordinal[item.command_ordinal] for item in scheduled.commands if type(item.source) is ControlCommandSource and type(item.source.attrs) is RequestBeginAttrs)
        end_ids = tuple(command_id_by_ordinal[item.command_ordinal] for item in scheduled.commands if type(item.source) is ControlCommandSource and type(item.source.attrs) is RequestEndAttrs)
        halt_ids = {item.owner_core: command_id_by_ordinal[item.command_ordinal] for item in scheduled.commands if type(item.source) is ControlCommandSource and type(item.source.attrs) is HaltAttrs}
        if len(begin_ids) != 1 or len(end_ids) != 1:
            raise MeshIrError("E_LIFECYCLE", "scheduled variant must contain one begin and one end command")
        begin_id = begin_ids[0]
        end_id = end_ids[0]
        participants = tuple(sorted({item.owner_core for item in scheduled.streams}, key=arch.core_ids.index))
        if set(halt_ids) != set(participants):
            raise MeshIrError("E_LIFECYCLE", "scheduled variant must contain one HALT per participant")
        last_command_id = command_base + len(scheduled.commands)
        barrier_occurrence = {}
        barrier_key_by_op = {}
        barrier_ops_by_key = {}
        for item in scheduled.commands:
            if type(item.source) is not KernelCommandSource:
                continue
            op = global_records.ops[item.source.kernel_op_id - 1]
            if op.opcode is not KernelOpcode.BARRIER:
                continue
            if type(op.attrs) is not BarrierAttrs or op.owner_core not in op.attrs.participants:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "barrier operation has invalid participants", op_id=op.op_id)
            occurrence = barrier_occurrence.get((op.attrs.participants, op.owner_core), 0)
            barrier_occurrence[(op.attrs.participants, op.owner_core)] = occurrence + 1
            key = (op.attrs.participants, occurrence)
            barrier_key_by_op[op.op_id] = key
            if op.owner_core in barrier_ops_by_key.setdefault(key, {}):
                raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "barrier has duplicate participant arrival", op_id=op.op_id)
            barrier_ops_by_key[key][op.owner_core] = op
        for (barrier_participants, occurrence), grouped in barrier_ops_by_key.items():
            if set(grouped) != set(barrier_participants):
                raise MeshIrError("E_EVENT_NO_PRODUCER", "barrier does not have one arrival per participant", participants=barrier_participants, occurrence=occurrence)
        event_by_command = {}
        barrier_event_by_key = {}
        authored_event_by_symbol = {}
        for command_id, item in scheduled_by_command.items():
            if type(item.source) is ControlCommandSource and type(item.source.attrs) is EventSignalAttrs:
                event_id = len(events) + 1
                authored_event_by_symbol[item.source.attrs.event_id] = event_id
                event_by_command[command_id] = event_id
                events.append(Event(event_id, A.EVENT_KIND.NORMAL, producer_command_id=command_id))
        for command_id, item in scheduled_by_command.items():
            source_op_id = item.source.kernel_op_id + bases.ops if type(item.source) is KernelCommandSource else 0
            if command_id in event_by_command or type(item.source) is ControlCommandSource and type(item.source.attrs) is HaltAttrs or source_op_id in barrier_key_by_op:
                continue
            event_id = len(events) + 1
            event_by_command[command_id] = event_id
            events.append(Event(event_id, A.EVENT_KIND.NORMAL, producer_command_id=command_id))
        for key in sorted(barrier_ops_by_key, key=lambda item: min(work_command_by_op[op.op_id - bases.ops] for op in barrier_ops_by_key[item].values())):
            event_id = len(events) + 1
            barrier_event_by_key[key] = event_id
            events.append(Event(event_id, A.EVENT_KIND.BARRIER, expected_arrivals=len(key[0])))
            for op in barrier_ops_by_key[key].values():
                event_by_command[work_command_by_op[op.op_id - bases.ops]] = event_id
        barrier_group_by_key = {}
        for key in sorted(barrier_ops_by_key, key=lambda item: barrier_event_by_key[item]):
            group_id = len(barrier_groups) + 1
            barrier_group_by_key[key] = group_id
            arrivals = tuple(BarrierArrival(core, barrier_ops_by_key[key][core].op_id, work_command_by_op[barrier_ops_by_key[key][core].op_id - bases.ops], barrier_ops_by_key[key][core].done_token) for core in key[0])
            barrier_groups.append(BarrierGroup(group_id, key[0], arrivals, barrier_event_by_key[key]))
        pending_semantics = {}
        for item in scheduled.commands:
            command_id = command_id_by_ordinal[item.command_ordinal]
            if type(item.source) is ControlCommandSource:
                attrs = item.source.attrs
                if type(attrs) in (EventWaitAttrs, EventSignalAttrs):
                    attrs = replace(attrs, event_id=authored_event_by_symbol[attrs.event_id])
                pending_semantics[command_id] = CommandSemantics(command_id, ControlCommandSource(attrs), ControlExecution())
                continue
            op = global_records.ops[item.source.kernel_op_id - 1]
            if op.opcode is KernelOpcode.DMA:
                execution = DmaExecution(0)
            elif op.opcode is KernelOpcode.RECV_WAIT:
                execution = RecvWaitExecution(op.attrs.transfer_id)
            elif op.opcode is KernelOpcode.BARRIER:
                execution = BarrierExecution(barrier_group_by_key[barrier_key_by_op[op.op_id]])
            else:
                execution = ComputeExecution(item.phases)
            pending_semantics[command_id] = CommandSemantics(command_id, KernelCommandSource(op.op_id), execution)
        edge_records = []
        source_edges = segments.schedule.hazards.edges
        for edge in source_edges:
            if edge.variant_ordinal != ordinal or edge.source_op_id not in work_command_by_op or edge.target_op_id not in work_command_by_op:
                continue
            source_command = work_command_by_op[edge.source_op_id]
            target_command = work_command_by_op[edge.target_op_id]
            identity = edge.source_identity + (bases.tokens if edge.kind is ScheduledDependencyKind.KERNEL_CONTROL else bases.states if edge.kind is ScheduledDependencyKind.KERNEL_STATE else bases.objects if edge.kind in (ScheduledDependencyKind.RAW, ScheduledDependencyKind.WAR, ScheduledDependencyKind.WAW, ScheduledDependencyKind.SRAM_REUSE) else 0)
            source = _source(edge.kind, identity)
            edge_records.append((source_command, target_command, edge.kind, source))
        work_ids = set(work_command_by_op.values())
        incoming = {target for source, target, kind, _ in edge_records if kind is not ScheduledDependencyKind.STREAM_ORDER and source in work_ids and target in work_ids}
        outgoing = {source for source, target, kind, _ in edge_records if kind is not ScheduledDependencyKind.STREAM_ORDER and source in work_ids and target in work_ids}
        roots = work_ids - incoming
        maxima = work_ids - outgoing
        if not work_ids:
            roots = {end_id}
            maxima = {begin_id}
        for target in sorted(roots):
            edge_records.append((begin_id, target, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(ordinal + 1)))
        for source_command in sorted(maxima):
            edge_records.append((source_command, end_id, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(ordinal + 1)))
            source_semantic = pending_semantics.get(source_command)
            if source_semantic is not None and type(source_semantic.source) is KernelCommandSource:
                op = global_records.ops[source_semantic.source.kernel_op_id - 1 - bases.ops]
                if op.opcode is not KernelOpcode.DMA and op.done_token is not None:
                    edge_records.append((source_command, end_id, ScheduledDependencyKind.KERNEL_CONTROL, KernelTokenSource(op.done_token)))
        for halt_id in halt_ids.values():
            edge_records.append((end_id, halt_id, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(ordinal + 1)))
        for stream in scheduled.streams:
            members = tuple(command_id_by_ordinal[item] for item in stream.command_ordinals)
            for position, command_id in enumerate(members):
                semantic = pending_semantics[command_id]
                attrs = semantic.source.attrs if type(semantic.source) is ControlCommandSource else None
                if type(attrs) in (RepeatCommandAttrs, AxiFenceAttrs, EventWaitAttrs, EventSignalAttrs):
                    edge_records.append((begin_id, command_id, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(ordinal + 1)))
                    edge_records.append((command_id, end_id, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(ordinal + 1)))
                prerequisites = ()
                if type(attrs) is RepeatCommandAttrs:
                    if attrs.subrange_begin_stream_ordinal + attrs.subrange_command_count != position:
                        raise MeshIrError("E_ABI_BOUNDS", "REPEAT body does not immediately precede its command", command_id=command_id)
                    prerequisites = members[attrs.subrange_begin_stream_ordinal:position]
                elif type(attrs) is EventWaitAttrs:
                    prerequisites = (next(item for item, event in event_by_command.items() if event == attrs.event_id),)
                for source_command in prerequisites:
                    edge_records.append((source_command, command_id, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(ordinal + 1)))
                    source_semantic = pending_semantics[source_command]
                    if type(source_semantic.source) is KernelCommandSource:
                        source_op = global_records.ops[source_semantic.source.kernel_op_id - 1 - bases.ops]
                        if source_op.opcode is not KernelOpcode.DMA and source_op.done_token is not None:
                            edge_records.append((source_command, command_id, ScheduledDependencyKind.KERNEL_CONTROL, KernelTokenSource(source_op.done_token)))
        edge_records = list(dict.fromkeys(edge_records))
        for source_command, target_command, kind, source in edge_records:
            dependencies.append(ScheduledDependency(len(dependencies) + 1, source_command, target_command, kind, source))
        waits_by_command = {command_id: [] for command_id in range(command_base + 1, last_command_id + 1)}
        for source_command, target_command, kind, source in edge_records:
            if kind is not ScheduledDependencyKind.STREAM_ORDER and source_command in event_by_command and event_by_command[source_command] not in waits_by_command[target_command]:
                waits_by_command[target_command].append(event_by_command[source_command])
        stream_for_command = {}
        lifecycle_stream_id = 0
        for stream in scheduled.streams:
            core = stream.owner_core
            physical = stream.physical_stream_id
            flags = stream.flags
            members = tuple(command_id_by_ordinal[item] for item in stream.command_ordinals)
            begin = len(stream_command_ids)
            stream_command_ids.extend(members)
            stream_id = len(semantic_streams) + 1
            semantic_streams.append(ScheduledStream(stream_id, core, physical, begin, len(members), flags))
            abi_streams.append(Stream(core, physical, begin, len(members), flags))
            if flags & A.STREAM_FLAGS.IS_LIFECYCLE:
                if lifecycle_stream_id:
                    raise MeshIrError("E_STREAM_CONTRACT", "variant has more than one lifecycle stream")
                lifecycle_stream_id = stream_id
            for command_id in members:
                stream_for_command[command_id] = (core, physical)
            for source_command, target_command in zip(members, members[1:]):
                dependencies.append(ScheduledDependency(len(dependencies) + 1, source_command, target_command, ScheduledDependencyKind.STREAM_ORDER, StreamOrderSource(stream_id)))
        for command_id in range(command_base + 1, last_command_id + 1):
            semantic = pending_semantics[command_id]
            op = global_records.ops[semantic.source.kernel_op_id - 1 - bases.ops] if type(semantic.source) is KernelCommandSource else None
            wait_begin = len(waits)
            waits.extend(CommandWait(event_id) for event_id in waits_by_command[command_id])
            operand_begin = len(operands)
            if op is not None:
                for access in op.reads:
                    view = global_records.views[access.view_id - 1 - bases.views]
                    obj = global_records.objects[view.object_id - 1 - bases.objects]
                    operands.append(CommandOperand(global_records.shards[view.shard_id - 1 - bases.shards].tensor_id, resident_by_view[view.view_id], allocation_by_object.get(obj.object_id, 0), int(Access.READ_ONLY)))
                for access in op.writes:
                    view = global_records.views[access.view_id - 1 - bases.views]
                    obj = global_records.objects[view.object_id - 1 - bases.objects]
                    operands.append(CommandOperand(global_records.shards[view.shard_id - 1 - bases.shards].tensor_id, resident_by_view[view.view_id], allocation_by_object.get(obj.object_id, 0), int(Access.READ_WRITE)))
            core, physical = stream_for_command[command_id]
            engine = scheduled_by_command[command_id].engine
            control_attrs = semantic.source.attrs if type(semantic.source) is ControlCommandSource else None
            opcode, control_attr = abi_projection_for_control(control_attrs) if control_attrs is not None else (abi_opcode_for_kernel_op(op), None)
            local_op = None if op is None else records.ops[op.op_id - 1 - bases.ops]
            attr = control_attr if control_attrs is not None else None if local_op is None else abi_attr_for_kernel_op(local_op, records)
            attr_index = 0
            if attr is not None:
                op_attrs.append(attr)
                attr_index = len(op_attrs)
            commands.append(Command(command_id, 0 if op is None else op.op_id, core, physical, int(engine), opcode, wait_begin, len(waits_by_command[command_id]), len(operands) - operand_begin, operand_begin, event_by_command.get(command_id, 0), attr_index))
            command_semantics.append(semantic)
        local_segments = tuple(item for item in segments.segments if item.variant_ordinal == ordinal)
        group_by_op = {}
        for segment in local_segments:
            op = global_records.ops[segment.kernel_op_id - 1]
            command_id = work_command_by_op[segment.kernel_op_id]
            descriptor_id = len(descriptors) + 1
            src_access = op.reads[segment.source_access_index] if op.reads else op.writes[segment.source_access_index]
            dst_access = op.writes[segment.destination_access_index]
            src_view = global_records.views[src_access.view_id - 1 - bases.views]
            dst_view = global_records.views[dst_access.view_id - 1 - bases.views]
            src_obj = global_records.objects[src_view.object_id - 1 - bases.objects]
            dst_obj = global_records.objects[dst_view.object_id - 1 - bases.objects]
            src_shard = global_records.shards[src_view.shard_id - 1 - bases.shards]
            dst_shard = global_records.shards[dst_view.shard_id - 1 - bases.shards]
            issuing = op.attrs.issuing_core
            src_space = src_obj.memory_space if src_obj.memory_space is not MemorySpace.CORE_SRAM else MemorySpace.CORE_SRAM if src_obj.owner_core == issuing else MemorySpace.PEER_SRAM
            dst_space = dst_obj.memory_space if dst_obj.memory_space is not MemorySpace.CORE_SRAM else MemorySpace.CORE_SRAM if dst_obj.owner_core == issuing else MemorySpace.PEER_SRAM
            src_endpoint = DmaEndpoint(int(src_space), _region_id(arch, src_space), src_obj.owner_core, src_shard.tensor_id, resident_by_view[src_view.view_id], offset_bytes=_access_offset(global_records, src_access, segment.source_region))
            dst_endpoint = DmaEndpoint(int(dst_space), _region_id(arch, dst_space), dst_obj.owner_core, dst_shard.tensor_id, resident_by_view[dst_view.view_id], offset_bytes=_access_offset(global_records, dst_access, segment.destination_region))
            descriptors.append(DmaDescriptor(descriptor_id, command_id, op.attrs.transfer_id, issuing, int(op.attrs.kind), src_endpoint, dst_endpoint, segment.rows, segment.row_bytes, segment.source_stride_bytes, segment.destination_stride_bytes, segment.useful_bytes, segment.physical_storage_bytes, command_id & ((1 << arch.axi_id_bits) - 1), arch.axi_qos_default, 0, effective_dma_burst_beats(op.attrs, arch), 0, event_by_command[command_id]))
            endpoint_uses.append(DescriptorEndpointUse(descriptor_id * 2 - 1, descriptor_id, EndpointSide.SRC, ReadAccessUse(op.op_id, segment.source_access_index, segment.source_region) if op.reads else WriteAccessUse(op.op_id, segment.source_access_index, segment.source_region)))
            endpoint_uses.append(DescriptorEndpointUse(descriptor_id * 2, descriptor_id, EndpointSide.DST, WriteAccessUse(op.op_id, segment.destination_access_index, segment.destination_region)))
            group_by_op.setdefault(op.op_id, []).append(descriptor_id)
        for op_id, descriptor_ids in group_by_op.items():
            command_id = work_command_by_op[op_id - bases.ops]
            group_id = len(descriptor_groups) + 1
            descriptor_groups.append(DescriptorGroup(group_id, command_id, op_id, tuple(descriptor_ids), event_by_command[command_id]))
            index = command_id - starts["commands"]
            command_semantics[starts["command_semantics"] - 1 + index] = replace(command_semantics[starts["command_semantics"] - 1 + index], execution=DmaExecution(group_id))
            outgoing_targets = {target for source, target, _, _ in edge_records if source == command_id}
            for target in sorted(outgoing_targets):
                for descriptor_id in descriptor_ids:
                    dependencies.append(ScheduledDependency(len(dependencies) + 1, command_id, target, ScheduledDependencyKind.DMA_COMPLETION, DescriptorSource(descriptor_id)))
        ends = {
            "abi_tensors": len(tensors), "runtime_shards": len(runtime_shards), "allocations": len(allocations), "streams": len(semantic_streams), "commands": len(commands), "events": len(events), "descriptors": len(descriptors), "relocations": len(relocations),
            "kernel_tensors": len(all_records.tensors), "computations": len(all_records.computations), "placements": len(all_records.placements), "logical_shards": len(all_records.shards), "partial_sums": len(all_records.partial_sums), "objects": len(all_records.objects), "views": len(all_records.views), "states": len(all_records.states), "tokens": len(all_records.tokens), "kernel_ops": len(all_records.ops), "object_backings": len(object_backings), "command_semantics": len(command_semantics), "barrier_groups": len(barrier_groups), "dependencies": len(dependencies), "endpoint_uses": len(endpoint_uses), "binding_slots": len(binding_slots),
        }
        membership = VariantMembership(*(IdSpan(starts[field], ends[field] - starts[field] + 1) for field in A.VARIANT_MEMBERSHIP_FIELDS))
        if lifecycle_stream_id == 0:
            raise MeshIrError("E_STREAM_CONTRACT", "variant has no lifecycle stream")
        variants.append(ProgramVariant(ordinal + 1, entrypoint_ids[variant_source.entrypoint], ordinal + 1, variant_source.lineage, lifecycle_stream_id, membership))
        bases = MemoryIdBases(len(all_records.tensors), len(all_records.computations), len(all_records.placements), len(all_records.shards), len(all_records.partial_sums), len(all_records.objects), len(all_records.views), len(all_records.states), len(all_records.tokens), len(all_records.ops))
    projected_entrypoints = []
    for entrypoint in entrypoints:
        selected = tuple(item for item in variants if item.entrypoint_id == entrypoint.entrypoint_id)
        lifecycle = tuple(semantic_streams[item.lifecycle_stream_id - 1] for item in selected)
        locations = {(item.core_id, item.physical_stream_id) for item in lifecycle}
        if len(locations) != 1:
            raise MeshIrError("E_STREAM_CONTRACT", "entrypoint profiles require one lifecycle stream location", entrypoint_id=entrypoint.entrypoint_id)
        core, physical = locations.pop()
        projected_entrypoints.append(replace(entrypoint, lifecycle_core_id=core, lifecycle_stream_id=physical))
    entrypoints = projected_entrypoints
    transport = _TransportSections(tuple(strings), tuple(entrypoints), profiles, tuple(tensors), tuple(runtime_shards), tuple(allocations), tuple(abi_streams), tuple(commands), tuple(waits), tuple(operands), tuple(events), tuple(descriptors), tuple(op_attrs), tuple(relocations))
    facts = _ProgramFacts(static.origin, tuple(variants), all_records.tensors, all_records.computations, all_records.placements, all_records.shards, all_records.partial_sums, all_records.objects, all_records.views, all_records.states, all_records.tokens, all_records.ops, tuple(object_backings), tuple(resident_views), tuple(command_semantics), tuple(barrier_groups), tuple(dependencies), tuple(semantic_streams), tuple(stream_command_ids), tuple(descriptor_groups), tuple(endpoint_uses), tuple(binding_slots))
    from mesh_ir.scheduled.addressing import _derive_descriptor_execution_set

    executions, identity = _derive_descriptor_execution_set(transport, facts, arch)
    pretraffic = _PreTrafficSemantics(facts.origin, facts.variants, facts.kernel_tensors, facts.computations, facts.placements, facts.logical_shards, facts.partial_sums, facts.objects, facts.views, facts.states, facts.tokens, facts.kernel_ops, facts.object_backings, facts.resident_views, facts.command_semantics, facts.barrier_groups, facts.dependencies, facts.streams, facts.stream_command_ids, facts.descriptor_groups, facts.endpoint_uses, facts.binding_slots, identity, executions)
    return _PreTrafficState(A.ABI_MAJOR, A.ABI_MINOR, arch.digest(), transport, pretraffic)


__all__ = ["bind_addresses_and_relocations_stage"]

from __future__ import annotations

from mesh_ir.analysis.regions import ordered_region_interval
from mesh_ir.architecture import ArchManifest
from mesh_ir.canonical import checked_add_u64, checked_mul_u64, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DmaKind, INVALID_CORE_ID, MemorySpace
from mesh_ir.model import Program
from mesh_ir.scheduled.assemble import _TransportSections
from mesh_ir.scheduled.model import BoundInvocation, ControlCommandSource, DescriptorEndpointUse, EndpointSide, ExternalSlotBacking, LocalAllocationBacking, ProgramSemantics, ReadAccessUse, RepeatCommandAttrs, WriteAccessUse, _ProgramFacts
from mesh_ir.traffic import AddressRef, Binding, DescriptorExecution, DescriptorIdentity, DirectAddress, ResolvedDescriptor, SlotAddress, TrafficDirection, calculate_traffic, resolve_addresses


def _execution_counts(transport: _TransportSections, facts: _ProgramFacts) -> dict[int, int]:
    counts = {item.command_id: 1 for item in transport.commands}
    semantics_by_id = {item.command_id: item for item in facts.command_semantics}
    for stream in facts.streams:
        members = facts.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]
        for ordinal, command_id in enumerate(members):
            semantic = semantics_by_id[command_id]
            if type(semantic.source) is not ControlCommandSource or type(semantic.source.attrs) is not RepeatCommandAttrs:
                continue
            attrs = semantic.source.attrs
            begin = attrs.subrange_begin_stream_ordinal
            if begin + attrs.subrange_command_count != ordinal:
                raise MeshIrError("E_ABI_BOUNDS", "REPEAT body does not immediately precede its command", command_id=command_id)
            for body_id in members[begin:ordinal]:
                if counts[body_id] != 1:
                    raise MeshIrError("E_ABI_BOUNDS", "nested or overlapping REPEAT body is invalid", command_id=command_id)
                counts[body_id] = attrs.repeat_count
    return counts


def _endpoint_span(facts: _ProgramFacts, endpoint_use: DescriptorEndpointUse):
    use = endpoint_use.use
    if type(use) not in (ReadAccessUse, WriteAccessUse):
        raise MeshIrError("E_DMA_RANGE", "descriptor endpoint use type is invalid", ref_id=endpoint_use.ref_id)
    op = next((item for item in facts.kernel_ops if item.op_id == use.kernel_op_id), None)
    if op is None:
        raise MeshIrError("E_DMA_RANGE", "descriptor endpoint use references an unknown operation", ref_id=endpoint_use.ref_id)
    accesses = op.reads if type(use) is ReadAccessUse else op.writes
    if type(use.access_index) is not int or not 0 <= use.access_index < len(accesses):
        raise MeshIrError("E_DMA_RANGE", "descriptor endpoint access index is invalid", ref_id=endpoint_use.ref_id)
    access = accesses[use.access_index]
    ordered_region_interval(access.region, use.region)
    views = {item.view_id: item for item in facts.views}
    objects = {item.object_id: item for item in facts.objects}
    shards = {item.shard_id: item for item in facts.logical_shards}
    tensors = {item.tensor_id: item for item in facts.kernel_tensors}
    view = views[access.view_id]
    obj = objects[view.object_id]
    tensor = tensors[shards[view.shard_id].tensor_id]
    element = view.object_offset_elements
    for origin, step, stride in zip(use.region.origin, use.region.steps, view.object_strides):
        element = checked_add_u64(element, checked_mul_u64(origin, stride, "descriptor region element offset"), "descriptor region element offset")
    begin = checked_mul_u64(element, tensor.dtype.byte_width, "descriptor region byte offset")
    if any(extent == 0 for extent in use.region.shape):
        end = begin
    else:
        span = tensor.dtype.byte_width
        for extent, step, stride in zip(use.region.shape, use.region.steps, view.object_strides):
            displacement = checked_mul_u64(checked_mul_u64(extent - 1, step, "descriptor region span"), stride, "descriptor region span")
            span = checked_add_u64(span, checked_mul_u64(displacement, tensor.dtype.byte_width, "descriptor region byte span"), "descriptor region byte span")
        end = checked_add_u64(begin, span, "descriptor region byte end")
    return op, access, view, obj, tensor, begin, end


def _address_ref(facts: _ProgramFacts, endpoint_use: DescriptorEndpointUse, endpoint, allocation_by_id, backing_by_object):
    op, access, view, obj, tensor, begin, end = _endpoint_span(facts, endpoint_use)
    backing = backing_by_object[obj.object_id]
    requested = Access.READ_ONLY if type(endpoint_use.use) is ReadAccessUse else Access.READ_WRITE
    if type(backing.backing) is LocalAllocationBacking:
        allocation = allocation_by_id[backing.backing.allocation_id]
        origin = DirectAddress(allocation.offset_bytes, allocation.offset_bytes, allocation.size_bytes, allocation.alignment_bytes, Access.READ_WRITE)
        region_id = endpoint.region_id
        owner = obj.owner_core
        expected_space = MemorySpace.CORE_SRAM if endpoint.memory_space == int(MemorySpace.CORE_SRAM) else MemorySpace.PEER_SRAM
    elif type(backing.backing) is ExternalSlotBacking:
        slot = next((item for item in facts.binding_slots if item.slot_id == backing.backing.slot_id), None)
        if slot is None:
            raise MeshIrError("E_RELOCATION", "external object backing references an unknown slot", object_id=obj.object_id)
        origin = SlotAddress(slot.slot_id)
        region_id = slot.region_id
        owner = slot.owner_core
        expected_space = slot.memory_space
    else:
        raise MeshIrError("E_RELOCATION", "object backing kind is invalid", object_id=obj.object_id)
    if (endpoint.region_id, endpoint.owner_core, endpoint.memory_space, endpoint.tensor_id, endpoint.offset_bytes) != (region_id, owner, int(expected_space), tensor.tensor_id, begin):
        raise MeshIrError("E_DMA_RANGE", "ABI DMA endpoint is not the derived access projection", ref_id=endpoint_use.ref_id)
    resident = next((item for item in facts.resident_views if item.view_id == view.view_id), None)
    if resident is None or endpoint.shard_id != resident.runtime_shard_id:
        raise MeshIrError("E_DMA_RANGE", "ABI DMA endpoint resident view is invalid", ref_id=endpoint_use.ref_id)
    return AddressRef(endpoint_use.ref_id, expected_space, region_id, owner, origin, begin, end - begin, end - begin, 1, requested), op, tensor


def _derive_descriptor_execution_set(transport: _TransportSections, facts: _ProgramFacts, arch: ArchManifest, bindings: tuple[Binding, ...] | None = None, selected_variant_id: int | None = None) -> tuple[tuple[DescriptorExecution, ...], str]:
    allocation_by_id = {item.allocation_id: item for item in transport.allocations}
    backing_by_object = {item.object_id: item for item in facts.object_backings}
    uses_by_descriptor = {}
    for item in facts.endpoint_uses:
        uses_by_descriptor.setdefault(item.descriptor_id, {})[item.side] = item
    groups = {descriptor_id: group for group in facts.descriptor_groups for descriptor_id in group.descriptor_ids}
    counts = _execution_counts(transport, facts)
    variants = facts.variants
    selected = {item.variant_id for item in variants} if selected_variant_id is None else {selected_variant_id}
    prepared = []
    for descriptor in transport.dma_descriptors:
        variant = next(item for item in variants if item.membership.descriptors.first_id <= descriptor.descriptor_id < item.membership.descriptors.first_id + item.membership.descriptors.count)
        if variant.variant_id not in selected:
            continue
        uses = uses_by_descriptor.get(descriptor.descriptor_id, {})
        if set(uses) != {EndpointSide.SRC, EndpointSide.DST}:
            raise MeshIrError("E_DMA_RANGE", "descriptor must have exactly two endpoint uses", descriptor_id=descriptor.descriptor_id)
        src_ref, src_op, src_tensor = _address_ref(facts, uses[EndpointSide.SRC], descriptor.src, allocation_by_id, backing_by_object)
        dst_ref, dst_op, dst_tensor = _address_ref(facts, uses[EndpointSide.DST], descriptor.dst, allocation_by_id, backing_by_object)
        group = groups.get(descriptor.descriptor_id)
        if group is None or src_op.op_id != group.kernel_op_id or dst_op.op_id != group.kernel_op_id or group.command_id != descriptor.command_id or group.completion_event_id != descriptor.completion_event:
            raise MeshIrError("E_DMA_RANGE", "descriptor group ownership is invalid", descriptor_id=descriptor.descriptor_id)
        prepared.append((variant, descriptor, src_ref, dst_ref, src_tensor, dst_tensor))
    resolved_groups = {}
    identity_parts = []
    for variant_id, issuing_core in sorted({(item[0].variant_id, item[1].owner_core) for item in prepared}):
        selected_items = tuple(item for item in prepared if (item[0].variant_id, item[1].owner_core) == (variant_id, issuing_core))
        refs = tuple(sorted((ref for item in selected_items for ref in item[2:4]), key=lambda item: item.ref_id))
        slot_ids = {ref.origin.slot_id for ref in refs if type(ref.origin) is SlotAddress}
        slots = tuple(sorted((item for item in facts.binding_slots if item.slot_id in slot_ids), key=lambda item: item.slot_id))
        selected_bindings = tuple(item.reference_binding for item in slots) if bindings is None else tuple(item for item in bindings if item.slot_id in slot_ids)
        resolved = resolve_addresses(arch, slots, selected_bindings, refs, issuing_core=issuing_core)
        resolved_groups[(variant_id, issuing_core)] = {item.ref_id: item for item in resolved.addresses}
        identity_parts.append((variant_id, issuing_core, resolved.identity_sha256))
    result = []
    for variant, descriptor, src_ref, dst_ref, src_tensor, dst_tensor in prepared:
        resolved_by_ref = resolved_groups[(variant.variant_id, descriptor.owner_core)]
        kind = DmaKind(descriptor.kind)
        direction = TrafficDirection.READ if kind in (DmaKind.LOAD, DmaKind.PREFETCH) else TrafficDirection.WRITE if kind is DmaKind.STORE else TrafficDirection.P2P if kind is DmaKind.P2P_PUSH else TrafficDirection.LOCAL
        peer = descriptor.dst.owner_core if kind is DmaKind.P2P_PUSH else INVALID_CORE_ID
        identity_tensor = src_tensor if kind in (DmaKind.LOAD, DmaKind.PREFETCH, DmaKind.P2P_PUSH, DmaKind.LOCAL_FILL) else dst_tensor
        identity = DescriptorIdentity(variant.entrypoint_id, variant.profile_id, descriptor.command_id, descriptor.descriptor_id, descriptor.owner_core, peer, identity_tensor.tensor_id, identity_tensor.role, direction)
        resolved_descriptor = ResolvedDescriptor(identity, kind, resolved_by_ref[src_ref.ref_id], resolved_by_ref[dst_ref.ref_id], descriptor.rows, descriptor.row_bytes, descriptor.src_stride_bytes, descriptor.dst_stride_bytes, descriptor.useful_bytes, descriptor.physical_storage_bytes, descriptor.max_burst_beats)
        result.append(DescriptorExecution(resolved_descriptor, counts[descriptor.command_id]))
    return tuple(result), semantic_sha256(tuple(identity_parts))


def derive_descriptor_execution_set(program: Program, arch: ArchManifest, bindings: tuple[Binding, ...] | None = None, selected_variant_id: int | None = None) -> tuple[tuple[DescriptorExecution, ...], str]:
    if type(program) is not Program or type(program.semantics) is not ProgramSemantics:
        raise MeshIrError("E_ABI_BOUNDS", "descriptor projection requires an exact Program")
    transport = _TransportSections(program.strings, program.entrypoints, program.profiles, program.tensors, program.shards, program.allocations, program.streams, program.commands, program.command_waits, program.command_operands, program.events, program.dma_descriptors, program.op_attrs, program.relocations)
    return _derive_descriptor_execution_set(transport, program.semantics, arch, bindings, selected_variant_id)


def derive_descriptor_executions(program: Program, arch: ArchManifest, bindings: tuple[Binding, ...] | None = None, selected_variant_id: int | None = None) -> tuple[DescriptorExecution, ...]:
    executions, identity = derive_descriptor_execution_set(program, arch, bindings, selected_variant_id)
    if bindings is None and selected_variant_id is None and identity != program.semantics.reference_binding_identity_sha256:
        raise MeshIrError("E_RELOCATION", "Program reference binding identity is stale")
    return executions


def bind_invocation(program: Program, arch: ArchManifest, entrypoint_id: int, profile_id: int, bindings: tuple[Binding, ...]) -> BoundInvocation:
    from mesh_ir.scheduled.verify import verify_program

    verify_program(program, arch)
    if type(entrypoint_id) is not int or type(profile_id) is not int or type(bindings) is not tuple or any(type(item) is not Binding for item in bindings):
        raise MeshIrError("E_RELOCATION", "invocation selection and bindings must be exact typed records")
    matches = tuple(item for item in program.semantics.variants if (item.entrypoint_id, item.profile_id) == (entrypoint_id, profile_id))
    if len(matches) != 1:
        raise MeshIrError("E_RELOCATION", "invocation selects no unique Program variant", entrypoint_id=entrypoint_id, profile_id=profile_id)
    variant = matches[0]
    span = variant.membership.binding_slots
    required = tuple(range(span.first_id, span.first_id + span.count))
    ordered = tuple(sorted(bindings, key=lambda item: item.slot_id))
    if tuple(item.slot_id for item in ordered) != required:
        raise MeshIrError("E_RELOCATION", "invocation bindings must exactly cover the selected variant")
    executions, _ = derive_descriptor_execution_set(program, arch, ordered, variant.variant_id)
    identity = semantic_sha256({"program_semantic_sha256": program.semantic_sha256, "arch_digest": arch.digest(), "variant_id": variant.variant_id, "bindings": ordered})
    traffic = calculate_traffic(arch, identity, executions)
    return BoundInvocation(program.semantic_sha256, arch.digest(), variant.variant_id, entrypoint_id, profile_id, ordered, identity, traffic)


__all__ = ["bind_invocation", "derive_descriptor_execution_set", "derive_descriptor_executions"]

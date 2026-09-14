"""MoE overlay runtime installation (contract 7.9.2).

Turns the materializer data plane into the complete batch-owned object
graph: route fills, dispatch/combine transfers, streamed weight loads,
padding fills, expert compute, per-token combines, region terminals and
the group exit.  References are handles while the graph is built and are
rewritten into final ordinals by `OverlayBuilder.finish()`, so ordering
and reference resolution can never drift apart.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError, canonical_json_bytes
from mesh_ir.moe_materializer import (
    MaterializeConfig,
    MaterializedDataPlane,
    data_plane_projection,
    materialize_data_plane,
)
from mesh_ir.moe_overlay import (
    AllocationRecord,
    CommandRecord,
    DescriptorRecord,
    EventRecord,
    Overlay,
    OverlayBuilder,
    OverlayKey,
    TransferRecord,
    ValidityShape,
    ViewRecord,
    allocate_scratch,
    verify_overlay,
)

INVALID_U16 = 0xFFFF
NO_EXPERT = 0xFFFF
NO_CORE = 0xFFFF
GROUP_REGION = 0


@dataclass
class MaterializedOverlay:
    overlay: Overlay
    plane: MaterializedDataPlane
    intervals: dict
    digest: str
    weight_bindings: tuple = ()

    def canonical(self) -> dict:
        return materialization_projection(self.overlay, self.plane,
                                         self.weight_bindings)


@dataclass
class Activity:
    commands: list = field(default_factory=list)
    terminals: dict = field(default_factory=dict)


def materialize_overlay(layer, kernel, plan, capacity, members, placement,
                        regions, region_scratch, config: MaterializeConfig,
                        bounds: dict, expert_specs=(),
                        weight_tags=(), weight_bindings=(),
                        program_allocations=()) -> MaterializedOverlay:
    for region in regions:
        if region.layer_id != layer.layer_id:
            raise MeshIrError(
                "E_MOE_MATERIALIZATION_V",
                "materializer region belongs to another layer",
                layer_id=layer.layer_id, region_id=region.region_id)
    plane = materialize_data_plane(layer, kernel, plan, capacity, members,
                                   placement, config)
    builder = OverlayBuilder(program_instance_id=1, layer_id=layer.layer_id)
    members_by_identity = {member.member_identity: member
                           for member in members}
    transfers = _install_transfers(builder, plane)
    terminals = {}
    for region in regions:
        activity = Activity()
        _install_region(builder, layer, kernel, region, plane, capacity,
                        members_by_identity, placement, config, transfers,
                        activity, expert_specs, weight_tags)
        terminals[region.region_id] = activity.terminals[region.region_id]
    _install_group_exit(builder, regions, terminals)
    entry_events = frozenset((region.region_id, A.MESH_OBJECT_KIND.EVENT, 0)
                             for region in regions)
    overlay = builder.finish(external_refs=entry_events)
    intervals = allocate_scratch(overlay, dict(region_scratch))
    verify_overlay(overlay, bounds, external_events=entry_events,
                   program_allocations=program_allocations)
    digest = materialization_digest(overlay, plane, config, weight_bindings)
    return MaterializedOverlay(overlay=overlay, plane=plane,
                               intervals=intervals, digest=digest,
                               weight_bindings=tuple(weight_bindings))


def _install_transfers(builder, plane) -> dict:
    handles = {}
    for chunk in plane.chunks:
        record = TransferRecord(
            phase=chunk["phase"], src_core=chunk["src_core"],
            dst_core=chunk["dst_core"], expert_id=chunk["expert_id"],
            chunk_ordinal=chunk["chunk_ordinal"],
            logical_bytes=chunk["row_bytes"],
            key=OverlayKey(region_group_id=0, region_id=GROUP_REGION,
                           kind=A.MESH_OBJECT_KIND.TRANSFER, ordinal=0))
        handles[id(chunk)] = builder.add(A.MESH_OBJECT_KIND.TRANSFER, record)
    return handles


def _add_allocation(builder, region_id, kind, owner_core, byte_count,
                    alignment, expert_id=NO_EXPERT, source_core=NO_CORE,
                    phase=0, validity=None):
    record = AllocationRecord(
        owner_core=owner_core, allocation_kind=kind, expert_id=expert_id,
        source_core=source_core, phase=phase, bytes=byte_count,
        alignment=alignment,
        validity=validity or ValidityShape(
            A.MOE_VALIDITY_KIND.FULL_PREFIX, valid_bytes=byte_count),
        key=OverlayKey(region_group_id=0, region_id=region_id,
                       kind=A.MESH_OBJECT_KIND.ALLOCATION, ordinal=0))
    return builder.add(A.MESH_OBJECT_KIND.ALLOCATION, record)


def _add_view(builder, region_id, owner_core, kind, access, backing_handle,
              offset, byte_count, owner_kind, owner_ref, token_ref,
              backing_kind=A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION,
              validity=(1,)):
    if backing_kind == A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION:
        backing_ref = (backing_handle[0], backing_handle[2])
    elif backing_kind in (A.MOE_VIEW_BACKING.STATIC_ALLOCATION,
                          A.MOE_VIEW_BACKING.WEIGHT_CACHE_SLOT):
        backing_ref = (int(backing_handle),)
    else:
        backing_ref = ()
    record = ViewRecord(
        owner_core=owner_core, view_kind=kind, access=access,
        validity_slices=((0, validity[0]),), backing_kind=backing_kind,
        backing_ref=backing_ref, offset=offset, bytes=byte_count,
        semantic_owner_kind=owner_kind, semantic_owner_ref=owner_ref,
        token_ref=token_ref,
        key=OverlayKey(region_group_id=0, region_id=region_id,
                       kind=A.MESH_OBJECT_KIND.VIEW, ordinal=0))
    return builder.add(A.MESH_OBJECT_KIND.VIEW, record)


def _add_event(builder, region_id, role, owner_core, event_phase,
               phase=0, expert_id=NO_EXPERT, src_core=NO_CORE,
               dst_core=NO_CORE, chunk_ordinal=0, token_ref=bytes(32)):
    record = EventRecord(
        event_phase=event_phase, owner_core=owner_core, phase=phase,
        src_core=src_core, dst_core=dst_core, expert_id=expert_id,
        chunk_ordinal=chunk_ordinal, role=role, token_ref=token_ref,
        producers=(),
        key=OverlayKey(region_group_id=0, region_id=region_id,
                       kind=A.MESH_OBJECT_KIND.EVENT, ordinal=0))
    return builder.add(A.MESH_OBJECT_KIND.EVENT, record)


def _add_command(builder, region_id, phase, opcode, role, owner_core, waits,
                 signals=(), descriptors=(), transfers=(), views=(),
                 expert_id=NO_EXPERT, src_core=NO_CORE, dst_core=NO_CORE,
                 chunk_ordinal=0, token_ref=bytes(32), kernel_spec_index=0,
                 payload_bytes=0):
    record = CommandRecord(
        phase=phase, opcode=opcode, owner_core=owner_core, src_core=src_core,
        dst_core=dst_core, expert_id=expert_id, chunk_ordinal=chunk_ordinal,
        role=role, token_ref=token_ref, wait_refs=tuple(waits),
        signal_refs=tuple(signals), descriptor_refs=tuple(descriptors),
        transfer_refs=tuple(transfers), view_refs=tuple(views),
        kernel_spec_index=kernel_spec_index, payload_bytes=payload_bytes,
        key=OverlayKey(region_group_id=0, region_id=region_id,
                       kind=A.MESH_OBJECT_KIND.COMMAND, ordinal=0))
    return builder.add(A.MESH_OBJECT_KIND.COMMAND, record)


def _add_descriptor(builder, region_id, moe_kind, owner_core, phase, src_core,
                    dst_core, expert_id, chunk_ordinal, token_ref,
                    valid_bytes=0, source_view=(), destination_view=(),
                    transfer=(), fill_content=b""):
    if fill_content and len(fill_content) != valid_bytes:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "fill content must match the valid bytes",
                          moe_kind=moe_kind, bytes=len(fill_content),
                          valid_bytes=valid_bytes)
    record = DescriptorRecord(
        moe_kind=moe_kind, owner_core=owner_core, phase=phase,
        src_core=src_core, dst_core=dst_core, expert_id=expert_id,
        chunk_ordinal=chunk_ordinal, token_ref=token_ref,
        valid_bytes=valid_bytes, source_view_ref=tuple(source_view),
        destination_view_ref=tuple(destination_view),
        transfer_ref=tuple(transfer), fill_content=bytes(fill_content),
        key=OverlayKey(region_group_id=0, region_id=region_id,
                       kind=A.MESH_OBJECT_KIND.DESCRIPTOR, ordinal=0))
    return builder.add(A.MESH_OBJECT_KIND.DESCRIPTOR, record)


def _link_event(builder, event_handle, command_handle, extra=()) -> None:
    record = builder._objects[event_handle]
    builder._objects[event_handle] = replace(
        record, producers=(command_handle,) + tuple(extra))


def _entry_event(region) -> tuple:
    return (region.region_id, A.MESH_OBJECT_KIND.EVENT, 0)


def _install_region(builder, layer, kernel, region, plane, capacity, members,
                    placement, config, transfers, activity,
                    expert_specs, weight_tags) -> None:
    core = region.core_id
    region_id = region.region_id
    entry = _entry_event(region)
    route_fill = _install_route_fill(builder, region, plane, entry, activity)
    member_ordinals = {row["token_ref"]: row["member"].binding_ordinal
                       for row in plane.expert_rows
                       if row["kind"] == "real"}
    dispatch_ready, remote_rows = _install_dispatch(
        builder, region, plane, transfers, entry, route_fill, config,
        activity, member_ordinals)
    experts = _install_experts(builder, layer, kernel, region, plane,
                               dispatch_ready, remote_rows, entry, config,
                               activity, placement, expert_specs,
                               weight_tags)
    combine_ready = _install_combine(builder, region, plane, experts, entry,
                                     config, transfers, activity)
    _install_tokens(builder, layer, region, plane, members, placement, entry,
                    experts, combine_ready, activity)
    terminal = _install_terminal(builder, region, entry, activity)
    activity.terminals[region_id] = terminal
    del capacity


def _install_route_fill(builder, region, plane, entry, activity):
    entries = plane.route_buffers.get(region.core_id, ())
    if not entries:
        return None
    byte_count = len(entries) * A.MOE_RUNTIME_ROUTE_ENTRY_BYTES
    region_id = region.region_id
    allocation = _add_allocation(
        builder, region_id, A.MOE_ALLOCATION_KIND.ROUTE_BUFFER,
        region.core_id, byte_count, 64)
    view = _add_view(
        builder, region_id, region.core_id, A.MOE_VIEW_KIND.ROUTE_METADATA,
        A.MOE_VIEW_ACCESS.READ_WRITE, allocation, 0, byte_count,
        A.MOE_SEMANTIC_OWNER.REGION, (region_id,), bytes(32))
    descriptor = _add_descriptor(
        builder, region_id, A.MOE_DESCRIPTOR_KIND.ROUTE_FILL, region.core_id,
        0, NO_CORE, region.core_id, NO_EXPERT, 0, bytes(32),
        valid_bytes=byte_count, destination_view=(view,),
        fill_content=b"".join(entries))
    event = _add_event(builder, region_id, A.MOE_EVENT_ROLE.ROUTE_FILL_DONE,
                       region.core_id, A.MOE_EVENT_PHASE.FILL)
    command = _add_command(
        builder, region_id, A.MOE_COMMAND_PHASE.ROUTE_FILL, A.OPCODE.DMA_FILL,
        A.MOE_COMMAND_ROLE.ROUTE_FILL, region.core_id, waits=(entry,),
        signals=(event,), descriptors=(descriptor,),
        payload_bytes=byte_count)
    _link_event(builder, event, command)
    activity.commands.append(command)
    return event


def _install_dispatch(builder, region, plane, transfers, entry, route_fill,
                      config, activity, member_ordinals):
    ready = {}
    remote_rows = {}
    core = region.core_id
    region_id = region.region_id
    source_ready = route_fill or entry
    for chunk in plane.chunks:
        if chunk["phase"] != A.MOE_TRANSFER_PHASE.DISPATCH:
            continue
        if chunk["src_core"] == core and chunk["dst_core"] != core:
            member_ordinal = member_ordinals.get(chunk["token_refs"][0], 0)
            source = _add_view(
                builder, region_id, core, A.MOE_VIEW_KIND.MEMBER_INPUT,
                A.MOE_VIEW_ACCESS.READ, chunk["src_allocation"],
                chunk["src_offset"], chunk["row_bytes"],
                A.MOE_SEMANTIC_OWNER.MEMBER, (member_ordinal,),
                chunk["token_refs"][0],
                backing_kind=A.MOE_VIEW_BACKING.STATIC_ALLOCATION,
                validity=(chunk["rows"],))
            descriptor = _add_descriptor(
                builder, region_id, A.MOE_DESCRIPTOR_KIND.DISPATCH, core,
                chunk["phase"], chunk["src_core"], chunk["dst_core"],
                chunk["expert_id"], chunk["chunk_ordinal"], bytes(32),
                valid_bytes=chunk["row_bytes"], source_view=(source,))
            command = _add_command(
                builder, region_id, A.MOE_COMMAND_PHASE.DISPATCH_PUSH,
                A.OPCODE.DMA_P2P_PUSH, A.MOE_COMMAND_ROLE.DISPATCH_SENDER,
                core, waits=(source_ready,), descriptors=(descriptor,),
                transfers=(transfers[id(chunk)],), src_core=chunk["src_core"],
                dst_core=chunk["dst_core"], expert_id=chunk["expert_id"],
                chunk_ordinal=chunk["chunk_ordinal"],
                payload_bytes=chunk["row_bytes"])
            activity.commands.append(command)
        if chunk["dst_core"] == core and chunk["src_core"] != core:
            allocation = _add_allocation(
                builder, region_id, A.MOE_ALLOCATION_KIND.DISPATCH_REMOTE,
                core, chunk["row_bytes"], config.axi_bus_bytes,
                expert_id=chunk["expert_id"], source_core=chunk["src_core"],
                phase=chunk["chunk_ordinal"])
            view = _add_view(
                builder, region_id, core, A.MOE_VIEW_KIND.DISPATCH_BUFFER,
                A.MOE_VIEW_ACCESS.READ_WRITE, allocation, 0,
                chunk["row_bytes"], A.MOE_SEMANTIC_OWNER.EXPERT,
                (chunk["expert_id"],), bytes(32))
            descriptor = _add_descriptor(
                builder, region_id, A.MOE_DESCRIPTOR_KIND.DISPATCH, core,
                chunk["phase"], chunk["src_core"], chunk["dst_core"],
                chunk["expert_id"], chunk["chunk_ordinal"], bytes(32),
                valid_bytes=chunk["row_bytes"], destination_view=(view,))
            event = _add_event(
                builder, region_id, A.MOE_EVENT_ROLE.DISPATCH_READY, core,
                A.MOE_EVENT_PHASE.DISPATCH, expert_id=chunk["expert_id"],
                src_core=chunk["src_core"], dst_core=chunk["dst_core"],
                chunk_ordinal=chunk["chunk_ordinal"])
            command = _add_command(
                builder, region_id, A.MOE_COMMAND_PHASE.DISPATCH_WAIT,
                A.OPCODE.RECV_WAIT, A.MOE_COMMAND_ROLE.DISPATCH_RECEIVER,
                core, waits=(entry,), signals=(event,),
                descriptors=(descriptor,), transfers=(transfers[id(chunk)],),
                src_core=chunk["src_core"], dst_core=chunk["dst_core"],
                expert_id=chunk["expert_id"],
                chunk_ordinal=chunk["chunk_ordinal"],
                payload_bytes=chunk["row_bytes"])
            _link_event(builder, event, command)
            activity.commands.append(command)
            ready.setdefault(chunk["expert_id"], []).append(event)
            remote_rows[(chunk["expert_id"], chunk["chunk_ordinal"])] = (
                view, chunk)
    return ready, remote_rows


def _install_experts(builder, layer, kernel, region, plane, dispatch_ready,
                     remote_rows, entry, config, activity, placement,
                     expert_specs, weight_tags):
    state = {}
    core = region.core_id
    region_id = region.region_id
    weight_bytes_by_expert = {spec.expert_id: spec.weight_bytes
                              for spec in expert_specs
                              if spec.layer_id == layer.layer_id}
    pad_by_expert = {fill["expert_id"]: fill for fill in plane.pad_fills}
    cached = config.weight_residency == "cached"
    for expert_id in range(layer.expert_count):
        if placement[expert_id] != core:
            continue
        rows = [row for row in plane.expert_rows
                if row["expert_id"] == expert_id]
        if not rows:
            continue
        real = [row for row in rows if row["kind"] == "real"]
        output_row_bytes = rows[0]["output_row_bytes"]
        bitmap = bytearray((len(rows) + 7) // 8)
        for index in range(len(real)):
            bitmap[index // 8] |= 1 << (index % 8)
        output = _add_allocation(
            builder, region_id, A.MOE_ALLOCATION_KIND.EXPERT_OUTPUT, core,
            len(rows) * output_row_bytes, config.axi_bus_bytes,
            expert_id=expert_id,
            validity=ValidityShape(
                A.MOE_VALIDITY_KIND.ROW_BITMAP, row_bytes=output_row_bytes,
                row_count=len(rows), bitmap_lowerhex=bytes(bitmap).hex()))
        output_view = _add_view(
            builder, region_id, core, A.MOE_VIEW_KIND.EXPERT_OUTPUT,
            A.MOE_VIEW_ACCESS.READ_WRITE, output, 0,
            len(rows) * output_row_bytes, A.MOE_SEMANTIC_OWNER.EXPERT,
            (expert_id,), bytes(32), validity=(len(rows),))
        weight_bytes = weight_bytes_by_expert.get(expert_id,
                                                  layer.token_bytes * 2)
        if cached:
            tag_index = weight_tags.get((layer.layer_id, expert_id))
            if tag_index is None:
                raise MeshIrError(
                    "E_MOE_MATERIALIZATION_V",
                    "cached weight demand needs a cache tag binding",
                    layer_id=layer.layer_id, expert_id=expert_id)
            weight_view = _add_view(
                builder, region_id, core, A.MOE_VIEW_KIND.WEIGHT,
                A.MOE_VIEW_ACCESS.READ, tag_index, 0, weight_bytes,
                A.MOE_SEMANTIC_OWNER.EXPERT, (expert_id,), bytes(32),
                backing_kind=A.MOE_VIEW_BACKING.WEIGHT_CACHE_SLOT)
            waits = [entry]
        else:
            weight = _add_allocation(
                builder, region_id, A.MOE_ALLOCATION_KIND.STREAMED_WEIGHT,
                core, weight_bytes, max(region.scratch_alignment,
                                        config.axi_bus_bytes),
                expert_id=expert_id)
            weight_view = _add_view(
                builder, region_id, core, A.MOE_VIEW_KIND.WEIGHT,
                A.MOE_VIEW_ACCESS.READ, weight, 0, weight_bytes,
                A.MOE_SEMANTIC_OWNER.EXPERT, (expert_id,), bytes(32))
            weight_descriptor = _add_descriptor(
                builder, region_id, A.MOE_DESCRIPTOR_KIND.STREAMED_WEIGHT,
                core, 0, NO_CORE, core, expert_id, 0, bytes(32),
                valid_bytes=weight_bytes, destination_view=(weight_view,))
            weight_event = _add_event(
                builder, region_id, A.MOE_EVENT_ROLE.WEIGHT_READY, core,
                A.MOE_EVENT_PHASE.WEIGHT, expert_id=expert_id)
            weight_command = _add_command(
                builder, region_id, A.MOE_COMMAND_PHASE.STREAMED_WEIGHT_LOAD,
                A.OPCODE.DMA_LOAD, A.MOE_COMMAND_ROLE.STREAMED_WEIGHT_LOAD,
                core, waits=(entry,), signals=(weight_event,),
                descriptors=(weight_descriptor,), expert_id=expert_id,
                payload_bytes=weight_bytes)
            _link_event(builder, weight_event, weight_command)
            activity.commands.append(weight_command)
            waits = [entry, weight_event]
        fill = pad_by_expert.get(expert_id)
        if fill is not None:
            pad_allocation = _add_allocation(
                builder, region_id, A.MOE_ALLOCATION_KIND.PAD_INPUT, core,
                fill["bytes"], config.axi_bus_bytes, expert_id=expert_id)
            pad_view = _add_view(
                builder, region_id, core, A.MOE_VIEW_KIND.PAD_BUFFER,
                A.MOE_VIEW_ACCESS.WRITE, pad_allocation, 0, fill["bytes"],
                A.MOE_SEMANTIC_OWNER.EXPERT, (expert_id,), bytes(32),
                validity=(len(fill["rows"]),))
            pad_descriptor = _add_descriptor(
                builder, region_id, A.MOE_DESCRIPTOR_KIND.PAD_FILL, core, 0,
                NO_CORE, core, expert_id, 0, bytes(32),
                valid_bytes=fill["bytes"], destination_view=(pad_view,),
                fill_content=b"".join(fill["rows"]))
            pad_event = _add_event(
                builder, region_id, A.MOE_EVENT_ROLE.PAD_FILL_DONE, core,
                A.MOE_EVENT_PHASE.FILL, expert_id=expert_id)
            pad_command = _add_command(
                builder, region_id, A.MOE_COMMAND_PHASE.PAD_FILL,
                A.OPCODE.DMA_FILL, A.MOE_COMMAND_ROLE.PAD_FILL, core,
                waits=(entry,), signals=(pad_event,),
                descriptors=(pad_descriptor,), expert_id=expert_id,
                payload_bytes=fill["bytes"])
            _link_event(builder, pad_event, pad_command)
            activity.commands.append(pad_command)
            waits.append(pad_event)
        waits.extend(dispatch_ready.get(expert_id, ()))
        gather = [output_view, weight_view]
        remote = {}
        for chunk in plane.chunks:
            if chunk["phase"] != A.MOE_TRANSFER_PHASE.DISPATCH:
                continue
            if chunk["dst_core"] != core or chunk["expert_id"] != expert_id:
                continue
            handle = remote_rows.get((expert_id, chunk["chunk_ordinal"]))
            if handle is None:
                continue
            if chunk["rows"] != len(chunk["token_refs"]):
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "dispatch chunk rows disagree",
                                  expert_id=expert_id)
            for index, token_ref in enumerate(chunk["token_refs"]):
                remote[token_ref] = (handle[0], index * chunk["row_bytes"],
                                     chunk["row_bytes"])
        for row in real:
            if row["source_core"] == core:
                member = row["member"]
                bound = member.input_allocation != 0
                gather.append(_add_view(
                    builder, region_id, core, A.MOE_VIEW_KIND.MEMBER_INPUT,
                    A.MOE_VIEW_ACCESS.READ,
                    member.input_allocation if bound else None,
                    member.input_offset + row["input_offset"],
                    row["input_row_bytes"], A.MOE_SEMANTIC_OWNER.MEMBER,
                    (member.binding_ordinal,), row["token_ref"],
                    backing_kind=(
                        A.MOE_VIEW_BACKING.STATIC_ALLOCATION if bound
                        else A.MOE_VIEW_BACKING.INSTANCE_MEMBER_BINDING)))
            else:
                entry = remote.get(row["token_ref"])
                if entry is None:
                    raise MeshIrError(
                        "E_MOE_MATERIALIZATION_V",
                        "remote expert row has no dispatch buffer",
                        expert_id=expert_id)
                handle, offset, row_bytes = entry
                gather.append(_add_view(
                    builder, region_id, core, A.MOE_VIEW_KIND.DISPATCH_BUFFER,
                    A.MOE_VIEW_ACCESS.READ, handle, offset, row_bytes,
                    A.MOE_SEMANTIC_OWNER.EXPERT, (expert_id,),
                    row["token_ref"]))
        if fill is not None:
            gather.append(pad_view)
        result_event = _add_event(
            builder, region_id, A.MOE_EVENT_ROLE.EXPERT_RESULT, core,
            A.MOE_EVENT_PHASE.EXPERT, expert_id=expert_id)
        compute = _add_command(
            builder, region_id, A.MOE_COMMAND_PHASE.EXPERT_COMPUTE,
            kernel.expert_opcode, A.MOE_COMMAND_ROLE.EXPERT_COMPUTE, core,
            waits=tuple(waits), signals=(result_event,), views=tuple(gather),
            expert_id=expert_id, kernel_spec_index=layer.kernel_spec_index,
            payload_bytes=len(rows) * output_row_bytes)
        _link_event(builder, result_event, compute)
        activity.commands.append(compute)
        state[expert_id] = {"result": result_event, "output_view": output_view}
    return state


def _install_combine(builder, region, plane, experts, entry, config,
                     transfers, activity):
    ready = {}
    core = region.core_id
    region_id = region.region_id
    for chunk in plane.chunks:
        if chunk["phase"] != A.MOE_TRANSFER_PHASE.COMBINE:
            continue
        if chunk["src_core"] == core and chunk["dst_core"] != core:
            state = experts.get(chunk["expert_id"])
            descriptor = _add_descriptor(
                builder, region_id, A.MOE_DESCRIPTOR_KIND.COMBINE, core,
                chunk["phase"], chunk["src_core"], chunk["dst_core"],
                chunk["expert_id"], chunk["chunk_ordinal"], bytes(32),
                valid_bytes=chunk["row_bytes"],
                source_view=(state["output_view"],) if state else ())
            command = _add_command(
                builder, region_id, A.MOE_COMMAND_PHASE.COMBINE_PUSH,
                A.OPCODE.DMA_P2P_PUSH, A.MOE_COMMAND_ROLE.COMBINE_SENDER,
                core,
                waits=(state["result"],) if state else (entry,),
                descriptors=(descriptor,), transfers=(transfers[id(chunk)],),
                src_core=chunk["src_core"], dst_core=chunk["dst_core"],
                expert_id=chunk["expert_id"],
                chunk_ordinal=chunk["chunk_ordinal"],
                payload_bytes=chunk["row_bytes"])
            activity.commands.append(command)
        if chunk["dst_core"] == core and chunk["src_core"] != core:
            allocation = _add_allocation(
                builder, region_id, A.MOE_ALLOCATION_KIND.COMBINE_REMOTE,
                core, chunk["row_bytes"], config.axi_bus_bytes,
                expert_id=chunk["expert_id"], source_core=chunk["src_core"],
                phase=chunk["chunk_ordinal"])
            view = _add_view(
                builder, region_id, core, A.MOE_VIEW_KIND.COMBINE_BUFFER,
                A.MOE_VIEW_ACCESS.READ_WRITE, allocation, 0,
                chunk["row_bytes"], A.MOE_SEMANTIC_OWNER.REGION,
                (region_id,), bytes(32))
            descriptor = _add_descriptor(
                builder, region_id, A.MOE_DESCRIPTOR_KIND.COMBINE, core,
                chunk["phase"], chunk["src_core"], chunk["dst_core"],
                chunk["expert_id"], chunk["chunk_ordinal"], bytes(32),
                valid_bytes=chunk["row_bytes"], destination_view=(view,))
            event = _add_event(
                builder, region_id, A.MOE_EVENT_ROLE.COMBINE_READY, core,
                A.MOE_EVENT_PHASE.COMBINE, expert_id=chunk["expert_id"],
                src_core=chunk["src_core"], dst_core=chunk["dst_core"],
                chunk_ordinal=chunk["chunk_ordinal"])
            command = _add_command(
                builder, region_id, A.MOE_COMMAND_PHASE.COMBINE_WAIT,
                A.OPCODE.RECV_WAIT, A.MOE_COMMAND_ROLE.COMBINE_RECEIVER, core,
                waits=(entry,), signals=(event,),
                descriptors=(descriptor,), transfers=(transfers[id(chunk)],),
                src_core=chunk["src_core"], dst_core=chunk["dst_core"],
                expert_id=chunk["expert_id"],
                chunk_ordinal=chunk["chunk_ordinal"],
                payload_bytes=chunk["row_bytes"])
            _link_event(builder, event, command)
            activity.commands.append(command)
            ready.setdefault(chunk["expert_id"], []).append(event)
    return ready


def _install_tokens(builder, layer, region, plane, members, placement, entry,
                    experts, combine_ready, activity) -> None:
    core = region.core_id
    region_id = region.region_id
    local = [record for record in plane.fan_in
             if members[record["member"]].core_id == core]
    if not local:
        return
    row_bytes = max(members[record["member"]].output_token_bytes
                    for record in local)
    accumulator = _add_allocation(
        builder, region_id, A.MOE_ALLOCATION_KIND.REDUCE_OUTPUT, core,
        row_bytes * len(local), region.scratch_alignment)
    for row_ordinal, record in enumerate(local):
        member = members[record["member"]]
        offset = row_ordinal * row_bytes
        token_ref = record["uid"].encode()
        bound = member.output_allocation != 0
        if not bound and record["fan_in"] == 0:
            raise MeshIrError(
                "E_MOE_MATERIALIZATION_V",
                "a dropped-token fill needs a bound member output",
                member=member.member_identity)
        output_view = _add_view(
            builder, region_id, core, A.MOE_VIEW_KIND.MEMBER_OUTPUT,
            A.MOE_VIEW_ACCESS.WRITE,
            member.output_allocation if bound else None,
            member.output_offset +
            member.output_row_offset(member.local_ordinal(record["uid"])),
            member.output_token_bytes, A.MOE_SEMANTIC_OWNER.MEMBER,
            (member.binding_ordinal,), token_ref,
            backing_kind=(A.MOE_VIEW_BACKING.STATIC_ALLOCATION if bound
                          else A.MOE_VIEW_BACKING.INSTANCE_MEMBER_BINDING))
        if record["fan_in"] == 0:
            fill_payload = next(
                (fill["payload"] for fill in plane.drop_fills
                 if fill["uid"].sort_key() == record["uid"].sort_key()),
                b"")
            descriptor = _add_descriptor(
                builder, region_id, A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL,
                core, 0, NO_CORE, core, NO_EXPERT, 0, token_ref,
                valid_bytes=layer.output_token_bytes,
                destination_view=(output_view,), fill_content=fill_payload)
            event = _add_event(
                builder, region_id, A.MOE_EVENT_ROLE.TOKEN_OUTPUT_READY, core,
                A.MOE_EVENT_PHASE.REDUCE, token_ref=token_ref)
            command = _add_command(
                builder, region_id, A.MOE_COMMAND_PHASE.DROPPED_TOKEN_FILL,
                A.OPCODE.DMA_FILL, A.MOE_COMMAND_ROLE.DROPPED_TOKEN_FILL,
                core, waits=(entry,), signals=(event,),
                descriptors=(descriptor,), token_ref=token_ref,
                payload_bytes=layer.output_token_bytes)
            _link_event(builder, event, command)
            activity.commands.append(command)
            continue
        waits = [entry]
        views = [output_view]
        for expert_id in record["accepted_experts"]:
            if placement[expert_id] == core:
                waits.append(experts[expert_id]["result"])
                views.append(experts[expert_id]["output_view"])
            else:
                for event in combine_ready.get(expert_id, ()):
                    waits.append(event)
        views.append(_add_view(
            builder, region_id, core, A.MOE_VIEW_KIND.REDUCE_ACCUMULATOR,
            A.MOE_VIEW_ACCESS.READ_WRITE, accumulator, offset,
            member.output_token_bytes, A.MOE_SEMANTIC_OWNER.TOKEN,
            record["token_key"], token_ref))
        event = _add_event(
            builder, region_id, A.MOE_EVENT_ROLE.TOKEN_OUTPUT_READY, core,
            A.MOE_EVENT_PHASE.REDUCE, token_ref=token_ref)
        copy = record["fan_in"] == 1
        command = _add_command(
            builder, region_id,
            A.MOE_COMMAND_PHASE.COPY_THROUGH if copy
            else A.MOE_COMMAND_PHASE.LOCAL_REDUCE,
            A.OPCODE.LOCAL_REDUCE,
            A.MOE_COMMAND_ROLE.COPY_THROUGH if copy
            else A.MOE_COMMAND_ROLE.LOCAL_REDUCE, core, waits=tuple(waits),
            signals=(event,), views=tuple(views), token_ref=token_ref,
            payload_bytes=member.output_token_bytes)
        _link_event(builder, event, command)
        activity.commands.append(command)


def _install_terminal(builder, region, entry, activity) -> tuple:
    region_id = region.region_id
    has_work = bool(activity.commands)
    waits = tuple(activity.commands) if has_work else (entry,)
    event = _add_event(
        builder, region_id,
        A.MOE_EVENT_ROLE.REGION_TERMINAL if has_work
        else A.MOE_EVENT_ROLE.EMPTY_REGION_TERMINAL, region.core_id,
        A.MOE_EVENT_PHASE.EXIT)
    command = _add_command(
        builder, region_id, A.MOE_COMMAND_PHASE.EXIT_SIGNAL,
        A.OPCODE.EVENT_SIGNAL,
        A.MOE_COMMAND_ROLE.REGION_TERMINAL if has_work
        else A.MOE_COMMAND_ROLE.EMPTY_REGION_TERMINAL, region.core_id,
        waits=waits, signals=(event,))
    _link_event(builder, event, command)
    return event


def _install_group_exit(builder, regions, terminals) -> None:
    coordinator = min(regions, key=lambda region: region.core_id)
    waits = tuple(terminals[region.region_id] for region in regions)
    event = _add_event(builder, GROUP_REGION, A.MOE_EVENT_ROLE.OVERLAY_EXIT,
                       coordinator.core_id, A.MOE_EVENT_PHASE.EXIT)
    command = _add_command(
        builder, GROUP_REGION, A.MOE_COMMAND_PHASE.EXIT_SIGNAL,
        A.OPCODE.EVENT_SIGNAL, A.MOE_COMMAND_ROLE.GROUP_EXIT,
        coordinator.core_id, waits=waits, signals=(event,))
    _link_event(builder, event, command)


def materialization_projection(overlay: Overlay, plane,
                              weight_bindings=()) -> dict:
    return {
        "projection_schema_version": 1,
        "layer_id": plane.layer_id,
        "weight_bindings": weight_binding_projection(weight_bindings),
        "data_plane": data_plane_projection(plane),
        "allocations": _project(overlay, A.MESH_OBJECT_KIND.ALLOCATION,
                                _allocation_projection),
        "views": _project(overlay, A.MESH_OBJECT_KIND.VIEW,
                          _view_projection),
        "commands": _project(overlay, A.MESH_OBJECT_KIND.COMMAND,
                             _command_projection),
        "events": _project(overlay, A.MESH_OBJECT_KIND.EVENT,
                           _event_projection),
        "descriptors": _project(overlay, A.MESH_OBJECT_KIND.DESCRIPTOR,
                                _descriptor_projection),
        "transfers": _project(overlay, A.MESH_OBJECT_KIND.TRANSFER,
                              _transfer_projection),
    }


WEIGHT_RESIDENCY_NAMES = ("streamed", "hit", "attach", "new_fill")


def weight_binding_projection(bindings) -> list:
    rows = []
    for binding in bindings:
        residency = binding["residency"]
        if residency not in WEIGHT_RESIDENCY_NAMES:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "unknown weight residency outcome",
                              residency=residency)
        fill_ref = binding.get("fill_ref")
        if residency in ("attach", "new_fill") and fill_ref is None:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "an active weight fill needs its fill reference")
        if residency == "hit" and fill_ref is not None:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "a residency hit never references a fill")
        rows.append({
            "layer_id": binding["layer_id"],
            "expert_id": binding["expert_id"],
            "weight_tag_index": binding["weight_tag_index"],
            "residency": residency,
            "slot_id": binding.get("slot_id"),
            "fill_ref": fill_ref,
        })
    return sorted(rows, key=lambda row: (row["layer_id"], row["expert_id"],
                                         row["weight_tag_index"]))


def _project(overlay, kind, projector) -> list:
    return sorted((projector(record) for record in overlay.of_kind(kind)),
                  key=lambda item: (item["region_id"], item["ordinal"]))


def _allocation_projection(record) -> dict:
    return {
        "region_id": record.key.region_id, "ordinal": record.key.ordinal,
        "owner_core": record.owner_core, "kind": record.allocation_kind,
        "expert_or_ffff": record.expert_id,
        "source_core_or_ffff": record.source_core, "phase": record.phase,
        "bytes": record.bytes, "alignment": record.alignment,
        "validity_kind": record.validity.kind,
        "valid_bytes": record.validity.valid_bytes,
        "row_bytes": record.validity.row_bytes,
        "row_count": record.validity.row_count,
        "bitmap_lowerhex": record.validity.bitmap_lowerhex,
    }


def _view_backing_fields(record) -> tuple:
    if record.backing_kind == A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION:
        return int(record.backing_ref[0]), int(record.backing_ref[1])
    if record.backing_kind in (A.MOE_VIEW_BACKING.STATIC_ALLOCATION,
                               A.MOE_VIEW_BACKING.WEIGHT_CACHE_SLOT):
        return 0, int(record.backing_ref[0])
    return 0, 0


def _view_projection(record) -> dict:
    ref_region, ref_ordinal = _view_backing_fields(record)
    return {
        "region_id": record.key.region_id, "ordinal": record.key.ordinal,
        "owner_core": record.owner_core, "view_kind": record.view_kind,
        "access": record.access, "backing_kind": record.backing_kind,
        "backing_ref": list(record.backing_ref), "offset": record.offset,
        "ref_region": ref_region, "ref_ordinal": ref_ordinal,
        "bytes": record.bytes,
        "semantic_owner_kind": record.semantic_owner_kind,
        "semantic_owner_ref": list(record.semantic_owner_ref),
        "validity_slices": [list(item) for item in record.validity_slices],
        "token_lowerhex": bytes(record.token_ref).hex(),
    }


def _command_projection(record) -> dict:
    descriptor = record.descriptor_refs[0] if record.descriptor_refs else None
    return {
        "ref_region": descriptor[0] if descriptor else 0,
        "ref_ordinal": descriptor[2] if descriptor else 0,
        "region_id": record.key.region_id, "ordinal": record.key.ordinal,
        "owner_core": record.owner_core, "phase": record.phase,
        "opcode": record.opcode, "role": record.role,
        "src_core": record.src_core, "dst_core": record.dst_core,
        "expert_id": record.expert_id, "chunk_ordinal": record.chunk_ordinal,
        "kernel_spec_index": record.kernel_spec_index,
        "payload_bytes": record.payload_bytes,
        "wait_refs": [list(item) for item in record.wait_refs],
        "signal_refs": [list(item) for item in record.signal_refs],
        "view_refs": [list(item) for item in record.view_refs],
        "token_lowerhex": bytes(record.token_ref).hex(),
    }


def _event_projection(record) -> dict:
    return {
        "region_id": record.key.region_id, "ordinal": record.key.ordinal,
        "owner_core": record.owner_core, "event_phase": record.event_phase,
        "phase": record.phase, "src_core": record.src_core,
        "dst_core": record.dst_core, "expert_id": record.expert_id,
        "chunk_ordinal": record.chunk_ordinal, "role": record.role,
        "producers": [list(item) for item in record.producers],
        "token_lowerhex": bytes(record.token_ref).hex(),
    }


def _endpoint_ref(refs) -> tuple:
    if not refs:
        return (0, 0)
    ref = refs[0]
    return (int(ref[0]), int(ref[2]))


def _descriptor_projection(record) -> dict:
    src_region, src_ordinal = _endpoint_ref(record.source_view_ref)
    dst_region, dst_ordinal = _endpoint_ref(record.destination_view_ref)
    return {
        "fill_content_hex": bytes(record.fill_content).hex(),
        "src_view_region": src_region, "src_view_ordinal": src_ordinal,
        "dst_view_region": dst_region, "dst_view_ordinal": dst_ordinal,
        "region_id": record.key.region_id, "ordinal": record.key.ordinal,
        "owner_core": record.owner_core, "moe_kind": record.moe_kind,
        "traffic_class": record.traffic_class(), "phase": record.phase,
        "src_core": record.src_core, "dst_core": record.dst_core,
        "expert_id": record.expert_id,
        "chunk_ordinal": record.chunk_ordinal,
        "valid_bytes": record.valid_bytes,
        "source_view_ref": list(record.source_view_ref),
        "destination_view_ref": list(record.destination_view_ref),
        "transfer_ref": list(record.transfer_ref),
        "token_lowerhex": bytes(record.token_ref).hex(),
    }


def _transfer_projection(record) -> dict:
    return {
        "region_id": record.key.region_id, "ordinal": record.key.ordinal,
        "phase": record.phase, "src_core": record.src_core,
        "dst_core": record.dst_core, "expert_id": record.expert_id,
        "chunk_ordinal": record.chunk_ordinal,
        "logical_bytes": record.logical_bytes,
    }


def materialization_digest(overlay: Overlay, plane,
                           config: MaterializeConfig,
                           weight_bindings=()) -> str:
    projection = dict(materialization_projection(overlay, plane,
                                                weight_bindings))
    projection["workload_plan_digest"] = config.workload_plan_digest.hex()
    projection["mesh_program_digest"] = config.program_semantic_digest.hex()
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


OBJECT_KINDS = (A.MESH_OBJECT_KIND.ALLOCATION, A.MESH_OBJECT_KIND.VIEW,
                A.MESH_OBJECT_KIND.COMMAND, A.MESH_OBJECT_KIND.EVENT,
                A.MESH_OBJECT_KIND.DESCRIPTOR, A.MESH_OBJECT_KIND.TRANSFER)


def overlay_objects_projection(overlay: Overlay) -> dict:
    return {
        "projection_schema_version": 1,
        "layer_id": overlay.layer_id,
        "allocations": _project(overlay, A.MESH_OBJECT_KIND.ALLOCATION,
                                _allocation_projection),
        "views": _project(overlay, A.MESH_OBJECT_KIND.VIEW,
                          _view_projection),
        "commands": _project(overlay, A.MESH_OBJECT_KIND.COMMAND,
                             _command_projection),
        "events": _project(overlay, A.MESH_OBJECT_KIND.EVENT,
                           _event_projection),
        "descriptors": _project(overlay, A.MESH_OBJECT_KIND.DESCRIPTOR,
                                _descriptor_projection),
        "transfers": _project(overlay, A.MESH_OBJECT_KIND.TRANSFER,
                              _transfer_projection),
    }


def overlay_objects_digest(overlay: Overlay) -> str:
    return hashlib.sha256(
        canonical_json_bytes(overlay_objects_projection(overlay))).hexdigest()


COUNT_NAMES = {
    A.MESH_OBJECT_KIND.ALLOCATION: "allocations",
    A.MESH_OBJECT_KIND.VIEW: "views",
    A.MESH_OBJECT_KIND.COMMAND: "commands",
    A.MESH_OBJECT_KIND.EVENT: "events",
    A.MESH_OBJECT_KIND.DESCRIPTOR: "descriptors",
    A.MESH_OBJECT_KIND.TRANSFER: "transfers",
}


def record_counts(overlay: Overlay) -> dict:
    return {COUNT_NAMES[kind]: len(overlay.of_kind(kind))
            for kind in OBJECT_KINDS}


WIRE_FIELDS = ("kind", "region_id", "ordinal", "owner_core",
               "secondary_kind", "expert_id", "src_core", "dst_core",
               "chunk_ordinal", "phase", "role", "access", "bytes",
               "alignment", "offset", "ref_region", "ref_ordinal",
               "backing_kind", "semantic_owner_kind",
               "semantic_owner_ref0", "validity_extent",
               "wait_count", "signal_count")


DIGEST_GROUP_ORDER = (A.MESH_OBJECT_KIND.ALLOCATION,
                      A.MESH_OBJECT_KIND.VIEW,
                      A.MESH_OBJECT_KIND.COMMAND,
                      A.MESH_OBJECT_KIND.EVENT,
                      A.MESH_OBJECT_KIND.DESCRIPTOR,
                      A.MESH_OBJECT_KIND.TRANSFER)


def overlay_wire_bytes(entries) -> bytes:
    import struct

    ordered = sorted(entries, key=lambda entry: (
        DIGEST_GROUP_ORDER.index(entry["kind"]),
        0 if entry["kind"] == A.MESH_OBJECT_KIND.TRANSFER
        else entry["region_id"],
        entry["ordinal"]))
    body = bytearray()
    for entry in ordered:
        body += struct.pack(
            "<HIIIIIIIIIIIIIIIIIIIIII",
            entry["kind"], entry["region_id"], entry["ordinal"],
            entry["owner_core"], entry["secondary_kind"],
            _u16_field(entry["expert_id"]), _u16_field(entry["src_core"]),
            _u16_field(entry["dst_core"]), entry["chunk_ordinal"],
            entry["phase"], entry["role"], entry["access"], entry["bytes"],
            entry["alignment"], entry["offset"], entry["ref_region"],
            entry["ref_ordinal"], entry["backing_kind"],
            entry["semantic_owner_kind"], entry["semantic_owner_ref0"],
            entry["validity_extent"], entry["wait_count"],
            entry["signal_count"])
        token = bytes.fromhex(entry["token_hex"] or "")
        if len(token) not in (0, 32):
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "token discriminator must be 32 B")
        body += token.ljust(32, b"\0")
    return bytes(body)


def _u16_field(value) -> int:
    return 0xFFFF if value in (None, "") else int(value)


def overlay_wire_digest(entries) -> str:
    return hashlib.sha256(overlay_wire_bytes(entries)).hexdigest()


OVERLAY_IMAGE_MAGIC = b"MOEV"
OVERLAY_IMAGE_VERSION = 4
OVERLAY_IMAGE_HEADER_BYTES = 64
OVERLAY_IMAGE_PROGRAM_DIGEST_BYTES = 32
OVERLAY_REFS_PER_OBJECT = 32
OVERLAY_IMAGE_RECORD_BYTES = 138 + OVERLAY_REFS_PER_OBJECT * 12 * 3
OVERLAY_IMAGE_SCRATCH_BYTES = 24
OVERLAY_IMAGE_PAYLOAD_BYTES = 24


def overlay_image_bytes(entries, layer_id: int, scratch=(), fill_mode=0,
                        program_digest=b"") -> bytes:
    """Fixed-width overlay image the gem5 runtime loads without a JSON parser.

    Each record is the 122 B canonical digest layout followed by the explicit
    wait/signal references; the digest covers only the first part.  The
    trailing scratch table resolves every allocation ordinal to its absolute
    SRAM interval so the runtime can execute overlay DMA and SRAM accesses at
    real addresses.
    """
    import struct

    ordered = sorted(entries, key=lambda entry: (
        DIGEST_GROUP_ORDER.index(entry["kind"]),
        0 if entry["kind"] == A.MESH_OBJECT_KIND.TRANSFER
        else entry["region_id"],
        entry["ordinal"]))
    rows = sorted(scratch)
    payloads = []
    blob = bytearray()
    for entry in ordered:
        content = bytes.fromhex(entry.get("fill_content_hex", "") or "")
        if not content:
            continue
        if len(content) != entry["bytes"]:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "fill content must match the record bytes",
                              kind=entry["kind"],
                              ordinal=entry["ordinal"])
        payloads.append((entry["region_id"], entry["ordinal"], len(blob),
                         len(content)))
        blob += content
    body = bytearray()
    body += OVERLAY_IMAGE_MAGIC
    body += struct.pack("<IIII", OVERLAY_IMAGE_VERSION, layer_id,
                        len(ordered), OVERLAY_REFS_PER_OBJECT)
    body += struct.pack("<III", len(rows), int(fill_mode), len(payloads))
    if program_digest and len(program_digest) != \
            OVERLAY_IMAGE_PROGRAM_DIGEST_BYTES:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "program image digest must be 32 bytes")
    body += bytes(program_digest).ljust(
        OVERLAY_IMAGE_PROGRAM_DIGEST_BYTES, b"\0")
    for entry in ordered:
        body += overlay_wire_bytes([entry])
        body += struct.pack("<IIII", entry.get("src_view_region", 0),
                            entry.get("src_view_ordinal", 0),
                            entry.get("dst_view_region", 0),
                            entry.get("dst_view_ordinal", 0))
        for refs in (entry["wait_refs"], entry["signal_refs"],
                     entry.get("view_refs", ())):
            if len(refs) > OVERLAY_REFS_PER_OBJECT:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "overlay object has too many references",
                                  kind=entry["kind"], ordinal=entry["ordinal"])
            slots = list(refs)
            while len(slots) < OVERLAY_REFS_PER_OBJECT:
                slots.append([0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF])
            for region, kind, ordinal in slots:
                body += struct.pack("<III", region, kind, ordinal)
    for region_id, ordinal, offset, byte_count in rows:
        body += struct.pack("<IIQQ", region_id, ordinal, offset, byte_count)
    for region_id, ordinal, offset, byte_count in payloads:
        body += struct.pack("<IIQQ", region_id, ordinal, offset, byte_count)
    body += blob
    return bytes(body)


DMA_OPCODES = frozenset((A.OPCODE.DMA_LOAD, A.OPCODE.DMA_STORE,
                         A.OPCODE.DMA_P2P_PUSH, A.OPCODE.DMA_FILL,
                         A.OPCODE.RECV_WAIT))


def _operand_directions(role: int, view: dict) -> tuple:
    kind = view["secondary_kind"]
    if role == A.MOE_COMMAND_ROLE.EXPERT_COMPUTE:
        result = kind == A.MOE_VIEW_KIND.EXPERT_OUTPUT
        return (not result, result)
    if role == A.MOE_COMMAND_ROLE.COPY_THROUGH:
        # The identity copy reads its unique gather row and writes the member
        # row plus the token's reduce output; nothing accumulates.
        result = kind in (A.MOE_VIEW_KIND.MEMBER_OUTPUT,
                          A.MOE_VIEW_KIND.REDUCE_ACCUMULATOR)
        return (not result, result)
    if role == A.MOE_COMMAND_ROLE.LOCAL_REDUCE:
        result = kind == A.MOE_VIEW_KIND.MEMBER_OUTPUT
        accumulates = kind == A.MOE_VIEW_KIND.REDUCE_ACCUMULATOR
        return (not result, result or accumulates)
    return (view["access"] != A.MOE_VIEW_ACCESS.WRITE,
            view["access"] != A.MOE_VIEW_ACCESS.READ)


def overlay_service_expectations(objects) -> dict:
    """SRAM service bytes the runtime must charge, derived from the frozen
    canonical object graph: {core: {region: {view_kind: (read, write)}}}."""
    views = {(row["region_id"], row["ordinal"]): row for row in objects
             if row["kind"] == A.MESH_OBJECT_KIND.VIEW}
    descriptors = {(row["region_id"], row["ordinal"]): row for row in objects
                   if row["kind"] == A.MESH_OBJECT_KIND.DESCRIPTOR}
    per_core = {}
    for row in objects:
        if row["kind"] != A.MESH_OBJECT_KIND.COMMAND:
            continue
        core = row["owner_core"]
        payload = row.get("bytes", 0)
        descriptor = descriptors.get((row.get("ref_region", 0),
                                      row.get("ref_ordinal", 0)))
        transfers = []
        if row["secondary_kind"] in DMA_OPCODES and descriptor is not None:
            for region, ordinal, reading, writing in (
                    (descriptor.get("src_view_region", 0),
                     descriptor.get("src_view_ordinal", 0), True, False),
                    (descriptor.get("dst_view_region", 0),
                     descriptor.get("dst_view_ordinal", 0), False, True)):
                view = views.get((region, ordinal))
                if view is not None:
                    transfers.append((view, reading, writing, payload))
        else:
            for ref in row.get("view_refs", ()):
                view = views.get((ref[0], ref[2]))
                if view is None:
                    continue
                reading, writing = _operand_directions(row["role"], view)
                if row["role"] == A.MOE_COMMAND_ROLE.EXPERT_COMPUTE:
                    charged = view["bytes"]
                else:
                    charged = payload
                transfers.append((view, reading, writing, charged))
        for view, reading, writing, byte_count in transfers:
            # A cache-slot weight view is resident SRAM: the expert reads the
            # line through the same operand path, so it is served like any
            # other view.  Only unbound member bindings have no local address.
            if view["backing_kind"] == \
                    A.MOE_VIEW_BACKING.INSTANCE_MEMBER_BINDING:
                continue
            if view["owner_core"] != core:
                continue
            slot = per_core.setdefault(core, {}).setdefault(
                row["region_id"], {}).setdefault(view["secondary_kind"],
                                                [0, 0])
            if reading:
                slot[0] += byte_count
            if writing:
                slot[1] += byte_count
    return per_core


def payload_digest(data: bytes) -> str:
    """The engine's two-word running content digest, so a frozen fill row can
    be compared byte for byte with the runtime's actual payload."""
    first = 0xCBF29CE484222325
    second = 0x9E3779B97F4A7C15
    for byte in data:
        first = ((first ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        second = ((second + ((first >> 31) ^ byte)) *
                  0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    if first == 0 and second == 0:
        return "0000000000000000-0000000000000000"
    return "%016x-%016x" % (first, second)

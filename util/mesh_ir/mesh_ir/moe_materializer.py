"""MoE route materializer data plane (contract 7.9/7.9.1).

Turns a frozen selection plan plus capacity disposition into the exact
batch-owned data plane: route-buffer records, dispatch/combine row moves
with greedy coalescing, expert row layout with padding, fan-in per token
and the deterministic pad/drop fill patterns.  Overlay objects (commands,
descriptors, events and their DAG) are installed on top of this projection
with two-phase reference resolution.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError, canonical_json_bytes
from mesh_ir.moe_fill import FUNCTIONAL_BYTES, drop_fill_bytes, \
    encode_route_entry, pad_fill_bytes
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


@dataclass(frozen=True)
class MemberSlice:
    member_identity: str
    member_request_id: int
    core_id: int
    first_semantic_token: int
    valid_token_count: int
    input_allocation: int
    input_offset: int
    input_token_bytes: int
    output_allocation: int
    output_offset: int
    output_token_bytes: int
    binding_ordinal: int = 1

    def local_ordinal(self, uid) -> int:
        ordinal = uid.semantic_ordinal() - self.first_semantic_token
        if ordinal < 0 or ordinal >= self.valid_token_count:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "token does not belong to its member slice",
                              member=self.member_identity)
        return ordinal

    def input_row_offset(self, ordinal: int) -> int:
        return self.input_offset + ordinal * self.input_token_bytes

    def output_row_offset(self, ordinal: int) -> int:
        return self.output_offset + ordinal * self.output_token_bytes


def member_slice(program, layer, member_identity, member_request_id, core_id,
                 valid_token_count=1, first_semantic_token=0) -> MemberSlice:
    source = program.shard_of(A.TENSOR_ROLE.INPUT, core_id)
    if source is None:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "member core owns no token shard", core=core_id)
    sink = program.shard_of(A.TENSOR_ROLE.OUTPUT, core_id)
    if sink is not None and sink.span_bytes < layer.output_token_bytes:
        sink = None
    return MemberSlice(
        member_identity=member_identity, member_request_id=member_request_id,
        core_id=core_id, first_semantic_token=first_semantic_token,
        valid_token_count=valid_token_count,
        input_allocation=source.allocation_id, input_offset=0,
        input_token_bytes=layer.token_bytes,
        output_allocation=sink.allocation_id if sink is not None else 0,
        output_offset=0, output_token_bytes=layer.output_token_bytes)


@dataclass(frozen=True)
class MaterializeConfig:
    workload_plan_digest: bytes
    program_semantic_digest: bytes
    p2p_chunk_bytes: int
    weight_residency: str = "streamed"
    fill_mode: int = FUNCTIONAL_BYTES
    axi_bus_bytes: int = 32


@dataclass(frozen=True)
class RowMove:
    phase: int
    src_core: int
    dst_core: int
    expert_id: int
    token_ref: bytes
    src_allocation: int
    src_offset: int
    dst_allocation: int
    dst_offset: int
    row_bytes: int


@dataclass
class MaterializedDataPlane:
    layer_id: int
    route_buffers: dict
    chunks: tuple
    expert_rows: tuple
    fan_in: tuple
    pad_fills: tuple
    drop_fills: tuple
    digest: str

    def canonical(self) -> dict:
        return data_plane_projection(self)


def materialize_data_plane(layer, kernel, plan, capacity, members, placement,
                           config: MaterializeConfig) -> MaterializedDataPlane:
    if config.weight_residency not in ("streamed", "cached"):
        raise MeshIrError("E_MOE_PROFILE", "unknown weight residency")
    if config.p2p_chunk_bytes <= 0:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "p2p chunk bytes must be positive")
    members_by_identity = {member.member_identity: member
                           for member in members}
    for expert_id, core_id in enumerate(placement):
        if core_id > 0xFFFE:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "expert placement core is not admissible",
                              expert_id=expert_id)
    for member in members:
        if member.input_token_bytes != layer.token_bytes or \
                member.output_token_bytes != layer.output_token_bytes:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "member rows must match the layer token rows",
                              member=member.member_identity)
    expert_rows = _expert_rows(layer, capacity, members_by_identity)
    fan_in = _fan_in_by_token(kernel, plan, capacity)
    dispatch = _dispatch_moves(capacity, members_by_identity, placement,
                               expert_rows)
    combine = _combine_moves(capacity, members_by_identity, placement,
                             expert_rows)
    chunks = _coalesce(dispatch, config.p2p_chunk_bytes,
                       A.MOE_TRANSFER_PHASE.DISPATCH) + \
        _coalesce(combine, config.p2p_chunk_bytes,
                  A.MOE_TRANSFER_PHASE.COMBINE)
    route_buffers = _route_buffers(plan, capacity, members_by_identity,
                                   placement)
    pad_fills = _pad_fills(layer, capacity, config)
    drop_fills = _drop_fills(layer, capacity, members_by_identity, config)
    plane = MaterializedDataPlane(
        layer_id=layer.layer_id, route_buffers=route_buffers, chunks=chunks,
        expert_rows=expert_rows, fan_in=fan_in, pad_fills=pad_fills,
        drop_fills=drop_fills, digest="")
    plane.digest = data_plane_digest(plane, config)
    return plane


def _expert_rows(layer, capacity, members) -> tuple:
    rows = {}
    for route in capacity.accepted:
        member = members[route.member_identity]
        rows.setdefault(route.selected_expert_id, []).append(
            (route.uid.sort_key(), member, route))
    out = []
    for expert_id in range(layer.expert_count):
        real = sorted(rows.get(expert_id, ()), key=lambda item: item[0])
        padded = capacity.padded_slots_by_expert[expert_id]
        if not real and not padded:
            continue
        for ordinal, (_, member, route) in enumerate(real):
            local = member.local_ordinal(route.uid)
            out.append({
                "expert_id": expert_id, "row_ordinal": ordinal,
                "kind": "real", "member": member, "uid": route.uid,
                "token_ref": route.uid.encode(), "topk_slot": route.topk_slot,
                "source_core": member.core_id,
                "input_row_bytes": member.input_token_bytes,
                "output_row_bytes": member.output_token_bytes,
                "input_offset": member.input_row_offset(local),
                "output_offset": member.output_row_offset(local),
            })
        for padding in range(padded):
            out.append({
                "expert_id": expert_id, "row_ordinal": len(real) + padding,
                "kind": "pad", "member": None, "uid": None,
                "token_ref": bytes(32), "topk_slot": 0,
                "source_core": NO_CORE,
                "input_row_bytes": layer.token_bytes,
                "output_row_bytes": layer.output_token_bytes,
                "input_offset": len(real) * layer.token_bytes,
                "output_offset": (len(real) + padding) *
                layer.output_token_bytes,
            })
    return tuple(out)


def _fan_in_by_token(kernel, plan, capacity) -> tuple:
    accepted = {}
    for route in capacity.accepted:
        accepted.setdefault(route.uid.sort_key(), []).append(route)
    every = {}
    for route in plan.routes:
        every.setdefault(route.uid.sort_key(), []).append(route)
    out = []
    for key, routes in sorted(every.items()):
        live = accepted.get(key, ())
        out.append({
            "token_key": key,
            "uid": routes[0].uid,
            "member": routes[0].member_identity,
            "fan_in": len(live),
            "accepted_slots": tuple(sorted(route.topk_slot for route in live)),
            "accepted_experts": tuple(sorted(
                route.selected_expert_id for route in live)),
            "dropped_slots": tuple(sorted(
                route.topk_slot for route in routes if route not in live)),
            "combine_kind": kernel.combine_kind if live else
            A.MOE_COMBINE_KIND.NONE,
        })
        if live and len(live) > 1 and \
                kernel.combine_kind == A.MOE_COMBINE_KIND.NONE:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "fan-in above one needs a combine kind")
    return tuple(out)


def _dispatch_moves(capacity, members, placement, expert_rows) -> tuple:
    return _moves(A.MOE_TRANSFER_PHASE.DISPATCH, capacity, members, placement,
                  expert_rows)


def _combine_moves(capacity, members, placement, expert_rows) -> tuple:
    return _moves(A.MOE_TRANSFER_PHASE.COMBINE, capacity, members, placement,
                  expert_rows)


def _moves(phase, capacity, members, placement, expert_rows) -> tuple:
    rows_by_key = {(row["expert_id"], row["token_ref"]): row
                   for row in expert_rows if row["kind"] == "real"}
    moves = []
    for route in capacity.accepted:
        member = members[route.member_identity]
        expert_core = placement[route.selected_expert_id]
        if expert_core == member.core_id:
            continue
        row = rows_by_key[(route.selected_expert_id, route.uid.encode())]
        local = member.local_ordinal(route.uid)
        if phase == A.MOE_TRANSFER_PHASE.DISPATCH:
            moves.append(RowMove(
                phase=phase, src_core=member.core_id, dst_core=expert_core,
                expert_id=route.selected_expert_id,
                token_ref=route.uid.encode(),
                src_allocation=member.input_allocation,
                src_offset=member.input_row_offset(local),
                dst_allocation=0,
                dst_offset=row["row_ordinal"] * member.input_token_bytes,
                row_bytes=member.input_token_bytes))
        else:
            moves.append(RowMove(
                phase=phase, src_core=expert_core, dst_core=member.core_id,
                expert_id=route.selected_expert_id,
                token_ref=route.uid.encode(),
                src_allocation=0,
                src_offset=row["row_ordinal"] * member.output_token_bytes,
                dst_allocation=member.output_allocation,
                dst_offset=member.output_row_offset(local),
                row_bytes=member.output_token_bytes))
    return tuple(moves)


def _coalesce(moves, chunk_bytes, phase) -> tuple:
    ordered = sorted([move for move in moves if move.phase == phase],
                     key=lambda move: (move.src_core, move.dst_core,
                                       move.expert_id, move.src_offset,
                                       move.token_ref))
    chunks = []
    current = None
    for move in ordered:
        if move.row_bytes > chunk_bytes:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "row is larger than the p2p chunk budget")
        if current is not None and _mergeable(current, move, chunk_bytes):
            current["row_bytes"] += move.row_bytes
            current["rows"] += 1
            current["token_refs"] = current["token_refs"] + (move.token_ref,)
            continue
        current = {
            "phase": move.phase, "src_core": move.src_core,
            "dst_core": move.dst_core, "expert_id": move.expert_id,
            "src_allocation": move.src_allocation,
            "dst_allocation": move.dst_allocation,
            "src_offset": move.src_offset, "dst_offset": move.dst_offset,
            "row_bytes": move.row_bytes, "rows": 1,
            "token_refs": (move.token_ref,),
        }
        chunks.append(current)
    for ordinal, chunk in enumerate(chunks):
        chunk["chunk_ordinal"] = ordinal
    return tuple(chunks)


def _mergeable(current, move, chunk_bytes) -> bool:
    return (current["src_core"] == move.src_core and
            current["dst_core"] == move.dst_core and
            current["expert_id"] == move.expert_id and
            current["src_allocation"] == move.src_allocation and
            current["dst_allocation"] == move.dst_allocation and
            current["src_offset"] + current["row_bytes"] == move.src_offset and
            current["dst_offset"] + current["row_bytes"] == move.dst_offset and
            current["row_bytes"] + move.row_bytes <= chunk_bytes)


def _route_buffers(plan, capacity, members, placement) -> dict:
    dropped = {id(route) for route in capacity.dropped}
    buffers = {}
    for route in plan.routes:
        member = members[route.member_identity]
        accepted = id(route) not in dropped
        entry = encode_route_entry(
            route.uid, member.member_request_id, route.source_rank,
            route.uid.semantic_ordinal(), route.topk_slot,
            route.selected_expert_id,
            route.selected_expert_id if accepted else INVALID_U16,
            placement[route.selected_expert_id] if accepted else INVALID_U16,
            A.ROUTE_DISPOSITION.ACCEPT if accepted
            else A.ROUTE_DISPOSITION.DROP)
        buffers.setdefault(member.core_id, []).append(entry)
    return {core_id: tuple(entries) for core_id, entries in buffers.items()}


def _pad_fills(layer, capacity, config) -> tuple:
    fills = []
    for expert_id, padded in enumerate(capacity.padded_slots_by_expert):
        if padded == 0:
            continue
        rows = tuple(
            pad_fill_bytes(config.program_semantic_digest,
                           config.workload_plan_digest, layer.layer_id,
                           expert_id, padding, layer.token_bytes)
            for padding in range(padded))
        fills.append({"expert_id": expert_id, "rows": rows,
                      "bytes": padded * layer.token_bytes})
    return tuple(fills)


def _drop_fills(layer, capacity, members, config) -> tuple:
    dropped = {}
    for route in capacity.dropped:
        dropped.setdefault(route.uid.sort_key(), []).append(route)
    fills = []
    for _, routes in sorted(dropped.items()):
        entries = [(route.topk_slot, route.selected_expert_id,
                    A.ROUTE_DISPOSITION.DROP)
                   for route in sorted(routes,
                                       key=lambda item: item.topk_slot)]
        payload = drop_fill_bytes(
            config.program_semantic_digest, config.workload_plan_digest,
            routes[0].uid, layer.layer_id, layer.output_token_bytes, entries)
        fills.append({"uid": routes[0].uid,
                      "member": members[routes[0].member_identity],
                      "bytes": layer.output_token_bytes, "payload": payload})
    return tuple(fills)


def data_plane_projection(plane: MaterializedDataPlane) -> dict:
    return {
        "projection_schema_version": 1,
        "layer_id": plane.layer_id,
        "route_buffers": [
            {"core_id": core_id, "entry_count": len(entries),
             "entry_bytes": A.MOE_RUNTIME_ROUTE_ENTRY_BYTES,
             "entries_lowerhex": [entry.hex() for entry in entries]}
            for core_id, entries in sorted(plane.route_buffers.items())
        ],
        "chunks": [
            {"phase": chunk["phase"], "src_core": chunk["src_core"],
             "dst_core": chunk["dst_core"], "expert_id": chunk["expert_id"],
             "chunk_ordinal": chunk["chunk_ordinal"],
             "rows": chunk["rows"], "row_bytes": chunk["row_bytes"],
             "src_allocation": chunk["src_allocation"],
             "src_offset": chunk["src_offset"],
             "dst_allocation": chunk["dst_allocation"],
             "dst_offset": chunk["dst_offset"],
             "token_lowerhex": [ref.hex() for ref in chunk["token_refs"]]}
            for chunk in plane.chunks
        ],
        "expert_rows": [
            {"expert_id": row["expert_id"],
             "row_ordinal": row["row_ordinal"], "kind": row["kind"],
             "source_core": row["source_core"],
             "input_row_bytes": row["input_row_bytes"],
             "output_row_bytes": row["output_row_bytes"],
             "token_lowerhex": row["token_ref"].hex()}
            for row in plane.expert_rows
        ],
        "fan_in": [
            {"token_lowerhex": record["uid"].encode().hex(),
             "member": record["member"], "fan_in": record["fan_in"],
             "accepted_slots": list(record["accepted_slots"]),
             "accepted_experts": list(record["accepted_experts"]),
             "dropped_slots": list(record["dropped_slots"]),
             "combine_kind": record["combine_kind"]}
            for record in plane.fan_in
        ],
        "pad_fills": [
            {"expert_id": fill["expert_id"], "bytes": fill["bytes"],
             "rows_lowerhex": [row.hex() for row in fill["rows"]]}
            for fill in plane.pad_fills
        ],
        "drop_fills": [
            {"token_lowerhex": fill["uid"].encode().hex(),
             "bytes": fill["bytes"], "payload_lowerhex": fill["payload"].hex()}
            for fill in plane.drop_fills
        ],
    }


def data_plane_digest(plane: MaterializedDataPlane,
                      config: MaterializeConfig) -> str:
    projection = data_plane_projection(plane)
    projection["workload_plan_digest"] = config.workload_plan_digest.hex()
    projection["mesh_program_digest"] = config.program_semantic_digest.hex()
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()

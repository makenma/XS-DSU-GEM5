"""Independent MoE traffic oracle (main contract 7.10, coding spec 9).

Every quantity the runtime reports is re-derived here from frozen inputs: the
route plan, the capacity outcome, the member slices, the expert placement, the
program weight binding and the architecture geometry.  Materializer output is
only ever compared against this oracle, never used as its input, so an emitter
bug and a runtime bug cannot cancel out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import FILL_TRAFFIC_DOMAIN

LANE_FIELDS = (
    "dispatch_all_route_bytes",
    "dispatch_local_sram_bytes",
    "dispatch_remote_dma_bytes",
    "combine_all_route_bytes",
    "combine_local_sram_bytes",
    "combine_remote_dma_bytes",
    "padding_compute_slots",
    "padding_fill_sram_wbytes",
    "expert_input_sram_rbytes",
    "expert_output_sram_wbytes",
    "padding_discarded_out_bytes",
    "fully_dropped_fill_sram_wbytes",
    "combine_gather_sram_rbytes",
    "combine_output_sram_wbytes",
    "copy_through_commands",
    "local_reduce_commands",
    "combine_reduce_ops",
    "weight_sram_read_bytes",
    "route_metadata_fill_sram_bytes",
)


@dataclass(frozen=True)
class Packetization:
    data_bytes: int
    burst_beats: int
    header_bytes: int
    flit_bytes: int

    def burst_bytes(self) -> int:
        return self.data_bytes * self.burst_beats

    def bursts(self, payload_bytes: int) -> int:
        return -(-payload_bytes // self.burst_bytes())

    def wire_bytes(self, payload_bytes: int) -> int:
        total = payload_bytes + self.header_bytes
        return -(-total // self.flit_bytes) * self.flit_bytes


@dataclass(frozen=True)
class MeshTopology:
    rows: int
    cols: int

    def router_of(self, core_id: int) -> tuple:
        if not 0 <= core_id < self.rows * self.cols:
            raise MeshIrError("E_MOE_TRAFFIC", "core is outside the mesh",
                              core_id=core_id)
        return core_id // self.cols, core_id % self.cols

    def hops(self, src_core: int, dst_core: int) -> int:
        src = self.router_of(src_core)
        dst = self.router_of(dst_core)
        return abs(src[0] - dst[0]) + abs(src[1] - dst[1])

    def link(self, src_core: int, dst_core: int) -> tuple:
        return tuple(sorted((self.router_of(src_core),
                             self.router_of(dst_core))))


@dataclass
class MoeTrafficLanes:
    lanes: dict = field(default_factory=dict)
    per_peer_logical_bytes: dict = field(default_factory=dict)
    per_link_wire_bytes: dict = field(default_factory=dict)
    token_fan_in: dict = field(default_factory=dict)
    route_entries_by_core: dict = field(default_factory=dict)

    def canonical(self) -> dict:
        return {
            "lanes": dict(self.lanes),
            "per_peer_logical_bytes": [
                {"phase": phase, "src_core": src, "dst_core": dst,
                 "bytes": byte_count}
                for (phase, src, dst), byte_count in sorted(
                    self.per_peer_logical_bytes.items())],
            "per_link_wire_bytes": [
                {"src_router": list(src), "dst_router": list(dst),
                 "bytes": byte_count}
                for (src, dst), byte_count in sorted(
                    self.per_link_wire_bytes.items())],
            "route_entries_by_core": [
                {"core_id": core_id, "entries": count}
                for core_id, count in sorted(
                    self.route_entries_by_core.items())],
        }


def moe_traffic_lanes(layer, kernel, plan, capacity, members, placement,
                      weight_bytes_by_expert, packetization: Packetization,
                      topology: MeshTopology) -> MoeTrafficLanes:
    members_by_identity = {member.member_identity: member
                           for member in members}
    lanes = {name: 0 for name in LANE_FIELDS}
    per_peer = {}
    fan_in = {}
    route_entries = {}
    for route in plan.routes:
        member = members_by_identity[route.member_identity]
        route_entries[member.core_id] = \
            route_entries.get(member.core_id, 0) + 1
        fan_in[route.uid.sort_key()] = 0
    for route in capacity.accepted:
        member = members_by_identity[route.member_identity]
        expert_core = placement[route.selected_expert_id]
        local = member.core_id == expert_core
        lanes["dispatch_all_route_bytes"] += member.input_token_bytes
        lanes["combine_all_route_bytes"] += member.output_token_bytes
        if local:
            lanes["dispatch_local_sram_bytes"] += member.input_token_bytes
            lanes["combine_local_sram_bytes"] += member.output_token_bytes
        key = route.uid.sort_key()
        fan_in[key] = fan_in.get(key, 0) + 1
        if not local:
            dispatch_key = (A.MOE_TRANSFER_PHASE.DISPATCH, member.core_id,
                            expert_core)
            combine_key = (A.MOE_TRANSFER_PHASE.COMBINE, expert_core,
                           member.core_id)
            per_peer[dispatch_key] = per_peer.get(dispatch_key, 0) + \
                member.input_token_bytes
            per_peer[combine_key] = per_peer.get(combine_key, 0) + \
                member.output_token_bytes
    lanes["dispatch_remote_dma_bytes"] = (
        lanes["dispatch_all_route_bytes"] -
        lanes["dispatch_local_sram_bytes"])
    lanes["combine_remote_dma_bytes"] = (
        lanes["combine_all_route_bytes"] -
        lanes["combine_local_sram_bytes"])
    padded = sum(capacity.padded_slots_by_expert)
    accepted = len(capacity.accepted)
    lanes["padding_compute_slots"] = padded
    lanes["padding_fill_sram_wbytes"] = padded * layer.token_bytes
    lanes["expert_input_sram_rbytes"] = (accepted + padded) * \
        layer.token_bytes
    lanes["expert_output_sram_wbytes"] = (accepted + padded) * \
        layer.output_token_bytes
    lanes["padding_discarded_out_bytes"] = padded * layer.output_token_bytes
    dropped = sum(1 for count in fan_in.values() if count == 0)
    lanes["fully_dropped_fill_sram_wbytes"] = dropped * \
        layer.output_token_bytes
    lanes["combine_gather_sram_rbytes"] = sum(
        count * layer.output_token_bytes for count in fan_in.values())
    lanes["combine_output_sram_wbytes"] = sum(
        layer.output_token_bytes for count in fan_in.values() if count > 0)
    lanes["copy_through_commands"] = sum(
        1 for count in fan_in.values() if count == 1)
    lanes["local_reduce_commands"] = sum(
        1 for count in fan_in.values() if count >= 2)
    lanes["combine_reduce_ops"] = sum(
        kernel.n * max(count - 1, 0) for count in fan_in.values())
    for expert_id in {route.selected_expert_id
                      for route in capacity.accepted}:
        lanes["weight_sram_read_bytes"] += weight_bytes_by_expert[expert_id]
    lanes["route_metadata_fill_sram_bytes"] = sum(
        count * A.MOE_RUNTIME_ROUTE_ENTRY_BYTES
        for count in route_entries.values())
    result = MoeTrafficLanes(lanes=lanes, per_peer_logical_bytes=per_peer,
                             token_fan_in=fan_in,
                             route_entries_by_core=route_entries)
    for (_phase, src_core, dst_core), byte_count in per_peer.items():
        hops = topology.hops(src_core, dst_core)
        if hops == 0:
            raise MeshIrError("E_MOE_TRAFFIC",
                              "a remote segment needs at least one hop",
                              src_core=src_core, dst_core=dst_core)
        link = topology.link(src_core, dst_core)
        per_link = result.per_link_wire_bytes
        per_link[link] = per_link.get(link, 0) + \
            hops * packetization.wire_bytes(byte_count)
    return result


MOE_DMA_KIND = {
    A.MOE_DESCRIPTOR_KIND.ROUTE_FILL: A.DMA_KIND.LOCAL_FILL,
    A.MOE_DESCRIPTOR_KIND.STREAMED_WEIGHT: A.DMA_KIND.LOAD,
    A.MOE_DESCRIPTOR_KIND.PAD_FILL: A.DMA_KIND.LOCAL_FILL,
    A.MOE_DESCRIPTOR_KIND.DISPATCH: A.DMA_KIND.P2P_PUSH,
    A.MOE_DESCRIPTOR_KIND.COMBINE: A.DMA_KIND.P2P_PUSH,
    A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL: A.DMA_KIND.LOCAL_FILL,
}

MOE_PAYLOAD_FIELD = {
    A.DMA_KIND.LOCAL_FILL: "fill_bytes",
    A.DMA_KIND.LOAD: "read_bytes",
    A.DMA_KIND.P2P_PUSH: "p2p_bytes",
}

MOE_BURST_FIELD = {
    A.DMA_KIND.LOCAL_FILL: "write_bursts",
    A.DMA_KIND.LOAD: "read_bursts",
    A.DMA_KIND.P2P_PUSH: "p2p_bursts",
}


def descriptor_expectations(overlay_document, packetization: Packetization,
                            instances: int = 1) -> dict:
    expectations = {}
    for row in overlay_document["objects"]:
        if row["kind"] != A.MESH_OBJECT_KIND.DESCRIPTOR:
            continue
        if row["secondary_kind"] in (A.MOE_DESCRIPTOR_KIND.DISPATCH,
                                     A.MOE_DESCRIPTOR_KIND.COMBINE) and \
                row["src_view_ordinal"] == 0:
            continue
        if row["secondary_kind"] not in MOE_DMA_KIND:
            raise MeshIrError("E_MOE_TRAFFIC",
                              "descriptor kind is outside the closed set",
                              kind=row["secondary_kind"])
        kind = MOE_DMA_KIND[row["secondary_kind"]]
        payload = row["bytes"] * instances
        bursts = (0 if kind == A.DMA_KIND.LOCAL_FILL
                  else packetization.bursts(row["bytes"]) * instances)
        expectations[(row["region_id"], row["ordinal"])] = {
            "phase": row["phase"],
            "dma_kind": kind,
            "payload_field": MOE_PAYLOAD_FIELD[kind],
            "payload_bytes": payload,
            "burst_field": MOE_BURST_FIELD[kind],
            "bursts": bursts,
        }
    return expectations


def command_expectations(overlay_document) -> dict:
    counts = {}
    for row in overlay_document["objects"]:
        if row["kind"] != A.MESH_OBJECT_KIND.COMMAND:
            continue
        counts[row["owner_core"]] = counts.get(row["owner_core"], 0) + 1
    return counts


def region_expectations(overlay_document) -> dict:
    expectations = {}
    for row in overlay_document["objects"]:
        if row["region_id"] == 0:
            continue
        entry = expectations.setdefault(row["region_id"],
                                        {"commands": 0, "read_bytes": 0,
                                         "fill_bytes": 0, "p2p_bytes": 0})
        if row["kind"] == A.MESH_OBJECT_KIND.COMMAND:
            entry["commands"] += 1
    return expectations


def unique_fill_ledger(fills) -> dict:
    ledger = {}
    for fill in fills:
        identity = fill.get("fill_traffic_id") or (
            fill["core_id"], fill["weight_tag_index"],
            fill["fill_incarnation"])
        if identity in ledger:
            raise MeshIrError("E_MOE_TRAFFIC",
                              "duplicate physical fill identity",
                              fill_traffic_id=str(identity))
        ledger[identity] = fill
    return ledger


def compare_descriptors(expectations, result) -> list:
    failures = []
    actual = {}
    for row in result.get("transport", []):
        if row.get("domain", 0) != 1:
            continue
        actual[(row["region_id"], row["descriptor_id"])] = row
    for key in sorted(set(expectations) - set(actual)):
        failures.append("descriptor %s is missing from the trace" % (key,))
    for key in sorted(set(actual) - set(expectations)):
        failures.append("trace has an unmaterialized descriptor %s" % (key,))
    for key in sorted(set(actual) & set(expectations)):
        expectation = expectations[key]
        row = actual[key]
        if row.get("object_kind") != A.MESH_OBJECT_KIND.DESCRIPTOR:
            failures.append("descriptor %s lost its typed owner" % (key,))
        if row.get("dma_kind") != expectation["dma_kind"]:
            failures.append("descriptor %s moved as dma kind %s, expected %s"
                            % (key, row.get("dma_kind"),
                               expectation["dma_kind"]))
        payload = row.get(expectation["payload_field"], -1)
        if payload != expectation["payload_bytes"]:
            failures.append(
                "descriptor %s moved %s of its %d expected bytes"
                % (key, payload, expectation["payload_bytes"]))
        bursts = row.get(expectation["burst_field"], -1)
        if bursts != expectation["bursts"]:
            failures.append("descriptor %s used %s of its %d expected bursts"
                            % (key, bursts, expectation["bursts"]))
        if row.get("error_code", 0) != 0:
            failures.append("descriptor %s drained with error %s"
                            % (key, row.get("error_code")))
    return failures


def compare_commands(counts, result) -> list:
    failures = []
    for region in result.get("moe", {}).get("regions", []):
        core_id = region["core_id"]
        if region["issued"] != counts.get(core_id, 0):
            failures.append(
                "core %d executed %d of its %d materialized overlay commands"
                % (core_id, region["issued"], counts.get(core_id, 0)))
        if region["issued"] != region["completed"]:
            failures.append("region %d issued %d but completed %d"
                            % (region["region_id"], region["issued"],
                               region["completed"]))
    return failures


def fill_traffic_id(key_fields) -> str:
    import hashlib
    import struct

    key = struct.pack("<HHIII", key_fields["core_id"],
                      key_fields["cache_partition_id"],
                      key_fields["weight_tag_index"],
                      key_fields["cache_generation"],
                      key_fields["fill_incarnation"])
    return hashlib.sha256(FILL_TRAFFIC_DOMAIN + key).hexdigest()


def compare_weight_fills(result, cache_geometry=None) -> list:
    failures = []
    fills = result.get("moe", {}).get("cache_fills", [])
    if not fills:
        return failures
    ledger = unique_fill_ledger(fills)
    for fill in ledger.values():
        key_fields = fill.get("key_fields")
        if key_fields is not None:
            if fill.get("fill_traffic_id") != fill_traffic_id(key_fields):
                failures.append("fill %s does not hash its own key"
                                % (fill.get("fill_traffic_id"),))
            for name in ("core_id", "weight_tag_index", "fill_incarnation",
                         "slot_id"):
                if name in fill and name in key_fields and \
                        fill[name] != key_fields[name]:
                    failures.append("fill %s disagrees about %s"
                                    % (fill.get("fill_traffic_id"), name))
        if cache_geometry is not None:
            base, slot_bytes = cache_geometry
            slot_id = (key_fields or fill)["slot_id"]
            expected = base + slot_id * slot_bytes
            if fill.get("address") != expected:
                failures.append("fill %s landed at %s instead of %s"
                                % (fill.get("fill_traffic_id", slot_id),
                                   fill.get("address"), expected))
    logged = sum(fill["bytes"] for fill in ledger.values())
    moved = sum(row["read_bytes"] for row in result.get("transport", [])
                if row.get("domain", 0) == 2)
    if logged != moved:
        failures.append("weight fill ledger holds %d of %d moved bytes"
                        % (logged, moved))
    for fill in ledger.values():
        if fill["bytes"] != fill["committed_bytes"]:
            failures.append("fill %s moved %d of %d bytes"
                            % (fill.get("fill_traffic_id", "?"),
                               fill["committed_bytes"], fill["bytes"]))
    return failures


def verify_result(expectations, counts, result, instances: int = 1,
                  cache_geometry=None) -> tuple:
    failures = []
    failures.extend(compare_descriptors(expectations, result))
    failures.extend(compare_commands(counts, result))
    failures.extend(compare_weight_fills(result, cache_geometry))
    records = result.get("instances", [])
    if records and len(records) != instances:
        failures.append("instance ledger count %d != %d"
                        % (len(records), instances))
    for record in records:
        for core_id, ledger in record["cores"].items():
            if ledger["errored"] or ledger["cancelled"]:
                failures.append("instance %d core %s is not clean"
                                % (record["instance"], core_id))
    return tuple(failures), tuple(failures) == ()


TAMPER_KINDS = (
    "route_record",
    "descriptor_bytes",
    "dma_kind",
    "typed_owner",
    "dag_edge",
    "view_validity",
    "peer_actual",
    "terminal_record",
    "fill_id",
    "slot_id",
    "subscriber",
    "latch_commit",
)


def tamper(document, kind: str, index: int = 0) -> dict:
    if kind not in TAMPER_KINDS:
        raise MeshIrError("E_MOE_TRAFFIC", "unknown tamper kind", kind=kind)
    body = json.loads(json.dumps(document))
    if kind in ("descriptor_bytes", "dma_kind", "typed_owner"):
        rows = [row for row in body.get("transport", [])
                if row.get("domain", 0) == 1]
        if not rows:
            rows = body.get("objects", [])
        row = rows[index % len(rows)]
        if kind == "descriptor_bytes":
            field = MOE_PAYLOAD_FIELD.get(row.get("dma_kind"))
            if field is None:
                row["bytes"] = row.get("bytes", 0) + 16
            else:
                row[field] = row.get(field, 0) + 16
        elif kind == "dma_kind":
            row["dma_kind"] = row.get("dma_kind", 1) ^ 1
        else:
            row["object_kind"] = row.get("object_kind", 2) ^ 1
        return body
    if kind in ("route_record", "dag_edge", "view_validity"):
        rows = body["objects"]
        targets = [row for row in rows if row["kind"] == kinds_of(kind)]
        row = targets[index % len(targets)]
        if kind == "route_record":
            row["secondary_kind"] = max(DESCRIPTOR_KINDS) + 1
        elif kind == "dag_edge":
            row["wait_refs"] = []
        else:
            row["validity_extent"] = 0
        return body
    if kind in ("terminal_record", "latch_commit"):
        rows = [row for row in body["objects"]
                if row["kind"] == A.MESH_OBJECT_KIND.COMMAND and
                row["role"] in (A.MOE_COMMAND_ROLE.REGION_TERMINAL,
                                A.MOE_COMMAND_ROLE.EMPTY_REGION_TERMINAL,
                                A.MOE_COMMAND_ROLE.GROUP_EXIT)]
        row = rows[index % len(rows)]
        if kind == "terminal_record":
            row["role"] = A.MOE_COMMAND_ROLE.EXPERT_COMPUTE
        else:
            row["wait_refs"] = []
        return body
    fills = body.get("moe", {}).get("cache_fills", [])
    if not fills:
        raise MeshIrError("E_MOE_TRAFFIC", "tamper needs a cached run",
                          kind=kind)
    fill = fills[index % len(fills)]
    if kind == "peer_actual":
        fill["bytes"] = fill["bytes"] + 16
    elif kind == "fill_id":
        fill["fill_incarnation"] = fill["fill_incarnation"] + 1
    elif kind == "slot_id":
        fill["slot_id"] = fill["slot_id"] + 1
    else:
        fill["committed_bytes"] = max(0, fill["committed_bytes"] - 16)
    return body


def kinds_of(kind: str) -> int:
    return {
        "route_record": A.MESH_OBJECT_KIND.DESCRIPTOR,
        "dag_edge": A.MESH_OBJECT_KIND.COMMAND,
        "view_validity": A.MESH_OBJECT_KIND.VIEW,
    }[kind]


DESCRIPTOR_KINDS = frozenset(
    value for name, value in vars(A.MOE_DESCRIPTOR_KIND).items()
    if not name.startswith("_") and isinstance(value, int))


def verify_overlay_document(document) -> tuple:
    failures = []
    objects = document["objects"]
    keys = {(row["region_id"], row["kind"], row["ordinal"])
            for row in objects}
    if len(keys) != len(objects):
        failures.append("overlay holds duplicate object keys")
    buckets = {}
    for row in objects:
        buckets.setdefault((row["region_id"], row["kind"]), []).append(
            row["ordinal"])
    for bucket, ordinals in sorted(buckets.items()):
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            failures.append("bucket %s is not densely ordinaled" % (bucket,))
    commands = [row for row in objects
                if row["kind"] == A.MESH_OBJECT_KIND.COMMAND]
    events = [row for row in objects
              if row["kind"] == A.MESH_OBJECT_KIND.EVENT]
    descriptors = [row for row in objects
                   if row["kind"] == A.MESH_OBJECT_KIND.DESCRIPTOR]
    views = [row for row in objects if row["kind"] == A.MESH_OBJECT_KIND.VIEW]
    for row in commands:
        for ref in row["wait_refs"] + row["signal_refs"]:
            if (ref[0], ref[1], ref[2]) in keys:
                continue
            if ref[1] == A.MESH_OBJECT_KIND.EVENT and ref[2] == 0:
                continue
            failures.append("command %s references a missing object %s"
                            % ((row["region_id"], row["ordinal"]),
                               tuple(ref)))
    for row in views:
        if row.get("backing_kind") == A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION:
            target = (row["ref_region"], A.MESH_OBJECT_KIND.ALLOCATION,
                      row["ref_ordinal"])
            if target not in keys:
                failures.append("view %s escapes its allocation"
                                % ((row["region_id"], row["ordinal"]),))
            elif row["offset"] + row["bytes"] > \
                    next(item for item in objects
                         if (item["region_id"], item["kind"],
                             item["ordinal"]) == target)["bytes"]:
                failures.append("view %s overruns its allocation"
                                % ((row["region_id"], row["ordinal"]),))
        if row.get("validity_extent", 0) <= 0:
            failures.append("view %s has an empty validity extent"
                            % ((row["region_id"], row["ordinal"]),))
        if row["bytes"] <= 0:
            failures.append("view %s covers no bytes"
                            % ((row["region_id"], row["ordinal"]),))
    producers = {}
    for row in commands:
        for ref in row["signal_refs"]:
            producers.setdefault(tuple(ref), []).append(row)
    for event in events:
        key = (event["region_id"], A.MESH_OBJECT_KIND.EVENT, event["ordinal"])
        if event["ordinal"] == 0:
            failures.append("an overlay event reuses the entry ordinal")
            continue
        if len(producers.get(key, [])) != 1:
            failures.append("event %s has %d producers"
                            % (key, len(producers.get(key, []))))
    for row in descriptors:
        if row["secondary_kind"] not in DESCRIPTOR_KINDS:
            failures.append("descriptor %s has an unknown kind"
                            % ((row["region_id"], row["ordinal"]),))
    for region_id in sorted({row["region_id"] for row in commands
                             if row["region_id"] != 0}):
        terminals = [row for row in commands
                     if row["region_id"] == region_id and
                     row["role"] in (A.MOE_COMMAND_ROLE.REGION_TERMINAL,
                                     A.MOE_COMMAND_ROLE.EMPTY_REGION_TERMINAL)]
        if len(terminals) != 1:
            failures.append("region %d has %d terminals"
                            % (region_id, len(terminals)))
            continue
        owners = {row["ordinal"] for row in commands
                  if row["region_id"] == region_id and row is not terminals[0]}
        waited = {ref[2] for ref in terminals[0]["wait_refs"]
                  if ref[1] == A.MESH_OBJECT_KIND.COMMAND}
        if owners and not owners <= waited:
            failures.append("region %d terminal misses its drain join"
                            % region_id)
    gated = set()
    edges = {}
    for row in commands:
        key = (row["region_id"], A.MESH_OBJECT_KIND.COMMAND, row["ordinal"])
        edges[key] = set()
        for ref in row["wait_refs"]:
            ref = tuple(ref)
            if ref[1] == A.MESH_OBJECT_KIND.EVENT and ref[2] == 0:
                gated.add(key)
                continue
            if ref[1] == A.MESH_OBJECT_KIND.COMMAND:
                edges[key].add(ref)
                continue
            for producer in producers.get(ref, ()):
                edges[key].add((producer["region_id"],
                                A.MESH_OBJECT_KIND.COMMAND,
                                producer["ordinal"]))
    reached = set(gated)
    changed = True
    while changed:
        changed = False
        for key, prerequisites in edges.items():
            if key in reached:
                continue
            if prerequisites and prerequisites <= reached:
                reached.add(key)
                changed = True
    for row in commands:
        key = (row["region_id"], A.MESH_OBJECT_KIND.COMMAND, row["ordinal"])
        if key not in reached:
            failures.append("command %s is not gated by its region entry"
                            % (key,))
    group = [row for row in commands
             if row["role"] == A.MOE_COMMAND_ROLE.GROUP_EXIT]
    if len(group) != 1:
        failures.append("the group exit count is %d" % len(group))
    else:
        waited = {tuple(ref) for ref in group[0]["wait_refs"]
                  if ref[1] == A.MESH_OBJECT_KIND.EVENT}
        terminals = {(row["region_id"], A.MESH_OBJECT_KIND.EVENT,
                      row["ordinal"]) for row in events
                     if row["role"] in (
                         A.MOE_EVENT_ROLE.REGION_TERMINAL,
                         A.MOE_EVENT_ROLE.EMPTY_REGION_TERMINAL)}
        if terminals != waited:
            failures.append("the group exit misses a region terminal")
    return tuple(failures)

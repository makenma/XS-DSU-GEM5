import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as G
from mesh_ir.moe_fill import DIGEST_ONLY
from mesh_ir.moe_materializer import MaterializeConfig, \
    member_slice
from mesh_ir.moe_overlay_runtime import (
    materialize_overlay,
    overlay_image_bytes,
    overlay_objects_projection,
    overlay_wire_digest,
    record_counts,
)
from mesh_ir.moe_provider import (
    FrozenToken,
    apply_capacity,
    correlated_select,
    load_provider_artifact,
    route_replay_select,
    uniform_select,
)
from mesh_ir.moe_uid import SemanticTokenUid
from mesh_ir.weight_registry import weight_tag_index_map

FIXTURE_DIR = Path(__file__).resolve().parent
FIXTURES = FIXTURE_DIR.parent
MOE_IMAGE = FIXTURE_DIR / "moe_multi.mshb"
MOE_DUAL_IMAGE = FIXTURE_DIR / "moe_dual.mshb"
MOE_DUAL_DROP_IMAGE = FIXTURE_DIR / "moe_dual_drop.mshb"
MOE_DUAL_COPY_IMAGE = FIXTURE_DIR / "moe_dual_copy.mshb"
QUAD_IMAGE = FIXTURE_DIR / "moe_quad.mshb"
PLAN_DIGEST = bytes.fromhex("01" * 32)
DUAL_PLAN_DIGEST = bytes.fromhex("02" * 32)
QUAD_PLAN_DIGEST = bytes.fromhex("03" * 32)
MASTER_SEED = 7
QUAD_MASTER_SEED = 11
LAYER_INDEX = 1
DUAL_LAYER_INDEX = 0
QUAD_LAYER_INDEX = 0
PLACEMENT = None
QUAD_CORES = 16
DROP_CAPACITY_FACTOR = 0x8000
QUAD_REPLAY_ARTIFACT = FIXTURE_DIR / "moe_quad_route_replay.json"
QUAD_HOTSPOT_PROFILE = FIXTURE_DIR / "moe_quad_hotspot_profile.json"
QUAD_REPLAY_FIRST = 4
QUAD_HOTSPOT_FIRST = 2


def token(item_id, ordinal, member_identity, source_rank=0):
    uid = SemanticTokenUid(
        workload_plan_item_id=item_id, user_id=1, task_seq=item_id,
        repair_round=0, phase=G.SEMANTIC_PHASE.DECODE,
        sequence_ordinal=ordinal, token_ordinal=0)
    return FrozenToken(uid=uid, member_identity=member_identity,
                       source_rank=source_rank, linear_ordinal=ordinal)


def member_slices(program, layer, requests):
    return [member_slice(program, layer, identity, request_id, core_id)
            for identity, request_id, core_id in requests]

def oracle_lane_expectations(layer, kernel, plan, capacity, members, placement,
                             expert_specs, topology=(1, 2)):
    from mesh_ir.moe_oracle import Packetization, MeshTopology, \
        moe_traffic_lanes

    weight_bytes = {spec.expert_id: spec.weight_bytes for spec in expert_specs
                    if spec.layer_id == layer.layer_id}
    lanes = moe_traffic_lanes(
        layer, kernel, plan, capacity, members, placement, weight_bytes,
        Packetization(data_bytes=32, burst_beats=16, header_bytes=16,
                      flit_bytes=32),
        MeshTopology(*topology))
    expectations = dict(lanes.lanes)
    members_by_identity = {member.member_identity: member
                           for member in members}
    rows_by_expert = {expert_id: capacity.padded_slots_by_expert[expert_id]
                      for expert_id in range(len(placement))}
    for route in capacity.accepted:
        rows_by_expert[route.selected_expert_id] = \
            rows_by_expert.get(route.selected_expert_id, 0) + 1
    fan_in = {}
    token_core = {}
    for route in capacity.accepted:
        key = route.uid.sort_key()
        fan_in[key] = fan_in.get(key, 0) + 1
        token_core[key] = members_by_identity[route.member_identity].core_id
    expectations["compute_geometry"] = {
        "layer_id": layer.layer_id,
        "batch": kernel.batch, "n": kernel.n, "k": kernel.k,
        "efficiency_q16": kernel.efficiency_q16,
        "input_dtype": kernel.input_dtype, "accum_dtype": kernel.accum_dtype,
        "tensor_setup_cycles": kernel.tensor_setup_cycles,
        "tensor_flush_cycles": kernel.tensor_flush_cycles,
        "combine_setup_cycles": kernel.combine_setup_cycles,
        "combine_flush_cycles": kernel.combine_flush_cycles,
        "experts": [{"expert_id": expert_id, "core_id": placement[expert_id],
                     "rows": rows}
                    for expert_id, rows in sorted(rows_by_expert.items())
                    if rows > 0],
        "tokens": [{"core_id": token_core[key], "fan_in": count}
                   for key, count in sorted(fan_in.items())],
    }
    return expectations

def program_image_digest(image_path) -> bytes:
    return image_path.read_bytes()[72:104]


def build_overlay(image_path=MOE_IMAGE, layer_index=LAYER_INDEX,
                  plan_digest=PLAN_DIGEST, weight_residency="streamed",
                  document_sink=None):
    program = decode_program(image_path.read_bytes())
    layer = program.moe_layer_specs[layer_index]
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[
        layer.dynamic_region_first:
        layer.dynamic_region_first + layer.dynamic_region_count]
    tokens = [token(1, 0, "m1")]
    members = member_slices(program, layer, (("m1", 11, 0),))
    plan = uniform_select(layer, tokens, plan_digest, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    config = MaterializeConfig(
        workload_plan_digest=plan_digest,
        program_semantic_digest=bytes.fromhex(program.semantic_sha256()),
        p2p_chunk_bytes=4096, weight_residency=weight_residency)
    bounds = {
        "per_region": {
            region.region_id: {
                A.MESH_OBJECT_KIND.COMMAND: 64, A.MESH_OBJECT_KIND.EVENT: 64,
                A.MESH_OBJECT_KIND.DESCRIPTOR: 64,
                A.MESH_OBJECT_KIND.ALLOCATION: 64,
            } for region in regions},
        "group": {
            A.MESH_OBJECT_KIND.COMMAND: 128, A.MESH_OBJECT_KIND.EVENT: 128,
            A.MESH_OBJECT_KIND.DESCRIPTOR: 128,
            A.MESH_OBJECT_KIND.TRANSFER: 32,
            A.MESH_OBJECT_KIND.ALLOCATION: 128,
        },
    }
    experiment = program.moe_expert_specs[
        layer.expert_first:layer.expert_first + layer.expert_count]
    placement = [spec.core_id for spec in experiment]
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        {region.region_id: (region.scratch_offset, region.scratch_bytes,
                            region.scratch_alignment)
         for region in regions},
        config, bounds, program.moe_expert_specs,
        weight_tag_index_map(program) if weight_residency == "cached" else (),
        (), program.allocations)
    counts = record_counts(result.overlay)
    document = overlay_document(result.overlay, "", counts, result.intervals,
                                config.fill_mode, image_path)
    document["oracle_expectations"] = oracle_lane_expectations(
        layer, kernel, plan, capacity, members, placement,
        program.moe_expert_specs)
    if document_sink is not None:
        document_sink.update(document)

    digest = overlay_wire_digest(document["objects"])
    return (result.overlay, digest, counts, result.intervals)


def overlay_document(overlay, digest, counts, intervals=(), fill_mode=0,
                     program_path=MOE_IMAGE) -> dict:
    projection = overlay_objects_projection(overlay)
    objects = []
    for allocation in projection["allocations"]:
        objects.append({
            "kind": A.MESH_OBJECT_KIND.ALLOCATION,
            "region_id": allocation["region_id"],
            "ordinal": allocation["ordinal"],
            "owner_core": allocation["owner_core"],
            "secondary_kind": allocation["kind"],
            "expert_id": _u16(allocation["expert_or_ffff"]),
            "src_core": _u16(allocation["source_core_or_ffff"]),
            "dst_core": 0xFFFF, "chunk_ordinal": 0,
            "phase": allocation["phase"], "role": 0, "access": 0,
            "bytes": allocation["bytes"], "alignment": allocation["alignment"],
            "offset": 0, "ref_region": 0, "ref_ordinal": 0,
            "token_hex": "", "wait_count": 0, "signal_count": 0,
            "backing_kind": 0,
            "semantic_owner_kind": 0,
            "semantic_owner_ref0": 0,
            "validity_extent": 0,
        })
    for view in projection["views"]:
        objects.append({
            "kind": A.MESH_OBJECT_KIND.VIEW,
            "region_id": view["region_id"], "ordinal": view["ordinal"],
            "owner_core": view["owner_core"],
            "secondary_kind": view["view_kind"], "expert_id": 0xFFFF,
            "src_core": 0xFFFF, "dst_core": 0xFFFF, "chunk_ordinal": 0,
            "phase": 0, "role": 0, "access": view["access"],
            "bytes": view["bytes"], "alignment": 0, "offset": view["offset"],
            "ref_region": view["ref_region"],
            "ref_ordinal": view["ref_ordinal"],
            "token_hex": view["token_lowerhex"],
            "wait_count": 0,
            "signal_count": 0,
            "backing_kind": view["backing_kind"],
            "semantic_owner_kind": view["semantic_owner_kind"],
            "semantic_owner_ref0": _first(view["semantic_owner_ref"]),
            "validity_extent": view["validity_slices"][0][1]
            if view["validity_slices"] else 0,
        })
    for command in projection["commands"]:
        objects.append({
            "kind": A.MESH_OBJECT_KIND.COMMAND,
            "region_id": command["region_id"], "ordinal": command["ordinal"],
            "owner_core": command["owner_core"],
            "secondary_kind": command["opcode"], "expert_id":
            _u16(command["expert_id"]), "src_core": _u16(command["src_core"]),
            "dst_core": _u16(command["dst_core"]),
            "chunk_ordinal": command["chunk_ordinal"],
            "phase": command["phase"], "role": command["role"], "access": 0,
            "bytes": command["payload_bytes"], "alignment": 0, "offset": 0,
            "ref_region": command["ref_region"],
            "ref_ordinal": command["ref_ordinal"],
            "wait_count": len(command["wait_refs"]),
            "signal_count": len(command["signal_refs"]),
            "wait_refs": [list(ref) for ref in command["wait_refs"]],
            "signal_refs": [list(ref) for ref in command["signal_refs"]],
            "view_refs": [list(ref) for ref in command["view_refs"]],
            "token_hex": command["token_lowerhex"],
            "backing_kind": 0,
            "semantic_owner_kind": 0,
            "semantic_owner_ref0": 0,
            "validity_extent": 0,
        })
    for event in projection["events"]:
        objects.append({
            "kind": A.MESH_OBJECT_KIND.EVENT,
            "region_id": event["region_id"], "ordinal": event["ordinal"],
            "owner_core": event["owner_core"],
            "secondary_kind": event["event_phase"],
            "expert_id": _u16(event["expert_id"]),
            "src_core": _u16(event["src_core"]),
            "dst_core": _u16(event["dst_core"]),
            "chunk_ordinal": event["chunk_ordinal"],
            "phase": event["phase"], "role": event["role"], "access": 0,
            "bytes": 0,
            "alignment": 0, "offset": 0, "ref_region": 0, "ref_ordinal": 0,
            "token_hex": "",
            "wait_count": len(event["producers"]), "signal_count": 0,
            "token_hex": event["token_lowerhex"],
            "backing_kind": 0,
            "semantic_owner_kind": 0,
            "semantic_owner_ref0": 0,
            "validity_extent": 0,
        })
    for descriptor in projection["descriptors"]:
        objects.append({
            "kind": A.MESH_OBJECT_KIND.DESCRIPTOR,
            "region_id": descriptor["region_id"],
            "ordinal": descriptor["ordinal"],
            "owner_core": descriptor["owner_core"],
            "secondary_kind": descriptor["moe_kind"], "expert_id":
            _u16(descriptor["expert_id"]), "src_core":
            _u16(descriptor["src_core"]), "dst_core":
            _u16(descriptor["dst_core"]),
            "chunk_ordinal": descriptor["chunk_ordinal"],
            "phase": descriptor["phase"], "role": descriptor["traffic_class"],
            "access": 0, "bytes": descriptor["valid_bytes"], "alignment": 0,
            "offset": 0, "ref_region": 0, "ref_ordinal": 0,
            "src_view_region": descriptor["src_view_region"],
            "src_view_ordinal": descriptor["src_view_ordinal"],
            "dst_view_region": descriptor["dst_view_region"],
            "dst_view_ordinal": descriptor["dst_view_ordinal"],
            "fill_content_hex": descriptor["fill_content_hex"],
            "token_hex": "",
            "wait_count": 0, "signal_count": 0,
            "token_hex": descriptor["token_lowerhex"],
            "backing_kind": 0,
            "semantic_owner_kind": 0,
            "semantic_owner_ref0": 0,
            "validity_extent": 0,
        })
    for transfer in projection["transfers"]:
        objects.append({
            "kind": A.MESH_OBJECT_KIND.TRANSFER,
            "region_id": transfer["region_id"],
            "ordinal": transfer["ordinal"], "owner_core": 0,
            "secondary_kind": transfer["phase"], "expert_id":
            _u16(transfer["expert_id"]), "src_core":
            _u16(transfer["src_core"]), "dst_core":
            _u16(transfer["dst_core"]),
            "chunk_ordinal": transfer["chunk_ordinal"], "phase": 0, "role": 0,
            "access": 0, "bytes": transfer["logical_bytes"], "alignment": 0,
            "offset": 0, "ref_region": 0, "ref_ordinal": 0, "token_hex": "",
            "wait_count": 0, "signal_count": 0,
            "backing_kind": 0,
            "semantic_owner_kind": 0,
            "semantic_owner_ref0": 0,
            "validity_extent": 0,
        })
    for entry in objects:
        entry.setdefault("wait_refs", [])
        entry.setdefault("signal_refs", [])
    objects.sort(key=lambda entry: (entry["region_id"], entry["kind"],
                                    entry["ordinal"]))
    return {
        "schema": "gate5_overlay_objects_golden_v1",
        "version": 1,
        "layer_id": overlay.layer_id,
        "objects_digest": digest,
        "counts": counts,
        "fill_mode": int(fill_mode),
        "program_digest_hex": program_image_digest(program_path).hex(),
        "scratch": [
            {"region_id": region_id, "ordinal": ordinal,
             "offset": offset, "bytes": byte_count}
            for (region_id, ordinal), (offset, byte_count) in
            sorted(intervals.items())
        ],
        "objects": objects,
    }


def _first(values):
    return int(values[0]) if values else 0


def _u16(value) -> int:
    return 0xFFFF if value is None else int(value)


def quad_plan(layer, tokens, selection):
    if selection == "uniform":
        return uniform_select(layer, tokens, QUAD_PLAN_DIGEST,
                              QUAD_MASTER_SEED)
    if selection == "replay":
        program = decode_program(QUAD_IMAGE.read_bytes())
        artifact = load_provider_artifact(QUAD_REPLAY_ARTIFACT, program,
                                          QUAD_PLAN_DIGEST, "route_replay")
        return route_replay_select(layer, tokens, artifact, QUAD_PLAN_DIGEST)
    if selection == "hotspot":
        program = decode_program(QUAD_IMAGE.read_bytes())
        profile = load_provider_artifact(QUAD_HOTSPOT_PROFILE, program,
                                         QUAD_PLAN_DIGEST,
                                         "correlated_synthetic")
        plan, _predecessor = correlated_select(
            layer, profile, tokens, QUAD_PLAN_DIGEST, QUAD_MASTER_SEED)
        return plan
    raise SystemExit("unknown quad selection %s" % selection)


def build_drop_overlay(document_sink=None):
    program = decode_program(MOE_DUAL_DROP_IMAGE.read_bytes())
    layer = program.moe_layer_specs[DUAL_LAYER_INDEX]
    if layer.capacity_factor_q16 != DROP_CAPACITY_FACTOR:
        raise SystemExit("the drop program must carry the 0.5 capacity")
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[
        layer.dynamic_region_first:
        layer.dynamic_region_first + layer.dynamic_region_count]
    tokens = [token(1, 0, "m1"), token(2, 0, "m2")]
    members = member_slices(program, layer, (("m1", 11, 0), ("m2", 12, 1)))
    plan = uniform_select(layer, tokens, DUAL_PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    if {route.member_identity for route in capacity.dropped} != {"m2"}:
        raise SystemExit("the drop plan must drop every m2 route")
    config = MaterializeConfig(
        workload_plan_digest=DUAL_PLAN_DIGEST,
        program_semantic_digest=bytes.fromhex(program.semantic_sha256()),
        p2p_chunk_bytes=4096)
    bounds = {
        "per_region": {
            region.region_id: {
                A.MESH_OBJECT_KIND.COMMAND: 64, A.MESH_OBJECT_KIND.EVENT: 64,
                A.MESH_OBJECT_KIND.DESCRIPTOR: 64,
                A.MESH_OBJECT_KIND.ALLOCATION: 64,
            } for region in regions},
        "group": {
            A.MESH_OBJECT_KIND.COMMAND: 128, A.MESH_OBJECT_KIND.EVENT: 128,
            A.MESH_OBJECT_KIND.DESCRIPTOR: 128,
            A.MESH_OBJECT_KIND.TRANSFER: 32,
            A.MESH_OBJECT_KIND.ALLOCATION: 128,
        },
    }
    placement = [spec.core_id for spec in program.moe_expert_specs]
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        {region.region_id: (region.scratch_offset, region.scratch_bytes,
                            region.scratch_alignment)
         for region in regions},
        config, bounds, program.moe_expert_specs, (), (),
        program.allocations)
    fills = [record for record in
             result.overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)
             if record.moe_kind == A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL]
    if not fills:
        raise SystemExit("the drop plan must materialize a dropped fill")
    counts = record_counts(result.overlay)
    document = overlay_document(result.overlay, "", counts, result.intervals,
                                config.fill_mode, MOE_DUAL_DROP_IMAGE)
    document["oracle_expectations"] = oracle_lane_expectations(
        layer, kernel, plan, capacity, members, placement,
        program.moe_expert_specs)
    if document_sink is not None:
        document_sink.update(document)
    digest = overlay_wire_digest(document["objects"])
    return (result.overlay, digest, counts, result.intervals)


def build_copy_overlay(document_sink=None):
    program = decode_program(MOE_DUAL_COPY_IMAGE.read_bytes())
    layer = program.moe_layer_specs[DUAL_LAYER_INDEX]
    if layer.top_k != 1:
        raise SystemExit("the copy program must select exactly one expert")
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[
        layer.dynamic_region_first:
        layer.dynamic_region_first + layer.dynamic_region_count]
    tokens = [token(1, 0, "m1"), token(2, 0, "m2")]
    members = member_slices(program, layer, (("m1", 11, 0), ("m2", 12, 1)))
    plan = uniform_select(layer, tokens, DUAL_PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    if capacity.dropped:
        raise SystemExit("the copy plan must accept every route")
    config = MaterializeConfig(
        workload_plan_digest=DUAL_PLAN_DIGEST,
        program_semantic_digest=bytes.fromhex(program.semantic_sha256()),
        p2p_chunk_bytes=4096)
    bounds = {
        "per_region": {
            region.region_id: {
                A.MESH_OBJECT_KIND.COMMAND: 64, A.MESH_OBJECT_KIND.EVENT: 64,
                A.MESH_OBJECT_KIND.DESCRIPTOR: 64,
                A.MESH_OBJECT_KIND.ALLOCATION: 64,
            } for region in regions},
        "group": {
            A.MESH_OBJECT_KIND.COMMAND: 128, A.MESH_OBJECT_KIND.EVENT: 128,
            A.MESH_OBJECT_KIND.DESCRIPTOR: 128,
            A.MESH_OBJECT_KIND.TRANSFER: 32,
            A.MESH_OBJECT_KIND.ALLOCATION: 128,
        },
    }
    placement = [spec.core_id for spec in program.moe_expert_specs]
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        {region.region_id: (region.scratch_offset, region.scratch_bytes,
                            region.scratch_alignment)
         for region in regions},
        config, bounds, program.moe_expert_specs, (), (),
        program.allocations)
    copies = [record for record in
              result.overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
              if record.role == A.MOE_COMMAND_ROLE.COPY_THROUGH]
    if not copies:
        raise SystemExit("the copy plan must materialize COPY_THROUGH")
    counts = record_counts(result.overlay)
    document = overlay_document(result.overlay, "", counts, result.intervals,
                                config.fill_mode, MOE_DUAL_COPY_IMAGE)
    document["oracle_expectations"] = oracle_lane_expectations(
        layer, kernel, plan, capacity, members, placement,
        program.moe_expert_specs)
    if document_sink is not None:
        document_sink.update(document)
    digest = overlay_wire_digest(document["objects"])
    return (result.overlay, digest, counts, result.intervals)


def build_quad_overlay(weight_residency="streamed", selection="uniform",
                       document_sink=None):
    program = decode_program(QUAD_IMAGE.read_bytes())
    layer = program.moe_layer_specs[QUAD_LAYER_INDEX]
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[
        layer.dynamic_region_first:
        layer.dynamic_region_first + layer.dynamic_region_count]
    tokens = [token(core + 1, 0, "m%d" % core, 0)
              for core in range(QUAD_CORES)]
    members = member_slices(program, layer, tuple(
        ("m%d" % core, 100 + core, core) for core in range(QUAD_CORES)))
    plan = quad_plan(layer, tokens, selection)
    capacity = apply_capacity(plan, layer, len(tokens))
    if capacity.dropped:
        raise SystemExit("the quad %s plan must not drop a route" % selection)
    config = MaterializeConfig(
        workload_plan_digest=QUAD_PLAN_DIGEST,
        program_semantic_digest=bytes.fromhex(program.semantic_sha256()),
        p2p_chunk_bytes=4096, weight_residency=weight_residency,
        fill_mode=DIGEST_ONLY)
    bounds = {
        "per_region": {
            region.region_id: {
                A.MESH_OBJECT_KIND.COMMAND: 64, A.MESH_OBJECT_KIND.EVENT: 64,
                A.MESH_OBJECT_KIND.DESCRIPTOR: 64,
                A.MESH_OBJECT_KIND.ALLOCATION: 64,
            } for region in regions},
        "group": {
            A.MESH_OBJECT_KIND.COMMAND: 512, A.MESH_OBJECT_KIND.EVENT: 512,
            A.MESH_OBJECT_KIND.DESCRIPTOR: 512,
            A.MESH_OBJECT_KIND.TRANSFER: 256,
            A.MESH_OBJECT_KIND.ALLOCATION: 512,
        },
    }
    placement = [spec.core_id for spec in program.moe_expert_specs
                 if spec.layer_id == layer.layer_id]
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        {region.region_id: (region.scratch_offset, region.scratch_bytes,
                            region.scratch_alignment)
         for region in regions},
        config, bounds, program.moe_expert_specs,
        weight_tag_index_map(program) if weight_residency == "cached" else (),
        (), program.allocations)
    counts = record_counts(result.overlay)
    document = overlay_document(result.overlay, "", counts, result.intervals,
                                config.fill_mode, QUAD_IMAGE)
    document["oracle_expectations"] = oracle_lane_expectations(
        layer, kernel, plan, capacity, members, placement,
        program.moe_expert_specs, topology=(4, 4))
    if document_sink is not None:
        document_sink.update(document)
    digest = overlay_wire_digest(document["objects"])
    return (result.overlay, digest, counts, result.intervals)


PROGRAM_PATH = {
    "moe_overlay_objects": MOE_IMAGE,
    "moe_dual_overlay_objects": MOE_DUAL_IMAGE,
    "moe_dual_overlay_cached": MOE_DUAL_IMAGE,
    "moe_dual_drop_overlay_objects": MOE_DUAL_DROP_IMAGE,
    "moe_dual_copy_overlay_objects": MOE_DUAL_COPY_IMAGE,
    "moe_quad_overlay_objects": QUAD_IMAGE,
    "moe_quad_replay_overlay_objects": QUAD_IMAGE,
    "moe_quad_hotspot_overlay_objects": QUAD_IMAGE,
}


def main() -> int:
    import json
    for stem, image_path, layer_index, plan_digest, residency in (
            ("moe_overlay_objects", MOE_IMAGE, LAYER_INDEX, PLAN_DIGEST,
             "streamed"),
            ("moe_multi_l1_overlay_cached", MOE_IMAGE, 0, PLAN_DIGEST,
             "cached"),
            ("moe_multi_l2_overlay_cached", MOE_IMAGE, LAYER_INDEX, PLAN_DIGEST,
             "cached"),
            ("moe_dual_overlay_objects", MOE_DUAL_IMAGE, DUAL_LAYER_INDEX,
             DUAL_PLAN_DIGEST, "streamed"),
            ("moe_dual_overlay_cached", MOE_DUAL_IMAGE, DUAL_LAYER_INDEX,
             DUAL_PLAN_DIGEST, "cached"),
            ("moe_dual_drop_overlay_objects", MOE_DUAL_DROP_IMAGE,
             DUAL_LAYER_INDEX, DUAL_PLAN_DIGEST, "streamed"),
            ("moe_dual_copy_overlay_objects", MOE_DUAL_COPY_IMAGE,
             DUAL_LAYER_INDEX, DUAL_PLAN_DIGEST, "streamed"),
            ("moe_quad_overlay_objects", QUAD_IMAGE, QUAD_LAYER_INDEX,
             QUAD_PLAN_DIGEST, "streamed"),
            ("moe_quad_replay_overlay_objects", QUAD_IMAGE, QUAD_LAYER_INDEX,
             QUAD_PLAN_DIGEST, "streamed"),
            ("moe_quad_hotspot_overlay_objects", QUAD_IMAGE, QUAD_LAYER_INDEX,
             QUAD_PLAN_DIGEST, "streamed")):
        selection = {QUAD_IMAGE: "uniform"}.get(image_path, "uniform")
        if image_path == QUAD_IMAGE:
            selection = {"moe_quad_overlay_objects": "uniform",
                         "moe_quad_replay_overlay_objects": "replay",
                         "moe_quad_hotspot_overlay_objects": "hotspot"}[stem]
        sink = {}
        if stem == "moe_dual_copy_overlay_objects":
            overlay, digest, counts, intervals = build_copy_overlay(
                document_sink=sink)
        elif stem == "moe_dual_drop_overlay_objects":
            overlay, digest, counts, intervals = build_drop_overlay(
                document_sink=sink)
        elif image_path == QUAD_IMAGE:
            overlay, digest, counts, intervals = build_quad_overlay(
                residency, selection, document_sink=sink)
        else:
            overlay, digest, counts, intervals = build_overlay(
                image_path, layer_index, plan_digest, residency,
                document_sink=sink)
        if sink:
            document = sink
        else:
            document = overlay_document(
                overlay, digest, counts, intervals,
                program_path=PROGRAM_PATH[stem])
        (FIXTURE_DIR / f"{stem}.bin").write_bytes(overlay_image_bytes(
            document["objects"], overlay.layer_id,
            [(row["region_id"], row["ordinal"], row["offset"], row["bytes"])
             for row in document["scratch"]],
            fill_mode=document["fill_mode"],
            program_digest=program_image_digest(image_path)))
        (FIXTURE_DIR / f"{stem}.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"wrote {stem}.json ({len(document['objects'])} objects, "
              f"layer {overlay.layer_id}, digest {digest[:16]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import runpy
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.generated import abi as A
from mesh_ir.execution_view import build_execution_views
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.addressing import descriptor_endpoint_span
from mesh_ir.scheduled.model import EndpointSide, LocalAllocationBacking
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.moe_materializer import MaterializeConfig, MemberSlice
from mesh_ir.moe_overlay_runtime import materialize_overlay, overlay_image_bytes, record_counts
from mesh_ir.moe_provider import FrozenToken, uniform_select, apply_capacity
from mesh_ir.moe_uid import SemanticTokenUid
from mesh_ir.weight_registry import weight_tag_index_map

from .serving_runtime import REPO


def assert_shared_moe_image(work, weight_policy):
    source = work / 'program.mshb'
    program = decode_program(source.read_bytes())
    workload = load_workload_plan(work / 'workload.json')
    layer = program.moe_layer_specs[1]
    profile = next(row for row in program.agent_instance_profiles
                   if row.member_count == 2 and row.phase == A.PHASE.PREFILL)
    facts = read_facts_document(work / 'shared' / 'gate6_facts.tsv')
    request_ids = [row['request_id'] for row in facts.serving_phases
                   if row['instance_profile_id'] == profile.instance_profile_id]
    assert len(request_ids) == 2
    commands = build_execution_views(program).for_instance(profile.mesh_entrypoint_id, profile.mesh_profile_id).command_ids
    catalog = ProgramBindingCatalog(program)
    endpoint_uses = {(row.descriptor_id, row.side): row for row in program.semantics.endpoint_uses}
    backings = {row.object_id: row.backing for row in program.semantics.object_backings}
    members, tokens = [], []
    users = sorted(workload.document['users'],
                   key=lambda user: user['tasks'][0]['rounds'][0]['workload_plan_item_id'])
    for ordinal, user in enumerate(users):
        mapping = program.agent_instance_member_bindings[profile.member_binding_first + ordinal]
        endpoints = []
        for symbol, kind in ((mapping.static_input_symbol_id, A.DMA_KIND.LOAD),
                             (mapping.static_kv_symbol_id, A.DMA_KIND.STORE)):
            remote = catalog.binding(profile.mesh_entrypoint_id, profile.mesh_profile_id,
                program.strings[symbol - 1].value)
            side = EndpointSide.SRC if kind == A.DMA_KIND.LOAD else EndpointSide.DST
            rows = [row for row in program.dma_descriptors if row.command_id in commands
                    and row.kind == kind and catalog.descriptor_binding(row.descriptor_id, side) == remote]
            assert len(rows) == 1
            local_side = EndpointSide.DST if kind == A.DMA_KIND.LOAD else EndpointSide.SRC
            _, _, _, obj, _, begin, _ = descriptor_endpoint_span(
                program.semantics, endpoint_uses[rows[0].descriptor_id, local_side])
            backing = backings[obj.object_id]
            assert type(backing) is LocalAllocationBacking
            endpoints.append((backing.allocation_id, begin))
        task = user['tasks'][0]
        item = task['rounds'][0]
        members.append(MemberSlice(str(ordinal), request_ids[ordinal], 0, 0,
            profile.valid_tokens_per_member, *endpoints[0], layer.token_bytes,
            *endpoints[1], layer.output_token_bytes, binding_ordinal=ordinal))
        for token in range(profile.valid_tokens_per_member):
            uid = SemanticTokenUid(item['workload_plan_item_id'], user['user_id'], 0, 0, 1, 0, token)
            tokens.append(FrozenToken(uid, str(ordinal), item['logical_source_rank'], token))
    seed = yaml.safe_load((work / "config.yaml").read_text())["runtime"]["master_seed"]
    selection = uniform_select(layer, tokens, bytes.fromhex(workload.digest), seed)
    capacity = apply_capacity(selection, layer, len(tokens))
    regions = [row for row in program.moe_dynamic_regions if row.layer_id == layer.layer_id]
    config = MaterializeConfig(bytes.fromhex(workload.digest), bytes.fromhex(program.semantic_sha256),
                               4096, weight_residency=weight_policy)
    bounds = {'per_region': {region.region_id: {
        A.MESH_OBJECT_KIND.COMMAND: region.max_overlay_commands,
        A.MESH_OBJECT_KIND.EVENT: region.max_overlay_events,
        A.MESH_OBJECT_KIND.DESCRIPTOR: region.max_overlay_descriptors,
        A.MESH_OBJECT_KIND.ALLOCATION: region.max_overlay_allocations,
    } for region in regions}, 'group': {
        A.MESH_OBJECT_KIND.COMMAND: layer.max_materialized_commands,
        A.MESH_OBJECT_KIND.EVENT: layer.max_materialized_events,
        A.MESH_OBJECT_KIND.DESCRIPTOR: layer.max_materialized_descriptors,
        A.MESH_OBJECT_KIND.TRANSFER: layer.max_materialized_transfers,
        A.MESH_OBJECT_KIND.ALLOCATION: layer.max_dynamic_allocations}}
    tags = weight_tag_index_map(program)
    materialized = materialize_overlay(layer, program.moe_kernel_specs[layer.kernel_spec_index],
        selection, capacity, members, [0, 0], regions,
        {r.region_id: (r.scratch_offset, r.scratch_bytes, r.scratch_alignment) for r in regions},
        config, bounds, program.moe_expert_specs,
        weight_tags=tags,
        program_allocations=program.allocations)
    formatter = runpy.run_path(str(REPO / 'tests/gem5/ai_mesh/fixtures/gate5/build_gate5_overlay.py'))
    document = formatter['overlay_document'](materialized.overlay, '', record_counts(materialized.overlay),
                                            materialized.intervals, config.fill_mode, source)
    scratch = [(row['region_id'], row['ordinal'], row['offset'], row['bytes'])
               for row in document['scratch']]
    expected = overlay_image_bytes(document['objects'], layer.layer_id, scratch, config.fill_mode,
                                    bytes.fromhex(document['program_digest_hex']))
    images = list((work / 'shared').glob('moe_instance_*_layer_*.bin'))
    assert len(images) == 1
    actual = images[0].read_bytes()
    (work / "shared" / "expected_moe.bin").write_bytes(expected)
    assert actual == expected

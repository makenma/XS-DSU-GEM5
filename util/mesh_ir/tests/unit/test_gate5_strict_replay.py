import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as G
from mesh_ir.model import MeshIrError
from mesh_ir.moe_capacity import expert_capacity
from mesh_ir.moe_materializer import MaterializeConfig, \
    member_slice
from mesh_ir.moe_overlay_runtime import materialize_overlay
from mesh_ir.moe_provider import FrozenToken, apply_capacity, uniform_select
from mesh_ir.moe_strict_replay import (
    StrictBatch,
    reserve_normal,
    run_strict_replay,
    strict_batch_barrier,
)
from mesh_ir.moe_uid import SemanticTokenUid
from mesh_ir.moe_weight_cache import (
    RESERVE_FAILED,
    CacheReservationCoordinator,
    apply_cache_state_replay,
    cache_state_replay_document,
    reserve_batch_wide,
    weight_fill_source,
)
from mesh_ir.weight_registry import weight_tag_index_map

REPO = Path(__file__).resolve().parents[4]
MOE_IMAGE = REPO / "tests/gem5/ai_mesh/fixtures/gate5/moe_multi.mshb"
PLAN_DIGEST = bytes.fromhex("0123456789abcdef" * 4)
TAG_BYTES = {1: 2048, 2: 4096, 3: 4096}
LAYER_A = 1
LAYER_B = 2


def coordinator(slot_count=2, tags=(1, 2, 3), mshr=2, eviction=2,
                obligations=2, subscribers=4):
    return CacheReservationCoordinator(
        core_id=0, slot_count=slot_count, slot_bytes=4096,
        cacheable_tags=tags, mshr_slots=mshr, eviction_slots=eviction,
        obligation_slots=obligations, subscriber_slots=subscribers)


def shared_tag_batches():
    return (
        StrictBatch(batch_id=1, occurrences=((LAYER_A, 0, 1),
                                             (LAYER_B, 0, 1),
                                             (LAYER_B, 1, 2))),
        StrictBatch(batch_id=2, occurrences=((LAYER_A, 1, 2),)),
    )


def test_gate5_strict_replay_cold_arm_shares_one_fill_across_layers():
    coord = coordinator()
    result = run_strict_replay(coord, shared_tag_batches()[:1],
                               {1: 50, 2: 60}, TAG_BYTES, arm="cold")
    decisions = result.batches[0]["decisions"]
    assert [entry["residency"] for entry in decisions] == \
        ["new_fill", "attach", "new_fill"]
    tag_one = [entry for entry in decisions if entry["weight_tag_index"] == 1]
    assert tag_one[0]["fill_ref"] == tag_one[1]["fill_ref"]
    assert tag_one[0]["slot_id"] == tag_one[1]["slot_id"]
    assert result.traffic_bytes == TAG_BYTES[1] + TAG_BYTES[2]
    assert len(result.fills) == 2
    assert not coord.obligations
    assert [slot.pins for slot in coord.slots] == [0, 0]
    assert [token.released for token in coord.tokens.values()] == \
        [True, True, True]


def test_gate5_strict_replay_warm_snapshot_hits_every_layer_occurrence():
    coord = coordinator()
    run_strict_replay(coord, shared_tag_batches()[:1], {1: 50, 2: 60},
                      TAG_BYTES, arm="warm-up")
    warm = run_strict_replay(coord, shared_tag_batches()[1:], {2: 70},
                             TAG_BYTES, arm="warm")
    decisions = warm.batches[0]["decisions"]
    assert [entry["residency"] for entry in decisions] == ["hit"]
    assert decisions[0]["fill_ref"] is None
    assert warm.traffic_bytes == 0
    assert warm.fills == ()
    assert coord.next_fill_incarnation == 3


def test_gate5_strict_replay_fast_and_slow_fill_agree_on_the_projection():
    fast = coordinator()
    slow = coordinator()
    fast_result = run_strict_replay(fast, shared_tag_batches(),
                                    {1: 50, 2: 60}, TAG_BYTES, arm="fast")
    slow_result = run_strict_replay(slow, shared_tag_batches(),
                                    {1: 5000, 2: 6000}, TAG_BYTES,
                                    arm="slow")
    assert fast_result.decision_digest == slow_result.decision_digest
    assert fast_result.batches == slow_result.batches
    assert fast_result.traffic_bytes == slow_result.traffic_bytes
    assert [row[1:3] for row in fast_result.fills] == \
        [row[1:3] for row in slow_result.fills]
    assert [row[3] for row in fast_result.fills] != \
        [row[3] for row in slow_result.fills]
    assert fast_result.canonical() != slow_result.canonical()
    assert fast_result.canonical()["fills"][0]["done_tick"] == 50
    assert slow_result.canonical()["fills"][0]["done_tick"] == 5000
    assert fast.next_fill_incarnation == slow.next_fill_incarnation


def test_gate5_strict_replay_proves_the_worst_case_cold_union_at_tick_zero():
    tight = coordinator(slot_count=2, tags=(1, 2, 3), mshr=2, eviction=2,
                        obligations=2, subscribers=2)
    with pytest.raises(MeshIrError) as err:
        run_strict_replay(tight, shared_tag_batches(), {1: 50, 2: 60},
                          TAG_BYTES)
    assert err.value.code == "E_CAPACITY_PLAN"
    assert tight.tokens == {} and tight.obligations == {}
    assert [slot.state for slot in tight.slots] == [0, 0]
    assert tight.next_fill_incarnation == 1
    exactly_sized = coordinator(slot_count=2, tags=(1, 2, 3), mshr=2,
                                eviction=2, obligations=2, subscribers=3)
    run_strict_replay(exactly_sized, shared_tag_batches()[:1], {1: 50, 2: 60},
                      TAG_BYTES)


def test_gate5_strict_replay_serializes_batches():
    coord = coordinator()
    tokens = reserve_normal(coord, 1, ((LAYER_A, 0, 1),), tick=5)
    assert tokens
    with pytest.raises(MeshIrError) as err:
        run_strict_replay(coord, shared_tag_batches()[1:], {2: 60}, TAG_BYTES)
    assert err.value.code == "E_MOE_CACHE"
    assert "serializes batches" in err.value.message
    with pytest.raises(MeshIrError) as barrier:
        strict_batch_barrier(coord, 2)
    assert barrier.value.code == "E_MOE_CACHE"
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, TAG_BYTES[1])
    coord.release_token(tokens[0].token_id)
    strict_batch_barrier(coord, 2)
    run_strict_replay(coord, shared_tag_batches()[1:], {2: 60}, TAG_BYTES)


def test_gate5_normal_mode_timing_changes_the_outcome_with_attribution():
    hit_arm = coordinator()
    tokens = reserve_normal(hit_arm, 1, ((LAYER_A, 0, 1),), tick=5)
    hit_arm.mark_filling(tokens[0].fill_key)
    hit_arm.note_fill_success(tokens[0].fill_key, TAG_BYTES[1])
    hit_arm.release_token(tokens[0].token_id)
    later = reserve_normal(hit_arm, 2, ((LAYER_B, 0, 1),), tick=60)
    assert [token.outcome for token in later] == \
        [A.CACHE_RESIDENCY_OUTCOME.HIT]
    assert later[0].fill_key is None
    attached_arm = coordinator()
    first = reserve_normal(attached_arm, 1, ((LAYER_A, 0, 1),), tick=5)
    attached = reserve_normal(attached_arm, 2, ((LAYER_B, 0, 1),), tick=6)
    assert [token.outcome for token in attached] == \
        [A.CACHE_RESIDENCY_OUTCOME.ATTACH]
    assert attached[0].fill_key == first[0].fill_key
    assert attached[0].slot_id == first[0].slot_id
    assert len(attached_arm.obligations) == 1


def test_gate5_cache_state_replay_installs_the_same_decision_state():
    source = coordinator()
    run_strict_replay(source, shared_tag_batches()[:1], {1: 50, 2: 60},
                      TAG_BYTES, arm="source")
    document = cache_state_replay_document(source, {
        "effective_architecture": "aa" * 32,
        "program_weight_registry": "bb" * 32,
        "model_weight_image": "cc" * 32,
        "weight_tag_manifest": "dd" * 32,
    })
    replayed = coordinator()
    apply_cache_state_replay(replayed, document)
    assert replayed.next_fill_incarnation == source.next_fill_incarnation
    assert [(slot.state, slot.weight_tag_index, slot.valid_bytes)
            for slot in replayed.slots] == \
        [(slot.state, slot.weight_tag_index, slot.valid_bytes)
         for slot in source.slots]
    warm = run_strict_replay(replayed, shared_tag_batches()[1:], {2: 70},
                             TAG_BYTES, arm="replay")
    direct = run_strict_replay(source, shared_tag_batches()[1:], {2: 70},
                               TAG_BYTES, arm="direct")
    assert warm.decision_digest == direct.decision_digest
    assert warm.batches == direct.batches
    assert [entry["residency"] for entry in
            warm.batches[0]["decisions"]] == ["hit"]


def test_gate5_cache_state_replay_carries_failure_tombstones():
    coord = coordinator(tags=(1, 2, 3))
    tokens = reserve_normal(coord, 1, ((LAYER_A, 0, 3),), tick=5)
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_failure(
        tokens[0].fill_key, A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_AXI_R, 40,
        weight_fill_source(tokens[0].fill_key))
    coord.release_token(tokens[0].token_id)
    document = cache_state_replay_document(coord, {
        "effective_architecture": "aa" * 32,
        "program_weight_registry": "bb" * 32,
        "model_weight_image": "cc" * 32,
        "weight_tag_manifest": "dd" * 32,
    })
    replayed = coordinator(tags=(1, 2, 3))
    apply_cache_state_replay(replayed, document)
    tombstone = replayed.failure_table[coord.base_key(3)]
    assert tombstone.first_error_code == "E_AXI_RESPONSE"
    assert tombstone.first_error_tick == 40
    assert tombstone.first_error_source == weight_fill_source(
        tokens[0].fill_key)
    status, failure = reserve_batch_wide(replayed, 9, [(LAYER_A, 3)])
    assert status == RESERVE_FAILED and failure == [coord.base_key(3)]
    assert replayed.tokens == {}


def test_gate5_cache_state_replay_rejects_foreign_snapshots():
    source = coordinator()
    run_strict_replay(source, shared_tag_batches()[:1], {1: 50, 2: 60},
                      TAG_BYTES, arm="source")
    document = cache_state_replay_document(source, {
        "effective_architecture": "aa" * 32,
        "program_weight_registry": "bb" * 32,
        "model_weight_image": "cc" * 32,
        "weight_tag_manifest": "dd" * 32,
    })
    other_core = coordinator()
    other_core.core_id = 7
    with pytest.raises(MeshIrError) as err:
        apply_cache_state_replay(other_core, document)
    assert err.value.code == "E_MOE_CACHE"
    other_generation = coordinator()
    other_generation.cache_generation = 3
    with pytest.raises(MeshIrError) as err:
        apply_cache_state_replay(other_generation, document)
    assert err.value.code == "E_MOE_CACHE"
    document["cores"][0]["slots"][0]["weight_tag_index"] = 3
    document["cores"][0]["slots"][0]["state"] = 1
    document["cores"][0]["slots"][1]["state"] = 1
    document["cores"][0]["slots"][1]["weight_tag_index"] = 3
    with pytest.raises(MeshIrError) as err:
        apply_cache_state_replay(coordinator(), document)
    assert err.value.code == "E_MOE_CACHE"


def _materialize(layer_index, bindings):
    program = decode_program(MOE_IMAGE.read_bytes())
    layer = program.moe_layer_specs[layer_index]
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[
        layer.dynamic_region_first:
        layer.dynamic_region_first + layer.dynamic_region_count]
    uid = SemanticTokenUid(
        workload_plan_item_id=1, user_id=1, task_seq=1, repair_round=0,
        phase=G.SEMANTIC_PHASE.DECODE, sequence_ordinal=0, token_ordinal=0)
    tokens = [FrozenToken(uid=uid, member_identity="m1", source_rank=0,
                          linear_ordinal=0)]
    members = [member_slice(program, layer, "m1", 11, 0)]
    placement = [spec.core_id for spec in program.moe_expert_specs
                 if spec.layer_id == layer.layer_id]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    experts = [spec for spec in program.moe_expert_specs
               if spec.layer_id == layer.layer_id]
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
    return materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        {region.region_id: (region.scratch_offset, region.scratch_bytes,
                            region.scratch_alignment)
         for region in regions},
        MaterializeConfig(
            workload_plan_digest=PLAN_DIGEST,
            program_semantic_digest=bytes.fromhex(program.semantic_sha256()),
            p2p_chunk_bytes=4096, weight_residency="cached"),
        bounds, experts, weight_tag_index_map(program), bindings)


def test_gate5_strict_replay_bindings_enter_the_materialization_digest():
    coord = coordinator()
    result = run_strict_replay(coord, shared_tag_batches()[:1], {1: 50, 2: 60},
                               TAG_BYTES, arm="fast")
    layer_bindings = [binding for binding in result.bindings
                      if binding["layer_id"] == LAYER_B]
    assert layer_bindings
    first = _materialize(1, layer_bindings)
    assert first.canonical()["weight_bindings"] == sorted(
        layer_bindings, key=lambda row: (row["layer_id"], row["expert_id"],
                                         row["weight_tag_index"]))
    other = coordinator()
    slow = run_strict_replay(other, shared_tag_batches()[:1], {1: 900, 2: 950},
                             TAG_BYTES, arm="slow")
    second = _materialize(1, [binding for binding in slow.bindings
                              if binding["layer_id"] == LAYER_B])
    assert first.digest == second.digest
    warm = coordinator()
    run_strict_replay(warm, shared_tag_batches()[:1], {1: 50, 2: 60},
                      TAG_BYTES, arm="warm-up")
    hot = run_strict_replay(warm, (StrictBatch(2, ((LAYER_B, 0, 1),)),),
                            {1: 70}, TAG_BYTES, arm="warm")
    third = _materialize(1, [binding for binding in hot.bindings
                             if binding["layer_id"] == LAYER_B])
    assert third.digest != first.digest
    assert third.canonical()["weight_bindings"][0]["residency"] == "hit"


def test_gate5_weight_bindings_reject_inconsistent_fill_references():
    coord = coordinator()
    result = run_strict_replay(coord, shared_tag_batches()[:1], {1: 50, 2: 60},
                               TAG_BYTES, arm="cold")
    bindings = [dict(binding) for binding in result.bindings
                if binding["layer_id"] == LAYER_B]
    assert bindings
    broken = [dict(bindings[0])]
    broken[0]["residency"] = "hit"
    broken[0]["fill_ref"] = "aa" * 16
    with pytest.raises(MeshIrError) as err:
        _materialize(1, broken)
    assert err.value.code == "E_MOE_MATERIALIZATION_V"
    unknown = [dict(bindings[0], residency="resident")]
    with pytest.raises(MeshIrError) as err:
        _materialize(1, unknown)
    assert err.value.code == "E_MOE_MATERIALIZATION_V"


def test_gate5_selection_digest_ignores_the_cache_outcome():
    program = decode_program(MOE_IMAGE.read_bytes())
    layer = program.moe_layer_specs[1]
    uid = SemanticTokenUid(
        workload_plan_item_id=1, user_id=1, task_seq=1, repair_round=0,
        phase=G.SEMANTIC_PHASE.DECODE, sequence_ordinal=0, token_ordinal=0)
    tokens = [FrozenToken(uid=uid, member_identity="m1", source_rank=0,
                          linear_ordinal=0)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    from mesh_ir.moe_provider import batch_selection_digest

    digest = batch_selection_digest(plan)
    hit_arm = coordinator()
    reserve_normal(hit_arm, 1, ((LAYER_B, 0, 1),), tick=5)
    assert batch_selection_digest(plan) == digest
    assert plan.provider_digest
    assert expert_capacity(1, layer.top_k, layer.capacity_factor_q16,
                           layer.expert_count) >= 0
    assert load_arch(REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml")

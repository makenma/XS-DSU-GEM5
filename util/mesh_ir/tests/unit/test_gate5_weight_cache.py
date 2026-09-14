import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import (
    BASE_KEY_BYTES,
    RESERVE_COMMITTED,
    RESERVE_FAILED,
    RESERVE_RESOURCE_WAIT,
    SLOT_ERROR_HELD,
    SLOT_EVICTING_RESERVED,
    SLOT_FILLING,
    SLOT_FREE,
    SLOT_FREE_RESERVED,
    SLOT_VALID,
    CacheReservationCoordinator,
    cache_fill_traffic_document,
    cache_state_replay_document,
    prove_strict_capacity,
    reserve_batch_wide,
    CACHE_DMA_DESCRIPTOR_KEY_BYTES,
    ERROR_SOURCE_KEY_BYTES,
    FAILURE_DETAIL_SET,
    FAILURE_SITE_DETAIL,
    FILL_KEY_BYTES,
    WEIGHT_CACHE_PARTITION_ID,
    ErrorSourceKey,
    WeightCacheBaseKey,
    WeightFillKey,
    dedup_base_keys,
    fill_key_order,
    weight_fill_source,
)


def base_key(core_id=0, tag=1, generation=0,
             partition=WEIGHT_CACHE_PARTITION_ID):
    return WeightCacheBaseKey(core_id, partition, tag, generation)


def test_gate5_base_key_wire_is_twelve_bytes_little_endian():
    key = WeightCacheBaseKey(core_id=0x0102, cache_partition_id=0x0001,
                             weight_tag_index=0x03040506,
                             cache_generation=0x0708090A)
    wire = key.wire_bytes()
    assert len(wire) == BASE_KEY_BYTES == 12
    assert wire == bytes.fromhex("02010100060504030a090807")
    assert WeightCacheBaseKey.from_wire_bytes(wire) == key


def test_gate5_base_key_order_is_numeric_not_byte_lexicographic():
    low = base_key(core_id=0x00FF)
    high = base_key(core_id=0x0100)
    assert low.sort_key() < high.sort_key()
    assert low.wire_bytes() > high.wire_bytes()
    assert [key.core_id for key in dedup_base_keys([high, low])] == [
        0x00FF, 0x0100]


def test_gate5_base_key_rejects_out_of_range_fields():
    with pytest.raises(MeshIrError) as err:
        base_key(core_id=0x1_0000_0000)
    assert err.value.code == "E_MOE_CACHE"


def test_gate5_fill_key_wire_and_traffic_id_are_canonical():
    key = WeightFillKey(base_key(core_id=3, tag=7, generation=2), 1)
    wire = key.wire_bytes()
    assert len(wire) == FILL_KEY_BYTES == 16
    assert wire == bytes.fromhex("03000100070000000200000001000000")
    assert WeightFillKey.from_wire_bytes(wire) == key
    assert key.traffic_id() != WeightFillKey(key.base, 2).traffic_id()
    import hashlib
    assert key.traffic_id() == hashlib.sha256(
        b"AI_MESH_WEIGHT_FILL_V1\0" + wire).hexdigest()


def test_gate5_fill_key_requires_nonzero_incarnation():
    with pytest.raises(MeshIrError) as err:
        WeightFillKey(base_key(), 0)
    assert err.value.code == "E_MOE_CACHE"


def test_gate5_fill_key_order_covers_all_five_fields():
    first = WeightFillKey(base_key(core_id=1, tag=9), 1)
    second = WeightFillKey(base_key(core_id=1, tag=9), 2)
    third = WeightFillKey(base_key(core_id=1, tag=10), 1)
    assert fill_key_order([third, second, first]) == [first, second, third]


def test_gate5_cache_dma_descriptor_key_is_the_fill_key_plus_segment():
    key = WeightFillKey(base_key(tag=5), 4)
    assert len(key.descriptor_key(0)) == CACHE_DMA_DESCRIPTOR_KEY_BYTES == 20
    assert key.descriptor_key(0)[:FILL_KEY_BYTES] == key.wire_bytes()
    assert key.descriptor_key(1)[-4:] == (1).to_bytes(4, "little")
    assert key.descriptor_key(0) != key.descriptor_key(1)


def test_gate5_failure_site_maps_to_the_frozen_detail_closure():
    assert FAILURE_SITE_DETAIL == {
        A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_AXI_R: "E_AXI_RESPONSE",
        A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_SRAM_BOUNDS:
            "E_DCORE_SRAM_BOUNDS",
        A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_SRAM_COMMIT:
            "E_DCORE_ENGINE",
        A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_SOURCE_VALIDITY:
            "E_DCORE_POISON_READ",
    }
    assert FAILURE_DETAIL_SET == frozenset(FAILURE_SITE_DETAIL.values())


def test_gate5_error_source_key_is_forty_bytes_and_carries_the_fill_key():
    fill = WeightFillKey(base_key(core_id=2, tag=6, generation=1), 3)
    source = weight_fill_source(fill)
    wire = source.wire_bytes()
    assert len(wire) == ERROR_SOURCE_KEY_BYTES == 40
    assert source.error_class == A.MOE_ERROR_CLASS.WEIGHT_FILL
    assert source.domain == A.MESH_OBJECT_DOMAIN.WEIGHT_FILL
    assert source.object_kind == A.MESH_OBJECT_KIND.WEIGHT_FILL_OBLIGATION
    assert source.aux_key == fill.wire_bytes()
    assert ErrorSourceKey.from_wire_bytes(wire) == source
    assert wire[4] == A.MESH_OBJECT_DOMAIN.WEIGHT_FILL
    assert wire[5] == A.MESH_OBJECT_KIND.WEIGHT_FILL_OBLIGATION


def test_gate5_error_source_order_is_field_numeric_then_aux_bytes():
    low = ErrorSourceKey(error_class=A.MOE_ERROR_CLASS.WEIGHT_FILL,
                         core_id_or_ffff=1,
                         domain=A.MESH_OBJECT_DOMAIN.WEIGHT_FILL,
                         object_kind=A.MESH_OBJECT_KIND.WEIGHT_FILL_OBLIGATION,
                         region_group_id=0, region_id=0, ordinal=0,
                         generation=0, aux_key=bytes(16))
    high = ErrorSourceKey(error_class=A.MOE_ERROR_CLASS.DMA_AXI,
                          core_id_or_ffff=0,
                          domain=A.MESH_OBJECT_DOMAIN.STATIC,
                          object_kind=A.MESH_OBJECT_KIND.DESCRIPTOR,
                          region_group_id=0, region_id=0, ordinal=0,
                          generation=0, aux_key=bytes(16))
    assert low.sort_key() < high.sort_key()
    with pytest.raises(MeshIrError):
        ErrorSourceKey(error_class=99,
                       core_id_or_ffff=0,
                       domain=A.MESH_OBJECT_DOMAIN.WEIGHT_FILL,
                       object_kind=A.MESH_OBJECT_KIND.WEIGHT_FILL_OBLIGATION,
                       region_group_id=0, region_id=0, ordinal=0,
                       generation=0)


def coordinator(slot_count=2, tags=(1, 2, 3), mshr=2, eviction=2,
                obligations=2, subscribers=4):
    return CacheReservationCoordinator(
        core_id=0, slot_count=slot_count, slot_bytes=4096,
        cacheable_tags=tags, mshr_slots=mshr, eviction_slots=eviction,
        obligation_slots=obligations, subscriber_slots=subscribers)


def fill_first(coord, tokens, valid_bytes=4096):
    key = next(token.fill_key for token in tokens
               if token.fill_key is not None)
    coord.mark_filling(key)
    coord.note_fill_success(key, valid_bytes)
    return key


def test_gate5_cold_fill_then_hit_reuses_the_same_slot():
    coord = coordinator()
    status, tokens = coord.reserve(1, 1, [1])
    assert status == RESERVE_COMMITTED
    assert tokens[0].outcome == A.CACHE_RESIDENCY_OUTCOME.NEW_FILL
    key = fill_first(coord, tokens)
    coord.release_token(tokens[0].token_id)
    assert coord.obligations == {}
    status, tokens = coord.reserve(2, 1, [1])
    assert status == RESERVE_COMMITTED
    assert tokens[0].outcome == A.CACHE_RESIDENCY_OUTCOME.HIT
    assert tokens[0].slot_id == 0 and tokens[0].fill_key is None
    assert coord.next_fill_incarnation == 2


def test_gate5_attach_shares_one_physical_fill():
    coord = coordinator()
    _, first = coord.reserve(1, 1, [1])
    status, second = coord.reserve(2, 1, [1])
    assert status == RESERVE_COMMITTED
    assert second[0].outcome == A.CACHE_RESIDENCY_OUTCOME.ATTACH
    assert second[0].fill_key == first[0].fill_key
    assert len(coord.obligations) == 1
    coord.mark_filling(first[0].fill_key)
    coord.note_fill_success(first[0].fill_key, 4096)
    obligation = next(iter(coord.obligations.values()))
    assert [subscriber.state for subscriber in
            obligation.subscribers.values()] == [
        A.CACHE_SUBSCRIBER_STATE.WOKEN, A.CACHE_SUBSCRIBER_STATE.WOKEN]
    assert coord.next_fill_incarnation == 2


def test_gate5_ab_eviction_allocates_a_new_incarnation():
    coord = coordinator(slot_count=1, tags=(1, 2), mshr=1, eviction=1,
                        obligations=2, subscribers=4)
    _, tokens = coord.reserve(1, 1, [1])
    first = fill_first(coord, tokens)
    coord.release_token(tokens[0].token_id)
    _, tokens = coord.reserve(2, 1, [2])
    assert tokens[0].outcome == A.CACHE_RESIDENCY_OUTCOME.NEW_FILL
    second = tokens[0].fill_key
    assert second.fill_incarnation == 2
    assert second.traffic_id() != first.traffic_id()
    assert coord.slots[0].state == SLOT_EVICTING_RESERVED
    coord.mark_filling(second)
    coord.note_fill_success(second, 4096)
    coord.release_token(tokens[0].token_id)
    _, tokens = coord.reserve(3, 1, [1])
    third = tokens[0].fill_key
    assert third.fill_incarnation == 3
    assert len({first.traffic_id(), second.traffic_id(),
                third.traffic_id()}) == 3


def test_gate5_resource_wait_leaves_no_partial_side_effect():
    coord = coordinator(slot_count=1, tags=(1, 2), mshr=1, eviction=1,
                        obligations=1, subscribers=2)
    _, tokens = coord.reserve(1, 1, [1])
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, 4096)
    before = (coord.next_use_epoch, coord.next_fill_incarnation,
              coord.slots[0].pins, len(coord.tokens), len(coord.obligations))
    status, waited = coord.reserve(2, 1, [2])
    assert status == RESERVE_RESOURCE_WAIT and waited == []
    assert (coord.next_use_epoch, coord.next_fill_incarnation,
            coord.slots[0].pins, len(coord.tokens),
            len(coord.obligations)) == before
    assert coord.slots[0].state == SLOT_VALID


def test_gate5_fill_failure_tombstones_and_blocks_the_same_generation():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1)
    _, tokens = coord.reserve(1, 1, [1])
    key = tokens[0].fill_key
    coord.mark_filling(key)
    source = weight_fill_source(key)
    coord.note_fill_failure(key, A.WEIGHT_FILL_FAILURE_SITE
                            .CACHE_FILL_AXI_R, tick=40, source=source)
    assert coord.slots[0].state == SLOT_ERROR_HELD
    assert coord.slots[0].valid_bytes == 0
    subscriber = next(iter(next(iter(coord.obligations.values()))
                           .subscribers.values()))
    assert subscriber.state == A.CACHE_SUBSCRIBER_STATE.FAIL_NOTIFIED
    coord.release_token(tokens[0].token_id)
    assert coord.obligations == {}
    assert coord.slots[0].state == SLOT_FREE
    tombstone = coord.failure_table[key.base]
    assert tombstone.first_error_code == "E_AXI_RESPONSE"
    assert tombstone.first_error_tick == 40
    status, blocked = coord.reserve(2, 1, [1])
    assert status == RESERVE_FAILED
    assert blocked == [key.base]
    assert coord.slots[0].state == SLOT_FREE
    coord.cache_generation += 1
    status, tokens = coord.reserve(3, 1, [1])
    assert status == RESERVE_COMMITTED
    assert tokens[0].fill_key.base.cache_generation == 1


def test_gate5_first_error_keeps_the_minimum_candidate():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1)
    _, tokens = coord.reserve(1, 1, [1])
    key = tokens[0].fill_key
    coord.mark_filling(key)
    source = weight_fill_source(key)
    coord.note_fill_failure(key, A.WEIGHT_FILL_FAILURE_SITE
                            .CACHE_FILL_SRAM_COMMIT, tick=90, source=source)
    coord.note_fill_failure(key, A.WEIGHT_FILL_FAILURE_SITE
                            .CACHE_FILL_AXI_R, tick=30, source=source)
    coord.note_fill_failure(key, A.WEIGHT_FILL_FAILURE_SITE
                            .CACHE_FILL_AXI_R, tick=70, source=source)
    obligation = next(iter(coord.obligations.values()))
    assert obligation.first_error[0] == 30
    assert obligation.first_error[2] == "E_AXI_RESPONSE"


def test_gate5_member_cancel_keeps_the_shared_fill_alive():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1)
    _, first = coord.reserve(1, 1, [1])
    _, second = coord.reserve(2, 1, [1])
    coord.cancel_member(2)
    obligation = next(iter(coord.obligations.values()))
    states = {occurrence[0]: subscriber.state for occurrence, subscriber in
              obligation.subscribers.items()}
    assert states[2] == A.CACHE_SUBSCRIBER_STATE.WAITING
    assert states[1] == A.CACHE_SUBSCRIBER_STATE.WAITING
    assert obligation.state == A.CACHE_OBLIGATION_STATE.RESERVED
    assert not second[0].released
    assert not first[0].released
    assert 2 in coord.cancelled_members
    coord.mark_filling(first[0].fill_key)
    coord.note_fill_success(first[0].fill_key, 4096)
    assert coord.slots[0].state == SLOT_VALID


def test_gate5_prestart_abort_tombstones_pending_subscribers():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1)
    _, tokens = coord.reserve(1, 1, [1])
    coord.abort_before_start(1)
    obligation = next(iter(coord.obligations.values()))
    assert [subscriber.state for subscriber in
            obligation.subscribers.values()] == [
        A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED]
    assert coord.slots[0].pins == 1
    assert coord.slots[0].state == SLOT_FREE_RESERVED
    coord.mark_filling(tokens[0].fill_key)
    coord.note_failure_fanout_done(1)
    coord.note_fill_success(tokens[0].fill_key, 4096)
    assert coord.obligations == {}
    assert coord.slots[0].state == SLOT_VALID


def test_gate5_snapshot_reports_dense_lru_ranks_and_replay_states():
    coord = coordinator(slot_count=3, tags=(1, 2), mshr=2, eviction=2)
    _, tokens = coord.reserve(1, 1, [1, 2])
    for token in tokens:
        coord.mark_filling(token.fill_key)
        coord.note_fill_success(token.fill_key, 1024)
        coord.release_token(token.token_id)
    document = coord.snapshot()
    assert document["core_id"] == 0
    assert document["cache_generation"] == 0
    assert document["next_fill_incarnation"] == 3
    assert [slot["slot_id"] for slot in document["slots"]] == [0, 1, 2]
    valid = [slot for slot in document["slots"]
             if slot["state"] == A.CACHE_SLOT_STATE.VALID]
    assert [slot["lru_rank"] for slot in valid] == [0, 1]
    assert [slot["weight_tag_index"] for slot in valid] == [1, 2]
    empty = [slot for slot in document["slots"]
             if slot["state"] == A.CACHE_SLOT_STATE.INVALID]
    assert empty[0]["weight_tag_index"] is None
    assert empty[0]["valid_bytes"] == 0 and empty[0]["lru_rank"] is None
    assert document["failure_tombstones"] == []


def test_gate5_snapshot_rejects_a_non_quiescent_cache():
    coord = coordinator(slot_count=1, tags=(1,))
    coord.reserve(1, 1, [1])
    with pytest.raises(MeshIrError) as err:
        coord.snapshot()
    assert err.value.code == "E_MOE_CACHE"


def test_gate5_fill_becomes_eligible_only_after_both_edges():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1,
                        obligations=1, subscribers=2)
    _, tokens = coord.reserve(1, 1, [1], tick=50)
    key = tokens[0].fill_key
    assert coord.dma_eligible() == []
    assert coord.slots[0].state == SLOT_FREE_RESERVED
    coord.advance_cache_edge()
    assert coord.slots[0].state == SLOT_FILLING
    assert coord.dma_eligible() == []
    coord.advance_engine_edge()
    assert [entry.key for entry in coord.dma_eligible()] == [key]
    issue = coord.mark_issued(key, batch_qos=2)
    assert issue.eligible_tick == 50
    assert issue.effective_qos == 2
    assert issue.key[:5] == key.sort_key()
    with pytest.raises(MeshIrError):
        coord.mark_in_flight(key) if False else coord.mark_issued(key)
    coord.mark_in_flight(key)
    assert coord.slots[0].state == SLOT_FILLING


def test_gate5_shared_engine_arbiter_orders_both_sources():
    from mesh_ir.moe_weight_cache import arbitrate_issues, cache_fill_issue, \
        runtime_issue
    fill = WeightFillKey(base_key(tag=1), 1)
    same_edge = [runtime_issue(9, 100, qos=0),
                 cache_fill_issue(fill, 100, batch_qos=0)]
    assert [entry.source_kind for entry in
            arbitrate_issues(same_edge, 2)] == [0, 1]
    high_qos_fill = cache_fill_issue(fill, 100, batch_qos=7)
    low_qos_runtime = runtime_issue(9, 100, qos=0)
    assert arbitrate_issues([low_qos_runtime, high_qos_fill], 1)[0] is \
        high_qos_fill
    later_fill = cache_fill_issue(WeightFillKey(base_key(tag=2), 1), 101)
    assert arbitrate_issues([later_fill, low_qos_runtime], 1)[0] is \
        low_qos_runtime
    assert arbitrate_issues([runtime_issue(3, 7), runtime_issue(2, 7),
                             runtime_issue(1, 7)], 2)[-1].key == (2,)
    assert len(arbitrate_issues([runtime_issue(1, 1)], 1)) == 1


def test_gate5_strict_batch_wide_shares_one_fill_across_layers():
    coord = coordinator(slot_count=2, tags=(1, 2, 3), mshr=2, eviction=2,
                        obligations=2, subscribers=4)
    status, tokens = reserve_batch_wide(coord, 7, [(1, 1), (2, 1), (1, 2)])
    assert status == RESERVE_COMMITTED
    assert len(tokens) == 3
    outcomes = [(token.base_key.weight_tag_index, token.outcome)
                for token in tokens]
    assert outcomes == [
        (1, A.CACHE_RESIDENCY_OUTCOME.NEW_FILL),
        (2, A.CACHE_RESIDENCY_OUTCOME.NEW_FILL),
        (1, A.CACHE_RESIDENCY_OUTCOME.ATTACH)]
    occurrences = [token for token in tokens
                   if token.base_key.weight_tag_index == 1]
    assert occurrences[0].fill_key == occurrences[1].fill_key
    assert len(coord.obligations) == 2
    obligation = coord.obligations[coord.base_key(1)]
    assert sorted(occurrence[1] for occurrence in obligation.subscribers) == \
        [1, 2]
    assert coord.slots[obligation.slot_id].pins == 2


def test_gate5_strict_batch_wide_warm_snapshot_hits_every_occurrence():
    coord = coordinator(slot_count=2, tags=(1, 2), mshr=2, eviction=2,
                        obligations=2, subscribers=4)
    _, tokens = reserve_batch_wide(coord, 1, [(1, 1)])
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, 4096)
    coord.release_token(tokens[0].token_id)
    _, warm = reserve_batch_wide(coord, 2, [(1, 1), (2, 1)])
    assert [token.outcome for token in warm] == [
        A.CACHE_RESIDENCY_OUTCOME.HIT, A.CACHE_RESIDENCY_OUTCOME.HIT]
    assert len({token.slot_id for token in warm}) == 1
    assert all(token.fill_key is None for token in warm)
    assert coord.next_fill_incarnation == 2


def test_gate5_strict_batch_wide_waits_without_partial_reservation():
    coord = coordinator(slot_count=1, tags=(1, 2), mshr=1, eviction=1,
                        obligations=1, subscribers=1)
    before = (coord.next_use_epoch, coord.next_fill_incarnation,
              len(coord.tokens), len(coord.obligations), coord.slots[0].pins)
    status, tokens = reserve_batch_wide(coord, 3, [(1, 1), (2, 2)])
    assert status == RESERVE_RESOURCE_WAIT and tokens == []
    assert (coord.next_use_epoch, coord.next_fill_incarnation,
            len(coord.tokens), len(coord.obligations),
            coord.slots[0].pins) == before


def test_gate5_strict_capacity_proof_reports_the_worst_case_union():
    coord = coordinator(slot_count=3, tags=(1, 2, 3), mshr=3, eviction=3,
                        obligations=3, subscribers=4)
    required = prove_strict_capacity(coord, [(1, 1), (2, 1), (1, 2), (1, 3)])
    assert required == {"lines": 3, "subscribers": 4, "obligations": 3,
                        "mshr": 3, "eviction": 3}
    tight = coordinator(slot_count=2, tags=(1, 2, 3), mshr=2, eviction=2,
                        obligations=2, subscribers=4)
    with pytest.raises(MeshIrError) as err:
        prove_strict_capacity(tight, [(1, 1), (2, 2), (3, 3)])
    assert err.value.code == "E_CAPACITY_PLAN"


DIGESTS = {
    "effective_architecture": "aa" * 32,
    "program_weight_registry": "bb" * 32,
    "model_weight_image": "cc" * 32,
    "weight_tag_manifest": "dd" * 32,
}


def test_gate5_cache_state_replay_document_matches_its_schema():
    from mesh_ir.acceptance import validate_schema
    from mesh_ir.moe_weight_cache import cache_state_replay_document
    coord = coordinator(slot_count=3, tags=(1, 2), mshr=2, eviction=2)
    _, tokens = reserve_batch_wide(coord, 1, [(1, 1), (1, 2)])
    for token in tokens:
        coord.mark_filling(token.fill_key)
        coord.note_fill_success(token.fill_key, 1024)
        coord.release_token(token.token_id)
    document = cache_state_replay_document(coord, DIGESTS)
    validate_schema("cache_state_replay.schema.json", document)
    assert document["schema"] == "cache_state_replay_v1"
    core = document["cores"][0]
    assert core["next_fill_incarnation"] == 3
    assert [slot["lru_rank"] for slot in core["slots"]
            if slot["state"] == 1] == [0, 1]

    coord.slots[0].state = SLOT_FILLING
    with pytest.raises(MeshIrError):
        cache_state_replay_document(coord, DIGESTS)


def test_gate5_cache_fill_traffic_document_matches_its_schema():
    from mesh_ir.acceptance import validate_schema
    from mesh_ir.moe_weight_cache import cache_fill_traffic_document
    coord = coordinator(slot_count=2, tags=(1, 2), mshr=2, eviction=2)
    _, tokens = coord.reserve(1, 1, [1], tick=10)
    key = tokens[0].fill_key
    fills = [{
        "key": key, "slot_id": 0, "source_addr": 0x100000,
        "destination_addr": 0x80000, "valid_bytes": 4096,
        "expected_bursts": 1, "outcome": A.CACHE_RESIDENCY_OUTCOME.NEW_FILL,
        "subscriber_semantic_ids": ["member:m1#0"],
        "actual_read_bytes": 4096, "actual_sram_commit_bytes": 4096,
    }]
    document = cache_fill_traffic_document(coord, DIGESTS, fills)
    validate_schema("cache_fill_traffic_report.schema.json", document)
    row = document["fills"][0]
    assert row["fill_traffic_id"] == key.traffic_id()
    assert row["descriptor_key"] == key.descriptor_key(0).hex()
    with pytest.raises(MeshIrError):
        cache_fill_traffic_document(coord, DIGESTS, fills + fills)


def test_gate5_cache_scenario_matches_the_frozen_transition_golden():
    from mesh_ir.gate5_cache_scenario import replay
    document = replay()
    assert [event["event"] for event in document["events"]] == [
        "cold_fill", "warm_hit", "hit_and_new_fill", "evict_new_fill",
        "tombstone_blocks"]
    assert [(token["tag"], token["outcome"], token["slot_id"],
             token["fill_incarnation"])
            for token in document["events"][0]["tokens"]] == [
        (1, 2, 0, 1), (2, 2, 1, 2)]
    assert [(token["tag"], token["outcome"], token["slot_id"])
            for token in document["events"][1]["tokens"]] == [(1, 0, 0)]
    assert [(token["tag"], token["outcome"], token["slot_id"],
             token["fill_incarnation"])
            for token in document["events"][2]["tokens"]] == [
        (2, 0, 1, None), (3, 2, 2, 3)]
    assert [(token["tag"], token["outcome"], token["slot_id"],
             token["fill_incarnation"])
            for token in document["events"][3]["tokens"]] == [(4, 2, 0, 4)]
    assert document["events"][4]["status"] == RESERVE_FAILED
    assert document["events"][4]["blocked_tags"] == [4]
    assert document["next_fill_incarnation"] == 5
    assert document["next_use_epoch"] == 6
    assert [(slot["slot_id"], slot["state"], slot["weight_tag_index"],
             slot["lru_rank"]) for slot in document["slots"]] == [
        (0, 0, None, None), (1, 1, 2, 0), (2, 1, 3, 1)]
    tombstone = document["failure_tombstones"][0]
    assert tombstone["weight_tag_index"] == 4
    assert tombstone["first_error"]["error_code"] == "E_AXI_RESPONSE"
    assert tombstone["first_error"]["tick"] == 50
    assert len(tombstone["first_error"]["source_key_wire"]) == 80


def test_gate5_cache_drain_state_keeps_the_persistent_line():
    coord = coordinator(slot_count=2, tags=(1, 2), mshr=2, eviction=2,
                        obligations=2, subscribers=4)
    status, tokens = reserve_batch_wide(coord, 21, [(1, 1)])
    assert status == RESERVE_COMMITTED
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, 2048)
    coord.release_token(tokens[0].token_id)
    assert not coord.obligations
    assert [token for token in coord.tokens.values() if not token.released] \
        == []
    assert coord.mshr_free == coord.mshr_slots
    assert coord.eviction_free == coord.eviction_slots
    assert coord.obligation_free == coord.obligation_slots
    assert coord.subscriber_free == coord.subscriber_slots
    assert [slot.state for slot in coord.slots] == [SLOT_VALID, SLOT_FREE]
    assert coord.slots[0].weight_tag_index == 1
    assert coord.slots[0].valid_bytes == 2048
    assert coord.slots[0].pins == 0
    assert not coord.failure_table


def test_gate5_cache_drain_state_keeps_the_failure_tombstone():
    coord = coordinator(slot_count=2, tags=(1, 2), mshr=2, eviction=2,
                        obligations=2, subscribers=4)
    status, tokens = reserve_batch_wide(coord, 22, [(1, 1)])
    assert status == RESERVE_COMMITTED
    key = tokens[0].fill_key
    coord.mark_filling(key)
    coord.note_fill_failure(
        key, A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_AXI_R, 30,
        weight_fill_source(key))
    coord.release_token(tokens[0].token_id)
    assert not coord.obligations
    assert [token for token in coord.tokens.values() if not token.released] \
        == []
    assert coord.mshr_free == coord.mshr_slots
    assert coord.eviction_free == coord.eviction_slots
    assert coord.obligation_free == coord.obligation_slots
    assert coord.subscriber_free == coord.subscriber_slots
    assert [slot.state for slot in coord.slots] == [SLOT_FREE, SLOT_FREE]
    assert sorted(coord.failure_table) == [coord.base_key(1)]
    assert coord.failure_table[coord.base_key(1)].first_error_code == \
        "E_AXI_RESPONSE"
    retry = reserve_batch_wide(coord, 23, [(1, 1)])
    assert retry[0] == RESERVE_FAILED
    assert not coord.obligations
    assert coord.mshr_free == coord.mshr_slots


def test_gate5_started_fault_joins_fill_terminal_drain_and_fanout():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1)
    _, tokens = coord.reserve(1, 1, [1])
    token = tokens[0]
    coord.mark_filling(token.fill_key)
    coord.note_instance_fault(1, 12)
    obligation = next(iter(coord.obligations.values()))
    assert [subscriber.state for subscriber in
            obligation.subscribers.values()] == [
        A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED]
    coord.note_owned_work_drained(1)
    assert token.released is False
    coord.note_failure_fanout_done(1)
    assert token.released is False
    coord.note_fill_success(token.fill_key, 4096)
    assert token.released is True
    assert coord.slots[0].state == SLOT_VALID
    assert coord.slots[0].pins == 0
    assert coord.obligations == {}
    assert coord.mshr_free == coord.mshr_slots
    assert coord.obligation_free == coord.obligation_slots
    assert coord.subscriber_free == coord.subscriber_slots


def test_gate5_fill_terminal_first_still_waits_for_the_owned_drain():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1)
    _, tokens = coord.reserve(2, 3, [1])
    token = tokens[0]
    coord.mark_filling(token.fill_key)
    coord.note_instance_fault(2, 12)
    coord.note_failure_fanout_done(2)
    coord.note_fill_success(token.fill_key, 4096)
    assert token.released is False
    assert token.pinned is True
    coord.note_owned_work_drained(2)
    assert token.released is True
    assert coord.slots[0].pins == 0
    assert coord.obligations == {}


def test_gate5_faulted_batch_subscriber_is_never_woken():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1)
    _, tokens = coord.reserve(3, 1, [1])
    coord.mark_filling(tokens[0].fill_key)
    coord.note_instance_fault(3, 5)
    coord.note_failure_fanout_done(3)
    coord.note_owned_work_drained(3)
    coord.note_fill_success(tokens[0].fill_key, 4096)
    obligation = coord.obligations.get(coord.base_key(1))
    assert obligation is None


def test_gate5_token_pin_holds_until_the_last_consumer_drains():
    coord = coordinator(slot_count=1, tags=(1,), mshr=1, eviction=1)
    _, tokens = coord.reserve(4, 2, [1])
    token = tokens[0]
    coord.set_token_consumers(token.token_id, 2)
    coord.mark_filling(token.fill_key)
    coord.note_fill_success(token.fill_key, 4096)
    coord.note_consumer_drained(4, 2, 1)
    assert token.released is False
    assert coord.slots[0].pins == 1
    coord.note_consumer_drained(4, 2, 1)
    assert token.released is True
    assert coord.slots[0].pins == 0
    assert coord.obligations == {}
    coord.note_consumer_drained(4, 2, 1)
    assert coord.slots[0].pins == 0


def test_gate5_probe_shadow_leaves_no_visible_state():
    coord = coordinator(slot_count=1, tags=(1, 2), mshr=1, eviction=0)
    _, tokens = coord.reserve(5, 1, [1])
    coord.mark_filling(tokens[0].fill_key)
    shadow, outcome = coord.probe([2])
    assert outcome[0] == RESERVE_RESOURCE_WAIT
    assert shadow is None
    assert coord.next_fill_incarnation == 2
    assert coord.slots[0].pins == 1
    assert len(coord.tokens) == 1
    assert len(coord.obligations) == 1

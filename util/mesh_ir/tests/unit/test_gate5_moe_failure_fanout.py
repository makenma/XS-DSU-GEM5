import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import (
    RESERVE_COMMITTED,
    RESERVE_FAILED,
    RESERVE_RESOURCE_WAIT,
    BatchCacheReservationSet,
    CacheReservationCoordinator,
    reserve_batch_wide,
    weight_fill_source,
)

SLOT_BYTES = 4096
LAYER_A = 1
LAYER_B = 2
SITE_AXI = A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_AXI_R
SITE_SRAM = A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_SRAM_COMMIT


def coordinator(core_id=0, slot_count=2, tags=(1, 2, 3), mshr=2, eviction=2,
                obligations=2, subscribers=4):
    return CacheReservationCoordinator(
        core_id=core_id, slot_count=slot_count, slot_bytes=SLOT_BYTES,
        cacheable_tags=tags, mshr_slots=mshr, eviction_slots=eviction,
        obligation_slots=obligations, subscriber_slots=subscribers)


def fill(coord, tag, bytes_count=SLOT_BYTES, tick=10):
    status, tokens = reserve_batch_wide(coord, 1, [(LAYER_A, tag)], tick)
    assert status == RESERVE_COMMITTED
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, bytes_count)
    for token in tokens:
        coord.release_token(token.token_id)
    return tokens[0]


def coordinate_state(coord):
    return {
        "slots": [(slot.state, slot.weight_tag_index, slot.valid_bytes,
                   slot.pins, slot.bound_fill) for slot in coord.slots],
        "tokens": [(token.token_id, token.released)
                   for token in coord.tokens.values()],
        "obligations": sorted(coord.obligations),
        "tombstones": sorted(coord.failure_table),
        "next_epoch": coord.next_use_epoch,
        "next_incarnation": coord.next_fill_incarnation,
        "mshr": coord.mshr_free,
        "eviction": coord.eviction_free,
        "obligation": coord.obligation_free,
        "subscriber": coord.subscriber_free,
    }


def test_gate5_batch_cross_cores_commits_all_or_none():
    left = coordinator(core_id=0, slot_count=2, tags=(1, 2))
    right = coordinator(core_id=1, slot_count=1, tags=(1, 2), mshr=1,
                        eviction=1, obligations=1, subscribers=2)
    reserve = BatchCacheReservationSet({1: right, 0: left})
    before = (coordinate_state(left), coordinate_state(right))
    blocked = reserve.reserve(4, {0: ((LAYER_A, 1),),
                                  1: ((LAYER_A, 1), (LAYER_A, 2))})
    assert blocked.status == RESERVE_RESOURCE_WAIT
    assert blocked.tokens == ()
    assert (coordinate_state(left), coordinate_state(right)) == before
    left = coordinator(core_id=0, slot_count=1, tags=(1, 2), mshr=1,
                       eviction=1, obligations=1, subscribers=2)
    right = coordinator(core_id=1, slot_count=2, tags=(1, 2))
    reserve = BatchCacheReservationSet({1: right, 0: left})
    before = (coordinate_state(left), coordinate_state(right))
    blocked = reserve.reserve(4, {0: ((LAYER_A, 1), (LAYER_A, 2)),
                                  1: ((LAYER_A, 2),)})
    assert blocked.status == RESERVE_RESOURCE_WAIT
    assert (coordinate_state(left), coordinate_state(right)) == before
    committed = reserve.reserve(4, {0: ((LAYER_A, 1), (LAYER_B, 1)),
                                    1: ((LAYER_A, 2),)})
    assert committed.status == RESERVE_COMMITTED
    assert len(committed.tokens) == 3
    assert len(committed.tokens_of(0)) == 2
    assert len(committed.tokens_of(1)) == 1
    assert sorted(left.obligations) == [left.base_key(1)]
    assert sorted(right.obligations) == [right.base_key(2)]


def test_gate5_batch_cross_cores_reports_a_tombstone_without_side_effects():
    left = coordinator(core_id=0)
    right = coordinator(core_id=1, tags=(1, 2, 3))
    tokens = reserve_batch_wide(right, 1, [(LAYER_A, 3)])[1]
    right.mark_filling(tokens[0].fill_key)
    right.note_fill_failure(tokens[0].fill_key, SITE_AXI, 7,
                            weight_fill_source(tokens[0].fill_key))
    right.release_token(tokens[0].token_id)
    assert right.failure_table
    reserve = BatchCacheReservationSet({0: left, 1: right})
    before = (coordinate_state(left), coordinate_state(right))
    outcome = reserve.reserve(5, {0: ((LAYER_A, 1),), 1: ((LAYER_A, 3),)})
    assert outcome.status == RESERVE_FAILED
    assert outcome.failed_key == right.base_key(3)
    assert (coordinate_state(left), coordinate_state(right)) == before


def test_gate5_full_cache_protects_a_hit_from_the_same_batch_miss():
    coord = coordinator(slot_count=2, tags=(1, 2, 3), mshr=2, eviction=2,
                        obligations=2, subscribers=4)
    fill(coord, 1)
    fill(coord, 2)
    status, tokens = reserve_batch_wide(coord, 2, [(LAYER_A, 1),
                                                   (LAYER_B, 3)])
    assert status == RESERVE_COMMITTED
    hit, miss = tokens[0], tokens[1]
    assert hit.outcome == A.CACHE_RESIDENCY_OUTCOME.HIT
    assert hit.slot_id == 0
    assert miss.outcome == A.CACHE_RESIDENCY_OUTCOME.NEW_FILL
    assert miss.slot_id == 1
    assert coord.slots[1].weight_tag_index == 3
    assert coord.slots[1].state == 3
    assert coord.eviction_free == coord.eviction_slots - 1


def test_gate5_exact_alias_shares_one_subscriber_until_the_last_drain():
    coord = coordinator()
    status, tokens = reserve_batch_wide(coord, 6,
                                        [(LAYER_A, 1), (LAYER_A, 1)])
    assert status == RESERVE_COMMITTED
    assert len(tokens) == 1
    obligation = coord.obligations[coord.base_key(1)]
    assert len(obligation.subscribers) == 1
    assert coord.slots[obligation.slot_id].pins == 1
    assert coord.mshr_free == coord.mshr_slots - 1
    assert coord.obligation_free == coord.obligation_slots - 1
    assert coord.subscriber_free == coord.subscriber_slots - 1
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, 2048)
    coord.release_token(tokens[0].token_id)
    assert not coord.obligations
    assert coord.obligation_free == coord.obligation_slots
    assert coord.subscriber_free == coord.subscriber_slots
    assert coord.slots[obligation.slot_id].state == 4
    assert coord.slots[obligation.slot_id].valid_bytes == 2048


def test_gate5_cross_layer_alias_keeps_one_fill_per_occurrence():
    coord = coordinator(subscribers=4)
    status, tokens = reserve_batch_wide(coord, 6,
                                        [(LAYER_A, 1), (LAYER_B, 1)])
    assert status == RESERVE_COMMITTED
    assert len(tokens) == 2
    obligation = coord.obligations[coord.base_key(1)]
    assert len(obligation.subscribers) == 2
    assert coord.slots[obligation.slot_id].pins == 2
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, 2048)
    coord.release_token(tokens[0].token_id)
    assert obligation.subscribers[(6, LAYER_A)].state == \
        A.CACHE_SUBSCRIBER_STATE.RELEASED
    assert obligation.subscribers[(6, LAYER_B)].state == \
        A.CACHE_SUBSCRIBER_STATE.WOKEN
    assert coord.slots[obligation.slot_id].pins == 1
    assert coord.obligations
    coord.release_token(tokens[1].token_id)
    assert not coord.obligations
    assert coord.obligation_free == coord.obligation_slots


def test_gate5_ab_eviction_spends_two_incarnations_and_two_fills():
    coord = coordinator(slot_count=1, tags=(1, 2), mshr=1, eviction=1,
                        obligations=1, subscribers=2)
    first = fill(coord, 1, 1024)
    assert coord.slots[0].weight_tag_index == 1
    fill(coord, 2, 2048)
    assert coord.slots[0].weight_tag_index == 2
    again = reserve_batch_wide(coord, 2, [(LAYER_A, 1)])
    assert again[0] == RESERVE_COMMITTED
    assert again[1][0].fill_key.fill_incarnation == 3
    assert again[1][0].fill_key != first.fill_key
    assert again[1][0].fill_key.traffic_id() != first.fill_key.traffic_id()
    assert coord.slots[0].weight_tag_index == 1
    assert coord.slots[0].state == 3
    assert coord.eviction_free == coord.eviction_slots - 1


def test_gate5_member_cancel_keeps_the_batch_subscriber_woken():
    coord = coordinator()
    status, tokens = reserve_batch_wide(coord, 8, [(LAYER_A, 1)])
    assert status == RESERVE_COMMITTED
    coord.cancel_member(8)
    assert 8 in coord.cancelled_members
    obligation = coord.obligations[coord.base_key(1)]
    assert obligation.subscribers[(8, LAYER_A)].state == \
        A.CACHE_SUBSCRIBER_STATE.WAITING
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, SLOT_BYTES)
    assert obligation.subscribers[(8, LAYER_A)].state == \
        A.CACHE_SUBSCRIBER_STATE.WOKEN
    assert coord.slots[obligation.slot_id].pins == 1
    coord.release_token(tokens[0].token_id)
    assert not coord.obligations
    assert coord.slots[obligation.slot_id].state == 4
    assert coord.tokens[tokens[0].token_id].released


def test_gate5_instance_fault_tombstones_only_its_own_subscriber():
    coord = coordinator()
    status, tokens = reserve_batch_wide(coord, 9, [(LAYER_A, 1), (LAYER_B, 1)])
    assert status == RESERVE_COMMITTED
    obligation = coord.obligations[coord.base_key(1)]
    coord.abort_before_start(9)
    coord.note_failure_fanout_done(9)
    states = {occurrence[1]: subscriber.state
              for occurrence, subscriber in obligation.subscribers.items()}
    assert states == {LAYER_A: A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED,
                      LAYER_B: A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED}
    assert coord.obligations
    assert coord.slots[obligation.slot_id].pins == 2
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, SLOT_BYTES)
    assert not coord.obligations
    assert coord.slots[obligation.slot_id].state == 4
    assert all(token.released for token in coord.tokens.values())
    assert coord.subscriber_free == coord.subscriber_slots


def test_gate5_mixed_batch_survives_another_batch_fault():
    coord = coordinator()
    status, tokens = reserve_batch_wide(coord, 10, [(LAYER_A, 1)])
    assert status == RESERVE_COMMITTED
    status, shared = reserve_batch_wide(coord, 11, [(LAYER_B, 1)])
    assert status == RESERVE_COMMITTED
    assert shared[0].fill_key == tokens[0].fill_key
    assert shared[0].outcome == A.CACHE_RESIDENCY_OUTCOME.ATTACH
    coord.abort_before_start(10)
    coord.note_failure_fanout_done(10)
    coord.mark_filling(tokens[0].fill_key)
    coord.note_fill_success(tokens[0].fill_key, SLOT_BYTES)
    obligation = coord.tokens[tokens[0].token_id]
    assert obligation.released
    assert coord.tokens[shared[0].token_id].released is False
    assert coord.obligations[coord.base_key(1)].subscribers[
        (11, LAYER_B)].state == A.CACHE_SUBSCRIBER_STATE.WOKEN
    coord.release_token(shared[0].token_id)
    assert not coord.obligations


def test_gate5_fill_error_fans_out_once_and_leaves_one_tombstone():
    coord = coordinator()
    status, tokens = reserve_batch_wide(coord, 12, [(LAYER_A, 1),
                                                    (LAYER_B, 1)])
    assert status == RESERVE_COMMITTED
    key = tokens[0].fill_key
    coord.mark_filling(key)
    coord.note_fill_failure(key, SITE_AXI, 30, weight_fill_source(key))
    coord.note_fill_success(key, SLOT_BYTES)
    obligation = coord.obligations[coord.base_key(1)]
    assert obligation.state == A.CACHE_OBLIGATION_STATE.FAILED_DRAINING
    assert coord.slots[obligation.slot_id].state == 5
    assert coord.slots[obligation.slot_id].valid_bytes == 0
    assert all(
        subscriber.state == A.CACHE_SUBSCRIBER_STATE.FAIL_NOTIFIED
        for subscriber in obligation.subscribers.values())
    coord.note_fill_failure(key, SITE_SRAM, 35, weight_fill_source(key))
    assert obligation.first_error[2] == "E_AXI_RESPONSE"
    assert obligation.first_error[0] == 30
    assert obligation.drained_bytes == SLOT_BYTES
    for token in tokens:
        coord.release_token(token.token_id)
    assert obligation.state == A.CACHE_OBLIGATION_STATE.FAILED_RETIRED
    assert not coord.obligations
    assert coord.slots[obligation.slot_id].state == 0
    assert coord.mshr_free == coord.mshr_slots
    assert coord.obligation_free == coord.obligation_slots
    assert coord.subscriber_free == coord.subscriber_slots
    assert len(coord.failure_table) == 1
    assert coord.failure_table[coord.base_key(1)].first_error_code == \
        "E_AXI_RESPONSE"


def test_gate5_failed_fill_never_retries_in_the_same_generation():
    coord = coordinator()
    status, tokens = reserve_batch_wide(coord, 13, [(LAYER_A, 2)])
    assert status == RESERVE_COMMITTED
    key = tokens[0].fill_key
    coord.mark_filling(key)
    coord.note_fill_failure(key, SITE_AXI, 30, weight_fill_source(key))
    coord.release_token(tokens[0].token_id)
    before = coordinate_state(coord)
    retry = reserve_batch_wide(coord, 14, [(LAYER_A, 2)])
    assert retry[0] == RESERVE_FAILED
    assert retry[1] == [coord.base_key(2)]
    assert coordinate_state(coord) == before
    assert coord.next_fill_incarnation == 2


def test_gate5_cancel_and_error_on_the_same_edge_keep_the_first_error():
    coord = coordinator()
    status, tokens = reserve_batch_wide(coord, 15, [(LAYER_A, 1)])
    assert status == RESERVE_COMMITTED
    key = tokens[0].fill_key
    coord.mark_filling(key)
    coord.note_fill_failure(key, SITE_AXI, 40, weight_fill_source(key))
    coord.cancel_member(15)
    obligation = coord.obligations[coord.base_key(1)]
    assert obligation.subscribers[(15, LAYER_A)].state == \
        A.CACHE_SUBSCRIBER_STATE.FAIL_NOTIFIED
    coord.abort_before_start(15)
    assert obligation.subscribers[(15, LAYER_A)].state == \
        A.CACHE_SUBSCRIBER_STATE.FAIL_NOTIFIED
    coord.release_token(tokens[0].token_id)
    assert coord.failure_table[coord.base_key(1)].first_error_tick == 40
    assert coord.failure_table[coord.base_key(1)].first_error_code == \
        "E_AXI_RESPONSE"


def test_gate5_background_fill_settles_a_tombstoned_subscriber():
    coord = coordinator()
    status, tokens = reserve_batch_wide(coord, 16, [(LAYER_A, 1)])
    assert status == RESERVE_COMMITTED
    key = tokens[0].fill_key
    coord.mark_filling(key)
    coord.abort_before_start(16)
    coord.note_failure_fanout_done(16)
    obligation = coord.obligations[coord.base_key(1)]
    assert obligation.subscribers[(16, LAYER_A)].state == \
        A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED
    assert coord.obligations and coord.slots[obligation.slot_id].pins == 1
    coord.note_fill_success(key, SLOT_BYTES)
    assert not coord.obligations
    assert coord.tokens[tokens[0].token_id].released
    assert coord.slots[obligation.slot_id].state == 4
    assert coord.slots[obligation.slot_id].valid_bytes == SLOT_BYTES
    assert coord.mshr_free == coord.mshr_slots
    assert coord.obligation_free == coord.obligation_slots
    warm = reserve_batch_wide(coord, 17, [(LAYER_A, 1)])
    assert warm[0] == RESERVE_COMMITTED
    assert warm[1][0].outcome == A.CACHE_RESIDENCY_OUTCOME.HIT


def test_gate5_batch_set_abort_releases_every_core():
    left = coordinator(core_id=0)
    right = coordinator(core_id=1)
    reserve = BatchCacheReservationSet({0: left, 1: right})
    outcome = reserve.reserve(20, {0: ((LAYER_A, 1),),
                                  1: ((LAYER_A, 2),)})
    assert outcome.status == RESERVE_COMMITTED
    reserve.abort_before_start(20)
    reserve.note_failure_fanout_done(20)
    assert all(left.slots[slot.slot_id].pins == 1 for slot in left.slots
               if slot.state != 0)
    for coordinator_ in (left, right):
        for token in coordinator_.tokens.values():
            if token.outcome == A.CACHE_RESIDENCY_OUTCOME.NEW_FILL:
                coordinator_.mark_filling(token.fill_key)
                coordinator_.note_fill_success(token.fill_key, SLOT_BYTES)
    assert not left.obligations and not right.obligations
    assert all(token.released for token in left.tokens.values())
    assert all(token.released for token in right.tokens.values())
    assert left.subscriber_free == left.subscriber_slots
    assert right.subscriber_free == right.subscriber_slots


def test_gate5_batch_set_cancel_and_release_all_members():
    coord = coordinator()
    reserve = BatchCacheReservationSet({0: coord})
    outcome = reserve.reserve(21, {0: ((LAYER_A, 1), (LAYER_B, 2))})
    assert outcome.status == RESERVE_COMMITTED
    reserve.cancel_member(21)
    assert 21 in coord.cancelled_members
    for token in outcome.tokens:
        coord.mark_filling(token.fill_key)
        coord.note_fill_success(token.fill_key, SLOT_BYTES)
    reserve.release_batch(21)
    assert not coord.obligations
    assert all(token.released for token in coord.tokens.values())
    assert coord.slots[0].state == 4 and coord.slots[1].state == 4


def test_gate5_batch_set_rejects_an_unknown_core_demand():
    coord = coordinator(core_id=0)
    reserve = BatchCacheReservationSet({0: coord})
    with pytest.raises(MeshIrError) as err:
        reserve.reserve(22, {7: ((LAYER_A, 1),)})
    assert err.value.code == "E_MOE_CACHE"

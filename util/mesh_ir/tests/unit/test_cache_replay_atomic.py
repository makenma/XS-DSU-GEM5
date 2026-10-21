import copy

import pytest

from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import (
    CacheReservationCoordinator, apply_cache_state_replay,
    cache_state_replay_document,
)


def coordinator():
    return CacheReservationCoordinator(0, 2, 4096, {1, 2}, 2, 2, 2, 4)


@pytest.mark.parametrize('fault', ['rank', 'duplicate', 'extra_core', 'missing_slot',
                                  'bytes', 'dirty_invalid', 'slot_order'])
def test_rejected_replay_does_not_install_its_valid_prefix(fault):
    owner = coordinator()
    document = cache_state_replay_document(owner, {
        key: 'ab' * 32 for key in ('effective_architecture',
        'program_weight_registry', 'model_weight_image', 'weight_tag_manifest')})
    core = document['cores'][0]
    core['slots'][0].update(state=1, weight_tag_index=1, valid_bytes=128, lru_rank=0)
    core['slots'][1].update(state=1, weight_tag_index=2, valid_bytes=128, lru_rank=1)
    if fault == 'rank': core['slots'][1]['lru_rank'] = 3
    if fault == 'duplicate': core['slots'][1]['weight_tag_index'] = 1
    if fault == 'extra_core': document['cores'].append(copy.deepcopy(core))
    if fault == 'missing_slot': core['slots'].pop()
    if fault == 'bytes': core['slots'][1]['valid_bytes'] = 4097
    if fault == 'dirty_invalid': core['slots'][1]['state'] = 0
    if fault == 'slot_order': core['slots'].reverse()
    before = owner.snapshot()
    with pytest.raises(MeshIrError):
        apply_cache_state_replay(owner, document)
    assert owner.snapshot() == before


def test_installation_replaces_old_valid_slots_and_rejects_live_pins():
    owner = coordinator()
    cold = cache_state_replay_document(owner, {
        key: 'ab' * 32 for key in ('effective_architecture',
        'program_weight_registry', 'model_weight_image', 'weight_tag_manifest')})
    warm = copy.deepcopy(cold)
    warm['cores'][0]['slots'][0].update(state=1, weight_tag_index=1,
                                      valid_bytes=128, lru_rank=0)
    apply_cache_state_replay(owner, warm)
    owner.slots[0].pins = 1
    with pytest.raises(MeshIrError):
        apply_cache_state_replay(owner, cold)
    assert owner.slots[0].weight_tag_index == 1
    owner.slots[0].pins = 0
    apply_cache_state_replay(owner, cold)
    assert owner.snapshot() == cold['cores'][0]


@pytest.mark.parametrize('fault', ['code', 'core', 'generation', 'incarnation', 'reserved'])
def test_failure_provenance_is_checked_before_installation(fault):
    from mesh_ir.generated.agent_abi import DETAIL_CODE
    from mesh_ir.moe_weight_cache import WeightFillKey, weight_fill_source

    owner = coordinator()
    document = cache_state_replay_document(owner, {
        key: 'ab' * 32 for key in ('effective_architecture',
        'program_weight_registry', 'model_weight_image', 'weight_tag_manifest')})
    core = document['cores'][0]
    core['next_fill_incarnation'] = 2
    source = weight_fill_source(WeightFillKey(owner.base_key(1), 1))
    wire = bytearray(source.wire_bytes())
    error = {'tick': 40, 'source_key_wire': wire.hex(),
             'error_code': DETAIL_CODE['E_AXI_RESPONSE']}
    core['failure_tombstones'] = [{'weight_tag_index': 1, 'first_error': error}]
    if fault == 'code': error['error_code'] = DETAIL_CODE['E_OK']
    if fault == 'core': wire[2] = 1
    if fault == 'generation': wire[32] = 1
    if fault == 'incarnation': wire[36] = 2
    if fault == 'reserved': wire[6] = 1
    error['source_key_wire'] = wire.hex()
    before = owner.snapshot()
    with pytest.raises(MeshIrError):
        apply_cache_state_replay(owner, document)
    assert owner.snapshot() == before


def test_large_failure_tick_round_trips_as_canonical_u64_json():
    from mesh_ir.moe_weight_cache import FailureTombstone, WeightFillKey, weight_fill_source

    owner = coordinator()
    tick = (1 << 53) + 20
    owner.next_fill_incarnation = 2
    key = owner.base_key(1)
    owner.failure_table[key] = FailureTombstone(1, tick,
        weight_fill_source(WeightFillKey(key, 1)), 'E_AXI_RESPONSE')
    document = cache_state_replay_document(owner, {
        name: 'ab' * 32 for name in ('effective_architecture',
        'program_weight_registry', 'model_weight_image', 'weight_tag_manifest')})
    assert document['cores'][0]['failure_tombstones'][0]['first_error']['tick'] == '0x0020000000000014'
    restored = coordinator()
    apply_cache_state_replay(restored, document)
    assert restored.failure_table[key].first_error_tick == tick

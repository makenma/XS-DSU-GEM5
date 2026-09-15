import dataclasses
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.kv_oracle import KvOracle
from mesh_ir.kv_types import (
    ALLOW_REPREFILL,
    NO_INTENT,
    KvPinPhase,
    KvRollbackPayload,
    KvRollbackToken,
    KvWaitReason,
    REQUIRE_KV_REUSE,
    KvAdmissionIntent,
    KvAdmissionOutcome,
    KvAppendArm,
    KvAppendTerminal,
    KvAppendObligation,
    KvCapacity,
    KvDmaCount,
    KvEdge,
    KvErrorCandidate,
    KvEvictionEvent,
    KvFaultEvent,
    KvGeometry,
    KvLaterFault,
    KvOwnerTerminal,
    KvPath,
    KvPersistentState,
    KvPromotionOutcome,
    KvRecord,
    KvReleaseIntent,
    KvReleaseOutcome,
    KvReleaseWaiter,
    KvRequestPin,
    KvRollbackKind,
    KvRuntimeView,
    KvSnapshotSource,
    KvState,
    KvStatus,
    KvTerminalSnapshot,
    KvViewArm,
    kv_contract_digest,
)
from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import ErrorSourceKey

BYTES_PER_TOKEN = 64
SLOT_BYTES = 4096
CONTRACT = kv_contract_digest(bytes(range(32)), bytes(range(32, 64)),
                              bytes(range(64, 96)), BYTES_PER_TOKEN)
OTHER_CONTRACT = kv_contract_digest(bytes(range(1, 33)), bytes(range(32, 64)),
                                    bytes(range(64, 96)), BYTES_PER_TOKEN)


def geometry(sessions=4):
    return KvGeometry(region_base=0x100000, slot_bytes=SLOT_BYTES,
                      slot_alignment=SLOT_BYTES, max_sessions=sessions,
                      bytes_per_token=BYTES_PER_TOKEN)


def capacity(records=4, tombstones=2, waiters=4, releases=2):
    return KvCapacity(record_entries=records, tombstone_entries=tombstones,
                      admission_wait_entries=waiters,
                      release_waiter_entries=releases)


def oracle(sessions=4, **kwargs):
    return KvOracle(geometry(sessions), capacity(**kwargs))


def admit(request_id, session_id, generation=1, flags=0, qos=0,
          ready_tick=None, deadline=0, contract=CONTRACT, cached=0,
          after_round=0):
    return KvAdmissionIntent(
        request_id=request_id, session_id=session_id, kv_handle=session_id,
        generation=generation, contract_digest=contract,
        deadline_or_max=deadline,
        qos=qos, ready_tick=request_id if ready_tick is None else ready_tick,
        flags=flags, required_cached_tokens=cached,
        required_tokens_after_round=after_round)


def release(request_id, session_id, generation=1):
    return KvReleaseIntent(request_id, session_id, session_id, generation)


def candidate(tick, ordinal=0, code=None):
    return KvErrorCandidate(
        tick, ErrorSourceKey(error_class=A.MOE_ERROR_CLASS.DMA_AXI,
                             core_id_or_ffff=0xFFFF,
                             domain=A.MESH_OBJECT_DOMAIN.KV_RUNTIME,
                             object_kind=A.MESH_OBJECT_KIND.KV_APPEND,
                             region_group_id=0, region_id=0, ordinal=ordinal,
                             generation=0),
        AG.DETAIL_CODE["E_AXI_RESPONSE"] if code is None else code)


def resident_state(session_id=7, generation=1, cached=8, view_epoch=3,
                   last_use=2, slot_id=0):
    return KvRecord(
        session_id=session_id, kv_handle=session_id, generation=generation,
        contract_digest=CONTRACT, state=KvState.RESIDENT, slot_id=slot_id,
        cached_tokens=cached, valid_bytes=cached * BYTES_PER_TOKEN,
        content_digest=b"c" * 32, view_epoch=view_epoch,
        last_use_epoch=last_use, pin_count=0, admission_claim_count=0,
        outstanding_kv_dma=0, first_error=None, diagnostic_prefix_tokens=0,
        diagnostic_prefix_bytes=0, diagnostic_prefix_digest=None)


def evicted_state(session_id=7, generation=1, view_epoch=7, last_use=3):
    return KvRecord(
        session_id=session_id, kv_handle=session_id, generation=generation,
        contract_digest=CONTRACT, state=KvState.EVICTED, slot_id=None,
        cached_tokens=0, valid_bytes=0, content_digest=None,
        view_epoch=view_epoch, last_use_epoch=last_use, pin_count=0,
        admission_claim_count=0, outstanding_kv_dma=0, first_error=None,
        diagnostic_prefix_tokens=0, diagnostic_prefix_bytes=0,
        diagnostic_prefix_digest=None)


def make_pin(request_id=1, session_id=11, generation=1, payload=None,
             phase=KvPinPhase.PRESTART, required=0, flags=0, cached=0):
    return KvRequestPin(request_id, session_id, session_id, generation,
                        CONTRACT, flags, cached, required, phase, payload)


def make_payload(serial=1, intent=KvPath.INITIAL_PREFILL, prior_absent=True,
                 prior=None):
    if prior is None:
        prior = dataclasses.replace(
            resident_state(session_id=11, cached=0), state=KvState.ALLOCATING,
            slot_id=None, view_epoch=0, last_use_epoch=0,
            content_digest=None)
    return KvRollbackPayload(serial, intent, prior_absent, prior)


def load_record(record, sessions=4, next_epoch=None, tombstones=()):
    kv = oracle(sessions)
    owners = [None] * sessions
    if record.slot_id is not None:
        owners[record.slot_id] = record.tuple
    epoch = next_epoch if next_epoch is not None else (
        record.last_use_epoch + 1 if record.last_use_epoch else 1)
    kv.load_persistent(KvPersistentState(tuple(owners), (record,),
                                         tuple(tombstones), epoch))
    return kv


# ------------------------------------------------------------------ A

def test_two_waiters_compete_for_one_slot():
    kv = oracle(sessions=1, records=2, waiters=2)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    assert first.promotions[1] == KvPromotionOutcome.PINNED
    second_edge = KvEdge(tick=2, admissions=[admit(2, 22)])
    second = kv.commit_edge(second_edge)
    assert second.admissions[2] == KvAdmissionOutcome.CLAIMED
    assert second.promotions[2] == KvPromotionOutcome.WAITING_SLOT
    assert kv.find_record(11, 11).slot_id == 0
    assert kv.find_record(22, 22).slot_id is None
    assert kv.claims[2].ready_tick == 2

    kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    third = kv.commit_edge(KvEdge(tick=4,
                                  admissions=[admit(2, 22, ready_tick=99)]))
    assert third.promotions[2] == KvPromotionOutcome.PINNED
    assert kv.find_record(22, 22).slot_id == 0
    assert kv.next_kv_use_epoch == 3
    assert kv.validate() == ()


def test_record_capacity_is_enforced_at_the_head_commit():
    kv = oracle(sessions=2, records=1, waiters=2)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    assert first.admissions[1] == KvAdmissionOutcome.CLAIMED
    second = kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    assert second.admissions[2] == KvAdmissionOutcome.WAITING
    assert second.waits[2] == KvWaitReason.RECORD_TABLE_FULL
    assert kv.record_count() == 1
    assert kv.find_record(22, 22) is None
    assert kv.validate() == ()
    for tick in range(3, 6):
        held = kv.commit_edge(KvEdge(tick=tick))
        assert held.waits[2] == KvWaitReason.RECORD_TABLE_FULL
        assert kv.record_count() == 1
    kv.commit_edge(KvEdge(tick=6, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    released = kv.commit_edge(KvEdge(tick=7, releases=[release(9, 11)]))
    assert released.releases[9] == KvReleaseOutcome.SUCCESS
    assert released.admissions[2] == KvAdmissionOutcome.CLAIMED
    assert released.promotions[2] == KvPromotionOutcome.PINNED
    assert kv.record_count() == 1
    assert 2 in kv.pins
    assert kv.validate() == ()


def test_admission_wait_capacity_backpressures_without_identity_loss():
    kv = oracle(sessions=1, records=4, waiters=1)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    first = kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    assert first.admissions[2] == KvAdmissionOutcome.CLAIMED
    assert first.promotions[2] == KvPromotionOutcome.WAITING_SLOT
    second = kv.commit_edge(KvEdge(tick=3, admissions=[admit(3, 33, qos=4)]))
    assert second.admissions[3] == KvAdmissionOutcome.BACKPRESSURE
    assert kv.find_record(33, 33) is None
    for tick in range(4, 8):
        retry = kv.commit_edge(KvEdge(tick=tick, admissions=[
            admit(3, 33, qos=0)]))
        assert retry.admissions[3] == KvAdmissionOutcome.BACKPRESSURE
    assert list(kv.claims) == [2]
    assert kv.claims[2].ready_tick == 2
    assert kv.validate() == ()


def test_tombstone_capacity_bound_minus_one_fails_closed():
    kv = oracle(sessions=2, records=2, tombstones=1)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    kv.commit_edge(KvEdge(tick=4, owner_terminals=[
        KvOwnerTerminal(2, KvStatus.SUCCESS)]))
    first = kv.commit_edge(KvEdge(tick=5, releases=[release(9, 11)]))
    assert first.releases[9] == KvReleaseOutcome.SUCCESS
    second = kv.commit_edge(KvEdge(tick=6, releases=[release(9, 22)]))
    assert second.fatal is not None
    assert "E_CAPACITY_PLAN" in second.fatal
    assert kv.find_record(22, 22) is not None
    assert kv.tombstone_count() == 1


def test_release_waiter_capacity_returns_busy_without_replacing():
    kv = oracle(sessions=2, records=2, releases=1)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    first = kv.commit_edge(KvEdge(tick=3, releases=[release(9, 11)]))
    assert first.releases == {}
    second = kv.commit_edge(KvEdge(tick=4, releases=[release(8, 22)]))
    assert second.releases[8] == KvReleaseOutcome.BUSY
    assert 9 in kv.release_waiters
    assert 8 not in kv.release_waiters
    assert kv.validate() == ()


def test_waiting_identity_and_age_survive_retries():
    kv = oracle(sessions=1, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22, qos=5)]))
    for tick in range(3, 8):
        kv.commit_edge(KvEdge(tick=tick, admissions=[admit(2, 22, qos=0)]))
    assert 2 in kv.claims
    assert kv.claims[2].ready_tick == 2
    assert kv.claims[2].qos == 5
    assert kv.find_record(11, 11).slot_id == 0


# ------------------------------------------------------------------ B

def test_same_edge_input_permutations_are_indistinguishable():
    def run(order):
        kv = oracle(sessions=2, records=3, tombstones=2, waiters=3)
        kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
        kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
        kv.commit_edge(KvEdge(tick=3, admissions=[admit(3, 33)]))
        kv.commit_edge(KvEdge(tick=4, owner_terminals=[
            KvOwnerTerminal(2, KvStatus.SUCCESS)]))
        parts = {
            "admissions": [admit(4, 44)],
            "releases": [release(9, 22)],
            "owner_terminals": [KvOwnerTerminal(1, KvStatus.CANCELLED)],
            "faults": [],
        }
        edge = KvEdge(tick=9)
        for key in order:
            setattr(edge, key, list(parts[key]))
        result = kv.commit_edge(edge)
        return kv.edge_scalars(result)

    baseline = run(["admissions", "releases", "owner_terminals", "faults"])
    for order in itertools.permutations(
            ["admissions", "releases", "owner_terminals", "faults"]):
        assert run(list(order)) == baseline


def test_same_edge_release_and_eviction_are_permutation_stable():
    def run(reversed_order):
        kv = oracle(sessions=1, records=3, tombstones=2)
        kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
        kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
        kv.commit_edge(KvEdge(tick=3, owner_terminals=[
            KvOwnerTerminal(1, KvStatus.SUCCESS),
            KvOwnerTerminal(2, KvStatus.SUCCESS)]))
        admissions = [admit(5, 55)]
        releases = [release(9, 11)]
        edge = KvEdge(tick=4, admissions=admissions, releases=releases)
        if reversed_order:
            edge = KvEdge(tick=4, admissions=list(reversed(admissions)),
                          releases=list(reversed(releases)))
        result = kv.commit_edge(edge)
        return kv.edge_scalars(result)

    assert run(False) == run(True)


# ------------------------------------------------------------------ C

def test_cancel_before_claim_leaves_no_session_effect():
    kv = oracle()
    result = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)],
                                   owner_terminals=[
                                       KvOwnerTerminal(
                                           1, KvStatus.CANCELLED,
                                           strict=False)]))
    assert result.ownerless_cancels == [1]
    assert result.admissions == {}
    assert kv.record_count() == 0
    assert kv.claims == {}
    assert kv.pins == {}
    assert kv.validate() == ()


def test_cancel_after_claim_restores_the_prior_snapshot():
    prior = evicted_state(session_id=7)
    kv = oracle(sessions=1, records=3)
    kv.load_persistent(KvPersistentState(
        ((11, 11),),
        (prior, resident_state(session_id=11, slot_id=0, last_use=2)),
        (), 4))
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL)]))
    assert first.admissions[1] == KvAdmissionOutcome.CLAIMED
    assert first.promotions[1] == KvPromotionOutcome.WAITING_SLOT
    assert 1 in kv.claims
    assert kv.claims[1].prior_absent is False
    assert kv.claims[1].prior == prior
    assert kv.find_record(7, 7).slot_id is None
    second = kv.commit_edge(KvEdge(
        tick=2, owner_terminals=[KvOwnerTerminal(
            1, KvStatus.CANCELLED, strict=False)]))
    assert kv.find_record(7, 7) == prior
    assert second.terminals[1].source == KvSnapshotSource.ADMISSION_CLAIM
    assert kv.claims == {}
    assert kv.pins == {}
    assert kv.validate() == ()


def test_cancel_after_claim_to_pin_rolls_back_once():
    kv = oracle()
    first = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    handoff = first.handoffs[1]
    second = kv.commit_edge(KvEdge(
        tick=2, owner_terminals=[KvOwnerTerminal(
            1, KvStatus.CANCELLED, token=handoff, strict=False)]))
    assert second.rollbacks[1] == KvRollbackKind.INITIAL_ERROR_RECORD
    record = kv.find_record(11, 11)
    assert record.state == KvState.ERROR
    assert record.slot_id is None
    assert record.cached_tokens == 0
    assert record.view_epoch == 0
    assert record.contract_digest == CONTRACT
    assert kv.pins == {}
    assert kv.validate() == ()
    third = kv.commit_edge(KvEdge(
        tick=3, owner_terminals=[KvOwnerTerminal(
            1, KvStatus.CANCELLED, token=handoff, strict=False)]))
    assert third.fatal is not None


def test_cancel_after_first_prefill_start_keeps_the_record():
    kv = oracle()
    first = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, core_starts=[1]))
    second = kv.commit_edge(KvEdge(tick=3, append_arms=[
        KvAppendArm(1, 0, 4)]))
    assert second.fatal is None
    third = kv.commit_edge(KvEdge(tick=4, append_terminals=[
        KvAppendTerminal(1, (True, True, True, True), b"d" * 32)]))
    assert third.fatal is None
    fourth = kv.commit_edge(KvEdge(
        tick=5, owner_terminals=[KvOwnerTerminal(
            1, KvStatus.CANCELLED, strict=False)]))
    assert fourth.rollbacks == {}
    record = kv.find_record(11, 11)
    assert record.state == KvState.RESIDENT
    assert record.cached_tokens == 4
    assert fourth.terminals[1].cached_tokens == 4


# ------------------------------------------------------------------ D

def test_waiting_head_survives_temporary_backpressure():
    prior = resident_state(cached=0, slot_id=0, last_use=1)
    prior.content_digest = None
    kv = load_record(prior, sessions=1, next_epoch=2)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, cached=0)]))
    assert first.promotions[1] == KvPromotionOutcome.PINNED
    queued = kv.commit_edge(KvEdge(tick=2, admissions=[
        admit(2, 7, flags=ALLOW_REPREFILL, cached=0, ready_tick=2)]))
    assert queued.admissions[2] == KvAdmissionOutcome.WAITING
    assert queued.waits[2] == KvWaitReason.TUPLE_OWNER_ACTIVE
    retry = kv.commit_edge(KvEdge(tick=3, admissions=[
        admit(2, 7, flags=ALLOW_REPREFILL, cached=0, ready_tick=99)]))
    assert retry.waits[2] == KvWaitReason.TUPLE_OWNER_ACTIVE
    assert kv.waiters[2].ready_tick == 2
    assert 2 in kv.waiters and 2 not in kv.claims
    done = kv.commit_edge(KvEdge(tick=4, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert done.admissions[2] == KvAdmissionOutcome.CLAIMED
    assert done.promotions[2] == KvPromotionOutcome.PINNED
    assert 2 in kv.pins
    assert kv.validate() == ()


def test_release_pending_does_not_block_the_existing_owner():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11, after_round=6)]))
    kv.commit_edge(KvEdge(tick=2))
    pending = kv.commit_edge(KvEdge(tick=3, releases=[release(9, 11)]))
    assert pending.releases == {}
    assert kv.release_pending(11, 11)
    blocked = kv.commit_edge(KvEdge(tick=4, admissions=[
        admit(2, 11, flags=ALLOW_REPREFILL)]))
    assert blocked.admissions[2] == KvAdmissionOutcome.WAITING
    assert blocked.waits[2] == KvWaitReason.RELEASE_PENDING
    assert 2 in kv.waiters
    owner = kv.commit_edge(KvEdge(tick=5, append_arms=[KvAppendArm(1, 0, 6)]))
    assert owner.fatal is None
    assert kv.find_record(11, 11).outstanding_kv_dma == 0
    kv.commit_edge(KvEdge(tick=6, append_terminals=[
        KvAppendTerminal(1, (True,) * 6, b"e" * 32)]))
    assert kv.find_record(11, 11).cached_tokens == 6
    done = kv.commit_edge(KvEdge(tick=7, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert done.releases[9] == KvReleaseOutcome.SUCCESS
    assert kv.find_record(11, 11) is None
    assert kv.tombstones == {(11, 11): 2}
    assert kv.validate() == ()


def test_release_waits_for_the_full_predicate():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11, after_round=4)]))
    kv.commit_edge(KvEdge(tick=2, releases=[release(9, 11)]))
    kv.commit_edge(KvEdge(tick=3, dma_accepts=[KvDmaCount(1, 2)]))
    assert kv.find_record(11, 11).outstanding_kv_dma == 2
    blocked = kv.commit_edge(KvEdge(tick=4))
    assert blocked.releases == {}
    kv.commit_edge(KvEdge(tick=5, dma_terminals=[KvDmaCount(1, 2)]))
    kv.commit_edge(KvEdge(tick=6, append_arms=[KvAppendArm(1, 0, 4)]))
    assert kv.commit_edge(KvEdge(tick=7)).releases == {}
    done = kv.commit_edge(KvEdge(tick=8, append_terminals=[
        KvAppendTerminal(1, (True,) * 4, b"k" * 32)]))
    assert done.releases == {}
    final = kv.commit_edge(KvEdge(tick=9, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert final.releases[9] == KvReleaseOutcome.SUCCESS
    assert kv.find_record(11, 11) is None


# ------------------------------------------------------------------ E

def test_reprefill_prestart_abort_restores_the_evicted_snapshot():
    prior = evicted_state(view_epoch=7, last_use=3)
    kv = load_record(prior)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, after_round=8)]))
    assert first.promotions[1] == KvPromotionOutcome.PINNED
    assert kv.find_record(7, 7).view_epoch == 8
    handoff = first.handoffs[1]
    assert kv.pins[1].payload.intent == KvPath.REPREFILL
    assert kv.pins[1].payload.prior_absent is False
    second = kv.commit_edge(KvEdge(
        tick=2, owner_terminals=[KvOwnerTerminal(
            1, KvStatus.CANCELLED, token=handoff, strict=False)]))
    assert second.rollbacks[1] == KvRollbackKind.RESTORE_PRIOR
    assert kv.find_record(7, 7) == prior
    assert kv.slot_owners[0] is None
    assert kv.validate() == ()


def test_later_fault_after_core_start_keeps_the_new_prefix():
    prior = evicted_state(view_epoch=7, last_use=3)
    kv = load_record(prior)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, after_round=9)]))
    assert first.promotions[1] == KvPromotionOutcome.PINNED
    kv.commit_edge(KvEdge(tick=2, core_starts=[1]))
    kv.commit_edge(KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 9)]))
    kv.commit_edge(KvEdge(tick=4, append_terminals=[
        KvAppendTerminal(1, (True,) * 9, b"f" * 32)]))
    record = kv.find_record(7, 7)
    assert record.cached_tokens == 9
    assert record.view_epoch == 9
    second = kv.commit_edge(KvEdge(tick=5, later_faults=[
        KvLaterFault(1, candidate(5))]))
    record = kv.find_record(7, 7)
    assert record.cached_tokens == 9
    assert record.valid_bytes == 9 * BYTES_PER_TOKEN
    assert record.view_epoch == 9
    assert record.state == KvState.ERROR
    assert second.terminals[1].status == KvStatus.ERROR
    assert second.rollbacks == {}
    assert kv.pins == {}


def test_initial_prestart_abort_leaves_a_slotless_error_record():
    kv = oracle()
    first = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    second = kv.commit_edge(KvEdge(
        tick=2, owner_terminals=[KvOwnerTerminal(
            1, KvStatus.CANCELLED, token=first.handoffs[1],
            strict=False)]))
    record = kv.find_record(11, 11)
    assert second.rollbacks[1] == KvRollbackKind.INITIAL_ERROR_RECORD
    assert record.state == KvState.ERROR
    assert record.slot_id is None
    assert record.cached_tokens == 0
    assert record.valid_bytes == 0
    assert record.content_digest is None
    assert record.view_epoch == 0
    assert record.last_use_epoch == 0
    assert record.contract_digest == CONTRACT
    assert kv.find_record(11, 11) is not None


def test_kv_reuse_prestart_abort_restores_the_resident_snapshot():
    prior = resident_state(cached=8, view_epoch=4, last_use=5)
    kv = load_record(prior, next_epoch=6)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, cached=8, after_round=8)]))
    assert first.promotions[1] == KvPromotionOutcome.PINNED
    assert kv.find_record(7, 7).state == KvState.RESIDENT
    second = kv.commit_edge(KvEdge(
        tick=2, owner_terminals=[KvOwnerTerminal(
            1, KvStatus.CANCELLED, token=first.handoffs[1],
            strict=False)]))
    assert second.rollbacks[1] == KvRollbackKind.RESTORE_PRIOR
    assert kv.find_record(7, 7) == prior


# ------------------------------------------------------------------ F

def test_terminal_snapshot_survives_same_edge_eviction_and_release():
    kv = oracle(sessions=1, records=2, tombstones=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11, after_round=8)]))
    kv.commit_edge(KvEdge(tick=2))
    kv.commit_edge(KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 8)]))
    kv.commit_edge(KvEdge(tick=4, append_terminals=[
        KvAppendTerminal(1, (True,) * 8, b"g" * 32)]))
    final = kv.commit_edge(KvEdge(tick=5, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    snapshot = final.terminals[1]
    assert snapshot.state == KvState.RESIDENT
    assert snapshot.cached_tokens == 8
    evicted = kv.commit_edge(KvEdge(tick=6, admissions=[admit(2, 22)]))
    assert evicted.evictions_started
    assert kv.find_record(11, 11).state == KvState.EVICTING
    done = kv.commit_edge(KvEdge(tick=7, releases=[release(9, 11)]))
    assert kv.find_record(11, 11) is None
    assert done.releases[9] == KvReleaseOutcome.SUCCESS
    assert final.terminals[1] == snapshot
    assert snapshot.valid_bytes == 8 * BYTES_PER_TOKEN


def test_terminal_snapshot_requires_the_required_prefix():
    kv = oracle()
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 11, after_round=9)]))
    kv.commit_edge(KvEdge(tick=2, core_starts=[1]))
    kv.commit_edge(KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 4)]))
    kv.commit_edge(KvEdge(tick=4, append_terminals=[
        KvAppendTerminal(1, (True,) * 4, None)]))
    second = kv.commit_edge(KvEdge(tick=5, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert second.fatal is not None
    assert "required prefix" in second.fatal
    assert first.promotions[1] == KvPromotionOutcome.PINNED


# ------------------------------------------------------------------ G

def test_eviction_excludes_every_live_owner():
    def setup():
        kv = oracle(sessions=1, records=3)
        kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
        return kv

    kv = setup()
    pinned = kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    assert pinned.evictions_started == []
    assert pinned.promotions[2] == KvPromotionOutcome.WAITING_SLOT

    kv = setup()
    kv.commit_edge(KvEdge(tick=2, releases=[release(9, 11)]))
    blocked = kv.commit_edge(KvEdge(tick=3, admissions=[admit(2, 22)]))
    assert blocked.evictions_started == []
    assert blocked.promotions[2] == KvPromotionOutcome.WAITING_SLOT

    kv = setup()
    kv.commit_edge(KvEdge(tick=2, dma_accepts=[KvDmaCount(1, 1)]))
    busy = kv.commit_edge(KvEdge(tick=3, admissions=[admit(2, 22)]))
    assert busy.evictions_started == []

    kv = setup()
    kv.commit_edge(KvEdge(tick=2, append_arms=[KvAppendArm(1, 0, 3)]))
    appending = kv.commit_edge(KvEdge(tick=3, admissions=[admit(2, 22)]))
    assert appending.evictions_started == []


def test_eviction_prefers_error_then_epoch_then_tuple():
    error = dataclasses.replace(resident_state(session_id=11, last_use=9),
                                state=KvState.ERROR)
    kv = oracle(sessions=2, records=3)
    kv.load_persistent(KvPersistentState(
        (error.tuple, (22, 22)),
        (error, resident_state(session_id=22, slot_id=1, last_use=1)),
        (), 10))
    result = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 33)]))
    assert result.evictions_started[0].session_id == 11

    kv = oracle(sessions=2, records=3)
    kv.load_persistent(KvPersistentState(
        ((11, 11), (22, 22)),
        (resident_state(session_id=22, slot_id=1, last_use=1),
         resident_state(session_id=11, last_use=1)),
        (), 2))
    result = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 33)]))
    assert result.evictions_started[0].session_id == 11

    kv = oracle(sessions=2, records=3)
    kv.load_persistent(KvPersistentState(
        ((11, 11), (22, 22)),
        (resident_state(session_id=11, last_use=4),
         resident_state(session_id=22, slot_id=1, last_use=4)),
        (), 5))
    result = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 33)]))
    assert result.evictions_started[0].session_id == 11


def test_eviction_completes_on_the_next_edge_and_frees_the_lowest_slot():
    kv = oracle(sessions=1, records=3)
    kv.load_persistent(KvPersistentState(
        ((11, 11),), (resident_state(session_id=11),), (), 3))
    first = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 22)]))
    assert first.evictions_started[0].session_id == 11
    assert first.promotions[1] == KvPromotionOutcome.WAITING_SLOT
    record = kv.find_record(11, 11)
    assert record.state == KvState.EVICTING
    assert record.slot_id == 0
    second = kv.commit_edge(KvEdge(tick=2))
    assert second.evictions_completed[0].session_id == 11
    assert second.promotions[1] == KvPromotionOutcome.PINNED
    assert kv.find_record(11, 11).state == KvState.EVICTED
    assert kv.find_record(22, 22).slot_id == 0


def test_error_eviction_preserves_the_diagnostic_prefix():
    prefix = 4
    error = dataclasses.replace(
        resident_state(session_id=11, cached=prefix),
        state=KvState.ERROR, content_digest=b"h" * 32)
    kv = oracle(sessions=1, records=3)
    kv.load_persistent(KvPersistentState(
        ((11, 11),), (error,), (), 3))
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 22)]))
    kv.commit_edge(KvEdge(tick=2))
    record = kv.find_record(11, 11)
    assert record.state == KvState.ERROR
    assert record.slot_id is None
    assert record.cached_tokens == 0
    assert record.diagnostic_prefix_tokens == prefix
    assert record.diagnostic_prefix_bytes == prefix * BYTES_PER_TOKEN
    assert record.diagnostic_prefix_digest == b"h" * 32
    assert record.contract_digest == CONTRACT
    assert kv.validate() == ()


# ------------------------------------------------------------------ H

def test_duplicate_and_unknown_owner_operations_fail_closed():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    again = kv.commit_edge(KvEdge(tick=2, admissions=[admit(1, 11)]))
    assert again.fatal == "duplicate KV acquire for a pinned request"
    assert sorted(kv.pins) == [1]
    unknown = kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(5, KvStatus.SUCCESS)]))
    assert unknown.fatal is not None
    assert kv.validate() == () or kv.fatal is not None


def test_double_release_is_fatal():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    first = kv.commit_edge(KvEdge(tick=2, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert first.fatal is None
    second = kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert second.fatal is not None


def test_claim_and_pin_must_not_coexist():
    kv = oracle(sessions=1, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    state = kv.save_live()
    clone = oracle(sessions=1, records=2, waiters=2)
    clone.load_live(state)
    assert clone.validate() == ()
    broken = dataclasses.replace(state, pins=(
        make_pin(2, 22),))
    with pytest.raises(MeshIrError):
        clone.load_live(broken)


def test_illegal_tagged_states_are_rejected():
    kv = oracle()
    with pytest.raises(MeshIrError):
        load_record(dataclasses.replace(resident_state(),
                                        state=KvState.RESIDENT, slot_id=None))
    with pytest.raises(MeshIrError):
        load_record(dataclasses.replace(resident_state(), slot_id=None,
                                        state=KvState.EVICTED,
                                        cached_tokens=3,
                                        valid_bytes=3 * BYTES_PER_TOKEN))
    with pytest.raises(MeshIrError):
        load_record(dataclasses.replace(resident_state(),
                                        valid_bytes=1))
    assert kv.validate() == ()


def test_pin_release_with_live_kv_work_is_fatal():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, dma_accepts=[KvDmaCount(1, 1)]))
    late = kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert late.fatal is not None
    assert "live KV work" in late.fatal


def test_dma_underflow_is_fatal():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    result = kv.commit_edge(KvEdge(tick=2, dma_terminals=[KvDmaCount(1, 1)]))
    assert result.fatal is not None
    assert "underflow" in result.fatal


def test_generation_and_epoch_overflow_fail_closed():
    kv = load_record(resident_state(generation=0xFFFFFFFF), next_epoch=3)
    kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, generation=0xFFFFFFFF, flags=ALLOW_REPREFILL, cached=8,
              after_round=8)]))
    kv.commit_edge(KvEdge(tick=2, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    result = kv.commit_edge(KvEdge(
        tick=3, releases=[release(9, 7, generation=0xFFFFFFFF)]))
    assert result.fatal is not None
    assert "generation" in result.fatal
    assert (7, 7) not in kv.tombstones
    assert kv.find_record(7, 7) is not None

    kv = oracle()
    kv.next_kv_use_epoch = 0xFFFFFFFFFFFFFFFF
    result = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    assert result.fatal is not None


# ------------------------------------------------------------------ I

def test_contract_generation_and_tuple_failures_are_pre_admission():
    kv = load_record(resident_state())
    mismatch = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, cached=8,
              contract=OTHER_CONTRACT)]))
    assert mismatch.admissions[1] == KvAdmissionOutcome.E_KV_CONTRACT_MISMATCH
    record = kv.find_record(7, 7)
    assert record.pin_count == 0
    assert record.admission_claim_count == 0
    assert kv.claims == {}
    assert kv.pins == {}

    stale = kv.commit_edge(KvEdge(tick=2, admissions=[
        admit(2, 7, generation=2, flags=ALLOW_REPREFILL, cached=8)]))
    assert stale.admissions[2] == KvAdmissionOutcome.E_KV_STALE_GENERATION
    missing = kv.commit_edge(KvEdge(tick=3, admissions=[
        admit(3, 99, flags=ALLOW_REPREFILL)]))
    assert missing.admissions[3] == KvAdmissionOutcome.E_KV_SESSION_NOT_FOUND
    assert kv.record_count() == 1


def test_initial_duplicate_and_released_tuple_are_rejected():
    kv = oracle(tombstones=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    duplicate = kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 11)]))
    assert duplicate.admissions[2] == KvAdmissionOutcome.E_SESSION_EXISTS
    kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    released = kv.commit_edge(KvEdge(tick=4, releases=[release(9, 11)]))
    assert released.releases[9] == KvReleaseOutcome.SUCCESS
    again = kv.commit_edge(KvEdge(tick=5, admissions=[admit(3, 11)]))
    assert again.admissions[3] == KvAdmissionOutcome.E_SESSION_EXISTS
    old = kv.commit_edge(KvEdge(tick=6, admissions=[
        admit(4, 11, flags=ALLOW_REPREFILL)]))
    assert old.admissions[4] == KvAdmissionOutcome.E_KV_STALE_GENERATION
    tomb_release = kv.commit_edge(KvEdge(tick=7, releases=[release(9, 11)]))
    assert tomb_release.releases[9] == KvReleaseOutcome.STALE_GENERATION


def test_post_admission_policy_errors_keep_the_claim_then_terminalize():
    prior = evicted_state()
    kv = load_record(prior)
    result = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=REQUIRE_KV_REUSE)]))
    assert result.admissions[1] == KvAdmissionOutcome.CLAIMED
    assert result.promotions[1] == KvPromotionOutcome.E_KV_REUSE_REQUIRED
    snapshot = result.terminals[1]
    assert snapshot.source == KvSnapshotSource.ADMISSION_CLAIM
    assert snapshot.status == KvStatus.ERROR
    assert kv.find_record(7, 7) == prior
    assert kv.claims == {}
    assert kv.pins == {}

    resident = resident_state(cached=8)
    kv = load_record(resident, next_epoch=6)
    result = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, cached=3)]))
    assert result.promotions[1] == KvPromotionOutcome.E_KV_TOKEN_MISMATCH
    assert kv.find_record(7, 7) == resident


def test_error_records_are_not_treated_as_absent():
    error = dataclasses.replace(evicted_state(), state=KvState.ERROR)
    kv = load_record(error)
    result = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL)]))
    assert result.admissions[1] == KvAdmissionOutcome.E_KV_STATE
    assert kv.find_record(7, 7) is not None
    released = kv.commit_edge(KvEdge(tick=2, releases=[release(9, 7)]))
    assert released.releases[9] == KvReleaseOutcome.SUCCESS


# ------------------------------------------------------------------ J

def test_persistent_round_trip_and_tamper_rejection():
    first = resident_state(session_id=11)
    error = dataclasses.replace(
        resident_state(session_id=22, slot_id=1, cached=4),
        state=KvState.ERROR, content_digest=None)
    kv = oracle(sessions=2, records=2, tombstones=1)
    kv.load_persistent(KvPersistentState(
        ((11, 11), (22, 22)), (first, error), (), 6))
    state = kv.save_persistent()
    clone = oracle(sessions=2, records=2, tombstones=1)
    clone.load_persistent(state)
    assert clone.state_scalars() == kv.state_scalars()
    assert clone.validate() == ()

    with pytest.raises(MeshIrError):
        oracle(sessions=2, records=2).load_persistent(
            dataclasses.replace(state, slot_owners=((11, 11), None)))
    with pytest.raises(MeshIrError):
        oracle(sessions=2, records=2).load_persistent(
            dataclasses.replace(state, slot_owners=(None, (22, 22))))
    with pytest.raises(MeshIrError):
        oracle(sessions=2, records=2).load_persistent(
            dataclasses.replace(state, next_kv_use_epoch=1))
    with pytest.raises(MeshIrError):
        oracle(sessions=2, records=2).load_persistent(
            dataclasses.replace(
                state, records=(dataclasses.replace(first, generation=0),
                                error)))
    with pytest.raises(MeshIrError):
        oracle(sessions=2, records=2).load_persistent(
            dataclasses.replace(state, records=(first,)))
    with pytest.raises(MeshIrError):
        oracle(sessions=2, records=2).load_persistent(
            dataclasses.replace(
                state, records=(first, error, resident_state(session_id=33))))
    with pytest.raises(MeshIrError):
        oracle(sessions=2, records=2, tombstones=1).load_persistent(
            dataclasses.replace(state, tombstones=(((11, 11), 0),)))
    with pytest.raises(MeshIrError):
        oracle(sessions=2, records=2, tombstones=1).load_persistent(
            dataclasses.replace(state, tombstones=(((11, 11), 2),)))


def test_live_state_round_trip_covers_claim_to_pin_and_prestart_abort():
    prior = resident_state(cached=8, view_epoch=4, last_use=5)
    kv = load_record(prior, next_epoch=6)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, cached=8, after_round=8)]))
    handoff = first.handoffs[1]
    live = kv.save_live()
    restored = load_record(prior, next_epoch=6)
    restored.load_live(live)
    assert restored.state_scalars() == kv.state_scalars()
    second = restored.commit_edge(KvEdge(
        tick=2, owner_terminals=[KvOwnerTerminal(
            1, KvStatus.CANCELLED, token=handoff, strict=False)]))
    assert second.rollbacks[1] == KvRollbackKind.RESTORE_PRIOR
    assert restored.find_record(7, 7) == prior


def test_live_state_rejects_owners_without_records():
    kv = oracle()
    live = kv.save_live()
    with pytest.raises(MeshIrError):
        kv.load_live(dataclasses.replace(
            live, pins=(make_pin(1, 11, payload=None,
                                 phase=KvPinPhase.STARTED),)))
    with pytest.raises(MeshIrError):
        kv.load_live(dataclasses.replace(
            live, release_waiters=(KvReleaseWaiter(1, 11, 11, 1),)))
    with pytest.raises(MeshIrError):
        kv.load_live(dataclasses.replace(
            live, appends=(KvAppendObligation(1, 11, 11, 1, 0, 4, 0, 256),)))


def test_legal_waiting_prefix_is_accepted():
    kv = oracle(sessions=1, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    live = kv.save_live()
    assert len(live.waiters) == 1
    clone = oracle(sessions=1, records=2, waiters=2)
    clone.records = dict(kv.records)
    clone.slot_owners = list(kv.slot_owners)
    clone.next_kv_use_epoch = kv.next_kv_use_epoch
    clone.load_live(live)
    assert clone.validate() == ()
    assert clone.state_scalars() == kv.state_scalars()


def test_append_prefix_and_view_freeze():
    kv = oracle()
    first = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    assert first.fatal is None
    kv.commit_edge(KvEdge(tick=2))
    kv.commit_edge(KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 6)]))
    terminal = kv.commit_edge(KvEdge(tick=4, append_terminals=[
        KvAppendTerminal(1, (True, False, True, True, True, True))]))
    record = kv.find_record(11, 11)
    assert terminal.fatal is None
    assert record.state == KvState.ERROR
    assert record.cached_tokens == 1
    assert record.valid_bytes == BYTES_PER_TOKEN
    assert record.diagnostic_prefix_tokens == 1
    assert kv.validate() == ()


def test_runtime_view_freezes_the_arm_window():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2))
    kv.commit_edge(KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 4)]))
    kv.commit_edge(KvEdge(tick=4, append_terminals=[
        KvAppendTerminal(1, (True,) * 4, b"i" * 32)]))
    view = kv.commit_edge(KvEdge(tick=5, view_arms=[KvViewArm(1, 0)]))
    snapshot = view.views[(1, 0)]
    assert snapshot.valid_bytes_at_arm == 4 * BYTES_PER_TOKEN
    assert snapshot.slot_base == 0x100000
    assert snapshot.view_epoch == 2
    kv.commit_edge(KvEdge(tick=6, append_arms=[KvAppendArm(1, 4, 4)]))
    kv.commit_edge(KvEdge(tick=7, append_terminals=[
        KvAppendTerminal(1, (True,) * 4, b"j" * 32)]))
    assert kv.find_record(11, 11).valid_bytes == 8 * BYTES_PER_TOKEN
    assert snapshot.valid_bytes_at_arm == 4 * BYTES_PER_TOKEN


def test_scalar_projection_names_track_the_projection():
    kv = oracle(sessions=1, records=2, waiters=2)
    assert len(kv.scalar_field_names()) == len(kv.state_scalars())
    admitted = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    assert admitted.admissions and admitted.promotions and admitted.handoffs
    assert len(kv.decision_field_names(admitted)) == len(
        kv.decision_scalars(admitted))
    merged = kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    assert merged.admissions and merged.promotions
    assert len(kv.decision_field_names(merged)) == len(
        kv.decision_scalars(merged))
    closed = kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert closed.terminals
    assert len(kv.decision_field_names(closed)) == len(
        kv.decision_scalars(closed))
    assert len(kv.edge_scalars(closed)) == len(
        kv.decision_field_names(closed)) + len(kv.scalar_field_names())


# ------------------------------------------------- R2 review regressions

def test_initial_claim_cancel_leaves_an_error_record_then_releases():
    kv = oracle(sessions=1)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(2, KvStatus.CANCELLED)]))
    record = kv.find_record(22, 22)
    assert record.state == KvState.ERROR
    assert record.slot_id is None
    assert record.cached_tokens == 0
    assert record.valid_bytes == 0
    assert record.view_epoch == 0
    assert record.contract_digest == CONTRACT
    assert kv.claims == {}
    assert kv.validate() == ()
    released = kv.commit_edge(KvEdge(tick=4, releases=[release(9, 22)]))
    assert released.releases[9] == KvReleaseOutcome.SUCCESS
    assert kv.find_record(22, 22) is None
    assert kv.tombstones == {(22, 22): 2}


def test_orphan_allocating_record_is_rejected():
    orphan = dataclasses.replace(
        resident_state(session_id=7, cached=0, view_epoch=1, last_use=3),
        state=KvState.ALLOCATING, content_digest=None)
    assert orphan.pin_count == 0 and orphan.admission_claim_count == 0
    with pytest.raises(MeshIrError):
        load_record(orphan, sessions=1, next_epoch=4)


def test_tail_waiter_does_not_block_the_canonical_head():
    prior = resident_state(cached=0, slot_id=0, last_use=4)
    prior.content_digest = None
    kv = load_record(prior, sessions=1, next_epoch=5)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 22, qos=255),
        admit(2, 7, flags=REQUIRE_KV_REUSE)]))
    assert first.promotions[1] == KvPromotionOutcome.WAITING_SLOT
    assert len(first.evictions_started) == 1
    assert 2 not in kv.claims
    assert kv.find_record(22, 22).slot_id is None
    second = kv.commit_edge(KvEdge(tick=2))
    assert second.promotions[1] == KvPromotionOutcome.PINNED
    assert kv.find_record(22, 22).slot_id == 0
    assert kv.find_record(7, 7).state == KvState.EVICTED
    assert kv.validate() == ()


def test_rollback_authority_expires_at_the_first_prefill_start():
    kv = oracle()
    handoff = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 11, after_round=1)])).handoffs[1]
    started = kv.commit_edge(KvEdge(tick=2, core_starts=[1]))
    assert started.fatal is None
    assert kv.pins[1].phase == KvPinPhase.STARTED
    assert kv.pins[1].payload is None
    assert kv.pins[1].phase == KvPinPhase.STARTED
    assert kv.pins[1].payload is None
    kv.commit_edge(KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 1)]))
    kv.commit_edge(KvEdge(tick=4, append_terminals=[
        KvAppendTerminal(1, (True,), b"x" * 32)]))
    late = kv.commit_edge(KvEdge(tick=5, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.ERROR, handoff)]))
    assert late.fatal == "rollback authority is no longer valid"
    assert late.rollbacks == {}
    record = kv.find_record(11, 11)
    assert record.cached_tokens == 1
    assert record.slot_id == 0


def test_core_start_requires_live_rollback_authority():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, core_starts=[1]))
    again = kv.commit_edge(KvEdge(tick=3, core_starts=[1]))
    assert again.fatal == "core start without rollback authority"


def test_live_restore_rejects_tampered_evidence():
    kv = oracle(sessions=1, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    live = kv.save_live()
    assert live.pins[0].payload is not None
    assert len(live.waiters) == 1
    mutations = {
        "missing_pin": dataclasses.replace(live, pins=()),
        "wrong_pin_generation": dataclasses.replace(
            live, pins=(dataclasses.replace(live.pins[0], generation=99),)),
        "wrong_pin_count": dataclasses.replace(
            live, records=(dataclasses.replace(live.records[0],
                                               pin_count=99),)),
        "invalid_state_enum": dataclasses.replace(
            live, records=(dataclasses.replace(live.records[0], state=99),)),
        "missing_payload": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=None),)),
        "wrong_payload_serial": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=dataclasses.replace(
                    live.pins[0].payload, serial=99)),)),
        "missing_waiter": dataclasses.replace(live, waiters=()),
        "dropped_record": dataclasses.replace(live, records=()),
    }
    for name, state in mutations.items():
        target = oracle()
        with pytest.raises(MeshIrError):
            target.load_live(state)
        assert target.records == {}, name
        assert target.pins == {}, name


def test_live_restore_keeps_tombstones_and_stale_generation():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    kv.commit_edge(KvEdge(tick=3, releases=[release(9, 11)]))
    assert kv.tombstone_count() == 1
    restored = oracle()
    restored.load_live(kv.save_live())
    assert restored.tombstone_count() == 1
    stale = restored.commit_edge(KvEdge(tick=4, releases=[release(10, 11)]))
    assert stale.releases[10] == KvReleaseOutcome.STALE_GENERATION
    assert restored.commit_edge(
        KvEdge(tick=5, admissions=[admit(1, 11)])).admissions[1] == \
        KvAdmissionOutcome.E_SESSION_EXISTS


def test_first_error_uses_the_typed_numeric_key():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, faults=[
        KvFaultEvent(1, candidate(2, ordinal=1)),
        KvFaultEvent(1, candidate(2, ordinal=256))]))
    assert kv.find_record(11, 11).first_error.source.ordinal == 1
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, faults=[
        KvFaultEvent(1, candidate(2, ordinal=256)),
        KvFaultEvent(1, candidate(2, ordinal=1))]))
    assert kv.find_record(11, 11).first_error.source.ordinal == 1


def test_duplicate_owner_terminal_is_fatal():
    for order in ((KvStatus.SUCCESS, KvStatus.CANCELLED),
                  (KvStatus.CANCELLED, KvStatus.SUCCESS)):
        kv = oracle()
        kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
        result = kv.commit_edge(KvEdge(
            tick=2, owner_terminals=[KvOwnerTerminal(1, status)
                                     for status in order]))
        assert result.fatal == "duplicate KV owner terminal"
        assert result.terminals == {}


def test_release_completion_follows_the_tuple_key():
    kv = oracle()
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    kv.commit_edge(KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS),
        KvOwnerTerminal(2, KvStatus.SUCCESS)]))
    result = kv.commit_edge(KvEdge(tick=4, releases=[
        release(20, 11), release(10, 22)]))
    assert sorted(result.releases) == [20]
    assert kv.find_record(11, 11) is None
    assert kv.find_record(22, 22) is not None


def test_geometry_keeps_floor_tokens_and_rejects_wrap():
    good = KvGeometry(region_base=0x100000, slot_bytes=4096,
                      slot_alignment=4096, max_sessions=1,
                      bytes_per_token=192)
    assert good.gaps() == ()
    assert good.tokens_per_slot == 21
    wrap = KvGeometry(region_base=(1 << 64) - 4096, slot_bytes=4096,
                      slot_alignment=4096, max_sessions=2,
                      bytes_per_token=64)
    assert any("kv_region_end overflow" in gap for gap in wrap.gaps())
    span = KvGeometry(region_base=0, slot_bytes=(1 << 63) + 4096,
                      slot_alignment=4096, max_sessions=2,
                      bytes_per_token=64)
    assert any("overflow" in gap for gap in span.gaps())


def test_waiting_entry_commits_only_as_the_canonical_head():
    kv = oracle(sessions=1, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    queued = kv.commit_edge(KvEdge(tick=2, admissions=[
        admit(3, 33, qos=9), admit(2, 22, qos=0)]))
    assert queued.admissions[3] == KvAdmissionOutcome.CLAIMED
    assert queued.admissions[2] == KvAdmissionOutcome.WAITING
    assert 3 in kv.claims and 2 not in kv.claims
    assert kv.find_record(33, 33) is not None
    assert kv.find_record(22, 22) is None
    promoted = kv.commit_edge(KvEdge(tick=3))
    assert promoted.promotions[3] == KvPromotionOutcome.WAITING_SLOT
    assert 3 in kv.claims and 2 not in kv.claims
    assert len(kv.waiters) == 2


def test_same_tuple_waiters_resolve_by_canonical_order():
    kv = oracle(sessions=2, records=2, waiters=2)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(2, 11, qos=0), admit(1, 11, qos=9)]))
    assert first.admissions[1] == KvAdmissionOutcome.CLAIMED
    assert first.admissions[2] == KvAdmissionOutcome.WAITING
    assert sorted(kv.pins) == [1]
    assert kv.find_record(11, 11) is not None
    second = kv.commit_edge(KvEdge(tick=2))
    assert second.admissions[2] == KvAdmissionOutcome.E_SESSION_EXISTS
    assert sorted(kv.pins) == [1]
    assert kv.validate() == ()


# ------------------------------------------------- F1-F6 review regressions

def test_admission_identity_is_frozen_per_request():
    kv = oracle(sessions=2, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    for intent in (admit(1, 22), admit(1, 11, generation=2),
                   admit(1, 11, contract=OTHER_CONTRACT)):
        victim = oracle(sessions=2, records=2, waiters=2)
        victim.records = dict(kv.records)
        victim.slot_owners = list(kv.slot_owners)
        victim.pins = dict(kv.pins)
        victim.next_kv_use_epoch = kv.next_kv_use_epoch
        victim.next_rollback_serial = kv.next_rollback_serial
        result = victim.commit_edge(KvEdge(tick=2, admissions=[intent]))
        assert result.fatal == "duplicate KV acquire for a pinned request"
        assert sorted(victim.records) == [(11, 11)]
        assert sorted(victim.pins) == [1]
        assert victim.next_kv_use_epoch == kv.next_kv_use_epoch
        assert victim.next_rollback_serial == kv.next_rollback_serial


def test_same_edge_duplicate_admission_and_live_release_identity():
    kv = oracle(sessions=2, records=2, waiters=2)
    duplicated = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 11), admit(1, 22)]))
    assert duplicated.fatal == "duplicate KV command identity in one edge"
    assert kv.records == {} and kv.pins == {} and kv.waiters == {}

    kv = oracle(sessions=2, records=2, waiters=2)
    released = kv.commit_edge(KvEdge(tick=2, admissions=[admit(1, 11)]))
    assert released.fatal is None
    pending = kv.commit_edge(KvEdge(tick=3, releases=[release(9, 11)]))
    assert pending.releases == {}
    assert 9 in kv.release_waiters
    clash = kv.commit_edge(KvEdge(tick=4, admissions=[admit(9, 33)]))
    assert clash.fatal == "request id is a live release waiter"


def test_waiter_retry_keeps_identity_and_age():
    kv = oracle(sessions=1, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    first = kv.commit_edge(KvEdge(tick=2, admissions=[
        admit(2, 22, qos=4, ready_tick=2)]))
    assert first.admissions[2] == KvAdmissionOutcome.CLAIMED
    assert first.promotions[2] == KvPromotionOutcome.WAITING_SLOT
    epoch = kv.next_kv_use_epoch
    serial = kv.next_rollback_serial
    retry = kv.commit_edge(KvEdge(tick=3, admissions=[
        admit(2, 22, qos=4, ready_tick=99)]))
    assert retry.admissions[2] == KvAdmissionOutcome.CLAIMED
    assert kv.waiters[2].ready_tick == 2
    assert len(kv.waiters) == 1
    assert kv.next_kv_use_epoch == epoch
    assert kv.next_rollback_serial == serial
    for changed in (admit(2, 22, qos=5, ready_tick=2),
                    admit(2, 22, deadline=7, ready_tick=2),
                    admit(2, 33, ready_tick=2),
                    admit(2, 22, flags=ALLOW_REPREFILL, ready_tick=2)):
        victim = oracle(sessions=1, records=2, waiters=2)
        victim.records = dict(kv.records)
        victim.slot_owners = list(kv.slot_owners)
        victim.waiters = dict(kv.waiters)
        victim.pins = dict(kv.pins)
        victim.next_kv_use_epoch = kv.next_kv_use_epoch
        victim.next_rollback_serial = kv.next_rollback_serial
        result = victim.commit_edge(KvEdge(tick=4, admissions=[changed]))
        assert result.fatal is not None
        assert victim.waiters[2].ready_tick == 2


def test_pin_bound_allows_two_prestart_pins_from_one_waiter_slot():
    kv = oracle(sessions=2, records=2, waiters=1)
    first = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    assert first.promotions[1] == KvPromotionOutcome.PINNED
    assert kv.waiters == {}
    second = kv.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    assert second.fatal is None
    assert second.promotions[2] == KvPromotionOutcome.PINNED
    assert kv.waiters == {}
    assert sorted(kv.pins) == [1, 2]
    assert all(pin.phase == KvPinPhase.PRESTART for pin in kv.pins.values())
    assert all(pin.payload is not None for pin in kv.pins.values())
    assert kv.pin_bound() == 2
    assert kv.validate() == ()


def test_live_restore_rejects_incomplete_raw_structure():
    kv = oracle(sessions=2, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    prior = evicted_state(session_id=7, view_epoch=7, last_use=3)
    kv.commit_edge(KvEdge(tick=2))
    reprefill = load_record(prior, sessions=1, next_epoch=4)
    pinned = reprefill.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL)]))
    assert pinned.promotions[1] == KvPromotionOutcome.PINNED
    live = reprefill.save_live()
    payload = live.pins[0].payload
    assert payload is not None and payload.prior_absent is False
    waiting = oracle(sessions=1, records=2, waiters=2)
    waiting.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    waiting.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    assert len(waiting.waiters) == 1
    assert len(waiting.claims) == 1
    mutations = {
        "empty_bitmap": dataclasses.replace(live, slot_owners=()),
        "long_bitmap": dataclasses.replace(
            live, slot_owners=live.slot_owners + (None,)),
        "bad_slot_owner": dataclasses.replace(live, slot_owners=("x",)),
        "duplicate_record": dataclasses.replace(
            live, records=live.records + (live.records[0].copy(),)),
        "duplicate_pin": dataclasses.replace(live, pins=live.pins * 2),
        "duplicate_waiter": dataclasses.replace(
            waiting.save_live(),
            waiters=waiting.save_live().waiters * 2),
        "duplicate_tombstone": dataclasses.replace(
            live, tombstones=(((11, 11), 2), ((11, 11), 3))),
        "prior_generation": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=dataclasses.replace(
                    payload, prior=dataclasses.replace(
                        payload.prior, generation=99))),)),
        "prior_contract": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=dataclasses.replace(
                    payload, prior=dataclasses.replace(
                        payload.prior,
                        contract_digest=OTHER_CONTRACT))),)),
        "prior_slot_state": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=dataclasses.replace(
                    payload, prior=dataclasses.replace(
                        payload.prior, state=KvState.RESIDENT, slot_id=1,
                        cached_tokens=1,
                        valid_bytes=BYTES_PER_TOKEN))),)),
        "prior_enum": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=dataclasses.replace(
                    payload, prior=dataclasses.replace(
                        payload.prior, state=99))),)),
        "path_enum": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=dataclasses.replace(
                    payload, intent=99)),)),
        "wrong_prior_absent": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=dataclasses.replace(
                    payload, prior_absent=True)),)),
        "dropped_payload": dataclasses.replace(
            live, pins=(dataclasses.replace(live.pins[0], payload=None),)),
        "started_with_payload": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], phase=KvPinPhase.STARTED),)),
        "bad_phase": dataclasses.replace(
            live, pins=(dataclasses.replace(live.pins[0], phase=99),)),
        "wrong_serial": dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], payload=dataclasses.replace(
                    payload, serial=99)),)),
        "zero_next_serial": dataclasses.replace(
            live, next_rollback_serial=0),
        "zero_next_epoch": dataclasses.replace(live, next_kv_use_epoch=0),
    }
    for name, state in mutations.items():
        target = oracle(sessions=1, records=2, waiters=2)
        target.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
        before = (target.state_scalars(), sorted(target.records),
                  sorted(target.pins), target.validate())
        with pytest.raises(MeshIrError):
            target.load_live(state)
        after = (target.state_scalars(), sorted(target.records),
                 sorted(target.pins), target.validate())
        assert before == after, name
        fresh = oracle(sessions=1, records=2, waiters=2)
        with pytest.raises(MeshIrError):
            fresh.load_live(state)
        assert fresh.records == {} and fresh.pins == {}


def test_live_restore_accepts_every_legal_pin_phase():
    cases = []
    initial = oracle(sessions=2, records=2, waiters=2)
    initial.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    cases.append(initial)
    evicted = load_record(evicted_state(session_id=7, view_epoch=7,
                                        last_use=3), sessions=1, next_epoch=4)
    evicted.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL)]))
    cases.append(evicted)
    resident = load_record(resident_state(cached=8), sessions=1, next_epoch=4)
    resident.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, cached=8, after_round=8)]))
    cases.append(resident)
    started = oracle(sessions=2, records=2, waiters=2)
    started.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    started.commit_edge(KvEdge(tick=2, core_starts=[1]))
    cases.append(started)
    for source in cases:
        live = source.save_live()
        assert source.validate() == ()
        clone = KvOracle(source.geometry, source.capacity)
        clone.load_live(live)
        assert clone.state_scalars() == source.state_scalars()
        assert clone.validate() == ()
        key = live.records[0].tuple
        mutated = dataclasses.replace(
            live, records=tuple(record.copy() for record in live.records))
        clone.records[key] = dataclasses.replace(
            clone.records[key], last_use_epoch=0)
        assert source.records[key].last_use_epoch == \
            live.records[0].last_use_epoch
        assert mutated.records[0] is not live.records[0]


# ------------------------------------------- A-D second review regressions

def _reprefill_live_state():
    prior = evicted_state(session_id=7, view_epoch=7, last_use=3)
    kv = load_record(prior, sessions=1, next_epoch=4)
    kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL)]))
    return kv


def _reuse_live_state():
    prior = resident_state(session_id=7, cached=8, view_epoch=3, last_use=2)
    kv = load_record(prior, sessions=2, next_epoch=3)
    kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, cached=8, after_round=8)]))
    return kv


def test_restore_rejects_prior_and_owner_relation_violations():
    reprefill = _reprefill_live_state()
    reuse = _reuse_live_state()
    cases = []
    initial = oracle(sessions=2, records=2, waiters=2)
    initial.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    live = initial.save_live()
    cases.append(("initial_prior_present", dataclasses.replace(
        live, pins=(dataclasses.replace(
            live.pins[0], payload=dataclasses.replace(
                live.pins[0].payload, prior_absent=False)),))))
    cases.append(("initial_prior_bad_diagnostic", dataclasses.replace(
        live, pins=(dataclasses.replace(
            live.pins[0], payload=dataclasses.replace(
                live.pins[0].payload, prior=dataclasses.replace(
                    live.pins[0].payload.prior,
                    diagnostic_prefix_bytes=1))),))))
    cases.append(("initial_prior_bad_content", dataclasses.replace(
        live, pins=(dataclasses.replace(
            live.pins[0], payload=dataclasses.replace(
                live.pins[0].payload, prior=dataclasses.replace(
                    live.pins[0].payload.prior,
                    content_digest=b"c" * 32))),))))
    reuse_live = reuse.save_live()
    other_slot = dataclasses.replace(
        reuse_live, slot_owners=((7, 7), (8, 8)))
    other_slot = dataclasses.replace(
        other_slot, pins=(dataclasses.replace(
            other_slot.pins[0], payload=dataclasses.replace(
                other_slot.pins[0].payload, prior=dataclasses.replace(
                    other_slot.pins[0].payload.prior, slot_id=1))),))
    cases.append(("reuse_prior_other_slot", other_slot))
    cases.append(("reuse_prior_invalid_bytes", dataclasses.replace(
        reuse_live, pins=(dataclasses.replace(
            reuse_live.pins[0], payload=dataclasses.replace(
                reuse_live.pins[0].payload, prior=dataclasses.replace(
                    reuse_live.pins[0].payload.prior, cached_tokens=0,
                    valid_bytes=64))),))))
    serial = reuse_live.pins[0].payload.serial
    duplicate = dataclasses.replace(
        reuse_live, pins=reuse_live.pins + (dataclasses.replace(
            reuse_live.pins[0], request_id=9, session_id=7, kv_handle=7,
            payload=dataclasses.replace(
                reuse_live.pins[0].payload,
                prior=reuse_live.pins[0].payload.prior.copy())),))
    cases.append(("duplicate_live_serial", duplicate))
    claimed = oracle(sessions=1, records=2, waiters=2)
    claimed.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    claimed.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    claim_live = claimed.save_live()
    assert claim_live.waiters and claim_live.waiters[0].claimed
    entry = claim_live.waiters[0]
    tampered = dataclasses.replace(
        entry, generation=99,
        prior=dataclasses.replace(entry.prior, generation=99))
    cases.append(("claim_wrong_record_generation", dataclasses.replace(
        claim_live, waiters=(tampered,))))
    mismatched = dataclasses.replace(entry, contract_digest=OTHER_CONTRACT,
                                     prior=dataclasses.replace(
                                         entry.prior,
                                         contract_digest=OTHER_CONTRACT))
    cases.append(("claim_wrong_record_contract", dataclasses.replace(
        claim_live, waiters=(mismatched,))))
    cases.append(("waiter_request_zero", dataclasses.replace(
        claim_live, waiters=(dataclasses.replace(entry, request_id=0),))))
    cases.append(("waiter_qos_256", dataclasses.replace(
        claim_live, waiters=(dataclasses.replace(entry, qos=256),))))
    cases.append(("waiter_entry_none", dataclasses.replace(
        claim_live, waiters=(None,))))
    cases.append(("pin_payload_string", dataclasses.replace(
        live, pins=(dataclasses.replace(live.pins[0], payload="x"),))))
    cases.append(("pin_prior_string", dataclasses.replace(
        live, pins=(dataclasses.replace(
            live.pins[0], payload=dataclasses.replace(
                live.pins[0].payload, prior="x")),))))
    cases.append(("waiter_bool_generation", dataclasses.replace(
        claim_live, waiters=(dataclasses.replace(entry, generation=True),))))
    for name, state in cases:
        target = oracle(sessions=2, records=2, waiters=2)
        target.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
        before = target.save_live()
        with pytest.raises(MeshIrError):
            target.load_live(state)
        assert target.save_live() == before, name
        fresh = oracle(sessions=2, records=2, waiters=2)
        with pytest.raises(MeshIrError):
            fresh.load_live(state)
        assert fresh.records == {} and fresh.pins == {}, name


def test_restore_accepts_multi_claim_and_continues():
    kv = oracle(sessions=1, records=3, waiters=3)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    for tick, request_id, session_id, qos in (
            (2, 2, 22, 0), (3, 3, 33, 255)):
        result = kv.commit_edge(KvEdge(tick=tick, admissions=[
            admit(request_id, session_id, qos=qos)]))
        assert result.fatal is None
        assert result.promotions[request_id] == \
            KvPromotionOutcome.WAITING_SLOT
    assert sorted(kv.claims) == [2, 3]
    assert kv.validate() == ()
    live = kv.save_live()
    assert len([entry for entry in live.waiters if entry.claimed]) == 2
    clone = KvOracle(kv.geometry, kv.capacity)
    clone.load_live(live)
    assert clone.validate() == ()
    assert clone.state_scalars() == kv.state_scalars()
    first = kv.commit_edge(KvEdge(tick=4, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    second = clone.commit_edge(KvEdge(tick=4, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)]))
    assert first.fatal is None and second.fatal is None
    assert kv.edge_scalars(first) == clone.edge_scalars(second)
    assert kv.validate() == () and clone.validate() == ()


def test_head_transition_is_atomic_on_counter_overflow():
    for name, field in (("overflow_next_kv_use_epoch", "next_kv_use_epoch"),
                        ("overflow_next_rollback_serial",
                         "next_rollback_serial")):
        kv = oracle(sessions=2, records=2, waiters=2)
        kv.next_kv_use_epoch = 1
        kv.next_rollback_serial = 1
        setattr(kv, field, 0xFFFFFFFFFFFFFFFF)
        before = kv.save_live()
        result = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
        assert result.fatal is not None, name
        assert kv.record_count() == 0, name
        assert kv.claims == {}, name
        assert all(owner is None for owner in kv.slot_owners), name
        assert kv.pins == {}, name
        assert not result.handoffs and not result.promotions, name
        after = kv.save_live()
        assert after.records == before.records, name
        assert after.slot_owners == before.slot_owners, name
    kv = oracle(sessions=2, records=2, waiters=2)
    kv.next_kv_use_epoch = 0xFFFFFFFFFFFFFFFF - 1
    kv.next_rollback_serial = 0xFFFFFFFFFFFFFFFF - 1
    boundary = kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    assert boundary.fatal is None
    assert boundary.promotions[1] == KvPromotionOutcome.PINNED
    assert kv.next_kv_use_epoch == 0xFFFFFFFFFFFFFFFF
    assert kv.next_rollback_serial == 0xFFFFFFFFFFFFFFFF


def test_release_and_rollback_token_identity():
    kv = oracle(sessions=2, records=2, waiters=2)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    clash = kv.commit_edge(KvEdge(tick=2, releases=[release(1, 11)]))
    assert clash.fatal is not None
    assert kv.pins and 1 in kv.pins
    assert kv.release_waiters == {}

    waiter = oracle(sessions=1, records=2, waiters=2)
    waiter.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    waiter.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    assert 2 in waiter.waiters
    clash = waiter.commit_edge(KvEdge(tick=3, releases=[release(2, 22)]))
    assert clash.fatal is not None

    pending = oracle(sessions=2, records=2, waiters=2)
    pending.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    pending.commit_edge(KvEdge(tick=2, releases=[release(9, 11)]))
    assert 9 in pending.release_waiters
    clash = pending.commit_edge(KvEdge(tick=3, releases=[release(9, 11)]))
    assert clash.fatal is not None

    same_edge = oracle(sessions=2, records=2, waiters=2)
    clash = same_edge.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)],
                                         releases=[release(1, 22)]))
    assert clash.fatal is not None
    assert same_edge.records == {} and same_edge.pins == {}

    token = oracle(sessions=2, records=2, waiters=2)
    handoff = token.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 11)])).handoffs[1]
    wrong = KvRollbackToken(99, handoff.serial)
    result = token.commit_edge(KvEdge(tick=2, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.ERROR, wrong)]))
    assert result.fatal is not None
    assert result.rollbacks == {} and result.terminals == {}
    assert token.find_record(11, 11).slot_id == 0
    assert 1 in token.pins
    valid = oracle(sessions=2, records=2, waiters=2)
    valid_handoff = valid.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 11)])).handoffs[1]
    good = valid.commit_edge(KvEdge(tick=2, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.ERROR, valid_handoff)]))
    assert good.fatal is None
    assert good.rollbacks[1] == KvRollbackKind.INITIAL_ERROR_RECORD


def test_restore_derives_waiter_kind_from_frozen_flags():
    kv = oracle(sessions=2, records=2, waiters=3)
    kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 11), admit(2, 22, flags=REQUIRE_KV_REUSE)]))
    live = kv.save_live()
    assert live.waiters and not live.waiters[0].claimed
    baseline = KvOracle(kv.geometry, kv.capacity)
    baseline.load_live(live)
    result = baseline.commit_edge(KvEdge(tick=2))
    assert result.fatal is None
    assert result.admissions[2] == KvAdmissionOutcome.E_KV_SESSION_NOT_FOUND
    assert baseline.record_count() == 1 and sorted(baseline.pins) == [1]

    target = oracle(sessions=2, records=2, waiters=3)
    target.commit_edge(KvEdge(tick=1, admissions=[admit(90, 90)]))
    before = target.save_live()
    marked = dataclasses.replace(
        live, waiters=(dataclasses.replace(live.waiters[0], initial=True),))
    with pytest.raises(MeshIrError) as error:
        target.load_live(marked)
    assert error.value.code == "E_KV_STATE"
    assert target.save_live() == before

    blank = oracle(sessions=1, records=1, waiters=3)
    blank.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    blank.commit_edge(KvEdge(tick=2, admissions=[admit(2, 22)]))
    plain = blank.save_live()
    assert plain.waiters and not plain.waiters[0].claimed
    assert plain.waiters[0].initial
    dropped = dataclasses.replace(
        plain, waiters=(dataclasses.replace(plain.waiters[0], initial=False),))
    with pytest.raises(MeshIrError) as error:
        blank.load_live(dropped)
    assert error.value.code == "E_KV_STATE"


def test_restore_rejects_prior_epoch_beyond_issued():
    kv = load_record(evicted_state(), sessions=1, next_epoch=4)
    token = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL)])).handoffs[1]
    live = kv.save_live()
    assert live.pins[0].payload.prior.last_use_epoch == 3
    assert kv.next_kv_use_epoch == 5
    positive = KvOracle(kv.geometry, kv.capacity)
    positive.load_live(live)
    rolled = positive.commit_edge(KvEdge(tick=2, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.CANCELLED, token)]))
    assert rolled.fatal is None
    assert positive.find_record(7, 7).last_use_epoch == 3

    future = dataclasses.replace(
        live, pins=(dataclasses.replace(
            live.pins[0], payload=dataclasses.replace(
                live.pins[0].payload, prior=dataclasses.replace(
                    live.pins[0].payload.prior, last_use_epoch=99))),))
    target = oracle(sessions=1, records=2, waiters=2)
    target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
    before = target.save_live()
    with pytest.raises(MeshIrError) as error:
        target.load_live(future)
    assert error.value.code == "E_KV_STATE"
    assert target.save_live() == before

    claim = oracle(sessions=1, records=3, waiters=3)
    claim.load_persistent(KvPersistentState(
        (None,), (evicted_state(session_id=7, last_use=3),), (), 4))
    claim.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    claim.commit_edge(KvEdge(tick=2, admissions=[
        admit(2, 7, flags=ALLOW_REPREFILL)]))
    assert claim.waiters[2].claimed
    claim_live = claim.save_live()
    assert [entry.prior.last_use_epoch for entry in claim_live.waiters
            if entry.claimed] == [3]
    claim_clone = KvOracle(claim.geometry, claim.capacity)
    claim_clone.load_live(claim_live)
    claim_bad = dataclasses.replace(claim_live, waiters=tuple(
        dataclasses.replace(entry, prior=dataclasses.replace(
            entry.prior, last_use_epoch=99)) if entry.claimed else entry
        for entry in claim_live.waiters))
    with pytest.raises(MeshIrError) as error:
        claim_clone.load_live(claim_bad)
    assert error.value.code == "E_KV_STATE"
    assert claim_clone.save_live() == claim_live


def test_restore_enforces_tombstone_capacity():
    kv = oracle(sessions=2, records=2, waiters=2, tombstones=2)
    live = kv.save_live()
    exact = dataclasses.replace(
        live, tombstones=(((1, 1), 2), ((2, 2), 2)))
    exact_clone = KvOracle(kv.geometry, kv.capacity)
    exact_clone.load_live(exact)
    assert exact_clone.tombstone_count() == 2
    over = dataclasses.replace(
        live, tombstones=(((1, 1), 2), ((2, 2), 2), ((3, 3), 2)))
    target = oracle(sessions=2, records=2, waiters=2, tombstones=2)
    target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
    before = target.save_live()
    with pytest.raises(MeshIrError) as error:
        target.load_live(over)
    assert error.value.code == "E_KV_STATE"
    assert target.save_live() == before
    with pytest.raises(MeshIrError) as error:
        KvOracle(kv.geometry, kv.capacity).load_persistent(
            KvPersistentState((None, None), (), (((1, 1), 2), ((2, 2), 2),
                                                 ((3, 3), 2)), 1))
    assert error.value.code == "E_KV_STATE"


def test_restore_rejects_ill_typed_and_out_of_range_fields():
    kv = load_record(resident_state(), sessions=1, next_epoch=4)
    kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=REQUIRE_KV_REUSE, cached=8, after_round=8)]))
    live = kv.save_live()
    cases = [(field + "_none", dataclasses.replace(live, **{field: None}))
             for field in ("records", "slot_owners", "waiters", "pins",
                           "release_waiters", "appends", "evicting",
                           "tombstones")]
    cases.append(("next_serial_none_with_live_pin", dataclasses.replace(
        live, next_rollback_serial=None)))
    cases.append(("next_serial_string_with_live_pin", dataclasses.replace(
        live, next_rollback_serial="7")))
    cases.append(("next_epoch_u64_overflow", dataclasses.replace(
        live, next_kv_use_epoch=0x10000000000000000)))
    cases.append(("next_serial_u64_overflow", dataclasses.replace(
        live, next_rollback_serial=0x10000000000000000)))
    cases.append(("next_epoch_bool", dataclasses.replace(
        live, next_kv_use_epoch=True)))
    cases.append(("record_state_bool", dataclasses.replace(
        live, records=(dataclasses.replace(live.records[0], state=True),))))
    cases.append(("record_cached_u32_overflow", dataclasses.replace(
        live, records=(dataclasses.replace(
            live.records[0], cached_tokens=0x100000000,
            valid_bytes=0),))))
    cases.append(("record_epoch_u64_overflow", dataclasses.replace(
        live, records=(dataclasses.replace(
            live.records[0], last_use_epoch=0x10000000000000000),))))
    cases.append(("record_state_string", dataclasses.replace(
        live, records=(dataclasses.replace(live.records[0], state="1"),))))
    pin = live.pins[0]
    cases.append(("pin_required_cached_u32_overflow", dataclasses.replace(
        live, pins=(dataclasses.replace(
            pin, required_cached_tokens=0x100000000),))))
    cases.append(("pin_after_round_u32_overflow", dataclasses.replace(
        live, pins=(dataclasses.replace(
            pin, required_tokens_after_round=0x100000000),))))
    cases.append(("pin_phase_bool", dataclasses.replace(
        live, pins=(dataclasses.replace(pin, phase=True),))))
    cases.append(("pin_payload_intent_bool", dataclasses.replace(
        live, pins=(dataclasses.replace(
            pin, payload=dataclasses.replace(pin.payload, intent=True)),))))
    cases.append(("pin_serial_u64_overflow", dataclasses.replace(
        live, pins=(dataclasses.replace(
            pin, payload=dataclasses.replace(
                pin.payload,
                serial=0x10000000000000000)),))))
    cases.append(("pin_flags_bool", dataclasses.replace(
        live, pins=(dataclasses.replace(pin, flags=True),))))
    for name, state in cases:
        target = oracle(sessions=1, records=2, waiters=2)
        target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
        before = target.save_live()
        with pytest.raises(MeshIrError) as error:
            target.load_live(state)
        assert error.value.code == "E_KV_STATE", name
        assert target.save_live() == before, name

    wide = dataclasses.replace(
        live, next_kv_use_epoch=0xFFFFFFFFFFFFFFFE,
        next_rollback_serial=0xFFFFFFFFFFFFFFFE)
    KvOracle(kv.geometry, kv.capacity).load_live(wide)


def test_restore_uses_frozen_reuse_requirement():
    kv = load_record(resident_state(cached=8), sessions=2, next_epoch=4)
    live_token = kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=REQUIRE_KV_REUSE, cached=8,
              after_round=8)])).handoffs[1]
    live = kv.save_live()
    assert live.pins[0].payload.intent == KvPath.KV_REUSE
    assert live.pins[0].required_cached_tokens == 8
    clone = KvOracle(kv.geometry, kv.capacity)
    clone.load_live(live)
    assert clone.validate() == ()
    accepted = clone.commit_edge(KvEdge(tick=2, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.CANCELLED, live_token)]))
    assert accepted.fatal is None

    blank_prior = dataclasses.replace(
        live.pins[0].payload.prior, state=KvState.ALLOCATING, slot_id=None,
        cached_tokens=0, valid_bytes=0, content_digest=None, view_epoch=0,
        last_use_epoch=0)
    flipped = dataclasses.replace(live, pins=(dataclasses.replace(
        live.pins[0], payload=dataclasses.replace(
            live.pins[0].payload, intent=KvPath.INITIAL_PREFILL,
            prior_absent=True, prior=blank_prior)),))
    with pytest.raises(MeshIrError) as error:
        clone.load_live(flipped)
    assert error.value.code == "E_KV_STATE"
    assert "frozen flags" in str(error.value)

    for name, value in (("reuse_wrong_required_cached", 7),
                        ("reuse_required_cached_u32_overflow",
                         0x100000000)):
        tampered = dataclasses.replace(
            live, pins=(dataclasses.replace(
                live.pins[0], required_cached_tokens=value),))
        target = oracle(sessions=2, records=2, waiters=2)
        target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
        before = target.save_live()
        with pytest.raises(MeshIrError) as error:
            target.load_live(tampered)
        assert error.value.code == "E_KV_STATE", name
        assert target.save_live() == before, name

    claim = oracle(sessions=3, records=4, waiters=4)
    claim.load_persistent(KvPersistentState(
        (None, None, None), (evicted_state(session_id=7, last_use=3),), (),
        4))
    for tick, (request_id, session_id) in enumerate(
            ((1, 11), (2, 22), (3, 33)), 1):
        claim.commit_edge(KvEdge(tick=tick, admissions=[
            admit(request_id, session_id)]))
    claim.commit_edge(KvEdge(tick=4, admissions=[
        admit(4, 7, flags=ALLOW_REPREFILL)]))
    claim_live = claim.save_live()
    claimed = [entry for entry in claim_live.waiters if entry.claimed][0]
    resident_prior = dataclasses.replace(
        claimed.prior, state=KvState.RESIDENT, slot_id=2, view_epoch=1,
        cached_tokens=8, valid_bytes=8 * 64, content_digest=b"c" * 32)
    resident = dataclasses.replace(resident_prior, admission_claim_count=1)
    reuse_claim = dataclasses.replace(
        claim_live, slot_owners=((11, 11), (22, 22), (7, 7)),
        records=tuple(
            resident if record.tuple == claimed.tuple else record
            for record in claim_live.records
            if record.tuple != (33, 33)),
        pins=tuple(pin for pin in claim_live.pins if pin.request_id != 3),
        waiters=tuple(
            dataclasses.replace(
                entry, intent=KvPath.KV_REUSE, prior_absent=False,
                prior=resident_prior, required_cached_tokens=8)
            if entry.claimed else entry for entry in claim_live.waiters))
    strict = KvOracle(claim.geometry, claim.capacity)
    strict.load_live(reuse_claim)
    assert strict.validate() == ()
    loose = dataclasses.replace(reuse_claim, waiters=tuple(
        dataclasses.replace(entry, required_cached_tokens=7)
        if entry.claimed else entry for entry in reuse_claim.waiters))
    with pytest.raises(MeshIrError) as error:
        strict.load_live(loose)
    assert error.value.code == "E_KV_STATE"
    assert strict.validate() == ()


def _raw_source(source, **overrides):
    raw = object.__new__(ErrorSourceKey)
    for field in dataclasses.fields(source):
        value = overrides.get(field.name, getattr(source, field.name))
        object.__setattr__(raw, field.name, value)
    return raw


def _reprefill_pin_state():
    kv = load_record(evicted_state(), sessions=2, next_epoch=4)
    kv.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL)]))
    live = kv.save_live()
    assert live.pins[0].payload.intent == KvPath.REPREFILL
    return live


def test_restore_binds_frozen_flags_to_the_decided_path():
    pin_live = _reprefill_pin_state()
    for flags in (REQUIRE_KV_REUSE, ALLOW_REPREFILL | REQUIRE_KV_REUSE):
        tampered = dataclasses.replace(
            pin_live, pins=(dataclasses.replace(pin_live.pins[0],
                                                flags=flags),))
        target = oracle(sessions=2, records=2, waiters=2)
        target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
        before = target.save_live()
        with pytest.raises(MeshIrError) as error:
            target.load_live(tampered)
        assert error.value.code == "E_KV_STATE", flags
        assert target.save_live() == before, flags

    kv = load_record(evicted_state(), sessions=1, next_epoch=4)
    kv.commit_edge(KvEdge(tick=1, admissions=[admit(1, 11)]))
    kv.commit_edge(KvEdge(tick=2, admissions=[
        admit(2, 7, flags=ALLOW_REPREFILL)]))
    claim_live = kv.save_live()
    assert claim_live.waiters[0].claimed
    assert claim_live.waiters[0].intent == KvPath.REPREFILL
    suffix = KvEdge(tick=3, owner_terminals=[
        KvOwnerTerminal(1, KvStatus.SUCCESS)], releases=[release(30, 11)])
    for flags in (REQUIRE_KV_REUSE, ALLOW_REPREFILL | REQUIRE_KV_REUSE):
        tampered = dataclasses.replace(
            claim_live, waiters=(dataclasses.replace(
                claim_live.waiters[0], flags=flags),))
        target = oracle(sessions=1, records=3, waiters=3)
        target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
        before = target.save_live()
        with pytest.raises(MeshIrError) as error:
            target.load_live(tampered)
        assert error.value.code == "E_KV_STATE", flags
        assert target.save_live() == before, flags

    baseline = KvOracle(kv.geometry, kv.capacity)
    baseline.load_live(claim_live)
    assert baseline.validate() == ()
    restored = KvOracle(kv.geometry, kv.capacity)
    restored.load_live(claim_live)
    left = restored.commit_edge(suffix)
    right = kv.commit_edge(suffix)
    assert left.fatal is None and right.fatal is None
    assert restored.edge_scalars(left) == kv.edge_scalars(right)
    assert sorted(restored.pins) == sorted(kv.pins) == [2]
    assert restored.pins[2].payload.intent == KvPath.REPREFILL
    assert restored.validate() == ()


def test_restore_accepts_legal_reuse_and_reprefill_paths():
    resident = load_record(resident_state(cached=8), sessions=2, next_epoch=4)
    reuse_pin = resident.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=REQUIRE_KV_REUSE, cached=8, after_round=8)]))
    assert reuse_pin.promotions[1] == KvPromotionOutcome.PINNED
    live = resident.save_live()
    assert live.pins[0].payload.intent == KvPath.KV_REUSE
    clone = KvOracle(resident.geometry, resident.capacity)
    clone.load_live(live)
    assert clone.validate() == ()

    allow = load_record(resident_state(cached=8), sessions=2, next_epoch=4)
    allow.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL, cached=8, after_round=8)]))
    allow_live = allow.save_live()
    assert allow_live.pins[0].payload.intent == KvPath.KV_REUSE
    allow_clone = KvOracle(allow.geometry, allow.capacity)
    allow_clone.load_live(allow_live)
    assert allow_clone.validate() == ()
    assert _reprefill_pin_state().pins[0].payload.intent == KvPath.REPREFILL


def test_admission_rejects_unknown_and_mixed_flags():
    for flags in (ALLOW_REPREFILL | REQUIRE_KV_REUSE, 4):
        kv = oracle(sessions=2, records=2, waiters=2)
        result = kv.commit_edge(KvEdge(tick=1, admissions=[
            admit(1, 11, flags=flags)]))
        assert result.fatal is None, flags
        assert result.admissions[1] == \
            KvAdmissionOutcome.FLAG_COMBINATION, flags
        assert kv.records == {} and kv.waiters == {} and kv.pins == {}
        assert all(owner is None for owner in kv.slot_owners)
        assert kv.validate() == ()
        follow = kv.commit_edge(KvEdge(tick=2))
        assert follow.fatal is None and follow.admissions == {}
        assert follow.promotions == {} and follow.terminals == {}
        assert kv.records == {} and kv.waiters == {} and kv.pins == {}
        assert kv.validate() == ()
    initial = oracle(sessions=2, records=2, waiters=2)
    assert initial.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 11)])).admissions[1] == KvAdmissionOutcome.CLAIMED
    assert initial.validate() == ()
    for flags in (ALLOW_REPREFILL, REQUIRE_KV_REUSE):
        kv = oracle(sessions=2, records=2, waiters=2)
        result = kv.commit_edge(KvEdge(tick=1, admissions=[
            admit(1, 11, flags=flags)]))
        assert result.admissions[1] == \
            KvAdmissionOutcome.E_KV_SESSION_NOT_FOUND, flags
        assert kv.validate() == ()
    reprefill = load_record(evicted_state(), sessions=2, next_epoch=4)
    assert reprefill.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=ALLOW_REPREFILL)])).admissions[1] == \
        KvAdmissionOutcome.CLAIMED
    assert reprefill.validate() == ()
    reuse = load_record(resident_state(cached=8), sessions=2, next_epoch=4)
    assert reuse.commit_edge(KvEdge(tick=1, admissions=[
        admit(1, 7, flags=REQUIRE_KV_REUSE, cached=8,
              after_round=8)])).admissions[1] == KvAdmissionOutcome.CLAIMED
    assert reuse.validate() == ()


def test_restore_validates_record_identity_and_nested_error():
    kv = load_record(evicted_state(), sessions=2, next_epoch=4)
    live = kv.save_live()
    for name, identity in (("negative", -1), ("past_u64", 0x10000000000000000),
                           ("zero", 0), ("bool", True), ("none", None)):
        for field in ("session_id", "kv_handle"):
            record = dataclasses.replace(live.records[0], **{field: identity})
            target = oracle(sessions=2, records=2, waiters=2)
            target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
            before = target.save_live()
            with pytest.raises(MeshIrError) as error:
                target.load_live(dataclasses.replace(live, records=(record,)))
            assert error.value.code == "E_KV_STATE", (field, name)
            assert target.save_live() == before, (field, name)

    pin_live = _reprefill_pin_state()
    nested = dataclasses.replace(
        pin_live, pins=(dataclasses.replace(
            pin_live.pins[0], payload=dataclasses.replace(
                pin_live.pins[0].payload, prior=dataclasses.replace(
                    pin_live.pins[0].payload.prior, session_id=-1))),))
    target = oracle(sessions=2, records=2, waiters=2)
    with pytest.raises(MeshIrError) as error:
        target.load_live(nested)
    assert error.value.code == "E_KV_STATE"

    error_record = dataclasses.replace(
        live.records[0], state=KvState.ERROR, first_error=candidate(1))
    positive = dataclasses.replace(live, records=(error_record,))
    accepted = KvOracle(kv.geometry, kv.capacity)
    accepted.load_live(positive)
    assert accepted.validate() == ()
    assert accepted.state_scalars()
    assert error_record.first_error.sort_key() < candidate(2).sort_key()

    source = error_record.first_error.source
    raw_source = _raw_source

    for name, overrides in (
            ("aux_type", {"aux_key": 7}),
            ("aux_length", {"aux_key": b"x" * 8}),
            ("error_class_enum", {"error_class": 99}),
            ("domain_enum", {"domain": 99}),
            ("object_kind_enum", {"object_kind": 99}),
            ("core_id_u16", {"core_id_or_ffff": 0x10000}),
            ("region_id_u32", {"region_id": 0x100000000}),
            ("generation_u32", {"generation": 0x100000000})):
        with pytest.raises(MeshIrError) as construction:
            dataclasses.replace(source, **overrides)
        assert construction.value.code == "E_MOE_CACHE", name

    for name, bad_source in (
            ("source_none", None),
            ("source_wrong_type", "source"),
            ("aux_length", raw_source(source, aux_key=b"x" * 8)),
            ("error_class_enum", raw_source(source, error_class=99)),
            ("core_id_u16", raw_source(source, core_id_or_ffff=0x10000)),
            ("generation_u32", raw_source(source, generation=0x100000000))):
        record = dataclasses.replace(
            error_record, first_error=dataclasses.replace(
                error_record.first_error, source=bad_source))
        target = oracle(sessions=2, records=2, waiters=2)
        target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
        before = target.save_live()
        with pytest.raises(MeshIrError) as error:
            target.load_live(dataclasses.replace(live, records=(record,)))
        assert error.value.code == "E_KV_STATE", name
        assert target.save_live() == before, name

    bad_candidates = (
        ("tick_u64", dataclasses.replace(error_record.first_error,
                                         tick=0x10000000000000000)),
        ("tick_bool", dataclasses.replace(error_record.first_error,
                                          tick=True)),
        ("code_u32", dataclasses.replace(error_record.first_error,
                                         code=0x100000000)),
        ("code_none", dataclasses.replace(error_record.first_error,
                                          code=None)),
    )
    for name, bad_candidate in bad_candidates:
        record = dataclasses.replace(error_record, first_error=bad_candidate)
        target = oracle(sessions=2, records=2, waiters=2)
        with pytest.raises(MeshIrError) as error:
            target.load_live(dataclasses.replace(live, records=(record,)))
        assert error.value.code == "E_KV_STATE", name


def test_restore_rejects_mutable_aux_key():
    kv = load_record(evicted_state(), sessions=2, next_epoch=4)
    live = kv.save_live()
    error_record = dataclasses.replace(
        live.records[0], state=KvState.ERROR, first_error=candidate(1))
    source = error_record.first_error.source
    with pytest.raises(MeshIrError) as construction:
        dataclasses.replace(source, aux_key=bytearray(16))
    assert construction.value.code == "E_MOE_CACHE"

    mutable = dataclasses.replace(
        error_record, first_error=dataclasses.replace(
            error_record.first_error,
            source=_raw_source(source, aux_key=bytearray(16))))
    target = oracle(sessions=2, records=2, waiters=2)
    target.commit_edge(KvEdge(tick=1, admissions=[admit(9, 9)]))
    before = target.save_live()
    with pytest.raises(MeshIrError) as error:
        target.load_live(dataclasses.replace(live, records=(mutable,)))
    assert error.value.code == "E_KV_STATE"
    assert target.save_live() == before

    immutable = dataclasses.replace(
        error_record, first_error=dataclasses.replace(
            error_record.first_error,
            source=dataclasses.replace(source, aux_key=bytes(16))))
    accepted = KvOracle(kv.geometry, kv.capacity)
    accepted.load_live(dataclasses.replace(live, records=(immutable,)))
    assert accepted.validate() == ()
    first = accepted.state_scalars()
    assert accepted.state_scalars() == first
    assert accepted.validate() == ()

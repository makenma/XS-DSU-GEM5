"""Independent KV admission/lifecycle oracle (main contract 10.4).

Recomputes admission, slot, claim, pin, rollback, eviction, release and
snapshot decisions from an explicit event sequence. It never reads the C++
manager or any runtime artifact, so the C++ component is graded against it
edge by edge.
"""

from __future__ import annotations

import dataclasses
import enum
import struct

from mesh_ir.kv_types import (
    SCHEMA_VERSION, NO_SLOT, NO_INTENT, NO_POLICY, U32_MAX, U64_MAX,
    POLICY_E_KV_REUSE_REQUIRED, POLICY_E_KV_TOKEN_MISMATCH, POLICY_E_KV_STATE,
    REQUIRE_KV_REUSE, ALLOW_REPREFILL, KV_FLAG_MASK, _STATES, _PATHS, KvState,
    KvPath, KvPinPhase, KvSnapshotSource, KvStatus, KvReleaseOutcome,
    KvAdmissionOutcome, KvPromotionOutcome, KvWaitReason, KvRollbackKind,
    _checked_add, _checked_mul, _is_int, KvGeometry, KvCapacity,
    KvErrorCandidate, KvRecord, KvRollbackPayload, KvAdmissionIntent,
    KvAdmissionWaiter, KvRequestPin, KvReleaseIntent, KvReleaseWaiter,
    KvAppendObligation, KvTerminalSnapshot, KvRuntimeView, KvEvictionEvent,
    KvEvictingEntry, KvOwnerTerminal, KvLaterFault, KvAppendArm,
    KvAppendTerminal, KvEdge, KvEdgeResult, KvPersistentState, KvLiveState,
    _empty_initial, _uniqueness_gaps, _frozen_key, _copy_waiter, _copy_pin,
    derived_initial,
    legal_flags,
)
from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import ErrorSourceKey


class KvOracle:
    def __init__(self, geometry: KvGeometry, capacity: KvCapacity):
        problems = list(geometry.gaps()) + list(capacity.gaps())
        if problems:
            raise MeshIrError("E_CAPACITY_PLAN", problems[0])
        self.geometry = geometry
        self.capacity = capacity
        self.records = {}
        self.tombstones = {}
        self.slot_owners = [None] * geometry.max_sessions
        self.waiters = {}
        self.pins = {}
        self.release_waiters = {}
        self.appends = {}
        self.evicting = {}
        self.next_kv_use_epoch = 1
        self.next_rollback_serial = 1
        self.fatal = None

    @property
    def claims(self) -> dict:
        return {request_id: entry
                for request_id, entry in self.waiters.items()
                if entry.claimed}

    def find_record(self, session_id: int, kv_handle: int):
        return self.records.get((session_id, kv_handle))

    def record_count(self) -> int:
        return len(self.records)

    def tombstone_count(self) -> int:
        return len(self.tombstones)

    def live_append(self, session_id: int, kv_handle: int) -> bool:
        return any(obligation.tuple == (session_id, kv_handle)
                   for obligation in self.appends.values())

    def release_pending(self, session_id: int, kv_handle: int) -> bool:
        return any(waiter.tuple == (session_id, kv_handle)
                   for waiter in self.release_waiters.values())

    def pin_bound(self) -> int:
        return min(self.capacity.record_entries, self.geometry.max_sessions)

    # ------------------------------------------------------------- edges

    def commit_edge(self, edge: KvEdge) -> KvEdgeResult:
        result = KvEdgeResult()
        result.commit_tick = edge.tick
        if self.fatal:
            result.fatal = self.fatal
            return result
        entry_pins = set(self.pins)
        cancelled = {terminal.request_id for terminal in edge.owner_terminals
                     if not terminal.strict}
        try:
            self._commit_identity(edge, entry_pins)
            if not self.fatal:
                self._commit_facts(edge, result)
            if not self.fatal:
                self._promote_allocating(edge)
            if not self.fatal:
                self._latch_releases(edge, result)
            if not self.fatal:
                self._commit_core_starts(edge)
            if not self.fatal:
                self._commit_terminals(edge, result)
            if not self.fatal:
                self._commit_release_and_eviction(result)
            if not self.fatal:
                self._commit_admissions(edge, result, cancelled)
        except MeshIrError as error:
            self.fatal = f"{error.code}: {error.message}"
        if self.fatal:
            result.fatal = self.fatal
        return result

    def _commit_identity(self, edge: KvEdge, entry_pins: set) -> None:
        command_ids = [intent.request_id for intent in edge.admissions]
        command_ids.extend(intent.request_id for intent in edge.releases)
        seen = set()
        for request_id in command_ids:
            if request_id in seen:
                self.fatal = "duplicate KV command identity in one edge"
                return
            seen.add(request_id)
        for intent in edge.admissions:
            if intent.request_id in entry_pins:
                self.fatal = "duplicate KV acquire for a pinned request"
                return
            if any(waiter.request_id == intent.request_id
                   for waiter in self.release_waiters.values()):
                self.fatal = "request id is a live release waiter"
                return
            entry = self.waiters.get(intent.request_id)
            if entry is None:
                continue
            if entry.frozen_key() != _frozen_key(intent):
                self.fatal = "waiter retry changed the frozen identity"
                return
            if entry.schedule_key() != (intent.deadline_or_max, intent.qos):
                self.fatal = "waiter retry changed the schedule key"
                return
        for intent in edge.releases:
            if intent.request_id in entry_pins:
                self.fatal = "release request id is a live KV pin"
                return
            if intent.request_id in self.waiters:
                self.fatal = "release request id is a live admission waiter"
                return
            if intent.request_id in self.release_waiters:
                self.fatal = "release request id is a live release waiter"
                return

    def _commit_facts(self, edge: KvEdge, result: KvEdgeResult) -> None:
        self._arm_views(edge, result)
        if self.fatal:
            return
        for arm in sorted(edge.append_arms, key=lambda item: item.request_id):
            self._arm_append(arm)
        if self.fatal:
            return
        for accept in sorted(edge.dma_accepts,
                             key=lambda item: item.request_id):
            record = self._pinned_record(accept.request_id)
            if record is None:
                self.fatal = "KV DMA accept without a KV pin"
                return
            record.outstanding_kv_dma = _checked_add(
                "outstanding_kv_dma", record.outstanding_kv_dma, accept.count,
                U32_MAX)
        for terminal in sorted(edge.dma_terminals,
                               key=lambda item: item.request_id):
            record = self._pinned_record(terminal.request_id)
            if record is None:
                self.fatal = "KV DMA terminal without a KV pin"
                return
            if terminal.count > record.outstanding_kv_dma:
                self.fatal = "outstanding_kv_dma underflow"
                return
            record.outstanding_kv_dma -= terminal.count
        for terminal in sorted(edge.append_terminals,
                               key=lambda item: item.request_id):
            self._commit_append(terminal)
            if self.fatal:
                return
        for fault in sorted(edge.faults,
                            key=lambda item: (item.request_id,
                                              item.candidate.sort_key())):
            record = self._pinned_record(fault.request_id)
            if record is None:
                self.fatal = "KV fault without a KV pin"
                return
            self._merge_first_error(record, fault.candidate)

    def _arm_append(self, arm: KvAppendArm) -> None:
        record = self._pinned_record(arm.request_id)
        if record is None:
            self.fatal = "KV append arm without a KV pin"
            return
        if arm.request_id in self.appends:
            self.fatal = "duplicate KV append obligation"
            return
        if record.slot_id is None:
            self.fatal = "KV append arm without a resident slot"
            return
        if arm.base_tokens != record.cached_tokens:
            self.fatal = "KV append base must equal cached tokens"
            return
        if arm.base_tokens + arm.append_tokens > \
                self.geometry.tokens_per_slot:
            self.fatal = "KV append exceeds kv_tokens_per_slot"
            return
        self.appends[arm.request_id] = KvAppendObligation(
            arm.request_id, record.session_id, record.kv_handle,
            record.generation, arm.base_tokens, arm.append_tokens,
            _checked_mul("kv append base byte", arm.base_tokens,
                         self.geometry.bytes_per_token),
            _checked_mul("kv append bytes", arm.append_tokens,
                         self.geometry.bytes_per_token))

    def _commit_append(self, terminal: KvAppendTerminal) -> None:
        obligation = self.appends.get(terminal.request_id)
        if obligation is None:
            self.fatal = "KV append terminal without an obligation"
            return
        record = self.records.get(obligation.tuple)
        if record is None:
            self.fatal = "KV append terminal without a record"
            return
        if len(terminal.tokens_ok) != obligation.append_tokens:
            self.fatal = "KV append bitmap size mismatch"
            return
        prefix = 0
        for ok in terminal.tokens_ok:
            if not ok:
                break
            prefix += 1
        record.cached_tokens = _checked_add(
            "cached_tokens", obligation.base_tokens, prefix,
            self.geometry.tokens_per_slot)
        record.valid_bytes = _checked_mul(
            "kv valid bytes", record.cached_tokens,
            self.geometry.bytes_per_token)
        del self.appends[terminal.request_id]
        if prefix == obligation.append_tokens:
            record.view_epoch = _checked_add("view_epoch", record.view_epoch,
                                             1, U64_MAX)
            if terminal.content_digest is not None:
                if len(terminal.content_digest) != 32:
                    self.fatal = "KV content digest must be 32 bytes"
                    return
                record.content_digest = terminal.content_digest
            return
        record.diagnostic_prefix_tokens = record.cached_tokens
        record.diagnostic_prefix_bytes = record.valid_bytes
        record.diagnostic_prefix_digest = record.content_digest
        record.content_digest = None
        record.state = KvState.ERROR

    @staticmethod
    def _merge_first_error(record: KvRecord,
                           candidate: KvErrorCandidate) -> None:
        if record.first_error is None or \
                candidate.sort_key() < record.first_error.sort_key():
            record.first_error = candidate

    def _promote_allocating(self, edge: KvEdge) -> None:
        terminalizing = {terminal.request_id
                         for terminal in edge.owner_terminals
                         if terminal.token is not None}
        terminalizing.update(fault.request_id for fault in edge.later_faults)
        for record in sorted(self.records.values(),
                             key=lambda item: item.tuple):
            if record.state != KvState.ALLOCATING or record.slot_id is None:
                continue
            if record.first_error is not None or not record.pin_count or \
                    record.admission_claim_count:
                continue
            owner = next((pin.request_id for pin in self.pins.values()
                          if pin.tuple == record.tuple), None)
            if owner is None or owner in terminalizing:
                continue
            record.state = KvState.RESIDENT

    def _latch_releases(self, edge: KvEdge, result: KvEdgeResult) -> None:
        for intent in sorted(edge.releases, key=lambda item: item.request_id):
            self._open_release(intent, result)

    def _commit_core_starts(self, edge: KvEdge) -> None:
        for request_id in sorted(set(edge.core_starts)):
            pin = self.pins.get(request_id)
            if pin is None:
                self.fatal = "core start without a KV pin"
                return
            if pin.phase != KvPinPhase.PRESTART or pin.payload is None:
                self.fatal = "core start without rollback authority"
                return
            self.pins[request_id] = dataclasses.replace(
                pin, phase=KvPinPhase.STARTED, payload=None)

    def _commit_terminals(self, edge: KvEdge, result: KvEdgeResult) -> None:
        seen_faults = set()
        for later in sorted(edge.later_faults,
                            key=lambda item: item.request_id):
            if later.request_id in seen_faults:
                self.fatal = "duplicate KV later fault"
                return
            seen_faults.add(later.request_id)
            self._apply_later_fault(later, result)
            if self.fatal:
                return
        terminals = {}
        for terminal in edge.owner_terminals:
            if terminal.request_id in terminals:
                self.fatal = "duplicate KV owner terminal"
                return
            terminals[terminal.request_id] = terminal
        for request_id in sorted(terminals):
            self._terminalize_owner(terminals[request_id], result)
            if self.fatal:
                return

    def _terminalize_owner(self, terminal: KvOwnerTerminal,
                           result: KvEdgeResult) -> None:
        request_id = terminal.request_id
        if terminal.token is not None:
            self._apply_rollback(terminal, result)
            return
        entry = self.waiters.get(request_id)
        if entry is not None and entry.claimed:
            self._terminalize_claim(entry, terminal.status,
                                    KvSnapshotSource.ADMISSION_CLAIM, result)
            return
        if request_id in self.pins:
            self._release_pin(request_id, terminal.status,
                              KvSnapshotSource.PIN, result)
            return
        if entry is not None:
            del self.waiters[request_id]
        if entry is None and terminal.strict:
            self.fatal = "KV owner terminal without an owner"
            return
        result.ownerless_cancels.append(request_id)

    def _apply_rollback(self, terminal: KvOwnerTerminal,
                        result: KvEdgeResult) -> None:
        request_id = terminal.request_id
        pin = self.pins.get(request_id)
        if terminal.token is None or pin is None or pin.payload is None or \
                terminal.token.request_id != request_id or \
                terminal.token.request_id != pin.request_id or \
                pin.payload.serial != terminal.token.serial:
            self.fatal = "rollback authority is no longer valid"
            return
        payload = pin.payload
        record = self.records.get(pin.tuple)
        if record is None:
            self.fatal = "prestart rollback without a record"
            return
        self._free_slot(record)
        self.appends.pop(request_id, None)
        if payload.prior_absent:
            record.state = KvState.ERROR
            record.cached_tokens = 0
            record.valid_bytes = 0
            record.content_digest = None
            record.view_epoch = 0
            record.last_use_epoch = 0
            record.diagnostic_prefix_tokens = 0
            record.diagnostic_prefix_bytes = 0
            record.diagnostic_prefix_digest = None
            record.pin_count = 0
            record.admission_claim_count = 0
            record.outstanding_kv_dma = 0
            record.first_error = None
            result.rollbacks[request_id] = \
                KvRollbackKind.INITIAL_ERROR_RECORD
        else:
            restored = payload.prior.copy()
            restored.pin_count = 0
            restored.admission_claim_count = 0
            restored.outstanding_kv_dma = 0
            self._install(restored)
            record = restored
            result.rollbacks[request_id] = KvRollbackKind.RESTORE_PRIOR
        self.waiters.pop(request_id, None)
        self.pins.pop(request_id, None)
        self._issue_terminal(record, request_id, terminal.status,
                             KvSnapshotSource.PIN, result)

    def _apply_later_fault(self, later: KvLaterFault,
                           result: KvEdgeResult) -> None:
        pin = self.pins.get(later.request_id)
        if pin is None:
            self.fatal = "later fault without a KV pin"
            return
        record = self.records.get(pin.tuple)
        if record is None:
            self.fatal = "later fault without a record"
            return
        self._merge_first_error(record, later.candidate)
        if record.state in (KvState.RESIDENT, KvState.ALLOCATING):
            record.state = KvState.ERROR
        self._release_pin(later.request_id, KvStatus.ERROR,
                          KvSnapshotSource.PIN, result)

    def _terminalize_claim(self, entry: KvAdmissionWaiter, status: int,
                           source: int, result: KvEdgeResult) -> None:
        record = self.records.get(entry.tuple)
        if record is None:
            self.fatal = "claim terminal without a record"
            return
        self._free_slot(record)
        if entry.prior_absent:
            record.state = KvState.ERROR
            record.cached_tokens = 0
            record.valid_bytes = 0
            record.content_digest = None
            record.view_epoch = 0
            record.last_use_epoch = 0
            record.diagnostic_prefix_tokens = 0
            record.diagnostic_prefix_bytes = 0
            record.diagnostic_prefix_digest = None
            record.pin_count = 0
            record.admission_claim_count = 0
            record.outstanding_kv_dma = 0
            record.first_error = None
        else:
            restored = entry.prior.copy()
            restored.pin_count = 0
            restored.admission_claim_count = 0
            restored.outstanding_kv_dma = 0
            self._install(restored)
            record = restored
        del self.waiters[entry.request_id]
        self.appends.pop(entry.request_id, None)
        self._issue_terminal(record, entry.request_id, status, source, result)

    def _release_pin(self, request_id: int, status: int, source: int,
                     result: KvEdgeResult) -> None:
        pin = self.pins.get(request_id)
        if pin is None:
            self.fatal = "pin release without an owner"
            return
        record = self.records.get(pin.tuple)
        if record is None:
            self.fatal = "pin release without a record"
            return
        if record.pin_count != 1:
            self.fatal = "pin release with a mismatched owner set"
            return
        if record.outstanding_kv_dma or self.live_append(*pin.tuple):
            self.fatal = "pin release with live KV work"
            return
        self._issue_terminal(record, request_id, status, source, result)
        record.pin_count = 0
        del self.pins[request_id]

    def _issue_terminal(self, record: KvRecord, request_id: int, status: int,
                        source: int, result: KvEdgeResult) -> None:
        if status == KvStatus.SUCCESS:
            required = self._required_tokens(request_id)
            if required is not None and (
                    record.cached_tokens != required or
                    record.valid_bytes != _checked_mul(
                        "kv valid bytes", required,
                        self.geometry.bytes_per_token)):
                self.fatal = "success terminal must match the required prefix"
                return
        result.terminals[request_id] = KvTerminalSnapshot(
            request_id, record.session_id, record.kv_handle,
            record.generation, record.contract_digest, record.state,
            record.cached_tokens, record.valid_bytes, record.content_digest,
            status, source)

    def _required_tokens(self, request_id: int):
        pin = self.pins.get(request_id)
        if pin is not None:
            return pin.required_tokens_after_round
        entry = self.waiters.get(request_id)
        if entry is not None and entry.claimed:
            return entry.required_tokens_after_round
        return None

    def _commit_release_and_eviction(self,
                                     result: KvEdgeResult) -> None:
        for entry in sorted(self.evicting):
            prior = self.evicting[entry]
            record = self.records.get(entry)
            if record is None or record.state != KvState.EVICTING:
                continue
            self._free_slot(record)
            if prior.prior_state == KvState.ERROR:
                record.diagnostic_prefix_tokens = prior.cached_tokens
                record.diagnostic_prefix_bytes = prior.valid_bytes
                record.diagnostic_prefix_digest = prior.content_digest
                record.state = KvState.ERROR
            else:
                record.state = KvState.EVICTED
            record.cached_tokens = 0
            record.valid_bytes = 0
            record.content_digest = None
            del self.evicting[entry]
            result.evictions_completed.append(KvEvictionEvent(
                record.session_id, record.kv_handle, record.generation,
                prior.prior_state, record.diagnostic_prefix_tokens,
                record.diagnostic_prefix_bytes))
            break
        for waiter in sorted(self.release_waiters.values(),
                             key=lambda item: (item.session_id,
                                               item.kv_handle,
                                               item.request_id)):
            if self._release_ready(waiter):
                self._finish_release(waiter, result)
                break

    def _release_ready(self, waiter: KvReleaseWaiter) -> bool:
        record = self.records.get(waiter.tuple)
        if record is None:
            return False
        if record.state in (KvState.ALLOCATING, KvState.EVICTING):
            return False
        return not (record.admission_claim_count or record.pin_count or
                    record.outstanding_kv_dma or
                    self.live_append(*waiter.tuple))

    def _finish_release(self, waiter: KvReleaseWaiter,
                        result: KvEdgeResult) -> None:
        record = self.records.get(waiter.tuple)
        if record is None:
            self.fatal = "release completion without a record"
            return
        if len(self.tombstones) >= self.capacity.tombstone_entries:
            self.fatal = "E_CAPACITY_PLAN: session tombstone table is full"
            return
        generation = _checked_add("session generation", record.generation, 1,
                                  U32_MAX)
        self._free_slot(record)
        del self.records[waiter.tuple]
        self.tombstones[waiter.tuple] = generation
        del self.release_waiters[waiter.request_id]
        result.releases[waiter.request_id] = KvReleaseOutcome.SUCCESS

    # --------------------------------------------------------- admission

    def _commit_admissions(self, edge: KvEdge, result: KvEdgeResult,
                           cancelled: set) -> None:
        for intent in sorted(edge.admissions,
                             key=lambda item: (item.request_id,
                                               item.ready_tick)):
            if intent.request_id in cancelled:
                continue
            self._open_waiter(intent, result)
            if self.fatal:
                return
        self._commit_head(result)

    def _open_waiter(self, intent: KvAdmissionIntent,
                     result: KvEdgeResult) -> None:
        if not legal_flags(intent.flags):
            result.admissions[intent.request_id] = \
                KvAdmissionOutcome.FLAG_COMBINATION
            return
        if not _is_int(intent.generation) or intent.generation == 0 or \
                not _is_int(intent.request_id) or intent.request_id == 0:
            result.admissions[intent.request_id] = \
                KvAdmissionOutcome.E_KV_STATE
            return
        entry = self.waiters.get(intent.request_id)
        if entry is not None:
            result.admissions[intent.request_id] = (
                KvAdmissionOutcome.CLAIMED if entry.claimed
                else KvAdmissionOutcome.WAITING)
            return
        if len(self.waiters) >= self.capacity.admission_wait_entries:
            result.admissions[intent.request_id] = \
                KvAdmissionOutcome.BACKPRESSURE
            return
        self.waiters[intent.request_id] = KvAdmissionWaiter(
            intent.request_id, intent.session_id, intent.kv_handle,
            intent.generation, intent.contract_digest, intent.deadline_or_max,
            intent.qos, intent.ready_tick, intent.flags,
            intent.required_cached_tokens, intent.required_tokens_after_round,
            derived_initial(intent.flags))
        result.admissions[intent.request_id] = KvAdmissionOutcome.WAITING

    def _permanent_rejection(self, entry: KvAdmissionWaiter, record):
        tombstone = self.tombstones.get(entry.tuple)
        if derived_initial(entry.flags):
            if record is not None or tombstone is not None:
                return KvAdmissionOutcome.E_SESSION_EXISTS
            return None
        if record is None:
            return (KvAdmissionOutcome.E_KV_STALE_GENERATION
                    if tombstone is not None and
                    entry.generation < tombstone
                    else KvAdmissionOutcome.E_KV_SESSION_NOT_FOUND)
        if record.generation != entry.generation:
            return KvAdmissionOutcome.E_KV_STALE_GENERATION
        if record.contract_digest != entry.contract_digest:
            return KvAdmissionOutcome.E_KV_CONTRACT_MISMATCH
        if record.state == KvState.ERROR:
            return KvAdmissionOutcome.E_KV_STATE
        return None

    def _temporary_block(self, entry: KvAdmissionWaiter, record):
        if derived_initial(entry.flags):
            if len(self.records) >= self.capacity.record_entries:
                return KvWaitReason.RECORD_TABLE_FULL
            return None
        if self.release_pending(*entry.tuple):
            return KvWaitReason.RELEASE_PENDING
        if record.pin_count or record.admission_claim_count:
            return KvWaitReason.TUPLE_OWNER_ACTIVE
        if record.state in (KvState.ALLOCATING, KvState.EVICTING):
            return KvWaitReason.RECORD_BUSY
        return None

    def _commit_head(self, result: KvEdgeResult) -> None:
        queue = sorted(self.waiters.values(),
                       key=lambda item: item.queue_key())
        if not queue:
            return
        head = queue[0]
        record = self.records.get(head.tuple)
        if head.claimed:
            if record is None:
                self.fatal = "admission head lost its record"
                return
            if head.prior is None:
                self.fatal = "claimed waiter lost its prior"
                return
            self._commit_pin(head, False, head.prior, head.prior_absent,
                             head.intent, result)
            return
        rejection = self._permanent_rejection(head, record)
        if rejection is not None:
            del self.waiters[head.request_id]
            result.admissions[head.request_id] = rejection
            return
        block = self._temporary_block(head, record)
        if block is not None:
            result.waits[head.request_id] = block
            return
        if derived_initial(head.flags):
            prior = _empty_initial(head)
            prior_absent = True
            path = KvPath.INITIAL_PREFILL
            policy_error = NO_POLICY
        else:
            prior = record.copy()
            prior_absent = False
            path, policy_error = self._decide_path(
                head.flags, head.required_cached_tokens, record)
        if policy_error != NO_POLICY:
            self._commit_policy_error(head, prior, prior_absent, policy_error,
                                      result)
            return
        if path != KvPath.KV_REUSE and self._lowest_free_slot() is None:
            self._commit_claim(head, record, prior, prior_absent, path,
                               result)
            victim = self._select_victim()
            if victim is not None:
                self._start_eviction(victim, result)
            result.promotions[head.request_id] = \
                KvPromotionOutcome.WAITING_SLOT
            return
        self._commit_pin(head, derived_initial(head.flags), prior,
                         prior_absent, path, result)

    def _commit_pin(self, head: KvAdmissionWaiter, create_record: bool,
                    prior: KvRecord, prior_absent: bool, path: int,
                    result: KvEdgeResult) -> None:
        slot_id = None
        view_epoch = 0
        if path != KvPath.KV_REUSE:
            slot_id = self._lowest_free_slot()
            if slot_id is None:
                victim = self._select_victim()
                if victim is not None:
                    self._start_eviction(victim, result)
                result.promotions[head.request_id] = \
                    KvPromotionOutcome.WAITING_SLOT
                return
            view_epoch = 1 if prior_absent else _checked_add(
                "view_epoch", prior.view_epoch, 1, U64_MAX)
        if len(self.pins) >= self.pin_bound():
            self.fatal = "E_CAPACITY_PLAN: KV pin table is full"
            return
        next_epoch = _checked_add("next_kv_use_epoch",
                                  self.next_kv_use_epoch, 1, U64_MAX)
        next_serial = _checked_add("next_rollback_serial",
                                   self.next_rollback_serial, 1, U64_MAX)
        payload = KvRollbackPayload(self.next_rollback_serial, path,
                                    prior_absent, prior.copy())
        pin = KvRequestPin(
            head.request_id, head.session_id, head.kv_handle,
            head.generation, head.contract_digest, head.flags,
            head.required_cached_tokens, head.required_tokens_after_round,
            KvPinPhase.PRESTART, payload)
        if create_record:
            record = prior.copy()
            record.admission_claim_count = 0
            self._install(record)
        else:
            record = self.records[head.tuple]
        if slot_id is not None:
            self._take_slot(record, slot_id, view_epoch)
        record.last_use_epoch = self.next_kv_use_epoch
        record.admission_claim_count = 0
        record.pin_count = 1
        self.next_kv_use_epoch = next_epoch
        self.next_rollback_serial = next_serial
        del self.waiters[head.request_id]
        self.pins[head.request_id] = pin
        result.admissions[head.request_id] = KvAdmissionOutcome.CLAIMED
        result.handoffs[head.request_id] = pin.token()
        result.promotions[head.request_id] = KvPromotionOutcome.PINNED

    def _commit_claim(self, head: KvAdmissionWaiter, record: KvRecord,
                      prior: KvRecord, prior_absent: bool, path: int,
                      result: KvEdgeResult) -> None:
        if derived_initial(head.flags):
            claimed = prior.copy()
            claimed.admission_claim_count = 1
            self._install(claimed)
        else:
            record.admission_claim_count = _checked_add(
                "admission_claim_count", record.admission_claim_count, 1,
                U32_MAX)
        head.claimed = True
        head.prior = prior
        head.prior_absent = prior_absent
        head.intent = path
        head.policy_error = NO_POLICY
        result.admissions[head.request_id] = KvAdmissionOutcome.CLAIMED

    def _commit_policy_error(self, head: KvAdmissionWaiter, prior: KvRecord,
                             prior_absent: bool, policy_error: int,
                             result: KvEdgeResult) -> None:
        if prior_absent:
            record = prior.copy()
            record.state = KvState.ERROR
            record.cached_tokens = 0
            record.valid_bytes = 0
            record.content_digest = None
            record.view_epoch = 0
            record.last_use_epoch = 0
            record.diagnostic_prefix_tokens = 0
            record.diagnostic_prefix_bytes = 0
            record.diagnostic_prefix_digest = None
            record.pin_count = 0
            record.admission_claim_count = 0
            record.outstanding_kv_dma = 0
            record.first_error = None
            self._install(record)
        else:
            record = self.records[head.tuple]
        result.admissions[head.request_id] = KvAdmissionOutcome.CLAIMED
        del self.waiters[head.request_id]
        self._issue_terminal(record, head.request_id, KvStatus.ERROR,
                             KvSnapshotSource.ADMISSION_CLAIM, result)
        result.promotions[head.request_id] = {
            POLICY_E_KV_REUSE_REQUIRED:
                KvPromotionOutcome.E_KV_REUSE_REQUIRED,
            POLICY_E_KV_TOKEN_MISMATCH:
                KvPromotionOutcome.E_KV_TOKEN_MISMATCH,
            POLICY_E_KV_STATE: KvPromotionOutcome.E_KV_STATE,
        }[policy_error]

    @staticmethod
    def _decide_path(flags, required_cached_tokens, prior: KvRecord):
        require_reuse = bool(flags & REQUIRE_KV_REUSE)
        allow_reprefill = bool(flags & ALLOW_REPREFILL)
        matched = prior.cached_tokens == required_cached_tokens
        if prior.state == KvState.RESIDENT and matched:
            return KvPath.KV_REUSE, NO_POLICY
        if require_reuse:
            if prior.state != KvState.RESIDENT:
                return None, POLICY_E_KV_REUSE_REQUIRED
            return None, POLICY_E_KV_TOKEN_MISMATCH
        if not allow_reprefill:
            return None, POLICY_E_KV_STATE
        if prior.state == KvState.EVICTED:
            return KvPath.REPREFILL, NO_POLICY
        if prior.state == KvState.RESIDENT:
            return None, POLICY_E_KV_TOKEN_MISMATCH
        return None, POLICY_E_KV_STATE

    def _open_release(self, intent: KvReleaseIntent,
                      result: KvEdgeResult) -> None:
        if any(waiter.tuple == intent.tuple
               for waiter in self.release_waiters.values()):
            result.releases[intent.request_id] = KvReleaseOutcome.BUSY
            return
        record = self.records.get(intent.tuple)
        if record is None:
            tombstone = self.tombstones.get(intent.tuple)
            result.releases[intent.request_id] = (
                KvReleaseOutcome.STALE_GENERATION
                if tombstone is not None and intent.generation < tombstone
                else KvReleaseOutcome.NOT_FOUND)
            return
        if record.generation != intent.generation:
            result.releases[intent.request_id] = \
                KvReleaseOutcome.STALE_GENERATION
            return
        if len(self.release_waiters) >= self.capacity.release_waiter_entries:
            result.releases[intent.request_id] = KvReleaseOutcome.BUSY
            return
        self.release_waiters[intent.request_id] = KvReleaseWaiter(
            intent.request_id, intent.session_id, intent.kv_handle,
            intent.generation)

    # ------------------------------------------------------------ helpers

    def _lowest_free_slot(self):
        for slot_id, owner in enumerate(self.slot_owners):
            if owner is None:
                return slot_id
        return None

    def _take_slot(self, record: KvRecord, slot_id: int,
                   view_epoch: int) -> None:
        record.slot_id = slot_id
        record.view_epoch = view_epoch
        record.state = KvState.ALLOCATING
        self.slot_owners[slot_id] = record.tuple

    def _free_slot(self, record: KvRecord) -> None:
        if record.slot_id is None:
            return
        if self.slot_owners[record.slot_id] == record.tuple:
            self.slot_owners[record.slot_id] = None
        record.slot_id = None

    def _install(self, record: KvRecord) -> None:
        self.records[record.tuple] = record
        if record.slot_id is not None:
            self.slot_owners[record.slot_id] = record.tuple

    def _select_victim(self):
        candidates = [record for record in self.records.values()
                      if record.eligible_for_eviction(
                          self.live_append(*record.tuple),
                          self.release_pending(*record.tuple))]
        if not candidates:
            return None
        return min(candidates, key=lambda item: item.victim_key())

    def _start_eviction(self, victim: KvRecord, result: KvEdgeResult) -> None:
        self.evicting[victim.tuple] = KvEvictingEntry(
            victim.state, victim.generation, victim.cached_tokens,
            victim.valid_bytes, victim.content_digest)
        victim.state = KvState.EVICTING
        result.evictions_started.append(KvEvictionEvent(
            victim.session_id, victim.kv_handle, victim.generation,
            self.evicting[victim.tuple].prior_state))

    def _pinned_record(self, request_id: int):
        pin = self.pins.get(request_id)
        return None if pin is None else self.records.get(pin.tuple)

    def _arm_views(self, edge: KvEdge, result: KvEdgeResult) -> None:
        for arm in sorted(edge.view_arms,
                          key=lambda item: (item.request_id,
                                            item.member_ordinal)):
            pin = self.pins.get(arm.request_id)
            if pin is None:
                self.fatal = "runtime view without a KV pin"
                return
            record = self.records.get(pin.tuple)
            if record is None or record.slot_id is None:
                self.fatal = "runtime view without a resident slot"
                return
            result.views[(arm.request_id, arm.member_ordinal)] = \
                KvRuntimeView(
                    arm.request_id, arm.member_ordinal, record.session_id,
                    record.kv_handle, record.generation,
                    record.contract_digest, record.view_epoch, record.slot_id,
                    self.geometry.slot_base(record.slot_id),
                    self.geometry.slot_bytes, record.valid_bytes)

    # --------------------------------------------------------- validation

    @staticmethod
    def _counter_gaps(name, value) -> tuple:
        if not _is_int(value) or not 0 < value <= U64_MAX:
            return (f"{name} must be a nonzero u64",)
        return ()

    def _shape_gaps(self, records, slot_owners, waiters, pins,
                    release_waiters, appends, evicting, tombstones,
                    next_kv_use_epoch, next_rollback_serial) -> tuple:
        problems = []
        if not isinstance(slot_owners, (tuple, list)):
            problems.append("slot bitmap must be a sequence")
        elif len(slot_owners) != self.geometry.max_sessions:
            problems.append("slot bitmap length must equal kv_max_sessions")
        else:
            for owner in slot_owners:
                if owner is None:
                    continue
                if not isinstance(owner, tuple) or len(owner) != 2 or \
                        not all(_is_int(part) and 0 < part <= U64_MAX
                                for part in owner):
                    problems.append(
                        "slot bitmap entry must be empty or a valid tuple")
                    break
        for name, value, kind in (
                ("record", records, KvRecord),
                ("waiter", waiters, KvAdmissionWaiter),
                ("pin", pins, KvRequestPin),
                ("release waiter", release_waiters, KvReleaseWaiter),
                ("append", appends, KvAppendObligation)):
            if not isinstance(value, (tuple, list)):
                problems.append(f"{name} table must be a sequence")
                continue
            for element in value:
                if not isinstance(element, kind):
                    problems.append(f"{name} entry must be a typed value")
                    break
        if not isinstance(evicting, (tuple, list)):
            problems.append("eviction table must be a sequence")
        else:
            for item in evicting:
                if not isinstance(item, tuple) or len(item) != 2 or \
                        not isinstance(item[0], tuple) or \
                        len(item[0]) != 2 or \
                        not all(_is_int(part) and 0 < part <= U64_MAX
                                for part in item[0]) or \
                        not isinstance(item[1], KvEvictingEntry):
                    problems.append("eviction entry must be a typed entry")
                    break
        if not isinstance(tombstones, (tuple, list)):
            problems.append("tombstone table must be a sequence")
        else:
            for item in tombstones:
                if not isinstance(item, tuple) or len(item) != 2 or \
                        not isinstance(item[0], tuple) or \
                        len(item[0]) != 2 or \
                        not all(_is_int(part) and 0 < part <= U64_MAX
                                for part in item[0]) or \
                        not _is_int(item[1]) or item[1] <= 0:
                    problems.append("tombstone entry must be a pair")
                    break
        problems.extend(self._counter_gaps("next_kv_use_epoch",
                                           next_kv_use_epoch))
        problems.extend(self._counter_gaps("next_rollback_serial",
                                           next_rollback_serial))
        return tuple(problems)

    def _identity_gaps(self, name, request_id, session_id, kv_handle,
                       generation, contract=None) -> tuple:
        problems = []
        for field, value in (("request_id", request_id),
                             ("session_id", session_id),
                             ("kv_handle", kv_handle)):
            if not _is_int(value) or not 0 < value <= U64_MAX:
                problems.append(f"{name} {field} must be a nonzero u64")
        if not _is_int(generation) or not 0 < generation <= U32_MAX:
            problems.append(f"{name} generation must be a nonzero u32")
        if contract is not None and (
                not isinstance(contract, bytes) or len(contract) != 32):
            problems.append(f"{name} contract digest must be 32 bytes")
        return tuple(problems)

    @staticmethod
    def _flags_gaps(name, flags) -> tuple:
        if not legal_flags(flags):
            return (f"{name} flags must be a legal combination",)
        return ()

    def _waiter_gaps(self, entry: KvAdmissionWaiter) -> tuple:
        problems = list(self._identity_gaps(
            "waiter", entry.request_id, entry.session_id, entry.kv_handle,
            entry.generation, entry.contract_digest))
        problems.extend(self._flags_gaps("waiter", entry.flags))
        if not _is_int(entry.qos) or not 0 <= entry.qos <= 255:
            problems.append("waiter qos must be a u8")
        for field, value in (("deadline_or_max", entry.deadline_or_max),
                             ("ready_tick", entry.ready_tick)):
            if not _is_int(value) or not 0 <= value <= U64_MAX:
                problems.append(f"waiter {field} must be a u64")
        for field, value in (("required_cached_tokens",
                              entry.required_cached_tokens),
                             ("required_tokens_after_round",
                              entry.required_tokens_after_round)):
            if not _is_int(value) or not 0 <= value <= U32_MAX:
                problems.append(f"waiter {field} must be a u32")
        for field, value in (("initial", entry.initial),
                             ("claimed", entry.claimed),
                             ("prior_absent", entry.prior_absent)):
            if not isinstance(value, bool):
                problems.append(f"waiter {field} must be a boolean")
        if entry.intent is not None and (
                not _is_int(entry.intent) or entry.intent not in _PATHS):
            problems.append("waiter path is outside the closed set")
        if not _is_int(entry.policy_error) or entry.policy_error not in (
                POLICY_E_KV_REUSE_REQUIRED, POLICY_E_KV_TOKEN_MISMATCH,
                POLICY_E_KV_STATE, NO_POLICY):
            problems.append("waiter policy error is outside the closed set")
        if problems:
            return tuple(problems)
        if entry.initial != derived_initial(entry.flags):
            problems.append("waiter kind must match the frozen flags")
        if entry.claimed:
            if entry.intent is None or entry.policy_error != NO_POLICY:
                problems.append("claimed waiter must carry a live path")
                return tuple(problems)
            if not isinstance(entry.prior, KvRecord):
                problems.append("claimed waiter must carry a prior record")
                return tuple(problems)
            if (entry.intent == KvPath.INITIAL_PREFILL) != entry.prior_absent:
                problems.append("absent prior only belongs to INITIAL")
        elif entry.intent is not None or entry.policy_error != NO_POLICY or \
                entry.prior_absent or entry.prior is not None:
            problems.append("waiting waiter must not carry a prior")
        return tuple(problems)

    def _pin_gaps(self, pin: KvRequestPin, next_epoch, next_serial,
                  record=None) -> tuple:
        problems = list(self._identity_gaps(
            "pin", pin.request_id, pin.session_id, pin.kv_handle,
            pin.generation, pin.contract_digest))
        problems.extend(self._flags_gaps("pin", pin.flags))
        for field, value in (("required_cached_tokens",
                              pin.required_cached_tokens),
                             ("required_tokens_after_round",
                              pin.required_tokens_after_round)):
            if not _is_int(value) or not 0 <= value <= U32_MAX:
                problems.append(f"pin {field} must be a u32")
        if not _is_int(pin.phase) or pin.phase not in (
                KvPinPhase.PRESTART, KvPinPhase.STARTED):
            problems.append("KV pin phase is outside the closed set")
            return tuple(problems)
        if problems:
            return tuple(problems)
        if pin.phase == KvPinPhase.STARTED:
            if pin.payload is not None:
                problems.append("STARTED pin must not carry rollback payload")
            return tuple(problems)
        payload = pin.payload
        if not isinstance(payload, KvRollbackPayload):
            problems.append("PRESTART pin must carry its rollback payload")
            return tuple(problems)
        if not _is_int(payload.serial) or not 0 < payload.serial <= U64_MAX:
            problems.append("rollback payload serial must be a u64")
        elif payload.serial >= next_serial:
            problems.append("rollback payload serial is not issued")
        if not _is_int(payload.intent) or payload.intent not in _PATHS:
            problems.append("rollback payload path is outside the set")
        if not isinstance(payload.prior_absent, bool) or \
                not isinstance(payload.prior, KvRecord):
            problems.append("rollback payload prior must be a record")
            return tuple(problems)
        if derived_initial(pin.flags) != (
                payload.intent == KvPath.INITIAL_PREFILL):
            problems.append("pin path must match the frozen flags")
        problems.extend(self._prior_gaps(
            pin, payload.intent, payload.prior_absent, payload.prior,
            next_epoch, record))
        return tuple(problems)

    def _prior_gaps(self, owner, intent, prior_absent,
                    prior: KvRecord, next_epoch, record=None) -> tuple:
        problems = list(prior.gaps(self.geometry, live=False))
        if owner.tuple != prior.tuple:
            problems.append("prior tuple must match the request identity")
        if owner.generation != prior.generation:
            problems.append("prior generation must match the request identity")
        if owner.contract_digest != prior.contract_digest:
            problems.append("prior contract must match the request identity")
        if prior.pin_count or prior.admission_claim_count or \
                prior.outstanding_kv_dma:
            problems.append("prior snapshot must not carry live ownership")
        if _is_int(prior.last_use_epoch) and prior.last_use_epoch and \
                prior.last_use_epoch >= next_epoch:
            problems.append("prior epoch must precede next epoch")
        if prior_absent:
            if intent != KvPath.INITIAL_PREFILL:
                problems.append("absent prior only belongs to INITIAL")
            elif prior.state != KvState.ALLOCATING or \
                    prior.slot_id is not None or prior.cached_tokens or \
                    prior.valid_bytes or \
                    prior.content_digest is not None or prior.view_epoch or \
                    prior.last_use_epoch or prior.first_error is not None or \
                    prior.diagnostic_prefix_tokens or \
                    prior.diagnostic_prefix_bytes or \
                    prior.diagnostic_prefix_digest is not None:
                problems.append("absent prior must be the empty INITIAL state")
            return tuple(problems)
        if intent == KvPath.INITIAL_PREFILL:
            problems.append("INITIAL prior must be absent")
        elif intent == KvPath.REPREFILL:
            if prior.state != KvState.EVICTED or prior.slot_id is not None or \
                    prior.cached_tokens or prior.valid_bytes or \
                    prior.content_digest is not None:
                problems.append("REPREFILL prior must be an EVICTED record")
        elif intent == KvPath.KV_REUSE:
            if prior.state != KvState.RESIDENT or prior.slot_id is None:
                problems.append("KV_REUSE prior must be a RESIDENT record")
            elif prior.cached_tokens != owner.required_cached_tokens:
                problems.append("KV_REUSE prior must satisfy the reuse input")
            if record is not None and prior.slot_id is not None and \
                    record.slot_id != prior.slot_id:
                problems.append("KV_REUSE prior must hold the owner slot")
        if not derived_initial(owner.flags):
            path, policy_error = self._decide_path(
                owner.flags, owner.required_cached_tokens, prior)
            if policy_error != NO_POLICY:
                problems.append("prior does not satisfy the frozen policy")
            elif path != intent:
                problems.append("prior path must match the frozen flags")
        return tuple(problems)

    def _validate_structure(self, records, slot_owners, waiters, pins,
                            release_waiters, appends, evicting, tombstones,
                            next_kv_use_epoch, next_rollback_serial,
                            live: bool) -> tuple:
        shape = self._shape_gaps(records, slot_owners, waiters, pins,
                                 release_waiters, appends, evicting,
                                 tombstones, next_kv_use_epoch,
                                 next_rollback_serial)
        if shape:
            return shape
        problems = []
        for name, size, capacity in (
                ("record", len(records), self.capacity.record_entries),
                ("admission waiter", len(waiters),
                 self.capacity.admission_wait_entries),
                ("KV pin", len(pins), self.pin_bound()),
                ("release waiter", len(release_waiters),
                 self.capacity.release_waiter_entries),
                ("tombstone", len(tombstones),
                 self.capacity.tombstone_entries)):
            if (live or name == "tombstone") and size > capacity:
                problems.append(f"{name} table exceeds its capacity")
        for record in records:
            problems.extend(record.gaps(self.geometry, live))
        for entry in waiters:
            problems.extend(self._waiter_gaps(entry))
        serials = []
        for pin in pins:
            problems.extend(self._pin_gaps(pin, next_kv_use_epoch,
                                           next_rollback_serial))
            if pin.phase == KvPinPhase.PRESTART and \
                    isinstance(pin.payload, KvRollbackPayload) and \
                    _is_int(pin.payload.serial):
                if pin.payload.serial in serials:
                    problems.append("duplicate rollback serial")
                serials.append(pin.payload.serial)
        for entry in release_waiters:
            problems.extend(self._identity_gaps(
                "release waiter", entry.request_id, entry.session_id,
                entry.kv_handle, entry.generation))
        for entry in appends:
            problems.extend(self._identity_gaps(
                "append", entry.request_id, entry.session_id,
                entry.kv_handle, entry.generation))
            for field, value, limit in (
                    ("base_tokens", entry.base_tokens, U32_MAX),
                    ("append_tokens", entry.append_tokens, U32_MAX),
                    ("base_byte", entry.base_byte, U64_MAX),
                    ("bytes", entry.bytes, U64_MAX)):
                if not _is_int(value) or not 0 <= value <= limit:
                    problems.append(f"append {field} is outside its width")
                    break
            else:
                if entry.bytes != entry.append_tokens * \
                        self.geometry.bytes_per_token or \
                        entry.bytes > U64_MAX or \
                        entry.base_byte != entry.base_tokens * \
                        self.geometry.bytes_per_token or \
                        entry.base_byte > U64_MAX:
                    problems.append("append obligation geometry is invalid")
        for key, entry in evicting:
            problems.extend(self._identity_gaps(
                "eviction", 1, key[0], key[1], entry.generation))
            if not _is_int(entry.prior_state) or \
                    entry.prior_state not in _STATES:
                problems.append("eviction prior state is outside the set")
            for field, value, limit in (
                    ("cached_tokens", entry.cached_tokens, U32_MAX),
                    ("valid_bytes", entry.valid_bytes, U64_MAX)):
                if not _is_int(value) or not 0 <= value <= limit:
                    problems.append(f"eviction {field} is outside its width")
                    break
        for key, generation in tombstones:
            problems.extend(self._identity_gaps(
                "tombstone", 1, key[0], key[1], generation))
        if problems:
            return tuple(problems)
        problems.extend(_uniqueness_gaps(records, waiters, pins,
                                         release_waiters, appends, evicting,
                                         tombstones))
        return tuple(problems)

    def validate(self) -> tuple:
        problems = list(self.geometry.gaps()) + list(self.capacity.gaps())
        problems.extend(self._validate_structure(
            list(self.records.values()), list(self.slot_owners),
            list(self.waiters.values()), list(self.pins.values()),
            list(self.release_waiters.values()), list(self.appends.values()),
            list(self.evicting.items()), list(self.tombstones.items()),
            self.next_kv_use_epoch, self.next_rollback_serial, live=True))
        if problems:
            return tuple(problems)
        for record in self.records.values():
            if record.slot_id is not None and \
                    self.slot_owners[record.slot_id] != record.tuple:
                problems.append("slot bitmap disagrees with the record")
            if record.last_use_epoch and \
                    record.last_use_epoch >= self.next_kv_use_epoch:
                problems.append("record epoch must precede next epoch")
        seen = set()
        for slot_id, owner in enumerate(self.slot_owners):
            if owner is None:
                continue
            if owner in seen:
                problems.append("duplicate slot owner")
            seen.add(owner)
            record = self.records.get(owner)
            if record is None or record.slot_id != slot_id:
                problems.append("slot bitmap carries a stale owner")
        pins_by_tuple: dict = {}
        for pin in self.pins.values():
            pins_by_tuple[pin.tuple] = pins_by_tuple.get(pin.tuple, 0) + 1
        claims_by_tuple: dict = {}
        for entry in self.waiters.values():
            if not entry.claimed:
                continue
            claims_by_tuple[entry.tuple] = \
                claims_by_tuple.get(entry.tuple, 0) + 1
        for record in self.records.values():
            if record.pin_count != pins_by_tuple.get(record.tuple, 0):
                problems.append("pin count must equal the owner set size")
            if record.admission_claim_count != \
                    claims_by_tuple.get(record.tuple, 0):
                problems.append("claim count must equal the owner set size")
            if record.pin_count and record.admission_claim_count:
                problems.append("claim and pin must not coexist")
            if record.pin_count > 1 or record.admission_claim_count > 1:
                problems.append("record accepts at most one KV owner")
        for pin in self.pins.values():
            record = self.records.get(pin.tuple)
            if record is None or record.generation != pin.generation or \
                    record.contract_digest != pin.contract_digest:
                problems.append("KV pin identity mismatch")
                continue
            if record.slot_id is None or \
                    self.slot_owners[record.slot_id] != record.tuple:
                problems.append("KV pin must own its slot")
            if isinstance(pin.payload, KvRollbackPayload):
                problems.extend(self._prior_gaps(
                    pin, pin.payload.intent, pin.payload.prior_absent,
                    pin.payload.prior, self.next_kv_use_epoch, record))
        for entry in self.waiters.values():
            if not entry.claimed:
                continue
            record = self.records.get(entry.tuple)
            if record is None or record.generation != entry.generation or \
                    record.contract_digest != entry.contract_digest:
                problems.append("KV claim identity mismatch")
                continue
            if record.admission_claim_count != 1:
                problems.append("claim count must equal the owner set size")
            if entry.intent == KvPath.KV_REUSE:
                if record.slot_id is None:
                    problems.append("KV_REUSE claim must hold its slot")
            elif record.slot_id is not None:
                problems.append("pending claim must not hold a slot")
            problems.extend(self._prior_gaps(
                entry, entry.intent, entry.prior_absent, entry.prior,
                self.next_kv_use_epoch, record))
        for waiter in self.release_waiters.values():
            record = self.records.get(waiter.tuple)
            if record is None or record.generation != waiter.generation:
                problems.append("release waiter identity mismatch")
        for obligation in self.appends.values():
            record = self.records.get(obligation.tuple)
            if record is None or record.generation != obligation.generation:
                problems.append("append obligation identity mismatch")
            elif not any(pin.request_id == obligation.request_id and
                         pin.tuple == obligation.tuple
                         for pin in self.pins.values()):
                problems.append("append obligation must belong to a live pin")
        for owner, entry in self.evicting.items():
            record = self.records.get(owner)
            if record is None or record.state != KvState.EVICTING:
                problems.append("EVICTING entry without a matching record")
            elif record.generation != entry.generation:
                problems.append("EVICTING entry generation mismatch")
        return tuple(problems)

    # -------------------------------------------------------- persistence

    def save_persistent(self) -> KvPersistentState:
        problems = self.validate()
        if problems:
            raise MeshIrError("E_KV_STATE", problems[0])
        if self.waiters or self.pins or self.release_waiters or self.appends \
                or self.evicting:
            raise MeshIrError("E_KV_STATE",
                              "persistent state must have no live owner")
        for record in self.records.values():
            if record.state not in (KvState.RESIDENT, KvState.EVICTED,
                                    KvState.ERROR):
                raise MeshIrError("E_KV_STATE",
                                  "persistent record state is not allowed")
        return KvPersistentState(
            tuple(self.slot_owners),
            tuple(sorted(self.records.values(), key=lambda item: item.tuple)),
            tuple(sorted(self.tombstones.items())), self.next_kv_use_epoch)

    def load_persistent(self, state: KvPersistentState) -> None:
        if not isinstance(state, KvPersistentState):
            raise MeshIrError("E_KV_STATE",
                              "persistent state must be a KvPersistentState")
        if len(state.records) > self.capacity.record_entries:
            raise MeshIrError("E_CAPACITY_PLAN",
                              "restore exceeds the record capacity")
        structure = self._validate_structure(
            state.records, state.slot_owners, [], [], [], [], [],
            state.tombstones, state.next_kv_use_epoch, 1, live=False)
        if structure:
            raise MeshIrError("E_KV_STATE", structure[0])
        candidate = KvOracle(self.geometry, self.capacity)
        candidate.slot_owners = list(state.slot_owners)
        candidate.next_kv_use_epoch = state.next_kv_use_epoch
        for record in state.records:
            candidate.records[record.tuple] = record.copy()
        for key, generation in state.tombstones:
            candidate.tombstones[key] = generation
        problems = candidate.validate()
        if problems:
            raise MeshIrError("E_KV_STATE", problems[0])
        for record in candidate.records.values():
            if record.state not in (KvState.RESIDENT, KvState.EVICTED,
                                    KvState.ERROR):
                raise MeshIrError("E_KV_STATE",
                                  "persistent record state is not allowed")
        self.records = candidate.records
        self.tombstones = candidate.tombstones
        self.slot_owners = candidate.slot_owners
        self.next_kv_use_epoch = candidate.next_kv_use_epoch
        self.waiters = {}
        self.pins = {}
        self.release_waiters = {}
        self.appends = {}
        self.evicting = {}
        self.next_rollback_serial = 1
        self.fatal = None

    def save_live(self) -> KvLiveState:
        return KvLiveState(
            tuple(record.copy() for record in sorted(
                self.records.values(), key=lambda item: item.tuple)),
            tuple(self.slot_owners),
            tuple(_copy_waiter(entry) for entry in sorted(
                self.waiters.values(), key=lambda item: item.request_id)),
            tuple(_copy_pin(pin) for pin in sorted(
                self.pins.values(), key=lambda item: item.request_id)),
            tuple(sorted(self.release_waiters.values(),
                         key=lambda item: item.request_id)),
            tuple(sorted(self.appends.values(),
                         key=lambda item: item.request_id)),
            tuple(sorted(self.evicting.items())),
            tuple(sorted(self.tombstones.items())),
            self.next_kv_use_epoch, self.next_rollback_serial)

    def load_live(self, state: KvLiveState) -> None:
        if not isinstance(state, KvLiveState):
            raise MeshIrError("E_KV_STATE", "live state must be a KvLiveState")
        structure = self._validate_structure(
            state.records, state.slot_owners, state.waiters, state.pins,
            state.release_waiters, state.appends, state.evicting,
            state.tombstones, state.next_kv_use_epoch,
            state.next_rollback_serial, live=True)
        if structure:
            raise MeshIrError("E_KV_STATE", structure[0])
        candidate = KvOracle(self.geometry, self.capacity)
        for record in state.records:
            candidate.records[record.tuple] = record.copy()
        candidate.slot_owners = list(state.slot_owners)
        for entry in state.waiters:
            candidate.waiters[entry.request_id] = _copy_waiter(entry)
        for pin in state.pins:
            candidate.pins[pin.request_id] = _copy_pin(pin)
        candidate.release_waiters = {entry.request_id: entry
                                     for entry in state.release_waiters}
        candidate.appends = {entry.request_id: entry
                             for entry in state.appends}
        candidate.evicting = dict(state.evicting)
        candidate.tombstones = dict(state.tombstones)
        candidate.next_kv_use_epoch = state.next_kv_use_epoch
        candidate.next_rollback_serial = state.next_rollback_serial
        problems = candidate.validate()
        if problems:
            raise MeshIrError("E_KV_STATE", problems[0])
        self.records = candidate.records
        self.slot_owners = candidate.slot_owners
        self.waiters = candidate.waiters
        self.pins = candidate.pins
        self.release_waiters = candidate.release_waiters
        self.appends = candidate.appends
        self.evicting = candidate.evicting
        self.tombstones = candidate.tombstones
        self.next_kv_use_epoch = candidate.next_kv_use_epoch
        self.next_rollback_serial = candidate.next_rollback_serial
        self.fatal = None

    # --------------------------------------------------------- projection

    def state_scalars(self) -> list:
        out = [SCHEMA_VERSION, self.next_kv_use_epoch,
               self.next_rollback_serial]
        words = (self.geometry.max_sessions + 63) // 64
        out.append(words)
        for word in range(words):
            value = 0
            for bit in range(64):
                slot_id = word * 64 + bit
                if slot_id < self.geometry.max_sessions and \
                        self.slot_owners[slot_id] is not None:
                    value |= 1 << bit
            out.append(value)
        out.append(len(self.records))
        for record in sorted(self.records.values(),
                             key=lambda item: item.tuple):
            out.extend(_record_scalars(record))
        out.append(len(self.waiters))
        for entry in sorted(self.waiters.values(),
                            key=lambda item: item.request_id):
            out.extend([entry.request_id, entry.session_id, entry.kv_handle,
                        entry.generation, entry.flags, entry.deadline_or_max,
                        entry.qos, entry.ready_tick,
                        entry.required_cached_tokens,
                        entry.required_tokens_after_round,
                        1 if entry.initial else 0,
                        1 if entry.claimed else 0,
                        NO_INTENT if entry.intent is None
                        else int(entry.intent),
                        entry.policy_error,
                        1 if entry.prior_absent else 0])
            out.extend(_record_scalars(entry.prior)
                       if entry.claimed and entry.prior is not None
                       else _EMPTY_RECORD_SCALARS)
        out.append(len(self.pins))
        for pin in sorted(self.pins.values(),
                          key=lambda item: item.request_id):
            out.extend([pin.request_id, pin.session_id, pin.kv_handle,
                        pin.generation, pin.flags,
                        pin.required_cached_tokens,
                        pin.required_tokens_after_round,
                        int(pin.phase)])
            if pin.payload is None:
                out.extend([0, NO_INTENT, 0])
                out.extend(_EMPTY_RECORD_SCALARS)
            else:
                out.extend([pin.payload.serial,
                            NO_INTENT if pin.payload.intent is None
                            else int(pin.payload.intent),
                            1 if pin.payload.prior_absent else 0])
                out.extend(_record_scalars(pin.payload.prior))
        out.append(len(self.release_waiters))
        for waiter in sorted(self.release_waiters.values(),
                             key=lambda item: item.request_id):
            out.extend([waiter.request_id, waiter.session_id,
                        waiter.kv_handle, waiter.generation])
        out.append(len(self.appends))
        for obligation in sorted(self.appends.values(),
                                 key=lambda item: item.request_id):
            out.extend([obligation.request_id, obligation.session_id,
                        obligation.kv_handle, obligation.generation,
                        obligation.base_tokens, obligation.append_tokens,
                        obligation.base_byte, obligation.bytes])
        out.append(len(self.evicting))
        for key, entry in sorted(self.evicting.items()):
            out.extend([key[0], key[1], entry.generation,
                        int(entry.prior_state), entry.cached_tokens,
                        entry.valid_bytes])
        out.append(len(self.tombstones))
        for key, generation in sorted(self.tombstones.items()):
            out.extend([key[0], key[1], generation])
        return out

    def decision_scalars(self, result: KvEdgeResult) -> list:
        out = [result.commit_tick]
        for request_id, outcome in sorted(result.admissions.items()):
            out.extend([0, request_id, int(outcome)])
        for request_id, outcome in sorted(result.promotions.items()):
            out.extend([1, request_id, int(outcome)])
        for request_id, reason in sorted(result.waits.items()):
            out.extend([10, request_id, int(reason)])
        for request_id, outcome in sorted(result.releases.items()):
            out.extend([2, request_id, int(outcome)])
        for event in sorted(result.evictions_started,
                            key=lambda item: (item.session_id, item.kv_handle,
                                              item.generation)):
            out.extend([3, event.session_id, event.kv_handle,
                        event.generation, event.prior_state])
        for event in sorted(result.evictions_completed,
                            key=lambda item: (item.session_id, item.kv_handle,
                                              item.generation)):
            out.extend([4, event.session_id, event.kv_handle,
                        event.generation, event.prior_state,
                        event.diagnostic_prefix_tokens,
                        event.diagnostic_prefix_bytes])
        for request_id, kind in sorted(result.rollbacks.items()):
            out.extend([5, request_id, int(kind)])
        for request_id, snapshot in sorted(result.terminals.items()):
            out.extend([6, request_id, snapshot.state, snapshot.cached_tokens,
                        snapshot.valid_bytes, snapshot.status,
                        snapshot.source,
                        1 if snapshot.content_digest is not None else 0])
            out.extend(_digest_scalars(snapshot.contract_digest))
        for request_id, token in sorted(result.handoffs.items()):
            out.extend([7, request_id, token.serial])
        for (request_id, ordinal), view in sorted(result.views.items()):
            out.extend([8, request_id, ordinal, view.view_epoch, view.slot_id,
                        view.slot_base, view.slot_bytes,
                        view.valid_bytes_at_arm])
        for request_id in sorted(result.ownerless_cancels):
            out.extend([9, request_id])
        return out

    def edge_scalars(self, result: KvEdgeResult) -> list:
        return self.decision_scalars(result) + self.state_scalars()

    def scalar_field_names(self) -> list:
        names = ["schema_version", "next_kv_use_epoch",
                 "next_rollback_serial", "slot_words"]
        words = (self.geometry.max_sessions + 63) // 64
        names.extend(f"slot_bitmap[{word}]" for word in range(words))
        names.append("record_count")
        for index, _ in enumerate(sorted(self.records.values(),
                                         key=lambda item: item.tuple)):
            names.extend(f"records[{index}].{name}"
                         for name in _RECORD_SCALAR_NAMES)
        names.append("waiter_count")
        for index, _ in enumerate(sorted(self.waiters.values(),
                                         key=lambda item: item.request_id)):
            names.extend(f"waiters[{index}].{name}"
                         for name in _WAITER_ENTRY_NAMES)
            names.extend(f"waiters[{index}].prior.{name}"
                         for name in _RECORD_SCALAR_NAMES)
        names.append("pin_count")
        for index, _ in enumerate(sorted(self.pins.values(),
                                         key=lambda item: item.request_id)):
            names.extend(f"pins[{index}].{name}" for name in _PIN_NAMES)
            names.extend(f"pins[{index}].payload.{name}"
                         for name in _PAYLOAD_NAMES)
            names.extend(f"pins[{index}].prior.{name}"
                         for name in _RECORD_SCALAR_NAMES)
        names.append("release_waiter_count")
        for index, _ in enumerate(sorted(
                self.release_waiters.values(),
                key=lambda item: item.request_id)):
            names.extend(f"release_waiters[{index}].{name}"
                         for name in _WAITER_NAMES)
        names.append("append_count")
        for index, _ in enumerate(sorted(self.appends.values(),
                                         key=lambda item: item.request_id)):
            names.extend(f"appends[{index}].{name}" for name in _APPEND_NAMES)
        names.append("evicting_count")
        for index, _ in enumerate(sorted(self.evicting.items())):
            names.extend(f"evicting[{index}].{name}"
                         for name in _EVICTING_NAMES)
        names.append("tombstone_count")
        for index, _ in enumerate(sorted(self.tombstones.items())):
            names.extend(f"tombstones[{index}].{name}"
                         for name in _TOMBSTONE_NAMES)
        return names

    def decision_field_names(self, result: KvEdgeResult) -> list:
        names = ["commit_tick"]

        def block(tag, fields):
            names.extend(f"{tag}.{field}" for field in fields)

        for request_id in sorted(result.admissions):
            block(f"admissions[{request_id}]",
                  ("kind", "request_id", "outcome"))
        for request_id in sorted(result.promotions):
            block(f"promotions[{request_id}]",
                  ("kind", "request_id", "outcome"))
        for request_id in sorted(result.waits):
            block(f"waits[{request_id}]", ("kind", "request_id", "reason"))
        for request_id in sorted(result.releases):
            block(f"releases[{request_id}]",
                  ("kind", "request_id", "outcome"))
        for event in sorted(result.evictions_started,
                            key=lambda item: (item.session_id, item.kv_handle,
                                              item.generation)):
            block(f"evictions_started[{event.session_id}]",
                  ("kind", "session_id", "kv_handle", "generation",
                   "prior_state"))
        for event in sorted(result.evictions_completed,
                            key=lambda item: (item.session_id, item.kv_handle,
                                              item.generation)):
            block(f"evictions_completed[{event.session_id}]",
                  ("kind", "session_id", "kv_handle", "generation",
                   "prior_state", "diagnostic_prefix_tokens",
                   "diagnostic_prefix_bytes"))
        for request_id in sorted(result.rollbacks):
            block(f"rollbacks[{request_id}]", ("kind", "request_id", "kind"))
        for request_id in sorted(result.terminals):
            block(f"terminals[{request_id}]",
                  ("kind", "request_id", "state", "cached_tokens",
                   "valid_bytes", "status", "source", "has_content_digest",
                   "contract[0]", "contract[1]", "contract[2]",
                   "contract[3]"))
        for request_id in sorted(result.handoffs):
            block(f"handoffs[{request_id}]", ("kind", "request_id", "serial"))
        for key in sorted(result.views):
            block(f"views[{key[0]},{key[1]}]",
                  ("kind", "request_id", "member_ordinal", "view_epoch",
                   "slot_id", "slot_base", "slot_bytes",
                   "valid_bytes_at_arm"))
        for request_id in sorted(result.ownerless_cancels):
            block(f"ownerless_cancels[{request_id}]", ("kind", "request_id"))
        return names




_RECORD_SCALAR_NAMES = (
    "session_id", "kv_handle", "generation", "state", "has_slot", "slot_id",
    "cached_tokens", "valid_bytes", "view_epoch", "last_use_epoch",
    "pin_count", "admission_claim_count", "outstanding_kv_dma",
    "has_content_digest", "diagnostic_prefix_tokens",
    "diagnostic_prefix_bytes", "has_diagnostic_digest",
    "contract[0]", "contract[1]", "contract[2]", "contract[3]",
    "content[0]", "content[1]", "content[2]", "content[3]",
    "diag[0]", "diag[1]", "diag[2]", "diag[3]",
    "has_first_error", "first_error_tick", "first_error_code",
    "source[0]", "source[1]", "source[2]", "source[3]", "source[4]",
)
_EMPTY_RECORD_SCALARS = [0] * len(_RECORD_SCALAR_NAMES)
_WAITER_ENTRY_NAMES = ("request_id", "session_id", "kv_handle", "generation",
                       "flags", "deadline_or_max", "qos", "ready_tick",
                       "required_cached_tokens",
                       "required_tokens_after_round", "initial", "claimed",
                       "intent", "policy_error", "prior_absent")
_PIN_NAMES = ("request_id", "session_id", "kv_handle", "generation",
              "flags", "required_cached_tokens",
              "required_tokens_after_round", "phase")
_PAYLOAD_NAMES = ("serial", "intent", "prior_absent")
_WAITER_NAMES = ("request_id", "session_id", "kv_handle", "generation")
_APPEND_NAMES = ("request_id", "session_id", "kv_handle", "generation",
                 "base_tokens", "append_tokens", "base_byte", "bytes")
_EVICTING_NAMES = ("session_id", "kv_handle", "generation", "prior_state",
                   "cached_tokens", "valid_bytes")
_TOMBSTONE_NAMES = ("session_id", "kv_handle", "next_generation")


def _digest_scalars(digest: bytes) -> list:
    return list(struct.unpack("<4Q", digest))


def _error_source_scalars(source: ErrorSourceKey) -> list:
    return list(struct.unpack("<5Q", source.wire_bytes()))


def _record_scalars(record) -> list:
    if record is None:
        return list(_EMPTY_RECORD_SCALARS)
    return _record_scalars_value(record)


def _record_scalars_value(record: KvRecord) -> list:
    out = [record.session_id, record.kv_handle, record.generation,
           int(record.state),
           1 if record.slot_id is not None else 0,
           NO_SLOT if record.slot_id is None else record.slot_id,
           record.cached_tokens, record.valid_bytes, record.view_epoch,
           record.last_use_epoch, record.pin_count,
           record.admission_claim_count, record.outstanding_kv_dma,
           1 if record.content_digest is not None else 0,
           record.diagnostic_prefix_tokens, record.diagnostic_prefix_bytes,
           1 if record.diagnostic_prefix_digest is not None else 0]
    out.extend(_digest_scalars(record.contract_digest))
    out.extend(_digest_scalars(record.content_digest)
               if record.content_digest is not None else [0, 0, 0, 0])
    out.extend(_digest_scalars(record.diagnostic_prefix_digest)
               if record.diagnostic_prefix_digest is not None
               else [0, 0, 0, 0])
    if record.first_error is None:
        out.extend([0, 0, 0] + [0] * 5)
    else:
        out.extend([1, record.first_error.tick, record.first_error.code])
        out.extend(_error_source_scalars(record.first_error.source))
    return out

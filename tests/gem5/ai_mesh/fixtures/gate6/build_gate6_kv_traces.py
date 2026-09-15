"""Emit the Gate 6 R2 cross-language KV trace fixtures.

The Python oracle produces the expected per-edge decision and state
projection; the C++ ``MeshKvManager`` replays the same event sequence and must
reproduce it scalar by scalar. The writer re-reads its own file and replays it
through the oracle again, so the wire layout cannot drift from the model.
"""

import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "util/mesh_ir"))

from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.kv_oracle import KvOracle
from mesh_ir.kv_types import (
    ALLOW_REPREFILL,
    KvAdmissionIntent,
    KvAppendArm,
    KvAppendTerminal,
    KvCapacity,
    KvDmaCount,
    KvEdge,
    KvErrorCandidate,
    KvFaultEvent,
    KvGeometry,
    KvLaterFault,
    KvOwnerTerminal,
    KvPersistentState,
    KvRecord,
    KvReleaseIntent,
    KvState,
    KvStatus,
    KvViewArm,
    kv_contract_digest,
)
from mesh_ir.moe_weight_cache import ErrorSourceKey

BYTES_PER_TOKEN = 64
SLOT_BYTES = 4096
CONTRACTS = (
    kv_contract_digest(bytes(range(32)), bytes(range(32, 64)),
                       bytes(range(64, 96)), BYTES_PER_TOKEN),
    kv_contract_digest(bytes(range(1, 33)), bytes(range(32, 64)),
                       bytes(range(64, 96)), BYTES_PER_TOKEN),
)
NO_SLOT = 0xFFFFFFFF
NO_ROLLBACK = 0

KIND_ADMISSION = 0
KIND_RELEASE = 1
KIND_OWNER_TERMINAL = 2
KIND_LATER_FAULT = 3
KIND_CORE_START = 4
KIND_APPEND_ARM = 5
KIND_APPEND_TERMINAL = 6
KIND_DMA_ACCEPT = 7
KIND_DMA_TERMINAL = 8
KIND_FAULT = 9
KIND_VIEW_ARM = 10
EVENT_WORDS = 12
RECORD_WORDS = 40


def geometry(sessions=4):
    return KvGeometry(region_base=0x100000, slot_bytes=SLOT_BYTES,
                      slot_alignment=SLOT_BYTES, max_sessions=sessions,
                      bytes_per_token=BYTES_PER_TOKEN)


def capacity(records=4, tombstones=2, waiters=4, releases=2):
    return KvCapacity(record_entries=records, tombstone_entries=tombstones,
                      admission_wait_entries=waiters,
                      release_waiter_entries=releases)


def admit(request_id, session_id, generation=1, flags=0, qos=0,
          ready_tick=None, deadline=0, contract=0, cached=0, after_round=0):
    return KvAdmissionIntent(
        request_id=request_id, session_id=session_id, kv_handle=session_id,
        generation=generation, contract_digest=CONTRACTS[contract],
        deadline_or_max=deadline, qos=qos,
        ready_tick=request_id if ready_tick is None else ready_tick,
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


def record(session_id=7, generation=1, state=KvState.EVICTED, slot_id=None,
           cached=0, view_epoch=0, last_use=0, contract=0, digest=None,
           diag_tokens=0, diag_digest=None):
    return KvRecord(
        session_id=session_id, kv_handle=session_id, generation=generation,
        contract_digest=CONTRACTS[contract], state=state, slot_id=slot_id,
        cached_tokens=cached, valid_bytes=cached * BYTES_PER_TOKEN,
        content_digest=digest, view_epoch=view_epoch, last_use_epoch=last_use,
        pin_count=0, admission_claim_count=0, outstanding_kv_dma=0,
        first_error=None, diagnostic_prefix_tokens=diag_tokens,
        diagnostic_prefix_bytes=diag_tokens * BYTES_PER_TOKEN,
        diagnostic_prefix_digest=diag_digest)


def persistent(records, slots, next_epoch, tombstones=()):
    owners = [None] * len(slots)
    for index, slot in enumerate(slots):
        owners[index] = slot
    return KvPersistentState(tuple(owners), tuple(records), tuple(tombstones),
                             next_epoch)


# -------------------------------------------------------------- scenarios

ROLLBACK_SOURCES = {}


def rollback(edge_index, request_id, source):
    return KvOwnerTerminal(request_id, KvStatus.CANCELLED, strict=False)


def scenario_lifecycle():
    return persistent([], [None] * 4, 1), [
        KvEdge(tick=1, admissions=[admit(1, 11, after_round=8)]),
        KvEdge(tick=2),
        KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 8)]),
        KvEdge(tick=4, append_terminals=[KvAppendTerminal(1, (True,) * 8)]),
        KvEdge(tick=5, view_arms=[KvViewArm(1, 0), KvViewArm(1, 1)]),
        KvEdge(tick=6, core_starts=[1]),
        KvEdge(tick=7, owner_terminals=[KvOwnerTerminal(1, KvStatus.SUCCESS)]),
        KvEdge(tick=8, releases=[release(9, 11)]),
        KvEdge(tick=9, admissions=[admit(2, 11)]),
    ]


def scenario_two_waiters():
    return persistent([], [None], 1), [
        KvEdge(tick=1, admissions=[admit(1, 11)]),
        KvEdge(tick=2, admissions=[admit(2, 22)]),
        KvEdge(tick=3, admissions=[admit(3, 33, qos=7)]),
        KvEdge(tick=4, owner_terminals=[KvOwnerTerminal(1, KvStatus.SUCCESS)]),
        KvEdge(tick=5),
    ]


def scenario_cancel_paths():
    initial = persistent(
        [record(session_id=7, state=KvState.EVICTED, view_epoch=7, last_use=3),
         record(session_id=11, state=KvState.RESIDENT, slot_id=0, cached=8,
                view_epoch=4, last_use=2)],
        [(11, 11)], 4)
    ROLLBACK_SOURCES[("cancel_paths", 5, 3)] = 3
    return initial, [
        KvEdge(tick=1, admissions=[admit(1, 7, flags=ALLOW_REPREFILL)],
               owner_terminals=[KvOwnerTerminal(1, KvStatus.CANCELLED,
                                                strict=False)]),
        KvEdge(tick=2, admissions=[admit(2, 7, flags=ALLOW_REPREFILL)]),
        KvEdge(tick=3, owner_terminals=[KvOwnerTerminal(2, KvStatus.CANCELLED,
                                                        strict=False)]),
        KvEdge(tick=4, admissions=[admit(3, 7, flags=ALLOW_REPREFILL)]),
        KvEdge(tick=5, owner_terminals=[KvOwnerTerminal(3, KvStatus.CANCELLED,
                                                        strict=False)]),
        KvEdge(tick=6, admissions=[admit(4, 7, flags=ALLOW_REPREFILL)]),
        KvEdge(tick=7, core_starts=[4]),
        KvEdge(tick=8, append_arms=[KvAppendArm(4, 0, 8)]),
        KvEdge(tick=9, append_terminals=[KvAppendTerminal(4, (True,) * 8)]),
        KvEdge(tick=10, owner_terminals=[KvOwnerTerminal(4, KvStatus.CANCELLED,
                                                         strict=False)]),
    ]


def scenario_eviction_cycle():
    initial = persistent(
        [record(session_id=11, state=KvState.ERROR, slot_id=0, cached=4,
                view_epoch=2, last_use=9, digest=b"\x11" * 32),
         record(session_id=22, state=KvState.RESIDENT, slot_id=1, cached=8,
                view_epoch=5, last_use=1)],
        [(11, 11), (22, 22)], 10)
    return initial, [
        KvEdge(tick=1, admissions=[admit(1, 33, after_round=8)]),
        KvEdge(tick=2, admissions=[admit(2, 44)]),
    ]


def scenario_reprefill_rollback():
    initial = persistent(
        [record(session_id=7, state=KvState.EVICTED, view_epoch=7, last_use=3)],
        [None], 4)
    ROLLBACK_SOURCES[("reprefill_rollback", 2, 1)] = 1
    return initial, [
        KvEdge(tick=1, admissions=[admit(1, 7, flags=ALLOW_REPREFILL,
                                         after_round=8)]),
        KvEdge(tick=2, owner_terminals=[KvOwnerTerminal(1, KvStatus.CANCELLED,
                                                        strict=False)]),
        KvEdge(tick=3, admissions=[admit(2, 7, flags=ALLOW_REPREFILL,
                                         after_round=9)]),
        KvEdge(tick=4, core_starts=[2]),
        KvEdge(tick=5, append_arms=[KvAppendArm(2, 0, 9)]),
        KvEdge(tick=6, append_terminals=[KvAppendTerminal(2, (True,) * 9)]),
        KvEdge(tick=7, later_faults=[KvLaterFault(2, candidate(7))]),
    ]


def scenario_release_pending():
    return persistent([], [None] * 2, 1), [
        KvEdge(tick=1, admissions=[admit(1, 11, after_round=6)]),
        KvEdge(tick=2),
        KvEdge(tick=3, releases=[release(9, 11)]),
        KvEdge(tick=4, admissions=[admit(2, 11, flags=ALLOW_REPREFILL)]),
        KvEdge(tick=5, append_arms=[KvAppendArm(1, 0, 6)]),
        KvEdge(tick=6, append_terminals=[KvAppendTerminal(1, (True,) * 6)]),
        KvEdge(tick=7, owner_terminals=[KvOwnerTerminal(1, KvStatus.SUCCESS)]),
        KvEdge(tick=8, admissions=[admit(3, 11)]),
    ]


def scenario_capacity_backpressure():
    return persistent([], [None], 1), [
        KvEdge(tick=1, admissions=[admit(1, 11)]),
        KvEdge(tick=2, admissions=[admit(2, 22)]),
        KvEdge(tick=3, admissions=[admit(3, 33, qos=9)]),
        KvEdge(tick=4, admissions=[admit(3, 33, qos=0)]),
        KvEdge(tick=5, releases=[release(8, 22)]),
        KvEdge(tick=6, owner_terminals=[KvOwnerTerminal(1, KvStatus.SUCCESS)]),
        KvEdge(tick=7),
    ]


def scenario_permutation_stable():
    return persistent([], [None] * 2, 1), [
        KvEdge(tick=1, admissions=[admit(1, 11)]),
        KvEdge(tick=2, admissions=[admit(2, 22)]),
        KvEdge(tick=3, owner_terminals=[KvOwnerTerminal(2, KvStatus.SUCCESS)]),
        KvEdge(tick=4, admissions=[admit(3, 33)], releases=[release(9, 22)],
               owner_terminals=[KvOwnerTerminal(1, KvStatus.CANCELLED,
                                                strict=False)]),
        KvEdge(tick=5, releases=[release(9, 22)],
               owner_terminals=[KvOwnerTerminal(1, KvStatus.CANCELLED,
                                                strict=False)],
               admissions=[admit(3, 33)]),
        KvEdge(tick=6, owner_terminals=[KvOwnerTerminal(3, KvStatus.SUCCESS)]),
    ]


def scenario_faults_and_views():
    return persistent([], [None] * 2, 1), [
        KvEdge(tick=1, admissions=[admit(1, 11, after_round=8)]),
        KvEdge(tick=2, dma_accepts=[KvDmaCount(1, 2)]),
        KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 6)]),
        KvEdge(tick=4, dma_terminals=[KvDmaCount(1, 2)],
               faults=[KvFaultEvent(1, candidate(4, ordinal=1))]),
        KvEdge(tick=5, view_arms=[KvViewArm(1, 0)]),
        KvEdge(tick=6, append_terminals=[KvAppendTerminal(
            1, (False,) + (True,) * 5)]),
        KvEdge(tick=7, later_faults=[KvLaterFault(1, candidate(7, ordinal=0))]),
        KvEdge(tick=8, releases=[release(9, 11)]),
        KvEdge(tick=9),
    ]


def scenario_record_capacity_wait():
    return persistent([], [None] * 2, 1), [
        KvEdge(tick=1, admissions=[admit(1, 11), admit(2, 22)]),
        KvEdge(tick=2),
        KvEdge(tick=3),
        KvEdge(tick=4, owner_terminals=[KvOwnerTerminal(1, KvStatus.SUCCESS)]),
        KvEdge(tick=5, releases=[release(9, 11)]),
        KvEdge(tick=6),
        KvEdge(tick=7),
    ]


def scenario_waiting_head_drain():
    initial = persistent(
        [record(session_id=7, state=KvState.RESIDENT, slot_id=0,
                view_epoch=3, last_use=2)],
        [(7, 7)], 3)
    return initial, [
        KvEdge(tick=1, admissions=[admit(1, 7, flags=ALLOW_REPREFILL)]),
        KvEdge(tick=2, admissions=[admit(2, 7, flags=ALLOW_REPREFILL,
                                         ready_tick=2)]),
        KvEdge(tick=3, admissions=[admit(2, 7, flags=ALLOW_REPREFILL,
                                         ready_tick=99)]),
        KvEdge(tick=4, owner_terminals=[KvOwnerTerminal(1, KvStatus.SUCCESS)]),
        KvEdge(tick=5),
    ]


def scenario_pin_phases():
    return persistent([], [None], 1), [
        KvEdge(tick=1, admissions=[admit(1, 11, after_round=2)]),
        KvEdge(tick=2, core_starts=[1]),
        KvEdge(tick=3, append_arms=[KvAppendArm(1, 0, 2)]),
        KvEdge(tick=4, append_terminals=[KvAppendTerminal(1, (True, True))]),
        KvEdge(tick=5, owner_terminals=[KvOwnerTerminal(1, KvStatus.SUCCESS)]),
        KvEdge(tick=6, releases=[release(9, 11)]),
    ]


def scenario_duplicate_identity():
    return persistent([], [None] * 2, 1), [
        KvEdge(tick=1, admissions=[admit(1, 11)]),
        KvEdge(tick=2, admissions=[admit(1, 22)]),
    ]


SCENARIOS = (
    ("lifecycle", scenario_lifecycle, 4, (4, 2, 4, 2)),
    ("two_waiters", scenario_two_waiters, 1, (4, 2, 2, 2)),
    ("cancel_paths", scenario_cancel_paths, 1, (4, 2, 4, 2)),
    ("eviction_cycle", scenario_eviction_cycle, 2, (4, 2, 4, 2)),
    ("reprefill_rollback", scenario_reprefill_rollback, 1, (4, 2, 4, 2)),
    ("release_pending", scenario_release_pending, 2, (4, 2, 4, 2)),
    ("capacity_backpressure", scenario_capacity_backpressure, 1, (1, 1, 1, 1)),
    ("permutation_stable", scenario_permutation_stable, 2, (4, 2, 4, 2)),
    ("faults_and_views", scenario_faults_and_views, 2, (4, 2, 4, 2)),
    ("record_capacity_wait", scenario_record_capacity_wait, 2, (1, 2, 2, 2)),
    ("waiting_head_drain", scenario_waiting_head_drain, 1, (4, 2, 4, 2)),
    ("pin_phases", scenario_pin_phases, 1, (2, 2, 2, 2)),
    ("duplicate_identity", scenario_duplicate_identity, 2, (2, 2, 2, 2)),
)


# --------------------------------------------------------------- encoding

def encode_edge(edge, name, edge_index):
    events = []

    def emit(kind, words):
        events.append([kind] + list(words) +
                      [0] * (EVENT_WORDS - 1 - len(words)))

    for intent in edge.admissions:
        emit(KIND_ADMISSION, [
            intent.request_id, intent.session_id, intent.kv_handle,
            intent.generation, intent.flags,
            CONTRACTS.index(intent.contract_digest), intent.deadline_or_max,
            intent.qos, intent.ready_tick, intent.required_cached_tokens,
            intent.required_tokens_after_round])
    for intent in edge.releases:
        emit(KIND_RELEASE, [intent.request_id, intent.session_id,
                            intent.kv_handle, intent.generation])
    for terminal in edge.owner_terminals:
        source = ROLLBACK_SOURCES.get((name, edge_index, terminal.request_id),
                                      NO_ROLLBACK)
        emit(KIND_OWNER_TERMINAL, [terminal.request_id, int(terminal.status),
                                   1 if terminal.strict else 0, source])
    for later in edge.later_faults:
        emit(KIND_LATER_FAULT, [later.request_id, later.candidate.tick,
                                later.candidate.source.ordinal,
                                later.candidate.code])
    for request_id in edge.core_starts:
        emit(KIND_CORE_START, [request_id])
    for arm in edge.append_arms:
        emit(KIND_APPEND_ARM, [arm.request_id, arm.base_tokens,
                               arm.append_tokens])
    for terminal in edge.append_terminals:
        bits = 0
        for index, ok in enumerate(terminal.tokens_ok):
            if ok:
                bits |= 1 << index
        emit(KIND_APPEND_TERMINAL, [terminal.request_id, bits,
                                    len(terminal.tokens_ok)])
    for accept in edge.dma_accepts:
        emit(KIND_DMA_ACCEPT, [accept.request_id, accept.count])
    for terminal in edge.dma_terminals:
        emit(KIND_DMA_TERMINAL, [terminal.request_id, terminal.count])
    for fault in edge.faults:
        emit(KIND_FAULT, [fault.request_id, fault.candidate.tick,
                          fault.candidate.source.ordinal,
                          fault.candidate.code])
    for arm in edge.view_arms:
        emit(KIND_VIEW_ARM, [arm.request_id, arm.member_ordinal])
    return events


def decode_edge(events, handoffs=None):
    edge = KvEdge()
    for words in events:
        kind = words[0]
        if kind == KIND_ADMISSION:
            edge.admissions.append(KvAdmissionIntent(
                request_id=words[1], session_id=words[2], kv_handle=words[3],
                generation=words[4], contract_digest=CONTRACTS[words[6]],
                deadline_or_max=words[7], qos=words[8], ready_tick=words[9],
                flags=words[5], required_cached_tokens=words[10],
                required_tokens_after_round=words[11]))
        elif kind == KIND_RELEASE:
            edge.releases.append(KvReleaseIntent(
                words[1], words[2], words[3], words[4]))
        elif kind == KIND_OWNER_TERMINAL:
            rollback = None
            if words[4]:
                assert handoffs is not None
                rollback = handoffs[words[4]]
            edge.owner_terminals.append(KvOwnerTerminal(
                words[1], KvStatus(words[2]), rollback, bool(words[3])))
        elif kind == KIND_LATER_FAULT:
            edge.later_faults.append(KvLaterFault(
                words[1], _candidate(words[2], words[3], words[4])))
        elif kind == KIND_CORE_START:
            edge.core_starts.append(words[1])
        elif kind == KIND_APPEND_ARM:
            edge.append_arms.append(KvAppendArm(words[1], words[2], words[3]))
        elif kind == KIND_APPEND_TERMINAL:
            edge.append_terminals.append(KvAppendTerminal(
                words[1], tuple(bool(words[2] >> index & 1)
                                for index in range(words[3]))))
        elif kind == KIND_DMA_ACCEPT:
            edge.dma_accepts.append(KvDmaCount(words[1], words[2]))
        elif kind == KIND_DMA_TERMINAL:
            edge.dma_terminals.append(KvDmaCount(words[1], words[2]))
        elif kind == KIND_FAULT:
            edge.faults.append(KvFaultEvent(
                words[1], _candidate(words[2], words[3], words[4])))
        elif kind == KIND_VIEW_ARM:
            edge.view_arms.append(KvViewArm(words[1], words[2]))
        else:
            raise ValueError(f"unknown event kind {kind}")
    return edge


def _candidate(tick, ordinal, code):
    return KvErrorCandidate(
        tick, ErrorSourceKey(error_class=A.MOE_ERROR_CLASS.DMA_AXI,
                             core_id_or_ffff=0xFFFF,
                             domain=A.MESH_OBJECT_DOMAIN.KV_RUNTIME,
                             object_kind=A.MESH_OBJECT_KIND.KV_APPEND,
                             region_group_id=0, region_id=0, ordinal=ordinal,
                             generation=0), code)


def encode_record(record):
    def digest_words(value):
        if value is None:
            return [0, 0, 0, 0]
        return list(struct.unpack("<4Q", value))

    source = record.first_error.source if record.first_error else None
    words = [
        record.session_id, record.kv_handle, record.generation,
        int(record.state), 1 if record.slot_id is not None else 0,
        NO_SLOT if record.slot_id is None else record.slot_id,
        record.cached_tokens, record.valid_bytes,
        1 if record.content_digest is not None else 0, record.view_epoch,
        record.last_use_epoch, record.pin_count,
        record.admission_claim_count, record.outstanding_kv_dma,
        record.diagnostic_prefix_tokens, record.diagnostic_prefix_bytes,
        1 if record.diagnostic_prefix_digest is not None else 0,
        1 if record.first_error is not None else 0,
        record.first_error.tick if record.first_error else 0,
        record.first_error.code if record.first_error else 0,
    ]
    words += list(struct.unpack("<5Q", source.wire_bytes())) if source else \
        [0] * 5
    words += digest_words(record.contract_digest)
    words += digest_words(record.content_digest)
    words += digest_words(record.diagnostic_prefix_digest)
    words += [0] * (RECORD_WORDS - len(words))
    return words


def decode_record(words):
    def digest(value):
        if not value[0]:
            return None
        return struct.pack("<4Q", *value[1:5])

    source = None
    if words[17]:
        source = ErrorSourceKey.from_wire_bytes(
            struct.pack("<5Q", *words[20:25]))
    return KvRecord(
        session_id=words[0], kv_handle=words[1], generation=words[2],
        contract_digest=struct.pack("<4Q", *words[25:29]),
        state=KvState(words[3]),
        slot_id=None if words[4] == 0 else words[5],
        cached_tokens=words[6], valid_bytes=words[7],
        content_digest=digest([words[8]] + words[29:33]),
        view_epoch=words[9], last_use_epoch=words[10], pin_count=words[11],
        admission_claim_count=words[12], outstanding_kv_dma=words[13],
        first_error=None if not words[17] else KvErrorCandidate(
            words[18], source, words[19]),
        diagnostic_prefix_tokens=words[14],
        diagnostic_prefix_bytes=words[15],
        diagnostic_prefix_digest=digest([words[16]] + words[33:37]))


def encode_persistent(state, max_sessions):
    out = bytearray()
    out += struct.pack("<I", max_sessions)
    for owner in state.slot_owners:
        out += struct.pack("<QQQ", owner[0] if owner else 0,
                           owner[1] if owner else 0, 1 if owner else 0)
    out += struct.pack("<I", len(state.records))
    for record in state.records:
        out += struct.pack("<" + "Q" * RECORD_WORDS, *encode_record(record))
    out += struct.pack("<I", len(state.tombstones))
    for key, generation in state.tombstones:
        out += struct.pack("<QQQ", key[0], key[1], generation)
    out += struct.pack("<Q", state.next_kv_use_epoch)
    return bytes(out)


def decode_persistent(data, offset):
    max_sessions, = struct.unpack_from("<I", data, offset)
    offset += 4
    owners = []
    for _ in range(max_sessions):
        session, handle, present = struct.unpack_from("<QQQ", data, offset)
        offset += 24
        owners.append((session, handle) if present else None)
    count, = struct.unpack_from("<I", data, offset)
    offset += 4
    records = []
    for _ in range(count):
        words = struct.unpack_from("<" + "Q" * RECORD_WORDS, data, offset)
        offset += RECORD_WORDS * 8
        records.append(decode_record(list(words)))
    tombstone_count, = struct.unpack_from("<I", data, offset)
    offset += 4
    tombstones = []
    for _ in range(tombstone_count):
        session, handle, generation = struct.unpack_from("<QQQ", data, offset)
        offset += 24
        tombstones.append(((session, handle), generation))
    next_epoch, = struct.unpack_from("<Q", data, offset)
    offset += 8
    return KvPersistentState(tuple(owners), tuple(records),
                             tuple(tombstones), next_epoch), offset


def run_scenario(name, builder, sessions, capacity_fields):
    initial, edges = builder()
    geo = geometry(sessions)
    cap = capacity(*capacity_fields)
    oracle = KvOracle(geo, cap)
    oracle.load_persistent(initial)
    handoffs = {}
    encoded_edges = []
    expected = []
    names = []
    fatal_edges = 0
    for index, edge in enumerate(edges):
        events = encode_edge(edge, name, index + 1)
        decoded = decode_edge(events, handoffs)
        restored = KvOracle(geo, cap)
        restored.load_live(oracle.save_live())
        result = oracle.commit_edge(decoded)
        clone = restored.commit_edge(decoded)
        replay = oracle.validate()
        assert replay == (), (name, index + 1, replay)
        assert restored.validate() == (), (name, index + 1)
        assert restored.edge_scalars(clone) == oracle.edge_scalars(result)
        assert restored.save_live() == oracle.save_live()
        encoded_edges.append(events)
        expected.append(oracle.edge_scalars(result))
        names.append(oracle.decision_field_names(result) +
                     oracle.scalar_field_names())
        if result.fatal is not None:
            fatal_edges += 1
            break
        for request_id, payload in result.handoffs.items():
            handoffs[request_id] = payload
    return geo, cap, initial, encoded_edges, expected, names, fatal_edges


def write_trace(path):
    out = bytearray()
    out += b"KVT1"
    out += struct.pack("<II", 1, len(SCENARIOS))
    for name, builder, sessions, capacity_fields in SCENARIOS:
        geo, cap, initial, edges, expected, names, fatals = run_scenario(
            name, builder, sessions, capacity_fields)
        encoded = name.encode("ascii")
        out += struct.pack("<I", len(encoded)) + encoded
        out += struct.pack("<QQQII", geo.region_base, geo.slot_bytes,
                           geo.slot_alignment, geo.max_sessions,
                           geo.bytes_per_token)
        out += struct.pack("<IIII", cap.record_entries, cap.tombstone_entries,
                           cap.admission_wait_entries,
                           cap.release_waiter_entries)
        out += struct.pack("<I", len(CONTRACTS))
        for contract in CONTRACTS:
            out += contract
        out += encode_persistent(initial, geo.max_sessions)
        out += struct.pack("<II", len(edges), fatals)
        for index, events in enumerate(edges):
            out += struct.pack("<I", len(events))
            for words in events:
                out += struct.pack("<" + "Q" * EVENT_WORDS, *words)
            scalars = expected[index]
            out += struct.pack("<I", len(scalars))
            out += struct.pack("<" + "Q" * len(scalars), *scalars)
            out += struct.pack("<I", len(names[index]))
            for field in names[index]:
                field_bytes = field.encode("ascii")
                out += struct.pack("<I", len(field_bytes)) + field_bytes
    path.write_bytes(bytes(out))
    return len(out)


def read_trace(path):
    data = path.read_bytes()
    assert data[:4] == b"KVT1", "trace magic mismatch"
    offset = 4
    version, count = struct.unpack_from("<II", data, offset)
    offset += 8
    assert version == 1, "trace version mismatch"
    scenarios = []
    for _ in range(count):
        length, = struct.unpack_from("<I", data, offset)
        offset += 4
        name = data[offset:offset + length].decode("ascii")
        offset += length
        fields = struct.unpack_from("<QQQII", data, offset)
        offset += struct.calcsize("<QQQII")
        cap_fields = struct.unpack_from("<IIII", data, offset)
        offset += 16
        geo = KvGeometry(fields[0], fields[1], fields[2], fields[3], fields[4])
        cap = KvCapacity(*cap_fields)
        contracts, = struct.unpack_from("<I", data, offset)
        offset += 4
        digests = []
        for _ in range(contracts):
            digests.append(data[offset:offset + 32])
            offset += 32
        initial, offset = decode_persistent(data, offset)
        edge_count, fatal_edges = struct.unpack_from("<II", data, offset)
        offset += 8
        edges = []
        for _ in range(edge_count):
            event_count, = struct.unpack_from("<I", data, offset)
            offset += 4
            events = []
            for _ in range(event_count):
                words = struct.unpack_from("<" + "Q" * EVENT_WORDS, data,
                                           offset)
                offset += EVENT_WORDS * 8
                events.append(list(words))
            size, = struct.unpack_from("<I", data, offset)
            offset += 4
            scalars = list(struct.unpack_from("<" + "Q" * size, data, offset))
            offset += size * 8
            name_count, = struct.unpack_from("<I", data, offset)
            offset += 4
            names = []
            for _ in range(name_count):
                field_size, = struct.unpack_from("<I", data, offset)
                offset += 4
                names.append(data[offset:offset + field_size].decode("ascii"))
                offset += field_size
            edges.append((events, scalars, names))
        assert digests == list(CONTRACTS)
        scenarios.append((name, geo, cap, initial, edges, fatal_edges))
    assert offset == len(data), "trace has trailing bytes"
    return scenarios


def verify_round_trip(path):
    for name, geo, cap, initial, edges, fatal_edges in read_trace(path):
        oracle = KvOracle(geo, cap)
        oracle.load_persistent(initial)
        handoffs = {}
        seen_fatals = 0
        for index, (events, scalars, names) in enumerate(edges):
            decoded = decode_edge(events, handoffs)
            result = oracle.commit_edge(decoded)
            assert oracle.edge_scalars(result) == scalars, (name, index + 1)
            assert oracle.decision_field_names(result) + \
                oracle.scalar_field_names() == names, (name, index + 1)
            if result.fatal is not None:
                seen_fatals += 1
                break
            for request_id, payload in result.handoffs.items():
                handoffs[request_id] = payload
        assert seen_fatals == fatal_edges, name


def main():
    path = Path(__file__).resolve().parent / "kv_trace.bin"
    size = write_trace(path)
    verify_round_trip(path)
    print(f"wrote {path.name} ({size} bytes, {len(SCENARIOS)} scenarios)")


if __name__ == "__main__":
    main()

"""Value types and scalar rules for the KV admission contract."""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import struct

from mesh_ir.generated import agent_abi as AG
from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import ErrorSourceKey


KV_CONTRACT_DOMAIN = b"AI_MESH_KV_CONTRACT_V1\0"
SCHEMA_VERSION = 2
NO_SLOT = 0xFFFFFFFF
NO_INTENT = 0xFF
NO_POLICY = 0xFF
U32_MAX = 0xFFFFFFFF
U64_MAX = 0xFFFFFFFFFFFFFFFF

POLICY_E_KV_REUSE_REQUIRED = 0
POLICY_E_KV_TOKEN_MISMATCH = 1
POLICY_E_KV_STATE = 2


class KvState(enum.IntEnum):
    ALLOCATING = 0
    RESIDENT = 1
    EVICTING = 2
    EVICTED = 3
    ERROR = 4


class KvPath(enum.IntEnum):
    INITIAL_PREFILL = 0
    KV_REUSE = 1
    REPREFILL = 2


class KvPinPhase(enum.IntEnum):
    PRESTART = 0
    STARTED = 1


class KvSnapshotSource(enum.IntEnum):
    PIN = 0
    ADMISSION_CLAIM = 1


class KvStatus(enum.IntEnum):
    SUCCESS = 0
    CANCELLED = 1
    ERROR = 2


class KvReleaseOutcome(enum.IntEnum):
    SUCCESS = 0
    NOT_FOUND = 1
    STALE_GENERATION = 2
    BUSY = 3


class KvAdmissionOutcome(enum.IntEnum):
    WAITING = 0
    CLAIMED = 1
    BACKPRESSURE = 2
    E_SESSION_EXISTS = 3
    E_KV_SESSION_NOT_FOUND = 4
    E_KV_STALE_GENERATION = 5
    E_KV_CONTRACT_MISMATCH = 6
    E_KV_STATE = 7
    FLAG_COMBINATION = 8


class KvPromotionOutcome(enum.IntEnum):
    PINNED = 0
    WAITING_SLOT = 1
    E_KV_REUSE_REQUIRED = 2
    E_KV_TOKEN_MISMATCH = 3
    E_KV_STATE = 4


class KvWaitReason(enum.IntEnum):
    RECORD_TABLE_FULL = 0
    TUPLE_OWNER_ACTIVE = 1
    RELEASE_PENDING = 2
    RECORD_BUSY = 3


class KvRollbackKind(enum.IntEnum):
    INITIAL_ERROR_RECORD = 0
    RESTORE_PRIOR = 1


REQUIRE_KV_REUSE = AG.SQ_FLAGS.REQUIRE_KV_REUSE
ALLOW_REPREFILL = AG.SQ_FLAGS.ALLOW_REPREFILL
KV_FLAG_MASK = REQUIRE_KV_REUSE | ALLOW_REPREFILL


def derived_initial(flags: int) -> bool:
    return (flags & KV_FLAG_MASK) == 0


def legal_flags(flags) -> bool:
    return _is_int(flags) and not flags & ~KV_FLAG_MASK and \
        flags != KV_FLAG_MASK

_STATES = (KvState.ALLOCATING, KvState.RESIDENT, KvState.EVICTING,
           KvState.EVICTED, KvState.ERROR)
_PATHS = (KvPath.INITIAL_PREFILL, KvPath.KV_REUSE, KvPath.REPREFILL)


def kv_contract_digest(program_semantic_digest: bytes,
                       model_weight_image_digest: bytes,
                       kv_layout_digest: bytes,
                       kv_bytes_per_token: int) -> bytes:
    for digest in (program_semantic_digest, model_weight_image_digest,
                   kv_layout_digest):
        if len(digest) != 32:
            raise MeshIrError("E_ABI_BOUNDS",
                              "kv contract digest inputs must be 32 bytes")
    if kv_bytes_per_token == 0:
        raise MeshIrError("E_CAPACITY_PLAN", "kv_bytes_per_token must be > 0")
    return hashlib.sha256(
        KV_CONTRACT_DOMAIN + program_semantic_digest +
        model_weight_image_digest + kv_layout_digest +
        struct.pack("<I", kv_bytes_per_token)).digest()


def _checked_add(name: str, left: int, right: int, limit: int) -> int:
    value = left + right
    if value > limit:
        raise MeshIrError("E_CAPACITY_PLAN", f"{name} overflow")
    return value


def _checked_mul(name: str, left: int, right: int) -> int:
    value = left * right
    if value > U64_MAX:
        raise MeshIrError("E_CAPACITY_PLAN", f"{name} overflow")
    return value


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclasses.dataclass(frozen=True)
class KvGeometry:
    region_base: int
    slot_bytes: int
    slot_alignment: int
    max_sessions: int
    bytes_per_token: int

    def gaps(self) -> tuple:
        problems = []
        for name, value in (("kv_region_base", self.region_base),
                            ("kv_session_slot_bytes", self.slot_bytes),
                            ("kv_slot_alignment", self.slot_alignment),
                            ("kv_max_sessions", self.max_sessions),
                            ("kv_bytes_per_token", self.bytes_per_token)):
            if not _is_int(value) or value < 0:
                problems.append(f"{name} must be unsigned")
        if problems:
            return tuple(problems)
        if not self.slot_alignment or not self.max_sessions or \
                not self.bytes_per_token or not self.slot_bytes:
            return ("kv geometry fields must be positive",)
        if self.region_base % self.slot_alignment:
            problems.append("kv_region_base % kv_slot_alignment != 0")
        if self.slot_bytes % self.slot_alignment:
            problems.append("kv_session_slot_bytes % kv_slot_alignment != 0")
        if self.slot_bytes > U64_MAX // self.max_sessions:
            problems.append("kv_region_bytes overflow")
            return tuple(problems)
        if self.slot_bytes * self.max_sessions > U64_MAX - self.region_base:
            problems.append("kv_region_end overflow")
            return tuple(problems)
        return tuple(problems)

    @property
    def tokens_per_slot(self) -> int:
        return self.slot_bytes // self.bytes_per_token

    def slot_base(self, slot_id: int) -> int:
        return self.region_base + slot_id * self.slot_bytes


@dataclasses.dataclass(frozen=True)
class KvCapacity:
    record_entries: int
    tombstone_entries: int
    admission_wait_entries: int
    release_waiter_entries: int

    def gaps(self) -> tuple:
        problems = []
        for name, value in (("record", self.record_entries),
                            ("tombstone", self.tombstone_entries),
                            ("admission_wait", self.admission_wait_entries),
                            ("release_waiter", self.release_waiter_entries)):
            if not _is_int(value) or value <= 0:
                problems.append(f"kv {name} capacity must be positive")
        return tuple(problems)


@dataclasses.dataclass(frozen=True)
class KvErrorCandidate:
    tick: int
    source: ErrorSourceKey
    code: int

    def gaps(self) -> tuple:
        problems = []
        if not _is_int(self.tick) or not 0 <= self.tick <= U64_MAX:
            problems.append("error tick must be a u64")
        if not _is_int(self.code) or not 0 <= self.code <= U32_MAX:
            problems.append("error code must be a u32")
        if not isinstance(self.source, ErrorSourceKey):
            problems.append("error source must be a typed source key")
        else:
            problems.extend(self.source.gaps())
        return tuple(problems)

    def sort_key(self) -> tuple:
        return (self.tick,) + self.source.sort_key() + (self.code,)


@dataclasses.dataclass
class KvRecord:
    session_id: int
    kv_handle: int
    generation: int
    contract_digest: bytes
    state: int
    slot_id: int | None
    cached_tokens: int
    valid_bytes: int
    content_digest: bytes | None
    view_epoch: int
    last_use_epoch: int
    pin_count: int
    admission_claim_count: int
    outstanding_kv_dma: int
    first_error: KvErrorCandidate | None
    diagnostic_prefix_tokens: int
    diagnostic_prefix_bytes: int
    diagnostic_prefix_digest: bytes | None

    @property
    def tuple(self) -> tuple:
        return (self.session_id, self.kv_handle)

    def copy(self) -> "KvRecord":
        return dataclasses.replace(self)

    def eligible_for_eviction(self, append_live: bool,
                              release_pending: bool) -> bool:
        return (self.state in (KvState.RESIDENT, KvState.ERROR) and
                self.slot_id is not None and self.pin_count == 0 and
                self.admission_claim_count == 0 and
                self.outstanding_kv_dma == 0 and not append_live and
                not release_pending)

    def victim_key(self) -> tuple:
        priority = 0 if self.state == KvState.ERROR else 1
        return (priority, self.last_use_epoch, self.session_id,
                self.kv_handle, self.generation)

    def gaps(self, geometry: KvGeometry, live: bool = True) -> tuple:
        problems = []
        for name, value in (("session_id", self.session_id),
                            ("kv_handle", self.kv_handle)):
            if not _is_int(value) or not 0 < value <= U64_MAX:
                problems.append(f"record {name} must be a nonzero u64")
        if not _is_int(self.generation) or \
                not 0 < self.generation <= U32_MAX:
            problems.append("record generation must be a nonzero u32")
        if not isinstance(self.contract_digest, bytes) or \
                len(self.contract_digest) != 32:
            problems.append("record contract digest must be 32 bytes")
        if not _is_int(self.state) or self.state not in _STATES:
            problems.append("record state is outside the closed set")
        for name, value, limit in (("cached_tokens", self.cached_tokens,
                                    U32_MAX),
                                   ("valid_bytes", self.valid_bytes,
                                    U64_MAX),
                                   ("view_epoch", self.view_epoch, U64_MAX),
                                   ("last_use_epoch", self.last_use_epoch,
                                    U64_MAX),
                                   ("pin_count", self.pin_count, U32_MAX),
                                   ("admission_claim_count",
                                    self.admission_claim_count, U32_MAX),
                                   ("outstanding_kv_dma",
                                    self.outstanding_kv_dma, U32_MAX),
                                   ("diagnostic_prefix_tokens",
                                    self.diagnostic_prefix_tokens, U32_MAX),
                                   ("diagnostic_prefix_bytes",
                                    self.diagnostic_prefix_bytes, U64_MAX)):
            if not _is_int(value) or not 0 <= value <= limit:
                problems.append(f"record {name} is outside its width")
        if self.slot_id is not None and (
                not _is_int(self.slot_id) or
                not 0 <= self.slot_id < geometry.max_sessions):
            problems.append("slot id outside the slot bitmap")
        if problems:
            return tuple(problems)
        if self.state == KvState.RESIDENT and self.slot_id is None:
            problems.append("RESIDENT requires a slot")
        if self.state == KvState.EVICTING:
            if self.slot_id is None:
                problems.append("EVICTING requires a slot")
            if self.pin_count or self.admission_claim_count or \
                    self.outstanding_kv_dma:
                problems.append("EVICTING must have no live owner")
        if self.state == KvState.EVICTED and (
                self.slot_id is not None or self.cached_tokens or
                self.valid_bytes or self.content_digest is not None):
            problems.append("EVICTED must hold no residency")
        if self.slot_id is None and (self.cached_tokens or self.valid_bytes):
            problems.append("slotless record must hold no residency")
        if self.cached_tokens > geometry.tokens_per_slot:
            problems.append("cached tokens exceed kv_tokens_per_slot")
        if self.valid_bytes != self.cached_tokens * geometry.bytes_per_token \
                or self.valid_bytes > U64_MAX:
            problems.append("valid_bytes != cached_tokens * bytes_per_token")
        if self.state == KvState.ALLOCATING and self.cached_tokens:
            problems.append("ALLOCATING must not hold cached tokens")
        if live and self.state == KvState.ALLOCATING and not self.pin_count \
                and not self.admission_claim_count:
            problems.append("ALLOCATING must hold a KV owner")
        if self.slot_id is None and self.view_epoch and \
                self.state not in (KvState.EVICTED, KvState.ERROR):
            problems.append("slotless record must keep view epoch 0")
        if self.content_digest is not None and (
                self.cached_tokens == 0 or
                not isinstance(self.content_digest, bytes) or
                len(self.content_digest) != 32):
            problems.append("content digest requires a committed prefix")
        if self.diagnostic_prefix_bytes != \
                self.diagnostic_prefix_tokens * geometry.bytes_per_token \
                or self.diagnostic_prefix_bytes > U64_MAX:
            problems.append("diagnostic prefix bytes mismatch")
        if self.diagnostic_prefix_digest is not None and (
                self.diagnostic_prefix_tokens == 0 or
                not isinstance(self.diagnostic_prefix_digest, bytes) or
                len(self.diagnostic_prefix_digest) != 32):
            problems.append("diagnostic digest requires a diagnostic prefix")
        if self.state != KvState.ERROR and (
                self.diagnostic_prefix_tokens or
                self.diagnostic_prefix_digest is not None):
            problems.append("diagnostic prefix only belongs to ERROR")
        if self.first_error is not None:
            if not isinstance(self.first_error, KvErrorCandidate):
                problems.append("first error must be a typed candidate")
            else:
                problems.extend(self.first_error.gaps())
        return tuple(problems)


@dataclasses.dataclass(frozen=True)
class KvRollbackPayload:
    serial: int
    intent: int
    prior_absent: bool
    prior: KvRecord


@dataclasses.dataclass(frozen=True)
class KvRollbackToken:
    request_id: int
    serial: int


@dataclasses.dataclass
class KvAdmissionIntent:
    request_id: int
    session_id: int
    kv_handle: int
    generation: int
    contract_digest: bytes
    deadline_or_max: int
    qos: int
    ready_tick: int
    flags: int
    required_cached_tokens: int
    required_tokens_after_round: int

    @property
    def tuple(self) -> tuple:
        return (self.session_id, self.kv_handle)

    def queue_key(self) -> tuple:
        return (self.deadline_or_max, 255 - self.qos, self.ready_tick,
                self.request_id)


@dataclasses.dataclass
class KvAdmissionWaiter:
    request_id: int
    session_id: int
    kv_handle: int
    generation: int
    contract_digest: bytes
    deadline_or_max: int
    qos: int
    ready_tick: int
    flags: int
    required_cached_tokens: int
    required_tokens_after_round: int
    initial: bool
    claimed: bool = False
    intent: int | None = None
    policy_error: int = NO_POLICY
    prior_absent: bool = False
    prior: KvRecord | None = None

    @property
    def tuple(self) -> tuple:
        return (self.session_id, self.kv_handle)

    def frozen_key(self) -> tuple:
        return (self.session_id, self.kv_handle, self.generation,
                self.contract_digest, self.flags,
                self.required_cached_tokens, self.required_tokens_after_round)

    def schedule_key(self) -> tuple:
        return (self.deadline_or_max, self.qos)

    def queue_key(self) -> tuple:
        return (self.deadline_or_max, 255 - self.qos, self.ready_tick,
                self.request_id)


@dataclasses.dataclass(frozen=True)
class KvRequestPin:
    request_id: int
    session_id: int
    kv_handle: int
    generation: int
    contract_digest: bytes
    flags: int
    required_cached_tokens: int
    required_tokens_after_round: int
    phase: int
    payload: KvRollbackPayload | None

    @property
    def tuple(self) -> tuple:
        return (self.session_id, self.kv_handle)

    def token(self):
        if self.payload is None:
            return None
        return KvRollbackToken(self.request_id, self.payload.serial)


@dataclasses.dataclass(frozen=True)
class KvReleaseIntent:
    request_id: int
    session_id: int
    kv_handle: int
    generation: int

    @property
    def tuple(self) -> tuple:
        return (self.session_id, self.kv_handle)


@dataclasses.dataclass(frozen=True)
class KvReleaseWaiter:
    request_id: int
    session_id: int
    kv_handle: int
    generation: int

    @property
    def tuple(self) -> tuple:
        return (self.session_id, self.kv_handle)


@dataclasses.dataclass(frozen=True)
class KvAppendObligation:
    request_id: int
    session_id: int
    kv_handle: int
    generation: int
    base_tokens: int
    append_tokens: int
    base_byte: int
    bytes: int

    @property
    def tuple(self) -> tuple:
        return (self.session_id, self.kv_handle)


@dataclasses.dataclass(frozen=True)
class KvTerminalSnapshot:
    request_id: int
    session_id: int
    kv_handle: int
    generation: int
    contract_digest: bytes
    state: int
    cached_tokens: int
    valid_bytes: int
    content_digest: bytes | None
    status: int
    source: int


@dataclasses.dataclass(frozen=True)
class KvRuntimeView:
    request_id: int
    member_ordinal: int
    session_id: int
    kv_handle: int
    generation: int
    contract_digest: bytes
    view_epoch: int
    slot_id: int
    slot_base: int
    slot_bytes: int
    valid_bytes_at_arm: int


@dataclasses.dataclass(frozen=True)
class KvEvictionEvent:
    session_id: int
    kv_handle: int
    generation: int
    prior_state: int
    diagnostic_prefix_tokens: int = 0
    diagnostic_prefix_bytes: int = 0


@dataclasses.dataclass(frozen=True)
class KvEvictingEntry:
    prior_state: int
    generation: int
    cached_tokens: int
    valid_bytes: int
    content_digest: bytes | None


@dataclasses.dataclass(frozen=True)
class KvOwnerTerminal:
    request_id: int
    status: int
    token: KvRollbackToken | None = None
    strict: bool = True


@dataclasses.dataclass(frozen=True)
class KvLaterFault:
    request_id: int
    candidate: KvErrorCandidate


@dataclasses.dataclass(frozen=True)
class KvAppendArm:
    request_id: int
    base_tokens: int
    append_tokens: int


@dataclasses.dataclass(frozen=True)
class KvAppendTerminal:
    request_id: int
    tokens_ok: tuple
    content_digest: bytes | None = None


@dataclasses.dataclass(frozen=True)
class KvDmaCount:
    request_id: int
    count: int


@dataclasses.dataclass(frozen=True)
class KvFaultEvent:
    request_id: int
    candidate: KvErrorCandidate


@dataclasses.dataclass(frozen=True)
class KvViewArm:
    request_id: int
    member_ordinal: int


@dataclasses.dataclass
class KvEdge:
    tick: int = 0
    admissions: list = dataclasses.field(default_factory=list)
    releases: list = dataclasses.field(default_factory=list)
    owner_terminals: list = dataclasses.field(default_factory=list)
    later_faults: list = dataclasses.field(default_factory=list)
    core_starts: list = dataclasses.field(default_factory=list)
    append_arms: list = dataclasses.field(default_factory=list)
    append_terminals: list = dataclasses.field(default_factory=list)
    dma_accepts: list = dataclasses.field(default_factory=list)
    dma_terminals: list = dataclasses.field(default_factory=list)
    faults: list = dataclasses.field(default_factory=list)
    view_arms: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class KvEdgeResult:
    commit_tick: int = 0
    admissions: dict = dataclasses.field(default_factory=dict)
    promotions: dict = dataclasses.field(default_factory=dict)
    waits: dict = dataclasses.field(default_factory=dict)
    releases: dict = dataclasses.field(default_factory=dict)
    evictions_started: list = dataclasses.field(default_factory=list)
    evictions_completed: list = dataclasses.field(default_factory=list)
    rollbacks: dict = dataclasses.field(default_factory=dict)
    terminals: dict = dataclasses.field(default_factory=dict)
    handoffs: dict = dataclasses.field(default_factory=dict)
    views: dict = dataclasses.field(default_factory=dict)
    ownerless_cancels: list = dataclasses.field(default_factory=list)
    fatal: str | None = None


@dataclasses.dataclass(frozen=True)
class KvPersistentState:
    slot_owners: tuple
    records: tuple
    tombstones: tuple
    next_kv_use_epoch: int


@dataclasses.dataclass(frozen=True)
class KvLiveState:
    records: tuple
    slot_owners: tuple
    waiters: tuple
    pins: tuple
    release_waiters: tuple
    appends: tuple
    evicting: tuple
    tombstones: tuple
    next_kv_use_epoch: int
    next_rollback_serial: int


def _empty_initial(head: KvAdmissionWaiter) -> KvRecord:
    return KvRecord(
        head.session_id, head.kv_handle, head.generation,
        head.contract_digest, KvState.ALLOCATING, None, 0, 0, None, 0, 0, 0,
        0, 0, None, 0, 0, None)


def _uniqueness_gaps(records, waiters, pins, release_waiters, appends,
                     evicting, tombstones) -> tuple:
    problems = []
    for name, keys in (
            ("session record", [record.tuple for record in records]),
            ("admission waiter request", [entry.request_id
                                          for entry in waiters]),
            ("KV pin request", [pin.request_id for pin in pins]),
            ("KV pin slot owner", [pin.tuple for pin in pins]),
            ("release waiter request", [entry.request_id
                                        for entry in release_waiters]),
            ("release waiter target", [entry.tuple
                                       for entry in release_waiters]),
            ("append obligation", [entry.request_id for entry in appends]),
            ("eviction entry", [key for key, _ in evicting]),
            ("tombstone", [key for key, _ in tombstones])):
        if len(set(keys)) != len(keys):
            problems.append(f"duplicate {name} identity")
    pin_ids = {pin.request_id for pin in pins}
    waiter_ids = {entry.request_id for entry in waiters}
    release_ids = {entry.request_id for entry in release_waiters}
    if pin_ids & waiter_ids:
        problems.append("pin and admission waiter identities must differ")
    if release_ids & pin_ids or release_ids & waiter_ids:
        problems.append("release waiter identity is not unique")
    record_keys = {record.tuple for record in records}
    for key, _ in tombstones:
        if key in record_keys:
            problems.append("record and tombstone tuple must be disjoint")
    return tuple(problems)


def _frozen_key(intent) -> tuple:
    return (intent.session_id, intent.kv_handle, intent.generation,
            intent.contract_digest, intent.flags,
            intent.required_cached_tokens,
            intent.required_tokens_after_round)


def _copy_waiter(entry: KvAdmissionWaiter) -> KvAdmissionWaiter:
    return dataclasses.replace(
        entry, prior=None if entry.prior is None else entry.prior.copy())


def _copy_pin(pin: KvRequestPin) -> KvRequestPin:
    if pin.payload is None:
        return pin
    return dataclasses.replace(
        pin, payload=dataclasses.replace(pin.payload,
                                         prior=pin.payload.prior.copy()))

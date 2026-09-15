"""Weight cache identities and comparators (main contract 7.8, 8.7.2).

The base key, the full fill key and the cache DMA descriptor key each have
exactly one definition here; C++ mirrors them from the same ABI registry.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError

WEIGHT_CACHE_PARTITION_ID = 1
FILL_TRAFFIC_DOMAIN = b"AI_MESH_WEIGHT_FILL_V1\0"
BASE_KEY_BYTES = 12
FILL_KEY_BYTES = 16
CACHE_DMA_DESCRIPTOR_KEY_BYTES = 20
ERROR_SOURCE_KEY_BYTES = 40
INVALID_CORE = 0xFFFF


def enum_values(enum) -> tuple:
    return tuple(value for name, value in vars(enum).items()
                 if not name.startswith("_") and isinstance(value, int))


@dataclass(frozen=True, order=False)
class WeightCacheBaseKey:
    core_id: int
    cache_partition_id: int
    weight_tag_index: int
    cache_generation: int

    def __post_init__(self) -> None:
        for name in ("core_id", "cache_partition_id", "weight_tag_index",
                     "cache_generation"):
            value = getattr(self, name)
            if not 0 <= value <= 0xFFFFFFFF:
                raise MeshIrError("E_MOE_CACHE",
                                  f"{name} out of range")

    def sort_key(self) -> tuple:
        return (self.core_id, self.cache_partition_id, self.weight_tag_index,
                self.cache_generation)

    def wire_bytes(self) -> bytes:
        return struct.pack("<HHII", self.core_id, self.cache_partition_id,
                           self.weight_tag_index, self.cache_generation)

    @classmethod
    def from_wire_bytes(cls, data: bytes) -> "WeightCacheBaseKey":
        if len(data) != BASE_KEY_BYTES:
            raise MeshIrError("E_MOE_CACHE", "base key wire size mismatch")
        fields = struct.unpack("<HHII", data)
        return cls(*fields)


@dataclass(frozen=True, order=False)
class WeightFillKey:
    base: WeightCacheBaseKey
    fill_incarnation: int

    def __post_init__(self) -> None:
        if not 0 < self.fill_incarnation <= 0xFFFFFFFF:
            raise MeshIrError("E_MOE_CACHE",
                              "fill incarnation must be nonzero and u32")

    def sort_key(self) -> tuple:
        return (*self.base.sort_key(), self.fill_incarnation)

    def wire_bytes(self) -> bytes:
        return struct.pack("<HHIII", self.base.core_id,
                           self.base.cache_partition_id,
                           self.base.weight_tag_index,
                           self.base.cache_generation, self.fill_incarnation)

    def traffic_id(self) -> str:
        return hashlib.sha256(
            FILL_TRAFFIC_DOMAIN + self.wire_bytes()).hexdigest()

    @classmethod
    def from_wire_bytes(cls, data: bytes) -> "WeightFillKey":
        if len(data) != FILL_KEY_BYTES:
            raise MeshIrError("E_MOE_CACHE", "fill key wire size mismatch")
        core_id, partition, tag, generation, incarnation = struct.unpack(
            "<HHIII", data)
        return cls(WeightCacheBaseKey(core_id, partition, tag, generation),
                   incarnation)

    def descriptor_key(self, segment_ordinal: int = 0) -> bytes:
        return self.wire_bytes() + struct.pack("<I", segment_ordinal)


def base_key_order(keys) -> list:
    return sorted(keys, key=lambda key: key.sort_key())


def fill_key_order(keys) -> list:
    return sorted(keys, key=lambda key: key.sort_key())


def dedup_base_keys(keys) -> list:
    ordered = base_key_order(set(keys))
    return ordered


FAILURE_SITE_DETAIL = {
    A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_AXI_R: "E_AXI_RESPONSE",
    A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_SRAM_BOUNDS:
        "E_DCORE_SRAM_BOUNDS",
    A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_SRAM_COMMIT: "E_DCORE_ENGINE",
    A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_SOURCE_VALIDITY:
        "E_DCORE_POISON_READ",
}
FAILURE_DETAIL_SET = frozenset(FAILURE_SITE_DETAIL.values())


@dataclass(frozen=True)
class ErrorSourceKey:
    error_class: int
    core_id_or_ffff: int
    domain: int
    object_kind: int
    region_group_id: int
    region_id: int
    ordinal: int
    generation: int
    aux_key: bytes = bytes(16)

    def gaps(self) -> tuple:
        problems = []
        if not isinstance(self.aux_key, bytes) or len(self.aux_key) != 16:
            problems.append("aux key must be 16 bytes")
        for name, value, limit in (
                ("error class", self.error_class, 0xFFFF),
                ("core id", self.core_id_or_ffff, 0xFFFF),
                ("domain", self.domain, 0xFF),
                ("object kind", self.object_kind, 0xFF),
                ("region group", self.region_group_id, 0xFFFFFFFF),
                ("region id", self.region_id, 0xFFFFFFFF),
                ("ordinal", self.ordinal, 0xFFFFFFFF),
                ("generation", self.generation, 0xFFFFFFFF)):
            if not isinstance(value, int) or isinstance(value, bool) or \
                    not 0 <= value <= limit:
                problems.append(
                    f"error source {name} is outside its width")
        if problems:
            return tuple(problems)
        if self.error_class not in enum_values(A.MOE_ERROR_CLASS):
            problems.append("error class outside the enum")
        if self.domain not in enum_values(A.MESH_OBJECT_DOMAIN):
            problems.append("domain outside the enum")
        if self.object_kind not in enum_values(A.MESH_OBJECT_KIND):
            problems.append("object kind outside the enum")
        return tuple(problems)

    def __post_init__(self) -> None:
        problems = self.gaps()
        if problems:
            raise MeshIrError("E_MOE_CACHE", problems[0])

    def sort_key(self) -> tuple:
        return (self.error_class, self.core_id_or_ffff, self.domain,
                self.object_kind, self.region_group_id, self.region_id,
                self.ordinal, self.generation) + tuple(self.aux_key)

    def wire_bytes(self) -> bytes:
        return struct.pack("<HHBBHIIII", self.error_class,
                           self.core_id_or_ffff, self.domain,
                           self.object_kind, 0, self.region_group_id,
                           self.region_id, self.ordinal,
                           self.generation) + self.aux_key

    @classmethod
    def from_wire_bytes(cls, data: bytes) -> "ErrorSourceKey":
        if len(data) != ERROR_SOURCE_KEY_BYTES:
            raise MeshIrError("E_MOE_CACHE",
                              "error source key wire size mismatch")
        fields = struct.unpack("<HHBBHIIII", data[:24])
        return cls(error_class=fields[0], core_id_or_ffff=fields[1],
                   domain=fields[2], object_kind=fields[3],
                   region_group_id=fields[5], region_id=fields[6],
                   ordinal=fields[7], generation=fields[8],
                   aux_key=data[24:])


def weight_fill_source(weight_fill_key: WeightFillKey) -> ErrorSourceKey:
    return ErrorSourceKey(
        error_class=A.MOE_ERROR_CLASS.WEIGHT_FILL, core_id_or_ffff=0xFFFF,
        domain=A.MESH_OBJECT_DOMAIN.WEIGHT_FILL,
        object_kind=A.MESH_OBJECT_KIND.WEIGHT_FILL_OBLIGATION,
        region_group_id=0, region_id=0, ordinal=0,
        generation=weight_fill_key.fill_incarnation,
        aux_key=weight_fill_key.wire_bytes())


SLOT_FREE = 0
SLOT_FREE_RESERVED = 1
SLOT_FILLING = 2
SLOT_EVICTING_RESERVED = 3
SLOT_VALID = 4
SLOT_ERROR_HELD = 5
RUNTIME_SLOT_STATES = frozenset((SLOT_FREE, SLOT_FREE_RESERVED, SLOT_FILLING,
                                 SLOT_EVICTING_RESERVED, SLOT_VALID,
                                 SLOT_ERROR_HELD))
REPLAY_VALID_STATES = frozenset((SLOT_FREE, SLOT_VALID))

RESERVE_COMMITTED = "COMMITTED"
RESERVE_RESOURCE_WAIT = "RESOURCE_WAIT"
RESERVE_FAILED = "FAILED"


@dataclass
class CacheSlot:
    slot_id: int
    state: int = SLOT_FREE
    weight_tag_index: int = None
    valid_bytes: int = 0
    last_use_epoch: int = None
    bound_fill: WeightFillKey = None
    pins: int = 0


@dataclass
class CacheSubscriber:
    batch_id: int
    layer_id: int
    base_key: WeightCacheBaseKey
    state: int = A.CACHE_SUBSCRIBER_STATE.WAITING

    def sort_key(self) -> tuple:
        return (self.batch_id, self.layer_id, self.base_key.sort_key())


@dataclass
class WeightFillObligation:
    key: WeightFillKey
    state: int = A.CACHE_OBLIGATION_STATE.RESERVED
    slot_id: int = 0
    subscribers: dict = None
    opened_tick: int = 0
    eligible_edge: int = None
    consumed_eviction: bool = False
    completed: bool = False
    first_error: tuple = None
    drained_bytes: int = 0

    def sort_key(self) -> tuple:
        return self.key.sort_key()


@dataclass
class FailureTombstone:
    weight_tag_index: int
    first_error_tick: int
    first_error_source: ErrorSourceKey
    first_error_code: str


@dataclass
class CacheReservationToken:
    token_id: int
    batch_id: int
    layer_id: int
    base_key: WeightCacheBaseKey
    outcome: int
    slot_id: int
    fill_key: WeightFillKey = None
    pinned: bool = True
    released: bool = False
    consumers: int = 0

    def sort_key(self) -> tuple:
        return (self.batch_id, self.layer_id, self.base_key.sort_key())


@dataclass
class BatchFault:
    started: bool = True
    fanout_done: bool = False
    owned_drained: bool = False
    tick: int = 0


@dataclass
class ReservationShadow:
    plan: list
    victims: dict
    incarnation: int
    next_epoch: int


class CacheReservationCoordinator:
    def __init__(self, core_id, slot_count, slot_bytes, cacheable_tags,
                 mshr_slots, eviction_slots, obligation_slots,
                 subscriber_slots, cache_generation=0):
        if slot_count <= 0 or slot_bytes <= 0:
            raise MeshIrError("E_CAPACITY_PLAN",
                              "weight cache needs a positive slot geometry")
        self.core_id = core_id
        self.slot_bytes = slot_bytes
        self.cacheable_tags = frozenset(cacheable_tags)
        self.slots = [CacheSlot(slot_id=index)
                      for index in range(slot_count)]
        self.cache_generation = cache_generation
        self.next_use_epoch = 0
        self.next_fill_incarnation = 1
        self.next_token_id = 1
        self.cache_edge = FIRST_CACHE_EDGE
        self.engine_edge = FIRST_CACHE_EDGE
        self.mshr_slots = mshr_slots
        self.eviction_slots = eviction_slots
        self.obligation_slots = obligation_slots
        self.subscriber_slots = subscriber_slots
        self.mshr_free = mshr_slots
        self.eviction_free = eviction_slots
        self.obligation_free = obligation_slots
        self.subscriber_free = subscriber_slots
        self.obligations = {}
        self.tokens = {}
        self.failure_table = {}
        self.cancelled_members = set()
        self.batch_faults = {}

    def base_key(self, tag_index, generation=None):
        if tag_index not in self.cacheable_tags:
            raise MeshIrError("E_MOE_CACHE", "tag is not cacheable",
                              tag_index=tag_index)
        return WeightCacheBaseKey(
            self.core_id, WEIGHT_CACHE_PARTITION_ID, tag_index,
            self.cache_generation if generation is None else generation)

    def slot_of(self, base_key):
        for slot in self.slots:
            if slot.state == SLOT_VALID and \
                    slot.weight_tag_index == base_key.weight_tag_index:
                return slot
        return None

    def _shadow(self, demands):
        keys = dedup_base_keys(demands)
        for key in keys:
            if key in self.failure_table:
                return None, ("FAILED", key)
        hits = {}
        attached = {}
        allocations = {}
        victims = {}
        for key in keys:
            slot = self.slot_of(key)
            if slot is not None:
                hits[key] = slot.slot_id
        taken = set(hits.values())
        for key in keys:
            if key in hits:
                continue
            obligation = self.obligations.get(key)
            if obligation is not None:
                attached[key] = obligation
        for key in keys:
            if key in hits or key in attached:
                continue
            free = next((slot for slot in self.slots
                         if slot.state == SLOT_FREE and
                         slot.slot_id not in taken), None)
            if free is None:
                candidates = [slot for slot in self.slots
                              if slot.state == SLOT_VALID and
                              slot.pins == 0 and slot.slot_id not in taken]
                if not candidates:
                    return None, ("RESOURCE_WAIT", key)
                free = min(candidates, key=lambda slot: (
                    slot.last_use_epoch if slot.last_use_epoch is not None
                    else 0, slot.weight_tag_index, slot.slot_id))
                victims[key] = free.slot_id
            taken.add(free.slot_id)
            allocations[key] = free
        new_fills = len(allocations)
        if new_fills > self.obligation_free:
            return None, ("RESOURCE_WAIT", keys[0])
        if new_fills > self.mshr_free:
            return None, ("RESOURCE_WAIT", keys[0])
        if len(victims) > self.eviction_free:
            return None, ("RESOURCE_WAIT", keys[0])
        if len(keys) > self.subscriber_free:
            return None, ("RESOURCE_WAIT", keys[0])
        plan = []
        for key in keys:
            if key in hits:
                plan.append((key, A.CACHE_RESIDENCY_OUTCOME.HIT,
                             hits[key]))
            elif key in attached:
                plan.append((key, A.CACHE_RESIDENCY_OUTCOME.ATTACH,
                             attached[key]))
            else:
                plan.append((key, A.CACHE_RESIDENCY_OUTCOME.NEW_FILL,
                             allocations[key].slot_id))
        shadow = ReservationShadow(plan=plan, victims=victims,
                                   incarnation=self.next_fill_incarnation,
                                   next_epoch=self.next_use_epoch)
        return shadow, None

    def probe(self, tag_indices):
        demands = [self.base_key(tag) for tag in tag_indices]
        if not demands:
            return None, (RESERVE_COMMITTED, [])
        shadow, failure = self._shadow(dedup_base_keys(demands))
        if shadow is None:
            if failure[0] == "FAILED":
                return None, (RESERVE_FAILED, [failure[1]])
            return None, (RESERVE_RESOURCE_WAIT, [])
        return shadow, (RESERVE_COMMITTED, [])

    def reserve(self, batch_id, layer_id, tag_indices, tick=0):
        shadow, outcome = self.probe(tag_indices)
        if shadow is None:
            return outcome
        return RESERVE_COMMITTED, self.apply_shadow(shadow, batch_id, layer_id,
                                                   tick)

    def apply_shadow(self, shadow, batch_id, layer_id, tick=0):
        self._verify_shadow(shadow)
        tokens = []
        for key, outcome, target in shadow.plan:
            if outcome == A.CACHE_RESIDENCY_OUTCOME.HIT:
                slot_id = target
            elif outcome == A.CACHE_RESIDENCY_OUTCOME.ATTACH:
                slot_id = target.slot_id
            else:
                slot_id = target
            slot = self.slots[slot_id]
            slot.last_use_epoch = shadow.next_epoch
            shadow.next_epoch += 1
            slot.pins += 1
            if outcome == A.CACHE_RESIDENCY_OUTCOME.HIT:
                tokens.append(self._new_token(
                    batch_id, layer_id, key, outcome, slot_id))
                continue
            if outcome == A.CACHE_RESIDENCY_OUTCOME.ATTACH:
                obligation = target
            else:
                fill_key = WeightFillKey(key, shadow.incarnation)
                shadow.incarnation += 1
                took_victim = slot_id in shadow.victims.values()
                if took_victim:
                    slot.state = SLOT_EVICTING_RESERVED
                    self.eviction_free -= 1
                else:
                    slot.state = SLOT_FREE_RESERVED
                slot.bound_fill = fill_key
                slot.weight_tag_index = key.weight_tag_index
                slot.valid_bytes = 0
                self.mshr_free -= 1
                self.obligation_free -= 1
                obligation = WeightFillObligation(
                    key=fill_key, slot_id=slot_id, subscribers={},
                    opened_tick=tick, consumed_eviction=took_victim)
                self.obligations[key] = obligation
            self.subscriber_free -= 1
            occurrence = (batch_id, layer_id)
            obligation.subscribers[occurrence] = CacheSubscriber(
                batch_id=batch_id, layer_id=layer_id, base_key=key)
            tokens.append(self._new_token(batch_id, layer_id, key, outcome,
                                          slot_id, obligation.key))
        self.next_use_epoch = shadow.next_epoch
        self.next_fill_incarnation = shadow.incarnation
        return tokens

    def _verify_shadow(self, shadow):
        for key, outcome, target in shadow.plan:
            if key in self.failure_table:
                raise MeshIrError("E_MOE_CACHE",
                                  "a probed demand became tombstoned")
            slot_id = target if outcome != A.CACHE_RESIDENCY_OUTCOME.ATTACH \
                else target.slot_id
            slot = self.slots[slot_id]
            if outcome == A.CACHE_RESIDENCY_OUTCOME.HIT:
                if slot.state != SLOT_VALID or \
                        slot.weight_tag_index != key.weight_tag_index:
                    raise MeshIrError("E_MOE_CACHE",
                                      "a probed hit is no longer valid")
            elif outcome == A.CACHE_RESIDENCY_OUTCOME.ATTACH:
                if self.obligations.get(key) is not target:
                    raise MeshIrError("E_MOE_CACHE",
                                      "a probed attach lost its obligation")
            elif slot.state not in (SLOT_FREE, SLOT_VALID):
                raise MeshIrError("E_MOE_CACHE",
                                  "a probed fill target is not reservable")

    def _new_token(self, batch_id, layer_id, base_key, outcome, slot_id,
                   fill_key=None):
        token = CacheReservationToken(
            token_id=self.next_token_id, batch_id=batch_id,
            layer_id=layer_id, base_key=base_key, outcome=outcome,
            slot_id=slot_id, fill_key=fill_key)
        self.next_token_id += 1
        self.tokens[token.token_id] = token
        return token

    def mark_filling(self, fill_key):
        obligation = self.obligations.get(fill_key.base)
        if obligation is None or obligation.key != fill_key:
            raise MeshIrError("E_MOE_CACHE", "fill has no active obligation")
        slot = self.slots[obligation.slot_id]
        if slot.bound_fill != fill_key:
            raise MeshIrError("E_MOE_CACHE", "fill key does not own the slot")
        if slot.state not in (SLOT_FREE_RESERVED, SLOT_EVICTING_RESERVED):
            raise MeshIrError("E_MOE_CACHE", "slot is not reserved")
        slot.state = SLOT_FILLING
        slot.valid_bytes = 0
        obligation.eligible_edge = self.engine_edge + 1

    def advance_cache_edge(self):
        self.cache_edge += 1
        pending = [obligation for obligation in
                   sorted(self.obligations.values(),
                          key=lambda entry: entry.sort_key())
                   if self.slots[obligation.slot_id].state in
                   (SLOT_FREE_RESERVED, SLOT_EVICTING_RESERVED)]
        for obligation in pending:
            self.mark_filling(obligation.key)
        return self.cache_edge

    def advance_engine_edge(self):
        self.engine_edge += 1
        return self.engine_edge

    def dma_eligible(self):
        return [obligation for obligation in
                sorted(self.obligations.values(),
                       key=lambda entry: entry.sort_key())
                if obligation.state == A.CACHE_OBLIGATION_STATE.RESERVED and
                obligation.eligible_edge is not None and
                obligation.eligible_edge <= self.engine_edge and
                self.slots[obligation.slot_id].state == SLOT_FILLING]

    def mark_issued(self, fill_key, batch_qos=0, segment_ordinal=0):
        obligation = self.obligations.get(fill_key.base)
        if obligation is None or obligation.key != fill_key:
            raise MeshIrError("E_MOE_CACHE", "fill has no active obligation")
        if obligation not in self.dma_eligible():
            raise MeshIrError("E_MOE_CACHE",
                              "fill is not eligible on this engine edge")
        obligation.state = A.CACHE_OBLIGATION_STATE.ISSUING
        return cache_fill_issue(fill_key, obligation.opened_tick, batch_qos,
                                segment_ordinal)

    def mark_in_flight(self, fill_key):
        obligation = self.obligations.get(fill_key.base)
        if obligation is None or \
                obligation.state != A.CACHE_OBLIGATION_STATE.ISSUING:
            raise MeshIrError("E_MOE_CACHE", "fill was never issued")
        obligation.state = A.CACHE_OBLIGATION_STATE.IN_FLIGHT

    def note_fill_success(self, fill_key, valid_bytes):
        obligation = self.obligations.get(fill_key.base)
        if obligation is None or obligation.key != fill_key:
            raise MeshIrError("E_MOE_CACHE", "fill has no active obligation")
        if valid_bytes > self.slot_bytes:
            raise MeshIrError("E_MOE_CACHE", "fill exceeds the slot size")
        if obligation.state == A.CACHE_OBLIGATION_STATE.FAILED_DRAINING:
            obligation.drained_bytes = valid_bytes
            return
        slot = self.slots[obligation.slot_id]
        slot.state = SLOT_VALID
        slot.valid_bytes = valid_bytes
        slot.bound_fill = None
        self.mshr_free += 1
        if obligation.consumed_eviction:
            self.eviction_free += 1
            obligation.consumed_eviction = False
        obligation.completed = True
        for subscriber in sorted(obligation.subscribers.values(),
                                 key=lambda entry: entry.sort_key()):
            if subscriber.state == A.CACHE_SUBSCRIBER_STATE.WAITING:
                subscriber.state = A.CACHE_SUBSCRIBER_STATE.WOKEN
        for batch_id in sorted(self.batch_faults):
            self._release_batch_tokens(batch_id)
        self._retire_if_settled(obligation)

    def note_fill_failure(self, fill_key, site, tick, source):
        obligation = self.obligations.get(fill_key.base)
        if obligation is None or obligation.key != fill_key:
            raise MeshIrError("E_MOE_CACHE", "fill has no active obligation")
        detail = FAILURE_SITE_DETAIL.get(site)
        if detail is None or detail not in FAILURE_DETAIL_SET:
            raise MeshIrError("E_MOE_CACHE",
                              "failure site is outside the detail closure")
        candidate = (tick, source.sort_key(), detail)
        if obligation.first_error is None or \
                candidate < obligation.first_error:
            obligation.first_error = candidate
        obligation.state = A.CACHE_OBLIGATION_STATE.FAILED_DRAINING
        slot = self.slots[obligation.slot_id]
        slot.state = SLOT_ERROR_HELD
        slot.valid_bytes = 0
        for subscriber in obligation.subscribers.values():
            if subscriber.state == A.CACHE_SUBSCRIBER_STATE.WAITING:
                subscriber.state = A.CACHE_SUBSCRIBER_STATE.FAIL_NOTIFIED

    def cancel_member(self, batch_id):
        # A member cancel only tombstones the member: the batch-level fill
        # subscriber, its token and the shared obligation are unaffected.
        self.cancelled_members.add(batch_id)

    def _fill_terminal(self, token):
        obligation = self.obligations.get(token.base_key)
        if obligation is None or token.fill_key != obligation.key:
            return True
        return obligation.completed or obligation.state in (
            A.CACHE_OBLIGATION_STATE.FAILED_DRAINING,
            A.CACHE_OBLIGATION_STATE.FAILED_RETIRED)

    def _subscriber_tombstoned(self, token):
        obligation = self.obligations.get(token.base_key)
        if obligation is None or token.fill_key != obligation.key:
            return True
        subscriber = obligation.subscribers.get(
            (token.batch_id, token.layer_id))
        if subscriber is None:
            return True
        return subscriber.state == \
            A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED

    def _release_token_if_owned(self, token_id) -> bool:
        token = self.tokens[token_id]
        if token.released:
            return False
        fault = self.batch_faults.get(token.batch_id)
        # A faulted batch releases its whole token set through the join; a
        # healthy batch holds each pin until its last consumer drains.
        if fault is None and token.consumers > 0:
            return False
        if fault is not None:
            if not fault.fanout_done:
                return False
            if fault.started and not fault.owned_drained:
                return False
            if token.fill_key is not None and \
                    self._subscriber_tombstoned(token) and \
                    not self._fill_terminal(token):
                return False
        self.release_token(token_id)
        return True

    def _release_batch_tokens(self, batch_id):
        for token in sorted(self.tokens.values(),
                            key=lambda entry: entry.sort_key()):
            if token.batch_id == batch_id:
                self._release_token_if_owned(token.token_id)

    def note_instance_fault(self, batch_id, tick=0):
        fault = self.batch_faults.setdefault(batch_id, BatchFault())
        fault.started = True
        fault.tick = tick
        for obligation in self.obligations.values():
            for occurrence, subscriber in obligation.subscribers.items():
                if occurrence[0] != batch_id:
                    continue
                if subscriber.state == A.CACHE_SUBSCRIBER_STATE.WAITING:
                    subscriber.state = \
                        A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED

    def note_failure_fanout_done(self, batch_id):
        self.batch_faults.setdefault(batch_id, BatchFault()).fanout_done = True
        self._release_batch_tokens(batch_id)

    def note_owned_work_drained(self, batch_id):
        self.batch_faults.setdefault(
            batch_id, BatchFault()).owned_drained = True
        self._release_batch_tokens(batch_id)

    def set_token_consumers(self, token_id, consumers, layer_id=None):
        token = self.tokens[token_id]
        token.consumers = consumers
        if layer_id is not None:
            token.layer_id = layer_id

    def note_consumer_drained(self, batch_id, layer_id, tag_index):
        # The batch identity is part of the occurrence: a drain only releases
        # the token of the batch that really finished its consumer.
        for token in sorted(self.tokens.values(),
                            key=lambda entry: entry.sort_key()):
            if token.released or token.batch_id != batch_id:
                continue
            if token.layer_id != layer_id:
                continue
            if token.base_key.weight_tag_index != tag_index:
                continue
            if token.consumers > 0:
                token.consumers -= 1
            self._release_token_if_owned(token.token_id)

    def abort_before_start(self, batch_id):
        fault = self.batch_faults.setdefault(batch_id, BatchFault())
        fault.started = False
        fault.owned_drained = True
        for token in sorted(self.tokens.values(),
                            key=lambda entry: entry.sort_key()):
            if token.batch_id != batch_id or token.released:
                continue
            obligation = (self.obligations.get(token.base_key)
                          if token.fill_key is not None else None)
            pending = (obligation is not None and
                       token.fill_key == obligation.key and
                       token.outcome != A.CACHE_RESIDENCY_OUTCOME.HIT)
            if not pending:
                continue
            occurrence = (token.batch_id, token.layer_id)
            subscriber = obligation.subscribers.get(occurrence)
            if subscriber is not None and subscriber.state == \
                    A.CACHE_SUBSCRIBER_STATE.WAITING:
                subscriber.state = \
                    A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED
        self._release_batch_tokens(batch_id)

    def release_token(self, token_id):
        token = self.tokens[token_id]
        if token.released:
            return
        token.released = True
        if token.pinned:
            token.pinned = False
            self.slots[token.slot_id].pins -= 1
        obligation = (self.obligations.get(token.base_key)
                      if token.fill_key is not None else None)
        if obligation is not None:
            self.subscriber_free += 1
            subscriber = obligation.subscribers.get(
                (token.batch_id, token.layer_id))
            if subscriber is not None and subscriber.state in (
                    A.CACHE_SUBSCRIBER_STATE.WOKEN,
                    A.CACHE_SUBSCRIBER_STATE.FAIL_NOTIFIED,
                    A.CACHE_SUBSCRIBER_STATE.TERMINAL_TOMBSTONED,
                    A.CACHE_SUBSCRIBER_STATE.CANCELLED):
                subscriber.state = A.CACHE_SUBSCRIBER_STATE.RELEASED
            self._retire_if_settled(obligation)

    def _retire_if_settled(self, obligation):
        if obligation.state in (A.CACHE_OBLIGATION_STATE.RETIRED,
                                A.CACHE_OBLIGATION_STATE.FAILED_RETIRED):
            return
        if obligation.subscribers and any(
                subscriber.state != A.CACHE_SUBSCRIBER_STATE.RELEASED
                for subscriber in obligation.subscribers.values()):
            return
        if obligation.state == A.CACHE_OBLIGATION_STATE.FAILED_DRAINING:
            if obligation.first_error is None:
                raise MeshIrError("E_MOE_CACHE",
                                  "failed fill has no first error")
            tick, _source_key, detail = obligation.first_error
            self.failure_table[obligation.key.base] = FailureTombstone(
                weight_tag_index=obligation.key.base.weight_tag_index,
                first_error_tick=tick,
                first_error_source=weight_fill_source(obligation.key),
                first_error_code=detail)
            slot = self.slots[obligation.slot_id]
            slot.state = SLOT_FREE
            slot.weight_tag_index = None
            slot.valid_bytes = 0
            slot.bound_fill = None
            slot.last_use_epoch = None
            if obligation.consumed_eviction:
                self.eviction_free += 1
                obligation.consumed_eviction = False
            self.mshr_free += 1
            self.obligation_free += 1
            obligation.state = A.CACHE_OBLIGATION_STATE.FAILED_RETIRED
        elif obligation.completed:
            obligation.state = A.CACHE_OBLIGATION_STATE.RETIRED
            self.obligation_free += 1
        else:
            return
        obligation.state = obligation.state if obligation.completed else \
            obligation.state
        self.obligations.pop(obligation.key.base, None)

    def snapshot(self):
        for slot in self.slots:
            if slot.state not in REPLAY_VALID_STATES:
                raise MeshIrError("E_MOE_CACHE",
                                  "only a quiescent cache can be serialized",
                                  slot_id=slot.slot_id)
        if any(obligation.subscribers for obligation in
               self.obligations.values()):
            raise MeshIrError("E_MOE_CACHE",
                              "active subscribers cannot be serialized")
        valid = [slot for slot in self.slots if slot.state == SLOT_VALID]
        ordered = sorted(valid, key=lambda slot: (
            slot.last_use_epoch if slot.last_use_epoch is not None else 0,
            slot.weight_tag_index, slot.slot_id))
        ranks = {slot.slot_id: rank for rank, slot in enumerate(ordered)}
        slots = []
        for slot in self.slots:
            if slot.state == SLOT_VALID:
                slots.append({"slot_id": slot.slot_id, "state":
                              A.CACHE_SLOT_STATE.VALID,
                              "weight_tag_index": slot.weight_tag_index,
                              "valid_bytes": slot.valid_bytes,
                              "lru_rank": ranks[slot.slot_id]})
            else:
                slots.append({"slot_id": slot.slot_id, "state":
                              A.CACHE_SLOT_STATE.INVALID,
                              "weight_tag_index": None, "valid_bytes": 0,
                              "lru_rank": None})
        tombstones = [
            {"weight_tag_index": tombstone.weight_tag_index,
             "first_error": {
                 "tick": tombstone.first_error_tick,
                 "source_key_wire":
                     tombstone.first_error_source.wire_bytes().hex(),
                 "error_code": tombstone.first_error_code}}
            for tombstone in sorted(self.failure_table.values(),
                                    key=lambda entry:
                                    entry.weight_tag_index)]
        return {
            "core_id": self.core_id,
            "cache_generation": self.cache_generation,
            "next_fill_incarnation": self.next_fill_incarnation,
            "slots": slots,
            "failure_tombstones": tombstones,
        }


FIRST_CACHE_EDGE = 1
ISSUE_SOURCE_RUNTIME = 0
ISSUE_SOURCE_CACHE_FILL = 1


def strict_capacity_required(coord, occurrences):
    lines = len(dedup_base_keys([coord.base_key(tag)
                                 for _layer, tag in occurrences]))
    return {
        "lines": lines,
        "subscribers": len(occurrences),
        "obligations": lines,
        "mshr": lines,
        "eviction": lines,
    }


def prove_strict_capacity(coord, occurrences):
    required = strict_capacity_required(coord, occurrences)
    available = {
        "lines": len(coord.slots),
        "subscribers": coord.subscriber_free,
        "obligations": coord.obligation_slots,
        "mshr": coord.mshr_slots,
        "eviction": coord.eviction_slots,
    }
    for field, needed in required.items():
        if needed > available[field]:
            raise MeshIrError(
                "E_CAPACITY_PLAN",
                f"strict batch-wide reservation needs more {field}",
                required=needed, available=available[field])
    return required


def dedup_occurrences(occurrences) -> tuple:
    ordered = sorted(occurrences, key=lambda entry: (entry[0], entry[1]))
    unique = []
    for occurrence in ordered:
        if unique and unique[-1][0] == occurrence[0] and \
                unique[-1][1] == occurrence[1]:
            continue
        unique.append(occurrence)
    return tuple(unique)


def probe_batch_wide(coord, occurrences):
    occurrences = dedup_occurrences(occurrences)
    keys = dedup_base_keys([coord.base_key(tag)
                            for _layer, tag in occurrences])
    if not keys:
        return None, (RESERVE_COMMITTED, [])
    if len(occurrences) > coord.subscriber_free:
        return None, (RESERVE_RESOURCE_WAIT, [])
    shadow, failure = coord._shadow(keys)
    if shadow is None:
        if failure[0] == "FAILED":
            return None, (RESERVE_FAILED, [failure[1]])
        return None, (RESERVE_RESOURCE_WAIT, [])
    return shadow, (RESERVE_COMMITTED, [])


def reserve_batch_wide(coord, batch_id, occurrences, tick=0):
    shadow, outcome = probe_batch_wide(coord, occurrences)
    if shadow is None:
        return outcome
    return RESERVE_COMMITTED, apply_batch_wide(coord, shadow, batch_id,
                                               occurrences, tick)


def apply_batch_wide(coord, shadow, batch_id, occurrences, tick=0):
    occurrences = dedup_occurrences(occurrences)
    outcome_by_key = {}
    for key, outcome, target in shadow.plan:
        outcome_by_key[key] = (outcome, target)
    canonical_fill = {}
    tokens = []
    for layer_id, tag in sorted(occurrences, key=lambda entry: (
            entry[0], coord.base_key(entry[1]).sort_key())):
        key = coord.base_key(tag)
        outcome, target = outcome_by_key[key]
        if outcome == A.CACHE_RESIDENCY_OUTCOME.HIT:
            slot_id = target
        elif outcome == A.CACHE_RESIDENCY_OUTCOME.ATTACH:
            slot_id = target.slot_id
        else:
            slot_id = target
        slot = coord.slots[slot_id]
        first_occurrence = canonical_fill.get(key)
        if outcome == A.CACHE_RESIDENCY_OUTCOME.NEW_FILL and \
                first_occurrence is not None:
            outcome = A.CACHE_RESIDENCY_OUTCOME.ATTACH
            target = first_occurrence
        slot.pins += 1
        if first_occurrence is None and outcome != \
                A.CACHE_RESIDENCY_OUTCOME.HIT:
            slot.last_use_epoch = shadow.next_epoch
            shadow.next_epoch += 1
        elif outcome == A.CACHE_RESIDENCY_OUTCOME.HIT:
            slot.last_use_epoch = shadow.next_epoch
            shadow.next_epoch += 1
        if outcome == A.CACHE_RESIDENCY_OUTCOME.HIT:
            tokens.append(coord._new_token(batch_id, layer_id, key, outcome,
                                           slot_id))
            continue
        if outcome == A.CACHE_RESIDENCY_OUTCOME.ATTACH:
            obligation = target
        else:
            fill_key = WeightFillKey(key, shadow.incarnation)
            shadow.incarnation += 1
            took_victim = slot_id in shadow.victims.values()
            if took_victim:
                slot.state = SLOT_EVICTING_RESERVED
                coord.eviction_free -= 1
            else:
                slot.state = SLOT_FREE_RESERVED
            slot.bound_fill = fill_key
            slot.weight_tag_index = key.weight_tag_index
            slot.valid_bytes = 0
            coord.mshr_free -= 1
            coord.obligation_free -= 1
            obligation = WeightFillObligation(
                key=fill_key, slot_id=slot_id, subscribers={},
                opened_tick=tick, consumed_eviction=took_victim)
            coord.obligations[key] = obligation
            canonical_fill[key] = obligation
        coord.subscriber_free -= 1
        occurrence = (batch_id, layer_id)
        obligation.subscribers[occurrence] = CacheSubscriber(
            batch_id=batch_id, layer_id=layer_id, base_key=key)
        tokens.append(coord._new_token(batch_id, layer_id, key, outcome,
                                       slot_id, obligation.key))
    coord.next_use_epoch = shadow.next_epoch
    coord.next_fill_incarnation = shadow.incarnation
    return tokens


@dataclass
class BatchCacheReservation:
    batch_id: int
    status: str
    tokens: tuple = ()
    failed_key: WeightCacheBaseKey = None

    def tokens_of(self, core_id: int) -> tuple:
        return tuple(token for token in self.tokens
                     if token.base_key.core_id == core_id)


class BatchCacheReservationSet:
    """All-or-none batch-wide reservation across cores (7.8, 17.3-28).

    Every core is probed in ascending core-id order without mutating state, so
    a shallow resource on one core leaves the whole batch uncommitted and no
    two batches can hold each other's resources.
    """

    def __init__(self, coordinators):
        self.coordinators = dict(sorted(coordinators.items()))
        self.members = {}

    def reserve(self, batch_id, occurrences_by_core, tick=0):
        unknown = sorted(set(occurrences_by_core) - set(self.coordinators))
        if unknown:
            raise MeshIrError("E_MOE_CACHE",
                              "batch demand names an unknown core",
                              core_ids=unknown)
        probes = {}
        for core_id, coordinator in self.coordinators.items():
            occurrences = tuple(occurrences_by_core.get(core_id, ()))
            if not occurrences:
                continue
            shadow, (status, failure) = probe_batch_wide(coordinator,
                                                         occurrences)
            if shadow is None:
                return BatchCacheReservation(
                    batch_id=batch_id, status=status,
                    failed_key=failure[0] if failure else None)
            probes[core_id] = (coordinator, shadow, occurrences)
        tokens = []
        for core_id, (coordinator, shadow, occurrences) in probes.items():
            applied = apply_batch_wide(coordinator, shadow, batch_id,
                                       occurrences, tick)
            tokens.extend(applied)
            self.members[(batch_id, core_id)] = tuple(applied)
        return BatchCacheReservation(batch_id=batch_id,
                                     status=RESERVE_COMMITTED,
                                     tokens=tuple(tokens))

    def cancel_member(self, batch_id):
        for core_id, coordinator in self.coordinators.items():
            coordinator.cancel_member(batch_id)

    def abort_before_start(self, batch_id):
        for core_id, coordinator in self.coordinators.items():
            coordinator.abort_before_start(batch_id)

    def note_failure_fanout_done(self, batch_id):
        for core_id, coordinator in self.coordinators.items():
            coordinator.note_failure_fanout_done(batch_id)

    def note_owned_work_drained(self, batch_id):
        for core_id, coordinator in self.coordinators.items():
            coordinator.note_owned_work_drained(batch_id)

    def release_batch(self, batch_id):
        for core_id, coordinator in self.coordinators.items():
            for token in sorted(coordinator.tokens.values(),
                                key=lambda entry: entry.sort_key()):
                if token.batch_id == batch_id:
                    coordinator.release_token(token.token_id)


@dataclass
class ArbitratedIssue:
    source_kind: int
    eligible_tick: int
    effective_qos: int
    key: tuple
    payload: object = None

    def sort_key(self) -> tuple:
        return (self.eligible_tick, 255 - self.effective_qos,
                self.source_kind, self.key)


def arbitrate_issues(candidates, issue_width):
    return sorted(candidates, key=lambda entry: entry.sort_key())[:issue_width]


def runtime_issue(command_id, eligible_tick, qos=0, payload=None):
    return ArbitratedIssue(source_kind=ISSUE_SOURCE_RUNTIME,
                           eligible_tick=eligible_tick, effective_qos=qos,
                           key=(int(command_id),), payload=payload)


def cache_fill_issue(fill_key, reservation_commit_tick, batch_qos=0,
                     segment_ordinal=0, payload=None):
    return ArbitratedIssue(
        source_kind=ISSUE_SOURCE_CACHE_FILL,
        eligible_tick=reservation_commit_tick, effective_qos=batch_qos,
        key=(*fill_key.sort_key(), segment_ordinal), payload=payload)


CACHE_STATE_REPLAY_SCHEMA = "cache_state_replay_v1"
CACHE_FILL_TRAFFIC_SCHEMA = "cache_fill_traffic_report_v1"


def cache_state_replay_document(coord, digests: dict) -> dict:
    core = coord.snapshot()
    valid_tags = [slot["weight_tag_index"] for slot in core["slots"]
                  if slot["state"] == A.CACHE_SLOT_STATE.VALID]
    if len(set(valid_tags)) != len(valid_tags):
        raise MeshIrError("E_MOE_CACHE", "duplicate VALID cache tag")
    tombstone_tags = [tombstone["weight_tag_index"] for tombstone in
                      core["failure_tombstones"]]
    if set(tombstone_tags) & set(valid_tags):
        raise MeshIrError("E_MOE_CACHE",
                          "a tag cannot be VALID and tombstoned")
    ranks = [slot["lru_rank"] for slot in core["slots"]
             if slot["state"] == A.CACHE_SLOT_STATE.VALID]
    if sorted(ranks) != list(range(len(ranks))):
        raise MeshIrError("E_MOE_CACHE", "LRU ranks must be dense")
    return {
        "schema": CACHE_STATE_REPLAY_SCHEMA,
        "version": 1,
        "effective_architecture_digest": digests["effective_architecture"],
        "program_weight_registry_digest": digests["program_weight_registry"],
        "model_weight_image_digest": digests["model_weight_image"],
        "weight_tag_manifest_digest": digests["weight_tag_manifest"],
        "cores": [core],
    }


def apply_cache_state_replay(coord, document) -> None:
    from mesh_ir.acceptance import validate_schema

    validate_schema("cache_state_replay.schema.json", document)
    core = document["cores"][0]
    if core["core_id"] != coord.core_id:
        raise MeshIrError("E_MOE_CACHE", "replay artifact is for another core",
                          core_id=core["core_id"])
    if core["cache_generation"] != coord.cache_generation:
        raise MeshIrError("E_MOE_CACHE",
                          "replay artifact is for another cache generation")
    ranks = []
    for slot in core["slots"]:
        target = coord.slots[slot["slot_id"]]
        if slot["state"] == A.CACHE_SLOT_STATE.INVALID:
            if slot["weight_tag_index"] is not None or slot["valid_bytes"]:
                raise MeshIrError("E_MOE_CACHE",
                                  "an invalid replay slot carries no tag")
            continue
        if slot["weight_tag_index"] not in coord.cacheable_tags:
            raise MeshIrError("E_MOE_CACHE",
                              "replay artifact holds an uncacheable tag")
        target.state = SLOT_VALID
        target.weight_tag_index = slot["weight_tag_index"]
        target.valid_bytes = slot["valid_bytes"]
        target.last_use_epoch = slot["lru_rank"]
        ranks.append(slot["lru_rank"])
    if sorted(ranks) != list(range(len(ranks))):
        raise MeshIrError("E_MOE_CACHE", "replay LRU ranks must be dense")
    valid_tags = [slot.weight_tag_index for slot in coord.slots
                  if slot.state == SLOT_VALID]
    if len(set(valid_tags)) != len(valid_tags):
        raise MeshIrError("E_MOE_CACHE", "replay holds a duplicate VALID tag")
    for tombstone in core["failure_tombstones"]:
        source = ErrorSourceKey.from_wire_bytes(bytes.fromhex(
            tombstone["first_error"]["source_key_wire"]))
        coord.failure_table[coord.base_key(tombstone["weight_tag_index"])] = \
            FailureTombstone(
                weight_tag_index=tombstone["weight_tag_index"],
                first_error_tick=tombstone["first_error"]["tick"],
                first_error_source=source,
                first_error_code=tombstone["first_error"]["error_code"])
        if tombstone["weight_tag_index"] in valid_tags:
            raise MeshIrError("E_MOE_CACHE",
                              "a replay tag cannot be VALID and tombstoned")
    coord.next_use_epoch = len(ranks)
    coord.next_fill_incarnation = core["next_fill_incarnation"]


def cache_fill_traffic_document(coord, digests: dict, fills) -> dict:
    ordered = sorted(fills, key=lambda entry: entry["key"].sort_key())
    seen = set()
    report = []
    for entry in ordered:
        key = entry["key"]
        if key.sort_key() in seen:
            raise MeshIrError("E_MOE_CACHE", "duplicate full fill id")
        seen.add(key.sort_key())
        report.append({
            "key_fields": {
                "core_id": key.base.core_id,
                "cache_partition_id": key.base.cache_partition_id,
                "weight_tag_index": key.base.weight_tag_index,
                "cache_generation": key.base.cache_generation,
                "fill_incarnation": key.fill_incarnation,
            },
            "fill_traffic_id": key.traffic_id(),
            "slot_id": entry["slot_id"],
            "source_addr": entry["source_addr"],
            "destination_addr": entry["destination_addr"],
            "valid_bytes": entry["valid_bytes"],
            "descriptor_key": key.descriptor_key(0).hex(),
            "expected_bursts": entry["expected_bursts"],
            "subscriber_semantic_ids": sorted(
                entry["subscriber_semantic_ids"]),
            "outcome": entry["outcome"],
            "actual_read_bytes": entry.get("actual_read_bytes", 0),
            "actual_sram_commit_bytes":
                entry.get("actual_sram_commit_bytes", 0),
            "discarded_error_bytes": entry.get("discarded_error_bytes", 0),
        })
    return {
        "schema": CACHE_FILL_TRAFFIC_SCHEMA,
        "version": 1,
        "effective_architecture_digest": digests["effective_architecture"],
        "program_weight_registry_digest": digests["program_weight_registry"],
        "model_weight_image_digest": digests["model_weight_image"],
        "weight_tag_manifest_digest": digests["weight_tag_manifest"],
        "fills": report,
    }

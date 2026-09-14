"""Dynamic MoE overlay object model (contract 7.2.1/7.2.3/7.9.2).

Static program IDs and overlay ordinals live in two ID domains.  Every
overlay object carries the full identity
`{program_instance, region_group_id=layer, region_id, kind, ordinal}` and a
canonical object key; ordinals are assigned only here, per region and kind,
from 1.  Scratch placement is a single checked bump allocator over the
region's `RUNTIME_SCRATCH` interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError

INVALID_U16 = 0xFFFF
MODULE_GROUP_REGION = 0
OVERLAY_KINDS = (
    A.MESH_OBJECT_KIND.COMMAND,
    A.MESH_OBJECT_KIND.EVENT,
    A.MESH_OBJECT_KIND.DESCRIPTOR,
    A.MESH_OBJECT_KIND.TRANSFER,
    A.MESH_OBJECT_KIND.ALLOCATION,
    A.MESH_OBJECT_KIND.VIEW,
)
GROUP_LEVEL_KINDS = (A.MESH_OBJECT_KIND.TRANSFER,)
ALLOCATION_ORDER = (
    A.MOE_ALLOCATION_KIND.ROUTE_BUFFER,
    A.MOE_ALLOCATION_KIND.STREAMED_WEIGHT,
    A.MOE_ALLOCATION_KIND.DISPATCH_REMOTE,
    A.MOE_ALLOCATION_KIND.PAD_INPUT,
    A.MOE_ALLOCATION_KIND.EXPERT_OUTPUT,
    A.MOE_ALLOCATION_KIND.COMBINE_REMOTE,
    A.MOE_ALLOCATION_KIND.REDUCE_OUTPUT,
)


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise MeshIrError("E_ABI_BOUNDS", "alignment must be a power of two")
    return (value + alignment - 1) & ~(alignment - 1)


@dataclass(frozen=True)
class OverlayKey:
    region_group_id: int
    region_id: int
    kind: int
    ordinal: int = 0

    def with_ordinal(self, ordinal: int) -> "OverlayKey":
        return replace(self, ordinal=ordinal)

    def sort_key(self) -> tuple:
        return (self.region_id, self.kind, self.ordinal)


@dataclass(frozen=True)
class ValidityShape:
    kind: int
    valid_bytes: int = 0
    row_bytes: int = 0
    row_count: int = 0
    bitmap_lowerhex: str = ""

    def validate(self, allocation_bytes: int) -> None:
        if self.kind == A.MOE_VALIDITY_KIND.FULL_PREFIX:
            if self.row_bytes or self.row_count or self.bitmap_lowerhex:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "FULL_PREFIX carries no row geometry")
            if not 0 <= self.valid_bytes <= allocation_bytes:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "FULL_PREFIX valid bytes escape allocation")
            return
        if self.kind != A.MOE_VALIDITY_KIND.ROW_BITMAP:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "unknown validity kind")
        if self.row_bytes == 0 or self.row_count == 0:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "ROW_BITMAP needs positive row geometry")
        if self.row_bytes * self.row_count != allocation_bytes:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "ROW_BITMAP rows must tile the allocation")
        expected_bytes = (self.row_count + 7) // 8
        if len(self.bitmap_lowerhex) != 2 * expected_bytes:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "ROW_BITMAP hex length mismatch")
        bitmap = bytes.fromhex(self.bitmap_lowerhex)
        if self.row_count % 8:
            mask = (1 << (self.row_count % 8)) - 1
            if bitmap[-1] & ~mask:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "ROW_BITMAP tail bits must be zero")


@dataclass(frozen=True)
class AllocationRecord:
    owner_core: int
    allocation_kind: int
    expert_id: int
    source_core: int
    phase: int
    bytes: int
    alignment: int
    validity: ValidityShape
    key: OverlayKey

    def canonical_key(self) -> tuple:
        return (self.owner_core, self.allocation_kind, self.expert_id,
                self.source_core, self.phase)


@dataclass(frozen=True)
class ViewRecord:
    owner_core: int
    view_kind: int
    access: int
    validity_slices: tuple
    backing_kind: int
    backing_ref: tuple
    offset: int
    bytes: int
    semantic_owner_kind: int
    semantic_owner_ref: tuple
    token_ref: tuple
    key: OverlayKey

    def canonical_key(self) -> tuple:
        return (self.owner_core, self.view_kind, self.access,
                self.validity_slices, self.backing_kind, self.backing_ref,
                self.offset, self.bytes, self.semantic_owner_kind,
                self.semantic_owner_ref, self.token_ref)


@dataclass(frozen=True)
class TransferRecord:
    phase: int
    src_core: int
    dst_core: int
    expert_id: int
    chunk_ordinal: int
    logical_bytes: int
    key: OverlayKey

    def canonical_key(self) -> tuple:
        return (self.phase, self.src_core, self.dst_core, self.expert_id,
                self.chunk_ordinal)


@dataclass(frozen=True)
class DescriptorRecord:
    moe_kind: int
    owner_core: int
    phase: int
    src_core: int
    dst_core: int
    expert_id: int
    chunk_ordinal: int
    token_ref: tuple
    valid_bytes: int = 0
    source_view_ref: tuple = ()
    destination_view_ref: tuple = ()
    transfer_ref: tuple = ()
    fill_content: bytes = b""
    key: OverlayKey = None

    def traffic_class(self) -> int:
        return traffic_class_of(self.moe_kind)

    def canonical_key(self) -> tuple:
        return (self.moe_kind, self.owner_core, self.phase, self.src_core,
                self.dst_core, self.expert_id, self.chunk_ordinal,
                self.token_ref)


TRAFFIC_CLASS_BY_DESCRIPTOR = {
    A.MOE_DESCRIPTOR_KIND.ROUTE_FILL: A.MOE_TRAFFIC_CLASS.ACTIVATION,
    A.MOE_DESCRIPTOR_KIND.STREAMED_WEIGHT: A.MOE_TRAFFIC_CLASS.WEIGHT,
    A.MOE_DESCRIPTOR_KIND.PAD_FILL: A.MOE_TRAFFIC_CLASS.ACTIVATION,
    A.MOE_DESCRIPTOR_KIND.DISPATCH: A.MOE_TRAFFIC_CLASS.MOE_DISPATCH,
    A.MOE_DESCRIPTOR_KIND.COMBINE: A.MOE_TRAFFIC_CLASS.MOE_COMBINE,
    A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL:
        A.MOE_TRAFFIC_CLASS.PARTIAL_RESULT,
}


def traffic_class_of(moe_kind: int) -> int:
    if moe_kind not in TRAFFIC_CLASS_BY_DESCRIPTOR:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "unknown MoE descriptor kind", moe_kind=moe_kind)
    return TRAFFIC_CLASS_BY_DESCRIPTOR[moe_kind]


@dataclass(frozen=True)
class EventRecord:
    event_phase: int
    owner_core: int
    phase: int
    src_core: int
    dst_core: int
    expert_id: int
    chunk_ordinal: int
    role: int
    token_ref: tuple
    producers: tuple
    consumers: tuple = ()
    key: OverlayKey = None

    def canonical_key(self) -> tuple:
        return (self.event_phase, self.owner_core, self.phase, self.src_core,
                self.dst_core, self.expert_id, self.chunk_ordinal, self.role,
                self.token_ref)


@dataclass(frozen=True)
class CommandRecord:
    phase: int
    opcode: int
    owner_core: int
    src_core: int
    dst_core: int
    expert_id: int
    chunk_ordinal: int
    role: int
    token_ref: tuple
    wait_refs: tuple = ()
    signal_refs: tuple = ()
    descriptor_refs: tuple = ()
    transfer_refs: tuple = ()
    view_refs: tuple = ()
    kernel_spec_index: int = 0
    payload_bytes: int = 0
    key: OverlayKey = None

    def canonical_key(self) -> tuple:
        return (self.phase, self.owner_core, self.src_core, self.dst_core,
                self.expert_id, self.chunk_ordinal, self.role, self.token_ref)


RECORD_TYPES = {
    A.MESH_OBJECT_KIND.ALLOCATION: AllocationRecord,
    A.MESH_OBJECT_KIND.VIEW: ViewRecord,
    A.MESH_OBJECT_KIND.TRANSFER: TransferRecord,
    A.MESH_OBJECT_KIND.DESCRIPTOR: DescriptorRecord,
    A.MESH_OBJECT_KIND.EVENT: EventRecord,
    A.MESH_OBJECT_KIND.COMMAND: CommandRecord,
}


@dataclass
class Overlay:
    program_instance_id: int
    layer_id: int
    objects: dict = field(default_factory=dict)

    def add(self, kind: int, record) -> None:
        self.objects.setdefault(kind, []).append(record)

    def of_kind(self, kind: int) -> list:
        return self.objects.get(kind, [])

    def key_of(self, kind: int, record) -> OverlayKey:
        return OverlayKey(region_group_id=self.layer_id,
                          region_id=self._region_of(kind, record),
                          kind=kind, ordinal=record.key.ordinal)

    @staticmethod
    def _region_of(kind: int, record) -> int:
        if kind in GROUP_LEVEL_KINDS:
            return MODULE_GROUP_REGION
        return record.key.region_id

    def region_count(self) -> int:
        regions = set()
        for kind in OVERLAY_KINDS:
            for record in self.of_kind(kind):
                regions.add(self._region_of(kind, record))
        regions.discard(MODULE_GROUP_REGION)
        return len(regions)


REF_FIELDS = ("producers", "wait_refs", "signal_refs", "descriptor_refs",
              "transfer_refs", "view_refs", "source_view_ref",
              "destination_view_ref")


@dataclass
class OverlayBuilder:
    """Two-phase overlay construction.

    Objects are added with creation-order handles; references between them
    are recorded as handles and only rewritten into final ordinals once
    every object has its canonical position.  `finish()` refuses to emit an
    overlay while any handle is unresolved.
    """
    program_instance_id: int
    layer_id: int
    _objects: dict = field(default_factory=dict)
    _handles: dict = field(default_factory=dict)
    _next: dict = field(default_factory=dict)
    _dedup: dict = field(default_factory=dict)

    def add(self, kind: int, record) -> tuple:
        region_id = Overlay._region_of(kind, record)
        canonical = record.canonical_key()
        existing = self._dedup.get((kind, region_id, canonical))
        if existing is not None:
            if _content_of(self._objects[existing]) != _content_of(record):
                raise MeshIrError(
                    "E_MOE_KEY_COLLISION",
                    "one canonical key maps to different overlay objects",
                    kind=kind, region_id=region_id)
            return existing
        local = self._next.get((kind, region_id), 1)
        self._next[(kind, region_id)] = local + 1
        handle = (region_id, kind, local)
        self._objects[handle] = record
        self._dedup[(kind, region_id, canonical)] = handle
        self._handles.setdefault(kind, []).append(handle)
        return handle

    def _ordinal_map(self) -> dict:
        mapping = {}
        buckets = {}
        for handle in self._objects:
            buckets.setdefault((handle[1], handle[0]), []).append(handle)
        for (kind, region_id), handles in buckets.items():
            ordered = sorted(handles,
                             key=lambda handle: self._objects[handle].
                             canonical_key())
            del kind, region_id
            for ordinal, handle in enumerate(ordered, start=1):
                mapping[handle] = ordinal
        return mapping

    def finish(self, external_refs=frozenset()) -> Overlay:
        ordinals = self._ordinal_map()
        overlay = Overlay(program_instance_id=self.program_instance_id,
                          layer_id=self.layer_id)
        for handle, record in self._objects.items():
            region_id, kind, _ = handle
            ordinal = ordinals[handle]
            key = OverlayKey(region_group_id=self.layer_id,
                             region_id=region_id, kind=kind, ordinal=ordinal)
            overlay.add(kind, self._rewrite(record, ordinals, key,
                                            external_refs))
        return overlay

    def _rewrite(self, record, ordinals, key, external_refs):
        changes = {"key": key}
        for name in REF_FIELDS:
            value = getattr(record, name, None)
            if not value:
                continue
            changes[name] = tuple(self._resolve(ref, ordinals, external_refs)
                                  for ref in value)
        backing = getattr(record, "backing_ref", None)
        if backing and getattr(record, "backing_kind", None) == \
                A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION:
            changes["backing_ref"] = (backing[0],
                                      self._resolve((backing[0],
                                                     A.MESH_OBJECT_KIND.
                                                     ALLOCATION,
                                                     backing[1]),
                                                    ordinals,
                                                    external_refs)[2])
        if getattr(record, "transfer_ref", ()):
            changes["transfer_ref"] = self._resolve(record.transfer_ref,
                                                    ordinals, external_refs)
        return replace(record, **changes)

    def _resolve(self, ref, ordinals, external_refs=frozenset()) -> tuple:
        if not isinstance(ref, tuple) or len(ref) != 3:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "overlay reference must be a handle")
        if ref in external_refs:
            return ref
        if ref not in ordinals:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "unresolved overlay reference",
                              reference=repr(ref))
        return (ref[0], ref[1], ordinals[ref])


def _content_of(record) -> tuple:
    return tuple(sorted((name, repr(value))
                        for name, value in vars(record).items()
                        if name != "key"))


def canonical_order(records) -> list:
    return sorted(records, key=lambda record: record.canonical_key())


def assign_ordinals(overlay: Overlay) -> Overlay:
    assigned = Overlay(program_instance_id=overlay.program_instance_id,
                       layer_id=overlay.layer_id)
    seen = set()
    for kind in OVERLAY_KINDS:
        buckets = {}
        for record in overlay.of_kind(kind):
            region_id = Overlay._region_of(kind, record)
            buckets.setdefault(region_id, []).append(record)
        for region_id, records in sorted(buckets.items()):
            ordered = canonical_order(records)
            for ordinal, record in enumerate(ordered, start=1):
                key = record.canonical_key()
                marker = (kind, region_id, key)
                if marker in seen:
                    raise MeshIrError(
                        "E_MOE_KEY_COLLISION",
                        "two overlay objects share one canonical key",
                        kind=kind, region_id=region_id)
                seen.add(marker)
                assigned.add(kind, replace(
                    record, key=OverlayKey(region_group_id=overlay.layer_id,
                                           region_id=region_id, kind=kind,
                                           ordinal=ordinal)))
    return assigned


def group_level_events(overlay: Overlay) -> list:
    return [record for record in overlay.of_kind(A.MESH_OBJECT_KIND.EVENT)
            if record.role == A.MOE_EVENT_ROLE.OVERLAY_EXIT]


def allocate_scratch(overlay: Overlay, region_scratch: dict) -> dict:
    """Bump-allocate every region's scratch allocations.

    `region_scratch` maps region_id to `(offset, bytes, alignment)`; the
    returned map gives each allocation ordinal its absolute interval.
    """
    allocations = overlay.of_kind(A.MESH_OBJECT_KIND.ALLOCATION)
    by_region = {}
    for record in allocations:
        by_region.setdefault(record.key.region_id, []).append(record)
    intervals = {}
    for region_id, records in sorted(by_region.items()):
        if region_id not in region_scratch:
            raise MeshIrError("E_MOE_MATERIALIZE_CAPACITY",
                              "region has no scratch interval",
                              region_id=region_id)
        base, size, region_alignment = region_scratch[region_id]
        cursor = base
        end = base + size
        ordered = sorted(records, key=lambda record: (
            ALLOCATION_ORDER.index(record.allocation_kind),
            record.canonical_key()))
        for record in ordered:
            alignment = max(record.alignment, region_alignment)
            cursor = align_up(cursor, alignment)
            if cursor + record.bytes > end:
                raise MeshIrError("E_MOE_MATERIALIZE_CAPACITY",
                                  "region scratch is exhausted",
                                  region_id=region_id)
            record.validity.validate(record.bytes)
            intervals[(region_id, record.key.ordinal)] = (cursor,
                                                          record.bytes)
            cursor += record.bytes
    return intervals


def verify_overlay(overlay: Overlay, bounds: dict,
                   external_events=frozenset(),
                   program_allocations=()) -> None:
    _verify_density(overlay)
    _verify_allocations(overlay)
    _verify_views(overlay, program_allocations)
    _verify_events(overlay)
    _verify_commands(overlay)
    _verify_bounds(overlay, bounds)
    _verify_dag(overlay, external_events)


def _verify_density(overlay: Overlay) -> None:
    for kind in OVERLAY_KINDS:
        buckets = {}
        for record in overlay.of_kind(kind):
            region_id = Overlay._region_of(kind, record)
            buckets.setdefault(region_id, []).append(record.key.ordinal)
        for region_id, ordinals in buckets.items():
            if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "overlay ordinals must be dense from 1",
                                  kind=kind, region_id=region_id)


def _verify_allocations(overlay: Overlay) -> None:
    for record in overlay.of_kind(A.MESH_OBJECT_KIND.ALLOCATION):
        if record.bytes <= 0:
            raise MeshIrError("E_MOE_MATERIALIZE_CAPACITY",
                              "overlay allocation must be non-empty")
        if record.alignment <= 0 or record.alignment & (record.alignment - 1):
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "allocation alignment must be a power of two")
        if record.owner_core == INVALID_U16:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "allocation needs an owner core")


def _verify_views(overlay: Overlay, program_allocations=()) -> None:
    allocations = {}
    for record in overlay.of_kind(A.MESH_OBJECT_KIND.ALLOCATION):
        allocations[(record.key.region_id, record.key.ordinal)] = record
    for record in overlay.of_kind(A.MESH_OBJECT_KIND.VIEW):
        if record.access not in (A.MOE_VIEW_ACCESS.READ,
                                 A.MOE_VIEW_ACCESS.WRITE,
                                 A.MOE_VIEW_ACCESS.READ_WRITE):
            raise MeshIrError("E_MOE_MATERIALIZATION_V", "unknown view access")
        if record.backing_kind not in vars(A.MOE_VIEW_BACKING).values():
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "unknown view backing kind")
        if len(record.validity_slices) > 8:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "view validity rank above 8")
        for offset, extent in record.validity_slices:
            if extent <= 0 or offset < 0:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "validity slice must be positive")
        if record.bytes <= 0:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "view must cover at least one byte")
        if record.backing_kind == A.MOE_VIEW_BACKING.STATIC_ALLOCATION and \
                program_allocations:
            target = next(
                (allocation for allocation in program_allocations
                 if allocation.allocation_id == record.backing_ref[0]), None)
            if target is None:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "view names an unknown program allocation")
            if record.owner_core != INVALID_U16 and \
                    target.owner_core != record.owner_core:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "view crosses the program allocation owner")
            if record.offset + record.bytes > target.size_bytes:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "view escapes its program allocation")
        if record.backing_kind == A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION:
            target = allocations.get(tuple(record.backing_ref))
            if target is None:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "view backing allocation is missing")
            if record.offset + record.bytes > target.bytes:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "view escapes its backing allocation")
            if record.access == A.MOE_VIEW_ACCESS.READ:
                shape = target.validity
                if shape.kind == A.MOE_VALIDITY_KIND.FULL_PREFIX and \
                        record.offset + record.bytes > shape.valid_bytes:
                    raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                      "read escapes the valid prefix")


def _verify_events(overlay: Overlay) -> None:
    for record in overlay.of_kind(A.MESH_OBJECT_KIND.EVENT):
        if not record.producers:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "overlay event needs a producer")
        if len(record.producers) != 1:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "overlay event needs exactly one producer")


def _verify_commands(overlay: Overlay) -> None:
    for record in overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND):
        if record.role == A.MOE_COMMAND_ROLE.EMPTY_REGION_TERMINAL:
            if record.payload_bytes or record.descriptor_refs or \
                    record.transfer_refs:
                raise MeshIrError(
                    "E_MOE_MATERIALIZATION_V",
                    "empty region terminal carries no payload")
        if record.role in (A.MOE_COMMAND_ROLE.LOCAL_REDUCE,
                           A.MOE_COMMAND_ROLE.COPY_THROUGH) and \
                not record.view_refs:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "combine command needs an explicit view")


def _verify_bounds(overlay: Overlay, bounds: dict) -> None:
    per_region = bounds["per_region"]
    group = bounds["group"]
    counts = {}
    for kind in (A.MESH_OBJECT_KIND.COMMAND, A.MESH_OBJECT_KIND.EVENT,
                 A.MESH_OBJECT_KIND.DESCRIPTOR, A.MESH_OBJECT_KIND.ALLOCATION):
        for record in overlay.of_kind(kind):
            region_id = record.key.region_id
            counts[(region_id, kind)] = counts.get((region_id, kind), 0) + 1
            if region_id == MODULE_GROUP_REGION:
                continue
            limit = per_region[region_id][kind]
            if counts[(region_id, kind)] > limit:
                raise MeshIrError("E_MOE_MATERIALIZE_CAPACITY",
                                  "per-region overlay bound exceeded",
                                  region_id=region_id, kind=kind)
    transfers = len({record.canonical_key() for record in
                     overlay.of_kind(A.MESH_OBJECT_KIND.TRANSFER)})
    events = sum(1 for record in overlay.of_kind(A.MESH_OBJECT_KIND.EVENT))
    commands = sum(1 for record in overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND))
    descriptors = sum(1 for record in
                      overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR))
    allocations = sum(1 for record in
                      overlay.of_kind(A.MESH_OBJECT_KIND.ALLOCATION))
    for name, actual, limit in (
            ("max_materialized_commands", commands,
             group[A.MESH_OBJECT_KIND.COMMAND]),
            ("max_materialized_events", events,
             group[A.MESH_OBJECT_KIND.EVENT]),
            ("max_materialized_descriptors", descriptors,
             group[A.MESH_OBJECT_KIND.DESCRIPTOR]),
            ("max_materialized_transfers", transfers,
             group[A.MESH_OBJECT_KIND.TRANSFER]),
            ("max_dynamic_allocations", allocations,
             group[A.MESH_OBJECT_KIND.ALLOCATION])):
        if actual > limit:
            raise MeshIrError("E_MOE_MATERIALIZE_CAPACITY",
                              f"group bound {name} exceeded",
                              actual=actual, limit=limit)


def _verify_dag(overlay: Overlay, external_events=frozenset()) -> None:
    """Dependency check over the materialized overlay.

    Waits on an event resolve to that event's unique producer; waits on a
    command/descriptor/transfer are direct drain-join edges, which is how
    the region terminal waits for pushes that signal no event of their own.
    """
    producers = {}
    for record in overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND):
        node = (A.MESH_OBJECT_KIND.COMMAND, record.key.region_id,
                record.key.ordinal)
        for event in record.signal_refs:
            if len(event) != 3:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "overlay signal must be a typed reference")
            key = (event[0], event[1], event[2])
            if key in producers:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "overlay event has two producers")
            producers[key] = node
    defined = set()
    for kind in OVERLAY_KINDS:
        for record in overlay.of_kind(kind):
            defined.add((record.key.region_id, kind, record.key.ordinal))
    waiting = {}
    for record in overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND):
        node = (A.MESH_OBJECT_KIND.COMMAND, record.key.region_id,
                record.key.ordinal)
        edges = set()
        for ref in record.wait_refs:
            if len(ref) != 3:
                raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                  "overlay wait must be a typed reference")
            kind = ref[1]
            if kind == A.MESH_OBJECT_KIND.EVENT:
                if ref in external_events:
                    continue
                if ref not in producers:
                    raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                      "overlay wait has no producer",
                                      event=repr(ref))
                edges.add(producers[ref])
            else:
                if ref not in defined:
                    raise MeshIrError("E_MOE_MATERIALIZATION_V",
                                      "overlay wait references a missing "
                                      "object", reference=repr(ref))
                edges.add((kind, ref[0], ref[2]))
        waiting[node] = edges
    state = {}

    def visit(node, stack):
        mark = state.get(node)
        if mark == 1:
            raise MeshIrError("E_MOE_MATERIALIZATION_V",
                              "overlay dependency cycle", cycle=list(stack))
        if mark == 2:
            return
        state[node] = 1
        for target in sorted(waiting.get(node, ())):
            visit(target, stack + (node,))
        state[node] = 2

    for node in sorted(waiting):
        visit(node, ())

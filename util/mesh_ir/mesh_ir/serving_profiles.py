"""Agent serving profile compiler helpers (main contract 10.3.1).

Owns the requested_profile_key double projection (zero-key base digest and
the final per-request key) and, later in Gate 6 R1, the exact selector,
Host/KV interval oracle and CapacityPlan derivation.
"""

from __future__ import annotations

import dataclasses
import hashlib

from mesh_ir.execution_view import build_execution_views
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.serving_tensor_bindings import ResolvedMemberTensorBinding, ServingTensorBindingCatalog
from mesh_ir.scheduled.model import EndpointSide
from mesh_ir.model import MeshIrError, Program, canonical_json_bytes

KEY_BASE_DOMAIN = b"AGENT_PROFILE_KEY_BASE_V1\0"
REQUEST_KEY_DOMAIN = b"AGENT_REQUEST_PROFILE_V1\0"

KEY_FIELD = "requested_profile_key"


def program_profile_key_base_bytes(program: Program) -> bytes:
    projection = canonical_json_bytes(_zeroed(program).semantic_dict())
    return KEY_BASE_DOMAIN + projection


def program_profile_key_base_digest(program: Program) -> bytes:
    return hashlib.sha256(program_profile_key_base_bytes(program)).digest()


def request_profile_key(record, base_digest: bytes) -> int:
    wire = _pack_request(record, requested_profile_key=0)
    digest = hashlib.sha256(
        REQUEST_KEY_DOMAIN + base_digest + wire).digest()
    return int.from_bytes(digest[:8], "little")


def compute_request_profile_keys(program: Program) -> tuple:
    base = program_profile_key_base_digest(program)
    return tuple(request_profile_key(record, base)
                 for record in program.agent_request_profiles)


def apply_request_profile_keys(program: Program) -> Program:
    keys = compute_request_profile_keys(program)
    if len(set(keys)) != len(keys):
        raise MeshIrError("E_REQUEST_PROFILE_KEY",
                          "request profile keys must be unique")
    if any(key == 0 for key in keys):
        raise MeshIrError("E_REQUEST_PROFILE_KEY",
                          "request profile key must be nonzero")
    records = tuple(
        dataclasses.replace(record, requested_profile_key=key)
        for record, key in zip(program.agent_request_profiles, keys)
    )
    result = dataclasses.replace(program, agent_request_profiles=records)
    return dataclasses.replace(result, semantic_sha256=semantic_sha256(result.semantic_dict()))


def verify_request_profile_keys(program: Program) -> None:
    base = program_profile_key_base_digest(program)
    keys = []
    for record in program.agent_request_profiles:
        expected = request_profile_key(record, base)
        if record.requested_profile_key == 0:
            raise MeshIrError("E_REQUEST_PROFILE_KEY",
                              "request profile key must be nonzero",
                              program_id=record.program_id,
                              profile_id=record.profile_id)
        if record.requested_profile_key != expected:
            raise MeshIrError("E_REQUEST_PROFILE_KEY",
                              "request profile key does not match the base "
                              "projection",
                              program_id=record.program_id,
                              profile_id=record.profile_id)
        keys.append(expected)
    if len(set(keys)) != len(keys):
        raise MeshIrError("E_REQUEST_PROFILE_KEY",
                          "request profile keys must be unique")


def _zeroed(program: Program) -> Program:
    records = tuple(
        dataclasses.replace(record, **{KEY_FIELD: 0})
        for record in program.agent_request_profiles
    )
    return dataclasses.replace(program, agent_request_profiles=records)


def _pack_request(record, requested_profile_key: int) -> bytes:
    fields = A.AGENT_REQUEST_PROFILES_FIELDS
    values = []
    for field in fields:
        if field["name"] == KEY_FIELD:
            values.append(requested_profile_key)
        elif field["name"] == "reserved0":
            values.append(0)
        else:
            values.append(getattr(record, field["name"]))
    return A.AGENT_REQUEST_PROFILES_FORMAT.pack(*values)


SELECTOR_FIELDS = (
    "request_program_id", "request_profile_id", "path_kind", "phase",
    "member_count", "valid_tokens_per_member", "kv_tokens_before",
    "decode_chunk_tokens",
)

HOST_LOAD = "host_input"
HOST_STORE = "host_output"
KV_LOAD = "kv_read"
KV_STORE = "kv_write"
WORKSPACE_LOAD = "workspace_read"
WORKSPACE_STORE = "workspace_write"


def member_records(program: Program, instance) -> tuple:
    first = instance.member_binding_first
    return tuple(program.agent_instance_member_bindings[
        first:first + instance.member_binding_count])


def member_rank_vector(program: Program, instance) -> tuple:
    return tuple(record.expected_logical_source_rank
                 for record in member_records(program, instance))


def member_source_core(program: Program, request, instance,
                       ordinal: int) -> int:
    rank = member_rank_vector(program, instance)[ordinal]
    return program.agent_source_core_map[
        request.source_core_map_begin + rank].core_id


def selector_tuple(program: Program, instance) -> tuple:
    scalars = tuple(getattr(instance, name) for name in SELECTOR_FIELDS)
    return scalars + (member_rank_vector(program, instance),)


class SelectorIndex:
    """Exact selector lookup keyed by the scalar tuple plus rank vector."""

    def __init__(self, program: Program):
        self.by_selector = {}
        for instance in program.agent_instance_profiles:
            key = selector_tuple(program, instance)
            if key in self.by_selector:
                raise MeshIrError("E_ABI_DUPLICATE",
                                  "duplicate instance selector",
                                  selector=key)
            self.by_selector[key] = instance

    def select(self, key: tuple):
        instance = self.by_selector.get(key)
        if instance is None:
            raise MeshIrError("E_REQUEST_PROFILE",
                              "no instance profile for the selector",
                              selector=key)
        return instance


def expected_selector(program_id: int, profile_id: int, path_kind: int,
                      phase: int, valid_tokens: int, kv_tokens_before: int,
                      chunk: int, ranks: tuple) -> tuple:
    return (program_id, profile_id, path_kind, phase, len(ranks),
            valid_tokens, kv_tokens_before, chunk, tuple(ranks))


def decode_sequence(output_tokens: int, chunk: int) -> tuple:
    sequence = []
    produced = 0
    while produced < output_tokens:
        size = min(chunk, output_tokens - produced)
        sequence.append((size, produced))
        produced += size
    return tuple(sequence)


def _request_instances(program: Program, request, path_kind: int,
                       phase: int) -> tuple:
    return tuple(
        instance for instance in program.agent_instance_profiles
        if instance.request_program_id == request.program_id and
        instance.request_profile_id == request.profile_id and
        instance.path_kind == path_kind and instance.phase == phase)


def _all_phase_instances(program: Program, request, path_kind: int) -> tuple:
    return tuple(
        instance for instance in program.agent_instance_profiles
        if instance.request_program_id == request.program_id and
        instance.request_profile_id == request.profile_id and
        instance.path_kind == path_kind)


def declared_rank_vectors(program: Program, request, path_kind: int) -> tuple:
    vectors = set()
    for ordinal in range(request.source_rank_count):
        vectors.add((ordinal,))
    for instance in _all_phase_instances(program, request, path_kind):
        vectors.add(member_rank_vector(program, instance))
    return tuple(sorted(vectors))


def declared_decode_chunk(program: Program, request, path_kind: int,
                          ranks: tuple) -> int:
    sizes = [
        instance.decode_chunk_tokens
        for instance in _all_phase_instances(program, request, path_kind)
        if instance.phase == A.PHASE.DECODE and
        member_rank_vector(program, instance) == ranks]
    return max(sizes) if sizes else 0


def verify_request_phase_mode(program: Program, request) -> bool:
    if request.flags not in (0, A.AGENT_REQUEST_FLAGS.HAS_KV):
        raise MeshIrError('E_REQUEST_PROFILE', 'unknown request mode')
    uses_kv = request.flags == A.AGENT_REQUEST_FLAGS.HAS_KV
    if uses_kv:
        if not (request.primary_kv_symbol_id and request.kv_bytes_per_token and request.output_tokens):
            raise MeshIrError('E_REQUEST_PROFILE', 'generation requires KV and output tokens')
    else:
        if (request.primary_kv_symbol_id or request.kv_bytes_per_token or
                request.output_tokens or request.expected_cached_tokens or
                request.path_mask != 1 << A.PATH_KIND.INITIAL_PREFILL or
                any((row.request_program_id, row.request_profile_id) == (request.program_id, request.profile_id)
                    and row.binding_kind == AG.BINDING_KIND.KV_EXTERNAL
                    for row in program.agent_request_binding_requirements)):
            raise MeshIrError('E_REQUEST_PROFILE', 'prefill-only request carries KV or generation state')
        for instance in program.agent_instance_profiles:
            if (instance.request_program_id, instance.request_profile_id) != (request.program_id, request.profile_id):
                continue
            if (instance.phase not in (A.PHASE.PREFILL, A.PHASE.PUBLISH) or
                    instance.path_kind != A.PATH_KIND.INITIAL_PREFILL or
                    instance.primary_kv_symbol_id or instance.kv_tokens_before or
                    instance.kv_read_bytes_per_member or instance.kv_write_bytes_per_member or
                    instance.decode_chunk_tokens or
                    any(row.instance_profile_id == instance.instance_profile_id and
                        row.binding_kind == AG.BINDING_KIND.KV_EXTERNAL
                        for row in program.agent_instance_tensor_bindings) or
                    any(row.static_kv_symbol_id for row in member_records(program, instance))):
                raise MeshIrError('E_REQUEST_PROFILE', 'prefill-only instance carries KV or generation state')
    return uses_kv


def planned_instance_sequence(program: Program, request, path_kind: int,
                              ranks: tuple = (0,)) -> tuple:
    """Derive the executed instance sequence of one request.

    The frozen plan is walked the way the serving runtime walks it: one PREFILL
    instance, then DECODE instances advancing a token cursor by the declared
    decode chunk until the request's output tokens are produced, then one
    PUBLISH instance.  Candidate records are filtered by request/profile/path;
    the sequence itself comes from the selector, the cursor and the decode
    chunk, never from a record count.
    """
    verify_path_closure(program, request)
    index = SelectorIndex(program)
    prefill = tuple(row for row in _request_instances(program, request, path_kind, A.PHASE.PREFILL)
                    if member_rank_vector(program, row) == ranks)
    publish = tuple(row for row in _request_instances(program, request, path_kind, A.PHASE.PUBLISH)
                    if member_rank_vector(program, row) == ranks)
    if len(prefill) != 1 or len(publish) != 1:
        raise MeshIrError("E_REQUEST_PROFILE", "rank vector needs exactly one PREFILL and one PUBLISH")
    chunk = prefill[0].decode_chunk_tokens
    if chunk == 0:
        candidates = [instance.decode_chunk_tokens for instance in
                      _request_instances(program, request, path_kind,
                                         A.PHASE.DECODE)
                      if member_rank_vector(program, instance) == ranks]
        chunk = max(candidates) if candidates else 0
    if chunk == 0 and request.output_tokens:
        raise MeshIrError("E_REQUEST_PROFILE", "request declares no decode chunk")
    sequence = [prefill[0]]
    for size, produced in decode_sequence(request.output_tokens, chunk):
        selector = expected_selector(
            request.program_id, request.profile_id, path_kind,
            A.PHASE.DECODE, size,
            request.full_input_tokens + produced, size,
            member_rank_vector(program, prefill[0]))
        matched = index.select(selector)
        if matched is None:
            raise MeshIrError("E_REQUEST_PROFILE", "decode step has no matching instance")
        sequence.append(matched)
    sequence.append(publish[0])
    return tuple(sequence)


def planned_instance_count(program: Program, request, path_kind: int) -> int:
    return len(planned_instance_sequence(program, request, path_kind))


def verify_path_closure(program: Program, request) -> None:
    uses_kv = verify_request_phase_mode(program, request)
    index = SelectorIndex(program)
    for path_kind in _declared_paths(request):
        prefills = _request_instances(program, request, path_kind,
                                      A.PHASE.PREFILL)
        singletons = [instance for instance in prefills
                      if instance.member_count == 1]
        if not singletons:
            raise MeshIrError("E_REQUEST_PROFILE",
                              "no singleton PREFILL profile for the path",
                              path_kind=path_kind)
        if path_kind == A.PATH_KIND.KV_REUSE:
            valid = request.delta_input_tokens
            kv_before = request.expected_cached_tokens
        else:
            valid = request.full_input_tokens
            kv_before = 0
        for instance in singletons:
            if (instance.valid_tokens_per_member != valid or
                    instance.kv_tokens_before != kv_before):
                raise MeshIrError(
                    "E_REQUEST_PROFILE",
                    "singleton PREFILL does not match the path formula",
                    path_kind=path_kind,
                    valid_tokens_per_member=instance.valid_tokens_per_member,
                    kv_tokens_before=instance.kv_tokens_before)
        for ranks in declared_rank_vectors(program, request, path_kind):
            index.select(expected_selector(
                request.program_id, request.profile_id, path_kind,
                A.PHASE.PREFILL, valid, kv_before, 0, ranks))
            chunk = declared_decode_chunk(program, request, path_kind, ranks)
            if request.output_tokens and not chunk:
                raise MeshIrError("E_REQUEST_PROFILE",
                                  "no DECODE profile for the path and rank",
                                  path_kind=path_kind, ranks=ranks)
            for size, produced in decode_sequence(request.output_tokens,
                                                  chunk):
                index.select(expected_selector(
                    request.program_id, request.profile_id, path_kind,
                    A.PHASE.DECODE, size,
                    request.full_input_tokens + produced, size, ranks))
            index.select(expected_selector(
                request.program_id, request.profile_id, path_kind,
                A.PHASE.PUBLISH, 0,
                request.full_input_tokens + request.output_tokens if uses_kv else 0, 0, ranks))


def _declared_paths(request) -> tuple:
    return tuple(path_kind for path_kind in (
        A.PATH_KIND.INITIAL_PREFILL, A.PATH_KIND.KV_REUSE,
        A.PATH_KIND.REPREFILL) if request.path_mask & (1 << path_kind))


ROLE_READS = {HOST_LOAD: True, HOST_STORE: False, KV_LOAD: True, KV_STORE: False,
              WORKSPACE_LOAD: True, WORKSPACE_STORE: False}
ROLE_KINDS = {
    HOST_LOAD: AG.BINDING_KIND.HOST_INPUT,
    HOST_STORE: AG.BINDING_KIND.HOST_OUTPUT,
    KV_LOAD: AG.BINDING_KIND.KV_EXTERNAL,
    KV_STORE: AG.BINDING_KIND.KV_EXTERNAL,
    WORKSPACE_LOAD: AG.BINDING_KIND.REQUEST_WORKSPACE,
    WORKSPACE_STORE: AG.BINDING_KIND.REQUEST_WORKSPACE,
}


@dataclasses.dataclass(frozen=True)
class MemberIoWindow:
    tensor: ResolvedMemberTensorBinding
    start: int
    end: int


class ServingInstanceIo:
    def __init__(self, program: Program, request, instance, catalog=None):
        self.program, self.request, self.instance = program, request, instance
        self.catalog = catalog if catalog is not None else ServingTensorBindingCatalog(program)
        if instance not in program.agent_instance_profiles:
            _fail('E_BINDING_ROLE', 'phase I/O profile differs from its admitted mapping')
        self.descriptors = tuple(instance_descriptors(program, instance))
        self.cores = tuple(member_source_core(program, request, instance, ordinal)
                           for ordinal in range(instance.member_count))
        self.windows = {(ordinal, role): [] for ordinal in range(instance.member_count) for role in ROLE_READS}
        for tensor in self.catalog.for_instance(instance.instance_profile_id):
            mapping = tensor.mapping
            for role, kind in ROLE_KINDS.items():
                if kind != mapping.binding_kind:
                    continue
                if role == HOST_LOAD:
                    begin = request.full_input_dma_bytes - request.delta_input_dma_bytes if instance.path_kind == A.PATH_KIND.KV_REUSE else 0
                    end = request.full_input_dma_bytes
                elif role == HOST_STORE:
                    begin, end = 0, instance.host_output_dma_bytes_per_member
                elif role == KV_LOAD:
                    begin, end = 0, instance.kv_tokens_before * request.kv_bytes_per_token
                elif role == KV_STORE:
                    begin = instance.kv_tokens_before * request.kv_bytes_per_token
                    end = begin + instance.kv_write_bytes_per_member
                else:
                    begin = mapping.offset_bytes
                    end = begin + tensor.binding.object.footprint_bytes
                start = max(begin, mapping.offset_bytes) - mapping.offset_bytes
                stop = min(end, mapping.offset_bytes + mapping.span_bytes) - mapping.offset_bytes
                if stop < start:
                    start = stop = 0
                if stop > tensor.binding.object.footprint_bytes:
                    _fail('E_BINDING_ROLE', 'member access exceeds its admitted object', role=role)
                self.windows[(mapping.member_ordinal, role)].append(MemberIoWindow(tensor, start, stop))
        self.matches = {}
        self.intervals = {(ordinal, role): [] for ordinal in range(instance.member_count) for role in ROLE_READS}
        kinds = {row.binding.slot.slot_id: row.mapping.binding_kind
                 for row in self.catalog.for_instance(instance.instance_profile_id)}
        for descriptor in self.descriptors:
            for role, read in ROLE_READS.items():
                if descriptor.kind not in ((A.DMA_KIND.LOAD, A.DMA_KIND.PREFETCH) if read else (A.DMA_KIND.STORE,)):
                    continue
                binding = self.catalog.slots.descriptor_binding(descriptor.descriptor_id,
                    EndpointSide.SRC if read else EndpointSide.DST)
                if binding is None or kinds.get(binding.slot.slot_id) != ROLE_KINDS[role]:
                    continue
                endpoint = descriptor.src if read else descriptor.dst
                stride = descriptor.src_stride_bytes if read else descriptor.dst_stride_bytes
                rows = [(endpoint.offset_bytes + row * stride, endpoint.offset_bytes + row * stride + descriptor.row_bytes)
                        for row in range(descriptor.rows)]
                matches = []
                for ordinal in range(instance.member_count):
                    for window in self.windows[(ordinal, role)]:
                        if (binding.slot.slot_id == window.tensor.binding.slot.slot_id and
                                all(window.start <= start and end <= window.end for start, end in rows)):
                            matches.append(window)
                            offset = window.tensor.mapping.offset_bytes
                            self.intervals[(ordinal, role)].extend((start + offset, end + offset) for start, end in rows)
                self.matches[(descriptor.descriptor_id, role)] = tuple(matches)

    def attribution(self) -> dict:
        result = {}
        for key, windows in self.matches.items():
            if len(windows) != 1:
                _fail('E_BINDING_ROLE', 'descriptor must belong to exactly one member slot',
                      instance_profile_id=self.instance.instance_profile_id, descriptor_id=key[0], role=key[1])
            result[key] = windows[0].tensor.mapping.member_ordinal
        return result

    def member_io(self, ordinal: int) -> dict:
        self.attribution()
        for descriptor in self.descriptors:
            if any((descriptor.descriptor_id, role) in self.matches for role in ROLE_READS):
                if not descriptor.rows or descriptor.useful_bytes != descriptor.rows * descriptor.row_bytes:
                    _fail('E_HOST_IO_SIZE_MISMATCH', 'descriptor row geometry is inconsistent', descriptor_id=descriptor.descriptor_id)
        return {'intervals': {role: self.intervals[(ordinal, role)] for role in ROLE_READS},
                'bases': {role: 0 for role in ROLE_READS}}

    def verify_ownership(self) -> None:
        for left in range(self.instance.member_count):
            for right in range(left + 1, self.instance.member_count):
                if self.cores[left] != self.cores[right]:
                    continue
                for role in ROLE_READS:
                    for lhs in self.windows[(left, role)]:
                        for rhs in self.windows[(right, role)]:
                            a, b = lhs.tensor.binding.slot.reference_binding, rhs.tensor.binding.slot.reference_binding
                            if ((a.region_id, a.owner_core) == (b.region_id, b.owner_core) and
                                    a.allocation_offset_bytes + lhs.start < b.allocation_offset_bytes + rhs.end and
                                    b.allocation_offset_bytes + rhs.start < a.allocation_offset_bytes + lhs.end):
                                _fail('E_BINDING_ROLE', 'member slots alias on one core',
                                      instance_profile_id=self.instance.instance_profile_id, member_ordinal=left)


def member_slot_base(program: Program, instance, ordinal: int, role: str):
    rows = [row for row in ServingTensorBindingCatalog(program).for_instance(instance.instance_profile_id)
            if row.mapping.member_ordinal == ordinal and row.mapping.binding_kind == ROLE_KINDS[role]]
    if len(rows) > 1:
        _fail('E_BINDING_ROLE', 'singular slot query has multiple tensor mappings')
    return rows[0].binding if rows else None


def member_intervals(program: Program, request, instance, ordinal: int) -> dict:
    io = ServingInstanceIo(program, request, instance)
    return {role: tuple((window.tensor.binding.object.object_id, window.start, window.end)
                        for window in io.windows[(ordinal, role)]) for role in ROLE_READS}


def instance_descriptors(program: Program, instance):
    view = build_execution_views(program).for_instance(instance.mesh_entrypoint_id, instance.mesh_profile_id)
    return [descriptor for descriptor in program.dma_descriptors if descriptor.descriptor_id in view.descriptor_ids]


def descriptor_attribution(program, request, instance) -> dict:
    return ServingInstanceIo(program, request, instance).attribution()


def verify_descriptor_attribution(program, request, instance) -> None:
    descriptor_attribution(program, request, instance)


def instance_io_intervals(program: Program, request, instance, ordinal: int = 0) -> dict:
    return ServingInstanceIo(program, request, instance).member_io(ordinal)


def _relative(intervals, base):
    return sorted((start - base, end - base) for start, end in intervals)


def _overlap_gaps(intervals) -> tuple:
    gaps = []
    cursor = None
    for start, end in sorted(intervals):
        if end <= start:
            gaps.append(f"empty interval at {start}")
        if cursor is not None and start < cursor:
            gaps.append(f"overlap at {start}")
        cursor = end if cursor is None else max(cursor, end)
    return tuple(gaps)


def coverage_gaps(intervals, start, end) -> tuple:
    gaps = []
    cursor = start
    for item_start, item_end in sorted(intervals):
        if item_start > cursor:
            gaps.append(f"hole [{cursor}, {item_start})")
        cursor = max(cursor, item_end)
    if cursor < end:
        gaps.append(f"hole [{cursor}, {end})")
    if cursor > end:
        gaps.append(f"range end {cursor} exceeds {end}")
    return tuple(gaps)


def verify_member_ownership(program: Program, request, instance) -> None:
    ServingInstanceIo(program, request, instance).verify_ownership()


def verify_instance_io(program: Program, request, instance, catalog=None) -> None:
    if instance.phase == A.PHASE.PUBLISH and request.flags & A.AGENT_REQUEST_FLAGS.HAS_KV:
        view = build_execution_views(program).for_profile(instance.mesh_profile_id)
        commands = [program.commands[index] for index in view.command_indices]
        retired = {d.completion_event for d in program.dma_descriptors
                   if d.command_id in view.command_ids and d.kind == A.DMA_KIND.STORE}
        allowed = {A.OPCODE.REQUEST_END, A.OPCODE.HALT, A.OPCODE.EVENT_WAIT,
                   A.OPCODE.EVENT_SIGNAL, A.OPCODE.BARRIER, A.OPCODE.AXI_FENCE}
        pending = list(commands)
        while pending:
            reached = [c for c in pending if retired.intersection(
                (w.event_id for w in program.command_waits[c.wait_begin:c.wait_begin + c.wait_count]))]
            if not reached:
                break
            for command in reached:
                if command.opcode not in allowed or command.operand_count:
                    _fail("E_BINDING_ROLE", "suppressed PUBLISH event reaches a data consumer",
                          instance_profile_id=instance.instance_profile_id)
                if command.signal_event:
                    retired.add(command.signal_event)
                retired.update(e.event_id for e in program.events
                               if e.producer_command_id == command.command_id)
                pending.remove(command)
    io = ServingInstanceIo(program, request, instance, catalog)
    io.verify_ownership()
    attribution = io.attribution()
    descriptors = io.descriptors
    commands = {row.command_id: row for row in program.commands}
    for descriptor in descriptors:
        producer = commands[descriptor.command_id]
        if descriptor.kind != A.DMA_KIND.LOCAL_FILL or not producer.attr_index:
            continue
        attr = program.op_attrs[producer.attr_index - 1]
        if attr.kind != A.ATTR_KIND.FILL_V1 or attr.payload_dict()['pattern'] != A.DMA_FILL_RUNTIME_BOUND_SENTINEL:
            continue
        if not producer.operand_count:
            _fail('E_BINDING_ROLE', 'runtime fill has no unique member STORE')
        allocation = program.command_operands[producer.operand_begin + producer.operand_count - 1].allocation_id
        owners = set()
        for consumer in descriptors:
            command = commands[consumer.command_id]
            if consumer.kind != A.DMA_KIND.STORE or not command.operand_count or \
                    program.command_operands[command.operand_begin].allocation_id != allocation:
                continue
            owners.update(attribution[(consumer.descriptor_id, role)] for role in (HOST_STORE, KV_STORE)
                          if (consumer.descriptor_id, role) in attribution)
        if len(owners) != 1:
            _fail('E_BINDING_ROLE', 'runtime fill has no unique member STORE')
    for ordinal in range(instance.member_count):
        _verify_member_io(request, instance, io.member_io(ordinal))


def _verify_member_io(request, instance, io) -> None:
    reads = _relative(io["intervals"][HOST_LOAD], io["bases"][HOST_LOAD])
    writes = _relative(io["intervals"][HOST_STORE], io["bases"][HOST_STORE])
    kv_reads = _relative(io["intervals"][KV_LOAD], io["bases"][KV_LOAD])
    kv_writes = _relative(io["intervals"][KV_STORE], io["bases"][KV_STORE])
    for name, intervals in (("host output", writes),
                            ("kv read", kv_reads), ("kv write", kv_writes)):
        gaps = _overlap_gaps(intervals)
        if gaps:
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  f"{name} intervals overlap: " + "; ".join(gaps),
                  instance_profile_id=instance.instance_profile_id)
    if sum(end - start for start, end in reads) != \
            instance.host_input_dma_bytes_per_member:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "host input load bytes do not match the record",
              instance_profile_id=instance.instance_profile_id)
    if sum(end - start for start, end in writes) != \
            instance.host_output_dma_bytes_per_member:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "host output store bytes do not match the record",
              instance_profile_id=instance.instance_profile_id)
    if sum(end - start for start, end in kv_reads) != \
            instance.kv_read_bytes_per_member:
        _fail("E_KV_TOKEN_MISMATCH",
              "kv read bytes do not match the record",
              instance_profile_id=instance.instance_profile_id)
    if sum(end - start for start, end in kv_writes) != \
            instance.kv_write_bytes_per_member:
        _fail("E_KV_TOKEN_MISMATCH",
              "kv write bytes do not match the record",
              instance_profile_id=instance.instance_profile_id)
    if instance.phase == A.PHASE.PUBLISH:
        if reads or kv_reads or kv_writes:
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  "PUBLISH instance must not read Host/KV",
                  instance_profile_id=instance.instance_profile_id)
        _verify_publish_chunks(writes, request, instance)
        return
    if writes:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "non-PUBLISH instance must not store Host output",
              instance_profile_id=instance.instance_profile_id)
    if instance.phase == A.PHASE.DECODE and reads:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "DECODE instance must not load host input",
              instance_profile_id=instance.instance_profile_id)
    if instance.phase == A.PHASE.PREFILL:
        _verify_host_reads(reads, request, instance)
    _verify_kv_reads(request, instance, kv_reads)
    _verify_kv_append(request, instance, kv_writes)


def _verify_host_reads(reads, request, instance) -> None:
    if not reads:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "PREFILL instance must load host input",
              instance_profile_id=instance.instance_profile_id)
    if instance.path_kind == A.PATH_KIND.KV_REUSE:
        start = request.full_input_dma_bytes - request.delta_input_dma_bytes
    else:
        start = 0
    gaps = coverage_gaps(reads, start, request.full_input_dma_bytes)
    if gaps:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "host input interval union is not exact: " + "; ".join(gaps),
              instance_profile_id=instance.instance_profile_id)


def _verify_publish_chunks(writes, request, instance) -> None:
    if not writes:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "PUBLISH instance must store host output",
              instance_profile_id=instance.instance_profile_id)
    gaps = coverage_gaps(writes, 0, request.host_output_bytes)
    if gaps:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "host output interval union is not exact: " + "; ".join(gaps),
              instance_profile_id=instance.instance_profile_id)
    ordered = sorted(writes)
    chunk = request.publish_chunk_bytes
    for index, (start, end) in enumerate(ordered):
        size = end - start
        if size > chunk or (index < len(ordered) - 1 and size != chunk):
            _fail("E_OUTPUT_CHUNK_MISMATCH",
                  "publish store does not follow publish_chunk_bytes",
                  instance_profile_id=instance.instance_profile_id)


def _verify_kv_reads(request, instance, kv_reads) -> None:
    valid = instance.kv_tokens_before * request.kv_bytes_per_token
    for start, end in kv_reads:
        if start < 0 or end > valid:
            _fail("E_KV_TOKEN_MISMATCH",
                  "kv read leaves the start valid prefix",
                  instance_profile_id=instance.instance_profile_id)


def _verify_kv_append(request, instance, kv_writes) -> None:
    append = instance.kv_write_bytes_per_member
    if append == 0:
        if kv_writes:
            _fail("E_KV_TOKEN_MISMATCH",
                  "phase must not append KV tokens",
                  instance_profile_id=instance.instance_profile_id)
        return
    base = instance.kv_tokens_before * request.kv_bytes_per_token
    gaps = coverage_gaps(kv_writes, base, base + append)
    if gaps:
        _fail("E_KV_TOKEN_MISMATCH",
              "kv append interval union is not exact: " + "; ".join(gaps),
              instance_profile_id=instance.instance_profile_id)


def _fail(code: str, message: str, **context):
    raise MeshIrError(code, message, **context)


from mesh_ir.generated import agent_abi as AG

CAPACITY_FIELDS = (
    "batch_weight_binding_entries_per_context",
    "instance_member_binding_entries_per_context",
    "batch_interval_entries_per_context",
)

@dataclasses.dataclass(frozen=True)
class ServingCapacity:
    batch_weight_binding_entries_per_context: int
    instance_member_binding_entries_per_context: int
    batch_interval_entries_per_context: int

    def fields(self) -> dict:
        return {name: getattr(self, name) for name in CAPACITY_FIELDS}


def unique_weight_symbols(program: Program, request) -> int:
    return len({
        requirement.symbol_id
        for requirement in program.agent_request_binding_requirements
        if (requirement.request_program_id, requirement.request_profile_id) ==
        (request.program_id, request.profile_id) and
        requirement.binding_kind == AG.BINDING_KIND.WEIGHT_EXTERNAL})


def instance_profile_capacity(program: Program, instance) -> dict:
    request = next(
        r for r in program.agent_request_profiles
        if (r.program_id, r.profile_id) ==
        (instance.request_program_id, instance.request_profile_id))
    weights = unique_weight_symbols(program, request)
    members = instance.member_count
    bindings = sum(row.instance_profile_id == instance.instance_profile_id
                   for row in program.agent_instance_tensor_bindings)
    if not bindings:
        _fail('E_CAPACITY_PLAN', 'instance has no tensor mappings')
    uses_kv = bool(request.flags & A.AGENT_REQUEST_FLAGS.HAS_KV)
    workspaces = sum(row.binding_kind == AG.BINDING_KIND.REQUEST_WORKSPACE
        for row in program.agent_request_binding_requirements
        if (row.request_program_id, row.request_profile_id) == (request.program_id, request.profile_id))
    return {
        "instance_member_binding_entries": bindings,
        "batch_weight_binding_entries": weights,
        "batch_interval_entries": (3 + uses_kv + workspaces) * members + weights,
    }


def serving_capacity_required(program: Program) -> ServingCapacity:
    rows = [instance_profile_capacity(program, instance)
            for instance in program.agent_instance_profiles]
    if not rows:
        raise MeshIrError("E_CAPACITY_PLAN",
                          "serving program has no reachable profile")
    return ServingCapacity(
        batch_weight_binding_entries_per_context=max(
            row["batch_weight_binding_entries"] for row in rows),
        instance_member_binding_entries_per_context=max(
            row["instance_member_binding_entries"] for row in rows),
        batch_interval_entries_per_context=max(
            row["batch_interval_entries"] for row in rows))


def serving_capacity_plan(program: Program,
                          configured: ServingCapacity) -> dict:
    required = serving_capacity_required(program)
    headroom = _capacity_headroom(required, configured)
    rows = []
    for instance in sorted(
            program.agent_instance_profiles,
            key=lambda instance: (instance.request_program_id,
                                  instance.request_profile_id,
                                  instance.instance_profile_id)):
        capacity = instance_profile_capacity(program, instance)
        rows.append({
            "program_id": instance.request_program_id,
            "profile_id": instance.request_profile_id,
            "instance_profile_id": instance.instance_profile_id,
            **capacity,
        })
    plan = {
        "per_instance_profile": rows,
        "required": required.fields(),
        "configured": configured.fields(),
        "headroom": headroom,
    }
    plan["capacity_plan_digest"] = hashlib.sha256(
        canonical_json_bytes(plan)).hexdigest()
    return plan


def _capacity_headroom(required: ServingCapacity,
                       configured: ServingCapacity) -> dict:
    headroom = {}
    for name in CAPACITY_FIELDS:
        required_value = getattr(required, name)
        configured_value = getattr(configured, name)
        if configured_value < required_value:
            raise MeshIrError("E_CAPACITY_PLAN",
                              f"configured {name} below the required "
                              "capacity", required=required_value,
                              configured=configured_value)
        headroom[name] = configured_value - required_value
    return headroom


ZERO_SIDE_EFFECTS = {
    "session_admitted": 0,
    "cache_reservations": 0,
    "core_starts": 0,
    "payload_dma_bytes": 0,
}

SERVING_PREFLIGHT_CODES = frozenset((
    "E_REQUEST_PROFILE",
    "E_REQUEST_PROFILE_KEY",
    "E_BINDING_ROLE",
    "E_HOST_IO_SIZE_MISMATCH",
    "E_OUTPUT_CHUNK_MISMATCH",
    "E_KV_TOKEN_MISMATCH",
))


@dataclasses.dataclass(frozen=True)
class PreflightOutcome:
    stage: str
    code: str
    disposition: tuple
    admitted: bool
    side_effects: dict
    context: dict


def preflight_stage(code: str) -> str:
    if code.startswith("E_ABI_"):
        return "ABI_DECODE"
    if code == "E_CAPACITY_PLAN":
        return "CAPACITY_PLAN"
    if code in SERVING_PREFLIGHT_CODES:
        return "REQUEST_PREFLIGHT"
    return "PROGRAM_LOAD"


def capture_preflight(action, *args, **kwargs) -> PreflightOutcome:
    try:
        action(*args, **kwargs)
    except MeshIrError as error:
        return PreflightOutcome(
            stage=preflight_stage(error.code),
            code=error.code,
            disposition=AG.DETAIL_DISPOSITION_V1.get(error.code),
            admitted=False,
            side_effects=dict(ZERO_SIDE_EFFECTS),
            context=dict(error.context))
    return PreflightOutcome(
        stage="ACCEPTED",
        code="E_OK",
        disposition=AG.DETAIL_DISPOSITION_V1["E_OK"],
        admitted=False,
        side_effects=dict(ZERO_SIDE_EFFECTS),
        context={})

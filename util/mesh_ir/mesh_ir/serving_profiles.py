"""Agent serving profile compiler helpers (main contract 10.3.1).

Owns the requested_profile_key double projection (zero-key base digest and
the final per-request key) and, later in Gate 6 R1, the exact selector,
Host/KV interval oracle and CapacityPlan derivation.
"""

from __future__ import annotations

import dataclasses
import hashlib

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError, Program, canonical_json_bytes

KEY_BASE_DOMAIN = b"AGENT_PROFILE_KEY_BASE_V1\0"
REQUEST_KEY_DOMAIN = b"AGENT_REQUEST_PROFILE_V1\0"

KEY_FIELD = "requested_profile_key"


def program_profile_key_base_bytes(program: Program) -> bytes:
    projection = canonical_json_bytes(_zeroed(program).canonical_dict())
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
    records = [
        dataclasses.replace(record, requested_profile_key=key)
        for record, key in zip(program.agent_request_profiles, keys)
    ]
    return dataclasses.replace(program, agent_request_profiles=records)


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
    records = [
        dataclasses.replace(record, **{KEY_FIELD: 0})
        for record in program.agent_request_profiles
    ]
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


def verify_path_closure(program: Program, request) -> None:
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
                request.full_input_tokens + request.output_tokens, 0, ranks))


def _declared_paths(request) -> tuple:
    return tuple(path_kind for path_kind in (
        A.PATH_KIND.INITIAL_PREFILL, A.PATH_KIND.KV_REUSE,
        A.PATH_KIND.REPREFILL) if request.path_mask & (1 << path_kind))


def _relocation(program: Program, symbol_id: int):
    for relocation in program.relocations:
        if relocation.symbol_sid == symbol_id:
            return relocation
    raise MeshIrError("E_RELOCATION", "symbol is not relocated",
                      symbol_id=symbol_id)


MEMBER_KIND_SYMBOLS = (
    (HOST_LOAD, "static_input_symbol_id", "primary_input_symbol_id"),
    (HOST_STORE, "static_output_symbol_id", "primary_output_symbol_id"),
    (KV_LOAD, "static_kv_symbol_id", "primary_kv_symbol_id"),
)

MEMBER_ROLE_BYTES = {
    HOST_LOAD: "host_input_dma_bytes_per_member",
    HOST_STORE: "host_output_dma_bytes_per_member",
    KV_LOAD: "kv_write_bytes_per_member",
}

ROLE_READS = {
    HOST_LOAD: True,
    HOST_STORE: False,
    KV_LOAD: True,
    KV_STORE: False,
}


def _request_kind_tensors(program: Program, request) -> dict:
    tensors = {HOST_LOAD: 0, HOST_STORE: 0, KV_LOAD: 0, KV_STORE: 0}
    for role, symbol_id in (
            (HOST_LOAD, request.primary_input_symbol_id),
            (HOST_STORE, request.primary_output_symbol_id)):
        if symbol_id:
            tensors[role] = _relocation(program, symbol_id).tensor_id
    if request.primary_kv_symbol_id:
        tensor_id = _relocation(program,
                                request.primary_kv_symbol_id).tensor_id
        tensors[KV_LOAD] = tensor_id
        tensors[KV_STORE] = tensor_id
    return tensors


def _verify_member_symbol_roles(program, request, instance, ordinal):
    record = member_records(program, instance)[ordinal]
    for role, field, primary_field in MEMBER_KIND_SYMBOLS:
        symbol_id = getattr(record, field)
        if symbol_id == 0:
            continue
        primary_symbol = getattr(request, primary_field)
        if primary_symbol == 0:
            _fail("E_BINDING_ROLE",
                  "member slot is present in a forbidden phase kind",
                  instance_profile_id=instance.instance_profile_id,
                  member_ordinal=ordinal, role=role)
        relocation = _relocation(program, symbol_id)
        primary = _relocation(program, primary_symbol)
        if relocation.tensor_id != primary.tensor_id:
            _fail("E_BINDING_ROLE",
                  "member slot must view the request primary tensor",
                  instance_profile_id=instance.instance_profile_id,
                  member_ordinal=ordinal, role=role)


def member_slot_base(program: Program, instance, ordinal: int, role: str):
    field = dict((item[0], item[1]) for item in MEMBER_KIND_SYMBOLS)[role]
    record = member_records(program, instance)[ordinal]
    symbol_id = getattr(record, field)
    if symbol_id == 0:
        return None
    return _relocation(program, symbol_id)


def member_intervals(program: Program, request, instance,
                     ordinal: int) -> dict:
    record = member_records(program, instance)[ordinal]
    windows = {}
    for role, field, _ in MEMBER_KIND_SYMBOLS:
        symbol_id = getattr(record, field)
        if symbol_id == 0:
            windows[role] = None
            if role == KV_LOAD:
                windows[KV_STORE] = None
            continue
        relocation = _relocation(program, symbol_id)
        base = relocation.offset_bytes
        if role == HOST_LOAD:
            if instance.path_kind == A.PATH_KIND.KV_REUSE:
                base += request.input_binding_bytes - \
                    request.delta_input_dma_bytes
            size = instance.host_input_dma_bytes_per_member
            windows[role] = (relocation.tensor_id, base, base + size)
        elif role == HOST_STORE:
            size = instance.host_output_dma_bytes_per_member
            windows[role] = (relocation.tensor_id, base, base + size)
        else:
            valid = instance.kv_tokens_before * request.kv_bytes_per_token
            windows[KV_LOAD] = (relocation.tensor_id, base, base + valid)
            append = base + valid
            windows[KV_STORE] = (
                relocation.tensor_id, append,
                append + instance.kv_write_bytes_per_member)
    return windows


def _same_tensor_overlap(left, right) -> bool:
    if left is None or right is None:
        return False
    if left[0] != right[0]:
        return False
    return left[1] < right[2] and right[1] < left[2]


def _descriptor_rows(endpoint, stride, rows, row_bytes):
    return [(endpoint.offset_bytes + index * stride,
             endpoint.offset_bytes + index * stride + row_bytes)
            for index in range(rows)]


def _profile_descriptors(program: Program, instance):
    descriptor_ids = {
        row.descriptor_id for row in program.expected_traffic
        if row.entrypoint_id == instance.mesh_entrypoint_id and
        row.profile_id == instance.mesh_profile_id}
    return [descriptor for descriptor in program.dma_descriptors
            if descriptor.descriptor_id in descriptor_ids]


def _rows_fit(endpoint, stride, rows, row_bytes, view) -> bool:
    if view is None or endpoint.tensor_id != view[0]:
        return False
    for item_start, item_end in _descriptor_rows(endpoint, stride, rows,
                                                 row_bytes):
        if item_start < view[1] or item_end > view[2]:
            return False
    return True


def _role_endpoint(descriptor, role):
    if role in (HOST_LOAD, KV_LOAD):
        return descriptor.src, descriptor.src_stride_bytes
    return descriptor.dst, descriptor.dst_stride_bytes


def descriptor_role_matches(program, request, descriptor, role) -> bool:
    reads = ROLE_READS[role]
    if reads and descriptor.kind != A.DMA_KIND.LOAD:
        return False
    if not reads and descriptor.kind != A.DMA_KIND.STORE:
        return False
    kind_tensors = _request_kind_tensors(program, request)
    wanted = kind_tensors[KV_STORE] if role in (KV_LOAD, KV_STORE) \
        else kind_tensors[role]
    if wanted == 0:
        return False
    endpoint, _ = _role_endpoint(descriptor, role)
    return endpoint.tensor_id == wanted


def descriptor_candidates(program, request, instance, descriptor,
                          role) -> tuple:
    if not descriptor_role_matches(program, request, descriptor, role):
        return ()
    endpoint, stride = _role_endpoint(descriptor, role)
    matches = []
    for ordinal in range(instance.member_count):
        core = member_source_core(program, request, instance, ordinal)
        if descriptor.owner_core != core:
            continue
        view = member_intervals(program, request, instance, ordinal)[role]
        if _rows_fit(endpoint, stride, descriptor.rows, descriptor.row_bytes,
                     view):
            matches.append(ordinal)
    return tuple(matches)


def descriptor_attribution(program, request, instance) -> dict:
    attribution = {}
    for descriptor in _profile_descriptors(program, instance):
        for role in (HOST_LOAD, HOST_STORE, KV_LOAD, KV_STORE):
            if not descriptor_role_matches(program, request, descriptor,
                                           role):
                continue
            candidates = descriptor_candidates(program, request, instance,
                                               descriptor, role)
            if len(candidates) != 1:
                _fail("E_BINDING_ROLE",
                      "descriptor must belong to exactly one member slot",
                      instance_profile_id=instance.instance_profile_id,
                      descriptor_id=descriptor.descriptor_id, role=role)
            attribution[(descriptor.descriptor_id, role)] = candidates[0]
    return attribution


def verify_descriptor_attribution(program, request, instance) -> None:
    descriptor_attribution(program, request, instance)


def attributed_descriptors(program, request, instance, ordinal,
                           role) -> tuple:
    out = []
    for descriptor in _profile_descriptors(program, instance):
        if ordinal in descriptor_candidates(program, request, instance,
                                            descriptor, role):
            out.append(descriptor)
    return tuple(out)


def instance_io_intervals(program: Program, request, instance,
                          ordinal: int = 0) -> dict:
    _verify_member_symbol_roles(program, request, instance, ordinal)
    intervals = {HOST_LOAD: [], HOST_STORE: [], KV_LOAD: [], KV_STORE: []}
    for role in (HOST_LOAD, HOST_STORE, KV_LOAD, KV_STORE):
        for descriptor in attributed_descriptors(program, request, instance,
                                                 ordinal, role):
            if descriptor.rows == 0 or \
                    descriptor.useful_bytes != descriptor.rows * \
                    descriptor.row_bytes:
                _fail("E_HOST_IO_SIZE_MISMATCH",
                      "descriptor row geometry is inconsistent",
                      descriptor_id=descriptor.descriptor_id)
            endpoint, stride = _role_endpoint(descriptor, role)
            intervals[role].extend(_descriptor_rows(
                endpoint, stride, descriptor.rows, descriptor.row_bytes))
    bases = {}
    for role, field, _ in MEMBER_KIND_SYMBOLS:
        record = member_records(program, instance)[ordinal]
        symbol_id = getattr(record, field)
        bases[role] = _relocation(program, symbol_id).offset_bytes \
            if symbol_id else 0
        if role == KV_LOAD:
            bases[KV_STORE] = bases[KV_LOAD]
    return {"intervals": intervals, "bases": bases}


def _collect(intervals, role, tensors, descriptor, endpoint, stride) -> None:
    if tensors[role] and endpoint.tensor_id == tensors[role]:
        intervals[role].extend(_descriptor_rows(
            endpoint, stride, descriptor.rows, descriptor.row_bytes))


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
    windows = [member_intervals(program, request, instance, ordinal)
               for ordinal in range(instance.member_count)]
    cores = [member_source_core(program, request, instance, ordinal)
             for ordinal in range(instance.member_count)]
    for left in range(instance.member_count):
        for right in range(left + 1, instance.member_count):
            if cores[left] != cores[right]:
                continue
            for role in (HOST_LOAD, HOST_STORE, KV_LOAD, KV_STORE):
                if _same_tensor_overlap(windows[left][role],
                                        windows[right][role]):
                    _fail("E_BINDING_ROLE",
                          "member slots alias on one core",
                          instance_profile_id=instance.instance_profile_id,
                          member_ordinal=left)


def verify_instance_io(program: Program, request, instance) -> None:
    verify_member_ownership(program, request, instance)
    verify_descriptor_attribution(program, request, instance)
    for ordinal in range(instance.member_count):
        _verify_member_io(program, request, instance, ordinal)


def _verify_member_io(program, request, instance, ordinal) -> None:
    io = instance_io_intervals(program, request, instance, ordinal)
    reads = _relative(io["intervals"][HOST_LOAD], io["bases"][HOST_LOAD])
    writes = _relative(io["intervals"][HOST_STORE], io["bases"][HOST_STORE])
    kv_reads = _relative(io["intervals"][KV_LOAD], io["bases"][KV_LOAD])
    kv_writes = _relative(io["intervals"][KV_STORE], io["bases"][KV_STORE])
    for name, intervals in (("host input", reads), ("host output", writes),
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

PHASE_ROLE_COUNT = {
    A.PHASE.PREFILL: 2,
    A.PHASE.DECODE: 1,
    A.PHASE.PUBLISH: 1,
}


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
    role_count = PHASE_ROLE_COUNT[instance.phase]
    return {
        "instance_member_binding_entries": members * role_count,
        "batch_weight_binding_entries": weights,
        "batch_interval_entries": 4 * members + weights,
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

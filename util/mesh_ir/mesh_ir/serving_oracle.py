"""Independent phase/token/byte plan for one GENERATE (R3).

Recomputes the exact PREFILL -> DECODE* -> PUBLISH instance chain from the
immutable decoded program and the workload request profile.  It never reads a
runtime outcome, so the C++ RequestContext is graded against it step by step.
"""

import dataclasses

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.serving_profiles import HOST_LOAD, coverage_gaps, instance_io_intervals, member_rank_vector, verify_request_phase_mode


@dataclasses.dataclass(frozen=True)
class PhaseStep:
    ordinal: int
    phase: int
    path_kind: int
    instance_profile_id: int
    ranks: tuple
    member_count: int
    valid_tokens_per_member: int
    kv_tokens_before: int
    decode_chunk_tokens: int
    cached_tokens_after: int
    kv_read_bytes_per_member: int
    kv_write_bytes_per_member: int
    host_input_dma_bytes_per_member: int
    host_output_dma_bytes_per_member: int


def _fail(stage: str, detail: str) -> None:
    raise MeshIrError("E_REQUEST_PROFILE", f"{stage}: {detail}")


def _phase_instances(program, request, path_kind, phase):
    return tuple(
        instance for instance in program.agent_instance_profiles
        if instance.request_program_id == request.program_id and
        instance.request_profile_id == request.profile_id and
        instance.path_kind == path_kind and instance.phase == phase)


def _select(program, request, path_kind, phase, kv_tokens_before):
    matches = tuple(
        instance for instance in
        _phase_instances(program, request, path_kind, phase)
        if instance.kv_tokens_before == kv_tokens_before)
    if len(matches) != 1:
        _fail("instance selector",
              f"no exact {phase} profile at token cursor "
              f"{kv_tokens_before} (matches {len(matches)})")
    return matches[0]


def _role_matrix(instance, phase, uses_kv=True) -> None:
    input_ok = instance.primary_input_symbol_id != 0
    output_ok = instance.primary_output_symbol_id != 0
    kv_ok = instance.primary_kv_symbol_id != 0
    expected = {
        A.PHASE.PREFILL: (True, False, uses_kv),
        A.PHASE.DECODE: (False, False, True),
        A.PHASE.PUBLISH: (False, True, False),
    }[phase]
    if (input_ok, output_ok, kv_ok) != expected:
        _fail("phase access matrix",
              f"phase {phase} primary roles "
              f"{input_ok}/{output_ok}/{kv_ok} are not the contract set")


def _prefill_step(program, request, path_kind, path: int, ordinal: int):
    instance = _select(program, request, path_kind, A.PHASE.PREFILL, 0)
    uses_kv = request.flags == A.AGENT_REQUEST_FLAGS.HAS_KV
    _role_matrix(instance, A.PHASE.PREFILL, uses_kv)
    if path == A.PATH_KIND.KV_REUSE:
        tokens = request.delta_input_tokens
        expected_read = request.delta_input_dma_bytes
    else:
        tokens = request.full_input_tokens
        expected_read = 0
    if instance.valid_tokens_per_member != tokens:
        _fail("prefill token cursor",
              f"valid tokens {instance.valid_tokens_per_member} != {tokens}")
    if instance.decode_chunk_tokens != 0:
        _fail("prefill token cursor",
              "prefill instance must declare decode_chunk_tokens 0")
    if instance.kv_read_bytes_per_member != expected_read:
        _fail("prefill KV read",
              f"read {instance.kv_read_bytes_per_member} != {expected_read}")
    if instance.kv_write_bytes_per_member != \
            tokens * request.kv_bytes_per_token:
        _fail("prefill KV write", "append bytes != tokens * bytes per token")
    if not instance.host_input_dma_bytes_per_member or instance.host_output_dma_bytes_per_member:
        _fail("prefill Host ranges", "host input/output access roles mismatch")
    return PhaseStep(
        ordinal, A.PHASE.PREFILL, path_kind, instance.instance_profile_id,
        member_rank_vector(program, instance), instance.member_count, tokens,
        0, 0, tokens if uses_kv else 0, instance.kv_read_bytes_per_member,
        instance.kv_write_bytes_per_member,
        instance.host_input_dma_bytes_per_member,
        instance.host_output_dma_bytes_per_member)


def _decode_steps(program, request, path_kind, start: int):
    steps = []
    produced = 0
    ordinal = 1
    while produced < request.output_tokens:
        instance = _select(program, request, path_kind, A.PHASE.DECODE,
                           start + produced)
        _role_matrix(instance, A.PHASE.DECODE)
        chunk = instance.decode_chunk_tokens
        if chunk <= 0 or chunk > request.output_tokens - produced:
            _fail("decode token cursor",
                  f"chunk {chunk} outside the remaining "
                  f"{request.output_tokens - produced} tokens")
        if instance.valid_tokens_per_member != chunk:
            _fail("decode token cursor", "valid tokens != decode chunk")
        if instance.kv_read_bytes_per_member != \
                (start + produced) * request.kv_bytes_per_token:
            _fail("decode KV read",
                  "read bytes != kv_tokens_before * bytes per token")
        if instance.kv_write_bytes_per_member != \
                chunk * request.kv_bytes_per_token:
            _fail("decode KV write", "append bytes != chunk * bytes per token")
        if instance.host_input_dma_bytes_per_member or \
                instance.host_output_dma_bytes_per_member:
            _fail("decode Host ranges", "decode must not touch Host payload")
        produced += chunk
        steps.append(PhaseStep(
            ordinal, A.PHASE.DECODE, path_kind, instance.instance_profile_id,
            member_rank_vector(program, instance), instance.member_count,
            chunk, start + produced - chunk, chunk, start + produced,
            instance.kv_read_bytes_per_member,
            instance.kv_write_bytes_per_member, 0, 0))
        ordinal += 1
    return steps


def _publish_step(program, request, path_kind, cursor: int, ordinal: int):
    instance = _select(program, request, path_kind, A.PHASE.PUBLISH, cursor)
    _role_matrix(instance, A.PHASE.PUBLISH)
    if instance.valid_tokens_per_member or instance.decode_chunk_tokens:
        _fail("publish token cursor", "publish must not append tokens")
    if instance.kv_read_bytes_per_member or \
            instance.kv_write_bytes_per_member:
        _fail("publish KV ranges", "publish must not read or write KV")
    if instance.host_input_dma_bytes_per_member or \
            instance.host_output_dma_bytes_per_member != \
            request.host_output_bytes:
        _fail("publish Host ranges",
              "publish must store exactly the planned output bytes")
    return PhaseStep(
        ordinal, A.PHASE.PUBLISH, path_kind, instance.instance_profile_id,
        member_rank_vector(program, instance), instance.member_count, 0,
        cursor, 0, cursor, 0, 0, 0,
        instance.host_output_dma_bytes_per_member)


def generate_phase_plan(program, request, path_kind):
    uses_kv = verify_request_phase_mode(program, request)
    if path_kind not in (A.PATH_KIND.INITIAL_PREFILL, A.PATH_KIND.KV_REUSE,
                         A.PATH_KIND.REPREFILL):
        _fail("path", f"path kind {path_kind} is outside the closed set")
    if not request.path_mask & (1 << path_kind):
        _fail("path", f"path kind {path_kind} is not declared reachable")
    prefill = _prefill_step(program, request, path_kind, path_kind, 0)
    start = prefill.valid_tokens_per_member if path_kind == \
        A.PATH_KIND.KV_REUSE else request.full_input_tokens
    if not uses_kv:
        start = 0
    if path_kind == A.PATH_KIND.KV_REUSE and \
            start != request.expected_cached_tokens:
        _fail("reuse token cursor",
              "reused prefix must equal the expected cached tokens")
    steps = [prefill]
    steps.extend(_decode_steps(program, request, path_kind, start))
    steps.append(_publish_step(program, request, path_kind,
                               start + request.output_tokens, len(steps)))
    expected_cursor = request.full_input_tokens + request.output_tokens if uses_kv else 0
    if steps[-1].cached_tokens_after != expected_cursor:
        _fail("cursor closure",
              "final cursor differs from the request mode")
    instance, = _phase_instances(program, request, path_kind, A.PHASE.PREFILL)
    input_start = request.full_input_dma_bytes - request.delta_input_dma_bytes if path_kind == A.PATH_KIND.KV_REUSE else 0
    for member in range(instance.member_count):
        reads = instance_io_intervals(program, request, instance, member)['intervals'][HOST_LOAD]
        if (coverage_gaps(reads, input_start, request.full_input_dma_bytes) or
                instance.host_input_dma_bytes_per_member != sum(end - start for start, end in reads)):
            _fail("prefill Host ranges", "host input coverage or traffic mismatch")
    return tuple(steps)

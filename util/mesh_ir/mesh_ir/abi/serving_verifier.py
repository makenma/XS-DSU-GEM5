"""Agent serving V1 semantic verification (main contract 10.3.1).

The base verifier owns the static program; this module owns every
cross-reference that only exists when MESH_FEATURE_AGENT_SERVING_V1 is
declared: request/instance profiles, source-core map, member bindings,
request binding requirements, PUBLISH surrogate bindings, the external
symbol role closure and the phase access matrix.
"""

from __future__ import annotations

from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as AG
from mesh_ir.model import MeshIrError
from mesh_ir.serving_profiles import (
    HOST_STORE,
    attributed_descriptors,
    coverage_gaps,
    member_slot_base,
    selector_tuple,
    _relocation,
    verify_instance_io,
    verify_path_closure,
    verify_request_profile_keys,
)

READ = AG.BINDING_FLAGS.READ
WRITE = AG.BINDING_FLAGS.WRITE
PERSISTENT = AG.BINDING_FLAGS.PERSISTENT
RESOLVE_BY_HANDLE = AG.BINDING_FLAGS.RESOLVE_BY_HANDLE

EXACT_BINDING_FLAGS = {
    AG.BINDING_KIND.HOST_INPUT: READ,
    AG.BINDING_KIND.HOST_OUTPUT: WRITE,
    AG.BINDING_KIND.KV_EXTERNAL:
        READ | WRITE | PERSISTENT | RESOLVE_BY_HANDLE,
    AG.BINDING_KIND.WEIGHT_EXTERNAL: READ | PERSISTENT,
}

PHASE_ACCESS = {
    A.PHASE.PREFILL: (True, False, True),
    A.PHASE.DECODE: (False, False, True),
    A.PHASE.PUBLISH: (False, True, False),
}

SYMBOL_ROLE_INPUT = "INPUT"
SYMBOL_ROLE_OUTPUT = "OUTPUT"
SYMBOL_ROLE_KV = "KV"

PATH_MASK = 0b111


def _fail(code: str, message: str, **context):
    raise MeshIrError(code, message, **context)


def _serving_tables(program):
    return (
        program.agent_request_profiles,
        program.agent_instance_profiles,
        program.agent_source_core_map,
        program.agent_instance_member_bindings,
        program.agent_request_binding_requirements,
        program.agent_publish_surrogate_bindings,
    )


def verify_serving_v1(program, arch) -> None:
    unknown = program.required_features & ~A.KNOWN_FEATURE_MASK
    if unknown:
        _fail("E_ABI_FEATURE", "unknown required feature bits",
              features=hex(unknown))
    if not program.required_features & A.AGENT_SERVING_V1:
        if any(_serving_tables(program)):
            _fail("E_ABI_FEATURE",
                  "Agent serving sections without the feature bit")
        return
    if any(not table for table in _serving_tables(program)[:5]):
        _fail("E_ABI_BOUNDS",
              "Agent serving feature requires every conditional section")
    requests = program.agent_request_profiles
    instances = program.agent_instance_profiles
    _verify_request_profiles(program, arch, requests)
    verify_request_profile_keys(program)
    _verify_instance_profiles(program, requests, instances)
    _verify_source_core_map(program, arch, requests)
    _verify_member_bindings(program, requests, instances)
    _verify_requirements(program, requests)
    _verify_symbol_classification(program)
    _verify_publish_bindings(program, requests, instances)
    for request in requests:
        verify_path_closure(program, request)
    for instance in instances:
        request = next(
            r for r in requests
            if (r.program_id, r.profile_id) ==
            (instance.request_program_id, instance.request_profile_id))
        verify_instance_io(program, request, instance)


def _profile_key(record) -> tuple:
    return (record.request_program_id, record.request_profile_id)


def _attr_of(program, attr_index):
    if attr_index == 0:
        return None
    if attr_index > len(program.op_attrs):
        _fail("E_ABI_BOUNDS", "attr index out of table",
              attr_index=attr_index)
    return program.op_attrs[attr_index - 1]


def _verify_request_profiles(program, arch, requests) -> None:
    keys = set()
    rank_maps = {}
    kv_bytes_per_token = None
    for request in requests:
        if request.program_id == 0 or request.profile_id == 0:
            _fail("E_REQUEST_PROFILE", "request profile id must be nonzero",
                  program_id=request.program_id, profile_id=request.profile_id)
        if request.program_id > 0xFFFF or request.profile_id > 0xFFFF:
            _fail("E_REQUEST_PROFILE",
                  "request profile id exceeds its u16 namespace",
                  program_id=request.program_id, profile_id=request.profile_id)
        key = (request.program_id, request.profile_id)
        if key in keys:
            _fail("E_ABI_DUPLICATE", "duplicate request profile", key=key)
        keys.add(key)
        if request.flags != A.AGENT_REQUEST_FLAGS.HAS_KV:
            _fail("E_REQUEST_PROFILE",
                  "serving request profile requires HAS_KV", key=key)
        if request.path_mask == 0 or request.path_mask & ~PATH_MASK:
            _fail("E_REQUEST_PROFILE", "path_mask is not a legal path set",
                  key=key)
        if request.kv_bytes_per_token == 0:
            _fail("E_REQUEST_PROFILE", "kv_bytes_per_token must be positive",
                  key=key)
        if kv_bytes_per_token is None:
            kv_bytes_per_token = request.kv_bytes_per_token
        elif kv_bytes_per_token != request.kv_bytes_per_token:
            _fail("E_REQUEST_PROFILE",
                  "kv_bytes_per_token must match across the program", key=key)
        if (request.full_input_dma_bytes != request.input_binding_bytes or
                request.delta_input_dma_bytes == 0 or
                request.delta_input_dma_bytes > request.full_input_dma_bytes):
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  "request host input byte relation is broken", key=key)
        if (request.delta_input_tokens > request.full_input_tokens or
                request.full_input_tokens - request.delta_input_tokens !=
                request.expected_cached_tokens):
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  "request token/cached relation is broken", key=key)
        if (request.publish_chunk_bytes == 0 or
                request.publish_chunk_bytes % arch.axi_data_bytes != 0 or
                request.publish_chunk_bytes > request.host_output_bytes):
            _fail("E_OUTPUT_CHUNK_MISMATCH",
                  "publish_chunk_bytes is not AXI-aligned in range", key=key)
        if request.host_output_bytes == 0 or request.output_tokens == 0:
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  "request output size/tokens must be positive", key=key)
        if request.source_rank_count == 0:
            _fail("E_REQUEST_PROFILE", "source_rank_count must be positive",
                  key=key)
        rank_maps.setdefault(request.program_id, set()).add(
            (request.source_rank_count, request.source_core_map_begin))
    for program_id, maps in rank_maps.items():
        if len(maps) > 1:
            _fail("E_REQUEST_PROFILE",
                  "request profiles of one program must share one rank map",
                  program_id=program_id)


def _verify_instance_profiles(program, requests, instances) -> None:
    request_keys = {(r.program_id, r.profile_id) for r in requests}
    entrypoints = {e.entrypoint_id: e for e in program.entrypoints}
    profiles = {p.profile_id: p for p in program.profiles}
    ids = set()
    selectors = set()
    for instance in instances:
        if instance.instance_profile_id == 0:
            _fail("E_REQUEST_PROFILE", "instance profile id must be nonzero")
        if instance.instance_profile_id in ids:
            _fail("E_ABI_DUPLICATE", "duplicate instance_profile_id",
                  instance_profile_id=instance.instance_profile_id)
        ids.add(instance.instance_profile_id)
        key = _profile_key(instance)
        if key not in request_keys:
            _fail("E_REQUEST_PROFILE",
                  "instance references no request profile", key=key)
        if instance.phase not in PHASE_ACCESS:
            _fail("E_REQUEST_PROFILE", "unknown instance phase",
                  instance_profile_id=instance.instance_profile_id)
        if instance.member_count == 0:
            _fail("E_REQUEST_PROFILE", "member_count must be positive",
                  instance_profile_id=instance.instance_profile_id)
        if instance.member_binding_count != instance.member_count:
            _fail("E_BINDING_ROLE",
                  "member_binding_count must equal member_count",
                  instance_profile_id=instance.instance_profile_id)
        _member_span(program, instance)
        selector = selector_tuple(program, instance)
        if selector in selectors:
            _fail("E_ABI_DUPLICATE", "duplicate instance selector",
                  selector=selector)
        selectors.add(selector)
        _verify_instance_mesh_binding(program, entrypoints, profiles, instance)
        _verify_instance_phase_matrix(instance)
        _verify_instance_byte_relation(program, instance)


def _member_span(program, instance):
    first = instance.member_binding_first
    end = first + instance.member_binding_count
    if end > len(program.agent_instance_member_bindings):
        _fail("E_BINDING_ROLE", "member binding span out of range",
              instance_profile_id=instance.instance_profile_id)
    return range(first, end)


def _verify_instance_mesh_binding(program, entrypoints, profiles,
                                  instance) -> None:
    entrypoint = entrypoints.get(instance.mesh_entrypoint_id)
    if entrypoint is None:
        _fail("E_REQUEST_PROFILE", "instance entrypoint is missing",
              instance_profile_id=instance.instance_profile_id)
    if instance.mesh_profile_id not in profiles:
        _fail("E_REQUEST_PROFILE", "instance mesh profile is missing",
              instance_profile_id=instance.instance_profile_id)
    begin = entrypoint.profile_begin
    end = begin + entrypoint.profile_count
    if end > len(program.profiles):
        _fail("E_REQUEST_PROFILE", "entrypoint profile span is broken",
              instance_profile_id=instance.instance_profile_id)
    owned = {p.profile_id for p in program.profiles[begin:end]}
    if instance.mesh_profile_id not in owned:
        _fail("E_REQUEST_PROFILE",
              "instance mesh profile is outside its entrypoint",
              instance_profile_id=instance.instance_profile_id)


def _verify_instance_phase_matrix(instance) -> None:
    input_present, output_present, kv_present = PHASE_ACCESS[instance.phase]
    if (instance.primary_input_symbol_id != 0) != input_present:
        _fail("E_BINDING_ROLE", "instance primary input symbol role mismatch",
              instance_profile_id=instance.instance_profile_id)
    if (instance.primary_output_symbol_id != 0) != output_present:
        _fail("E_BINDING_ROLE", "instance primary output symbol role mismatch",
              instance_profile_id=instance.instance_profile_id)
    if (instance.primary_kv_symbol_id != 0) != kv_present:
        _fail("E_BINDING_ROLE", "instance primary kv symbol role mismatch",
              instance_profile_id=instance.instance_profile_id)
    if instance.flags != 0:
        _fail("E_ABI_RESERVED", "instance flags must be zero",
              instance_profile_id=instance.instance_profile_id)
    if instance.phase == A.PHASE.DECODE:
        if instance.decode_chunk_tokens == 0:
            _fail("E_REQUEST_PROFILE",
                  "decode instance needs a positive chunk",
                  instance_profile_id=instance.instance_profile_id)
    elif instance.decode_chunk_tokens != 0:
        _fail("E_REQUEST_PROFILE",
              "only DECODE instances carry decode_chunk_tokens",
              instance_profile_id=instance.instance_profile_id)


def _verify_instance_byte_relation(program, instance) -> None:
    request = next(r for r in program.agent_request_profiles
                   if (r.program_id, r.profile_id) == _profile_key(instance))
    if instance.phase == A.PHASE.PREFILL:
        new_tokens = instance.valid_tokens_per_member
    elif instance.phase == A.PHASE.DECODE:
        new_tokens = instance.decode_chunk_tokens
    else:
        new_tokens = 0
    if instance.kv_write_bytes_per_member != \
            new_tokens * request.kv_bytes_per_token:
        _fail("E_KV_TOKEN_MISMATCH",
              "kv_write_bytes_per_member does not match the phase",
              instance_profile_id=instance.instance_profile_id)
    if instance.phase == A.PHASE.PUBLISH:
        if (instance.kv_read_bytes_per_member != 0 or
                instance.host_input_dma_bytes_per_member != 0 or
                instance.host_output_dma_bytes_per_member !=
                request.host_output_bytes):
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  "PUBLISH instance byte roles are broken",
                  instance_profile_id=instance.instance_profile_id)
    else:
        if instance.host_output_dma_bytes_per_member != 0:
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  "non-PUBLISH instance must not store host output",
                  instance_profile_id=instance.instance_profile_id)
        if instance.phase == A.PHASE.PREFILL and \
                instance.host_input_dma_bytes_per_member == 0:
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  "PREFILL instance must load host input",
                  instance_profile_id=instance.instance_profile_id)
        if instance.phase == A.PHASE.DECODE and \
                instance.host_input_dma_bytes_per_member != 0:
            _fail("E_HOST_IO_SIZE_MISMATCH",
                  "DECODE instance must not load host input",
                  instance_profile_id=instance.instance_profile_id)


def _verify_source_core_map(program, arch, requests) -> None:
    cores = set(arch.core_ids)
    for request in requests:
        begin = request.source_core_map_begin
        end = begin + request.source_rank_count
        if end > len(program.agent_source_core_map):
            _fail("E_REQUEST_PROFILE", "source core map span out of range",
                  program_id=request.program_id, profile_id=request.profile_id)
        for record in program.agent_source_core_map[begin:end]:
            if record.core_id not in cores:
                _fail("E_REQUEST_PROFILE",
                      "source core map core is not in the architecture",
                      core_id=record.core_id)


def _verify_member_bindings(program, requests, instances) -> None:
    requests_by_key = {(r.program_id, r.profile_id): r for r in requests}
    role_of_symbol = {}
    for instance in instances:
        request = requests_by_key[_profile_key(instance)]
        access = PHASE_ACCESS[instance.phase]
        symbols = []
        for offset in _member_span(program, instance):
            record = program.agent_instance_member_bindings[offset]
            if record.instance_profile_id != instance.instance_profile_id:
                _fail("E_BINDING_ROLE",
                      "member binding targets another instance profile",
                      instance_profile_id=instance.instance_profile_id)
            if record.member_ordinal != offset - \
                    instance.member_binding_first:
                _fail("E_BINDING_ROLE",
                      "member ordinals must be dense from zero",
                      instance_profile_id=instance.instance_profile_id)
            if record.expected_logical_source_rank >= \
                    request.source_rank_count:
                _fail("E_REQUEST_PROFILE",
                      "member expected rank exceeds source_rank_count",
                      instance_profile_id=instance.instance_profile_id)
            present = (record.static_input_symbol_id != 0,
                       record.static_output_symbol_id != 0,
                       record.static_kv_symbol_id != 0)
            if present != access:
                _fail("E_BINDING_ROLE",
                      "member symbol presence violates the phase matrix",
                      instance_profile_id=instance.instance_profile_id,
                      member_ordinal=record.member_ordinal)
            for symbol_id in (record.static_input_symbol_id,
                              record.static_output_symbol_id,
                              record.static_kv_symbol_id):
                if symbol_id:
                    symbols.append(symbol_id)
            _record_symbol_roles(role_of_symbol, record)
        if len(symbols) != len(set(symbols)):
            _fail("E_BINDING_ROLE",
                  "member symbols must be globally distinct in a profile",
                  instance_profile_id=instance.instance_profile_id)
        _verify_instance_primary_symbols(instance, request)


def _record_symbol_roles(role_of_symbol, record) -> None:
    pairs = (
        (record.static_input_symbol_id, SYMBOL_ROLE_INPUT),
        (record.static_output_symbol_id, SYMBOL_ROLE_OUTPUT),
        (record.static_kv_symbol_id, SYMBOL_ROLE_KV),
    )
    for symbol_id, role in pairs:
        if symbol_id == 0:
            continue
        if role_of_symbol.setdefault(symbol_id, role) != role:
            _fail("E_BINDING_ROLE", "symbol changes role across profiles",
                  symbol_id=symbol_id)


def _verify_instance_primary_symbols(instance, request) -> None:
    if instance.phase == A.PHASE.PREFILL:
        expected = ((instance.primary_input_symbol_id,
                     request.primary_input_symbol_id, "input"),
                    (instance.primary_kv_symbol_id,
                     request.primary_kv_symbol_id, "kv"))
    elif instance.phase == A.PHASE.DECODE:
        expected = ((instance.primary_kv_symbol_id,
                     request.primary_kv_symbol_id, "kv"),)
    else:
        expected = ((instance.primary_output_symbol_id,
                     request.primary_output_symbol_id, "output"),)
    for actual, wanted, role in expected:
        if actual != wanted:
            _fail("E_BINDING_ROLE", "instance primary symbol mismatch",
                  instance_profile_id=instance.instance_profile_id, role=role)


def _verify_requirements(program, requests) -> None:
    request_keys = {(r.program_id, r.profile_id) for r in requests}
    order = []
    seen = set()
    for requirement in program.agent_request_binding_requirements:
        key = _profile_key(requirement)
        if key not in request_keys:
            _fail("E_REQUEST_PROFILE",
                  "requirement references no request profile", key=key)
        if requirement.symbol_id == 0:
            _fail("E_BINDING_ROLE", "requirement symbol id 0 is invalid")
        sort_key = (requirement.request_program_id,
                    requirement.request_profile_id, requirement.symbol_id)
        if sort_key in seen:
            _fail("E_ABI_DUPLICATE", "duplicate request requirement",
                  symbol_id=requirement.symbol_id)
        seen.add(sort_key)
        order.append(sort_key)
        _verify_requirement_flags(requirement)
    if order != sorted(order):
        _fail("E_ABI_ORDER",
              "requirements must be sorted by request and symbol")
    _verify_primary_requirements(program, requests)


def _verify_requirement_flags(requirement) -> None:
    kind = requirement.binding_kind
    if kind not in EXACT_BINDING_FLAGS:
        _fail("E_BINDING_ROLE", "unknown binding kind", kind=kind)
    flags = requirement.binding_flags
    if flags != EXACT_BINDING_FLAGS[kind]:
        _fail("E_BINDING_ROLE",
              "binding flags must equal the exact combination for the kind",
              kind=kind, flags=flags,
              expected=EXACT_BINDING_FLAGS[kind])


def _verify_primary_requirements(program, requests) -> None:
    by_key = {}
    for requirement in program.agent_request_binding_requirements:
        by_key.setdefault(_profile_key(requirement), []).append(requirement)
    for request in requests:
        key = (request.program_id, request.profile_id)
        table = by_key.get(key, [])
        for symbol_id, kind in (
                (request.primary_input_symbol_id,
                 AG.BINDING_KIND.HOST_INPUT),
                (request.primary_output_symbol_id,
                 AG.BINDING_KIND.HOST_OUTPUT),
                (request.primary_kv_symbol_id,
                 AG.BINDING_KIND.KV_EXTERNAL)):
            _require_kind(table, symbol_id, kind, key)


def _require_kind(table, symbol_id, kind, key) -> None:
    if symbol_id == 0:
        return
    match = [r for r in table if r.symbol_id == symbol_id]
    if len(match) != 1 or match[0].binding_kind != kind:
        _fail("E_BINDING_ROLE",
              "primary symbol has no matching requirement", key=key,
              symbol_id=symbol_id)


def _verify_symbol_classification(program) -> None:
    requirement_symbols = {
        r.symbol_id for r in program.agent_request_binding_requirements}
    member_symbols = set()
    for record in program.agent_instance_member_bindings:
        for symbol_id in (record.static_input_symbol_id,
                          record.static_output_symbol_id,
                          record.static_kv_symbol_id):
            if symbol_id:
                member_symbols.add(symbol_id)
    overlap = requirement_symbols & member_symbols
    if overlap:
        _fail("E_BINDING_ROLE",
              "symbol is both REQUEST_BINDABLE and INSTANCE_MEMBER_SLOT",
              symbol_id=min(overlap))
    tensors = {t.tensor_id: t for t in program.tensors}
    classified = requirement_symbols | member_symbols
    for relocation in program.relocations:
        tensor = tensors.get(relocation.tensor_id)
        if tensor is None:
            _fail("E_RELOCATION", "relocation references a missing tensor")
        if tensor.storage_class != A.STORAGE_CLASS.EXTERNAL:
            continue
        if relocation.symbol_sid not in classified:
            _fail("E_BINDING_ROLE",
                  "external relocation symbol is unclassified",
                  symbol_id=relocation.symbol_sid)


def _endpoint_allocation(program, endpoint) -> int:
    if endpoint.memory_space != A.MEMORY_SPACE.CORE_SRAM or \
            endpoint.shard_id == 0:
        return 0
    shard = next((s for s in program.shards
                  if s.shard_id == endpoint.shard_id), None)
    return shard.allocation_id if shard else 0


COMPUTE_WRITE_OPCODES = frozenset((
    A.OPCODE.GEMM, A.OPCODE.BMM, A.OPCODE.ELEMENTWISE, A.OPCODE.LOCAL_REDUCE,
    A.OPCODE.SOFTMAX, A.OPCODE.NORM,
))

SRAM_WRITE_DESCRIPTOR_KINDS = frozenset((
    A.DMA_KIND.LOAD, A.DMA_KIND.LOCAL_FILL, A.DMA_KIND.PREFETCH,
    A.DMA_KIND.P2P_PUSH,
))


def _allocation_writers(program, allocation_id: int) -> set:
    writers = set()
    for command in program.commands:
        if command.opcode in COMPUTE_WRITE_OPCODES:
            end = command.operand_begin + command.operand_count
            if end > len(program.command_operands):
                _fail("E_ABI_BOUNDS", "command operand span out of range",
                      command_id=command.command_id)
            for operand in program.command_operands[
                    command.operand_begin:end]:
                if operand.allocation_id == allocation_id and \
                        operand.access == A.ACCESS_KIND.READ_WRITE:
                    writers.add(command.command_id)
    for descriptor in program.dma_descriptors:
        if descriptor.kind in SRAM_WRITE_DESCRIPTOR_KINDS and \
                _endpoint_allocation(program, descriptor.dst) == \
                allocation_id:
            writers.add(descriptor.command_id)
    return writers


def _command_waits(program, command) -> set:
    end = command.wait_begin + command.wait_count
    if end > len(program.command_waits):
        _fail("E_ABI_BOUNDS", "command wait span out of range",
              command_id=command.command_id)
    return {wait.event_id for wait in
            program.command_waits[command.wait_begin:end]}


def _profile_descriptors(program, instance):
    descriptor_ids = {
        row.descriptor_id for row in program.expected_traffic
        if row.entrypoint_id == instance.mesh_entrypoint_id and
        row.profile_id == instance.mesh_profile_id}
    return [descriptor for descriptor in program.dma_descriptors
            if descriptor.descriptor_id in descriptor_ids]


def _verify_publish_bindings(program, requests, instances) -> None:
    by_instance = {}
    records = program.agent_publish_surrogate_bindings
    order = [(r.instance_profile_id, r.member_ordinal) for r in records]
    if order != sorted(order):
        _fail("E_ABI_ORDER", "publish surrogate bindings must be sorted")
    for record in records:
        key = (record.instance_profile_id, record.member_ordinal)
        if key in by_instance:
            _fail("E_ABI_DUPLICATE", "duplicate publish surrogate binding",
                  key=key)
        by_instance[key] = record
    known = {i.instance_profile_id for i in instances}
    for instance_profile_id, _ in by_instance:
        if instance_profile_id not in known:
            _fail("E_REQUEST_PROFILE",
                  "publish binding references no instance profile",
                  instance_profile_id=instance_profile_id)
    requests_by_key = {(r.program_id, r.profile_id): r for r in requests}
    for instance in instances:
        records = [r for r in program.agent_publish_surrogate_bindings
                   if r.instance_profile_id == instance.instance_profile_id]
        exposed = [by_instance.get((instance.instance_profile_id, ordinal))
                   for ordinal in range(instance.member_count)]
        if instance.phase != A.PHASE.PUBLISH:
            if records:
                _fail("E_BINDING_ROLE",
                      "non-PUBLISH profile carries publish bindings",
                      instance_profile_id=instance.instance_profile_id)
            continue
        if any(record is None for record in exposed):
            _fail("E_BINDING_ROLE",
                  "PUBLISH profile needs one binding per member ordinal",
                  instance_profile_id=instance.instance_profile_id)
        if len(records) != instance.member_count:
            _fail("E_BINDING_ROLE",
                  "PUBLISH binding ordinal set must be exact",
                  instance_profile_id=instance.instance_profile_id)
        allocations = {record.allocation_id for record in exposed}
        if len(allocations) != len(exposed):
            _fail("E_BINDING_ROLE",
                  "each PUBLISH member needs its own surrogate allocation",
                  instance_profile_id=instance.instance_profile_id)
        request = requests_by_key[(instance.request_program_id,
                                   instance.request_profile_id)]
        for ordinal, record in enumerate(exposed):
            _verify_publish_record(program, request, instance, ordinal,
                                   record)


def _verify_publish_record(program, request, instance, ordinal,
                           record) -> None:
    if record.fill_kind != A.DMA_FILL_KIND.AGENT_OUTPUT_SURROGATE:
        _fail("E_BINDING_ROLE", "publish producer must use the serving fill",
              instance_profile_id=instance.instance_profile_id)
    if record.allocation_role != \
            A.AGENT_ALLOCATION_ROLE.PUBLISH_SURROGATE_SOURCE:
        _fail("E_BINDING_ROLE", "publish allocation role mismatch",
              instance_profile_id=instance.instance_profile_id)
    if record.digest_source != \
            A.AGENT_DIGEST_SOURCE.REQUEST_SEMANTIC_OUTPUT_DIGEST:
        _fail("E_BINDING_ROLE", "publish digest source mismatch",
              instance_profile_id=instance.instance_profile_id)
    if record.allocation_id == 0 or record.producer_command_id == 0 or \
            record.completion_event_id == 0:
        _fail("E_BINDING_ROLE", "publish binding ids must be nonzero",
              instance_profile_id=instance.instance_profile_id)
    allocation = next((a for a in program.allocations
                       if a.allocation_id == record.allocation_id), None)
    if allocation is None:
        _fail("E_BINDING_ROLE", "publish allocation is missing",
              allocation_id=record.allocation_id)
    if allocation.size_bytes != instance.host_output_dma_bytes_per_member:
        _fail("E_HOST_IO_SIZE_MISMATCH",
              "publish allocation size must equal the member output bytes",
              allocation_id=record.allocation_id)
    command = next((c for c in program.commands
                    if c.command_id == record.producer_command_id), None)
    if command is None or command.opcode != A.OPCODE.DMA_FILL:
        _fail("E_BINDING_ROLE", "publish producer must be a DMA_FILL command",
              producer_command_id=record.producer_command_id)
    event = next((e for e in program.events
                  if e.event_id == record.completion_event_id), None)
    completes = any(d.command_id == command.command_id and
                    d.completion_event == record.completion_event_id
                    for d in program.dma_descriptors)
    if event is None or event.producer_command_id != command.command_id or \
            not completes:
        _fail("E_BINDING_ROLE",
              "publish completion event is not the producer signal",
              completion_event_id=record.completion_event_id)
    attr = _attr_of(program, command.attr_index)
    if attr is None or attr.kind != A.ATTR_KIND.FILL_V1:
        _fail("E_BINDING_ROLE", "publish producer needs a FILL_V1 attr",
              command_id=command.command_id)
    if attr.payload_dict()["pattern"] != A.DMA_FILL_RUNTIME_BOUND_SENTINEL:
        _fail("E_BINDING_ROLE",
              "publish producer must use the runtime-bound sentinel",
              command_id=command.command_id)
    writers = _allocation_writers(program, record.allocation_id)
    if writers != {command.command_id}:
        _fail("E_BINDING_ROLE",
              "surrogate allocation must have exactly one producer",
              allocation_id=record.allocation_id,
              writers=sorted(writers))
    producer_descriptors = [d for d in program.dma_descriptors
                            if d.command_id == command.command_id]
    if not producer_descriptors:
        _fail("E_BINDING_ROLE", "publish producer has no descriptor",
              command_id=command.command_id)
    closure = _profile_descriptors(program, instance)
    producer_fill = []
    for descriptor in closure:
        if descriptor.command_id != command.command_id:
            continue
        if _endpoint_allocation(program, descriptor.dst) != \
                record.allocation_id:
            continue
        producer_fill.extend(_endpoint_rows(descriptor.dst,
                                            descriptor.dst_stride_bytes,
                                            descriptor.rows,
                                            descriptor.row_bytes))
    if not producer_fill:
        _fail("E_BINDING_ROLE",
              "publish producer is not in the PUBLISH profile closure",
              command_id=command.command_id)
    gaps = coverage_gaps(producer_fill, allocation.offset_bytes,
                         allocation.offset_bytes + allocation.size_bytes)
    if gaps:
        _fail("E_BINDING_ROLE",
              "publish producer must fill the bound allocation exactly: " +
              "; ".join(gaps), allocation_id=record.allocation_id)
    for descriptor in program.dma_descriptors:
        if descriptor.command_id == command.command_id:
            continue
        if _endpoint_allocation(program, descriptor.dst) == \
                record.allocation_id:
            _fail("E_BINDING_ROLE",
                  "publish surrogate allocation has another writer",
                  allocation_id=record.allocation_id)
    _verify_publish_store_chain(program, request, instance, ordinal, record,
                                command)


def _endpoint_rows(endpoint, stride, rows, row_bytes) -> list:
    return [(endpoint.offset_bytes + index * stride,
             endpoint.offset_bytes + index * stride + row_bytes)
            for index in range(rows)]


def _verify_publish_store_chain(program, request, instance, ordinal, record,
                                producer) -> None:
    output = member_slot_base(program, instance, ordinal, HOST_STORE)
    if output is None:
        _fail("E_BINDING_ROLE",
              "PUBLISH member has no output slot view",
              instance_profile_id=instance.instance_profile_id)
    output_tensor = output.tensor_id
    output_base = output.offset_bytes
    allocation = next((a for a in program.allocations
                       if a.allocation_id == record.allocation_id), None)
    if allocation is None:
        _fail("E_BINDING_ROLE", "publish allocation is missing",
              allocation_id=record.allocation_id)
    stores = [descriptor for descriptor in
              attributed_descriptors(program, request, instance, ordinal,
                                     HOST_STORE)
              if descriptor.kind == A.DMA_KIND.STORE and
              descriptor.dst.tensor_id == output_tensor]
    if not stores:
        _fail("E_BINDING_ROLE",
              "publish surrogate allocation has no output store",
              allocation_id=record.allocation_id)
    for descriptor in stores:
        if _endpoint_allocation(program, descriptor.src) != \
                record.allocation_id:
            _fail("E_BINDING_ROLE",
                  "publish store must read the bound surrogate allocation",
                  descriptor_id=descriptor.descriptor_id)
        command = next((c for c in program.commands
                        if c.command_id == descriptor.command_id), None)
        if command is None or record.completion_event_id not in \
                _command_waits(program, command):
            _fail("E_BINDING_ROLE",
                  "publish store must wait for the producer completion",
                  descriptor_id=descriptor.descriptor_id)
        for index in range(descriptor.rows):
            source_start = descriptor.src.offset_bytes + \
                index * descriptor.src_stride_bytes
            target_start = descriptor.dst.offset_bytes + \
                index * descriptor.dst_stride_bytes
            if source_start - allocation.offset_bytes != \
                    target_start - output_base:
                _fail("E_BINDING_ROLE",
                      "publish store source must match the member output "
                      "offset", descriptor_id=descriptor.descriptor_id)

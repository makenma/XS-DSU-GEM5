"""Host plan and runtime projections of admitted request binding requirements."""

from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.generated import agent_abi as AG
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.model import BoundInvocation
from mesh_ir.model import MeshIrError
from mesh_ir.serving_profiles import planned_instance_count

HOST_INPUT = AG.BINDING_KIND.HOST_INPUT
HOST_OUTPUT = AG.BINDING_KIND.HOST_OUTPUT
KV_EXTERNAL = AG.BINDING_KIND.KV_EXTERNAL
WEIGHT_EXTERNAL = AG.BINDING_KIND.WEIGHT_EXTERNAL
REQUEST_WORKSPACE = AG.BINDING_KIND.REQUEST_WORKSPACE

PROFILE_ORDER = ("program_id", "profile_id")


@dataclass(frozen=True)
class RequestBindingRequirement:
    symbol_id: int
    kind: int
    flags: int


@dataclass(frozen=True)
class HostBindingRequirement:
    symbol_id: int
    kind: int
    flags: int
    platform_address: int = 0
    required_bytes: int = 0


@dataclass(frozen=True)
class HostBindingPlan:
    program_id: int
    profile_id: int
    primary_input_symbol_id: int
    primary_output_symbol_id: int
    primary_kv_symbol_id: int
    requirements: tuple
    instance_counts: tuple
    publish_chunk_bytes: int

    @property
    def workspace_bytes(self) -> int:
        rows = [row for row in self.requirements if row.kind == REQUEST_WORKSPACE]
        if not rows:
            return 0
        if (len(rows) != 1 or rows[0].platform_address or rows[0].required_bytes <= 0 or
                rows[0].flags != AG.BINDING_FLAGS.READ | AG.BINDING_FLAGS.WRITE):
            raise MeshIrError('E_BINDING_ROLE', 'workspace requires one ephemeral request range')
        return rows[0].required_bytes


def request_binding_requirements(program, program_id: int,
                                 profile_id: int) -> tuple:
    if not any(record.program_id == program_id and
               record.profile_id == profile_id
               for record in program.agent_request_profiles):
        raise MeshIrError("E_REQUEST_PROFILE", "profile is not in the program",
                          program_id=program_id, profile_id=profile_id)
    requirements = [
        requirement
        for requirement in program.agent_request_binding_requirements
        if requirement.request_program_id == program_id and
        requirement.request_profile_id == profile_id
    ]
    if not requirements:
        raise MeshIrError("E_BINDING_ROLE",
                          "profile declares no binding requirements",
                          program_id=program_id, profile_id=profile_id)
    symbols = [requirement.symbol_id for requirement in requirements]
    if len(set(symbols)) != len(symbols):
        raise MeshIrError("E_BINDING_ROLE",
                          "binding requirements repeat a symbol",
                          program_id=program_id, profile_id=profile_id)
    return tuple(sorted(
        (RequestBindingRequirement(
            symbol_id=requirement.symbol_id,
            kind=requirement.binding_kind,
            flags=requirement.binding_flags)
         for requirement in requirements),
        key=lambda requirement: requirement.symbol_id))


def request_binding_count(program, program_id: int, profile_id: int) -> int:
    return len(request_binding_requirements(program, program_id, profile_id))


def request_binding_counts(program) -> dict:
    counts = {}
    for requirement in program.agent_request_binding_requirements:
        key = (requirement.request_program_id, requirement.request_profile_id)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _platform_range(program, catalog, profile, symbol_id: int, region_bases, invocation_bindings):
    if not 1 <= symbol_id <= len(program.strings):
        raise MeshIrError("E_BINDING_ROLE", "weight requirement symbol is missing", symbol_id=symbol_id)
    symbol = program.strings[symbol_id - 1].value
    ranges = set()
    variants = {(instance.mesh_entrypoint_id, instance.mesh_profile_id)
                for instance in program.agent_instance_profiles
                if (instance.request_program_id, instance.request_profile_id) ==
                (profile.program_id, profile.profile_id)}
    for entrypoint, selected_profile in sorted(variants):
        slots = catalog.for_variant(entrypoint, selected_profile)
        if symbol not in slots:
            continue
        binding = slots[symbol]
        tensor = binding.tensor
        if not binding.is_immutable_model_content:
            raise MeshIrError("E_BINDING_ROLE", "persistent model requirement has no admitted immutable HBM content", symbol_id=symbol_id)
        if invocation_bindings:
            selected = invocation_bindings.get((entrypoint, selected_profile))
            if selected is None or binding.slot.slot_id not in selected:
                raise MeshIrError('E_RELOCATION', 'model invocation does not cover a request weight variant',
                    symbol_id=symbol_id, entrypoint_id=entrypoint, profile_id=selected_profile)
            reference = selected[binding.slot.slot_id]
        else:
            reference = binding.slot.reference_binding
        if not 0 <= reference.region_id < len(region_bases):
            raise MeshIrError("E_BINDING_ROLE", "weight requirement region is not mapped", symbol_id=symbol_id)
        address = region_bases[reference.region_id] + reference.allocation_offset_bytes
        size = reference.allocation_size_bytes
        if address <= 0 or size <= 0 or address + size > 1 << 64:
            raise MeshIrError("E_BINDING_ROLE", "weight requirement range is invalid", symbol_id=symbol_id)
        ranges.add((address, size, tensor.content_sha256))
    if len(ranges) != 1:
        raise MeshIrError("E_BINDING_ROLE", "request weight must have one identical admitted range and content across its variants", symbol_id=symbol_id)
    address, size, _ = ranges.pop()
    return address, size


def _instance_counts(program, program_id: int, profile_id: int) -> tuple:
    profile = next(record for record in program.agent_request_profiles
                   if record.program_id == program_id and record.profile_id == profile_id)
    counts = tuple(sorted({planned_instance_count(program, profile, path)
                           for path in range(3) if profile.path_mask & (1 << path)}))
    if not counts or counts[0] == 0:
        raise MeshIrError("E_REQUEST_PROFILE", "execution plan has no instance",
                          program_id=program_id, profile_id=profile_id)
    return counts


def build_host_binding_plans(program, region_bases, *, invocations: tuple[BoundInvocation, ...] = ()) -> tuple:
    if type(invocations) is not tuple or any(type(row) is not BoundInvocation for row in invocations):
        raise MeshIrError('E_RELOCATION', 'Host model plan requires typed bound invocations')
    invocation_bindings = {}
    for invocation in invocations:
        key = (invocation.entrypoint_id, invocation.profile_id)
        if (key in invocation_bindings or invocation.program_semantic_sha256 != program.semantic_sha256 or
                invocation.arch_digest != program.arch_digest):
            raise MeshIrError('E_RELOCATION', 'Host model invocation is duplicated or has a different input identity')
        invocation_bindings[key] = {row.slot_id: row for row in invocation.bindings}
    keys = sorted({(requirement.request_program_id,
                    requirement.request_profile_id)
                   for requirement in program.agent_request_binding_requirements})
    if not keys:
        raise MeshIrError("E_BINDING_ROLE",
                          "program declares no binding requirement")
    plans = []
    catalog = ProgramBindingCatalog(program)
    workspace_sizes = {}
    if any(row.binding_kind == REQUEST_WORKSPACE for row in program.agent_request_binding_requirements):
        from mesh_ir.serving_tensor_bindings import ServingTensorBindingCatalog
        workspace_sizes = ServingTensorBindingCatalog(program).workspace_sizes
    for program_id, profile_id in keys:
        profile = next(record for record in program.agent_request_profiles
                       if record.program_id == program_id and
                       record.profile_id == profile_id)
        requirements = []
        for requirement in request_binding_requirements(program, program_id,
                                                        profile_id):
            address = 0
            bytes_ = 0
            if requirement.kind == WEIGHT_EXTERNAL:
                address, bytes_ = _platform_range(program, catalog, profile,
                                                  requirement.symbol_id,
                                                  region_bases, invocation_bindings)
            elif requirement.kind == REQUEST_WORKSPACE:
                bytes_ = workspace_sizes[(program_id, profile_id)]
                if not bytes_:
                    raise MeshIrError('E_BINDING_ROLE', 'workspace requirement has no mapped storage')
            requirements.append(HostBindingRequirement(
                symbol_id=requirement.symbol_id,
                kind=requirement.kind,
                flags=requirement.flags,
                platform_address=address,
                required_bytes=bytes_,
            ))
        plans.append(HostBindingPlan(
            program_id=program_id,
            profile_id=profile_id,
            primary_input_symbol_id=profile.primary_input_symbol_id,
            primary_output_symbol_id=profile.primary_output_symbol_id,
            primary_kv_symbol_id=profile.primary_kv_symbol_id,
            requirements=tuple(requirements),
            instance_counts=_instance_counts(program, program_id, profile_id),
            publish_chunk_bytes=profile.publish_chunk_bytes,
        ))
    return tuple(plans)


def resolve_host_binding_records(plan, input_address, input_bytes,
                                 output_address, output_bytes,
                                 kv_session_slot_bytes, *, workspace_address=0) -> tuple:
    records = []
    for requirement in plan.requirements:
        if requirement.kind == HOST_INPUT:
            address, bytes_ = input_address, input_bytes
        elif requirement.kind == HOST_OUTPUT:
            address, bytes_ = output_address, output_bytes
        elif requirement.kind == KV_EXTERNAL:
            address, bytes_ = 0, kv_session_slot_bytes
        elif requirement.kind == REQUEST_WORKSPACE:
            address, bytes_ = workspace_address, plan.workspace_bytes
        elif requirement.kind == WEIGHT_EXTERNAL:
            address = requirement.platform_address
            bytes_ = requirement.required_bytes
        else:
            raise MeshIrError("E_BINDING_ROLE", "unknown binding kind",
                              kind=requirement.kind)
        if bytes_ <= 0 or address < 0 or address + bytes_ > (1 << 64) - 1:
            raise MeshIrError('E_BINDING_ROLE', 'binding range is empty or overflows', symbol_id=requirement.symbol_id)
        if requirement.kind != KV_EXTERNAL and address == 0:
            raise MeshIrError("E_BINDING_ROLE", "binding address is zero",
                              symbol_id=requirement.symbol_id)
        records.append((requirement.symbol_id, requirement.kind,
                        requirement.flags, address, bytes_))
    return tuple(records)

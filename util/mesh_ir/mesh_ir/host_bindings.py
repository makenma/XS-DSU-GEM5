"""Host parameter binding contract for a selected request profile.

The unique truth source for a request's binding set is the selected request
profile's ``AGENT_REQUEST_BINDING_REQUIREMENTS`` (main contract 8.3).  This
module owns both projections of those requirements:

* ``build_host_binding_plans`` produces the frozen plan the plan image carries
  to the Host: symbol/kind/flags, the platform-resident WEIGHT_EXTERNAL range
  resolved from the program relocation, and the frozen successful instance
  count of the profile's execution plan.
* ``resolve_host_binding_records`` derives the runtime table the Host must
  observe for a given request geometry.  HOST_INPUT/HOST_OUTPUT/KV_EXTERNAL
  stay runtime-resolved so no NPU physical slot or arena address freezes into
  the plan image.
"""

from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.generated import agent_abi as AG
from mesh_ir.model import MeshIrError
from mesh_ir.serving_profiles import planned_instance_count

HOST_INPUT = AG.BINDING_KIND.HOST_INPUT
HOST_OUTPUT = AG.BINDING_KIND.HOST_OUTPUT
KV_EXTERNAL = AG.BINDING_KIND.KV_EXTERNAL
WEIGHT_EXTERNAL = AG.BINDING_KIND.WEIGHT_EXTERNAL

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
    platform_bytes: int = 0


@dataclass(frozen=True)
class HostBindingPlan:
    program_id: int
    profile_id: int
    primary_input_symbol_id: int
    primary_output_symbol_id: int
    primary_kv_symbol_id: int
    requirements: tuple
    instance_count: int


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


def _relocation(program, symbol_id: int):
    for relocation in program.relocations:
        if relocation.symbol_sid == symbol_id:
            return relocation
    raise MeshIrError("E_BINDING_ROLE", "requirement symbol has no relocation",
                      symbol_id=symbol_id)


def _tensor_bytes(program, tensor_id: int) -> int:
    spans = [shard.span_bytes for shard in program.shards
             if shard.tensor_id == tensor_id]
    if not spans:
        raise MeshIrError("E_BINDING_ROLE", "weight tensor has no shard",
                          tensor_id=tensor_id)
    return sum(spans)


def _platform_range(program, symbol_id: int, region_bases):
    relocation = _relocation(program, symbol_id)
    if relocation.region_id >= len(region_bases):
        raise MeshIrError("E_BINDING_ROLE", "requirement region is not mapped",
                          symbol_id=symbol_id)
    bytes_ = _tensor_bytes(program, relocation.tensor_id)
    if bytes_ == 0:
        raise MeshIrError("E_BINDING_ROLE", "weight range is empty",
                          symbol_id=symbol_id)
    return region_bases[relocation.region_id] + relocation.offset_bytes, bytes_


def _instance_count(program, program_id: int, profile_id: int) -> int:
    paths = {instance.path_kind for instance in program.agent_instance_profiles
             if instance.request_program_id == program_id and
             instance.request_profile_id == profile_id}
    if len(paths) != 1:
        raise MeshIrError("E_REQUEST_PROFILE", "plan needs exactly one path",
                          program_id=program_id, profile_id=profile_id)
    path_kind = paths.pop()
    profile = next(record for record in program.agent_request_profiles
                   if record.program_id == program_id and
                   record.profile_id == profile_id)
    count = planned_instance_count(program, profile, path_kind)
    if count == 0:
        raise MeshIrError("E_REQUEST_PROFILE",
                          "execution plan has no instance",
                          program_id=program_id, profile_id=profile_id)
    return count


def build_host_binding_plans(program, region_bases) -> tuple:
    keys = sorted({(requirement.request_program_id,
                    requirement.request_profile_id)
                   for requirement in program.agent_request_binding_requirements})
    if not keys:
        raise MeshIrError("E_BINDING_ROLE",
                          "program declares no binding requirement")
    plans = []
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
                address, bytes_ = _platform_range(program,
                                                  requirement.symbol_id,
                                                  region_bases)
            requirements.append(HostBindingRequirement(
                symbol_id=requirement.symbol_id,
                kind=requirement.kind,
                flags=requirement.flags,
                platform_address=address,
                platform_bytes=bytes_,
            ))
        plans.append(HostBindingPlan(
            program_id=program_id,
            profile_id=profile_id,
            primary_input_symbol_id=profile.primary_input_symbol_id,
            primary_output_symbol_id=profile.primary_output_symbol_id,
            primary_kv_symbol_id=profile.primary_kv_symbol_id,
            requirements=tuple(requirements),
            instance_count=_instance_count(program, program_id, profile_id),
        ))
    return tuple(plans)


def resolve_host_binding_records(plan, input_address, input_bytes,
                                 output_address, output_bytes,
                                 kv_session_slot_bytes) -> tuple:
    records = []
    for requirement in plan.requirements:
        if requirement.kind == HOST_INPUT:
            address, bytes_ = input_address, input_bytes
        elif requirement.kind == HOST_OUTPUT:
            address, bytes_ = output_address, output_bytes
        elif requirement.kind == KV_EXTERNAL:
            address, bytes_ = 0, kv_session_slot_bytes
        elif requirement.kind == WEIGHT_EXTERNAL:
            address = requirement.platform_address
            bytes_ = requirement.platform_bytes
        else:
            raise MeshIrError("E_BINDING_ROLE", "unknown binding kind",
                              kind=requirement.kind)
        if bytes_ == 0:
            raise MeshIrError("E_BINDING_ROLE", "binding range is empty",
                              symbol_id=requirement.symbol_id)
        if requirement.kind != KV_EXTERNAL and address == 0:
            raise MeshIrError("E_BINDING_ROLE", "binding address is zero",
                              symbol_id=requirement.symbol_id)
        records.append((requirement.symbol_id, requirement.kind,
                        requirement.flags, address, bytes_))
    return tuple(records)

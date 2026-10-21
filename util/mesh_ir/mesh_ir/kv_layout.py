"""Canonical KV layout projection for the serving contract identity."""

from __future__ import annotations

import hashlib

from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, StorageClass, TensorRole
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.canonical import checked_add_u64, to_canonical
from mesh_ir.model import (
    MeshIrError,
    Program,
    canonical_json_bytes,
)

KV_LAYOUT_DOMAIN = b"AI_MESH_KV_LAYOUT_V2\0"
KV_LAYOUT_SCHEMA = "mesh_kv_layout_v2"


def _fail(detail: str) -> None:
    raise MeshIrError("E_KV_CONTRACT_MISMATCH", detail)


def kv_layout_projection(program: Program) -> dict:
    catalog = ProgramBindingCatalog(program)
    tensors = {tensor.tensor_id: tensor for tensor in program.tensors}
    selected = tuple(item for item in program.agent_request_profiles if item.flags & A.AGENT_REQUEST_FLAGS.HAS_KV)
    requests = {(item.program_id, item.profile_id): item for item in selected}
    if not requests or len(requests) != len(selected):
        _fail("KV request identities are missing or duplicated")
    token_bytes = {item.kv_bytes_per_token for item in requests.values()}
    if len(token_bytes) != 1 or next(iter(token_bytes)) <= 0:
        _fail("kv_bytes_per_token must be positive and identical across request profiles")
    if any(not 1 <= item.primary_kv_symbol_id <= len(program.strings) for item in requests.values()):
        _fail("request profile has no KV role")
    instances = sorted(program.agent_instance_profiles, key=lambda item: item.instance_profile_id)
    if len({item.instance_profile_id for item in instances}) != len(instances):
        _fail("KV instance identities are duplicated")
    seen, bound_requests, consumed_members = set(), set(), set()
    bindings = []
    for instance in instances:
        if not instance.primary_kv_symbol_id:
            continue
        key = (instance.request_program_id, instance.request_profile_id)
        request = requests.get(key)
        if request is None or instance.primary_kv_symbol_id != request.primary_kv_symbol_id:
            _fail("instance KV role differs from its request")
        begin = instance.member_binding_first
        end = begin + instance.member_binding_count
        if instance.member_count <= 0 or instance.member_binding_count != instance.member_count or begin < 0 or end > len(program.agent_instance_member_bindings):
            _fail("KV member span is invalid")
        for index in range(begin, end):
            member = program.agent_instance_member_bindings[index]
            if index in consumed_members or member.instance_profile_id != instance.instance_profile_id or member.member_ordinal != index - begin:
                _fail("KV member has no unique instance owner")
            consumed_members.add(index)
            symbol = member.static_kv_symbol_id
            if not 1 <= symbol <= len(program.strings):
                _fail("KV member slot symbol is missing")
            try:
                binding = catalog.binding(instance.mesh_entrypoint_id, instance.mesh_profile_id,
                                          program.strings[symbol - 1].value)
            except MeshIrError as error:
                raise MeshIrError("E_KV_CONTRACT_MISMATCH", "KV member slot has no admitted variant owner") from error
            tensor = tensors.get(binding.tensor.tensor_id)
            if (tensor is None or binding.tensor.role is not TensorRole.KV_CACHE or
                    binding.tensor.storage_class is not StorageClass.EXTERNAL or binding.tensor.access is not Access.READ_WRITE or
                    tensor.role != A.TENSOR_ROLE.KV_CACHE or tensor.storage_class != A.STORAGE_CLASS.EXTERNAL or tensor.access != A.ACCESS_KIND.READ_WRITE):
                _fail("KV member slot must own an external read-write KV tensor")
            relocation = next((item for item in program.relocations if item.relocation_id == binding.slot.slot_id), None)
            if relocation is None or relocation.tensor_id != tensor.tensor_id or relocation.symbol_sid != symbol:
                _fail("KV member relocation differs from its admitted slot")
            seen.add(tensor.tensor_id)
            bound_requests.add(key)
            bindings.append({"request": to_canonical(request), "instance": to_canonical(instance),
                             "member": to_canonical(member), "relocation": to_canonical(relocation),
                             "tensor": to_canonical(tensor)})
    if bound_requests != set(requests):
        _fail("KV request has no bound instance member")
    if consumed_members != {index for index, member in enumerate(program.agent_instance_member_bindings) if member.static_kv_symbol_id}:
        _fail("KV member is not owned by its instance span")
    descriptors = [to_canonical(item) for item in sorted(program.dma_descriptors, key=lambda item: item.descriptor_id)
                   if item.kind in (A.DMA_KIND.LOAD, A.DMA_KIND.STORE) and (item.src.tensor_id in seen or item.dst.tensor_id in seen)]
    return {"bindings": bindings, "descriptors": descriptors,
            "schema": KV_LAYOUT_SCHEMA, "token_bytes": token_bytes.pop()}


def reference_kv_pool_base(program: Program, region_bases: list[int] | tuple[int, ...]) -> int:
    kv_layout_projection(program)
    catalog = ProgramBindingCatalog(program)
    origins = set()
    for instance in program.agent_instance_profiles:
        if not instance.primary_kv_symbol_id:
            continue
        member = program.agent_instance_member_bindings[instance.member_binding_first]
        binding = catalog.binding(instance.mesh_entrypoint_id, instance.mesh_profile_id,
            program.strings[member.static_kv_symbol_id - 1].value).slot.reference_binding
        if binding.region_id >= len(region_bases) or type(region_bases[binding.region_id]) is not int:
            _fail("KV reference binding has no architecture region base")
        origins.add(checked_add_u64(region_bases[binding.region_id], binding.allocation_offset_bytes, "KV pool reference base"))
    if len(origins) != 1:
        _fail("KV member-zero references need an explicit runtime pool base")
    return origins.pop()


def kv_layout_digest(program: Program) -> bytes:
    return hashlib.sha256(
        KV_LAYOUT_DOMAIN +
        canonical_json_bytes(kv_layout_projection(program))).digest()


def verify_kv_layout(program: Program) -> None:
    if not program.required_features & A.AGENT_SERVING_V1 or not any(
            row.flags & A.AGENT_REQUEST_FLAGS.HAS_KV for row in program.agent_request_profiles):
        return
    kv_layout_digest(program)


def serving_contract_digest(program: Program,
                            model_weight_image_digest: bytes) -> bytes:
    from mesh_ir.kv_types import kv_contract_digest

    token_bytes = {request.kv_bytes_per_token
                   for request in program.agent_request_profiles if request.flags & A.AGENT_REQUEST_FLAGS.HAS_KV}
    if len(token_bytes) != 1:
        _fail("kv_bytes_per_token differs across request profiles")
    return kv_contract_digest(bytes.fromhex(program.semantic_sha256),
                              model_weight_image_digest,
                              kv_layout_digest(program), token_bytes.pop())

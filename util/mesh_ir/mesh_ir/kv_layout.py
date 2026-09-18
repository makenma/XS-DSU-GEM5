"""Canonical KV layout projection for the serving contract identity."""

from __future__ import annotations

import hashlib

from mesh_ir.generated import abi as A
from mesh_ir.model import (
    MeshIrError,
    Program,
    canonical,
    canonical_json_bytes,
)

KV_LAYOUT_DOMAIN = b"AI_MESH_KV_LAYOUT_V1\0"
KV_LAYOUT_SCHEMA = "mesh_kv_layout_v1"


def _fail(detail: str) -> None:
    raise MeshIrError("E_KV_CONTRACT_MISMATCH", detail)


def _relocation(program: Program, symbol_id: int):
    for relocation in program.relocations:
        if relocation.symbol_sid == symbol_id:
            return relocation
    _fail("KV symbol has no relocation")
    return None


def kv_layout_projection(program: Program) -> dict:
    tensors = {tensor.tensor_id: tensor for tensor in program.tensors}
    seen = set()
    token_bytes = set()
    bindings = []

    def record_binding(role: str, profile, symbol_id: int):
        relocation = _relocation(program, symbol_id)
        tensor = tensors.get(relocation.tensor_id)
        if tensor is None:
            _fail("KV binding references an unknown tensor")
        seen.add(relocation.tensor_id)
        return {"role": role, "profile": canonical(profile),
                "relocation": canonical(relocation),
                "tensor": canonical(tensor)}

    for request in sorted(program.agent_request_profiles,
                          key=lambda item: (item.program_id, item.profile_id)):
        if request.primary_kv_symbol_id == 0:
            _fail("request profile has no KV binding")
        token_bytes.add(request.kv_bytes_per_token)
        bindings.append(record_binding("REQUEST", request,
                                       request.primary_kv_symbol_id))
    for instance in sorted(program.agent_instance_profiles,
                           key=lambda item: item.instance_profile_id):
        if instance.primary_kv_symbol_id == 0:
            continue
        bindings.append(record_binding("INSTANCE", instance,
                                       instance.primary_kv_symbol_id))
    for member in program.agent_instance_member_bindings:
        if member.static_kv_symbol_id == 0:
            continue
        bindings.append(record_binding("MEMBER", member,
                                       member.static_kv_symbol_id))
    if len(seen) != 1:
        _fail("KV bindings do not resolve to one tensor")
    if len(token_bytes) != 1:
        _fail("kv_bytes_per_token differs across request profiles")
    descriptors = []
    for descriptor in sorted(program.dma_descriptors,
                             key=lambda item: item.descriptor_id):
        if descriptor.kind not in (A.DMA_KIND.LOAD, A.DMA_KIND.STORE):
            continue
        if descriptor.src.tensor_id not in seen and \
                descriptor.dst.tensor_id not in seen:
            continue
        descriptors.append(canonical(descriptor))
    return {
        "bindings": bindings,
        "descriptors": descriptors,
        "schema": KV_LAYOUT_SCHEMA,
        "token_bytes": token_bytes.pop(),
    }


def kv_layout_digest(program: Program) -> bytes:
    return hashlib.sha256(
        KV_LAYOUT_DOMAIN +
        canonical_json_bytes(kv_layout_projection(program))).digest()


def verify_kv_layout(program: Program) -> None:
    if not program.required_features & A.AGENT_SERVING_V1:
        return
    kv_layout_digest(program)


def serving_contract_digest(program: Program,
                            model_weight_image_digest: bytes) -> bytes:
    from mesh_ir.kv_types import kv_contract_digest

    token_bytes = {request.kv_bytes_per_token
                   for request in program.agent_request_profiles}
    if len(token_bytes) != 1:
        _fail("kv_bytes_per_token differs across request profiles")
    return kv_contract_digest(bytes.fromhex(program.semantic_sha256()),
                              model_weight_image_digest,
                              kv_layout_digest(program), token_bytes.pop())

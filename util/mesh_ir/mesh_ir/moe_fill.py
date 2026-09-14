"""Deterministic overlay fill patterns (contract 7.9, 7.9.1).

Padding rows, fully dropped tokens and the route-buffer records are all
byte-exact data: the same layer/expert/row must reproduce the same bytes in
Python and C++, and the three functional modes (FUNCTIONAL_BYTES,
DIGEST_ONLY, VALIDITY_ONLY) must agree on commands, SRAM byte counts and
cycles while differing only in what is physically installed.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.moe_uid import SemanticTokenUid

PAD_KEY_DOMAIN = b"AI_MESH_MOE_PAD_FILL_KEY_V1\0"
PAD_BLOCK_DOMAIN = b"AI_MESH_MOE_PAD_FILL_BLOCK_V1\0"
DROP_PROJECTION_DOMAIN = b"AI_MESH_MOE_DROP_PROJECTION_V1\0"
DROP_KEY_DOMAIN = b"AI_MESH_MOE_DROPPED_FILL_KEY_V1\0"
DROP_BLOCK_DOMAIN = b"AI_MESH_MOE_DROPPED_FILL_BLOCK_V1\0"
FUNCTIONAL_BYTES = A.MOE_FILL_MODE.FUNCTIONAL_BYTES
DIGEST_ONLY = A.MOE_FILL_MODE.DIGEST_ONLY
VALIDITY_ONLY = A.MOE_FILL_MODE.VALIDITY_ONLY
FILL_MODES = (FUNCTIONAL_BYTES, DIGEST_ONLY, VALIDITY_ONLY)


@dataclass(frozen=True)
class FillInstallation:
    mode: int
    bytes_installed: int
    digest: bytes
    functional: bytes = b""

    def row_bytes(self) -> bytes:
        if self.mode == FUNCTIONAL_BYTES:
            return self.functional
        return bytes(self.bytes_installed)


def _blocks(key: bytes, byte_count: int, domain: bytes) -> bytes:
    if byte_count < 0:
        raise MeshIrError("E_MOE_MATERIALIZATION_V", "negative fill length")
    body = bytearray()
    ordinal = 0
    while len(body) < byte_count:
        body += hashlib.sha256(domain + key +
                               struct.pack("<Q", ordinal)).digest()
        ordinal += 1
    return bytes(body[:byte_count])


def pad_fill_key(program_semantic_digest: bytes, workload_plan_digest: bytes,
                 layer_id: int, expert_id: int, padding_row: int,
                 input_token_row_bytes: int) -> bytes:
    if len(program_semantic_digest) != 32 or len(workload_plan_digest) != 32:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "fill keys need 32 B program/plan digests")
    if padding_row < 0 or input_token_row_bytes <= 0:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "padding row geometry is invalid")
    return hashlib.sha256(
        PAD_KEY_DOMAIN + program_semantic_digest + workload_plan_digest +
        struct.pack("<III", layer_id, expert_id, padding_row) +
        struct.pack("<I", input_token_row_bytes)).digest()


def pad_fill_bytes(program_semantic_digest: bytes, workload_plan_digest: bytes,
                   layer_id: int, expert_id: int, padding_row: int,
                   input_token_row_bytes: int) -> bytes:
    key = pad_fill_key(program_semantic_digest, workload_plan_digest, layer_id,
                       expert_id, padding_row, input_token_row_bytes)
    return _blocks(key, input_token_row_bytes, PAD_BLOCK_DOMAIN)


def drop_projection_digest(entries) -> bytes:
    body = bytearray()
    for topk_slot, selected_expert_id, disposition in entries:
        body += struct.pack("<HHB", topk_slot, selected_expert_id,
                            disposition) + bytes(3)
    return hashlib.sha256(DROP_PROJECTION_DOMAIN +
                          struct.pack("<I", len(entries)) +
                          bytes(body)).digest()


def drop_fill_key(program_semantic_digest: bytes, workload_plan_digest: bytes,
                  uid: SemanticTokenUid, layer_id: int,
                  output_token_row_bytes: int, entries) -> bytes:
    projection = drop_projection_digest(entries)
    return hashlib.sha256(
        DROP_KEY_DOMAIN + program_semantic_digest + workload_plan_digest +
        uid.encode() + struct.pack("<II", layer_id, output_token_row_bytes) +
        projection).digest()


def drop_fill_bytes(program_semantic_digest: bytes,
                    workload_plan_digest: bytes, uid: SemanticTokenUid,
                    layer_id: int,
                    output_token_row_bytes: int, entries) -> bytes:
    key = drop_fill_key(program_semantic_digest, workload_plan_digest, uid,
                        layer_id, output_token_row_bytes, entries)
    return _blocks(key, output_token_row_bytes, DROP_BLOCK_DOMAIN)


def install_fill(mode: int, functional: bytes) -> FillInstallation:
    if mode not in FILL_MODES:
        raise MeshIrError("E_MOE_MATERIALIZATION_V", "unknown fill mode")
    digest = hashlib.sha256(functional).digest()
    if mode == FUNCTIONAL_BYTES:
        return FillInstallation(mode=mode, bytes_installed=len(functional),
                                digest=digest, functional=functional)
    return FillInstallation(mode=mode, bytes_installed=len(functional),
                            digest=digest)


def encode_route_entry(uid: SemanticTokenUid, member_request_id: int,
                       source_rank: int, token_ordinal: int, topk_slot: int,
                       selected_expert_id: int, assigned_expert_id: int,
                       destination_core: int, disposition: int) -> bytes:
    if assigned_expert_id > 0xFFFF or destination_core > 0xFFFF:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "route entry core/expert out of range")
    if disposition not in (A.ROUTE_DISPOSITION.ACCEPT,
                           A.ROUTE_DISPOSITION.DROP):
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "unknown route disposition")
    return A.MOE_RUNTIME_ROUTE_ENTRY_FORMAT.pack(
        uid.encode(), member_request_id, source_rank, token_ordinal, topk_slot,
        selected_expert_id, assigned_expert_id, destination_core, disposition,
        b"\0" * 7)


def decode_route_entry(payload: bytes) -> dict:
    values = A.MOE_RUNTIME_ROUTE_ENTRY_FORMAT.unpack(payload)
    names = [field["name"] for field in A.MOE_RUNTIME_ROUTE_ENTRY_FIELDS]
    entry = dict(zip(names, values))
    entry["uid"] = SemanticTokenUid.decode(entry.pop("semantic_token_uid"))
    if entry["reserved"] != bytes(7):
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "route entry reserved bytes must be zero")
    if entry["disposition"] not in (A.ROUTE_DISPOSITION.ACCEPT,
                                    A.ROUTE_DISPOSITION.DROP):
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "unknown route disposition")
    return entry


def route_buffer_bytes(entries) -> bytes:
    if not entries:
        raise MeshIrError("E_MOE_MATERIALIZATION_V",
                          "route buffer needs at least one entry")
    return b"".join(entries)

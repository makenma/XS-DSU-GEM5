"""AgentAxiDriver SQ/CQ protocol records (spec section 8).

Layouts and enums come from the generated agent_abi module (SSOT:
agent_protocol_abi.yaml); this module adds CRC32C and fail-closed
encode/decode helpers shared by the driver, the frontend and the tests.
"""

from __future__ import annotations

import struct

from mesh_ir.generated import agent_abi as A

_CRC32C_TABLE = []


def _build_table():
    poly = 0x82F63B78
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (poly if c & 1 else 0)
        _CRC32C_TABLE.append(c)


_build_table()


def crc32c(data: bytes, crc: int = 0) -> int:
    crc ^= 0xFFFFFFFF
    for byte in data:
        crc = _CRC32C_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _grouped(record: str, blob: bytes):
    fields = getattr(A, f"{record}_FIELDS")
    values = getattr(A, f"{record}_FORMAT").unpack(blob)
    grouped = {}
    cursor = 0
    for field in fields:
        if field["type"] == "u64x8":
            grouped[field["name"]] = tuple(values[cursor:cursor + 8])
            cursor += 8
        else:
            grouped[field["name"]] = values[cursor]
            cursor += 1
    return grouped


def _pack(record: str, values: dict, crc_field: str = None) -> bytes:
    fields = getattr(A, f"{record}_FIELDS")
    ordered = []
    for field in fields:
        name = field["name"]
        if name == crc_field:
            ordered.append(0)
            continue
        if name not in values:
            raise ProtocolError("E_SQ_MALFORMED", f"missing field {name}")
        ordered.append(values[name])
    return getattr(A, f"{record}_FORMAT").pack(*ordered)


def encode_sq(values: dict) -> bytes:
    blob = bytearray(_pack("SQ_DESCRIPTOR", values, "crc32"))
    crc = crc32c(blob[:60])
    struct.pack_into("<I", blob, A.SQ_DESCRIPTOR_FIELD_OFFSETS["crc32"], crc)
    return bytes(blob)


def decode_sq(blob: bytes) -> dict:
    if len(blob) != A.SQ_DESCRIPTOR_BYTES:
        raise ProtocolError("E_SQ_MALFORMED", "SQ record must be 64 bytes")
    values = _grouped("SQ_DESCRIPTOR", blob)
    stored = values.pop("crc32")
    if crc32c(blob[:60]) != stored:
        raise ProtocolError("E_SQ_CRC", "SQ CRC32C mismatch")
    return values


def encode_cq(values: dict) -> bytes:
    return _pack("CQ_DESCRIPTOR", values)


def decode_cq(blob: bytes) -> dict:
    if len(blob) != A.CQ_DESCRIPTOR_BYTES:
        raise ProtocolError("E_SQ_MALFORMED", "CQ record must be 32 bytes")
    return _grouped("CQ_DESCRIPTOR", blob)


def encode_parameter(values: dict, tail: bytes = b"") -> bytes:
    """Encode one complete parameter block (header + bindings + TLVs).

    The CRC covers the full total_bytes with the CRC field treated as zero
    (spec 8.3).  The header total_bytes must equal 160 + len(tail)."""
    total = A.PARAMETER_HEADER_BYTES + len(tail)
    values = dict(values)
    if "total_bytes" in values and values["total_bytes"] != total:
        raise ProtocolError(
            "E_PARAMETER_LENGTH_MISMATCH",
            f"total_bytes {values['total_bytes']} != header+tail {total}",
        )
    values["total_bytes"] = total
    blob = bytearray(_pack("PARAMETER_HEADER", values, "crc32") + tail)
    crc_offset = A.PARAMETER_HEADER_FIELD_OFFSETS["crc32"]
    struct.pack_into("<I", blob, crc_offset, 0)
    crc = crc32c(blob)
    struct.pack_into("<I", blob, crc_offset, crc)
    return bytes(blob)


def decode_parameter(blob: bytes) -> dict:
    if len(blob) < A.PARAMETER_HEADER_BYTES:
        raise ProtocolError("E_SQ_MALFORMED", "parameter header must be 160 bytes")
    header = bytes(blob[:A.PARAMETER_HEADER_BYTES])
    values = _grouped("PARAMETER_HEADER", header)
    stored = values.pop("crc32")
    total = values["total_bytes"]
    if total < A.PARAMETER_HEADER_BYTES or total % 8 or total != len(blob):
        raise ProtocolError(
            "E_PARAMETER_LENGTH_MISMATCH",
            f"parameter total_bytes {total} invalid for {len(blob)} bytes",
        )
    covered = bytearray(blob[:total])
    crc_offset = A.PARAMETER_HEADER_FIELD_OFFSETS["crc32"]
    struct.pack_into("<I", covered, crc_offset, 0)
    if crc32c(covered) != stored:
        raise ProtocolError("E_SQ_CRC", "parameter CRC32C mismatch")
    values["binding_records"] = []
    binding_count = values["binding_count"]
    binding_offset = values["binding_table_offset"]
    binding_bytes = binding_count * A.BINDING_RECORD_BYTES
    extension_offset = values["extension_offset"]
    extension_bytes = values["extension_bytes"]
    if values["binding_record_bytes"] != A.BINDING_RECORD_BYTES:
        raise ProtocolError("E_REQUEST_BINDING", "binding record size mismatch")
    if binding_count:
        if binding_offset % 8 or binding_offset < A.PARAMETER_HEADER_BYTES or \
                binding_offset + binding_bytes > total:
            raise ProtocolError(
                "E_REQUEST_BINDING", "binding table outside the parameter block")
        for index in range(binding_count):
            start = binding_offset + index * A.BINDING_RECORD_BYTES
            values["binding_records"].append(
                decode_binding(blob[start:start + A.BINDING_RECORD_BYTES]))
    if extension_bytes:
        if extension_offset % 8 or extension_offset < A.PARAMETER_HEADER_BYTES \
                or extension_offset + extension_bytes > total:
            raise ProtocolError(
                "E_REQUEST_BINDING", "extension outside the parameter block")
        if binding_count and max(binding_offset, extension_offset) < min(
                binding_offset + binding_bytes,
                extension_offset + extension_bytes):
            raise ProtocolError(
                "E_REQUEST_BINDING", "binding table overlaps extension")
    values["tlvs"] = decode_tlvs(
        blob[extension_offset:extension_offset + extension_bytes]
        if extension_bytes else b"")
    return values


def encode_binding(values: dict) -> bytes:
    return _pack("BINDING_RECORD", values)


def decode_binding(blob: bytes) -> dict:
    if len(blob) != A.BINDING_RECORD_BYTES:
        raise ProtocolError("E_SQ_MALFORMED", "binding record must be 24 bytes")
    return _grouped("BINDING_RECORD", blob)


def encode_tlv(tlv_type: int, payload: bytes, flags: int = 0) -> bytes:
    if len(payload) % 8:
        raise ProtocolError("E_SQ_MALFORMED", "TLV payload must be 8-byte sized")
    return struct.pack("<HHI", tlv_type, flags, len(payload)) + payload


def decode_tlvs(blob: bytes) -> list:
    out = []
    offset = 0
    while offset < len(blob):
        if offset + 8 > len(blob):
            raise ProtocolError("E_SQ_MALFORMED", "truncated TLV header")
        tlv_type, flags, payload_bytes = struct.unpack_from("<HHI", blob, offset)
        offset += 8
        if offset + payload_bytes > len(blob):
            raise ProtocolError("E_SQ_MALFORMED", "truncated TLV payload")
        out.append({
            "type": tlv_type,
            "flags": flags,
            "payload": blob[offset:offset + payload_bytes],
        })
        offset += payload_bytes
    types = [t["type"] for t in out]
    if types != sorted(types):
        raise ProtocolError("E_SQ_MALFORMED", "TLV types must be ascending")
    if len(set(types)) != len(types):
        raise ProtocolError("E_SQ_MALFORMED", "duplicate TLV type")
    return out


def encode_metadata(values: dict, tail: bytes = b"") -> bytes:
    total = A.OUTPUT_METADATA_BYTES + len(tail)
    values = dict(values)
    values["total_bytes"] = total
    blob = bytearray(_pack("OUTPUT_METADATA", values, "crc32"))
    crc_offset = A.OUTPUT_METADATA_FIELD_OFFSETS["crc32"]
    full = bytearray(bytes(blob) + tail)
    crc = crc32c(full)
    struct.pack_into("<I", full, crc_offset, crc)
    return bytes(full)


def decode_metadata(blob: bytes) -> dict:
    if len(blob) < A.OUTPUT_METADATA_BYTES:
        raise ProtocolError("E_SQ_MALFORMED", "metadata header must be 128 bytes")
    header = bytearray(blob[:A.OUTPUT_METADATA_BYTES])
    values = _grouped("OUTPUT_METADATA", bytes(header))
    stored = values.pop("crc32")
    total = values["total_bytes"]
    if not (A.OUTPUT_METADATA_BYTES <= total <= len(blob)):
        raise ProtocolError("E_SQ_MALFORMED", "metadata total_bytes out of range")
    if total % 8:
        raise ProtocolError("E_SQ_MALFORMED", "metadata total_bytes unaligned")
    crc_offset = A.OUTPUT_METADATA_FIELD_OFFSETS["crc32"]
    covered = bytearray(blob[:total])
    struct.pack_into("<I", covered, crc_offset, 0)
    if crc32c(covered) != stored:
        raise ProtocolError("E_SQ_CRC", "metadata CRC32C mismatch")
    values["tlvs"] = decode_tlvs(blob[A.OUTPUT_METADATA_BYTES:total])
    return values


def slot_index(seq: int, depth: int) -> int:
    return seq & (depth - 1)


def ring_occupancy(producer: int, consumer: int, depth: int) -> int:
    occupancy = producer - consumer
    if occupancy < 0 or occupancy > depth:
        raise ProtocolError("E_AGENT_PROTOCOL_FATAL", "ring occupancy out of window")
    return occupancy

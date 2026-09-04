"""Binary encoder for .mshb (Scheduled Mesh IR ABI v1).

Deterministic: the same Program always encodes to byte-identical output
(sections sorted by type, records in table order, zero padding).
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import fields as dc_fields

from mesh_ir.generated import abi as A
from mesh_ir.model import (
    RECORD_CLASSES,
    DmaEndpoint,
    DmaDescriptor,
    MeshIrError,
    OpAttr,
    Program,
)

CLASS_TO_SECTION = {cls: name for name, cls in RECORD_CLASSES.items()}

SECTION_ORDER = (
    "STRINGS",
    "ENTRYPOINTS",
    "PROFILES",
    "TENSORS",
    "SHARDS",
    "ALLOCATIONS",
    "STREAMS",
    "COMMANDS",
    "COMMAND_WAITS",
    "COMMAND_OPERANDS",
    "EVENTS",
    "DMA_DESCRIPTORS",
    "OP_ATTRS",
    "RELOCATIONS",
    "EXPECTED_TRAFFIC",
)


def _align8(value: int) -> int:
    return (value + 7) & ~7


def _pack_record(record) -> bytes:
    if isinstance(record, DmaEndpoint):
        fmt = A.DMA_ENDPOINT_FORMAT
        field_iter = dc_fields(record)
    else:
        section = CLASS_TO_SECTION[type(record)]
        fmt = getattr(A, f"{section}_FORMAT")
        field_iter = dc_fields(record)
    args = []
    for f in field_iter:
        value = getattr(record, f.name)
        if isinstance(value, tuple):
            args.extend(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            args.append(value)
        elif isinstance(value, bytes):
            args.append(value)
        else:
            raise MeshIrError("E_ABI_ENUM", f"field {f.name} has unsupported type", field=f.name)
    return fmt.pack(*args)


def _pack_descriptor(record) -> bytes:
    args = []
    for f in dc_fields(record):
        value = getattr(record, f.name)
        if f.name in ("src", "dst"):
            args.append(_pack_record(value))
        elif isinstance(value, tuple):
            args.extend(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            args.append(value)
        elif isinstance(value, bytes):
            args.append(value)
        else:
            raise MeshIrError("E_ABI_ENUM", f"field {f.name} has unsupported type", field=f.name)
    return A.DMA_DESCRIPTORS_FORMAT.pack(*args)


def _pack_attr(attr: OpAttr) -> bytes:
    kind_name = _attr_kind_name(attr.kind)
    fmt = getattr(A, f"{kind_name}_FORMAT")
    payload = fmt.pack(*attr.payload).ljust(28, b"\x00")
    return A.OP_ATTRS_FORMAT.pack(attr.kind, attr.reserved, payload)


def _attr_kind_name(kind: int) -> str:
    for name, value in vars(A.ATTR_KIND).items():
        if not name.startswith("_") and value == kind:
            return name
    raise MeshIrError("E_ABI_ENUM", f"unknown attr kind {kind}", kind=kind)


def encode_program(program: Program) -> bytes:
    sections: dict = {}

    blob = "".join(s.value for s in program.strings).encode("utf-8")
    directory = bytearray(struct.pack("<I", len(program.strings)))
    offset = 0
    for entry in program.strings:
        encoded = entry.value.encode("utf-8")
        directory += struct.pack("<II", offset, len(encoded))
        offset += len(encoded)
    sections["STRINGS"] = bytes(directory) + blob

    tables = {
        "ENTRYPOINTS": program.entrypoints,
        "PROFILES": program.profiles,
        "TENSORS": program.tensors,
        "SHARDS": program.shards,
        "ALLOCATIONS": program.allocations,
        "STREAMS": program.streams,
        "COMMANDS": program.commands,
        "COMMAND_WAITS": program.command_waits,
        "COMMAND_OPERANDS": program.command_operands,
        "EVENTS": program.events,
        "RELOCATIONS": program.relocations,
        "EXPECTED_TRAFFIC": program.expected_traffic,
    }
    for name, records in tables.items():
        sections[name] = b"".join(_pack_record(rec) for rec in records)
    sections["DMA_DESCRIPTORS"] = b"".join(
        _pack_descriptor(rec) for rec in program.dma_descriptors
    )
    sections["OP_ATTRS"] = b"".join(_pack_attr(attr) for attr in program.op_attrs)

    section_dir_offset = A.HEADER_BYTES
    section_dir_size = len(SECTION_ORDER) * A.SECTION_DIR_BYTES
    cursor = _align8(section_dir_offset + section_dir_size)
    entries = []
    for name in SECTION_ORDER:
        payload = sections[name]
        while cursor % 8:
            cursor += 1
        entry_offset = cursor
        entries.append(
            {
                "type": getattr(A.SECTION_TYPE, name),
                "offset": entry_offset,
                "size": len(payload),
                "count": _section_count(name, program),
                "crc32": zlib.crc32(payload) & 0xFFFFFFFF,
            }
        )
        cursor += len(payload)
    file_bytes = cursor

    directory_bytes = bytearray()
    for entry, name in zip(entries, SECTION_ORDER):
        record_bytes = _record_bytes_for(name)
        directory_bytes += A.SECTION_DIR_FORMAT.pack(
            entry["type"],
            0,
            record_bytes,
            entry["offset"],
            entry["size"],
            entry["count"],
            entry["crc32"],
            0,
        )

    body = bytearray()
    body += directory_bytes
    for entry, name in zip(entries, SECTION_ORDER):
        pad = entry["offset"] - (A.HEADER_BYTES + len(body))
        body += b"\x00" * pad
        body += sections[name]
    payload_bytes = bytes(body)
    payload_sha = hashlib.sha256(payload_bytes).digest()

    header = A.HEADER_FORMAT.pack(
        A.MAGIC,
        program.abi_major,
        program.abi_minor,
        A.HEADER_BYTES,
        file_bytes,
        section_dir_offset,
        len(SECTION_ORDER),
        0,
        program.arch_digest,
        payload_sha,
        0,
        bytes(16),
    )
    return header + payload_bytes


def _record_bytes_for(name: str) -> int:
    if name == "STRINGS":
        return 0
    if name == "COMMAND_WAITS":
        return A.COMMAND_WAITS_BYTES
    return getattr(A, f"{name}_BYTES")


def _section_count(name: str, program: Program) -> int:
    sizes = {
        "STRINGS": len(program.strings),
        "ENTRYPOINTS": len(program.entrypoints),
        "PROFILES": len(program.profiles),
        "TENSORS": len(program.tensors),
        "SHARDS": len(program.shards),
        "ALLOCATIONS": len(program.allocations),
        "STREAMS": len(program.streams),
        "COMMANDS": len(program.commands),
        "COMMAND_WAITS": len(program.command_waits),
        "COMMAND_OPERANDS": len(program.command_operands),
        "EVENTS": len(program.events),
        "DMA_DESCRIPTORS": len(program.dma_descriptors),
        "OP_ATTRS": len(program.op_attrs),
        "RELOCATIONS": len(program.relocations),
        "EXPECTED_TRAFFIC": len(program.expected_traffic),
    }
    return sizes[name]

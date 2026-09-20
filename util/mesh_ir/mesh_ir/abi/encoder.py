from __future__ import annotations

import hashlib
import struct
import zlib

from mesh_ir.abi.rules import (
    check_abi_header,
    check_field_rules,
    check_min_reader_minor,
    check_payload_rules,
    check_record_rules,
)
from mesh_ir.abi.semantic import encode_semantics, encode_string_table, semantic_payloads
from mesh_ir.abi.optional import validate_optional_sections
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.model import DmaDescriptor, DmaEndpoint, OpAttr, Program, RECORD_CLASSES, StringEntry
from mesh_ir.scheduled.model import ProgramSemantics


U64_MAX = (1 << 64) - 1


def _record_values(section_name: str, record: object, semantic_strings) -> tuple:
    values = []
    for field in getattr(A, f"{section_name}_FIELDS"):
        name = field.get("python_field", field["name"])
        value = getattr(record, name)
        if field.get("string_pool") == "SEMANTIC_STRINGS":
            value = semantic_strings.intern_string(value, name)
        if field["type"] == "record_ref":
            nested_fields = getattr(A, f"{field['ref']}_FIELDS")
            nested = getattr(A, f"{field['ref']}_FORMAT")
            nested_values = []
            for nested_field in nested_fields:
                nested_value = getattr(value, nested_field["name"])
                if nested_field["type"] == "u64x8":
                    nested_values.extend(nested_value)
                else:
                    nested_values.append(nested_value)
            value = nested.pack(*nested_values)
        if field["type"] == "u64x8":
            values.extend(value)
        else:
            values.append(value)
    return tuple(values)


def _encode_table(section_name: str, records: tuple, semantic_strings) -> bytes:
    fmt = getattr(A, f"{section_name}_FORMAT")
    try:
        return b"".join(fmt.pack(*_record_values(section_name, record, semantic_strings)) for record in records)
    except (struct.error, TypeError, OverflowError) as error:
        raise MeshIrError("E_ABI_BOUNDS", "transport record cannot be represented", section=section_name) from error


def _encode_attrs(records: tuple[OpAttr, ...]) -> bytes:
    payloads = []
    for record in records:
        name = A.PAYLOAD_BY_KIND[record.kind]
        fields = getattr(A, f"{name}_FIELDS")
        try:
            payload = getattr(A, f"{name}_FORMAT").pack(*record.payload)
            payloads.append(A.OP_ATTRS_FORMAT.pack(record.kind, record.reserved, payload.ljust(28, b"\0")))
        except (struct.error, TypeError, OverflowError) as error:
            raise MeshIrError("E_ABI_BOUNDS", "attr payload cannot be represented", kind=record.kind) from error
    return b"".join(payloads)


def _align(value: int, alignment: int = 8) -> int:
    return (value + alignment - 1) // alignment * alignment


def _assemble(program: Program, sections: list[tuple[int, int, int, bytes]]) -> bytes:
    sections.sort(key=lambda item: item[0])
    directory_bytes = len(sections) * A.SECTION_DIR_BYTES
    cursor = A.HEADER_BYTES + directory_bytes
    entries = []
    chunks = []
    for section_type, record_bytes, count, payload in sections:
        offset = _align(cursor)
        chunks.append(bytes(offset - cursor))
        chunks.append(payload)
        entries.append((section_type, 0, record_bytes, offset, len(payload), count, zlib.crc32(payload) & 0xFFFFFFFF, 0))
        cursor = offset + len(payload)
    directory = b"".join(A.SECTION_DIR_FORMAT.pack(*entry) for entry in entries)
    body = directory + b"".join(chunks)
    header = A.HEADER_FORMAT.pack(
        A.MAGIC,
        program.abi_major,
        program.abi_minor,
        A.HEADER_BYTES,
        A.HEADER_BYTES + len(body),
        A.HEADER_BYTES,
        len(entries),
        0,
        program.arch_digest,
        hashlib.sha256(body).digest(),
        program.required_features,
        bytes(16),
    )
    return header + body


def encode_program(program: Program) -> bytes:
    if type(program) is not Program or type(program.semantics) is not ProgramSemantics:
        raise MeshIrError("E_ABI_BOUNDS", "encoder requires a complete typed Program")
    check_abi_header(program.abi_major, program.abi_minor, program.required_features)
    check_min_reader_minor(
        program.min_reader_minor,
        program.abi_minor,
        program.required_features,
    )
    if type(program.arch_digest) is not bytes or len(program.arch_digest) != 32:
        raise MeshIrError("E_ABI_BOUNDS", "architecture digest must contain 32 bytes")
    for name, binding in A.TRANSPORT_CANONICAL_SECTIONS.items():
        records = getattr(program, binding["program_field"])
        if type(records) is not tuple:
            raise MeshIrError("E_ABI_BOUNDS", "program table must be an immutable tuple", section=name)
        if name == "STRINGS":
            if any(type(item) is not StringEntry for item in records):
                raise MeshIrError("E_ABI_BOUNDS", "STRINGS contains a wrong record type")
            for item in records:
                if type(item.value) is not str:
                    raise MeshIrError("E_ABI_BOUNDS", "STRINGS contains a non-string")
                try:
                    item.value.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise MeshIrError("E_ABI_BOUNDS", "STRINGS contains invalid Unicode") from error
        elif name == "OP_ATTRS":
            fields = {field["name"]: field for field in A.OP_ATTRS_FIELDS}
            for record in records:
                if type(record) is not OpAttr:
                    raise MeshIrError("E_ABI_BOUNDS", "OP_ATTRS contains a wrong record type")
                check_field_rules(fields["kind"], record.kind, "OP_ATTRS")
                check_field_rules(fields["reserved"], record.reserved, "OP_ATTRS")
                payload_name = A.PAYLOAD_BY_KIND.get(record.kind)
                if payload_name is None:
                    raise MeshIrError("E_ABI_ENUM", "unknown attr kind", kind=record.kind)
                payload_fields = getattr(A, f"{payload_name}_FIELDS")
                if type(record.payload) is not tuple or type(record.payload_fields) is not tuple:
                    raise MeshIrError("E_ABI_BOUNDS", "attr payload must use immutable tuples", kind=record.kind)
                if record.payload_fields != tuple(field["name"] for field in payload_fields) or len(record.payload) != len(payload_fields):
                    raise MeshIrError("E_ABI_BOUNDS", "attr payload fields do not match kind", kind=record.kind)
                check_payload_rules(payload_name, record.payload, payload_fields)
        else:
            expected = RECORD_CLASSES[name]
            if any(type(record) is not expected for record in records):
                raise MeshIrError("E_ABI_BOUNDS", "transport table contains a wrong record type", section=name)
            if name == "DMA_DESCRIPTORS":
                for record in records:
                    if type(record.src) is not DmaEndpoint or type(record.dst) is not DmaEndpoint:
                        raise MeshIrError("E_ABI_BOUNDS", "DMA descriptor endpoint has a wrong record type")
                    check_record_rules("DMA_ENDPOINT", (record.src, record.dst))
            check_record_rules(name, records)
    root_ref, semantic = encode_semantics(program.semantics)
    validate_optional_sections(program)
    for name, binding in A.TRANSPORT_CANONICAL_SECTIONS.items():
        for record in getattr(program, binding["program_field"]):
            for field in A.TRANSPORT_CANONICAL_FIELDS.get(name, ()):
                if field["string_pool"] == "SEMANTIC_STRINGS":
                    semantic.intern_string(getattr(record, field["name"]), field["name"])
    semantic.finalize_strings()
    transport = {}
    for name, binding in A.TRANSPORT_CANONICAL_SECTIONS.items():
        records = getattr(program, binding["program_field"])
        if binding["optional"] and not records:
            continue
        if name == "STRINGS":
            if any(type(item) is not StringEntry for item in records):
                raise MeshIrError("E_ABI_BOUNDS", "STRINGS contains a wrong record type")
            payload = encode_string_table([item.value for item in records], "STRINGS")
        elif name == "OP_ATTRS":
            payload = _encode_attrs(records)
        else:
            payload = _encode_table(name, records, semantic)
        transport[name] = (len(records), payload)

    actual_sha = semantic_sha256(program.semantic_dict())
    if program.semantic_sha256 != actual_sha:
        raise MeshIrError("E_ABI_CHECKSUM", "program semantic checksum is stale")
    semantic_sections = semantic_payloads(semantic)
    try:
        digest = bytes.fromhex(program.semantic_sha256)
    except ValueError as error:
        raise MeshIrError("E_ABI_CHECKSUM", "program semantic checksum is malformed") from error
    if len(digest) != 32:
        raise MeshIrError("E_ABI_CHECKSUM", "program semantic checksum is malformed")
    metadata = A.PROGRAM_METADATA_FORMAT.pack(
        program.min_reader_minor,
        0,
        0,
        A.SEMANTIC_REF_FORMAT.pack(root_ref[0], 0, root_ref[1]),
        digest,
    )
    all_payloads = {**transport, "PROGRAM_METADATA": (1, metadata), **semantic_sections}
    sections = []
    for name, (count, payload) in all_payloads.items():
        section_type = getattr(A.SECTION_TYPE, name)
        record_bytes = A.SECTION_RECORD_BYTES.get(section_type, 0)
        if not 0 <= count <= U64_MAX:
            raise MeshIrError("E_ABI_OVERFLOW", "section count exceeds wire range", section=name)
        sections.append((section_type, record_bytes, count, payload))
    return _assemble(program, sections)


__all__ = ["encode_program"]

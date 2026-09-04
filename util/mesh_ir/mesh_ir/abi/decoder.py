"""Fail-closed binary decoder for .mshb (Scheduled Mesh IR ABI v1).

Every header, checksum, section, enum, reserved-field and bounds violation
raises MeshIrError with a stable code before any record is installed.
"""

from __future__ import annotations

import hashlib
import struct
import zlib

from mesh_ir.abi.rules import check_payload_rules, check_record_rules
from mesh_ir.generated import abi as A
from mesh_ir.model import (
    RECORD_CLASSES,
    Allocation,
    Command,
    CommandOperand,
    CommandWait,
    DmaDescriptor,
    DmaEndpoint,
    Entrypoint,
    Event,
    ExpectedTrafficRow,
    MeshIrError,
    OpAttr,
    Profile,
    Program,
    Relocation,
    Shard,
    Stream,
    StringEntry,
    Tensor,
)

U64_MAX = (1 << 64) - 1

SECTION_RECORD_BYTES = {
    getattr(A.SECTION_TYPE, name): getattr(A, f"{name}_BYTES")
    for name in ("ENTRYPOINTS", "PROFILES", "TENSORS", "SHARDS", "ALLOCATIONS",
                 "STREAMS", "COMMANDS", "COMMAND_WAITS", "COMMAND_OPERANDS",
                 "EVENTS", "DMA_DESCRIPTORS", "OP_ATTRS", "RELOCATIONS",
                 "EXPECTED_TRAFFIC", "SOURCE_MAP", "CONTENT_DIGESTS")
}


class _Reader:
    def __init__(self, data: bytes):
        self.data = data

    def take(self, offset: int, size: int, what: str) -> memoryview:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise MeshIrError(
                "E_ABI_SECTION_RANGE",
                f"{what} out of file bounds",
                offset=offset,
                size=size,
            )
        return memoryview(self.data)[offset : offset + size]


def decode_header(data: bytes) -> dict:
    reader = _Reader(data)
    raw = reader.take(0, A.HEADER_BYTES, "header")
    values = A.HEADER_FORMAT.unpack(raw)
    names = [f["name"] for f in A.HEADER_FIELDS]
    header = dict(zip(names, values))
    if header["magic"] != A.MAGIC:
        raise MeshIrError("E_ABI_MAGIC", "bad magic", magic=hex(header["magic"]))
    if header["abi_major"] != A.ABI_MAJOR:
        raise MeshIrError("E_ABI_VERSION", "abi major mismatch", major=header["abi_major"])
    if header["abi_minor"] < A.MIN_READER_MINOR or header["abi_minor"] > A.ABI_MINOR:
        raise MeshIrError("E_ABI_VERSION", "abi minor out of supported range", minor=header["abi_minor"])
    if header["header_bytes"] != A.HEADER_BYTES:
        raise MeshIrError("E_ABI_SECTION_RANGE", "header_bytes mismatch")
    if header["flags"] != 0:
        raise MeshIrError("E_ABI_RESERVED", "header flags must be zero")
    if header["required_features"] != 0:
        raise MeshIrError("E_ABI_VERSION", "unknown required feature bits", features=hex(header["required_features"]))
    if header["reserved"] != bytes(16):
        raise MeshIrError("E_ABI_RESERVED", "header reserved bytes must be zero")
    if header["file_bytes"] != len(data):
        raise MeshIrError("E_ABI_SECTION_RANGE", "file_bytes mismatch", file_bytes=header["file_bytes"])
    payload = reader.take(A.HEADER_BYTES, len(data) - A.HEADER_BYTES, "payload")
    if hashlib.sha256(payload).digest() != header["payload_sha256"]:
        raise MeshIrError("E_ABI_CHECKSUM", "payload sha256 mismatch")
    return header


def decode_section_dir(data: bytes, header: dict) -> list:
    reader = _Reader(data)
    count = header["section_count"]
    expected_dir_span = count * A.SECTION_DIR_BYTES
    if header["section_dir_offset"] != A.HEADER_BYTES:
        raise MeshIrError("E_ABI_SECTION_RANGE", "section directory must start at header end")
    raw_entries = []
    for index in range(count):
        raw = reader.take(
            header["section_dir_offset"] + index * A.SECTION_DIR_BYTES,
            A.SECTION_DIR_BYTES,
            f"section dir entry {index}",
        )
        values = A.SECTION_DIR_FORMAT.unpack(raw)
        names = [f["name"] for f in A.SECTION_DIR_FIELDS]
        raw_entries.append(dict(zip(names, values)))
    # Required sections plus any of the three known optional sections.
    if count < len(A.REQUIRED_SECTIONS) or count > len(A.REQUIRED_SECTIONS) + 3:
        raise MeshIrError("E_ABI_SECTION_RANGE", "unexpected section count", count=count)

    last_type = 0
    last_end = header["section_dir_offset"] + expected_dir_span
    known_types = set(vars(A.SECTION_TYPE).values())
    payloads = {}
    for entry in raw_entries:
        stype = entry["section_type"]
        if stype not in known_types:
            raise MeshIrError("E_ABI_ENUM", "unknown section type", section_type=stype)
        if stype <= last_type:
            raise MeshIrError("E_ABI_ORDER", "section types must strictly increase", section_type=stype)
        last_type = stype
        if entry["flags"] != 0 or entry["reserved"] != 0:
            raise MeshIrError("E_ABI_RESERVED", "section dir flags/reserved must be zero", section_type=stype)
        if entry["offset"] % 8 != 0:
            raise MeshIrError("E_ABI_SECTION_RANGE", "section offset not 8-aligned", section_type=stype)
        if entry["offset"] < last_end:
            raise MeshIrError("E_ABI_SECTION_RANGE", "section overlaps directory or sibling", section_type=stype)
        gap = data[last_end : entry["offset"]]
        if gap.strip(b"\x00"):
            raise MeshIrError("E_ABI_CORRUPT", "padding bytes must be zero", section_type=stype)
        payload = bytes(reader.take(entry["offset"], entry["size"], f"section {stype}"))
        if entry["offset"] + entry["size"] > len(data):
            raise MeshIrError("E_ABI_SECTION_RANGE", "section exceeds file", section_type=stype)
        if (zlib.crc32(payload) & 0xFFFFFFFF) != entry["crc32"]:
            raise MeshIrError("E_ABI_CHECKSUM", "section crc32 mismatch", section_type=stype)
        record_bytes = entry["record_bytes"]
        fixed_name = SECTION_RECORD_BYTES.get(stype)
        if fixed_name is not None and record_bytes != fixed_name:
            raise MeshIrError(
                "E_ABI_SECTION_RANGE",
                "fixed-record section has wrong record size",
                section_type=stype,
            )
        if stype == A.SECTION_TYPE.STRINGS and record_bytes != 0:
            raise MeshIrError(
                "E_ABI_SECTION_RANGE",
                "STRINGS is a blob section and must declare record_bytes 0",
                section_type=stype,
            )
        if record_bytes == 0:
            if stype == A.SECTION_TYPE.STRINGS:
                if entry["size"] < 4:
                    raise MeshIrError(
                        "E_ABI_SECTION_RANGE",
                        "STRINGS section smaller than its count word",
                        section_type=stype,
                    )
                inner_count = struct.unpack_from(
                    "<I", payload, 0)[0]
                if inner_count != entry["count"]:
                    raise MeshIrError(
                        "E_ABI_SECTION_RANGE",
                        "STRINGS outer count != inner directory count",
                        section_type=stype,
                    )
                directory_span = 4 + entry["count"] * A.STRING_DIR_BYTES
                if directory_span > entry["size"]:
                    raise MeshIrError(
                        "E_ABI_SECTION_RANGE",
                        "string directory exceeds section",
                        section_type=stype,
                    )
                blob = payload[directory_span:]
                end = 0
                for index in range(inner_count):
                    _, length = struct.unpack_from(
                        "<II", payload, 4 + index * A.STRING_DIR_BYTES)
                    end += length
                if end != len(blob):
                    raise MeshIrError(
                        "E_ABI_SECTION_RANGE",
                        "STRINGS data does not cover the section exactly",
                        section_type=stype,
                    )
        else:
            if entry["size"] != entry["count"] * record_bytes:
                raise MeshIrError(
                    "E_ABI_SECTION_RANGE",
                    "fixed table size != count * record_bytes",
                    section_type=stype,
                )
        payloads[stype] = (entry, payload)
        last_end = entry["offset"] + entry["size"]
    if last_end != len(data):
        raise MeshIrError("E_ABI_SECTION_RANGE", "trailing bytes after last section")

    for required in A.REQUIRED_SECTIONS:
        stype = getattr(A.SECTION_TYPE, required)
        if stype not in payloads:
            raise MeshIrError("E_ABI_SECTION_RANGE", "missing required section", section=required)
    return payloads


def _grouped_values(field_specs: list, values: tuple) -> dict:
    """Group flattened struct.unpack output back per schema field."""
    grouped = {}
    cursor = 0
    for spec in field_specs:
        if spec["type"] == "u64x8":
            grouped[spec["name"]] = tuple(values[cursor : cursor + 8])
            cursor += 8
        else:
            grouped[spec["name"]] = values[cursor]
            cursor += 1
    if cursor != len(values):
        raise MeshIrError("E_ABI_CORRUPT", "record field count mismatch")
    return grouped


def _unpack_table(section_name: str, payload: bytes, count: int, cls):
    fmt = getattr(A, f"{section_name}_FORMAT")
    record_bytes = getattr(A, f"{section_name}_BYTES")
    if len(payload) != count * record_bytes:
        raise MeshIrError("E_ABI_SECTION_RANGE", f"{section_name} size mismatch")
    field_specs = getattr(A, f"{section_name}_FIELDS")
    out = []
    for index in range(count):
        values = fmt.unpack_from(payload, index * record_bytes)
        out.append(cls(**_grouped_values(field_specs, values)))
    return out


def _decode_strings(payload: bytes) -> list:
    (count,) = struct.unpack_from("<I", payload, 0)
    directory_span = 4 + count * A.STRING_DIR_BYTES
    if len(payload) < directory_span:
        raise MeshIrError("E_ABI_SECTION_RANGE", "string directory exceeds section")
    blob = payload[directory_span:]
    strings = []
    previous_end = 0
    for index in range(count):
        offset, length = struct.unpack_from("<II", payload, 4 + index * A.STRING_DIR_BYTES)
        if offset != previous_end:
            raise MeshIrError("E_ABI_ORDER", "string offsets must be dense and increasing")
        if offset + length > len(blob):
            raise MeshIrError("E_ABI_SECTION_RANGE", "string exceeds blob")
        try:
            value = blob[offset : offset + length].decode("utf-8")
        except UnicodeDecodeError as err:
            raise MeshIrError("E_ABI_CORRUPT", "string not valid utf-8") from err
        strings.append(StringEntry(value))
        previous_end = offset + length
    return strings


def _decode_descriptors(payload: bytes, count: int) -> list:
    out = []
    for index in range(count):
        base = index * A.DMA_DESCRIPTORS_BYTES
        values = A.DMA_DESCRIPTORS_FORMAT.unpack_from(payload, base)
        names = [f["name"] for f in A.DMA_DESCRIPTORS_FIELDS]
        kwargs = dict(zip(names, values))
        src = A.DMA_ENDPOINT_FORMAT.unpack(kwargs.pop("src"))
        dst = A.DMA_ENDPOINT_FORMAT.unpack(kwargs.pop("dst"))
        src_names = [f["name"] for f in A.DMA_ENDPOINT_FIELDS]
        dst_names = [f["name"] for f in A.DMA_ENDPOINT_FIELDS]
        kwargs["src"] = DmaEndpoint(**dict(zip(src_names, src)))
        kwargs["dst"] = DmaEndpoint(**dict(zip(dst_names, dst)))
        out.append(DmaDescriptor(**kwargs))
    return out


def _attr_formats() -> dict:
    formats = {}
    for name, kind in vars(A.ATTR_KIND).items():
        if name.startswith("_"):
            continue
        formats[kind] = (getattr(A, f"{name}_FORMAT"), getattr(A, f"{name}_FIELDS"))
    return formats


def _decode_attrs(payload: bytes, count: int) -> list:
    formats = _attr_formats()
    out = []
    for index in range(count):
        base = index * A.OP_ATTRS_BYTES
        values = A.OP_ATTRS_FORMAT.unpack_from(payload, base)
        kind, reserved, raw_payload = values
        if reserved != 0:
            raise MeshIrError("E_ABI_RESERVED", "attr reserved must be zero", kind=kind)
        if kind not in formats:
            raise MeshIrError("E_ABI_ENUM", "unknown attr kind", kind=kind)
        fmt, fields = formats[kind]
        payload_values = fmt.unpack(raw_payload[: fmt.size])
        payload_fields = tuple(f["name"] for f in fields)
        check_payload_rules(A.PAYLOAD_BY_KIND[kind], payload_values, fields)
        if raw_payload[fmt.size :] != bytes(28 - fmt.size):
            raise MeshIrError("E_ABI_RESERVED", "attr payload padding must be zero", kind=kind)
        out.append(
            OpAttr(
                kind=kind,
                reserved=reserved,
                payload=payload_values,
                payload_fields=payload_fields,
            )
        )
    return out


def decode_program(data: bytes) -> Program:
    header = decode_header(data)
    payloads = decode_section_dir(data, header)

    def records_of(name: str):
        entry, payload = payloads[getattr(A.SECTION_TYPE, name)]
        return entry, payload

    _, strings_payload = records_of("STRINGS")
    strings = _decode_strings(strings_payload)

    simple_sections = {
        "ENTRYPOINTS": Entrypoint,
        "PROFILES": Profile,
        "TENSORS": Tensor,
        "SHARDS": Shard,
        "ALLOCATIONS": Allocation,
        "STREAMS": Stream,
        "COMMANDS": Command,
        "COMMAND_WAITS": CommandWait,
        "COMMAND_OPERANDS": CommandOperand,
        "EVENTS": Event,
        "RELOCATIONS": Relocation,
        "EXPECTED_TRAFFIC": ExpectedTrafficRow,
    }
    decoded = {}
    for name, cls in simple_sections.items():
        entry, payload = records_of(name)
        table = _unpack_table(name, payload, entry["count"], cls)
        check_record_rules(name, table)
        decoded[name] = table

    entry, payload = records_of("DMA_DESCRIPTORS")
    descriptors = _decode_descriptors(payload, entry["count"])
    for descriptor in descriptors:
        check_record_rules("DMA_ENDPOINT", (descriptor.src, descriptor.dst))
    check_record_rules("DMA_DESCRIPTORS", descriptors)

    entry, payload = records_of("OP_ATTRS")
    attrs = _decode_attrs(payload, entry["count"])

    return Program(
        abi_major=header["abi_major"],
        abi_minor=header["abi_minor"],
        arch_digest=header["arch_digest"],
        strings=strings,
        entrypoints=decoded["ENTRYPOINTS"],
        profiles=decoded["PROFILES"],
        tensors=decoded["TENSORS"],
        shards=decoded["SHARDS"],
        allocations=decoded["ALLOCATIONS"],
        streams=decoded["STREAMS"],
        commands=decoded["COMMANDS"],
        command_waits=decoded["COMMAND_WAITS"],
        command_operands=decoded["COMMAND_OPERANDS"],
        events=decoded["EVENTS"],
        dma_descriptors=descriptors,
        op_attrs=attrs,
        relocations=decoded["RELOCATIONS"],
        expected_traffic=decoded["EXPECTED_TRAFFIC"],
    )

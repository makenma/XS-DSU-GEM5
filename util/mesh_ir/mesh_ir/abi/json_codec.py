from __future__ import annotations

from mesh_ir.abi.rules import (
    check_abi_header,
    check_min_reader_minor,
    check_payload_rules,
    check_record_rules,
)
from mesh_ir.abi.semantic import semantic_from_canonical
from mesh_ir.abi.optional import validate_optional_sections
from mesh_ir.canonical import semantic_sha256, strict_json_loads
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.model import DmaEndpoint, OpAttr, Program, RECORD_CLASSES, StringEntry


_INTEGER_MAX = {
    "u8": (1 << 8) - 1,
    "u16": (1 << 16) - 1,
    "u32": (1 << 32) - 1,
    "u64": (1 << 64) - 1,
}


def _integer(value: object, kind: str, field: str) -> int:
    maximum = _INTEGER_MAX[kind]
    if type(value) is int:
        if not 0 <= value <= min(maximum, A.JSON_SAFE_INTEGER_MAX):
            raise MeshIrError("E_ABI_BOUNDS", "JSON integer is outside its canonical range", field=field)
        return value
    if kind == "u64" and type(value) is str and value.startswith("0x") and value == value.lower():
        try:
            result = int(value, 16)
        except ValueError as error:
            raise MeshIrError("E_ABI_BOUNDS", "malformed hexadecimal integer", field=field) from error
        if result <= A.JSON_SAFE_INTEGER_MAX or result > maximum or value != hex(result):
            raise MeshIrError("E_ABI_BOUNDS", "noncanonical hexadecimal integer", field=field)
        return result
    raise MeshIrError("E_ABI_BOUNDS", "JSON integer has wrong type", field=field)


def _bytes(value: object, width: int, field: str) -> bytes:
    if type(value) is not str or len(value) != width * 2 or value != value.lower():
        raise MeshIrError("E_ABI_BOUNDS", "JSON bytes have wrong canonical width", field=field)
    try:
        decoded = bytes.fromhex(value)
    except ValueError as error:
        raise MeshIrError("E_ABI_BOUNDS", "JSON bytes are malformed", field=field) from error
    if decoded.hex() != value:
        raise MeshIrError("E_ABI_BOUNDS", "JSON bytes are not canonical hexadecimal", field=field)
    return decoded


def _record(document: object, section: str, cls):
    if type(document) is not dict:
        raise MeshIrError("E_ABI_BOUNDS", "section record must be an object", section=section)
    fields = getattr(A, f"{section}_FIELDS")
    expected = {field.get("python_field", field["name"]) for field in fields}
    if set(document) != expected:
        raise MeshIrError("E_ABI_BOUNDS", "section record fields do not match schema", section=section)
    values = {}
    for field in fields:
        name = field.get("python_field", field["name"])
        value = document[name]
        kind = field["type"]
        if field.get("string_pool") == "SEMANTIC_STRINGS":
            if type(value) is not str:
                raise MeshIrError("E_ABI_BOUNDS", "JSON string field has wrong type", field=name)
        elif kind in _INTEGER_MAX:
            value = _integer(value, kind, name)
        elif kind == "u64x8":
            if type(value) is not list or len(value) != 8:
                raise MeshIrError("E_ABI_BOUNDS", "fixed vector has wrong length", field=name)
            value = tuple(_integer(item, "u64", name) for item in value)
        elif kind.startswith("bytes"):
            value = _bytes(value, int(kind[5:]), name)
        elif kind == "record_ref":
            nested = DmaEndpoint if field["ref"] == "DMA_ENDPOINT" else None
            if nested is None:
                raise MeshIrError("E_ABI_CORRUPT", "unknown generated nested record", field=name)
            value = _record(value, field["ref"], nested)
        else:
            raise MeshIrError("E_ABI_CORRUPT", "unknown generated transport field type", field=name)
        values[name] = value
    try:
        result = cls(**values)
    except (TypeError, ValueError) as error:
        raise MeshIrError("E_ABI_BOUNDS", "section record construction failed", section=section) from error
    check_record_rules(section, (result,))
    return result


def _attrs(documents: object) -> tuple[OpAttr, ...]:
    if type(documents) is not list:
        raise MeshIrError("E_ABI_BOUNDS", "OP_ATTRS must be an array")
    rows = []
    for document in documents:
        if type(document) is not dict or type(document.get("kind")) is not int:
            raise MeshIrError("E_ABI_BOUNDS", "OP_ATTRS record is malformed")
        kind = document["kind"]
        payload_name = A.PAYLOAD_BY_KIND.get(kind)
        if payload_name is None:
            raise MeshIrError("E_ABI_ENUM", "OP_ATTRS kind is unknown", kind=kind)
        fields = getattr(A, f"{payload_name}_FIELDS")
        expected = {"kind", *(field["name"] for field in fields)}
        if set(document) != expected:
            raise MeshIrError("E_ABI_BOUNDS", "OP_ATTRS payload fields do not match kind", kind=kind)
        values = []
        for field in fields:
            value = document[field["name"]]
            field_type = field["type"]
            if field_type in _INTEGER_MAX:
                value = _integer(value, field_type, field["name"])
            elif field_type.startswith("bytes"):
                value = _bytes(value, int(field_type[5:]), field["name"])
            else:
                raise MeshIrError("E_ABI_CORRUPT", "unknown generated attr field type", field=field["name"])
            values.append(value)
        check_payload_rules(payload_name, tuple(values), fields)
        rows.append(OpAttr(kind, 0, tuple(values), tuple(field["name"] for field in fields)))
    return tuple(rows)


def load_program_json(payload: str | bytes) -> Program:
    document = strict_json_loads(payload)
    root_fields = {field["name"] for field in A.PROGRAM_CANONICAL_FIELDS}
    if type(document) is not dict or set(document) != root_fields:
        raise MeshIrError("E_ABI_BOUNDS", "Program JSON root fields do not match schema")
    abi = document["abi"]
    if type(abi) is not dict or set(abi) != set(A.CANONICAL_ABI_FIELDS):
        raise MeshIrError("E_ABI_BOUNDS", "Program JSON ABI fields do not match schema")
    major = _integer(abi["major"], "u16", "major")
    minor = _integer(abi["minor"], "u16", "minor")
    min_reader_minor = _integer(abi["min_reader_minor"], "u16", "min_reader_minor")
    required_features = _integer(abi["required_features"], "u64", "required_features")
    check_abi_header(major, minor, required_features)
    check_min_reader_minor(min_reader_minor, minor, required_features)
    sections = document["sections"]
    if type(sections) is not dict:
        raise MeshIrError("E_ABI_BOUNDS", "Program JSON sections must be an object")
    bindings = A.TRANSPORT_CANONICAL_SECTIONS
    required = {name for name, binding in bindings.items() if not binding["optional"]}
    if not required <= set(sections) or set(sections) - set(bindings):
        raise MeshIrError("E_ABI_BOUNDS", "Program JSON section set is invalid")
    decoded = {}
    for name, binding in bindings.items():
        if name not in sections:
            if binding["optional"]:
                decoded[binding["program_field"]] = ()
                continue
            raise MeshIrError("E_ABI_BOUNDS", "required Program JSON section is absent", section=name)
        rows = sections[name]
        if rows is None:
            raise MeshIrError("E_ABI_BOUNDS", "Program JSON section cannot be null", section=name)
        if type(rows) is not list:
            raise MeshIrError("E_ABI_BOUNDS", "Program JSON section must be an array", section=name)
        if binding["optional"] and not rows:
            raise MeshIrError("E_ABI_BOUNDS", "empty optional Program JSON section must be omitted", section=name)
        if name == "STRINGS":
            if any(type(value) is not str for value in rows):
                raise MeshIrError("E_ABI_BOUNDS", "STRINGS contains a non-string")
            try:
                for value in rows:
                    value.encode("utf-8")
            except UnicodeEncodeError as error:
                raise MeshIrError("E_ABI_BOUNDS", "STRINGS contains invalid Unicode") from error
            records = tuple(StringEntry(value) for value in rows)
        elif name == "OP_ATTRS":
            records = _attrs(rows)
        else:
            cls = RECORD_CLASSES[name]
            records = tuple(_record(value, name, cls) for value in rows)
        decoded[binding["program_field"]] = records
    semantics = semantic_from_canonical(document["semantics"])
    digest = document["semantic_sha256"]
    if type(digest) is not str or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise MeshIrError("E_ABI_CHECKSUM", "Program JSON semantic checksum is malformed")
    program = Program(
        abi_major=major,
        abi_minor=minor,
        arch_digest=_bytes(document["arch_digest"], 32, "arch_digest"),
        semantics=semantics,
        semantic_sha256=digest,
        min_reader_minor=min_reader_minor,
        required_features=required_features,
        **decoded,
    )
    if semantic_sha256(program.semantic_dict()) != digest:
        raise MeshIrError("E_ABI_CHECKSUM", "Program JSON semantic checksum is stale")
    validate_optional_sections(program)
    return program


__all__ = ["load_program_json"]

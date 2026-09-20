from __future__ import annotations

import dataclasses
import importlib
import math
import struct
from enum import Enum

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A


U32_MAX = (1 << 32) - 1
U64_MAX = (1 << 64) - 1
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1


def _load_type(path: str) -> type:
    module_name, class_name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), class_name)


SEMANTIC_CLASSES = {path: _load_type(path) for path in A.SEMANTIC_RECORDS}
CLASS_PATHS = {cls: path for path, cls in SEMANTIC_CLASSES.items()}
ENUM_CLASSES = {path: _load_type(path) for path in A.SEMANTIC_ENUMS}


def _enum_members(path: str) -> tuple[tuple[int, object], ...]:
    definition = A.SEMANTIC_ENUMS[path]
    if "members" in definition:
        return tuple((item["wire"], item["python"]) for item in definition["members"])
    source = getattr(A, definition["values_from"].upper())
    return tuple(
        (value, value)
        for name, value in vars(source).items()
        if not name.startswith("_")
    )


ENUM_WIRE_BY_VALUE = {
    path: {python_value: wire for wire, python_value in _enum_members(path)}
    for path in A.SEMANTIC_ENUMS
}
ENUM_VALUE_BY_WIRE = {
    path: {wire: python_value for wire, python_value in _enum_members(path)}
    for path in A.SEMANTIC_ENUMS
}


def _class_path(value: object) -> str:
    try:
        return CLASS_PATHS[type(value)]
    except KeyError as error:
        raise MeshIrError(
            "E_ABI_BOUNDS",
            "semantic value has no declared record type",
            type=f"{type(value).__module__}.{type(value).__qualname__}",
        ) from error


def _checked_integer(value: object, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MeshIrError("E_ABI_BOUNDS", "semantic integer is out of range", field=field)
    return value


def _checked_tuple(value: object, field: str) -> tuple:
    if type(value) is not tuple:
        raise MeshIrError("E_ABI_BOUNDS", "semantic list field must be a tuple", field=field)
    return value


class SemanticImage:
    def __init__(self) -> None:
        self.records = {record["section_type"]: [] for record in A.SEMANTIC_RECORDS.values()}
        self.strings: list[str] = []
        self._string_ids: dict[str, int] = {}
        self._strings_final = False
        self.u64_values: list[int] = []
        self.i64_values: list[int] = []
        self.references: list[tuple[int, int]] = []
        self.byte_values = bytearray()
        self.integer_values: list[tuple[int, int]] = []

    def intern_string(self, value: object, field: str) -> int:
        if type(value) is not str:
            raise MeshIrError("E_ABI_BOUNDS", "semantic string field has wrong type", field=field)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise MeshIrError("E_ABI_BOUNDS", "semantic string is not valid Unicode", field=field) from error
        if value in self._string_ids:
            return self._string_ids[value]
        if self._strings_final:
            raise MeshIrError("E_ABI_CORRUPT", "semantic string pool changed after finalization")
        self.strings.append(value)
        string_id = len(self.strings)
        self._string_ids[value] = string_id
        return string_id

    def finalize_strings(self) -> None:
        if self._strings_final:
            return
        original = tuple(self.strings)
        self.strings = sorted(self.strings, key=lambda item: item.encode("utf-8"))
        self._string_ids = {value: index for index, value in enumerate(self.strings, 1)}
        remap = {index: self._string_ids[value] for index, value in enumerate(original, 1)}
        for path, spec in A.SEMANTIC_RECORDS.items():
            string_fields = tuple(field for field in spec["fields"] if field["kind"] == "string")
            for row in self.records[spec["section_type"]]:
                for field in string_fields:
                    old, reserved = A.STRING_REF_FORMAT.unpack_from(row, field["offset"])
                    if old:
                        A.STRING_REF_FORMAT.pack_into(row, field["offset"], remap[old], reserved)
        self._strings_final = True

    def append_span(self, target: list, values: tuple, field: str) -> tuple[int, int]:
        if not values:
            return 0, 0
        begin = len(target)
        if begin > U32_MAX or len(values) > U32_MAX - begin:
            raise MeshIrError("E_ABI_OVERFLOW", "semantic list exceeds wire range", field=field)
        target.extend(values)
        return begin, len(values)


@dataclasses.dataclass
class _EncodeFrame:
    value: object
    path: str
    row: bytearray
    field_index: int = 0
    pending_field: dict | None = None
    pending_values: tuple = ()
    pending_index: int = 0
    pending_begin: int = 0


@dataclasses.dataclass
class _DecodeFrame:
    reference: tuple[int, int]
    path: str
    raw: bytes
    kwargs: dict = dataclasses.field(default_factory=dict)
    field_index: int = 0
    pending_field: dict | None = None
    pending_refs: tuple[tuple[int, int], ...] = ()
    pending_index: int = 0
    pending_values: list[object] = dataclasses.field(default_factory=list)
    destination: tuple["_DecodeFrame", str, bool] | None = None


@dataclasses.dataclass
class _JsonDecodeFrame:
    document: object
    path: str
    discriminator: bool
    destination: tuple["_JsonDecodeFrame", str, bool] | None = None
    initialized: bool = False
    kwargs: dict = dataclasses.field(default_factory=dict)
    field_index: int = 0
    pending_field: dict | None = None
    pending_documents: tuple = ()
    pending_index: int = 0
    pending_values: list[object] = dataclasses.field(default_factory=list)


def encode_semantics(root: object) -> tuple[tuple[int, int], SemanticImage]:
    image = SemanticImage()
    active: set[int] = set()

    def reserve(value: object) -> tuple[tuple[int, int], _EncodeFrame]:
        path = _class_path(value)
        if id(value) in active:
            raise MeshIrError("E_ABI_BOUNDS", "semantic records must form an owned tree")
        active.add(id(value))
        spec = A.SEMANTIC_RECORDS[path]
        rows = image.records[spec["section_type"]]
        if len(rows) >= U32_MAX:
            raise MeshIrError("E_ABI_OVERFLOW", "semantic table exceeds wire range")
        row = bytearray(spec["record_bytes"])
        rows.append(row)
        return (spec["section_type"], len(rows)), _EncodeFrame(value, path, row)

    root_ref, root_frame = reserve(root)
    stack = [root_frame]
    while stack:
        frame = stack[-1]
        spec = A.SEMANTIC_RECORDS[frame.path]
        fields = spec["fields"]
        if frame.pending_field is not None:
            if frame.pending_index < len(frame.pending_values):
                child = frame.pending_values[frame.pending_index]
                targets = frame.pending_field["targets"]
                child_path = _class_path(child)
                if child_path not in targets:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic reference has wrong concrete type", field=frame.pending_field["name"])
                child_ref, child_frame = reserve(child)
                image.references[frame.pending_begin + frame.pending_index] = child_ref
                frame.pending_index += 1
                stack.append(child_frame)
                continue
            frame.pending_field = None
            frame.pending_values = ()
            frame.pending_index = 0
            frame.pending_begin = 0
            frame.field_index += 1
            continue
        if frame.field_index == len(fields):
            active.remove(id(frame.value))
            stack.pop()
            continue
        field = fields[frame.field_index]
        value = getattr(frame.value, field["name"])
        bit = field.get("optional_bit")
        if value is None:
            if bit is None:
                raise MeshIrError("E_ABI_BOUNDS", "required semantic field is absent", field=field["name"])
            frame.field_index += 1
            continue
        if bit is not None:
            mask = struct.unpack_from("<Q", frame.row, 0)[0] | (1 << bit)
            struct.pack_into("<Q", frame.row, 0, mask)
        kind = field["kind"]
        offset = field["offset"]
        name = field["name"]
        if kind == "ref":
            child_path = _class_path(value)
            if child_path not in field["targets"]:
                raise MeshIrError("E_ABI_BOUNDS", "semantic reference has wrong concrete type", field=name)
            child_ref, child_frame = reserve(value)
            A.SEMANTIC_REF_FORMAT.pack_into(frame.row, offset, child_ref[0], 0, child_ref[1])
            frame.field_index += 1
            stack.append(child_frame)
            continue
        if kind == "ref_list":
            frame.pending_field = field
            frame.pending_values = _checked_tuple(value, name)
            begin, count = image.append_span(
                image.references,
                tuple((0, 0) for _ in frame.pending_values),
                name,
            )
            frame.pending_begin = begin
            A.LIST_SPAN_FORMAT.pack_into(frame.row, offset, begin, count)
            continue
        if kind == "u64":
            struct.pack_into("<Q", frame.row, offset, _checked_integer(value, 0, U64_MAX, name))
        elif kind == "i64":
            struct.pack_into("<q", frame.row, offset, _checked_integer(value, I64_MIN, I64_MAX, name))
        elif kind == "bool":
            if type(value) is not bool:
                raise MeshIrError("E_ABI_BOUNDS", "semantic bool field has wrong type", field=name)
            struct.pack_into("<Q", frame.row, offset, int(value))
        elif kind == "f64":
            if type(value) is not float or not math.isfinite(value):
                raise MeshIrError("E_ABI_BOUNDS", "semantic float field has wrong type", field=name)
            struct.pack_into("<d", frame.row, offset, value)
        elif kind == "string":
            A.STRING_REF_FORMAT.pack_into(frame.row, offset, image.intern_string(value, name), 0)
        elif kind == "enum":
            enum_cls = ENUM_CLASSES[field["enum"]]
            if type(value) is not enum_cls:
                raise MeshIrError("E_ABI_ENUM", "semantic enum has wrong type", field=name)
            try:
                wire = ENUM_WIRE_BY_VALUE[field["enum"]][value.value]
            except KeyError as error:
                raise MeshIrError("E_ABI_ENUM", "semantic enum value is not declared", field=name) from error
            A.ENUM_VALUE_FORMAT.pack_into(frame.row, offset, wire, 0)
        elif kind in {"u64_list", "i64_list", "integer_list"}:
            values = _checked_tuple(value, name)
            if kind == "u64_list":
                encoded = tuple(_checked_integer(item, 0, U64_MAX, name) for item in values)
                begin, count = image.append_span(image.u64_values, encoded, name)
            elif kind == "i64_list":
                encoded = tuple(_checked_integer(item, I64_MIN, I64_MAX, name) for item in values)
                begin, count = image.append_span(image.i64_values, encoded, name)
            else:
                encoded = []
                for item in values:
                    item = _checked_integer(item, I64_MIN, U64_MAX, name)
                    encoded.append(
                        (A.SCALAR_KIND.I64, item & U64_MAX)
                        if item < 0
                        else (A.SCALAR_KIND.U64, item)
                    )
                begin, count = image.append_span(image.integer_values, tuple(encoded), name)
            A.LIST_SPAN_FORMAT.pack_into(frame.row, offset, begin, count)
        elif kind == "bytes":
            if type(value) is not bytes:
                raise MeshIrError("E_ABI_BOUNDS", "semantic byte field has wrong type", field=name)
            begin, count = image.append_span(image.byte_values, tuple(value), name)
            A.LIST_SPAN_FORMAT.pack_into(frame.row, offset, begin, count)
        elif kind == "scalar":
            if type(value) is bool:
                scalar_kind, payload = A.SCALAR_KIND.BOOL, int(value)
            elif type(value) is int:
                value = _checked_integer(value, I64_MIN, U64_MAX, name)
                scalar_kind, payload = ((A.SCALAR_KIND.I64, value & U64_MAX) if value < 0 else (A.SCALAR_KIND.U64, value))
            elif type(value) is float and math.isfinite(value):
                scalar_kind, payload = A.SCALAR_KIND.F64, struct.unpack("<Q", struct.pack("<d", value))[0]
            else:
                raise MeshIrError("E_ABI_BOUNDS", "semantic scalar has wrong type", field=name)
            A.SCALAR_VALUE_FORMAT.pack_into(frame.row, offset, scalar_kind, bytes(6), payload)
        else:
            raise MeshIrError("E_ABI_CORRUPT", "unknown generated semantic field kind", kind=kind)
        frame.field_index += 1
    return root_ref, image


def encode_string_table(strings: list[str], section_name: str) -> bytes:
    encoded = []
    for value in strings:
        try:
            encoded.append(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise MeshIrError("E_ABI_BOUNDS", "wire string is not valid Unicode") from error
    header_format = getattr(A, f"{section_name}_BLOB_HEADER_FORMAT")
    directory_format = getattr(A, f"{section_name}_DIRECTORY_FORMAT")
    directory = bytearray(header_format.pack(len(encoded)))
    cursor = 0
    for value in encoded:
        directory.extend(directory_format.pack(cursor, len(value)))
        cursor += len(value)
    return bytes(directory) + b"".join(encoded)


def semantic_payloads(image: SemanticImage) -> dict[str, tuple[int, bytes]]:
    image.finalize_strings()
    payloads: dict[str, tuple[int, bytes]] = {
        "SEMANTIC_STRINGS": (
            len(image.strings),
            encode_string_table(image.strings, "SEMANTIC_STRINGS"),
        ),
        "SEMANTIC_U64_VALUES": (
            len(image.u64_values),
            b"".join(A.SEMANTIC_U64_VALUES_FORMAT.pack(value) for value in image.u64_values),
        ),
        "SEMANTIC_I64_VALUES": (
            len(image.i64_values),
            b"".join(A.SEMANTIC_I64_VALUES_FORMAT.pack(value) for value in image.i64_values),
        ),
        "SEMANTIC_REFERENCES": (
            len(image.references),
            b"".join(
                A.SEMANTIC_REF_FORMAT.pack(section, 0, row)
                for section, row in image.references
            ),
        ),
        "SEMANTIC_BYTES": (len(image.byte_values), bytes(image.byte_values)),
        "SEMANTIC_INTEGER_VALUES": (
            len(image.integer_values),
            b"".join(
                A.SEMANTIC_INTEGER_VALUES_FORMAT.pack(kind, bytes(6), value)
                for kind, value in image.integer_values
            ),
        ),
    }
    for path, spec in A.SEMANTIC_RECORDS.items():
        rows = image.records[spec["section_type"]]
        payloads[spec["section_name"]] = (len(rows), b"".join(rows))
    return payloads


def _canonical_integer(value: int):
    return hex(value) if value > A.JSON_SAFE_INTEGER_MAX else value


def semantic_to_canonical(value: object) -> dict:
    result = {}
    tasks = [(value, _class_path(value), result, frozenset())]
    while tasks:
        record, path, output, ancestors = tasks.pop()
        if id(record) in ancestors:
            raise MeshIrError("E_ABI_BOUNDS", "semantic records must form an owned tree")
        child_ancestors = ancestors | {id(record)}
        for field in A.SEMANTIC_RECORDS[path]["fields"]:
            item = getattr(record, field["name"])
            if item is None:
                if field.get("optional_bit") is None:
                    raise MeshIrError("E_ABI_BOUNDS", "required semantic field is absent", field=field["name"])
                continue
            kind = field["kind"]
            if kind == "ref":
                child_path = _class_path(item)
                if child_path not in field["targets"]:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic reference has wrong concrete type", field=field["name"])
                child = {}
                if field["json_union_discriminator"]:
                    child["$type"] = A.SEMANTIC_RECORDS[child_path]["json_tag"]
                output[field["name"]] = child
                tasks.append((item, child_path, child, child_ancestors))
                continue
            if kind == "ref_list":
                children = _checked_tuple(item, field["name"])
                rows = []
                child_tasks = []
                for child_value in children:
                    child_path = _class_path(child_value)
                    if child_path not in field["targets"]:
                        raise MeshIrError("E_ABI_BOUNDS", "semantic reference has wrong concrete type", field=field["name"])
                    child = {}
                    if field["json_union_discriminator"]:
                        child["$type"] = A.SEMANTIC_RECORDS[child_path]["json_tag"]
                    rows.append(child)
                    child_tasks.append((child_value, child_path, child, child_ancestors))
                output[field["name"]] = rows
                tasks.extend(reversed(child_tasks))
                continue
            if kind == "enum":
                if type(item) is not ENUM_CLASSES[field["enum"]]:
                    raise MeshIrError("E_ABI_ENUM", "semantic enum has wrong type", field=field["name"])
                item = item.value
            elif kind == "bytes":
                if type(item) is not bytes:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic byte field has wrong type", field=field["name"])
                item = item.hex()
            elif kind == "u64":
                item = _canonical_integer(_checked_integer(item, 0, U64_MAX, field["name"]))
            elif kind == "i64":
                item = _checked_integer(item, I64_MIN, I64_MAX, field["name"])
            elif kind in {"u64_list", "integer_list"}:
                minimum = 0 if kind == "u64_list" else I64_MIN
                item = [_canonical_integer(_checked_integer(element, minimum, U64_MAX, field["name"])) for element in _checked_tuple(item, field["name"])]
            elif kind == "i64_list":
                item = [_checked_integer(element, I64_MIN, I64_MAX, field["name"]) for element in _checked_tuple(item, field["name"])]
            elif kind == "bool":
                if type(item) is not bool:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic bool field has wrong type", field=field["name"])
            elif kind == "f64":
                if type(item) is not float or not math.isfinite(item):
                    raise MeshIrError("E_ABI_BOUNDS", "semantic float field has wrong type", field=field["name"])
            elif kind == "string":
                if type(item) is not str:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic string field has wrong type", field=field["name"])
            elif kind == "scalar":
                if type(item) is int:
                    item = _canonical_integer(_checked_integer(item, I64_MIN, U64_MAX, field["name"]))
                elif type(item) is float:
                    if not math.isfinite(item):
                        raise MeshIrError("E_ABI_BOUNDS", "semantic scalar is not finite", field=field["name"])
                elif type(item) is not bool:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic scalar has wrong type", field=field["name"])
            output[field["name"]] = item
    return result


def _json_integer(value: object, minimum: int, maximum: int, field: str) -> int:
    if type(value) is int:
        if value > A.JSON_SAFE_INTEGER_MAX:
            raise MeshIrError("E_ABI_BOUNDS", "large JSON integer must use canonical hexadecimal", field=field)
        result = value
    elif type(value) is str and value.startswith("0x") and value == value.lower():
        try:
            result = int(value, 16)
        except ValueError as error:
            raise MeshIrError("E_ABI_BOUNDS", "malformed canonical hexadecimal integer", field=field) from error
        if result <= A.JSON_SAFE_INTEGER_MAX or value != hex(result):
            raise MeshIrError("E_ABI_BOUNDS", "noncanonical hexadecimal integer", field=field)
    else:
        raise MeshIrError("E_ABI_BOUNDS", "semantic JSON integer has wrong type", field=field)
    return _checked_integer(result, minimum, maximum, field)


def semantic_from_canonical(document: object, root_path: str = "mesh_ir.scheduled.model.ProgramSemantics") -> object:
    def _json_target(value: object, field: dict) -> str:
        targets = tuple(field["targets"])
        if field["json_union_discriminator"]:
            if type(value) is not dict or type(value.get("$type")) is not str:
                raise MeshIrError("E_ABI_ENUM", "semantic JSON union lacks a type tag", field=field["name"])
            matches = [path for path in targets if A.SEMANTIC_RECORDS[path]["json_tag"] == value["$type"]]
            if len(matches) != 1:
                raise MeshIrError("E_ABI_ENUM", "semantic JSON union tag is unknown", field=field["name"])
            return matches[0]
        if len(targets) != 1:
            raise MeshIrError("E_ABI_CORRUPT", "generated semantic field lacks union discriminator", field=field["name"])
        return targets[0]

    result = None
    stack = [_JsonDecodeFrame(document, root_path, False)]
    while stack:
        frame = stack[-1]
        spec = A.SEMANTIC_RECORDS[frame.path]
        fields = spec["fields"]
        if not frame.initialized:
            if type(frame.document) is not dict:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON record must be an object", type=frame.path)
            expected = {field["name"] for field in fields}
            if frame.discriminator:
                expected.add("$type")
                if frame.document.get("$type") != spec["json_tag"]:
                    raise MeshIrError("E_ABI_ENUM", "semantic JSON union tag is invalid", type=frame.path)
            if set(frame.document) - expected:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON record has unknown fields", type=frame.path)
            frame.initialized = True
        if frame.pending_field is not None:
            if frame.pending_index < len(frame.pending_documents):
                item = frame.pending_documents[frame.pending_index]
                frame.pending_index += 1
                target = _json_target(item, frame.pending_field)
                stack.append(_JsonDecodeFrame(
                    item,
                    target,
                    frame.pending_field["json_union_discriminator"],
                    (frame, frame.pending_field["name"], True),
                ))
                continue
            frame.kwargs[frame.pending_field["name"]] = tuple(frame.pending_values)
            frame.pending_field = None
            frame.pending_documents = ()
            frame.pending_index = 0
            frame.pending_values = []
            frame.field_index += 1
            continue
        if frame.field_index == len(fields):
            try:
                value = SEMANTIC_CLASSES[frame.path](**frame.kwargs)
            except (TypeError, ValueError) as error:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON record construction failed", type=frame.path) from error
            stack.pop()
            if frame.destination is None:
                result = value
            else:
                parent, field_name, is_list = frame.destination
                if is_list:
                    parent.pending_values.append(value)
                else:
                    parent.kwargs[field_name] = value
            continue
        field = fields[frame.field_index]
        name = field["name"]
        if name not in frame.document:
            if field.get("optional_bit") is not None:
                frame.kwargs[name] = None
                frame.field_index += 1
                continue
            raise MeshIrError("E_ABI_BOUNDS", "semantic JSON record is missing a field", type=frame.path, field=name)
        item = frame.document[name]
        if item is None:
            raise MeshIrError("E_ABI_BOUNDS", "semantic JSON must omit absent optional fields", field=name)
        kind = field["kind"]
        if kind == "ref":
            target = _json_target(item, field)
            frame.field_index += 1
            stack.append(_JsonDecodeFrame(
                item,
                target,
                field["json_union_discriminator"],
                (frame, name, False),
            ))
            continue
        if kind == "ref_list":
            if type(item) is not list:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON list has wrong type", field=name)
            frame.pending_field = field
            frame.pending_documents = tuple(item)
            continue
        if kind == "enum":
            enum_cls = ENUM_CLASSES[field["enum"]]
            if not any(type(item) is type(candidate) and item == candidate for candidate in ENUM_WIRE_BY_VALUE[field["enum"]]):
                raise MeshIrError("E_ABI_ENUM", "semantic JSON enum value is invalid", field=name)
            item = enum_cls(item)
        elif kind == "bytes":
            if type(item) is not str or len(item) % 2 or item != item.lower():
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON bytes are not canonical hexadecimal", field=name)
            try:
                item = bytes.fromhex(item)
            except ValueError as error:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON bytes are malformed", field=name) from error
            if item.hex() != frame.document[name]:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON bytes are not canonical hexadecimal", field=name)
        elif kind == "u64":
            item = _json_integer(item, 0, U64_MAX, name)
        elif kind == "i64":
            item = _json_integer(item, I64_MIN, I64_MAX, name)
        elif kind in {"u64_list", "i64_list", "integer_list"}:
            if type(item) is not list:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON list has wrong type", field=name)
            minimum = 0 if kind == "u64_list" else I64_MIN
            maximum = I64_MAX if kind == "i64_list" else U64_MAX
            item = tuple(_json_integer(element, minimum, maximum, name) for element in item)
        elif kind == "bool":
            if type(item) is not bool:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON bool has wrong type", field=name)
        elif kind == "f64":
            if type(item) is not float or not math.isfinite(item):
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON float has wrong type", field=name)
        elif kind == "string":
            if type(item) is not str:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON string has wrong type", field=name)
        elif kind == "scalar":
            if type(item) is int:
                item = _json_integer(item, I64_MIN, U64_MAX, name)
            elif type(item) is str:
                item = _json_integer(item, 0, U64_MAX, name)
            elif type(item) is float:
                if not math.isfinite(item):
                    raise MeshIrError("E_ABI_BOUNDS", "semantic JSON scalar is not finite", field=name)
            elif type(item) is not bool:
                raise MeshIrError("E_ABI_BOUNDS", "semantic JSON scalar has wrong type", field=name)
        frame.kwargs[name] = item
        frame.field_index += 1
    return result


class SemanticDecoder:
    def __init__(self, payloads: dict[int, tuple[dict, bytes]]) -> None:
        self.payloads = payloads
        self.strings = self._strings()
        self.used_strings: set[int] = set()
        self.used_records: set[tuple[int, int]] = set()
        self.next_record = {section_type: 1 for section_type in A.SEMANTIC_RECORD_BY_SECTION}
        self.next_primitive = {
            A.SECTION_TYPE.SEMANTIC_U64_VALUES: 0,
            A.SECTION_TYPE.SEMANTIC_I64_VALUES: 0,
            A.SECTION_TYPE.SEMANTIC_REFERENCES: 0,
            A.SECTION_TYPE.SEMANTIC_BYTES: 0,
            A.SECTION_TYPE.SEMANTIC_INTEGER_VALUES: 0,
        }

    def _section(self, section_type: int) -> tuple[dict, bytes]:
        try:
            return self.payloads[section_type]
        except KeyError as error:
            raise MeshIrError("E_ABI_SECTION_RANGE", "semantic section is missing", section_type=section_type) from error

    def _strings(self) -> tuple[str, ...]:
        entry, payload = self._section(A.SECTION_TYPE.SEMANTIC_STRINGS)
        header_bytes = A.SEMANTIC_STRINGS_BLOB_HEADER_BYTES
        directory_bytes = A.SEMANTIC_STRINGS_DIRECTORY_RECORD_BYTES
        if len(payload) < header_bytes:
            raise MeshIrError("E_ABI_SECTION_RANGE", "semantic string table is truncated")
        count = A.SEMANTIC_STRINGS_BLOB_HEADER_FORMAT.unpack_from(payload)[0]
        if count != entry["count"] or header_bytes + count * directory_bytes > len(payload):
            raise MeshIrError("E_ABI_SECTION_RANGE", "semantic string directory is inconsistent")
        blob = payload[header_bytes + count * directory_bytes:]
        values = []
        end = 0
        for index in range(count):
            offset, length = A.SEMANTIC_STRINGS_DIRECTORY_FORMAT.unpack_from(
                payload, header_bytes + index * directory_bytes
            )
            if offset != end or offset + length > len(blob):
                raise MeshIrError("E_ABI_ORDER", "semantic string offsets must be dense")
            try:
                values.append(blob[offset:offset + length].decode("utf-8"))
            except UnicodeDecodeError as error:
                raise MeshIrError("E_ABI_CORRUPT", "semantic string is not valid UTF-8") from error
            end += length
        if end != len(blob):
            raise MeshIrError("E_ABI_SECTION_RANGE", "semantic strings do not cover their blob")
        if values != sorted(set(values), key=lambda item: item.encode("utf-8")):
            raise MeshIrError("E_ABI_ORDER", "semantic strings must be sorted and unique")
        return tuple(values)

    def _span(self, section_type: int, begin: int, count: int) -> range:
        entry, _ = self._section(section_type)
        if count == 0 and begin != 0:
            raise MeshIrError("E_ABI_ORDER", "empty semantic span must be canonical zero")
        if begin > entry["count"] or count > entry["count"] - begin:
            raise MeshIrError("E_ABI_BOUNDS", "semantic span is out of bounds", section_type=section_type)
        if count and begin < self.next_primitive[section_type]:
            raise MeshIrError("E_ABI_DUPLICATE", "semantic spans overlap an earlier owner", section_type=section_type)
        if count and begin > self.next_primitive[section_type]:
            raise MeshIrError("E_ABI_ORDER", "semantic spans are not in canonical occurrence order", section_type=section_type)
        self.next_primitive[section_type] += count
        return range(begin, begin + count)

    def _reference(self, raw: bytes, offset: int, targets: tuple[str, ...]) -> tuple[int, int]:
        section_type, reserved, row = A.SEMANTIC_REF_FORMAT.unpack_from(raw, offset)
        if reserved:
            raise MeshIrError("E_ABI_RESERVED", "semantic reference reserved field is nonzero")
        path = A.SEMANTIC_RECORD_BY_SECTION.get(section_type)
        if path is None or path not in targets:
            raise MeshIrError("E_ABI_BOUNDS", "semantic reference target is invalid", section_type=section_type)
        entry, _ = self._section(section_type)
        if not 1 <= row <= entry["count"]:
            raise MeshIrError("E_ABI_BOUNDS", "semantic reference row is out of bounds", section_type=section_type, row=row)
        reference = (section_type, row)
        if reference in self.used_records:
            raise MeshIrError("E_ABI_DUPLICATE", "semantic record has multiple owners", section_type=section_type, row=row)
        if row != self.next_record[section_type]:
            raise MeshIrError("E_ABI_ORDER", "semantic records are not in canonical occurrence order", section_type=section_type, row=row)
        self.next_record[section_type] += 1
        self.used_records.add(reference)
        return reference

    def _record_frame(self, reference: tuple[int, int], destination=None) -> _DecodeFrame:
        section_type, row = reference
        path = A.SEMANTIC_RECORD_BY_SECTION[section_type]
        spec = A.SEMANTIC_RECORDS[path]
        _, payload = self._section(section_type)
        begin = (row - 1) * spec["record_bytes"]
        raw = payload[begin:begin + spec["record_bytes"]]
        mask = struct.unpack_from("<Q", raw)[0]
        if mask & ~spec["optional_mask"]:
            raise MeshIrError("E_ABI_RESERVED", "semantic optional mask has unknown bits", type=path)
        return _DecodeFrame(reference, path, raw, destination=destination)

    def _primitive(self, field: dict, raw: bytes):
        kind = field["kind"]
        offset = field["offset"]
        name = field["name"]
        if kind == "u64":
            return struct.unpack_from("<Q", raw, offset)[0]
        if kind == "i64":
            return struct.unpack_from("<q", raw, offset)[0]
        if kind == "bool":
            value = struct.unpack_from("<Q", raw, offset)[0]
            if value > 1:
                raise MeshIrError("E_ABI_ENUM", "semantic bool is not zero or one", field=name)
            return bool(value)
        if kind == "f64":
            value = struct.unpack_from("<d", raw, offset)[0]
            if not math.isfinite(value):
                raise MeshIrError("E_ABI_BOUNDS", "semantic float is not finite", field=name)
            return value
        if kind == "string":
            string_id, reserved = A.STRING_REF_FORMAT.unpack_from(raw, offset)
            if reserved:
                raise MeshIrError("E_ABI_RESERVED", "semantic string reference reserved field is nonzero")
            if not 1 <= string_id <= len(self.strings):
                raise MeshIrError("E_ABI_BOUNDS", "semantic string reference is out of bounds")
            self.used_strings.add(string_id - 1)
            return self.strings[string_id - 1]
        if kind == "enum":
            wire, reserved = A.ENUM_VALUE_FORMAT.unpack_from(raw, offset)
            if reserved:
                raise MeshIrError("E_ABI_RESERVED", "semantic enum reserved field is nonzero")
            try:
                python_value = ENUM_VALUE_BY_WIRE[field["enum"]][wire]
                return ENUM_CLASSES[field["enum"]](python_value)
            except (KeyError, ValueError) as error:
                raise MeshIrError("E_ABI_ENUM", "semantic enum value is unknown", field=name, value=wire) from error
        if kind in {"u64_list", "i64_list", "integer_list", "bytes"}:
            begin, count = A.LIST_SPAN_FORMAT.unpack_from(raw, offset)
            if kind == "u64_list":
                section_type = A.SECTION_TYPE.SEMANTIC_U64_VALUES
                fmt = A.SEMANTIC_U64_VALUES_FORMAT
                width = A.SEMANTIC_U64_VALUES_BYTES
            elif kind == "i64_list":
                section_type = A.SECTION_TYPE.SEMANTIC_I64_VALUES
                fmt = A.SEMANTIC_I64_VALUES_FORMAT
                width = A.SEMANTIC_I64_VALUES_BYTES
            elif kind == "integer_list":
                section_type = A.SECTION_TYPE.SEMANTIC_INTEGER_VALUES
                fmt = None
                width = A.SEMANTIC_INTEGER_VALUES_BYTES
            else:
                section_type = A.SECTION_TYPE.SEMANTIC_BYTES
                fmt = None
                width = A.SEMANTIC_BYTES_BYTES
            indices = self._span(section_type, begin, count)
            _, payload = self._section(section_type)
            if kind == "bytes":
                return bytes(payload[index] for index in indices)
            values = []
            for index in indices:
                if kind == "integer_list":
                    scalar_kind, reserved, value = A.SEMANTIC_INTEGER_VALUES_FORMAT.unpack_from(
                        payload, index * width
                    )
                    if reserved != bytes(6):
                        raise MeshIrError("E_ABI_RESERVED", "semantic integer reserved bytes are nonzero")
                    if scalar_kind == A.SCALAR_KIND.I64:
                        value = value - (1 << 64) if value >= 1 << 63 else value
                        if value >= 0:
                            raise MeshIrError("E_ABI_ORDER", "I64 semantic integer must be negative")
                    elif scalar_kind != A.SCALAR_KIND.U64:
                        raise MeshIrError("E_ABI_ENUM", "semantic integer kind is invalid")
                    values.append(value)
                else:
                    values.append(fmt.unpack_from(payload, index * width)[0])
            return tuple(values)
        if kind == "scalar":
            scalar_kind, reserved, payload = A.SCALAR_VALUE_FORMAT.unpack_from(raw, offset)
            if reserved != bytes(6):
                raise MeshIrError("E_ABI_RESERVED", "semantic scalar reserved bytes are nonzero")
            if scalar_kind == A.SCALAR_KIND.BOOL:
                if payload > 1:
                    raise MeshIrError("E_ABI_ENUM", "semantic bool scalar is invalid")
                return bool(payload)
            if scalar_kind == A.SCALAR_KIND.I64:
                value = payload - (1 << 64) if payload >= 1 << 63 else payload
                if value >= 0:
                    raise MeshIrError("E_ABI_ORDER", "I64 semantic scalar must be negative")
                return value
            if scalar_kind == A.SCALAR_KIND.U64:
                return payload
            if scalar_kind == A.SCALAR_KIND.F64:
                value = struct.unpack("<d", struct.pack("<Q", payload))[0]
                if not math.isfinite(value):
                    raise MeshIrError("E_ABI_BOUNDS", "semantic float scalar is not finite")
                return value
            raise MeshIrError("E_ABI_ENUM", "semantic scalar kind is invalid")
        raise MeshIrError("E_ABI_CORRUPT", "unknown generated semantic field kind", kind=kind)

    def decode(self, root_raw: bytes) -> object:
        root = self._reference(root_raw, 0, ("mesh_ir.scheduled.model.ProgramSemantics",))
        stack = [self._record_frame(root)]
        result = None
        while stack:
            frame = stack[-1]
            spec = A.SEMANTIC_RECORDS[frame.path]
            fields = spec["fields"]
            if frame.pending_field is not None:
                if frame.pending_index < len(frame.pending_refs):
                    index = frame.pending_refs[frame.pending_index]
                    _, payload = self._section(A.SECTION_TYPE.SEMANTIC_REFERENCES)
                    reference = self._reference(
                        payload,
                        index * A.SEMANTIC_REF_BYTES,
                        tuple(frame.pending_field["targets"]),
                    )
                    frame.pending_index += 1
                    stack.append(self._record_frame(reference, (frame, frame.pending_field["name"], True)))
                    continue
                frame.kwargs[frame.pending_field["name"]] = tuple(frame.pending_values)
                frame.pending_field = None
                frame.pending_refs = ()
                frame.pending_values = []
                frame.pending_index = 0
                frame.field_index += 1
                continue
            if frame.field_index == len(fields):
                try:
                    value = SEMANTIC_CLASSES[frame.path](**frame.kwargs)
                except (TypeError, ValueError) as error:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic record construction failed", type=frame.path) from error
                stack.pop()
                if frame.destination is None:
                    result = value
                else:
                    parent, field_name, is_list = frame.destination
                    if is_list:
                        parent.pending_values.append(value)
                    else:
                        parent.kwargs[field_name] = value
                continue
            field = fields[frame.field_index]
            bit = field.get("optional_bit")
            mask = struct.unpack_from("<Q", frame.raw)[0]
            present = bit is None or bool(mask & (1 << bit))
            if not present:
                if frame.raw[field["offset"]:field["offset"] + field["wire_bytes"]] != bytes(field["wire_bytes"]):
                    raise MeshIrError("E_ABI_RESERVED", "absent semantic field bytes are nonzero", field=field["name"])
                frame.kwargs[field["name"]] = None
                frame.field_index += 1
                continue
            if field["kind"] == "ref":
                reference = self._reference(frame.raw, field["offset"], tuple(field["targets"]))
                frame.field_index += 1
                stack.append(self._record_frame(reference, (frame, field["name"], False)))
                continue
            if field["kind"] == "ref_list":
                begin, count = A.LIST_SPAN_FORMAT.unpack_from(frame.raw, field["offset"])
                indices = self._span(A.SECTION_TYPE.SEMANTIC_REFERENCES, begin, count)
                frame.pending_field = field
                frame.pending_refs = indices
                continue
            frame.kwargs[field["name"]] = self._primitive(field, frame.raw)
            frame.field_index += 1
        for section_type, entry_payload in self.payloads.items():
            entry, _ = entry_payload
            if section_type in A.SEMANTIC_RECORD_BY_SECTION:
                expected = {(section_type, row) for row in range(1, entry["count"] + 1)}
                if not expected <= self.used_records:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic table contains unreachable records", section_type=section_type)
            elif section_type in self.next_primitive:
                if self.next_primitive[section_type] != entry["count"]:
                    raise MeshIrError("E_ABI_BOUNDS", "semantic primitive table contains unreachable rows", section_type=section_type)
        if self.used_strings != set(range(len(self.strings))):
            raise MeshIrError("E_ABI_BOUNDS", "semantic string table contains unreachable strings")
        return result


__all__ = [
    "CLASS_PATHS",
    "ENUM_CLASSES",
    "ENUM_VALUE_BY_WIRE",
    "SEMANTIC_CLASSES",
    "SemanticImage",
    "encode_semantics",
    "encode_string_table",
    "semantic_payloads",
    "SemanticDecoder",
    "semantic_from_canonical",
    "semantic_to_canonical",
]

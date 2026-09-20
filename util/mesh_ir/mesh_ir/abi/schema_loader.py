from __future__ import annotations

import functools
import operator
import re
from pathlib import Path

import yaml

SCALAR_FORMATS = {"u8": "B", "u16": "H", "u32": "I", "u64": "Q", "i64": "q", "f64": "d"}
SCALAR_BYTES = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "i64": 8, "f64": 8}
VECTOR_TYPES = {"bytes6": 6, "bytes16": 16, "bytes28": 28, "bytes32": 32, "u64x8": 64}
CPP_TYPES = {"u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "u64": "uint64_t", "i64": "int64_t", "f64": "double"}
UNSIGNED_MAX = {"u8": (1 << 8) - 1, "u16": (1 << 16) - 1, "u32": (1 << 32) - 1, "u64": (1 << 64) - 1}
PYTHON_CONTAINER_ONLY_ENUMS = {"section_type"}
SEMANTIC_SUPPORT_RECORDS = {
    "PROGRAM_METADATA",
    "SEMANTIC_U64_VALUES",
    "SEMANTIC_I64_VALUES",
    "SEMANTIC_REFERENCES",
    "SEMANTIC_BYTES",
    "SEMANTIC_INTEGER_VALUES",
}
CPP_KEYWORDS = {
    "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor", "bool", "break", "case", "catch", "char", "char16_t", "char32_t", "class", "compl", "concept", "const", "consteval", "constexpr", "constinit", "const_cast", "continue", "co_await", "co_return", "co_yield", "decltype", "default", "delete", "do", "double", "dynamic_cast", "else", "enum", "explicit", "export", "extern", "false", "float", "for", "friend", "goto", "if", "inline", "int", "long", "mutable", "namespace", "new", "noexcept", "not", "not_eq", "nullptr", "operator", "or", "or_eq", "private", "protected", "public", "register", "reinterpret_cast", "requires", "return", "short", "signed", "sizeof", "static", "static_assert", "static_cast", "struct", "switch", "template", "this", "thread_local", "throw", "true", "try", "typedef", "typeid", "typename", "union", "unsigned", "using", "virtual", "void", "volatile", "wchar_t", "while", "xor", "xor_eq",
}


def singular(name: str) -> str:
    return name[:-1] if name.endswith("s") else name


def cpp_name(record_name: str) -> str:
    return singular("".join(part.capitalize() for part in record_name.split("_")))


def cpp_identifier(name: str) -> str:
    return name + "_" if name in CPP_KEYWORDS else name


def enum_allowed_bits(schema: dict) -> dict:
    flag_enums = {
        field["enum"]
        for container in (schema["records"], schema["attr_payloads"])
        for definition in container.values()
        for field in definition["fields"]
        if field.get("flags")
    }
    return {
        enum_name: functools.reduce(operator.or_, values.values(), 0)
        for enum_name, values in schema["enums"].items()
        if enum_name in flag_enums
    }


def enum_field_type_uses(schema: dict) -> dict[str, set[str]]:
    uses = {"section_type": {"u16"}}
    for container in (
        schema["semantic_wire_types"],
        schema["records"],
        schema["attr_payloads"],
    ):
        for definition in container.values():
            for field in definition["fields"]:
                if "enum" in field:
                    uses.setdefault(field["enum"], set()).add(field["type"])
    return uses


def enum_field_types(schema: dict) -> dict[str, str]:
    uses = enum_field_type_uses(schema)
    result = {}
    for enum_name, types in uses.items():
        if not types <= set(UNSIGNED_MAX):
            raise ValueError(f"{enum_name}: enum field must use an unsigned integer")
        result[enum_name] = max(types, key=lambda name: UNSIGNED_MAX[name])
    return result


def field_size(field: dict) -> int:
    ftype = field["type"]
    if ftype in SCALAR_BYTES:
        return SCALAR_BYTES[ftype]
    if ftype in VECTOR_TYPES:
        return VECTOR_TYPES[ftype]
    if "_semantic_bytes" in field:
        return field["_semantic_bytes"]
    if ftype == "record_ref":
        return field["_ref_bytes"]
    raise ValueError(f"unknown field type {ftype}")


def check_layout(name: str, fields: list[dict], total_bytes: int) -> None:
    # Layout validation: ascending offsets, no overlap, no gap, in-bounds,
    # unique names, and total coverage equal to the declared record size.
    if type(total_bytes) is not int or not 0 < total_bytes <= 0xFFFFFFFF:
        raise ValueError(f"{name}: record size must be a positive u32")
    seen_names = set()
    cursor = 0
    for field in fields:
        if field["name"] in seen_names:
            raise ValueError(f"{name}.{field['name']}: duplicate field name")
        seen_names.add(field["name"])
        if type(field["offset"]) is not int or not 0 <= field["offset"] <= 0xFFFFFFFF:
            raise ValueError(f"{name}.{field['name']}: offset must be u32")
        if "const" in field:
            value = field["const"]
            ftype = field["type"]
            if ftype in UNSIGNED_MAX and (type(value) is not int or not 0 <= value <= UNSIGNED_MAX[ftype]):
                raise ValueError(f"{name}.{field['name']}: constant does not fit {ftype}")
            if ftype == "i64" and (type(value) is not int or not -(1 << 63) <= value <= (1 << 63) - 1):
                raise ValueError(f"{name}.{field['name']}: constant does not fit i64")
        if "const_zero" in field and field["const_zero"] is not True:
            raise ValueError(f"{name}.{field['name']}: const_zero must be true")
        if field["offset"] < cursor:
            raise ValueError(
                f"{name}.{field['name']} at {field['offset']} overlaps or "
                f"precedes the previous field ending at {cursor}"
            )
        if field["offset"] > cursor:
            raise ValueError(
                f"{name}: gap of {field['offset'] - cursor} byte(s) before "
                f"{field['name']} (explicit reserved fields only)"
            )
        cursor = field["offset"] + field_size(field)
        if cursor > total_bytes:
            raise ValueError(f"{name}.{field['name']} overflows record")
    if cursor != total_bytes:
        raise ValueError(
            f"{name}: fields cover {cursor} bytes, record declares {total_bytes}"
        )


def load_schema(path: Path) -> dict:
    class StrictLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ValueError(f"duplicate YAML key: {key}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    with path.open("rb") as handle:
        schema = yaml.load(handle, Loader=StrictLoader)
    identifier = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

    def require_identifier(value: object, label: str) -> None:
        if type(value) is not str or identifier.fullmatch(value) is None:
            raise ValueError(f"{label}: invalid identifier")

    def require_uint(value: object, bits: int, label: str) -> None:
        if type(value) is not int or not 0 <= value <= (1 << bits) - 1:
            raise ValueError(f"{label}: value must be u{bits}")

    if set(schema["abi"]) != {"major", "minor", "min_reader_minor"}:
        raise ValueError("ABI version fields are invalid")
    for name, value in schema["abi"].items():
        require_uint(value, 16, f"abi.{name}")
    if schema["abi"]["min_reader_minor"] > schema["abi"]["minor"]:
        raise ValueError("minimum reader minor exceeds ABI minor")
    canonical = schema["canonical"]
    if type(canonical["json_safe_integer_max"]) is not int or canonical["json_safe_integer_max"] != (1 << 53) - 1:
        raise ValueError("canonical JSON safe integer bound is invalid")
    abi_fields = canonical["abi_fields"]
    if set(abi_fields) != {"major", "minor", "min_reader_minor", "required_features"}:
        raise ValueError("canonical ABI fields are incomplete")
    expected_abi_fields = {
        "major": ("abi_major", "u16"),
        "minor": ("abi_minor", "u16"),
        "min_reader_minor": ("min_reader_minor", "u16"),
        "required_features": ("required_features", "u64"),
    }
    for name, definition in abi_fields.items():
        if set(definition) != {"program_field", "type"} or (
            definition["program_field"], definition["type"]
        ) != expected_abi_fields[name]:
            raise ValueError(f"canonical ABI field is invalid: {name}")
    program_fields = canonical["program_fields"]
    program_field_names = [field["name"] for field in program_fields]
    if set(program_field_names) != {"abi", "arch_digest", "sections", "semantics", "semantic_sha256"} or len(program_field_names) != 5:
        raise ValueError("canonical Program fields are incomplete or duplicated")
    if any(
        identifier.fullmatch(field["name"]) is None
        or field["kind"] not in {"abi", "bytes", "transport_sections", "semantic_ref", "digest"}
        or type(field["semantic"]) is not bool
        for field in program_fields
    ):
        raise ValueError("canonical Program field is invalid")
    feature_cpp_names = []
    for name, feature in schema["required_features"].items():
        require_identifier(name, "required feature")
        if name in CPP_KEYWORDS:
            raise ValueError(f"{name}: C++ keyword feature")
        feature_cpp_names.append(name.title().replace("_", ""))
        require_uint(feature["min_reader_minor"], 16, f"{name}.min_reader_minor")
        if feature["min_reader_minor"] > schema["abi"]["minor"]:
            raise ValueError(f"{name}: minimum reader minor exceeds ABI minor")
    if len(feature_cpp_names) != len(set(feature_cpp_names)):
        raise ValueError("required feature C++ names collide")
    for name, wire_type in schema["semantic_wire_types"].items():
        require_identifier(name, "semantic wire type")
        for field in wire_type["fields"]:
            require_identifier(field["name"], f"{name} field")
            if field["name"] in CPP_KEYWORDS:
                raise ValueError(f"{name}.{field['name']}: C++ keyword field")
            if "enum" in field and field["enum"] not in schema["enums"]:
                raise ValueError(f"{name}.{field['name']}: unknown enum {field['enum']}")
        cpp_fields = [cpp_identifier(field["name"]) for field in wire_type["fields"]]
        if len(cpp_fields) != len(set(cpp_fields)):
            raise ValueError(f"{name}: C++ field names collide")
        check_layout(name, wire_type["fields"], wire_type["bytes"])
    semantic_wire_shapes = {
        "semantic_ref": {"section_type": "u16", "reserved": "u16", "row_id": "u32"},
        "list_span": {"begin": "u32", "count": "u32"},
        "string_ref": {"string_id": "u32", "reserved": "u32"},
        "enum_value": {"value": "u32", "reserved": "u32"},
        "scalar_value": {"kind": "u16", "reserved": "bytes6", "payload": "u64"},
    }
    if set(schema["semantic_wire_types"]) != set(semantic_wire_shapes):
        raise ValueError("semantic wire type set is invalid")
    for name, expected in semantic_wire_shapes.items():
        actual = {field["name"]: field["type"] for field in schema["semantic_wire_types"][name]["fields"]}
        if actual != expected:
            raise ValueError(f"{name}: semantic wire field types are invalid")
    transport_cpp_names = []
    for record_name, record in schema["records"].items():
        require_identifier(record_name, "record")
        transport_cpp_names.append(cpp_name(record_name))
        for field in record["fields"]:
            require_identifier(field["name"], f"{record_name} field")
            if field["name"] in CPP_KEYWORDS:
                raise ValueError(f"{record_name}.{field['name']}: C++ keyword field")
            if field["type"] == "record_ref":
                if field["ref"] not in schema["records"]:
                    raise ValueError(f"{record_name}.{field['name']}: unknown record reference")
                field["_ref_bytes"] = schema["records"][field["ref"]]["bytes"]
            if field["type"] in schema["semantic_wire_types"]:
                field["_semantic_bytes"] = schema["semantic_wire_types"][field["type"]]["bytes"]
            if "enum" in field and field["enum"] not in schema["enums"]:
                raise ValueError(f"{record_name}.{field['name']}: unknown enum {field['enum']}")
        cpp_fields = [cpp_identifier(field["name"]) for field in record["fields"]]
        if len(cpp_fields) != len(set(cpp_fields)):
            raise ValueError(f"{record_name}: C++ field names collide")
        check_layout(record_name, record["fields"], record["bytes"])
    for payload_name, payload in schema["attr_payloads"].items():
        require_identifier(payload_name, "payload")
        transport_cpp_names.append(cpp_name(payload_name))
        for field in payload["fields"]:
            require_identifier(field["name"], f"{payload_name} field")
            if field["name"] in CPP_KEYWORDS:
                raise ValueError(f"{payload_name}.{field['name']}: C++ keyword field")
            if "enum" in field and field["enum"] not in schema["enums"]:
                raise ValueError(f"{payload_name}.{field['name']}: unknown enum {field['enum']}")
        cpp_fields = [cpp_identifier(field["name"]) for field in payload["fields"]]
        if len(cpp_fields) != len(set(cpp_fields)):
            raise ValueError(f"{payload_name}: C++ field names collide")
        check_layout(payload_name, payload["fields"], payload["bytes"])
    if len(transport_cpp_names) != len(set(transport_cpp_names)):
        raise ValueError("transport C++ type names collide")
    support_shapes = {
        "PROGRAM_METADATA": {
            "min_reader_minor": "u16", "flags": "u16", "reserved": "u32",
            "semantics": "semantic_ref", "semantic_sha256": "bytes32",
        },
        "SEMANTIC_INTEGER_VALUES": {
            "kind": "u16", "reserved": "bytes6", "payload": "u64",
        },
    }
    for name, expected in support_shapes.items():
        actual = {field["name"]: field["type"] for field in schema["records"][name]["fields"]}
        if actual != expected:
            raise ValueError(f"{name}: support record field types are invalid")
    header = schema["header"]
    for name, definition in (("header", header), ("section_dir", schema["section_dir"])):
        for field in definition["fields"]:
            require_identifier(field["name"], f"{name} field")
            if field["name"] in CPP_KEYWORDS:
                raise ValueError(f"{name}.{field['name']}: C++ keyword field")
        cpp_fields = [cpp_identifier(field["name"]) for field in definition["fields"]]
        if len(cpp_fields) != len(set(cpp_fields)):
            raise ValueError(f"{name}: C++ field names collide")
    check_layout("header", header["fields"], header["bytes"])
    check_layout("section_dir", schema["section_dir"]["fields"], schema["section_dir"]["bytes"])
    if set(schema["blob_sections"]) != {"STRINGS", "SEMANTIC_STRINGS"}:
        raise ValueError("blob section set is invalid")
    for name, blob in schema["blob_sections"].items():
        if blob.get("record_bytes") != 0 or blob.get("encoding") != "utf-8":
            raise ValueError(f"{name}: blob wire contract is invalid")
        check_layout(f"{name} blob header", blob["header_fields"], blob["header_bytes"])
        check_layout(f"{name} blob directory", blob["directory_fields"], blob["directory_record_bytes"])
        if {field["name"]: field["type"] for field in blob["header_fields"]} != {"count": "u32"}:
            raise ValueError(f"{name}: blob header is invalid")
        if {field["name"]: field["type"] for field in blob["directory_fields"]} != {"offset": "u32", "length": "u32"}:
            raise ValueError(f"{name}: blob directory is invalid")
    feature_bits = [item["bit"] for item in schema["required_features"].values()]
    if any(type(bit) is not int or bit < 1 or bit > (1 << 63) or bit & (bit - 1) for bit in feature_bits) or len(feature_bits) != len(set(feature_bits)):
        raise ValueError("required feature bits must be unique powers of two")
    enum_class_names = [item["class_name"] for item in schema["semantic_enums"].values()]
    if len(enum_class_names) != len(set(enum_class_names)):
        raise ValueError("semantic enum class names must be unique")
    enum_value_fields = {
        field["name"]: field for field in schema["semantic_wire_types"]["enum_value"]["fields"]
    }
    if set(enum_value_fields) != {"value", "reserved"} or enum_value_fields["value"]["type"] not in UNSIGNED_MAX:
        raise ValueError("semantic enum wire type is invalid")
    semantic_enum_wire_max = UNSIGNED_MAX[enum_value_fields["value"]["type"]]
    for qualified_name, definition in schema["semantic_enums"].items():
        if any(identifier.fullmatch(part) is None for part in qualified_name.split(".")) or identifier.fullmatch(definition["class_name"]) is None or definition["class_name"] in CPP_KEYWORDS:
            raise ValueError(f"{qualified_name}: invalid semantic enum identifier")
        python_base = definition.get("python_base")
        if python_base not in {"Enum", "IntEnum", "StrEnum"}:
            raise ValueError(f"{qualified_name}: invalid Python enum base")
        if "values_from" in definition:
            if python_base != "IntEnum" or definition["values_from"] not in schema["enums"] or isinstance(schema["enums"][definition["values_from"]], list):
                raise ValueError(f"{qualified_name}: invalid shared enum source")
            member_names = set(schema["enums"][definition["values_from"]])
        else:
            members = definition["members"]
            member_names = {item["name"] for item in members}
            if any(
                type(item.get("name")) is not str
                or identifier.fullmatch(item["name"]) is None
                or item["name"] in CPP_KEYWORDS
                or type(item.get("wire")) is not int
                or not 0 <= item["wire"] <= semantic_enum_wire_max
                for item in members
            ):
                raise ValueError(f"{qualified_name}: invalid semantic enum member")
            if python_base in {"Enum", "StrEnum"}:
                if any(type(item.get("python")) is not str for item in members):
                    raise ValueError(f"{qualified_name}: string enum has non-string Python value")
                try:
                    for item in members:
                        item["python"].encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ValueError(f"{qualified_name}: Python enum value is not UTF-8") from exc
            elif any(type(item.get("python")) is not int or not -(1 << 63) <= item["python"] <= (1 << 63) - 1 for item in members):
                raise ValueError(f"{qualified_name}: integer enum Python value does not fit i64")
            python_values = [(type(item["python"]), item["python"]) for item in members]
            if len({item["name"] for item in members}) != len(members) or len({item["wire"] for item in members}) != len(members) or len(set(python_values)) != len(members):
                raise ValueError(f"{qualified_name}: semantic enum values are not unique")
        for property_name, property_definition in definition.get("python_properties", {}).items():
            require_identifier(property_name, f"{qualified_name} property")
            if set(property_definition) != {"kind", "values"} or property_definition["kind"] not in {"scalar", "enum"}:
                raise ValueError(f"{qualified_name}.{property_name}: invalid property")
            if not set(property_definition["values"]) <= member_names:
                raise ValueError(f"{qualified_name}.{property_name}: unknown member")
            if property_definition["kind"] == "enum" and not set(property_definition["values"].values()) <= member_names:
                raise ValueError(f"{qualified_name}.{property_name}: unknown result member")
    enum_uses = enum_field_type_uses(schema)
    enum_cpp_names = []
    for enum_name, values in schema["enums"].items():
        if enum_name == "opcode_engine_map" or isinstance(values, list):
            continue
        require_identifier(enum_name, "enum")
        enum_cpp_names.append("".join(part.capitalize() for part in enum_name.split("_")))
        if enum_name not in enum_uses:
            raise ValueError(f"{enum_name}: enum has no typed field")
        if len(set(values.values())) != len(values):
            raise ValueError(f"{enum_name}: enum values are not unique")
        max_value = min(UNSIGNED_MAX[field_type] for field_type in enum_uses[enum_name])
        if any(
            identifier.fullmatch(name) is None
            or name in CPP_KEYWORDS
            or type(value) is not int
            or not 0 <= value <= max_value
            for name, value in values.items()
        ):
            raise ValueError(f"{enum_name}: enum member does not fit its field")
    if len(enum_cpp_names) != len(set(enum_cpp_names)):
        raise ValueError("transport C++ enum names collide")
    if set(enum_cpp_names) & set(transport_cpp_names):
        raise ValueError("transport C++ type names collide")
    section_types = schema["enums"]["section_type"]
    if len(set(section_types.values())) != len(section_types):
        raise ValueError("section type values must be unique")
    canonical_sections = canonical["transport_sections"]
    expected_transport_sections = {
        "STRINGS",
        *(
            name for name in schema["records"]
            if name not in SEMANTIC_SUPPORT_RECORDS and name in section_types
        ),
    }
    if set(canonical_sections) != expected_transport_sections:
        raise ValueError("canonical transport sections are incomplete")
    for name, definition in canonical_sections.items():
        if (
            identifier.fullmatch(name) is None
            or identifier.fullmatch(definition["program_field"]) is None
            or definition["projection"] not in {"strings", "records", "attr_payloads"}
            or type(definition["optional"]) is not bool
            or type(definition["semantic"]) is not bool
            or definition["optional"] != (name in schema["optional_sections"])
        ):
            raise ValueError(f"invalid canonical transport section: {name}")
        if definition["projection"] == "strings" and name != "STRINGS":
            raise ValueError(f"invalid string projection section: {name}")
        if definition["projection"] == "attr_payloads" and name != "OP_ATTRS":
            raise ValueError(f"invalid attr projection section: {name}")
    semantic_names = tuple(sorted(schema["semantic_records"]))
    semantic_ids = tuple(schema["semantic_records"][name]["section_type"] for name in semantic_names)
    if len(set(semantic_ids)) != len(semantic_ids) or any(
        type(value) is not int or not 256 <= value <= 0xFFFF
        for value in semantic_ids
    ):
        raise ValueError("semantic record section IDs must be unique explicit u16 values")
    tags = set()
    field_kinds = {"u64", "i64", "bool", "f64", "string", "bytes", "enum", "ref", "u64_list", "integer_list", "ref_list", "scalar"}
    kind_widths = {
        "u64": SCALAR_BYTES["u64"],
        "i64": SCALAR_BYTES["i64"],
        "bool": SCALAR_BYTES["u64"],
        "f64": SCALAR_BYTES["f64"],
        "string": schema["semantic_wire_types"]["string_ref"]["bytes"],
        "bytes": schema["semantic_wire_types"]["list_span"]["bytes"],
        "enum": schema["semantic_wire_types"]["enum_value"]["bytes"],
        "ref": schema["semantic_wire_types"]["semantic_ref"]["bytes"],
        "u64_list": schema["semantic_wire_types"]["list_span"]["bytes"],
        "integer_list": schema["semantic_wire_types"]["list_span"]["bytes"],
        "ref_list": schema["semantic_wire_types"]["list_span"]["bytes"],
        "scalar": schema["semantic_wire_types"]["scalar_value"]["bytes"],
    }
    for qualified_name in semantic_names:
        record = schema["semantic_records"][qualified_name]
        if any(identifier.fullmatch(part) is None for part in qualified_name.split(".")) or identifier.fullmatch(record["section_name"]) is None or identifier.fullmatch(record["json_tag"]) is None or record["json_tag"] in CPP_KEYWORDS:
            raise ValueError(f"{qualified_name}: invalid semantic record identifier")
        require_uint(record["record_bytes"], 32, f"{qualified_name}.record_bytes")
        if record["record_bytes"] < 8:
            raise ValueError(f"{qualified_name}: semantic record is too small")
        if section_types.get(record["section_name"]) != record["section_type"]:
            raise ValueError(f"{qualified_name}: section identity mismatch")
        if record["json_tag"] in tags:
            raise ValueError(f"{qualified_name}: duplicate JSON union tag")
        tags.add(record["json_tag"])
        cursor = 8
        optional_mask = 0
        field_names = set()
        cpp_field_names = set()
        for index, field in enumerate(record["fields"]):
            if identifier.fullmatch(field["name"]) is None or field["name"] in field_names:
                raise ValueError(f"{qualified_name}: invalid or duplicate semantic field")
            field_names.add(field["name"])
            cpp_field = cpp_identifier(field["name"])
            if cpp_field in cpp_field_names:
                raise ValueError(f"{qualified_name}: C++ semantic field names collide")
            cpp_field_names.add(cpp_field)
            if (
                field["kind"] not in field_kinds
                or type(field["wire_bytes"]) is not int
                or field["wire_bytes"] != kind_widths.get(field["kind"])
                or type(field["offset"]) is not int
                or field["offset"] != cursor
            ):
                raise ValueError(f"{qualified_name}.{field['name']}: invalid semantic wire field")
            cursor += field["wire_bytes"]
            if "optional_bit" in field:
                if type(field["optional_bit"]) is not int or field["optional_bit"] != index or not 0 <= field["optional_bit"] < 64:
                    raise ValueError(f"{qualified_name}.{field['name']}: optional bit differs from field index")
                optional_mask |= 1 << index
            if field["kind"] == "enum" and field["enum"] not in schema["semantic_enums"]:
                raise ValueError(f"{qualified_name}.{field['name']}: unknown semantic enum")
            if field["kind"] in ("ref", "ref_list") and not field.get("targets"):
                raise ValueError(f"{qualified_name}.{field['name']}: missing semantic reference targets")
            if field["kind"] not in ("ref", "ref_list") and "targets" in field:
                raise ValueError(f"{qualified_name}.{field['name']}: unexpected semantic reference targets")
            if len(field.get("targets", ())) != len(set(field.get("targets", ()))):
                raise ValueError(f"{qualified_name}.{field['name']}: duplicate semantic reference target")
            for target in field.get("targets", ()):
                if target not in schema["semantic_records"]:
                    raise ValueError(f"{qualified_name}.{field['name']}: unknown semantic reference target")
            field["json_union_discriminator"] = len(field.get("targets", ())) > 1
        if cursor != record["record_bytes"]:
            raise ValueError(f"{qualified_name}: semantic fields do not cover record")
        record["optional_mask"] = optional_mask
    variant_membership_name = "mesh_ir.scheduled.model.VariantMembership"
    program_semantics_name = "mesh_ir.scheduled.model.ProgramSemantics"
    variant_membership = schema["semantic_records"].get(variant_membership_name)
    program_semantics = schema["semantic_records"].get(program_semantics_name)
    if variant_membership is None or program_semantics is None:
        raise ValueError("variant membership relation records are missing")
    semantic_targets = {
        field["name"]: field for field in program_semantics["fields"]
    }
    transport_targets = {
        definition["program_field"]
        for definition in canonical_sections.values()
    }
    membership_targets = set()
    for field in variant_membership["fields"]:
        target = field.get("membership_target")
        if (
            field["kind"] != "ref"
            or field.get("targets") != ["mesh_ir.scheduled.model.IdSpan"]
            or type(target) is not dict
            or set(target) != {"source", "program_field"}
            or type(target["source"]) is not str
            or target["source"] not in {"transport", "semantic"}
            or type(target["program_field"]) is not str
        ):
            raise ValueError("variant membership target is invalid")
        identity = (target["source"], target["program_field"])
        if identity in membership_targets:
            raise ValueError("duplicate variant membership target")
        membership_targets.add(identity)
        if target["source"] == "transport":
            if target["program_field"] not in transport_targets:
                raise ValueError("variant membership transport target is invalid")
            continue
        semantic_target = semantic_targets.get(target["program_field"])
        if (
            semantic_target is None
            or semantic_target["kind"] != "ref_list"
            or "optional_bit" in semantic_target
        ):
            raise ValueError("variant membership semantic target is invalid")
    if set(enum_class_names) & tags:
        raise ValueError("semantic C++ type names collide")
    expected_required = tuple(
        name for name, _ in sorted(section_types.items(), key=lambda item: item[1])
        if name not in schema["optional_sections"]
    )
    if tuple(schema["enums"]["required_sections"]) != expected_required:
        raise ValueError("required sections do not exactly cover non-optional section types")
    enum_allowed_bits(schema)
    return schema



__all__ = [
    "CPP_KEYWORDS", "CPP_TYPES", "PYTHON_CONTAINER_ONLY_ENUMS",
    "SCALAR_BYTES", "SCALAR_FORMATS", "SEMANTIC_SUPPORT_RECORDS",
    "UNSIGNED_MAX", "VECTOR_TYPES", "check_layout", "cpp_identifier",
    "cpp_name", "enum_allowed_bits", "enum_field_types",
    "enum_field_type_uses", "field_size", "load_schema", "singular",
]

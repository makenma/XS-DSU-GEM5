#!/usr/bin/env python3
"""Generate Python/C++/docs ABI artifacts from mesh_ir_abi.yaml.

The yaml file is the single source of truth (SSOT) for every enum, section
id, record field, offset and size of the .mshb binary ABI.  Generated files
embed the schema SHA-256; regeneration must be byte-identical (--check).
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import operator
import sys
from pathlib import Path

import yaml

SCALAR_FORMATS = {"u8": "B", "u16": "H", "u32": "I", "u64": "Q"}
SCALAR_BYTES = {"u8": 1, "u16": 2, "u32": 4, "u64": 8}
VECTOR_TYPES = {"bytes16": 16, "bytes28": 28, "bytes32": 32, "u64x8": 64}
CPP_TYPES = {"u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "u64": "uint64_t"}
ENUM_VALUE_BIT_LIMIT = 32
CONTAINER_ONLY_ENUMS = {"section_type"}


def singular(name: str) -> str:
    return name[:-1] if name.endswith("s") else name


def cpp_name(record_name: str) -> str:
    return singular("".join(part.capitalize() for part in record_name.split("_")))


def enum_masks(schema: dict) -> dict:
    masks = {}
    for enum_name, values in schema["enums"].items():
        if enum_name == "opcode_engine_map" or enum_name in CONTAINER_ONLY_ENUMS or isinstance(values, list):
            continue
        mask = 0
        for value in values.values():
            if not isinstance(value, int) or value < 0 or value >= ENUM_VALUE_BIT_LIMIT:
                raise ValueError(
                    f"{enum_name} value {value} outside mask bit range"
                )
            mask |= 1 << value
        masks[enum_name] = mask
    return masks


def enum_allowed_bits(schema: dict) -> dict:
    return {
        enum_name: functools.reduce(operator.or_, values.values(), 0)
        for enum_name, values in schema["enums"].items()
        if enum_name != "opcode_engine_map"
        and enum_name not in CONTAINER_ONLY_ENUMS
        and not isinstance(values, list)
    }


def field_size(field: dict) -> int:
    ftype = field["type"]
    if ftype in SCALAR_BYTES:
        return SCALAR_BYTES[ftype]
    if ftype in VECTOR_TYPES:
        return VECTOR_TYPES[ftype]
    if ftype == "record_ref":
        return field["_ref_bytes"]
    raise ValueError(f"unknown field type {ftype}")


def check_layout(name: str, fields: list[dict], total_bytes: int) -> None:
    # Layout validation: ascending offsets, no overlap, no gap, in-bounds,
    # unique names, and total coverage equal to the declared record size.
    seen_names = set()
    cursor = 0
    for field in fields:
        if field["name"] in seen_names:
            raise ValueError(f"{name}.{field['name']}: duplicate field name")
        seen_names.add(field["name"])
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
    with path.open("rb") as handle:
        schema = yaml.safe_load(handle)
    for record_name, record in schema["records"].items():
        for field in record["fields"]:
            if field["type"] == "record_ref":
                field["_ref_bytes"] = schema["records"][field["ref"]]["bytes"]
            if "enum" in field and field["enum"] not in schema["enums"]:
                raise ValueError(f"{record_name}.{field['name']}: unknown enum {field['enum']}")
        check_layout(record_name, record["fields"], record["bytes"])
    for payload_name, payload in schema["attr_payloads"].items():
        for field in payload["fields"]:
            if "enum" in field and field["enum"] not in schema["enums"]:
                raise ValueError(f"{payload_name}.{field['name']}: unknown enum {field['enum']}")
        check_layout(payload_name, payload["fields"], payload["bytes"])
    header = schema["header"]
    check_layout("header", header["fields"], header["bytes"])
    check_layout("section_dir", schema["section_dir"]["fields"], schema["section_dir"]["bytes"])
    enum_masks(schema)
    enum_allowed_bits(schema)
    return schema


def schema_sha256(schema: dict, yaml_bytes: bytes) -> str:
    return hashlib.sha256(yaml_bytes).hexdigest()


def py_struct_expr(fields: list[dict], total: int) -> str:
    parts = []
    for field in fields:
        ftype = field["type"]
        if ftype in SCALAR_FORMATS:
            parts.append(SCALAR_FORMATS[ftype])
        elif ftype == "u64x8":
            parts.append("8Q")
        elif ftype.startswith("bytes"):
            parts.append(f"{ftype[5:]}s")
        elif ftype == "record_ref":
            ref_total = field["_ref_bytes"]
            parts.append(f"{ref_total}s")
    expr = "".join(parts)
    accounted = sum(field_size(f) for f in fields)
    if accounted != total:
        raise ValueError(f"fields account for {accounted} bytes, record says {total}")
    return expr


def render_python(schema: dict, sha: str) -> str:
    lines = [
        '"""Generated by generate_abi.py from mesh_ir_abi.yaml -- do not edit.',
        "",
        f"schema_sha256: {sha}",
        '"""',
        "",
        "import struct",
        "",
        "",
    ]
    lines.append(f"SCHEMA_SHA256 = {sha!r}")
    lines.append(f"ABI_MAJOR = {schema['abi']['major']}")
    lines.append(f"ABI_MINOR = {schema['abi']['minor']}")
    lines.append(f"MIN_READER_MINOR = {schema['abi']['min_reader_minor']}")
    magic_le = int.from_bytes(bytes.fromhex(schema["magic_hex"]), "little")
    lines.append(f"MAGIC = {magic_le}")
    lines.append(f"HEADER_BYTES = {schema['header']['bytes']}")
    lines.append(f"SECTION_DIR_BYTES = {schema['section_dir']['bytes']}")
    lines.append("")
    for enum_name, values in schema["enums"].items():
        if enum_name == "opcode_engine_map":
            continue
        if isinstance(values, list):
            lines.append(f"{enum_name.upper()} = {values!r}")
            continue
        lines.append(f"class {enum_name.upper()}:")
        for key, value in values.items():
            lines.append(f"    {key} = {value}")
        lines.append("")
    lines.append("OPCODE_ENGINE = {")
    for opcode, engine in schema["enums"]["opcode_engine_map"].items():
        lines.append(f"    {schema['enums']['opcode'][opcode]}: {schema['enums']['engine'][engine]},  # {opcode} -> {engine}")
    lines.append("}")
    lines.append("")
    for record_name, record in schema["records"].items():
        lines.append(f"{record_name}_BYTES = {record['bytes']}")
        lines.append(f"{record_name}_FORMAT = struct.Struct('<{py_struct_expr(record['fields'], record['bytes'])}')")
        lines.append(f"{record_name}_FIELDS = {record['fields']!r}")
        lines.append("")
    header = schema["header"]
    lines.append(f"HEADER_FORMAT = struct.Struct('<{py_struct_expr(header['fields'], header['bytes'])}')")
    lines.append(f"HEADER_FIELDS = {header['fields']!r}")
    secdir = schema["section_dir"]
    lines.append(f"SECTION_DIR_FORMAT = struct.Struct('<{py_struct_expr(secdir['fields'], secdir['bytes'])}')")
    lines.append(f"SECTION_DIR_FIELDS = {secdir['fields']!r}")
    lines.append("STRING_DIR_FORMAT = struct.Struct('<II')")
    lines.append(f"STRING_DIR_BYTES = {schema['blob_sections']['STRINGS']['directory_record_bytes']}")
    lines.append("")
    for payload_name, payload in schema["attr_payloads"].items():
        lines.append(f"{payload_name}_BYTES = {payload['bytes']}")
        lines.append(f"{payload_name}_FORMAT = struct.Struct('<{py_struct_expr(payload['fields'], payload['bytes'])}')")
        lines.append(f"{payload_name}_FIELDS = {payload['fields']!r}")
        lines.append(f"{payload_name}_FIELD_OFFSETS = " + "{"
            + ", ".join(f"{f['name']!r}: {f['offset']}" for f in payload["fields"]) + "}")
        lines.append("")
    masks = enum_masks(schema)
    lines.append("ENUM_CLOSED_SETS = {")
    for enum_name in schema["enums"]:
        if enum_name == "opcode_engine_map" or enum_name in CONTAINER_ONLY_ENUMS or isinstance(schema["enums"][enum_name], list):
            continue
        values = schema["enums"][enum_name]
        lines.append(f"    {enum_name!r}: " + "{" + ", ".join(str(v) for v in values.values()) + "},")
    lines.append("}")
    lines.append("ENUM_MASKS = " + repr(masks))
    lines.append("ENUM_ALLOWED_BITS = " + repr(enum_allowed_bits(schema)))
    payload_names = set(schema["attr_payloads"])
    attr_kinds = set(schema["enums"]["attr_kind"])
    if payload_names != attr_kinds:
        raise ValueError(f"attr_kind enums and attr_payloads diverge: {payload_names ^ attr_kinds}")
    lines.append("PAYLOAD_BY_KIND = " + repr(
        {value: key for key, value in schema["enums"]["attr_kind"].items()}
    ))
    for record_name, record in schema["records"].items():
        offsets = ", ".join(f"{f['name']!r}: {f['offset']}" for f in record["fields"])
        lines.append(f"{record_name}_FIELD_OFFSETS = {{{offsets}}}")
    lines.append("")
    return "\n".join(lines) + "\n"


def cpp_scalar(field: dict) -> str:
    return CPP_TYPES[field["type"]]


def render_cpp(schema: dict, sha: str) -> str:
    guard = "GEM5_DEV_AI_MESH_GENERATED_MESH_IR_ABI_HH"
    out = []
    out.extend(
        [
            "// Generated by util/mesh_ir/mesh_ir/abi/generate_abi.py from",
            "// mesh_ir_abi.yaml -- do not edit.",
            f"// schema_sha256: {sha}",
            f"#ifndef {guard}",
            f"#define {guard}",
            "",
            "#include <array>",
            "#include <cstdint>",
            "#include <cstring>",
            "#include <variant>",
            "",
            "namespace gem5",
            "{",
            "namespace ai_mesh",
            "{",
            "namespace mesh_abi",
            "{",
            "",
        ]
    )
    out.append(f"constexpr char kSchemaSha256[] = \"{sha}\";")
    out.append(f"constexpr uint16_t kAbiMajor = {schema['abi']['major']};")
    out.append(f"constexpr uint16_t kAbiMinor = {schema['abi']['minor']};")
    out.append(f"constexpr uint16_t kMinReaderMinor = {schema['abi']['min_reader_minor']};")
    magic_le = int.from_bytes(bytes.fromhex(schema["magic_hex"]), "little")
    out.append(f"constexpr uint64_t kMagic = 0x{magic_le:016x}ull;")
    out.append(f"constexpr uint32_t kHeaderBytes = {schema['header']['bytes']};")
    out.append(f"constexpr uint32_t kSectionDirBytes = {schema['section_dir']['bytes']};")
    out.append("")
    for enum_name, values in schema["enums"].items():
        if enum_name == "opcode_engine_map":
            continue
        if isinstance(values, list):
            out.append(f"// {enum_name}: {values}")
            out.append("")
            continue
        cpp_enum = "".join(part.capitalize() for part in enum_name.split("_"))
        out.append(f"enum class {cpp_enum} : uint16_t")
        out.append("{")
        for key, value in values.items():
            out.append(f"    {key} = {value},")
        out.append("};")
        out.append("")
        for key, value in values.items():
            out.append(f"constexpr uint16_t k{cpp_enum}{key} = {value};")
        out.append("")
    out.append("// opcode -> engine mapping (closed set)")
    out.append("constexpr uint16_t opcodeEngine(uint16_t opcode)")
    out.append("{")
    out.append("    switch (opcode) {")
    for opcode, engine in schema["enums"]["opcode_engine_map"].items():
        out.append(f"    case static_cast<uint16_t>(Opcode::{opcode}): return static_cast<uint16_t>(Engine::{engine});")
    out.append("    default: return 0;")
    out.append("    }")
    out.append("}")
    out.append("")
    for record_name, record in schema["records"].items():
        const = "k" + "".join(part.capitalize() for part in record_name.split("_"))
        out.append(f"constexpr uint32_t {const}Bytes = {record['bytes']};")
        for field in record["fields"]:
            fname = "k" + record_name.title().replace("_", "") + "".join(
                part.capitalize() for part in field["name"].split("_")
            )
            if field["type"] in SCALAR_BYTES or field["type"] in VECTOR_TYPES or field["type"] == "record_ref":
                out.append(f"constexpr uint32_t {fname}Offset = {field['offset']};")
        out.append("")
    for payload_name, payload in schema["attr_payloads"].items():
        const = "k" + "".join(part.capitalize() for part in payload_name.split("_"))
        out.append(f"constexpr uint32_t {const}Bytes = {payload['bytes']};")
        for field in payload["fields"]:
            fname = "k" + payload_name.title().replace("_", "") + "".join(
                part.capitalize() for part in field["name"].split("_")
            )
            if field["type"] in SCALAR_BYTES:
                out.append(f"constexpr uint32_t {fname}Offset = {field['offset']};")
        out.append("")

    masks = enum_masks(schema)
    allowed = enum_allowed_bits(schema)
    for enum_name, mask in masks.items():
        cpp_enum = "".join(part.capitalize() for part in enum_name.split("_"))
        out.append(f"constexpr uint32_t k{cpp_enum}ValuesMask = 0x{mask:x}u;")
    for enum_name, bits in allowed.items():
        cpp_enum = "".join(part.capitalize() for part in enum_name.split("_"))
        out.append(f"constexpr uint16_t k{cpp_enum}AllowedBits = 0x{bits:x}u;")
    out.append("")
    out.extend(
        [
            "struct AbiError",
            "{",
            "    const char *code = \"\";",
            "    const char *message = \"\";",
            "};",
            "",
            "constexpr const char *kCodeEnum = \"E_ABI_ENUM\";",
            "constexpr const char *kCodeReserved = \"E_ABI_RESERVED\";",
            "",
            "inline uint8_t rdU8(const uint8_t *p) { return p[0]; }",
            "inline uint16_t rdU16(const uint8_t *p)",
            "{",
            "    return static_cast<uint16_t>(p[0]) | (static_cast<uint16_t>(p[1]) << 8);",
            "}",
            "inline uint32_t rdU32(const uint8_t *p)",
            "{",
            "    return static_cast<uint32_t>(rdU16(p)) | (static_cast<uint32_t>(rdU16(p + 2)) << 16);",
            "}",
            "inline uint64_t rdU64(const uint8_t *p)",
            "{",
            "    return static_cast<uint64_t>(rdU32(p)) | (static_cast<uint64_t>(rdU32(p + 4)) << 32);",
            "}",
            "",
            "inline bool enumMember(uint32_t mask, uint16_t value)",
            "{",
            "    return value < 32 && ((mask >> value) & 1u) != 0;",
            "}",
            "inline bool flagsWithin(uint32_t mask, uint16_t value)",
            "{",
            "    return (value & ~static_cast<uint16_t>(mask)) == 0;",
            "}",
            "",
        ]
    )

    def cpp_field_type(field: dict) -> str:
        ftype = field["type"]
        if ftype in CPP_TYPES:
            return CPP_TYPES[ftype]
        if ftype in VECTOR_TYPES:
            if ftype == "u64x8":
                return "std::array<uint64_t, 8>"
            return f"std::array<uint8_t, {VECTOR_TYPES[ftype]}>"
        if ftype == "record_ref":
            return cpp_name(field["ref"])
        raise ValueError(ftype)

    for record_name, record in schema["records"].items():
        struct = cpp_name(record_name)
        out.append(f"struct {struct}")
        out.append("{")
        for field in record["fields"]:
            ftype = cpp_field_type(field)
            if field["type"] in CPP_TYPES:
                out.append(f"    {ftype} {field['name']} = 0;")
            elif field["type"] in VECTOR_TYPES:
                out.append(f"    {ftype} {field['name']} = {{}};")
            else:
                out.append(f"    {ftype} {field['name']};")
        out.append("};")
        out.append("")
    for payload_name, payload in schema["attr_payloads"].items():
        struct = cpp_name(payload_name)
        out.append(f"struct {struct}")
        out.append("{")
        for field in payload["fields"]:
            out.append(f"    {CPP_TYPES[field['type']]} {field['name']} = 0;")
        out.append("};")
        out.append("")

    def emit_field_checks(label: str, field: dict, out: list) -> None:
        name = field["name"]
        if "enum" in field:
            cpp_enum = "".join(part.capitalize() for part in field["enum"].split("_"))
            mask = f"k{cpp_enum}ValuesMask"
            if field.get("flags"):
                out.append(
                    f"    if (!flagsWithin(k{cpp_enum}AllowedBits, out.{name})) {{ "
                    f"error = {{kCodeEnum, \"{label}.{name} flags have unknown bits\"}}; return false; }}"
                )
            else:
                out.append(
                    f"    if (!enumMember({mask}, out.{name})) {{ "
                    f"error = {{kCodeEnum, \"{label}.{name} not in closed set\"}}; return false; }}"
                )
        if field.get("const_zero"):
            ftype = field["type"]
            if ftype in CPP_TYPES:
                out.append(
                    f"    if (out.{name} != 0) {{ "
                    f"error = {{kCodeReserved, \"{label}.{name} must be zero\"}}; return false; }}"
                )
            elif ftype.startswith("bytes"):
                out.append(
                    f"    if (out.{name} != std::array<uint8_t, {VECTOR_TYPES[ftype]}>{{}}) {{ "
                    f"error = {{kCodeReserved, \"{label}.{name} must be zero\"}}; return false; }}"
                )
        if "const" in field:
            out.append(
                f"    if (out.{name} != {field['const']}) {{ "
                f"error = {{kCodeReserved, \"{label}.{name} must equal {field['const']}\"}}; return false; }}"
            )

    def emit_decoder(fn_name: str, struct: str, label: str, fields: list,
                     out: list) -> None:
        out.append(f"inline bool {fn_name}(const uint8_t *r, {struct} &out, AbiError &error)")
        out.append("{")
        for field in fields:
            ftype = field["type"]
            name = field["name"]
            offset = field["offset"]
            if ftype in CPP_TYPES:
                width = {"u8": "U8", "u16": "U16", "u32": "U32", "u64": "U64"}[ftype]
                out.append(f"    out.{name} = rd{width}(r + {offset});")
            elif ftype == "u64x8":
                out.append(
                    f"    for (size_t i = 0; i < 8; i++) out.{name}[i] = rdU64(r + {offset} + i * 8);"
                )
            elif ftype.startswith("bytes"):
                out.append(
                    f"    std::memcpy(out.{name}.data(), r + {offset}, {VECTOR_TYPES[ftype]});"
                )
            elif ftype == "record_ref":
                out.append(
                    f"    if (!decode{cpp_name(field['ref'])}(r + {offset}, out.{name}, error)) return false;"
                )
            emit_field_checks(label, field, out)
        out.append("    return true;")
        out.append("}")
        out.append("")

    for record_name, record in schema["records"].items():
        emit_decoder(f"decode{cpp_name(record_name)}", cpp_name(record_name),
                     record_name, record["fields"], out)
    for payload_name, payload in schema["attr_payloads"].items():
        emit_decoder(f"decode{cpp_name(payload_name)}", cpp_name(payload_name),
                     payload_name, payload["fields"], out)

    variant_members = ", ".join(cpp_name(name) for name in schema["attr_payloads"])
    out.extend(
        [
            "using AttrPayload = std::variant<std::monostate, " + variant_members + ">;",
            "",
            "constexpr uint32_t attrPayloadBytes(uint16_t kind)",
            "{",
            "    switch (kind) {",
        ]
    )
    for payload_name in schema["attr_payloads"]:
        const = "kAttrKind" + payload_name
        out.append(f"    case {const}: return k{cpp_name(payload_name)}Bytes;")
    out.extend(
        [
            "    default: return 0;",
            "    }",
            "}",
            "",
            "inline bool decodeAttrPayload(uint16_t kind, const uint8_t *payload,",
            "                               AttrPayload &out, AbiError &error)",
            "{",
            "    switch (kind) {",
        ]
    )
    for payload_name in schema["attr_payloads"]:
        struct = cpp_name(payload_name)
        out.append(
            f"    case kAttrKind{payload_name}:"
            f" out = {struct}{{}};"
            f" return decode{struct}(payload, std::get<{struct}>(out), error);"
        )
    out.extend(
        [
            "    default:",
            "        error = {kCodeEnum, \"unknown attr kind\"};",
            "        return false;",
            "    }",
            "}",
            "",
        ]
    )
    out.extend(
        [
            "static_assert(kHeaderBytes == 128, \"header size fixed by spec\");",
            "static_assert(kSectionDirBytes == 40, \"section dir size fixed by spec\");",
            "static_assert(kCommandsBytes == 40, \"COMMANDS record fixed by spec\");",
            "",
            "} // namespace mesh_abi",
            "} // namespace ai_mesh",
            "} // namespace gem5",
            "",
            f"#endif // {guard}",
            "",
        ]
    )
    return "\n".join(out)


def render_markdown(schema: dict, sha: str) -> str:
    out = [
        "# Mesh IR ABI Reference",
        "",
        "Generated from `util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml` -- do not edit.",
        "",
        f"- schema_sha256: `{sha}`",
        f"- ABI: {schema['abi']['major']}.{schema['abi']['minor']} (min reader minor {schema['abi']['min_reader_minor']})",
        f"- magic: `{schema['magic_hex']}`",
        "",
        "## Enums",
        "",
    ]
    for enum_name, values in schema["enums"].items():
        if isinstance(values, list):
            out.append(f"- `{enum_name}` (required): {', '.join(values)}")
            continue
        rows = ", ".join(f"`{k}={v}`" for k, v in values.items())
        out.append(f"- `{enum_name}`: {rows}")
    out.extend(["", "## Records", ""])
    for record_name, record in schema["records"].items():
        out.append(f"### {record_name} ({record['bytes']} B)")
        out.append("")
        out.append("| field | type | offset |")
        out.append("|---|---|---:|")
        for field in record["fields"]:
            out.append(f"| `{field['name']}` | {field['type']} | {field['offset']} |")
        out.append("")
    out.append("## Attr payloads")
    out.append("")
    for payload_name, payload in schema["attr_payloads"].items():
        out.append(f"- `{payload_name}` ({payload['bytes']} B): " + ", ".join(
            f"`{f['name']}`@{f['offset']}" for f in payload["fields"]
        ))
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yaml", default=str(Path(__file__).with_name("mesh_ir_abi.yaml")))
    parser.add_argument("--python-out", default=None)
    parser.add_argument("--cpp-out", default=None)
    parser.add_argument("--docs-out", default=None)
    parser.add_argument("--check", action="store_true", help="verify outputs are up to date")
    args = parser.parse_args()

    yaml_path = Path(args.yaml)
    repo = yaml_path.parents[4]
    defaults = {
        "python_out": repo / "util/mesh_ir/mesh_ir/generated/abi.py",
        "cpp_out": repo / "src/dev/ai_mesh/generated/mesh_ir_abi.hh",
        "docs_out": repo / "docs/generated/mesh_ir_abi.md",
    }
    yaml_bytes = yaml_path.read_bytes()
    schema = load_schema(yaml_path)
    sha = schema_sha256(schema, yaml_bytes)
    outputs = {
        defaults["python_out"]: render_python(schema, sha),
        defaults["cpp_out"]: render_cpp(schema, sha),
        defaults["docs_out"]: render_markdown(schema, sha),
    }
    if args.python_out:
        outputs[Path(args.python_out)] = outputs.pop(defaults["python_out"])
    if args.cpp_out:
        outputs[Path(args.cpp_out)] = outputs.pop(defaults["cpp_out"])
    if args.docs_out:
        outputs[Path(args.docs_out)] = outputs.pop(defaults["docs_out"])

    stale = []
    for path, content in outputs.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path}")
    if args.check and stale:
        print("stale generated files: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

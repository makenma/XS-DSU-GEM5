#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


CODE_PATTERN = re.compile(r"E(?:_[A-Z0-9]+)+\Z")
SEVERITIES = {"error": "Error", "warning": "Warning"}


class CatalogError(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: StrictLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict:
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise CatalogError("mapping key must be scalar") from error
        if duplicate:
            raise CatalogError(f"duplicate mapping key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


@dataclass(frozen=True)
class Definition:
    code: str
    severity: str
    message: str


def load_catalog(yaml_bytes: bytes) -> tuple[Definition, ...]:
    raw = yaml.load(yaml_bytes, Loader=StrictLoader)
    if type(raw) is not dict:
        raise CatalogError("catalog root must be a mapping")
    if set(raw) != {"schema_version", "diagnostics"}:
        raise CatalogError(
            "catalog keys must be schema_version and diagnostics"
        )
    if raw["schema_version"] != "mesh-diagnostics-v1":
        raise CatalogError("schema_version must equal mesh-diagnostics-v1")
    diagnostics = raw["diagnostics"]
    if type(diagnostics) is not dict or not diagnostics:
        raise CatalogError("diagnostics must be a nonempty mapping")
    if len(diagnostics) > 1 << 16:
        raise CatalogError("diagnostics exceeds uint16_t enum capacity")

    definitions = []
    for code, entry in diagnostics.items():
        if type(code) is not str:
            raise CatalogError("diagnostic code must be a string")
        if CODE_PATTERN.fullmatch(code) is None:
            raise CatalogError(f"diagnostic code {code!r} is invalid")
        if type(entry) is not dict:
            raise CatalogError(f"{code} definition must be a mapping")
        if set(entry) != {"severity", "message"}:
            raise CatalogError(
                f"{code} definition keys must be severity and message"
            )
        severity = entry["severity"]
        message = entry["message"]
        if type(severity) is not str or severity not in SEVERITIES:
            raise CatalogError(f"{code} severity must be error or warning")
        if type(message) is not str or not message or "\0" in message:
            raise CatalogError(f"{code} message must be a nonempty string")
        try:
            message.encode("utf-8")
        except UnicodeEncodeError as error:
            raise CatalogError(
                f"{code} message must be valid UTF-8"
            ) from error
        definitions.append(Definition(code, severity, message))
    return tuple(definitions)


def _cpp_string(value: str) -> str:
    encoded = []
    for byte in value.encode("utf-8"):
        if byte == ord('"'):
            encoded.append(r'\"')
        elif byte == ord("\\"):
            encoded.append(r"\\")
        elif 32 <= byte <= 126:
            encoded.append(chr(byte))
        else:
            encoded.append(f"\\{byte:03o}")
    return '"' + "".join(encoded) + '"'


def render_header(
    definitions: tuple[Definition, ...],
    catalog_sha256: str,
) -> str:
    lines = [
        "#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_DIAGNOSTICS_HH",
        "#define GEM5_DEV_AI_MESH_GENERATED_MESH_DIAGNOSTICS_HH",
        "",
        "#include <array>",
        "#include <cstdint>",
        "#include <string_view>",
        "",
        "namespace gem5",
        "{",
        "namespace ai_mesh",
        "{",
        "namespace mesh_diagnostics",
        "{",
        "",
        "enum class DiagnosticCode : uint16_t",
        "{",
    ]
    lines.extend(
        f"    {definition.code} = {index},"
        for index, definition in enumerate(definitions)
    )
    lines.extend(
        [
            "};",
            "",
            "enum class Severity : uint8_t",
            "{",
            "    Error,",
            "    Warning,",
            "};",
            "",
            "struct Definition",
            "{",
            "    DiagnosticCode id;",
            "    std::string_view code;",
            "    Severity severity;",
            "    std::string_view message;",
            "};",
            "",
            "inline constexpr char kCatalogSha256[] = "
            f"{_cpp_string(catalog_sha256)};",
            "",
        ]
    )
    lines.extend(
        f"inline constexpr char {definition.code}[] = "
        f"{_cpp_string(definition.code)};"
        for definition in definitions
    )
    lines.extend(
        [
            "",
            "inline constexpr std::array<Definition, "
            f"{len(definitions)}> kDefinitions{{{{",
        ]
    )
    lines.extend(
        "    {DiagnosticCode::"
        f"{definition.code}, {definition.code}, "
        f"Severity::{SEVERITIES[definition.severity]}, "
        f"{_cpp_string(definition.message)}}},"
        for definition in definitions
    )
    lines.extend(
        [
            "}};",
            "",
            "constexpr const Definition *",
            "findDefinition(DiagnosticCode code)",
            "{",
            "    for (const auto &definition : kDefinitions) {",
            "        if (definition.id == code) {",
            "            return &definition;",
            "        }",
            "    }",
            "    return nullptr;",
            "}",
            "",
            "constexpr const Definition *",
            "findDefinition(std::string_view code)",
            "{",
            "    for (const auto &definition : kDefinitions) {",
            "        if (definition.code == code) {",
            "            return &definition;",
            "        }",
            "    }",
            "    return nullptr;",
            "}",
            "",
            "}",
            "}",
            "}",
            "",
            "#endif",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    repository = Path(__file__).resolve().parents[4]
    parser.add_argument(
        "--yaml",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "diagnostics.yaml",
    )
    parser.add_argument(
        "--cpp-out",
        type=Path,
        default=repository / "src/dev/ai_mesh/generated/mesh_diagnostics.hh",
    )
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        yaml_bytes = arguments.yaml.read_bytes()
        definitions = load_catalog(yaml_bytes)
    except (CatalogError, OSError, UnicodeError, yaml.YAMLError) as error:
        print(f"invalid diagnostics catalog: {error}", file=sys.stderr)
        return 2
    content = render_header(
        definitions,
        hashlib.sha256(yaml_bytes).hexdigest(),
    )
    if arguments.check:
        if (
            not arguments.cpp_out.exists()
            or arguments.cpp_out.read_text(encoding="utf-8") != content
        ):
            print(
                f"stale generated file: {arguments.cpp_out}",
                file=sys.stderr,
            )
            return 1
        return 0
    arguments.cpp_out.parent.mkdir(parents=True, exist_ok=True)
    arguments.cpp_out.write_text(content, encoding="utf-8")
    print(f"wrote {arguments.cpp_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations


def render_envelope_metadata(schema: dict, field_size) -> list[str]:
    out = [
        f"constexpr uint32_t kHeaderBytes = {schema['header']['bytes']};",
        f"constexpr uint32_t kSectionDirBytes = {schema['section_dir']['bytes']};",
    ]
    for prefix, record in (("Header", schema["header"]), ("SectionDir", schema["section_dir"])):
        for field in record["fields"]:
            name = "".join(part.capitalize() for part in field["name"].split("_"))
            out.append(f"constexpr uint32_t k{prefix}{name}Offset = {field['offset']};")
            out.append(f"constexpr uint32_t k{prefix}{name}Bytes = {field_size(field)};")
    for section_name, definition in schema["blob_sections"].items():
        prefix = "".join(part.capitalize() for part in section_name.split("_"))
        out.append(f"constexpr uint32_t k{prefix}BlobHeaderBytes = {definition['header_bytes']};")
        for field in definition["header_fields"]:
            name = "".join(part.capitalize() for part in field["name"].split("_"))
            out.append(f"constexpr uint32_t k{prefix}Blob{name}Offset = {field['offset']};")
            out.append(f"constexpr uint32_t k{prefix}Blob{name}Bytes = {field_size(field)};")
        out.append(f"constexpr uint32_t k{prefix}DirectoryRecordBytes = {definition['directory_record_bytes']};")
        for field in definition["directory_fields"]:
            name = "".join(part.capitalize() for part in field["name"].split("_"))
            out.append(f"constexpr uint32_t k{prefix}Directory{name}Offset = {field['offset']};")
            out.append(f"constexpr uint32_t k{prefix}Directory{name}Bytes = {field_size(field)};")
    section_types = schema["enums"]["section_type"]
    required = schema["enums"]["required_sections"]
    record_bytes = {
        name: definition["bytes"]
        for name, definition in schema["records"].items()
    }
    record_bytes.update({
        definition["section_name"]: definition["record_bytes"]
        for definition in schema["semantic_records"].values()
    })
    out.extend([
        "struct RequiredSectionDescriptor { uint16_t section_type; uint32_t record_bytes; };",
        f"inline constexpr std::array<RequiredSectionDescriptor, {len(required)}> kRequiredSections = {{{{",
    ])
    for name in required:
        out.append(f"    {{{section_types[name]}, {record_bytes.get(name, 0)}}},")
    out.extend([
        "}};",
        f"inline constexpr std::array<uint16_t, {len(required)}> kRequiredSectionTypes = {{{{",
    ])
    for name in required:
        out.append(f"    {section_types[name]},")
    out.extend(["}};", ""])
    return out


def render_python_envelope_metadata(schema: dict, py_struct_expr) -> list[str]:
    out = []
    for section_name, definition in schema["blob_sections"].items():
        out.append(
            f"{section_name}_BLOB_HEADER_FORMAT = "
            f"struct.Struct('<{py_struct_expr(definition['header_fields'], definition['header_bytes'])}')"
        )
        out.append(f"{section_name}_BLOB_HEADER_BYTES = {definition['header_bytes']}")
        out.append(
            f"{section_name}_DIRECTORY_FORMAT = "
            f"struct.Struct('<{py_struct_expr(definition['directory_fields'], definition['directory_record_bytes'])}')"
        )
        out.append(
            f"{section_name}_DIRECTORY_RECORD_BYTES = "
            f"{definition['directory_record_bytes']}"
        )
    section_types = schema["enums"]["section_type"]
    required = schema["enums"]["required_sections"]
    out.append(
        f"BLOB_SECTION_TYPES = "
        f"{tuple(section_types[name] for name in schema['blob_sections'])!r}"
    )
    out.append(f"REQUIRED_SECTION_TYPES = {tuple(section_types[name] for name in required)!r}")
    record_bytes = {
        section_types[name]: definition["bytes"]
        for name, definition in schema["records"].items()
        if name in section_types
    }
    record_bytes.update({
        definition["section_type"]: definition["record_bytes"]
        for definition in schema["semantic_records"].values()
    })
    out.append(f"SECTION_RECORD_BYTES = {dict(sorted(record_bytes.items()))!r}")
    return out


__all__ = ["render_envelope_metadata", "render_python_envelope_metadata"]

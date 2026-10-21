from __future__ import annotations


def render_semantic_enums(schema: dict) -> str:
    lines = ["from enum import Enum, IntEnum, StrEnum", ""]
    exported = []
    for qualified_name, definition in schema["semantic_enums"].items():
        name = definition["class_name"]
        exported.append(name)
        members = definition.get("members")
        if members is None:
            members = [
                {"name": member, "python": value}
                for member, value in schema["enums"][definition["values_from"]].items()
            ]
        base = definition["python_base"]
        declaration = f"str, {base}" if base == "Enum" else base
        lines.append(f"class {name}({declaration}):")
        for member in members:
            lines.append(f"    {member['name']} = {member['python']!r}")
        for property_name, property_definition in definition.get("python_properties", {}).items():
            lines.extend(["", "    @property", f"    def {property_name}(self):", "        return {"])
            for member, result in property_definition["values"].items():
                value = f"{name}.{result}" if property_definition["kind"] == "enum" else repr(result)
                lines.append(f"            {name}.{member}: {value},")
            lines.append("        }[self]")
        lines.append("")
        lines.append(f"{name}.__module__ = {qualified_name.rsplit('.', 1)[0]!r}")
        lines.append("")
    lines.append(f"__all__ = {exported!r}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_semantic_enums"]

from __future__ import annotations

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError


def _zero(value) -> bool:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value) == bytes(len(value))
    if isinstance(value, tuple):
        return all(item == 0 for item in value)
    return value == 0


def check_record_rules(section_name: str, records) -> None:
    fields = getattr(A, f"{section_name}_FIELDS")
    for record in records:
        for field in fields:
            name = field["name"]
            enum_name = field.get("enum")
            if enum_name is not None:
                value = getattr(record, name)
                if field.get("flags"):
                    if value & ~A.ENUM_ALLOWED_BITS[enum_name]:
                        raise MeshIrError(
                            "E_ABI_ENUM",
                            f"{section_name}.{name} flags have unknown bits",
                            value=value,
                        )
                elif value not in A.ENUM_CLOSED_SETS[enum_name]:
                    raise MeshIrError(
                        "E_ABI_ENUM",
                        f"{section_name}.{name} not in closed set",
                        value=value,
                    )
            if field.get("const_zero") and not _zero(getattr(record, name)):
                raise MeshIrError(
                    "E_ABI_RESERVED",
                    f"{section_name}.{name} must be zero",
                )
            if "const" in field:
                if getattr(record, name) != field["const"]:
                    raise MeshIrError(
                        "E_ABI_RESERVED",
                        f"{section_name}.{name} must equal {field['const']}",
                    )


def check_payload_rules(payload_name: str, payload_values: tuple, payload_fields) -> None:
    for field, value in zip(payload_fields, payload_values):
        enum_name = field.get("enum")
        if enum_name is not None:
            if field.get("flags"):
                if value & ~A.ENUM_ALLOWED_BITS[enum_name]:
                    raise MeshIrError(
                        "E_ABI_ENUM",
                        f"{payload_name}.{field['name']} flags have unknown bits",
                        value=value,
                    )
            elif value not in A.ENUM_CLOSED_SETS[enum_name]:
                raise MeshIrError(
                    "E_ABI_ENUM",
                    f"{payload_name}.{field['name']} not in closed set",
                    value=value,
                )
        if field.get("const_zero") and not _zero(value):
            raise MeshIrError(
                "E_ABI_RESERVED",
                f"{payload_name}.{field['name']} must be zero",
            )
        if "const" in field:
            if value != field["const"]:
                raise MeshIrError(
                    "E_ABI_RESERVED",
                    f"{payload_name}.{field['name']} must equal {field['const']}",
                )

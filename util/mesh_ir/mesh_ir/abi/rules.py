from __future__ import annotations

import math

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError


_UNSIGNED_MAX = {
    "u8": (1 << 8) - 1,
    "u16": (1 << 16) - 1,
    "u32": (1 << 32) - 1,
    "u64": (1 << 64) - 1,
}


def check_abi_header(major: object, minor: object, required_features: object) -> None:
    if any(type(value) is not int for value in (major, minor, required_features)):
        raise MeshIrError("E_ABI_VERSION", "program ABI header fields must be integers")
    if major != A.ABI_MAJOR or not A.MIN_READER_MINOR <= minor <= _UNSIGNED_MAX["u16"]:
        raise MeshIrError("E_ABI_VERSION", "program ABI version is unsupported")
    if required_features != A.REQUIRED_FEATURES:
        raise MeshIrError("E_ABI_VERSION", "program required features are unsupported")


def check_min_reader_minor(
    min_reader_minor: object,
    abi_minor: int,
    required_features: int,
) -> None:
    if type(min_reader_minor) is not int:
        raise MeshIrError("E_ABI_VERSION", "minimum reader minor must be an integer")
    feature_floor = max(
        (
            minimum
            for bit, minimum in A.REQUIRED_FEATURE_MIN_READER_MINOR.items()
            if required_features & bit
        ),
        default=0,
    )
    if not feature_floor <= min_reader_minor <= A.ABI_MINOR or min_reader_minor > abi_minor:
        raise MeshIrError("E_ABI_VERSION", "program minimum reader version is unsupported")


def check_field_rules(field: dict, value, section: str) -> None:
    name = field["name"]
    kind = field["type"]
    if field.get("string_pool") == "SEMANTIC_STRINGS":
        if type(value) is not str:
            raise MeshIrError("E_ABI_BOUNDS", f"{section}.{name} must be a string")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise MeshIrError("E_ABI_BOUNDS", f"{section}.{name} is not valid Unicode") from error
    elif kind in _UNSIGNED_MAX:
        if type(value) is not int or not 0 <= value <= _UNSIGNED_MAX[kind]:
            raise MeshIrError("E_ABI_BOUNDS", f"{section}.{name} does not fit {kind}")
    elif kind == "i64":
        if type(value) is not int or not -(1 << 63) <= value <= (1 << 63) - 1:
            raise MeshIrError("E_ABI_BOUNDS", f"{section}.{name} does not fit i64")
    elif kind == "f64":
        if type(value) is not float or not math.isfinite(value):
            raise MeshIrError("E_ABI_BOUNDS", f"{section}.{name} must be a finite float")
    elif kind == "u64x8":
        if type(value) is not tuple or len(value) != 8 or any(
            type(item) is not int or not 0 <= item <= _UNSIGNED_MAX["u64"]
            for item in value
        ):
            raise MeshIrError("E_ABI_BOUNDS", f"{section}.{name} must contain eight u64 values")
    elif kind.startswith("bytes"):
        width = int(kind[5:])
        if type(value) is not bytes or len(value) != width:
            raise MeshIrError("E_ABI_BOUNDS", f"{section}.{name} must contain {width} bytes")
    enum_name = field.get("enum")
    if enum_name is not None:
        if field.get("flags"):
            if value & ~A.ENUM_ALLOWED_BITS[enum_name]:
                raise MeshIrError(
                    "E_ABI_ENUM",
                    f"{section}.{name} flags have unknown bits",
                    value=value,
                )
        elif value not in A.ENUM_CLOSED_SETS[enum_name]:
            raise MeshIrError(
                "E_ABI_ENUM",
                f"{section}.{name} not in closed set",
                value=value,
            )
    if field.get("const_zero") and not _zero(value):
        raise MeshIrError(
            "E_ABI_RESERVED",
            f"{section}.{name} must be zero",
        )
    if "const" in field and value != field["const"]:
        raise MeshIrError(
            "E_ABI_RESERVED",
            f"{section}.{name} must equal {field['const']}",
        )


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
            value = getattr(record, field.get("python_field", name))
            check_field_rules(field, value, section_name)


def check_payload_rules(payload_name: str, payload_values: tuple, payload_fields) -> None:
    for field, value in zip(payload_fields, payload_values):
        check_field_rules(field, value, payload_name)

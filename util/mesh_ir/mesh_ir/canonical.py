from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from enum import Enum
from typing import Any, Mapping

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A


U64_MAX = (1 << 64) - 1
I64_MIN = -(1 << 63)
JSON_SAFE_INTEGER_MAX = A.JSON_SAFE_INTEGER_MAX


def checked_u64(value: int, field: str = "value") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= U64_MAX:
        raise MeshIrError("E_CONFIG", "unsigned 64-bit integer required", field=field, value=value)
    return value


def checked_add_u64(left: int, right: int, field: str = "value") -> int:
    checked_u64(left, field)
    checked_u64(right, field)
    if left > U64_MAX - right:
        raise MeshIrError("E_ABI_OVERFLOW", "unsigned 64-bit addition overflowed", field=field, left=left, right=right)
    return left + right


def checked_mul_u64(left: int, right: int, field: str = "value") -> int:
    checked_u64(left, field)
    checked_u64(right, field)
    if left and right > U64_MAX // left:
        raise MeshIrError("E_ABI_OVERFLOW", "unsigned 64-bit multiplication overflowed", field=field, left=left, right=right)
    return left * right


def to_canonical(value: Any) -> Any:
    root = [None]
    active: set[int] = set()
    stack = [("value", value, root, 0)]
    while stack:
        action, item, parent, key = stack.pop()
        if action == "exit":
            active.remove(item)
            continue
        if isinstance(item, Enum):
            stack.append(("value", item.value, parent, key))
            continue
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            identity = id(item)
            if identity in active:
                raise MeshIrError("E_CONFIG", "canonical values must not contain cycles")
            active.add(identity)
            output = {}
            parent[key] = output
            stack.append(("exit", identity, None, None))
            fields = tuple(field for field in dataclasses.fields(item) if getattr(item, field.name) is not None)
            for field in reversed(fields):
                stack.append(("value", getattr(item, field.name), output, field.name))
            continue
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                raise MeshIrError("E_CONFIG", "canonical values must not contain cycles")
            if not all(isinstance(map_key, str) for map_key in item):
                raise MeshIrError("E_CONFIG", "canonical mappings require string keys")
            output = {}
            parent[key] = output
            active.add(identity)
            stack.append(("exit", identity, None, None))
            for map_key in reversed(tuple(item)):
                try:
                    map_key.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise MeshIrError("E_CONFIG", "canonical mapping key is not valid Unicode") from error
                stack.append(("value", item[map_key], output, map_key))
            continue
        if isinstance(item, (tuple, list)):
            identity = id(item)
            if identity in active:
                raise MeshIrError("E_CONFIG", "canonical values must not contain cycles")
            output = [None] * len(item)
            parent[key] = output
            active.add(identity)
            stack.append(("exit", identity, None, None))
            for index in range(len(item) - 1, -1, -1):
                stack.append(("value", item[index], output, index))
            continue
        if isinstance(item, bytes):
            parent[key] = item.hex()
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as error:
                raise MeshIrError("E_CONFIG", "canonical string is not valid Unicode") from error
            parent[key] = item
        elif item is None or isinstance(item, bool):
            parent[key] = item
        elif isinstance(item, int):
            if not I64_MIN <= item <= U64_MAX:
                raise MeshIrError("E_CONFIG", "integer outside canonical domain")
            parent[key] = hex(item) if item > JSON_SAFE_INTEGER_MAX else item
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise MeshIrError("E_CONFIG", "canonical JSON forbids non-finite numbers")
            parent[key] = item
        else:
            raise MeshIrError("E_CONFIG", "unsupported canonical value type", type=type(item).__name__)
    return root[0]


def canonical_json_bytes(value: Any) -> bytes:
    canonical = to_canonical(value)
    try:
        return json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, UnicodeEncodeError, ValueError) as error:
        raise MeshIrError("E_CONFIG", "canonical JSON emission failed", detail=str(error)) from error


def semantic_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def strict_json_loads(payload: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise MeshIrError("E_CONFIG", "duplicate JSON key", key=key)
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise MeshIrError("E_CONFIG", "non-finite JSON number", value=value)

    def parse_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise MeshIrError("E_CONFIG", "JSON floating-point value is out of range", value=value)
        return result

    def parse_int(value: str) -> int:
        digits = value[1:] if value.startswith("-") else value
        if len(digits) > 20:
            raise MeshIrError("E_CONFIG", "JSON integer is out of range", digits=len(digits))
        try:
            result = int(value)
        except ValueError as error:
            raise MeshIrError("E_CONFIG", "malformed JSON integer") from error
        if not I64_MIN <= result <= U64_MAX:
            raise MeshIrError("E_CONFIG", "JSON integer is out of range")
        return result

    try:
        result = json.loads(
            payload,
            object_pairs_hook=pairs,
            parse_constant=constant,
            parse_float=parse_float,
            parse_int=parse_int,
        )
        pending = [result]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                value.encode("utf-8")
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return result
    except MeshIrError:
        raise
    except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise MeshIrError("E_CONFIG", "malformed JSON", detail=str(error)) from error

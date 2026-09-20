from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
from types import MappingProxyType
from typing import Mapping

import yaml


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class DiagnosticDefinition:
    code: str
    severity: Severity
    message: str


def _load_catalog() -> Mapping[str, DiagnosticDefinition]:
    raw = yaml.safe_load(files("mesh_ir").joinpath("diagnostics.yaml").read_text(encoding="utf-8"))
    definitions = {
        code: DiagnosticDefinition(code, Severity(entry["severity"]), entry["message"])
        for code, entry in raw["diagnostics"].items()
    }
    return MappingProxyType(definitions)


DIAGNOSTICS = _load_catalog()
ERROR_CODES = tuple(DIAGNOSTICS)


class MeshIrError(Exception):
    def __init__(self, code: str, message: str | None = None, **context: object):
        if code not in DIAGNOSTICS:
            raise ValueError(f"unregistered diagnostic code {code}")
        definition = DIAGNOSTICS[code]
        self.code = code
        self.severity = definition.severity
        self.message = definition.message if message is None else message
        self.context = {key: value for key, value in context.items() if value is not None}
        super().__init__(f"{self.code}: {self.message}")

    def as_dict(self) -> dict[str, object]:
        record: dict[str, object] = {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.context:
            record["context"] = self.context
        return record

    def to_jsonl(self) -> str:
        from mesh_ir.canonical import canonical_json_bytes

        def safe(value: object) -> object:
            if isinstance(value, float) and not math.isfinite(value):
                if math.isnan(value):
                    return {"invalid_scalar": "nan"}
                return {"invalid_scalar": "infinity" if value > 0 else "-infinity"}
            if type(value) is int and not -(1 << 63) <= value <= (1 << 64) - 1:
                prefix = "-0x" if value < 0 else "0x"
                return {"invalid_integer": prefix + format(abs(value), "x")}
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    text = str(key)
                    try:
                        text.encode("utf-8")
                    except UnicodeEncodeError:
                        text = text.encode("unicode_escape").decode("ascii")
                    result[text] = safe(item)
                return result
            if isinstance(value, (list, tuple)):
                return [safe(item) for item in value]
            if isinstance(value, str):
                try:
                    value.encode("utf-8")
                except UnicodeEncodeError:
                    return {"invalid_string": "unicode"}
                return value
            if value is None or isinstance(value, (bool, int, float)):
                return value
            value_type = type(value)
            return {"unsupported_type": f"{value_type.__module__}.{value_type.__qualname__}"}

        return canonical_json_bytes(safe(self.as_dict())).decode("utf-8")

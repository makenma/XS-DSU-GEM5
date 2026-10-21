from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.passes.canonicalize import TARGET_SCHEMAS
from mesh_ir.torch_compat import DECOMPOSITION_DIGEST, PRESERVED_OPERATORS, ExportHandle, current_dialect, decompose_program, op_histogram


@dataclass(frozen=True)
class DecompositionManifest:
    torch_version: str
    source_opset: tuple[tuple[str, int], ...]
    source_schema: tuple[int, int]
    dialect_before: str
    dialect_after: str
    before_histogram: tuple[tuple[str, int], ...]
    after_histogram: tuple[tuple[str, int], ...]
    preserved_operators: tuple[str, ...]
    unsupported_operators: tuple[str, ...]
    decomposition_digest: str


def decompose(handle: ExportHandle) -> tuple[ExportHandle, DecompositionManifest]:
    before = op_histogram(handle)
    result = decompose_program(handle)
    after = op_histogram(result)
    unsupported = tuple(name for name, _ in after if name not in TARGET_SCHEMAS)
    return result, DecompositionManifest(
        handle.torch_version,
        handle.source_opset,
        handle.source_schema,
        current_dialect(handle),
        current_dialect(result),
        before,
        after,
        PRESERVED_OPERATORS,
        unsupported,
        DECOMPOSITION_DIGEST,
    )

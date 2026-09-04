from __future__ import annotations

from mesh_ir.model import MeshIrError


def span_fits(begin: int, count: int, total: int) -> bool:
    return begin <= total and count <= total - begin


def checked_span(begin: int, count: int, total: int, message: str) -> slice:
    if not span_fits(begin, count, total):
        raise MeshIrError("E_ABI_BOUNDS", message, begin=begin, count=count, total=total)
    return slice(begin, begin + count)

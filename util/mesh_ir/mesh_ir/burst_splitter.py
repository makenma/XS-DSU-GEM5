"""Reference AXI burst splitter (spec section 7.2 formula).

This is the compiler-side reference implementation.  The gem5 C++ runtime
splitter is an independent implementation; both are pinned by the shared
cross-language golden vectors in tests/golden/burst_splitter_golden.json.
"""

from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.model import MeshIrError


@dataclass(frozen=True)
class Burst:
    beat_base: int
    logical_start: int
    useful_bytes: int
    beats: int


@dataclass(frozen=True)
class SegmentPlan:
    segments: int
    bursts: tuple
    beat_bytes: int
    useful_bytes: int


def checked_mul(a: int, b: int) -> int:
    result = a * b
    if result >= 1 << 64:
        raise MeshIrError("E_ABI_OVERFLOW", "checked multiply overflow", a=a, b=b)
    return result


def split_segment(address: int, useful: int, width: int, max_beats: int) -> tuple:
    """Split one contiguous logical range into AXI bursts (spec 7.2)."""
    bursts = []
    a = address
    remaining = useful
    while remaining > 0:
        beat_base = a & ~(width - 1)
        head = a - beat_base
        page_cap = 4096 - (beat_base & 0xFFF)
        burst_cap = max_beats * width
        take = min(remaining, page_cap - head, burst_cap - head)
        beats = (head + take + width - 1) // width
        if not (1 <= beats <= max_beats <= 256):
            raise MeshIrError("E_DMA_RANGE", "burst beats out of legal range", beats=beats)
        if (beat_base >> 12) != ((beat_base + beats * width - 1) >> 12):
            raise MeshIrError("E_DMA_4K_SPLIT", "burst crosses 4KiB page", beat_base=beat_base)
        bursts.append(Burst(beat_base, a, take, beats))
        a += take
        remaining -= take
    return tuple(bursts)


def plan_descriptor(row_bytes: int, rows: int, base: int, src_stride: int, width: int, max_beats: int) -> SegmentPlan:
    """Explode a 2-D descriptor into row segments then bursts."""
    if rows == 0 or row_bytes == 0:
        return SegmentPlan(0, (), 0, 0)
    useful_total = checked_mul(rows, row_bytes)
    all_bursts = []
    for row in range(rows):
        seg_addr = base + checked_mul(row, src_stride)
        all_bursts.extend(split_segment(seg_addr, row_bytes, width, max_beats))
    beat_bytes = sum(checked_mul(b.beats, width) for b in all_bursts)
    return SegmentPlan(rows, tuple(all_bursts), beat_bytes, useful_total)


def validate_burst_invariants(bursts: tuple, useful: int) -> None:
    total = sum(b.useful_bytes for b in bursts)
    if total != useful:
        raise MeshIrError("E_DMA_RANGE", "burst useful bytes do not cover descriptor", total=total, useful=useful)

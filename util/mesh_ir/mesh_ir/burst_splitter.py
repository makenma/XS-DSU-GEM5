from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.canonical import checked_add_u64, checked_mul_u64 as checked_mul, checked_u64
from mesh_ir.diagnostics import MeshIrError


@dataclass(frozen=True)
class Burst:
    beat_base: int
    logical_start: int
    useful_bytes: int
    beats: int


@dataclass(frozen=True)
class SegmentPlan:
    segments: int
    bursts: tuple[Burst, ...]
    beat_bytes: int
    useful_bytes: int


def split_segment(address: int, useful: int, width: int, max_beats: int) -> tuple[Burst, ...]:
    checked_u64(address, "address")
    checked_u64(useful, "useful_bytes")
    if type(width) is not int or width not in (8, 16, 32, 64):
        raise MeshIrError("E_DMA_RANGE", "AXI width must be 8, 16, 32, or 64 bytes", width=width)
    if type(max_beats) is not int or not 1 <= max_beats <= 256:
        raise MeshIrError("E_DMA_RANGE", "maximum AXI burst beats is out of range", max_beats=max_beats)
    if useful:
        checked_add_u64(address, useful - 1, "logical segment end")
    burst_cap = checked_mul(max_beats, width, "burst capacity")
    bursts: list[Burst] = []
    a = address
    remaining = useful
    while remaining > 0:
        beat_base = a & ~(width - 1)
        head = a - beat_base
        page_cap = 4096 - (beat_base & 0xFFF)
        take = min(remaining, page_cap - head, burst_cap - head)
        beats = (head + take + width - 1) // width
        if not (1 <= beats <= max_beats <= 256):
            raise MeshIrError("E_DMA_RANGE", "burst beats out of legal range", beats=beats)
        physical_end = checked_add_u64(beat_base, checked_mul(beats, width, "burst physical bytes") - 1, "burst physical end")
        if (beat_base >> 12) != (physical_end >> 12):
            raise MeshIrError("E_DMA_4K_SPLIT", "burst crosses 4KiB page", beat_base=beat_base)
        bursts.append(Burst(beat_base, a, take, beats))
        a = checked_add_u64(a, take, "next burst address")
        remaining -= take
    return tuple(bursts)


def plan_descriptor(row_bytes: int, rows: int, base: int, src_stride: int, width: int, max_beats: int) -> SegmentPlan:
    checked_u64(row_bytes, "row_bytes")
    checked_u64(rows, "rows")
    checked_u64(base, "base")
    checked_u64(src_stride, "row_stride_bytes")
    if rows > 0xFFFFFFFF:
        raise MeshIrError("E_DMA_RANGE", "descriptor row count exceeds u32", rows=rows)
    split_segment(base, 0, width, max_beats)
    if rows == 0 or row_bytes == 0:
        return SegmentPlan(0, (), 0, 0)
    useful_total = checked_mul(rows, row_bytes, "descriptor useful bytes")
    last_row = checked_add_u64(base, checked_mul(rows - 1, src_stride, "last row displacement"), "last row address")
    checked_add_u64(last_row, row_bytes - 1, "last row logical end")
    all_bursts: list[Burst] = []
    for row in range(rows):
        seg_addr = checked_add_u64(base, checked_mul(row, src_stride, "row displacement"), "row address")
        all_bursts.extend(split_segment(seg_addr, row_bytes, width, max_beats))
    beat_bytes = 0
    for burst in all_bursts:
        beat_bytes = checked_add_u64(beat_bytes, checked_mul(burst.beats, width, "burst beat bytes"), "descriptor physical beat bytes")
    return SegmentPlan(rows, tuple(all_bursts), beat_bytes, useful_total)


def validate_burst_invariants(bursts: tuple[Burst, ...], useful: int) -> None:
    checked_u64(useful, "useful_bytes")
    if type(bursts) is not tuple or any(not isinstance(burst, Burst) for burst in bursts):
        raise MeshIrError("E_DMA_RANGE", "burst sequence must be an immutable Burst tuple")
    total = 0
    for burst in bursts:
        total = checked_add_u64(total, burst.useful_bytes, "covered useful bytes")
    if total != useful:
        raise MeshIrError("E_DMA_RANGE", "burst useful bytes do not cover descriptor", total=total, useful=useful)

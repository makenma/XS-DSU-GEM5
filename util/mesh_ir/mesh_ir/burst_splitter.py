from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import gcd

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


def _descriptor_extent(row_bytes: int, rows: int, base: int, src_stride: int, width: int, max_beats: int) -> int:
    checked_u64(row_bytes, "row_bytes")
    checked_u64(rows, "rows")
    checked_u64(base, "base")
    checked_u64(src_stride, "row_stride_bytes")
    if rows > 0xFFFFFFFF:
        raise MeshIrError("E_DMA_RANGE", "descriptor row count exceeds u32", rows=rows)
    split_segment(base, 0, width, max_beats)
    if rows == 0 or row_bytes == 0:
        return 0
    useful_total = checked_mul(rows, row_bytes, "descriptor useful bytes")
    last_row = checked_add_u64(base, checked_mul(rows - 1, src_stride, "last row displacement"), "last row address")
    checked_add_u64(last_row, row_bytes - 1, "last row logical end")
    return useful_total


def plan_descriptor(row_bytes: int, rows: int, base: int, src_stride: int, width: int, max_beats: int) -> SegmentPlan:
    useful_total = _descriptor_extent(row_bytes, rows, base, src_stride, width, max_beats)
    if not useful_total:
        return SegmentPlan(0, (), 0, 0)
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


@dataclass(frozen=True)
class DescriptorCounts:
    segments: int
    bursts: int
    beats: int
    useful_bytes: int
    exact: bool


@lru_cache(maxsize=8192, typed=True)
def count_segment(address: int, useful: int, width: int, max_beats: int) -> DescriptorCounts:
    split_segment(address, 0, width, max_beats)
    checked_u64(useful, 'useful_bytes')
    if not useful:
        return DescriptorCounts(0, 0, 0, 0, True)
    checked_add_u64(address, useful - 1, 'logical segment end')
    capacity = width * max_beats
    first = min(useful, 4096 - address % 4096)
    first_physical = address % width + first
    pages, tail = divmod(useful - first, 4096)
    bursts = checked_add_u64((first_physical + capacity - 1) // capacity,
        checked_mul(pages, (4096 + capacity - 1) // capacity, 'full-page bursts'), 'segment bursts')
    bursts = checked_add_u64(bursts, (tail + capacity - 1) // capacity, 'segment tail bursts')
    beats = checked_add_u64((first_physical + width - 1) // width,
        checked_mul(pages, 4096 // width, 'full-page beats'), 'segment beats')
    beats = checked_add_u64(beats, (tail + width - 1) // width, 'segment tail beats')
    checked_mul(beats, width, 'segment physical beat bytes')
    return DescriptorCounts(1, bursts, beats, useful, True)


def count_descriptor(row_bytes: int, rows: int, base: int, src_stride: int, width: int, max_beats: int) -> DescriptorCounts:
    useful = _descriptor_extent(row_bytes, rows, base, src_stride, width, max_beats)
    if not useful:
        return DescriptorCounts(0, 0, 0, 0, True)
    period = 4096 // gcd(src_stride, 4096)
    counts = tuple(count_segment((base + index * src_stride) % 4096, row_bytes, width, max_beats)
        for index in range(min(rows, period)))
    cycles, tail = divmod(rows, period)
    bursts = checked_add_u64(checked_mul(cycles, sum(item.bursts for item in counts), 'descriptor period bursts'),
        sum(item.bursts for item in counts[:tail]), 'descriptor bursts')
    beats = checked_add_u64(checked_mul(cycles, sum(item.beats for item in counts), 'descriptor period beats'),
        sum(item.beats for item in counts[:tail]), 'descriptor beats')
    checked_mul(beats, width, 'descriptor physical beat bytes')
    return DescriptorCounts(rows, bursts, beats, useful, True)


def plan_indexed_descriptor(row_bytes: int, rows: int, base: int, stride: int,
                            table_rows: int, width: int, max_beats: int) -> DescriptorCounts:
    for name, value in (("row_bytes", row_bytes), ("rows", rows), ("base", base),
                        ("stride", stride), ("table_rows", table_rows)):
        checked_u64(value, name)
    split_segment(base, 0, width, max_beats)
    if rows > 0xFFFFFFFF:
        raise MeshIrError("E_DMA_RANGE", "indexed descriptor row count exceeds u32")
    if not rows or not row_bytes:
        return DescriptorCounts(0, 0, 0, 0, True)
    if not table_rows or stride < row_bytes:
        raise MeshIrError("E_DMA_RANGE", "indexed source needs nonempty disjoint rows")
    end = checked_add_u64(base, checked_mul(table_rows - 1, stride, "table displacement"), "last table row")
    checked_add_u64(end, row_bytes, "table end")
    period = 4096 // gcd(stride, 4096)
    counts = set()
    for index in range(min(table_rows, period)):
        counted = count_segment((base + index * stride) % 4096, row_bytes, width, max_beats)
        counts.add((counted.bursts, counted.beats))
    return DescriptorCounts(
        rows, checked_mul(rows, max(count[0] for count in counts), "indexed bursts"),
        checked_mul(rows, max(count[1] for count in counts), "indexed beats"),
        checked_mul(rows, row_bytes, "indexed useful bytes"), len(counts) == 1,
    )

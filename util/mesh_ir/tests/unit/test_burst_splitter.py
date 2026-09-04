import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.burst_splitter import plan_descriptor, split_segment, validate_burst_invariants
from mesh_ir.model import MeshIrError

W = 32
L = 16


def test_single_byte_range():
    bursts = split_segment(0x100, 1, W, L)
    assert len(bursts) == 1
    assert bursts[0].beats == 1
    assert bursts[0].useful_bytes == 1
    assert bursts[0].beat_base == 0x100
    validate_burst_invariants(bursts, 1)


def test_width_minus_one_does_not_extra_split():
    bursts = split_segment(0, W - 1, W, L)
    assert len(bursts) == 1
    assert bursts[0].beats == 1


def test_width_plus_one_needs_two_beats_single_burst():
    bursts = split_segment(0, W + 1, W, L)
    assert len(bursts) == 1
    assert bursts[0].beats == 2
    assert sum(b.useful_bytes for b in bursts) == W + 1


def test_unaligned_start_head_lane_accounting():
    bursts = split_segment(0x5, 100, W, L)
    assert bursts[0].beat_base == 0x0
    assert bursts[0].logical_start == 0x5
    total = sum(b.useful_bytes for b in bursts)
    assert total == 100
    validate_burst_invariants(bursts, 100)


def test_4k_boundary_split():
    bursts = split_segment(0xFF0, 64, W, L)
    assert len(bursts) == 2
    for burst in bursts:
        assert (burst.beat_base >> 12) == ((burst.beat_base + burst.beats * W - 1) >> 12)
    assert sum(b.useful_bytes for b in bursts) == 64


def test_max_burst_cap_split():
    bursts = split_segment(0, L * W + 88, W, L)
    assert bursts[0].beats == L
    assert bursts[0].useful_bytes == L * W
    assert bursts[1].beats == 3
    assert sum(b.useful_bytes for b in bursts) == L * W + 88


def test_head_reduces_first_burst_capacity():
    bursts = split_segment(0x10, 512, W, L)
    assert bursts[0].beats == L
    assert bursts[0].useful_bytes == L * W - 0x10
    assert bursts[1].beats == 1
    validate_burst_invariants(bursts, 512)


def test_two_dimensional_descriptor_plan():
    plan = plan_descriptor(100, 3, 0x200, 128, W, L)
    assert plan.segments == 3
    assert plan.useful_bytes == 300
    row_bursts = [b for b in plan.bursts if b.logical_start in (0x200, 0x280, 0x300)]
    assert len(row_bursts) == 3
    validate_burst_invariants(plan.bursts, 300)


def test_max_burst_minus_and_plus_one():
    plan_minus = plan_descriptor((L - 1) * W, 1, 0, (L - 1) * W, W, L)
    assert len(plan_minus.bursts) == 1
    plan_plus = plan_descriptor((L + 1) * W, 1, 0, (L + 1) * W, W, L)
    assert len(plan_plus.bursts) == 2


def test_useful_conservation_is_enforced():
    with pytest.raises(MeshIrError):
        validate_burst_invariants(split_segment(0, 64, W, L), 65)

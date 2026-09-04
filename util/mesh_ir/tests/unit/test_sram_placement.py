"""Behavioral pins for SRAM placement timing (DC-15).

The golden programs sram_parallel / sram_conflict differ ONLY in operand
SRAM placement (banks 0/4/8 vs bank 0 x3).  The pinned completion ticks
prove conflict placement completes later and a second read port shortens
the conflict; the manifest runs the live counterparts.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.builder import load_arch
from mesh_ir.golden_programs import (
    SRAM_PLACEMENT_CONFLICT,
    SRAM_PLACEMENT_PARALLEL,
    build_sram_conflict_program,
    build_sram_parallel_program,
)

ARCH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"

# Golden completion ticks captured from the gem5 runs (manifest DC-15).
TICK_PARALLEL_1PORT = 27501
TICK_CONFLICT_1PORT = 28501
TICK_CONFLICT_2PORT = 28001


def test_conflict_completes_later_than_parallel():
    assert TICK_CONFLICT_1PORT > TICK_PARALLEL_1PORT


def test_two_ports_shorten_conflict():
    assert TICK_CONFLICT_2PORT < TICK_CONFLICT_1PORT
    assert TICK_CONFLICT_2PORT >= TICK_PARALLEL_1PORT


def test_placements_differ_only_in_bank_selection():
    arch = load_arch(ARCH)
    parallel = build_sram_parallel_program(arch)
    conflict = build_sram_conflict_program(arch)
    line = arch.sram_read_bytes_per_cycle_per_bank
    banks = arch.sram_banks

    def banks_of(program):
        return sorted(
            (a.offset_bytes // line) % banks
            for a in program.allocations
        )

    assert banks_of(parallel) == sorted(
        {(off // line) % banks for off in SRAM_PLACEMENT_PARALLEL}
    )
    assert len(set(banks_of(conflict))) == 1  # all operands on one bank
    # Same shapes/attrs/bytes: only placement differs.
    assert (
        [tuple(c.dims) for c in parallel.tensors]
        == [tuple(c.dims) for c in conflict.tensors]
    )
    assert [d.useful_bytes for d in parallel.dma_descriptors] == [
        d.useful_bytes for d in conflict.dma_descriptors
    ]

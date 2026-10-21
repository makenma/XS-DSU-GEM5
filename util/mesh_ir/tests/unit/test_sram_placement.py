import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.architecture import load_arch
from mesh_ir.golden_programs import (
    SRAM_PLACEMENT_CONFLICT,
    SRAM_PLACEMENT_PARALLEL,
    build_sram_conflict_program,
    build_sram_parallel_program,
)

ARCH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"

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
    assert len(set(banks_of(conflict))) == 1
    assert (
        [tuple(c.dims) for c in parallel.tensors]
        == [tuple(c.dims) for c in conflict.tensors]
    )
    assert [d.useful_bytes for d in parallel.dma_descriptors] == [
        d.useful_bytes for d in conflict.dma_descriptors
    ]

    occupied = [
        {(offset // arch.sram_bank_interleave_bytes) % banks
         for offset in range(item.offset_bytes, item.offset_bytes + item.size_bytes)}
        for item in parallel.allocations
    ]
    assert all(not left & right for index, left in enumerate(occupied) for right in occupied[index + 1:])
    assert {item.size_bytes for item in parallel.allocations} == {128}

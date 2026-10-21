"""The reported SRAM allocation peak is the planner's high-water mark.

``workload_statistics._peak_allocation_bytes`` projects the admitted
ALLOCATIONS table with the same formula ``analysis/sram.py`` uses for
``SramCoreReport.peak_bytes``; on a carrier whose lifetimes legally reuse
addresses, the projection must equal the planner's own report instead of
the sum of allocation sizes.
"""

from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.architecture import load_arch
from mesh_ir.workload_statistics import _peak_allocation_bytes

from tests.unit.test_gate2_dependency_sram import weight_scratch_kernel

ARCH = __import__("pathlib").Path(__file__).resolve().parents[2] / \
    "../../configs/example/ai_mesh/arch/mesh_2x2.yaml"


def _sections_of(allocations):
    return {"ALLOCATIONS": [
        {"allocation_id": allocation.allocation_id,
         "owner_core": allocation.owner_core,
         "memory_space": 3,
         "offset_bytes": allocation.offset_bytes,
         "size_bytes": allocation.size_bytes}
        for allocation in allocations]}


def test_peak_projection_equals_the_planner_high_water():
    arch = load_arch(ARCH)
    for scratch_first in (False, True):
        records = weight_scratch_kernel(arch, scratch_first).memory_records()
        plan = plan_memory_sram(records, arch)
        sections = _sections_of(plan.allocations)
        for report in plan.reports:
            projected = _peak_allocation_bytes(sections, report.owner_core)
            assert projected == report.peak_bytes, (
                scratch_first, report.owner_core, projected,
                report.peak_bytes)

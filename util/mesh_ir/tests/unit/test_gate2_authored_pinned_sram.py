import pytest

from mesh_ir.analysis.sram import SramAllocation, plan_memory_sram
from mesh_ir.diagnostics import MeshIrError
from tests.unit.test_gate2_dependency_sram import concurrent_load_kernel, local_arch, weight_scratch_kernel


def test_explicit_pins_preserve_ids_offsets_and_safe_lifetime_reuse():
    arch = local_arch(axi_data_bytes=8)
    records = weight_scratch_kernel(arch, False).memory_records()
    pins = (
        SramAllocation(1, 1, arch.core_ids[0], 0, 16, 8),
        SramAllocation(2, 2, arch.core_ids[0], 16, 16, 8),
        SramAllocation(3, 3, arch.core_ids[0], 0, 16, 8),
    )

    plan = plan_memory_sram(records, arch, allocations=pins)

    assert plan.allocations == pins
    assert plan.reports[0].peak_bytes == 32
    assert plan.reports[0].fragmentation_bytes == 0
    assert {(item.first_object_id, item.second_object_id) for item in plan.conflict_witnesses} == {(1, 2)}


def test_explicit_pins_reject_unordered_overlap():
    arch = local_arch(axi_data_bytes=8)
    records = concurrent_load_kernel(arch).memory_records()
    pins = (
        SramAllocation(1, 2, arch.core_ids[0], 0, 16, 8),
        SramAllocation(2, 4, arch.core_ids[0], 0, 16, 8),
    )

    with pytest.raises(MeshIrError) as caught:
        plan_memory_sram(records, arch, allocations=pins)

    assert caught.value.code == "E_SRAM_OOM"


@pytest.mark.parametrize(
    "pins",
    (
        (),
        (SramAllocation(2, 2, 0, 0, 16, 8), SramAllocation(1, 4, 0, 16, 16, 8)),
        (SramAllocation(1, 2, 0, 0, 16, 8), SramAllocation(2, 2, 0, 16, 16, 8)),
        (SramAllocation(1, 2, 0, 1, 16, 8), SramAllocation(2, 4, 0, 24, 16, 8)),
        (SramAllocation(1, 2, 0, 0, 15, 8), SramAllocation(2, 4, 0, 16, 16, 8)),
        (SramAllocation(1, 2, 0, 0, 16, 4), SramAllocation(2, 4, 0, 16, 16, 8)),
        (SramAllocation(1, [], 0, 0, 16, 8), SramAllocation(2, 4, 0, 16, 16, 8)),
        (SramAllocation(True, 2, 0, 0, 16, 8), SramAllocation(2, 4, 0, 16, 16, 8)),
    ),
)
def test_explicit_pins_reject_incomplete_or_invalid_allocation_facts(pins):
    arch = local_arch(axi_data_bytes=8)
    records = concurrent_load_kernel(arch).memory_records()

    with pytest.raises(MeshIrError) as caught:
        plan_memory_sram(records, arch, allocations=pins)

    assert caught.value.code in ("E_ABI_BOUNDS", "E_SRAM_OOM")


def test_explicit_pins_do_not_mutate_the_caller_tuple():
    arch = local_arch(axi_data_bytes=8)
    records = concurrent_load_kernel(arch).memory_records()
    pins = (
        SramAllocation(1, 2, arch.core_ids[0], 32, 16, 8),
        SramAllocation(2, 4, arch.core_ids[0], 64, 16, 8),
    )
    before = pins

    plan_memory_sram(records, arch, allocations=pins)

    assert pins == before

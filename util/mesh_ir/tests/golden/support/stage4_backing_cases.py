from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.architecture import ArchManifest
from mesh_ir.builder import ProgramBuilder
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, INVALID_CORE_ID, MemorySpace
from mesh_ir.model import Program
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin,
    ExternalSlotBacking,
    HaltAttrs,
    ObjectBacking,
    RequestBeginAttrs,
    RequestEndAttrs,
)
from mesh_ir.traffic import Binding, BindingSlot
from tests.golden.support.stage3_intrinsic_cases import build_complete_program
from tests.unit.test_gate2_kernel_ir import load_compute_store_kernel, real_gemm_kernel


def build_persistent_authored_program(arch: ArchManifest) -> Program:
    return build_complete_program(
        arch,
        real_gemm_kernel().memory_records(),
        "stage4-persistent-authored",
    )


def build_two_variant_shared_symbol_program(arch: ArchManifest) -> Program:
    region_id = next(index for index, region in enumerate(arch.regions) if region.kind == "HBM")
    input_binding = Binding(1, region_id, INVALID_CORE_ID, 0, 32, 32, Access.READ_ONLY)
    output_binding = Binding(2, region_id, INVALID_CORE_ID, 64, 32, 32, Access.READ_WRITE)
    records = load_compute_store_kernel().memory_records()
    backings = (
        ObjectBacking(1, ExternalSlotBacking(1)),
        ObjectBacking(4, ExternalSlotBacking(2)),
    )
    slots = (
        BindingSlot(1, "stage4:shared-input", MemorySpace.HBM, region_id, INVALID_CORE_ID, 16, 32, Access.READ_ONLY, input_binding),
        BindingSlot(2, "stage4:shared-output", MemorySpace.HBM, region_id, INVALID_CORE_ID, 16, 32, Access.READ_WRITE, output_binding),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage4-shared-symbol", 1))
    for profile_id in ("stage4-symbol-one", "stage4-symbol-two"):
        variant = builder.variant(
            "main",
            profile_id,
            profile_id,
            records=records,
            allocations=plan_memory_sram(records, arch).allocations,
            external_backings=backings,
            binding_slots=slots,
        )
        stream = variant.stream(3, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
        stream.control_command(RequestBeginAttrs())
        stream.kernel_command(9)
        stream.kernel_command(10)
        stream.kernel_command(11)
        stream.control_command(RequestEndAttrs())
        stream.control_command(HaltAttrs())
    return builder.build()

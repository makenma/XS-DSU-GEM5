from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.builder import ProgramBuilder
from mesh_ir.generated import abi as A
from mesh_ir.scheduled.model import AuthoredProgramOrigin, HaltAttrs, RequestBeginAttrs, RequestEndAttrs
from tests.unit.test_gate2_dependency_sram import weight_scratch_kernel


def build_sram_reuse_program(arch):
    records = weight_scratch_kernel(arch, False).memory_records()
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage2-sram-reuse", 1))
    variant = builder.variant(
        "main",
        "stage2-sram-reuse",
        "stage2-sram-reuse",
        records=records,
        allocations=plan_memory_sram(records, arch).allocations,
    )
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(7)
    stream.kernel_command(8)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()

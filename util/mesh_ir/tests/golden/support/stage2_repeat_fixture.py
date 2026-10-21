from dataclasses import replace
from pathlib import Path

from mesh_ir.analysis.sram import SramAllocation
from mesh_ir.architecture import load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, INVALID_CORE_ID, MemorySpace
from mesh_ir.ir.kernel_ir import KernelMemoryRecords
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ControlCommandSource, EventSignalAttrs, EventWaitAttrs, ExternalSlotBacking, HaltAttrs, LifecycleSource, ObjectBacking, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs, ScheduledDependencyKind
from mesh_ir.traffic import Binding, BindingSlot, calculate_traffic
from tests.unit.test_gate2_kernel_ir import load_compute_store_kernel


ROOT = Path(__file__).resolve().parents[5]


def build_mixed_engine_repeat_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    records = load_compute_store_kernel().memory_records()
    allocations = (
        SramAllocation(1, 2, 3, 0, 32, arch.sram_base_alignment_bytes),
        SramAllocation(2, 3, 3, arch.sram_base_alignment_bytes, 32, arch.sram_base_alignment_bytes),
    )
    region_id = next(index for index, item in enumerate(arch.regions) if item.kind == "HBM")
    input_binding = Binding(1, region_id, INVALID_CORE_ID, 0, 32, 32, Access.READ_ONLY)
    output_binding = Binding(2, region_id, INVALID_CORE_ID, 64, 32, 32, Access.READ_WRITE)
    slots = (
        BindingSlot(1, "input", MemorySpace.HBM, region_id, INVALID_CORE_ID, 16, 32, Access.READ_ONLY, input_binding),
        BindingSlot(2, "output", MemorySpace.HBM, region_id, INVALID_CORE_ID, 16, 32, Access.READ_WRITE, output_binding),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "mixed-engine-repeat", 1))
    variant = builder.variant(
        "forward",
        "p4",
        "mixed-engine-repeat",
        records=records,
        allocations=allocations,
        external_backings=(ObjectBacking(1, ExternalSlotBacking(1)), ObjectBacking(4, ExternalSlotBacking(2))),
        binding_slots=slots,
    )
    stream = variant.stream(3, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.kernel_command(9)
    stream.kernel_command(10)
    stream.kernel_command(11)
    stream.control_command(RepeatCommandAttrs(1, 3, 3))
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return arch, builder.build()


def with_repeat_range(program, arch, begin, count, command_id=None, recompute_traffic=True):
    semantic_index = next(
        index
        for index, item in enumerate(program.semantics.command_semantics)
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is RepeatCommandAttrs
        and (command_id is None or item.command_id == command_id)
    )
    command_id = program.semantics.command_semantics[semantic_index].command_id
    repeat_count = program.semantics.command_semantics[semantic_index].source.attrs.repeat_count
    source = ControlCommandSource(RepeatCommandAttrs(begin, count, repeat_count))
    command_semantics = tuple(replace(item, source=source) if item.command_id == command_id else item for item in program.semantics.command_semantics)
    semantics = replace(program.semantics, command_semantics=command_semantics)
    attr_index = program.commands[command_id - 1].attr_index - 1
    attr = replace(program.op_attrs[attr_index], payload=(begin, count, repeat_count, 0))
    op_attrs = program.op_attrs[:attr_index] + (attr,) + program.op_attrs[attr_index + 1:]
    provisional = replace(program, op_attrs=op_attrs, expected_traffic=(), semantics=semantics, semantic_sha256="")
    if not recompute_traffic:
        provisional = replace(provisional, expected_traffic=program.expected_traffic)
        return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))
    executions, identity = derive_descriptor_execution_set(provisional, arch)
    report = calculate_traffic(arch, identity, executions)
    semantics = replace(semantics, reference_binding_identity_sha256=identity, intrinsic_traffic=report)
    provisional = replace(provisional, semantics=semantics, expected_traffic=_expected_traffic(report, executions, arch))
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def build_control_repeat_event_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    records = KernelMemoryRecords((), (), (), (), (), (), (), (), (), ())
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "control-repeat-event", 1))
    variant = builder.variant("main", "control-repeat-event", "control-repeat-event", records=records, allocations=())
    lifecycle = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    worker = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    lifecycle.control_command(RequestBeginAttrs())
    lifecycle.control_command(EventSignalAttrs(1))
    lifecycle.control_command(EventSignalAttrs(2))
    lifecycle.control_command(RepeatCommandAttrs(1, 2, 2))
    lifecycle.control_command(EventSignalAttrs(3))
    lifecycle.control_command(RequestEndAttrs())
    lifecycle.control_command(HaltAttrs())
    worker.control_command(EventWaitAttrs(3))
    worker.control_command(HaltAttrs())
    return arch, builder.build()


def build_disjoint_control_repeat_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    records = KernelMemoryRecords((), (), (), (), (), (), (), (), (), ())
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "disjoint-control-repeat", 1))
    variant = builder.variant("main", "disjoint-control-repeat", "disjoint-control-repeat", records=records, allocations=())
    stream = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    stream.control_command(RequestBeginAttrs())
    stream.control_command(EventSignalAttrs(1))
    stream.control_command(EventSignalAttrs(2))
    stream.control_command(RepeatCommandAttrs(1, 2, 2))
    stream.control_command(EventSignalAttrs(3))
    stream.control_command(EventSignalAttrs(4))
    stream.control_command(RepeatCommandAttrs(4, 2, 2))
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return arch, builder.build()


def with_repeat_event_escape(program):
    body_signal = next(
        item
        for item in program.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is EventSignalAttrs
        and item.source.attrs.event_id == 1
    )
    completion_signal = next(
        item
        for item in program.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is EventSignalAttrs
        and item.source.attrs.event_id == 3
    )
    waiter = next(
        item
        for item in program.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is EventWaitAttrs
        and item.source.attrs.event_id == 3
    )
    command = program.commands[waiter.command_id - 1]
    wait_index = next(
        index
        for index, item in enumerate(program.command_waits)
        if command.wait_begin <= index < command.wait_begin + command.wait_count
        and item.event_id == 3
    )
    command_semantics = tuple(
        replace(item, source=ControlCommandSource(EventWaitAttrs(1)))
        if item.command_id == waiter.command_id else item
        for item in program.semantics.command_semantics
    )
    dependencies = tuple(
        replace(item, source_command_id=body_signal.command_id)
        if item.source_command_id == completion_signal.command_id
        and item.target_command_id == waiter.command_id
        and item.kind is ScheduledDependencyKind.LIFECYCLE
        and type(item.source) is LifecycleSource
        else item
        for item in program.semantics.dependencies
    )
    provisional = replace(
        program,
        command_waits=(
            *program.command_waits[:wait_index],
            replace(program.command_waits[wait_index], event_id=1),
            *program.command_waits[wait_index + 1:],
        ),
        semantics=replace(
            program.semantics,
            command_semantics=command_semantics,
            dependencies=dependencies,
        ),
        semantic_sha256="",
    )
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.generated import abi as A
from mesh_ir.ir.kernel_ir import BarrierAttrs, ControlToken, KernelMemoryRecords, KernelOp, KernelOpcode
from mesh_ir.model import Program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ControlCommandSource, EventSignalAttrs, EventWaitAttrs, HaltAttrs, KernelCommandSource, KernelTokenSource, RequestBeginAttrs, RequestEndAttrs, ScheduledDependencyKind


ROOT = Path(__file__).resolve().parents[5]


@dataclass(frozen=True)
class EventFaninFixture:
    arch: ArchManifest
    program: Program
    variant_id: int
    commands: Mapping[str, int]
    events: Mapping[str, int]
    operations: Mapping[str, int]
    tokens: Mapping[str, int]
    dependencies: Mapping[str, int]
    barrier_group_id: int


def build_event_fanin_fixture() -> EventFaninFixture:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    participants = (0, 1)
    records = KernelMemoryRecords(
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        tuple(ControlToken(index) for index in range(1, 5)),
        (
            KernelOp(1, 0, "barrier:arrive:0", KernelOpcode.BARRIER, 0, 0, (), (), BarrierAttrs(participants), (), 1),
            KernelOp(2, 0, "barrier:arrive:1", KernelOpcode.BARRIER, 1, 0, (), (), BarrierAttrs(participants), (), 2),
            KernelOp(3, 0, "barrier:wait:0", KernelOpcode.BARRIER, 0, 0, (), (), BarrierAttrs(participants), (1, 2), 3),
            KernelOp(4, 0, "barrier:wait:1", KernelOpcode.BARRIER, 1, 0, (), (), BarrierAttrs(participants), (1, 2), 4),
        ),
    )
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "stage2-event-fanin", 1))
    variant = builder.variant("main", "stage2-event-fanin", "stage2-event-fanin", records=records, allocations=())
    lifecycle = variant.stream(0, 0, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    worker = variant.stream(1, 0, A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    lifecycle.control_command(RequestBeginAttrs())
    lifecycle.control_command(EventSignalAttrs(1))
    lifecycle.control_command(EventSignalAttrs(2))
    lifecycle.control_command(EventWaitAttrs(1))
    lifecycle.kernel_command(1)
    lifecycle.kernel_command(3)
    lifecycle.control_command(RequestEndAttrs())
    lifecycle.control_command(HaltAttrs())
    worker.kernel_command(2)
    worker.kernel_command(4)
    worker.control_command(HaltAttrs())
    program = builder.build()
    command_semantics = {item.command_id: item for item in program.semantics.command_semantics}
    command_ids = {
        "begin": next(item.command_id for item in command_semantics.values() if type(item.source) is ControlCommandSource and type(item.source.attrs) is RequestBeginAttrs),
        "normal_signal_first": next(item.command_id for item in command_semantics.values() if type(item.source) is ControlCommandSource and type(item.source.attrs) is EventSignalAttrs and item.source.attrs.event_id == 1),
        "normal_signal_second": next(item.command_id for item in command_semantics.values() if type(item.source) is ControlCommandSource and type(item.source.attrs) is EventSignalAttrs and item.source.attrs.event_id == 2),
        "normal_waiter": next(item.command_id for item in command_semantics.values() if type(item.source) is ControlCommandSource and type(item.source.attrs) is EventWaitAttrs),
        "barrier_arrival_core0": next(item.command_id for item in command_semantics.values() if type(item.source) is KernelCommandSource and item.source.kernel_op_id == 1),
        "barrier_arrival_core1": next(item.command_id for item in command_semantics.values() if type(item.source) is KernelCommandSource and item.source.kernel_op_id == 2),
        "barrier_waiter_core0": next(item.command_id for item in command_semantics.values() if type(item.source) is KernelCommandSource and item.source.kernel_op_id == 3),
        "barrier_waiter_core1": next(item.command_id for item in command_semantics.values() if type(item.source) is KernelCommandSource and item.source.kernel_op_id == 4),
        "end": next(item.command_id for item in command_semantics.values() if type(item.source) is ControlCommandSource and type(item.source.attrs) is RequestEndAttrs),
    }
    command_ids["halt"] = next(item.command_id for item in command_semantics.values() if type(item.source) is ControlCommandSource and type(item.source.attrs) is HaltAttrs and program.commands[item.command_id - 1].core_id == 0)
    operations = MappingProxyType({
        "barrier_arrival_core0": 1,
        "barrier_arrival_core1": 2,
        "barrier_waiter_core0": 3,
        "barrier_waiter_core1": 4,
    })
    tokens = MappingProxyType({
        "barrier_arrival_core0": 1,
        "barrier_arrival_core1": 2,
        "barrier_waiter_core0": 3,
        "barrier_waiter_core1": 4,
    })
    groups = {item.barrier_group_id: item for item in program.semantics.barrier_groups}
    barrier_arrivals = next(item for item in groups.values() if tuple(arrival.kernel_op_id for arrival in item.arrivals) == (1, 2))
    barrier_completion = next(item for item in groups.values() if tuple(arrival.kernel_op_id for arrival in item.arrivals) == (3, 4))
    events = MappingProxyType({
        "normal_signal_first": next(item.event_id for item in program.events if item.producer_command_id == command_ids["normal_signal_first"]),
        "normal_signal_second": next(item.event_id for item in program.events if item.producer_command_id == command_ids["normal_signal_second"]),
        "barrier_arrivals": barrier_arrivals.completion_event_id,
        "barrier_completion": barrier_completion.completion_event_id,
    })
    dependencies = {}
    for source_name in ("barrier_arrival_core0", "barrier_arrival_core1"):
        for target_name in ("barrier_waiter_core0", "barrier_waiter_core1"):
            source_command = command_ids[source_name]
            target_command = command_ids[target_name]
            token_id = tokens[source_name]
            dependency = next(
                item
                for item in program.semantics.dependencies
                if item.source_command_id == source_command
                and item.target_command_id == target_command
                and item.kind is ScheduledDependencyKind.KERNEL_CONTROL
                and type(item.source) is KernelTokenSource
                and item.source.token_id == token_id
            )
            dependencies[f"{source_name}_to_{target_name.removeprefix('barrier_')}"] = dependency.dependency_id
    dependencies["normal_signal_to_waiter"] = next(
        item.dependency_id
        for item in program.semantics.dependencies
        if item.source_command_id == command_ids["normal_signal_first"]
        and item.target_command_id == command_ids["normal_waiter"]
        and item.kind is ScheduledDependencyKind.LIFECYCLE
    )
    return EventFaninFixture(
        arch,
        program,
        1,
        MappingProxyType(command_ids),
        events,
        operations,
        tokens,
        MappingProxyType(dependencies),
        barrier_arrivals.barrier_group_id,
    )

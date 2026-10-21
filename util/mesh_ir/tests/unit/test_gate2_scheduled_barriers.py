from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Engine
from mesh_ir.ir.kernel_ir import BarrierAttrs, ControlToken, KernelOp, KernelOpcode
from mesh_ir.model import Command, CommandWait, Entrypoint, Event, Profile, Stream, StringEntry
from mesh_ir.scheduled.assemble import _PreTrafficSemantics, _PreTrafficState, _TransportSections, assemble_program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AuthoredVariantLineage, BarrierArrival, BarrierExecution, BarrierGroup, CommandSemantics, ControlCommandSource, ControlExecution, HaltAttrs, IdSpan, KernelCommandSource, KernelTokenSource, LifecycleSource, ProgramVariant, RequestBeginAttrs, RequestEndAttrs, ScheduledDependency, ScheduledDependencyKind, ScheduledStream, VariantMembership
from mesh_ir.scheduled.verify import verify_pretraffic_state, verify_program


ROOT = Path(__file__).resolve().parents[4]


def _state(arch):
    z = IdSpan(1, 0)
    membership = VariantMembership(z, z, z, IdSpan(1, 2), IdSpan(1, 8), IdSpan(1, 4), z, z, z, z, z, z, z, z, z, z, IdSpan(1, 4), IdSpan(1, 4), z, IdSpan(1, 8), IdSpan(1, 2), IdSpan(1, 10), z, z)
    flags = A.STREAM_FLAGS.IS_LOCAL_CONTROL
    commands = (
        Command(1, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_BEGIN, signal_event=1),
        Command(2, 1, 0, 0, A.ENGINE.CONTROL, A.OPCODE.BARRIER, 0, 1, signal_event=2),
        Command(3, 2, 1, 0, A.ENGINE.CONTROL, A.OPCODE.BARRIER, 1, 1, signal_event=2),
        Command(4, 3, 0, 0, A.ENGINE.CONTROL, A.OPCODE.BARRIER, 2, 1, signal_event=3),
        Command(5, 4, 1, 0, A.ENGINE.CONTROL, A.OPCODE.BARRIER, 3, 1, signal_event=3),
        Command(6, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_END, 4, 1, signal_event=4),
        Command(7, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.HALT, 5, 1),
        Command(8, 0, 1, 0, A.ENGINE.CONTROL, A.OPCODE.HALT, 6, 1),
    )
    waits = (CommandWait(1), CommandWait(1), CommandWait(2), CommandWait(2), CommandWait(3), CommandWait(4), CommandWait(4))
    events = (Event(1, A.EVENT_KIND.NORMAL, producer_command_id=1), Event(2, A.EVENT_KIND.BARRIER, expected_arrivals=2), Event(3, A.EVENT_KIND.BARRIER, expected_arrivals=2), Event(4, A.EVENT_KIND.NORMAL, producer_command_id=6))
    transport = _TransportSections((StringEntry("main"), StringEntry("default")), (Entrypoint(1, 1, 0, 1, 0, 0),), (Profile(1, 1, 2, 0),), (), (), (), (Stream(0, 0, 0, 5, flags | A.STREAM_FLAGS.IS_LIFECYCLE), Stream(1, 0, 5, 3, flags)), commands, waits, (), events, (), (), ())
    kernel_ops = (
        KernelOp(1, 0, "barrier:1:0", KernelOpcode.BARRIER, 0, 0, (), (), BarrierAttrs((0, 1)), (), 1),
        KernelOp(2, 0, "barrier:1:1", KernelOpcode.BARRIER, 1, 0, (), (), BarrierAttrs((0, 1)), (), 2),
        KernelOp(3, 0, "barrier:2:0", KernelOpcode.BARRIER, 0, 0, (), (), BarrierAttrs((0, 1)), (1, 2), 3),
        KernelOp(4, 0, "barrier:2:1", KernelOpcode.BARRIER, 1, 0, (), (), BarrierAttrs((0, 1)), (1, 2), 4),
    )
    command_semantics = (
        CommandSemantics(1, ControlCommandSource(RequestBeginAttrs()), ControlExecution()),
        CommandSemantics(2, KernelCommandSource(1), BarrierExecution(1)),
        CommandSemantics(3, KernelCommandSource(2), BarrierExecution(1)),
        CommandSemantics(4, KernelCommandSource(3), BarrierExecution(2)),
        CommandSemantics(5, KernelCommandSource(4), BarrierExecution(2)),
        CommandSemantics(6, ControlCommandSource(RequestEndAttrs()), ControlExecution()),
        CommandSemantics(7, ControlCommandSource(HaltAttrs()), ControlExecution()),
        CommandSemantics(8, ControlCommandSource(HaltAttrs()), ControlExecution()),
    )
    groups = (
        BarrierGroup(1, (0, 1), (BarrierArrival(0, 1, 2, 1), BarrierArrival(1, 2, 3, 2)), 2),
        BarrierGroup(2, (0, 1), (BarrierArrival(0, 3, 4, 3), BarrierArrival(1, 4, 5, 4)), 3),
    )
    pairs = ((1, 2), (1, 3), (2, 4), (3, 4), (2, 5), (3, 5), (4, 6), (5, 6), (6, 7), (6, 8))
    dependencies = tuple(ScheduledDependency(index, source, target, ScheduledDependencyKind.LIFECYCLE if source in (1, 6) else ScheduledDependencyKind.KERNEL_CONTROL, LifecycleSource(1) if source in (1, 6) else KernelTokenSource(source - 1),) for index, (source, target) in enumerate(pairs, 1))
    semantics = _PreTrafficSemantics(AuthoredProgramOrigin("unit", "barriers", 1), (ProgramVariant(1, 1, 1, AuthoredVariantLineage("barriers"), 1, membership),), (), (), (), (), (), (), (), (), (ControlToken(1), ControlToken(2), ControlToken(3), ControlToken(4)), kernel_ops, (), (), command_semantics, groups, dependencies, (ScheduledStream(1, 0, 0, 0, 5, flags | A.STREAM_FLAGS.IS_LIFECYCLE), ScheduledStream(2, 1, 0, 5, 3, flags)), (1, 2, 4, 6, 7, 3, 5, 8), (), (), (), semantic_sha256({"reference_bindings": []}), ())
    return _PreTrafficState(A.ABI_MAJOR, A.ABI_MINOR, arch.digest(), transport, semantics)


def test_successive_equal_participant_barriers_keep_distinct_groups_and_events():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)

    assert tuple((item.barrier_group_id, item.completion_event_id) for item in program.semantics.barrier_groups) == ((1, 2), (2, 3))


def test_equal_participant_barriers_cannot_share_one_completion_event():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    forged_groups = (program.semantics.barrier_groups[0], replace(program.semantics.barrier_groups[1], completion_event_id=2))
    provisional = replace(program, semantics=replace(program.semantics, barrier_groups=forged_groups), semantic_sha256="")
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_EVENT_MULTIPLE_PRODUCERS"

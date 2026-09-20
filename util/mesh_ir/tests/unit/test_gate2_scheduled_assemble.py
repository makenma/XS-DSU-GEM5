from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Engine
from mesh_ir.model import Command, CommandWait, Entrypoint, Event, OpAttr, Profile, Stream, StringEntry
from mesh_ir.scheduled.assemble import _PreTrafficSemantics, _PreTrafficState, _TransportSections, assemble_program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AuthoredVariantLineage, AxiFenceAttrs, CommandSemantics, ControlCommandSource, ControlExecution, EventWaitAttrs, FenceScope, HaltAttrs, IdSpan, LifecycleSource, ProgramVariant, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs, ScheduledDependency, ScheduledDependencyKind, ScheduledStream, VariantMembership
from mesh_ir.scheduled.verify import verify_pretraffic_state, verify_program


ROOT = Path(__file__).resolve().parents[4]


def _span(first_id, count):
    return IdSpan(first_id, count)


def _state(arch):
    membership = VariantMembership(
        _span(1, 0), _span(1, 0), _span(1, 0), _span(1, 1),
        _span(1, 3), _span(1, 2), _span(1, 0), _span(1, 0),
        _span(1, 0), _span(1, 0), _span(1, 0), _span(1, 0),
        _span(1, 0), _span(1, 0), _span(1, 0), _span(1, 0),
        _span(1, 0), _span(1, 0), _span(1, 0), _span(1, 3),
        _span(1, 0), _span(1, 2), _span(1, 0), _span(1, 0),
    )
    transport = _TransportSections(
        strings=(StringEntry("main"), StringEntry("default")),
        entrypoints=(Entrypoint(1, 1, 0, 1, 0, 0),),
        profiles=(Profile(1, 1, 2, 0),),
        tensors=(), shards=(), allocations=(),
        streams=(Stream(0, 0, 0, 3, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL),),
        commands=(
            Command(1, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_BEGIN, signal_event=1),
            Command(2, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_END, wait_begin=0, wait_count=1, signal_event=2),
            Command(3, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.HALT, wait_begin=1, wait_count=1),
        ),
        command_waits=(CommandWait(1), CommandWait(2)),
        command_operands=(),
        events=(Event(1, A.EVENT_KIND.NORMAL, producer_command_id=1, expected_arrivals=1), Event(2, A.EVENT_KIND.NORMAL, producer_command_id=2, expected_arrivals=1)),
        dma_descriptors=(), op_attrs=(), relocations=(),
    )
    semantics = _PreTrafficSemantics(
        origin=AuthoredProgramOrigin("unit", "minimal", 1),
        variants=(ProgramVariant(1, 1, 1, AuthoredVariantLineage("minimal"), 1, membership),),
        kernel_tensors=(), computations=(), placements=(), logical_shards=(), partial_sums=(), objects=(), views=(), states=(), tokens=(), kernel_ops=(), object_backings=(), resident_views=(),
        command_semantics=(
            CommandSemantics(1, ControlCommandSource(RequestBeginAttrs()), ControlExecution()),
            CommandSemantics(2, ControlCommandSource(RequestEndAttrs()), ControlExecution()),
            CommandSemantics(3, ControlCommandSource(HaltAttrs()), ControlExecution()),
        ),
        barrier_groups=(),
        dependencies=(
            ScheduledDependency(1, 1, 2, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1)),
            ScheduledDependency(2, 2, 3, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1)),
        ),
        streams=(ScheduledStream(1, 0, 0, 0, 3, A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL),),
        stream_command_ids=(1, 2, 3), descriptor_groups=(), endpoint_uses=(), binding_slots=(),
        reference_binding_identity_sha256=semantic_sha256({"reference_bindings": []}),
        executions=(),
    )
    return _PreTrafficState(A.ABI_MAJOR, A.ABI_MINOR, arch.digest(), transport, semantics)


def _repeat_state(arch, repeat_count=3):
    state = _state(arch)
    commands = (
        Command(1, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_BEGIN, signal_event=1),
        Command(2, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.AXI_FENCE, 0, 1, signal_event=2, attr_index=1),
        Command(3, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REPEAT, 1, 1, signal_event=3, attr_index=2),
        Command(4, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_END, 2, 1, signal_event=4),
        Command(5, 0, 0, 0, A.ENGINE.CONTROL, A.OPCODE.HALT, 3, 1),
    )
    waits = (CommandWait(1), CommandWait(2), CommandWait(3), CommandWait(4))
    events = tuple(Event(index, A.EVENT_KIND.NORMAL, producer_command_id=index) for index in range(1, 5))
    attrs = (OpAttr(A.ATTR_KIND.FENCE_V1, payload=(A.FENCE_SCOPE.ALL_INSTANCE, 0, 0, 0, 0), payload_fields=("fence_scope", "reserved", "reserved0", "reserved1", "reserved2")), OpAttr(A.ATTR_KIND.REPEAT_V1, payload=(1, 1, repeat_count, 0), payload_fields=("subrange_begin_stream_ordinal", "subrange_command_count", "repeat_count", "flags")))
    transport = replace(state.transport, streams=(replace(state.transport.streams[0], command_count=5),), commands=commands, command_waits=waits, events=events, op_attrs=attrs)
    semantics = state.semantics
    membership = replace(semantics.variants[0].membership, commands=IdSpan(1, 5), events=IdSpan(1, 4), command_semantics=IdSpan(1, 5), dependencies=IdSpan(1, 4))
    command_semantics = (
        CommandSemantics(1, ControlCommandSource(RequestBeginAttrs()), ControlExecution()),
        CommandSemantics(2, ControlCommandSource(AxiFenceAttrs(FenceScope.ALL_INSTANCE)), ControlExecution()),
        CommandSemantics(3, ControlCommandSource(RepeatCommandAttrs(1, 1, repeat_count)), ControlExecution()),
        CommandSemantics(4, ControlCommandSource(RequestEndAttrs()), ControlExecution()),
        CommandSemantics(5, ControlCommandSource(HaltAttrs()), ControlExecution()),
    )
    dependencies = tuple(ScheduledDependency(index, index, index + 1, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1)) for index in range(1, 5))
    semantics = replace(semantics, variants=(replace(semantics.variants[0], membership=membership),), command_semantics=command_semantics, dependencies=dependencies, streams=(replace(semantics.streams[0], command_count=5),), stream_command_ids=(1, 2, 3, 4, 5))
    return replace(state, transport=transport, semantics=semantics)


def test_genuine_authored_lifecycle_uses_the_only_program_assembler():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)

    verified = verify_program(program, arch)

    assert verified.program is program
    assert program.semantic_sha256 == semantic_sha256(program.semantic_dict())
    assert program.semantics.intrinsic_traffic.descriptors == ()
    with pytest.raises(FrozenInstanceError):
        program.commands[0].command_id = 4


@pytest.mark.parametrize("rank", (True, -1, 9))
def test_profile_rank_is_an_exact_abi_capacity(rank):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    state = _state(arch)
    transport = replace(
        state.transport,
        profiles=(replace(state.transport.profiles[0], rank=rank),),
    )

    with pytest.raises(MeshIrError) as caught:
        verify_pretraffic_state(replace(state, transport=transport), arch)

    assert caught.value.code == "E_ABI_BOUNDS"


def test_complete_verifier_rejects_fresh_hash_with_interleaved_stream_slice():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    forged_semantics = replace(program.semantics, stream_command_ids=(1, 3, 2))
    provisional = replace(program, semantics=forged_semantics, semantic_sha256="")
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_STREAM_CONTRACT"


@pytest.mark.parametrize("field", ("wait_begin", "wait_count"))
@pytest.mark.parametrize("value", (0.0, None, True))
def test_control_wait_span_scalars_are_admitted_before_event_slicing(field, value):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    state = _state(arch)
    command = replace(state.transport.commands[1], opcode=A.OPCODE.EVENT_WAIT, **{field: value})
    transport = replace(state.transport, commands=(state.transport.commands[0], command, state.transport.commands[2]))
    semantic = replace(state.semantics.command_semantics[1], source=ControlCommandSource(EventWaitAttrs(1)))
    semantics = replace(state.semantics, command_semantics=(state.semantics.command_semantics[0], semantic, state.semantics.command_semantics[2]))

    with pytest.raises(MeshIrError) as caught:
        verify_pretraffic_state(replace(state, transport=transport, semantics=semantics), arch)

    assert caught.value.code == "E_ABI_BOUNDS"


def test_finite_repeat_accepts_generation_zero_and_positive_total_count():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")

    program = assemble_program(verify_pretraffic_state(_repeat_state(arch), arch), arch)

    assert program.semantics.command_semantics[2].source.attrs.repeat_count == 3


@pytest.mark.parametrize("count", [0, True, 1 << 32])
def test_finite_repeat_count_is_exact_positive_u32(count):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")

    with pytest.raises(MeshIrError) as caught:
        verify_pretraffic_state(_repeat_state(arch, count), arch)

    assert caught.value.code == "E_ABI_BOUNDS"

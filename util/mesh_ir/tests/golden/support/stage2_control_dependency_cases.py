from dataclasses import dataclass, replace

from mesh_ir.analysis.sram import plan_memory_sram
from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_dual_core_program, build_fence_scopes_program, build_p2p_reuse_program, build_repeat_program, build_single_core_program
from mesh_ir.ir.common import DmaKind, INVALID_CORE_ID
from mesh_ir.ir.kernel_ir import KernelOpcode, RecvWaitAttrs
from mesh_ir.model import Program
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AxiFenceAttrs, BarrierExecution, ControlCommandSource, ControlExecution, ComputeExecution, DmaExecution, EventSignalAttrs, EventWaitAttrs, HaltAttrs, KernelCommandSource, ObjectSource, RepeatCommandAttrs, RequestBeginAttrs, RequestEndAttrs, ScheduledDependencyKind
from mesh_ir.scheduled.verify import _local_memory_records
from mesh_ir.traffic import calculate_traffic
from tests.golden.support.stage2_control_fixture import ROOT, build_event_fanin_fixture
from tests.golden.support.stage2_repeat_fixture import build_control_repeat_event_program, build_disjoint_control_repeat_program, build_mixed_engine_repeat_program, with_repeat_event_escape, with_repeat_range
from tests.golden.support.stage2_sram_reuse_fixture import build_sram_reuse_program


@dataclass(frozen=True)
class Stage2ControlDependencyCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None


def _refreshed(program, **changes):
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def _with_semantic(program, command_id, **changes):
    semantics = tuple(
        replace(item, **changes) if item.command_id == command_id else item
        for item in program.semantics.command_semantics
    )
    return _refreshed(program, semantics=replace(program.semantics, command_semantics=semantics))


def _with_command(program, command_id, **changes):
    commands = tuple(
        replace(item, **changes) if item.command_id == command_id else item
        for item in program.commands
    )
    return _refreshed(program, commands=commands)


def _with_kernel_op(program, op_id, **changes):
    kernel_ops = tuple(
        replace(item, **changes) if item.op_id == op_id else item
        for item in program.semantics.kernel_ops
    )
    return _refreshed(program, semantics=replace(program.semantics, kernel_ops=kernel_ops))


def _with_command_operand(program, operand_index, **changes):
    operands = tuple(
        replace(item, **changes) if index == operand_index else item
        for index, item in enumerate(program.command_operands)
    )
    return _refreshed(program, command_operands=operands)


def _with_repeat_attrs(program, command_id, attrs):
    command = program.commands[command_id - 1]
    attr_index = command.attr_index - 1
    attr = program.op_attrs[attr_index]
    semantics = tuple(
        replace(item, source=ControlCommandSource(attrs)) if item.command_id == command_id else item
        for item in program.semantics.command_semantics
    )
    return _refreshed(
        program,
        op_attrs=(
            *program.op_attrs[:attr_index],
            replace(
                attr,
                payload=(
                    attrs.subrange_begin_stream_ordinal,
                    attrs.subrange_command_count,
                    attrs.repeat_count,
                    *attr.payload[3:],
                ),
            ),
            *program.op_attrs[attr_index + 1:],
        ),
        semantics=replace(program.semantics, command_semantics=semantics),
    )


def build_two_variant_p2p_reuse_program(arch: ArchManifest) -> Program:
    seed = build_p2p_reuse_program(arch)
    records = _local_memory_records(seed.semantics, seed.semantics.variants[0])
    shifted_records = replace(
        records,
        ops=tuple(
            replace(item, attrs=replace(item.attrs, transfer_id=item.attrs.transfer_id + 100))
            if item.opcode is KernelOpcode.DMA and item.attrs.kind is DmaKind.P2P_PUSH
            else replace(
                item,
                attrs=RecvWaitAttrs(
                    item.attrs.transfer_id + 100,
                    item.attrs.source_core,
                    item.attrs.destination_core,
                    item.attrs.expected_bytes,
                ),
            )
            if item.opcode is KernelOpcode.RECV_WAIT
            else item
            for item in records.ops
        ),
    )
    command_semantics = {
        item.command_id: item for item in seed.semantics.command_semantics
    }
    builder = ProgramBuilder(
        arch, AuthoredProgramOrigin("unit", "stage2-recv-variants", 1)
    )
    for profile_id, variant_records in (
        ("stage2-recv-one", records),
        ("stage2-recv-two", shifted_records),
    ):
        variant = builder.variant(
            "main",
            profile_id,
            profile_id,
            records=variant_records,
            allocations=plan_memory_sram(variant_records, arch).allocations,
        )
        for stream in seed.semantics.streams:
            target = variant.stream(
                stream.core_id, stream.physical_stream_id, stream.flags
            )
            for command_id in seed.semantics.stream_command_ids[
                stream.command_begin:stream.command_begin + stream.command_count
            ]:
                source = command_semantics[command_id].source
                if type(source) is ControlCommandSource:
                    target.control_command(source.attrs)
                else:
                    target.kernel_command(source.kernel_op_id)
    return builder.build()


def build_stage2_control_dependency_cases() -> tuple[Stage2ControlDependencyCase, ...]:
    control = build_event_fanin_fixture()
    signal = control.program.commands[control.commands["normal_signal_first"] - 1]
    changed_signal = _refreshed(
        control.program,
        commands=(
            *control.program.commands[:signal.command_id - 1],
            replace(signal, signal_event=control.events["normal_signal_second"]),
            *control.program.commands[signal.command_id:],
        ),
    )
    changed_control_engine = _refreshed(
        control.program,
        commands=(
            *control.program.commands[:signal.command_id - 1],
            replace(signal, engine=A.ENGINE.DMA_READ),
            *control.program.commands[signal.command_id:],
        ),
    )
    changed_control_operands = _refreshed(
        control.program,
        commands=(
            *control.program.commands[:signal.command_id - 1],
            replace(signal, operand_count=1),
            *control.program.commands[signal.command_id:],
        ),
    )
    changed_signal_zero = _with_semantic(
        control.program,
        signal.command_id,
        source=ControlCommandSource(EventSignalAttrs(0)),
    )
    changed_signal_large = _with_semantic(
        control.program,
        signal.command_id,
        source=ControlCommandSource(EventSignalAttrs(1 << 32)),
    )
    waiter = control.program.commands[control.commands["normal_waiter"] - 1]
    changed_wait_attrs = _with_semantic(
        control.program,
        waiter.command_id,
        source=ControlCommandSource(EventWaitAttrs(control.events["normal_signal_second"])),
    )
    changed_wait_zero = _with_semantic(
        control.program,
        waiter.command_id,
        source=ControlCommandSource(EventWaitAttrs(0)),
    )
    changed_wait_large = _with_semantic(
        control.program,
        waiter.command_id,
        source=ControlCommandSource(EventWaitAttrs(1 << 32)),
    )
    changed_wait_transport = _refreshed(
        control.program,
        command_waits=(
            *control.program.command_waits[:waiter.wait_begin],
            replace(control.program.command_waits[waiter.wait_begin], event_id=control.events["normal_signal_second"]),
            *control.program.command_waits[waiter.wait_begin + 1:],
        ),
    )
    changed_barrier_group = _with_semantic(
        control.program,
        control.commands["barrier_arrival_core0"],
        execution=BarrierExecution(control.barrier_group_id + 1),
    )
    barrier_command = control.program.commands[control.commands["barrier_arrival_core0"] - 1]
    changed_barrier_engine = _refreshed(
        control.program,
        commands=(
            *control.program.commands[:barrier_command.command_id - 1],
            replace(barrier_command, engine=A.ENGINE.DMA_READ),
            *control.program.commands[barrier_command.command_id:],
        ),
    )
    barrier_group_index = next(
        index
        for index, item in enumerate(control.program.semantics.barrier_groups)
        if item.barrier_group_id == control.barrier_group_id
    )
    barrier_group = control.program.semantics.barrier_groups[barrier_group_index]
    changed_barrier_missing = _refreshed(
        control.program,
        semantics=replace(
            control.program.semantics,
            barrier_groups=(
                *control.program.semantics.barrier_groups[:barrier_group_index],
                replace(barrier_group, arrivals=barrier_group.arrivals[:1]),
                *control.program.semantics.barrier_groups[barrier_group_index + 1:],
            ),
        ),
    )
    changed_barrier_duplicate = _refreshed(
        control.program,
        semantics=replace(
            control.program.semantics,
            barrier_groups=(
                *control.program.semantics.barrier_groups[:barrier_group_index],
                replace(
                    barrier_group,
                    arrivals=(
                        barrier_group.arrivals[0],
                        replace(
                            barrier_group.arrivals[1],
                            done_token_id=barrier_group.arrivals[0].done_token_id,
                        ),
                    ),
                ),
                *control.program.semantics.barrier_groups[barrier_group_index + 1:],
            ),
        ),
    )
    changed_barrier_participants = _refreshed(
        control.program,
        semantics=replace(
            control.program.semantics,
            barrier_groups=(
                *control.program.semantics.barrier_groups[:barrier_group_index],
                replace(barrier_group, participants=barrier_group.participants[:1]),
                *control.program.semantics.barrier_groups[barrier_group_index + 1:],
            ),
        ),
    )
    barrier_event_index = next(
        index
        for index, item in enumerate(control.program.events)
        if item.event_id == barrier_group.completion_event_id
    )
    barrier_event = control.program.events[barrier_event_index]
    changed_barrier_event_count = _refreshed(
        control.program,
        events=(
            *control.program.events[:barrier_event_index],
            replace(barrier_event, expected_arrivals=barrier_event.expected_arrivals - 1),
            *control.program.events[barrier_event_index + 1:],
        ),
    )
    changed_barrier_event_kind = _refreshed(
        control.program,
        events=(
            *control.program.events[:barrier_event_index],
            replace(barrier_event, kind=A.EVENT_KIND.NORMAL),
            *control.program.events[barrier_event_index + 1:],
        ),
    )
    changed_barrier_event_producer = _refreshed(
        control.program,
        events=(
            *control.program.events[:barrier_event_index],
            replace(
                barrier_event,
                producer_command_id=control.commands["barrier_arrival_core0"],
            ),
            *control.program.events[barrier_event_index + 1:],
        ),
    )
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    single = build_single_core_program(arch)
    begin = next(
        item
        for item in single.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is RequestBeginAttrs
    )
    end = next(
        item
        for item in single.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is RequestEndAttrs
    )
    halt = next(
        item
        for item in single.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is HaltAttrs
    )
    changed_control_source = _with_semantic(
        single,
        begin.command_id,
        source=ControlCommandSource(HaltAttrs()),
    )
    changed_control_execution = _with_semantic(
        single,
        begin.command_id,
        execution=ComputeExecution(()),
    )
    changed_end_source = _with_semantic(
        single,
        end.command_id,
        source=ControlCommandSource(HaltAttrs()),
    )
    changed_halt_source = _with_semantic(
        single,
        halt.command_id,
        source=ControlCommandSource(RequestEndAttrs()),
    )
    changed_control_source_op = _with_command(single, begin.command_id, source_op_id=1)
    dma = next(
        item
        for item in single.semantics.command_semantics
        if type(item.source) is KernelCommandSource
        and single.semantics.kernel_ops[item.source.kernel_op_id - 1].opcode is KernelOpcode.DMA
    )
    changed_dma_source = _with_semantic(
        single,
        dma.command_id,
        source=ControlCommandSource(HaltAttrs()),
        execution=ControlExecution(),
    )
    changed_dma_execution = _with_semantic(single, dma.command_id, execution=ControlExecution())
    changed_dma_group = _with_semantic(single, dma.command_id, execution=DmaExecution(999))
    changed_stream_abi_physical = _refreshed(
        single,
        streams=(replace(single.streams[0], stream_id=1),),
    )
    changed_stream_abi_count = _refreshed(
        single,
        streams=(replace(single.streams[0], command_count=single.streams[0].command_count - 1),),
    )
    changed_stream_abi_flags = _refreshed(
        single,
        streams=(replace(single.streams[0], flags=0),),
    )
    changed_stream_semantic_core = _refreshed(
        single,
        semantics=replace(
            single.semantics,
            streams=(replace(single.semantics.streams[0], core_id=9999),),
        ),
    )
    changed_stream_semantic_physical = _refreshed(
        single,
        semantics=replace(
            single.semantics,
            streams=(replace(single.semantics.streams[0], physical_stream_id=9999),),
        ),
    )
    changed_stream_semantic_physical_large = _refreshed(
        single,
        semantics=replace(
            single.semantics,
            streams=(replace(single.semantics.streams[0], physical_stream_id=1 << 32),),
        ),
    )
    changed_stream_abi_range = _refreshed(
        single,
        streams=(
            replace(
                single.streams[0],
                command_begin=len(single.semantics.stream_command_ids) + 1,
            ),
        ),
    )
    changed_stream_entrypoint = _refreshed(
        single,
        entrypoints=(replace(single.entrypoints[0], lifecycle_stream_id=1),),
    )
    changed_stream_command_tail = _refreshed(
        single,
        semantics=replace(
            single.semantics,
            stream_command_ids=(
                *single.semantics.stream_command_ids,
                single.semantics.stream_command_ids[-1],
            ),
        ),
    )
    control_worker_stream = control.program.semantics.streams[1]
    changed_stream_abi_core = _refreshed(
        control.program,
        streams=(
            control.program.streams[0],
            replace(control.program.streams[1], core_id=0),
        ),
    )
    changed_stream_abi_begin = _refreshed(
        control.program,
        streams=(
            control.program.streams[0],
            replace(
                control.program.streams[1],
                command_begin=control.program.streams[1].command_begin - 1,
            ),
        ),
    )
    changed_stream_canonical_location = _refreshed(
        control.program,
        commands=tuple(
            replace(item, core_id=0)
            if (item.core_id, item.stream_id)
            == (control_worker_stream.core_id, control_worker_stream.physical_stream_id)
            else item
            for item in control.program.commands
        ),
        streams=(
            control.program.streams[0],
            replace(control.program.streams[1], core_id=0),
        ),
        semantics=replace(
            control.program.semantics,
            streams=(
                control.program.semantics.streams[0],
                replace(control_worker_stream, core_id=0),
            ),
        ),
    )
    fence = build_fence_scopes_program(arch)
    fence_semantic = next(
        item
        for item in fence.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is AxiFenceAttrs
    )
    alternate_scope = next(
        scope
        for scope in type(fence_semantic.source.attrs.scope)
        if scope is not fence_semantic.source.attrs.scope
    )
    changed_fence = _with_semantic(
        fence,
        fence_semantic.command_id,
        source=ControlCommandSource(AxiFenceAttrs(alternate_scope)),
    )
    dual = build_dual_core_program(arch)
    dual_worker_stream = dual.semantics.streams[1]
    changed_stream_command_physical = _refreshed(
        dual,
        commands=tuple(
            replace(item, stream_id=dual_worker_stream.physical_stream_id + 1)
            if item.command_id
            == dual.semantics.stream_command_ids[dual_worker_stream.command_begin]
            else item
            for item in dual.commands
        ),
    )
    dma_command = single.commands[dma.command_id - 1]
    compute_command = next(item for item in single.commands if item.opcode == A.OPCODE.GEMM)
    compute_input_index = compute_command.operand_begin
    compute_result_index = compute_command.operand_begin + compute_command.operand_count - 1
    dma_result_index = dma_command.operand_begin + dma_command.operand_count - 1
    changed_compute_output_permission = _with_command_operand(
        single,
        compute_result_index,
        access=A.ACCESS_KIND.READ_ONLY,
    )
    changed_dma_output_permission = _with_command_operand(
        single,
        dma_result_index,
        access=A.ACCESS_KIND.READ_ONLY,
    )
    changed_compute_input_permission = _with_command_operand(
        single,
        compute_input_index,
        access=A.ACCESS_KIND.READ_WRITE,
    )
    changed_compute_reordered_operands = _refreshed(
        single,
        command_operands=tuple(
            single.command_operands[compute_input_index + 1]
            if index == compute_input_index
            else single.command_operands[compute_input_index]
            if index == compute_input_index + 1
            else item
            for index, item in enumerate(single.command_operands)
        ),
    )
    foreign_operand = next(
        item
        for item in single.command_operands
        if item.allocation_id == 0 and item.tensor_id != single.command_operands[compute_input_index].tensor_id
    )
    changed_compute_foreign_operand = _with_command_operand(
        single,
        compute_input_index,
        tensor_id=foreign_operand.tensor_id,
        shard_id=foreign_operand.shard_id,
        allocation_id=foreign_operand.allocation_id,
        access=foreign_operand.access,
        reserved=foreign_operand.reserved,
    )
    changed_operand_begin = _with_command(
        single,
        compute_command.command_id,
        operand_begin=compute_command.operand_begin - 1,
    )
    changed_operand_span = _with_command(
        single,
        compute_command.command_id,
        operand_count=len(single.command_operands),
    )
    zero_operand_command = next(
        item
        for item in single.commands
        if item.opcode is A.OPCODE.REQUEST_END and item.operand_count == 0
    )
    changed_zero_operand_begin = _with_command(
        single,
        zero_operand_command.command_id,
        operand_begin=zero_operand_command.operand_begin - 1,
    )
    changed_operand_tail = _refreshed(
        single,
        command_operands=(*single.command_operands, single.command_operands[-1]),
    )
    receive = next(
        item
        for item in dual.semantics.command_semantics
        if type(item.source) is KernelCommandSource
        and dual.semantics.kernel_ops[item.source.kernel_op_id - 1].opcode is KernelOpcode.RECV_WAIT
    )
    changed_receive_execution = _with_semantic(dual, receive.command_id, execution=DmaExecution(0))
    receive_command = dual.commands[receive.command_id - 1]
    changed_receive_engine = _refreshed(
        dual,
        commands=(
            *dual.commands[:receive_command.command_id - 1],
            replace(receive_command, engine=A.ENGINE.DMA_READ),
            *dual.commands[receive_command.command_id:],
        ),
    )
    changed_receive_completion = _refreshed(
        dual,
        command_waits=(
            *dual.command_waits[:receive_command.wait_begin],
            replace(dual.command_waits[receive_command.wait_begin], event_id=1),
            *dual.command_waits[receive_command.wait_begin + 1:],
        ),
    )
    receive_op = dual.semantics.kernel_ops[receive.source.kernel_op_id - 1]
    receive_attr_index = receive_command.attr_index - 1
    receive_attr = dual.op_attrs[receive_attr_index]
    changed_receive_op = replace(
        receive_op,
        attrs=RecvWaitAttrs(
            999,
            receive_op.attrs.destination_core,
            receive_op.attrs.source_core,
            receive_op.attrs.expected_bytes,
        ),
    )
    changed_receive_semantic = replace(
        receive,
        execution=replace(receive.execution, transfer_id=999),
    )
    changed_receive_attrs = _refreshed(
        dual,
        op_attrs=(
            *dual.op_attrs[:receive_attr_index],
            replace(receive_attr, payload=(999, *receive_attr.payload[1:])),
            *dual.op_attrs[receive_attr_index + 1:],
        ),
        semantics=replace(
            dual.semantics,
            kernel_ops=(
                *dual.semantics.kernel_ops[:receive_op.op_id - 1],
                changed_receive_op,
                *dual.semantics.kernel_ops[receive_op.op_id:],
            ),
            command_semantics=tuple(
                changed_receive_semantic if item.command_id == receive.command_id else item
                for item in dual.semantics.command_semantics
            ),
        ),
    )
    changed_receive_source_core = _with_kernel_op(
        dual,
        receive_op.op_id,
        attrs=RecvWaitAttrs(
            receive_op.attrs.transfer_id,
            receive_op.attrs.source_core + 1,
            receive_op.attrs.destination_core,
            receive_op.attrs.expected_bytes,
        ),
    )
    changed_receive_destination_core = _with_kernel_op(
        dual,
        receive_op.op_id,
        attrs=RecvWaitAttrs(
            receive_op.attrs.transfer_id,
            receive_op.attrs.source_core,
            receive_op.attrs.destination_core - 1,
            receive_op.attrs.expected_bytes,
        ),
    )
    changed_receive_expected_bytes = _with_kernel_op(
        dual,
        receive_op.op_id,
        attrs=RecvWaitAttrs(
            receive_op.attrs.transfer_id,
            receive_op.attrs.source_core,
            receive_op.attrs.destination_core,
            receive_op.attrs.expected_bytes + 1,
        ),
    )
    changed_receive_completion_token = _with_kernel_op(
        dual,
        receive_op.op_id,
        after_tokens=(),
    )
    changed_receive_transfer_zero = _with_kernel_op(
        dual,
        receive_op.op_id,
        attrs=RecvWaitAttrs(
            0,
            receive_op.attrs.source_core,
            receive_op.attrs.destination_core,
            receive_op.attrs.expected_bytes,
        ),
    )
    changed_receive_source_invalid = _with_kernel_op(
        dual,
        receive_op.op_id,
        attrs=RecvWaitAttrs(
            receive_op.attrs.transfer_id,
            INVALID_CORE_ID,
            receive_op.attrs.destination_core,
            receive_op.attrs.expected_bytes,
        ),
    )
    changed_receive_destination_invalid = _with_kernel_op(
        dual,
        receive_op.op_id,
        attrs=RecvWaitAttrs(
            receive_op.attrs.transfer_id,
            receive_op.attrs.source_core,
            INVALID_CORE_ID,
            receive_op.attrs.expected_bytes,
        ),
    )
    p2p_reuse = build_p2p_reuse_program(arch)
    reuse_receives = tuple(
        item
        for item in p2p_reuse.semantics.command_semantics
        if type(item.source) is KernelCommandSource
        and p2p_reuse.semantics.kernel_ops[item.source.kernel_op_id - 1].opcode is KernelOpcode.RECV_WAIT
    )
    first_reuse_receive, second_reuse_receive = reuse_receives
    first_reuse_op = p2p_reuse.semantics.kernel_ops[first_reuse_receive.source.kernel_op_id - 1]
    second_reuse_op = p2p_reuse.semantics.kernel_ops[second_reuse_receive.source.kernel_op_id - 1]
    second_reuse_command = p2p_reuse.commands[second_reuse_receive.command_id - 1]
    second_reuse_attr_index = second_reuse_command.attr_index - 1
    changed_duplicate_receive_op = replace(
        second_reuse_op,
        attrs=RecvWaitAttrs(
            first_reuse_op.attrs.transfer_id,
            second_reuse_op.attrs.source_core,
            second_reuse_op.attrs.destination_core,
            second_reuse_op.attrs.expected_bytes,
        ),
    )
    changed_duplicate_receive = _refreshed(
        p2p_reuse,
        op_attrs=(
            *p2p_reuse.op_attrs[:second_reuse_attr_index],
            replace(
                p2p_reuse.op_attrs[second_reuse_attr_index],
                payload=(
                    first_reuse_op.attrs.transfer_id,
                    *p2p_reuse.op_attrs[second_reuse_attr_index].payload[1:],
                ),
            ),
            *p2p_reuse.op_attrs[second_reuse_attr_index + 1:],
        ),
        semantics=replace(
            p2p_reuse.semantics,
            kernel_ops=(
                *p2p_reuse.semantics.kernel_ops[:second_reuse_op.op_id - 1],
                changed_duplicate_receive_op,
                *p2p_reuse.semantics.kernel_ops[second_reuse_op.op_id:],
            ),
            command_semantics=tuple(
                replace(
                    item,
                    execution=replace(
                        item.execution,
                        transfer_id=first_reuse_op.attrs.transfer_id,
                    ),
                )
                if item.command_id == second_reuse_receive.command_id
                else item
                for item in p2p_reuse.semantics.command_semantics
            ),
        ),
    )
    p2p_variants = build_two_variant_p2p_reuse_program(arch)
    first_p2p_variant, second_p2p_variant = p2p_variants.semantics.variants
    first_variant_receive = next(
        item
        for item in p2p_variants.semantics.command_semantics[
            first_p2p_variant.membership.command_semantics.first_id - 1:
            first_p2p_variant.membership.command_semantics.first_id - 1
            + first_p2p_variant.membership.command_semantics.count
        ]
        if type(item.source) is KernelCommandSource
        and p2p_variants.semantics.kernel_ops[item.source.kernel_op_id - 1].opcode
        is KernelOpcode.RECV_WAIT
    )
    second_variant_receive = next(
        item
        for item in p2p_variants.semantics.command_semantics[
            second_p2p_variant.membership.command_semantics.first_id - 1:
            second_p2p_variant.membership.command_semantics.first_id - 1
            + second_p2p_variant.membership.command_semantics.count
        ]
        if type(item.source) is KernelCommandSource
        and p2p_variants.semantics.kernel_ops[item.source.kernel_op_id - 1].opcode
        is KernelOpcode.RECV_WAIT
    )
    first_variant_receive_op = p2p_variants.semantics.kernel_ops[
        first_variant_receive.source.kernel_op_id - 1
    ]
    second_variant_receive_op = p2p_variants.semantics.kernel_ops[
        second_variant_receive.source.kernel_op_id - 1
    ]
    first_variant_receive_command = p2p_variants.commands[
        first_variant_receive.command_id - 1
    ]
    second_variant_receive_command = p2p_variants.commands[
        second_variant_receive.command_id - 1
    ]
    first_variant_receive_attr_index = first_variant_receive_command.attr_index - 1
    second_variant_receive_attr_index = second_variant_receive_command.attr_index - 1
    changed_first_cross_variant_receive_op = replace(
        first_variant_receive_op,
        attrs=RecvWaitAttrs(
            second_variant_receive_op.attrs.transfer_id,
            first_variant_receive_op.attrs.source_core,
            first_variant_receive_op.attrs.destination_core,
            first_variant_receive_op.attrs.expected_bytes,
        ),
    )
    changed_second_cross_variant_receive_op = replace(
        second_variant_receive_op,
        attrs=RecvWaitAttrs(
            first_variant_receive_op.attrs.transfer_id,
            second_variant_receive_op.attrs.source_core,
            second_variant_receive_op.attrs.destination_core,
            second_variant_receive_op.attrs.expected_bytes,
        ),
    )
    changed_cross_variant_receive = _refreshed(
        p2p_variants,
        op_attrs=tuple(
            replace(
                item,
                payload=(
                    second_variant_receive_op.attrs.transfer_id,
                    *item.payload[1:],
                ),
            )
            if index == first_variant_receive_attr_index
            else replace(
                item,
                payload=(
                    first_variant_receive_op.attrs.transfer_id,
                    *item.payload[1:],
                ),
            )
            if index == second_variant_receive_attr_index
            else item
            for index, item in enumerate(p2p_variants.op_attrs)
        ),
        semantics=replace(
            p2p_variants.semantics,
            kernel_ops=tuple(
                changed_first_cross_variant_receive_op
                if item.op_id == first_variant_receive_op.op_id
                else changed_second_cross_variant_receive_op
                if item.op_id == second_variant_receive_op.op_id
                else item
                for item in p2p_variants.semantics.kernel_ops
            ),
            command_semantics=tuple(
                replace(
                    item,
                    execution=replace(
                        item.execution,
                        transfer_id=second_variant_receive_op.attrs.transfer_id,
                    ),
                )
                if item.command_id == first_variant_receive.command_id
                else replace(
                    item,
                    execution=replace(
                        item.execution,
                        transfer_id=first_variant_receive_op.attrs.transfer_id,
                    ),
                )
                if item.command_id == second_variant_receive.command_id
                else item
                for item in p2p_variants.semantics.command_semantics
            ),
        ),
    )
    repeat = build_repeat_program(arch)
    repeat_semantic = next(
        item
        for item in repeat.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is RepeatCommandAttrs
    )
    repeat_attrs = repeat_semantic.source.attrs
    changed_repeat = _with_semantic(
        repeat,
        repeat_semantic.command_id,
        source=ControlCommandSource(
            RepeatCommandAttrs(
                repeat_attrs.subrange_begin_stream_ordinal,
                repeat_attrs.subrange_command_count,
                repeat_attrs.repeat_count + 1,
            )
        ),
    )
    changed_repeat_range = _with_repeat_attrs(
        repeat,
        repeat_semantic.command_id,
        RepeatCommandAttrs(
            repeat_attrs.subrange_begin_stream_ordinal,
            repeat_attrs.subrange_command_count - 1,
            repeat_attrs.repeat_count,
        ),
    )
    changed_repeat_begin_large = _with_semantic(
        repeat,
        repeat_semantic.command_id,
        source=ControlCommandSource(
            RepeatCommandAttrs(
                1 << 32,
                repeat_attrs.subrange_command_count,
                repeat_attrs.repeat_count,
            )
        ),
    )
    changed_repeat_begin_zero = _with_semantic(
        repeat,
        repeat_semantic.command_id,
        source=ControlCommandSource(
            RepeatCommandAttrs(
                0,
                repeat_attrs.subrange_command_count,
                repeat_attrs.repeat_count,
            )
        ),
    )
    changed_repeat_count_zero = _with_semantic(
        repeat,
        repeat_semantic.command_id,
        source=ControlCommandSource(
            RepeatCommandAttrs(
                repeat_attrs.subrange_begin_stream_ordinal,
                0,
                repeat_attrs.repeat_count,
            )
        ),
    )
    changed_repeat_count_large = _with_semantic(
        repeat,
        repeat_semantic.command_id,
        source=ControlCommandSource(
            RepeatCommandAttrs(
                repeat_attrs.subrange_begin_stream_ordinal,
                1 << 32,
                repeat_attrs.repeat_count,
            )
        ),
    )
    changed_repeat_zero = _with_semantic(
        repeat,
        repeat_semantic.command_id,
        source=ControlCommandSource(
            RepeatCommandAttrs(
                repeat_attrs.subrange_begin_stream_ordinal,
                repeat_attrs.subrange_command_count,
                0,
            )
        ),
    )
    changed_repeat_large = _with_semantic(
        repeat,
        repeat_semantic.command_id,
        source=ControlCommandSource(
            RepeatCommandAttrs(
                repeat_attrs.subrange_begin_stream_ordinal,
                repeat_attrs.subrange_command_count,
                1 << 32,
            )
        ),
    )
    changed_repeat_forbidden = _with_repeat_attrs(
        repeat,
        repeat_semantic.command_id,
        RepeatCommandAttrs(
            0,
            repeat_attrs.subrange_begin_stream_ordinal + repeat_attrs.subrange_command_count,
            repeat_attrs.repeat_count,
        ),
    )
    repeat_executions, repeat_identity = derive_descriptor_execution_set(repeat, arch)
    submitted_repeat_executions = (
        replace(repeat_executions[0], execution_count=repeat_executions[0].execution_count - 1),
        *repeat_executions[1:],
    )
    submitted_repeat_traffic = calculate_traffic(arch, repeat_identity, submitted_repeat_executions)
    submitted_repeat = replace(
        repeat,
        expected_traffic=_expected_traffic(submitted_repeat_traffic, submitted_repeat_executions, arch),
        semantics=replace(repeat.semantics, intrinsic_traffic=submitted_repeat_traffic),
        semantic_sha256="",
    )
    changed_repeat_traffic = replace(
        submitted_repeat,
        semantic_sha256=semantic_sha256(submitted_repeat.semantic_dict()),
    )
    mixed_arch, mixed_repeat = build_mixed_engine_repeat_program()
    changed_repeat_generation = with_repeat_range(mixed_repeat, mixed_arch, 2, 2)
    repeat_event_arch, repeat_event = build_control_repeat_event_program()
    changed_repeat_escape = with_repeat_event_escape(repeat_event)
    repeat_disjoint_arch, repeat_disjoint = build_disjoint_control_repeat_program()
    second_repeat = tuple(
        item
        for item in repeat_disjoint.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is RepeatCommandAttrs
    )[1]
    changed_repeat_nested_overlap = with_repeat_range(
        repeat_disjoint,
        repeat_disjoint_arch,
        1,
        5,
        command_id=second_repeat.command_id,
        recompute_traffic=False,
    )
    sram = build_sram_reuse_program(arch)
    reuse_index = next(index for index, item in enumerate(sram.semantics.dependencies) if item.kind is ScheduledDependencyKind.SRAM_REUSE)
    changed_dependency = replace(sram.semantics.dependencies[reuse_index], source=ObjectSource(3))
    changed_sram = _refreshed(
        sram,
        semantics=replace(
            sram.semantics,
            dependencies=(
                *sram.semantics.dependencies[:reuse_index],
                changed_dependency,
                *sram.semantics.dependencies[reuse_index + 1:],
            ),
        ),
    )
    return (
        Stage2ControlDependencyCase("event_fanin_positive", control.arch, control.program, control.program, None),
        Stage2ControlDependencyCase("control_engine", control.arch, control.program, changed_control_engine, "E_ENGINE_MISMATCH"),
        Stage2ControlDependencyCase("control_operands", control.arch, control.program, changed_control_operands, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("event_signal_projection", control.arch, control.program, changed_signal, "E_EVENT_NO_PRODUCER"),
        Stage2ControlDependencyCase("event_signal_zero", control.arch, control.program, changed_signal_zero, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("event_signal_u32_overflow", control.arch, control.program, changed_signal_large, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("event_wait_projection", control.arch, control.program, changed_wait_attrs, "E_EVENT_NO_PRODUCER"),
        Stage2ControlDependencyCase("event_wait_zero", control.arch, control.program, changed_wait_zero, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("event_wait_u32_overflow", control.arch, control.program, changed_wait_large, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("event_wait_transport_lifecycle", control.arch, control.program, changed_wait_transport, "E_LIFECYCLE"),
        Stage2ControlDependencyCase("barrier_execution_projection", control.arch, control.program, changed_barrier_group, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("barrier_engine", control.arch, control.program, changed_barrier_engine, "E_ENGINE_MISMATCH"),
        Stage2ControlDependencyCase("barrier_missing_arrival", control.arch, control.program, changed_barrier_missing, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("barrier_duplicate_arrival", control.arch, control.program, changed_barrier_duplicate, "E_EVENT_MULTIPLE_PRODUCERS"),
        Stage2ControlDependencyCase("barrier_participant_projection", control.arch, control.program, changed_barrier_participants, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("barrier_event_count_projection", control.arch, control.program, changed_barrier_event_count, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("barrier_event_kind_projection", control.arch, control.program, changed_barrier_event_kind, "E_EVENT_MULTIPLE_PRODUCERS"),
        Stage2ControlDependencyCase("barrier_event_producer_projection", control.arch, control.program, changed_barrier_event_producer, "E_EVENT_NO_PRODUCER"),
        Stage2ControlDependencyCase("wrong_control_source", arch, single, changed_control_source, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("wrong_control_execution", arch, single, changed_control_execution, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("request_end_attribute_projection", arch, single, changed_end_source, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("halt_attribute_projection", arch, single, changed_halt_source, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("control_source_op_projection", arch, single, changed_control_source_op, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("dma_control_source", arch, single, changed_dma_source, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("dma_execution_projection", arch, single, changed_dma_execution, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("dma_descriptor_group_projection", arch, single, changed_dma_group, "E_DMA_RANGE"),
        Stage2ControlDependencyCase("operand_projection_positive", arch, single, single, None),
        Stage2ControlDependencyCase("operand_compute_output_permission", arch, single, changed_compute_output_permission, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("operand_dma_output_permission", arch, single, changed_dma_output_permission, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("operand_compute_input_permission", arch, single, changed_compute_input_permission, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("operand_compute_reordered", arch, single, changed_compute_reordered_operands, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("operand_compute_foreign_identity", arch, single, changed_compute_foreign_operand, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("operand_noncanonical_begin", arch, single, changed_operand_begin, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("operand_out_of_table_span", arch, single, changed_operand_span, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("operand_zero_operand_noncanonical_begin", arch, single, changed_zero_operand_begin, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("operand_unused_tail", arch, single, changed_operand_tail, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("stream_abi_physical_projection", arch, single, changed_stream_abi_physical, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_abi_count_projection", arch, single, changed_stream_abi_count, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_abi_core_projection", control.arch, control.program, changed_stream_abi_core, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_abi_begin_projection", control.arch, control.program, changed_stream_abi_begin, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_abi_flags_projection", arch, single, changed_stream_abi_flags, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_semantic_invalid_core", arch, single, changed_stream_semantic_core, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_semantic_physical_projection", arch, single, changed_stream_semantic_physical, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_semantic_physical_u32_overflow", arch, single, changed_stream_semantic_physical_large, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_command_physical_projection", arch, dual, changed_stream_command_physical, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_abi_range", arch, single, changed_stream_abi_range, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("stream_canonical_location", control.arch, control.program, changed_stream_canonical_location, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_entrypoint_lifecycle_projection", arch, single, changed_stream_entrypoint, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("stream_command_vector_tail", arch, single, changed_stream_command_tail, "E_STREAM_CONTRACT"),
        Stage2ControlDependencyCase("fence_positive", arch, fence, fence, None),
        Stage2ControlDependencyCase("fence_attribute_projection", arch, fence, changed_fence, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("recv_wait_execution_projection", arch, dual, changed_receive_execution, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("recv_wait_engine", arch, dual, changed_receive_engine, "E_ENGINE_MISMATCH"),
        Stage2ControlDependencyCase("recv_wait_completion_wait", arch, dual, changed_receive_completion, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("recv_wait_unmatched_typed", arch, dual, changed_receive_attrs, "E_P2P_UNMATCHED"),
        Stage2ControlDependencyCase("recv_wait_source_core", arch, dual, changed_receive_source_core, "E_P2P_UNMATCHED"),
        Stage2ControlDependencyCase("recv_wait_destination_core", arch, dual, changed_receive_destination_core, "E_P2P_UNMATCHED"),
        Stage2ControlDependencyCase("recv_wait_expected_bytes", arch, dual, changed_receive_expected_bytes, "E_P2P_UNMATCHED"),
        Stage2ControlDependencyCase("recv_wait_completion_token", arch, dual, changed_receive_completion_token, "E_P2P_UNMATCHED"),
        Stage2ControlDependencyCase("recv_wait_transfer_zero", arch, dual, changed_receive_transfer_zero, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("recv_wait_source_invalid", arch, dual, changed_receive_source_invalid, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("recv_wait_destination_invalid", arch, dual, changed_receive_destination_invalid, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("recv_wait_duplicate_receive", arch, p2p_reuse, changed_duplicate_receive, "E_P2P_UNMATCHED"),
        Stage2ControlDependencyCase("recv_wait_cross_variant_positive", arch, p2p_variants, p2p_variants, None),
        Stage2ControlDependencyCase("recv_wait_cross_variant", arch, p2p_variants, changed_cross_variant_receive, "E_P2P_UNMATCHED"),
        Stage2ControlDependencyCase("repeat_attribute_projection", arch, repeat, changed_repeat, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("repeat_range_projection", arch, repeat, changed_repeat_range, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_begin_zero", arch, repeat, changed_repeat_begin_zero, "E_ABI_ENUM"),
        Stage2ControlDependencyCase("repeat_begin_u32_overflow", arch, repeat, changed_repeat_begin_large, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_count_zero", arch, repeat, changed_repeat_count_zero, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_count_u32_overflow", arch, repeat, changed_repeat_count_large, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_repeat_count_zero", arch, repeat, changed_repeat_zero, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_repeat_count_u32_overflow", arch, repeat, changed_repeat_large, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_forbidden_member", arch, repeat, changed_repeat_forbidden, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_traffic_execution_count", arch, repeat, changed_repeat_traffic, "E_TRAFFIC_MISMATCH"),
        Stage2ControlDependencyCase("repeat_generation_positive", mixed_arch, mixed_repeat, mixed_repeat, None),
        Stage2ControlDependencyCase("repeat_generation_state", mixed_arch, mixed_repeat, changed_repeat_generation, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_event_positive", repeat_event_arch, repeat_event, repeat_event, None),
        Stage2ControlDependencyCase("repeat_event_escape", repeat_event_arch, repeat_event, changed_repeat_escape, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("repeat_disjoint_positive", repeat_disjoint_arch, repeat_disjoint, repeat_disjoint, None),
        Stage2ControlDependencyCase("repeat_nested_overlap", repeat_disjoint_arch, repeat_disjoint, changed_repeat_nested_overlap, "E_ABI_BOUNDS"),
        Stage2ControlDependencyCase("sram_reuse_positive", arch, sram, sram, None),
        Stage2ControlDependencyCase("sram_reuse_source", arch, sram, changed_sram, "E_ABI_BOUNDS"),
    )

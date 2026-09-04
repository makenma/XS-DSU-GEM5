"""Structural verifier for decoded .mshb programs (spec section 11).

Fail-closed: any violation raises MeshIrError with a stable code.  The same
checks are mirrored by the C++ loader before any command is installed, so
the Python and C++ sides must stay behaviourally identical.
"""

from __future__ import annotations

from mesh_ir.abi.rules import check_payload_rules, check_record_rules
from mesh_ir.abi.spans import checked_span, span_fits
from mesh_ir.burst_splitter import checked_mul, plan_descriptor
from mesh_ir.generated import abi as A
from mesh_ir.model import (
    ArchManifest,
    MeshIrError,
    OpAttr,
    Program,
)

_U64_MAX = (1 << 64) - 1


class VerifiedProgram:
    """Convenience views over a verified program (loader-side indices)."""

    def __init__(self, program: Program, arch: ArchManifest):
        self.program = program
        self.arch = arch
        self.commands_by_id = {c.command_id: c for c in program.commands}
        self.events_by_id = {e.event_id: e for e in program.events}
        self.tensors_by_id = {t.tensor_id: t for t in program.tensors}
        self.allocations_by_id = {a.allocation_id: a for a in program.allocations}
        self.descriptors_by_id = {d.descriptor_id: d for d in program.dma_descriptors}
        self.streams_by_key = {(s.core_id, s.stream_id): s for s in program.streams}
        self.strings = {i + 1: s.value for i, s in enumerate(program.strings)}


def verify_program(program: Program, arch: ArchManifest) -> VerifiedProgram:
    _verify_arch_digest(program, arch)
    _verify_closed_sets(program)
    _verify_ids_and_references(program)
    _verify_streams(program, arch)
    _verify_commands(program, arch)
    _verify_operand_contract(program)
    _verify_events(program)
    _verify_allocations(program, arch)
    _verify_descriptors(program, arch)
    _verify_relocations(program, arch)
    _verify_expected_traffic(program, arch)
    _verify_lifecycle(program)
    _verify_acyclic(program)
    return VerifiedProgram(program, arch)


def _verify_arch_digest(program: Program, arch: ArchManifest) -> None:
    if program.arch_digest != arch.digest():
        raise MeshIrError(
            "E_ARCH_DIGEST",
            "architecture digest mismatch",
            expected=arch.digest().hex(),
            actual=program.arch_digest.hex(),
        )


def _sid(program: Program, sid: int) -> str:
    if not (1 <= sid <= len(program.strings)):
        raise MeshIrError("E_ABI_BOUNDS", "string id out of range", sid=sid)
    return program.strings[sid - 1].value


_RULE_SECTIONS = (
    ("ENTRYPOINTS", "entrypoints"),
    ("PROFILES", "profiles"),
    ("TENSORS", "tensors"),
    ("SHARDS", "shards"),
    ("ALLOCATIONS", "allocations"),
    ("STREAMS", "streams"),
    ("COMMANDS", "commands"),
    ("COMMAND_WAITS", "command_waits"),
    ("COMMAND_OPERANDS", "command_operands"),
    ("EVENTS", "events"),
    ("DMA_DESCRIPTORS", "dma_descriptors"),
    ("RELOCATIONS", "relocations"),
    ("EXPECTED_TRAFFIC", "expected_traffic"),
)


def _verify_closed_sets(program: Program) -> None:
    for section, attr in _RULE_SECTIONS:
        check_record_rules(section, getattr(program, attr))
    for descriptor in program.dma_descriptors:
        check_record_rules("DMA_ENDPOINT", (descriptor.src, descriptor.dst))
    for op_attr in program.op_attrs:
        if op_attr.kind not in A.PAYLOAD_BY_KIND:
            raise MeshIrError("E_ABI_ENUM", "unknown attr kind", kind=op_attr.kind)
        payload_name = A.PAYLOAD_BY_KIND[op_attr.kind]
        check_payload_rules(
            payload_name,
            op_attr.payload,
            getattr(A, f"{payload_name}_FIELDS"),
        )


def _verify_ids_and_references(program: Program) -> None:
    def unique(items, key, what):
        seen = set()
        for item in items:
            value = key(item)
            if value in seen:
                raise MeshIrError("E_ABI_DUPLICATE", f"duplicate {what}", id=value)
            seen.add(value)

    unique(program.entrypoints, lambda e: e.entrypoint_id, "entrypoint_id")
    unique(program.profiles, lambda p: p.profile_id, "profile_id")
    unique(program.tensors, lambda t: t.tensor_id, "tensor_id")
    unique(program.shards, lambda s: s.shard_id, "shard_id")
    unique(program.allocations, lambda a: a.allocation_id, "allocation_id")
    unique(program.commands, lambda c: c.command_id, "command_id")
    unique(program.events, lambda e: e.event_id, "event_id")
    unique(program.dma_descriptors, lambda d: d.descriptor_id, "descriptor_id")
    unique(program.relocations, lambda r: r.relocation_id, "relocation_id")

    ids = [c.command_id for c in program.commands]
    if ids != sorted(ids):
        raise MeshIrError("E_ABI_ORDER", "commands must be sorted by command_id")

    for entrypoint in program.entrypoints:
        _sid(program, entrypoint.name_sid)
    for profile in program.profiles:
        _sid(program, profile.name_sid)
        if profile.rank > 8:
            raise MeshIrError("E_ABI_BOUNDS", "profile rank > 8")
    for tensor in program.tensors:
        _sid(program, tensor.name_sid)
        if tensor.rank > 8:
            raise MeshIrError("E_ABI_BOUNDS", "tensor rank > 8")
        if tensor.layout == A.LAYOUT_KIND.BLOCKED_MNK and tensor.layout_attr == 0:
            raise MeshIrError("E_ABI_BOUNDS", "BLOCKED_MNK tensor requires layout attr")

    symbol_names = set()
    for relocation in program.relocations:
        name = _sid(program, relocation.symbol_sid)
        if name in symbol_names:
            raise MeshIrError("E_RELOCATION", "duplicate relocation symbol", symbol=name)
        symbol_names.add(name)


def _stream_commands(program: Program, stream) -> list:
    span = checked_span(
        stream.command_begin, stream.command_count, len(program.commands),
        "stream range out of table",
    )
    return program.commands[span]


def _verify_streams(program: Program, arch: ArchManifest) -> None:
    keys = set()
    per_core_streams = {}
    for stream in program.streams:
        key = (stream.core_id, stream.stream_id)
        if key in keys:
            raise MeshIrError("E_ABI_DUPLICATE", "duplicate stream", core=stream.core_id, stream=stream.stream_id)
        keys.add(key)
        if stream.core_id not in arch.core_ids:
            raise MeshIrError("E_ABI_BOUNDS", "stream core not in arch", core=stream.core_id)
        per_core_streams.setdefault(stream.core_id, []).append(stream)

    for core, streams in per_core_streams.items():
        control = [s for s in streams if s.flags & A.STREAM_FLAGS.IS_LOCAL_CONTROL]
        if len(control) != 1:
            raise MeshIrError("E_STREAM_CONTRACT", "core must have exactly one local control stream", core=core)
        halts = [c for c in _stream_commands(program, control[0]) if c.opcode == A.OPCODE.HALT]
        if len(halts) != 1:
            raise MeshIrError(
                "E_STREAM_CONTRACT",
                "control stream must contain exactly one HALT",
                core=core,
                halts=len(halts),
            )
        for stream in streams:
            for command in _stream_commands(program, stream):
                if command.core_id != core:
                    raise MeshIrError("E_STREAM_CONTRACT", "command core mismatch", command=command.command_id)
                if command.stream_id != stream.stream_id:
                    raise MeshIrError("E_STREAM_CONTRACT", "command stream mismatch", command=command.command_id)
                if stream is not control[0] and command.opcode == A.OPCODE.HALT:
                    raise MeshIrError(
                        "E_STREAM_CONTRACT",
                        "HALT outside the local control stream",
                        command=command.command_id,
                    )

    covered = sorted(
        c.command_id
        for stream in program.streams
        for c in _stream_commands(program, stream)
    )
    if covered != sorted(c.command_id for c in program.commands):
        raise MeshIrError("E_STREAM_CONTRACT", "stream ranges must partition the command table")


def _verify_commands(program: Program, arch: ArchManifest) -> None:
    waits = program.command_waits
    operands = program.command_operands
    tensor_ids = {t.tensor_id for t in program.tensors}
    shard_ids = {s.shard_id for s in program.shards}
    allocation_ids = {a.allocation_id for a in program.allocations}
    event_ids = {e.event_id for e in program.events}

    for command in program.commands:
        if command.core_id not in arch.core_ids:
            raise MeshIrError("E_ABI_BOUNDS", "command core not in arch", command=command.command_id)
        expected_engine = A.OPCODE_ENGINE.get(command.opcode)
        if expected_engine is None:
            raise MeshIrError("E_ABI_ENUM", "unknown opcode", opcode=command.opcode)
        if command.engine != expected_engine:
            raise MeshIrError(
                "E_ENGINE_MISMATCH",
                "opcode does not map to engine",
                command=command.command_id,
                opcode=command.opcode,
                engine=command.engine,
            )
        wait_span = checked_span(
            command.wait_begin, command.wait_count, len(waits),
            "wait range out of table",
        )
        for wait in waits[wait_span]:
            if wait.event_id not in event_ids:
                raise MeshIrError("E_ABI_BOUNDS", "wait references unknown event", command=command.command_id)
        operand_span = checked_span(
            command.operand_begin, command.operand_count, len(operands),
            "operand range out of table",
        )
        for operand in operands[operand_span]:
            if operand.tensor_id not in tensor_ids:
                raise MeshIrError("E_ABI_BOUNDS", "operand tensor unknown", command=command.command_id)
            if operand.shard_id and operand.shard_id not in shard_ids:
                raise MeshIrError("E_ABI_BOUNDS", "operand shard unknown", command=command.command_id)
            if operand.allocation_id and operand.allocation_id not in allocation_ids:
                raise MeshIrError("E_ABI_BOUNDS", "operand allocation unknown", command=command.command_id)
            if operand.shard_id:
                shard = next(s for s in program.shards if s.shard_id == operand.shard_id)
                if operand.allocation_id and shard.allocation_id != operand.allocation_id:
                    raise MeshIrError(
                        "E_ABI_BOUNDS",
                        "operand allocation does not match its shard allocation",
                        command=command.command_id,
                        operand_allocation=operand.allocation_id,
                        shard_allocation=shard.allocation_id,
                    )
                if shard.tensor_id != operand.tensor_id:
                    raise MeshIrError(
                        "E_ABI_BOUNDS",
                        "operand shard belongs to a different tensor",
                        command=command.command_id,
                        operand_tensor=operand.tensor_id,
                        shard_tensor=shard.tensor_id,
                    )
                if shard.owner_core != command.core_id:
                    raise MeshIrError(
                        "E_ABI_BOUNDS",
                        "operand shard owned by another core",
                        command=command.command_id,
                        shard=operand.shard_id,
                        owner=shard.owner_core,
                    )
        if command.signal_event and command.signal_event not in event_ids:
            raise MeshIrError("E_ABI_BOUNDS", "signal event unknown", command=command.command_id)

    _verify_attrs(program, arch)


def _attr_of(program: Program, index: int) -> OpAttr:
    if not (1 <= index <= len(program.op_attrs)):
        raise MeshIrError("E_ABI_BOUNDS", "attr index out of table", attr_index=index)
    return program.op_attrs[index - 1]


def _verify_attrs(program: Program, arch) -> None:
    for command in program.commands:
        opcode = command.opcode
        if opcode == A.OPCODE.REPEAT:
            attr = _attr_of(program, command.attr_index)
            if attr.kind != A.ATTR_KIND.REPEAT_V1:
                raise MeshIrError("E_ABI_ENUM", "REPEAT requires REPEAT_V1 attr", command=command.command_id)
            values = dict(zip(attr.payload_fields, attr.payload))
            begin = values["subrange_begin_stream_ordinal"]
            count = values["subrange_command_count"]
            repeat = values["repeat_count"]
            if count == 0 or repeat < 1:
                raise MeshIrError("E_ABI_BOUNDS", "REPEAT count invalid", command=command.command_id)
            stream = next(
                s for s in program.streams
                if s.core_id == command.core_id and s.stream_id == command.stream_id
            )
            commands = _stream_commands(program, stream)
            ordinal = next(
                i for i, c in enumerate(commands) if c.command_id == command.command_id
            )
            if not span_fits(begin, count, len(commands)):
                raise MeshIrError(
                    "E_ABI_BOUNDS",
                    "REPEAT subrange must immediately precede REPEAT in its stream",
                    command=command.command_id,
                )
            if begin + count != ordinal:
                raise MeshIrError(
                    "E_ABI_BOUNDS",
                    "REPEAT subrange must immediately precede REPEAT in its stream",
                    command=command.command_id,
                )
            for member in commands[begin : begin + count]:
                if member.opcode in (
                    A.OPCODE.HALT,
                    A.OPCODE.REQUEST_BEGIN,
                    A.OPCODE.REQUEST_END,
                    A.OPCODE.BARRIER,
                    A.OPCODE.DMA_P2P_PUSH,
                    A.OPCODE.RECV_WAIT,
                    A.OPCODE.REPEAT,
                ):
                    raise MeshIrError(
                        "E_ABI_BOUNDS",
                        "forbidden opcode inside REPEAT subrange",
                        command=member.command_id,
                        opcode=member.opcode,
                    )
        elif opcode == A.OPCODE.AXI_FENCE:
            attr = _attr_of(program, command.attr_index)
            if attr.kind != A.ATTR_KIND.FENCE_V1:
                raise MeshIrError("E_ABI_ENUM", "AXI_FENCE requires FENCE_V1 attr", command=command.command_id)
        elif opcode == A.OPCODE.RECV_WAIT:
            attr = _attr_of(program, command.attr_index)
            if attr.kind != A.ATTR_KIND.RECV_WAIT_V1:
                raise MeshIrError("E_ABI_ENUM", "RECV_WAIT requires RECV_WAIT_V1 attr", command=command.command_id)
        elif opcode in (A.OPCODE.DMA_LOAD, A.OPCODE.DMA_STORE, A.OPCODE.DMA_P2P_PUSH, A.OPCODE.DMA_PREFETCH):
            if command.attr_index != 0:
                raise MeshIrError("E_ABI_ENUM", "DMA command must not carry attr", command=command.command_id)
        elif opcode == A.OPCODE.DMA_FILL:
            attr = _attr_of(program, command.attr_index)
            if attr.kind != A.ATTR_KIND.FILL_V1:
                raise MeshIrError("E_ABI_ENUM", "DMA_FILL requires FILL_V1 attr", command=command.command_id)
        elif opcode in (A.OPCODE.GEMM, A.OPCODE.BMM):
            attr = _attr_of(program, command.attr_index)
            expected = A.ATTR_KIND.GEMM_V1 if opcode == A.OPCODE.GEMM else A.ATTR_KIND.BMM_V1
            if attr.kind != expected:
                raise MeshIrError("E_ABI_ENUM", "GEMM/BMM attr kind mismatch", command=command.command_id)
            values = dict(zip(attr.payload_fields, attr.payload))
            if not 1 <= values["efficiency_q16"] <= 65536:
                raise MeshIrError(
                    "E_ABI_BOUNDS",
                    "efficiency_q16 outside [1, 65536]",
                    command=command.command_id,
                )
            if dtype_name(values["dtype"]) not in arch.tensor_macs_per_cycle:
                raise MeshIrError(
                    "E_CAPABILITY_MISMATCH",
                    "GEMM dtype has no tensor throughput capability",
                    command=command.command_id,
                    dtype=dtype_name(values["dtype"]),
                )
            work = 1
            for key in ("batch", "m", "n", "k"):
                work *= values[key]
                if work >= 1 << 63:
                    raise MeshIrError(
                        "E_ABI_OVERFLOW",
                        "GEMM workload exceeds the schedulable cycle range",
                        command=command.command_id,
                    )
        elif opcode == A.OPCODE.ELEMENTWISE:
            attr = _attr_of(program, command.attr_index)
            if attr.kind != A.ATTR_KIND.ELEMENTWISE_V1:
                raise MeshIrError("E_ABI_ENUM", "ELEMENTWISE attr kind mismatch", command=command.command_id)
            values = dict(zip(attr.payload_fields, attr.payload))
            if values["ops_per_element"] < 1:
                raise MeshIrError(
                    "E_ABI_BOUNDS", "ops_per_element must be >= 1",
                    command=command.command_id)
            if dtype_name(values["dtype"]) not in arch.vector_elements_per_cycle:
                raise MeshIrError(
                    "E_CAPABILITY_MISMATCH",
                    "elementwise dtype has no vector capability",
                    command=command.command_id)
        elif opcode == A.OPCODE.LOCAL_REDUCE:
            attr = _attr_of(program, command.attr_index)
            if attr.kind != A.ATTR_KIND.REDUCE_V1:
                raise MeshIrError("E_ABI_ENUM", "LOCAL_REDUCE attr kind mismatch", command=command.command_id)
            values = dict(zip(attr.payload_fields, attr.payload))
            if values["fan_in"] < 2:
                raise MeshIrError(
                    "E_ABI_BOUNDS", "fan_in must be >= 2",
                    command=command.command_id)
            if dtype_name(values["dtype"]) not in arch.reduce_ops_per_cycle:
                raise MeshIrError(
                    "E_CAPABILITY_MISMATCH",
                    "reduce dtype has no reduce capability",
                    command=command.command_id)
        elif opcode in (A.OPCODE.SOFTMAX, A.OPCODE.NORM):
            attr = _attr_of(program, command.attr_index)
            expected = A.ATTR_KIND.SOFTMAX_V1 if opcode == A.OPCODE.SOFTMAX else A.ATTR_KIND.NORM_V1
            if attr.kind != expected:
                raise MeshIrError("E_ABI_ENUM", "SOFTMAX/NORM attr kind mismatch", command=command.command_id)
            values = dict(zip(attr.payload_fields, attr.payload))
            if dtype_name(values["dtype"]) not in arch.vector_elements_per_cycle:
                raise MeshIrError(
                    "E_CAPABILITY_MISMATCH",
                    "vector reduction dtype has no vector capability",
                    command=command.command_id,
                )
            # The V1 ABI cannot carry the spec 5.7 concrete phase plan;
            # both sides reject until the ABI SSOT is extended.
            raise MeshIrError(
                "E_ABI_BOUNDS",
                "vector reduction opcode requires a concrete phase plan "
                "the V1 ABI cannot express",
                command=command.command_id,
            )


OPCODE_OPERAND_CONTRACT = {
    "GEMM": (3, 3),
    "BMM": (3, 3),
    "ELEMENTWISE": (1, 2),
    "LOCAL_REDUCE": (1, 8),
    "SOFTMAX": (1, 1),
    "NORM": (1, 1),
    "DMA_LOAD": (1, 8),
    "DMA_STORE": (1, 8),
    "DMA_P2P_PUSH": (1, 8),
    "DMA_PREFETCH": (1, 8),
    "DMA_FILL": (1, 8),
    "RECV_WAIT": (1, 1),
}
COMPUTE_OPCODES = {
    getattr(A.OPCODE, name)
    for name in ("GEMM", "BMM", "ELEMENTWISE", "LOCAL_REDUCE", "SOFTMAX", "NORM")
}


def _verify_operand_contract(program: Program) -> None:
    for command in program.commands:
        contract = OPCODE_OPERAND_CONTRACT.get(_opcode_name(command.opcode))
        if contract is None:
            continue
        low, high = contract
        if not low <= command.operand_count <= high:
            raise MeshIrError(
                "E_ABI_BOUNDS",
                "operand count outside the opcode contract",
                command=command.command_id,
                opcode=_opcode_name(command.opcode),
                count=command.operand_count,
            )
        if command.opcode in COMPUTE_OPCODES and command.operand_count:
            result = program.command_operands[
                command.operand_begin + command.operand_count - 1
            ]
            if result.access != A.ACCESS_KIND.READ_WRITE:
                raise MeshIrError(
                    "E_ABI_BOUNDS",
                    "result operand must be READ_WRITE",
                    command=command.command_id,
                )


def dtype_name(dtype: int) -> str:
    for name, value in vars(A.DTYPE).items():
        if name.startswith("_"):
            continue
        if value == dtype:
            return name.lower()
    return str(dtype)


def _opcode_name(opcode: int) -> str:
    for name, value in vars(A.OPCODE).items():
        if name.startswith("_"):
            continue
        if value == opcode:
            return name
    return str(opcode)


def _verify_events(program: Program) -> None:
    producers = {}
    descriptor_by_event = {}
    for descriptor in program.dma_descriptors:
        descriptor_by_event.setdefault(descriptor.completion_event, []).append(descriptor)
    for command in program.commands:
        if command.signal_event:
            producers.setdefault(command.signal_event, []).append(command)

    for event in program.events:
        if event.expected_arrivals < 1:
            raise MeshIrError("E_ABI_BOUNDS", "expected_arrivals must be >= 1", event=event.event_id)
        cmd_producers = producers.get(event.event_id, [])
        dma_producers = descriptor_by_event.get(event.event_id, [])
        if event.kind == A.EVENT_KIND.NORMAL:
            total = len(cmd_producers) + len(dma_producers)
            if total == 0:
                raise MeshIrError("E_EVENT_NO_PRODUCER", "event has no producer", event=event.event_id)
            if total > 1:
                raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "normal event has multiple producers", event=event.event_id)
            producer_id = (
                cmd_producers[0].command_id if cmd_producers else dma_producers[0].command_id
            )
            if event.producer_command_id != producer_id:
                raise MeshIrError(
                    "E_EVENT_NO_PRODUCER",
                    "event producer_command_id mismatch",
                    event=event.event_id,
                    declared=event.producer_command_id,
                    actual=producer_id,
                )
        else:
            for producer in cmd_producers:
                if producer.opcode != A.OPCODE.BARRIER:
                    raise MeshIrError(
                        "E_EVENT_MULTIPLE_PRODUCERS",
                        "barrier event signaled by non-BARRIER command",
                        event=event.event_id,
                    )
            if dma_producers:
                raise MeshIrError(
                    "E_EVENT_MULTIPLE_PRODUCERS",
                    "barrier event signaled by a DMA descriptor completion",
                    event=event.event_id,
                )
            if len(cmd_producers) != event.expected_arrivals:
                raise MeshIrError(
                    "E_ABI_BOUNDS",
                    "barrier arrivals != signaling BARRIER commands",
                    event=event.event_id,
                    declared=event.expected_arrivals,
                    actual=len(cmd_producers),
                )


def _verify_allocations(program: Program, arch: ArchManifest) -> None:
    per_core = {}
    for allocation in program.allocations:
        if allocation.owner_core not in arch.core_ids:
            raise MeshIrError("E_ABI_BOUNDS", "allocation core not in arch", allocation=allocation.allocation_id)
        if allocation.memory_space != A.MEMORY_SPACE.CORE_SRAM:
            raise MeshIrError("E_ABI_BOUNDS", "V1 allocations must be CORE_SRAM", allocation=allocation.allocation_id)
        if allocation.alignment_bytes == 0 or allocation.alignment_bytes & (allocation.alignment_bytes - 1):
            raise MeshIrError("E_ABI_BOUNDS", "alignment must be power of two", allocation=allocation.allocation_id)
        if allocation.offset_bytes % allocation.alignment_bytes:
            raise MeshIrError("E_DMA_RANGE", "allocation offset misaligned", allocation=allocation.allocation_id)
        end = allocation.offset_bytes + allocation.size_bytes
        if end > arch.sram_bytes:
            raise MeshIrError(
                "E_SRAM_OOM",
                "allocation exceeds SRAM capacity",
                allocation=allocation.allocation_id,
                end=end,
                capacity=arch.sram_bytes,
            )
        per_core.setdefault(allocation.owner_core, []).append(allocation)

    for core, allocations in per_core.items():
        ordered = sorted(allocations, key=lambda a: a.offset_bytes)
        previous = None
        for allocation in ordered:
            if previous is not None and allocation.offset_bytes < previous.offset_bytes + previous.size_bytes:
                raise MeshIrError(
                    "E_DMA_RANGE",
                    "SRAM allocations overlap",
                    core=core,
                    a=allocation.allocation_id,
                    b=previous.allocation_id,
                )
            previous = allocation

    for shard in program.shards:
        if shard.tensor_id not in {t.tensor_id for t in program.tensors}:
            raise MeshIrError("E_ABI_BOUNDS", "shard tensor unknown", shard=shard.shard_id)
        if shard.allocation_id:
            if shard.allocation_id not in {a.allocation_id for a in program.allocations}:
                raise MeshIrError("E_ABI_BOUNDS", "shard allocation unknown", shard=shard.shard_id)
            allocation = next(a for a in program.allocations if a.allocation_id == shard.allocation_id)
            if allocation.owner_core != shard.owner_core:
                raise MeshIrError("E_ABI_BOUNDS", "shard/allocation core mismatch", shard=shard.shard_id)
            if shard.allocation_offset + shard.span_bytes > allocation.size_bytes:
                raise MeshIrError("E_DMA_RANGE", "shard exceeds allocation", shard=shard.shard_id)
        for local, valid in zip(shard.local_shape, shard.valid_shape):
            if valid > local:
                raise MeshIrError("E_ABI_BOUNDS", "valid shape exceeds local shape", shard=shard.shard_id)


def _endpoint_span(descriptor, which) -> int:
    if descriptor.rows == 0 or descriptor.row_bytes == 0:
        return 0
    rows = descriptor.rows if descriptor.rows else 1
    stride = descriptor.src_stride_bytes if which == "src" else descriptor.dst_stride_bytes
    span = checked_mul(rows - 1, stride) + descriptor.row_bytes if rows > 1 else descriptor.row_bytes
    return span


def _verify_descriptors(program: Program, arch: ArchManifest) -> None:
    commands_by_id = {c.command_id: c for c in program.commands}
    opcode_to_kind = {
        A.OPCODE.DMA_LOAD: A.DMA_KIND.LOAD,
        A.OPCODE.DMA_PREFETCH: A.DMA_KIND.PREFETCH,
        A.OPCODE.DMA_STORE: A.DMA_KIND.STORE,
        A.OPCODE.DMA_P2P_PUSH: A.DMA_KIND.P2P_PUSH,
        A.OPCODE.DMA_FILL: A.DMA_KIND.LOCAL_FILL,
    }
    transfers = {}
    for descriptor in program.dma_descriptors:
        command = commands_by_id.get(descriptor.command_id)
        if command is None:
            raise MeshIrError("E_ABI_BOUNDS", "descriptor command unknown", descriptor=descriptor.descriptor_id)
        if command.opcode not in opcode_to_kind:
            raise MeshIrError("E_ABI_ENUM", "command cannot own descriptor", command=command.command_id)
        if opcode_to_kind[command.opcode] != descriptor.kind:
            raise MeshIrError(
                "E_ABI_ENUM",
                "descriptor kind does not match command opcode",
                descriptor=descriptor.descriptor_id,
                kind=descriptor.kind,
            )
        if command.core_id != descriptor.owner_core:
            raise MeshIrError("E_ABI_BOUNDS", "descriptor owner mismatch", descriptor=descriptor.descriptor_id)
        if descriptor.max_burst_beats < 1 or descriptor.max_burst_beats > arch.axi_max_burst_beats:
            raise MeshIrError(
                "E_DMA_RANGE",
                "max_burst_beats outside arch bounds",
                descriptor=descriptor.descriptor_id,
            )
        useful = checked_mul(descriptor.rows, descriptor.row_bytes)
        if useful != descriptor.useful_bytes:
            raise MeshIrError("E_ABI_OVERFLOW", "useful_bytes != rows * row_bytes", descriptor=descriptor.descriptor_id)
        if descriptor.physical_storage_bytes < descriptor.useful_bytes:
            raise MeshIrError("E_DMA_RANGE", "physical < useful", descriptor=descriptor.descriptor_id)
        if descriptor.rows > 1:
            for stride in (descriptor.src_stride_bytes, descriptor.dst_stride_bytes):
                if stride < descriptor.row_bytes:
                    raise MeshIrError(
                        "E_DMA_RANGE",
                        "stride < row_bytes implies row overlap",
                        descriptor=descriptor.descriptor_id,
                    )

        _verify_endpoint(program, arch, descriptor, descriptor.src, "src")
        _verify_endpoint(program, arch, descriptor, descriptor.dst, "dst")

        local = False
        for endpoint in (descriptor.src, descriptor.dst):
            if endpoint.memory_space in (A.MEMORY_SPACE.CORE_SRAM, A.MEMORY_SPACE.PEER_SRAM):
                if endpoint.owner_core == descriptor.owner_core:
                    local = True
        if not local:
            raise MeshIrError(
                "E_DMA_RANGE",
                "at least one endpoint must belong to issuing core",
                descriptor=descriptor.descriptor_id,
            )

        remote = (A.MEMORY_SPACE.HBM, A.MEMORY_SPACE.HOST_SHARED)
        if descriptor.kind in (A.DMA_KIND.LOAD, A.DMA_KIND.PREFETCH):
            if descriptor.src.memory_space not in remote:
                raise MeshIrError(
                    "E_DMA_RANGE",
                    "LOAD/PREFETCH source must be HBM or HOST_SHARED",
                    descriptor=descriptor.descriptor_id,
                )
            if descriptor.dst.memory_space != A.MEMORY_SPACE.CORE_SRAM:
                raise MeshIrError(
                    "E_DMA_RANGE",
                    "LOAD/PREFETCH destination must be CORE_SRAM",
                    descriptor=descriptor.descriptor_id,
                )
        elif descriptor.kind == A.DMA_KIND.STORE:
            if descriptor.src.memory_space != A.MEMORY_SPACE.CORE_SRAM:
                raise MeshIrError(
                    "E_DMA_RANGE",
                    "STORE source must be CORE_SRAM",
                    descriptor=descriptor.descriptor_id,
                )
            if descriptor.dst.memory_space not in remote:
                raise MeshIrError(
                    "E_DMA_RANGE",
                    "STORE destination must be HBM or HOST_SHARED",
                    descriptor=descriptor.descriptor_id,
                )
        elif descriptor.kind == A.DMA_KIND.LOCAL_FILL:
            if descriptor.dst.memory_space != A.MEMORY_SPACE.CORE_SRAM:
                raise MeshIrError(
                    "E_DMA_RANGE",
                    "FILL destination must be CORE_SRAM",
                    descriptor=descriptor.descriptor_id,
                )
        if descriptor.kind == A.DMA_KIND.P2P_PUSH:
            if descriptor.dst.memory_space != A.MEMORY_SPACE.PEER_SRAM:
                raise MeshIrError(
                    "E_DMA_RANGE",
                    "P2P destination must be PEER_SRAM",
                    descriptor=descriptor.descriptor_id,
                )
            if descriptor.dst.owner_core == descriptor.owner_core:
                raise MeshIrError("E_DMA_RANGE", "P2P to self", descriptor=descriptor.descriptor_id)
            if descriptor.transfer_id in transfers:
                raise MeshIrError("E_ABI_DUPLICATE", "duplicate transfer_id", transfer=descriptor.transfer_id)
            transfers[descriptor.transfer_id] = descriptor
        if descriptor.completion_event not in {e.event_id for e in program.events}:
            raise MeshIrError(
                "E_ABI_BOUNDS",
                "descriptor completion event unknown",
                descriptor=descriptor.descriptor_id,
            )

    descriptors_per_command = {}
    for descriptor in program.dma_descriptors:
        descriptors_per_command[descriptor.command_id] = (
            descriptors_per_command.get(descriptor.command_id, 0) + 1
        )
    for command in program.commands:
        if command.opcode not in opcode_to_kind:
            continue
        count = descriptors_per_command.get(command.command_id, 0)
        if count == 0:
            raise MeshIrError(
                "E_ABI_BOUNDS",
                "DMA command has no descriptor",
                command=command.command_id,
            )
        if count > 1:
            raise MeshIrError(
                "E_ABI_DUPLICATE",
                "multiple descriptors for DMA command",
                command=command.command_id,
            )

    # The local endpoint's shard must be one of the command's operands: the
    # runtime pins and validates operands, the descriptor moves bytes at the
    # endpoint -- they must designate the same SRAM object.
    commands_by_id = {c.command_id: c for c in program.commands}
    for descriptor in program.dma_descriptors:
        command = commands_by_id[descriptor.command_id]
        local = None
        for endpoint in (descriptor.src, descriptor.dst):
            if endpoint.memory_space in (
                A.MEMORY_SPACE.CORE_SRAM,
                A.MEMORY_SPACE.PEER_SRAM,
            ) and endpoint.owner_core == descriptor.owner_core:
                local = endpoint
        if local is None or local.shard_id == 0:
            continue
        operand_shards = set()
        operand_span = checked_span(
            command.operand_begin, command.operand_count,
            len(program.command_operands), "operand range out of table",
        )
        for operand in program.command_operands[operand_span]:
            if operand.shard_id:
                operand_shards.add(operand.shard_id)
        if local.shard_id not in operand_shards:
            raise MeshIrError(
                "E_DMA_RANGE",
                "descriptor local endpoint shard is not a command operand",
                descriptor=descriptor.descriptor_id,
                endpoint_shard=local.shard_id,
                operand_shards=sorted(operand_shards),
            )

    recv_wait_transfers = set()
    for command in program.commands:
        if command.opcode == A.OPCODE.RECV_WAIT:
            attr = _attr_of(program, command.attr_index)
            values = dict(zip(attr.payload_fields, attr.payload))
            if values["transfer_id"] not in transfers:
                raise MeshIrError(
                    "E_P2P_UNMATCHED",
                    "RECV_WAIT transfer has no matching P2P push",
                    command=command.command_id,
                    transfer=values["transfer_id"],
                )
            if values["transfer_id"] in recv_wait_transfers:
                raise MeshIrError(
                    "E_P2P_UNMATCHED",
                    "two RECV_WAIT commands reference one transfer",
                    command=command.command_id,
                    transfer=values["transfer_id"],
                )
            recv_wait_transfers.add(values["transfer_id"])


def _verify_endpoint(program: Program, arch: ArchManifest, descriptor, endpoint, which: str) -> None:
    span = _endpoint_span(descriptor, which)
    if endpoint.shard_id:
        shard = next((s for s in program.shards if s.shard_id == endpoint.shard_id), None)
        if shard is None:
            raise MeshIrError(
                "E_ABI_BOUNDS",
                f"endpoint {which} shard unknown",
                descriptor=descriptor.descriptor_id,
                shard=endpoint.shard_id,
            )
        if shard.tensor_id != endpoint.tensor_id:
            raise MeshIrError(
                "E_ABI_BOUNDS",
                f"endpoint {which} shard belongs to a different tensor",
                descriptor=descriptor.descriptor_id,
                endpoint_tensor=endpoint.tensor_id,
                shard_tensor=shard.tensor_id,
            )
        if endpoint.memory_space in (A.MEMORY_SPACE.CORE_SRAM, A.MEMORY_SPACE.PEER_SRAM):
            if shard.owner_core != endpoint.owner_core:
                raise MeshIrError(
                    "E_ABI_BOUNDS",
                    f"endpoint {which} shard owner mismatch",
                    descriptor=descriptor.descriptor_id,
                )
    if endpoint.region_id >= len(arch.regions):
        raise MeshIrError(
            "E_ABI_BOUNDS",
            f"endpoint {which} region unknown",
            descriptor=descriptor.descriptor_id,
            region=endpoint.region_id,
        )
    if endpoint.memory_space in (A.MEMORY_SPACE.CORE_SRAM, A.MEMORY_SPACE.PEER_SRAM):
        if endpoint.owner_core not in arch.core_ids:
            raise MeshIrError("E_ABI_BOUNDS", "endpoint core not in arch", descriptor=descriptor.descriptor_id)
        if not endpoint.shard_id:
            raise MeshIrError(
                "E_DMA_RANGE",
                f"endpoint {which} has no shard view",
                descriptor=descriptor.descriptor_id,
            )
        allocation = next(
            (a for a in program.allocations
             if a.allocation_id == shard.allocation_id), None)
        if allocation is None:
            raise MeshIrError(
                "E_ABI_BOUNDS", f"endpoint {which} shard allocation unknown",
                descriptor=descriptor.descriptor_id)
        view_start = allocation.offset_bytes + shard.allocation_offset
        view_end = view_start + shard.span_bytes
        if endpoint.offset_bytes < view_start or \
                endpoint.offset_bytes > view_end or \
                span > view_end - endpoint.offset_bytes:
            raise MeshIrError(
                "E_DMA_RANGE",
                f"endpoint {which} span escapes its shard view",
                descriptor=descriptor.descriptor_id,
            )
        region = arch.region_by_id(endpoint.region_id)
        if not region.tile_bytes:
            raise MeshIrError(
                "E_DMA_RANGE",
                f"endpoint {which} memory space requires an SRAM aperture "
                "region",
                descriptor=descriptor.descriptor_id,
            )
        capacity = region.tile_bytes if region.tile_bytes else region.bytes
        if endpoint.offset_bytes > capacity or \
                span > capacity - endpoint.offset_bytes:
            raise MeshIrError(
                "E_DMA_RANGE",
                f"endpoint {which} exceeds SRAM tile",
                descriptor=descriptor.descriptor_id,
            )
    else:
        expected_kind = (
            "HBM" if endpoint.memory_space == A.MEMORY_SPACE.HBM
            else "HOST_SHARED"
        )
        if endpoint.owner_core != 0xFFFF:
            raise MeshIrError(
                "E_ABI_BOUNDS",
                f"endpoint {which} owner must use the no-core sentinel",
                descriptor=descriptor.descriptor_id,
            )
        region = arch.region_by_id(endpoint.region_id)
        if region.kind != expected_kind:
            raise MeshIrError(
                "E_DMA_RANGE",
                f"endpoint {which} region kind does not match memory space",
                descriptor=descriptor.descriptor_id,
            )
        if endpoint.offset_bytes > region.bytes or \
                span > region.bytes - endpoint.offset_bytes:
            raise MeshIrError(
                "E_DMA_RANGE",
                f"endpoint {which} exceeds memory region",
                descriptor=descriptor.descriptor_id,
            )


def _verify_relocations(program: Program, arch: ArchManifest) -> None:
    tensor_ids = {t.tensor_id for t in program.tensors}
    for relocation in program.relocations:
        if relocation.tensor_id not in tensor_ids:
            raise MeshIrError("E_RELOCATION", "relocation tensor unknown", tensor=relocation.tensor_id)
        if relocation.region_id >= len(arch.regions):
            raise MeshIrError("E_RELOCATION", "relocation region unknown", region=relocation.region_id)
        region = arch.region_by_id(relocation.region_id)
        if relocation.offset_bytes >= region.bytes:
            raise MeshIrError("E_RELOCATION", "relocation offset outside region")


def _verify_expected_traffic(program: Program, arch: ArchManifest) -> None:
    commands_by_id = {c.command_id: c for c in program.commands}
    seen = set()
    for descriptor in program.dma_descriptors:
        if descriptor.descriptor_id not in {r.descriptor_id for r in program.expected_traffic}:
            raise MeshIrError(
                "E_TRAFFIC_MISMATCH",
                "descriptor has no expected traffic row",
                descriptor=descriptor.descriptor_id,
            )
    for row in program.expected_traffic:
        if row.descriptor_id in seen:
            raise MeshIrError("E_ABI_DUPLICATE", "duplicate traffic row", descriptor=row.descriptor_id)
        seen.add(row.descriptor_id)
        descriptor = next((d for d in program.dma_descriptors if d.descriptor_id == row.descriptor_id), None)
        if descriptor is None:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic row references unknown descriptor", descriptor=row.descriptor_id)
        if row.command_id != descriptor.command_id:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic row command mismatch", descriptor=row.descriptor_id)
        if row.kind != descriptor.kind:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic row kind mismatch", descriptor=row.descriptor_id)
        if row.useful_bytes != descriptor.useful_bytes:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic row useful bytes mismatch", descriptor=row.descriptor_id)

        if descriptor.kind == A.DMA_KIND.LOCAL_FILL:
            expected = dict(bursts=0, beat_bytes=0, segments=0)
        else:
            # The burst plan is shaped by the REMOTE address/stride: source
            # for reads, destination for writes (STORE/P2P).
            is_read = descriptor.kind in (A.DMA_KIND.LOAD, A.DMA_KIND.PREFETCH)
            remote = descriptor.src if is_read else descriptor.dst
            remote_stride = descriptor.src_stride_bytes if is_read else descriptor.dst_stride_bytes
            plan = plan_descriptor(
                descriptor.row_bytes,
                descriptor.rows,
                remote.offset_bytes,
                remote_stride,
                arch.axi_data_bytes,
                min(descriptor.max_burst_beats, arch.axi_max_burst_beats),
            )
            expected = dict(
                bursts=len(plan.bursts),
                beat_bytes=plan.beat_bytes,
                segments=plan.segments,
            )
        is_write = descriptor.kind in (A.DMA_KIND.STORE, A.DMA_KIND.P2P_PUSH)
        if row.bursts != expected["bursts"] or row.physical_beat_bytes != expected["beat_bytes"]:
            raise MeshIrError(
                "E_TRAFFIC_MISMATCH",
                "traffic row burst plan mismatch",
                descriptor=row.descriptor_id,
                row_bursts=row.bursts,
                expected_bursts=expected["bursts"],
            )
        if row.segments != expected["segments"]:
            raise MeshIrError(
                "E_TRAFFIC_MISMATCH",
                "traffic row segments mismatch",
                descriptor=row.descriptor_id,
                row_segments=row.segments,
                expected_segments=expected["segments"],
            )
        if row.aw_count != (expected["bursts"] if is_write else 0):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic row AW count mismatch", descriptor=row.descriptor_id)
        if row.ar_count != (0 if is_write else expected["bursts"]):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "traffic row AR count mismatch", descriptor=row.descriptor_id)
        if row.b_count != row.aw_count:
            raise MeshIrError("E_TRAFFIC_MISMATCH", "B count must equal AW count", descriptor=row.descriptor_id)
        if row.r_beats != (0 if is_write else expected["beat_bytes"] // arch.axi_data_bytes):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "R beat count mismatch", descriptor=row.descriptor_id)
        if row.w_beats != (expected["beat_bytes"] // arch.axi_data_bytes if is_write else 0):
            raise MeshIrError("E_TRAFFIC_MISMATCH", "W beat count mismatch", descriptor=row.descriptor_id)


def _verify_lifecycle(program: Program) -> None:
    for entrypoint in program.entrypoints:
        key = (entrypoint.lifecycle_core_id, entrypoint.lifecycle_stream_id)
        stream = next((s for s in program.streams if (s.core_id, s.stream_id) == key), None)
        if stream is None:
            raise MeshIrError("E_LIFECYCLE", "lifecycle stream missing", entrypoint=entrypoint.entrypoint_id)
        commands = _stream_commands(program, stream)
        begins = [c for c in commands if c.opcode == A.OPCODE.REQUEST_BEGIN]
        ends = [c for c in commands if c.opcode == A.OPCODE.REQUEST_END]
        if len(begins) != 1 or len(ends) != 1:
            raise MeshIrError(
                "E_LIFECYCLE",
                "lifecycle stream must hold exactly one REQUEST_BEGIN and one REQUEST_END",
                entrypoint=entrypoint.entrypoint_id,
            )
        begin_index = commands.index(begins[0])
        end_index = commands.index(ends[0])
        if begin_index != 0:
            raise MeshIrError("E_LIFECYCLE", "REQUEST_BEGIN must dominate all instance work")
        if end_index < begin_index:
            raise MeshIrError("E_LIFECYCLE", "REQUEST_END before REQUEST_BEGIN")
        if not ends[0].signal_event:
            raise MeshIrError("E_LIFECYCLE", "REQUEST_END must signal an event")
        if begins[0].signal_event == 0:
            raise MeshIrError("E_LIFECYCLE", "REQUEST_BEGIN must signal an event")


def _wait_closure(program: Program) -> dict:
    waits_by_command = {}
    for command in program.commands:
        span = checked_span(
            command.wait_begin, command.wait_count,
            len(program.command_waits), "wait range out of table",
        )
        waits_by_command[command.command_id] = [
            w.event_id for w in program.command_waits[span]
        ]
    return waits_by_command


def _verify_acyclic(program: Program) -> None:
    producers = {}
    for command in program.commands:
        if command.signal_event:
            producers.setdefault(command.signal_event, []).append(command.command_id)
    for descriptor in program.dma_descriptors:
        producers.setdefault(descriptor.completion_event, []).append(descriptor.command_id)

    waits_by_command = _wait_closure(program)
    edges = {}
    for command in program.commands:
        targets = set()
        for event_id in waits_by_command[command.command_id]:
            targets.update(producers.get(event_id, ()))
        edges[command.command_id] = targets

    state = {}

    def visit(node: int, stack: tuple) -> None:
        mark = state.get(node)
        if mark == 1:
            cycle = stack[stack.index(node):] + (node,)
            raise MeshIrError("E_DEPENDENCY_CYCLE", "command dependency cycle", cycle=list(cycle))
        if mark == 2:
            return
        state[node] = 1
        for target in sorted(edges.get(node, ())):
            visit(target, stack + (node,))
        state[node] = 2

    for command_id in sorted(edges):
        visit(command_id, ())

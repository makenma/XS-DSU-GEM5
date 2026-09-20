"""Cross-language mutation corpus: every mutation must produce IDENTICAL
{accepted, error_code} on the Python and the C++ readers.

The C++ side runs through a standalone ASan/UBSan build of the real
decoder + verifier (tests/golden/support/mutation_driver.cc), so parser
overflow inputs additionally prove "deterministic error, never a crash".
"""

import copy
import dataclasses
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_barrier_e2e_program, build_dual_core_program, build_repeat_program, build_single_core_program, build_zero_dma_program
from mesh_ir.model import MeshIrError
from mesh_ir.scheduled.model import ScheduledDependencyKind, StreamOrderSource
from mesh_ir.scheduled.verify import verify_program

ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
REPO = Path(__file__).resolve().parents[4]


def _recompute(blob: bytearray) -> bytearray:
    dir_off = struct.unpack_from("<Q", blob, 24)[0]
    count = struct.unpack_from("<I", blob, 32)[0]
    import hashlib

    for index in range(count):
        entry = dir_off + index * 40
        sec_off, size = struct.unpack_from("<QQ", blob, entry + 8)
        crc = zlib.crc32(bytes(blob[sec_off : sec_off + size])) & 0xFFFFFFFF
        struct.pack_into("<I", blob, entry + 32, crc)
    blob[72:104] = hashlib.sha256(bytes(blob[128:])).digest()
    return blob


def _sections(blob):
    dir_off = struct.unpack_from("<Q", blob, 24)[0]
    count = struct.unpack_from("<I", blob, 32)[0]
    out = []
    for index in range(count):
        entry = dir_off + index * 40
        stype = struct.unpack_from("<H", blob, entry)[0]
        record_bytes = struct.unpack_from("<I", blob, entry + 4)[0]
        off, size, cnt = struct.unpack_from("<QQQ", blob, entry + 8)
        out.append({"entry": entry, "type": stype, "off": off, "size": size,
                    "count": cnt, "record_bytes": record_bytes})
    return out


def _section_offsets(blob):
    return {row["type"]: row for row in _sections(blob)}


def _python_verdict(blob: bytes):
    try:
        program = decode_program(blob)
    except MeshIrError as err:
        return f"DECODE:{err.code}"
    try:
        verify_program(program, _python_verdict.arch)
    except MeshIrError as err:
        return f"VERIFY:{err.code}"
    return "ACCEPTED"


def _write_arch_facts(path: Path, arch):
    def _dtype_mask(table):
        mask = 0
        from mesh_ir.generated import abi as _A
        for name in table:
            value = getattr(_A.DTYPE, name.upper(), None)
            if value:
                mask |= 1 << (value - 1)
        return mask

    lines = [
        arch.digest().hex(),
        str(arch.sram_bytes),
        str(arch.sram_banks),
        str(arch.sram_base_alignment_bytes),
        str(arch.axi_data_bytes),
        str(arch.axi_max_burst_beats),
        ",".join(str(c) for c in arch.core_ids),
        f"{_dtype_mask(arch.tensor_macs_per_cycle)} "
        f"{_dtype_mask(arch.vector_elements_per_cycle)} "
        f"{_dtype_mask(arch.reduce_ops_per_cycle)}",
    ]
    for region_id, region in enumerate(arch.regions):
        kind = {"HBM": 0, "HOST_SHARED": 1}.get(region.kind, 2)
        lines.append(
            f"{region_id} {region.base} {region.bytes} "
            f"{region.tile_stride if region.tile_stride else 0} "
            f"{region.tile_bytes if region.tile_bytes else 0} {kind}"
        )
    path.write_text("\n".join(lines) + "\n")


def _cpp_verdict(blob: bytes, driver: str, tmp: Path, arch):
    path = tmp / "case.mshb"
    path.write_bytes(blob)
    facts = tmp / "arch_facts.txt"
    _write_arch_facts(facts, arch)
    environment = os.environ.copy()
    environment["ASAN_OPTIONS"] = "detect_leaks=0"
    result = subprocess.run(
        [driver, str(path), str(facts)], capture_output=True, text=True,
        timeout=30, env=environment,
    )
    if result.returncode != 0 or result.stderr.strip() or not result.stdout.strip():
        return f"CRASH:rc={result.returncode}:{result.stderr.strip()[:80]}"
    return result.stdout.strip()


@dataclasses.dataclass(frozen=True)
class MutationCase:
    name: str
    blob: bytes
    expected: str
    wire_path: str
    baseline: bytes | None = None


@dataclasses.dataclass(frozen=True)
class _CorpusInputs:
    golden_blob: bytes
    mutation_cases: tuple[MutationCase, ...]
    transport_cases: tuple[MutationCase, ...]


def _replace_item(values, index, value):
    return (*values[:index], value, *values[index + 1 :])


def _replace_transport(program, table, index, **changes):
    values = getattr(program, table)
    return dataclasses.replace(
        program,
        **{table: _replace_item(values, index, dataclasses.replace(values[index], **changes))},
    )


def _encoded_domain_case(name, expected, wire_path, program):
    return MutationCase(
        name,
        encode_program(_with_fresh_semantic_sha(program)),
        expected,
        wire_path,
    )


def _encoded_transport_case(name, expected, wire_path, baseline_blob, program):
    return dataclasses.replace(
        _encoded_domain_case(name, expected, wire_path, program),
        baseline=baseline_blob,
    )


def _mutations(blob: bytes, arch):
    base = bytearray(blob)
    program = decode_program(blob)
    secs = _section_offsets(base)
    st = {
        "entrypoints": A.SECTION_TYPE.ENTRYPOINTS, "profiles": A.SECTION_TYPE.PROFILES,
        "tensors": A.SECTION_TYPE.TENSORS, "shards": A.SECTION_TYPE.SHARDS,
        "allocations": A.SECTION_TYPE.ALLOCATIONS, "streams": A.SECTION_TYPE.STREAMS,
        "commands": A.SECTION_TYPE.COMMANDS, "waits": A.SECTION_TYPE.COMMAND_WAITS,
        "operands": A.SECTION_TYPE.COMMAND_OPERANDS, "events": A.SECTION_TYPE.EVENTS,
        "descriptors": A.SECTION_TYPE.DMA_DESCRIPTORS, "attrs": A.SECTION_TYPE.OP_ATTRS,
        "relocations": A.SECTION_TYPE.RELOCATIONS,
        "traffic": A.SECTION_TYPE.EXPECTED_TRAFFIC,
    }
    reserved_tables = (
        ("ENTRYPOINTS", "entrypoints", ("reserved",)),
        ("PROFILES", "profiles", ("reserved",)),
        ("TENSORS", "tensors", ("reserved",)),
        ("SHARDS", "shards", ("reserved", "reserved2")),
        ("STREAMS", "streams", ("reserved",)),
        ("COMMAND_OPERANDS", "operands", ("reserved",)),
        ("EVENTS", "events", ("reserved", "reserved2")),
        ("DMA_DESCRIPTORS", "descriptors", ("reserved", "reserved2")),
        ("RELOCATIONS", "relocations", ("reserved", "reserved2")),
        ("EXPECTED_TRAFFIC", "traffic", ("reserved", "reserved2")),
    )
    for record, short, fields in reserved_tables:
        sec = secs[st[short]]
        if sec["count"] == 0:
            continue
        offsets = getattr(A, f"{record}_FIELD_OFFSETS")
        for field in fields:
            m = bytearray(base)
            m[sec["off"] + offsets[field]] = 1
            yield MutationCase(
                f"{short}_reserved_{field}",
                bytes(_recompute(m)),
                "DECODE:E_ABI_RESERVED",
                f"{record}.{field}",
            )

    commands = secs[st["commands"]]
    m = bytearray(base)
    struct.pack_into("<H", m, commands["off"] + A.COMMANDS_FIELD_OFFSETS["opcode"], 99)
    yield MutationCase(
        "commands_unknown_opcode",
        bytes(_recompute(m)),
        "DECODE:E_ABI_ENUM",
        "COMMANDS[0].opcode",
    )
    m = bytearray(base)
    struct.pack_into("<H", m, commands["off"] + A.COMMANDS_FIELD_OFFSETS["engine"], 99)
    yield MutationCase(
        "commands_unknown_engine",
        bytes(_recompute(m)),
        "DECODE:E_ABI_ENUM",
        "COMMANDS[0].engine",
    )
    yield _encoded_domain_case(
        "commands_unknown_signal_event",
        "VERIFY:E_ABI_BOUNDS",
        "COMMANDS[0].signal_event",
        _replace_transport(program, "commands", 0, signal_event=9999),
    )

    yield _encoded_domain_case(
        "waits_unknown_event",
        "VERIFY:E_ABI_BOUNDS",
        "COMMAND_WAITS[0].event_id",
        _replace_transport(program, "command_waits", 0, event_id=9999),
    )

    for field in ("tensor_id", "shard_id", "allocation_id"):
        yield _encoded_domain_case(
            f"operands_unknown_{field}",
            "VERIFY:E_ABI_BOUNDS",
            f"COMMAND_OPERANDS[0].{field}",
            _replace_transport(program, "command_operands", 0, **{field: 9999}),
        )

    descriptors = secs[st["descriptors"]]
    endpoint_base = A.DMA_DESCRIPTORS_FIELD_OFFSETS["src"]
    m = bytearray(base)
    struct.pack_into(
        "<H", m, descriptors["off"] + endpoint_base + A.DMA_ENDPOINT_FIELD_OFFSETS["memory_space"], 99
    )
    yield MutationCase(
        "descriptors_unknown_space",
        bytes(_recompute(m)),
        "DECODE:E_ABI_ENUM",
        "DMA_DESCRIPTORS[0].src.memory_space",
    )
    descriptor = program.dma_descriptors[0]
    yield _encoded_domain_case(
        "descriptors_unknown_region",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[0].src.region_id",
        _replace_transport(
            program,
            "dma_descriptors",
            0,
            src=dataclasses.replace(descriptor.src, region_id=99),
        ),
    )
    offset_bytes = descriptor.src.offset_bytes | (0xFF << 56)
    yield _encoded_domain_case(
        "descriptors_offset_wrap",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[0].src.offset_bytes",
        _replace_transport(
            program,
            "dma_descriptors",
            0,
            src=dataclasses.replace(descriptor.src, offset_bytes=offset_bytes),
        ),
    )
    yield _encoded_domain_case(
        "descriptors_kind_mismatch",
        "VERIFY:E_ABI_ENUM",
        "DMA_DESCRIPTORS[0].kind",
        _replace_transport(program, "dma_descriptors", 0, kind=A.DMA_KIND.STORE),
    )

    attrs = secs[st["attrs"]]
    m = bytearray(base)
    struct.pack_into("<H", m, attrs["off"] + A.OP_ATTRS_FIELD_OFFSETS["kind"], 99)
    yield MutationCase(
        "attrs_unknown_kind",
        bytes(_recompute(m)),
        "DECODE:E_ABI_ENUM",
        "OP_ATTRS[0].kind",
    )
    m = bytearray(base)
    struct.pack_into("<H", m, attrs["off"] + A.OP_ATTRS_FIELD_OFFSETS["reserved"], 1)
    yield MutationCase(
        "attrs_reserved",
        bytes(_recompute(m)),
        "DECODE:E_ABI_RESERVED",
        "OP_ATTRS[0].reserved",
    )
    repeat_blob = bytearray(encode_program(build_repeat_program(arch)))
    repeat_attrs = _section_offsets(repeat_blob)[A.SECTION_TYPE.OP_ATTRS]
    repeat_index = next(
        index
        for index in range(repeat_attrs["count"])
        if struct.unpack_from(
            "<H", repeat_blob, repeat_attrs["off"] + index * A.OP_ATTRS_BYTES
        )[0] == A.ATTR_KIND.REPEAT_V1
    )
    padding_offset = (
        repeat_attrs["off"] + repeat_index * A.OP_ATTRS_BYTES +
        A.OP_ATTRS_FIELD_OFFSETS["payload"] + A.REPEAT_V1_BYTES
    )
    repeat_blob[padding_offset] = 1
    yield MutationCase(
        "attrs_payload_padding",
        bytes(_recompute(repeat_blob)),
        "DECODE:E_ABI_RESERVED",
        f"OP_ATTRS[{repeat_index}].payload_padding",
    )
    m = bytearray(base)
    struct.pack_into(
        "<H", m, attrs["off"] + 4 + A.GEMM_V1_FIELD_OFFSETS["dtype"], 99
    )
    yield MutationCase(
        "attrs_unknown_gemm_dtype",
        bytes(_recompute(m)),
        "DECODE:E_ABI_ENUM",
        "OP_ATTRS[0].GEMM_V1.dtype",
    )

    yield _encoded_domain_case(
        "traffic_burst_plan_mismatch",
        "VERIFY:E_TRAFFIC_MISMATCH",
        "EXPECTED_TRAFFIC[0].bursts",
        _replace_transport(program, "expected_traffic", 0, bursts=1 << 30),
    )

    yield _encoded_domain_case(
        "allocations_alignment_not_pow2",
        "VERIFY:E_ABI_BOUNDS",
        "ALLOCATIONS[0].alignment_bytes",
        _replace_transport(program, "allocations", 0, alignment_bytes=3),
    )

    streams = secs[st["streams"]]
    m = bytearray(base)
    struct.pack_into("<H", m, streams["off"] + A.STREAMS_FIELD_OFFSETS["flags"], 0xFF)
    yield MutationCase(
        "streams_unknown_flag_bits",
        bytes(_recompute(m)),
        "DECODE:E_ABI_ENUM",
        "STREAMS[0].flags",
    )

    yield _encoded_domain_case(
        "entrypoints_unknown_stream",
        "VERIFY:E_STREAM_CONTRACT",
        "ENTRYPOINTS[0].lifecycle_stream_id",
        _replace_transport(program, "entrypoints", 0, lifecycle_stream_id=99),
    )

    m = bytearray(base)
    struct.pack_into("<Q", m, 104, A.REQUIRED_FEATURES | (1 << 63))
    yield MutationCase(
        "header_required_feature_bit",
        bytes(m),
        "DECODE:E_ABI_VERSION",
        "HEADER.required_features",
    )

    m = bytearray(base); m[struct.unpack_from("<Q", m, 24)[0]] = 77
    yield MutationCase(
        "directory_unknown_section_type",
        bytes(_recompute(m)),
        "DECODE:E_ABI_ENUM",
        "SECTION_DIRECTORY[0].section_type",
    )

    strings = secs[A.SECTION_TYPE.STRINGS]
    if strings["count"] >= 2:
        m = bytearray(base)
        struct.pack_into("<I", m, strings["off"] + 4, 1)
        yield MutationCase(
            "strings_directory_not_dense",
            bytes(_recompute(m)),
            "DECODE:E_ABI_ORDER",
            "STRINGS.directory[0].offset",
        )
        m = bytearray(base)
        struct.pack_into("<I", m, strings["off"] + 8, 1 << 30)
        yield MutationCase(
            "strings_directory_out_of_blob",
            bytes(_recompute(m)),
            "DECODE:E_ABI_SECTION_RANGE",
            "STRINGS.directory[0].size",
        )

    yield _encoded_domain_case(
        "events_producer_mismatch",
        "VERIFY:E_EVENT_NO_PRODUCER",
        "EVENTS[0].producer_command_id",
        _replace_transport(program, "events", 0, producer_command_id=0),
    )
    events_sec = secs[st["events"]]
    m = bytearray(base)
    struct.pack_into(
        "<H", m, events_sec["off"] + A.EVENTS_FIELD_OFFSETS["kind"], 99
    )
    yield MutationCase(
        "events_unknown_kind",
        bytes(_recompute(m)),
        "DECODE:E_ABI_ENUM",
        "EVENTS[0].kind",
    )

    yield _encoded_domain_case(
        "allocations_non_sram_space",
        "VERIFY:E_ABI_BOUNDS",
        "ALLOCATIONS[0].memory_space",
        _replace_transport(
            program,
            "allocations",
            0,
            memory_space=A.MEMORY_SPACE.HBM,
        ),
    )
    yield _encoded_domain_case(
        "allocations_offset_wrap",
        "VERIFY:E_ABI_OVERFLOW",
        "ALLOCATIONS[0].offset_bytes",
        _replace_transport(
            program,
            "allocations",
            0,
            offset_bytes=(1 << 64) - 64,
        ),
    )

    yield _encoded_domain_case(
        "descriptors_physical_lt_useful",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[0].physical_storage_bytes",
        _replace_transport(
            program,
            "dma_descriptors",
            0,
            physical_storage_bytes=0,
        ),
    )
    yield _encoded_domain_case(
        "descriptors_unknown_completion_event",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[0].completion_event",
        _replace_transport(program, "dma_descriptors", 0, completion_event=9999),
    )

    yield _encoded_domain_case(
        "descriptors_command_id_collision",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[1].command_id",
        _replace_transport(
            program,
            "dma_descriptors",
            1,
            command_id=program.dma_descriptors[0].command_id,
        ),
    )

    yield _encoded_domain_case(
        "profiles_rank_gt8",
        "VERIFY:E_ABI_BOUNDS",
        "PROFILES[0].rank",
        _replace_transport(program, "profiles", 0, rank=9),
    )
    yield _encoded_domain_case(
        "shards_offset_exceeds_allocation",
        "VERIFY:E_ABI_BOUNDS",
        "SHARDS[0].allocation_offset",
        _replace_transport(
            program,
            "shards",
            0,
            allocation_offset=(1 << 64) - 64,
        ),
    )

    profiles = secs[st["profiles"]]
    m = bytearray(base)
    struct.pack_into("<I", m, profiles["entry"] + 4, A.PROFILES_BYTES // 2)
    struct.pack_into("<Q", m, profiles["entry"] + 24, profiles["count"] * 2)
    yield MutationCase(
        "profiles_fixed_record_size",
        bytes(_recompute(m)),
        "DECODE:E_ABI_SECTION_RANGE",
        "SECTION_DIRECTORY.PROFILES.record_bytes",
    )

    yield _encoded_domain_case(
        "traffic_command_mismatch",
        "VERIFY:E_TRAFFIC_MISMATCH",
        "EXPECTED_TRAFFIC[0].command_id",
        _replace_transport(program, "expected_traffic", 0, command_id=9999),
    )
    yield _encoded_domain_case(
        "traffic_kind_mismatch",
        "VERIFY:E_TRAFFIC_MISMATCH",
        "EXPECTED_TRAFFIC[0].kind",
        _replace_transport(program, "expected_traffic", 0, kind=A.DMA_KIND.LOCAL_FILL),
    )
    yield _encoded_domain_case(
        "traffic_segments_mismatch",
        "VERIFY:E_TRAFFIC_MISMATCH",
        "EXPECTED_TRAFFIC[0].segments",
        _replace_transport(program, "expected_traffic", 0, segments=99),
    )
    yield _encoded_domain_case(
        "traffic_w_beats_mismatch",
        "VERIFY:E_TRAFFIC_MISMATCH",
        "EXPECTED_TRAFFIC[0].w_beats",
        _replace_transport(program, "expected_traffic", 0, w_beats=1 << 30),
    )

    repeat_attr = next(
        attr
        for attr in build_repeat_program(arch).op_attrs
        if attr.kind == A.ATTR_KIND.REPEAT_V1
    )
    yield _encoded_domain_case(
        "attrs_opcode_binding_mismatch",
        "VERIFY:E_ABI_ENUM",
        "OP_ATTRS[0].kind",
        _replace_transport(program, "op_attrs", 0, **dataclasses.asdict(repeat_attr)),
    )

    if strings["count"] >= 1:
        first_off = struct.unpack_from("<I", base, strings["off"] + 4)[0]
        first_len = struct.unpack_from("<I", base, strings["off"] + 8)[0]
        if first_len >= 1:
            m = bytearray(base)
            dir_span = 4 + strings["count"] * 8
            m[strings["off"] + dir_span + first_off] = 0xFF
            yield MutationCase(
                "strings_invalid_utf8",
                bytes(_recompute(m)),
                "DECODE:E_ABI_CORRUPT",
                "STRINGS.blob[0]",
            )

    gemm_index = next(
        index
        for index, attr in enumerate(program.op_attrs)
        if attr.kind == A.ATTR_KIND.GEMM_V1
    )
    gemm_attr = program.op_attrs[gemm_index]
    values = dict(zip(gemm_attr.payload_fields, gemm_attr.payload))
    values.update(m=1 << 21, n=1 << 21, k=1 << 21)
    candidate = _replace_transport(
        program,
        "op_attrs",
        gemm_index,
        payload=tuple(values[field] for field in gemm_attr.payload_fields),
    )
    yield _encoded_domain_case(
        "gemm_work_2e63",
        "VERIFY:E_ABI_OVERFLOW",
        f"OP_ATTRS[{gemm_index}].GEMM_V1.mnk",
        candidate,
    )


def _build_dma_barrier_completion_program(arch):
    from mesh_ir.analysis.sram import SramAllocation
    from mesh_ir.builder import ProgramBuilder
    from mesh_ir.ir.kernel_ir import BarrierAttrs, ControlToken, KernelMemoryRecords, KernelOp, KernelOpcode
    from mesh_ir.scheduled.model import AuthoredProgramOrigin, ExternalSlotBacking, HaltAttrs, LocalAllocationBacking, RequestBeginAttrs, RequestEndAttrs

    base = build_single_core_program(arch)
    semantics = base.semantics
    last = semantics.kernel_ops[-1]
    barrier = KernelOp(
        last.op_id + 1,
        0,
        "barrier:descriptor-completion",
        KernelOpcode.BARRIER,
        0,
        0,
        (),
        (),
        BarrierAttrs((0,)),
        (last.done_token,),
        last.done_token + 1,
    )
    records = KernelMemoryRecords(
        semantics.kernel_tensors,
        semantics.computations,
        semantics.placements,
        semantics.logical_shards,
        semantics.partial_sums,
        semantics.objects,
        semantics.views,
        semantics.states,
        (*semantics.tokens, ControlToken(barrier.done_token)),
        (*semantics.kernel_ops, barrier),
    )
    allocation_object_ids = {
        item.backing.allocation_id: item.object_id
        for item in semantics.object_backings
        if type(item.backing) is LocalAllocationBacking
    }
    allocations = tuple(
        SramAllocation(
            allocation.allocation_id,
            allocation_object_ids[allocation.allocation_id],
            allocation.owner_core,
            allocation.offset_bytes,
            allocation.size_bytes,
            allocation.alignment_bytes,
        )
        for allocation in base.allocations
    )
    builder = ProgramBuilder(
        arch, AuthoredProgramOrigin("mutation", "dma_barrier_completion", 1)
    )
    variant = builder.variant(
        "main",
        "dma_barrier",
        "dma_barrier_completion:main",
        records=records,
        allocations=allocations,
        external_backings=tuple(
            item
            for item in semantics.object_backings
            if type(item.backing) is ExternalSlotBacking
        ),
        binding_slots=semantics.binding_slots,
    )
    stream = variant.stream(
        0,
        0,
        A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    stream.control_command(RequestBeginAttrs())
    for operation in records.ops:
        if operation.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW):
            stream.kernel_command(operation.op_id)
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return builder.build()


def _transport_cases(arch):
    single = build_single_core_program(arch)
    single_blob = encode_program(single)
    dual = build_dual_core_program(arch)
    dual_blob = encode_program(dual)
    barrier = build_barrier_e2e_program(arch)
    barrier_blob = encode_program(barrier)
    repeat = build_repeat_program(arch)
    repeat_blob = encode_program(repeat)
    dma_barrier = _build_dma_barrier_completion_program(arch)
    dma_barrier_blob = encode_program(dma_barrier)

    program = single
    baseline = single_blob
    halt = next(item for item in program.commands if item.opcode == A.OPCODE.HALT)
    yield _encoded_transport_case(
        "events_multiple_producers",
        "VERIFY:E_EVENT_MULTIPLE_PRODUCERS",
        f"COMMANDS[{halt.command_id - 1}].signal_event",
        baseline,
        _replace_transport(program, "commands", halt.command_id - 1, signal_event=1),
    )
    event = program.events[0]
    yield _encoded_transport_case(
        "events_normal_expected_arrivals",
        "VERIFY:E_ABI_BOUNDS",
        "EVENTS[0].expected_arrivals",
        baseline,
        _replace_transport(program, "events", 0, expected_arrivals=1),
    )

    program = barrier
    baseline = barrier_blob
    event = next(item for item in program.events if item.kind == A.EVENT_KIND.BARRIER)
    yield _encoded_transport_case(
        "events_barrier_arrival_mismatch",
        "VERIFY:E_ABI_BOUNDS",
        f"EVENTS[{event.event_id - 1}].expected_arrivals",
        baseline,
        _replace_transport(
            program,
            "events",
            event.event_id - 1,
            expected_arrivals=event.expected_arrivals + 1,
        ),
    )

    program = dma_barrier
    baseline = dma_barrier_blob
    barrier_event = next(
        item for item in program.events if item.kind == A.EVENT_KIND.BARRIER
    )
    descriptor = program.dma_descriptors[0]
    yield _encoded_transport_case(
        "descriptors_completion_barrier_literal",
        "VERIFY:E_DMA_RANGE",
        f"DMA_DESCRIPTORS[{descriptor.descriptor_id - 1}].completion_event",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            descriptor.descriptor_id - 1,
            completion_event=barrier_event.event_id,
        ),
    )

    program = single
    baseline = single_blob
    yield _encoded_transport_case(
        "streams_command_span_wrap",
        "VERIFY:E_ABI_BOUNDS",
        "STREAMS[0].command_begin,command_count",
        baseline,
        _replace_transport(
            program,
            "streams",
            0,
            command_begin=(1 << 32) - 1,
            command_count=2,
        ),
    )
    yield _encoded_transport_case(
        "streams_in_range_projection_mismatch",
        "VERIFY:E_STREAM_CONTRACT",
        "STREAMS[0].command_begin,command_count",
        baseline,
        _replace_transport(program, "streams", 0, command_begin=1, command_count=6),
    )
    yield _encoded_transport_case(
        "commands_duplicate_id",
        "VERIFY:E_ABI_DUPLICATE",
        "COMMANDS[1].command_id",
        baseline,
        _replace_transport(program, "commands", 1, command_id=1),
    )
    yield _encoded_transport_case(
        "commands_row_order",
        "VERIFY:E_ABI_ORDER",
        "COMMANDS[0],COMMANDS[1]",
        baseline,
        dataclasses.replace(
            program,
            commands=(program.commands[1], program.commands[0], *program.commands[2:]),
        ),
    )

    program = dual
    baseline = dual_blob
    halt = next(
        item
        for item in program.commands
        if item.opcode == A.OPCODE.HALT and item.core_id == 0
    )
    yield _encoded_transport_case(
        "commands_halt_outside_control_stream",
        "VERIFY:E_STREAM_CONTRACT",
        f"COMMANDS[{halt.command_id - 1}].stream_id",
        baseline,
        _replace_transport(program, "commands", halt.command_id - 1, stream_id=1),
    )

    program = single
    baseline = single_blob
    yield _encoded_transport_case(
        "streams_missing_control_flags",
        "VERIFY:E_STREAM_CONTRACT",
        "STREAMS[0].flags",
        baseline,
        _replace_transport(program, "streams", 0, flags=0),
    )
    from mesh_ir.scheduled.model import EventSignalAttrs

    request_end = next(
        item for item in program.commands if item.opcode == A.OPCODE.REQUEST_END
    )
    semantic = program.semantics.command_semantics[request_end.command_id - 1]
    semantic = dataclasses.replace(
        semantic,
        source=dataclasses.replace(
            semantic.source,
            attrs=EventSignalAttrs(request_end.signal_event),
        ),
    )
    yield _encoded_transport_case(
        "commands_missing_request_end",
        "VERIFY:E_LIFECYCLE",
        f"COMMANDS[{request_end.command_id - 1}].opcode",
        baseline,
        dataclasses.replace(
            _replace_transport(
                program,
                "commands",
                request_end.command_id - 1,
                opcode=A.OPCODE.EVENT_SIGNAL,
            ),
            semantics=dataclasses.replace(
                program.semantics,
                command_semantics=_replace_item(
                    program.semantics.command_semantics,
                    request_end.command_id - 1,
                    semantic,
                ),
            ),
        ),
    )
    command = program.commands[3]
    yield _encoded_transport_case(
        "commands_wait_span_wrap",
        "VERIFY:E_ABI_BOUNDS",
        f"COMMANDS[{command.command_id - 1}].wait_begin,wait_count",
        baseline,
        _replace_transport(
            program,
            "commands",
            command.command_id - 1,
            wait_begin=(1 << 32) - 1,
            wait_count=2,
        ),
    )
    yield _encoded_transport_case(
        "commands_operand_span_wrap",
        "VERIFY:E_ABI_BOUNDS",
        f"COMMANDS[{command.command_id - 1}].operand_begin,operand_count",
        baseline,
        _replace_transport(
            program,
            "commands",
            command.command_id - 1,
            operand_begin=(1 << 32) - 1,
            operand_count=2,
        ),
    )
    descriptor = program.dma_descriptors[0]
    yield _encoded_transport_case(
        "descriptors_unknown_completion_event",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[0].completion_event",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            0,
            completion_event=9999,
        ),
    )
    yield _encoded_transport_case(
        "descriptors_endpoint_memory_space",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[0].src.memory_space",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            0,
            src=dataclasses.replace(
                descriptor.src,
                memory_space=A.MEMORY_SPACE.HOST_SHARED,
            ),
        ),
    )
    load = next(
        item for item in program.dma_descriptors if item.kind == A.DMA_KIND.LOAD
    )
    yield _encoded_transport_case(
        "descriptors_missing_literal",
        "VERIFY:E_ABI_BOUNDS",
        f"DMA_DESCRIPTORS[{load.descriptor_id - 1}]",
        baseline,
        dataclasses.replace(
            program,
            dma_descriptors=tuple(
                item
                for item in program.dma_descriptors
                if item.descriptor_id != load.descriptor_id
            ),
            expected_traffic=tuple(
                item
                for item in program.expected_traffic
                if item.descriptor_id != load.descriptor_id
            ),
        ),
    )
    yield _encoded_transport_case(
        "descriptors_append_literal",
        "VERIFY:E_ABI_BOUNDS",
        "DMA_DESCRIPTORS[3].descriptor_id",
        baseline,
        dataclasses.replace(
            program,
            dma_descriptors=(
                *program.dma_descriptors,
                dataclasses.replace(load, descriptor_id=900),
            ),
        ),
    )
    yield _encoded_transport_case(
        "descriptors_duplicate_dma_owner",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[1].command_id",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            1,
            command_id=program.dma_descriptors[0].command_id,
        ),
    )

    program = dual
    baseline = dual_blob
    descriptor = program.dma_descriptors[0]
    yield _encoded_transport_case(
        "descriptors_row_useful_overflow",
        "VERIFY:E_ABI_OVERFLOW",
        "DMA_DESCRIPTORS[0].row_bytes",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            0,
            row_bytes=1 << 58,
        ),
    )
    p2p = next(
        item
        for item in program.dma_descriptors
        if item.kind == A.DMA_KIND.P2P_PUSH
    )
    shard = next(item for item in program.shards if item.shard_id == p2p.dst.shard_id)
    allocation = next(
        item
        for item in program.allocations
        if item.allocation_id == shard.allocation_id
    )
    yield _encoded_transport_case(
        "endpoints_one_byte_before_shard",
        "VERIFY:E_DMA_RANGE",
        f"DMA_DESCRIPTORS[{p2p.descriptor_id - 1}].dst.offset_bytes",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            p2p.descriptor_id - 1,
            dst=dataclasses.replace(
                p2p.dst,
                offset_bytes=allocation.offset_bytes + shard.allocation_offset - 1,
            ),
        ),
    )
    yield _encoded_transport_case(
        "endpoints_stride_tail_escapes_shard",
        "VERIFY:E_DMA_RANGE",
        "DMA_DESCRIPTORS[0].dst.offset_bytes",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            0,
            dst=dataclasses.replace(descriptor.dst, offset_bytes=1),
        ),
    )
    yield _encoded_transport_case(
        "endpoints_local_without_shard",
        "VERIFY:E_DMA_RANGE",
        f"DMA_DESCRIPTORS[{p2p.descriptor_id - 1}].src.shard_id",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            p2p.descriptor_id - 1,
            src=dataclasses.replace(p2p.src, shard_id=0),
        ),
    )

    program = repeat
    baseline = repeat_blob
    repeat = next(item for item in program.commands if item.opcode == A.OPCODE.REPEAT)
    semantic = program.semantics.command_semantics[repeat.command_id - 1]
    attrs = dataclasses.replace(
        semantic.source.attrs,
        subrange_begin_stream_ordinal=(1 << 32) - 1,
        subrange_command_count=1,
    )
    semantic = dataclasses.replace(
        semantic,
        source=dataclasses.replace(semantic.source, attrs=attrs),
    )
    attr = program.op_attrs[repeat.attr_index - 1]
    values = dict(zip(attr.payload_fields, attr.payload))
    values.update(
        subrange_begin_stream_ordinal=(1 << 32) - 1,
        subrange_command_count=1,
    )
    attr = dataclasses.replace(
        attr,
        payload=tuple(values[field] for field in attr.payload_fields),
    )
    yield _encoded_transport_case(
        "attrs_repeat_subrange_wrap",
        "VERIFY:E_ABI_BOUNDS",
        f"OP_ATTRS[{repeat.attr_index - 1}].REPEAT_V1.subrange",
        baseline,
        dataclasses.replace(
            _replace_transport(
                program,
                "op_attrs",
                repeat.attr_index - 1,
                payload=attr.payload,
            ),
            semantics=dataclasses.replace(
                program.semantics,
                command_semantics=_replace_item(
                    program.semantics.command_semantics,
                    repeat.command_id - 1,
                    semantic,
                ),
            ),
        ),
    )

    program = single
    baseline = single_blob
    command = program.commands[1]
    yield _encoded_transport_case(
        "waits_dependency_cycle_literal",
        "VERIFY:E_ABI_BOUNDS",
        f"COMMAND_WAITS[{command.wait_begin}].event_id",
        baseline,
        _replace_transport(
            program,
            "command_waits",
            command.wait_begin,
            event_id=5,
        ),
    )
    effectful = tuple(
        item for item in program.semantics.kernel_ops
        if item.done_token is not None
    )
    load = effectful[0]
    store = effectful[-1]
    load_index = program.semantics.kernel_ops.index(load)
    yield _encoded_transport_case(
        "kernel_complete_dependency_cycle",
        "VERIFY:E_DEPENDENCY_CYCLE",
        f"SEMANTICS.kernel_ops[{load.op_id}].after_tokens",
        baseline,
        dataclasses.replace(
            program,
            semantics=dataclasses.replace(
                program.semantics,
                kernel_ops=_replace_item(
                    program.semantics.kernel_ops,
                    load_index,
                    dataclasses.replace(load, after_tokens=(store.done_token,)),
                ),
            ),
        ),
    )
    yield _encoded_transport_case(
        "allocations_overlap_literal",
        "VERIFY:E_SRAM_OOM",
        "ALLOCATIONS[1].offset_bytes",
        baseline,
        _replace_transport(
            program,
            "allocations",
            1,
            offset_bytes=0x1000,
        ),
    )
    yield _encoded_transport_case(
        "allocations_capacity_literal",
        "VERIFY:E_ABI_BOUNDS",
        "ALLOCATIONS[0].offset_bytes",
        baseline,
        _replace_transport(
            program,
            "allocations",
            0,
            offset_bytes=arch.sram_bytes,
        ),
    )
    shard = next(item for item in program.shards if item.allocation_id)
    yield _encoded_transport_case(
        "shards_offset_literal",
        "VERIFY:E_ABI_BOUNDS",
        f"SHARDS[{shard.shard_id - 1}].allocation_offset",
        baseline,
        _replace_transport(
            program,
            "shards",
            shard.shard_id - 1,
            allocation_offset=(1 << 64) - 64,
        ),
    )

    program = dual
    baseline = dual_blob
    attr_index = next(
        index
        for index, item in enumerate(program.op_attrs)
        if item.kind == A.ATTR_KIND.RECV_WAIT_V1
    )
    attr = program.op_attrs[attr_index]
    values = dict(zip(attr.payload_fields, attr.payload))
    values["transfer_id"] = 999
    yield _encoded_transport_case(
        "attrs_recv_wait_unmatched_literal",
        "VERIFY:E_ABI_ENUM",
        f"OP_ATTRS[{attr_index}].RECV_WAIT_V1.transfer_id",
        baseline,
        _replace_transport(
            program,
            "op_attrs",
            attr_index,
            payload=tuple(values[field] for field in attr.payload_fields),
        ),
    )
    p2p = next(
        item
        for item in program.dma_descriptors
        if item.kind == A.DMA_KIND.P2P_PUSH
    )
    yield _encoded_transport_case(
        "endpoints_p2p_self_literal",
        "VERIFY:E_DMA_RANGE",
        f"DMA_DESCRIPTORS[{p2p.descriptor_id - 1}].dst.owner_core",
        baseline,
        _replace_transport(
            program,
            "dma_descriptors",
            p2p.descriptor_id - 1,
            dst=dataclasses.replace(
                p2p.dst,
                owner_core=p2p.src.owner_core,
            ),
        ),
    )
    op_index = next(
        index
        for index, item in enumerate(program.semantics.kernel_ops)
        if item.opcode.name == "RECV_WAIT"
    )
    op = program.semantics.kernel_ops[op_index]
    op = dataclasses.replace(
        op,
        attrs=dataclasses.replace(op.attrs, transfer_id=999),
    )
    semantic_index = next(
        index
        for index, item in enumerate(program.semantics.command_semantics)
        if getattr(item.source, "kernel_op_id", None) == op.op_id
    )
    semantic = program.semantics.command_semantics[semantic_index]
    semantic = dataclasses.replace(
        semantic,
        execution=dataclasses.replace(semantic.execution, transfer_id=999),
    )
    command = program.commands[semantic.command_id - 1]
    attr = program.op_attrs[command.attr_index - 1]
    values = dict(zip(attr.payload_fields, attr.payload))
    values["transfer_id"] = 999
    attr = dataclasses.replace(
        attr,
        payload=tuple(values[field] for field in attr.payload_fields),
    )
    yield _encoded_transport_case(
        "attrs_recv_wait_unmatched_typed",
        "VERIFY:E_P2P_UNMATCHED",
        f"OP_ATTRS[{command.attr_index - 1}].RECV_WAIT_V1.transfer_id",
        baseline,
        dataclasses.replace(
            _replace_transport(
                program,
                "op_attrs",
                command.attr_index - 1,
                payload=attr.payload,
            ),
            semantics=dataclasses.replace(
                program.semantics,
                kernel_ops=_replace_item(
                    program.semantics.kernel_ops,
                    op_index,
                    op,
                ),
                command_semantics=_replace_item(
                    program.semantics.command_semantics,
                    semantic_index,
                    semantic,
                ),
            ),
        ),
    )


_CORPUS_INPUTS = None


def _corpus_inputs():
    global _CORPUS_INPUTS
    if _CORPUS_INPUTS is None:
        arch = load_arch(ARCH_PATH)
        golden_blob = encode_program(build_single_core_program(arch))
        mutation_cases = tuple(_mutations(golden_blob, arch))
        transport_cases = tuple(_transport_cases(arch))
        assert len(mutation_cases) == 55
        assert all(case.blob != golden_blob for case in mutation_cases)
        assert all(
            case.baseline is not None and case.blob != case.baseline
            for case in transport_cases
        )
        _CORPUS_INPUTS = _CorpusInputs(
            golden_blob,
            mutation_cases,
            transport_cases,
        )
    return _CORPUS_INPUTS


def pytest_generate_tests(metafunc):
    if {
        "mutation_case",
        "transport_case",
    }.isdisjoint(metafunc.fixturenames):
        return
    corpus = _corpus_inputs()
    if "mutation_case" in metafunc.fixturenames:
        metafunc.parametrize(
            "mutation_case",
            corpus.mutation_cases,
            ids=lambda case: case.name,
        )
    if "transport_case" in metafunc.fixturenames:
        metafunc.parametrize(
            "transport_case",
            corpus.transport_cases,
            ids=lambda case: case.name,
        )


@pytest.fixture(scope="module")
def arch():
    _python_verdict.arch = load_arch(ARCH_PATH)
    return _python_verdict.arch


@pytest.fixture(scope="module")
def golden_blob(arch):
    return _corpus_inputs().golden_blob


def test_mutation_corpus_python_inventory(mutation_case, arch):
    assert _python_verdict(mutation_case.blob) == mutation_case.expected, mutation_case


def test_mutation_corpus_languages_agree(mutation_case, driver, tmp_path, arch):
    python = _python_verdict(mutation_case.blob)
    cpp = _cpp_verdict(mutation_case.blob, driver, tmp_path, arch)
    assert python == mutation_case.expected, mutation_case
    assert cpp == mutation_case.expected, mutation_case


def test_transport_admission_python_inventory(transport_case, arch):
    assert _python_verdict(transport_case.blob) == transport_case.expected, transport_case


def test_transport_admission_languages_agree(transport_case, driver, tmp_path, arch):
    python = _python_verdict(transport_case.blob)
    cpp = _cpp_verdict(transport_case.blob, driver, tmp_path, arch)
    assert python == transport_case.expected, transport_case
    assert cpp == transport_case.expected, transport_case


def test_dma_barrier_completion_positive(arch, driver, tmp_path):
    program = _build_dma_barrier_completion_program(arch)
    python, cpp = _program_verdicts(program, driver, tmp_path, arch)
    assert python == cpp == "ACCEPTED", (python, cpp)


@pytest.mark.parametrize(
    "mutation,expected",
    (
        ("offset_wrap", "DECODE:E_ABI_SECTION_RANGE"),
        ("count_huge_fixed_table", "DECODE:E_ABI_SECTION_RANGE"),
        ("strings_size_lt4", "DECODE:E_ABI_SECTION_RANGE"),
        ("strings_span_overflow", "DECODE:E_ABI_SECTION_RANGE"),
    ),
)
def test_parser_overflow_inputs_are_deterministic_errors(
    golden_blob, driver, tmp_path, arch, mutation, expected
):
    base = bytearray(golden_blob)
    if mutation == "offset_wrap":
        dir_off = struct.unpack_from("<Q", base, 24)[0]
        struct.pack_into("<Q", base, dir_off + 8, (1 << 64) - 7)
        struct.pack_into("<Q", base, dir_off + 16, 16)
        blob = bytes(_recompute(base))
    elif mutation == "count_huge_fixed_table":
        commands_entry = next(
            row["entry"] for row in _sections(base)
            if row["type"] == A.SECTION_TYPE.COMMANDS
        )
        struct.pack_into("<Q", base, commands_entry + 24, 1 << 60)
        blob = bytes(_recompute(base))
    elif mutation == "strings_size_lt4":
        strings_entry = next(
            row["entry"] for row in _sections(base)
            if row["type"] == A.SECTION_TYPE.STRINGS
        )
        struct.pack_into("<Q", base, strings_entry + 16, 2)
        struct.pack_into("<Q", base, strings_entry + 24, 0)
        blob = bytes(_recompute(base))
    else:
        strings_payload = next(
            row["off"] for row in _sections(base)
            if row["type"] == A.SECTION_TYPE.STRINGS
        )
        struct.pack_into("<I", base, strings_payload, 1 << 20)
        blob = bytes(_recompute(base))
    py = _python_verdict(blob)
    cpp = _cpp_verdict(blob, driver, tmp_path, arch)
    assert py == cpp == expected, (mutation, py, cpp)


def test_optional_section_record_size_parity(arch, driver, tmp_path):
    """An empty optional fixed table must be omitted on both readers."""
    from mesh_ir.golden_programs import build_single_core_program
    from tests.golden.support.optional_section import rebuild_with_extra_section

    blob = encode_program(build_single_core_program(arch))
    canonical_size = rebuild_with_extra_section(
        blob, A.SECTION_TYPE.SOURCE_MAP, b"", record_bytes=A.SOURCE_MAP_BYTES)
    py = _python_verdict(canonical_size)
    cpp = _cpp_verdict(canonical_size, driver, tmp_path, arch)
    assert py == cpp == "DECODE:E_ABI_BOUNDS", (py, cpp)

    illegal = bytearray(canonical_size)
    for row in _sections(illegal):
        if row["type"] == A.SECTION_TYPE.SOURCE_MAP:
            struct.pack_into("<I", illegal, row["entry"] + 4, 0)
    illegal = bytes(_recompute(illegal))
    py = _python_verdict(illegal)
    cpp = _cpp_verdict(illegal, driver, tmp_path, arch)
    assert py == cpp == "DECODE:E_ABI_SECTION_RANGE", (py, cpp)


def test_repeat_flags_const_parity(arch, driver, tmp_path):
    """REPEAT payload flags (const 0) is enforced even for const==0."""
    from mesh_ir.golden_programs import build_repeat_program

    blob = encode_program(build_repeat_program(arch))
    attrs = None
    for row in _sections(bytearray(blob)):
        if row["type"] == A.SECTION_TYPE.OP_ATTRS:
            attrs = row
    assert attrs is not None
    mutated = bytearray(blob)
    for index in range(attrs["count"]):
        kind = struct.unpack_from(
            "<H", blob, attrs["off"] + index * A.OP_ATTRS_BYTES)[0]
        if kind == A.ATTR_KIND.REPEAT_V1:
            struct.pack_into(
                "<I", mutated,
                attrs["off"] + index * A.OP_ATTRS_BYTES + 4 +
                A.REPEAT_V1_FIELD_OFFSETS["flags"], 1)
            break
    else:
        raise AssertionError("repeat program carries no REPEAT attr")
    mutated = bytes(_recompute(mutated))
    py = _python_verdict(mutated)
    cpp = _cpp_verdict(mutated, driver, tmp_path, arch)
    assert py == cpp == "DECODE:E_ABI_RESERVED", (py, cpp)


def _program_verdicts(program, driver, tmp_path, arch):
    had_arch = hasattr(_python_verdict, "arch")
    previous_arch = getattr(_python_verdict, "arch", None)
    _python_verdict.arch = arch
    try:
        blob = encode_program(program)
        return (
            _python_verdict(blob),
            _cpp_verdict(blob, driver, tmp_path, arch),
        )
    finally:
        if had_arch:
            _python_verdict.arch = previous_arch
        else:
            del _python_verdict.arch


def _assert_shared_rejection(program, driver, tmp_path, arch, code):
    program = _with_fresh_semantic_sha(program)
    py, cpp = _program_verdicts(program, driver, tmp_path, arch)
    assert py == cpp, (py, cpp)
    assert py == f"VERIFY:{code}", py


@pytest.mark.parametrize(
    ("builder", "opcode", "wrong_engine"),
    (
        (build_single_core_program, A.OPCODE.REQUEST_BEGIN, A.ENGINE.DMA_READ),
        (build_single_core_program, A.OPCODE.DMA_LOAD, A.ENGINE.DMA_WRITE),
        (build_single_core_program, A.OPCODE.GEMM, A.ENGINE.VECTOR),
        (build_dual_core_program, A.OPCODE.RECV_WAIT, A.ENGINE.DMA_READ),
        (build_barrier_e2e_program, A.OPCODE.BARRIER, A.ENGINE.DMA_READ),
    ),
    ids=("control", "dma", "compute", "recv_wait", "barrier"),
)
def test_command_transport_engine_mismatch_reaches_dedicated_verifier(arch, driver, tmp_path, builder, opcode, wrong_engine):
    program = builder(arch)
    command = next(item for item in program.commands if item.opcode == opcode)
    changed = dataclasses.replace(command, engine=wrong_engine)
    program = dataclasses.replace(program, commands=_replace_item(program.commands, command.command_id - 1, changed))

    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ENGINE_MISMATCH")


def test_wrong_valid_stream_order_identity_reaches_both_domain_verifiers(empty_tp4_case, driver, tmp_path):
    program, arch = empty_tp4_case
    dependency = next(item for item in program.semantics.dependencies if item.kind is ScheduledDependencyKind.STREAM_ORDER)
    wrong_stream_id = next(item.stream_id for item in program.semantics.streams if item.stream_id != dependency.source.stream_id)
    changed = dataclasses.replace(dependency, source=StreamOrderSource(wrong_stream_id))
    semantics = dataclasses.replace(program.semantics, dependencies=_replace_item(program.semantics.dependencies, dependency.dependency_id - 1, changed))
    program = dataclasses.replace(program, semantics=semantics)

    _assert_shared_rejection(program, driver, tmp_path, arch, "E_STREAM_CONTRACT")


def test_operand_allocation_must_match_shard_allocation(arch, driver, tmp_path):
    program = copy.deepcopy(build_single_core_program(arch))
    command = next(c for c in program.commands if c.opcode == A.OPCODE.GEMM)
    first = command.operand_begin
    program = dataclasses.replace(
        program,
        command_operands=_replace_item(
            program.command_operands,
            first,
            dataclasses.replace(
                program.command_operands[first],
                allocation_id=program.command_operands[first + 1].allocation_id,
            ),
        ),
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_BOUNDS")


def test_compute_result_operand_must_be_read_write(arch, driver, tmp_path):
    program = copy.deepcopy(build_single_core_program(arch))
    command = next(c for c in program.commands if c.opcode == A.OPCODE.GEMM)
    result_index = command.operand_begin + command.operand_count - 1
    program = dataclasses.replace(
        program,
        command_operands=_replace_item(
            program.command_operands,
            result_index,
            dataclasses.replace(
                program.command_operands[result_index],
                access=A.ACCESS_KIND.READ_ONLY,
            ),
        ),
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_BOUNDS")


def test_nonempty_compute_attribute_projection_is_exact(
    arch, driver, tmp_path
):
    program = build_single_core_program(arch)
    command = next(c for c in program.commands if c.opcode == A.OPCODE.GEMM)
    index = command.attr_index - 1
    attr = program.op_attrs[index]
    values = dict(zip(attr.payload_fields, attr.payload))
    values["m"] += 1
    program = dataclasses.replace(
        program,
        op_attrs=_replace_item(
            program.op_attrs,
            index,
            dataclasses.replace(
                attr,
                payload=tuple(values[field] for field in attr.payload_fields),
            ),
        ),
    )

    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_ENUM")


def test_compute_semantic_opcode_attribute_family_is_exact(
    arch, driver, tmp_path
):
    from mesh_ir.ir.kernel_ir import LocalCopyAttrs

    program = build_single_core_program(arch)
    index = next(
        index
        for index, operation in enumerate(program.semantics.kernel_ops)
        if operation.opcode.name == "GEMM"
    )
    operation = dataclasses.replace(
        program.semantics.kernel_ops[index],
        attrs=LocalCopyAttrs(),
    )
    semantics = dataclasses.replace(
        program.semantics,
        kernel_ops=_replace_item(
            program.semantics.kernel_ops, index, operation
        ),
    )
    program = dataclasses.replace(program, semantics=semantics)

    _assert_shared_rejection(
        program, driver, tmp_path, arch, "E_EXPORT_UNSUPPORTED_OP"
    )


def test_compute_execution_work_projection_is_exact(arch, driver, tmp_path):
    program = build_single_core_program(arch)
    operation = next(
        operation
        for operation in program.semantics.kernel_ops
        if operation.opcode.name == "GEMM"
    )
    index = next(
        index
        for index, semantic in enumerate(program.semantics.command_semantics)
        if getattr(semantic.source, "kernel_op_id", None) == operation.op_id
    )
    semantic = program.semantics.command_semantics[index]
    phase = semantic.execution.phases[0]
    work = dataclasses.replace(
        phase.work[0], operations=phase.work[0].operations + 1
    )
    phase = dataclasses.replace(
        phase, work=_replace_item(phase.work, 0, work)
    )
    execution = dataclasses.replace(
        semantic.execution,
        phases=_replace_item(semantic.execution.phases, 0, phase),
    )
    semantic = dataclasses.replace(semantic, execution=execution)
    semantics = dataclasses.replace(
        program.semantics,
        command_semantics=_replace_item(
            program.semantics.command_semantics, index, semantic
        ),
    )
    program = dataclasses.replace(program, semantics=semantics)

    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_ENUM")


def test_compute_execution_work_unit_is_exact(arch, driver, tmp_path):
    program = build_single_core_program(arch)
    operation = next(
        operation
        for operation in program.semantics.kernel_ops
        if operation.opcode.name == "GEMM"
    )
    index = next(
        index
        for index, semantic in enumerate(program.semantics.command_semantics)
        if getattr(semantic.source, "kernel_op_id", None) == operation.op_id
    )
    semantic = program.semantics.command_semantics[index]
    phase = semantic.execution.phases[0]
    work = dataclasses.replace(
        phase.work[0], unit=type(phase.work[0].unit).ADD
    )
    phase = dataclasses.replace(
        phase, work=_replace_item(phase.work, 0, work)
    )
    execution = dataclasses.replace(
        semantic.execution,
        phases=_replace_item(semantic.execution.phases, 0, phase),
    )
    semantic = dataclasses.replace(semantic, execution=execution)
    semantics = dataclasses.replace(
        program.semantics,
        command_semantics=_replace_item(
            program.semantics.command_semantics, index, semantic
        ),
    )
    program = dataclasses.replace(program, semantics=semantics)

    _assert_shared_rejection(program, driver, tmp_path, arch, "E_ABI_ENUM")


def test_endpoint_shard_must_belong_to_endpoint_tensor(arch, driver, tmp_path):
    program = copy.deepcopy(build_single_core_program(arch))
    descriptor = program.dma_descriptors[0]
    other_tensor = next(
        tensor.tensor_id
        for tensor in program.tensors
        if tensor.tensor_id != descriptor.src.tensor_id
    )
    program = dataclasses.replace(
        program,
        dma_descriptors=_replace_item(
            program.dma_descriptors,
            0,
            dataclasses.replace(
                descriptor,
                src=dataclasses.replace(descriptor.src, tensor_id=other_tensor),
            ),
        ),
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_DMA_RANGE")


@pytest.mark.parametrize(
    "side,region_kind,code",
    (
        ("src", "HOST_SHARED", "E_DMA_RANGE"),
        ("dst", "HBM", "E_RELOCATION"),
    ),
)
def test_endpoint_memory_space_must_match_region_kind(
    arch, driver, tmp_path, side, region_kind, code
):
    program = copy.deepcopy(build_single_core_program(arch))
    descriptor = program.dma_descriptors[0]
    region_id = next(
        index for index, region in enumerate(arch.regions)
        if region.kind == region_kind
    )
    endpoint = dataclasses.replace(getattr(descriptor, side), region_id=region_id)
    program = dataclasses.replace(
        program,
        dma_descriptors=_replace_item(
            program.dma_descriptors,
            0,
            dataclasses.replace(descriptor, **{side: endpoint}),
        ),
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, code)


def test_remote_endpoint_owner_must_use_sentinel(arch, driver, tmp_path):
    program = copy.deepcopy(build_single_core_program(arch))
    descriptor = program.dma_descriptors[0]
    program = dataclasses.replace(
        program,
        dma_descriptors=_replace_item(
            program.dma_descriptors,
            0,
            dataclasses.replace(
                descriptor,
                src=dataclasses.replace(descriptor.src, owner_core=0),
            ),
        ),
    )
    _assert_shared_rejection(program, driver, tmp_path, arch, "E_DMA_RANGE")


def test_zero_length_endpoint_may_equal_view_end(arch, driver, tmp_path):
    program = build_zero_dma_program(arch)
    descriptor = program.dma_descriptors[1]
    shard = next(s for s in program.shards if s.shard_id == descriptor.src.shard_id)

    assert descriptor.row_bytes == 0
    assert descriptor.src.offset_bytes == shard.span_bytes
    py, cpp = _program_verdicts(program, driver, tmp_path, arch)
    assert py == cpp == "ACCEPTED", (py, cpp)


@pytest.fixture(scope="module")
def empty_tp4_case():
    from mesh_ir.compile_config import (
        load_compile_config_text,
        resolve_compile_config,
    )
    from mesh_ir.ir.common import (
        Const,
        DType,
        TensorRole,
        contiguous_strides,
    )
    from mesh_ir.ir.graph_ir import (
        GraphFunction,
        GraphModule,
        GraphOp,
        GraphValue,
        NormAttrs,
        OpCode,
        PytreeSpec,
    )
    from mesh_ir.passes.execution import PassExecutor
    from mesh_ir.passes.graph_to_kernel import lower_to_kernel
    from mesh_ir.passes.planning_prefix import plan_graphs_with_executor
    from mesh_ir.passes.scheduled import lower_to_program
    from tests.unit.test_gate2_graph_to_kernel import COMPILE

    arch = load_arch(REPO / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    source_shape = (Const(2), Const(4), Const(8))
    parameter_shape = (Const(8),)
    source = GraphValue(
        1, "source", TensorRole.INPUT, DType.FP32, source_shape,
        contiguous_strides(source_shape), 0, 1,
    )
    weight = GraphValue(
        2, "weight", TensorRole.WEIGHT, DType.FP32, parameter_shape,
        contiguous_strides(parameter_shape), 0, 2,
        content_sha256="a" * 64,
    )
    bias = GraphValue(
        3, "bias", TensorRole.WEIGHT, DType.FP32, parameter_shape,
        contiguous_strides(parameter_shape), 0, 3,
        content_sha256="b" * 64,
    )
    result = GraphValue(
        4, "result", TensorRole.OUTPUT, DType.FP32, source_shape,
        contiguous_strides(source_shape), 0, 4,
    )
    operation = GraphOp(
        1, OpCode.LAYERNORM, (1, 2, 3), (4,),
        NormAttrs((2,), 1e-5, True, True), "layernorm:empty-ranks",
    )
    function = GraphFunction(
        1, "forward", (1,), (operation,), (4,),
        PytreeSpec.leaf(), PytreeSpec.leaf(),
    )
    graph = GraphModule.create(
        arch.digest().hex(), "2" * 64, "forward", "p1",
        (source, weight, bias, result), (function,),
    )
    text = COMPILE.replace(
        "tensor_parallel: 1", "tensor_parallel: 4"
    ).replace("allowed_cores: [7, 2]", "allowed_cores: [7, 2, 9, 13]")
    effective = resolve_compile_config(
        load_compile_config_text(text, arch), arch
    )
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor(
            (graph,), arch, effective, source_graphs=(graph,),
            executor=executor,
        )
        lowering = lower_to_kernel(planning, arch, effective, executor)
        program = lower_to_program(
            lowering, arch, effective, executor
        ).program
    return program, arch


def _with_fresh_semantic_sha(program):
    return dataclasses.replace(
        program, semantic_sha256=semantic_sha256(program.semantic_dict())
    )


def test_empty_tp4_compute_projection_is_accepted(
    empty_tp4_case, driver, tmp_path
):
    program, arch = empty_tp4_case
    py, cpp = _program_verdicts(program, driver, tmp_path, arch)
    assert py == cpp == "ACCEPTED", (py, cpp)


@pytest.mark.parametrize(
    "mutation,expected",
    (
        ("nonempty_shard", "VERIFY:E_EXPORT_LAYOUT"),
        ("nonzero_cost", "VERIFY:E_EXPORT_UNSUPPORTED_OP"),
        ("nonzero_attr", "VERIFY:E_ABI_ENUM"),
        ("wrong_engine", "VERIFY:E_ENGINE_MISMATCH"),
    ),
)
def test_empty_compute_requires_complete_physical_proof(
    empty_tp4_case, driver, tmp_path, mutation, expected
):
    program, arch = empty_tp4_case
    operation = next(
        item for item in program.semantics.kernel_ops
        if item.opcode.name == "NORM" and not item.reads and not item.writes
    )
    command_semantic = next(
        item for item in program.semantics.command_semantics
        if getattr(item.source, "kernel_op_id", None) == operation.op_id
    )
    command_index = next(
        index for index, item in enumerate(program.commands)
        if item.command_id == command_semantic.command_id
    )
    if mutation == "nonempty_shard":
        shard_index = next(
            index for index, item in enumerate(program.semantics.logical_shards)
            if item.shard_id == operation.result_shard_id
        )
        shard = program.semantics.logical_shards[shard_index]
        shape = tuple(max(1, extent) for extent in shard.valid_shape)
        semantics = dataclasses.replace(
            program.semantics,
            logical_shards=(
                *program.semantics.logical_shards[:shard_index],
                dataclasses.replace(shard, valid_shape=shape),
                *program.semantics.logical_shards[shard_index + 1:],
            ),
        )
        program = dataclasses.replace(program, semantics=semantics)
    elif mutation == "nonzero_cost":
        operation_index = program.semantics.kernel_ops.index(operation)
        operation = dataclasses.replace(
            operation,
            attrs=dataclasses.replace(
                operation.attrs,
                cost=dataclasses.replace(operation.attrs.cost, vector_ops=1),
            ),
        )
        semantics = dataclasses.replace(
            program.semantics,
            kernel_ops=(
                *program.semantics.kernel_ops[:operation_index],
                operation,
                *program.semantics.kernel_ops[operation_index + 1:],
            ),
        )
        program = dataclasses.replace(program, semantics=semantics)
    elif mutation == "nonzero_attr":
        command = program.commands[command_index]
        attr_index = command.attr_index - 1
        attr = program.op_attrs[attr_index]
        values = dict(zip(attr.payload_fields, attr.payload))
        values["element_count"] = 1
        attr = dataclasses.replace(
            attr,
            payload=tuple(values[field] for field in attr.payload_fields),
        )
        program = dataclasses.replace(
            program,
            op_attrs=(
                *program.op_attrs[:attr_index], attr,
                *program.op_attrs[attr_index + 1:],
            ),
        )
    else:
        command = dataclasses.replace(
            program.commands[command_index], engine=A.ENGINE.REDUCE
        )
        program = dataclasses.replace(
            program,
            commands=(
                *program.commands[:command_index], command,
                *program.commands[command_index + 1:],
            ),
        )
    program = _with_fresh_semantic_sha(program)
    py, cpp = _program_verdicts(program, driver, tmp_path, arch)
    assert py == cpp == expected, (mutation, py, cpp)


@pytest.mark.parametrize(
    "builder_name,throughput,dtype",
    (
        ("single", "tensor_macs_per_cycle", "fp16"),
        ("repeat", "vector_elements_per_cycle", "fp16"),
        ("dual", "reduce_ops_per_cycle", "fp32"),
    ),
)
def test_known_dtype_without_engine_capability_is_rejected(
    arch, driver, tmp_path, builder_name, throughput, dtype
):
    from mesh_ir.golden_programs import (
        build_dual_core_program,
        build_repeat_program,
    )

    builder = {
        "single": build_single_core_program,
        "repeat": build_repeat_program,
        "dual": build_dual_core_program,
    }[builder_name]
    rates = dict(getattr(arch, throughput))
    del rates[dtype]
    unsupported = dataclasses.replace(arch, **{throughput: rates})
    program = dataclasses.replace(
        builder(arch), arch_digest=unsupported.digest()
    )

    _assert_shared_rejection(
        program,
        driver,
        tmp_path,
        unsupported,
        "E_CAPABILITY_MISMATCH",
    )


def _mutate_strings_metadata(blob, mutation):
    data = bytearray(blob)
    strings = _section_offsets(data)[A.SECTION_TYPE.STRINGS]
    if mutation == "record_bytes":
        struct.pack_into("<I", data, strings["entry"] + 4, strings["size"])
        struct.pack_into("<Q", data, strings["entry"] + 24, 1)
    elif mutation == "directory_count":
        struct.pack_into("<Q", data, strings["entry"] + 24, strings["count"] + 1)
    else:
        count = struct.unpack_from("<I", data, strings["off"])[0]
        last = (
            strings["off"] + A.STRINGS_BLOB_HEADER_BYTES +
            (count - 1) * A.STRINGS_DIRECTORY_RECORD_BYTES
        )
        size = struct.unpack_from("<I", data, last + 4)[0]
        assert size > 1
        struct.pack_into("<I", data, last + 4, size - 1)
    return bytes(_recompute(data))


@pytest.mark.parametrize(
    "mutation", ("record_bytes", "directory_count", "trailing_data")
)
def test_strings_section_metadata_is_exact(
    golden_blob, arch, driver, tmp_path, mutation
):
    blob = _mutate_strings_metadata(golden_blob, mutation)
    py = _python_verdict(blob)
    cpp = _cpp_verdict(blob, driver, tmp_path, arch)
    assert py == cpp == "DECODE:E_ABI_SECTION_RANGE", (py, cpp)

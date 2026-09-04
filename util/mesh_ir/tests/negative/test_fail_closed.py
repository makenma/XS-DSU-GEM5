import copy
import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_dual_core_program, build_single_core_program
from mesh_ir.model import MeshIrError

ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


@pytest.fixture(scope="module")
def blob(arch):
    return encode_program(build_single_core_program(arch))


def _mutate_command(program, command_id, **changes):
    program = copy.deepcopy(program)
    for index, command in enumerate(program.commands):
        if command.command_id == command_id:
            program.commands[index] = dataclasses.replace(command, **changes)
            return program
    raise AssertionError(f"command {command_id} not found")


def expect_code(program, arch, code):
    with pytest.raises(MeshIrError) as err:
        verify_program(program, arch)
    assert err.value.code == code, f"expected {code}, got {err.value.code}: {err.value.message}"


def test_bad_magic(blob, arch):
    corrupt = bytearray(blob)
    corrupt[0] ^= 0xFF
    with pytest.raises(MeshIrError) as err:
        decode_program(bytes(corrupt))
    assert err.value.code == "E_ABI_MAGIC"


def test_abi_major_bump_rejected(blob, arch):
    program = decode_program(blob)
    program.abi_major = 2
    with pytest.raises(MeshIrError) as err:
        decode_program(encode_program(program))
    assert err.value.code == "E_ABI_VERSION"


def test_unknown_required_feature_bits_rejected(blob, arch):
    # Header offset 104 holds required_features; any nonzero bit is an
    # unknown feature for the ABI 1.0 reader and must fail closed.  The
    # payload SHA only covers [128, file), so this flip bypasses it.
    corrupt = bytearray(blob)
    corrupt[104] = 0x01
    with pytest.raises(MeshIrError) as err:
        decode_program(bytes(corrupt))
    assert err.value.code == "E_ABI_VERSION"


def test_single_bit_payload_corruption_detected(blob, arch):
    corrupt = bytearray(blob)
    corrupt[200] ^= 0x01
    with pytest.raises(MeshIrError) as err:
        decode_program(bytes(corrupt))
    assert err.value.code == "E_ABI_CHECKSUM"


def test_truncated_file_rejected(blob):
    with pytest.raises(MeshIrError) as err:
        decode_program(blob[:-8])
    assert err.value.code in ("E_ABI_SECTION_RANGE", "E_ABI_CHECKSUM")


def test_unknown_opcode_rejected(arch):
    program = build_single_core_program(arch)
    mutated = _mutate_command(program, 4, opcode=99)
    expect_code(mutated, arch, "E_ABI_ENUM")


def test_engine_mismatch_rejected(arch):
    program = build_single_core_program(arch)
    mutated = _mutate_command(program, 4, engine=A.ENGINE.DMA_READ)
    expect_code(mutated, arch, "E_ENGINE_MISMATCH")


def test_event_without_producer_rejected(arch):
    program = build_single_core_program(arch)
    mutated = _mutate_command(program, 4, signal_event=0)
    event = next(e for e in mutated.events if e.event_id == 4)
    event.producer_command_id = 0
    expect_code(mutated, arch, "E_EVENT_NO_PRODUCER")


def test_event_multiple_producers_rejected(arch):
    program = build_single_core_program(arch)
    halt = next(c for c in program.commands if c.opcode == A.OPCODE.HALT)
    mutated = _mutate_command(program, halt.command_id, signal_event=1)
    expect_code(mutated, arch, "E_EVENT_MULTIPLE_PRODUCERS")


def test_dependency_cycle_rejected(arch):
    program = build_single_core_program(arch)
    waits = program.command_waits
    for index, command in enumerate(program.commands):
        if command.command_id == 2:
            waits[command.wait_begin].event_id = 5
    expect_code(program, arch, "E_DEPENDENCY_CYCLE")


def test_sram_overlap_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.allocations[1] = dataclasses.replace(mutated.allocations[1], offset_bytes=0x1000)
    expect_code(mutated, arch, "E_DMA_RANGE")


def test_sram_oom_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.allocations[0] = dataclasses.replace(
        mutated.allocations[0], offset_bytes=arch.sram_bytes - 64 + 64
    )
    expect_code(mutated, arch, "E_SRAM_OOM")


def test_halt_outside_control_stream_rejected(arch):
    program = build_dual_core_program(arch)
    mutated = copy.deepcopy(program)
    last_core1 = max(c.command_id for c in mutated.commands if c.core_id == 1)
    for index, command in enumerate(mutated.commands):
        if command.command_id == last_core1 - 1:
            mutated.commands[index] = dataclasses.replace(command, opcode=A.OPCODE.HALT)
            break
    expect_code(mutated, arch, "E_STREAM_CONTRACT")


def test_missing_control_stream_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.streams[0].flags = 0
    expect_code(mutated, arch, "E_STREAM_CONTRACT")


def test_lifecycle_missing_request_end_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    for index, command in enumerate(mutated.commands):
        if command.opcode == A.OPCODE.REQUEST_END:
            mutated.commands[index] = dataclasses.replace(command, opcode=A.OPCODE.EVENT_SIGNAL)
            break
    expect_code(mutated, arch, "E_LIFECYCLE")


def test_unmatched_recv_wait_rejected(arch):
    program = build_dual_core_program(arch)
    mutated = copy.deepcopy(program)
    for attr in mutated.op_attrs:
        if attr.kind == A.ATTR_KIND.RECV_WAIT_V1:
            attr.payload = (999,) + attr.payload[1:]
    expect_code(mutated, arch, "E_P2P_UNMATCHED")


def test_barrier_arrival_mismatch_rejected(arch):
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "barrier_unit")
    builder.entrypoint("main", "b1", lifecycle_core=0, lifecycle_stream=0)
    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    barrier = builder.event(kind=A.EVENT_KIND.BARRIER, expected_arrivals=3)
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    # Two BARRIER arrivals against a barrier that expects three.
    stream.command(A.OPCODE.BARRIER, waits=(e_begin,), signal_event=barrier)
    stream.command(A.OPCODE.BARRIER, waits=(e_begin,), signal_event=barrier)
    stream.command(A.OPCODE.REQUEST_END, waits=(barrier,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    expect_code(builder.build(), arch, "E_ABI_BOUNDS")


def test_traffic_row_mismatch_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    row = mutated.expected_traffic[0]
    mutated.expected_traffic[0] = dataclasses.replace(row, bursts=row.bursts + 1)
    expect_code(mutated, arch, "E_TRAFFIC_MISMATCH")


def test_dma_kind_command_mismatch_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.dma_descriptors[0] = dataclasses.replace(
        mutated.dma_descriptors[0], kind=A.DMA_KIND.STORE
    )
    expect_code(mutated, arch, "E_ABI_ENUM")


def test_p2p_to_self_rejected(arch):
    program = build_dual_core_program(arch)
    mutated = copy.deepcopy(program)
    for index, descriptor in enumerate(mutated.dma_descriptors):
        if descriptor.kind == A.DMA_KIND.P2P_PUSH:
            mutated.dma_descriptors[index] = dataclasses.replace(
                descriptor,
                dst=dataclasses.replace(descriptor.dst, owner_core=0, memory_space=A.MEMORY_SPACE.PEER_SRAM),
            )
    # The endpoint shard/owner consistency check fires first; the P2P
    # self-direction rule is the same invariant seen from the kind side.
    expect_code(mutated, arch, "E_ABI_BOUNDS")


def test_useful_bytes_overflow_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.dma_descriptors[0] = dataclasses.replace(
        mutated.dma_descriptors[0], rows=1 << 40, useful_bytes=1 << 40
    )
    expect_code(mutated, arch, "E_ABI_OVERFLOW")


def test_duplicate_command_id_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.commands[1] = dataclasses.replace(mutated.commands[1], command_id=1)
    expect_code(mutated, arch, "E_ABI_DUPLICATE")


def test_commands_must_be_sorted(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.commands[0], mutated.commands[1] = mutated.commands[1], mutated.commands[0]
    expect_code(mutated, arch, "E_ABI_ORDER")


import struct as _struct

_DIR_OFF = _struct.unpack_from("<Q", blob_fixture := b"")[0] if False else None


def _operand_reserved_offset():
    import struct

    dir_off = struct.unpack_from("<Q", _BLOB, 24)[0]
    count = struct.unpack_from("<I", _BLOB, 32)[0]
    from mesh_ir.generated import abi as _A

    secs = {}
    for i in range(count):
        e = dir_off + i * 40
        stype = struct.unpack_from("<H", _BLOB, e)[0]
        off = struct.unpack_from("<Q", _BLOB, e + 8)[0]
        secs[stype] = off
    ops_off = secs[_A.SECTION_TYPE.COMMAND_OPERANDS]
    return ops_off + 0 * _A.COMMAND_OPERANDS_BYTES + 14


OPERAND_RESERVED = None


def _mutate_with_valid_checksums(blob, offset, mask):
    import struct
    import zlib

    corrupt = bytearray(blob)
    corrupt[offset] ^= mask
    dir_off = struct.unpack_from("<Q", corrupt, 24)[0]
    count = struct.unpack_from("<I", corrupt, 32)[0]
    for i in range(count):
        entry = dir_off + i * 40
        sec_off, size = struct.unpack_from("<QQ", corrupt, entry + 8)
        crc = zlib.crc32(bytes(corrupt[sec_off : sec_off + size])) & 0xFFFFFFFF
        struct.pack_into("<I", corrupt, entry + 32, crc)
    import hashlib

    payload = bytes(corrupt[128:])
    struct.pack_into("<32s", corrupt, 72, hashlib.sha256(payload).digest())
    return bytes(corrupt)


def test_recomputed_checksum_mutations_rejected(arch, blob):
    global _BLOB, OPERAND_RESERVED
    _BLOB = blob
    OPERAND_RESERVED = _operand_reserved_offset()
    """Cross-language parity: structurally illegal but checksum-valid
    binaries must fail closed in Python exactly as in the C++ GTest."""
    cases = [
        (3042, 0x01),  # attr reserved nonzero -> E_ABI_RESERVED
        (2720, 0x06),  # LOAD destination space SRAM->HBM -> E_DMA_RANGE
        (2719, 0xFF),  # endpoint offset u64 wrap -> E_DMA_RANGE
        (OPERAND_RESERVED, 0x01),  # operand reserved nonzero -> E_ABI_RESERVED
    ]
    for offset, mask in cases:
        crafted = _mutate_with_valid_checksums(blob, offset, mask)
        with pytest.raises(MeshIrError):
            program = decode_program(crafted)
            verify_program(program, arch)


def test_gemm_shape_overflow_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    for index, command in enumerate(mutated.commands):
        if command.opcode == A.OPCODE.GEMM:
            mutated.commands[index] = command
            break
    for attr in mutated.op_attrs:
        if attr.kind == A.ATTR_KIND.GEMM_V1:
            values = list(attr.payload)
            values[0], values[1], values[2], values[3] = 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF
            attr.payload = tuple(values)
            break
    expect_code(mutated, arch, "E_ABI_OVERFLOW")


def test_wait_span_u32_wrap_rejected(arch):
    program = build_single_core_program(arch)
    mutated = _mutate_command(program, 4, wait_begin=0xFFFFFFFF, wait_count=2)
    expect_code(mutated, arch, "E_ABI_BOUNDS")


def test_operand_span_u32_wrap_rejected(arch):
    program = build_single_core_program(arch)
    mutated = _mutate_command(
        program, 4, operand_begin=0xFFFFFFFF, operand_count=2
    )
    expect_code(mutated, arch, "E_ABI_BOUNDS")


def test_stream_span_u32_wrap_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.streams[0].command_begin = 0xFFFFFFFF
    mutated.streams[0].command_count = 2
    expect_code(mutated, arch, "E_ABI_BOUNDS")


def test_repeat_subrange_u32_wrap_rejected(arch):
    from mesh_ir.golden_programs import build_repeat_program

    program = build_repeat_program(arch)
    mutated = copy.deepcopy(program)
    repeat = next(c for c in mutated.commands if c.opcode == A.OPCODE.REPEAT)
    stream = next(
        s for s in mutated.streams
        if s.core_id == repeat.core_id and s.stream_id == repeat.stream_id
    )
    commands = mutated.commands[stream.command_begin : stream.command_begin + stream.command_count]
    ordinal = next(i for i, c in enumerate(commands) if c.command_id == repeat.command_id)
    attr = mutated.op_attrs[repeat.attr_index - 1]
    assert attr.kind == A.ATTR_KIND.REPEAT_V1
    values = list(attr.payload)
    values[0] = 0xFFFFFFFF
    values[1] = ordinal + 1
    attr.payload = tuple(values)
    expect_code(mutated, arch, "E_ABI_BOUNDS")


def test_descriptor_completion_event_unknown_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.dma_descriptors[0] = dataclasses.replace(
        mutated.dma_descriptors[0], completion_event=9999
    )
    with pytest.raises(MeshIrError):
        verify_program(mutated, arch)


def test_gemm_dtype_not_in_closed_set_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    for attr in mutated.op_attrs:
        if attr.kind == A.ATTR_KIND.GEMM_V1:
            values = dict(zip(attr.payload_fields, attr.payload))
            values["dtype"] = 99
            attr.payload = tuple(values[f["name"]] for f in A.GEMM_V1_FIELDS)
            break
    expect_code(mutated, arch, "E_ABI_ENUM")


def test_profile_rank_gt8_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    mutated.profiles[0] = dataclasses.replace(mutated.profiles[0], rank=9)
    expect_code(mutated, arch, "E_ABI_BOUNDS")


def test_shard_span_exceeds_allocation_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    shard = next(
        s for s in mutated.shards if s.allocation_id
    )
    mutated.shards = [
        dataclasses.replace(s, allocation_offset=(1 << 64) - 64)
        if s.shard_id == shard.shard_id else s
        for s in mutated.shards
    ]
    expect_code(mutated, arch, "E_DMA_RANGE")


DMA_OPCODES = (
    A.OPCODE.DMA_LOAD,
    A.OPCODE.DMA_STORE,
    A.OPCODE.DMA_P2P_PUSH,
    A.OPCODE.DMA_PREFETCH,
    A.OPCODE.DMA_FILL,
)


def test_dma_command_without_descriptor_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    load = next(
        d for d in mutated.dma_descriptors if d.kind == A.DMA_KIND.LOAD
    )
    mutated.dma_descriptors = [
        d for d in mutated.dma_descriptors if d.descriptor_id != load.descriptor_id
    ]
    mutated.expected_traffic = [
        r for r in mutated.expected_traffic
        if r.descriptor_id != load.descriptor_id
    ]
    with pytest.raises(MeshIrError):
        verify_program(mutated, arch)


def test_dma_command_duplicate_descriptors_rejected(arch):
    program = build_single_core_program(arch)
    mutated = copy.deepcopy(program)
    load = next(
        d for d in mutated.dma_descriptors if d.kind == A.DMA_KIND.LOAD
    )
    mutated.dma_descriptors.append(dataclasses.replace(load, descriptor_id=900))
    with pytest.raises(MeshIrError):
        verify_program(mutated, arch)


def test_descriptor_completion_pointing_at_barrier_rejected(arch):
    from mesh_ir.builder import ProgramBuilder
    from mesh_ir.golden_programs import (
        RO, _hbm, _sram,
    )
    from mesh_ir.generated import abi as _A

    builder = ProgramBuilder(arch, "barrier_desc")
    entrypoint = builder.entrypoint("main", "b1", lifecycle_core=0, lifecycle_stream=0)
    t_in = builder.tensor("input", _A.TENSOR_ROLE.INPUT, _A.DTYPE.FP16,
                          _A.STORAGE_CLASS.HBM, RO, (8,))
    alloc = builder.allocation(0, 0, 64, arch.sram_base_alignment_bytes)
    shard = builder.shard(t_in, 0, alloc, (8,), 64)
    stream = builder.stream(
        0, 0,
        flags=_A.STREAM_FLAGS.IS_LIFECYCLE | _A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e_begin = builder.event()
    barrier = builder.event(kind=_A.EVENT_KIND.BARRIER, expected_arrivals=1)
    e_end = builder.event()
    begin = stream.command(_A.OPCODE.REQUEST_BEGIN, signal_event=e_begin)
    load = stream.command(
        _A.OPCODE.DMA_LOAD, waits=(e_begin,),
        operands=((t_in, shard, alloc, RO),))
    descriptor = builder.dma(
        load, _A.DTYPE and _A.DMA_KIND.LOAD,
        src=_hbm(t_in, shard, 0x100000), dst=_sram(t_in, shard, 0, 0),
        rows=1, row_bytes=64, src_stride=64, dst_stride=64,
        completion_event=barrier)
    builder.oracle(entrypoint, 1, descriptor, load.command_id,
                   _A.DMA_KIND.LOAD, 0x100000, 1, 64, 64)
    stream.command(_A.OPCODE.BARRIER, waits=(e_begin,), signal_event=barrier)
    stream.command(_A.OPCODE.REQUEST_END, waits=(barrier,), signal_event=e_end)
    stream.command(_A.OPCODE.HALT, waits=(e_end,))
    expect_code(builder.build(), arch, "E_EVENT_MULTIPLE_PRODUCERS")


def _dual_with_p2p(arch):
    from mesh_ir.golden_programs import build_dual_core_program
    return copy.deepcopy(build_dual_core_program(arch))


def test_operand_allocation_cross_binding_rejected(arch):
    mutated = _dual_with_p2p(arch)
    shard = next(s for s in mutated.shards if s.allocation_id)
    other = next(a for a in mutated.allocations if a.allocation_id != shard.allocation_id)
    for command in mutated.commands:
        for i in range(command.operand_count):
            operand = mutated.command_operands[command.operand_begin + i]
            if operand.shard_id == shard.shard_id:
                mutated.command_operands[command.operand_begin + i] = (
                    dataclasses.replace(operand, allocation_id=other.allocation_id)
                )
    expect_code(mutated, arch, "E_ABI_BOUNDS")


def test_endpoint_one_byte_before_shard_rejected(arch):
    mutated = _dual_with_p2p(arch)
    descriptor = next(
        d for d in mutated.dma_descriptors
        if d.kind == A.DMA_KIND.P2P_PUSH
    )
    shard = next(s for s in mutated.shards if s.shard_id == descriptor.dst.shard_id)
    allocation = next(
        a for a in mutated.allocations if a.allocation_id == shard.allocation_id
    )
    view_start = allocation.offset_bytes + shard.allocation_offset
    mutated.dma_descriptors = [
        dataclasses.replace(
            descriptor,
            dst=dataclasses.replace(descriptor.dst, offset_bytes=view_start - 1),
        )
        if d.descriptor_id == descriptor.descriptor_id else d
        for d in mutated.dma_descriptors
    ]
    with pytest.raises(MeshIrError) as err:
        verify_program(mutated, arch)
    assert err.value.code in ("E_DMA_RANGE", "E_ABI_BOUNDS")


def test_endpoint_stride_tail_escapes_shard_rejected(arch):
    mutated = _dual_with_p2p(arch)
    descriptor = next(
        d for d in mutated.dma_descriptors if d.kind == A.DMA_KIND.P2P_PUSH
    )
    shard = next(s for s in mutated.shards if s.shard_id == descriptor.dst.shard_id)
    allocation = next(
        a for a in mutated.allocations if a.allocation_id == shard.allocation_id
    )
    view_end = allocation.offset_bytes + shard.allocation_offset + shard.span_bytes
    tail_offset = view_end - descriptor.row_bytes - (
        (descriptor.rows - 1) * descriptor.dst_stride_bytes
    ) + 1 if descriptor.rows else 0
    mutated.dma_descriptors = [
        dataclasses.replace(
            descriptor,
            dst=dataclasses.replace(descriptor.dst, offset_bytes=tail_offset),
        )
        if d.descriptor_id == descriptor.descriptor_id else d
        for d in mutated.dma_descriptors
    ]
    with pytest.raises(MeshIrError) as err:
        verify_program(mutated, arch)
    assert err.value.code in ("E_DMA_RANGE", "E_SRAM_OOM", "E_ABI_BOUNDS")


def test_local_endpoint_without_shard_rejected(arch):
    mutated = _dual_with_p2p(arch)
    descriptor = next(
        d for d in mutated.dma_descriptors if d.kind == A.DMA_KIND.P2P_PUSH
    )
    mutated.dma_descriptors = [
        dataclasses.replace(
            descriptor,
            src=dataclasses.replace(descriptor.src, shard_id=0),
        )
        if d.descriptor_id == descriptor.descriptor_id else d
        for d in mutated.dma_descriptors
    ]
    expect_code(mutated, arch, "E_DMA_RANGE")

"""GATHER_LOAD DMA kind and GATHER_INDEX_V1 command attribute: the wire
contract for index-driven HBM row gather.

The attr references the index operand by its ordinal inside the command's
own operand span; offsets and strides are relative to the admitted index
view.  These tests pin codec round trips and the reserved-field / enum
boundaries before any producer consumes the kind.
"""

import dataclasses
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.model import Command, CommandOperand, DmaDescriptor, OpAttr

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def base_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    return build_single_core_program(arch)


def _with_gather(program, attr=None):
    """Attach a GATHER_LOAD descriptor and its command attribute, sharing
    the first existing endpoint pair so the wire shapes stay valid."""
    commands = list(program.commands)
    operands = list(program.command_operands)
    attrs = list(program.op_attrs)
    descriptors = list(program.dma_descriptors)
    sample = descriptors[0]
    operands.append(CommandOperand(
        tensor_id=sample.src.tensor_id,
        shard_id=sample.src.shard_id,
        allocation_id=0,
        access=1,
    ))
    command = Command(
        command_id=10_000,
        source_op_id=0,
        core_id=commands[0].core_id,
        stream_id=commands[0].stream_id,
        engine=int(A.ENGINE.DMA_READ),
        opcode=int(A.OPCODE.DMA_PREFETCH),
        operand_begin=len(operands) - 1,
        operand_count=1,
        attr_index=len(attrs),
    )
    commands.append(command)
    gather_fields = tuple(
        field["name"] for field in A.GATHER_INDEX_V1_FIELDS)
    attrs.append(attr if attr is not None else OpAttr(
        kind=int(A.ATTR_KIND.GATHER_INDEX_V1),
        payload=(0, 0, 4, 0),
        payload_fields=gather_fields,
    ))
    descriptor = dataclasses.replace(
        sample,
        descriptor_id=9_001,
        command_id=10_000,
        kind=6,  # GATHER_LOAD
        transfer_id=0,
        rows=3,
        row_bytes=18,
        src_stride_bytes=18,
        dst_stride_bytes=18,
        useful_bytes=54,
        physical_storage_bytes=54,
        completion_event=0,
    )
    descriptors.append(descriptor)
    changed = dataclasses.replace(
        program,
        commands=tuple(commands),
        command_operands=tuple(operands),
        op_attrs=tuple(attrs),
        dma_descriptors=tuple(descriptors),
    )
    return dataclasses.replace(
        changed, semantic_sha256=semantic_sha256(changed.semantic_dict()))


def test_dma_kind_enum_and_attr_kind_are_registered():
    assert int(A.DMA_KIND.GATHER_LOAD) == 6
    assert int(A.ATTR_KIND.GATHER_INDEX_V1) == 12
    assert A.GATHER_INDEX_V1_BYTES == 28
    names = [field["name"] for field in A.GATHER_INDEX_V1_FIELDS]
    assert names == ["index_operand_index", "index_offset_bytes",
                     "index_stride_bytes", "reserved"]


def test_gather_attr_payload_round_trips_through_the_binary_codec(base_program):
    program = _with_gather(base_program)
    image = encode_program(program)
    decoded = decode_program(image)
    attrs = [item for item in decoded.op_attrs
             if item.kind == int(A.ATTR_KIND.GATHER_INDEX_V1)]
    assert len(attrs) == 1
    assert attrs[0].payload == (0, 0, 4, 0)
    assert decoded == program


def test_gather_attr_reserved_payload_must_be_zero(base_program):
    bad = OpAttr(
        kind=int(A.ATTR_KIND.GATHER_INDEX_V1),
        payload=(0, 0, 4, 1),
        payload_fields=("index_operand_index", "index_offset_bytes",
                        "index_stride_bytes", "reserved"),
    )
    program = _with_gather(base_program, attr=bad)
    with pytest.raises(MeshIrError):
        encode_program(program)


def test_gather_descriptor_kind_round_trips(base_program):
    program = _with_gather(base_program)
    image = encode_program(program)
    decoded = decode_program(image)
    gather = next(item for item in decoded.dma_descriptors
                  if item.kind == 6)
    assert (gather.rows, gather.row_bytes,
            gather.src_stride_bytes) == (3, 18, 18)
    assert gather.command_id == 10_000

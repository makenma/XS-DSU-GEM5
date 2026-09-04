import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.model import MeshIrError

ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


def _repeat_program(arch, begin_ordinal, command_count, repeat_count):
    program = build_single_core_program(arch)
    attr_index = 0
    from mesh_ir.builder import ProgramBuilder

    builder = ProgramBuilder(arch, "repeat_unit")
    builder.entrypoint("main", "b1_m64n64k64", lifecycle_core=0, lifecycle_stream=0)
    t = builder.tensor("input", A.TENSOR_ROLE.INPUT, A.DTYPE.FP16, A.STORAGE_CLASS.HBM,
                       A.ACCESS_KIND.READ_ONLY, (4, 4))
    builder.relocation("input", A.RELOCATION_KIND.TENSOR_BASE, 0, t, 0x100000)
    a = builder.allocation(0, 0, 32, 64)
    s = builder.shard(t, 0, a, (4, 4), 32)
    stream = builder.stream(0, 0, flags=A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL)
    e1 = builder.event()
    e2 = builder.event()
    e3 = builder.event()
    e_end = builder.event()
    stream.command(A.OPCODE.REQUEST_BEGIN, signal_event=e1)
    gemm_operands = ((t, s, a, A.ACCESS_KIND.READ_ONLY),
                     (t, s, a, A.ACCESS_KIND.READ_ONLY),
                     (t, s, a, A.ACCESS_KIND.READ_WRITE))
    stream.command(A.OPCODE.GEMM, waits=(e1,), signal_event=e2,
                   operands=gemm_operands,
                   attr_index=builder.gemm_attr(1, 4, 4, 4, A.DTYPE.FP16, A.DTYPE.FP32))
    stream.command(A.OPCODE.ELEMENTWISE, waits=(e2,), signal_event=e3,
                   operands=((t, s, a, A.ACCESS_KIND.READ_ONLY),
                             (t, s, a, A.ACCESS_KIND.READ_WRITE)),
                   attr_index=builder.elementwise_attr(16, A.DTYPE.FP16))
    repeat_attr = builder.repeat_attr(begin_ordinal, command_count, repeat_count)
    stream.command(A.OPCODE.REPEAT, waits=(e3,), attr_index=repeat_attr)
    stream.command(A.OPCODE.REQUEST_END, waits=(e3,), signal_event=e_end)
    stream.command(A.OPCODE.HALT, waits=(e_end,))
    return builder.build()


def test_repeat_attr_wire_round_trip(arch):
    program = _repeat_program(arch, 1, 2, 3)
    verify_program(program, arch)
    blob = encode_program(program)
    decoded = decode_program(blob)
    verify_program(decoded, arch)
    attr = decoded.op_attrs[1]  # 0 = gemm, 1 = elementwise, 2 = repeat
    repeat_attrs = [a for a in decoded.op_attrs if a.kind == A.ATTR_KIND.REPEAT_V1]
    assert len(repeat_attrs) == 1
    values = dict(zip(repeat_attrs[0].payload_fields, repeat_attrs[0].payload))
    assert values["subrange_begin_stream_ordinal"] == 1
    assert values["subrange_command_count"] == 2
    assert values["repeat_count"] == 3
    assert values["flags"] == 0


def test_repeat_non_adjacent_subrange_rejected(arch):
    program = _repeat_program(arch, 0, 2, 3)  # subrange must end at REPEAT ordinal
    with pytest.raises(MeshIrError) as err:
        verify_program(program, arch)
    assert err.value.code == "E_ABI_BOUNDS"


def test_repeat_zero_count_rejected(arch):
    program = _repeat_program(arch, 1, 0, 3)
    with pytest.raises(MeshIrError) as err:
        verify_program(program, arch)
    assert err.value.code == "E_ABI_BOUNDS"


def test_repeat_missing_repeat_count_rejected(arch):
    program = _repeat_program(arch, 1, 2, 0)
    with pytest.raises(MeshIrError) as err:
        verify_program(program, arch)
    assert err.value.code == "E_ABI_BOUNDS"


def test_repeat_forbidden_member_rejected(arch):
    program = _repeat_program(arch, 1, 2, 3)
    # subrange commands are ordinals 1,2 (GEMM, ELEMENTWISE); swap the first
    # member to HALT which is forbidden inside a REPEAT subrange.
    for index, command in enumerate(program.commands):
        if command.opcode == A.OPCODE.GEMM:
            program.commands[index] = copy.copy(command)
            program.commands[index].opcode = A.OPCODE.HALT
            program.commands[index].engine = A.ENGINE.CONTROL
            break
    with pytest.raises(MeshIrError) as err:
        verify_program(program, arch)
    assert err.value.code in ("E_ABI_BOUNDS", "E_STREAM_CONTRACT")

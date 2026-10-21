from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import REPEAT_COUNT, build_repeat_program
from mesh_ir.scheduled.model import ControlCommandSource, RepeatCommandAttrs
from mesh_ir.scheduled.verify import verify_program


ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


def refreshed(program):
    return replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))


def changed_repeat(program, attrs):
    semantics = tuple(
        replace(item, source=ControlCommandSource(attrs))
        if type(item.source) is ControlCommandSource and type(item.source.attrs) is RepeatCommandAttrs
        else item
        for item in program.semantics.command_semantics
    )
    return refreshed(replace(program, semantics=replace(program.semantics, command_semantics=semantics)))


def test_repeat_three_command_body_roundtrips_complete_codec(arch):
    program = build_repeat_program(arch)
    assert verify_program(program, arch).program is program
    attrs = next(item.source.attrs for item in program.semantics.command_semantics if type(item.source) is ControlCommandSource and type(item.source.attrs) is RepeatCommandAttrs)
    assert attrs == RepeatCommandAttrs(1, 3, REPEAT_COUNT)
    encoded = encode_program(program)
    decoded = decode_program(encoded)
    assert decoded == program
    assert encode_program(decoded) == encoded
    assert verify_program(decoded, arch).program is decoded


@pytest.mark.parametrize(
    ("attrs", "expected_code"),
    (
        (RepeatCommandAttrs(0, 3, REPEAT_COUNT), "E_ABI_ENUM"),
        (RepeatCommandAttrs(1, 0, REPEAT_COUNT), "E_ABI_BOUNDS"),
        (RepeatCommandAttrs(1, 3, 0), "E_ABI_BOUNDS"),
    ),
)
def test_repeat_invalid_ranges_are_test_only_fresh_hash_mutations(arch, attrs, expected_code):
    baseline = build_repeat_program(arch)
    changed = changed_repeat(baseline, attrs)
    assert changed.semantic_sha256 != baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(changed, arch)
    assert error.value.code == expected_code


def test_repeat_forbidden_member_is_a_test_only_fresh_hash_mutation(arch):
    baseline = build_repeat_program(arch)
    target = next(item for item in baseline.commands if item.opcode == A.OPCODE.GEMM)
    commands = tuple(replace(item, opcode=A.OPCODE.HALT, engine=A.ENGINE.CONTROL) if item.command_id == target.command_id else item for item in baseline.commands)
    changed = refreshed(replace(baseline, commands=commands))
    assert changed.semantic_sha256 != baseline.semantic_sha256
    with pytest.raises(MeshIrError):
        verify_program(changed, arch)

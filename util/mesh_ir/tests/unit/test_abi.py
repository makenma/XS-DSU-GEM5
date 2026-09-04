import dataclasses
import json
import subprocess
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
from mesh_ir.model import RECORD_CLASSES, MeshIrError

ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


@pytest.fixture(scope="module")
def programs(arch):
    return {
        "single": build_single_core_program(arch),
        "dual": build_dual_core_program(arch),
    }


def test_header_and_directory_layout_pinned_by_spec():
    assert A.HEADER_BYTES == 128
    assert A.SECTION_DIR_BYTES == 40
    header_offsets = {f["name"]: f["offset"] for f in A.HEADER_FIELDS}
    assert header_offsets["magic"] == 0
    assert header_offsets["abi_major"] == 8
    assert header_offsets["abi_minor"] == 10
    assert header_offsets["header_bytes"] == 12
    assert header_offsets["file_bytes"] == 16
    assert header_offsets["section_dir_offset"] == 24
    assert header_offsets["section_count"] == 32
    assert header_offsets["flags"] == 36
    assert header_offsets["arch_digest"] == 40
    assert header_offsets["payload_sha256"] == 72
    assert header_offsets["required_features"] == 104
    assert header_offsets["reserved"] == 112
    dir_offsets = {f["name"]: f["offset"] for f in A.SECTION_DIR_FIELDS}
    assert dir_offsets == {
        "section_type": 0,
        "flags": 2,
        "record_bytes": 4,
        "offset": 8,
        "size": 16,
        "count": 24,
        "crc32": 32,
        "reserved": 36,
    }


def test_commands_record_is_exactly_the_spec_40_byte_layout():
    assert A.COMMANDS_BYTES == 40
    offsets = {f["name"]: f["offset"] for f in A.COMMANDS_FIELDS}
    assert offsets == {
        "command_id": 0,
        "source_op_id": 4,
        "core_id": 8,
        "stream_id": 10,
        "engine": 12,
        "opcode": 14,
        "wait_begin": 16,
        "wait_count": 20,
        "operand_count": 22,
        "operand_begin": 24,
        "signal_event": 28,
        "attr_index": 32,
        "debug_loc_id": 36,
    }


def test_every_record_layout_is_dense_and_in_bounds():
    for name in RECORD_CLASSES:
        specs = getattr(A, f"{name}_FIELDS")
        total = getattr(A, f"{name}_BYTES")
        for spec in specs:
            size = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "u64x8": 64}.get(spec["type"])
            if size is None:
                continue
            assert spec["offset"] + size <= total, f"{name}.{spec['name']} overflows"


def test_opcode_closed_set_and_engine_mapping():
    opcodes = {v for k, v in vars(A.OPCODE).items() if not k.startswith("_")}
    assert len(opcodes) == 20
    assert set(A.OPCODE_ENGINE) == opcodes
    engines = {v for k, v in vars(A.ENGINE).items() if not k.startswith("_")}
    assert set(A.OPCODE_ENGINE.values()) <= engines


def test_required_sections_present_in_enum():
    required = set(A.REQUIRED_SECTIONS)
    assert len(required) == 15
    for name in required:
        assert hasattr(A.SECTION_TYPE, name)


def test_model_matches_generated_schema_exactly():
    for name, cls in RECORD_CLASSES.items():
        schema_names = tuple(f["name"] for f in getattr(A, f"{name}_FIELDS"))
        model_names = tuple(f.name for f in dataclasses.fields(cls))
        assert schema_names == model_names, name


def test_golden_programs_round_trip_semantic_sha(programs, arch):
    for name, program in programs.items():
        verify_program(program, arch)
        blob = encode_program(program)
        decoded = decode_program(blob)
        verify_program(decoded, arch)
        assert program.canonical_dict() == decoded.canonical_dict(), name
        assert decoded.semantic_sha256() == program.semantic_sha256(), name


def test_encode_is_deterministic_across_hashseeds(programs, arch):
    import hashlib

    build_call = {
        "single": "build_single_core_program",
        "dual": "build_dual_core_program",
    }
    for name, program in programs.items():
        blob = encode_program(program)
        script = (
            "import sys, hashlib; "
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r}); "
            "from mesh_ir.builder import load_arch; "
            f"from mesh_ir.golden_programs import {build_call[name]}; "
            "from mesh_ir.abi.encoder import encode_program; "
            f"blob = encode_program({build_call[name]}(load_arch({str(ARCH_PATH)!r}))); "
            "print(hashlib.sha256(blob).hexdigest())"
        )
        digests = set()
        for seed in ("0", "1", "12345"):
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
                check=True,
            )
            digests.add(result.stdout.strip())
        assert digests == {hashlib.sha256(blob).hexdigest()}, name


def test_single_core_program_shape(programs):
    program = programs["single"]
    opcodes = [c.opcode for c in program.commands]
    assert opcodes == [
        A.OPCODE.REQUEST_BEGIN,
        A.OPCODE.DMA_LOAD,
        A.OPCODE.DMA_LOAD,
        A.OPCODE.GEMM,
        A.OPCODE.DMA_STORE,
        A.OPCODE.REQUEST_END,
        A.OPCODE.HALT,
    ]
    assert all(c.core_id == 0 for c in program.commands)
    assert len(program.expected_traffic) == 3


def test_dual_core_program_shape(programs):
    program = programs["dual"]
    core0 = [c.opcode for c in program.commands if c.core_id == 0]
    core1 = [c.opcode for c in program.commands if c.core_id == 1]
    assert core0 == [
        A.OPCODE.REQUEST_BEGIN,
        A.OPCODE.DMA_LOAD,
        A.OPCODE.DMA_LOAD,
        A.OPCODE.GEMM,
        A.OPCODE.DMA_P2P_PUSH,
        A.OPCODE.REQUEST_END,
        A.OPCODE.HALT,
    ]
    assert core1 == [
        A.OPCODE.DMA_LOAD,
        A.OPCODE.DMA_LOAD,
        A.OPCODE.GEMM,
        A.OPCODE.RECV_WAIT,
        A.OPCODE.LOCAL_REDUCE,
        A.OPCODE.DMA_STORE,
        A.OPCODE.HALT,
    ]
    p2p = [d for d in program.dma_descriptors if d.kind == A.DMA_KIND.P2P_PUSH]
    assert len(p2p) == 1
    assert p2p[0].dst.memory_space == A.MEMORY_SPACE.PEER_SRAM
    assert p2p[0].dst.owner_core == 1
    assert p2p[0].src.owner_core == 0


def test_arch_digest_binding(programs, arch):
    wrong = load_arch(ARCH_PATH)
    wrong.sram_bytes += 1
    with pytest.raises(MeshIrError) as err:
        verify_program(programs["single"], wrong)
    assert err.value.code == "E_ARCH_DIGEST"


def test_generated_enum_closed_sets_cover_every_enum():
    assert set(A.ENUM_CLOSED_SETS) == {
        "opcode", "engine", "memory_space", "tensor_role", "dtype",
        "storage_class", "access_kind", "layout_kind", "dma_kind",
        "event_kind", "attr_kind", "relocation_kind", "stream_flags",
        "tensor_flags", "fence_scope", "vector_algorithm",
    }
    for enum_name, values in A.ENUM_CLOSED_SETS.items():
        mask = A.ENUM_MASKS[enum_name]
        for value in values:
            assert mask & (1 << value), enum_name
    assert A.ENUM_MASKS["stream_flags"] == 0b110
    assert A.ENUM_ALLOWED_BITS["stream_flags"] == (
        A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL
    )


def test_generated_field_offsets_match_field_specs():
    for name in RECORD_CLASSES:
        offsets = getattr(A, f"{name}_FIELD_OFFSETS")
        spec_offsets = {f["name"]: f["offset"] for f in getattr(A, f"{name}_FIELDS")}
        assert offsets == spec_offsets, name
    assert A.GEMM_V1_FIELD_OFFSETS["dtype"] == 18
    assert A.GEMM_V1_FIELD_OFFSETS["efficiency_q16"] == 24
    assert A.DMA_DESCRIPTORS_FIELD_OFFSETS["physical_storage_bytes"] == 100
    assert A.EVENTS_FIELD_OFFSETS["kind"] == 4


def test_payload_by_kind_covers_attr_kinds():
    kinds = {v for k, v in vars(A.ATTR_KIND).items() if not k.startswith("_")}
    assert set(A.PAYLOAD_BY_KIND) == kinds
    for kind, name in A.PAYLOAD_BY_KIND.items():
        assert hasattr(A, f"{name}_FIELDS"), name


def test_generated_cpp_header_has_decoders():
    header = (
        Path(__file__).resolve().parents[4]
        / "src/dev/ai_mesh/generated/mesh_ir_abi.hh"
    ).read_text()
    for record in ("Command", "DmaDescriptor", "DmaEndpoint", "Profile", "Shard",
                   "Tensor", "Relocation", "Entrypoint", "ExpectedTraffic"):
        assert f"struct {record}" in header, record
        assert f"decode{record}(" in header, record
    assert "using AttrPayload = std::variant<" in header
    assert "decodeAttrPayload(" in header
    assert "kDtypeValuesMask" in header
    for payload in ("RepeatV1", "GemmV1", "BmmV1", "ElementwiseV1", "ReduceV1",
                    "SoftmaxV1", "NormV1", "FillV1", "BlockedMnkLayoutV1",
                    "RecvWaitV1"):
        assert f"decode{payload}(" in header, payload

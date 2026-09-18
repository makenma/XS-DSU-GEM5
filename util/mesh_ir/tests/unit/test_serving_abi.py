import dataclasses
import hashlib
import json
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import (
    decode_header,
    decode_program,
    decode_section_dir,
)
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.model import MeshIrError
from mesh_ir.serving_programs import serving_program

REPO = Path(__file__).resolve().parents[4]
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"

SERVING_RECORDS = {
    "AGENT_REQUEST_PROFILES": 96,
    "AGENT_INSTANCE_PROFILES": 96,
    "AGENT_SOURCE_CORE_MAP": 4,
    "AGENT_INSTANCE_MEMBER_BINDINGS": 24,
    "AGENT_REQUEST_BINDING_REQUIREMENTS": 16,
    "AGENT_PUBLISH_SURROGATE_BINDINGS": 28,
}

SERVING_SECTIONS = (
    "AGENT_REQUEST_PROFILES",
    "AGENT_INSTANCE_PROFILES",
    "AGENT_SOURCE_CORE_MAP",
    "AGENT_INSTANCE_MEMBER_BINDINGS",
    "AGENT_REQUEST_BINDING_REQUIREMENTS",
    "AGENT_PUBLISH_SURROGATE_BINDINGS",
)

FIELD_BYTES = {
    "u8": 1, "u16": 2, "u32": 4, "u64": 8,
    "bytes7": 7, "bytes16": 16, "bytes28": 28, "bytes32": 32, "u64x8": 64,
}


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


def test_agent_serving_abi_surface_is_frozen_by_contract():
    assert A.AGENT_SERVING_V1 == 0x2
    assert A.KNOWN_FEATURE_MASK == 0x7
    assert A.FEATURE_MIN_WRITER_MINOR == {
        "DYNAMIC_MOE_V1": 1, "AGENT_SERVING_V1": 1,
        "PROFILE_SCOPED_EXECUTION_V1": 3}
    assert A.SECTION_TYPE.PROFILE_STREAM_RANGES == 16
    assert A.SECTION_TYPE.AGENT_REQUEST_PROFILES == 0x4004
    assert A.SECTION_TYPE.AGENT_INSTANCE_PROFILES == 0x4005
    assert A.SECTION_TYPE.AGENT_SOURCE_CORE_MAP == 0x4006
    assert A.SECTION_TYPE.AGENT_INSTANCE_MEMBER_BINDINGS == 0x4007
    assert A.SECTION_TYPE.AGENT_REQUEST_BINDING_REQUIREMENTS == 0x4008
    assert A.SECTION_TYPE.AGENT_PUBLISH_SURROGATE_BINDINGS == 0x4009
    for name, size in SERVING_RECORDS.items():
        assert getattr(A, f"{name}_BYTES") == size, name
    assert A.DMA_FILL_RUNTIME_BOUND_SENTINEL == 0x52554E54494D4531
    assert A.DMA_FILL_KIND.CONSTANT_PATTERN == 0
    assert A.DMA_FILL_KIND.AGENT_OUTPUT_SURROGATE == 1
    assert A.AGENT_ALLOCATION_ROLE.NONE == 0
    assert A.AGENT_ALLOCATION_ROLE.PUBLISH_SURROGATE_SOURCE == 1
    assert A.AGENT_DIGEST_SOURCE.REQUEST_SEMANTIC_OUTPUT_DIGEST == 0
    assert A.PATH_KIND.INITIAL_PREFILL == 0
    assert A.PATH_KIND.KV_REUSE == 1
    assert A.PATH_KIND.REPREFILL == 2
    assert (A.PHASE.PREFILL, A.PHASE.DECODE, A.PHASE.PUBLISH) == (1, 2, 3)
    assert A.conditional_required_sections(0) == ()
    assert A.conditional_required_sections(A.AGENT_SERVING_V1) == (
        A.SECTION_TYPE.AGENT_REQUEST_PROFILES,
        A.SECTION_TYPE.AGENT_INSTANCE_PROFILES,
        A.SECTION_TYPE.AGENT_SOURCE_CORE_MAP,
        A.SECTION_TYPE.AGENT_INSTANCE_MEMBER_BINDINGS,
        A.SECTION_TYPE.AGENT_REQUEST_BINDING_REQUIREMENTS,
        A.SECTION_TYPE.AGENT_PUBLISH_SURROGATE_BINDINGS,
    )
    assert A.SECTION_REQUIRED_FEATURES[
        A.SECTION_TYPE.AGENT_PUBLISH_SURROGATE_BINDINGS] == A.AGENT_SERVING_V1


def test_agent_serving_records_follow_the_declared_wire_order():
    expected = {
        "AGENT_REQUEST_PROFILES": [
            "program_id", "profile_id", "flags", "reserved0",
            "requested_profile_key", "delta_input_tokens",
            "full_input_tokens", "expected_cached_tokens", "output_tokens",
            "input_binding_bytes", "delta_input_dma_bytes",
            "full_input_dma_bytes", "host_output_bytes",
            "primary_input_symbol_id", "primary_output_symbol_id",
            "primary_kv_symbol_id", "source_rank_count",
            "source_core_map_begin", "path_mask", "kv_bytes_per_token",
            "publish_chunk_bytes"],
        "AGENT_INSTANCE_PROFILES": [
            "instance_profile_id", "request_program_id", "request_profile_id",
            "path_kind", "phase", "member_count", "decode_chunk_tokens",
            "mesh_entrypoint_id", "mesh_profile_id",
            "valid_tokens_per_member", "kv_tokens_before",
            "local_padded_members", "local_padded_tokens_per_member",
            "primary_input_symbol_id", "primary_output_symbol_id",
            "primary_kv_symbol_id", "flags", "member_binding_first",
            "member_binding_count", "host_input_dma_bytes_per_member",
            "host_output_dma_bytes_per_member", "kv_read_bytes_per_member",
            "kv_write_bytes_per_member"],
        "AGENT_SOURCE_CORE_MAP": ["core_id", "reserved"],
        "AGENT_INSTANCE_MEMBER_BINDINGS": [
            "instance_profile_id", "member_ordinal", "reserved0",
            "static_input_symbol_id", "static_output_symbol_id",
            "static_kv_symbol_id", "expected_logical_source_rank"],
        "AGENT_REQUEST_BINDING_REQUIREMENTS": [
            "request_program_id", "request_profile_id", "binding_kind",
            "binding_flags", "symbol_id", "reserved"],
        "AGENT_PUBLISH_SURROGATE_BINDINGS": [
            "instance_profile_id", "member_ordinal", "reserved0",
            "allocation_id", "producer_command_id", "completion_event_id",
            "fill_kind", "allocation_role", "digest_source", "reserved1"],
    }
    for name, fields in expected.items():
        specs = getattr(A, f"{name}_FIELDS")
        assert [spec["name"] for spec in specs] == fields, name
        cursor = 0
        for spec in specs:
            assert spec["offset"] == cursor, f"{name}.{spec['name']}"
            cursor += FIELD_BYTES[spec["type"]]
        assert cursor == getattr(A, f"{name}_BYTES"), name


def test_serving_program_round_trips_byte_identically(arch):
    program = serving_program(arch)
    blob = encode_program(program)
    decoded = decode_program(blob)
    assert decoded.required_features == (
        A.AGENT_SERVING_V1 | A.PROFILE_SCOPED_EXECUTION_V1)
    assert encode_program(decoded) == blob
    assert decoded.agent_request_profiles == program.agent_request_profiles
    assert decoded.agent_instance_profiles == program.agent_instance_profiles
    assert decoded.agent_source_core_map == program.agent_source_core_map
    assert (decoded.agent_instance_member_bindings ==
            program.agent_instance_member_bindings)
    assert (decoded.agent_request_binding_requirements ==
            program.agent_request_binding_requirements)
    assert (decoded.agent_publish_surrogate_bindings ==
            program.agent_publish_surrogate_bindings)


def test_serving_program_declares_exactly_the_conditional_sections(arch):
    blob = encode_program(serving_program(arch))
    header = decode_header(blob)
    payloads = decode_section_dir(blob, header)
    for name in SERVING_SECTIONS:
        assert getattr(A.SECTION_TYPE, name) in payloads, name
    assert A.SECTION_TYPE.MOE_LAYER_SPECS not in payloads


def _section_payload(blob: bytes, name: str):
    header = decode_header(blob)
    payloads = decode_section_dir(blob, header)
    entry, payload = payloads[getattr(A.SECTION_TYPE, name)]
    return entry, payload


def _install(blob: bytes, name: str, offset: int, raw: bytes) -> bytes:
    entry, _ = _section_payload(blob, name)
    header = decode_header(blob)
    image = bytearray(blob)
    start = entry["offset"] + offset
    image[start:start + len(raw)] = raw
    section = bytes(image[entry["offset"]:entry["offset"] + entry["size"]])
    crc = zlib.crc32(section) & 0xFFFFFFFF
    stype = getattr(A.SECTION_TYPE, name)
    for index in range(header["section_count"]):
        base = header["section_dir_offset"] + index * A.SECTION_DIR_BYTES
        if int.from_bytes(image[base:base + 2], "little") == stype:
            image[base + 32:base + 36] = crc.to_bytes(4, "little")
            break
    image[72:104] = hashlib.sha256(bytes(image[A.HEADER_BYTES:])).digest()
    return bytes(image)


def test_reader_rejects_serving_sections_without_the_feature_bit(arch):
    blob = encode_program(serving_program(arch))
    image = bytearray(blob)
    image[104:112] = (0).to_bytes(8, "little")
    with pytest.raises(MeshIrError) as err:
        decode_program(bytes(image))
    assert err.value.code == "E_ABI_FEATURE"


def test_reader_rejects_the_feature_bit_without_every_section(arch):
    base = encode_program(build_single_core_program(arch))
    header = decode_header(base)
    header["required_features"] = A.AGENT_SERVING_V1
    with pytest.raises(MeshIrError) as err:
        decode_section_dir(base, header)
    assert err.value.code == "E_ABI_SECTION_RANGE"


def test_writer_rejects_serving_sections_without_the_bit(arch):
    program = dataclasses.replace(
        serving_program(arch), required_features=0)
    with pytest.raises(MeshIrError) as err:
        encode_program(program)
    assert err.value.code == "E_ABI_FEATURE"


def test_feature_bit_below_writer_minor_is_rejected(arch):
    blob = encode_program(serving_program(arch))
    image = bytearray(blob)
    image[10:12] = (0).to_bytes(2, "little")
    image[72:104] = hashlib.sha256(bytes(image[A.HEADER_BYTES:])).digest()
    with pytest.raises(MeshIrError) as err:
        decode_program(bytes(image))
    assert err.value.code == "E_ABI_VERSION"


def test_combined_feature_bits_round_trip(arch):
    program = dataclasses.replace(
        serving_program(arch),
        required_features=(A.AGENT_SERVING_V1 |
                           A.PROFILE_SCOPED_EXECUTION_V1 |
                           A.DYNAMIC_MOE_V1))
    blob = encode_program(program)
    header = decode_header(blob)
    payloads = decode_section_dir(blob, header)
    for name in ("MOE_LAYER_SPECS", "CONTENT_DIGESTS",
                 "AGENT_REQUEST_PROFILES"):
        assert getattr(A.SECTION_TYPE, name) in payloads, name
    decoded = decode_program(blob)
    assert decoded.required_features == (
        A.AGENT_SERVING_V1 | A.PROFILE_SCOPED_EXECUTION_V1 |
        A.DYNAMIC_MOE_V1)
    assert encode_program(decoded) == blob
    serving_only = decode_program(encode_program(serving_program(arch)))
    assert decoded.semantic_sha256() != serving_only.semantic_sha256()


def test_serving_only_feature_does_not_enable_dynamic_moe(arch):
    blob = encode_program(serving_program(arch))
    program = decode_program(blob)
    assert program.moe_layer_specs == []
    assert not program.required_features & A.DYNAMIC_MOE_V1


def test_reserved_fields_are_fail_closed(arch):
    blob = encode_program(serving_program(arch))
    tampered = _install(blob, "AGENT_REQUEST_PROFILES", 6, b"\x01\x00")
    with pytest.raises(MeshIrError) as err:
        decode_program(tampered)
    assert err.value.code == "E_ABI_RESERVED"


def test_enum_fields_are_fail_closed(arch):
    blob = encode_program(serving_program(arch))
    for offset, value in ((8, 3), (10, 4), (20, 2), (22, 2), (24, 1)):
        name = ("AGENT_INSTANCE_PROFILES" if offset in (8, 10)
                else "AGENT_PUBLISH_SURROGATE_BINDINGS")
        tampered = _install(blob, name, offset,
                            value.to_bytes(2, "little"))
        with pytest.raises(MeshIrError) as err:
            decode_program(tampered)
        assert err.value.code == "E_ABI_ENUM", (offset, value)


def test_instance_flags_must_be_zero(arch):
    blob = encode_program(serving_program(arch))
    tampered = _install(blob, "AGENT_INSTANCE_PROFILES", 52,
                        (1).to_bytes(4, "little"))
    with pytest.raises(MeshIrError) as err:
        decode_program(tampered)
    assert err.value.code == "E_ABI_RESERVED"


def test_u32_instance_profile_id_round_trips(arch):
    program = serving_program(arch)
    program = dataclasses.replace(
        program,
        agent_instance_profiles=[
            dataclasses.replace(record, instance_profile_id=0xFFFFFFFF)
            for record in program.agent_instance_profiles
        ],
        agent_instance_member_bindings=[
            dataclasses.replace(record, instance_profile_id=0xFFFFFFFF)
            for record in program.agent_instance_member_bindings
        ],
    )
    decoded = decode_program(encode_program(program))
    assert decoded.agent_instance_profiles[0].instance_profile_id == 0xFFFFFFFF


EXACT_RECORD_BYTES = {
    "AGENT_REQUEST_PROFILES": (
        0x0102, 0x0304, 1, 0, 0x1122334455667788, 0x00000008, 0x00000010,
        0x00000008, 0x00000004, 0x0000000000000100, 0x0000000000000080,
        0x0000000000000100, 0x0000000000002000, 0x00000011, 0x00000012,
        0x00000013, 0x00000002, 0x00000001, 0x00000007, 0x00000010,
        0x00001000),
    "AGENT_INSTANCE_PROFILES": (
        0x0000000B, 0x0102, 0x0304, 1, 2, 0x0002, 0x0001, 0x00000001,
        0x00000002, 0x00000001, 0x00000008, 0, 0, 0, 0, 0x00000013, 0,
        0x00000002, 0x00000002, 0, 0, 0x0000000000000080,
        0x0000000000000010),
    "AGENT_SOURCE_CORE_MAP": (0x0001, 0),
    "AGENT_INSTANCE_MEMBER_BINDINGS": (
        0x0000000B, 0x0001, 0, 0x00000021, 0x00000022, 0x00000023,
        0x00000001),
    "AGENT_REQUEST_BINDING_REQUIREMENTS": (
        0x0102, 0x0304, 0x0003, 0x0002, 0x00000031, 0),
    "AGENT_PUBLISH_SURROGATE_BINDINGS": (
        0x0000000B, 0x0001, 0, 0x00000041, 0x00000042, 0x00000043, 1, 1, 0,
        0),
}

EXACT_RECORD_HEX = {
    "AGENT_REQUEST_PROFILES":
        "0201040301000000887766554433221108000000100000000800000004000000"
        "0001000000000000800000000000000000010000000000000020000000000000"
        "1100000012000000130000000200000001000000070000001000000000100000",
    "AGENT_INSTANCE_PROFILES":
        "0b00000002010403010002000200010001000000020000000100000008000000"
        "0000000000000000000000000000000013000000000000000200000002000000"
        "0000000000000000000000000000000080000000000000001000000000000000",
    "AGENT_SOURCE_CORE_MAP":
        "01000000",
    "AGENT_INSTANCE_MEMBER_BINDINGS":
        "0b0000000100000021000000220000002300000001000000",
    "AGENT_REQUEST_BINDING_REQUIREMENTS":
        "02010403030002003100000000000000",
    "AGENT_PUBLISH_SURROGATE_BINDINGS":
        "0b000000010000004100000042000000430000000100010000000000",
}

def test_serving_record_bytes_are_frozen_goldens():
    for name, values in EXACT_RECORD_BYTES.items():
        fields = getattr(A, f"{name}_FIELDS")
        assert len(values) == len(fields), name
        packed = getattr(A, f"{name}_FORMAT").pack(*values)
        assert len(packed) == getattr(A, f"{name}_BYTES"), name
        assert packed.hex() == EXACT_RECORD_HEX[name], name


FIXTURE = REPO / "tests/gem5/ai_mesh/fixtures/gate6"


def test_serving_fixture_matches_the_cross_language_expected():
    from mesh_ir.model import canonical
    from mesh_ir.serving_profiles import (
        program_profile_key_base_digest,
        serving_capacity_required,
    )

    blob = (FIXTURE / "serving_min.mshb").read_bytes()
    expected = json.loads((FIXTURE / "serving_min_expected.json").read_text())
    program = decode_program(blob)
    assert encode_program(program) == blob
    assert program.abi_major == expected["abi_major"] == 1
    assert program.abi_minor == expected["abi_minor"]
    assert program.required_features == expected["required_features"]
    assert program.required_features == (
        A.AGENT_SERVING_V1 | A.PROFILE_SCOPED_EXECUTION_V1)
    assert program.semantic_sha256() == expected["semantic_sha256"]
    assert program_profile_key_base_digest(program).hex() == \
        expected["profile_key_base_digest"]
    assert serving_capacity_required(program).fields() == \
        expected["capacity_required"]
    for name in (
            "agent_request_profiles", "agent_instance_profiles",
            "agent_source_core_map", "agent_instance_member_bindings",
            "agent_request_binding_requirements",
            "agent_publish_surrogate_bindings"):
        actual = [canonical(record) for record in getattr(program, name)]
        assert actual == expected[name], name


def test_full_view_fixture_matches_the_cross_language_expected():
    from mesh_ir.model import canonical

    blob = (FIXTURE / "serving_fullview.mshb").read_bytes()
    expected = json.loads(
        (FIXTURE / "serving_fullview_expected.json").read_text())
    program = decode_program(blob)
    assert encode_program(program) == blob
    assert program.required_features == (
        A.AGENT_SERVING_V1 | A.PROFILE_SCOPED_EXECUTION_V1)
    assert program.semantic_sha256() == expected["semantic_sha256"]
    for name in (
            "agent_request_profiles", "agent_instance_profiles",
            "agent_source_core_map", "agent_instance_member_bindings",
            "agent_request_binding_requirements",
            "agent_publish_surrogate_bindings"):
        actual = [canonical(record) for record in getattr(program, name)]
        assert actual == expected[name], name
    verify_program(program, load_arch(ARCH_PATH))

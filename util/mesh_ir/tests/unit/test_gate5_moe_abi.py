import hashlib
import json
import sys
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import (
    decode_header,
    decode_program,
    decode_section_dir,
)
from mesh_ir.abi.dependency import (
    command_prerequisites,
    prerequisite_closure,
)
from mesh_ir.abi.encoder import encode_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.abi.verifier import verify_program
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.model import MeshIrError, canonical
from mesh_ir.weight_registry import (
    load_model_weight_image,
    load_program_weight_registry,
    region_digest,
)

REPO = Path(__file__).resolve().parents[4]
FIXTURE = REPO / "tests/gem5/ai_mesh/fixtures/gate5"
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"
WEIGHT_BACKING_BYTES = 2 * 8192


@pytest.fixture(scope="module")
def fixture():
    blob = (FIXTURE / "moe_min.mshb").read_bytes()
    expected = json.loads((FIXTURE / "moe_min_expected.json").read_text())
    return blob, decode_program(blob), expected


def test_gate5_fixture_header_and_feature_bits(fixture):
    blob, program, expected = fixture
    assert program.abi_major == expected["abi_major"] == 1
    assert program.abi_minor == expected["abi_minor"] == 1
    assert program.required_features == expected["required_features"]
    assert program.required_features == A.DYNAMIC_MOE_V1


def test_gate5_fixture_records_match_expected_values(fixture):
    _, program, expected = fixture
    for name in ("content_digests", "moe_layer_specs", "moe_expert_specs",
                 "moe_dynamic_regions", "moe_kernel_specs"):
        actual = [canonical(record) for record in getattr(program, name)]
        assert actual == expected[name], name


def test_gate5_fixture_weight_binding_is_external_read_only(fixture):
    _, program, expected = fixture
    expert = program.moe_expert_specs[0]
    weight = next(t for t in program.tensors
                  if t.role == A.TENSOR_ROLE.WEIGHT)
    symbol = next(r for r in program.relocations
                  if r.tensor_id == weight.tensor_id)
    assert expert.weight_symbol_id == symbol.symbol_sid
    assert weight.storage_class == A.STORAGE_CLASS.EXTERNAL
    assert weight.access == A.ACCESS_KIND.READ_ONLY
    assert weight.flags & A.TENSOR_FLAGS.HAS_CONTENT_SHA256
    assert weight.content_sha256.hex() == expected["weight_digest_hex"]
    digest = program.content_digests[expert.weight_digest_index]
    assert digest.object_kind == A.TENSOR_ROLE.WEIGHT
    assert digest.object_id == weight.tensor_id
    assert digest.digest == weight.content_sha256
    for expert_spec in program.moe_expert_specs:
        assert expert_spec.weight_symbol_id == expert.weight_symbol_id
        assert (expert_spec.weight_region_offset + expert_spec.weight_bytes
                <= WEIGHT_BACKING_BYTES)


def test_gate5_fixture_region_lands_inside_static_lifecycle(fixture):
    _, program, _ = fixture
    region = program.moe_dynamic_regions[0]
    commands = {c.command_id: c for c in program.commands}
    first = commands[region.insert_after_command_id]
    second = commands[region.resume_before_command_id]
    assert first.stream_id == second.stream_id == region.stream_id
    assert first.core_id == second.core_id == region.core_id
    assert first.signal_event == region.entry_event_id
    assert second.command_id == first.command_id + 1
    begin = next(c for c in program.commands
                 if c.opcode == A.OPCODE.REQUEST_BEGIN)
    end = next(c for c in program.commands if c.opcode == A.OPCODE.REQUEST_END)
    assert begin.command_id < first.command_id < second.command_id
    assert second.command_id < end.command_id
    layer = program.moe_layer_specs[0]
    assert layer.dynamic_region_count == len(program.moe_dynamic_regions)
    assert layer.expert_count == len(program.moe_expert_specs)
    assert layer.top_k == layer.expert_count
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    assert kernel.layer_id == layer.layer_id
    assert kernel.input_token_bytes == layer.token_bytes
    assert kernel.output_token_bytes == layer.output_token_bytes
    expert_bytes = program.moe_expert_specs[0].weight_bytes
    assert kernel.weight_operand_bytes == expert_bytes
    assert kernel.max_m >= layer.max_tokens_per_frozen_batch


def test_gate5_fixture_reencodes_byte_identically(fixture):
    blob, program, expected = fixture
    assert encode_program(program) == blob
    assert program.semantic_sha256() == expected["semantic_sha256"]


def _strip_feature(blob: bytes) -> bytes:
    image = bytearray(blob)
    image[104:112] = (0).to_bytes(8, "little")
    return bytes(image)


def test_gate5_sections_are_rejected_without_the_feature_bit(fixture):
    blob, _, _ = fixture
    with pytest.raises(MeshIrError) as err:
        decode_program(_strip_feature(blob))
    assert err.value.code == "E_ABI_FEATURE"


def test_gate5_feature_requires_every_conditional_section():
    base = encode_program(build_single_core_program(load_arch(ARCH_PATH)))
    header = decode_header(base)
    header["required_features"] = A.DYNAMIC_MOE_V1
    with pytest.raises(MeshIrError) as err:
        decode_section_dir(base, header)
    assert err.value.code == "E_ABI_SECTION_RANGE"


def test_gate5_writer_rejects_feature_sections_without_the_bit(fixture):
    _, program, _ = fixture
    stripped = replace(program, required_features=0)
    with pytest.raises(MeshIrError) as err:
        encode_program(stripped)
    assert err.value.code == "E_ABI_FEATURE"


def _patch_record(blob: bytes, section_type: int, offset: int, width: int,
                  value: int) -> bytes:
    image = bytearray(blob)
    directory = int.from_bytes(image[24:32], "little")
    count = int.from_bytes(image[32:36], "little")
    for index in range(count):
        entry = directory + index * A.SECTION_DIR_BYTES
        if int.from_bytes(image[entry:entry + 2], "little") != section_type:
            continue
        section = int.from_bytes(image[entry + 8:entry + 16], "little")
        size = int.from_bytes(image[entry + 16:entry + 24], "little")
        start = section + offset
        image[start:start + width] = value.to_bytes(width, "little")
        crc = zlib.crc32(bytes(image[section:section + size])) & 0xFFFFFFFF
        image[entry + 32:entry + 36] = crc.to_bytes(4, "little")
        image[72:104] = hashlib.sha256(bytes(image[128:])).digest()
        return bytes(image)
    raise AssertionError(f"section {section_type} not found")


MOE_MUTATIONS = (
    ("content_digest_object_kind", A.SECTION_TYPE.CONTENT_DIGESTS, 0, 2,
     0xFFFF, "E_ABI_ENUM"),
    ("content_digest_reserved", A.SECTION_TYPE.CONTENT_DIGESTS, 2, 2, 1,
     "E_ABI_RESERVED"),
    ("layer_overflow_policy", A.SECTION_TYPE.MOE_LAYER_SPECS, 28, 2, 9,
     "E_ABI_ENUM"),
    ("layer_transport_mode", A.SECTION_TYPE.MOE_LAYER_SPECS, 30, 2, 5,
     "E_ABI_ENUM"),
    ("layer_reserved1", A.SECTION_TYPE.MOE_LAYER_SPECS, 76, 4, 1,
     "E_ABI_RESERVED"),
    ("expert_reserved_core", A.SECTION_TYPE.MOE_EXPERT_SPECS, 10, 2, 1,
     "E_ABI_RESERVED"),
    ("expert_reserved", A.SECTION_TYPE.MOE_EXPERT_SPECS,
     A.MOE_EXPERT_SPECS_BYTES + 36, 4, 1, "E_ABI_RESERVED"),
    ("region_reserved", A.SECTION_TYPE.MOE_DYNAMIC_REGIONS, 68, 4, 1,
     "E_ABI_RESERVED"),
    ("kernel_expert_opcode", A.SECTION_TYPE.MOE_KERNEL_SPECS, 4, 2, 99,
     "E_ABI_ENUM"),
    ("kernel_combine_kind", A.SECTION_TYPE.MOE_KERNEL_SPECS, 26, 2, 3,
     "E_ABI_ENUM"),
    ("kernel_reserved0", A.SECTION_TYPE.MOE_KERNEL_SPECS, 52, 4, 1,
     "E_ABI_RESERVED"),
)


@pytest.mark.parametrize(
    "name,section_type,offset,width,value,code",
    MOE_MUTATIONS,
    ids=[case[0] for case in MOE_MUTATIONS],
)
def test_gate5_recomputed_checksum_mutations_are_rejected(
        fixture, name, section_type, offset, width, value, code):
    blob, _, _ = fixture
    corrupted = _patch_record(blob, section_type, offset, width, value)
    assert corrupted != blob
    with pytest.raises(MeshIrError) as err:
        decode_program(corrupted)
    assert err.value.code == code, name


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


MOE_VIOLATIONS = (
    ("layer_top_k", ("moe_layer_specs", 0, "top_k", 3), "E_ABI_BOUNDS"),
    ("layer_flags", ("moe_layer_specs", 0, "flags", 1), "E_ABI_RESERVED"),
    ("layer_event_bound",
     ("moe_layer_specs", 0, "max_materialized_events", 16), "E_ABI_BOUNDS"),
    ("layer_route_bound", ("moe_layer_specs", 0, "max_routes", 1),
     "E_ABI_BOUNDS"),
    ("layer_token_bytes", ("moe_layer_specs", 0, "token_bytes", 0),
     "E_ABI_BOUNDS"),
    ("expert_core", ("moe_expert_specs", 1, "core_id", 9), "E_ABI_BOUNDS"),
    ("expert_dense_id", ("moe_expert_specs", 1, "expert_id", 5),
     "E_ABI_ORDER"),
    ("expert_weight_escape", ("moe_expert_specs", 1,
                              "weight_region_offset", 8192), "E_ABI_BOUNDS"),
    ("expert_weight_empty", ("moe_expert_specs", 0, "weight_bytes", 0),
     "E_ABI_BOUNDS"),
    ("expert_digest_index", ("moe_expert_specs", 0, "weight_digest_index",
                             0xFFFFFFFF), "E_ABI_BOUNDS"),
    ("region_gate_adjacency", ("moe_dynamic_regions", 0,
                               "resume_before_command_id", 6),
     "E_ABI_BOUNDS"),
    ("region_entry_event", ("moe_dynamic_regions", 0, "entry_event_id", 1),
     "E_ABI_BOUNDS"),
    ("region_scratch_escape", ("moe_dynamic_regions", 0, "scratch_bytes",
                               0x200000), "E_ABI_BOUNDS"),
    ("region_alignment", ("moe_dynamic_regions", 0, "scratch_alignment", 48),
     "E_ABI_BOUNDS"),
    ("kernel_shape", ("moe_kernel_specs", 0, "k", 64), "E_ABI_BOUNDS"),
    ("kernel_max_m", ("moe_kernel_specs", 0, "max_m", 1), "E_ABI_BOUNDS"),
    ("kernel_opcode", ("moe_kernel_specs", 0, "expert_opcode",
                       A.OPCODE.SOFTMAX), "E_ABI_ENUM"),
    ("kernel_weight", ("moe_kernel_specs", 0, "weight_operand_bytes", 8192),
     "E_ABI_BOUNDS"),
    ("digest_object", ("content_digests", 0, "object_id", 3), "E_ABI_BOUNDS"),
)


@pytest.mark.parametrize(
    "table,index,field,value,code",
    [case[1] + (case[2],) for case in MOE_VIOLATIONS],
    ids=[case[0] for case in MOE_VIOLATIONS],
)
def test_gate5_verifier_rejects_semantic_violations(fixture, arch, table,
                                                    index, field, value,
                                                    code):
    _, program, _ = fixture
    records = list(getattr(program, table))
    records[index] = replace(records[index], **{field: value})
    broken = replace(program, **{table: records})
    with pytest.raises(MeshIrError) as err:
        verify_program(broken, arch)
    assert err.value.code == code, f"{table}.{field}"


def test_gate5_region_gate_must_sit_inside_the_request_body(fixture, arch):
    _, program, _ = fixture
    region = replace(program.moe_dynamic_regions[0],
                     insert_after_command_id=6, resume_before_command_id=7,
                     entry_event_id=6)
    broken = replace(program, moe_dynamic_regions=[region])
    with pytest.raises(MeshIrError) as err:
        verify_program(broken, arch)
    assert err.value.code == "E_LIFECYCLE"
    assert "region gate must precede" in err.value.message


def test_gate5_fixture_passes_the_full_verifier(fixture, arch):
    _, program, expected = fixture
    verify_program(program, arch)
    assert program.semantic_sha256() == expected["semantic_sha256"]


MULTI_FIXTURE = FIXTURE / "moe_multi.mshb"
IMAGE_JSON = REPO / "tests/gem5/ai_mesh/fixtures/model_weight_image_v1.json"
REGISTRY_JSON = (REPO / "tests/gem5/ai_mesh/fixtures"
                 / "program_weight_bindings_v1.json")


def test_gate5_multi_layer_fixture_has_distinct_expert_counts(arch):
    program = decode_program(MULTI_FIXTURE.read_bytes())
    verify_program(program, arch)
    layers = program.moe_layer_specs
    assert [layer.layer_id for layer in layers] == [1, 2]
    assert layers[0].expert_count == 2
    assert layers[1].expert_count == 4
    assert layers[0].top_k == layers[1].top_k == 2
    assert len(program.moe_expert_specs) == 6
    assert len(program.moe_kernel_specs) == 2
    assert len(program.moe_dynamic_regions) == 2
    sites = {(region.insert_after_command_id,
              region.resume_before_command_id, region.entry_event_id)
             for region in program.moe_dynamic_regions}
    assert len(sites) == 2
    scratch = [(region.scratch_offset, region.scratch_bytes)
               for region in program.moe_dynamic_regions]
    assert scratch[0][0] + scratch[0][1] <= scratch[1][0]
    assert encode_program(program) == MULTI_FIXTURE.read_bytes()


DUAL_FIXTURE = FIXTURE / "moe_dual.mshb"


def test_gate5_dual_core_fixture_pairs_a_region_per_core(arch):
    program = decode_program(DUAL_FIXTURE.read_bytes())
    verify_program(program, arch)
    layer = program.moe_layer_specs[0]
    regions = program.moe_dynamic_regions
    assert layer.dynamic_region_count == len(regions) == 2
    assert [region.region_id for region in regions] == [1, 2]
    assert [region.core_id for region in regions] == [0, 1]
    assert [region.layer_id for region in regions] == [1, 1]
    assert [expert.core_id for expert in program.moe_expert_specs] == [0, 1]
    assert len({(region.insert_after_command_id,
                 region.resume_before_command_id, region.entry_event_id)
                for region in regions}) == 2
    assert len({region.stream_id for region in regions}) == 1
    streams = {(stream.core_id, stream.stream_id) for stream in program.streams}
    assert streams == {(0, 0), (1, 0)}
    assert encode_program(program) == DUAL_FIXTURE.read_bytes()


def test_gate5_dual_core_region_gate_needs_a_cross_core_prerequisite(arch):
    program = decode_program(DUAL_FIXTURE.read_bytes())
    prerequisites = command_prerequisites(program)
    peer_region = program.moe_dynamic_regions[1]
    core0_chain = {c.command_id for c in program.commands if c.core_id == 0}
    assert core0_chain & prerequisite_closure(
        prerequisites, peer_region.insert_after_command_id)
    assert peer_region.resume_before_command_id in prerequisite_closure(
        prerequisites, next(c.command_id for c in program.commands
                            if c.opcode == A.OPCODE.REQUEST_END))
    assert peer_region.resume_before_command_id in prerequisite_closure(
        prerequisites, next(c.command_id for c in program.commands
                            if c.core_id == 1 and c.opcode == A.OPCODE.HALT))


def test_gate5_dual_core_region_gate_rejects_an_independent_core(arch):
    program = decode_program(DUAL_FIXTURE.read_bytes())
    commands = [replace(c, wait_count=0) if c.core_id == 1 else c
                for c in program.commands]
    broken = replace(program, commands=commands)
    with pytest.raises(MeshIrError) as err:
        verify_program(broken, arch)
    assert err.value.code == "E_LIFECYCLE"
    assert "request begin must precede the region gate" in err.value.message


def test_gate5_weight_registry_covers_both_fixture_programs(arch):
    image = load_model_weight_image(IMAGE_JSON)
    for path in (FIXTURE / "moe_min.mshb", MULTI_FIXTURE):
        program = decode_program(path.read_bytes())
        registry = load_program_weight_registry(REGISTRY_JSON, image, program)
        for expert in program.moe_expert_specs:
            digest = region_digest(registry, program, expert.weight_symbol_id,
                                   expert.weight_region_offset,
                                   expert.weight_bytes)
            assert len(digest) == 32

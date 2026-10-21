import dataclasses
import hashlib
import json
import math
import zlib
from pathlib import Path

import pytest

import mesh_ir.abi as abi
from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.semantic import (
    SemanticDecoder,
    encode_semantics,
    semantic_from_canonical,
    semantic_payloads,
    semantic_to_canonical,
)
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import canonical_json_bytes, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_dual_core_program, build_single_core_program
from mesh_ir.ir.common import Add, Const
from mesh_ir.ir.graph_ir import ElementwiseAttrs, ViewAttrs
from mesh_ir.model import ContentDigest, ProfileHint, SourceMap, StringEntry


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def programs():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    return build_single_core_program(arch), build_dual_core_program(arch)


def test_complete_binary_and_json_codecs_round_trip_typed_programs(programs):
    for program in programs:
        image = encode_program(program)
        decoded = decode_program(image)
        assert decoded == program
        assert encode_program(decoded) == image
        assert abi.load_program_json(program.canonical_bytes()) == program


def test_scheduled_canonical_projection_omits_none_and_discriminates_unions(programs):
    document = json.loads(programs[1].canonical_bytes())
    pending = [document]
    while pending:
        value = pending.pop()
        assert value is not None
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    endpoint_use = document["semantics"]["endpoint_uses"][0]["use"]
    assert endpoint_use["$type"] in {"ReadAccessUse", "WriteAccessUse"}
    assert document["semantics"]["intrinsic_traffic"] == programs[1].semantics.intrinsic_traffic.canonical_dict()


def test_optional_program_sections_are_typed_retained_and_projected(programs):
    base = programs[0]
    source_map = (SourceMap(1, "model.py", 7, 3),)
    hints = (ProfileHint(base.entrypoints[0].entrypoint_id, base.profiles[0].profile_id, "mode", "fast"),)
    digests = (ContentDigest(A.CONTENT_DIGEST_OBJECT_KIND["TENSOR"], 0, base.tensors[0].tensor_id, bytes.fromhex("11" * 32)),)
    changed = dataclasses.replace(base, source_map=source_map, profile_hints=hints, content_digests=digests)
    changed = dataclasses.replace(changed, semantic_sha256=semantic_sha256(changed.semantic_dict()))
    decoded = decode_program(encode_program(changed))
    assert decoded == changed
    document = json.loads(changed.canonical_bytes())
    assert document["sections"]["SOURCE_MAP"] == [{"column": 3, "file": "model.py", "line": 7, "loc_id": 1}]
    assert document["sections"]["PROFILE_HINTS"] == [{"entrypoint_id": 1, "name": "mode", "profile_id": 1, "value": "fast"}]
    assert document["sections"]["CONTENT_DIGESTS"][0]["digest"] == "11" * 32


def test_semantic_support_spans_are_reserved_in_depth_first_field_order(programs):
    root, image = encode_semantics(programs[1].semantics)
    assert root[1] == 1
    payloads = {
        getattr(A.SECTION_TYPE, name): ({"count": count}, payload)
        for name, (count, payload) in semantic_payloads(image).items()
    }
    assert SemanticDecoder(payloads).decode(A.SEMANTIC_REF_FORMAT.pack(root[0], 0, root[1])) == programs[1].semantics
    root_spec = A.SEMANTIC_RECORDS["mesh_ir.scheduled.model.ProgramSemantics"]
    root_row = bytearray(image.records[root[0]][0])
    first = next(
        field
        for field in root_spec["fields"]
        if field["kind"] == "ref_list"
        and A.LIST_SPAN_FORMAT.unpack_from(root_row, field["offset"])[1]
    )
    begin, count = A.LIST_SPAN_FORMAT.unpack_from(root_row, first["offset"])
    A.LIST_SPAN_FORMAT.pack_into(root_row, first["offset"], begin + count, count)
    corrupted = dict(payloads)
    records = list(image.records[root[0]])
    records[0] = root_row
    corrupted[root[0]] = ({"count": len(records)}, b"".join(records))
    with pytest.raises(MeshIrError) as caught:
        SemanticDecoder(corrupted).decode(A.SEMANTIC_REF_FORMAT.pack(root[0], 0, root[1]))
    assert caught.value.code == "E_ABI_ORDER"


def test_deep_semantic_json_reconstruction_and_canonical_emission_are_iterative():
    value = Const(1)
    for _ in range(1500):
        value = Add(value, Const(1))
    document = semantic_to_canonical(value)
    assert canonical_json_bytes(document).startswith(b'{"lhs":')
    decoded = semantic_from_canonical(document, "mesh_ir.ir.common.Add")
    depth = 0
    while isinstance(decoded, Add):
        decoded = decoded.lhs
        depth += 1
    assert depth == 1500
    assert decoded == Const(1)


def test_semantic_json_preserves_full_integer_union_and_scalar_domains():
    view = semantic_from_canonical(
        {
            "shape": [],
            "permutation": [],
            "axes": [],
            "starts": [-(1 << 63)],
            "ends": ["0xffffffffffffffff"],
            "steps": [],
            "expanded_axes": [],
        },
        "mesh_ir.ir.graph_ir.ViewAttrs",
    )
    assert view == ViewAttrs(starts=(-(1 << 63),), ends=((1 << 64) - 1,))
    for scalar in (True, 1.25, -0.0):
        value = ElementwiseAttrs(scalar=scalar)
        decoded = semantic_from_canonical(
            semantic_to_canonical(value),
            "mesh_ir.ir.graph_ir.ElementwiseAttrs",
        )
        assert type(decoded.scalar) is type(scalar)
        assert decoded.scalar == scalar
        if scalar == 0.0:
            assert math.copysign(1.0, decoded.scalar) == -1.0


def test_required_features_imply_the_minimum_reader_version(programs):
    program = programs[0]
    candidate = dataclasses.replace(program, min_reader_minor=2)
    candidate = dataclasses.replace(
        candidate,
        semantic_sha256=semantic_sha256(candidate.semantic_dict()),
    )
    with pytest.raises(MeshIrError) as json_error:
        abi.load_program_json(candidate.canonical_bytes())
    assert json_error.value.code == "E_ABI_VERSION"

    image = bytearray(encode_program(program))
    header = dict(zip((field["name"] for field in A.HEADER_FIELDS), A.HEADER_FORMAT.unpack_from(image)))
    directory_fields = tuple(field["name"] for field in A.SECTION_DIR_FIELDS)
    for index in range(header["section_count"]):
        entry_offset = A.HEADER_BYTES + index * A.SECTION_DIR_BYTES
        entry = dict(zip(directory_fields, A.SECTION_DIR_FORMAT.unpack_from(image, entry_offset)))
        if entry["section_type"] == A.SECTION_TYPE.PROGRAM_METADATA:
            metadata = entry["offset"]
            A.PROGRAM_METADATA_FORMAT.pack_into(
                image,
                metadata,
                2,
                0,
                0,
                image[metadata + A.PROGRAM_METADATA_FIELD_OFFSETS["semantics"]:metadata + A.PROGRAM_METADATA_FIELD_OFFSETS["semantic_sha256"]],
                bytes.fromhex(candidate.semantic_sha256),
            )
            crc_offset = entry_offset + next(
                field["offset"] for field in A.SECTION_DIR_FIELDS if field["name"] == "crc32"
            )
            image[crc_offset:crc_offset + 4] = zlib.crc32(
                image[metadata:metadata + entry["size"]]
            ).to_bytes(4, "little")
            break
    payload_sha_offset = next(
        field["offset"] for field in A.HEADER_FIELDS if field["name"] == "payload_sha256"
    )
    image[payload_sha_offset:payload_sha_offset + 32] = hashlib.sha256(
        image[A.HEADER_BYTES:]
    ).digest()
    with pytest.raises(MeshIrError) as binary_error:
        decode_program(bytes(image))
    assert binary_error.value.code == "E_ABI_VERSION"


def test_encoder_admits_transport_representation_before_domain_checks(programs):
    program = programs[0]
    descriptor = program.dma_descriptors[0]
    invalid = (
        (dataclasses.replace(program, op_attrs=(dataclasses.replace(program.op_attrs[0], reserved=1), *program.op_attrs[1:])), "E_ABI_RESERVED"),
        (dataclasses.replace(program, dma_descriptors=(dataclasses.replace(descriptor, src=dataclasses.replace(descriptor.src, reserved=1)), *program.dma_descriptors[1:])), "E_ABI_RESERVED"),
        (dataclasses.replace(program, commands=(dataclasses.replace(program.commands[0], command_id=True), *program.commands[1:])), "E_ABI_BOUNDS"),
        (dataclasses.replace(program, tensors=(dataclasses.replace(program.tensors[0], content_sha256=bytes(31)), *program.tensors[1:])), "E_ABI_BOUNDS"),
        (dataclasses.replace(program, profiles=(dataclasses.replace(program.profiles[0], dims=list(program.profiles[0].dims)), *program.profiles[1:])), "E_ABI_BOUNDS"),
        (dataclasses.replace(program, op_attrs=(dataclasses.replace(program.op_attrs[0], payload=list(program.op_attrs[0].payload)), *program.op_attrs[1:])), "E_ABI_BOUNDS"),
        (dataclasses.replace(program, strings=(StringEntry(42), *program.strings[1:])), "E_ABI_BOUNDS"),
        (dataclasses.replace(program, commands=None), "E_ABI_BOUNDS"),
        (dataclasses.replace(program, source_map=(SourceMap(1, "one.py", 1, 0), SourceMap("bad", "two.py", 1, 0))), "E_ABI_BOUNDS"),
    )
    for candidate, code in invalid:
        with pytest.raises(MeshIrError) as error:
            encode_program(candidate)
        assert error.value.code == code


@pytest.mark.parametrize("objects", (None, (object(),)))
def test_encoder_admits_semantic_tree_before_optional_references(programs, objects):
    program = programs[0]
    changed = dataclasses.replace(
        program,
        semantics=dataclasses.replace(program.semantics, objects=objects),
    )
    with pytest.raises(MeshIrError) as error:
        encode_program(changed)
    assert error.value.code == "E_ABI_BOUNDS"


def test_empty_semantic_support_span_checks_canonical_shape_before_bounds(programs):
    root, image = encode_semantics(programs[0].semantics)
    found = False
    for section_type, rows in image.records.items():
        spec = A.SEMANTIC_RECORDS[A.SEMANTIC_RECORD_BY_SECTION[section_type]]
        for row in rows:
            field = next(
                (
                    item
                    for item in spec["fields"]
                    if item["kind"] in {"ref_list", "u64_list", "i64_list", "integer_list", "bytes"}
                    and A.LIST_SPAN_FORMAT.unpack_from(row, item["offset"])[1] == 0
                ),
                None,
            )
            if field is not None:
                A.LIST_SPAN_FORMAT.pack_into(row, field["offset"], (1 << 32) - 1, 0)
                found = True
                break
        if found:
            break
    if not found:
        pytest.fail("complete semantics has no empty support span")
    payloads = {
        getattr(A.SECTION_TYPE, name): ({"count": count}, payload)
        for name, (count, payload) in semantic_payloads(image).items()
    }
    with pytest.raises(MeshIrError) as error:
        SemanticDecoder(payloads).decode(A.SEMANTIC_REF_FORMAT.pack(root[0], 0, root[1]))
    assert error.value.code == "E_ABI_ORDER"

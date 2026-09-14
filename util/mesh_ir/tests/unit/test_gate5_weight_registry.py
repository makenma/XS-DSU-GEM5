import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.model import MeshIrError
from mesh_ir.weight_registry import (
    TAG_TUPLE_BYTES,
    encode_tag_tuple,
    load_model_weight_image,
    load_program_weight_registry,
    materialize_content,
    region_digest,
    tag_manifest_bytes,
    weight_region_order,
    weight_tag_index_map,
    weight_tag_manifest,
)

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures"
GOLDEN = REPO / "tests/gem5/ai_mesh/golden"
MOE_IMAGE = FIXTURES / "gate5/moe_min.mshb"
IMAGE_JSON = FIXTURES / "model_weight_image_v1.json"
REGISTRY_JSON = FIXTURES / "program_weight_bindings_v1.json"
CYCLE_GOLDEN = GOLDEN / "program_weight_registry_image_cycle_golden.json"
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"


@pytest.fixture(scope="module")
def artifacts():
    program = decode_program(MOE_IMAGE.read_bytes())
    image = load_model_weight_image(IMAGE_JSON)
    registry = load_program_weight_registry(REGISTRY_JSON, image, program)
    golden = json.loads(CYCLE_GOLDEN.read_text())
    return program, image, registry, golden


def test_gate5_weight_artifacts_validate_against_their_schemas():
    image_document = json.loads(IMAGE_JSON.read_text())
    del image_document["model_weight_image_digest"]
    tmp = IMAGE_JSON.parent / "model_weight_image_missing_field.json"
    tmp.write_text(json.dumps(image_document), encoding="utf-8")
    try:
        with pytest.raises(MeshIrError) as err:
            load_model_weight_image(tmp)
    finally:
        tmp.unlink()
    assert err.value.code == "E_RELOCATION"
    assert load_model_weight_image(IMAGE_JSON).digest


def test_gate5_weight_artifacts_are_byte_stable(artifacts):
    program, image, registry, _ = artifacts
    assert image.document["model_weight_image_digest"] == image.digest
    assert registry.document["program_weight_registry_digest"] == \
        registry.digest
    assert registry.document["model_weight_image_digest"] == image.digest
    home = image.home(1)
    assert home.content == materialize_content(home.seed_utf8, home.bytes)
    assert home.arena_offset + home.bytes <= image.arena_bytes
    assert registry.document["programs"][0]["program_semantic_digest"] == \
        program.semantic_sha256()


def test_gate5_projection_dependency_graph_is_acyclic(artifacts):
    _, image, registry, golden = artifacts
    manifest = weight_tag_manifest(artifacts[0], registry)
    digests = {
        "program_semantic_digest": artifacts[0].semantic_sha256(),
        "model_weight_image_digest": image.digest,
        "program_weight_registry_digest": registry.digest,
        "weight_tag_manifest_digest": __import__("hashlib").sha256(
            tag_manifest_bytes(manifest)).hexdigest(),
    }
    assert golden["projection_order"] == ["item",
                                          "program_semantic_digest",
                                          "model_weight_image_digest",
                                          "program_weight_registry_digest",
                                          "weight_tag_manifest_digest"]
    for stage, inputs in golden["cycle_breaks"].items():
        assert stage in digests
        for source in inputs:
            assert source in digests
            assert golden[source] == digests[source]
    for key, value in digests.items():
        assert golden[key] == value, key
    assert golden["weight_tag_count"] == len(manifest)
    assert golden["weight_tag_tuple_bytes"] == TAG_TUPLE_BYTES


def test_gate5_weight_tags_are_dense_sorted_and_deduplicated(artifacts):
    program, _, registry, golden = artifacts
    manifest = weight_tag_manifest(program, registry)
    assert len(manifest) == len(program.moe_expert_specs)
    tuples = [tuple_bytes for _, tuple_bytes in manifest]
    assert tuples == sorted(tuples)
    assert len(set(tuples)) == len(tuples)
    assert [index for index, _ in manifest] == list(range(len(manifest)))
    for index, tuple_bytes in manifest:
        assert len(tuple_bytes) == TAG_TUPLE_BYTES
        entry = golden["weight_tags"][index]
        assert entry["tuple_hex"] == tuple_bytes.hex()
        assert entry["weight_tag_index"] == index
    for expert in program.moe_expert_specs:
        digest = region_digest(registry, program, expert.weight_symbol_id,
                               expert.weight_region_offset,
                               expert.weight_bytes)
        expected = encode_tag_tuple(
            bytes.fromhex(program.semantic_sha256()),
            expert.weight_symbol_id, expert.weight_region_offset,
            expert.weight_bytes, digest)
        assert expected in tuples


def test_gate5_tag_order_is_the_runtime_expert_projection(artifacts):
    program, _, registry, _ = artifacts
    order = weight_region_order(program)
    manifest = weight_tag_manifest(program, registry)
    assert len(order) == len(manifest)
    semantic = bytes.fromhex(program.semantic_sha256())
    for index, (symbol_id, offset, byte_count) in enumerate(order):
        assert manifest[index][0] == index
        digest = region_digest(registry, program, symbol_id, offset,
                               byte_count)
        assert manifest[index][1] == encode_tag_tuple(
            semantic, symbol_id, offset, byte_count, digest)
    mapping = weight_tag_index_map(program)
    assert set(mapping) == {(expert.layer_id, expert.expert_id)
                            for expert in program.moe_expert_specs}
    for expert in program.moe_expert_specs:
        region = (expert.weight_symbol_id, expert.weight_region_offset,
                  expert.weight_bytes)
        key = (expert.layer_id, expert.expert_id)
        assert mapping[key] == order.index(region)
        assert encode_tag_tuple(
            semantic, *region, region_digest(registry, program, *region)) == \
            manifest[mapping[key]][1]


def test_gate5_tag_order_deduplicates_aliased_regions(artifacts):
    program, _, registry, _ = artifacts
    first, second = program.moe_expert_specs[0], program.moe_expert_specs[1]
    alias = replace(second,
                    weight_symbol_id=first.weight_symbol_id,
                    weight_region_offset=first.weight_region_offset,
                    weight_bytes=first.weight_bytes)
    aliased = replace(program,
                      moe_expert_specs=[first, alias] +
                      list(program.moe_expert_specs[2:]))
    order = weight_region_order(aliased)
    assert len(order) == len({(expert.weight_symbol_id,
                               expert.weight_region_offset,
                               expert.weight_bytes)
                              for expert in aliased.moe_expert_specs})
    assert len(order) < len(aliased.moe_expert_specs)
    assert order == tuple(sorted(order))
    mapping = weight_tag_index_map(aliased)
    assert mapping[(first.layer_id, first.expert_id)] == \
        mapping[(alias.layer_id, alias.expert_id)]
    assert len(set(mapping.values())) == len(order)


def test_gate5_registry_declares_every_moe_weight_region(artifacts):
    program, _, registry, _ = artifacts
    semantic = bytes.fromhex(program.semantic_sha256())
    for expert in program.moe_expert_specs:
        binding = registry.symbol(semantic, expert.weight_symbol_id)
        assert binding.bytes >= expert.weight_region_offset + \
            expert.weight_bytes
        matched = [region for region in binding.regions
                   if region.region_offset == expert.weight_region_offset and
                   region.bytes == expert.weight_bytes]
        assert len(matched) == 1
        digest = matched[0].resolved_content_digest
        assert digest != bytes(32)
        assert digest == region_digest(registry, program,
                                       expert.weight_symbol_id,
                                       expert.weight_region_offset,
                                       expert.weight_bytes)


def test_gate5_registry_rejects_unbound_weight_region(artifacts):
    program, image, registry, _ = artifacts
    document = json.loads(REGISTRY_JSON.read_text())
    document["programs"][0]["symbols"][0]["regions"] = [
        dict(document["programs"][0]["symbols"][0]["regions"][0])]
    from mesh_ir.model import canonical_json_bytes
    import hashlib
    projected = {key: value for key, value in document.items()
                 if key != "program_weight_registry_digest"}
    document["program_weight_registry_digest"] = hashlib.sha256(
        canonical_json_bytes(projected)).hexdigest()
    tmp = REGISTRY_JSON.parent / "program_weight_bindings_unbound.json"
    tmp.write_text(json.dumps(document), encoding="utf-8")
    try:
        loaded = load_program_weight_registry(tmp, image, program)
    finally:
        tmp.unlink()
    with pytest.raises(MeshIrError) as err:
        region_digest(loaded, program, 4, 4096, 4096)
    assert err.value.code == "E_RELOCATION"


def test_gate5_registry_rejects_digest_tampering(artifacts):
    _, image, _, _ = artifacts
    document = json.loads(REGISTRY_JSON.read_text())
    document["programs"][0]["symbols"][0]["regions"][0][
        "resolved_content_digest"] = "0" * 64
    tmp = REGISTRY_JSON.parent / "program_weight_bindings_tampered.json"
    tmp.write_text(json.dumps(document), encoding="utf-8")
    try:
        with pytest.raises(MeshIrError) as err:
            load_program_weight_registry(tmp, image, decode_program(
                MOE_IMAGE.read_bytes()))
    finally:
        tmp.unlink()
    assert err.value.code == "E_RELOCATION"


def test_gate5_image_rejects_content_digest_mismatch():
    document = json.loads(IMAGE_JSON.read_text())
    document["homes"][0]["content_sha256"] = "0" * 64
    tmp = IMAGE_JSON.parent / "model_weight_image_tampered.json"
    tmp.write_text(json.dumps(document), encoding="utf-8")
    try:
        with pytest.raises(MeshIrError) as err:
            load_model_weight_image(tmp)
    finally:
        tmp.unlink()
    assert err.value.code == "E_RELOCATION"


def test_gate5_fixture_still_passes_the_program_verifier(artifacts):
    program, _, _, _ = artifacts
    verify_program(program, load_arch(ARCH_PATH))


def test_gate5_tag_tuple_layout_is_frozen():
    tuple_bytes = encode_tag_tuple(bytes(range(32)), 0x01020304,
                                   0x1112131415161718,
                                   0x2122232425262728,
                                   bytes(range(32, 64)))
    assert len(tuple_bytes) == TAG_TUPLE_BYTES
    assert tuple_bytes[:32] == bytes(range(32))
    assert tuple_bytes[32:36] == bytes([4, 3, 2, 1])
    assert tuple_bytes[36:44] == bytes(range(0x18, 0x10, -1))
    assert tuple_bytes[44:52] == bytes(range(0x28, 0x20, -1))
    assert tuple_bytes[52:] == bytes(range(32, 64))


def test_gate5_zero_feature_program_has_no_weight_tags(artifacts):
    from mesh_ir.golden_programs import build_single_core_program
    _, _, registry, _ = artifacts
    program = build_single_core_program(load_arch(ARCH_PATH))
    assert weight_tag_manifest(program, registry) == ()

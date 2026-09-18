import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import abi as A
from mesh_ir.kv_layout import (
    KV_LAYOUT_DOMAIN,
    kv_layout_digest,
    kv_layout_projection,
    serving_contract_digest,
)
from mesh_ir.model import MeshIrError, canonical_json_bytes

FIXTURE = Path(__file__).resolve().parents[4] / \
    "tests/gem5/ai_mesh/fixtures/gate6"
WEIGHT_IMAGE = Path(__file__).resolve().parents[4] / \
    "tests/gem5/ai_mesh/fixtures/model_weight_image_v1.json"

GOLDEN = {
    "serving_min":
        "78a2574e02b4bf19635acfbbba932d1c1ac712312863a2f86ba7f4cee68d89a1",
    "serving_two_tokens":
        "260d84795086ee48029a9d2b3bed4066f0e0e4d5887fb7e8b01d409bc87dae90",
    "serving_fullview":
        "2715bce028e710e98ce8c093c0119976751daf1f61b7fc2f52d605fa385a035d",
}
CONTRACT = \
    "f3ba400f75b24a69d3236921aacc37743b3b0f1f9cc56b37290af4a3db2778e6"


def fixture(name):
    return decode_program((FIXTURE / f"{name}.mshb").read_bytes())


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_layout_digest_is_pinned_by_the_cross_language_golden(name):
    program = fixture(name)
    projection = kv_layout_projection(program)
    assert projection["schema"] == "mesh_kv_layout_v1"
    assert projection["token_bytes"] == 16
    assert projection["bindings"]
    assert projection["descriptors"]
    expected = hashlib.sha256(
        KV_LAYOUT_DOMAIN + canonical_json_bytes(projection)).hexdigest()
    assert expected == GOLDEN[name]
    assert kv_layout_digest(program).hex() == GOLDEN[name]


def test_projection_carries_role_profile_relocation_and_tensor():
    program = fixture("serving_min")
    projection = kv_layout_projection(program)
    roles = [binding["role"] for binding in projection["bindings"]]
    assert roles == ["REQUEST", "INSTANCE", "INSTANCE", "MEMBER", "MEMBER"]
    for binding in projection["bindings"]:
        assert set(binding) == {"role", "profile", "relocation", "tensor"}
        assert binding["tensor"]["role"] == A.TENSOR_ROLE.KV_CACHE
        assert binding["tensor"]["dtype"] == A.DTYPE.INT8
    descriptor_ids = [d["descriptor_id"] for d in projection["descriptors"]]
    assert descriptor_ids == sorted(descriptor_ids)
    for descriptor in projection["descriptors"]:
        assert descriptor["kind"] in (A.DMA_KIND.LOAD, A.DMA_KIND.STORE)


def test_layout_digest_changes_with_the_token_stride():
    program = fixture("serving_min")
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=[
            dataclasses.replace(record, kv_bytes_per_token=32)
            for record in program.agent_request_profiles])
    assert kv_layout_digest(mutated) != kv_layout_digest(program)


def test_layout_digest_changes_with_the_descriptor_layout():
    program = fixture("serving_min")
    descriptors = list(program.dma_descriptors)
    for index, descriptor in enumerate(descriptors):
        if descriptor.kind == A.DMA_KIND.STORE:
            descriptors[index] = dataclasses.replace(
                descriptor, row_bytes=descriptor.row_bytes // 2,
                src_stride_bytes=descriptor.src_stride_bytes // 2,
                dst_stride_bytes=descriptor.dst_stride_bytes // 2)
            break
    assert kv_layout_digest(
        dataclasses.replace(program, dma_descriptors=descriptors)) != \
        kv_layout_digest(program)


def test_missing_kv_binding_is_rejected():
    program = fixture("serving_min")
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=[
            dataclasses.replace(record, primary_kv_symbol_id=0)
            for record in program.agent_request_profiles])
    with pytest.raises(MeshIrError) as err:
        kv_layout_digest(mutated)
    assert err.value.code == "E_KV_CONTRACT_MISMATCH"


def test_symbol_without_a_relocation_is_rejected():
    program = fixture("serving_min")
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=[
            dataclasses.replace(record, primary_kv_symbol_id=0xFFFF)
            for record in program.agent_request_profiles])
    with pytest.raises(MeshIrError) as err:
        kv_layout_digest(mutated)
    assert err.value.code == "E_KV_CONTRACT_MISMATCH"


def test_serving_contract_digest_is_pinned():
    program = fixture("serving_two_tokens")
    weights = json.loads(WEIGHT_IMAGE.read_text())
    digest = serving_contract_digest(
        program, bytes.fromhex(weights["model_weight_image_digest"]))
    assert digest.hex() == CONTRACT
    assert len(digest) == 32

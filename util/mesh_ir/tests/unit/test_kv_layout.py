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

GOLDEN = {name: json.loads((FIXTURE / f"{name}_expected.json").read_text())
          for name in ("serving_min", "serving_two_tokens", "serving_fullview")}


def fixture(name):
    return decode_program((FIXTURE / f"{name}.mshb").read_bytes())


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_layout_digest_is_pinned_by_the_cross_language_golden(name):
    program = fixture(name)
    projection = kv_layout_projection(program)
    assert projection["schema"] == "mesh_kv_layout_v2"
    assert projection["token_bytes"] == 16
    assert projection["bindings"]
    assert projection["descriptors"]
    expected = hashlib.sha256(
        KV_LAYOUT_DOMAIN + canonical_json_bytes(projection)).hexdigest()
    assert expected == GOLDEN[name]["kv_layout_digest"]
    assert kv_layout_digest(program).hex() == GOLDEN[name]["kv_layout_digest"]


def test_projection_carries_exact_instance_member_variant_binding():
    program = fixture("serving_min")
    projection = kv_layout_projection(program)
    members = {(row.instance_profile_id, row.member_ordinal): row
               for row in program.agent_instance_member_bindings if row.static_kv_symbol_id}
    actual = set()
    for binding in projection["bindings"]:
        assert set(binding) == {"request", "instance", "member", "relocation", "tensor"}
        member = binding["member"]
        key = (member["instance_profile_id"], member["member_ordinal"])
        assert key not in actual
        actual.add(key)
        assert member["static_kv_symbol_id"] == members[key].static_kv_symbol_id
        assert binding["instance"]["instance_profile_id"] == key[0]
        assert binding["request"]["profile_id"] == binding["instance"]["request_profile_id"]
        assert binding["relocation"]["symbol_sid"] == member["static_kv_symbol_id"]
        assert binding["relocation"]["tensor_id"] == binding["tensor"]["tensor_id"]
        assert binding["tensor"]["role"] == A.TENSOR_ROLE.KV_CACHE
        assert binding["tensor"]["dtype"] == A.DTYPE.INT8
    assert actual == set(members)
    descriptor_ids = [row["descriptor_id"] for row in projection["descriptors"]]
    assert descriptor_ids == sorted(descriptor_ids)
    assert all(row["kind"] in (A.DMA_KIND.LOAD, A.DMA_KIND.STORE)
               for row in projection["descriptors"])


def test_layout_digest_changes_with_the_token_stride():
    program = fixture("serving_min")
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=tuple(
            dataclasses.replace(record, kv_bytes_per_token=32)
            for record in program.agent_request_profiles))
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
        dataclasses.replace(program, dma_descriptors=tuple(descriptors))) != \
        kv_layout_digest(program)


def test_missing_kv_binding_is_rejected():
    program = fixture("serving_min")
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=tuple(
            dataclasses.replace(record, primary_kv_symbol_id=0)
            for record in program.agent_request_profiles))
    with pytest.raises(MeshIrError) as err:
        kv_layout_digest(mutated)
    assert err.value.code == "E_KV_CONTRACT_MISMATCH"


def test_symbol_without_a_relocation_is_rejected():
    program = fixture("serving_min")
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=tuple(
            dataclasses.replace(record, primary_kv_symbol_id=0xFFFF)
            for record in program.agent_request_profiles))
    with pytest.raises(MeshIrError) as err:
        kv_layout_digest(mutated)
    assert err.value.code == "E_KV_CONTRACT_MISMATCH"


def test_serving_contract_digest_is_pinned():
    program = fixture("serving_two_tokens")
    weights = json.loads(WEIGHT_IMAGE.read_text())
    digest = serving_contract_digest(
        program, bytes.fromhex(weights["model_weight_image_digest"]))
    assert digest.hex() == GOLDEN["serving_two_tokens"]["serving_contract_digest"]
    assert len(digest) == 32

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.generated import agent_abi as G
from mesh_ir.model import MeshIrError
from mesh_ir.moe_rng import (
    KEY_BYTES,
    RngKey,
    categorical,
    checked_round_q16,
    draw,
    scenario_provider_digest,
    seed64_of,
    splitmix64_once,
    threshold,
    uniform_profile_digest,
    without_replacement,
)
from mesh_ir.moe_uid import SemanticTokenUid, uid_less

REPO = Path(__file__).resolve().parents[4]
RNG_GOLDEN = REPO / "tests/gem5/ai_mesh/golden/rng_golden.json"
PLAN_DIGEST = bytes.fromhex("0123456789abcdef" * 4)


@pytest.fixture(scope="module")
def golden():
    return json.loads(RNG_GOLDEN.read_text())


def key_from(fields) -> RngKey:
    return RngKey(
        master_seed=fields["master_seed"],
        workload_plan_digest=bytes.fromhex(fields["workload_plan_digest_hex"]),
        user_id=fields["user_id"], task_seq=fields["task_seq"],
        repair_round=fields["repair_round"], phase=fields["phase"],
        sequence_ordinal=fields["sequence_ordinal"],
        token_ordinal=fields["token_ordinal"], layer_id=fields["layer_id"],
        logical_source_rank=fields["logical_source_rank"],
        topk_slot=fields["topk_slot"], draw_id=fields["draw_id"])


def test_gate5_uid_wire_is_frozen_little_endian():
    uid = SemanticTokenUid(workload_plan_item_id=0x05060708,
                           user_id=0x11121314, task_seq=0x21222324,
                           repair_round=0x3132, phase=G.SEMANTIC_PHASE.PREFILL,
                           sequence_ordinal=0, token_ordinal=0x41424344)
    wire = uid.encode()
    assert len(wire) == 32
    assert wire[0:8] == bytes([8, 7, 6, 5, 0, 0, 0, 0])
    assert wire[8:12] == bytes([0x14, 0x13, 0x12, 0x11])
    assert wire[12:16] == bytes([0x24, 0x23, 0x22, 0x21])
    assert wire[16:18] == bytes([0x32, 0x31])
    assert wire[18] == G.SEMANTIC_PHASE.PREFILL
    assert wire[19] == 0
    assert wire[20:24] == bytes(4)
    assert wire[24:28] == bytes([0x44, 0x43, 0x42, 0x41])
    assert wire[28:32] == bytes(4)
    assert SemanticTokenUid.decode(wire) == uid


def test_gate5_uid_comparator_is_numeric_not_memcmp():
    large_ordinal = SemanticTokenUid(
        workload_plan_item_id=1, user_id=0, task_seq=0, repair_round=0,
        phase=G.SEMANTIC_PHASE.PREFILL, sequence_ordinal=0, token_ordinal=256)
    small_ordinal = SemanticTokenUid(
        workload_plan_item_id=1, user_id=0, task_seq=0, repair_round=0,
        phase=G.SEMANTIC_PHASE.PREFILL, sequence_ordinal=0, token_ordinal=2)
    assert large_ordinal.encode() < small_ordinal.encode()
    assert uid_less(small_ordinal, large_ordinal)
    ordered = sorted([large_ordinal, small_ordinal],
                     key=lambda uid: uid.sort_key())
    assert ordered == [small_ordinal, large_ordinal]
    slot_order = sorted([
        SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.PREFILL, 0, 0),
        SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.PREFILL, 0, 1),
        SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.DECODE, 1, 0),
    ], key=lambda uid: uid.sort_key())
    assert [uid.token_ordinal for uid in slot_order] == [0, 1, 0]
    assert slot_order[-1].phase == G.SEMANTIC_PHASE.DECODE


def test_gate5_uid_rejects_reserved_and_ordinal_violations():
    with pytest.raises(MeshIrError) as reserved:
        SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.PREFILL, 0, 1,
                         reserved1=1).encode()
    assert reserved.value.code == "E_MOE_UID"
    with pytest.raises(MeshIrError):
        SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.PREFILL, 3,
                         1).encode()
    with pytest.raises(MeshIrError):
        SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.DECODE, 1, 1).encode()
    with pytest.raises(MeshIrError):
        SemanticTokenUid(1 << 32, 0, 0, 0, G.SEMANTIC_PHASE.PREFILL, 0,
                         1).encode()
    with pytest.raises(MeshIrError):
        SemanticTokenUid(1, 0, 0, 0, 9, 0, 1).encode()


def test_gate5_uid_semantic_and_route_ordinals_follow_phase():
    prefill = SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.PREFILL, 0, 7)
    decode = SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.DECODE, 5, 0)
    assert prefill.semantic_ordinal() == 7
    assert prefill.route_linear_ordinal() == 7
    assert decode.semantic_ordinal() == 5
    assert decode.route_linear_ordinal() == 5
    publish = SemanticTokenUid(1, 0, 0, 0, G.SEMANTIC_PHASE.PUBLISH, 0, 0)
    with pytest.raises(MeshIrError):
        publish.route_linear_ordinal()


def test_gate5_rng_key_and_draws_match_the_checked_in_golden(golden):
    assert golden["key_bytes"] == KEY_BYTES
    assert golden["vectors"]
    for vector in golden["vectors"]:
        key = key_from(vector["key_fields"])
        assert key.encode().hex() == vector["key_hex"], vector["name"]
        assert seed64_of(key) == vector["seed64"], vector["name"]
        assert draw(key) == vector["splitmix64"], vector["name"]
        assert splitmix64_once(seed64_of(key)) == vector["splitmix64"]
        assert threshold(vector["total"], vector["splitmix64"]) == \
            vector["threshold"], vector["name"]
        assert categorical(vector["weights"], key) == \
            vector["selected_index"], vector["name"]


def test_gate5_uniform_without_replacement_matches_the_golden(golden):
    for vector in golden["uniform_vectors"]:
        base = key_from(vector["key_fields"])
        assert base.encode().hex() == vector["key_hex"], vector["name"]
        selected = without_replacement(
            vector["expert_count"], vector["top_k"],
            lambda slot, base=base: RngKey(
                master_seed=base.master_seed,
                workload_plan_digest=base.workload_plan_digest,
                user_id=base.user_id, task_seq=base.task_seq,
                repair_round=base.repair_round, phase=base.phase,
                sequence_ordinal=base.sequence_ordinal,
                token_ordinal=base.token_ordinal, layer_id=base.layer_id,
                logical_source_rank=base.logical_source_rank,
                topk_slot=slot, draw_id=0))
        assert selected == vector["selected"], vector["name"]
        assert len(set(selected)) == len(selected)


def test_gate5_without_replacement_rejects_topk_above_expert_count():
    base = RngKey(master_seed=1, workload_plan_digest=PLAN_DIGEST, user_id=1,
                  task_seq=1, repair_round=0, phase=2, sequence_ordinal=1,
                  token_ordinal=0, layer_id=1, logical_source_rank=0,
                  topk_slot=0, draw_id=0)
    with pytest.raises(MeshIrError) as err:
        without_replacement(2, 3, lambda slot: base)
    assert err.value.code == "E_MOE_RNG"


def test_gate5_splitmix_matches_published_reference_sequence():
    assert splitmix64_once(0) == 0xE220A8397B1DCDAF
    assert splitmix64_once(1) == 0x910A2DEC89025CC1


def test_gate5_q16_rounding_and_profile_digests():
    assert checked_round_q16(0) == 0
    assert checked_round_q16(32768) == 1
    assert checked_round_q16(98304) == 2
    assert checked_round_q16(65535) == 1
    first = uniform_profile_digest(7, 1, 8, 2)
    second = uniform_profile_digest(7, 2, 8, 2)
    assert first != second
    scenario = scenario_provider_digest([(1, first), (2, second)])
    assert scenario == scenario_provider_digest([(2, second), (1, first)])
    assert scenario != scenario_provider_digest([(1, second), (2, first)])

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as G
from mesh_ir.model import MeshIrError, canonical_json_bytes
from mesh_ir.moe_capacity import expert_capacity
from mesh_ir.moe_provider import (
    FALLBACK_KINDS,
    FrozenToken,
    apply_capacity,
    batch_selection_digest,
    correlated_select,
    freeze_validator,
    histogram_select,
    lexicographic_assignment,
    load_provider_artifact,
    member_selection_digest,
    member_selection_projection,
    population_digest,
    resolve_provider,
    route_replay_select,
    scenario_selection_digest,
    uniform_select,
)
from mesh_ir.moe_uid import SemanticTokenUid

REPO = Path(__file__).resolve().parents[4]
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"
MOE_IMAGE = REPO / "tests/gem5/ai_mesh/fixtures/gate5/moe_multi.mshb"
PLAN_DIGEST = bytes.fromhex("0123456789abcdef" * 4)
MASTER_SEED = 20260901
LAYER_ID = 2
EXPERT_COUNT = 4
TOP_K = 2
FIRST_LAYER_EXPERTS = 2
PROGRAM_LAYERS = (
    {"layer_id": 1, "expert_count": FIRST_LAYER_EXPERTS, "top_k": TOP_K},
    {"layer_id": LAYER_ID, "expert_count": EXPERT_COUNT, "top_k": TOP_K},
)


class Layer:
    def __init__(self, layer_id=LAYER_ID, expert_count=EXPERT_COUNT,
                 top_k=TOP_K, capacity_factor_q16=0x10000,
                 overflow_policy=A.MOE_OVERFLOW_POLICY.DROP):
        self.layer_id = layer_id
        self.expert_count = expert_count
        self.top_k = top_k
        self.capacity_factor_q16 = capacity_factor_q16
        self.overflow_policy = overflow_policy


def token(item_id, ordinal, member, source_rank, phase=G.SEMANTIC_PHASE.DECODE,
          user_id=1, task_seq=None, repair_round=0):
    task_seq = item_id if task_seq is None else task_seq
    uid = SemanticTokenUid(
        workload_plan_item_id=item_id, user_id=user_id, task_seq=task_seq,
        repair_round=repair_round, phase=phase,
        sequence_ordinal=ordinal if phase == G.SEMANTIC_PHASE.DECODE else 0,
        token_ordinal=ordinal if phase == G.SEMANTIC_PHASE.PREFILL else 0)
    return FrozenToken(uid=uid, member_identity=member,
                       source_rank=source_rank, linear_ordinal=ordinal)


@pytest.fixture(scope="module")
def population():
    return [
        token(1, 0, "m1", 0), token(1, 1, "m1", 0),
        token(1, 2, "m1", 1), token(2, 0, "m2", 1),
    ]


def write_json(tmp_path, name, document) -> Path:
    document = dict(document)
    document.pop("route_profile_digest", None)
    document["route_profile_digest"] = __import__("hashlib").sha256(
        canonical_json_bytes(document)).hexdigest()
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def replay_document(program, population, tmp_path) -> Path:
    entries = []
    for index, item in enumerate(population):
        for slot in range(TOP_K):
            entries.append({
                "layer_id": LAYER_ID,
                "uid": {
                    "workload_plan_item_id": item.uid.workload_plan_item_id,
                    "user_id": item.uid.user_id,
                    "task_seq": item.uid.task_seq,
                    "repair_round": item.uid.repair_round,
                    "phase": item.uid.phase,
                    "sequence_ordinal": item.uid.sequence_ordinal,
                    "token_ordinal": item.uid.token_ordinal,
                },
                "logical_source_rank": item.source_rank,
                "topk_slot": slot,
                "selected_expert_id": (index + slot) % EXPERT_COUNT,
            })
    document = {
        "schema": "moe_route_replay_v1",
        "version": 1,
        "workload_plan_digest": PLAN_DIGEST.hex(),
        "mesh_program_digest": program.semantic_sha256(),
        "layers": PROGRAM_LAYERS,
        "entries": entries,
    }
    return write_json(tmp_path, "route_replay.json", document)


def correlated_document(tmp_path) -> Path:
    document = {
        "schema": "moe_correlated_profile_v1",
        "version": 1,
        "route_window_tokens": 4,
        "layers": [
            {
                "layer_id": 1,
                "expert_count": FIRST_LAYER_EXPERTS,
                "top_k": TOP_K,
                "windows": [{
                    "phase": G.SEMANTIC_PHASE.DECODE,
                    "window_ordinal": 0,
                    "base_weights_q32": [1 << 31, 1 << 31],
                    "hotset": [0],
                    "source_bias_q16": [[65536, 65536],
                                        [65536, 32768]],
                    "hotset_boost_q16": 131072,
                    "sticky_expert_boost_q16": 196608,
                }],
            },
            {
                "layer_id": LAYER_ID,
                "expert_count": EXPERT_COUNT,
                "top_k": TOP_K,
                "windows": [{
                    "phase": G.SEMANTIC_PHASE.DECODE,
                    "window_ordinal": 0,
                    "base_weights_q32": [1 << 30, 1 << 30, 1 << 30, 1 << 30],
                    "hotset": [0, 2],
                    "source_bias_q16": [[65536, 65536, 65536, 65536],
                                        [65536, 32768, 65536, 65536]],
                    "hotset_boost_q16": 131072,
                    "sticky_expert_boost_q16": 196608,
                }],
            },
        ],
    }
    return write_json(tmp_path, "correlated.json", document)


def program_layers():
    program = decode_program(MOE_IMAGE.read_bytes())
    return program, program.moe_layer_specs[1]


def test_gate5_uniform_provider_is_token_local_and_frozen(population):
    layer = Layer()
    first = uniform_select(layer, population, PLAN_DIGEST, MASTER_SEED)
    subset = uniform_select(layer, population[:2], PLAN_DIGEST, MASTER_SEED)
    assert [route.canonical_key() for route in subset.routes] == [
        route.canonical_key() for route in first.routes
        if route.uid.sort_key() <= population[1].uid.sort_key()]
    assert len(first.routes) == len(population) * TOP_K
    for uid_routes in _by_uid(first.routes).values():
        assert sorted(route.topk_slot for route in uid_routes) == \
            list(range(TOP_K))
        assert len({route.selected_expert_id for route in uid_routes}) == TOP_K
    assert first.provider_kind == "uniform_smoke"
    assert len(member_selection_projection(first)["members"]) == 2


def _by_uid(routes):
    grouped = {}
    for route in routes:
        grouped.setdefault(route.uid.sort_key(), []).append(route)
    return grouped


def test_gate5_route_replay_provider_projects_the_population(tmp_path,
                                                             population):
    program, _ = program_layers()
    artifact_path = replay_document(program, population, tmp_path)
    artifact = load_provider_artifact(artifact_path, program, PLAN_DIGEST,
                                      "route_replay")
    layer = Layer()
    plan = route_replay_select(layer, population, artifact, PLAN_DIGEST)
    assert len(plan.routes) == len(population) * TOP_K
    assert plan.provider_digest == artifact["route_profile_digest"]
    assert plan.provider_kind == "route_replay"
    trimmed = route_replay_select(layer, population[:1], artifact,
                                  PLAN_DIGEST)
    assert len(trimmed.routes) == TOP_K
    assert {route.uid.workload_plan_item_id for route in trimmed.routes} == {1}
    extended = population + [token(3, 0, "m3", 0)]
    with pytest.raises(MeshIrError) as err:
        route_replay_select(layer, extended, artifact, PLAN_DIGEST)
    assert err.value.code == "E_MOE_ROUTE_REPLAY"


def test_gate5_route_replay_rejects_digest_and_layer_mismatch(tmp_path,
                                                             population):
    program, _ = program_layers()
    artifact_path = replay_document(program, population, tmp_path)
    document = json.loads(artifact_path.read_text())
    document["layers"][0]["top_k"] = 1
    document["route_profile_digest"] = __import__("hashlib").sha256(
        canonical_json_bytes({key: value for key, value in document.items()
                              if key != "route_profile_digest"})).hexdigest()
    mismatched = tmp_path / "route_replay_mismatch.json"
    mismatched.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MeshIrError) as err:
        load_provider_artifact(mismatched, program, PLAN_DIGEST,
                               "route_replay")
    assert err.value.code == "E_MOE_PROFILE"
    document = json.loads(artifact_path.read_text())
    document["mesh_program_digest"] = "0" * 64
    document["route_profile_digest"] = __import__("hashlib").sha256(
        canonical_json_bytes({key: value for key, value in document.items()
                              if key != "route_profile_digest"})).hexdigest()
    wrong_digest = tmp_path / "route_replay_digest.json"
    wrong_digest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MeshIrError) as err:
        load_provider_artifact(wrong_digest, program, PLAN_DIGEST,
                               "route_replay")
    assert err.value.code == "E_MOE_PROFILE"


def test_gate5_correlated_provider_is_token_local_and_windowed(tmp_path,
                                                               population):
    program, _ = program_layers()
    artifact_path = correlated_document(tmp_path)
    profile = load_provider_artifact(artifact_path, program, PLAN_DIGEST,
                                     "correlated_synthetic")
    layer = Layer()
    plan, predecessor = correlated_select(layer, profile, population,
                                          PLAN_DIGEST, MASTER_SEED)
    assert len(plan.routes) == len(population) * TOP_K
    assert set(predecessor) == set(range(TOP_K))
    again, _ = correlated_select(layer, profile, population, PLAN_DIGEST,
                                 MASTER_SEED)
    assert [route.canonical_key() for route in plan.routes] == \
        [route.canonical_key() for route in again.routes]
    subset, _ = correlated_select(layer, profile, population[:2], PLAN_DIGEST,
                                  MASTER_SEED)
    assert {route.canonical_key() for route in subset.routes} == {
        route.canonical_key() for route in plan.routes
        if route.uid.sort_key() <= population[1].uid.sort_key()}


def test_gate5_correlated_provider_validates_its_tables(tmp_path):
    program, _ = program_layers()
    document = json.loads(correlated_document(tmp_path).read_text())
    document["layers"][0]["windows"][0]["base_weights_q32"] = [1 << 31] * 4
    document["route_profile_digest"] = __import__("hashlib").sha256(
        canonical_json_bytes({key: value for key, value in document.items()
                              if key != "route_profile_digest"})).hexdigest()
    bad = tmp_path / "correlated_bad.json"
    bad.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MeshIrError) as err:
        load_provider_artifact(bad, program, PLAN_DIGEST,
                               "correlated_synthetic")
    assert err.value.code == "E_MOE_PROFILE"
    document["layers"][0]["windows"][0]["base_weights_q32"] = [1 << 30] * 4
    document["layers"][0]["windows"][0]["hotset"] = [3, 1]
    document["route_profile_digest"] = __import__("hashlib").sha256(
        canonical_json_bytes({key: value for key, value in document.items()
                              if key != "route_profile_digest"})).hexdigest()
    bad.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MeshIrError) as err:
        load_provider_artifact(bad, program, PLAN_DIGEST,
                               "correlated_synthetic")
    assert err.value.code == "E_MOE_PROFILE"


def histogram_document(program, population, tmp_path, row=None,
                       window=4) -> Path:
    from mesh_ir.moe_provider import population_digest as digest_of

    by_source = {}
    for item in population:
        by_source.setdefault(item.source_rank, []).append(item)
    counts = []
    token_counts = []
    for source_rank in range(max(by_source) + 1):
        items = by_source.get(source_rank, [])
        token_counts.append(len(items))
        source_row = row(source_rank, len(items)) if row else \
            [(len(items) * TOP_K) // EXPERT_COUNT] * EXPERT_COUNT
        if row is None:
            remainder = len(items) * TOP_K - sum(source_row)
            if remainder:
                source_row[0] += remainder
        counts.append(source_row)
    pop = [(item.uid, item.source_rank)
           for item in sorted(population, key=lambda i: i.uid.sort_key())]
    ordinal = min(item.linear_ordinal for item in population)
    document = {
        "schema": "moe_histogram_replay_v1",
        "version": 1,
        "workload_plan_digest": PLAN_DIGEST.hex(),
        "mesh_program_digest": program.semantic_sha256(),
        "route_window_tokens": window,
        "layers": PROGRAM_LAYERS,
        "records": [{
            "layer_id": LAYER_ID,
            "phase": G.SEMANTIC_PHASE.DECODE,
            "window_ordinal": ordinal // window,
            "population_digest": digest_of(LAYER_ID,
                                           G.SEMANTIC_PHASE.DECODE,
                                           ordinal // window, pop),
            "source_token_counts": token_counts,
            "source_expert_counts": counts,
        }],
    }
    return write_json(tmp_path, "histogram.json", document)


def test_gate5_histogram_provider_matches_counts_exactly(tmp_path, population):
    program, _ = program_layers()
    artifact_path = histogram_document(program, population, tmp_path)
    artifact = load_provider_artifact(artifact_path, program, PLAN_DIGEST,
                                      "histogram_replay")
    layer = Layer()
    plan = histogram_select(layer, population, artifact, PLAN_DIGEST)
    assert len(plan.routes) == len(population) * TOP_K
    counts = {}
    for route in plan.routes:
        counts[(route.source_rank, route.selected_expert_id)] = \
            counts.get((route.source_rank, route.selected_expert_id), 0) + 1
    expected = artifact["records"][0]["source_expert_counts"]
    for source_rank, row in enumerate(expected):
        for expert, value in enumerate(row):
            assert counts.get((source_rank, expert), 0) == int(value)
    for uid_routes in _by_uid(plan.routes).values():
        assert sorted(route.topk_slot for route in uid_routes) == \
            list(range(TOP_K))
        assert len({route.selected_expert_id for route in uid_routes}) == TOP_K


def test_gate5_histogram_uses_lexicographically_minimal_assignment():
    layer = Layer(expert_count=2, top_k=1)
    tokens = [token(1, index, "m1", 0) for index in range(3)]
    assignment = lexicographic_assignment(tokens, [1, 2], layer.top_k)
    assert assignment == [[0], [1], [1]]
    heavier_first = lexicographic_assignment(tokens, [2, 1], layer.top_k)
    assert heavier_first == [[0], [0], [1]]


def test_gate5_histogram_rejects_unreachable_and_mismatched_tables(
        tmp_path, population):
    program, _ = program_layers()
    artifact_path = histogram_document(program, population, tmp_path,
                                       row=lambda source, tokens: [0] * 4)
    artifact = load_provider_artifact(artifact_path, program, PLAN_DIGEST,
                                      "histogram_replay")
    with pytest.raises(MeshIrError) as err:
        histogram_select(Layer(), population, artifact, PLAN_DIGEST)
    assert err.value.code == "E_MOE_PROVIDER"
    artifact_path = histogram_document(program, population, tmp_path,
                                       window=1)
    artifact = load_provider_artifact(artifact_path, program, PLAN_DIGEST,
                                      "histogram_replay")
    with pytest.raises(MeshIrError) as err:
        histogram_select(Layer(), population, artifact, PLAN_DIGEST)
    assert err.value.code == "E_MOE_PROVIDER"


def test_gate5_histogram_population_digest_tracks_membership():
    first = [(token(1, 0, "m1", 0).uid, 0)]
    second = [(token(1, 1, "m2", 0).uid, 0)]
    assert population_digest(1, 2, 0, first) != population_digest(1, 2, 0,
                                                                 second)
    assert population_digest(1, 2, 0, first) == population_digest(1, 2, 0,
                                                                  list(first))


def test_gate5_freeze_validator_rejects_duplicate_expert(population):
    layer = Layer()
    plan = uniform_select(layer, population, PLAN_DIGEST, MASTER_SEED)
    duplicated = []
    for route in plan.routes:
        expert = 0 if route.topk_slot == 1 else route.selected_expert_id
        duplicated.append(route.__class__(
            uid=route.uid, member_identity=route.member_identity,
            topk_slot=route.topk_slot, selected_expert_id=expert,
            source_rank=route.source_rank))
    broken = plan.__class__(
        layer_id=plan.layer_id, provider_kind=plan.provider_kind,
        provider_digest=plan.provider_digest,
        workload_plan_digest=plan.workload_plan_digest,
        routes=tuple(duplicated),
        source_expert_counts=plan.source_expert_counts)
    with pytest.raises(MeshIrError) as err:
        freeze_validator(broken, layer, len(population))
    assert err.value.code == "E_MOE_TOPK_DUP"
    short = plan.__class__(
        layer_id=plan.layer_id, provider_kind=plan.provider_kind,
        provider_digest=plan.provider_digest,
        workload_plan_digest=plan.workload_plan_digest,
        routes=tuple(route for route in plan.routes
                     if route.uid.sort_key() != population[-1].uid.sort_key()),
        source_expert_counts=plan.source_expert_counts)
    with pytest.raises(MeshIrError) as err:
        freeze_validator(short, layer, len(population))
    assert err.value.code == "E_MOE_ROUTE_REPLAY"


def test_gate5_capacity_application_follows_the_overflow_policy(population):
    layer = Layer(expert_count=2, top_k=2, capacity_factor_q16=0x8000)
    plan = uniform_select(layer, population, PLAN_DIGEST, MASTER_SEED)
    capacity = expert_capacity(len(population), TOP_K, 0x8000, 2)
    assert capacity == 2
    outcome = apply_capacity(plan, layer, len(population))
    assert outcome.expert_capacity == (capacity, capacity)
    assert len(outcome.accepted) + len(outcome.dropped) == len(plan.routes)
    assert all(load <= capacity for load in outcome.expert_loads)
    assert outcome.padded_slots_by_expert == (0, 0)
    padded_layer = Layer(expert_count=2, top_k=2, capacity_factor_q16=0x20000,
                         overflow_policy=A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY)
    padded = apply_capacity(plan, padded_layer, len(population))
    assert sum(padded.padded_slots_by_expert) == \
        sum(padded.expert_capacity) - sum(padded.expert_loads)
    fail_layer = Layer(expert_count=2, top_k=2, capacity_factor_q16=0x1000,
                       overflow_policy=A.MOE_OVERFLOW_POLICY.FAIL)
    with pytest.raises(MeshIrError) as err:
        apply_capacity(plan, fail_layer, len(population))
    assert err.value.code == "E_MOE_CAPACITY"


def test_gate5_zero_token_population_stays_empty():
    layer = Layer()
    plan = uniform_select(layer, [], PLAN_DIGEST, MASTER_SEED)
    assert plan.routes == ()
    outcome = apply_capacity(plan, layer, 0)
    assert outcome.accepted == () and outcome.dropped == ()
    assert outcome.expert_loads == (0,) * EXPERT_COUNT
    assert outcome.expert_capacity == (0,) * EXPERT_COUNT


def test_gate5_selection_digests_are_membership_sensitive(population):
    layer = Layer()
    plan = uniform_select(layer, population, PLAN_DIGEST, MASTER_SEED)
    single = uniform_select(layer, [population[3]], PLAN_DIGEST, MASTER_SEED)
    assert batch_selection_digest(plan) != batch_selection_digest(single)
    assert member_selection_digest(plan, "m2") == \
        member_selection_digest(single, "m2")
    assert member_selection_digest(plan, "m1") != \
        member_selection_digest(plan, "m2")
    other_layer = Layer(layer_id=1)
    other_plan = uniform_select(other_layer, population, PLAN_DIGEST,
                                MASTER_SEED)
    assert scenario_selection_digest([plan]) != \
        scenario_selection_digest([other_plan])


def test_gate5_missing_policy_fails_or_falls_back(tmp_path, population):
    program, _ = program_layers()
    artifact_path = correlated_document(tmp_path)
    profile = load_provider_artifact(artifact_path, program, PLAN_DIGEST,
                                     "correlated_synthetic")
    layer = Layer()

    def broken():
        raise MeshIrError("E_MOE_PROVIDER", "no histogram population")

    def correlated():
        return correlated_select(layer, profile, population, PLAN_DIGEST,
                                 MASTER_SEED)[0]

    def uniform():
        return uniform_select(layer, population, PLAN_DIGEST, MASTER_SEED)

    with pytest.raises(MeshIrError):
        resolve_provider("histogram_replay", "FAIL", broken, correlated,
                         uniform)
    resolution = resolve_provider("histogram_replay",
                                  "FALLBACK_CORRELATED", broken, correlated,
                                  uniform)
    assert resolution.events[0]["fallback"] == "FALLBACK_CORRELATED"
    assert resolution.plan.provider_kind == "correlated_synthetic"
    resolution = resolve_provider("histogram_replay", "FALLBACK_UNIFORM",
                                  broken, correlated, uniform)
    assert resolution.plan.provider_kind == "uniform_smoke"
    assert "FAIL" in FALLBACK_KINDS
    with pytest.raises(MeshIrError) as err:
        resolve_provider("histogram_replay", "GUESS", broken, correlated,
                         uniform)
    assert err.value.code == "E_MOE_PROVIDER"


def _routes_of(plan):
    return sorted((route.uid.sort_key(), route.source_rank, route.topk_slot,
                   route.selected_expert_id) for route in plan.routes)


def _rank_projection(plan):
    return sorted((route.uid.sort_key(), route.source_rank, route.topk_slot)
                  for route in plan.routes)


def _member_route_lists(plan):
    rows = []
    for member in member_selection_projection(plan)["members"]:
        rows.append(tuple(sorted(
            (route["token_uid"]["workload_plan_item_id"],
             route["token_uid"]["sequence_ordinal"], route["source_rank"],
             route["topk_slot"], route["selected_expert"])
            for route in member["routes"])))
    return sorted(rows)


def _reattributed(population, mapping):
    return [token(item.uid.workload_plan_item_id, item.linear_ordinal,
                  mapping[item.member_identity], item.source_rank)
            for item in population]


def test_gate5_token_local_member_rank_projection_ab(tmp_path, population):
    program, _ = program_layers()
    replay = load_provider_artifact(replay_document(program, population,
                                                    tmp_path), program,
                                    PLAN_DIGEST, "route_replay")
    profile = load_provider_artifact(correlated_document(tmp_path), program,
                                     PLAN_DIGEST, "correlated_synthetic")
    layer = Layer()
    split = population
    merged = _reattributed(population, {"m1": "m9", "m2": "m10"})
    arms = {
        "route_replay": (
            route_replay_select(layer, split, replay, PLAN_DIGEST),
            route_replay_select(layer, merged, replay, PLAN_DIGEST)),
        "correlated_synthetic": (
            correlated_select(layer, profile, split, PLAN_DIGEST,
                              MASTER_SEED)[0],
            correlated_select(layer, profile, merged, PLAN_DIGEST,
                              MASTER_SEED)[0]),
        "uniform_smoke": (
            uniform_select(layer, split, PLAN_DIGEST, MASTER_SEED),
            uniform_select(layer, merged, PLAN_DIGEST, MASTER_SEED)),
    }
    for kind, (first, second) in arms.items():
        assert first.provider_kind == kind
        assert _routes_of(first) == _routes_of(second)
        assert _rank_projection(first) == _rank_projection(second)
        assert scenario_selection_digest([first]) == \
            scenario_selection_digest([second])
        assert batch_selection_digest(first) == batch_selection_digest(second)
    first, second = arms["uniform_smoke"]
    assert _member_route_lists(first) == _member_route_lists(second)
    assert member_selection_digest(first, "m1") != \
        member_selection_digest(second, "m9")
    assert member_selection_digest(first, "m1") != \
        member_selection_digest(first, "m2")


def test_gate5_token_local_selection_survives_batch_split(tmp_path,
                                                          population):
    program, _ = program_layers()
    replay = load_provider_artifact(replay_document(program, population,
                                                    tmp_path), program,
                                    PLAN_DIGEST, "route_replay")
    profile = load_provider_artifact(correlated_document(tmp_path), program,
                                     PLAN_DIGEST, "correlated_synthetic")
    layer = Layer()
    first_half, predecessor = correlated_select(
        layer, profile, population[:2], PLAN_DIGEST, MASTER_SEED)
    second_half, _ = correlated_select(layer, profile, population[2:],
                                       PLAN_DIGEST, MASTER_SEED, predecessor)
    whole = {
        "route_replay": route_replay_select(layer, population, replay,
                                            PLAN_DIGEST),
        "correlated_synthetic": correlated_select(layer, profile, population,
                                                 PLAN_DIGEST,
                                                 MASTER_SEED)[0],
        "uniform_smoke": uniform_select(layer, population, PLAN_DIGEST,
                                        MASTER_SEED),
    }
    halves = {
        "route_replay": [
            route_replay_select(layer, population[:2], replay, PLAN_DIGEST),
            route_replay_select(layer, population[2:], replay, PLAN_DIGEST)],
        "correlated_synthetic": [first_half, second_half],
        "uniform_smoke": [
            uniform_select(layer, population[:2], PLAN_DIGEST, MASTER_SEED),
            uniform_select(layer, population[2:], PLAN_DIGEST, MASTER_SEED)],
    }
    for kind, plan in whole.items():
        merged = sorted([route for part in halves[kind]
                         for route in _routes_of(part)])
        assert merged == _routes_of(plan)
        assert scenario_selection_digest(halves[kind]) == \
            scenario_selection_digest([plan])
        assert scenario_selection_digest(halves[kind][:1]) != \
            scenario_selection_digest([plan])


def test_gate5_histogram_population_equality_is_assignment_stable(
        tmp_path, population):
    program, _ = program_layers()
    artifact = load_provider_artifact(
        histogram_document(program, population, tmp_path), program,
        PLAN_DIGEST, "histogram_replay")
    layer = Layer()
    first = histogram_select(layer, population, artifact, PLAN_DIGEST)
    reordered = histogram_select(layer, list(reversed(population)), artifact,
                                 PLAN_DIGEST)
    merged = histogram_select(
        layer, _reattributed(population, {"m1": "m9", "m2": "m10"}),
        artifact, PLAN_DIGEST)
    assert first.member_identities != merged.member_identities
    assert _routes_of(first) == _routes_of(reordered)
    assert _routes_of(first) == _routes_of(merged)
    assert batch_selection_digest(first) == batch_selection_digest(reordered)
    assert scenario_selection_digest([first]) == \
        scenario_selection_digest([merged])
    shifted = [token(9, 0, "m1", 0)]
    with pytest.raises(MeshIrError) as err:
        histogram_select(layer, shifted, artifact, PLAN_DIGEST)
    assert err.value.code == "E_MOE_PROVIDER"


def test_gate5_histogram_reassignment_stays_exact(tmp_path, population):
    program, _ = program_layers()
    dense = population + [token(2, 1, "m2", 1), token(2, 2, "m2", 0)]

    def skewed(source, tokens):
        row = [0] * EXPERT_COUNT
        row[2 * source] = tokens
        row[2 * source + 1] = tokens
        return row

    artifact = load_provider_artifact(
        histogram_document(program, dense, tmp_path, row=skewed), program,
        PLAN_DIGEST, "histogram_replay")
    layer = Layer()
    plan = histogram_select(layer, dense, artifact, PLAN_DIGEST)
    again = histogram_select(layer, dense, artifact, PLAN_DIGEST)
    assert _routes_of(plan) == _routes_of(again)
    counts = {}
    for route in plan.routes:
        counts[(route.source_rank, route.selected_expert_id)] = \
            counts.get((route.source_rank, route.selected_expert_id), 0) + 1
    for source_rank, row in enumerate(
            artifact["records"][0]["source_expert_counts"]):
        for expert, value in enumerate(row):
            assert counts.get((source_rank, expert), 0) == int(value)
    for uid_routes in _by_uid(plan.routes).values():
        assert len({route.selected_expert_id for route in uid_routes}) == TOP_K
    assert scenario_selection_digest([plan]) != \
        scenario_selection_digest([histogram_select(
            layer, population, load_provider_artifact(
                histogram_document(program, population, tmp_path), program,
                PLAN_DIGEST, "histogram_replay"), PLAN_DIGEST)])


def test_gate5_selection_digest_scope_is_token_and_rank_only(population):
    layer = Layer()
    plan = uniform_select(layer, population, PLAN_DIGEST, MASTER_SEED)
    merged = uniform_select(layer, _reattributed(population, {"m1": "m9",
                                                             "m2": "m10"}),
                            PLAN_DIGEST, MASTER_SEED)
    assert member_selection_projection(plan)["members"][0][
        "member_workload_identity"] != \
        member_selection_projection(merged)["members"][0][
            "member_workload_identity"]
    assert scenario_selection_digest([plan]) == \
        scenario_selection_digest([merged])
    assert batch_selection_digest(plan) == batch_selection_digest(merged)
    assert member_selection_digest(plan, "m1") == \
        member_selection_digest(plan.__class__(
            **{**plan.__dict__, "member_identities": ("m1",)}), "m1")
    reshuffled = [token(item.uid.workload_plan_item_id, item.linear_ordinal,
                        item.member_identity, 1 - item.source_rank)
                  for item in population]
    ranked = uniform_select(layer, reshuffled, PLAN_DIGEST, MASTER_SEED)
    assert _rank_projection(ranked) == sorted(
        (uid, 1 - rank, slot)
        for uid, rank, slot in _rank_projection(plan))
    assert scenario_selection_digest([ranked]) != \
        scenario_selection_digest([plan])
    other_layer = Layer(layer_id=1, expert_count=FIRST_LAYER_EXPERTS)
    other = uniform_select(other_layer, population, PLAN_DIGEST, MASTER_SEED)
    assert scenario_selection_digest([other]) != \
        scenario_selection_digest([plan])

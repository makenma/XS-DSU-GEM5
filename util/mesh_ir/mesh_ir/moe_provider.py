"""MoE route providers and selection freeze (contract 7.4-7.7).

Four providers share one freeze validator and one capacity application
owner: `route_replay`, `histogram_replay`, `correlated_synthetic` and
`uniform_smoke`.  Every provider returns only a pre-capacity
`BatchMoeSelectionPlan`; capacity disposition and everything downstream is
derived here so the runtime and the oracle cannot diverge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError, canonical_json_bytes
from mesh_ir.moe_capacity import expert_capacity
from mesh_ir.moe_rng import (
    RngKey,
    categorical,
    checked_round_q16,
    uniform_profile_digest,
    without_replacement,
)
from mesh_ir.moe_uid import SemanticTokenUid

Q32_TOTAL = 1 << 32
SELECTION_SCHEMA = "moe_member_selection_v1"
BATCH_SELECTION_SCHEMA = "moe_selection_v1"
SCENARIO_SELECTION_SCHEMA = "moe_scenario_selection_v1"
FALLBACK_KINDS = ("FAIL", "FALLBACK_CORRELATED", "FALLBACK_UNIFORM")


def _schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "schemas" / "ai_mesh"


def _validate_schema(filename: str, document) -> None:
    schema_path = _schema_root() / filename
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(document),
                    key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        path = "/".join(str(part) for part in first.absolute_path)
        raise MeshIrError("E_MOE_PROVIDER",
                          f"{filename}: {path or '$'} {first.message}")


def _digest(document) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _projection_digest(document: dict) -> str:
    return _digest({key: value for key, value in document.items()
                    if key != "route_profile_digest"})


@dataclass(frozen=True)
class FrozenToken:
    uid: SemanticTokenUid
    member_identity: str
    source_rank: int
    linear_ordinal: int


@dataclass(frozen=True)
class SelectedTokenRoute:
    uid: SemanticTokenUid
    member_identity: str
    topk_slot: int
    selected_expert_id: int
    source_rank: int

    def canonical_key(self) -> tuple:
        return (self.uid.sort_key(), self.source_rank, self.topk_slot,
                self.selected_expert_id)


@dataclass(frozen=True)
class BatchMoeSelectionPlan:
    layer_id: int
    provider_kind: str
    provider_digest: str
    workload_plan_digest: str
    routes: tuple
    source_expert_counts: tuple
    fallback_events: tuple = ()
    member_identities: tuple = ()

    def routes_of(self, member_identity: str) -> tuple:
        return tuple(route for route in self.routes
                     if route.member_identity == member_identity)

    def members(self) -> tuple:
        if self.member_identities:
            return self.member_identities
        return tuple(sorted({route.member_identity for route in self.routes}))


@dataclass(frozen=True)
class CapacityOutcome:
    accepted: tuple
    dropped: tuple
    expert_loads: tuple
    padded_slots_by_expert: tuple
    expert_capacity: tuple


@dataclass(frozen=True)
class ProviderResolution:
    plan: BatchMoeSelectionPlan
    events: tuple = field(default=())


def _layer_digests(program) -> dict:
    return {layer.layer_id: (layer.expert_count, layer.top_k)
            for layer in program.moe_layer_specs}


def load_provider_artifact(path, program, workload_plan_digest: bytes,
                           provider_kind: str) -> dict:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    filename = {
        "route_replay": "moe_route_replay.schema.json",
        "histogram_replay": "moe_histogram_replay.schema.json",
        "correlated_synthetic": "moe_correlated_profile.schema.json",
    }[provider_kind]
    _validate_schema(filename, document)
    expected = _layer_digests(program)
    layers = {entry["layer_id"]: (entry["expert_count"], entry["top_k"])
              for entry in document["layers"]}
    if layers != expected:
        raise MeshIrError("E_MOE_PROFILE",
                          "provider layer registry differs from the program")
    if provider_kind in ("route_replay", "histogram_replay"):
        if bytes.fromhex(document["workload_plan_digest"]) != \
                workload_plan_digest:
            raise MeshIrError("E_MOE_PROFILE",
                              "provider workload plan digest mismatch")
        if bytes.fromhex(document["mesh_program_digest"]) != \
                bytes.fromhex(program.semantic_sha256()):
            raise MeshIrError("E_MOE_PROFILE",
                              "provider mesh program digest mismatch")
    if provider_kind == "histogram_replay":
        if document["route_window_tokens"] <= 0:
            raise MeshIrError("E_MOE_PROFILE",
                              "route_window_tokens must be positive")
    if provider_kind == "correlated_synthetic":
        if document["route_window_tokens"] <= 0:
            raise MeshIrError("E_MOE_PROFILE",
                              "route_window_tokens must be positive")
        for layer in document["layers"]:
            for window in layer["windows"]:
                _validate_correlated_window(layer, window)
    if document["route_profile_digest"] != _projection_digest(document):
        raise MeshIrError("E_MOE_PROFILE", "route_profile_digest mismatch")
    return document


def _validate_correlated_window(layer, window) -> None:
    experts = layer["expert_count"]
    base = window["base_weights_q32"]
    if len(base) != experts or sum(base) != Q32_TOTAL:
        raise MeshIrError("E_MOE_PROFILE",
                          "correlated base weights must cover experts and "
                          "sum to 2^32")
    hotset = window["hotset"]
    if not hotset or hotset != sorted(set(hotset)) or \
            hotset[-1] >= experts:
        raise MeshIrError("E_MOE_PROFILE", "correlated hotset is invalid")
    bias = window["source_bias_q16"]
    if not bias or any(len(row) != experts for row in bias):
        raise MeshIrError("E_MOE_PROFILE",
                          "correlated source bias matrix is invalid")


def _key_of(workload_plan_digest: bytes, master_seed: int, token: FrozenToken,
            layer_id: int, slot: int) -> RngKey:
    uid = token.uid
    return RngKey(master_seed=master_seed,
                  workload_plan_digest=workload_plan_digest,
                  user_id=uid.user_id, task_seq=uid.task_seq,
                  repair_round=uid.repair_round, phase=uid.phase,
                  sequence_ordinal=uid.sequence_ordinal,
                  token_ordinal=uid.token_ordinal, layer_id=layer_id,
                  logical_source_rank=token.source_rank, topk_slot=slot,
                  draw_id=0)


def uniform_select(layer, tokens, workload_plan_digest: bytes,
                   master_seed: int) -> BatchMoeSelectionPlan:
    routes = []
    for token in tokens:
        selected = without_replacement(
            layer.expert_count, layer.top_k,
            lambda slot, token=token: _key_of(workload_plan_digest,
                                              master_seed, token,
                                              layer.layer_id, slot))
        for slot, expert in enumerate(selected):
            routes.append(SelectedTokenRoute(
                uid=token.uid, member_identity=token.member_identity,
                topk_slot=slot, selected_expert_id=expert,
                source_rank=token.source_rank))
    return _finish_plan(layer, "uniform_smoke",
                        uniform_profile_digest(master_seed, layer.layer_id,
                                               layer.expert_count,
                                               layer.top_k),
                        workload_plan_digest, routes, len(tokens))


def route_replay_select(layer, tokens, artifact: dict,
                        workload_plan_digest: bytes) -> \
        BatchMoeSelectionPlan:
    entries = {}
    for entry in artifact["entries"]:
        if entry["layer_id"] != layer.layer_id:
            continue
        uid = _uid_of(entry["uid"])
        entries[(uid.sort_key(), entry["logical_source_rank"],
                 entry["topk_slot"])] = entry["selected_expert_id"]
    routes = []
    for token in tokens:
        for slot in range(layer.top_k):
            key = (token.uid.sort_key(), token.source_rank, slot)
            if key not in entries:
                raise MeshIrError("E_MOE_ROUTE_REPLAY",
                                  "route replay misses a population slot",
                                  token_ordinal=token.uid.token_ordinal,
                                  topk_slot=slot)
            routes.append(SelectedTokenRoute(
                uid=token.uid, member_identity=token.member_identity,
                topk_slot=slot, selected_expert_id=entries[key],
                source_rank=token.source_rank))
    return _finish_plan(layer, "route_replay",
                        artifact["route_profile_digest"],
                        workload_plan_digest, routes, len(tokens))


def _uid_of(fields) -> SemanticTokenUid:
    return SemanticTokenUid(
        workload_plan_item_id=int(fields["workload_plan_item_id"]),
        user_id=int(fields["user_id"]), task_seq=int(fields["task_seq"]),
        repair_round=int(fields["repair_round"]), phase=int(fields["phase"]),
        sequence_ordinal=int(fields["sequence_ordinal"]),
        token_ordinal=int(fields["token_ordinal"]))


def population_digest(layer_id: int, phase: int, window_ordinal: int,
                      population) -> str:
    entries = b"".join(uid.encode() + source_rank.to_bytes(4, "little")
                       for uid, source_rank in population)
    body = (b"MOE_HIST_POPULATION_V1\0" +
            layer_id.to_bytes(4, "little") + bytes([phase, 0, 0, 0]) +
            window_ordinal.to_bytes(4, "little") +
            len(population).to_bytes(4, "little") + entries)
    return hashlib.sha256(body).hexdigest()


def _histogram_record(artifact: dict, layer_id: int, phase: int,
                      window_ordinal: int, population) -> dict:
    digest = population_digest(layer_id, phase, window_ordinal, population)
    for record in artifact["records"]:
        if record["layer_id"] == layer_id and record["phase"] == phase and \
                int(record["window_ordinal"]) == window_ordinal and \
                record["population_digest"] == digest:
            return record
    raise MeshIrError("E_MOE_PROVIDER", "histogram population is missing",
                      layer_id=layer_id, window_ordinal=window_ordinal)


def histogram_select(layer, tokens, artifact: dict,
                     workload_plan_digest: bytes) -> BatchMoeSelectionPlan:
    by_source = {}
    for token in tokens:
        by_source.setdefault(token.source_rank, []).append(token)
    routes = []
    window = artifact["route_window_tokens"]
    phases = {token.uid.phase for token in tokens}
    if len(phases) != 1:
        raise MeshIrError("E_MOE_PROVIDER",
                          "one histogram selection covers one phase")
    phase = phases.pop()
    ordinals = [token.linear_ordinal for token in tokens]
    if not ordinals:
        raise MeshIrError("E_MOE_PROVIDER", "empty token population")
    window_ordinals = {ordinal // window for ordinal in ordinals}
    if len(window_ordinals) != 1:
        raise MeshIrError("E_MOE_PROVIDER",
                          "histogram selection mixes route windows")
    window_ordinal = window_ordinals.pop()
    pop = [(token.uid, token.source_rank)
           for token in sorted(tokens, key=lambda token: token.uid.sort_key())]
    record = _histogram_record(artifact, layer.layer_id, phase,
                               window_ordinal, pop)
    counts = record["source_expert_counts"]
    token_counts = record["source_token_counts"]
    for source_rank, source_tokens in sorted(by_source.items()):
        if source_rank >= len(counts):
            raise MeshIrError("E_MOE_PROVIDER",
                              "histogram source rank out of range",
                              source_rank=source_rank)
        row = [int(value) for value in counts[source_rank]]
        if len(row) != layer.expert_count:
            raise MeshIrError("E_MOE_PROVIDER",
                              "histogram expert row length mismatch")
        if int(token_counts[source_rank]) != len(source_tokens):
            raise MeshIrError("E_MOE_PROVIDER",
                              "histogram source token count mismatch")
        if sum(row) != len(source_tokens) * layer.top_k:
            raise MeshIrError("E_MOE_PROVIDER",
                              "histogram does not equal tokens*top_k")
        ordered = sorted(source_tokens, key=lambda token: token.uid.sort_key())
        assignment = lexicographic_assignment(ordered, row, layer.top_k)
        for token, experts in zip(ordered, assignment):
            for slot, expert in enumerate(sorted(experts)):
                routes.append(SelectedTokenRoute(
                    uid=token.uid, member_identity=token.member_identity,
                    topk_slot=slot, selected_expert_id=expert,
                    source_rank=token.source_rank))
    plan = _finish_plan(layer, "histogram_replay",
                        artifact["route_profile_digest"], workload_plan_digest,
                        routes, len(tokens))
    return plan


def _feasible(token_needs, expert_caps, allowed):
    tokens = [token for token in sorted(token_needs) if token_needs[token]]
    experts = list(range(len(expert_caps)))
    if sum(token_needs[token] for token in tokens) != sum(expert_caps):
        return False
    source = 0
    token_base = 1
    expert_base = token_base + len(tokens)
    sink = expert_base + len(experts)
    graph = [[] for _ in range(sink + 1)]

    def add_edge(u, v, capacity):
        graph[u].append([v, capacity, len(graph[v])])
        graph[v].append([u, 0, len(graph[u]) - 1])

    for index, token in enumerate(tokens):
        add_edge(source, token_base + index, token_needs[token])
    for index, expert in enumerate(experts):
        if expert_caps[expert]:
            add_edge(expert_base + index, sink, expert_caps[expert])
    for token_index, token in enumerate(tokens):
        for expert in allowed[token]:
            if expert_caps[expert]:
                add_edge(token_base + token_index, expert_base + expert, 1)

    flow = 0
    demand = sum(token_needs[token] for token in tokens)
    while True:
        level = [-1] * len(graph)
        level[source] = 0
        queue = [source]
        while queue:
            node = queue.pop(0)
            for edge in graph[node]:
                if edge[1] > 0 and level[edge[0]] < 0:
                    level[edge[0]] = level[node] + 1
                    queue.append(edge[0])
        if level[sink] < 0:
            break
        iterator = [0] * len(graph)

        def augment(node, pushed):
            if node == sink:
                return pushed
            while iterator[node] < len(graph[node]):
                edge = graph[node][iterator[node]]
                target = edge[0]
                if edge[1] > 0 and level[target] == level[node] + 1:
                    amount = augment(target, min(pushed, edge[1]))
                    if amount:
                        edge[1] -= amount
                        graph[target][edge[2]][1] += amount
                        return amount
                iterator[node] += 1
            return 0

        while True:
            pushed = augment(source, demand - flow)
            if not pushed:
                break
            flow += pushed
            if flow == demand:
                break
        if flow == demand:
            break
    return flow == demand


def lexicographic_assignment(tokens, expert_row, top_k):
    remaining = list(expert_row)
    needs = {token.uid.sort_key(): top_k for token in tokens}
    assigned = {token.uid.sort_key(): [] for token in tokens}
    allowed = {token.uid.sort_key(): list(range(len(expert_row)))
               for token in tokens}
    for token in tokens:
        key = token.uid.sort_key()
        for expert in range(len(expert_row)):
            if len(assigned[key]) == top_k:
                break
            if remaining[expert] == 0:
                continue
            remaining[expert] -= 1
            needs[key] -= 1
            allowed[key] = [candidate for candidate in allowed[key]
                            if candidate != expert]
            if _feasible(needs, remaining, allowed):
                assigned[key].append(expert)
            else:
                remaining[expert] += 1
                needs[key] += 1
                allowed[key] = sorted(allowed[key] + [expert])
        if len(assigned[key]) != top_k:
            raise MeshIrError("E_MOE_PROVIDER",
                              "histogram assignment is infeasible",
                              token_ordinal=token.uid.token_ordinal)
    return [assigned[token.uid.sort_key()] for token in tokens]


def correlated_select(layer, profile: dict, tokens, workload_plan_digest:
                      bytes, master_seed: int, predecessor=None) -> tuple:
    layer_profile = next((entry for entry in profile["layers"]
                          if entry["layer_id"] == layer.layer_id), None)
    if layer_profile is None or \
            layer_profile["expert_count"] != layer.expert_count or \
            layer_profile["top_k"] != layer.top_k:
        raise MeshIrError("E_MOE_PROFILE",
                          "correlated profile does not cover this layer")
    windows = {}
    for window in layer_profile["windows"]:
        windows[(window["phase"], int(window["window_ordinal"]))] = window
    schedule = profile["route_window_tokens"]
    predecessor = dict(predecessor or {})
    routes = []
    for token in tokens:
        phase = token.uid.phase
        window_ordinal = token.linear_ordinal // schedule
        window = windows.get((phase, window_ordinal))
        if window is None:
            raise MeshIrError("E_MOE_PROVIDER",
                              "correlated profile misses a window",
                              window_ordinal=window_ordinal)
        base = [int(value) for value in window["base_weights_q32"]]
        hotset = set(window["hotset"])
        bias = window["source_bias_q16"]
        if token.source_rank >= len(bias):
            raise MeshIrError("E_MOE_PROVIDER",
                              "correlated source rank out of range")
        row = [int(value) for value in bias[token.source_rank]]
        selected = []
        for slot in range(layer.top_k):
            weights = []
            for expert in range(layer.expert_count):
                weight = base[expert]
                if expert in hotset:
                    weight = checked_round_q16(
                        weight * int(window["hotset_boost_q16"]))
                weight = checked_round_q16(weight * row[expert])
                if predecessor.get(slot) == expert:
                    weight = checked_round_q16(
                        weight * int(window["sticky_expert_boost_q16"]))
                if expert in selected:
                    weight = 0
                weights.append(weight)
            key = _key_of(workload_plan_digest, master_seed, token,
                          layer.layer_id, slot)
            expert = categorical(weights, key)
            selected.append(expert)
        for slot, expert in enumerate(selected):
            predecessor[slot] = expert
            routes.append(SelectedTokenRoute(
                uid=token.uid, member_identity=token.member_identity,
                topk_slot=slot, selected_expert_id=expert,
                source_rank=token.source_rank))
    plan = _finish_plan(layer, "correlated_synthetic",
                        profile["route_profile_digest"],
                        workload_plan_digest, routes, len(tokens))
    return plan, predecessor


def _finish_plan(layer, provider_kind, provider_digest,
                 workload_plan_digest, routes, token_count):
    ordered = tuple(sorted(routes, key=lambda route: route.canonical_key()))
    source_counts = {}
    for route in ordered:
        key = (route.source_rank, route.selected_expert_id)
        source_counts[key] = source_counts.get(key, 0) + 1
    counts = tuple(sorted(source_counts.items()))
    members = tuple(sorted({route.member_identity for route in ordered}))
    plan = BatchMoeSelectionPlan(
        layer_id=layer.layer_id, provider_kind=provider_kind,
        provider_digest=provider_digest,
        workload_plan_digest=workload_plan_digest.hex(), routes=ordered,
        source_expert_counts=counts, member_identities=members)
    freeze_validator(plan, layer, token_count)
    return plan


def freeze_validator(plan, layer, token_count) -> None:
    if token_count == 0 and plan.routes:
        raise MeshIrError("E_MOE_ROUTE_REPLAY",
                          "zero-token population must have no routes")
    per_uid = {}
    for route in plan.routes:
        if route.selected_expert_id >= layer.expert_count:
            raise MeshIrError("E_MOE_PROVIDER",
                              "selected expert out of range",
                              expert_id=route.selected_expert_id)
        if route.topk_slot >= layer.top_k:
            raise MeshIrError("E_MOE_ROUTE_REPLAY", "top-k slot out of range")
        per_uid.setdefault(route.uid.sort_key(), []).append(route)
    if len(per_uid) != token_count:
        raise MeshIrError("E_MOE_ROUTE_REPLAY",
                          "selection does not cover the frozen population")
    for uid_routes in per_uid.values():
        slots = sorted(route.topk_slot for route in uid_routes)
        if slots != list(range(layer.top_k)):
            raise MeshIrError("E_MOE_ROUTE_REPLAY",
                              "top-k slots must be dense per token",
                              topk_slots=slots)
        experts = [route.selected_expert_id for route in uid_routes]
        if len(set(experts)) != layer.top_k:
            raise MeshIrError("E_MOE_TOPK_DUP",
                              "a token selected one expert twice",
                              experts=experts)


def apply_capacity(plan, layer, total_valid_tokens: int) -> CapacityOutcome:
    loads = [0] * layer.expert_count
    for route in plan.routes:
        loads[route.selected_expert_id] += 1
    capacities = [expert_capacity(total_valid_tokens, layer.top_k,
                                  layer.capacity_factor_q16,
                                  layer.expert_count)
                  for _ in range(layer.expert_count)]
    policy = layer.overflow_policy
    if policy == A.MOE_OVERFLOW_POLICY.FAIL and any(
            loads[expert] > capacities[expert]
            for expert in range(layer.expert_count)):
        raise MeshIrError("E_MOE_CAPACITY",
                          "expert overflow fails the frozen batch")
    accepted = []
    dropped = []
    seen = [0] * layer.expert_count
    for route in plan.routes:
        expert = route.selected_expert_id
        if seen[expert] < capacities[expert]:
            seen[expert] += 1
            accepted.append(route)
        else:
            dropped.append(route)
    padded = []
    if policy == A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY:
        padded = [capacities[expert] - seen[expert]
                  for expert in range(layer.expert_count)]
    else:
        padded = [0] * layer.expert_count
    return CapacityOutcome(accepted=tuple(accepted), dropped=tuple(dropped),
                           expert_loads=tuple(seen),
                           padded_slots_by_expert=tuple(padded),
                           expert_capacity=tuple(capacities))


def _uid_fields(uid: SemanticTokenUid) -> dict:
    return {
        "workload_plan_item_id": uid.workload_plan_item_id,
        "user_id": uid.user_id,
        "task_seq": uid.task_seq,
        "repair_round": uid.repair_round,
        "phase": uid.phase,
        "sequence_ordinal": uid.sequence_ordinal,
        "token_ordinal": uid.token_ordinal,
    }


def member_selection_projection(plan) -> dict:
    return {
        "schema": SELECTION_SCHEMA,
        "workload_plan_digest": plan.workload_plan_digest,
        "layer_id": plan.layer_id,
        "members": [
            {
                "member_workload_identity": member,
                "routes": [
                    {
                        "token_uid": _uid_fields(route.uid),
                        "source_rank": route.source_rank,
                        "topk_slot": route.topk_slot,
                        "selected_expert": route.selected_expert_id,
                    }
                    for route in plan.routes_of(member)
                ],
            }
            for member in plan.members()
        ],
    }


def member_selection_digest(plan, member_identity: str) -> str:
    projection = {
        "schema": SELECTION_SCHEMA,
        "workload_plan_digest": plan.workload_plan_digest,
        "layer_id": plan.layer_id,
        "member_workload_identity": member_identity,
        "routes": [
            {
                "token_uid": _uid_fields(route.uid),
                "source_rank": route.source_rank,
                "topk_slot": route.topk_slot,
                "selected_expert": route.selected_expert_id,
            }
            for route in plan.routes_of(member_identity)
        ],
    }
    return _digest(projection)


def batch_selection_digest(plan) -> str:
    projection = {
        "schema": BATCH_SELECTION_SCHEMA,
        "workload_plan_digest": plan.workload_plan_digest,
        "layer_id": plan.layer_id,
        "routes": [
            {
                "token_uid": _uid_fields(route.uid),
                "source_rank": route.source_rank,
                "topk_slot": route.topk_slot,
                "selected_expert": route.selected_expert_id,
            }
            for route in plan.routes
        ],
    }
    return _digest(projection)


def scenario_selection_digest(plans) -> str:
    entries = []
    for plan in plans:
        for route in plan.routes:
            entries.append((plan.layer_id, route.uid.sort_key(),
                            route.source_rank, route.topk_slot,
                            _uid_fields(route.uid),
                            route.selected_expert_id))
    entries.sort(key=lambda entry: entry[:4])
    projection = {
        "schema": SCENARIO_SELECTION_SCHEMA,
        "workload_plan_digest": plans[0].workload_plan_digest if plans else "",
        "routes": [
            {
                "layer_id": layer_id,
                "token_uid": uid_fields,
                "source_rank": source_rank,
                "topk_slot": topk_slot,
                "selected_expert": expert,
            }
            for (layer_id, _, source_rank, topk_slot, uid_fields, expert)
            in entries
        ],
    }
    return _digest(projection)


def resolve_provider(provider_kind: str, missing_policy: str,
                     build_primary, build_correlated, build_uniform) -> \
        ProviderResolution:
    if missing_policy not in FALLBACK_KINDS:
        raise MeshIrError("E_MOE_PROVIDER", "unknown provider missing policy")
    try:
        return ProviderResolution(plan=build_primary())
    except MeshIrError as error:
        if error.code not in ("E_MOE_PROVIDER", "E_MOE_ROUTE_REPLAY"):
            raise
        if missing_policy == "FAIL":
            raise
        fallback = (build_correlated() if missing_policy ==
                    "FALLBACK_CORRELATED" else build_uniform())
        event = {
            "provider": provider_kind,
            "fallback": missing_policy,
            "reason": error.code,
        }
        return ProviderResolution(plan=fallback, events=(event,))

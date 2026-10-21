from __future__ import annotations

import json
from pathlib import Path

from mesh_ir.moe_provider import (
    FrozenToken, histogram_select, load_provider_artifact,
    member_selection_projection, route_replay_select,
)
from mesh_ir.moe_uid import SemanticTokenUid


class ServingMoeProvider:
    def __init__(self, program, workload_digest, kind, artifact, output_directory):
        self.layers = {layer.layer_id: layer for layer in program.moe_layer_specs}
        self.workload_digest = bytes.fromhex(workload_digest)
        self.artifact = load_provider_artifact(artifact, program, self.workload_digest, kind)
        self.kind = kind
        self.select = {'route_replay': route_replay_select, 'histogram_replay': histogram_select}[kind]
        self.output_directory = Path(output_directory)
        self.invocations = 0
        (self.output_directory / 'moe_provider.json').write_text(json.dumps(self.artifact, sort_keys=True))

    def __call__(self, layer_id, population):
        tokens = []
        for item, user, task, repair, phase, sequence, token, member, rank in population:
            uid = SemanticTokenUid(item, user, task, repair, phase, sequence, token)
            uid.validate()
            tokens.append(FrozenToken(uid, str(member), rank, token if phase == 1 else sequence))
        layer = self.layers[layer_id]
        groups = {}
        for token in tokens:
            window = token.linear_ordinal // self.artifact['route_window_tokens'] if self.kind == 'histogram_replay' else 0
            groups.setdefault((token.uid.phase, window), []).append(token)
        selections = [self.select(layer, group, self.artifact, self.workload_digest)
                      for _, group in sorted(groups.items())]
        routes = {(route.uid.sort_key(), route.source_rank, route.topk_slot): route.selected_expert_id
                  for selection in selections for route in selection.routes}
        experts = [routes[(token.uid.sort_key(), token.source_rank, slot)]
                   for token in tokens for slot in range(layer.top_k)]
        self.invocations += 1
        for index, selection in enumerate(selections):
            (self.output_directory / f'provider_selection_{self.invocations}_{index}.json').write_text(
                json.dumps(member_selection_projection(selection), sort_keys=True))
        return experts

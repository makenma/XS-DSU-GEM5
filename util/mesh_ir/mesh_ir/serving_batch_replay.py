from dataclasses import dataclass
import hashlib

from mesh_ir.agent_workload import PlanError, load_strict_json, validate_against_schema
from mesh_ir.generated import abi as A
from mesh_ir.model import canonical_json_bytes
from mesh_ir.serving_profiles import member_rank_vector


@dataclass(frozen=True)
class ReplayMember:
    workload_plan_item_id: int
    user_id: int
    task_seq: int
    repair_round: int
    sequence_ordinal_begin: int
    valid_token_count: int
    logical_source_rank: int

    @property
    def identity(self):
        return (self.workload_plan_item_id, self.user_id, self.task_seq, self.repair_round)


@dataclass(frozen=True)
class ReplayBatch:
    batch_ordinal: int
    phase: str
    decode_iteration: int
    concrete_profile_id: int
    members: tuple[ReplayMember, ...]


@dataclass(frozen=True)
class ServingBatchReplay:
    workload_plan_digest: str
    mesh_program_digest: str
    batches: tuple[ReplayBatch, ...]
    digest: str


def load_batch_replay(path, workload, program):
    document = load_strict_json(path)
    validate_against_schema('serving_batch_replay.schema.json', document)
    if document['workload_plan_digest'] != workload.digest or document['mesh_program_digest'] != program.semantic_sha256:
        raise PlanError('E_BATCH_REPLAY_DIVERGENCE', '$', 'replay input digest mismatch')
    rounds = {(round_.workload_plan_item_id, user.user_id, task.task_seq, round_.repair_round): round_
              for user in workload.users for task in user.tasks for round_ in task.rounds
              if round_.batch_replay}
    profiles = {row.instance_profile_id: row for row in program.agent_instance_profiles}
    cursors = {}
    batches = []
    for ordinal, source in enumerate(document['batches']):
        batch = ReplayBatch(**dict(source, members=tuple(ReplayMember(**row) for row in source['members'])))
        location = f'batches/{ordinal}'
        profile = profiles.get(batch.concrete_profile_id)
        identities = tuple(member.identity for member in batch.members)
        if (batch.batch_ordinal != ordinal or not profile or
                identities != tuple(sorted(set(identities))) or
                any(identity not in rounds for identity in identities)):
            raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'invalid ordinal, profile or canonical member population')
        if (profile.phase != getattr(A.PHASE, batch.phase) or
                member_rank_vector(program, profile) != tuple(row.logical_source_rank for row in batch.members)):
            raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'concrete phase or rank vector mismatch')
        for member in batch.members:
            round_ = rounds[member.identity]
            if (profile.request_program_id, profile.request_profile_id) != (round_.program_id, round_.profile_id):
                raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'member request profile mismatch')
            previous = cursors.get(member.identity)
            if batch.phase == 'PREFILL':
                valid = round_.delta_prompt_tokens if profile.path_kind == A.PATH_KIND.KV_REUSE else round_.full_context_tokens
                begin = round_.expected_cached_tokens if profile.path_kind == A.PATH_KIND.KV_REUSE else 0
                allowed_paths = {'INITIAL': (A.PATH_KIND.INITIAL_PREFILL,),
                                 'REQUIRE_REUSE': (A.PATH_KIND.KV_REUSE,),
                                 'ALLOW_REPREFILL': (A.PATH_KIND.KV_REUSE, A.PATH_KIND.REPREFILL)}[round_.kv_policy]
                if previous is not None or batch.decode_iteration != 0 or profile.path_kind not in allowed_paths:
                    raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'PREFILL repeats or violates the frozen KV policy')
                following = (profile.path_kind, 0, 0, False)
            else:
                if previous is None or previous[3] or previous[0] != profile.path_kind or batch.decode_iteration != previous[1]:
                    raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'phase has no matching live predecessor')
                begin = previous[2]
                if batch.phase == 'DECODE':
                    valid = profile.decode_chunk_tokens
                    if valid == 0 or begin + valid > round_.output_tokens:
                        raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'DECODE exceeds the frozen output range')
                    following = (previous[0], previous[1] + 1, begin + valid, False)
                else:
                    valid = 0
                    if begin != round_.output_tokens:
                        raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'PUBLISH precedes complete DECODE coverage')
                    following = (previous[0], previous[1], begin, True)
                if profile.kv_tokens_before != round_.full_context_tokens + begin:
                    raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'concrete profile KV cursor mismatch')
            if (member.sequence_ordinal_begin != begin or member.valid_token_count != valid or
                    profile.valid_tokens_per_member != valid or member.logical_source_rank != round_.logical_source_rank):
                raise PlanError('E_BATCH_REPLAY_DIVERGENCE', location, 'member token interval or stable rank mismatch')
            cursors[member.identity] = following
        batches.append(batch)
    if set(cursors) != set(rounds) or any(not row[3] for row in cursors.values()):
        raise PlanError('E_BATCH_REPLAY_DIVERGENCE', 'batches', 'every flagged round requires one complete phase chain')
    return ServingBatchReplay(document['workload_plan_digest'], document['mesh_program_digest'],
        tuple(batches), hashlib.sha256(canonical_json_bytes(document)).hexdigest())

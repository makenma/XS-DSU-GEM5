#include "dev/ai_mesh/serving_mesh_executor.hh"

namespace gem5::ai_mesh
{
std::set<uint32_t>
ServingMeshExecutor::replayBoundaryAdmissions(const std::set<uint32_t> &activeItems) const
{
    if (!batch_replay)
        return {};
    auto required = activeItems;
    size_t end = replay_cursor;
    size_t previousSize;
    do {
        previousSize = required.size();
        for (size_t index = end; index < batch_replay->batches.size(); ++index)
            for (const auto &member : batch_replay->batches[index].members)
                if (required.count(member.identity.item))
                    end = index + 1;
        for (size_t index = replay_cursor; index < end; ++index)
            for (const auto &member : batch_replay->batches[index].members)
                required.insert(member.identity.item);
    } while (required.size() != previousSize);
    for (const auto item : activeItems)
        required.erase(item);
    return required;
}

void ServingMeshExecutor::configureReplay(std::optional<ServingBatchReplay> replay, bool strict)
{
    fatal_if(!slots.empty() || batch_replay, "cannot replace an active batch replay");
    fatal_if(strict && !replay, "E_BATCH_REPLAY_DIVERGENCE: strict replay requires an artifact");
    batch_replay = std::move(replay);
    strict_replay = strict;
}

void ServingMeshExecutor::onReplayMemberTerminal(const ServingReplayIdentity &identity) const
{
    if (!batch_replay)
        return;
    for (size_t index = replay_cursor; index < batch_replay->batches.size(); ++index)
        for (const auto &member : batch_replay->batches[index].members)
            fatal_if(member.identity == identity,
                "E_BATCH_REPLAY_DIVERGENCE: listed member terminated before its recorded batch");
}

SchedulerDecision ServingMeshExecutor::selectReplayBatch(uint64_t tick, uint64_t available) const
{
    SchedulerDecision decision;
    if (replay_cursor == batch_replay->batches.size()) {
        for (const auto &[id, current] : slots)
            fatal_if(current.submitted_request && current.submitted_request->batchReplay &&
                current.phase_ready_tick && !current.terminal_outcome,
                "E_BATCH_REPLAY_DIVERGENCE: artifact exhausted with a replay member READY");
        return decision;
    }
    const auto &batch = batch_replay->batches[replay_cursor];
    const auto profile = std::find_if(program->transport.agent_instance_profiles.begin(),
        program->transport.agent_instance_profiles.end(), [&](const auto &row) {
            return row.instance_profile_id == batch.profile;
        });
    fatal_if(profile == program->transport.agent_instance_profiles.end() ||
        profile->member_count != batch.members.size(),
        "E_BATCH_REPLAY_DIVERGENCE: recorded profile is absent or has another population");
    fatal_if(batch.members.size() > scheduler_limits.maxActiveSequences ||
        uint64_t(profile->member_count) * profile->valid_tokens_per_member > scheduler_limits.maxBatchTokens ||
        (batch.phase == mesh_abi::kPhasePREFILL &&
         uint64_t(profile->member_count) * profile->valid_tokens_per_member > scheduler_limits.maxPrefillTokensPerBatch) ||
        (batch.phase == mesh_abi::kPhaseDECODE &&
         profile->member_count > scheduler_limits.maxDecodeSequencesPerBatch),
        "E_CAPACITY_PLAN: recorded batch exceeds scheduler limits");
    if (batch.members.size() > available)
        return decision;
    bool waiting = false;
    for (const auto &member : batch.members) {
        const RequestSlot *found = nullptr;
        uint64_t request_id = 0;
        for (const auto &[id, current] : slots) {
            if (!current.submitted_request)
                continue;
            const auto &request = *current.submitted_request;
            const ServingReplayIdentity identity{request.workloadPlanItemId, request.userId,
                request.taskSeq, request.repairRound};
            if (!(identity == member.identity))
                continue;
            fatal_if(found || !request.batchReplay,
                "E_BATCH_REPLAY_DIVERGENCE: recorded identity is duplicated or unflagged");
            found = &current;
            request_id = id;
        }
        if (!found) {
            waiting = true;
            continue;
        }
        fatal_if(found->failed_flag || found->terminal_outcome,
            "E_BATCH_REPLAY_DIVERGENCE: recorded member terminated prematurely");
        if (!found->phase_ready_tick || *found->phase_ready_tick >= tick) {
            waiting = true;
            continue;
        }
        const auto &request = *found->submitted_request;
        const auto &context = found->request_context;
        const auto &step = context.step();
        const uint32_t iteration = step.phase == mesh_abi::kPhasePREFILL ? 0 : context.phaseIndex() - 1;
        const uint32_t begin = step.phase == mesh_abi::kPhasePREFILL ?
            step.kv_tokens_before : found->output_tokens_committed;
        fatal_if(step.phase != batch.phase || iteration != batch.decodeIteration ||
            begin != member.sequenceBegin || step.valid_tokens_per_member != member.validTokens ||
            request.logicalSourceRank != member.logicalSourceRank ||
            profile->request_program_id != request.programId ||
            profile->request_profile_id != request.profileId ||
            profile->path_kind != context.identity().path_kind ||
            profile->phase != step.phase || profile->kv_tokens_before != step.kv_tokens_before ||
            profile->valid_tokens_per_member != step.valid_tokens_per_member ||
            profile->decode_chunk_tokens != step.decode_chunk_tokens,
            "E_BATCH_REPLAY_DIVERGENCE: READY member differs from its recorded phase or token interval");
        decision.members.push_back(request_id);
    }
    if (waiting)
        return {};
    decision.action = SchedulerAction::Run;
    decision.memberCount = decision.members.size();
    decision.phase = batch.phase == mesh_abi::kPhasePREFILL ? SchedulerPhase::Prefill :
        batch.phase == mesh_abi::kPhaseDECODE ? SchedulerPhase::Decode : SchedulerPhase::Publish;
    return decision;
}
}

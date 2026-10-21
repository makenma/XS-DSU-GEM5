#include "dev/ai_mesh/serving_mesh_executor.hh"
#include "dev/ai_mesh/serving_fill_inputs.hh"

#include <algorithm>
#include <map>
#include <set>
#include <utility>

#include "base/logging.hh"
#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/agent_surrogate_codec.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/npu_serving_frontend_kv_map.hh"
#include "dev/ai_mesh/serving_host_bindings.hh"
#include "dev/ai_mesh/mesh_serving_tensor_bindings.hh"

namespace gem5
{
namespace ai_mesh
{

const ProfileExecutionView &
ServingMeshExecutor::executionView(const mesh_abi::AgentInstanceProfile &profile) const
{
    const auto *view = execution_views->forInstance(profile.mesh_entrypoint_id,
                                                   profile.mesh_profile_id);
    fatal_if(!view, "serving profile has no admitted execution view");
    return *view;
}

ServingMeshExecutor::ServingMeshExecutor(std::shared_ptr<const MeshProgramAdmission> admission_ref,
                                         MeshKvManager &kv_ref,
                                         const ServingCapacityRequirement &capacity,
                                         uint32_t context_capacity)
    : program_admission(std::move(admission_ref)), program(nullptr),
      execution_views(nullptr), kv(&kv_ref), max_contexts(context_capacity)
{
    fatal_if(!program_admission, "serving executor requires an admitted program");
    program = &program_admission->program();
    execution_views = &program_admission->executionViews();
    batches.reserve(context_capacity);
    for (uint32_t i = 0; i < context_capacity; ++i)
        batches.push_back(std::make_unique<BatchContext>(capacity));
    fatal_if(!kv->attachEdgeObserver(*this), "KV edge owner is already attached");
    scheduler_limits.maxActiveSequences = UINT32_MAX;
    scheduler_limits.maxBatchTokens = UINT64_MAX;
    scheduler_limits.maxPrefillTokensPerBatch = UINT64_MAX;
    scheduler_limits.maxDecodeSequencesPerBatch = UINT32_MAX;
}

ServingMeshExecutor::~ServingMeshExecutor()
{
    fatal_if(!kv->detachEdgeObserver(*this), "KV edge owner changed during execution");
}

void
ServingMeshExecutor::onKvEdgeCommitted(const KvEdgeResult &result)
{
    if (result.fatal)
        return;
    last_tick = std::max(last_tick, result.commit_tick);
    for (const auto &[request_id, terminal] : result.terminals) {
        const auto found = slots.find(request_id);
        if (found != slots.end() && !found->second.kv_terminal_snapshot) {
            found->second.kv_terminal_snapshot = terminal;
            found->second.terminal_tick = result.commit_tick;
        }
    }
    for (const auto &[request_id, outcome] : result.admissions) {
        const auto found = slots.find(request_id);
        if (found == slots.end() || outcome != KvAdmissionOutcome::Claimed)
            continue;
        auto &current = found->second;
        if (!current.ownership_claimed) {
            current.ownership_claimed = true;
            current.request_start_tick = result.commit_tick;
            if (sink_ != nullptr)
                sink_->onSessionAdmitted(request_id);
        }
    }
    bool ready = false;
    for (const auto &[request_id, token] : result.handoffs) {
        const auto found = slots.find(request_id);
        if (found == slots.end())
            continue;
        auto &current = found->second;
        current.rollback_token = token;
        const auto path = kv->admittedPath(token);
        std::string reason;
        if (!path || !current.request_context.resolve(*path, reason)) {
            markFailed(current, reason.empty() ? "KV handoff has no admitted path" : reason);
            continue;
        }
        current.first_phase_pending = true;
        current.phase_ready_tick = result.commit_tick;
        ready = ready || current.submitted_request.has_value();
    }
    for (const auto &[request_id, outcome] : result.admissions) {
        const auto found = slots.find(request_id);
        if (found == slots.end() || found->second.failed_flag ||
                found->second.terminal_outcome ||
                outcome == KvAdmissionOutcome::Waiting ||
                outcome == KvAdmissionOutcome::Backpressure)
            continue;
        const auto promotion = result.promotions.find(request_id);
        if (outcome == KvAdmissionOutcome::Claimed &&
                (promotion == result.promotions.end() ||
                 promotion->second == KvPromotionOutcome::WaitingSlot ||
                 promotion->second == KvPromotionOutcome::Pinned))
            continue;
        const auto mapping = mapKvAdmissionOutcome(outcome,
            promotion == result.promotions.end() ? std::nullopt
                : std::optional<KvPromotionOutcome>(promotion->second));
        fatal_if(mapping.protocol_error, "unmapped KV admission outcome");
        auto &current = found->second;
        current.admission_reject_detail = mapping.detail;
        markFailed(current, "KV admission rejected by the committed edge");
    }
    const bool resources_changed = !result.terminals.empty() ||
        !result.handoffs.empty() || !result.releases.empty() ||
        !result.evictions_started.empty() || !result.evictions_completed.empty() ||
        !result.ownerless_cancels.empty() ||
        std::any_of(result.admissions.begin(), result.admissions.end(),
            [](const auto &entry) {
                return entry.second != KvAdmissionOutcome::Claimed &&
                    entry.second != KvAdmissionOutcome::Waiting &&
                    entry.second != KvAdmissionOutcome::Backpressure;
            });
    for (const auto &[request_id, current] : slots)
        if (resources_changed && current.request_context.usesKv() && current.submitted_request &&
                !current.failed_flag && !current.phase_finished && !current.terminal_outcome &&
                !current.kv_terminal_snapshot && !kv->hasPin(request_id) &&
                std::find(result.ownerless_cancels.begin(),
                          result.ownerless_cancels.end(), request_id) ==
                    result.ownerless_cancels.end())
            ready = true;
    if (ready)
        requestNextQuantum(result.commit_tick);
    if (release_outcome_sink && !result.releases.empty())
        release_outcome_sink(result);
}

uint64_t
ServingMeshExecutor::planOutputBytes(const RequestSlot &current) const
{
    for (const auto &item : current.request_context.plan())
        if (item.phase == mesh_abi::kPhasePUBLISH)
            return item.host_output_dma_bytes_per_member;
    return 0;
}

uint32_t
ServingMeshExecutor::rejectDetailOf(const std::string &reason)
{
    static const std::pair<const char *, uint32_t> table[] = {
        {"E_REQUEST_PROFILE", agent_abi::E_REQUEST_PROFILE},
        {"E_KV_STATE", agent_abi::E_KV_STATE},
        {"E_KV_TOKEN_MISMATCH", agent_abi::E_KV_TOKEN_MISMATCH},
        {"E_KV_CONTRACT_MISMATCH", agent_abi::E_KV_CONTRACT_MISMATCH},
        {"E_KV_REUSE_REQUIRED", agent_abi::E_KV_REUSE_REQUIRED},
        {"E_KV_FLAG_COMBINATION", agent_abi::E_KV_FLAG_COMBINATION},
        {"E_BINDING_ROLE", agent_abi::E_BINDING_ROLE},
        {"E_BINDING_ALIAS_MISMATCH", agent_abi::E_BINDING_ALIAS_MISMATCH},
        {"E_HOST_IO_SIZE_MISMATCH", agent_abi::E_HOST_IO_SIZE_MISMATCH},
    };
    for (const auto &entry : table)
        if (reason.rfind(entry.first, 0) == 0)
            return entry.second;
    return agent_abi::E_KV_STATE;
}

NpuAdmission
ServingMeshExecutor::submit(const NpuExecutionRequest &request)
{
    NpuAdmission admission;
    const ServingReplayIdentity replay_identity{request.workloadPlanItemId, request.userId,
        request.taskSeq, request.repairRound};
    const bool replay_member = batch_replay && std::any_of(
        batch_replay->batches.begin() + replay_cursor, batch_replay->batches.end(),
        [&](const auto &batch) {
            return std::any_of(batch.members.begin(), batch.members.end(),
                [&](const auto &member) { return member.identity == replay_identity; });
        });
    if (request.batchReplay != replay_member) {
        admission.rejectDetail = agent_abi::E_BATCH_REPLAY_DIVERGENCE;
        return admission;
    }
    if (slots.count(request.requestId)) {
        admission.rejectDetail = agent_abi::E_KV_STATE;
        return admission;
    }
    fatal_if(slots.size() >= max_contexts, "E_CAPACITY_PLAN: serving request contexts exhausted");
    ServingRequestIdentity identity;
    identity.request_id = request.requestId;
    identity.session_id = request.sessionId;
    identity.kv_handle = request.kvHandle;
    identity.generation = request.generation;
    identity.program_id = request.programId;
    identity.profile_id = request.profileId;
    identity.logical_source_rank = request.logicalSourceRank;
    identity.requested_profile_key = request.requestedProfileKey;
    identity.input_bytes = request.inputBytes;
    identity.input_tokens = request.inputTokens;
    identity.output_tokens = request.outputTokens;
    identity.output_capacity_bytes = request.outputCapacityBytes;
    const ServingRequestContext mode(*program, identity);
    identity.kv_bytes_per_token = mode.usesKv() ? kv->geometry().bytes_per_token : 0;
    identity.contract_digest = request.contractDigest;
    identity.cached_tokens = request.cachedTokens;
    identity.kv_flags = request.kvFlags;
    identity.repair_round = request.repairRound;
    identity.deadline_tick = request.deadlineTick;
    identity.qos = request.qos;
    RequestSlot prepared(*program, identity);
    std::string reason;
    const uint64_t tick = tick_source ? tick_source() : last_tick;
    if (!verifyHostBindings(request, reason)) {
        admission.rejectDetail = rejectDetailOf(reason);
        return admission;
    }
    if (!prepareRequest(prepared, tick, reason)) {
        admission.rejectDetail = prepared.request_context.rejectDetail() != 0
            ? prepared.request_context.rejectDetail() : rejectDetailOf(reason);
        return admission;
    }
    RequestSlot &current = slots.emplace(request.requestId, std::move(prepared)).first->second;
    if (!admit(current, tick, reason) && !current.ownership_claimed) {
        admission.rejectDetail = current.admission_reject_detail.value_or(
            static_cast<agent_abi::DetailCode>(rejectDetailOf(reason)));
        return admission;
    }
    current.submitted_request = request;
    admission.admitted = true;
    if (current.terminal_outcome)
        postCompletion(request);
    else
        requestNextQuantum(tick);
    return admission;
}

void
ServingMeshExecutor::retireCompletedRequest(uint64_t request_id)
{
    const auto found = slots.find(request_id);
    if (found == slots.end())
        return;
    fatal_if(!found->second.terminal_outcome || kv->hasPin(request_id) ||
             kv->hasClaim(request_id), "retiring a live serving request");
    if (found->second.submitted_request && found->second.submitted_request->batchReplay) {
        const auto &request = *found->second.submitted_request;
        onReplayMemberTerminal({request.workloadPlanItemId, request.userId, request.taskSeq, request.repairRound});
    }
    slots.erase(found);
}

void
ServingMeshExecutor::finishServingRun()
{
    if (run_finished)
        return;
    fatal_if(batch_replay && replay_cursor != batch_replay->batches.size(),
             "E_BATCH_REPLAY_DIVERGENCE: run ended before replay was consumed");
    fatal_if(std::any_of(batches.begin(), batches.end(),
        [](const auto &candidate) { return candidate->draining(); }) || std::any_of(slots.begin(), slots.end(),
        [](const auto &entry) { return !entry.second.terminal_outcome; }),
        "serving run finished with live request work");
    for (auto &candidate : batches)
        candidate->release();
    run_finished = true;
    if (mesh_dispatch)
        mesh_dispatch->finishExecution();
}

NpuExecutionCompletion
ServingMeshExecutor::completionFor(const NpuExecutionRequest &request) const
{
    NpuExecutionCompletion completion;
    const auto found = slots.find(request.requestId);
    if (found == slots.end() ||
            found->second.request_context.identity().generation != request.generation)
        return completion;
    const RequestSlot &current = found->second;
    completion.outcome = current.terminal_outcome.value_or(NpuExecutionOutcome::Pending);
    completion.infrastructureFailure = current.infrastructure_failure;
    completion.failure = current.batch_failure;
    completion.terminalPosition = current.cancel_position;
    completion.coreStarted = current.started;
    if (completion.outcome == NpuExecutionOutcome::Error)
        completion.admissionRejectDetail = current.admission_reject_detail;
    completion.phaseInstances = current.phases_committed;
    completion.outputBytes = current.output_bytes_committed;
    completion.outputTokens = current.output_tokens_committed;
    completion.kvTerminal = current.kv_terminal_snapshot;
    completion.requestStartTick = current.request_start_tick;
    completion.terminalTick = current.terminal_tick;
    if (current.terminal_outcome && current.request_context.usesKv() && digest_source)
        completion.semanticDigest = digest_source(request, current.output_tokens_committed);
    return completion;
}

bool
ServingMeshExecutor::bindPhaseFillContent(RequestSlot &current, const NpuExecutionRequest &request,
                                          uint32_t ordinal, std::string &reason)
{
    if (!current.request_context.usesKv() || current.request_context.finished() || mesh_dispatch == nullptr)
        return true;
    const auto &batch = batchFor(current);
    const auto &step = current.request_context.step();
    const auto *profile = current.request_context.requestProfile();
    if (profile == nullptr) {
        reason = "E_REQUEST_PROFILE: fill has no resolved request profile";
        return false;
    }
    std::vector<uint8_t> content;
    const auto &member = program->transport.agent_instance_member_bindings.at(
        batch.profile->member_binding_first + ordinal);
    const auto source = program_admission->context().symbolBinding(
        batch.profile->mesh_entrypoint_id, batch.profile->mesh_profile_id,
        step.phase == mesh_abi::kPhasePUBLISH ? member.static_output_symbol_id :
                                              member.static_kv_symbol_id);
    if (!source) {
        reason = "E_BINDING_ROLE: runtime fill member has no admitted symbol";
        return false;
    }
    if (step.phase == mesh_abi::kPhasePUBLISH) {
        if (!current.digest_frozen) {
            const uint64_t bytes = planOutputBytes(current);
            if (!digest_source || bytes == 0) {
                reason = "E_BINDING_ROLE: PUBLISH has no semantic digest source";
                return false;
            }
            current.frozen_digest = digest_source(request, request.maxOutputTokens);
            current.digest_frozen = true;
            mesh_dispatch->bindSemanticDigest(current.frozen_digest);
        }
        content = surrogateOutputBytes(current.frozen_digest.data(), planOutputBytes(current));
    } else {
        const uint32_t input_tokens = current.request_context.plan().front().cached_tokens_after;
        const uint32_t token_bytes = profile->kv_bytes_per_token;
        content = agentInputSurrogateBytes(request.inputDigest.data(),
                                          uint64_t(input_tokens) * token_bytes);
        const auto seed = surrogateSeed(request.inputDigest.data(), request.programId,
                                       request.profileId, request.requestedProfileKey);
        for (uint32_t token = input_tokens; token < step.cached_tokens_after; ++token) {
            const auto digest = surrogateTokenDigest(seed.data(), token - input_tokens);
            const auto bytes = surrogateOutputBytes(digest.data(), token_bytes);
            content.insert(content.end(), bytes.begin(), bytes.end());
        }
    }
    const auto &view = executionView(*batch.profile);
    std::set<uint32_t> command_ids;
    for (const auto &range : view.ranges)
        command_ids.insert(range.command_ids.begin(), range.command_ids.end());
    std::vector<ServingFillBinding> bindings;
    for (const auto &descriptor : program->transport.dma_descriptors) {
        if (descriptor.kind != mesh_abi::kDmaKindLOCAL_FILL ||
                command_ids.count(descriptor.command_id) == 0)
            continue;
        const auto command = std::find_if(program->transport.commands.begin(), program->transport.commands.end(),
            [&](const auto &item) { return item.command_id == descriptor.command_id; });
        if (command != program->transport.commands.end() &&
                program->transport.op_attrs.at(command->attr_index - 1).as<mesh_abi::FillV1>()->pattern !=
                    mesh_abi::kDmaFillRuntimeBoundSentinel)
            continue;
        if (command == program->transport.commands.end() || command->operand_count == 0) {
            reason = "E_BINDING_ROLE: runtime fill has no matching source tensor";
            return false;
        }
        const auto owner = servingFillMember(*program, view,
            batch.bindings.executionBinding(), descriptor.command_id);
        if (!owner) {
            reason = "E_BINDING_ROLE: runtime fill has no unique frozen member";
            return false;
        }
        if (owner->ordinal != ordinal)
            continue;
        if (descriptor.dst.tensor_id != source->tensor->tensor_id) {
            reason = "E_BINDING_ROLE: runtime fill differs from its admitted member tensor";
            return false;
        }
        ServingFillBinding binding;
        binding.request_id = request.requestId;
        binding.request_generation = request.generation;
        binding.instance_profile_id = batch.profile->instance_profile_id;
        binding.member_ordinal = ordinal;
        binding.producer_command_id = descriptor.command_id;
        binding.allocation_id = program->transport.command_operands.at(
            command->operand_begin + command->operand_count - 1).allocation_id;
        const auto allocation = std::find_if(
            program->transport.allocations.begin(), program->transport.allocations.end(),
            [&](const auto &item) { return item.allocation_id == binding.allocation_id; });
        const auto *destination = program_admission->dma().descriptorBinding(
            descriptor.descriptor_id, false);
        if (allocation == program->transport.allocations.end() || !destination ||
                destination->slotId()) {
            reason = "E_BINDING_ROLE: runtime fill allocation is missing";
            return false;
        }
        for (uint32_t row = 0; row < descriptor.rows; ++row) {
            const uint64_t offset = destination->begin() +
                uint64_t(row) * descriptor.dst_stride_bytes;
            if (offset > content.size() || descriptor.row_bytes > content.size() - offset) {
                reason = "E_BINDING_ROLE: runtime fill leaves its content range";
                return false;
            }
            binding.content.insert(binding.content.end(), content.begin() + offset,
                                   content.begin() + offset + descriptor.row_bytes);
        }
        bindings.push_back(std::move(binding));
    }
    for (const auto &binding : bindings)
        mesh_dispatch->bindFillContent(binding);
    return true;
}

bool
ServingMeshExecutor::verifyHostBindings(const NpuExecutionRequest &request,
                                        std::string &reason) const
{
    std::vector<mesh_abi::AgentRequestBindingRequirement> requirements;
    for (const auto &requirement : program->transport.agent_request_binding_requirements)
        if (requirement.request_program_id == request.programId &&
                requirement.request_profile_id == request.profileId)
            requirements.push_back(requirement);
    if (requirements.empty())
        return true;
    if (request.hostBindings.empty()) {
        reason = "E_BINDING_ROLE: the admitted request carries no proof table";
        return false;
    }
    std::string detail;
    if (verifyHostBindingRequirements(
            request.hostBindings, requirements, request.inputAddress,
            request.inputBytes, request.outputAddress,
            request.outputCapacityBytes, request.metadataAddress,
            request.metadataCapacityBytes, kv->geometry().region_base,
            kv->geometry().regionBytes(), kv->geometry().slot_bytes,
            detail) != HostBindingVerdict::Match) {
        reason = detail;
        return false;
    }
    for (const auto &binding : request.hostBindings)
        if (binding.kind == agent_abi::kBindingKindREQUEST_WORKSPACE &&
                binding.bytes != requestWorkspaceBytes(*program, request.programId, request.profileId)) {
            reason = "E_BINDING_ROLE: workspace capacity differs from its admitted mappings";
            return false;
        }
    struct Range {
        uint64_t begin, end;
        bool workspace;
    };
    const auto ranges = [](const NpuExecutionRequest &owner) {
        std::vector<Range> result;
        for (const auto &binding : owner.hostBindings)
            if (binding.kind != agent_abi::kBindingKindKV_EXTERNAL)
                result.push_back({binding.address, binding.address + binding.bytes,
                    binding.kind == agent_abi::kBindingKindREQUEST_WORKSPACE});
        result.push_back({owner.metadataAddress, owner.metadataAddress + owner.metadataCapacityBytes, false});
        return result;
    };
    const auto incoming = ranges(request);
    for (const auto &[id, current] : slots) {
        if (!current.submitted_request)
            continue;
        for (const auto &held : ranges(*current.submitted_request))
            for (const auto &candidate : incoming)
                if ((held.workspace || candidate.workspace) &&
                        candidate.begin < held.end && held.begin < candidate.end) {
                    reason = "E_BINDING_ALIAS_MISMATCH: workspace overlaps an unretired request range";
                    return false;
                }
    }
    return true;
}

void
ServingMeshExecutor::reportCompletion(RequestSlot &current, NpuExecutionOutcome outcome)
{
    if (current.terminal_outcome)
        return;
    fatal_if(outcome == NpuExecutionOutcome::Pending, "cannot publish pending completion");
    if (ownsBatch(current)) {
        const auto &output = batchFor(current).bindings.executionBinding().outputLedger();
        if (output)
            current.output_bytes_committed = output->prefix(current.request_context.identity().request_id);
    }
    fatal_if(outcome == NpuExecutionOutcome::Success &&
                 current.output_bytes_committed != planOutputBytes(current),
             "PUBLISH success has an incomplete committed output prefix");
    current.terminal_outcome = outcome;
    if (!current.kv_terminal_snapshot)
        current.terminal_tick = last_tick;
    if (current.submitted_request)
        postCompletion(*current.submitted_request);
    if (std::any_of(slots.begin(), slots.end(), [](const auto &entry) {
            return entry.second.submitted_request && !entry.second.terminal_outcome;
        }))
        requestNextQuantum(last_tick);
}

bool
ServingMeshExecutor::sealTerminalSnapshot(RequestSlot &current, KvTerminalStatus status, uint64_t tick,
                                          std::string &reason)
{
    const ServingRequestIdentity identity = current.request_context.identity();
    if (!current.request_context.usesKv()) {
        if (!terminalJoinSatisfied(current)) {
            reason = "E_REQUEST_PROFILE: request ended before instance drain";
            return false;
        }
        last_tick = tick;
        return true;
    }
    if (!current.kv_terminal_snapshot.has_value() &&
            (kv->hasPin(identity.request_id) ||
             kv->hasClaim(identity.request_id))) {
        KvEdgeInputs edge;
        edge.tick = tick;
        edge.owner_terminals.push_back(
            current.request_context.ownerTerminal(status));
        KvEdgeResult result;
        if (!commitEdge(edge, result, reason))
            return false;
    }
    if (!current.kv_terminal_snapshot.has_value()) {
        reason = "E_KV_STATE: request ended without an owner terminal";
        return false;
    }
    const auto &snapshot = *current.kv_terminal_snapshot;
    if (snapshot.request_id != identity.request_id ||
            snapshot.session_id != identity.session_id ||
            snapshot.kv_handle != identity.kv_handle ||
            snapshot.generation != identity.generation ||
            snapshot.contract_digest != identity.contract_digest) {
        reason = "E_KV_STATE: terminal snapshot belongs to another owner";
        return false;
    }
    if (kv->hasPin(identity.request_id) || kv->hasClaim(identity.request_id)) {
        reason = "E_KV_STATE: KV ownership survived the owner terminal";
        return false;
    }
    last_tick = tick;
    return true;
}

bool
ServingMeshExecutor::terminalJoinSatisfied(const RequestSlot &current) const
{
    if (ownsBatch(current) && batchFor(current).draining())
        return false;
    const ServingRequestIdentity identity = current.request_context.identity();
    if (current.request_context.usesKv() &&
            !kv->kvWorkDrained(identity.session_id, identity.kv_handle))
        return false;
    for (size_t index = 0; index < current.kv_accepted.size(); ++index)
        if (current.kv_accepted[index] && !current.kv_terminal[index])
            return false;
    return true;
}

void
ServingMeshExecutor::commitPendingFault(RequestSlot &current)
{
    if (!current.pending_fault.has_value())
        return;
    const ServingRequestIdentity identity = current.request_context.identity();
    if (!kv->kvWorkDrained(identity.session_id, identity.kv_handle))
        return;
    KvEdgeInputs edge;
    edge.tick = last_tick;
    edge.faults.push_back(*current.pending_fault);
    KvEdgeResult result;
    std::string reason;
    if (!commitEdge(edge, result, reason))
        return;
    current.pending_fault.reset();
}

void
ServingMeshExecutor::settleDeferredTerminal(RequestSlot &current)
{
    if (current.terminal_outcome || !current.ownership_claimed || !current.failed_flag)
        return;
    // Every issued descriptor being terminal is what lets the append close
    // out; descriptors that were never issued can never terminalize, so they
    // must not keep the append (and the KV drain) open.
    if (current.append_ledger) {
        bool issued_terminal = true;
        for (size_t index = 0; index < current.kv_accepted.size(); ++index)
            if (current.kv_accepted[index] && !current.kv_terminal[index])
                issued_terminal = false;
        if (issued_terminal) {
            std::string close_reason;
            finishAppend(current, last_tick, close_reason);
        }
    }
    commitPendingFault(current);
    if (!current.kv_terminal_snapshot.has_value() && !terminalJoinSatisfied(current)) {
        // The owner terminal is join-gated.  Wait for the legal drain facts
        // instead of probing the manager with one it would reject.
        current.terminal_deferred = true;
        return;
    }
    std::string reason;
    const auto status = failureTerminalStatus(current);
    if (!sealTerminalSnapshot(current, status, last_tick, reason)) {
        current.terminal_deferred = true;
        return;
    }
    current.terminal_deferred = false;
    reportCompletion(current, status == KvTerminalStatus::Cancelled ?
        NpuExecutionOutcome::Cancelled : NpuExecutionOutcome::Error);
}

KvTerminalStatus
ServingMeshExecutor::failureTerminalStatus(const RequestSlot &current) const
{
    if (current.batch_failure)
        return current.cancelled_before_batch_failure ?
            KvTerminalStatus::Cancelled : KvTerminalStatus::Error;
    const auto error_tick = current.first_error_tick;
    return current.cancel_tick && error_tick && *current.cancel_tick < *error_tick
        ? KvTerminalStatus::Cancelled : KvTerminalStatus::Error;
}

bool ServingMeshExecutor::latchRunFailure(RequestSlot &current, const std::string &reason)
{
    if (current.failed_flag || current.terminal_outcome)
        return false;
    current.failed_flag = true;
    if (!current.first_error_tick)
        current.first_error_tick = last_tick;
    current.phase_ready_tick.reset();
    if (current.failure_reason.empty())
        current.failure_reason = reason;
    if (current.first_phase_pending && current.rollback_token) {
        std::string close_reason;
        rollbackPrestart(current, failureTerminalStatus(current), close_reason);
    } else if (current.request_context.usesKv() && !current.started &&
            kv->hasAdmissionWaiter(current.request_context.identity().request_id)) {
        std::string close_reason;
        terminalizePendingAdmission(current, failureTerminalStatus(current), close_reason);
    } else if (mesh_dispatch != nullptr && ownsBatch(current)) {
        const auto &owned_batch = batchFor(current);
        if (owned_batch.draining()) {
            if (!current.batch_failure) {
                const auto &identity = current.request_context.identity();
                const auto member = std::find_if(owned_batch.members.begin(), owned_batch.members.end(),
                    [&](const auto &row) { return row.request_id == identity.request_id; });
                fatal_if(member == owned_batch.members.end(), "Failure join has no frozen member owner");
                ErrorCandidate failure;
                failure.tick = last_tick;
                failure.code = agent_abi::E_DCORE_ENGINE;
                auto &source = failure.source;
                source.error_class = mesh_abi::kMoeErrorClassINTERNAL;
                source.core_id_or_ffff = UINT16_MAX;
                source.domain = current.request_context.usesKv() ?
                    mesh_abi::kMeshObjectDomainKV_RUNTIME : mesh_abi::kMeshObjectDomainINTERNAL;
                source.object_kind = current.request_context.usesKv() ?
                    mesh_abi::kErrorSourceObjectKindKV_APPEND : mesh_abi::kErrorSourceObjectKindINTERNAL_CHECK;
                if (current.request_context.usesKv()) {
                    source.region_group_id = current.request_context.phaseIndex();
                    source.region_id = member->ordinal;
                    source.ordinal = 1;
                    source.generation = identity.generation;
                    agent_abi::wrU64(source.aux_key.data(), identity.request_id);
                    agent_abi::wrU64(source.aux_key.data() + 8, owned_batch.selected_instance->value());
                } else {
                    source.ordinal = mesh_abi::kInternalCheckSiteLEDGER;
                }
                mesh_dispatch->failCurrentInstance(failure);
            }
        } else if (owned_batch.selected_instance &&
                 !owned_batch.armed_instance_generation)
            cancelPendingInstance(current);
    }
    return true;
}

bool ServingMeshExecutor::markFailed(RequestSlot &current, const std::string &reason)
{
    if (!latchRunFailure(current, reason))
        return false;
    if (current.ownership_claimed) {
        settleDeferredTerminal(current);
        return false;
    }
    reportCompletion(current, failureTerminalStatus(current) == KvTerminalStatus::Cancelled ?
        NpuExecutionOutcome::Cancelled : NpuExecutionOutcome::Error);
    return false;
}

SchedulerDecision
ServingMeshExecutor::selectBatch(uint64_t tick) const
{
    uint64_t frozen_members = 0;
    for (const auto &batch : batches)
        frozen_members += batch->members.size();
    if (strict_replay && frozen_members)
        return {};
    if (strict_replay && !mesh_dispatch->replayQuiescent()) {
        SchedulerDecision wait;
        wait.action = SchedulerAction::Wait;
        fatal_if(tick > UINT64_MAX - clock_period_ticks, "E_BATCH_REPLAY_DIVERGENCE: replay barrier tick overflow");
        wait.waitUntil = tick + clock_period_ticks;
        return wait;
    }
    if (frozen_members >= scheduler_limits.maxActiveSequences)
        return {};
    const uint64_t available_members = scheduler_limits.maxActiveSequences - frozen_members;
    if (batch_replay) {
        auto replay = selectReplayBatch(tick, available_members);
        if (replay.action != SchedulerAction::Idle)
            return replay;
    }
    std::vector<SchedulerMember> members;
    for (const auto &[id, current] : slots) {
        if (current.failed_flag || current.terminal_outcome || !current.submitted_request ||
                !current.phase_ready_tick || *current.phase_ready_tick >= tick)
            continue;
        const auto &request = *current.submitted_request;
        if (request.batchReplay)
            continue;
        const auto &step = current.request_context.step();
        SchedulerMember member;
        member.requestId = id;
        member.logicalSourceRank = request.logicalSourceRank;
        switch (step.phase) {
          case mesh_abi::kPhasePREFILL: member.phase = SchedulerPhase::Prefill; break;
          case mesh_abi::kPhaseDECODE: member.phase = SchedulerPhase::Decode; break;
          case mesh_abi::kPhasePUBLISH: member.phase = SchedulerPhase::Publish; break;
          default: fatal("E_REQUEST_PROFILE: unknown serving phase");
        }
        member.phaseReadyTick = *current.phase_ready_tick;
        member.deadlineTick = request.deadlineTick;
        member.qos = request.qos;
        member.programId = request.programId;
        member.requestProfileId = request.profileId;
        member.pathKind = current.request_context.identity().path_kind;
        member.validTokensPerMember = step.valid_tokens_per_member;
        member.kvTokensBefore = step.kv_tokens_before;
        member.decodeChunkTokens = step.decode_chunk_tokens;
        members.push_back(member);
    }
    return servingSchedulerDecide(members, scheduler_limits, tick,
        [this, available_members](const ServingBatchKey &key) {
            uint16_t phase = 0;
            switch (key.phase) {
              case SchedulerPhase::Prefill: phase = mesh_abi::kPhasePREFILL; break;
              case SchedulerPhase::Decode: phase = mesh_abi::kPhaseDECODE; break;
              case SchedulerPhase::Publish: phase = mesh_abi::kPhasePUBLISH; break;
            }
            std::vector<uint32_t> counts;
            for (const auto &instance : program->transport.agent_instance_profiles)
                if (instance.request_program_id == key.programId &&
                        instance.request_profile_id == key.requestProfileId &&
                        instance.path_kind == key.pathKind && instance.phase == phase &&
                        instance.kv_tokens_before == key.kvTokensBefore &&
                        instance.valid_tokens_per_member == key.validTokensPerMember &&
                        instance.decode_chunk_tokens == key.decodeChunkTokens &&
                        instance.member_count <= available_members)
                    counts.push_back(instance.member_count);
            return counts;
        }, clock_period_ticks);
}

void
ServingMeshExecutor::cancelPendingInstance(RequestSlot &current)
{
    if (!ownsBatch(current) || !batchFor(current).selected_instance)
        return;
    if (mesh_dispatch != nullptr)
        mesh_dispatch->cancelPendingInstance(batchFor(current).selected_instance->value());
    batchFor(current).selected_instance.reset();
}

bool ServingMeshExecutor::rollbackPrestart(RequestSlot &current, KvTerminalStatus status,
                                           std::string &reason)
{
    if (!current.first_phase_pending || !current.rollback_token) {
        reason = "E_KV_STATE: abort outside an armed request";
        return false;
    }
    cancelPendingInstance(current);
    current.first_phase_pending = false;
    KvEdgeInputs edge;
    edge.tick = last_tick;
    edge.owner_terminals.push_back(
        current.request_context.rollbackTerminal(status, *current.rollback_token));
    KvEdgeResult result;
    if (!commitEdge(edge, result, reason))
        return false;
    current.rollback_token.reset();
    current.phase_append_tokens = 0;
    current.clearKvDescriptors();
    return true;
}

bool ServingMeshExecutor::commitEdge(KvEdgeInputs &edge, KvEdgeResult &result,
                                     std::string &reason)
{
    edge.tick = edge.tick == 0 ? last_tick : edge.tick;
    result = kv->commitEdge(edge);
    if (result.fatal) {
        reason = "E_KV_STATE: " + *result.fatal;
        return false;
    }
    return true;
}

bool ServingMeshExecutor::submitPhaseEdge(BatchContext &batch, uint64_t tick, std::string &reason)
{
    if (!mesh_dispatch || !batch.profile || batch.members.empty()) {
        reason = "E_REQUEST_PROFILE: phase has no dispatcher or frozen population";
        return false;
    }
    KvEdgeInputs edge;
    edge.tick = tick;
    std::vector<ServingBatchMember> members;
    members.reserve(batch.members.size());
    for (const auto &identity : batch.members) {
        auto &current = slot(identity.request_id);
        current.phase_ready_tick.reset();
        current.request_context.beginPhase(edge, identity.ordinal);
        const bool uses_kv = current.request_context.usesKv();
        current.phase_append_tokens = uses_kv ? batch.profile->kv_write_bytes_per_member /
            current.request_context.requestProfile()->kv_bytes_per_token : 0;
        const auto owned_slot = uses_kv ? kv->ownedSlot(identity.request_id) : std::nullopt;
        if (!current.submitted_request || (uses_kv && !owned_slot)) {
            current.infrastructure_failure = agent_abi::E_AGENT_PROTOCOL_FATAL;
            reason = "E_AGENT_PROTOCOL_FATAL: frozen member lacks a request or owned KV slot";
            return false;
        }
        ServingBatchMember member;
        member.request = &*current.submitted_request;
        member.phase_ordinal = current.request_context.phaseIndex();
        member.logical_source_rank = member.request->logicalSourceRank;
        if (owned_slot)
            member.owned_slot = *owned_slot;
        member.path = static_cast<KvPath>(current.request_context.identity().path_kind);
        if (uses_kv && batch.profile->phase != mesh_abi::kPhasePUBLISH)
            member.kv_view = kv->runtimeView(identity.request_id, identity.ordinal);
        members.push_back(member);
    }
    if (batch.bindings.freeze(program_admission, *batch.profile, members.data(), members.size(), reason) !=
            MeshServingBatch::FreezeResult::Frozen) {
        for (const auto &identity : batch.members)
            slot(identity.request_id).infrastructure_failure = agent_abi::E_AGENT_PROTOCOL_FATAL;
        reason = "E_AGENT_PROTOCOL_FATAL: " + reason;
        return false;
    }
    for (const auto &identity : batch.members)
        if (!buildPhaseKvDescriptors(slot(identity.request_id), identity.ordinal, reason))
            return false;
    const auto generation = mesh_dispatch->selectInstance(
        batch.profile->mesh_entrypoint_id, batch.profile->mesh_profile_id, reason);
    if (!generation)
        return false;
    batch.selected_instance = *generation;
    MoePreparationError preparation_error;
    if (!mesh_dispatch->prepareMoe(*batch.profile, members,
            batch.bindings.executionBinding(), batch.overlays, preparation_error))
        batch.preparation_failure = ErrorCandidate{tick, preparation_error.source,
            uint32_t(preparation_error.detail)};
    if (const auto &output = batch.bindings.executionBinding().outputLedger()) {
        output->setSink([this, instance = generation->value(), ledger = output.get()](PublishSegmentFact fact) {
            fact.instance = instance;
            auto &current = slot(fact.request);
            const bool first_chunk = fact.kind == PublishSegmentFact::Kind::Okay &&
                !current.output_chunk_notified && ledger->prefix(fact.request) >=
                    current.request_context.requestProfile()->publish_chunk_bytes;
            current.output_chunk_notified |= first_chunk;
            if (sink_)
                sink_->onPublishSegment(fact, first_chunk);
        });
    }
    latest_instance = std::max(latest_instance, generation->value());
    for (const auto &identity : batch.members) {
        auto &current = slot(identity.request_id);
        if (!bindPhaseFillContent(current, *current.submitted_request, identity.ordinal, reason))
            return false;
    }
    KvEdgeResult result;
    if (slot(batch.members.front().request_id).request_context.usesKv() &&
            !commitEdge(edge, result, reason))
        return false;
    mesh_dispatch->bindInstance(batch.bindings.executionBinding(), batch.overlays);
    for (const auto &identity : batch.members) {
        auto &current = slot(identity.request_id);
        ServingPhaseFact fact;
        fact.request_id = identity.request_id;
        fact.request_generation = identity.generation;
        fact.instance_generation = generation->value();
        fact.phase = batch.profile->phase;
        fact.instance_profile_id = batch.profile->instance_profile_id;
        fact.mesh_profile_id = batch.profile->mesh_profile_id;
        fact.kv_tokens_before = batch.profile->kv_tokens_before;
        fact.append_tokens = current.phase_append_tokens;
        current.phase_facts.push_back(fact);
    }
    return true;
}

bool
ServingMeshExecutor::buildPhaseKvDescriptors(RequestSlot &current, uint32_t ordinal, std::string &reason)
{
    current.clearKvDescriptors();
    if (current.phase_append_tokens == 0)
        return true;
    const mesh_abi::AgentInstanceProfile *instance =
        batchFor(current).profile;
    const mesh_abi::AgentRequestProfile *request =
        current.request_context.requestProfile();
    if (instance == nullptr || request == nullptr) {
        reason = "E_REQUEST_PROFILE: phase instance profile is missing";
        return false;
    }
    if (instance->member_binding_first >=
            program->transport.agent_instance_member_bindings.size()) {
        reason = "E_BINDING_ROLE: member binding span is out of range";
        return false;
    }
    const mesh_abi::AgentInstanceMemberBinding &binding =
        program->transport.agent_instance_member_bindings[
            instance->member_binding_first + ordinal];
    const auto kv_slot = program_admission->context().symbolBinding(
        instance->mesh_entrypoint_id, instance->mesh_profile_id, binding.static_kv_symbol_id);
    if (!kv_slot) {
        reason = "E_RELOCATION: KV member has no admitted slot";
        return false;
    }
    std::set<uint32_t> commands;
    for (const auto &range : executionView(*instance).ranges)
        commands.insert(range.command_ids.begin(), range.command_ids.end());
    const uint32_t token_bytes = request->kv_bytes_per_token;
    const uint32_t base = current.request_context.step().kv_tokens_before;
    const uint64_t append_bytes = uint64_t(current.phase_append_tokens) * token_bytes;
    for (const auto &descriptor : program->transport.dma_descriptors) {
        const auto *endpoint = program_admission->dma().descriptorBinding(descriptor.descriptor_id, false);
        if (descriptor.kind != mesh_abi::kDmaKindSTORE || !endpoint ||
                endpoint->slotId() != kv_slot->slot->slot_id)
            continue;
        if (std::find(commands.begin(), commands.end(),
                      descriptor.command_id) == commands.end())
            continue;
        const auto access = batchFor(current).bindings.executionBinding().resolveDescriptorRows(descriptor.descriptor_id, false);
        if (!access || !access->member) {
            reason = "E_BINDING_ROLE: KV descriptor has no frozen member";
            return false;
        }
        if (access->member->ordinal != ordinal)
            continue;
        const uint64_t region = endpoint->begin();
        const uint64_t base_bytes = uint64_t(base) * token_bytes;
        if (region < base_bytes || descriptor.row_bytes == 0 ||
                descriptor.row_bytes % token_bytes != 0) {
            reason = "E_KV_TOKEN_MISMATCH: KV store is not token aligned";
            return false;
        }
        const uint64_t append_offset = region - base_bytes;
        if (descriptor.rows == 0) {
            reason = "E_KV_TOKEN_MISMATCH: KV store has no rows";
            return false;
        }
        for (uint32_t row = 0; row < descriptor.rows; ++row) {
            const uint64_t row_offset = append_offset +
                uint64_t(row) * descriptor.dst_stride_bytes;
            if (row_offset + descriptor.row_bytes > append_bytes) {
                reason = "E_KV_TOKEN_MISMATCH: KV store row leaves the append "
                         "region";
                return false;
            }
        }
        KvDescriptorBinding item;
        item.command_id = descriptor.command_id;
        item.descriptor_id = descriptor.descriptor_id;
        item.first_token = static_cast<uint32_t>(append_offset / token_bytes);
        item.tokens = static_cast<uint32_t>(
            uint64_t(descriptor.rows) * descriptor.row_bytes / token_bytes);
        item.rows = descriptor.rows;
        item.append_offset = append_offset;
        item.dst_stride_bytes = descriptor.dst_stride_bytes;
        item.row_bytes = descriptor.row_bytes;
        current.kv_descriptors.push_back(item);
    }
    if (current.kv_descriptors.empty()) {
        reason = "E_KV_TOKEN_MISMATCH: append has no KV store descriptor";
        return false;
    }
    current.append_ledger.emplace(current.request_context.identity().request_id,
                          current.request_context.identity().session_id,
                          current.request_context.identity().kv_handle,
                          current.request_context.identity().generation, base,
                          current.phase_append_tokens, token_bytes);
    current.kv_accepted.assign(current.kv_descriptors.size(), false);
    current.kv_terminal.assign(current.kv_descriptors.size(), false);
    return true;
}

bool
ServingMeshExecutor::commitDmaCount(RequestSlot &current, const char *kind, uint32_t count,
                                    uint64_t tick, std::string &reason)
{
    if (count == 0)
        return true;
    KvEdgeInputs edge;
    edge.tick = tick;
    KvDmaCount item;
    item.request_id = current.request_context.identity().request_id;
    item.count = count;
    if (std::string(kind) == "accept")
        edge.dma_accepts.push_back(item);
    else
        edge.dma_terminals.push_back(item);
    KvEdgeResult result;
    return commitEdge(edge, result, reason);
}

void
ServingMeshExecutor::onDmaAccepted(uint32_t descriptor_id, uint32_t command_id,
                                   uint64_t instance_generation,
                                   uint64_t request_id,
                                   uint32_t request_generation, uint64_t tick)
{
    RequestSlot *found = requestForFact(request_id, request_generation, tick);
    if (found == nullptr)
        return;
    RequestSlot &current = *found;
    const InstanceFactVerdict verdict =
        classifyInstanceFact(current, instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (verdict != InstanceFactVerdict::Current) {
        last_tick = std::max(last_tick, tick);
        markFailed(current, "E_KV_STATE: DMA accept from an unknown mesh instance");
        return;
    }
    if (!current.started || current.failed_flag || current.phase_finished || !current.append_ledger)
        return;
    last_tick = tick;
    for (size_t index = 0; index < current.kv_descriptors.size(); ++index) {
        if (current.kv_descriptors[index].command_id != command_id)
            continue;
        if (current.kv_descriptors[index].descriptor_id != descriptor_id) {
            markFailed(current, "E_KV_TOKEN_MISMATCH: KV descriptor identity mismatch");
            return;
        }
        if (current.kv_accepted[index])
            return;
        if (!current.append_ledger->acceptDescriptor(current.kv_descriptors[index].descriptor_id,
                                             current.kv_descriptors[index].first_token,
                                             current.kv_descriptors[index].tokens)) {
            markFailed(current, "E_KV_TOKEN_MISMATCH: KV descriptor is not admissible");
            return;
        }
        current.kv_accepted[index] = true;
        ++current.accepted_kv_descriptors;
        ++current.phase_facts.back().accepted_descriptors;
        std::string reason;
        if (!commitDmaCount(current, "accept", current.append_ledger->acceptDelta(), tick,
                            reason)) {
            markFailed(current, reason);
            return;
        }
        current.append_ledger->clearDeltas();
        return;
    }
}

void
ServingMeshExecutor::onDmaTerminal(
    uint32_t descriptor_id, uint32_t command_id, uint64_t committed_bytes,
    uint32_t error_code, uint64_t instance_generation, uint64_t request_id,
    uint32_t request_generation, uint64_t tick,
    const std::vector<DmaCommittedSegment> &committed_segments)
{
    RequestSlot *found = requestForFact(request_id, request_generation, tick);
    if (found == nullptr)
        return;
    RequestSlot &current = *found;
    const InstanceFactVerdict verdict =
        classifyInstanceFact(current, instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (verdict != InstanceFactVerdict::Current) {
        last_tick = std::max(last_tick, tick);
        markFailed(current, "E_KV_STATE: DMA terminal from an unknown mesh instance");
        return;
    }
    if (!current.started || current.phase_finished || !current.append_ledger)
        return;
    // A failed request still has to see its accepted descriptors terminalize:
    // that drain is what makes the owner terminal legal.
    const bool draining = current.failed_flag;
    last_tick = tick;
    for (size_t index = 0; index < current.kv_descriptors.size(); ++index) {
        if (current.kv_descriptors[index].command_id != command_id)
            continue;
        if (current.kv_descriptors[index].descriptor_id != descriptor_id) {
            markFailed(current, "E_KV_TOKEN_MISMATCH: KV descriptor identity mismatch");
            return;
        }
        if (current.kv_terminal[index])
            return;
        if (!current.kv_accepted[index]) {
            if (draining)
                return;
            markFailed(current, "E_KV_TOKEN_MISMATCH: KV terminal without an accept");
            return;
        }
        const bool all_okay = error_code == 0;
        std::vector<DmaCommittedSegment> segments = committed_segments;
        std::sort(segments.begin(), segments.end(),
                  [](const DmaCommittedSegment &left,
                     const DmaCommittedSegment &right) {
                      if (left.row != right.row)
                          return left.row < right.row;
                      return left.offset_in_row < right.offset_in_row;
                  });
        const uint32_t row_count = current.kv_descriptors[index].rows;
        const uint64_t row_bytes = current.kv_descriptors[index].row_bytes;
        uint64_t descriptor_bytes = uint64_t(row_count) * row_bytes;
        bool segments_valid = true;
        uint64_t unique_covered = 0;
        {
            std::map<uint32_t, std::vector<std::pair<uint64_t, uint64_t>>> rows;
            for (const DmaCommittedSegment &segment : segments) {
                if (segment.row >= row_count || segment.bytes == 0 ||
                        segment.offset_in_row > row_bytes ||
                        segment.bytes > row_bytes - segment.offset_in_row) {
                    segments_valid = false;
                    break;
                }
                rows[segment.row].push_back(
                    {segment.offset_in_row,
                     segment.offset_in_row + segment.bytes});
            }
            for (uint32_t row = 0; row < row_count && segments_valid; ++row) {
                const auto found = rows.find(row);
                if (found == rows.end())
                    continue;
                std::vector<std::pair<uint64_t, uint64_t>> intervals =
                    found->second;
                std::sort(intervals.begin(), intervals.end());
                uint64_t cursor = 0;
                for (const auto &interval : intervals) {
                    if (interval.first < cursor) {
                        segments_valid = false;
                        break;
                    }
                    unique_covered += interval.second - interval.first;
                    cursor = interval.second;
                }
            }
        }
        const bool coverage_valid = segments_valid &&
            unique_covered == committed_bytes &&
            (!all_okay || unique_covered == descriptor_bytes);
        std::optional<ErrorCandidate> candidate;
        if (!all_okay || !coverage_valid) {
            ErrorCandidate fault;
            fault.tick = tick;
            fault.code = agent_abi::E_AXI_RESPONSE;
            const auto descriptor = std::find_if(program->transport.dma_descriptors.begin(), program->transport.dma_descriptors.end(),
                [&](const auto &row) { return row.descriptor_id == descriptor_id; });
            fatal_if(descriptor == program->transport.dma_descriptors.end(), "KV terminal has no static descriptor");
            const auto command = std::find_if(program->transport.commands.begin(), program->transport.commands.end(),
                [&](const auto &row) { return row.command_id == descriptor->command_id; });
            fatal_if(command == program->transport.commands.end(), "KV descriptor has no command owner");
            const auto source = batchFor(current).bindings.executionBinding().kvDescriptorSource(
                descriptor_id, mesh_abi::kMoeErrorClassDMA_AXI, command->core_id, instance_generation);
            fatal_if(!source, "KV descriptor has no frozen error source");
            fault.source = *source;
            candidate = fault;
        }
        for (uint32_t row = 0; row < row_count; ++row) {
            const uint64_t row_offset = current.kv_descriptors[index].append_offset +
                uint64_t(row) * current.kv_descriptors[index].dst_stride_bytes;
            if (!coverage_valid) {
                current.append_ledger->noteBurst(current.kv_descriptors[index].descriptor_id,
                                         row_offset, row_bytes, false,
                                         candidate);
                continue;
            }
            uint64_t cursor = 0;
            for (const DmaCommittedSegment &segment : segments) {
                if (segment.row != row)
                    continue;
                if (cursor < segment.offset_in_row)
                    current.append_ledger->noteBurst(
                        current.kv_descriptors[index].descriptor_id,
                        row_offset + cursor,
                        segment.offset_in_row - cursor, false, candidate);
                current.append_ledger->noteBurst(current.kv_descriptors[index].descriptor_id,
                                         row_offset + segment.offset_in_row,
                                         segment.bytes, true, std::nullopt);
                cursor = segment.offset_in_row + segment.bytes;
            }
            if (cursor < row_bytes)
                current.append_ledger->noteBurst(
                    current.kv_descriptors[index].descriptor_id, row_offset + cursor,
                    row_bytes - cursor, false, candidate);
        }
        current.append_ledger->noteDescriptorTerminal(
            current.kv_descriptors[index].descriptor_id);
        current.kv_terminal[index] = true;
        ++current.terminal_kv_descriptors;
        ++current.phase_facts.back().terminal_descriptors;
        std::string reason;
        if (!commitDmaCount(current, "terminal", current.append_ledger->terminalDelta(), tick,
                            reason)) {
            markFailed(current, reason);
            return;
        }
        current.append_ledger->clearDeltas();
        if (!coverage_valid)
            latchRunFailure(current, "E_KV_TOKEN_MISMATCH: KV terminal bytes disagree "
                            "with its committed segments");
        const bool all_terminal =
            std::all_of(current.kv_terminal.begin(), current.kv_terminal.end(),
                        [](bool done) { return done; });
        if (current.failed_flag) {
            // The drain is the legal route to the owner terminal: the append
            // terminal is a valid drain fact, while no phase may advance.
            // Only issued descriptors can terminalize, so un-issued ones must
            // not keep the append (and therefore the KV drain) open.
            bool issued_terminal = true;
            for (size_t index = 0; index < current.kv_accepted.size(); ++index)
                if (current.kv_accepted[index] && !current.kv_terminal[index])
                    issued_terminal = false;
            if (issued_terminal)
                finishAppend(current, tick, reason);
            commitPendingFault(current);
            settleDeferredTerminal(current);
            return;
        }
        if (all_terminal) {
            if (!finishAppend(current, tick, reason)) {
                markFailed(current, reason);
                return;
            }
        }
        return;
    }
}

bool
ServingMeshExecutor::finishAppend(RequestSlot &current, uint64_t tick, std::string &reason)
{
    KvEdgeInputs edge;
    edge.tick = tick;
    edge.append_terminals.push_back(current.append_ledger->appendTerminal());
    for (const ErrorCandidate &error : current.append_ledger->errors())
        edge.faults.push_back(
            KvFaultEvent{current.request_context.identity().request_id, error});
    KvEdgeResult result;
    if (!commitEdge(edge, result, reason))
        return false;
    if (!current.phase_facts.empty())
        current.phase_facts.back().append_terminal_tick = tick;
    current.request_context.noteAppendTerminal(current.append_ledger->tokenBitmap(),
                                       std::nullopt);
    current.phase_append_tokens = 0;
    current.clearKvDescriptors();
    notePhaseJoin(current, tick);
    return true;
}

bool
ServingMeshExecutor::prepareRequest(RequestSlot &current, uint64_t tick,
                            std::string &reason)
{
    if (!current.request_context.validate(reason))
        return false;
    const auto &geometry = kv->geometry();
    if (current.request_context.usesKv() && mesh_dispatch != nullptr && !mesh_dispatch->canAccessKv(
            geometry.region_base, geometry.slot_bytes * geometry.max_sessions)) {
        reason = "E_BINDING_ROLE: KV slots are not reachable through the memory endpoint";
        return false;
    }
    current.kv_admission_ready_tick = tick;
    return true;
}

bool
ServingMeshExecutor::admit(RequestSlot &current, uint64_t tick,
                            std::string &reason)
{
    const auto &identity = current.request_context.identity();
    last_tick = std::max(last_tick, tick);
    if (!current.request_context.usesKv()) {
        if (!current.request_context.resolve(KvPath::InitialPrefill, reason))
            return markFailed(current, reason);
        current.ownership_claimed = true;
        current.request_start_tick = tick;
        current.first_phase_pending = true;
        current.phase_ready_tick = tick;
        if (sink_)
            sink_->onSessionAdmitted(identity.request_id);
        return true;
    }
    KvEdgeInputs admission;
    admission.tick = tick;
    if (!kv->hasAdmissionWaiter(identity.request_id))
        admission.admissions.push_back(current.request_context.admissionIntent(
            current.kv_admission_ready_tick));
    KvEdgeResult result;
    if (!commitEdge(admission, result, reason))
        return markFailed(current, reason);
    if (current.failed_flag) {
        reason = current.failure_reason;
        return false;
    }
    return true;
}

void
ServingMeshExecutor::startMember(uint64_t instance_generation,
                                       uint64_t request_id,
                                       uint32_t request_generation,
                                       uint64_t tick)
{
    RequestSlot *found = requestForFact(request_id, request_generation, tick);
    if (found == nullptr)
        return;
    RequestSlot &current = *found;
    if (current.failed_flag || current.phase_finished)
        return;
    const InstanceFactVerdict verdict =
        classifyInstanceFact(current, instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (current.started_instance == instance_generation) {
        recordStaleFact(tick);
        return;
    }
    if (verdict == InstanceFactVerdict::Violation) {
        last_tick = std::max(last_tick, tick);
        markFailed(current, "E_KV_STATE: core start from an unknown mesh instance");
        return;
    }
    current.started_instance = instance_generation;
    batchFor(current).armed_instance_generation = instance_generation;
    if (!current.first_phase_pending)
        return;
    KvEdgeInputs edge;
    edge.tick = tick;
    edge.core_starts.push_back(current.request_context.identity().request_id);
    KvEdgeResult result;
    std::string reason;
    if (current.request_context.usesKv() && !commitEdge(edge, result, reason)) {
        markFailed(current, reason);
        return;
    }
    current.first_phase_pending = false;
    current.rollback_token.reset();
    current.started = true;
    last_tick = tick;
    if (sink_ != nullptr && current.submitted_request)
        sink_->onCoreStart(*current.submitted_request);
}

void
ServingMeshExecutor::errorMember(uint64_t instance_generation,
    uint64_t request_id, uint32_t request_generation, uint64_t tick)
{
    auto *current = requestForFact(request_id, request_generation, tick);
    if (current == nullptr)
        return;
    last_tick = std::max(last_tick, tick);
    const auto verdict = classifyInstanceFact(*current, instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (verdict != InstanceFactVerdict::Current &&
            verdict != InstanceFactVerdict::PendingStart) {
        markFailed(*current, "E_KV_STATE: error from an unknown mesh instance");
        return;
    }
    auto &first = current->first_error_tick;
    if (!first || tick < *first)
        first = tick;
}

void
ServingMeshExecutor::bindCancellation(RequestSlot &current)
{
    if (!current.cancel_tick || current.cancel_position.instance || !ownsBatch(current))
        return;
    const auto &batch = batchFor(current);
    if (!batch.selected_instance)
        return;
    const auto request = current.request_context.identity().request_id;
    current.cancel_position = {batch.selected_instance->value(),
        static_cast<uint32_t>(current.request_context.phaseIndex())};
    mesh_dispatch->noteMemberCancel(batch.selected_instance->value(), request, *current.cancel_tick);
    if (const auto &output = batch.bindings.executionBinding().outputLedger())
        fatal_if(!output->cancel(request, *current.cancel_tick), "PUBLISH cancel has no frozen member");
}

ServingMeshExecutor::CancelResult
ServingMeshExecutor::requestCancel(uint64_t request_id, uint64_t tick)
{
    const auto found = slots.find(request_id);
    if (found == slots.end())
        return CancelResult::NotFound;
    auto &current = found->second;
    if (current.terminal_outcome)
        return CancelResult::AlreadyTerminal;
    if (!current.cancel_tick) {
        current.cancel_tick = tick;
        bindCancellation(current);
    }
    requestNextQuantum(tick);
    return CancelResult::Accepted;
}

bool ServingMeshExecutor::abortBeforeStart(uint64_t request_id, uint64_t tick, std::string &reason)
{
    if (slots.find(request_id) == slots.end()) {
        reason = "E_KV_STATE: abort outside an armed request";
        return false;
    }
    RequestSlot &current = slot(request_id);
    if (current.started || current.failed_flag || current.terminal_outcome ||
            current.phase_finished) {
        reason = "E_KV_STATE: request is no longer pending a first core start";
        return false;
    }
    last_tick = std::max(last_tick, tick);
    const bool cancelled = current.rollback_token
        ? rollbackPrestart(current, KvTerminalStatus::Cancelled, reason)
        : terminalizePendingAdmission(current, KvTerminalStatus::Cancelled, reason);
    if (!cancelled) {
        markFailed(current, reason);
        return false;
    }
    current.phase_ready_tick.reset();
    if (current.ownership_claimed &&
            !sealTerminalSnapshot(current, KvTerminalStatus::Cancelled, last_tick, reason))
        return false;
    reportCompletion(current, NpuExecutionOutcome::Cancelled);
    reason.clear();
    return true;
}

bool
ServingMeshExecutor::terminalizePendingAdmission(RequestSlot &current,
    KvTerminalStatus status, std::string &reason)
{
    if (!current.request_context.usesKv()) {
        cancelPendingInstance(current);
        current.first_phase_pending = false;
        return true;
    }
    KvEdgeInputs edge;
    edge.tick = last_tick;
    edge.owner_terminals.push_back({
        current.request_context.identity().request_id, status, std::nullopt, false});
    KvEdgeResult result;
    return commitEdge(edge, result, reason);
}

void ServingMeshExecutor::settleMember(uint64_t instance_generation,
                                            uint64_t request_id,
                                            uint32_t request_generation,
                                            bool errored, uint64_t tick)
{
    RequestSlot *found = requestForFact(request_id, request_generation, tick);
    if (found == nullptr)
        return;
    RequestSlot &current = *found;
    if (current.phase_finished)
        return;
    // One settle per instance, no matter whether the phase committed yet.
    if (!ownsBatch(current)) {
        recordStaleFact(tick);
        return;
    }
    if (current.settled_instance &&
            instance_generation <= *current.settled_instance) {
        recordStaleFact(tick);
        return;
    }
    const InstanceFactVerdict verdict =
        classifyInstanceFact(current, instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (verdict != InstanceFactVerdict::Current) {
        last_tick = std::max(last_tick, tick);
        markFailed(current, "E_KV_STATE: completion callback from an unknown "
                   "instance");
        return;
    }
    // The settle is consumed exactly once per instance; the instance itself
    // stays current until its phase commits, because a failed request still
    // has to accept the drain facts of that same instance.
    current.settled_instance = instance_generation;
    batchFor(current).settled_instance_generation = instance_generation;
    ++current.instance_settles;
    last_tick = tick;
    if (errored) {
        if (!current.request_context.usesKv()) {
            markFailed(current, "E_REQUEST_PROFILE: mesh instance faulted");
            settleDeferredTerminal(current);
            return;
        }
        std::string reason;
        KvEdgeInputs edge;
        edge.tick = tick;
        if (current.first_phase_pending && current.rollback_token) {
            current.first_phase_pending = false;
            edge.owner_terminals.push_back(current.request_context.rollbackTerminal(
                KvTerminalStatus::Error, *current.rollback_token));
        } else {
            if (current.phase_append_tokens != 0) {
                edge.append_terminals.push_back(KvAppendTerminal{
                    current.request_context.identity().request_id,
                    std::vector<bool>(current.phase_append_tokens, false),
                    std::nullopt});
                current.phase_append_tokens = 0;
            }
            KvFaultEvent fault;
            fault.request_id = current.request_context.identity().request_id;
            fatal_if(!current.batch_failure,
                     "instance error drain has no global failure record");
            fault.candidate = *current.batch_failure;
            // With live KV work the record cannot take the fault yet.  Hold
            // it and re-submit once the drain makes the record admissible,
            // instead of submitting one the manager would have to fatal on.
            const ServingRequestIdentity fault_identity =
                current.request_context.identity();
            if (!kv->kvWorkDrained(fault_identity.session_id,
                                   fault_identity.kv_handle)) {
                current.pending_fault = fault;
                markFailed(current, "E_KV_STATE: mesh instance faulted");
                settleDeferredTerminal(current);
                return;
            }
            edge.faults.push_back(fault);
        }
        KvEdgeResult result;
        commitEdge(edge, result, reason);
        markFailed(current, "E_KV_STATE: mesh instance faulted");
        settleDeferredTerminal(current);
        return;
    }
    if (current.failed_flag) {
        // A failed request only completes its drain; it never advances.
        settleDeferredTerminal(current);
        return;
    }
    current.request_context.noteCoreDrain();
    if (!current.phase_facts.empty())
        current.phase_facts.back().core_drain_tick = tick;
    notePhaseJoin(current, tick);
}

ServingMeshExecutor::RequestSlot *
ServingMeshExecutor::requestForFact(uint64_t request_id,
                                              uint32_t request_generation,
                                              uint64_t tick)
{
    const auto it = slots.find(request_id);
    if (it != slots.end() &&
            it->second.request_context.identity().generation ==
                request_generation)
        return &it->second;
    recordStaleFact(tick);
    return nullptr;
}

ServingMeshExecutor::InstanceFactVerdict
ServingMeshExecutor::classifyInstanceFact(RequestSlot &current, uint64_t instance_generation,
                                          uint64_t tick)
{
    if (!ownsBatch(current)) {
        if (instance_generation > latest_instance && !current.terminal_outcome)
            return InstanceFactVerdict::Violation;
        recordStaleFact(tick);
        return InstanceFactVerdict::Stale;
    }
    if (current.phase_finished || current.terminal_outcome) {
        recordStaleFact(tick);
        return InstanceFactVerdict::Stale;
    }
    if (batchFor(current).armed_instance_generation) {
        if (instance_generation < *batchFor(current).armed_instance_generation) {
            recordStaleFact(tick);
            return InstanceFactVerdict::Stale;
        }
        if (instance_generation > *batchFor(current).armed_instance_generation)
            return InstanceFactVerdict::Violation;
        return InstanceFactVerdict::Current;
    }
    if (current.settled_instance &&
            instance_generation <= *current.settled_instance) {
        recordStaleFact(tick);
        return InstanceFactVerdict::Stale;
    }
    if (batchFor(current).selected_instance &&
            instance_generation == batchFor(current).selected_instance->value())
        return InstanceFactVerdict::PendingStart;
    return InstanceFactVerdict::Violation;
}

void
ServingMeshExecutor::recordStaleFact(uint64_t tick)
{
    ++stale_instance_facts;
    last_tick = std::max(last_tick, tick);
}

void
ServingMeshExecutor::notePhaseJoin(RequestSlot &current, uint64_t tick)
{
    if (current.failed_flag || current.terminal_outcome ||
            !current.request_context.joinSatisfied() || batchFor(current).join_ready_tick)
        return;
    auto &batch = batchFor(current);
    for (const auto &member : batch.members)
        if (!slot(member.request_id).request_context.joinSatisfied())
            return;
    batch.join_ready_tick = tick;
    requestNextQuantum(tick);
}

bool
ServingMeshExecutor::commitPhaseJoin(RequestSlot &current, uint64_t tick, std::string &reason)
{
    const auto step = current.request_context.step();
    if (!current.request_context.commitPhase(reason))
        return false;
    const bool committed_before_cancel = !current.cancel_tick || *current.cancel_tick >= tick;
    if (committed_before_cancel) {
        ++current.phases_committed;
        if (step.phase == mesh_abi::kPhaseDECODE)
            current.output_tokens_committed += step.decode_chunk_tokens;
    }
    if (!current.phase_facts.empty()) {
        current.phase_facts.back().commit_tick = committed_before_cancel ? tick : 0;
        if (phase_fact_sink)
            phase_fact_sink(current.phase_facts.back());
    }
    if (current.request_context.finished() &&
            !(current.cancel_tick && *current.cancel_tick < tick)) {
        if (!sealTerminalSnapshot(current, KvTerminalStatus::Success, tick, reason))
            return false;
        current.phase_finished = true;
        reportCompletion(current, NpuExecutionOutcome::Success);
        return true;
    }
    current.phase_ready_tick = tick;
    requestNextQuantum(tick);
    return true;
}

void
ServingMeshExecutor::requestNextQuantum(uint64_t ready_tick)
{
    fatal_if(ready_tick == UINT64_MAX,
             "no scheduler quantum is representable after the READY edge");
    requestQuantum(ready_tick + 1);
}

void
ServingMeshExecutor::requestQuantum(uint64_t tick)
{
    if (quantum_scheduled_tick && *quantum_scheduled_tick <= tick)
        return;
    quantum_scheduled_tick = tick;
    if (quantum_driver)
        quantum_driver(tick);
}

void
ServingMeshExecutor::onQuantumEdge(uint64_t tick)
{
    if (quantum_scheduled_tick && *quantum_scheduled_tick <= tick)
        quantum_scheduled_tick.reset();
    if (slots.empty())
        return;
    last_tick = std::max(last_tick, tick);
    std::string reason;
    for (auto &candidate : batches) {
        auto &batch = *candidate;
        if (!batch.members.empty() && !batch.draining() &&
            std::all_of(batch.members.begin(), batch.members.end(), [&](const auto &member) {
                const auto found = slots.find(member.request_id);
                return found == slots.end() || found->second.terminal_outcome;
            }))
            batch.release();
        if (batch.join_ready_tick && *batch.join_ready_tick < tick) {
            batch.join_ready_tick.reset();
            const auto members = batch.members;
            const auto &output = batch.bindings.executionBinding().outputLedger();
            if (output)
                for (const auto &member : members)
                    slot(member.request_id).output_bytes_committed = output->prefix(member.request_id);
            batch.release();
            for (const auto &member : members) {
                auto &current = slot(member.request_id);
                if (!current.failed_flag && !commitPhaseJoin(current, tick, reason))
                    markFailed(current, reason);
            }
        }
    }
    bool pending_admission = false;
    for (auto &[id, current] : slots) {
        if (current.terminal_outcome || current.failed_flag)
            continue;
        if (current.cancel_tick && *current.cancel_tick < tick) {
            if (ownsBatch(current) && batchFor(current).selected_instance) {
                const auto &batch = batchFor(current);
                if (!batch.armed_instance_generation || batch.draining() ||
                        !terminalJoinSatisfied(current))
                    continue;
            }
            if (!current.started) {
                abortBeforeStart(id, tick, reason);
            } else {
                cancelPendingInstance(current);
                current.phase_ready_tick.reset();
                if (!sealTerminalSnapshot(current, KvTerminalStatus::Cancelled, tick, reason))
                    markFailed(current, reason);
                else
                    reportCompletion(current, NpuExecutionOutcome::Cancelled);
            }
            continue;
        }
        if (current.request_context.usesKv() && !current.kv_terminal_snapshot && !kv->hasPin(id) &&
                tick > current.kv_admission_ready_tick)
            pending_admission = true;
    }
    if (pending_admission) {
        KvEdgeInputs edge;
        edge.tick = tick;
        for (const auto &[id, current] : slots)
            if (current.request_context.usesKv() && !current.terminal_outcome && !current.failed_flag &&
                    !current.kv_terminal_snapshot && !kv->hasPin(id) &&
                    !kv->hasAdmissionWaiter(id) && tick > current.kv_admission_ready_tick)
                edge.admissions.push_back(current.request_context.admissionIntent(
                    current.kv_admission_ready_tick));
        KvEdgeResult result;
        if (!commitEdge(edge, result, reason))
            return;
    }
    const auto available = std::find_if(batches.begin(), batches.end(),
        [](const auto &candidate) { return candidate->members.empty(); });
    if (available == batches.end())
        return;
    auto &batch = **available;
    const auto decision = selectBatch(tick);
    if (decision.action == SchedulerAction::Idle)
        return;
    if (decision.action == SchedulerAction::Wait) {
        requestQuantum(decision.waitUntil);
        return;
    }
    if (decision.action == SchedulerAction::FailHead) {
        markFailed(slot(decision.members.front()), "E_REQUEST_PROFILE: no executable batch count for the head request");
        requestNextQuantum(tick);
        return;
    }
    auto members = decision.members;
    std::sort(members.begin(), members.end(), [&](uint64_t left, uint64_t right) {
        const auto &a = *slot(left).submitted_request;
        const auto &b = *slot(right).submitted_request;
        return std::tie(a.workloadPlanItemId, a.userId, a.taskSeq, a.repairRound, a.requestId) <
               std::tie(b.workloadPlanItemId, b.userId, b.taskSeq, b.repairRound, b.requestId);
    });
    const auto &head = slot(members.front()).request_context;
    const auto &step = head.step();
    for (const auto &candidate : program->transport.agent_instance_profiles) {
        if (slot(members.front()).submitted_request->batchReplay &&
                candidate.instance_profile_id != batch_replay->batches.at(replay_cursor).profile)
            continue;
        if (candidate.request_program_id != head.identity().program_id ||
            candidate.request_profile_id != head.identity().profile_id ||
            candidate.path_kind != head.identity().path_kind || candidate.phase != step.phase ||
            candidate.kv_tokens_before != step.kv_tokens_before ||
            candidate.valid_tokens_per_member != step.valid_tokens_per_member ||
            candidate.decode_chunk_tokens != step.decode_chunk_tokens ||
            candidate.member_count != members.size())
            continue;
        bool matches = true;
        for (size_t ordinal = 0; ordinal < members.size(); ++ordinal)
            matches = matches && program->transport.agent_instance_member_bindings.at(
                candidate.member_binding_first + ordinal).expected_logical_source_rank ==
                slot(members[ordinal]).submitted_request->logicalSourceRank;
        if (!matches)
            continue;
        fatal_if(batch.profile, "E_AGENT_PROTOCOL_FATAL: ambiguous concrete serving selector");
        batch.profile = &candidate;
    }
    if (!batch.profile) {
        for (uint64_t id : members)
            markFailed(slot(id), "E_REQUEST_PROFILE: frozen rank vector has no exact profile");
        return;
    }
    for (uint32_t ordinal = 0; ordinal < members.size(); ++ordinal)
        batch.members.push_back({members[ordinal], slot(members[ordinal]).request_context.identity().generation, ordinal});
    if (!submitPhaseEdge(batch, tick, reason)) {
        for (uint64_t id : members)
            markFailed(slot(id), reason);
        return;
    }
    if (slot(members.front()).submitted_request->batchReplay)
        ++replay_cursor;
    mesh_dispatch->beginSelectedInstance(batch.preparation_failure);
    for (const auto &member : batch.members) {
        bindCancellation(slot(member.request_id));
        if (sink_)
            sink_->onBatchFrozen(member.request_id);
    }
    requestNextQuantum(tick);
}

}
}

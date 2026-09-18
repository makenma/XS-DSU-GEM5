#include "dev/ai_mesh/serving_mesh_executor.hh"

#include <algorithm>
#include <map>
#include <set>
#include <utility>

#include "base/logging.hh"
#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/agent_surrogate_codec.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/serving_host_bindings.hh"

namespace gem5
{
namespace ai_mesh
{

ServingMeshExecutor::ServingMeshExecutor(const DecodedProgram &program_ref,
                                         MeshKvManager &kv_ref)
    : program(&program_ref), kv(&kv_ref),
      request_context(program_ref, ServingRequestIdentity())
{}

uint64_t
ServingMeshExecutor::planOutputBytes() const
{
    for (const auto &item : request_context.plan())
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
    if (submitted_request) {
        admission.rejectDetail = agent_abi::E_KV_STATE;
        return admission;
    }
    ServingRequestIdentity identity;
    identity.request_id = request.requestId;
    identity.session_id = request.sessionId;
    identity.kv_handle = request.kvHandle;
    identity.generation = request.generation;
    identity.program_id = request.programId;
    identity.profile_id = request.profileId;
    identity.path_kind = request.pathKind;
    identity.requested_profile_key = request.requestedProfileKey;
    identity.input_bytes = request.inputBytes;
    identity.input_tokens = request.inputTokens;
    identity.output_tokens = request.outputTokens;
    identity.output_capacity_bytes = request.outputCapacityBytes;
    identity.kv_bytes_per_token = kv->geometry().bytes_per_token;
    identity.contract_digest = request.contractDigest;
    identity.cached_tokens = request.cachedTokens;
    identity.kv_flags = request.kvFlags;
    identity.repair_round = request.repairRound;
    std::string reason;
    const uint64_t tick = tick_source ? tick_source() : last_tick;
    if (!verifyHostBindings(request, reason)) {
        admission.rejectDetail = rejectDetailOf(reason);
        return admission;
    }
    if (!start(identity, tick, reason)) {
        admission.rejectDetail = request_context.rejectDetail() != 0
            ? request_context.rejectDetail() : rejectDetailOf(reason);
        markFailed(reason);
        reportCompletion();
        return admission;
    }
    if (!bindRequestTensors(request, reason)) {
        admission.rejectDetail = agent_abi::E_BINDING_ROLE;
        return admission;
    }
    if (!bindPhaseFillContent(request, reason)) {
        admission.rejectDetail = rejectDetailOf(reason);
        markFailed(reason);
        reportCompletion();
        return admission;
    }
    if (!beginFirstPhase(tick, reason)) {
        admission.rejectDetail = rejectDetailOf(reason);
        markFailed(reason);
        reportCompletion();
        return admission;
    }
    submitted_request = request;
    admission.admitted = true;
    return admission;
}

NpuExecutionCompletion
ServingMeshExecutor::completionFor(const NpuExecutionRequest &request) const
{
    NpuExecutionCompletion completion;
    completion.success = phase_finished && !failed_flag;
    completion.coreStarted = started;
    completion.phaseInstances = phases_committed;
    completion.outputBytes = planOutputBytes();
    completion.kvTerminal = kv_terminal_snapshot;
    completion.requestStartTick = request_start_tick;
    completion.terminalTick = kv_terminal_tick;
    if (!phase_finished || completion.outputBytes == 0)
        return completion;
    if (!digest_frozen || !digest_source)
        return completion;
    completion.semanticDigest = frozen_digest;
    return completion;
}

bool
ServingMeshExecutor::bindPhaseFillContent(const NpuExecutionRequest &request,
                                          std::string &reason)
{
    if (request_context.finished() || mesh_dispatch == nullptr)
        return true;
    const auto &step = request_context.step();
    const auto *profile = request_context.requestProfile();
    if (profile == nullptr) {
        reason = "E_REQUEST_PROFILE: fill has no resolved request profile";
        return false;
    }
    std::vector<uint8_t> content;
    uint32_t tensor_id = 0;
    const uint32_t symbol = step.phase == mesh_abi::kPhasePUBLISH ?
        profile->primary_output_symbol_id : profile->primary_kv_symbol_id;
    for (const auto &relocation : program->relocations)
        if (relocation.symbol_sid == symbol)
            tensor_id = relocation.tensor_id;
    if (step.phase == mesh_abi::kPhasePUBLISH) {
        if (!digest_frozen) {
            const uint64_t bytes = planOutputBytes();
            if (!digest_source || bytes == 0) {
                reason = "E_BINDING_ROLE: PUBLISH has no semantic digest source";
                return false;
            }
            frozen_digest = digest_source(request, bytes);
            digest_frozen = true;
            mesh_dispatch->bindSemanticDigest(frozen_digest);
        }
        content = surrogateOutputBytes(frozen_digest.data(), planOutputBytes());
    } else {
        const uint32_t input_tokens = request_context.plan().front().cached_tokens_after;
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
    std::set<uint32_t> command_ids;
    for (const auto &range : program->profile_stream_ranges)
        if (range.profile_id == step.mesh_profile_id)
            for (uint32_t index = range.command_begin;
                 index < range.command_begin + range.command_count; ++index)
                command_ids.insert(program->commands.at(index).command_id);
    std::vector<ServingFillBinding> bindings;
    for (const auto &descriptor : program->descriptors) {
        if (descriptor.kind != mesh_abi::kDmaKindLOCAL_FILL ||
                command_ids.count(descriptor.command_id) == 0)
            continue;
        const auto command = std::find_if(program->commands.begin(), program->commands.end(),
            [&](const auto &item) { return item.command_id == descriptor.command_id; });
        if (command != program->commands.end() &&
                program->attrs.at(command->attr_index - 1).as<mesh_abi::FillV1>().pattern !=
                    mesh_abi::kDmaFillRuntimeBoundSentinel)
            continue;
        if (command == program->commands.end() || command->operand_count == 0 ||
                (step.phase != mesh_abi::kPhasePUBLISH &&
                 descriptor.dst.tensor_id != tensor_id)) {
            reason = "E_BINDING_ROLE: runtime fill has no matching source tensor";
            return false;
        }
        ServingFillBinding binding;
        binding.request_id = request.requestId;
        binding.request_generation = request.generation;
        binding.instance_profile_id = step.instance_profile_id;
        binding.producer_command_id = descriptor.command_id;
        binding.allocation_id = program->operands.at(
            command->operand_begin + command->operand_count - 1).allocation_id;
        const auto allocation = std::find_if(
            program->allocations.begin(), program->allocations.end(),
            [&](const auto &item) { return item.allocation_id == binding.allocation_id; });
        if (allocation == program->allocations.end() ||
                descriptor.dst.offset_bytes < allocation->offset_bytes) {
            reason = "E_BINDING_ROLE: runtime fill allocation is missing";
            return false;
        }
        for (uint32_t row = 0; row < descriptor.rows; ++row) {
            const uint64_t offset = descriptor.dst.offset_bytes - allocation->offset_bytes +
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
    for (const auto &requirement : program->agent_request_binding_requirements)
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
    return true;
}

bool
ServingMeshExecutor::bindRequestTensors(const NpuExecutionRequest &request,
                                        std::string &reason)
{
    instance_binding = ServingInstanceBinding(request.requestId,
                                              request.generation);
    if (request.hostBindings.empty())
        return true;
    struct Pending
    {
        uint32_t tensor_id = 0;
        uint64_t origin = 0;
        uint64_t address = 0;
        ServingByteWindow read{};
        ServingByteWindow write{};
    };
    std::vector<Pending> pending;
    for (const agent_abi::BindingRecord &binding : request.hostBindings) {
        if (binding.kind == agent_abi::kBindingKindKV_EXTERNAL)
            continue;
        const auto relocation = std::find_if(
            program->relocations.begin(), program->relocations.end(),
            [&](const auto &item) {
                return item.symbol_sid == binding.symbol_id;
            });
        if (relocation == program->relocations.end()) {
            reason = "E_BINDING_ROLE: verified binding has no relocation";
            return false;
        }
        const bool write = binding.kind == agent_abi::kBindingKindHOST_OUTPUT;
        Pending entry;
        entry.tensor_id = relocation->tensor_id;
        entry.origin = relocation->offset_bytes;
        entry.address = binding.address;
        entry.read = {0, write ? 0 : binding.bytes};
        entry.write = {0, write ? binding.bytes : 0};
        const auto duplicate = std::find_if(
            pending.begin(), pending.end(), [&](const Pending &other) {
                return other.tensor_id == entry.tensor_id;
            });
        if (duplicate != pending.end()) {
            if (duplicate->origin != entry.origin ||
                    duplicate->address != entry.address ||
                    duplicate->read.bytes != entry.read.bytes ||
                    duplicate->write.bytes != entry.write.bytes) {
                reason = "E_BINDING_ROLE: one tensor carries conflicting "
                    "requirements";
                return false;
            }
            continue;
        }
        pending.push_back(entry);
    }
    for (const Pending &entry : pending)
        if (!instance_binding.bind(entry.tensor_id, entry.origin,
                                   entry.address, entry.read, entry.write)) {
            reason = "E_BINDING_ROLE: request binding is not installable";
            return false;
        }
    return true;
}

void
ServingMeshExecutor::reportCompletion()
{
    if (completion_reported || submitted_request == std::nullopt)
        return;
    completion_reported = true;
    postCompletion(*submitted_request);
}

bool
ServingMeshExecutor::sealTerminalSnapshot(KvTerminalStatus status, uint64_t tick,
                                          std::string &reason)
{
    const ServingRequestIdentity identity = request_context.identity();
    if (!kv_terminal_snapshot.has_value() &&
            (kv->hasPin(identity.request_id) ||
             kv->hasClaim(identity.request_id))) {
        KvEdgeInputs edge;
        edge.tick = tick;
        edge.owner_terminals.push_back(
            request_context.ownerTerminal(status));
        KvEdgeResult result;
        if (!commitEdge(edge, result, reason))
            return false;
    }
    if (!kv_terminal_snapshot.has_value()) {
        reason = "E_KV_STATE: request ended without an owner terminal";
        return false;
    }
    const KvRecord *record =
        kv->findRecord(identity.session_id, identity.kv_handle);
    if (record == nullptr) {
        reason = "E_KV_STATE: terminal snapshot has no KV record";
        return false;
    }
    if (record->pin_count != 0 || record->admission_claim_count != 0) {
        reason = "E_KV_STATE: KV ownership survived the owner terminal";
        return false;
    }
    if (status == KvTerminalStatus::Success &&
            record->outstanding_kv_dma != 0) {
        reason = "E_KV_STATE: KV work survived the owner terminal";
        return false;
    }
    last_tick = tick;
    return true;
}

bool
ServingMeshExecutor::terminalJoinSatisfied() const
{
    // Every already-issued piece of work has to be accounted for before the
    // request may terminalize: the core that was started must have settled and
    // each accepted descriptor must have terminalized.  Descriptors that were
    // never issued cannot produce a terminal and must not gate the wrap-up.
    if (started &&
            drained_instance != static_cast<uint32_t>(phases_committed) + 1)
        return false;
    const ServingRequestIdentity identity = request_context.identity();
    if (!kv->kvWorkDrained(identity.session_id, identity.kv_handle))
        return false;
    for (size_t index = 0; index < kv_accepted.size(); ++index)
        if (kv_accepted[index] && !kv_terminal[index])
            return false;
    return true;
}

void
ServingMeshExecutor::commitPendingFault()
{
    if (!pending_fault.has_value())
        return;
    const ServingRequestIdentity identity = request_context.identity();
    if (!kv->kvWorkDrained(identity.session_id, identity.kv_handle))
        return;
    KvEdgeInputs edge;
    edge.tick = last_tick;
    edge.faults.push_back(*pending_fault);
    KvEdgeResult result;
    std::string reason;
    if (!commitEdge(edge, result, reason))
        return;
    pending_fault.reset();
}

void
ServingMeshExecutor::settleDeferredTerminal()
{
    if (completion_reported || !ownership_claimed || !failed_flag)
        return;
    // Every issued descriptor being terminal is what lets the append close
    // out; descriptors that were never issued can never terminalize, so they
    // must not keep the append (and the KV drain) open.
    if (append_ledger) {
        bool issued_terminal = true;
        for (size_t index = 0; index < kv_accepted.size(); ++index)
            if (kv_accepted[index] && !kv_terminal[index])
                issued_terminal = false;
        if (issued_terminal) {
            std::string close_reason;
            finishAppend(last_tick, close_reason);
        }
    }
    commitPendingFault();
    if (!kv_terminal_snapshot.has_value() && !terminalJoinSatisfied()) {
        // The owner terminal is join-gated.  Wait for the legal drain facts
        // instead of probing the manager with one it would reject.
        terminal_deferred = true;
        return;
    }
    std::string reason;
    if (!sealTerminalSnapshot(KvTerminalStatus::Error, last_tick, reason)) {
        terminal_deferred = true;
        return;
    }
    terminal_deferred = false;
    reportCompletion();
}

bool ServingMeshExecutor::latchRunFailure(const std::string &reason)
{
    if (failed_flag)
        return false;
    failed_flag = true;
    if (failure_reason.empty())
        failure_reason = reason;
    if (first_phase_pending && rollback_token) {
        std::string close_reason;
        rollbackPrestart(KvTerminalStatus::Error, close_reason);
    } else if (started && mesh_dispatch != nullptr) {
        mesh_dispatch->failCurrentInstance();
    }
    return true;
}

bool ServingMeshExecutor::markFailed(const std::string &reason)
{
    if (!latchRunFailure(reason))
        return false;
    if (ownership_claimed) {
        settleDeferredTerminal();
        return false;
    }
    reportCompletion();
    return false;
}

bool ServingMeshExecutor::rollbackPrestart(KvTerminalStatus status,
                                           std::string &reason)
{
    if (!first_phase_pending || !rollback_token) {
        reason = "E_KV_STATE: abort outside an armed request";
        return false;
    }
    if (mesh_dispatch != nullptr)
        mesh_dispatch->cancelPendingInstance();
    first_phase_pending = false;
    KvEdgeInputs edge;
    edge.tick = last_tick;
    edge.owner_terminals.push_back(
        request_context.rollbackTerminal(status, *rollback_token));
    KvEdgeResult result;
    if (!commitEdge(edge, result, reason))
        return false;
    rollback_token.reset();
    return true;
}

std::vector<std::pair<uint32_t, uint32_t>>
ServingMeshExecutor::selectors(std::string &reason) const
{
    std::vector<std::pair<uint32_t, uint32_t>> out;
    for (const auto &item : request_context.plan()) {
        const mesh_abi::AgentInstanceProfile *match = nullptr;
        uint32_t matches = 0;
        for (const auto &instance : program->agent_instance_profiles) {
            if (instance.instance_profile_id != item.instance_profile_id)
                continue;
            match = &instance;
            ++matches;
        }
        if (matches != 1 || match == nullptr) {
            reason = "E_REQUEST_PROFILE: phase instance profile is not unique";
            return {};
        }
        out.emplace_back(match->mesh_entrypoint_id, match->mesh_profile_id);
    }
    return out;
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
    // Every edge that releases this request's KV ownership hands back the
    // manager's immutable terminal snapshot; keeping it here makes the
    // snapshot the single source for completion, metadata and the CQ.
    const auto terminal =
        result.terminals.find(request_context.identity().request_id);
    if (terminal != result.terminals.end() &&
            !kv_terminal_snapshot.has_value()) {
        kv_terminal_snapshot = terminal->second;
        kv_terminal_tick = edge.tick;
    }
    return true;
}

bool ServingMeshExecutor::submitPhaseEdge(uint64_t tick, std::string &reason)
{
    KvEdgeInputs edge;
    edge.tick = tick;
    request_context.beginPhase(edge);
    phase_append_tokens = edge.append_arms.empty()
        ? 0 : edge.append_arms.front().append_tokens;
    if (!buildPhaseKvDescriptors(reason))
        return false;
    KvEdgeResult result;
    if (!commitEdge(edge, result, reason))
        return false;
    if (phase_append_tokens != 0) {
        const auto frozen = result.views.find({request_context.identity().request_id, 0});
        const auto *profile = request_context.requestProfile();
        const auto *instance = request_context.instanceProfile();
        if (frozen == result.views.end() || profile == nullptr || instance == nullptr) {
            reason = "E_BINDING_ROLE: phase has no frozen KV view";
            return false;
        }
        const auto &view = frozen->second;
        const uint64_t before = uint64_t(request_context.step().kv_tokens_before) *
            profile->kv_bytes_per_token;
        const uint64_t append = uint64_t(phase_append_tokens) * profile->kv_bytes_per_token;
        const uint32_t symbol = program->agent_instance_member_bindings.at(
            instance->member_binding_first).static_kv_symbol_id;
        const auto relocation = std::find_if(
            program->relocations.begin(), program->relocations.end(),
            [&](const auto &item) { return item.symbol_sid == symbol; });
        if (relocation == program->relocations.end() ||
                view.valid_bytes_at_arm != before || before > view.slot_bytes ||
                append > view.slot_bytes - before ||
                !instance_binding.bind(relocation->tensor_id, relocation->offset_bytes,
                    view.slot_base, {0, view.valid_bytes_at_arm}, {before, append})) {
            reason = "E_BINDING_ROLE: KV descriptor window does not match its pinned slot";
            return false;
        }
    }
    if (mesh_dispatch != nullptr)
        mesh_dispatch->bindInstance(instance_binding);
    ServingPhaseFact fact;
    fact.phase = request_context.step().phase;
    fact.instance_profile_id = request_context.step().instance_profile_id;
    fact.mesh_profile_id = request_context.step().mesh_profile_id;
    fact.kv_tokens_before = request_context.step().kv_tokens_before;
    fact.append_tokens = phase_append_tokens;
    phase_facts.push_back(fact);
    return true;
}

bool
ServingMeshExecutor::buildPhaseKvDescriptors(std::string &reason)
{
    append_ledger.reset();
    kv_descriptors.clear();
    kv_accepted.clear();
    kv_terminal.clear();
    if (phase_append_tokens == 0)
        return true;
    const mesh_abi::AgentInstanceProfile *instance =
        request_context.instanceProfile();
    const mesh_abi::AgentRequestProfile *request =
        request_context.requestProfile();
    if (instance == nullptr || request == nullptr) {
        reason = "E_REQUEST_PROFILE: phase instance profile is missing";
        return false;
    }
    if (instance->member_count != 1) {
        reason = "E_REQUEST_PROFILE: R3 serves singleton members only";
        return false;
    }
    if (instance->member_binding_first >=
            program->agent_instance_member_bindings.size()) {
        reason = "E_BINDING_ROLE: member binding span is out of range";
        return false;
    }
    const mesh_abi::AgentInstanceMemberBinding &binding =
        program->agent_instance_member_bindings[
            instance->member_binding_first];
    const mesh_abi::Relocation *kv_slot = nullptr;
    for (const auto &relocation : program->relocations)
        if (relocation.symbol_sid == binding.static_kv_symbol_id)
            kv_slot = &relocation;
    const mesh_abi::Relocation *kv_tensor = nullptr;
    for (const auto &relocation : program->relocations)
        if (relocation.symbol_sid == request->primary_kv_symbol_id)
            kv_tensor = &relocation;
    if (kv_slot == nullptr || kv_tensor == nullptr) {
        reason = "E_RELOCATION: KV slot or tensor relocation is missing";
        return false;
    }
    std::vector<uint32_t> commands;
    for (const auto &range : program->profile_stream_ranges) {
        if (range.profile_id != instance->mesh_profile_id)
            continue;
        for (uint32_t index = 0; index < range.command_count; ++index)
            commands.push_back(
                program->commands[size_t(range.command_begin) + index].
                    command_id);
    }
    const uint32_t token_bytes = request->kv_bytes_per_token;
    const uint32_t base = request_context.step().kv_tokens_before;
    const uint64_t append_bytes = uint64_t(phase_append_tokens) * token_bytes;
    for (const auto &descriptor : program->descriptors) {
        if (descriptor.kind != mesh_abi::kDmaKindSTORE ||
                descriptor.dst.tensor_id != kv_tensor->tensor_id)
            continue;
        if (std::find(commands.begin(), commands.end(),
                      descriptor.command_id) == commands.end())
            continue;
        if (descriptor.dst.offset_bytes < kv_slot->offset_bytes) {
            reason = "E_KV_TOKEN_MISMATCH: KV store starts below its slot";
            return false;
        }
        const uint64_t region =
            descriptor.dst.offset_bytes - kv_slot->offset_bytes;
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
        kv_descriptors.push_back(item);
    }
    if (kv_descriptors.empty()) {
        reason = "E_KV_TOKEN_MISMATCH: append has no KV store descriptor";
        return false;
    }
    append_ledger.emplace(request_context.identity().request_id,
                          request_context.identity().session_id,
                          request_context.identity().kv_handle,
                          request_context.identity().generation, base,
                          phase_append_tokens, token_bytes);
    kv_accepted.assign(kv_descriptors.size(), false);
    kv_terminal.assign(kv_descriptors.size(), false);
    return true;
}

bool
ServingMeshExecutor::commitDmaCount(const char *kind, uint32_t count,
                                    uint64_t tick, std::string &reason)
{
    if (count == 0)
        return true;
    KvEdgeInputs edge;
    edge.tick = tick;
    KvDmaCount item;
    item.request_id = request_context.identity().request_id;
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
    if (!factBelongsToThisRequest(request_id, request_generation, tick))
        return;
    const InstanceFactVerdict verdict =
        classifyInstanceFact(instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (verdict != InstanceFactVerdict::Current) {
        last_tick = std::max(last_tick, tick);
        markFailed("E_KV_STATE: DMA accept from an unknown mesh instance");
        return;
    }
    if (!started || failed_flag || phase_finished || !append_ledger)
        return;
    last_tick = tick;
    for (size_t index = 0; index < kv_descriptors.size(); ++index) {
        if (kv_descriptors[index].command_id != command_id)
            continue;
        if (kv_descriptors[index].descriptor_id != descriptor_id) {
            markFailed("E_KV_TOKEN_MISMATCH: KV descriptor identity mismatch");
            return;
        }
        if (kv_accepted[index])
            return;
        if (!append_ledger->acceptDescriptor(kv_descriptors[index].descriptor_id,
                                             kv_descriptors[index].first_token,
                                             kv_descriptors[index].tokens)) {
            markFailed("E_KV_TOKEN_MISMATCH: KV descriptor is not admissible");
            return;
        }
        kv_accepted[index] = true;
        ++accepted_kv_descriptors;
        ++phase_facts.back().accepted_descriptors;
        std::string reason;
        if (!commitDmaCount("accept", append_ledger->acceptDelta(), tick,
                            reason)) {
            markFailed(reason);
            return;
        }
        append_ledger->clearDeltas();
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
    if (!factBelongsToThisRequest(request_id, request_generation, tick))
        return;
    const InstanceFactVerdict verdict =
        classifyInstanceFact(instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (verdict != InstanceFactVerdict::Current) {
        last_tick = std::max(last_tick, tick);
        markFailed("E_KV_STATE: DMA terminal from an unknown mesh instance");
        return;
    }
    if (!started || phase_finished || !append_ledger)
        return;
    // A failed request still has to see its accepted descriptors terminalize:
    // that drain is what makes the owner terminal legal.
    const bool draining = failed_flag;
    last_tick = tick;
    for (size_t index = 0; index < kv_descriptors.size(); ++index) {
        if (kv_descriptors[index].command_id != command_id)
            continue;
        if (kv_descriptors[index].descriptor_id != descriptor_id) {
            markFailed("E_KV_TOKEN_MISMATCH: KV descriptor identity mismatch");
            return;
        }
        if (kv_terminal[index])
            return;
        if (!kv_accepted[index]) {
            if (draining)
                return;
            markFailed("E_KV_TOKEN_MISMATCH: KV terminal without an accept");
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
        const uint32_t row_count = kv_descriptors[index].rows;
        const uint64_t row_bytes = kv_descriptors[index].row_bytes;
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
        std::optional<KvErrorCandidate> candidate;
        if (!all_okay || !coverage_valid) {
            KvErrorCandidate fault;
            fault.tick = tick;
            fault.code = error_code;
            fault.source.error_class = 2;
            fault.source.core_id_or_ffff = 0xFFFF;
            fault.source.domain = mesh_abi::kMeshObjectDomainKV_RUNTIME;
            fault.source.object_kind = 9;
            fault.source.ordinal = descriptor_id;
            candidate = fault;
        }
        for (uint32_t row = 0; row < row_count; ++row) {
            const uint64_t row_offset = kv_descriptors[index].append_offset +
                uint64_t(row) * kv_descriptors[index].dst_stride_bytes;
            if (!coverage_valid) {
                append_ledger->noteBurst(kv_descriptors[index].descriptor_id,
                                         row_offset, row_bytes, false,
                                         candidate);
                continue;
            }
            uint64_t cursor = 0;
            for (const DmaCommittedSegment &segment : segments) {
                if (segment.row != row)
                    continue;
                if (cursor < segment.offset_in_row)
                    append_ledger->noteBurst(
                        kv_descriptors[index].descriptor_id,
                        row_offset + cursor,
                        segment.offset_in_row - cursor, false, candidate);
                append_ledger->noteBurst(kv_descriptors[index].descriptor_id,
                                         row_offset + segment.offset_in_row,
                                         segment.bytes, true, std::nullopt);
                cursor = segment.offset_in_row + segment.bytes;
            }
            if (cursor < row_bytes)
                append_ledger->noteBurst(
                    kv_descriptors[index].descriptor_id, row_offset + cursor,
                    row_bytes - cursor, false, candidate);
        }
        append_ledger->noteDescriptorTerminal(
            kv_descriptors[index].descriptor_id);
        kv_terminal[index] = true;
        ++terminal_kv_descriptors;
        ++phase_facts.back().terminal_descriptors;
        std::string reason;
        if (!commitDmaCount("terminal", append_ledger->terminalDelta(), tick,
                            reason)) {
            markFailed(reason);
            return;
        }
        append_ledger->clearDeltas();
        if (!coverage_valid)
            latchRunFailure("E_KV_TOKEN_MISMATCH: KV terminal bytes disagree "
                            "with its committed segments");
        const bool all_terminal =
            std::all_of(kv_terminal.begin(), kv_terminal.end(),
                        [](bool done) { return done; });
        if (failed_flag) {
            // The drain is the legal route to the owner terminal: the append
            // terminal is a valid drain fact, while no phase may advance.
            // Only issued descriptors can terminalize, so un-issued ones must
            // not keep the append (and therefore the KV drain) open.
            bool issued_terminal = true;
            for (size_t index = 0; index < kv_accepted.size(); ++index)
                if (kv_accepted[index] && !kv_terminal[index])
                    issued_terminal = false;
            if (issued_terminal)
                finishAppend(tick, reason);
            commitPendingFault();
            settleDeferredTerminal();
            return;
        }
        if (all_terminal) {
            if (!finishAppend(tick, reason)) {
                markFailed(reason);
                return;
            }
        }
        return;
    }
}

bool
ServingMeshExecutor::finishAppend(uint64_t tick, std::string &reason)
{
    KvEdgeInputs edge;
    edge.tick = tick;
    edge.append_terminals.push_back(append_ledger->appendTerminal());
    for (const KvErrorCandidate &error : append_ledger->errors())
        edge.faults.push_back(
            KvFaultEvent{request_context.identity().request_id, error});
    KvEdgeResult result;
    if (!commitEdge(edge, result, reason))
        return false;
    if (!phase_facts.empty())
        phase_facts.back().append_terminal_tick = tick;
    request_context.noteAppendTerminal(append_ledger->tokenBitmap(),
                                       std::nullopt);
    phase_append_tokens = 0;
    append_ledger.reset();
    kv_descriptors.clear();
    kv_accepted.clear();
    kv_terminal.clear();
    return advance(tick, reason);
}

bool ServingMeshExecutor::start(const ServingRequestIdentity &identity,
                                uint64_t tick, std::string &reason)
{
    request_context = ServingRequestContext(*program, identity);
    if (!request_context.resolve(reason))
        return false;
    const auto &geometry = kv->geometry();
    if (mesh_dispatch != nullptr && !mesh_dispatch->canAccessKv(
            geometry.region_base, geometry.slot_bytes * geometry.max_sessions)) {
        reason = "E_BINDING_ROLE: KV slots are not reachable through the memory endpoint";
        return false;
    }
    const auto phase_selectors = selectors(reason);
    if (phase_selectors.empty())
        return false;
    if (mesh_dispatch != nullptr &&
            !mesh_dispatch->installSelectors(phase_selectors, reason))
        return false;
    last_tick = tick;
    request_start_tick = tick;
    KvEdgeInputs admission;
    admission.tick = tick;
    admission.admissions.push_back(request_context.admissionIntent(tick));
    KvEdgeResult result;
    if (!commitEdge(admission, result, reason))
        return markFailed(reason);
    const auto outcome = result.admissions.find(identity.request_id);
    if (outcome == result.admissions.end() ||
            outcome->second != KvAdmissionOutcome::Claimed) {
        reason = "E_KV_STATE: admission was not claimed";
        return markFailed(reason);
    }
    const auto handoff = result.handoffs.find(identity.request_id);
    if (handoff != result.handoffs.end())
        rollback_token = handoff->second;
    ownership_claimed = true;
    first_phase_pending = true;
    return true;
}

bool ServingMeshExecutor::beginFirstPhase(uint64_t tick, std::string &reason)
{
    if (!first_phase_pending)
        return markFailed("E_KV_STATE: no first phase is pending");
    if (!submitPhaseEdge(tick, reason))
        return markFailed(reason);
    if (mesh_dispatch != nullptr)
        mesh_dispatch->beginFirstInstance();
    return true;
}

void
ServingMeshExecutor::onInstanceStarted(uint64_t instance_generation,
                                       uint64_t request_id,
                                       uint32_t request_generation,
                                       uint64_t tick)
{
    if (!factBelongsToThisRequest(request_id, request_generation, tick))
        return;
    if (failed_flag || phase_finished)
        return;
    const InstanceFactVerdict verdict =
        classifyInstanceFact(instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (verdict == InstanceFactVerdict::Current) {
        recordStaleFact(tick);
        return;
    }
    if (verdict == InstanceFactVerdict::Violation) {
        last_tick = std::max(last_tick, tick);
        markFailed("E_KV_STATE: core start from an unknown mesh instance");
        return;
    }
    armed_instance_generation = instance_generation;
    if (!first_phase_pending)
        return;
    KvEdgeInputs edge;
    edge.tick = tick;
    edge.core_starts.push_back(request_context.identity().request_id);
    KvEdgeResult result;
    std::string reason;
    if (!commitEdge(edge, result, reason)) {
        markFailed(reason);
        return;
    }
    first_phase_pending = false;
    rollback_token.reset();
    started = true;
    last_tick = tick;
    if (sink_ != nullptr)
        sink_->onCoreStart(request_context.identity().request_id);
}

bool ServingMeshExecutor::abortBeforeStart(uint64_t tick, std::string &reason)
{
    last_tick = std::max(last_tick, tick);
    if (!rollbackPrestart(KvTerminalStatus::Cancelled, reason)) {
        markFailed(reason);
        return false;
    }
    reason = "E_KV_STATE: request aborted before the first core start";
    markFailed(reason);
    return true;
}

void ServingMeshExecutor::onInstanceSettled(uint64_t instance_generation,
                                            uint64_t request_id,
                                            uint32_t request_generation,
                                            bool errored, uint64_t tick)
{
    if (!factBelongsToThisRequest(request_id, request_generation, tick))
        return;
    if (phase_finished)
        return;
    // One settle per instance, no matter whether the phase committed yet.
    if (settled_instance_generation &&
            instance_generation <= *settled_instance_generation) {
        recordStaleFact(tick);
        return;
    }
    const InstanceFactVerdict verdict =
        classifyInstanceFact(instance_generation, tick);
    if (verdict == InstanceFactVerdict::Stale)
        return;
    if (verdict != InstanceFactVerdict::Current) {
        last_tick = std::max(last_tick, tick);
        markFailed("E_KV_STATE: completion callback from an unknown "
                   "instance");
        return;
    }
    // The settle is consumed exactly once per instance; the instance itself
    // stays current until its phase commits, because a failed request still
    // has to accept the drain facts of that same instance.
    settled_instance_generation = instance_generation;
    const uint32_t instance_index =
        static_cast<uint32_t>(phases_committed) + 1;
    ++instance_settles;
    last_tick = tick;
    drained_instance = instance_index;
    if (errored) {
        std::string reason;
        KvEdgeInputs edge;
        edge.tick = tick;
        if (first_phase_pending && rollback_token) {
            first_phase_pending = false;
            edge.owner_terminals.push_back(request_context.rollbackTerminal(
                KvTerminalStatus::Error, *rollback_token));
        } else {
            if (phase_append_tokens != 0) {
                edge.append_terminals.push_back(KvAppendTerminal{
                    request_context.identity().request_id,
                    std::vector<bool>(phase_append_tokens, false),
                    std::nullopt});
                phase_append_tokens = 0;
            }
            KvFaultEvent fault;
            fault.request_id = request_context.identity().request_id;
            fault.candidate.tick = tick;
            fault.candidate.code = agent_abi::E_KV_STATE;
            fault.candidate.source.error_class = 2;
            fault.candidate.source.core_id_or_ffff = 0xFFFF;
            fault.candidate.source.domain = mesh_abi::kMeshObjectDomainKV_RUNTIME;
            fault.candidate.source.object_kind = 9;
            // With live KV work the record cannot take the fault yet.  Hold
            // it and re-submit once the drain makes the record admissible,
            // instead of submitting one the manager would have to fatal on.
            const ServingRequestIdentity fault_identity =
                request_context.identity();
            if (!kv->kvWorkDrained(fault_identity.session_id,
                                   fault_identity.kv_handle)) {
                pending_fault = fault;
                markFailed("E_KV_STATE: mesh instance faulted");
                settleDeferredTerminal();
                return;
            }
            edge.faults.push_back(fault);
        }
        KvEdgeResult result;
        commitEdge(edge, result, reason);
        markFailed("E_KV_STATE: mesh instance faulted");
        settleDeferredTerminal();
        return;
    }
    if (failed_flag) {
        // A failed request only completes its drain; it never advances.
        settleDeferredTerminal();
        return;
    }
    request_context.noteCoreDrain();
    if (!phase_facts.empty())
        phase_facts.back().core_drain_tick = tick;
    std::string reason;
    if (!advance(tick, reason))
        markFailed(reason);
}

bool
ServingMeshExecutor::factBelongsToThisRequest(uint64_t request_id,
                                              uint32_t request_generation,
                                              uint64_t tick)
{
    const ServingRequestIdentity &identity = request_context.identity();
    if (request_id == identity.request_id &&
            request_generation == identity.generation)
        return true;
    recordStaleFact(tick);
    return false;
}

ServingMeshExecutor::InstanceFactVerdict
ServingMeshExecutor::classifyInstanceFact(uint64_t instance_generation,
                                          uint64_t tick)
{
    if (phase_finished || completion_reported) {
        recordStaleFact(tick);
        return InstanceFactVerdict::Stale;
    }
    if (armed_instance_generation) {
        if (instance_generation < *armed_instance_generation) {
            recordStaleFact(tick);
            return InstanceFactVerdict::Stale;
        }
        if (instance_generation > *armed_instance_generation)
            return InstanceFactVerdict::Violation;
        return InstanceFactVerdict::Current;
    }
    if (settled_instance_generation &&
            instance_generation <= *settled_instance_generation) {
        recordStaleFact(tick);
        return InstanceFactVerdict::Stale;
    }
    if (instance_generation == uint64_t(phases_committed) + 1)
        return InstanceFactVerdict::PendingStart;
    return InstanceFactVerdict::Violation;
}

void
ServingMeshExecutor::recordStaleFact(uint64_t tick)
{
    ++stale_instance_facts;
    last_tick = std::max(last_tick, tick);
}

bool ServingMeshExecutor::advance(uint64_t tick, std::string &reason)
{
    if (failed_flag)
        return true;
    if (!request_context.joinSatisfied()) {
        return true;
    }
    if (!request_context.commitPhase(reason))
        return false;
    ++phases_committed;
    armed_instance_generation.reset();
    if (!phase_facts.empty()) {
        phase_facts.back().commit_tick = tick;
        if (phase_fact_sink)
            phase_fact_sink(phase_facts.back());
    }
    if (request_context.finished()) {
        if (!sealTerminalSnapshot(KvTerminalStatus::Success, tick, reason))
            return false;
        phase_finished = true;
        reportCompletion();
        if (mesh_dispatch != nullptr)
            mesh_dispatch->resumeInstance();
        return true;
    }
    if (submitted_request) {
        if (!bindRequestTensors(*submitted_request, reason))
            return false;
    } else
        instance_binding = ServingInstanceBinding(request_context.identity().request_id,
                                                  request_context.identity().generation);
    if (!submitPhaseEdge(tick, reason))
        return false;
    if (submitted_request &&
            !bindPhaseFillContent(*submitted_request, reason))
        return false;
    if (mesh_dispatch != nullptr)
        mesh_dispatch->resumeInstance();
    return true;
}

}
}

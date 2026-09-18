#include "dev/ai_mesh/serving_request_context.hh"

#include "dev/ai_mesh/mesh_serving_projection.hh"

#include <algorithm>
#include <utility>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

uint32_t
pathKindOfSqFlags(uint16_t sq_flags)
{
    const bool reuse = (sq_flags & agent_abi::kSqFlagsREQUIRE_KV_REUSE) != 0;
    const bool reprefill = (sq_flags & agent_abi::kSqFlagsALLOW_REPREFILL) != 0;
    if (reuse && reprefill)
        return mesh_abi::kPathKindValuesMask;
    if (reuse)
        return mesh_abi::kPathKindKV_REUSE;
    if (reprefill)
        return mesh_abi::kPathKindREPREFILL;
    return mesh_abi::kPathKindINITIAL_PREFILL;
}

namespace
{

constexpr uint16_t kPrefill = mesh_abi::kPhasePREFILL;
constexpr uint16_t kDecode = mesh_abi::kPhaseDECODE;
constexpr uint16_t kPublish = mesh_abi::kPhasePUBLISH;

uint32_t detailCodeOf(const std::string &code)
{
    if (code == "E_REQUEST_PROFILE")
        return agent_abi::E_REQUEST_PROFILE;
    if (code == "E_KV_TOKEN_MISMATCH")
        return agent_abi::E_KV_TOKEN_MISMATCH;
    if (code == "E_HOST_IO_SIZE_MISMATCH")
        return agent_abi::E_HOST_IO_SIZE_MISMATCH;
    if (code == "E_KV_FLAG_COMBINATION")
        return agent_abi::E_KV_FLAG_COMBINATION;
    return agent_abi::E_KV_STATE;
}

ServingPhaseStep makeStep(const mesh_abi::AgentInstanceProfile &instance,
                          uint32_t cached_tokens_after)
{
    ServingPhaseStep item;
    item.phase = instance.phase;
    item.instance_profile_id = instance.instance_profile_id;
    item.mesh_profile_id = instance.mesh_profile_id;
    item.member_count = instance.member_count;
    item.valid_tokens_per_member = instance.valid_tokens_per_member;
    item.kv_tokens_before = instance.kv_tokens_before;
    item.decode_chunk_tokens = instance.decode_chunk_tokens;
    item.cached_tokens_after = cached_tokens_after;
    item.kv_read_bytes_per_member = instance.kv_read_bytes_per_member;
    item.kv_write_bytes_per_member = instance.kv_write_bytes_per_member;
    item.host_input_dma_bytes_per_member =
        instance.host_input_dma_bytes_per_member;
    item.host_output_dma_bytes_per_member =
        instance.host_output_dma_bytes_per_member;
    return item;
}

}

ServingRequestContext::ServingRequestContext(
    const DecodedProgram &program_ref, const ServingRequestIdentity &id)
    : program(&program_ref), request_identity(id)
{}

bool
ServingRequestContext::fail(std::string &reason, const std::string &code,
                            const std::string &detail) const
{
    reason = code + ": " + detail;
    reject_detail = detailCodeOf(code);
    return false;
}

const mesh_abi::AgentRequestProfile *
ServingRequestContext::requestProfile() const
{
    for (const auto &request : program->agent_request_profiles) {
        if (request.program_id == request_identity.program_id &&
                request.profile_id == request_identity.profile_id)
            return &request;
    }
    return nullptr;
}

const mesh_abi::AgentInstanceProfile *
ServingRequestContext::select(uint16_t phase, uint32_t kv_tokens_before,
                              std::string &reason) const
{
    const mesh_abi::AgentInstanceProfile *match = nullptr;
    uint32_t matches = 0;
    for (const auto &instance : program->agent_instance_profiles) {
        if (instance.request_program_id != request_identity.program_id ||
                instance.request_profile_id != request_identity.profile_id ||
                instance.path_kind != request_identity.path_kind ||
                instance.phase != phase ||
                instance.kv_tokens_before != kv_tokens_before)
            continue;
        match = &instance;
        ++matches;
    }
    if (matches != 1) {
        fail(reason, "E_REQUEST_PROFILE",
             "no exact instance profile at the token cursor");
        return nullptr;
    }
    return match;
}

uint32_t
ServingRequestContext::appendTokens(const ServingPhaseStep &item) const
{
    const auto *request = requestProfile();
    if (request == nullptr || request->kv_bytes_per_token == 0)
        return 0;
    return static_cast<uint32_t>(item.kv_write_bytes_per_member /
                                 request->kv_bytes_per_token);
}

bool
ServingRequestContext::validatePrefill(
    const mesh_abi::AgentRequestProfile &request,
    const mesh_abi::AgentInstanceProfile &instance, uint32_t tokens,
    uint64_t host_input, std::string &reason) const
{
    if (instance.primary_input_symbol_id == 0 ||
            instance.primary_kv_symbol_id == 0 ||
            instance.primary_output_symbol_id != 0)
        return fail(reason, "E_REQUEST_PROFILE",
                    "prefill primary roles are not the contract set");
    if (instance.valid_tokens_per_member != tokens ||
            instance.decode_chunk_tokens != 0)
        return fail(reason, "E_REQUEST_PROFILE",
                    "prefill token cursor is inconsistent");
    if (instance.kv_write_bytes_per_member !=
            uint64_t(tokens) * request.kv_bytes_per_token)
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "prefill append bytes do not match the token count");
    if (instance.host_input_dma_bytes_per_member != host_input ||
            instance.host_output_dma_bytes_per_member != 0)
        return fail(reason, "E_HOST_IO_SIZE_MISMATCH",
                    "prefill Host ranges do not match the request profile");
    return true;
}

bool
ServingRequestContext::validateDecode(
    const mesh_abi::AgentRequestProfile &request,
    const mesh_abi::AgentInstanceProfile &instance, uint32_t cursor,
    std::string &reason) const
{
    if (instance.primary_kv_symbol_id == 0 ||
            instance.primary_input_symbol_id != 0 ||
            instance.primary_output_symbol_id != 0)
        return fail(reason, "E_REQUEST_PROFILE",
                    "decode primary roles are not the contract set");
    const uint32_t chunk = instance.decode_chunk_tokens;
    if (chunk == 0 || chunk > request.output_tokens ||
            instance.valid_tokens_per_member != chunk)
        return fail(reason, "E_REQUEST_PROFILE",
                    "decode chunk is inconsistent with the plan");
    if (instance.kv_read_bytes_per_member !=
            uint64_t(cursor) * request.kv_bytes_per_token)
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "decode read bytes do not match the token cursor");
    if (instance.kv_write_bytes_per_member !=
            uint64_t(chunk) * request.kv_bytes_per_token)
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "decode append bytes do not match the chunk");
    if (instance.host_input_dma_bytes_per_member != 0 ||
            instance.host_output_dma_bytes_per_member != 0)
        return fail(reason, "E_HOST_IO_SIZE_MISMATCH",
                    "decode must not touch Host payload");
    return true;
}

bool
ServingRequestContext::validatePublish(
    const mesh_abi::AgentRequestProfile &request,
    const mesh_abi::AgentInstanceProfile &instance, uint32_t cursor,
    std::string &reason) const
{
    if (instance.primary_output_symbol_id == 0 ||
            instance.primary_input_symbol_id != 0 ||
            instance.primary_kv_symbol_id != 0)
        return fail(reason, "E_REQUEST_PROFILE",
                    "publish primary roles are not the contract set");
    if (instance.valid_tokens_per_member != 0 ||
            instance.decode_chunk_tokens != 0)
        return fail(reason, "E_REQUEST_PROFILE",
                    "publish must not append tokens");
    if (instance.kv_read_bytes_per_member != 0 ||
            instance.kv_write_bytes_per_member != 0)
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "publish must not read or write KV");
    if (instance.host_input_dma_bytes_per_member != 0 ||
            instance.host_output_dma_bytes_per_member !=
            request.host_output_bytes)
        return fail(reason, "E_HOST_IO_SIZE_MISMATCH",
                    "publish must store the planned output bytes");
    if (instance.kv_tokens_before != cursor)
        return fail(reason, "E_REQUEST_PROFILE",
                    "publish token cursor does not close the plan");
    return true;
}

bool
ServingRequestContext::resolve(std::string &reason)
{
    steps.clear();
    phase_index = 0;
    core_started = false;
    core_drained = false;
    append_terminal_seen = false;
    append_prefix_complete = false;
    reject_detail = 0;
    const auto *request = requestProfile();
    if (request == nullptr)
        return fail(reason, "E_REQUEST_PROFILE", "unknown request profile");
    const uint16_t kv_flags = request_identity.kv_flags &
        (agent_abi::kSqFlagsREQUIRE_KV_REUSE |
         agent_abi::kSqFlagsALLOW_REPREFILL);
    const bool reuse = (kv_flags & agent_abi::kSqFlagsREQUIRE_KV_REUSE) != 0;
    const bool reprefill = (kv_flags & agent_abi::kSqFlagsALLOW_REPREFILL) != 0;
    if (request_identity.repair_round == 0) {
        if (kv_flags != 0)
            return fail(reason, "E_KV_FLAG_COMBINATION",
                        "round 0 must not request a KV reuse path");
    } else if (reuse == reprefill) {
        return fail(reason, "E_KV_FLAG_COMBINATION",
                    "a repair round needs exactly one KV path bit");
    }
    request_identity.path_kind = pathKindOfSqFlags(kv_flags);
    if (request_identity.path_kind > mesh_abi::kPathKindREPREFILL)
        return fail(reason, "E_REQUEST_PROFILE", "path kind is outside range");
    if (!(request->path_mask & (1u << request_identity.path_kind)))
        return fail(reason, "E_REQUEST_PROFILE",
                    "path kind is not declared reachable");
    const uint32_t start = reuse ? request->delta_input_tokens :
        request->full_input_tokens;
    if (reuse && start != request->expected_cached_tokens)
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "reused prefix must equal the expected cached tokens");
    const uint32_t expected_cached = reuse ? request->expected_cached_tokens : 0;
    if (request_identity.cached_tokens != expected_cached)
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "round cached tokens do not match the selected path");
    if (request_identity.kv_bytes_per_token != request->kv_bytes_per_token)
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "manager token geometry differs from the request profile");
    const std::array<uint8_t, 32> key_base =
        programProfileKeyBaseDigest(*program);
    if (requestProfileKey(*request, key_base) !=
            request_identity.requested_profile_key)
        return fail(reason, "E_REQUEST_PROFILE",
                    "requested profile key does not match the serving profile");
    const uint64_t proof_input_bytes =
        reuse ? request->delta_input_dma_bytes : request->full_input_dma_bytes;
    if (request_identity.input_bytes != proof_input_bytes ||
            request_identity.input_tokens != start)
        return fail(reason, "E_HOST_IO_SIZE_MISMATCH",
                    "Host input range does not match the request profile");
    if (request_identity.output_tokens != request->output_tokens ||
            request_identity.output_capacity_bytes < request->host_output_bytes)
        return fail(reason, "E_HOST_IO_SIZE_MISMATCH",
                    "Host output range does not match the request profile");

    const auto *prefill = select(kPrefill, 0, reason);
    if (prefill == nullptr)
        return false;
    const uint64_t host_input = reuse ? request->delta_input_dma_bytes :
        request->full_input_dma_bytes;
    if (!validatePrefill(*request, *prefill, start, host_input, reason))
        return false;
    if (prefill->kv_read_bytes_per_member !=
            (reuse ? request->delta_input_dma_bytes : 0))
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "prefill read bytes do not match the path");
    steps.push_back(makeStep(*prefill, start));

    uint32_t produced = 0;
    while (produced < request->output_tokens) {
        const auto *decode = select(kDecode, start + produced, reason);
        if (decode == nullptr)
            return false;
        if (!validateDecode(*request, *decode, start + produced, reason))
            return false;
        const uint32_t chunk = decode->decode_chunk_tokens;
        if (produced + chunk > request->output_tokens)
            return fail(reason, "E_REQUEST_PROFILE",
                        "decode chunk overruns the planned output tokens");
        produced += chunk;
        steps.push_back(makeStep(*decode, start + produced));
    }
    const auto *publish = select(kPublish, start + produced, reason);
    if (publish == nullptr)
        return false;
    if (!validatePublish(*request, *publish, start + produced, reason))
        return false;
    steps.push_back(makeStep(*publish, start + produced));
    if (steps.back().cached_tokens_after !=
            request->full_input_tokens + request->output_tokens)
        return fail(reason, "E_KV_TOKEN_MISMATCH",
                    "final cursor must equal full context plus output");
    return true;
}

uint32_t
ServingRequestContext::requiredTokensAfterRound() const
{
    const auto *request = requestProfile();
    if (request == nullptr)
        return 0;
    return request->full_input_tokens + request->output_tokens;
}

const mesh_abi::AgentInstanceProfile *
ServingRequestContext::instanceProfile() const
{
    if (finished())
        return nullptr;
    std::string reason;
    return select(step().phase, step().kv_tokens_before, reason);
}

KvAdmissionIntent
ServingRequestContext::admissionIntent(uint64_t tick) const
{
    KvAdmissionIntent intent;
    intent.request_id = request_identity.request_id;
    intent.session_id = request_identity.session_id;
    intent.kv_handle = request_identity.kv_handle;
    intent.generation = request_identity.generation;
    intent.contract_digest = request_identity.contract_digest;
    intent.ready_tick = tick;
    if (request_identity.path_kind == mesh_abi::kPathKindKV_REUSE)
        intent.flags = agent_abi::kSqFlagsREQUIRE_KV_REUSE;
    else if (request_identity.path_kind == mesh_abi::kPathKindREPREFILL)
        intent.flags = agent_abi::kSqFlagsALLOW_REPREFILL;
    const auto *request = requestProfile();
    if (request != nullptr) {
        intent.required_cached_tokens = request->expected_cached_tokens;
        intent.required_tokens_after_round =
            request->full_input_tokens + request->output_tokens;
    }
    return intent;
}

void
ServingRequestContext::beginPhase(KvEdgeInputs &edge)
{
    if (finished())
        return;
    core_drained = false;
    append_terminal_seen = false;
    append_prefix_complete = false;
    const ServingPhaseStep &item = step();
    if (item.phase != kPublish) {
        for (uint32_t ordinal = 0; ordinal < item.member_count; ++ordinal) {
            KvViewArm arm;
            arm.request_id = request_identity.request_id;
            arm.member_ordinal = ordinal;
            edge.view_arms.push_back(arm);
        }
    }
    const uint32_t tokens = appendTokens(item);
    if (tokens != 0) {
        KvAppendArm arm;
        arm.request_id = request_identity.request_id;
        arm.base_tokens = item.kv_tokens_before;
        arm.append_tokens = tokens;
        edge.append_arms.push_back(arm);
    }
}

void
ServingRequestContext::noteAppendTerminal(
    std::vector<bool> tokens_ok,
    std::optional<std::array<uint8_t, 32>> digest)
{
    append_terminal_seen = true;
    append_prefix_complete = !tokens_ok.empty() &&
        std::all_of(tokens_ok.begin(), tokens_ok.end(),
                    [](bool ok) { return ok; });
    if (digest)
        content_digest = digest;
}

bool
ServingRequestContext::joinSatisfied() const
{
    if (finished() || !core_drained)
        return false;
    if (step().phase == kPublish)
        return true;
    return append_terminal_seen && append_prefix_complete;
}

bool
ServingRequestContext::commitPhase(std::string &reason)
{
    if (finished())
        return fail(reason, "E_KV_STATE", "phase plan is already complete");
    if (!joinSatisfied())
        return fail(reason, "E_KV_STATE",
                    "core drain and append terminal are not both satisfied");
    ++phase_index;
    core_drained = false;
    append_terminal_seen = false;
    append_prefix_complete = false;
    return true;
}

KvOwnerTerminal
ServingRequestContext::ownerTerminal(KvTerminalStatus status) const
{
    KvOwnerTerminal terminal;
    terminal.request_id = request_identity.request_id;
    terminal.status = status;
    terminal.strict = true;
    return terminal;
}

KvOwnerTerminal
ServingRequestContext::rollbackTerminal(KvTerminalStatus status,
                                        const KvRollbackToken &token) const
{
    KvOwnerTerminal terminal;
    terminal.request_id = request_identity.request_id;
    terminal.status = status;
    terminal.token = token;
    terminal.strict = false;
    return terminal;
}

}
}

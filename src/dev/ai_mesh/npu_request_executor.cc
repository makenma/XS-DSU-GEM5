#include "dev/ai_mesh/npu_request_executor.hh"

#include <utility>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

NpuAdmission
NpuProtocolProbeExecutor::submit(const NpuExecutionRequest &request)
{
    if (sink_ != nullptr)
        sink_->onCoreStart(request.requestId);
    return NpuAdmission{true, 0};
}

NpuExecutionCompletion
NpuProtocolProbeExecutor::completionFor(
    const NpuExecutionRequest &) const
{
    return NpuExecutionCompletion{};
}

FullContextSurrogateExecutor::FullContextSurrogateExecutor(
    SurrogateProfileRegistry registry)
    : registry(std::move(registry))
{}

const SurrogateProfile *
FullContextSurrogateExecutor::profileFor(
    const SurrogateProfileRegistry &registry,
    const NpuExecutionRequest &request)
{
    return registry.find(request.programId, request.profileId);
}

NpuAdmission
FullContextSurrogateExecutor::submit(const NpuExecutionRequest &request)
{
    const SurrogateProfile *profile = profileFor(registry, request);
    if (profile == nullptr)
        return NpuAdmission{false, agent_abi::E_WORKLOAD_PLAN_MISMATCH};
    const bool matches = profile->profileKey == request.requestedProfileKey &&
        profile->inputTokens == request.inputTokens &&
        profile->inputBytes == request.inputBytes &&
        profile->outputTokens == request.maxOutputTokens;
    if (!matches)
        return NpuAdmission{false, agent_abi::E_WORKLOAD_PLAN_MISMATCH};
    if (profile->outputBytes > request.outputCapacityBytes)
        return NpuAdmission{false, agent_abi::E_OUTPUT_CAPACITY};
    return NpuAdmission{true, 0};
}

uint64_t
FullContextSurrogateExecutor::modeledServiceNs(
    const NpuExecutionRequest &request) const
{
    const SurrogateProfile *profile = profileFor(registry, request);
    return profile == nullptr ? 0 : profile->serviceNs;
}

NpuExecutionCompletion
FullContextSurrogateExecutor::completionFor(
    const NpuExecutionRequest &request) const
{
    NpuExecutionCompletion completion;
    const SurrogateProfile *profile = profileFor(registry, request);
    if (profile == nullptr)
        return completion;
    completion.success = true;
    completion.outputBytes = profile->outputBytes;
    completion.semanticDigest = semanticDigestFor(request, *profile);
    completion.outputPayload = surrogateOutputBytes(
        completion.semanticDigest.data(), profile->outputBytes);
    return completion;
}

std::array<uint8_t, 32>
FullContextSurrogateExecutor::semanticDigestFor(
    const NpuExecutionRequest &request, const SurrogateProfile &profile)
{
    const std::array<uint8_t, 32> seed = surrogateSeed(
        request.inputDigest.data(), request.programId, request.profileId,
        profile.profileKey);
    std::vector<std::array<uint8_t, 32>> tokenDigests;
    tokenDigests.reserve(profile.outputTokens);
    for (uint32_t ordinal = 0; ordinal < profile.outputTokens; ++ordinal)
        tokenDigests.push_back(surrogateTokenDigest(seed.data(), ordinal));
    return surrogateOutputPrefixDigest(
        request.workloadDigest.data(), request.workloadPlanItemId,
        profile.outputTokens, tokenDigests);
}

}
}

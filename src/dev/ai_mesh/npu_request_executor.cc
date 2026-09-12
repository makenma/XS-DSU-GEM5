#include "dev/ai_mesh/npu_request_executor.hh"

#include <utility>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

NpuExecutionOutcome
NpuProtocolProbeExecutor::accept(const NpuExecutionRequest &)
{
    return NpuExecutionOutcome{0, true, true};
}

FullContextSurrogateExecutor::FullContextSurrogateExecutor(
    SurrogateProfileRegistry registry)
    : registry(std::move(registry))
{}

const SurrogateProfile *
FullContextSurrogateExecutor::profileFor(
    const NpuExecutionRequest &request) const
{
    return registry.find(request.programId, request.profileId);
}

NpuExecutionOutcome
FullContextSurrogateExecutor::accept(const NpuExecutionRequest &request)
{
    const SurrogateProfile *profile = profileFor(request);
    if (profile == nullptr)
        return NpuExecutionOutcome{0, false, false,
                                   agent_abi::E_WORKLOAD_PLAN_MISMATCH};
    const bool matches = profile->profileKey == request.requestedProfileKey &&
        profile->inputTokens == request.inputTokens &&
        profile->inputBytes == request.inputBytes &&
        profile->outputTokens == request.maxOutputTokens;
    if (!matches)
        return NpuExecutionOutcome{0, false, false,
                                   agent_abi::E_WORKLOAD_PLAN_MISMATCH};
    if (profile->outputBytes > request.outputCapacityBytes)
        return NpuExecutionOutcome{0, false, false,
                                   agent_abi::E_OUTPUT_CAPACITY};
    return NpuExecutionOutcome{profile->serviceNs, false, true, 0};
}

std::array<uint8_t, 32>
FullContextSurrogateExecutor::semanticDigest(
    const NpuExecutionRequest &request) const
{
    const SurrogateProfile *profile = profileFor(request);
    if (profile == nullptr)
        return {};
    const std::array<uint8_t, 32> seed = surrogateSeed(
        request.inputDigest.data(), request.programId, request.profileId,
        profile->profileKey);
    std::vector<std::array<uint8_t, 32>> tokenDigests;
    tokenDigests.reserve(profile->outputTokens);
    for (uint32_t ordinal = 0; ordinal < profile->outputTokens; ++ordinal)
        tokenDigests.push_back(surrogateTokenDigest(seed.data(), ordinal));
    return surrogateOutputPrefixDigest(
        request.workloadDigest.data(), request.workloadPlanItemId,
        profile->outputTokens, tokenDigests);
}

std::vector<uint8_t>
FullContextSurrogateExecutor::outputPayload(
    const NpuExecutionRequest &request) const
{
    const SurrogateProfile *profile = profileFor(request);
    if (profile == nullptr)
        return {};
    const std::array<uint8_t, 32> digest = semanticDigest(request);
    return surrogateOutputBytes(digest.data(), profile->outputBytes);
}

uint64_t
FullContextSurrogateExecutor::outputBytes(
    const NpuExecutionRequest &request) const
{
    const SurrogateProfile *profile = profileFor(request);
    return profile == nullptr ? 0 : profile->outputBytes;
}

}
}

#include <gtest/gtest.h>

#include <cstdint>
#include <optional>
#include <utility>
#include <vector>

#include "dev/ai_mesh/agent_surrogate_codec.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/npu_request_executor.hh"

namespace
{

std::optional<gem5::ai_mesh::SurrogateProfileRegistry>
registryFromSection()
{
    std::vector<uint8_t> section;
    const uint8_t digest[32] = {
        0x0f, 0xf7, 0xcd, 0xf1, 0xbb, 0x69, 0x56, 0x35,
        0xee, 0x95, 0xf4, 0x3e, 0x62, 0x45, 0xec, 0x3d,
        0xdf, 0xe2, 0x1d, 0x4a, 0x1b, 0xad, 0x05, 0x21,
        0x0e, 0xd4, 0xd3, 0xc1, 0x82, 0x07, 0x77, 0x00,
    };
    section.insert(section.end(), digest, digest + 32);
    const uint8_t body[] = {
        1, 0, 0, 0,
        1, 0, 1, 0,
        0x01, 0x10, 0, 0, 0, 0, 0, 0,
        0x00, 0x04, 0, 0,
        0x00, 0x40, 0, 0, 0, 0, 0, 0,
        0x80, 0, 0, 0,
        0x00, 0x10, 0, 0, 0, 0, 0, 0,
        0x00, 0x09, 0x3d, 0x00, 0, 0, 0, 0,
        0x00, 0x10, 0, 0,
    };
    section.insert(section.end(), body, body + sizeof(body));
    return gem5::ai_mesh::SurrogateProfileRegistry::parse(
        section.data(), section.size());
}

gem5::ai_mesh::NpuExecutionRequest
matchingRequest()
{
    gem5::ai_mesh::NpuExecutionRequest request;
    request.requestId = 1;
    request.programId = 1;
    request.profileId = 1;
    request.inputTokens = 1024;
    request.inputBytes = 16384;
    request.maxOutputTokens = 128;
    request.outputTokens = 128;
    request.requestedProfileKey = 4097;
    request.outputCapacityBytes = 4096;
    return request;
}

class RecordingSink : public gem5::ai_mesh::NpuCompletionSink
{
  public:
    void onCoreStart(uint64_t requestId) override
    {
        core_starts.push_back(requestId);
    }

    void onRequestComplete(
        const gem5::ai_mesh::NpuExecutionRequest &request,
        const gem5::ai_mesh::NpuExecutionCompletion &completion) override
    {
        completed.push_back(request.requestId);
        payload_bytes = completion.outputPayload.size();
        output_bytes = completion.outputBytes;
    }

    std::vector<uint64_t> core_starts;
    std::vector<uint64_t> completed;
    size_t payload_bytes = 0;
    uint64_t output_bytes = 0;
};

}

TEST(NpuProtocolProbeExecutor, SubmitOnlyReportsAdmissionAndCoreStart)
{
    gem5::ai_mesh::NpuProtocolProbeExecutor executor;
    RecordingSink sink;
    executor.attachSink(&sink);
    gem5::ai_mesh::NpuExecutionRequest request;
    request.sqSequence = 7;
    request.requestId = 42;
    request.completionCookie = 42;
    const gem5::ai_mesh::NpuAdmission admission = executor.submit(request);
    ASSERT_EQ(sink.core_starts.size(), 1u);
    EXPECT_EQ(sink.core_starts[0], 42u);
    EXPECT_TRUE(admission.admitted);
    EXPECT_EQ(admission.rejectDetail, 0u);
    EXPECT_EQ(executor.kind(),
              gem5::ai_mesh::NpuExecutorKind::ProtocolProbe);
    EXPECT_EQ(executor.modeledServiceNs(request), 0u);
    const gem5::ai_mesh::NpuExecutionCompletion completion =
        executor.completionFor(request);
    EXPECT_TRUE(completion.outputPayload.empty());
    EXPECT_EQ(completion.outputBytes, 0u);
    EXPECT_EQ(completion.semanticDigest, (std::array<uint8_t, 32>{}));
}

TEST(FullContextSurrogateExecutor, SubmitAdmitsWithoutFabricatingCompletion)
{
    auto registry = registryFromSection();
    ASSERT_TRUE(registry.has_value());
    gem5::ai_mesh::FullContextSurrogateExecutor executor(
        std::move(*registry));
    const gem5::ai_mesh::NpuExecutionRequest request = matchingRequest();
    const gem5::ai_mesh::NpuAdmission admission = executor.submit(request);
    EXPECT_TRUE(admission.admitted);
    EXPECT_EQ(admission.rejectDetail, 0u);
    EXPECT_EQ(executor.kind(),
              gem5::ai_mesh::NpuExecutorKind::FullContextSurrogate);
    EXPECT_EQ(executor.modeledServiceNs(request), 4000000u);
    const gem5::ai_mesh::NpuExecutionCompletion completion =
        executor.completionFor(request);
    EXPECT_EQ(completion.outputBytes, 4096u);
    ASSERT_EQ(completion.outputPayload.size(), 4096u);
    EXPECT_EQ(completion.semanticDigest.size(), 32u);
    EXPECT_FALSE(completion.coreStarted);
}

TEST(FullContextSurrogateExecutor, CompletionCarriesTheSurrogatePayload)
{
    auto registry = registryFromSection();
    ASSERT_TRUE(registry.has_value());
    gem5::ai_mesh::FullContextSurrogateExecutor executor(
        std::move(*registry));
    gem5::ai_mesh::NpuExecutionRequest request = matchingRequest();
    request.inputDigest.fill(0xaa);
    request.workloadDigest.fill(0x5a);
    request.workloadPlanItemId = 7;
    const std::array<uint8_t, 32> seed = gem5::ai_mesh::surrogateSeed(
        request.inputDigest.data(), 1, 1, 4097);
    std::vector<std::array<uint8_t, 32>> tokenDigests;
    for (uint32_t ordinal = 0; ordinal < 128; ++ordinal)
        tokenDigests.push_back(
            gem5::ai_mesh::surrogateTokenDigest(seed.data(), ordinal));
    const gem5::ai_mesh::NpuExecutionCompletion completion =
        executor.completionFor(request);
    EXPECT_EQ(completion.semanticDigest,
              gem5::ai_mesh::surrogateOutputPrefixDigest(
                  request.workloadDigest.data(), 7, 128, tokenDigests));
    EXPECT_EQ(completion.outputPayload,
              gem5::ai_mesh::surrogateOutputBytes(
                  completion.semanticDigest.data(), 4096));
}

TEST(FullContextSurrogateExecutor, RejectsUnknownProfileIdentity)
{
    auto registry = registryFromSection();
    ASSERT_TRUE(registry.has_value());
    gem5::ai_mesh::FullContextSurrogateExecutor executor(
        std::move(*registry));
    gem5::ai_mesh::NpuExecutionRequest request;
    request.requestId = 2;
    request.programId = 9;
    request.profileId = 9;
    EXPECT_EQ(executor.profileFor(request), nullptr);
    const gem5::ai_mesh::NpuAdmission admission = executor.submit(request);
    EXPECT_FALSE(admission.admitted);
    EXPECT_EQ(admission.rejectDetail,
              gem5::ai_mesh::agent_abi::E_WORKLOAD_PLAN_MISMATCH);
    const gem5::ai_mesh::NpuExecutionCompletion completion =
        executor.completionFor(request);
    EXPECT_TRUE(completion.outputPayload.empty());
    EXPECT_EQ(completion.semanticDigest, (std::array<uint8_t, 32>{}));
    EXPECT_EQ(completion.outputBytes, 0u);
}

TEST(FullContextSurrogateExecutor, RejectsWireFieldMismatch)
{
    auto registry = registryFromSection();
    ASSERT_TRUE(registry.has_value());
    gem5::ai_mesh::FullContextSurrogateExecutor executor(
        std::move(*registry));
    const gem5::ai_mesh::agent_abi::DetailCode mismatch =
        gem5::ai_mesh::agent_abi::E_WORKLOAD_PLAN_MISMATCH;
    gem5::ai_mesh::NpuExecutionRequest request = matchingRequest();
    request.requestedProfileKey = 4098;
    EXPECT_FALSE(executor.submit(request).admitted);
    EXPECT_EQ(executor.submit(request).rejectDetail, mismatch);
    request = matchingRequest();
    request.inputTokens = 2048;
    EXPECT_FALSE(executor.submit(request).admitted);
    request = matchingRequest();
    request.outputCapacityBytes = 4095;
    EXPECT_EQ(executor.submit(request).rejectDetail,
              gem5::ai_mesh::agent_abi::E_OUTPUT_CAPACITY);
}

TEST(FullContextSurrogateExecutor, PostCompletionReachesTheSink)
{
    auto registry = registryFromSection();
    ASSERT_TRUE(registry.has_value());
    gem5::ai_mesh::FullContextSurrogateExecutor executor(
        std::move(*registry));
    RecordingSink sink;
    executor.attachSink(&sink);
    const gem5::ai_mesh::NpuExecutionRequest request = matchingRequest();
    ASSERT_TRUE(executor.submit(request).admitted);
    executor.postCompletion(request);
    ASSERT_EQ(sink.completed.size(), 1u);
    EXPECT_EQ(sink.completed[0], 1u);
    EXPECT_EQ(sink.payload_bytes, 4096u);
    EXPECT_EQ(sink.output_bytes, 4096u);
}

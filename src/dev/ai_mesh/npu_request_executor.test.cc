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

}

TEST(NpuProtocolProbeExecutor, AcceptsSynchronouslyWithCoreStartProbe)
{
    gem5::ai_mesh::NpuProtocolProbeExecutor executor;
    gem5::ai_mesh::NpuExecutionRequest request;
    request.sqSequence = 7;
    request.requestId = 42;
    request.completionCookie = 42;
    request.inputAddress = 0x20000;
    request.inputBytes = 32;
    request.outputAddress = 0x30000;
    request.metadataAddress = 0x40000;
    request.qos = 4;
    const gem5::ai_mesh::NpuExecutionOutcome outcome =
        executor.accept(request);
    EXPECT_TRUE(outcome.coreStartProbe);
    EXPECT_EQ(outcome.serviceNs, 0u);
    EXPECT_TRUE(outcome.admitted);
    EXPECT_TRUE(executor.idle());
}

TEST(FullContextSurrogateExecutor, AdmitsMatchingProfileWithServiceNs)
{
    auto registry = registryFromSection();
    ASSERT_TRUE(registry.has_value());
    gem5::ai_mesh::FullContextSurrogateExecutor executor(
        std::move(*registry));
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
    const gem5::ai_mesh::SurrogateProfile *profile =
        executor.profileFor(request);
    ASSERT_NE(profile, nullptr);
    EXPECT_EQ(profile->profileKey, 4097u);
    const gem5::ai_mesh::NpuExecutionOutcome outcome =
        executor.accept(request);
    EXPECT_TRUE(outcome.admitted);
    EXPECT_FALSE(outcome.coreStartProbe);
    EXPECT_EQ(outcome.serviceNs, 4000000u);
    EXPECT_EQ(outcome.rejectDetail, 0u);
    EXPECT_TRUE(executor.idle());
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
    const gem5::ai_mesh::NpuExecutionOutcome outcome =
        executor.accept(request);
    EXPECT_FALSE(outcome.admitted);
    EXPECT_FALSE(outcome.coreStartProbe);
    EXPECT_EQ(outcome.serviceNs, 0u);
    EXPECT_EQ(outcome.rejectDetail,
              gem5::ai_mesh::agent_abi::E_WORKLOAD_PLAN_MISMATCH);
    EXPECT_TRUE(executor.outputPayload(request).empty());
    EXPECT_EQ(executor.semanticDigest(request),
              (std::array<uint8_t, 32>{}));
    EXPECT_EQ(executor.outputBytes(request), 0u);
}

namespace
{

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
    EXPECT_FALSE(executor.accept(request).admitted);
    EXPECT_EQ(executor.accept(request).rejectDetail, mismatch);
    request = matchingRequest();
    request.inputTokens = 2048;
    EXPECT_FALSE(executor.accept(request).admitted);
    EXPECT_EQ(executor.accept(request).rejectDetail, mismatch);
    request = matchingRequest();
    request.inputBytes = 8192;
    EXPECT_FALSE(executor.accept(request).admitted);
    EXPECT_EQ(executor.accept(request).rejectDetail, mismatch);
    request = matchingRequest();
    request.maxOutputTokens = 127;
    EXPECT_FALSE(executor.accept(request).admitted);
    EXPECT_EQ(executor.accept(request).rejectDetail, mismatch);
}

TEST(FullContextSurrogateExecutor, RejectsOutputCapacityShortfall)
{
    auto registry = registryFromSection();
    ASSERT_TRUE(registry.has_value());
    gem5::ai_mesh::FullContextSurrogateExecutor executor(
        std::move(*registry));
    gem5::ai_mesh::NpuExecutionRequest request = matchingRequest();
    request.outputCapacityBytes = 4095;
    const gem5::ai_mesh::NpuExecutionOutcome outcome =
        executor.accept(request);
    EXPECT_FALSE(outcome.admitted);
    EXPECT_EQ(outcome.rejectDetail,
              gem5::ai_mesh::agent_abi::E_OUTPUT_CAPACITY);
}

TEST(FullContextSurrogateExecutor, SurrogateOutputsDeriveFromProfileIdentity)
{
    auto registry = registryFromSection();
    ASSERT_TRUE(registry.has_value());
    gem5::ai_mesh::FullContextSurrogateExecutor executor(
        std::move(*registry));
    gem5::ai_mesh::NpuExecutionRequest request;
    request.requestId = 1;
    request.programId = 1;
    request.profileId = 1;
    request.inputDigest.fill(0xaa);
    request.workloadDigest.fill(0x5a);
    request.workloadPlanItemId = 7;
    const std::array<uint8_t, 32> seed =
        gem5::ai_mesh::surrogateSeed(
            request.inputDigest.data(), 1, 1, 4097);
    std::vector<std::array<uint8_t, 32>> tokenDigests;
    for (uint32_t ordinal = 0; ordinal < 128; ++ordinal)
        tokenDigests.push_back(
            gem5::ai_mesh::surrogateTokenDigest(seed.data(), ordinal));
    EXPECT_EQ(executor.semanticDigest(request),
              gem5::ai_mesh::surrogateOutputPrefixDigest(
                  request.workloadDigest.data(), 7, 128, tokenDigests));
    EXPECT_EQ(executor.outputBytes(request), 4096u);
    const std::vector<uint8_t> payload = executor.outputPayload(request);
    ASSERT_EQ(payload.size(), 4096u);
    EXPECT_EQ(payload, gem5::ai_mesh::surrogateOutputBytes(
                           executor.semanticDigest(request).data(), 4096));
}

TEST(NpuProtocolProbeExecutor, ProbeExecutorHasNoSurrogateOutputs)
{
    gem5::ai_mesh::NpuProtocolProbeExecutor executor;
    gem5::ai_mesh::NpuExecutionRequest request;
    request.programId = 1;
    request.profileId = 1;
    EXPECT_TRUE(executor.outputPayload(request).empty());
    EXPECT_EQ(executor.semanticDigest(request),
              (std::array<uint8_t, 32>{}));
    EXPECT_EQ(executor.outputBytes(request), 0u);
}

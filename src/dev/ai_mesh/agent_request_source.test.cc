#include <gtest/gtest.h>

#include <cstdint>
#include <fstream>
#include <iterator>
#include <optional>
#include <set>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/agent_request_source.hh"

namespace
{

std::vector<uint64_t>
requestIds(const std::vector<gem5::ai_mesh::AgentSubmissionIntent> &intents)
{
    std::vector<uint64_t> ids;
    for (const auto &intent : intents)
        ids.push_back(intent.requestId);
    return ids;
}

std::optional<gem5::ai_mesh::AgentPlanImage>
loadPlanImage()
{
    std::ifstream stream(
        "tests/gem5/ai_mesh/fixtures/gate4/agent_plan_image_two_user.bin",
        std::ios::binary);
    const std::vector<uint8_t> bytes(
        (std::istreambuf_iterator<char>(stream)),
        std::istreambuf_iterator<char>());
    return gem5::ai_mesh::AgentPlanImage::parse(bytes.data(), bytes.size());
}

}

TEST(ProtocolProfileRequestSource, ExhaustedFollowsCompletedRequests)
{
    gem5::ai_mesh::ProtocolProfileRequestSource source(3);
    EXPECT_FALSE(source.exhausted(2));
    EXPECT_TRUE(source.exhausted(3));
    EXPECT_TRUE(source.exhausted(4));
}

TEST(ProtocolProfileRequestSource, FirstAttemptBatchesWhenDepthAllows)
{
    gem5::ai_mesh::ProtocolProfileRequestSource source(4);
    const auto intents = source.nextBatch({}, 0, 8);
    EXPECT_EQ(requestIds(intents), (std::vector<uint64_t>{1, 2, 3, 4}));
    for (const auto &intent : intents) {
        EXPECT_EQ(intent.completionCookie, intent.requestId);
        EXPECT_EQ(intent.sessionId, 0u);
        EXPECT_EQ(intent.userId, 0u);
        EXPECT_EQ(intent.taskSequence, 0u);
        EXPECT_EQ(intent.repairRound, 0u);
        EXPECT_EQ(intent.outputMetadataAddress, 0u);
        EXPECT_EQ(intent.outputMetadataCapacityBytes, 0u);
    }
}

TEST(ProtocolProfileRequestSource, SingleIntentWhenCountExceedsDepth)
{
    gem5::ai_mesh::ProtocolProfileRequestSource source(9);
    const auto intents = source.nextBatch({}, 0, 8);
    EXPECT_EQ(requestIds(intents), (std::vector<uint64_t>{1}));
}

TEST(ProtocolProfileRequestSource, SingleRequestNeverBatches)
{
    gem5::ai_mesh::ProtocolProfileRequestSource source(1);
    const auto intents = source.nextBatch({}, 0, 8);
    EXPECT_EQ(requestIds(intents), (std::vector<uint64_t>{1}));
}

TEST(ProtocolProfileRequestSource, LaterAttemptsStaySingleAndSkipIssued)
{
    gem5::ai_mesh::ProtocolProfileRequestSource source(6);
    EXPECT_EQ(requestIds(source.nextBatch({1, 2}, 2, 4)),
              (std::vector<uint64_t>{3}));
    EXPECT_EQ(requestIds(source.nextBatch({1, 2, 3}, 3, 4)),
              (std::vector<uint64_t>{4}));
    EXPECT_EQ(requestIds(source.nextBatch({1, 3, 5}, 4, 4)),
              (std::vector<uint64_t>{6}));
}

TEST(ReplayPlanRequestSource, WalksGenerateCommandsInOrder)
{
    auto image = loadPlanImage();
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ReplayPlanRequestSource source(std::move(*image));
    EXPECT_FALSE(source.exhausted(0));
    std::vector<uint64_t> ids;
    std::vector<gem5::ai_mesh::AgentSubmissionIntent> intents;
    for (int step = 0; step < 5; ++step) {
        const auto batch = source.nextBatch({}, 0, 8);
        ASSERT_EQ(batch.size(), 1u);
        intents.push_back(batch[0]);
        ids.push_back(batch[0].requestId);
    }
    EXPECT_EQ(ids, (std::vector<uint64_t>{
        1, 2, 3, 4, (1ull << 32) | 1}));
    EXPECT_FALSE(source.exhausted(4));
    EXPECT_TRUE(source.exhausted(5));
    EXPECT_TRUE(source.nextBatch({}, 5, 8).empty());
}

TEST(ReplayPlanRequestSource, FirstIntentCarriesFullPlanContext)
{
    auto image = loadPlanImage();
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ReplayPlanRequestSource source(std::move(*image));
    const auto batch = source.nextBatch({}, 0, 8);
    ASSERT_EQ(batch.size(), 1u);
    const auto &intent = batch[0];
    EXPECT_EQ(intent.requestId, 1u);
    EXPECT_EQ(intent.completionCookie, 1u);
    EXPECT_EQ(intent.userId, 0u);
    EXPECT_EQ(intent.taskSequence, 0u);
    EXPECT_EQ(intent.repairRound, 0u);
    EXPECT_EQ(intent.sessionId, 1u);
    EXPECT_EQ(intent.inputAddress, 0x0000000101000000ull);
    EXPECT_EQ(intent.inputBytes, 16384u);
    EXPECT_EQ(intent.outputAddress, 0x0000000105000000ull);
    EXPECT_EQ(intent.outputCapacityBytes, 8192u);
    EXPECT_EQ(intent.parameterAddress, 0x0000000100100000ull);
    EXPECT_EQ(intent.outputMetadataAddress, 0x0000000109000000ull);
    EXPECT_EQ(intent.outputMetadataCapacityBytes, 512u);
    EXPECT_EQ(intent.kvHandle, 1u);
    EXPECT_EQ(intent.kvGeneration, 1u);
    EXPECT_EQ(intent.programId, 1u);
    EXPECT_EQ(intent.profileId, 1u);
    EXPECT_EQ(intent.profileKey, 4097u);
    EXPECT_EQ(intent.maxOutputTokens, 128u);
    EXPECT_EQ(intent.workloadPlanItemId, 1u);
    EXPECT_EQ(intent.publishChunkBytes, 4096u);
    EXPECT_EQ(intent.qos, 4u);
    EXPECT_EQ(intent.kvPolicy, 0u);
    EXPECT_FALSE(intent.hasDeadline);
    EXPECT_TRUE(intent.planDriven);
    ASSERT_NE(intent.round, nullptr);
    ASSERT_NE(intent.task, nullptr);
    EXPECT_EQ(intent.round->itemId, 1u);
    EXPECT_EQ(intent.task->taskSeq, 0u);
    for (size_t index = 0; index < 32; ++index)
        EXPECT_EQ(intent.inputDigest[index], 0xaa);
    EXPECT_EQ(intent.workloadDigest[0], 0xaf);
    EXPECT_EQ(intent.workloadDigest[31], 0x5e);
}

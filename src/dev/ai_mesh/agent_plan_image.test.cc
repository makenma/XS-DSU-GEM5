#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"

namespace
{

const char kFixturePath[] =
    "tests/gem5/ai_mesh/fixtures/gate4/agent_plan_image_two_user.bin";
const char kControlFixturePath[] =
    "tests/gem5/ai_mesh/fixtures/gate4/agent_plan_image_ctrl_cancel_live.bin";

std::vector<uint8_t>
loadFixture()
{
    std::ifstream stream(kFixturePath, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(stream)),
                                std::istreambuf_iterator<char>());
}

std::vector<uint8_t>
loadControlFixture()
{
    std::ifstream stream(kControlFixturePath, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(stream)),
                                std::istreambuf_iterator<char>());
}

std::string
hexOf(const std::array<uint8_t, 32> &digest)
{
    static const char *digits = "0123456789abcdef";
    std::string text;
    for (const uint8_t byte : digest) {
        text += digits[byte >> 4];
        text += digits[byte & 0xf];
    }
    return text;
}

}

TEST(AgentPlanImage, ParsesFrozenTwoUserFixture)
{
    const std::vector<uint8_t> image = loadFixture();
    ASSERT_FALSE(image.empty());
    const auto parsed = gem5::ai_mesh::AgentPlanImage::parse(
        image.data(), image.size());
    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(hexOf(parsed->imageDigest()),
              "e917bd290cf4de0a0ead22424267c2d50e644dae95f64084fc76210a458"
              "0593d");
    EXPECT_EQ(hexOf(parsed->workloadPlanDigest()),
              "afc9cc56e7bed3228009e27e99b42cff882cdf580430f47a4016c786"
              "c6113e5e");
    EXPECT_EQ(parsed->controlActions().size(), 0u);
    EXPECT_EQ(parsed->users().size(), 2u);

    const auto &firstUser = parsed->users()[0];
    EXPECT_EQ(firstUser.userId, 0u);
    ASSERT_EQ(firstUser.tasks.size(), 2u);
    EXPECT_EQ(firstUser.tasks[0].taskSeq, 0u);
    EXPECT_EQ(firstUser.tasks[0].sessionId, 1u);
    EXPECT_EQ(firstUser.tasks[0].kvHandle, 1u);
    EXPECT_EQ(firstUser.tasks[0].effectiveCap, 1u);
    ASSERT_EQ(firstUser.tasks[0].rounds.size(), 2u);

    const auto &round0 = firstUser.tasks[0].rounds[0];
    EXPECT_EQ(round0.itemId, 1u);
    EXPECT_EQ(round0.repairRound, 0u);
    EXPECT_EQ(round0.kvPolicy, 0u);
    EXPECT_FALSE(round0.hasDeadline);
    EXPECT_EQ(round0.fullContextTokens, 1024u);
    EXPECT_EQ(round0.fullContextBytes, 16384u);
    EXPECT_EQ(round0.outputTokens, 128u);
    EXPECT_EQ(round0.generatedCodeBytes, 4096u);
    EXPECT_EQ(round0.outputCapacityBytes, 8192u);
    EXPECT_EQ(round0.metadataCapacityBytes, 512u);
    EXPECT_EQ(round0.programId, 1u);
    EXPECT_EQ(round0.profileId, 1u);
    EXPECT_EQ(round0.profileKey, 4097u);
    EXPECT_EQ(round0.qos, 4u);
    EXPECT_TRUE(round0.hasPrompt);
    EXPECT_EQ(round0.promptTokens, 1024u);
    EXPECT_EQ(round0.promptBytes, 16384u);
    EXPECT_FALSE(round0.hasDelta);
    EXPECT_EQ(round0.inputDigest[0], 0xaa);
    EXPECT_EQ(round0.compile.outcome, 1u);
    EXPECT_EQ(round0.compile.nominalNs, 100000000u);
    EXPECT_EQ(round0.compile.hostTokensRequired, 2u);
    EXPECT_EQ(round0.compile.readBytes, 4096u);
    EXPECT_EQ(round0.compile.writeBytes, 65536u);
    EXPECT_EQ(round0.compile.rawLogBytes, 65536u);
    EXPECT_FALSE(round0.hasTest);
    EXPECT_TRUE(round0.hasParse);
    EXPECT_EQ(round0.logParse.outcome, 2u);
    EXPECT_EQ(round0.logParse.excerptBytes, 16384u);
    EXPECT_EQ(round0.logParse.excerptTokens, 2048u);

    const auto &round1 = firstUser.tasks[0].rounds[1];
    EXPECT_EQ(round1.kvPolicy, 2u);
    EXPECT_TRUE(round1.hasTest);
    EXPECT_EQ(round1.test.outcome, 0u);
    EXPECT_FALSE(round1.hasParse);
    EXPECT_TRUE(round1.hasDelta);
    EXPECT_EQ(round1.deltaPromptTokens, 512u);

    EXPECT_EQ(parsed->users()[1].tasks.size(), 1u);
    EXPECT_EQ(parsed->users()[1].tasks[0].effectiveCap, 0u);

    ASSERT_EQ(parsed->commands().size(), 5u);
    EXPECT_EQ(parsed->commands()[0].requestId, 1u);
    EXPECT_EQ(parsed->commands()[0].commandKind, 0u);
    EXPECT_EQ(parsed->commands()[4].requestId, (1ull << 32) | 1);
    EXPECT_EQ(parsed->commands()[4].commandKind, 0u);

    EXPECT_EQ(parsed->hostTasks().size(), 11u);
    EXPECT_EQ(parsed->hostTasks()[0].hostTaskId, 1u);
    EXPECT_EQ(parsed->hostTasks()[10].hostTaskId, 11u);

    ASSERT_EQ(parsed->arena().size(), 20u);
    EXPECT_EQ(parsed->arena()[0].arenaKind, 0u);
    EXPECT_EQ(parsed->arena()[0].base, 0x0000000101000000ull);
    EXPECT_EQ(parsed->arena()[0].allocationBytes, 16384u);
    EXPECT_EQ(parsed->arena()[0].initialValidBytes, 16384u);
    EXPECT_EQ(parsed->arena()[1].arenaKind, 1u);
    EXPECT_EQ(parsed->arena()[1].base, 0x0000000100100000ull);
    EXPECT_EQ(parsed->arena()[19].arenaKind, 3u);
    EXPECT_EQ(parsed->arena()[19].allocationBytes, 512u);

    const gem5::ai_mesh::SurrogateProfileRegistry *surrogate =
        parsed->surrogateRegistry();
    ASSERT_NE(surrogate, nullptr);
    EXPECT_EQ(surrogate->profileCount(), 5u);
    EXPECT_EQ(surrogate->publishChunkBytes(), 4096u);
    const gem5::ai_mesh::SurrogateProfile *profile = surrogate->find(1, 1);
    ASSERT_NE(profile, nullptr);
    EXPECT_EQ(profile->profileKey, 4097u);
    EXPECT_EQ(hexOf(surrogate->fixtureDigest()),
              "d6aa0634939a4d745b25a1ce36b16c94685a7a8d9d58c1d608ed2d20f0"
              "39d897");
}

TEST(AgentPlanImage, ParsesControlActionsFromScenarioFixture)
{
    const std::vector<uint8_t> image = loadControlFixture();
    ASSERT_FALSE(image.empty());
    const auto parsed = gem5::ai_mesh::AgentPlanImage::parse(
        image.data(), image.size());
    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(hexOf(parsed->imageDigest()),
              "ec0f8d1fc3dfb47f2a0817fab540f28e48046d8bf365eebb3329a32fdc0"
              "48a09");
    ASSERT_EQ(parsed->controlActions().size(), 1u);
    const gem5::ai_mesh::AgentControlAction &action =
        parsed->controlActions()[0];
    EXPECT_EQ(action.controlOrdinal, 1u);
    EXPECT_EQ(action.opcode, 2u);
    EXPECT_EQ(action.triggerKind, 1u);
    EXPECT_TRUE(action.hasAnchor);
    EXPECT_FALSE(action.hasAfterOrdinal);
    EXPECT_EQ(action.anchorUserId, 1u);
    EXPECT_EQ(action.anchorTaskSeq, 0u);
    EXPECT_EQ(action.anchorRepairRound, 0u);
    EXPECT_EQ(action.issuerUserId, 1u);
    EXPECT_EQ(action.issuerTaskSeq, 0u);
    EXPECT_EQ(action.targetUserId, 1u);
    EXPECT_EQ(action.targetTaskSeq, 0u);
    EXPECT_EQ(action.targetRepairRound, 0u);
    EXPECT_EQ(action.targetSessionId, 0u);
    EXPECT_EQ(action.targetKvHandle, 0u);
    EXPECT_EQ(action.targetGeneration, 0u);
    ASSERT_EQ(parsed->commands().size(), 6u);
    EXPECT_EQ(parsed->commands()[3].commandKind, 2u);
    EXPECT_EQ(parsed->commands()[3].controlOrdinal, 1u);
    EXPECT_EQ(parsed->commands()[3].requestId, (1ull << 32) | 2);
    EXPECT_EQ(parsed->commands()[3].targetRequestId, (1ull << 32) | 1);
    ASSERT_EQ(parsed->arena().size(), 21u);
    EXPECT_EQ(parsed->arena()[12].arenaKind, 1u);
    EXPECT_EQ(parsed->arena()[12].commandKind, 2u);
    EXPECT_EQ(parsed->arena()[12].allocationBytes, 160u);
    EXPECT_EQ(parsed->arena()[12].requestId, (1ull << 32) | 2);
}

TEST(AgentPlanImage, RejectsCorruptedDigest)
{
    std::vector<uint8_t> image = loadFixture();
    ASSERT_FALSE(image.empty());
    image[image.size() - 1] ^= 0x01;
    EXPECT_FALSE(gem5::ai_mesh::AgentPlanImage::parse(
                     image.data(), image.size()).has_value());
}

TEST(AgentPlanImage, RejectsTruncatedImage)
{
    const std::vector<uint8_t> image = loadFixture();
    ASSERT_GT(image.size(), 100u);
    for (const size_t cut : {size_t{8}, size_t{64}, image.size() - 1}) {
        EXPECT_FALSE(gem5::ai_mesh::AgentPlanImage::parse(
                         image.data(), cut).has_value())
            << "cut at " << cut;
    }
}

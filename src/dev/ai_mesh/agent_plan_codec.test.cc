#include <gtest/gtest.h>

#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_plan_codec.hh"
#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/agent_sha256.hh"

namespace
{

const char kFixturePath[] =
    "tests/gem5/ai_mesh/fixtures/gate4/agent_plan_image_two_user.bin";

std::vector<uint8_t>
loadFixture()
{
    std::ifstream stream(kFixturePath, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(stream)),
                                std::istreambuf_iterator<char>());
}

std::string
hexOf(const uint8_t *data, size_t length)
{
    static const char *digits = "0123456789abcdef";
    std::string text;
    for (size_t index = 0; index < length; ++index) {
        text += digits[data[index] >> 4];
        text += digits[data[index] & 0xf];
    }
    return text;
}

const char kParam0Hex[] =
    "41474e5001000000a00000000001000000000000000000000000000101000000"
    "0040000000000000000400000000000000000005010000000020000000000000"
    "0000000901000000000200008000000001000000000000000100000000000000"
    "000000000000000000000401010000000000000000000000a000000000001800"
    "a000000060000000011000000000000000000000000000002c917c7a00000000"
    "0100010020000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    "aaaaaaaaaaaaaaaa030001000800000000100000000000000400010020000000"
    "afc9cc56e7bed3228009e27e99b42cff882cdf580430f47a4016c786c6113e5e";

}

TEST(AgentPlanCodec, ParameterRound0MatchesPythonGolden)
{
    const std::vector<uint8_t> image = loadFixture();
    ASSERT_FALSE(image.empty());
    const auto parsed = gem5::ai_mesh::AgentPlanImage::parse(
        image.data(), image.size());
    ASSERT_TRUE(parsed.has_value());
    const auto &task = parsed->users()[0].tasks[0];
    const auto &round = task.rounds[0];
    gem5::ai_mesh::PlanWireAddresses addresses;
    addresses.inputBase = 0x0000000101000000ull;
    addresses.outputBase = 0x0000000105000000ull;
    addresses.metadataBase = 0x0000000109000000ull;
    const auto parameter = gem5::ai_mesh::buildPlanParameter(
        round, task, 0, addresses, parsed->workloadPlanDigest().data(),
        4096);
    ASSERT_EQ(parameter.size(), 256u);
    EXPECT_EQ(hexOf(parameter.data(), parameter.size()), kParam0Hex);
}

TEST(AgentPlanCodec, ParameterRound1MatchesPythonDigest)
{
    const std::vector<uint8_t> image = loadFixture();
    const auto parsed = gem5::ai_mesh::AgentPlanImage::parse(
        image.data(), image.size());
    ASSERT_TRUE(parsed.has_value());
    const auto &task = parsed->users()[0].tasks[0];
    const auto &round = task.rounds[1];
    gem5::ai_mesh::PlanWireAddresses addresses;
    addresses.inputBase = 0x0000000101000000ull + 16384;
    addresses.outputBase = 0x0000000105000000ull + 8192;
    addresses.metadataBase = 0x0000000109000000ull + 512;
    const auto parameter = gem5::ai_mesh::buildPlanParameter(
        round, task, 0, addresses, parsed->workloadPlanDigest().data(),
        4096);
    ASSERT_EQ(parameter.size(), 256u);
    EXPECT_EQ(hexOf(gem5::ai_mesh::agentSha256(parameter).data(), 32),
              "2854ec712c5739031bf5256627f1d26d18c567ee5417fe095e0891b7343"
              "137c7");
}

TEST(AgentPlanCodec, DeadlineTlvKeepsAscendingTypeOrder)
{
    gem5::ai_mesh::AgentPlanRound round;
    round.hasDeadline = true;
    round.deadlineTick = 12345;
    round.fullContextBytes = 64;
    round.fullContextTokens = 2;
    round.outputCapacityBytes = 64;
    round.metadataCapacityBytes = 512;
    round.outputTokens = 1;
    round.itemId = 9;
    round.qos = 1;
    gem5::ai_mesh::AgentPlanTask task;
    task.taskSeq = 0;
    task.kvHandle = 7;
    task.generation = 1;
    gem5::ai_mesh::PlanWireAddresses addresses;
    const uint8_t workloadDigest[32] = {};
    const auto parameter = gem5::ai_mesh::buildPlanParameter(
        round, task, 0, addresses, workloadDigest, 4096);
    ASSERT_EQ(parameter.size(), 160u + 40 + 16 + 16 + 40);
    const size_t tail = 160;
    EXPECT_EQ(parameter[tail], 0x01);
    EXPECT_EQ(parameter[tail + 8 + 32], 0x02);
    EXPECT_EQ(parameter[tail + 8 + 32 + 8 + 8], 0x03);
    EXPECT_EQ(parameter[tail + 8 + 32 + 8 + 8 + 8 + 8], 0x04);
    uint64_t deadline = 0;
    for (size_t index = 0; index < 8; ++index)
        deadline |= uint64_t(parameter[tail + 8 + 32 + 8 + index])
                    << (8 * index);
    EXPECT_EQ(deadline, 12345u);
}

TEST(AgentPlanCodec, KvPolicyMapsToSqFlags)
{
    EXPECT_EQ(gem5::ai_mesh::kvPolicyToSqFlags(0), 0);
    EXPECT_EQ(gem5::ai_mesh::kvPolicyToSqFlags(1), 0x0001);
    EXPECT_EQ(gem5::ai_mesh::kvPolicyToSqFlags(2), 0x0002);
    EXPECT_EQ(gem5::ai_mesh::kvPolicyToSqFlags(3), 0xffff);
}

const char kControlCancelHex[] =
    "41474e5001000000a0000000a000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000030000000001000000010000000000000000001800"
    "0000000000000000000000000000000000000000000000007ffdc3cb00000000";
const char kControlReleaseHex[] =
    "41474e5001000000a0000000a000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000b000000000000000100000000000000"
    "0000000000000000000000020000000000000000000000000000000000001800"
    "000000000000000000000000000000000000000000000000ec41f81e00000000";

TEST(AgentPlanCodec, ControlParameterMatchesPythonGolden)
{
    const auto cancel = gem5::ai_mesh::buildControlParameter(
        2, (1ull << 32) | 1, 0, 0);
    ASSERT_EQ(cancel.size(), 160u);
    EXPECT_EQ(hexOf(cancel.data(), cancel.size()), kControlCancelHex);
    const auto release = gem5::ai_mesh::buildControlParameter(1, 0, 11, 1);
    ASSERT_EQ(release.size(), 160u);
    EXPECT_EQ(hexOf(release.data(), release.size()), kControlReleaseHex);
}

#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

#include "dev/ai_mesh/agent_surrogate_codec.hh"

namespace
{

using gem5::ai_mesh::SurrogateProfile;
using gem5::ai_mesh::SurrogateProfileRegistry;

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

constexpr uint8_t kInputDigest[32] = {
    0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa,
    0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa,
    0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa,
    0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa,
};

}

TEST(AgentSurrogateCodec, SeedAndTokenDigestsMatchFrozenVectors)
{
    const auto seed = gem5::ai_mesh::surrogateSeed(kInputDigest, 1, 1, 4097);
    EXPECT_EQ(hexOf(seed.data(), 32),
              "4c761e3200080964e264fa86207d5c17d476dc540164998b44a1b6ca2f52"
              "fdd3");
    const auto token0 = gem5::ai_mesh::surrogateTokenDigest(seed.data(), 0);
    EXPECT_EQ(hexOf(token0.data(), 32),
              "eeede03a4f24cf2865dbea4c1cb6b11eaddf42ebde450e4a68adcc754fa"
              "ae717");
    const auto token127 =
        gem5::ai_mesh::surrogateTokenDigest(seed.data(), 127);
    EXPECT_EQ(hexOf(token127.data(), 32),
              "f22439e5cb4cdc53e24ea0176ca4617f471f75883971cce3ea349f19cd5"
              "54b23");
}

TEST(AgentSurrogateCodec, PrefixDigestMatchesFrozenVector)
{
    const auto seed = gem5::ai_mesh::surrogateSeed(kInputDigest, 1, 1, 4097);
    std::vector<std::array<uint8_t, 32>> tokens;
    for (uint32_t index = 0; index < 128; ++index)
        tokens.push_back(
            gem5::ai_mesh::surrogateTokenDigest(seed.data(), index));
    const uint8_t workloadDigest[32] = {
        0xaf, 0xc9, 0xcc, 0x56, 0xe7, 0xbe, 0xd3, 0x22,
        0x80, 0x09, 0xe2, 0x7e, 0x99, 0xb4, 0x2c, 0xff,
        0x88, 0x2c, 0xdf, 0x58, 0x04, 0x30, 0xf4, 0x7a,
        0x40, 0x16, 0xc7, 0x86, 0xc6, 0x11, 0x3e, 0x5e,
    };
    const auto prefix = gem5::ai_mesh::surrogateOutputPrefixDigest(
        workloadDigest, 1, 128, tokens);
    EXPECT_EQ(hexOf(prefix.data(), 32),
              "820a06e846b8b2d4a777e77d6fdb28833034511376cae0d0288534ef7b5"
              "38511");
}

TEST(AgentSurrogateCodec, OutputBytesFollowBlockFormulaWithTail)
{
    const uint8_t semanticDigest[32] = {
        0x82, 0x0a, 0x06, 0xe8, 0x46, 0xb8, 0xb2, 0xd4,
        0xa7, 0x77, 0xe7, 0x7d, 0x6f, 0xdb, 0x28, 0x83,
        0x30, 0x34, 0x51, 0x13, 0x76, 0xca, 0xe0, 0xd0,
        0x28, 0x85, 0x34, 0xef, 0x7b, 0x53, 0x85, 0x11,
    };
    const auto single = gem5::ai_mesh::surrogateOutputBytes(
        semanticDigest, 32);
    ASSERT_EQ(single.size(), 32u);
    EXPECT_EQ(hexOf(single.data(), 32),
              "d6d0959a2ea3b54f752882e4bb2f4828cae43d336c1b402da45b0f276a1"
              "9fade");
    const auto tail = gem5::ai_mesh::surrogateOutputBytes(
        semanticDigest, 100);
    ASSERT_EQ(tail.size(), 100u);
    EXPECT_EQ(hexOf(tail.data(), 32),
              "d6d0959a2ea3b54f752882e4bb2f4828cae43d336c1b402da45b0f276a1"
              "9fade");
}

TEST(AgentSurrogateCodec, InputSurrogateMatchesFrozenBlocks)
{
    const auto payload = gem5::ai_mesh::agentInputSurrogateBytes(
        kInputDigest, 16384);
    ASSERT_EQ(payload.size(), 16384u);
    EXPECT_EQ(hexOf(payload.data(), 32),
              "4088196a730ae2895c595ad2ce2c680b9b6f42c022196eaf2d7f649153f"
              "1bdb1");
    EXPECT_EQ(hexOf(payload.data() + 16380, 4), "40f940de");
}

TEST(AgentSurrogateCodec, RegistryParsesAscendingAndFindsProfiles)
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
        2, 0, 0, 0,
        1, 0, 1, 0,
        0x01, 0x10, 0, 0, 0, 0, 0, 0,
        0x00, 0x04, 0, 0,
        0x00, 0x40, 0, 0, 0, 0, 0, 0,
        0x80, 0, 0, 0,
        0x00, 0x10, 0, 0, 0, 0, 0, 0,
        0x00, 0x09, 0x3d, 0x00, 0, 0, 0, 0,
        0x00, 0x10, 0, 0,
        1, 0, 2, 0,
        0x02, 0x10, 0, 0, 0, 0, 0, 0,
        0x80, 0x06, 0, 0,
        0x00, 0x60, 0, 0, 0, 0, 0, 0,
        0x40, 0, 0, 0,
        0x00, 0x30, 0, 0, 0, 0, 0, 0,
        0x80, 0xee, 0x36, 0x00, 0, 0, 0, 0,
        0x00, 0x10, 0, 0,
    };
    section.insert(section.end(), body, body + sizeof(body));
    const auto registry = SurrogateProfileRegistry::parse(
        section.data(), section.size());
    ASSERT_TRUE(registry.has_value());
    const SurrogateProfile *first = registry->find(1, 1);
    ASSERT_NE(first, nullptr);
    EXPECT_EQ(first->profileKey, 4097u);
    EXPECT_EQ(first->inputTokens, 1024u);
    EXPECT_EQ(first->inputBytes, 16384u);
    EXPECT_EQ(first->outputTokens, 128u);
    EXPECT_EQ(first->outputBytes, 4096u);
    EXPECT_EQ(first->serviceNs, 4000000u);
    EXPECT_EQ(registry->publishChunkBytes(), 4096u);
    EXPECT_EQ(registry->find(9, 9), nullptr);
    EXPECT_EQ(hexOf(registry->fixtureDigest().data(), 4), "0ff7cdf1");

    section.push_back(0);
    EXPECT_FALSE(SurrogateProfileRegistry::parse(
                     section.data(), section.size()).has_value());
}

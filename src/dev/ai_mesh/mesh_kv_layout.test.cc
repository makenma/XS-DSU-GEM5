#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <fstream>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_kv_layout.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

using namespace mesh_abi;

MeshBytes loadFixture(const std::string &path)
{
    std::ifstream file(path, std::ios::binary);
    return MeshBytes((std::istreambuf_iterator<char>(file)),
                     std::istreambuf_iterator<char>());
}

std::string hexDigest(const std::array<uint8_t, 32> &digest)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    for (uint8_t byte : digest) {
        out += digits[byte >> 4];
        out += digits[byte & 0xF];
    }
    return out;
}

DecodedProgram decodeOrFail(const std::string &path)
{
    const MeshBytes image = loadFixture(path);
    EXPECT_FALSE(image.empty()) << path;
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    return program;
}

const char *kServingMinGolden =
    "78a2574e02b4bf19635acfbbba932d1c1ac712312863a2f86ba7f4cee68d89a1";
const char *kTwoTokensGolden =
    "260d84795086ee48029a9d2b3bed4066f0e0e4d5887fb7e8b01d409bc87dae90";

}

TEST(MeshKvLayoutTest, MatchesTheServingMinGolden)
{
    const DecodedProgram program = decodeOrFail(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    std::array<uint8_t, 32> digest{};
    MeshLoadError error;
    ASSERT_TRUE(kvLayoutDigest(program, digest, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(hexDigest(digest), kServingMinGolden);
}

TEST(MeshKvLayoutTest, MatchesTheTwoTokenGolden)
{
    const DecodedProgram program = decodeOrFail(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    std::array<uint8_t, 32> digest{};
    MeshLoadError error;
    ASSERT_TRUE(kvLayoutDigest(program, digest, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(hexDigest(digest), kTwoTokensGolden);
}

TEST(MeshKvLayoutTest, RejectsARequestProfileWithoutKvBinding)
{
    DecodedProgram program = decodeOrFail(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(program.agent_request_profiles.empty());
    program.agent_request_profiles[0].primary_kv_symbol_id = 0;
    std::array<uint8_t, 32> digest{};
    MeshLoadError error;
    EXPECT_FALSE(kvLayoutDigest(program, digest, error));
    EXPECT_EQ(error.code, "E_KV_CONTRACT_MISMATCH");
}

TEST(MeshKvLayoutTest, RejectsKvSymbolsWithoutARelocation)
{
    DecodedProgram program = decodeOrFail(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(program.agent_request_profiles.empty());
    program.agent_request_profiles[0].primary_kv_symbol_id = 0xFFFF;
    std::array<uint8_t, 32> digest{};
    MeshLoadError error;
    EXPECT_FALSE(kvLayoutDigest(program, digest, error));
    EXPECT_EQ(error.code, "E_KV_CONTRACT_MISMATCH");
}

}
}

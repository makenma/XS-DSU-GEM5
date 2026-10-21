#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <fstream>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/generated/serving_golden.inc"
#include "dev/ai_mesh/mesh_kv_layout.hh"
#include "dev/ai_mesh/mesh_hash.hh"

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


}

TEST(MeshKvLayoutTest, MatchesTheServingMinGolden)
{
    const DecodedProgram program = decodeOrFail(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    std::array<uint8_t, 32> digest{};
    MeshLoadError error;
    ProgramSemanticContext context;
    ASSERT_TRUE(buildProgramSemanticContext(program, context, error))
        << error.code << ": " << error.message;
    ASSERT_TRUE(kvLayoutDigest(program, context, digest, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(mesh_hash::digestHex(digest), golden::kServingKvLayoutDigest);
}

TEST(MeshKvLayoutTest, MatchesTheTwoTokenGolden)
{
    const DecodedProgram program = decodeOrFail(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    std::array<uint8_t, 32> digest{};
    MeshLoadError error;
    ProgramSemanticContext context;
    ASSERT_TRUE(buildProgramSemanticContext(program, context, error))
        << error.code << ": " << error.message;
    ASSERT_TRUE(kvLayoutDigest(program, context, digest, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(mesh_hash::digestHex(digest), golden::kTwoTokenKvLayoutDigest);
}

TEST(MeshKvLayoutTest, RejectsARequestProfileWithoutKvBinding)
{
    DecodedProgram program = decodeOrFail(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(program.transport.agent_request_profiles.empty());
    program.transport.agent_request_profiles[0].primary_kv_symbol_id = 0;
    std::array<uint8_t, 32> digest{};
    MeshLoadError error;
    ProgramSemanticContext context;
    ASSERT_TRUE(buildProgramSemanticContext(program, context, error))
        << error.code << ": " << error.message;
    EXPECT_FALSE(kvLayoutDigest(program, context, digest, error));
    EXPECT_EQ(error.code, "E_KV_CONTRACT_MISMATCH");
}

TEST(MeshKvLayoutTest, RejectsKvSymbolsWithoutARelocation)
{
    DecodedProgram program = decodeOrFail(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(program.transport.agent_request_profiles.empty());
    program.transport.agent_request_profiles[0].primary_kv_symbol_id = 0xFFFF;
    std::array<uint8_t, 32> digest{};
    MeshLoadError error;
    ProgramSemanticContext context;
    ASSERT_TRUE(buildProgramSemanticContext(program, context, error))
        << error.code << ": " << error.message;
    EXPECT_FALSE(kvLayoutDigest(program, context, digest, error));
    EXPECT_EQ(error.code, "E_KV_CONTRACT_MISMATCH");
}

}
}

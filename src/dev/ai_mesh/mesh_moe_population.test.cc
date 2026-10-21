#include <gtest/gtest.h>
#include <algorithm>
#include <fstream>

#include "dev/ai_mesh/mesh_moe_population.hh"

using namespace gem5::ai_mesh;

TEST(MoePopulation, SemanticOrderIsNumericAndEncodingIsLittleEndian)
{
    MoeSemanticToken first{255, 3, 4, 2, 1, 0, 7};
    auto second = first;
    second.item = 256;
    EXPECT_TRUE(first < second);
    const auto bytes = first.encode();
    EXPECT_EQ(bytes[0], 255u);
    EXPECT_EQ(bytes[8], 3u);
    EXPECT_EQ(bytes[16], 2u);
    EXPECT_EQ(bytes[18], 1u);
    EXPECT_EQ(bytes[24], 7u);
}

TEST(MoePopulation, FrozenRoutesIgnoreArrivalOrderAndPreserveOwner)
{
    mesh_abi::MoeLayerSpec layer;
    layer.layer_id = 7;
    layer.expert_count = 4;
    layer.top_k = 2;
    layer.capacity_factor_q16 = 65536;
    layer.overflow_policy = mesh_abi::kMoeOverflowPolicyPAD_TO_CAPACITY;
    layer.max_tokens_per_frozen_batch = 8;
    layer.max_routes = 16;
    std::vector<MoePopulationToken> tokens;
    for (uint32_t member = 0; member < 2; ++member)
        for (uint32_t ordinal = 0; ordinal < 4; ++ordinal)
            tokens.push_back({{member + 1, member, 0, 0, 1, 0, ordinal}, member, member});
    MoePopulation first, reversed;
    std::string error;
    ASSERT_TRUE(first.freezeUniform(layer, tokens, {}, 17, error)) << error;
    std::reverse(tokens.begin(), tokens.end());
    ASSERT_TRUE(reversed.freezeUniform(layer, tokens, {}, 17, error)) << error;
    ASSERT_EQ(first.routes().size(), 16u);
    ASSERT_EQ(reversed.routes().size(), 16u);
    std::ifstream golden("tests/gem5/ai_mesh/fixtures/gate6/moe_population.golden");
    ASSERT_TRUE(golden.good());
    uint32_t route_count, expert_count;
    ASSERT_TRUE(golden >> route_count >> expert_count);
    ASSERT_EQ(route_count, first.routes().size());
    ASSERT_EQ(expert_count, first.loads().size());
    for (size_t i = 0; i < first.routes().size(); ++i) {
        const auto &left = first.routes()[i];
        const auto &right = reversed.routes()[i];
        EXPECT_EQ(left.token.uid.encode(), right.token.uid.encode());
        EXPECT_EQ(left.expert, right.expert);
        EXPECT_EQ(left.slot, right.slot);
        EXPECT_EQ(left.accepted, right.accepted);
        EXPECT_EQ(left.token.member, left.token.uid.item - 1);
        uint32_t item, token, slot, expert, accepted;
        ASSERT_TRUE(golden >> item >> token >> slot >> expert >> accepted);
        EXPECT_EQ(left.token.uid.item, item);
        EXPECT_EQ(left.token.uid.token, token);
        EXPECT_EQ(left.slot, slot);
        EXPECT_EQ(left.expert, expert);
        EXPECT_EQ(left.accepted, bool(accepted));
    }
    for (uint32_t load : first.loads()) {
        EXPECT_LE(load, 4u);
        uint32_t expected;
        ASSERT_TRUE(golden >> expected);
        EXPECT_EQ(load, expected);
    }
    for (uint32_t padding : first.padding()) {
        uint32_t expected;
        ASSERT_TRUE(golden >> expected);
        EXPECT_EQ(padding, expected);
    }
}

TEST(MoePopulation, InvalidOrOverflowingPopulationPublishesNothing)
{
    mesh_abi::MoeLayerSpec layer;
    layer.expert_count = layer.top_k = 1;
    layer.max_tokens_per_frozen_batch = layer.max_routes = 2;
    layer.capacity_factor_q16 = 1;
    layer.overflow_policy = mesh_abi::kMoeOverflowPolicyFAIL;
    std::vector<MoePopulationToken> tokens{{{1, 0, 0, 0, 1, 0, 0}, 0, 0},
                                          {{1, 0, 0, 0, 1, 0, 1}, 0, 0}};
    MoePopulation population;
    std::string error;
    EXPECT_FALSE(population.freezeUniform(layer, tokens, {}, 1, error));
    EXPECT_EQ(error, "E_MOE_CAPACITY");
    EXPECT_TRUE(population.routes().empty());
    tokens[1] = tokens[0];
    EXPECT_FALSE(population.freezeUniform(layer, tokens, {}, 1, error));
    EXPECT_EQ(error, "E_MOE_UID");
    EXPECT_TRUE(population.routes().empty());
}

TEST(MoePopulation, ExternalSelectionUsesTheSameCanonicalCapacityTransaction)
{
    mesh_abi::MoeLayerSpec layer;
    layer.expert_count = 2;
    layer.top_k = 2;
    layer.max_tokens_per_frozen_batch = 2;
    layer.max_routes = 4;
    layer.capacity_factor_q16 = 65536;
    layer.overflow_policy = mesh_abi::kMoeOverflowPolicyDROP;
    std::vector<MoePopulationToken> tokens{{{2, 0, 0, 0, 1, 0, 0}, 1, 0},
                                          {{1, 0, 0, 0, 1, 0, 0}, 0, 0}};
    MoePopulation population;
    std::string error;
    ASSERT_TRUE(population.freeze(layer, tokens, [](const auto &canonical) {
        EXPECT_EQ(canonical[0].uid.item, 1u);
        return std::vector<uint32_t>{1, 0, 0, 1};
    }, error)) << error;
    EXPECT_EQ(population.loads(), (std::vector<uint32_t>{2, 2}));
    EXPECT_EQ(population.routes()[0].token.member, 0u);
    for (const auto &experts : {std::vector<uint32_t>{1, 0, 0},
                               std::vector<uint32_t>{2, 0, 0, 1},
                               std::vector<uint32_t>{1, 1, 0, 1}}) {
        EXPECT_FALSE(population.freeze(layer, tokens,
            [experts](const auto &) { return experts; }, error));
        EXPECT_TRUE(population.routes().empty());
    }
}

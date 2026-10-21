#include <gtest/gtest.h>

#include <fstream>

#include "dev/ai_mesh/mesh_serving_capacity.hh"

using namespace gem5::ai_mesh;

TEST(MeshBinaryServingTest, BindingCapacityRejectsEveryUndersizedTable)
{
    DecodedProgram program;
    MeshLoadError error;
    std::ifstream file("tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb",
                       std::ios::binary);
    ASSERT_TRUE(file.good());
    const MeshBytes image((std::istreambuf_iterator<char>(file)),
                          std::istreambuf_iterator<char>());
    ASSERT_TRUE(decodeMeshBinary(image, program, error));
    ServingCapacityRequirement required;
    const ServingCapacityRequirement exact{1, 4, 9};
    ASSERT_TRUE(servingCapacityPlan(program, exact, required, error));
    EXPECT_EQ(required.batch_weight_binding_entries, 1u);
    EXPECT_EQ(required.instance_member_binding_entries, 4u);
    EXPECT_EQ(required.batch_interval_entries, 9u);
    for (const auto configured : {ServingCapacityRequirement{0, 4, 9},
                                  ServingCapacityRequirement{1, 3, 9},
                                  ServingCapacityRequirement{1, 4, 8}}) {
        error = {};
        EXPECT_FALSE(servingCapacityPlan(program, configured, required, error));
        EXPECT_EQ(error.code, "E_CAPACITY_PLAN");
        EXPECT_FALSE(error.message.empty());
    }
    EXPECT_TRUE(servingCapacityPlan(program, {8, 32, 72}, required, error));
    DecodedProgram ordinary;
    EXPECT_TRUE(servingCapacityPlan(ordinary, {}, required, error));
    EXPECT_EQ(required.batch_interval_entries, 0u);
}

TEST(MeshBinaryServingTest, StrictReplayCountsColdUnionAndEveryLayerOccurrence)
{
    DecodedProgram program;
    ProfileExecutionView profile;
    profile.profile_id = 9;
    profile.ranges.push_back({0, 0, 0, 2, {10, 20}});
    program.transport.commands.resize(2);
    program.transport.commands[0].command_id = 10;
    program.transport.commands[1].command_id = 20;
    program.transport.moe_dynamic_regions.resize(2);
    program.transport.moe_dynamic_regions[0].layer_id = 1;
    program.transport.moe_dynamic_regions[0].insert_after_command_id = 10;
    program.transport.moe_dynamic_regions[1].layer_id = 2;
    program.transport.moe_dynamic_regions[1].insert_after_command_id = 20;
    program.transport.moe_expert_specs.resize(3);
    for (size_t i = 0; i < 3; ++i) {
        auto &expert = program.transport.moe_expert_specs[i];
        expert.layer_id = i == 2 ? 2 : 1;
        expert.core_id = 0;
        expert.weight_symbol_id = 1;
        expert.weight_region_offset = i == 1 ? 64 : 0;
        expert.weight_bytes = 64;
    }
    const auto demand = servingStrictCacheCapacity(program, profile, 0);
    EXPECT_EQ(demand.lines, 2u);
    EXPECT_EQ(demand.occurrences, 3u);
    EXPECT_EQ(servingStrictCacheCapacity(program, profile, 1).lines, 0u);
}

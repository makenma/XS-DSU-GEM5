#include <gtest/gtest.h>
#include <fstream>
#include <iterator>

#include "dev/ai_mesh/serving_batch_replay.hh"
#include "dev/ai_mesh/mesh_json.hh"

using namespace gem5::ai_mesh;

TEST(ServingBatchReplay, ValidatesFrozenPopulationAndRejectsDivergence)
{
    const std::string root = "tests/gem5/ai_mesh/fixtures/gate6/";
    std::ifstream planFile(root + "agent_plan_image_serving_replay.bin", std::ios::binary);
    std::vector<uint8_t> planBytes((std::istreambuf_iterator<char>(planFile)), {});
    auto plan = AgentPlanImage::parse(planBytes.data(), planBytes.size());
    ASSERT_TRUE(plan);
    std::ifstream programFile(root + "serving_two_tokens.mshb", std::ios::binary);
    MeshBytes programBytes((std::istreambuf_iterator<char>(programFile)), {});
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(programBytes, program, error));
    std::ifstream replayFile(root + "serving_batch_replay.json");
    std::string json((std::istreambuf_iterator<char>(replayFile)), {});
    std::string reason;
    auto replay = ServingBatchReplay::parse(json, *plan, program, reason);
    ASSERT_TRUE(replay) << reason;
    ASSERT_EQ(replay->batches.size(), 4u);
    EXPECT_EQ(replay->digest, "8bbd701911769f7e9dc5eb0f3028a899b30f9eb23e69d972b1aca15b59dcd861");
    EXPECT_EQ(replay->batches.back().decodeIteration, 2u);
    EXPECT_EQ(replay->batches.back().members[0].sequenceBegin, 2u);
    for (unsigned mode = 0; mode < 9; ++mode) {
        auto value = parseMeshJson(json, reason);
        ASSERT_TRUE(value);
        auto &rootObject = value->as_object();
        auto &batches = rootObject.at("batches").as_array();
        auto &member = batches[1].as_object().at("members").as_array()[0].as_object();
        switch (mode) {
          case 0: rootObject["runtime_id"] = 1; break;
          case 1: batches[1].as_object()["batch_ordinal"] = 9; break;
          case 2: member["logical_source_rank"] = 1; break;
          case 3: member["valid_token_count"] = 2; break;
          case 4: member["sequence_ordinal_begin"] = 3; break;
          case 5: batches[1].as_object()["decode_iteration"] = 4; break;
          case 6: batches.pop_back(); break;
          case 7: rootObject["workload_plan_digest"] = std::string(64, '0'); break;
          case 8: member["workload_plan_item_id"] = 999; break;
        }
        EXPECT_FALSE(ServingBatchReplay::parse(canonicalMeshJson(*value), *plan, program, reason)) << mode;
        EXPECT_FALSE(reason.empty());
    }
}

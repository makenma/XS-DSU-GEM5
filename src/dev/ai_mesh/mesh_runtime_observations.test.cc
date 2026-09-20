#include <gtest/gtest.h>

#include <sstream>

#include "dev/ai_mesh/mesh_runtime_observations.hh"

using namespace gem5::ai_mesh;

namespace
{

CommandGeneration
key(uint32_t command_id, uint32_t generation)
{
    return CommandGeneration{command_id, generation};
}

DescriptorKey
descriptor(uint32_t command_id, uint32_t generation, uint32_t descriptor_id)
{
    DescriptorKey result;
    result.command = key(command_id, generation);
    result.descriptor_id = descriptor_id;
    return result;
}

TrafficContribution
contribution(uint64_t bytes, const std::string &digest)
{
    TrafficContribution transfer;
    transfer.read_bytes = bytes;
    transfer.read_bursts = 1;
    transfer.payload_digest = digest;
    return transfer;
}

TEST(RuntimeObservationsTest, framesAreOpenedAndTakenOnce)
{
    RuntimeObservations observations;
    EXPECT_FALSE(observations.frameActive());
    observations.beginFrame(3, 0);
    EXPECT_TRUE(observations.frameActive());
    EXPECT_EQ(observations.instanceId(), 3u);
    observations.recordCommandAdmission(key(4, 0), 100);
    ObservationFrame frame = observations.takeFrame();
    EXPECT_FALSE(observations.frameActive());
    EXPECT_EQ(frame.instance_id, 3u);
    EXPECT_EQ(frame.core_id, 0u);
    ASSERT_EQ(frame.commands.size(), 1u);
    EXPECT_TRUE(frame.commands[0].issued);
    EXPECT_EQ(frame.commands[0].issue_tick, 100u);
    observations.beginFrame(4, 0);
    EXPECT_EQ(observations.view().instance_id, 4u);
}

TEST(RuntimeObservationsTest, duplicateAdmissionAndTerminalFailClosed)
{
    RuntimeObservations observations;
    observations.beginFrame(1, 0);
    observations.recordCommandAdmission(key(7, 0), 10);
    EXPECT_ANY_THROW(observations.recordCommandAdmission(key(7, 0), 11));
    observations.recordCommandTerminal(key(7, 0), 20);
    EXPECT_ANY_THROW(observations.recordCommandTerminal(key(7, 0), 21));
}

TEST(RuntimeObservationsTest, terminalWithoutAdmissionKeepsIssueUnset)
{
    RuntimeObservations observations;
    observations.beginFrame(1, 0);
    observations.recordCommandTerminal(key(9, 2), 30);
    const CommandObservation *row = observations.findCommand(key(9, 2));
    ASSERT_NE(row, nullptr);
    EXPECT_FALSE(row->issued);
    EXPECT_TRUE(row->terminal);
    EXPECT_EQ(row->terminal_tick, 30u);
}

TEST(RuntimeObservationsTest, enginePlanAndEndAreDistinctFacts)
{
    RuntimeObservations observations;
    observations.beginFrame(2, 0);
    observations.recordEnginePlan(key(4, 0), EngineKind::Tensor, 200, 900);
    const EngineExecutionObservation *planned =
        observations.findEngineExecution(key(4, 0));
    ASSERT_NE(planned, nullptr);
    EXPECT_FALSE(planned->ended);
    EXPECT_EQ(planned->scheduled_end_tick, 900u);
    observations.recordEngineEnd(key(4, 0), 901);
    EXPECT_TRUE(planned->ended);
    EXPECT_EQ(planned->end_tick, 901u);
    EXPECT_ANY_THROW(observations.recordEngineEnd(key(4, 0), 902));
    EXPECT_ANY_THROW(observations.recordEngineEnd(key(5, 0), 903));
}

TEST(RuntimeObservationsTest, engineBlockEpisodesFollowHolderChanges)
{
    RuntimeObservations observations;
    observations.beginFrame(1, 0);
    observations.recordEngineBlock(key(9, 0), EngineKind::Vector, {key(3, 0)}, 100,
                                   1, 1);
    observations.recordEngineBlock(key(9, 0), EngineKind::Vector, {key(3, 0)}, 200,
                                   1, 1);
    observations.closeEngineEpisode(key(9, 0));
    observations.recordEngineBlock(key(9, 0), EngineKind::Vector, {key(4, 0)}, 300,
                                   1, 1);
    ASSERT_EQ(observations.view().engine_blocks.size(), 2u);
    EXPECT_EQ(observations.view().engine_blocks[0].episode_index, 0u);
    EXPECT_EQ(observations.view().engine_blocks[0].first_reject_tick, 100u);
    EXPECT_EQ(observations.view().engine_blocks[0].last_reject_tick, 200u);
    EXPECT_EQ(observations.view().engine_blocks[1].episode_index, 1u);
    ASSERT_EQ(observations.view().engine_blocks[1].holders.size(), 1u);
    EXPECT_EQ(observations.view().engine_blocks[1].holders[0].command_id, 4u);
}

TEST(RuntimeObservationsTest, descriptorCompletionRequiresSubmission)
{
    RuntimeObservations observations;
    observations.beginFrame(1, 0);
    EXPECT_ANY_THROW(observations.recordDescriptorCompletion(
        descriptor(6, 0, 3), 500, true, 500, DmaStatus::OK,
        contribution(64, "aa-bb")));
    observations.recordDescriptorSubmission(descriptor(6, 0, 3), 165500, 318500);
    const DescriptorObservation *submitted =
        observations.findDescriptor(descriptor(6, 0, 3));
    ASSERT_NE(submitted, nullptr);
    EXPECT_FALSE(submitted->completed);
    EXPECT_FALSE(submitted->committed);
    EXPECT_EQ(submitted->submit_tick, 165500u);
    EXPECT_EQ(submitted->scheduled_completion_tick, 318500u);
    observations.recordDescriptorCompletion(descriptor(6, 0, 3), 318500, true,
                                            318500, DmaStatus::OK,
                                            contribution(8192, "cc-dd"));
    EXPECT_TRUE(submitted->completed);
    EXPECT_TRUE(submitted->committed);
    EXPECT_EQ(submitted->commit_tick, 318500u);
    EXPECT_EQ(submitted->transfer.payload_digest, "cc-dd");
    EXPECT_ANY_THROW(observations.recordDescriptorCompletion(
        descriptor(6, 0, 3), 400, true, 400, DmaStatus::OK,
        contribution(8, "ee-ff")));
}

TEST(RuntimeObservationsTest, lostDescriptorKeepsPlanWithoutCompletion)
{
    RuntimeObservations observations;
    observations.beginFrame(1, 0);
    observations.recordDescriptorSubmission(descriptor(6, 0, 9), 1000, 2000);
    const DescriptorObservation *row =
        observations.findDescriptor(descriptor(6, 0, 9));
    ASSERT_NE(row, nullptr);
    EXPECT_FALSE(row->completed);
    EXPECT_FALSE(row->committed);
    EXPECT_FALSE(row->has_status);
    EXPECT_FALSE(row->has_transfer);
    EXPECT_EQ(row->scheduled_completion_tick, 2000u);
}

TEST(RuntimeObservationsTest, errorCompletionIsNotASuccessfulCommit)
{
    RuntimeObservations observations;
    observations.beginFrame(1, 0);
    observations.recordDescriptorSubmission(descriptor(6, 0, 1), 10, 20);
    observations.recordDescriptorCompletion(descriptor(6, 0, 1), 20, false, 0,
                                            DmaStatus::AXI_READ_ERROR,
                                            contribution(32, "11-22"));
    const DescriptorObservation *row =
        observations.findDescriptor(descriptor(6, 0, 1));
    ASSERT_NE(row, nullptr);
    EXPECT_TRUE(row->completed);
    EXPECT_FALSE(row->committed);
    EXPECT_EQ(row->commit_tick, 0u);
    EXPECT_EQ(row->status, DmaStatus::AXI_READ_ERROR);
    EXPECT_EQ(row->transfer.read_bytes, 32u);
}

TEST(RuntimeObservationsTest, serializedFrameCarriesEveryIdentity)
{
    RuntimeObservations observations;
    observations.beginFrame(5, 1);
    observations.recordCommandAdmission(key(4, 0), 10);
    observations.recordEnginePlan(key(4, 0), EngineKind::Tensor, 11, 20);
    observations.recordEngineEnd(key(4, 0), 21);
    observations.recordComputeOutput(key(4, 0), 3, 4096, {1, 2, 3, 4});
    observations.recordDescriptorSubmission(descriptor(3, 0, 1), 5, 8);
    observations.recordDescriptorCompletion(descriptor(3, 0, 1), 8, true, 8,
                                            DmaStatus::OK,
                                            contribution(8192, "ab-cd"));
    std::ostringstream out;
    writeObservationFrameJson(out, observations.view());
    const std::string text = out.str();
    EXPECT_NE(text.find("\"instance\": 5"), std::string::npos);
    EXPECT_NE(text.find("\"core_id\": 1"), std::string::npos);
    EXPECT_NE(text.find("\"scheduled_end_tick\": 20"), std::string::npos);
    EXPECT_NE(text.find("\"end_tick\": 21"), std::string::npos);
    EXPECT_NE(text.find("\"commit_tick\": 8"), std::string::npos);
    EXPECT_NE(text.find("\"payload_digest\": \"ab-cd\""), std::string::npos);
    EXPECT_NE(text.find("\"digest_words\": [1,2,3,4]"), std::string::npos);
}

TEST(RuntimeObservationsTest, observationOutsideAFrameFailsClosed)
{
    RuntimeObservations observations;
    EXPECT_ANY_THROW(observations.recordCommandAdmission(key(1, 0), 1));
}

} // namespace

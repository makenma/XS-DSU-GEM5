#include <sstream>
#include <boost/archive/text_iarchive.hpp>
#include <boost/archive/text_oarchive.hpp>
#include <gtest/gtest.h>

#include "dev/ai_mesh/program_scoreboard.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

RuntimeObjectKey
eventKey(uint32_t ordinal)
{
    return staticProgramObject(InstanceGeneration(1),
                               mesh_abi::MeshObjectKind::EVENT, ordinal);
}

RuntimeObjectKey
commandKey(uint32_t ordinal)
{
    return staticProgramObject(InstanceGeneration(1),
                               mesh_abi::MeshObjectKind::COMMAND, ordinal);
}

TEST(ProgramScoreboardTest, BarrierRendezvousByGeneration)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 3, 10));
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(101), 3, 20));
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(102), 3, 30));
}

TEST(ProgramScoreboardTest, BarrierGenerationReopens)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 1, 10));
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(1), commandKey(100), 1, 20));
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(2), commandKey(100), 1, 30));
}

TEST(ProgramScoreboardTest, DuplicateParticipantRejected)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 2, 10));
    EXPECT_ANY_THROW(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 2, 10));
}

TEST(ProgramScoreboardTest, ClosedGenerationOverArrivalRejected)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 1, 10));
    EXPECT_ANY_THROW(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(101), 1, 20));
}

TEST(ProgramScoreboardTest, ResetClearsRendezvous)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 1, 10));
    scoreboard.reset();
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 1, 10));
}


TEST(ProgramScoreboardTest, PartialArrivalStaysCollecting)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 2, 10));
    const auto open = scoreboard.openBarriers();
    ASSERT_EQ(open.size(), 1u);
    EXPECT_EQ(open[0].phase, ProgramScoreboard::BarrierPhase::Collecting);
    EXPECT_EQ(open[0].expected, 2u);
    ASSERT_EQ(open[0].arrivals.size(), 1u);
    EXPECT_EQ(*open[0].arrivals.begin(), commandKey(100));
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(101), 2, 20));
    EXPECT_TRUE(scoreboard.openBarriers().empty());
    const auto history = scoreboard.barrierGroups();
    ASSERT_EQ(history.size(), 1u);
    EXPECT_EQ(history[0].phase, ProgramScoreboard::BarrierPhase::Released);
    EXPECT_EQ(history[0].release_tick, 20u);
}

TEST(ProgramScoreboardTest, ReleasedGroupRecordsArrivalTicks)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(3), commandKey(100), 2, 10));
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(3), commandKey(101), 2, 20));
    const auto history = scoreboard.barrierHistory();
    ASSERT_EQ(history.size(), 1u);
    EXPECT_EQ(history[0].generation, RepeatGeneration(3));
    ASSERT_EQ(history[0].arrivals.size(), 2u);
    EXPECT_EQ(history[0].arrivals[0].participant, commandKey(100));
    EXPECT_EQ(history[0].arrivals[0].tick, 10u);
    EXPECT_EQ(history[0].arrivals[1].tick, 20u);
}

TEST(ProgramScoreboardTest, ErrorCancelsOpenGroupAndKeepsArrivals)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 2, 10));
    scoreboard.cancelOpen(30);
    EXPECT_TRUE(scoreboard.openBarriers().empty());
    const auto history = scoreboard.barrierHistory();
    ASSERT_EQ(history.size(), 1u);
    EXPECT_EQ(history[0].phase, ProgramScoreboard::BarrierPhase::Cancelled);
    EXPECT_EQ(history[0].cancel_tick, 30u);
    ASSERT_EQ(history[0].arrivals.size(), 1u);
    EXPECT_EQ(history[0].arrivals[0].participant, commandKey(100));
    EXPECT_ANY_THROW(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(101), 2, 40));
}

TEST(ProgramScoreboardTest, ResetClearsReleasedAndCancelledHistory)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 1, 10));
    EXPECT_FALSE(scoreboard.arrive(eventKey(8), RepeatGeneration(0), commandKey(100), 2, 20));
    scoreboard.cancelOpen(30);
    EXPECT_EQ(scoreboard.barrierHistory().size(), 2u);
    scoreboard.reset();
    EXPECT_TRUE(scoreboard.barrierHistory().empty());
    EXPECT_TRUE(scoreboard.openBarriers().empty());
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0), commandKey(100), 1, 40));
}


TEST(ProgramScoreboardTest, VisibilityIsKeyedByEventIdentity)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.visible(eventKey(3)));
    scoreboard.set_visible(eventKey(3));
    EXPECT_TRUE(scoreboard.visible(eventKey(3)));
    EXPECT_FALSE(scoreboard.visible(eventKey(4)));
    auto next = eventKey(3);
    next.instance = InstanceGeneration(2);
    EXPECT_FALSE(scoreboard.visible(next));
    scoreboard.unpublish(eventKey(3));
    EXPECT_FALSE(scoreboard.visible(eventKey(3)));
}

TEST(ProgramScoreboardTest, BarrierIdentitySeparatesInstanceAndOverlay)
{
    ProgramScoreboard scoreboard;
    const auto first = eventKey(7);
    auto second = first;
    second.instance = InstanceGeneration(2);
    const auto dynamic = overlayObject(InstanceGeneration(1), 3, 4,
                                      mesh_abi::MeshObjectKind::EVENT, 7);
    EXPECT_TRUE(scoreboard.arrive(first, RepeatGeneration(0), commandKey(100), 1, 10));
    EXPECT_FALSE(scoreboard.arrive(second, RepeatGeneration(0), commandKey(100), 2, 20));
    EXPECT_FALSE(scoreboard.arrive(dynamic, RepeatGeneration(0), commandKey(100), 2, 30));
    EXPECT_EQ(scoreboard.openBarriers().size(), 2u);
    EXPECT_EQ(scoreboard.barrierHistory().size(), 3u);
}

TEST(ProgramScoreboardTest, CheckpointRetainsOpenReleasedAndCancelledGroups)
{
    ProgramScoreboard source;
    source.set_visible(eventKey(9));
    EXPECT_TRUE(source.arrive(eventKey(1), RepeatGeneration(0), commandKey(10), 1, 10));
    EXPECT_FALSE(source.arrive(eventKey(2), RepeatGeneration(1), commandKey(11), 2, 20));
    source.cancelOpen(30);
    EXPECT_FALSE(source.arrive(eventKey(3), RepeatGeneration(2), commandKey(12), 2, 40));
    std::stringstream bytes;
    {
        boost::archive::text_oarchive archive(bytes);
        archive << source;
    }
    ProgramScoreboard restored;
    {
        boost::archive::text_iarchive archive(bytes);
        archive >> restored;
    }
    ASSERT_EQ(restored.barrierHistory().size(), 3u);
    EXPECT_TRUE(restored.visible(eventKey(9)));
    EXPECT_EQ(restored.barrierHistory()[0].release_tick, 10u);
    EXPECT_EQ(restored.barrierHistory()[1].cancel_tick, 30u);
    EXPECT_EQ(restored.barrierHistory()[2].arrivals[0].tick, 40u);
    EXPECT_ANY_THROW(restored.arrive(eventKey(1), RepeatGeneration(0), commandKey(13), 1, 50));
    EXPECT_ANY_THROW(restored.arrive(eventKey(2), RepeatGeneration(1), commandKey(13), 2, 50));
    EXPECT_TRUE(restored.arrive(eventKey(3), RepeatGeneration(2), commandKey(13), 2, 50));
    EXPECT_TRUE(restored.openBarriers().empty());
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

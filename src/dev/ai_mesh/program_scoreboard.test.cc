#include <gtest/gtest.h>

#include "dev/ai_mesh/program_scoreboard.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(ProgramScoreboardTest, BarrierRendezvousByGeneration)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(7, 0, 100, 3, 10));
    EXPECT_FALSE(scoreboard.arrive(7, 0, 101, 3, 20));
    EXPECT_TRUE(scoreboard.arrive(7, 0, 102, 3, 30));
}

TEST(ProgramScoreboardTest, BarrierGenerationReopens)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1, 10));
    EXPECT_TRUE(scoreboard.arrive(7, 1, 100, 1, 20));
    EXPECT_TRUE(scoreboard.arrive(7, 2, 100, 1, 30));
}

TEST(ProgramScoreboardTest, DuplicateParticipantRejected)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(7, 0, 100, 2, 10));
    EXPECT_ANY_THROW(scoreboard.arrive(7, 0, 100, 2, 10));
}

TEST(ProgramScoreboardTest, ClosedGenerationOverArrivalRejected)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1, 10));
    EXPECT_ANY_THROW(scoreboard.arrive(7, 0, 101, 1, 20));
}

TEST(ProgramScoreboardTest, ResetClearsRendezvous)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1, 10));
    scoreboard.reset();
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1, 10));
}


TEST(ProgramScoreboardTest, PartialArrivalStaysCollecting)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(7, 0, 100, 2, 10));
    const auto open = scoreboard.openBarriers();
    ASSERT_EQ(open.size(), 1u);
    EXPECT_EQ(open[0].phase, ProgramScoreboard::BarrierPhase::Collecting);
    EXPECT_EQ(open[0].expected, 2u);
    ASSERT_EQ(open[0].arrivals.size(), 1u);
    EXPECT_EQ(*open[0].arrivals.begin(), 100u);
    EXPECT_TRUE(scoreboard.arrive(7, 0, 101, 2, 20));
    EXPECT_TRUE(scoreboard.openBarriers().empty());
    const auto history = scoreboard.barrierGroups();
    ASSERT_EQ(history.size(), 1u);
    EXPECT_EQ(history[0].phase, ProgramScoreboard::BarrierPhase::Released);
    EXPECT_EQ(history[0].release_tick, 20u);
}

TEST(ProgramScoreboardTest, ReleasedGroupRecordsArrivalTicks)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(7, 3, 100, 2, 10));
    EXPECT_TRUE(scoreboard.arrive(7, 3, 101, 2, 20));
    const auto history = scoreboard.barrierHistory();
    ASSERT_EQ(history.size(), 1u);
    EXPECT_EQ(history[0].generation, 3u);
    ASSERT_EQ(history[0].arrivals.size(), 2u);
    EXPECT_EQ(history[0].arrivals[0].participant, 100u);
    EXPECT_EQ(history[0].arrivals[0].tick, 10u);
    EXPECT_EQ(history[0].arrivals[1].tick, 20u);
}

TEST(ProgramScoreboardTest, ErrorCancelsOpenGroupAndKeepsArrivals)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(7, 0, 100, 2, 10));
    scoreboard.cancelOpen(30);
    EXPECT_TRUE(scoreboard.openBarriers().empty());
    const auto history = scoreboard.barrierHistory();
    ASSERT_EQ(history.size(), 1u);
    EXPECT_EQ(history[0].phase, ProgramScoreboard::BarrierPhase::Cancelled);
    EXPECT_EQ(history[0].cancel_tick, 30u);
    ASSERT_EQ(history[0].arrivals.size(), 1u);
    EXPECT_EQ(history[0].arrivals[0].participant, 100u);
    EXPECT_ANY_THROW(scoreboard.arrive(7, 0, 101, 2, 40));
}

TEST(ProgramScoreboardTest, ResetClearsReleasedAndCancelledHistory)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1, 10));
    EXPECT_FALSE(scoreboard.arrive(8, 0, 100, 2, 20));
    scoreboard.cancelOpen(30);
    EXPECT_EQ(scoreboard.barrierHistory().size(), 2u);
    scoreboard.reset();
    EXPECT_TRUE(scoreboard.barrierHistory().empty());
    EXPECT_TRUE(scoreboard.openBarriers().empty());
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1, 40));
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

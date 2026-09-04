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
    EXPECT_FALSE(scoreboard.arrive(7, 0, 100, 3));
    EXPECT_FALSE(scoreboard.arrive(7, 0, 101, 3));
    EXPECT_TRUE(scoreboard.arrive(7, 0, 102, 3));
}

TEST(ProgramScoreboardTest, BarrierGenerationReopens)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1));
    EXPECT_TRUE(scoreboard.arrive(7, 1, 100, 1));
    EXPECT_TRUE(scoreboard.arrive(7, 2, 100, 1));
}

TEST(ProgramScoreboardTest, DuplicateParticipantRejected)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(7, 0, 100, 2));
    EXPECT_ANY_THROW(scoreboard.arrive(7, 0, 100, 2));
}

TEST(ProgramScoreboardTest, ClosedGenerationOverArrivalRejected)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1));
    EXPECT_ANY_THROW(scoreboard.arrive(7, 0, 101, 1));
}

TEST(ProgramScoreboardTest, ResetClearsRendezvous)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1));
    scoreboard.reset();
    EXPECT_TRUE(scoreboard.arrive(7, 0, 100, 1));
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

#include <gtest/gtest.h>

#include "dev/ai_mesh/program_scoreboard.hh"
#include "dev/ai_mesh/runtime_key.hh"

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
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                   commandKey(100), 3));
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                   commandKey(101), 3));
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                  commandKey(102), 3));
}

TEST(ProgramScoreboardTest, BarrierGenerationReopens)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                  commandKey(100), 1));
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(1),
                                  commandKey(100), 1));
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(2),
                                  commandKey(100), 1));
}

TEST(ProgramScoreboardTest, DuplicateParticipantRejected)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                   commandKey(100), 2));
    EXPECT_ANY_THROW(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                       commandKey(100), 2));
}

TEST(ProgramScoreboardTest, ClosedGenerationOverArrivalRejected)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                  commandKey(100), 1));
    EXPECT_ANY_THROW(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                       commandKey(101), 1));
}

TEST(ProgramScoreboardTest, ResetClearsRendezvous)
{
    ProgramScoreboard scoreboard;
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                  commandKey(100), 1));
    scoreboard.reset();
    EXPECT_TRUE(scoreboard.arrive(eventKey(7), RepeatGeneration(0),
                                  commandKey(100), 1));
}

TEST(ProgramScoreboardTest, VisibilityIsKeyedByEventIdentity)
{
    ProgramScoreboard scoreboard;
    EXPECT_FALSE(scoreboard.visible(eventKey(3)));
    scoreboard.set_visible(eventKey(3));
    EXPECT_TRUE(scoreboard.visible(eventKey(3)));
    EXPECT_FALSE(scoreboard.visible(eventKey(4)));
    scoreboard.unpublish(eventKey(3));
    EXPECT_FALSE(scoreboard.visible(eventKey(3)));
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

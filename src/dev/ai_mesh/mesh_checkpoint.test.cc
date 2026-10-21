#include <gtest/gtest.h>
#include <sstream>
#include <boost/archive/binary_iarchive.hpp>
#include <boost/archive/binary_oarchive.hpp>

#include "dev/ai_mesh/mesh_moe_runtime.hh"

using namespace gem5::ai_mesh;

TEST(MeshCheckpoint, PreservesUnstartedAndDrainedOverlayGraphs)
{
    for (bool started : {false, true}) {
        ProgramScoreboard scoreboard;
        MoeOverlayExecutor source({}, &scoreboard, nullptr, nullptr);
        MoeOverlayExecutor restored({}, &scoreboard, nullptr, nullptr);
        MoeOverlayGraph graph;
        source.load(7, InstanceGeneration(9), graph);
        if (started)
            source.publishEntry(staticProgramObject(InstanceGeneration(9),
                mesh_abi::MeshObjectKind::EVENT, 1), 123);
        std::stringstream bytes;
        { boost::archive::binary_oarchive archive(bytes); archive << source; }
        { boost::archive::binary_iarchive archive(bytes); archive >> restored; }
        EXPECT_EQ(restored.started(), started);
        EXPECT_EQ(restored.drained(), started);
        EXPECT_EQ(restored.layerId(), 7u);
        EXPECT_EQ(restored.describeBlocked(), source.describeBlocked());
        EXPECT_EQ(restored.uncommittedViews(), source.uncommittedViews());
    }
}

#include <gtest/gtest.h>

#include <array>
#include <cstdint>

#include "mem/ruby/network/garnet/GarnetQuiescence.hh"

namespace gem5
{
namespace ruby
{
namespace garnet
{

namespace
{

using Field = uint64_t GarnetQuiescenceSnapshot::*;

constexpr std::array<Field, 9> Fields = {
    &GarnetQuiescenceSnapshot::niQueuedFlits,
    &GarnetQuiescenceSnapshot::niQueuedMessages,
    &GarnetQuiescenceSnapshot::routerBufferedFlits,
    &GarnetQuiescenceSnapshot::nonIdleInputVcs,
    &GarnetQuiescenceSnapshot::nonIdleOutputVcs,
    &GarnetQuiescenceSnapshot::dataLinkPendingFlits,
    &GarnetQuiescenceSnapshot::creditLinkPendingCredits,
    &GarnetQuiescenceSnapshot::bridgePendingItems,
    &GarnetQuiescenceSnapshot::creditDeficit,
};

} // anonymous namespace

TEST(GarnetQuiescenceSnapshotTest, DetectsEveryPendingClass)
{
    for (const Field field : Fields) {
        GarnetQuiescenceSnapshot snapshot;
        EXPECT_TRUE(snapshot.empty());
        snapshot.*field = 1;
        EXPECT_FALSE(snapshot.empty());
    }
}

TEST(GarnetQuiescenceSnapshotTest, BecomesEmptyOnlyAfterRestore)
{
    GarnetQuiescenceSnapshot snapshot;
    for (const Field field : Fields)
        snapshot.*field = 1;
    EXPECT_FALSE(snapshot.empty());
    for (size_t index = 0; index < Fields.size(); ++index) {
        snapshot.*Fields[index] = 0;
        EXPECT_EQ(snapshot.empty(), index + 1 == Fields.size());
    }
}

TEST(GarnetQuiescenceSnapshotTest, AccessorsDoNotScheduleEvents)
{
    const GarnetQuiescenceSnapshot snapshot{
        1, 2, 3, 4, 5, 6, 7, 8, 9};
    for (int iteration = 0; iteration < 100; ++iteration)
        EXPECT_FALSE(snapshot.empty());
    EXPECT_EQ(snapshot.niQueuedFlits, 1);
    EXPECT_EQ(snapshot.niQueuedMessages, 2);
    EXPECT_EQ(snapshot.routerBufferedFlits, 3);
    EXPECT_EQ(snapshot.nonIdleInputVcs, 4);
    EXPECT_EQ(snapshot.nonIdleOutputVcs, 5);
    EXPECT_EQ(snapshot.dataLinkPendingFlits, 6);
    EXPECT_EQ(snapshot.creditLinkPendingCredits, 7);
    EXPECT_EQ(snapshot.bridgePendingItems, 8);
    EXPECT_EQ(snapshot.creditDeficit, 9);
}

} // namespace garnet
} // namespace ruby
} // namespace gem5

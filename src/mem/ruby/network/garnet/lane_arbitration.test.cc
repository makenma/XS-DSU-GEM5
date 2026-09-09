#include <array>
#include <gtest/gtest.h>

#include "mem/ruby/network/garnet/LaneArbiter.hh"

namespace gem5::ruby::garnet
{

TEST(LaneArbitrationTest, OtherLaneCannotStarveAnInput)
{
    std::array<LaneArbiter, 2> lanes{
        LaneArbiter({0, 2}, 1, 4), LaneArbiter({1}, 1, 4)};
    LaneMergeArbiter merge;
    std::array<int, 3> grants{};
    for (int cycle = 0; cycle < 120; ++cycle) {
        lanes[0].request(0, 0, 0);
        lanes[0].request(1, 0, 0);
        lanes[1].request(0, 0, 0);
        const auto lane = merge.select({true, true});
        ASSERT_TRUE(lane);
        auto &arbiter = lanes[*lane];
        const auto winner = arbiter.candidate(0);
        ASSERT_TRUE(winner);
        ++grants[arbiter.inputPort(winner->slot)];
        arbiter.commit(0, *winner);
        merge.commit(*lane);
        for (auto &entry : lanes)
            entry.clearRequests();
    }
    EXPECT_EQ(grants, (std::array<int, 3>{30, 60, 30}));
}

TEST(LaneArbitrationTest, RejectedCandidateDoesNotAdvanceEitherArbiter)
{
    LaneArbiter lane({0, 2}, 2, 4);
    LaneMergeArbiter merge;
    lane.request(0, 0, 2);
    lane.request(1, 0, 3);
    for (int attempt = 0; attempt < 5; ++attempt) {
        EXPECT_EQ(lane.candidate(0)->slot, 0);
        EXPECT_EQ(lane.firstVc(0), 0);
        EXPECT_EQ(merge.select({true, true}), 0);
    }
    lane.commit(0, *lane.candidate(0));
    merge.commit(0);
    EXPECT_EQ(lane.firstVc(0), 3);
    EXPECT_EQ(lane.candidate(0)->slot, 1);
    EXPECT_EQ(merge.select({true, true}), 1);
}

TEST(LaneArbitrationTest, OutputsAndLanesProgressIndependently)
{
    LaneArbiter a({0, 2}, 2, 4), b({1, 3}, 2, 4);
    a.request(0, 0, 1);
    a.request(1, 1, 2);
    b.request(0, 0, 3);
    b.request(1, 1, 0);
    a.commit(0, *a.candidate(0));
    EXPECT_EQ(b.candidate(0)->slot, 0);
    EXPECT_EQ(b.firstVc(0), 0);
    EXPECT_EQ(a.candidate(1)->slot, 1);
    b.commit(1, *b.candidate(1));
    EXPECT_EQ(b.firstVc(1), 1);
    EXPECT_EQ(a.firstVc(1), 0);
}

TEST(LaneArbitrationTest, EmptyLaneDoesNotBlockAvailableLane)
{
    LaneMergeArbiter merge;
    EXPECT_FALSE(merge.select({false, false}));
    EXPECT_EQ(merge.select({false, true}), 1);
    merge.commit(1);
    EXPECT_EQ(merge.select({true, true}), 0);
    EXPECT_EQ(merge.select({true, false}), 0);
}

TEST(LaneArbitrationTest, SingleLanePreservesInputAndVcRoundRobin)
{
    LaneArbiter lane({0, 1, 2}, 1, 4);
    for (int cycle = 0; cycle < 12; ++cycle) {
        for (size_t slot = 0; slot < lane.size(); ++slot)
            lane.request(slot, 0, lane.firstVc(slot));
        const auto winner = lane.candidate(0);
        ASSERT_TRUE(winner);
        EXPECT_EQ(lane.inputPort(winner->slot), cycle % 3);
        EXPECT_EQ(winner->vc, cycle / 3);
        lane.commit(0, *winner);
        lane.clearRequests();
    }
}

}

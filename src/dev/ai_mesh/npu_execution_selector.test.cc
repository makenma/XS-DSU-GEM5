#include <gtest/gtest.h>

#include <algorithm>
#include <vector>

#include "dev/ai_mesh/npu_execution_selector.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

NpuExecutionCandidate candidate(uint64_t requestId, uint64_t deadline,
                                uint64_t readyTick, uint8_t qos)
{
    return NpuExecutionCandidate{requestId, deadline, readyTick, qos};
}

TEST(NpuExecutionSelectorTest, EarlierDeadlineWinsFirst)
{
    EXPECT_TRUE(npuExecutionPrecedes(candidate(9, 500, 900, 1),
                                     candidate(1, 600, 100, 4)));
    EXPECT_FALSE(npuExecutionPrecedes(candidate(1, 600, 100, 4),
                                      candidate(9, 500, 900, 1)));
}

TEST(NpuExecutionSelectorTest, MissingDeadlineSortsLast)
{
    EXPECT_TRUE(npuExecutionPrecedes(
        candidate(9, 1, 900, 0), candidate(1, kNpuNoDeadline, 100, 4)));
    EXPECT_TRUE(npuExecutionPrecedes(
        candidate(2, kNpuNoDeadline, 900, 0),
        candidate(1, kNpuNoDeadline, 100, 4)) == false);
    EXPECT_EQ(kNpuNoDeadline, UINT64_MAX);
}

TEST(NpuExecutionSelectorTest, EqualDeadlineBreaksByQosDescending)
{
    EXPECT_TRUE(npuExecutionPrecedes(candidate(9, 500, 900, 4),
                                     candidate(1, 500, 100, 3)));
    EXPECT_FALSE(npuExecutionPrecedes(candidate(1, 500, 100, 3),
                                      candidate(9, 500, 900, 4)));
}

TEST(NpuExecutionSelectorTest, EqualDeadlineQosBreaksByReadyTick)
{
    EXPECT_TRUE(npuExecutionPrecedes(candidate(9, 500, 100, 2),
                                     candidate(1, 500, 200, 2)));
    EXPECT_FALSE(npuExecutionPrecedes(candidate(1, 500, 200, 2),
                                      candidate(9, 500, 100, 2)));
}

TEST(NpuExecutionSelectorTest, FullTieBreaksByRequestId)
{
    EXPECT_TRUE(npuExecutionPrecedes(candidate(3, 500, 100, 2),
                                     candidate(7, 500, 100, 2)));
    EXPECT_FALSE(npuExecutionPrecedes(candidate(7, 500, 100, 2),
                                      candidate(3, 500, 100, 2)));
    EXPECT_FALSE(npuExecutionPrecedes(candidate(3, 500, 100, 2),
                                      candidate(3, 500, 100, 2)));
}

TEST(NpuExecutionSelectorTest, SelectionIsTotalAndRepeatable)
{
    const std::vector<NpuExecutionCandidate> queue = {
        candidate(5, kNpuNoDeadline, 400, 2),
        candidate(2, 900, 700, 1),
        candidate(8, 300, 500, 0),
        candidate(1, 300, 600, 3),
        candidate(4, 300, 500, 3),
        candidate(7, kNpuNoDeadline, 100, 4),
    };
    std::vector<uint64_t> first;
    for (const NpuExecutionCandidate &item : queue)
        first.push_back(item.requestId);
    std::vector<uint64_t> order;
    std::vector<NpuExecutionCandidate> remaining(queue);
    while (!remaining.empty()) {
        auto best = remaining.begin();
        for (auto it = remaining.begin(); it != remaining.end(); ++it)
            if (npuExecutionPrecedes(*it, *best))
                best = it;
        order.push_back(best->requestId);
        remaining.erase(best);
    }
    EXPECT_EQ(order, (std::vector<uint64_t>{4, 1, 8, 2, 7, 5}));
    std::vector<uint64_t> again;
    remaining = queue;
    while (!remaining.empty()) {
        auto best = remaining.begin();
        for (auto it = remaining.begin(); it != remaining.end(); ++it)
            if (npuExecutionPrecedes(*it, *best))
                best = it;
        again.push_back(best->requestId);
        remaining.erase(best);
    }
    EXPECT_EQ(again, order);
}

}
}
}

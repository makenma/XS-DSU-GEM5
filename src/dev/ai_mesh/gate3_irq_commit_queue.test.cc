#include <gtest/gtest.h>

#include <algorithm>

#include "dev/ai_mesh/gate3_irq_commit_queue.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(Gate3IrqCommitQueueTest, CallbackPermutationsProduceSamePrefix)
{
    std::vector<Gate3IrqCommit> input{{1, 9, 32}, {2, 10, 33}, {3, 11, 34}};
    std::vector<uint64_t> expected;
    do {
        Gate3IrqCommitQueue queue(3);
        for (const auto &commit : input)
            EXPECT_EQ(queue.stage(commit), Gate3IrqStageResult::Accepted);
        const auto prefix = queue.consumePrefix();
        std::vector<uint64_t> tails;
        for (const auto &commit : prefix)
            tails.push_back(commit.tail);
        if (expected.empty())
            expected = tails;
        EXPECT_EQ(tails, expected);
    } while (std::next_permutation(
        input.begin(), input.end(),
        [](const auto &left, const auto &right) {
            return left.tail < right.tail;
        }));
    EXPECT_EQ(expected, (std::vector<uint64_t>{1, 2, 3}));
}

TEST(Gate3IrqCommitQueueTest, HoldsHoleAndClassifiesDuplicates)
{
    Gate3IrqCommitQueue queue(3);
    EXPECT_EQ(queue.stage({2, 10, 33}), Gate3IrqStageResult::Accepted);
    EXPECT_TRUE(queue.consumePrefix().empty());
    EXPECT_EQ(queue.stage({2, 10, 33}), Gate3IrqStageResult::Duplicate);
    EXPECT_EQ(queue.stage({2, 11, 33}), Gate3IrqStageResult::Conflict);
    EXPECT_EQ(queue.stage({1, 9, 32}), Gate3IrqStageResult::Accepted);
    EXPECT_EQ(queue.consumePrefix(),
              (std::vector<Gate3IrqCommit>{{1, 9, 32}, {2, 10, 33}}));
    EXPECT_EQ(queue.stage({1, 9, 32}), Gate3IrqStageResult::Stale);
}

}
}
}

#include <gtest/gtest.h>
#include "dev/ai_mesh/publish_segment_ledger.hh"
#include "dev/ai_mesh/mesh_splitter.hh"

using namespace gem5::ai_mesh;

TEST(PublishSegmentLedger, CrossPageChainRequiresMatchingSuccessfulResponse)
{
    PublishSegmentLedger ledger({{1, 10, 0, 192}, {2, 10, 192, 64}, {3, 11, 0, 64}});
    const auto bursts = splitBursts(4096 - 64, 192, 64, 16);
    ASSERT_EQ(bursts.size(), 2u);
    ASSERT_EQ(ledger.decision(1, 5), PublishSegmentLedger::Decision::Issue);
    ASSERT_TRUE(ledger.issue(1, 0, bursts[0].useful_bytes, 5));
    EXPECT_FALSE(ledger.commit(1, 0, 64, true, 4));
    EXPECT_EQ(ledger.decision(1, 6), PublishSegmentLedger::Decision::Wait);
    EXPECT_EQ(ledger.decision(2, 6), PublishSegmentLedger::Decision::Wait);
    EXPECT_EQ(ledger.decision(3, 6), PublishSegmentLedger::Decision::Issue);
    EXPECT_FALSE(ledger.commit(1, 64, 64, true, 7));
    EXPECT_EQ(ledger.prefix(10), 0u);
    ASSERT_TRUE(ledger.commit(1, 0, 64, true, 7));
    EXPECT_EQ(ledger.prefix(10), 64u);
    EXPECT_EQ(ledger.decision(1, 7), PublishSegmentLedger::Decision::Wait);
    EXPECT_EQ(ledger.decision(1, 8), PublishSegmentLedger::Decision::Issue);
    ASSERT_TRUE(ledger.issue(1, 64, 128, 8));
    ASSERT_TRUE(ledger.commit(1, 64, 128, true, 9));
    EXPECT_EQ(ledger.prefix(10), 192u);
    EXPECT_EQ(ledger.decision(2, 10), PublishSegmentLedger::Decision::Issue);
}

TEST(PublishSegmentLedger, CancelDrainsStartedDescriptorAndSuppressesFollowingOnes)
{
    for (bool cancel_first : {false, true}) {
        PublishSegmentLedger ledger({{1, 10, 0, 128}, {2, 10, 128, 64}, {3, 11, 0, 64}});
        if (cancel_first) { ASSERT_TRUE(ledger.cancel(10, 5)); }
        ASSERT_TRUE(ledger.issue(1, 0, 64, 5));
        if (!cancel_first) { ASSERT_TRUE(ledger.cancel(10, 5)); }
        ASSERT_TRUE(ledger.commit(1, 0, 64, true, 6));
        EXPECT_EQ(ledger.decision(1, 7), PublishSegmentLedger::Decision::Issue);
        ASSERT_TRUE(ledger.issue(1, 64, 64, 7));
        ASSERT_TRUE(ledger.commit(1, 64, 64, true, 8));
        EXPECT_EQ(ledger.decision(2, 9), PublishSegmentLedger::Decision::Suppress);
        ASSERT_TRUE(ledger.suppress(2, 9));
        EXPECT_EQ(ledger.prefix(10), 128u);
        EXPECT_EQ(ledger.decision(3, 9), PublishSegmentLedger::Decision::Issue);
    }
}

TEST(PublishSegmentLedger, ErrorStopsUnissuedBytesAndPreservesOnlyCommittedPrefix)
{
    PublishSegmentLedger ledger({{1, 10, 0, 192}, {2, 10, 192, 64}});
    ASSERT_TRUE(ledger.issue(1, 0, 64, 5));
    ASSERT_TRUE(ledger.commit(1, 0, 64, true, 6));
    ASSERT_TRUE(ledger.issue(1, 64, 64, 7));
    ASSERT_TRUE(ledger.commit(1, 64, 64, false, 8));
    EXPECT_EQ(ledger.prefix(10), 64u);
    EXPECT_EQ(ledger.decision(1, 9), PublishSegmentLedger::Decision::Stop);
    EXPECT_EQ(ledger.decision(2, 9), PublishSegmentLedger::Decision::Stop);
    EXPECT_FALSE(ledger.issue(1, 128, 64, 9));
    EXPECT_FALSE(ledger.commit(1, 64, 64, true, 9));
}

TEST(PublishSegmentLedger, FrozenPlanRejectsHolesOverlapAndDuplicateDescriptor)
{
    EXPECT_THROW(PublishSegmentLedger({{1, 10, 1, 64}}), std::invalid_argument);
    EXPECT_THROW(PublishSegmentLedger({{1, 10, 0, 64}, {2, 10, 32, 64}}), std::invalid_argument);
    EXPECT_THROW(PublishSegmentLedger({{1, 10, 0, 64}, {1, 11, 0, 64}}), std::invalid_argument);
}

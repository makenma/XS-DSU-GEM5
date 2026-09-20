#include <gtest/gtest.h>

#include <cstdint>
#include <utility>
#include <vector>

#include "dev/ai_mesh/peer_transfer_coverage.hh"

using namespace gem5::ai_mesh;
using gem5::Tick;

namespace
{

using Ranges = std::vector<std::pair<uint64_t, uint64_t>>;

PeerTransferCoverageTable::CommitFacts
commitFacts(uint64_t address, uint64_t bytes, uint64_t uid, Tick tick = 1000)
{
    PeerTransferCoverageTable::CommitFacts facts;
    facts.tick = tick;
    facts.base_address = address;
    facts.bus_bytes = 1;
    for (uint64_t offset = 0; offset < bytes; offset++)
        facts.lanes.push_back(address + offset);
    facts.has_meta = true;
    facts.txn_uid = uid;
    return facts;
}

TEST(PeerTransferCoverageTest, HealthyReuseCompletesWithItsOwnTransactions)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x1000, 0x1010}});
    auto first = table.commit(commitFacts(0x1000, 16, 7));
    EXPECT_EQ(first.completed, std::vector<uint32_t>{1});
    EXPECT_TRUE(table.notified(1));

    table.expect(2, {{0x1000, 0x1010}});
    auto second = table.commit(commitFacts(0x1000, 16, 8));
    EXPECT_EQ(second.completed, std::vector<uint32_t>{2});
    EXPECT_TRUE(table.notified(2));

    auto rows = table.rows();
    ASSERT_EQ(rows.size(), 2u);
    EXPECT_EQ(rows[0].transaction_uids, std::vector<uint64_t>{7});
    EXPECT_EQ(rows[1].transaction_uids, std::vector<uint64_t>{8});
    EXPECT_EQ(rows[0].replayed_lanes, 0u);
    EXPECT_EQ(rows[1].replayed_lanes, 0u);
}

TEST(PeerTransferCoverageTest, ReplayedRetiredTransactionCannotCompleteANewTransfer)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x1000, 0x1010}});
    EXPECT_EQ(table.commit(commitFacts(0x1000, 16, 7)).completed,
              std::vector<uint32_t>{1});

    table.expect(2, {{0x1000, 0x1010}});
    auto replay = table.commit(commitFacts(0x1000, 16, 7));
    EXPECT_TRUE(replay.completed.empty());
    EXPECT_EQ(replay.newly_covered, 0u);
    EXPECT_EQ(replay.replayed_lanes, 16u);
    EXPECT_FALSE(table.notified(2));

    auto rows = table.rows();
    ASSERT_EQ(rows.size(), 2u);
    EXPECT_EQ(rows[1].transfer_id, 2u);
    EXPECT_EQ(rows[1].covered_bytes, 0u);
    EXPECT_EQ(rows[1].uncovered_bytes, 16u);
    EXPECT_EQ(rows[1].replayed_lanes, 16u);
    EXPECT_TRUE(rows[1].transaction_uids.empty());
    EXPECT_EQ(table.replayedLanes(), 16u);

    // The new transfer still completes through its own real transaction.
    EXPECT_EQ(table.commit(commitFacts(0x1000, 16, 8)).completed,
              std::vector<uint32_t>{2});
    EXPECT_TRUE(table.notified(2));
}

TEST(PeerTransferCoverageTest, StaleReplayAfterAnInstanceResetIsRejected)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x2000, 0x2008}});
    EXPECT_EQ(table.commit(commitFacts(0x2000, 8, 11)).completed,
              std::vector<uint32_t>{1});

    table.beginInstance();
    table.expect(1, {{0x2000, 0x2008}});
    auto replay = table.commit(commitFacts(0x2000, 8, 11, 5000));
    EXPECT_TRUE(replay.completed.empty());
    EXPECT_EQ(replay.replayed_lanes, 8u);
    EXPECT_FALSE(table.notified(1));

    EXPECT_EQ(table.commit(commitFacts(0x2000, 8, 12, 6000)).completed,
              std::vector<uint32_t>{1});
}

TEST(PeerTransferCoverageTest, ATransactionCannotCoverTwoTransfers)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x3000, 0x3008}});
    table.expect(2, {{0x4000, 0x4008}});
    EXPECT_TRUE(table.commit(commitFacts(0x3000, 4, 21)).completed.empty());

    auto foreign = table.commit(commitFacts(0x4000, 4, 21));
    EXPECT_TRUE(foreign.completed.empty());
    EXPECT_EQ(foreign.replayed_lanes, 4u);
    EXPECT_FALSE(table.notified(2));

    EXPECT_EQ(table.commit(commitFacts(0x3004, 4, 22)).completed,
              std::vector<uint32_t>{1});
    // The refused foreign byte leaves a real hole: transfer 2 completes only
    // once its own accepted transactions cover the whole admitted range.
    EXPECT_TRUE(table.commit(commitFacts(0x4000, 4, 23)).completed.empty());
    EXPECT_EQ(table.commit(commitFacts(0x4004, 4, 23)).completed,
              std::vector<uint32_t>{2});
}

TEST(PeerTransferCoverageTest, ReplayOfALiveTransactionIsIdempotent)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x5000, 0x5010}});
    EXPECT_TRUE(table.commit(commitFacts(0x5000, 8, 31)).completed.empty());
    auto duplicate = table.commit(commitFacts(0x5000, 8, 31));
    EXPECT_EQ(duplicate.newly_covered, 0u);
    EXPECT_EQ(duplicate.replayed_lanes, 0u);
    auto rows = table.rows();
    ASSERT_EQ(rows.size(), 1u);
    EXPECT_EQ(rows[0].duplicate_notifications, 8u);
    EXPECT_EQ(rows[0].covered_bytes, 8u);
    EXPECT_EQ(rows[0].transaction_uids, std::vector<uint64_t>{31});
}

TEST(PeerTransferCoverageTest, MockCommitsWithoutMetaKeepAddressAttribution)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x6000, 0x6008}});
    auto facts = commitFacts(0x6000, 8, 0);
    facts.has_meta = false;
    EXPECT_EQ(table.commit(facts).completed, std::vector<uint32_t>{1});
    EXPECT_TRUE(table.notified(1));
}

TEST(PeerTransferCoverageTest, AbandonedExpectationKeepsItsPartialCoverage)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x7000, 0x7010}});
    EXPECT_TRUE(table.commit(commitFacts(0x7000, 4, 41)).completed.empty());
    table.abandonAll();
    auto rows = table.rows();
    ASSERT_EQ(rows.size(), 1u);
    EXPECT_EQ(rows[0].covered_bytes, 4u);
    EXPECT_EQ(rows[0].uncovered_bytes, 12u);
    EXPECT_TRUE(rows[0].abandoned);
}

TEST(PeerTransferCoverageTest, LandingEventsCarryTickIdentityAndMergedRuns)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x8000, 0x8010}});
    EXPECT_TRUE(table.commit(commitFacts(0x8000, 4, 51, 2000)).completed
                      .empty());
    EXPECT_TRUE(table.commit(commitFacts(0x8006, 4, 52, 3000)).completed
                      .empty());
    EXPECT_EQ(table.commit(commitFacts(0x8004, 12, 53, 4000)).completed,
              std::vector<uint32_t>{1});

    auto rows = table.rows();
    ASSERT_EQ(rows.size(), 1u);
    const auto &events = rows[0].landing_events;
    ASSERT_EQ(events.size(), 3u);
    EXPECT_EQ(events[0].tick, 2000u);
    EXPECT_EQ(events[0].txn_uid, 51u);
    EXPECT_EQ(events[0].ranges, Ranges({{0x8000, 4}}));
    EXPECT_EQ(events[1].tick, 3000u);
    EXPECT_EQ(events[1].txn_uid, 52u);
    EXPECT_EQ(events[1].ranges, Ranges({{0x8006, 4}}));
    EXPECT_EQ(events[2].tick, 4000u);
    EXPECT_EQ(events[2].txn_uid, 53u);
    // Only the bytes this commit newly covered belong to the event: the
    // already covered middle run stays with the event that landed it.
    EXPECT_EQ(events[2].ranges, Ranges({{0x8004, 2}, {0x800A, 6}}));
}

TEST(PeerTransferCoverageTest, DuplicateAndReplayedLanesCreateNoLandingEvent)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0x9000, 0x9008}});
    EXPECT_EQ(table.commit(commitFacts(0x9000, 8, 61)).completed,
              std::vector<uint32_t>{1});
    auto duplicate = table.commit(commitFacts(0x9000, 8, 61));
    EXPECT_EQ(duplicate.newly_covered, 0u);

    table.expect(2, {{0x9000, 0x9008}});
    auto replay = table.commit(commitFacts(0x9004, 4, 61));
    EXPECT_EQ(replay.replayed_lanes, 4u);

    auto rows = table.rows();
    ASSERT_EQ(rows.size(), 2u);
    const PeerTransferCoverage *second = nullptr;
    for (const auto &row : rows)
        if (row.transfer_id == 2u)
            second = &row;
    ASSERT_NE(second, nullptr);
    EXPECT_TRUE(second->landing_events.empty());
    ASSERT_EQ(rows[0].landing_events.size(), 1u);
    EXPECT_EQ(rows[0].landing_events[0].ranges, Ranges({{0x9000, 8}}));
}

TEST(PeerTransferCoverageTest, LandingEventRunsMergeAdjacentLanes)
{
    PeerTransferCoverageTable table;
    table.expect(1, {{0xA000, 0xA008}});
    EXPECT_EQ(table.commit(commitFacts(0xA000, 8, 71)).completed,
              std::vector<uint32_t>{1});
    auto rows = table.rows();
    ASSERT_EQ(rows.size(), 1u);
    ASSERT_EQ(rows[0].landing_events.size(), 1u);
    EXPECT_EQ(rows[0].landing_events[0].ranges, Ranges({{0xA000, 8}}));
    EXPECT_EQ(rows[0].landing_events[0].txn_uid, 71u);
}

} // namespace

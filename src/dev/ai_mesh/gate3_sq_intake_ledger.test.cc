#include "dev/ai_mesh/gate3_sq_intake_ledger.hh"

#include <gtest/gtest.h>

namespace gem5
{
namespace ai_mesh
{

TEST(Gate3SqIntakeLedgerTest, TransfersPreArReservationExactlyOnce)
{
    Gate3SqIntakeLedger ledger;
    const SqIntakeId id = ledger.reserve(SqSeq(7));

    EXPECT_EQ(id.value(), 1);
    EXPECT_EQ(ledger.createdCount(), 1);
    EXPECT_EQ(ledger.liveCount(), 1);

    const auto transferred = ledger.transferLiveToFatal();
    ASSERT_EQ(transferred.size(), 1);
    EXPECT_EQ(transferred.front().id, id);
    EXPECT_EQ(transferred.front().expectedSqSeq, SqSeq(7));
    EXPECT_EQ(transferred.front().readTag, 0);
    EXPECT_EQ(transferred.front().stateAtCut,
              Gate3SqIntakeState::PreAr);
    EXPECT_EQ(transferred.front().terminal,
              Gate3SqIntakeTerminal::None);
    EXPECT_EQ(ledger.liveCount(), 0);
    EXPECT_EQ(ledger.fatalCount(), 1);
    EXPECT_TRUE(ledger.transferLiveToFatal().empty());
}

TEST(Gate3SqIntakeLedgerTest, RetainsPostCutReadEvidence)
{
    Gate3SqIntakeLedger ledger;
    const SqIntakeId id = ledger.reserve(SqSeq(3));
    ASSERT_TRUE(ledger.acceptRead(id, 91));
    ASSERT_EQ(ledger.transferLiveToFatal().size(), 1);

    ASSERT_TRUE(ledger.observeTerminal(
        id, Gate3SqIntakeTerminal::ROk));
    const auto &record = ledger.fatalRecords().at(id);
    EXPECT_EQ(record.stateAtCut, Gate3SqIntakeState::WaitR);
    EXPECT_EQ(record.terminal, Gate3SqIntakeTerminal::ROk);
    EXPECT_EQ(record.readTag, 91);
    EXPECT_EQ(ledger.releasedCount(), 0);
}

TEST(Gate3SqIntakeLedgerTest, ReleasedIntakeIsOutsideFatalOwnership)
{
    Gate3SqIntakeLedger ledger;
    const SqIntakeId id = ledger.reserve(SqSeq(0));
    ASSERT_TRUE(ledger.acceptRead(id, 8));
    ASSERT_TRUE(ledger.observeTerminal(
        id, Gate3SqIntakeTerminal::ROk));
    ASSERT_TRUE(ledger.release(id));

    EXPECT_EQ(ledger.createdCount(), 1);
    EXPECT_EQ(ledger.releasedCount(), 1);
    EXPECT_EQ(ledger.liveCount(), 0);
    EXPECT_EQ(ledger.fatalCount(), 0);
    EXPECT_TRUE(ledger.transferLiveToFatal().empty());
}

}
}

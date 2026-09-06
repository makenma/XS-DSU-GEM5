#include <gtest/gtest.h>

#include <stdexcept>

#include "dev/ai_mesh/gate3_completion_ledger.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(Gate3CompletionLedgerTest, FatalCutFreezesProtocolState)
{
    Gate3CompletionLedger ledger(2);
    const auto id = ledger.reserve(SqSeq(0), RequestId(1), CompletionCookie(1));
    ASSERT_TRUE(id);
    ASSERT_TRUE(ledger.markTerminalReady(*id, 0, 0, 1));
    ASSERT_TRUE(ledger.assign(*id));
    ASSERT_TRUE(ledger.issueMsi(*id, 32));
    ledger.transferLiveToFatal();
    EXPECT_FALSE(ledger.completeMsi(32, axi::AxiResp::Okay));
    EXPECT_THROW(ledger.acceptAck(1), std::logic_error);
    EXPECT_FALSE(ledger.reserve(SqSeq(1), RequestId(2), CompletionCookie(2)));
    EXPECT_EQ(ledger.notifiedSequence(), 0u);
    EXPECT_EQ(ledger.issuedSequence(), 1u);
    EXPECT_EQ(ledger.ackReceivedSequence(), 0u);
    EXPECT_EQ(ledger.msiRobEntries(), 1u);
    EXPECT_EQ(ledger.fatalCount(), 1u);
    EXPECT_EQ(ledger.liveCount(), 0u);
}

TEST(Gate3CompletionLedgerTest, CapacityIsReservedAtAdmission)
{
    Gate3CompletionLedger ledger(2);
    EXPECT_TRUE(ledger.reserve(SqSeq(0), RequestId(1), CompletionCookie(1)));
    EXPECT_TRUE(ledger.reserve(SqSeq(1), RequestId(2), CompletionCookie(2)));
    EXPECT_FALSE(ledger.reserve(SqSeq(2), RequestId(3), CompletionCookie(3)));
    EXPECT_EQ(ledger.liveCount(), 2u);
}

TEST(Gate3CompletionLedgerTest, TerminalReadyTickPrecedesQos)
{
    Gate3CompletionLedger ledger(2);
    const auto first = ledger.reserve(
        SqSeq(0), RequestId(1), CompletionCookie(1));
    const auto second = ledger.reserve(
        SqSeq(1), RequestId(2), CompletionCookie(2));
    ASSERT_TRUE(first);
    ASSERT_TRUE(second);
    ASSERT_TRUE(ledger.markTerminalReady(*first, 20, 15, 1));
    ASSERT_TRUE(ledger.markTerminalReady(*second, 10, 0, 2));
    EXPECT_EQ(ledger.nextTerminal(9), std::nullopt);
    EXPECT_EQ(ledger.nextTerminal(10), second);
    ASSERT_EQ(ledger.assign(*second), CqSeq(0));
    EXPECT_EQ(ledger.nextTerminal(20), first);
}

TEST(Gate3CompletionLedgerTest, SameTickUsesDescendingQosThenIdentity)
{
    Gate3CompletionLedger ledger(4);
    const auto low = ledger.reserve(
        SqSeq(0), RequestId(8), CompletionCookie(8));
    const auto high = ledger.reserve(
        SqSeq(1), RequestId(9), CompletionCookie(9));
    const auto sequenceOnly = ledger.reserve(
        SqSeq(2), RequestId(0), CompletionCookie(2));
    ASSERT_TRUE(low);
    ASSERT_TRUE(high);
    ASSERT_TRUE(sequenceOnly);
    ASSERT_TRUE(ledger.markTerminalReady(*low, 30, 2, 8));
    ASSERT_TRUE(ledger.markTerminalReady(*high, 30, 7, 9));
    ASSERT_TRUE(ledger.markTerminalReady(*sequenceOnly, 30, 0, 0));
    EXPECT_EQ(ledger.nextTerminal(30), high);
    ASSERT_TRUE(ledger.assign(*high));
    EXPECT_EQ(ledger.nextTerminal(30), low);
    ASSERT_TRUE(ledger.assign(*low));
    EXPECT_EQ(ledger.nextTerminal(30), sequenceOnly);
}

TEST(Gate3CompletionLedgerTest, SequenceOnlyErrorReservesWithoutIdentity)
{
    Gate3CompletionLedger ledger(1);
    EXPECT_TRUE(ledger.reserve(SqSeq(0), RequestId(0), CompletionCookie(0)));
    EXPECT_EQ(ledger.liveCount(), 1u);
}

TEST(Gate3CompletionLedgerTest, ReverseMsiResponsesAdvanceOnlyOkayPrefix)
{
    Gate3CompletionLedger ledger(4);
    std::vector<CqObligationId> ids;
    for (uint64_t index = 0; index < 3; ++index) {
        const auto id = ledger.reserve(
            SqSeq(index), RequestId(index + 1), CompletionCookie(index + 1));
        ASSERT_TRUE(id);
        ids.push_back(*id);
        ASSERT_TRUE(ledger.markTerminalReady(
            *id, index, 0, index + 1));
        ASSERT_EQ(ledger.assign(*id), CqSeq(index));
        ASSERT_TRUE(ledger.issueMsi(*id, 32 + index));
    }
    EXPECT_EQ(ledger.issuedSequence(), 3u);
    ASSERT_TRUE(ledger.completeMsi(34, axi::AxiResp::Okay));
    EXPECT_EQ(ledger.notifiedSequence(), 0u);
    ASSERT_TRUE(ledger.completeMsi(33, axi::AxiResp::Okay));
    EXPECT_EQ(ledger.notifiedSequence(), 0u);
    ASSERT_TRUE(ledger.completeMsi(32, axi::AxiResp::Okay));
    EXPECT_EQ(ledger.notifiedSequence(), 3u);
    EXPECT_EQ(ledger.msiRobEntries(), 0u);
}

TEST(Gate3CompletionLedgerTest, ErrorHoleBlocksLaterOkayResponses)
{
    Gate3CompletionLedger ledger(2);
    for (uint64_t index = 0; index < 2; ++index) {
        const auto id = ledger.reserve(
            SqSeq(index), RequestId(index + 1), CompletionCookie(index + 1));
        ASSERT_TRUE(id);
        ASSERT_TRUE(ledger.markTerminalReady(
            *id, index, 0, index + 1));
        ASSERT_TRUE(ledger.assign(*id));
        ASSERT_TRUE(ledger.issueMsi(*id, 40 + index));
    }
    ASSERT_TRUE(ledger.completeMsi(41, axi::AxiResp::Okay));
    ASSERT_TRUE(ledger.completeMsi(40, axi::AxiResp::SlvErr));
    EXPECT_EQ(ledger.notifiedSequence(), 0u);
    EXPECT_EQ(ledger.msiRobEntries(), 2u);
}

TEST(Gate3CompletionLedgerTest, NotificationAndAckAreIndependent)
{
    Gate3CompletionLedger ledger(1);
    const auto id = ledger.reserve(SqSeq(0), RequestId(1), CompletionCookie(1));
    ASSERT_TRUE(id);
    ASSERT_TRUE(ledger.markTerminalReady(*id, 0, 0, 1));
    ASSERT_TRUE(ledger.assign(*id));
    EXPECT_EQ(ledger.acceptAck(1), AckDisposition::Future);
    ASSERT_TRUE(ledger.issueMsi(*id, 52));
    ASSERT_TRUE(ledger.completeMsi(52, axi::AxiResp::Okay));
    EXPECT_EQ(ledger.notifiedSequence(), 1u);
    EXPECT_TRUE(ledger.retirable().empty());
    EXPECT_EQ(ledger.acceptAck(1), AckDisposition::Accepted);
    ASSERT_EQ(ledger.retirable(), std::vector<CqObligationId>{*id});
}

TEST(Gate3CompletionLedgerTest, AckClassificationDoesNotAdvanceWatermark)
{
    Gate3CompletionLedger ledger(1);
    const auto id = ledger.reserve(SqSeq(0), RequestId(1), CompletionCookie(1));
    ASSERT_TRUE(id);
    ASSERT_TRUE(ledger.markTerminalReady(*id, 0, 0, 1));
    ASSERT_TRUE(ledger.assign(*id));
    ASSERT_TRUE(ledger.issueMsi(*id, 52));

    EXPECT_EQ(ledger.classifyAck(1), AckDisposition::Accepted);
    EXPECT_EQ(ledger.ackReceivedSequence(), 0u);
    EXPECT_EQ(ledger.acceptAck(1), AckDisposition::Accepted);
    EXPECT_EQ(ledger.classifyAck(1), AckDisposition::Duplicate);
}

TEST(Gate3CompletionLedgerTest, FatalTransferPreservesUniqueOwnership)
{
    Gate3CompletionLedger ledger(2);
    const auto first = ledger.reserve(
        SqSeq(0), RequestId(1), CompletionCookie(1));
    const auto second = ledger.reserve(
        SqSeq(1), RequestId(2), CompletionCookie(2));
    ASSERT_TRUE(first);
    ASSERT_TRUE(second);
    ASSERT_TRUE(ledger.markTerminalReady(*first, 0, 0, 1));
    ASSERT_TRUE(ledger.assign(*first));
    ASSERT_TRUE(ledger.issueMsi(*first, 60));
    ASSERT_TRUE(ledger.completeMsi(60, axi::AxiResp::Okay));
    EXPECT_EQ(ledger.acceptAck(1), AckDisposition::Accepted);
    ASSERT_TRUE(ledger.retire(*first));
    ledger.transferLiveToFatal();
    EXPECT_EQ(ledger.liveCount(), 0u);
    EXPECT_EQ(ledger.retiredCount(), 1u);
    EXPECT_EQ(ledger.fatalCount(), 1u);
    const auto fatal = ledger.fatalObligations();
    ASSERT_EQ(fatal.size(), 1u);
    EXPECT_EQ(fatal[0].id, second);
}

}
}
}

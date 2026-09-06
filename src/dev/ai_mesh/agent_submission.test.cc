#include <gtest/gtest.h>

#include "dev/ai_mesh/agent_submission_ledger.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(AgentSubmissionTest, CommitMovesReusablePrefix)
{
    SubmissionLedger ledger(2, 4);
    const auto slot = ledger.reserve(RequestId(11));
    ASSERT_TRUE(slot.has_value());
    EXPECT_EQ(slot->value(), 0u);
    EXPECT_EQ(ledger.tentativeProducer().value(), 1u);
    EXPECT_EQ(ledger.committedProducer().value(), 0u);
    EXPECT_EQ(ledger.available(), 1u);
    ASSERT_TRUE(ledger.observeHead(SqSeq(1)));
    EXPECT_EQ(ledger.observedHead().value(), 1u);
    EXPECT_EQ(ledger.reusableHead().value(), 0u);
    EXPECT_EQ(ledger.resolve(axi::AxiResp::Okay), PublicationResolution::Committed);
    EXPECT_EQ(ledger.committedProducer().value(), 1u);
    EXPECT_EQ(ledger.reusableHead().value(), 1u);
}

TEST(AgentSubmissionTest, SafeRollbackKeepsRequestIdentityRetired)
{
    SubmissionLedger ledger(1, 4);
    ASSERT_TRUE(ledger.reserve(RequestId(21)).has_value());
    EXPECT_EQ(
        ledger.resolve(axi::AxiResp::SlvErr, true),
        PublicationResolution::RolledBack);
    EXPECT_EQ(ledger.committedProducer().value(), 0u);
    EXPECT_EQ(ledger.tentativeProducer().value(), 0u);
    EXPECT_TRUE(ledger.wasIssued(RequestId(21)));
    ASSERT_TRUE(ledger.reserve(RequestId(22)).has_value());
    EXPECT_EQ(ledger.pending()->base.value(), 0u);
}

TEST(AgentSubmissionTest, EvidenceMakesErrorFatal)
{
    SubmissionLedger ledger(2, 4);
    ASSERT_TRUE(ledger.reserve(RequestId(31)).has_value());
    ASSERT_TRUE(ledger.observeHead(SqSeq(1)));
    EXPECT_EQ(
        ledger.resolve(axi::AxiResp::SlvErr, true),
        PublicationResolution::Fatal);
    EXPECT_TRUE(ledger.hasPending());
    EXPECT_EQ(ledger.tentativeProducer().value(), 1u);
}

TEST(AgentSubmissionTest, CqEvidenceHasSamePriorityAsHeadEvidence)
{
    SubmissionLedger ledger(2, 4);
    ASSERT_TRUE(ledger.reserve(RequestId(41)).has_value());
    ledger.observeCompletion(SqSeq(0));
    EXPECT_EQ(
        ledger.resolve(axi::AxiResp::SlvErr, true),
        PublicationResolution::Fatal);
}

TEST(AgentSubmissionTest, FullPublicationBackpressures)
{
    SubmissionLedger ledger(1, 4);
    ASSERT_TRUE(ledger.reserve(RequestId(51)).has_value());
    EXPECT_EQ(ledger.available(), 0u);
    EXPECT_FALSE(ledger.reserve(RequestId(52)).has_value());
}

TEST(AgentSubmissionTest, DuplicateRequestIdIsRejected)
{
    SubmissionLedger ledger(2, 4);
    ASSERT_TRUE(ledger.reserve(RequestId(61)).has_value());
    EXPECT_EQ(ledger.resolve(axi::AxiResp::Okay), PublicationResolution::Committed);
    EXPECT_FALSE(ledger.reserve(RequestId(61)).has_value());
}

TEST(AgentSubmissionTest, BatchPublicationUsesOneCumulativeTail)
{
    SubmissionLedger ledger(4, 8);
    const auto base = ledger.reserveBatch(
        {RequestId(1), RequestId(2), RequestId(3)});
    ASSERT_EQ(base, SqSeq(0));
    ASSERT_TRUE(ledger.pending());
    EXPECT_EQ(ledger.pending()->tail, SqSeq(3));
    EXPECT_EQ(ledger.tentativeProducer(), SqSeq(3));
    EXPECT_EQ(ledger.available(), 1u);
    EXPECT_EQ(
        ledger.resolve(axi::AxiResp::Okay), PublicationResolution::Committed);
    EXPECT_EQ(ledger.committedProducer(), SqSeq(3));
}

TEST(AgentSubmissionTest, BatchReservationIsAtomic)
{
    SubmissionLedger ledger(2, 4);
    EXPECT_FALSE(ledger.reserveBatch(
        {RequestId(1), RequestId(1)}));
    EXPECT_EQ(ledger.tentativeProducer(), SqSeq(0));
    EXPECT_FALSE(ledger.hasPending());
    EXPECT_FALSE(ledger.reserveBatch(
        {RequestId(1), RequestId(2), RequestId(3)}));
    EXPECT_EQ(ledger.tentativeProducer(), SqSeq(0));
}

}
}
}



namespace gem5 {
namespace ai_mesh {
namespace {
TEST(AgentSubmissionTest, CommittedButNotReusableRejectsReserve)
{
    SubmissionLedger ledger(1, 4);
    ASSERT_TRUE(ledger.reserve(RequestId(41)).has_value());
    // doorbell resolves OKAY: slot becomes committed but the head update
    // has not been observed, so the slot is not reusable yet.
    EXPECT_EQ(
        ledger.resolve(axi::AxiResp::Okay), PublicationResolution::Committed);
    EXPECT_EQ(ledger.available(), 0u);
    EXPECT_FALSE(ledger.reserve(RequestId(42)).has_value());
    ASSERT_TRUE(ledger.observeHead(SqSeq(1)));
    EXPECT_EQ(ledger.available(), 1u);
    EXPECT_TRUE(ledger.reserve(RequestId(42)).has_value());
}

} // namespace
} // namespace ai_mesh
} // namespace gem5

#include <gtest/gtest.h>

#include <cstdint>
#include <algorithm>
#include <optional>
#include <vector>

#include "dev/ai_mesh/kv_token_ledger.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"

namespace
{

using namespace gem5::ai_mesh;

KvGeometry testGeometry()
{
    KvGeometry geo;
    geo.region_base = 0x100000;
    geo.slot_bytes = 4096;
    geo.slot_alignment = 4096;
    geo.max_sessions = 2;
    geo.bytes_per_token = 16;
    return geo;
}

KvCapacity testCapacity()
{
    KvCapacity cap;
    cap.record_entries = 4;
    cap.tombstone_entries = 2;
    cap.admission_wait_entries = 2;
    cap.release_waiter_entries = 2;
    return cap;
}

KvTokenLedger ledger(uint32_t base_tokens = 0, uint32_t append_tokens = 8)
{
    return KvTokenLedger(1, 11, 11, 1, base_tokens, append_tokens, 16);
}

}

TEST(KvTokenLedger, CompletesTheWholeAppendInOrder)
{
    KvTokenLedger tokens = ledger();
    ASSERT_TRUE(tokens.acceptDescriptor(7, 0, 8));
    EXPECT_EQ(tokens.outstandingDescriptors(), 1u);
    EXPECT_EQ(tokens.acceptDelta(), 1u);
    EXPECT_FALSE(tokens.descriptorComplete(7));
    tokens.noteBurst(7, 0, 64, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 4u);
    EXPECT_FALSE(tokens.descriptorComplete(7));
    tokens.noteBurst(7, 64, 64, true, std::nullopt);
    tokens.noteDescriptorTerminal(7);
    EXPECT_TRUE(tokens.descriptorComplete(7));
    EXPECT_EQ(tokens.completePrefix(), 8u);
    EXPECT_EQ(tokens.terminalDelta(), 1u);
    EXPECT_EQ(tokens.outstandingDescriptors(), 0u);
    const KvAppendTerminal terminal = tokens.appendTerminal();
    ASSERT_EQ(terminal.tokens_ok.size(), 8u);
    EXPECT_TRUE(std::all_of(terminal.tokens_ok.begin(),
                            terminal.tokens_ok.end(),
                            [](bool ok) { return ok; }));
    EXPECT_FALSE(tokens.hasFault());
}

TEST(KvTokenLedger, CommitsBurstsInAnyOrder)
{
    KvTokenLedger tokens = ledger();
    ASSERT_TRUE(tokens.acceptDescriptor(3, 0, 8));
    tokens.noteBurst(3, 64, 64, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 0u);
    tokens.noteBurst(3, 0, 64, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 8u);
    EXPECT_TRUE(tokens.descriptorComplete(3));
}

TEST(KvTokenLedger, PartialTokenStopsThePrefixAndKeepsTheFault)
{
    KvTokenLedger tokens = ledger();
    ASSERT_TRUE(tokens.acceptDescriptor(4, 0, 8));
    tokens.noteBurst(4, 0, 48, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 3u);
    KvErrorCandidate candidate;
    candidate.tick = 55;
    candidate.code = 9;
    candidate.source.domain = 4;
    candidate.source.object_kind = 9;
    tokens.noteBurst(4, 48, 16, false, candidate);
    EXPECT_EQ(tokens.completePrefix(), 3u);
    EXPECT_TRUE(tokens.hasFault());
    ASSERT_EQ(tokens.errors().size(), 1u);
    EXPECT_EQ(tokens.errors()[0].tick, 55u);
    const KvAppendTerminal terminal = tokens.appendTerminal();
    ASSERT_EQ(terminal.tokens_ok.size(), 8u);
    EXPECT_TRUE(terminal.tokens_ok[0]);
    EXPECT_TRUE(terminal.tokens_ok[2]);
    EXPECT_FALSE(terminal.tokens_ok[3]);
    EXPECT_FALSE(terminal.tokens_ok[7]);
}

TEST(KvTokenLedger, RejectsDuplicateDescriptorIdentity)
{
    KvTokenLedger tokens = ledger();
    ASSERT_TRUE(tokens.acceptDescriptor(7, 0, 8));
    EXPECT_FALSE(tokens.acceptDescriptor(7, 0, 8));
    EXPECT_EQ(tokens.outstandingDescriptors(), 1u);
    EXPECT_EQ(tokens.acceptDelta(), 1u);
    EXPECT_FALSE(tokens.acceptDescriptor(8, 4, 8));
}

TEST(KvTokenLedger, JoinsTwoDescriptorsIntoOnePrefix)
{
    KvTokenLedger tokens = ledger(8, 2);
    ASSERT_TRUE(tokens.acceptDescriptor(1, 0, 1));
    ASSERT_TRUE(tokens.acceptDescriptor(2, 1, 1));
    tokens.noteBurst(1, 0, 16, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 1u);
    tokens.noteBurst(2, 16, 16, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 2u);
    EXPECT_EQ(tokens.acceptDelta(), 2u);
    tokens.noteDescriptorTerminal(1);
    tokens.noteDescriptorTerminal(2);
    EXPECT_EQ(tokens.terminalDelta(), 2u);
    EXPECT_EQ(tokens.appendTerminal().tokens_ok.size(), 2u);
}

TEST(KvTokenLedger, EmptyAppendHasNoWork)
{
    KvTokenLedger tokens = ledger(10, 0);
    EXPECT_EQ(tokens.completePrefix(), 0u);
    EXPECT_EQ(tokens.outstandingDescriptors(), 0u);
    EXPECT_EQ(tokens.acceptDelta(), 0u);
    EXPECT_TRUE(tokens.appendTerminal().tokens_ok.empty());
    EXPECT_FALSE(tokens.acceptDescriptor(1, 0, 1));
}


TEST(KvTokenLedger, AgreesWithTheKvManagerAppendCommit)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs admission;
    admission.tick = 10;
    KvAdmissionIntent intent;
    intent.request_id = 1;
    intent.session_id = 11;
    intent.kv_handle = 11;
    intent.generation = 1;
    intent.contract_digest.fill(0x5c);
    admission.admissions.push_back(intent);
    const KvEdgeResult admitted = manager.commitEdge(admission);
    ASSERT_FALSE(admitted.fatal);
    ASSERT_EQ(admitted.promotions.at(1), KvPromotionOutcome::Pinned);

    KvTokenLedger tokens(1, 11, 11, 1, 0, 8, 16);
    ASSERT_TRUE(tokens.acceptDescriptor(5, 0, 8));
    tokens.noteBurst(5, 0, 64, true, std::nullopt);
    tokens.noteBurst(5, 64, 40, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 6u);
    EXPECT_FALSE(tokens.descriptorComplete(5));

    KvEdgeInputs arm;
    arm.tick = 20;
    arm.append_arms.push_back(KvAppendArm{1, 0, 8});
    ASSERT_FALSE(manager.commitEdge(arm).fatal);
    KvEdgeInputs terminal;
    terminal.tick = 30;
    terminal.append_terminals.push_back(tokens.appendTerminal());
    const KvEdgeResult applied = manager.commitEdge(terminal);
    ASSERT_FALSE(applied.fatal);
    const KvRecord *record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens,
              tokens.baseTokenCount() + tokens.completePrefix());
    EXPECT_EQ(record->cached_tokens, 6u);
    EXPECT_EQ(record->valid_bytes, uint64_t(record->cached_tokens) * 16);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_TRUE(manager.validate().empty());
}

TEST(KvTokenLedger, FullPrefixCommitMatchesTheManager)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs admission;
    admission.tick = 10;
    KvAdmissionIntent intent;
    intent.request_id = 1;
    intent.session_id = 11;
    intent.kv_handle = 11;
    intent.generation = 1;
    intent.contract_digest.fill(0x5c);
    admission.admissions.push_back(intent);
    ASSERT_FALSE(manager.commitEdge(admission).fatal);

    KvTokenLedger tokens(1, 11, 11, 1, 0, 8, 16);
    ASSERT_TRUE(tokens.acceptDescriptor(5, 0, 8));
    tokens.noteBurst(5, 64, 64, true, std::nullopt);
    tokens.noteBurst(5, 0, 64, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 8u);
    tokens.noteDescriptorTerminal(5);
    EXPECT_TRUE(tokens.descriptorComplete(5));
    EXPECT_EQ(tokens.terminalDelta(), 1u);

    KvEdgeInputs arm;
    arm.tick = 20;
    arm.append_arms.push_back(KvAppendArm{1, 0, 8});
    ASSERT_FALSE(manager.commitEdge(arm).fatal);
    KvEdgeInputs terminal;
    terminal.tick = 30;
    terminal.append_terminals.push_back(tokens.appendTerminal());
    const KvEdgeResult applied = manager.commitEdge(terminal);
    ASSERT_FALSE(applied.fatal);
    const KvRecord *record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 8u);
    EXPECT_EQ(record->valid_bytes, 128u);
    EXPECT_EQ(record->state, KvState::Resident);
    EXPECT_TRUE(manager.validate().empty());
}

TEST(KvTokenLedger, RepeatedHalfIntervalNeverCompletesTheToken)
{
    KvTokenLedger tokens = ledger();
    ASSERT_TRUE(tokens.acceptDescriptor(7, 0, 8));
    tokens.noteBurst(7, 0, 8, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 0u);
    tokens.noteBurst(7, 0, 8, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 0u);
    EXPECT_FALSE(tokens.descriptorComplete(7));
    tokens.noteBurst(7, 8, 8, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 1u);
}

TEST(KvTokenLedger, OutOfOrderBytesCompleteTheTokenOnce)
{
    KvTokenLedger tokens = ledger();
    ASSERT_TRUE(tokens.acceptDescriptor(7, 0, 8));
    tokens.noteBurst(7, 8, 8, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 0u);
    tokens.noteBurst(7, 0, 8, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 1u);
    tokens.noteBurst(7, 0, 16, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 1u);
}

TEST(KvTokenLedger, FaultAfterAFullTokenKeepsTheCommittedPrefix)
{
    KvTokenLedger tokens = ledger();
    ASSERT_TRUE(tokens.acceptDescriptor(7, 0, 8));
    tokens.noteBurst(7, 0, 16, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 1u);
    KvErrorCandidate candidate;
    candidate.tick = 300;
    candidate.code = 9;
    tokens.noteBurst(7, 16, 16, false, candidate);
    EXPECT_EQ(tokens.completePrefix(), 1u);
    EXPECT_TRUE(tokens.hasFault());
    const KvAppendTerminal terminal = tokens.appendTerminal();
    ASSERT_EQ(terminal.tokens_ok.size(), 8u);
    EXPECT_TRUE(terminal.tokens_ok[0]);
    EXPECT_FALSE(terminal.tokens_ok[1]);
    EXPECT_FALSE(tokens.descriptorComplete(7));
}

TEST(KvTokenLedger, EveryRowOfATwoRowDescriptorCoversItsBytes)
{
    KvTokenLedger tokens = ledger();
    ASSERT_TRUE(tokens.acceptDescriptor(7, 0, 8));
    tokens.noteBurst(7, 0, 64, true, std::nullopt);
    tokens.noteBurst(7, 64, 64, true, std::nullopt);
    EXPECT_EQ(tokens.completePrefix(), 8u);
    EXPECT_TRUE(tokens.descriptorComplete(7));
}

TEST(KvTokenLedger, IntervalCrossingAFourKibibyteWindowIsExact)
{
    const uint32_t tokens = 256;
    KvTokenLedger wide(1, 11, 11, 1, 0, tokens, 16);
    ASSERT_TRUE(wide.acceptDescriptor(9, 0, tokens));
    const uint64_t span = uint64_t(tokens) * 16;
    wide.noteBurst(9, 0, span / 2, true, std::nullopt);
    EXPECT_EQ(wide.completePrefix(), tokens / 2);
    wide.noteBurst(9, span / 2, span - span / 2, true, std::nullopt);
    wide.noteDescriptorTerminal(9);
    EXPECT_EQ(wide.completePrefix(), tokens);
    EXPECT_TRUE(wide.descriptorComplete(9));
    EXPECT_EQ(wide.outstandingDescriptors(), 0u);
}

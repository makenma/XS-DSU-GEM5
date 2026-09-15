#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"
#include "dev/ai_mesh/mesh_kv_manager_test_support.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

using namespace mesh_kv_test;

TEST(MeshKvManager, TwoWaitersCompeteForOneSlot)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 2, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    ASSERT_EQ(manager.commitEdge(first).promotions.at(1),
              KvPromotionOutcome::Pinned);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    const KvEdgeResult result = manager.commitEdge(second);
    EXPECT_EQ(result.admissions.at(2), KvAdmissionOutcome::Claimed);
    EXPECT_EQ(result.promotions.at(2), KvPromotionOutcome::WaitingSlot);
    ASSERT_NE(manager.findRecord(11, 11), nullptr);
    EXPECT_EQ(*manager.findRecord(11, 11)->slot_id, 0u);
    ASSERT_NE(manager.findRecord(22, 22), nullptr);
    EXPECT_FALSE(manager.findRecord(22, 22)->slot_id);
    KvEdgeInputs release_edge;
    release_edge.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    manager.commitEdge(release_edge);
    KvEdgeInputs retry;
    retry.admissions.push_back(admit(2, 22, 1, 0, 0, 99));
    const KvEdgeResult promoted = manager.commitEdge(retry);
    EXPECT_EQ(promoted.promotions.at(2), KvPromotionOutcome::Pinned);
    EXPECT_EQ(*manager.findRecord(22, 22)->slot_id, 0u);
    EXPECT_EQ(manager.nextKvUseEpoch(), 3u);
    EXPECT_TRUE(manager.validate().empty());
}

TEST(MeshKvManager, CapacityBoundsBackpressureAndFailClosed)
{
    MeshKvManager manager(testGeometry(2), testCapacity(1, 1, 1, 1));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    EXPECT_EQ(manager.commitEdge(first).admissions.at(1),
              KvAdmissionOutcome::Claimed);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    const KvEdgeResult queued = manager.commitEdge(second);
    EXPECT_EQ(queued.admissions.at(2), KvAdmissionOutcome::Waiting);
    EXPECT_EQ(queued.waits.at(2), KvWaitReason::RecordTableFull);
    EXPECT_EQ(manager.recordCount(), 1u);
    EXPECT_EQ(manager.findRecord(22, 22), nullptr);
    EXPECT_TRUE(manager.validate().empty());
    manager.commitEdge(KvEdgeInputs{});
    EXPECT_EQ(manager.recordCount(), 1u);
}

TEST(MeshKvManager, ReleaseWaiterCapacityReturnsBusy)
{
    MeshKvManager manager(testGeometry(2), testCapacity(4, 2, 4, 1));
    KvEdgeInputs admissions;
    admissions.admissions.push_back(admit(1, 11));
    admissions.admissions.push_back(admit(2, 22));
    manager.commitEdge(admissions);
    KvEdgeInputs release_edge;
    release_edge.releases.push_back(release(9, 11));
    EXPECT_TRUE(manager.commitEdge(release_edge).releases.empty());
    KvEdgeInputs busy;
    busy.releases.push_back(release(8, 22));
    EXPECT_EQ(manager.commitEdge(busy).releases.at(8), KvReleaseOutcome::Busy);
    EXPECT_TRUE(manager.releasePending(11, 11));
    EXPECT_FALSE(manager.releasePending(22, 22));
}

// ------------------------------------------------------- cancel and state

TEST(MeshKvManager, CancelBeforeClaimLeavesNoSessionEffect)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    edge.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Cancelled, std::nullopt, false});
    const KvEdgeResult result = manager.commitEdge(edge);
    EXPECT_EQ(result.ownerless_cancels.size(), 1u);
    EXPECT_TRUE(result.admissions.empty());
    EXPECT_EQ(manager.recordCount(), 0u);
    EXPECT_TRUE(manager.validate().empty());
}

TEST(MeshKvManager, CancelAfterClaimRestoresThePriorSnapshot)
{
    MeshKvManager manager(testGeometry(1), testCapacity(3, 2, 4, 2));
    std::string reason;
    const KvPersistentState initial = persistentState(
        {makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3),
         makeRecord(11, 1, KvState::Resident, 0, 8, 4, 2)},
        {std::make_pair<uint64_t, uint64_t>(11, 11)}, 4);
    ASSERT_TRUE(manager.loadPersistent(initial, reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    const KvEdgeResult result = manager.commitEdge(edge);
    EXPECT_EQ(result.admissions.at(1), KvAdmissionOutcome::Claimed);
    EXPECT_EQ(result.promotions.at(1), KvPromotionOutcome::WaitingSlot);
    KvEdgeInputs cancel;
    cancel.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Cancelled, std::nullopt, false});
    const KvEdgeResult cancelled = manager.commitEdge(cancel);
    ASSERT_NE(manager.findRecord(7, 7), nullptr);
    EXPECT_EQ(manager.findRecord(7, 7)->state, KvState::Evicted);
    EXPECT_EQ(manager.findRecord(7, 7)->view_epoch, 7u);
    EXPECT_EQ(cancelled.terminals.at(1).source,
              KvSnapshotSource::AdmissionClaim);
    EXPECT_TRUE(manager.validate().empty());
}

TEST(MeshKvManager, CancelAfterClaimToPinRollsBackOnce)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    const KvEdgeResult pinned = manager.commitEdge(edge);
    ASSERT_EQ(pinned.handoffs.count(1), 1u);
    KvEdgeInputs cancel;
    cancel.owner_terminals.push_back(KvOwnerTerminal{
        1, KvTerminalStatus::Cancelled, pinned.handoffs.at(1), false});
    const KvEdgeResult rolled = manager.commitEdge(cancel);
    EXPECT_EQ(rolled.rollbacks.at(1), KvRollbackKind::InitialErrorRecord);
    const KvRecord *record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_FALSE(record->slot_id);
    EXPECT_EQ(record->view_epoch, 0u);
    EXPECT_TRUE(manager.validate().empty());
    KvEdgeInputs replay;
    replay.owner_terminals.push_back(KvOwnerTerminal{
        1, KvTerminalStatus::Cancelled, pinned.handoffs.at(1), false});
    EXPECT_TRUE(manager.commitEdge(replay).fatal.has_value());
}

TEST(MeshKvManager, ReleasePendingDoesNotBlockTheExistingOwner)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11, 1, 0, 0, 0, 0, 0, 6));
    manager.commitEdge(edge);
    manager.commitEdge(KvEdgeInputs{});
    KvEdgeInputs release_edge;
    release_edge.releases.push_back(release(9, 11));
    EXPECT_TRUE(manager.commitEdge(release_edge).releases.empty());
    KvEdgeInputs blocked;
    blocked.admissions.push_back(
        admit(2, 11, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    const KvEdgeResult waiting = manager.commitEdge(blocked);
    EXPECT_EQ(waiting.admissions.at(2), KvAdmissionOutcome::Waiting);
    EXPECT_EQ(waiting.waits.at(2), KvWaitReason::ReleasePending);
    EXPECT_TRUE(manager.hasClaim(2) == false);
    KvEdgeInputs append;
    append.append_arms.push_back(KvAppendArm{1, 0, 6});
    EXPECT_FALSE(manager.commitEdge(append).fatal.has_value());
    KvEdgeInputs terminal;
    terminal.append_terminals.push_back(
        KvAppendTerminal{1, std::vector<bool>(6, true), std::nullopt});
    manager.commitEdge(terminal);
    KvEdgeInputs done;
    done.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    const KvEdgeResult finished = manager.commitEdge(done);
    EXPECT_EQ(finished.releases.at(9), KvReleaseOutcome::Success);
    EXPECT_EQ(manager.findRecord(11, 11), nullptr);
    EXPECT_EQ(manager.tombstoneCount(), 1u);
    EXPECT_TRUE(manager.validate().empty());
}

// ---------------------------------------------------------- rollback

TEST(MeshKvManager, ReprefillPrestartAbortRestoresTheEvictedSnapshot)
{
    MeshKvManager manager(testGeometry(1), testCapacity());
    std::string reason;
    const KvRecord prior = makeRecord(7, 1, KvState::Evicted, std::nullopt, 0,
                                      7, 3);
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {prior}, {std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 7, 1,
                                    agent_abi::kSqFlagsALLOW_REPREFILL, 0, 0,
                                    0, 0, 8));
    const KvEdgeResult pinned = manager.commitEdge(edge);
    ASSERT_NE(manager.findRecord(7, 7), nullptr);
    EXPECT_EQ(manager.findRecord(7, 7)->view_epoch, 8u);
    ASSERT_EQ(pinned.handoffs.count(1), 1u);
    const KvLiveState prestart = manager.saveLive();
    ASSERT_EQ(prestart.pins.size(), 1u);
    ASSERT_TRUE(prestart.pins[0].payload.has_value());
    EXPECT_EQ(prestart.pins[0].payload->intent, KvPath::Reprefill);
    KvEdgeInputs abort;
    abort.owner_terminals.push_back(KvOwnerTerminal{
        1, KvTerminalStatus::Cancelled, pinned.handoffs.at(1), false});
    const KvEdgeResult rolled = manager.commitEdge(abort);
    EXPECT_EQ(rolled.rollbacks.at(1), KvRollbackKind::RestorePrior);
    const KvRecord *record = manager.findRecord(7, 7);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->state, KvState::Evicted);
    EXPECT_EQ(record->view_epoch, 7u);
    EXPECT_EQ(record->last_use_epoch, 3u);
    EXPECT_FALSE(record->slot_id);
    EXPECT_TRUE(manager.validate().empty());
}

TEST(MeshKvManager, LaterFaultAfterCoreStartKeepsTheNewPrefix)
{
    MeshKvManager manager(testGeometry(1), testCapacity());
    std::string reason;
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3)},
        {std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 7, 1,
                                    agent_abi::kSqFlagsALLOW_REPREFILL, 0, 0,
                                    0, 0, 9));
    manager.commitEdge(edge);
    KvEdgeInputs start;
    start.core_starts.push_back(1);
    manager.commitEdge(start);
    KvEdgeInputs append;
    append.append_arms.push_back(KvAppendArm{1, 0, 9});
    manager.commitEdge(append);
    KvEdgeInputs terminal;
    terminal.append_terminals.push_back(
        KvAppendTerminal{1, std::vector<bool>(9, true), std::nullopt});
    manager.commitEdge(terminal);
    KvEdgeInputs fault;
    fault.later_faults.push_back(KvLaterFault{1, candidate(5)});
    const KvEdgeResult result = manager.commitEdge(fault);
    const KvRecord *record = manager.findRecord(7, 7);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_EQ(record->cached_tokens, 9u);
    EXPECT_EQ(record->valid_bytes, 9u * kBytesPerToken);
    EXPECT_EQ(record->view_epoch, 9u);
    EXPECT_EQ(result.terminals.at(1).status, KvTerminalStatus::Error);
    EXPECT_TRUE(result.rollbacks.empty());
}

TEST(MeshKvManager, TerminalSnapshotSurvivesEvictionAndRelease)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 4, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11, 1, 0, 0, 0, 0, 0, 8));
    manager.commitEdge(first);
    manager.commitEdge(KvEdgeInputs{});
    KvEdgeInputs append;
    append.append_arms.push_back(KvAppendArm{1, 0, 8});
    manager.commitEdge(append);
    KvEdgeInputs terminal;
    terminal.append_terminals.push_back(
        KvAppendTerminal{1, std::vector<bool>(8, true), std::nullopt});
    manager.commitEdge(terminal);
    KvEdgeInputs done;
    done.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    const KvEdgeResult finished = manager.commitEdge(done);
    const KvTerminalSnapshot snapshot = finished.terminals.at(1);
    KvEdgeInputs evict;
    evict.admissions.push_back(admit(2, 22));
    const KvEdgeResult evicted = manager.commitEdge(evict);
    ASSERT_EQ(evicted.evictions_started.size(), 1u);
    EXPECT_EQ(manager.findRecord(11, 11)->state, KvState::Evicting);
    KvEdgeInputs release_edge;
    release_edge.releases.push_back(release(9, 11));
    manager.commitEdge(release_edge);
    EXPECT_EQ(manager.findRecord(11, 11), nullptr);
    EXPECT_EQ(finished.terminals.at(1).cached_tokens,
              snapshot.cached_tokens);
    EXPECT_EQ(snapshot.valid_bytes, 8u * kBytesPerToken);
}

// ---------------------------------------------------------- eviction

TEST(MeshKvManager, EvictionExcludesEveryLiveOwner)
{
    auto prepared = [](MeshKvManager &manager) {
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(1, 11));
        manager.commitEdge(edge);
    };
    {
        MeshKvManager manager(testGeometry(1), testCapacity(3, 2, 4, 2));
        prepared(manager);
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(2, 22));
        const KvEdgeResult result = manager.commitEdge(edge);
        EXPECT_TRUE(result.evictions_started.empty());
        EXPECT_EQ(result.promotions.at(2), KvPromotionOutcome::WaitingSlot);
    }
    {
        MeshKvManager manager(testGeometry(1), testCapacity(3, 2, 4, 2));
        prepared(manager);
        KvEdgeInputs release_edge;
        release_edge.releases.push_back(release(9, 11));
        manager.commitEdge(release_edge);
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(2, 22));
        EXPECT_TRUE(manager.commitEdge(edge).evictions_started.empty());
    }
    {
        MeshKvManager manager(testGeometry(1), testCapacity(3, 2, 4, 2));
        prepared(manager);
        KvEdgeInputs dma;
        dma.dma_accepts.push_back(KvDmaCount{1, 1});
        manager.commitEdge(dma);
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(2, 22));
        EXPECT_TRUE(manager.commitEdge(edge).evictions_started.empty());
    }
    {
        MeshKvManager manager(testGeometry(1), testCapacity(3, 2, 4, 2));
        prepared(manager);
        KvEdgeInputs append;
        append.append_arms.push_back(KvAppendArm{1, 0, 3});
        manager.commitEdge(append);
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(2, 22));
        EXPECT_TRUE(manager.commitEdge(edge).evictions_started.empty());
    }
}

TEST(MeshKvManager, EvictionPrefersErrorThenEpochThenTuple)
{
    {
        MeshKvManager manager(testGeometry(2), testCapacity(3, 2, 4, 2));
        std::string reason;
        ASSERT_TRUE(manager.loadPersistent(persistentState(
            {makeRecord(11, 1, KvState::Error, 0, 4, 2, 9),
             makeRecord(22, 1, KvState::Resident, 1, 8, 5, 1)},
            {std::make_pair<uint64_t, uint64_t>(11, 11),
             std::make_pair<uint64_t, uint64_t>(22, 22)}, 10), reason))
            << reason;
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(1, 33));
        const KvEdgeResult result = manager.commitEdge(edge);
        ASSERT_EQ(result.evictions_started.size(), 1u);
        EXPECT_EQ(result.evictions_started[0].session_id, 11u);
    }
    {
        MeshKvManager manager(testGeometry(2), testCapacity(3, 2, 4, 2));
        std::string reason;
        ASSERT_TRUE(manager.loadPersistent(persistentState(
            {makeRecord(11, 1, KvState::Resident, 0, 8, 5, 1),
             makeRecord(22, 1, KvState::Resident, 1, 8, 5, 1)},
            {std::make_pair<uint64_t, uint64_t>(11, 11),
             std::make_pair<uint64_t, uint64_t>(22, 22)}, 2), reason))
            << reason;
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(1, 33));
        const KvEdgeResult result = manager.commitEdge(edge);
        ASSERT_EQ(result.evictions_started.size(), 1u);
        EXPECT_EQ(result.evictions_started[0].session_id, 11u);
    }
}

TEST(MeshKvManager, ErrorEvictionPreservesTheDiagnosticPrefix)
{
    MeshKvManager manager(testGeometry(1), testCapacity(3, 2, 4, 2));
    std::string reason;
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {makeRecord(11, 1, KvState::Error, 0, 4, 2, 9)},
        {std::make_pair<uint64_t, uint64_t>(11, 11)}, 10), reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 22));
    manager.commitEdge(edge);
    manager.commitEdge(KvEdgeInputs{});
    const KvRecord *record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_FALSE(record->slot_id);
    EXPECT_EQ(record->diagnostic_prefix_tokens, 4u);
    EXPECT_EQ(record->diagnostic_prefix_bytes, 4u * kBytesPerToken);
    EXPECT_TRUE(record->diagnostic_prefix_digest.has_value());
    EXPECT_TRUE(manager.validate().empty());
}

// ------------------------------------------------------------ faults

TEST(MeshKvManager, OwnerProtocolViolationsFailClosed)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    manager.commitEdge(edge);
    KvEdgeInputs duplicate;
    duplicate.admissions.push_back(admit(1, 11));
    const KvEdgeResult repeated = manager.commitEdge(duplicate);
    ASSERT_TRUE(repeated.fatal);
    EXPECT_EQ(*repeated.fatal, "duplicate KV acquire for a pinned request");
    EXPECT_TRUE(manager.validate().empty());
    MeshKvManager other(testGeometry(), testCapacity());
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    other.commitEdge(first);
    KvEdgeInputs unknown;
    unknown.owner_terminals.push_back(
        KvOwnerTerminal{5, KvTerminalStatus::Success, std::nullopt, true});
    EXPECT_TRUE(other.commitEdge(unknown).fatal.has_value());
}

TEST(MeshKvManager, DoubleReleaseAndDmaUnderflowAreFatal)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    manager.commitEdge(edge);
    KvEdgeInputs done;
    done.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    EXPECT_FALSE(manager.commitEdge(done).fatal.has_value());
    KvEdgeInputs again;
    again.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    EXPECT_TRUE(manager.commitEdge(again).fatal.has_value());

    MeshKvManager dma(testGeometry(), testCapacity());
    KvEdgeInputs accept;
    accept.admissions.push_back(admit(1, 11));
    dma.commitEdge(accept);
    KvEdgeInputs underflow;
    underflow.dma_terminals.push_back(KvDmaCount{1, 1});
    EXPECT_TRUE(dma.commitEdge(underflow).fatal.has_value());

    MeshKvManager live(testGeometry(), testCapacity());
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    live.commitEdge(first);
    KvEdgeInputs busy;
    busy.dma_accepts.push_back(KvDmaCount{1, 1});
    live.commitEdge(busy);
    KvEdgeInputs late;
    late.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    EXPECT_TRUE(live.commitEdge(late).fatal.has_value());
}

// ----------------------------------------------------------- admission

TEST(MeshKvManager, AdmissionFailuresArePreAdmission)
{
    MeshKvManager manager(testGeometry(1), testCapacity());
    std::string reason;
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Resident, 0, 8, 4, 2)},
        {std::make_pair<uint64_t, uint64_t>(7, 7)}, 3), reason)) << reason;
    KvEdgeInputs mismatch;
    mismatch.admissions.push_back(admit(
        1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL, 0, 0, 0, 8, 0,
        contractDigest(kBytesPerToken + 1)));
    EXPECT_EQ(manager.commitEdge(mismatch).admissions.at(1),
              KvAdmissionOutcome::ContractMismatch);
    EXPECT_EQ(manager.findRecord(7, 7)->pin_count, 0u);
    EXPECT_EQ(manager.findRecord(7, 7)->admission_claim_count, 0u);
    KvEdgeInputs stale;
    stale.admissions.push_back(
        admit(2, 7, 2, agent_abi::kSqFlagsALLOW_REPREFILL, 0, 0, 0, 8));
    EXPECT_EQ(manager.commitEdge(stale).admissions.at(2),
              KvAdmissionOutcome::StaleGeneration);
    KvEdgeInputs missing;
    missing.admissions.push_back(
        admit(3, 99, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    EXPECT_EQ(manager.commitEdge(missing).admissions.at(3),
              KvAdmissionOutcome::SessionNotFound);
    EXPECT_EQ(manager.recordCount(), 1u);
}

TEST(MeshKvManager, PostAdmissionPolicyErrorsTerminalizeTheClaim)
{
    MeshKvManager manager(testGeometry(1), testCapacity());
    std::string reason;
    const KvRecord prior = makeRecord(7, 1, KvState::Evicted, std::nullopt, 0,
                                      7, 3);
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {prior}, {std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsREQUIRE_KV_REUSE));
    const KvEdgeResult result = manager.commitEdge(edge);
    EXPECT_EQ(result.admissions.at(1), KvAdmissionOutcome::Claimed);
    EXPECT_EQ(result.promotions.at(1), KvPromotionOutcome::ReuseRequired);
    EXPECT_EQ(result.terminals.at(1).source,
              KvSnapshotSource::AdmissionClaim);
    const KvRecord *record = manager.findRecord(7, 7);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->state, KvState::Evicted);
    EXPECT_EQ(record->view_epoch, 7u);
    EXPECT_TRUE(manager.validate().empty());
}

TEST(MeshKvManager, ErrorRecordsAreNotTreatedAsAbsent)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 4, 2));
    std::string reason;
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Error, std::nullopt, 0, 7, 3)},
        {std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    EXPECT_EQ(manager.commitEdge(edge).admissions.at(1),
              KvAdmissionOutcome::KvState);
    ASSERT_NE(manager.findRecord(7, 7), nullptr);
    KvEdgeInputs release_edge;
    release_edge.releases.push_back(release(9, 7));
    EXPECT_EQ(manager.commitEdge(release_edge).releases.at(9),
              KvReleaseOutcome::Success);
}

// ------------------------------------------------------- persistence

TEST(MeshKvManager, RuntimeViewFreezesTheArmWindow)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    manager.commitEdge(first);
    manager.commitEdge(KvEdgeInputs{});
    KvEdgeInputs append;
    append.append_arms.push_back(KvAppendArm{1, 0, 4});
    manager.commitEdge(append);
    KvEdgeInputs terminal;
    terminal.append_terminals.push_back(
        KvAppendTerminal{1, std::vector<bool>(4, true), std::nullopt});
    manager.commitEdge(terminal);
    KvEdgeInputs arm;
    arm.view_arms.push_back(KvViewArm{1, 0});
    const KvEdgeResult result = manager.commitEdge(arm);
    ASSERT_EQ(result.views.count({1, 0}), 1u);
    const KvRuntimeView view = result.views.at({1, 0});
    EXPECT_EQ(view.valid_bytes_at_arm, 4u * kBytesPerToken);
    EXPECT_EQ(view.slot_base, 0x100000u);
    EXPECT_EQ(view.view_epoch, 2u);
    KvEdgeInputs again;
    again.append_arms.push_back(KvAppendArm{1, 4, 4});
    manager.commitEdge(again);
    KvEdgeInputs terminal2;
    terminal2.append_terminals.push_back(
        KvAppendTerminal{1, std::vector<bool>(4, true), std::nullopt});
    manager.commitEdge(terminal2);
    EXPECT_EQ(manager.findRecord(11, 11)->valid_bytes,
              8u * kBytesPerToken);
    EXPECT_EQ(view.valid_bytes_at_arm, 4u * kBytesPerToken);
}

TEST(MeshKvManager, ContractDigestMatchesTheContractFormula)
{
    const std::array<uint8_t, 32> expected = contractDigest(kBytesPerToken);
    EXPECT_EQ(expected, MeshKvManager::contractDigest(
        digestOf(0), digestOf(32), digestOf(64), kBytesPerToken));
    EXPECT_NE(expected, MeshKvManager::contractDigest(
        digestOf(0), digestOf(32), digestOf(64), kBytesPerToken + 1));
}


// ------------------------------------------------- R2 review regressions

TEST(MeshKvManager, InitialClaimCancelLeavesAnErrorRecordThenReleases)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 4, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    manager.commitEdge(first);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    manager.commitEdge(second);
    KvEdgeInputs cancel;
    cancel.owner_terminals.push_back(
        KvOwnerTerminal{2, KvTerminalStatus::Cancelled, std::nullopt, true});
    const KvEdgeResult cancelled = manager.commitEdge(cancel);
    ASSERT_FALSE(cancelled.fatal) << *cancelled.fatal;
    const KvRecord *record = manager.findRecord(22, 22);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_FALSE(record->slot_id);
    EXPECT_EQ(record->cached_tokens, 0u);
    EXPECT_EQ(record->view_epoch, 0u);
    EXPECT_FALSE(manager.hasClaim(2));
    EXPECT_FALSE(manager.hasPin(2));
    EXPECT_TRUE(manager.validate().empty());
    KvEdgeInputs release_edge;
    release_edge.releases.push_back(release(9, 22));
    const KvEdgeResult released = manager.commitEdge(release_edge);
    EXPECT_EQ(released.releases.at(9), KvReleaseOutcome::Success);
    EXPECT_EQ(manager.findRecord(22, 22), nullptr);
    EXPECT_EQ(manager.tombstoneCount(), 1u);
}

TEST(MeshKvManager, TailWaiterDoesNotBlockTheCanonicalHead)
{
    MeshKvManager manager(testGeometry(1), testCapacity(4, 2, 4, 2));
    std::string reason;
    KvRecord prior = makeRecord(7, 1, KvState::Resident, 0, 0, 5, 4);
    prior.content_digest.reset();
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {prior}, {std::make_pair<uint64_t, uint64_t>(7, 7)}, 5), reason))
        << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 22, 1, 0, 255));
    edge.admissions.push_back(admit(2, 7, 1,
                                    agent_abi::kSqFlagsREQUIRE_KV_REUSE));
    const KvEdgeResult first = manager.commitEdge(edge);
    ASSERT_FALSE(first.fatal) << *first.fatal;
    EXPECT_EQ(first.promotions.at(1), KvPromotionOutcome::WaitingSlot);
    EXPECT_EQ(first.evictions_started.size(), 1u);
    EXPECT_FALSE(manager.hasClaim(2));
    EXPECT_FALSE(manager.findRecord(22, 22)->slot_id);
    const KvEdgeResult second = manager.commitEdge(KvEdgeInputs{});
    ASSERT_FALSE(second.fatal) << *second.fatal;
    EXPECT_EQ(second.promotions.at(1), KvPromotionOutcome::Pinned);
    EXPECT_EQ(*manager.findRecord(22, 22)->slot_id, 0u);
    EXPECT_EQ(manager.findRecord(7, 7)->state, KvState::Evicted);
    EXPECT_TRUE(manager.validate().empty());
}

TEST(MeshKvManager, RollbackAuthorityExpiresAtTheFirstPrefillStart)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11, 1, 0, 0, 0, 0, 0, 1));
    const KvEdgeResult pinned = manager.commitEdge(edge);
    ASSERT_EQ(pinned.handoffs.count(1), 1u);
    const KvRollbackToken authority = pinned.handoffs.at(1);
    KvEdgeInputs start;
    start.core_starts.push_back(1);
    const KvEdgeResult started = manager.commitEdge(start);
    ASSERT_FALSE(started.fatal) << *started.fatal;
    KvEdgeInputs append;
    append.append_arms.push_back(KvAppendArm{1, 0, 1});
    manager.commitEdge(append);
    KvEdgeInputs terminal;
    terminal.append_terminals.push_back(
        KvAppendTerminal{1, std::vector<bool>(1, true), std::nullopt});
    manager.commitEdge(terminal);
    KvEdgeInputs late;
    late.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Error, authority, true});
    const KvEdgeResult expired = manager.commitEdge(late);
    ASSERT_TRUE(expired.fatal);
    EXPECT_EQ(*expired.fatal, "rollback authority is no longer valid");
    EXPECT_TRUE(expired.rollbacks.empty());
    const KvRecord *record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 1u);
    EXPECT_TRUE(record->slot_id.has_value());
}

TEST(MeshKvManager, CoreStartRequiresLiveRollbackAuthority)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    manager.commitEdge(edge);
    KvEdgeInputs start;
    start.core_starts.push_back(1);
    EXPECT_FALSE(manager.commitEdge(start).fatal.has_value());
    EXPECT_EQ(*manager.commitEdge(start).fatal,
              "core start without rollback authority");
}

TEST(MeshKvManager, FirstErrorUsesTheTypedNumericKey)
{
    for (int reversed = 0; reversed < 2; ++reversed) {
        MeshKvManager manager(testGeometry(), testCapacity());
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(1, 11));
        manager.commitEdge(edge);
        KvEdgeInputs faults;
        if (reversed == 0) {
            faults.faults.push_back(KvFaultEvent{1, candidate(2, 1)});
            faults.faults.push_back(KvFaultEvent{1, candidate(2, 256)});
        } else {
            faults.faults.push_back(KvFaultEvent{1, candidate(2, 256)});
            faults.faults.push_back(KvFaultEvent{1, candidate(2, 1)});
        }
        EXPECT_FALSE(manager.commitEdge(faults).fatal.has_value());
        const KvRecord *record = manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        ASSERT_TRUE(record->first_error.has_value());
        EXPECT_EQ(record->first_error->source.ordinal, 1u);
    }
}

TEST(MeshKvManager, DuplicateOwnerTerminalIsFatal)
{
    const std::pair<KvTerminalStatus, KvTerminalStatus> orders[2] = {
        {KvTerminalStatus::Success, KvTerminalStatus::Cancelled},
        {KvTerminalStatus::Cancelled, KvTerminalStatus::Success}};
    for (const auto &order : orders) {
        MeshKvManager manager(testGeometry(), testCapacity());
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(1, 11));
        manager.commitEdge(edge);
        KvEdgeInputs terminals;
        terminals.owner_terminals.push_back(
            KvOwnerTerminal{1, order.first, std::nullopt, true});
        terminals.owner_terminals.push_back(
            KvOwnerTerminal{1, order.second, std::nullopt, true});
        const KvEdgeResult result = manager.commitEdge(terminals);
        ASSERT_TRUE(result.fatal);
        EXPECT_EQ(*result.fatal, "duplicate KV owner terminal");
        EXPECT_TRUE(result.terminals.empty());
    }
}

TEST(MeshKvManager, ReleaseCompletionFollowsTheTupleKey)
{
    MeshKvManager manager(testGeometry(), testCapacity(4, 2, 4, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    manager.commitEdge(first);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    manager.commitEdge(second);
    KvEdgeInputs done;
    done.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    done.owner_terminals.push_back(
        KvOwnerTerminal{2, KvTerminalStatus::Success, std::nullopt, true});
    manager.commitEdge(done);
    KvEdgeInputs releases;
    releases.releases.push_back(release(20, 11));
    releases.releases.push_back(release(10, 22));
    const KvEdgeResult result = manager.commitEdge(releases);
    ASSERT_EQ(result.releases.size(), 1u);
    EXPECT_EQ(result.releases.count(20), 1u);
    EXPECT_EQ(manager.findRecord(11, 11), nullptr);
    EXPECT_NE(manager.findRecord(22, 22), nullptr);
}

TEST(MeshKvManager, GeometryKeepsFloorTokensAndRejectsWrap)
{
    KvGeometry good;
    good.region_base = 0x100000;
    good.slot_bytes = 4096;
    good.slot_alignment = 4096;
    good.max_sessions = 1;
    good.bytes_per_token = 192;
    std::string reason;
    EXPECT_TRUE(good.valid(reason)) << reason;
    EXPECT_EQ(good.tokensPerSlot(), 21u);
    KvGeometry wrap;
    wrap.region_base = UINT64_MAX - 4095;
    wrap.slot_bytes = 4096;
    wrap.slot_alignment = 4096;
    wrap.max_sessions = 2;
    wrap.bytes_per_token = 64;
    EXPECT_FALSE(wrap.valid(reason));
    EXPECT_EQ(reason, "kv_region_end overflow");
}

TEST(MeshKvManager, WaitingEntryCommitsOnlyAsTheCanonicalHead)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 2, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    manager.commitEdge(first);
    KvEdgeInputs queued;
    queued.admissions.push_back(admit(3, 33, 1, 0, 9));
    queued.admissions.push_back(admit(2, 22, 1, 0, 0));
    const KvEdgeResult admitted = manager.commitEdge(queued);
    EXPECT_EQ(admitted.admissions.at(3), KvAdmissionOutcome::Claimed);
    EXPECT_EQ(admitted.admissions.at(2), KvAdmissionOutcome::Waiting);
    EXPECT_TRUE(manager.hasClaim(3));
    EXPECT_FALSE(manager.hasClaim(2));
    EXPECT_NE(manager.findRecord(33, 33), nullptr);
    EXPECT_EQ(manager.findRecord(22, 22), nullptr);
    const KvEdgeResult promoted = manager.commitEdge(KvEdgeInputs{});
    EXPECT_EQ(promoted.promotions.at(3), KvPromotionOutcome::WaitingSlot);
    EXPECT_TRUE(manager.hasClaim(3));
    EXPECT_FALSE(manager.hasClaim(2));
}

TEST(MeshKvManager, SameTupleWaitersResolveByCanonicalOrder)
{
    MeshKvManager manager(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(2, 11, 1, 0, 0));
    edge.admissions.push_back(admit(1, 11, 1, 0, 9));
    const KvEdgeResult first = manager.commitEdge(edge);
    ASSERT_FALSE(first.fatal) << *first.fatal;
    EXPECT_EQ(first.admissions.at(1), KvAdmissionOutcome::Claimed);
    EXPECT_EQ(first.admissions.at(2), KvAdmissionOutcome::Waiting);
    EXPECT_TRUE(manager.hasPin(1));
    EXPECT_FALSE(manager.hasPin(2));
    EXPECT_NE(manager.findRecord(11, 11), nullptr);
    const KvEdgeResult second = manager.commitEdge(KvEdgeInputs{});
    ASSERT_FALSE(second.fatal) << *second.fatal;
    EXPECT_EQ(second.admissions.at(2), KvAdmissionOutcome::SessionExists);
    EXPECT_TRUE(manager.hasPin(1));
    EXPECT_TRUE(manager.validate().empty());
}


TEST(MeshKvManager, RecordCapacityIsEnforcedAtTheHeadCommit)
{
    MeshKvManager manager(testGeometry(2), testCapacity(1, 2, 2, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    EXPECT_EQ(manager.commitEdge(first).promotions.at(1),
              KvPromotionOutcome::Pinned);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    const KvEdgeResult queued = manager.commitEdge(second);
    EXPECT_EQ(queued.admissions.at(2), KvAdmissionOutcome::Waiting);
    EXPECT_EQ(queued.waits.at(2), KvWaitReason::RecordTableFull);
    EXPECT_EQ(manager.recordCount(), 1u);
    EXPECT_EQ(manager.findRecord(22, 22), nullptr);
    EXPECT_TRUE(manager.validate().empty());
    for (int tick = 3; tick < 6; ++tick) {
        const KvEdgeResult held = manager.commitEdge(KvEdgeInputs{});
        EXPECT_EQ(held.waits.at(2), KvWaitReason::RecordTableFull);
        EXPECT_EQ(manager.recordCount(), 1u);
    }
    KvEdgeInputs done;
    done.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    manager.commitEdge(done);
    KvEdgeInputs release_edge;
    release_edge.releases.push_back(release(9, 11));
    const KvEdgeResult released = manager.commitEdge(release_edge);
    EXPECT_EQ(released.releases.at(9), KvReleaseOutcome::Success);
    EXPECT_EQ(released.admissions.at(2), KvAdmissionOutcome::Claimed);
    EXPECT_EQ(released.promotions.at(2), KvPromotionOutcome::Pinned);
    EXPECT_EQ(manager.recordCount(), 1u);
    EXPECT_TRUE(manager.hasPin(2));
    EXPECT_TRUE(manager.validate().empty());
}

TEST(MeshKvManager, WaitingHeadSurvivesTemporaryBackpressure)
{
    MeshKvManager manager(testGeometry(1), testCapacity(4, 2, 4, 2));
    std::string reason;
    KvRecord prior = makeRecord(7, 1, KvState::Resident, 0, 0, 3, 2);
    prior.content_digest.reset();
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {prior}, {std::make_pair<uint64_t, uint64_t>(7, 7)}, 3), reason))
        << reason;
    KvEdgeInputs first;
    first.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    EXPECT_EQ(manager.commitEdge(first).promotions.at(1),
              KvPromotionOutcome::Pinned);
    KvEdgeInputs queued;
    queued.admissions.push_back(
        admit(2, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL, 0, 2));
    const KvEdgeResult waiting = manager.commitEdge(queued);
    EXPECT_EQ(waiting.admissions.at(2), KvAdmissionOutcome::Waiting);
    EXPECT_EQ(waiting.waits.at(2), KvWaitReason::TupleOwnerActive);
    KvEdgeInputs retry;
    retry.admissions.push_back(
        admit(2, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL, 0, 99));
    const KvEdgeResult ignored = manager.commitEdge(retry);
    EXPECT_EQ(ignored.waits.at(2), KvWaitReason::TupleOwnerActive);
    const KvLiveState live = manager.saveLive();
    ASSERT_EQ(live.waiters.size(), 1u);
    EXPECT_EQ(live.waiters[0].ready_tick, 2u);
    EXPECT_FALSE(manager.hasClaim(2));
    KvEdgeInputs done;
    done.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    const KvEdgeResult drained = manager.commitEdge(done);
    EXPECT_EQ(drained.admissions.at(2), KvAdmissionOutcome::Claimed);
    EXPECT_EQ(drained.promotions.at(2), KvPromotionOutcome::Pinned);
    EXPECT_TRUE(manager.hasPin(2));
    EXPECT_TRUE(manager.validate().empty());
}

TEST(MeshKvManager, AdmissionIdentityIsFrozenPerRequest)
{
    const uint64_t expected_epoch = 2;
    const uint64_t expected_serial = 2;
    const uint64_t request_ids[3] = {1, 1, 1};
    const uint64_t sessions[3] = {22, 11, 11};
    const uint32_t generations[3] = {1, 2, 1};
    for (int kind = 0; kind < 3; ++kind) {
        MeshKvManager manager(testGeometry(2), testCapacity(2, 2, 2, 2));
        KvEdgeInputs first;
        first.admissions.push_back(admit(1, 11));
        manager.commitEdge(first);
        KvEdgeInputs changed;
        changed.admissions.push_back(
            admit(request_ids[kind], sessions[kind], generations[kind]));
        const KvEdgeResult result = manager.commitEdge(changed);
        ASSERT_TRUE(result.fatal);
        EXPECT_EQ(*result.fatal, "duplicate KV acquire for a pinned request");
        EXPECT_EQ(manager.recordCount(), 1u);
        EXPECT_TRUE(manager.hasPin(1));
        EXPECT_EQ(manager.nextKvUseEpoch(), expected_epoch);
        EXPECT_EQ(manager.nextRollbackSerial(), expected_serial);
    }
}

TEST(MeshKvManager, SameEdgeDuplicateAdmissionAndReleaseWaiterIdentity)
{
    MeshKvManager duplicated(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    edge.admissions.push_back(admit(1, 22));
    const KvEdgeResult clash = duplicated.commitEdge(edge);
    ASSERT_TRUE(clash.fatal);
    EXPECT_EQ(*clash.fatal, "duplicate KV command identity in one edge");
    EXPECT_EQ(duplicated.recordCount(), 0u);
    EXPECT_TRUE(duplicated.validate().empty());

    MeshKvManager manager(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    manager.commitEdge(first);
    KvEdgeInputs pending;
    pending.releases.push_back(release(9, 11));
    const KvEdgeResult parked = manager.commitEdge(pending);
    EXPECT_TRUE(parked.releases.empty());
    EXPECT_TRUE(manager.hasReleaseWaiter(9));
    KvEdgeInputs clash_edge;
    clash_edge.admissions.push_back(admit(9, 33));
    const KvEdgeResult rejected = manager.commitEdge(clash_edge);
    ASSERT_TRUE(rejected.fatal);
    EXPECT_EQ(*rejected.fatal, "request id is a live release waiter");
}

TEST(MeshKvManager, PinBoundAllowsTwoPrestartPinsFromOneWaiterSlot)
{
    MeshKvManager manager(testGeometry(2), testCapacity(2, 2, 1, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    ASSERT_EQ(manager.commitEdge(first).promotions.at(1),
              KvPromotionOutcome::Pinned);
    EXPECT_EQ(manager.saveLive().waiters.size(), 0u);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    const KvEdgeResult result = manager.commitEdge(second);
    ASSERT_FALSE(result.fatal) << *result.fatal;
    EXPECT_EQ(result.promotions.at(2), KvPromotionOutcome::Pinned);
    const KvLiveState live = manager.saveLive();
    ASSERT_EQ(live.pins.size(), 2u);
    for (const KvRequestPin &pin : live.pins) {
        EXPECT_EQ(pin.phase, KvPinPhase::Prestart);
        EXPECT_TRUE(pin.payload.has_value());
    }
}

TEST(MeshKvManager, HeadTransitionIsAtomicOnCounterOverflow)
{
    for (uint32_t field = 0; field < 2; ++field) {
        MeshKvManager manager(testGeometry(2), testCapacity(2, 2, 2, 2));
        KvLiveState seeded = manager.saveLive();
        if (field == 0) {
            seeded.next_kv_use_epoch = UINT64_MAX;
        } else {
            seeded.next_rollback_serial = UINT64_MAX;
        }
        std::string reason;
        ASSERT_TRUE(manager.loadLive(seeded, reason)) << reason;
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(1, 11));
        const KvEdgeResult result = manager.commitEdge(edge);
        ASSERT_TRUE(result.fatal) << field;
        EXPECT_FALSE(result.fatal->empty());
        EXPECT_EQ(manager.recordCount(), 0u);
        EXPECT_TRUE(result.handoffs.empty());
        EXPECT_TRUE(result.promotions.empty());
        EXPECT_TRUE(result.rollbacks.empty());
        const KvLiveState after = manager.saveLive();
        EXPECT_EQ(after.records.size(), seeded.records.size());
        EXPECT_EQ(after.pins.size(), 0u);
        EXPECT_EQ(after.waiters.size(), 1u);
        EXPECT_EQ(after.waiters[0].claimed, false);
        EXPECT_TRUE(after.slot_owners == seeded.slot_owners);
        EXPECT_TRUE(manager.validate().empty());
    }
}

TEST(MeshKvManager, ReleaseRequestIdMustBeFree)
{
    MeshKvManager pinned(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs pin_edge;
    pin_edge.admissions.push_back(admit(1, 11));
    ASSERT_EQ(pinned.commitEdge(pin_edge).promotions.at(1),
              KvPromotionOutcome::Pinned);
    KvEdgeInputs pin_clash;
    pin_clash.releases.push_back(release(1, 11));
    const KvEdgeResult on_pin = pinned.commitEdge(pin_clash);
    ASSERT_TRUE(on_pin.fatal);
    EXPECT_EQ(*on_pin.fatal, "release request id is a live KV pin");
    EXPECT_TRUE(pinned.validate().empty());

    MeshKvManager waiting(testGeometry(1), testCapacity(2, 2, 3, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    waiting.commitEdge(first);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    ASSERT_EQ(waiting.commitEdge(second).promotions.at(2),
              KvPromotionOutcome::WaitingSlot);
    KvEdgeInputs waiter_clash;
    waiter_clash.releases.push_back(release(2, 22));
    const KvEdgeResult on_waiter = waiting.commitEdge(waiter_clash);
    ASSERT_TRUE(on_waiter.fatal);
    EXPECT_EQ(*on_waiter.fatal,
              "release request id is a live admission waiter");
    EXPECT_TRUE(waiting.validate().empty());

    MeshKvManager parked(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs owner;
    owner.admissions.push_back(admit(1, 11));
    parked.commitEdge(owner);
    KvEdgeInputs drain;
    drain.releases.push_back(release(9, 11));
    parked.commitEdge(drain);
    ASSERT_TRUE(parked.hasReleaseWaiter(9));
    KvEdgeInputs repeat;
    repeat.releases.push_back(release(9, 11));
    const KvEdgeResult on_release = parked.commitEdge(repeat);
    ASSERT_TRUE(on_release.fatal);
    EXPECT_EQ(*on_release.fatal,
              "release request id is a live release waiter");
    EXPECT_TRUE(parked.validate().empty());

    MeshKvManager mixed(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs overlap;
    overlap.admissions.push_back(admit(4, 44));
    overlap.releases.push_back(release(4, 44));
    const KvEdgeResult shared = mixed.commitEdge(overlap);
    ASSERT_TRUE(shared.fatal);
    EXPECT_EQ(*shared.fatal, "duplicate KV command identity in one edge");
    EXPECT_EQ(mixed.recordCount(), 0u);
    EXPECT_TRUE(mixed.validate().empty());
}

TEST(MeshKvManager, RollbackTokenIdentityIsChecked)
{
    MeshKvManager manager(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    const KvEdgeResult pinned = manager.commitEdge(edge);
    ASSERT_EQ(pinned.handoffs.count(1), 1u);
    const uint64_t before_pins = manager.saveLive().pins.size();
    KvRollbackToken wrong_request = pinned.handoffs.at(1);
    wrong_request.request_id = 2;
    KvEdgeInputs cancel;
    cancel.owner_terminals.push_back(KvOwnerTerminal{
        1, KvTerminalStatus::Cancelled, wrong_request, false});
    const KvEdgeResult refused = manager.commitEdge(cancel);
    ASSERT_TRUE(refused.fatal);
    EXPECT_EQ(*refused.fatal, "rollback authority is no longer valid");
    EXPECT_TRUE(refused.rollbacks.empty());
    EXPECT_EQ(manager.saveLive().pins.size(), before_pins);
    const KvRecord *record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->state, KvState::Allocating);
    EXPECT_TRUE(manager.validate().empty());

    MeshKvManager serial(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs serial_edge;
    serial_edge.admissions.push_back(admit(1, 11));
    serial.commitEdge(serial_edge);
    KvRollbackToken wrong_serial = pinned.handoffs.at(1);
    wrong_serial.serial = 99;
    KvEdgeInputs serial_cancel;
    serial_cancel.owner_terminals.push_back(KvOwnerTerminal{
        1, KvTerminalStatus::Cancelled, wrong_serial, false});
    const KvEdgeResult serial_refused = serial.commitEdge(serial_cancel);
    ASSERT_TRUE(serial_refused.fatal);
    EXPECT_TRUE(serial_refused.rollbacks.empty());
    EXPECT_TRUE(serial.validate().empty());
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

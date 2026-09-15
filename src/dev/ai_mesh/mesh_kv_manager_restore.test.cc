#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <fstream>
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

TEST(MeshKvManager, PersistentRoundTripRejectsTampering)
{
    MeshKvManager manager(testGeometry(2), testCapacity(2, 1, 4, 2));
    std::string reason;
    const KvPersistentState initial = persistentState(
        {makeRecord(11, 1, KvState::Resident, 0, 8, 3, 2),
         makeRecord(22, 1, KvState::Error, 1, 4, 2, 4)},
        {std::make_pair<uint64_t, uint64_t>(11, 11),
         std::make_pair<uint64_t, uint64_t>(22, 22)}, 6);
    ASSERT_TRUE(manager.loadPersistent(initial, reason)) << reason;
    const KvPersistentState saved = manager.savePersistent();
    MeshKvManager clone(testGeometry(2), testCapacity(2, 1, 4, 2));
    ASSERT_TRUE(clone.loadPersistent(saved, reason)) << reason;
    EXPECT_EQ(clone.stateScalars(), manager.stateScalars());
    EXPECT_TRUE(clone.validate().empty());

    MeshKvManager target(testGeometry(2), testCapacity(2, 1, 4, 2));
    KvPersistentState dropped = saved;
    dropped.slot_owners[1].reset();
    EXPECT_FALSE(target.loadPersistent(dropped, reason));
    KvPersistentState epoch = saved;
    epoch.next_kv_use_epoch = 1;
    EXPECT_FALSE(target.loadPersistent(epoch, reason));
    KvPersistentState zero_generation = saved;
    zero_generation.records[0].generation = 0;
    EXPECT_FALSE(target.loadPersistent(zero_generation, reason));
    KvPersistentState missing = saved;
    missing.records.pop_back();
    EXPECT_FALSE(target.loadPersistent(missing, reason));
    KvPersistentState extra = saved;
    extra.records.push_back(makeRecord(33, 1, KvState::Evicted));
    extra.records.push_back(makeRecord(44, 1, KvState::Evicted));
    EXPECT_FALSE(target.loadPersistent(extra, reason));
    KvPersistentState tombstone = saved;
    tombstone.tombstones.push_back({{11, 11}, 0});
    EXPECT_FALSE(target.loadPersistent(tombstone, reason));
}

TEST(MeshKvManager, LiveRoundTripCoversClaimToPinThenPrestartAbort)
{
    MeshKvManager manager(testGeometry(1), testCapacity());
    std::string reason;
    const KvRecord prior = makeRecord(7, 1, KvState::Resident, 0, 8, 4, 5);
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {prior}, {std::make_pair<uint64_t, uint64_t>(7, 7)}, 6), reason))
        << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 7, 1,
                                    agent_abi::kSqFlagsALLOW_REPREFILL, 0, 0,
                                    0, 8, 8));
    const KvEdgeResult pinned = manager.commitEdge(edge);
    ASSERT_EQ(pinned.handoffs.count(1), 1u);
    const KvLiveState live = manager.saveLive();
    MeshKvManager restored(testGeometry(1), testCapacity());
    ASSERT_TRUE(restored.loadLive(live, reason)) << reason;
    EXPECT_EQ(restored.stateScalars(), manager.stateScalars());
    KvEdgeInputs abort;
    abort.owner_terminals.push_back(KvOwnerTerminal{
        1, KvTerminalStatus::Cancelled, pinned.handoffs.at(1), false});
    const KvEdgeResult rolled = restored.commitEdge(abort);
    EXPECT_EQ(rolled.rollbacks.at(1), KvRollbackKind::RestorePrior);
    const KvRecord *record = restored.findRecord(7, 7);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->state, KvState::Resident);
    EXPECT_EQ(record->cached_tokens, 8u);
    EXPECT_EQ(record->view_epoch, 4u);
}

TEST(MeshKvManager, LiveStateRejectsOwnersWithoutRecords)
{
    MeshKvManager manager(testGeometry(), testCapacity());
    std::string reason;
    const KvLiveState live = manager.saveLive();
    KvLiveState broken_pin = live;
    broken_pin.pins.push_back(makePin(1, 11));
    EXPECT_FALSE(manager.loadLive(broken_pin, reason));
    KvLiveState broken_waiter = live;
    broken_waiter.release_waiters.push_back(KvReleaseWaiter{1, 11, 11, 1});
    EXPECT_FALSE(manager.loadLive(broken_waiter, reason));
    KvLiveState broken_append = live;
    broken_append.appends.push_back(
        KvAppendObligation{1, 11, 11, 1, 0, 4, 0, 256});
    EXPECT_FALSE(manager.loadLive(broken_append, reason));
}

TEST(MeshKvManager, RestoreRejectsPriorAndOwnerRelationViolations)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 2, 2));
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    ASSERT_EQ(manager.commitEdge(edge).promotions.at(1),
              KvPromotionOutcome::Pinned);
    const KvLiveState live = manager.saveLive();
    MeshKvManager target(testGeometry(1), testCapacity(2, 2, 2, 2));
    target.commitEdge(KvEdgeInputs{});
    std::string reason;
    auto rejected = [&target](const char *name, const KvLiveState &state) {
        std::string why;
        EXPECT_FALSE(target.loadLive(state, why)) << name;
        EXPECT_FALSE(why.empty()) << name;
        EXPECT_TRUE(target.validate().empty()) << name;
    };

    KvLiveState prior_present = live;
    prior_present.pins[0].payload->prior_absent = false;
    rejected("initial_prior_present", prior_present);
    KvLiveState bad_diagnostic = live;
    bad_diagnostic.pins[0].payload->prior.diagnostic_prefix_bytes = 1;
    rejected("initial_prior_bad_diagnostic", bad_diagnostic);
    KvLiveState bad_content = live;
    bad_content.pins[0].payload->prior.content_digest = digestOf(9);
    rejected("initial_prior_bad_content", bad_content);
    KvLiveState duplicate_serial = live;
    KvLiveState second_pin = live;
    second_pin.pins[0].request_id = 9;
    duplicate_serial.pins.push_back(second_pin.pins[0]);
    rejected("duplicate_live_serial", duplicate_serial);
    KvLiveState zero_waiter = live;
    KvAdmissionWaiter trailer;
    trailer.request_id = 0;
    trailer.session_id = 22;
    trailer.kv_handle = 22;
    zero_waiter.waiters.push_back(trailer);
    rejected("waiter_request_zero", zero_waiter);
    KvLiveState qos_waiter = live;
    KvAdmissionWaiter high_qos;
    high_qos.request_id = 3;
    high_qos.session_id = 33;
    high_qos.kv_handle = 33;
    high_qos.generation = 1;
    high_qos.qos = 256;
    qos_waiter.waiters.push_back(high_qos);
    rejected("waiter_qos_out_of_range", qos_waiter);

    MeshKvManager reuse_source(testGeometry(2), testCapacity(2, 2, 2, 2));
    ASSERT_TRUE(reuse_source.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Resident, 0u, 8, 3, 2)},
        {std::make_pair<uint64_t, uint64_t>(7, 7), std::nullopt}, 3),
        reason)) << reason;
    KvEdgeInputs reuse_edge;
    reuse_edge.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL, 0, 0, 0, 8));
    const KvEdgeResult reuse = reuse_source.commitEdge(reuse_edge);
    ASSERT_EQ(reuse.promotions.at(1), KvPromotionOutcome::Pinned);
    const KvLiveState reuse_live = reuse_source.saveLive();
    ASSERT_EQ(reuse_live.pins[0].payload->prior.state, KvState::Resident);
    MeshKvManager reuse_target(testGeometry(2), testCapacity(2, 2, 2, 2));
    reuse_target.commitEdge(KvEdgeInputs{});
    KvLiveState other_slot = reuse_live;
    other_slot.slot_owners[1] = std::make_pair<uint64_t, uint64_t>(8, 8);
    other_slot.pins[0].payload->prior.slot_id = 1;
    EXPECT_FALSE(reuse_target.loadLive(other_slot, reason));
    KvLiveState invalid_bytes = reuse_live;
    invalid_bytes.pins[0].payload->prior.cached_tokens = 0;
    invalid_bytes.pins[0].payload->prior.valid_bytes = kBytesPerToken;
    EXPECT_FALSE(reuse_target.loadLive(invalid_bytes, reason));

    MeshKvManager claimed(testGeometry(1), testCapacity(3, 2, 3, 2));
    KvEdgeInputs pinned;
    pinned.admissions.push_back(admit(1, 11));
    claimed.commitEdge(pinned);
    KvEdgeInputs waiting;
    waiting.admissions.push_back(admit(2, 22));
    ASSERT_EQ(claimed.commitEdge(waiting).promotions.at(2),
              KvPromotionOutcome::WaitingSlot);
    const KvLiveState claim_live = claimed.saveLive();
    ASSERT_EQ(claim_live.waiters.size(), 1u);
    MeshKvManager claim_target(testGeometry(1), testCapacity(3, 2, 3, 2));
    claim_target.commitEdge(KvEdgeInputs{});
    KvLiveState wrong_generation = claim_live;
    wrong_generation.waiters[0].generation = 99;
    wrong_generation.waiters[0].prior.generation = 99;
    EXPECT_FALSE(claim_target.loadLive(wrong_generation, reason));
    KvLiveState wrong_slot = claim_live;
    ASSERT_EQ(claim_live.records[1].session_id, 22u);
    wrong_slot.records[1].slot_id = 0;
    EXPECT_FALSE(claim_target.loadLive(wrong_slot, reason));
    EXPECT_EQ(claim_target.recordCount(), 0u);
    EXPECT_TRUE(claim_target.validate().empty());

    ASSERT_TRUE(target.loadLive(live, reason)) << reason;
    EXPECT_TRUE(target.validate().empty());
    EXPECT_EQ(target.saveLive().pins.size(), 1u);
}

TEST(MeshKvManager, RestoreAcceptsMultiClaimAndContinues)
{
    MeshKvManager manager(testGeometry(1), testCapacity(3, 2, 3, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    ASSERT_EQ(manager.commitEdge(first).promotions.at(1),
              KvPromotionOutcome::Pinned);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    ASSERT_EQ(manager.commitEdge(second).promotions.at(2),
              KvPromotionOutcome::WaitingSlot);
    KvEdgeInputs third;
    third.admissions.push_back(admit(3, 33, 1, 0, 255));
    ASSERT_EQ(manager.commitEdge(third).promotions.at(3),
              KvPromotionOutcome::WaitingSlot);
    EXPECT_TRUE(manager.validate().empty());
    const KvLiveState live = manager.saveLive();
    ASSERT_EQ(live.waiters.size(), 2u);
    ASSERT_EQ(live.records.size(), 3u);

    MeshKvManager clone(testGeometry(1), testCapacity(3, 2, 3, 2));
    std::string reason;
    ASSERT_TRUE(clone.loadLive(live, reason)) << reason;
    EXPECT_TRUE(clone.validate().empty());
    KvEdgeInputs terminal;
    terminal.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    const KvEdgeResult left = manager.commitEdge(terminal);
    const KvEdgeResult right = clone.commitEdge(terminal);
    EXPECT_EQ(left.fatal.has_value(), right.fatal.has_value());
    EXPECT_TRUE(manager.edgeScalars(left) == clone.edgeScalars(right));
    EXPECT_TRUE(manager.validate().empty());
    EXPECT_TRUE(clone.validate().empty());
}

TEST(MeshKvManager, RestoreDerivesWaiterKindFromFrozenFlags)
{
    const KvGeometry geometry = testGeometry(2);
    const KvCapacity capacity = testCapacity(2, 2, 3, 2);
    MeshKvManager source(geometry, capacity);
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 11));
    edge.admissions.push_back(
        admit(2, 22, 1, agent_abi::kSqFlagsREQUIRE_KV_REUSE));
    source.commitEdge(edge);
    const KvLiveState live = source.saveLive();
    ASSERT_EQ(live.waiters.size(), 1u);
    ASSERT_FALSE(live.waiters[0].claimed);

    std::string reason;
    MeshKvManager baseline(geometry, capacity);
    ASSERT_TRUE(baseline.loadLive(live, reason)) << reason;
    const KvEdgeResult result = baseline.commitEdge(KvEdgeInputs{});
    EXPECT_EQ(result.admissions.at(2), KvAdmissionOutcome::SessionNotFound);
    EXPECT_EQ(baseline.recordCount(), 1u);
    EXPECT_EQ(baseline.saveLive().pins.size(), 1u);

    KvLiveState marked = live;
    marked.waiters[0].initial = true;
    MeshKvManager target(geometry, capacity);
    KvEdgeInputs seed;
    seed.admissions.push_back(admit(9, 9));
    target.commitEdge(seed);
    const KvLiveState before = target.saveLive();
    EXPECT_FALSE(target.loadLive(marked, reason));
    EXPECT_FALSE(reason.empty());
    EXPECT_EQ(target.saveLive().records.size(), before.records.size());
    EXPECT_EQ(target.saveLive().waiters.size(), before.waiters.size());

    MeshKvManager plain(testGeometry(1), testCapacity(1, 2, 3, 2));
    KvEdgeInputs open;
    open.admissions.push_back(admit(1, 11));
    plain.commitEdge(open);
    KvEdgeInputs blocked;
    blocked.admissions.push_back(admit(2, 22));
    plain.commitEdge(blocked);
    const KvLiveState plain_live = plain.saveLive();
    ASSERT_EQ(plain_live.waiters.size(), 1u);
    ASSERT_FALSE(plain_live.waiters[0].claimed);
    ASSERT_TRUE(plain_live.waiters[0].initial);
    KvLiveState dropped = plain_live;
    dropped.waiters[0].initial = false;
    EXPECT_FALSE(plain.loadLive(dropped, reason));
    EXPECT_TRUE(plain.saveLive().records.size() == plain_live.records.size());
}

TEST(MeshKvManager, RestoreRejectsPriorEpochBeyondIssued)
{
    const KvGeometry geometry = testGeometry(1);
    const KvCapacity capacity = testCapacity(2, 2, 2, 2);
    MeshKvManager source(geometry, capacity);
    std::string reason;
    ASSERT_TRUE(source.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3)},
        {std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    const KvEdgeResult pinned = source.commitEdge(edge);
    ASSERT_EQ(pinned.promotions.at(1), KvPromotionOutcome::Pinned);
    const KvLiveState live = source.saveLive();
    ASSERT_TRUE(live.pins[0].payload.has_value());
    ASSERT_EQ(live.pins[0].payload->prior.last_use_epoch, 3u);
    ASSERT_EQ(live.next_kv_use_epoch, 5u);

    MeshKvManager positive(geometry, capacity);
    ASSERT_TRUE(positive.loadLive(live, reason)) << reason;
    KvEdgeInputs cancel;
    cancel.owner_terminals.push_back(KvOwnerTerminal{
        1, KvTerminalStatus::Cancelled, pinned.handoffs.at(1), false});
    const KvEdgeResult rolled = positive.commitEdge(cancel);
    ASSERT_FALSE(rolled.fatal);
    const KvRecord *restored = positive.findRecord(7, 7);
    ASSERT_NE(restored, nullptr);
    EXPECT_EQ(restored->last_use_epoch, 3u);
    EXPECT_TRUE(positive.validate().empty());

    KvLiveState future = live;
    future.pins[0].payload->prior.last_use_epoch = 99;
    MeshKvManager target(geometry, capacity);
    KvEdgeInputs seed;
    seed.admissions.push_back(admit(9, 9));
    target.commitEdge(seed);
    const KvLiveState before = target.saveLive();
    EXPECT_FALSE(target.loadLive(future, reason));
    EXPECT_EQ(target.saveLive().records.size(), before.records.size());
    EXPECT_EQ(target.findRecord(7, 7), nullptr);

    MeshKvManager claimed(geometry, testCapacity(3, 2, 3, 2));
    ASSERT_TRUE(claimed.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3)},
        {std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    claimed.commitEdge(first);
    KvEdgeInputs second;
    second.admissions.push_back(
        admit(2, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    claimed.commitEdge(second);
    KvLiveState claim_live = claimed.saveLive();
    size_t claimed_index = claim_live.waiters.size();
    for (size_t index = 0; index < claim_live.waiters.size(); ++index)
        if (claim_live.waiters[index].claimed)
            claimed_index = index;
    ASSERT_LT(claimed_index, claim_live.waiters.size());
    ASSERT_EQ(claim_live.waiters[claimed_index].prior.last_use_epoch, 3u);
    MeshKvManager claim_target(geometry, testCapacity(3, 2, 3, 2));
    ASSERT_TRUE(claim_target.loadLive(claim_live, reason)) << reason;
    KvLiveState claim_future = claim_live;
    claim_future.waiters[claimed_index].prior.last_use_epoch = 99;
    EXPECT_FALSE(claim_target.loadLive(claim_future, reason));
    EXPECT_EQ(claim_target.saveLive().waiters[claimed_index].prior
                  .last_use_epoch, 3u);
}

TEST(MeshKvManager, RestoreEnforcesTombstoneCapacity)
{
    const KvGeometry geometry = testGeometry(2);
    const KvCapacity capacity = testCapacity(2, 2, 2, 2);
    MeshKvManager source(geometry, capacity);
    const KvLiveState live = source.saveLive();
    std::string reason;

    KvLiveState exact = live;
    exact.tombstones = {{{1, 1}, 2}, {{2, 2}, 2}};
    MeshKvManager accepted(geometry, capacity);
    ASSERT_TRUE(accepted.loadLive(exact, reason)) << reason;
    EXPECT_EQ(accepted.tombstoneCount(), 2u);

    KvLiveState over = live;
    over.tombstones = {{{1, 1}, 2}, {{2, 2}, 2}, {{3, 3}, 2}};
    EXPECT_FALSE(accepted.loadLive(over, reason));
    EXPECT_EQ(accepted.tombstoneCount(), 2u);

    KvPersistentState persistent = persistentState({}, {std::nullopt,
        std::nullopt}, 1);
    persistent.tombstones = over.tombstones;
    MeshKvManager persistent_target(geometry, capacity);
    EXPECT_FALSE(persistent_target.loadPersistent(persistent, reason));
}

TEST(MeshKvManager, RestoreUsesFrozenReuseRequirement)
{
    const KvGeometry geometry = testGeometry(2);
    const KvCapacity capacity = testCapacity(2, 2, 2, 2);
    MeshKvManager source(geometry, capacity);
    std::string reason;
    ASSERT_TRUE(source.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Resident, 0u, 8, 3, 2)},
        {std::make_pair<uint64_t, uint64_t>(7, 7), std::nullopt}, 3),
        reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 7, 1,
        agent_abi::kSqFlagsREQUIRE_KV_REUSE, 0, 0, 0, 8, 8));
    const KvEdgeResult pinned = source.commitEdge(edge);
    ASSERT_EQ(pinned.promotions.at(1), KvPromotionOutcome::Pinned);
    const KvLiveState live = source.saveLive();
    ASSERT_TRUE(live.pins[0].payload.has_value());
    ASSERT_EQ(live.pins[0].payload->intent, KvPath::KvReuse);
    ASSERT_EQ(live.pins[0].required_cached_tokens, 8u);

    MeshKvManager accepted(geometry, capacity);
    ASSERT_TRUE(accepted.loadLive(live, reason)) << reason;
    EXPECT_TRUE(accepted.validate().empty());

    KvLiveState flipped = live;
    flipped.pins[0].payload->intent = KvPath::InitialPrefill;
    flipped.pins[0].payload->prior_absent = true;
    flipped.pins[0].payload->prior.state = KvState::Allocating;
    flipped.pins[0].payload->prior.slot_id.reset();
    flipped.pins[0].payload->prior.cached_tokens = 0;
    flipped.pins[0].payload->prior.valid_bytes = 0;
    flipped.pins[0].payload->prior.content_digest.reset();
    flipped.pins[0].payload->prior.view_epoch = 0;
    flipped.pins[0].payload->prior.last_use_epoch = 0;
    EXPECT_FALSE(accepted.loadLive(flipped, reason));
    EXPECT_TRUE(reason.find("frozen flags") != std::string::npos);

    KvLiveState wrong = live;
    wrong.pins[0].required_cached_tokens = 7;
    MeshKvManager rejected(geometry, capacity);
    KvEdgeInputs seed;
    seed.admissions.push_back(admit(9, 9));
    rejected.commitEdge(seed);
    const KvLiveState before = rejected.saveLive();
    EXPECT_FALSE(rejected.loadLive(wrong, reason));
    EXPECT_EQ(rejected.saveLive().records.size(), before.records.size());
    EXPECT_EQ(rejected.saveLive().pins.size(), before.pins.size());
}

TEST(MeshKvManager, RestoreBindsFrozenFlagsToTheDecidedPath)
{
    const KvGeometry geometry = testGeometry(2);
    const KvCapacity capacity = testCapacity(2, 2, 2, 2);
    MeshKvManager source(geometry, capacity);
    std::string reason;
    ASSERT_TRUE(source.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3)},
        {std::nullopt, std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    const KvEdgeResult pinned = source.commitEdge(edge);
    ASSERT_EQ(pinned.promotions.at(1), KvPromotionOutcome::Pinned);
    const KvLiveState pin_live = source.saveLive();
    ASSERT_TRUE(pin_live.pins[0].payload.has_value());
    ASSERT_EQ(pin_live.pins[0].payload->intent, KvPath::Reprefill);
    for (uint32_t flags : {kKvRequireReuse, kKvFlagMask}) {
        KvLiveState tampered = pin_live;
        tampered.pins[0].flags = flags;
        MeshKvManager target(geometry, capacity);
        KvEdgeInputs seed;
        seed.admissions.push_back(admit(9, 9));
        target.commitEdge(seed);
        const KvLiveState before = target.saveLive();
        EXPECT_FALSE(target.loadLive(tampered, reason)) << flags;
        EXPECT_EQ(target.saveLive().records.size(), before.records.size());
    }

    MeshKvManager claim(testGeometry(1), testCapacity(3, 2, 3, 2));
    ASSERT_TRUE(claim.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3)},
        {std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    claim.commitEdge(first);
    KvEdgeInputs second;
    second.admissions.push_back(
        admit(2, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    claim.commitEdge(second);
    const KvLiveState claim_live = claim.saveLive();
    ASSERT_EQ(claim_live.waiters.size(), 1u);
    ASSERT_TRUE(claim_live.waiters[0].claimed);
    ASSERT_EQ(claim_live.waiters[0].intent, KvPath::Reprefill);
    for (uint32_t flags : {kKvRequireReuse, kKvFlagMask}) {
        KvLiveState tampered = claim_live;
        tampered.waiters[0].flags = flags;
        MeshKvManager target(testGeometry(1), testCapacity(3, 2, 3, 2));
        KvEdgeInputs seed;
        seed.admissions.push_back(admit(9, 9));
        target.commitEdge(seed);
        const KvLiveState before = target.saveLive();
        EXPECT_FALSE(target.loadLive(tampered, reason)) << flags;
        EXPECT_EQ(target.saveLive().records.size(), before.records.size());
    }

    KvEdgeInputs suffix;
    suffix.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    suffix.releases.push_back(KvReleaseIntent{30, 11, 11, 1});
    MeshKvManager restored(testGeometry(1), testCapacity(3, 2, 3, 2));
    ASSERT_TRUE(restored.loadLive(claim_live, reason)) << reason;
    const KvEdgeResult left = restored.commitEdge(suffix);
    const KvEdgeResult right = claim.commitEdge(suffix);
    ASSERT_FALSE(left.fatal);
    ASSERT_FALSE(right.fatal);
    EXPECT_TRUE(restored.edgeScalars(left) == claim.edgeScalars(right));
    EXPECT_EQ(restored.saveLive().pins.size(), 1u);
    ASSERT_TRUE(restored.saveLive().pins[0].payload.has_value());
    EXPECT_EQ(restored.saveLive().pins[0].payload->intent, KvPath::Reprefill);
    EXPECT_TRUE(restored.validate().empty());
}

TEST(MeshKvManager, AdmissionRejectsUnknownAndMixedFlags)
{
    for (uint32_t flags : {kKvFlagMask, 4u}) {
        MeshKvManager kv(testGeometry(2), testCapacity(2, 2, 2, 2));
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(1, 11, 1, flags));
        const KvEdgeResult result = kv.commitEdge(edge);
        EXPECT_FALSE(result.fatal) << flags;
        ASSERT_TRUE(result.admissions.count(1) == 1u) << flags;
        EXPECT_EQ(result.admissions.at(1),
                  KvAdmissionOutcome::FlagCombination) << flags;
        EXPECT_EQ(kv.recordCount(), 0u);
        EXPECT_EQ(kv.saveLive().waiters.size(), 0u);
        EXPECT_EQ(kv.saveLive().pins.size(), 0u);
        for (const auto &owner : kv.saveLive().slot_owners)
            EXPECT_FALSE(owner.has_value()) << flags;
        EXPECT_TRUE(kv.validate().empty());
        const KvEdgeResult follow = kv.commitEdge(KvEdgeInputs{});
        EXPECT_FALSE(follow.fatal) << flags;
        EXPECT_TRUE(follow.admissions.empty()) << flags;
        EXPECT_TRUE(follow.promotions.empty()) << flags;
        EXPECT_TRUE(follow.terminals.empty()) << flags;
        EXPECT_EQ(kv.recordCount(), 0u);
        EXPECT_EQ(kv.saveLive().pins.size(), 0u);
        EXPECT_EQ(kv.saveLive().waiters.size(), 0u);
    }
    MeshKvManager initial(testGeometry(2), testCapacity(2, 2, 2, 2));
    KvEdgeInputs plain;
    plain.admissions.push_back(admit(1, 11));
    ASSERT_EQ(initial.commitEdge(plain).admissions.at(1),
              KvAdmissionOutcome::Claimed);
    EXPECT_TRUE(initial.validate().empty());
    for (uint32_t flags : {kKvAllowReprefill, kKvRequireReuse}) {
        MeshKvManager kv(testGeometry(2), testCapacity(2, 2, 2, 2));
        KvEdgeInputs edge;
        edge.admissions.push_back(admit(1, 11, 1, flags));
        const KvEdgeResult result = kv.commitEdge(edge);
        ASSERT_TRUE(result.admissions.count(1) == 1u) << flags;
        EXPECT_EQ(result.admissions.at(1),
                  KvAdmissionOutcome::SessionNotFound) << flags;
        EXPECT_TRUE(kv.validate().empty());
    }
    MeshKvManager reprefill(testGeometry(2), testCapacity(2, 2, 2, 2));
    std::string reason;
    ASSERT_TRUE(reprefill.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3)},
        {std::nullopt, std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs repair;
    repair.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    ASSERT_EQ(reprefill.commitEdge(repair).admissions.at(1),
              KvAdmissionOutcome::Claimed);
    EXPECT_TRUE(reprefill.validate().empty());
    MeshKvManager reuse(testGeometry(2), testCapacity(2, 2, 2, 2));
    ASSERT_TRUE(reuse.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Resident, 0u, 8, 3, 2)},
        {std::make_pair<uint64_t, uint64_t>(7, 7), std::nullopt}, 4),
        reason)) << reason;
    KvEdgeInputs matched;
    matched.admissions.push_back(admit(1, 7, 1,
        agent_abi::kSqFlagsREQUIRE_KV_REUSE, 0, 0, 0, 8, 8));
    ASSERT_EQ(reuse.commitEdge(matched).admissions.at(1),
              KvAdmissionOutcome::Claimed);
    EXPECT_TRUE(reuse.validate().empty());
}

TEST(MeshKvManager, RestoreValidatesNestedErrorSource)
{
    const KvGeometry geometry = testGeometry(2);
    const KvCapacity capacity = testCapacity(2, 2, 2, 2);
    MeshKvManager source(geometry, capacity);
    std::string reason;
    ASSERT_TRUE(source.loadPersistent(persistentState(
        {makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3)},
        {std::nullopt, std::nullopt}, 4), reason)) << reason;
    KvLiveState live = source.saveLive();
    live.records[0].state = KvState::Error;
    live.records[0].first_error = KvErrorCandidate{};
    MeshKvManager accepted(geometry, capacity);
    ASSERT_TRUE(accepted.loadLive(live, reason)) << reason;
    EXPECT_TRUE(accepted.validate().empty());
    EXPECT_FALSE(accepted.stateScalars().empty());

    for (uint32_t kind = 0; kind < 3; ++kind) {
        KvLiveState bad = live;
        if (kind == 0)
            bad.records[0].first_error->source.error_class = 99;
        else if (kind == 1)
            bad.records[0].first_error->source.domain = 99;
        else
            bad.records[0].first_error->source.object_kind = 99;
        EXPECT_FALSE(accepted.loadLive(bad, reason)) << kind;
        EXPECT_EQ(accepted.saveLive().records[0].state, KvState::Error);
    }
}

TEST(MeshKvManager, LegalWaitingPrefixIsAccepted)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 2, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    manager.commitEdge(first);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    manager.commitEdge(second);
    const KvLiveState live = manager.saveLive();
    ASSERT_EQ(live.waiters.size(), 1u);
    MeshKvManager clone(testGeometry(1), testCapacity(2, 2, 2, 2));
    std::string reason;
    ASSERT_TRUE(clone.loadLive(live, reason)) << reason;
    EXPECT_TRUE(clone.validate().empty());
    EXPECT_EQ(clone.stateScalars(), manager.stateScalars());
}

TEST(MeshKvManager, OrphanAllocatingRecordIsRejected)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 4, 2));
    std::string reason;
    KvRecord orphan = makeRecord(7, 1, KvState::Allocating, 0, 0, 1, 3);
    EXPECT_FALSE(manager.loadPersistent(persistentState(
        {orphan}, {std::make_pair<uint64_t, uint64_t>(7, 7)}, 4), reason));
    EXPECT_EQ(reason, "ALLOCATING must hold a KV owner");
    EXPECT_EQ(manager.recordCount(), 0u);
}

TEST(MeshKvManager, LiveRestoreRejectsTamperedEvidence)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 2, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    manager.commitEdge(first);
    KvEdgeInputs second;
    second.admissions.push_back(admit(2, 22));
    manager.commitEdge(second);
    const KvLiveState live = manager.saveLive();
    ASSERT_TRUE(live.pins[0].payload.has_value());
    ASSERT_EQ(live.waiters.size(), 1u);
    MeshKvManager target(testGeometry(1), testCapacity(2, 2, 2, 2));
    std::string reason;

    KvLiveState missing_pin = live;
    missing_pin.pins.clear();
    EXPECT_FALSE(target.loadLive(missing_pin, reason));

    KvLiveState wrong_generation = live;
    wrong_generation.pins[0].generation = 99;
    EXPECT_FALSE(target.loadLive(wrong_generation, reason));

    KvLiveState wrong_count = live;
    wrong_count.records[0].pin_count = 99;
    EXPECT_FALSE(target.loadLive(wrong_count, reason));

    KvLiveState bad_enum = live;
    bad_enum.records[0].state = static_cast<KvState>(99);
    EXPECT_FALSE(target.loadLive(bad_enum, reason));

    KvLiveState missing_payload = live;
    missing_payload.pins[0].payload.reset();
    EXPECT_FALSE(target.loadLive(missing_payload, reason));

    KvLiveState wrong_serial = live;
    wrong_serial.pins[0].payload->serial = 99;
    EXPECT_FALSE(target.loadLive(wrong_serial, reason));

    KvLiveState missing_waiter = live;
    missing_waiter.waiters.clear();
    EXPECT_FALSE(target.loadLive(missing_waiter, reason));

    KvLiveState dropped_record = live;
    dropped_record.records.clear();
    EXPECT_FALSE(target.loadLive(dropped_record, reason));

    EXPECT_EQ(target.recordCount(), 0u);
    EXPECT_TRUE(target.validate().empty());
}

TEST(MeshKvManager, LiveRestoreKeepsTombstonesAndStaleGeneration)
{
    MeshKvManager manager(testGeometry(), testCapacity(2, 2, 4, 2));
    KvEdgeInputs first;
    first.admissions.push_back(admit(1, 11));
    manager.commitEdge(first);
    KvEdgeInputs done;
    done.owner_terminals.push_back(
        KvOwnerTerminal{1, KvTerminalStatus::Success, std::nullopt, true});
    manager.commitEdge(done);
    KvEdgeInputs release_edge;
    release_edge.releases.push_back(release(9, 11));
    manager.commitEdge(release_edge);
    ASSERT_EQ(manager.tombstoneCount(), 1u);
    MeshKvManager restored(testGeometry(), testCapacity(2, 2, 4, 2));
    std::string reason;
    ASSERT_TRUE(restored.loadLive(manager.saveLive(), reason)) << reason;
    EXPECT_EQ(restored.tombstoneCount(), 1u);
    KvEdgeInputs stale;
    stale.releases.push_back(release(10, 11));
    EXPECT_EQ(restored.commitEdge(stale).releases.at(10),
              KvReleaseOutcome::StaleGeneration);
    KvEdgeInputs again;
    again.admissions.push_back(admit(1, 11));
    EXPECT_EQ(restored.commitEdge(again).admissions.at(1),
              KvAdmissionOutcome::SessionExists);
}

TEST(MeshKvManager, LiveRestoreRejectsIncompleteRawStructure)
{
    MeshKvManager manager(testGeometry(1), testCapacity(2, 2, 2, 2));
    std::string reason;
    KvRecord prior = makeRecord(7, 1, KvState::Evicted, std::nullopt, 0, 7, 3);
    ASSERT_TRUE(manager.loadPersistent(persistentState(
        {prior}, {std::nullopt}, 4), reason)) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(
        admit(1, 7, 1, agent_abi::kSqFlagsALLOW_REPREFILL));
    const KvEdgeResult pinned = manager.commitEdge(edge);
    ASSERT_EQ(pinned.promotions.at(1), KvPromotionOutcome::Pinned);
    const KvLiveState live = manager.saveLive();
    ASSERT_EQ(live.pins.size(), 1u);
    ASSERT_TRUE(live.pins[0].payload.has_value());
    MeshKvManager target(testGeometry(1), testCapacity(2, 2, 2, 2));
    target.commitEdge(KvEdgeInputs{});

    KvLiveState empty_bitmap = live;
    empty_bitmap.slot_owners.clear();
    EXPECT_FALSE(target.loadLive(empty_bitmap, reason));
    KvLiveState long_bitmap = live;
    long_bitmap.slot_owners.push_back(std::nullopt);
    EXPECT_FALSE(target.loadLive(long_bitmap, reason));
    KvLiveState bad_owner = live;
    bad_owner.slot_owners[0] = std::make_pair<uint64_t, uint64_t>(0, 0);
    EXPECT_FALSE(target.loadLive(bad_owner, reason));
    KvLiveState duplicate_record = live;
    duplicate_record.records.push_back(live.records[0]);
    EXPECT_FALSE(target.loadLive(duplicate_record, reason));
    KvLiveState duplicate_tombstone = live;
    duplicate_tombstone.tombstones.push_back({{11, 11}, 2});
    duplicate_tombstone.tombstones.push_back({{11, 11}, 3});
    EXPECT_FALSE(target.loadLive(duplicate_tombstone, reason));
    KvLiveState duplicate_evicting = live;
    duplicate_evicting.evicting[{11, 11}] = KvEvictingEntry{};
    duplicate_evicting.evicting[{11, 11}] = KvEvictingEntry{};
    EXPECT_FALSE(target.loadLive(duplicate_evicting, reason) &&
                 duplicate_evicting.evicting.size() != 1);

    KvLiveState prior_generation = live;
    prior_generation.pins[0].payload->prior.generation = 99;
    EXPECT_FALSE(target.loadLive(prior_generation, reason));
    KvLiveState prior_contract = live;
    prior_contract.pins[0].payload->prior.contract_digest = digestOf(200);
    EXPECT_FALSE(target.loadLive(prior_contract, reason));
    KvLiveState prior_slot_state = live;
    prior_slot_state.pins[0].payload->prior.state = KvState::Resident;
    prior_slot_state.pins[0].payload->prior.slot_id = 0;
    prior_slot_state.pins[0].payload->prior.cached_tokens = 1;
    prior_slot_state.pins[0].payload->prior.valid_bytes = kBytesPerToken;
    EXPECT_FALSE(target.loadLive(prior_slot_state, reason));
    KvLiveState prior_enum = live;
    prior_enum.pins[0].payload->prior.state = static_cast<KvState>(99);
    EXPECT_FALSE(target.loadLive(prior_enum, reason));
    KvLiveState path_enum = live;
    path_enum.pins[0].payload->intent = static_cast<KvPath>(99);
    EXPECT_FALSE(target.loadLive(path_enum, reason));
    KvLiveState wrong_prior_absent = live;
    wrong_prior_absent.pins[0].payload->prior_absent = true;
    EXPECT_FALSE(target.loadLive(wrong_prior_absent, reason));
    KvLiveState dropped_payload = live;
    dropped_payload.pins[0].payload.reset();
    EXPECT_FALSE(target.loadLive(dropped_payload, reason));
    KvLiveState started_with_payload = live;
    started_with_payload.pins[0].phase = KvPinPhase::Started;
    EXPECT_FALSE(target.loadLive(started_with_payload, reason));
    KvLiveState bad_phase = live;
    bad_phase.pins[0].phase = static_cast<KvPinPhase>(99);
    EXPECT_FALSE(target.loadLive(bad_phase, reason));
    KvLiveState wrong_serial = live;
    wrong_serial.pins[0].payload->serial = 99;
    EXPECT_FALSE(target.loadLive(wrong_serial, reason));
    KvLiveState zero_serial = live;
    zero_serial.next_rollback_serial = 0;
    EXPECT_FALSE(target.loadLive(zero_serial, reason));
    KvLiveState zero_epoch = live;
    zero_epoch.next_kv_use_epoch = 0;
    EXPECT_FALSE(target.loadLive(zero_epoch, reason));
    EXPECT_EQ(target.recordCount(), 0u);
    EXPECT_TRUE(target.validate().empty());

    MeshKvManager started = manager;
    EXPECT_TRUE(started.validate().empty());
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

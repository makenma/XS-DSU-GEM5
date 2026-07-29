#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <optional>
#include <type_traits>
#include <utility>
#include <vector>

#ifndef UNIT_TEST
#define UNIT_TEST
#endif
#include "mem/cache/CHI/HnfSLCSF.hh"

namespace gem5::Chi
{

namespace
{

constexpr uint64_t VictimAddr = 0x80008000;
constexpr uint64_t ReplacementAddr = VictimAddr + 64;

static_assert(!std::is_copy_constructible_v<HnfSLCSFBackend>);
static_assert(!std::is_copy_assignable_v<HnfSLCSFBackend>);
static_assert(!std::is_move_constructible_v<HnfSLCSFBackend>);
static_assert(!std::is_move_assignable_v<HnfSLCSFBackend>);

std::vector<uint8_t>
lineData(uint8_t seed)
{
    std::vector<uint8_t> data(64);
    for (size_t i = 0; i < data.size(); ++i) {
        data[i] = seed + i;
    }
    return data;
}

template <class Backend, class = void>
struct ExposesDirtyVictimWritePermit : public std::false_type
{};

template <class Backend>
struct ExposesDirtyVictimWritePermit<
    Backend, std::void_t<typename Backend::DirtyVictimWritePermit>> :
    public std::true_type
{};

template <class Backend, class = void>
struct CanSnapshotDirtyVictim : public std::false_type
{};

template <class Backend>
struct CanSnapshotDirtyVictim<Backend, std::void_t<decltype(
    std::declval<Backend&>().snapshotDirtySlcVictim(
        std::declval<SlcSfVictimId>(), ReplacementAddr,
        std::declval<const typename Backend::LookupSnapshot&>()))>> :
    public std::true_type
{};

struct PublicDirtyVictimSnapshotApi
{
    using LookupSnapshot = HnfSLCSFBackend::LookupSnapshot;

    SlcSfSlcVictim snapshotDirtySlcVictim(
        SlcSfVictimId, uint64_t, const LookupSnapshot&);
};

template <class Backend, class = void>
struct CanSupplyCommitReadPreservation : public std::false_type
{};

template <class Backend>
struct CanSupplyCommitReadPreservation<Backend, std::void_t<decltype(
    std::declval<Backend&>().commitRead(
        ReplacementAddr, 1, PocqTxnKind::ReadShared,
        std::declval<const std::vector<uint8_t>&>(), false, 0,
        static_cast<const typename Backend::LookupSnapshot*>(nullptr),
        std::optional<uint32_t>{},
        static_cast<typename Backend::SeqVictim*>(nullptr),
        static_cast<const SlcSfSlcVictim*>(nullptr)))>> :
    public std::true_type
{};

template <class Backend, class = void>
struct CanSupplyFillPreservation : public std::false_type
{};

template <class Backend>
struct CanSupplyFillPreservation<Backend, std::void_t<decltype(
    std::declval<Backend&>().fillCleanShared(
        ReplacementAddr, 1,
        std::declval<const std::vector<uint8_t>&>(),
        static_cast<const typename Backend::LookupSnapshot*>(nullptr),
        std::optional<uint32_t>{},
        static_cast<typename Backend::SeqVictim*>(nullptr),
        static_cast<const SlcSfSlcVictim*>(nullptr)))>> :
    public std::true_type
{};

template <class Backend, class = void>
struct CanSupplyWritePreservation : public std::false_type
{};

template <class Backend>
struct CanSupplyWritePreservation<Backend, std::void_t<decltype(
    std::declval<Backend&>().writeLine(
        ReplacementAddr, 1,
        std::declval<const std::vector<uint8_t>&>(),
        PocqTxnKind::WriteUnique, 0,
        static_cast<const typename Backend::LookupSnapshot*>(nullptr),
        std::optional<uint32_t>{},
        static_cast<typename Backend::SeqVictim*>(nullptr),
        static_cast<const SlcSfSlcVictim*>(nullptr)))>> :
    public std::true_type
{};

template <class Backend, class = void>
struct CanSupplyFlushPreservation : public std::false_type
{};

template <class Backend>
struct CanSupplyFlushPreservation<Backend, std::void_t<decltype(
    std::declval<Backend&>().writeL3FlushSf(
        ReplacementAddr, 1,
        std::declval<const std::vector<uint8_t>&>(),
        static_cast<const typename Backend::LookupSnapshot*>(nullptr),
        static_cast<const SlcSfSlcVictim*>(nullptr)))>> :
    public std::true_type
{};

static_assert(!ExposesDirtyVictimWritePermit<HnfSLCSFBackend>::value);
static_assert(!CanSnapshotDirtyVictim<HnfSLCSFBackend>::value);
static_assert(CanSnapshotDirtyVictim<PublicDirtyVictimSnapshotApi>::value);
static_assert(!CanSupplyCommitReadPreservation<HnfSLCSFBackend>::value);
static_assert(!CanSupplyFillPreservation<HnfSLCSFBackend>::value);
static_assert(!CanSupplyWritePreservation<HnfSLCSFBackend>::value);
static_assert(!CanSupplyFlushPreservation<HnfSLCSFBackend>::value);

SlcSfCommitToken
lookupToken(HnfSLCSF& model, uint64_t req_id)
{
    SlcSfReqHeader header{};
    header.reqId = SlcSfReqId{req_id};
    header.pocEntryId = req_id;
    header.lineAddress = ReplacementAddr;
    header.requester = 1;
    SlcSfRequest request = makeSlcSfLookupReq(
        std::move(header), PocqTxnKind::ReadShared);
    EXPECT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0; cycle < 16; ++cycle) {
        model.wakeup();
        if (auto response = model.popVisibleResponse()) {
            EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
            return std::get<SlcSfLookupResponse>(response->payload()).token;
        }
    }
    ADD_FAILURE() << "lookup did not complete";
    return {};
}

SlcSfRequest
fillRequest(uint64_t req_id, const SlcSfCommitToken& token,
            const std::vector<uint8_t>& data)
{
    SlcSfReqHeader header{};
    header.reqId = SlcSfReqId{req_id};
    header.pocEntryId = req_id;
    header.lineAddress = ReplacementAddr;
    header.requester = 1;
    return makeSlcSfFillCleanSharedReq(
        std::move(header), data, {}, token, token.lookupReqId);
}

std::optional<SlcSfResponse>
complete(HnfSLCSF& model)
{
    for (size_t cycle = 0; cycle < 32; ++cycle) {
        model.wakeup();
        if (auto response = model.popVisibleResponse()) {
            return response;
        }
    }
    return std::nullopt;
}

TEST(HnfSlcSfBackendPermitTest,
     PublicSynchronousApiCannotBypassDirtyVictimPreservation)
{
    HnfSLCSFBackend backend(64, 1, 1, 1, 1);
    const auto victim_data = lineData(0x31);
    backend.writeLine(
        VictimAddr, 0, victim_data, PocqTxnKind::WriteUnique);
    const HnfSlcLookupReq replacement_lookup{
        1, RawReq{}, PocqTxnKind::ReadShared, ReplacementAddr};
    const auto target = backend.probe(replacement_lookup).snapshot;

    EXPECT_ANY_THROW(backend.fillCleanShared(
        ReplacementAddr, 1, lineData(0x41), &target));

    const auto victim = backend.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, VictimAddr});
    const auto replacement = backend.probe(replacement_lookup);
    EXPECT_TRUE(victim.result.slcHit);
    EXPECT_TRUE(victim.result.dataDirty);
    EXPECT_EQ(victim.result.data, victim_data);
    EXPECT_FALSE(replacement.result.slcHit);
}

TEST(HnfSlcSfBackendPermitTest,
     InstalledSnapshotCapabilityCommitsCompleteOwnedVictim)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const auto victim_data = lineData(0x51);
    model.writeLine(
        VictimAddr, 0, victim_data, PocqTxnKind::WriteUnique);
    const SlcSfCommitToken token = lookupToken(model, 101);
    SlcSfRequest request = fillRequest(102, token, lineData(0x61));
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    auto response = complete(model);
    ASSERT_TRUE(response.has_value());
    ASSERT_EQ(response->status(), SlcSfTerminalStatus::Done);
    const auto& fill = std::get<SlcSfFillResponse>(response->payload());
    ASSERT_TRUE(fill.slcVictim.has_value());
    EXPECT_NE(fill.slcVictim->victimId.value, 0);
    EXPECT_EQ(fill.slcVictim->lineAddress, VictimAddr);
    EXPECT_EQ(fill.slcVictim->line.data, victim_data);
    EXPECT_EQ(fill.slcVictim->line.byteMask,
              std::vector<uint8_t>(64, 0xff));
    EXPECT_TRUE(fill.slcVictim->line.dirty);
}

TEST(HnfSlcSfBackendPermitTest,
     InstalledSnapshotSealRejectsReplacementStampChangeBeforeU2)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const auto victim_data = lineData(0x71);
    model.writeLine(
        VictimAddr, 0, victim_data, PocqTxnKind::WriteUnique);
    const SlcSfCommitToken token = lookupToken(model, 201);
    SlcSfRequest request = fillRequest(202, token, lineData(0x81));
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0;
         cycle < 8 && model.mutationStageCount(
             HnfSLCSF::MutationStage::U2ArrayWrite) == 0;
         ++cycle) {
        model.wakeup();
    }
    ASSERT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    ASSERT_EQ(model.victimReservationCount(), 1);

    // A lookup touch changes only replacement order. The ordinary commit
    // token intentionally tolerates it, while the installed-snapshot seal
    // must reject it before the dirty target can be overwritten.
    HnfSLCSFBackend::LookupSnapshot touched{};
    model.lookup(HnfSlcLookupReq{
        9, RawReq{}, PocqTxnKind::ReadShared, VictimAddr}, &touched);
    const auto victim_before = model.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, VictimAddr});
    const auto replacement_before = model.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, ReplacementAddr});
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 16 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    ASSERT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(response->payload()).reason,
              SlcSfReplayReason::StaleCommitToken);

    const auto victim_after = model.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, VictimAddr});
    const auto replacement_after = model.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, ReplacementAddr});
    EXPECT_EQ(victim_after.result.slcHit, victim_before.result.slcHit);
    EXPECT_EQ(victim_after.result.slcState, victim_before.result.slcState);
    EXPECT_EQ(victim_after.result.data, victim_before.result.data);
    EXPECT_EQ(victim_after.snapshot.slc.generation,
              victim_before.snapshot.slc.generation);
    EXPECT_EQ(victim_after.snapshot.slc.replacementStamp,
              victim_before.snapshot.slc.replacementStamp);
    EXPECT_EQ(replacement_after.result.slcHit,
              replacement_before.result.slcHit);
    EXPECT_EQ(replacement_after.snapshot.slc.generation,
              replacement_before.snapshot.slc.generation);
    EXPECT_EQ(replacement_after.snapshot.slc.replacementStamp,
              replacement_before.snapshot.slc.replacementStamp);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_EQ(model.victimReservationCount(), 0);
    EXPECT_EQ(model.dirtyVictimSealCount(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.correctnessReplayCount(), 1);
}

TEST(HnfSlcSfBackendPermitTest,
     InstalledSnapshotSealRejectsReplacedVictimIdAndCleansResources)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const auto victim_data = lineData(0x91);
    model.writeLine(
        VictimAddr, 0, victim_data, PocqTxnKind::WriteUnique);
    const SlcSfCommitToken token = lookupToken(model, 301);
    SlcSfRequest request = fillRequest(302, token, lineData(0xa1));
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0;
         cycle < 8 && model.mutationStageCount(
             HnfSLCSF::MutationStage::U2ArrayWrite) == 0;
         ++cycle) {
        model.wakeup();
    }
    ASSERT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    ASSERT_EQ(model.victimReservationCount(), 1);
    ASSERT_EQ(model.dirtyVictimSealCount(), 1);
    ASSERT_TRUE(model.corruptInstalledDirtyVictimIdForTest(
        SlcSfVictimId{999}));

    auto response = complete(model);
    ASSERT_TRUE(response.has_value());
    ASSERT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(response->payload()).reason,
              SlcSfReplayReason::StaleCommitToken);
    const auto victim = model.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, VictimAddr});
    const auto replacement = model.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, ReplacementAddr});
    EXPECT_TRUE(victim.result.slcHit);
    EXPECT_EQ(victim.result.data, victim_data);
    EXPECT_FALSE(replacement.result.slcHit);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_EQ(model.victimReservationCount(), 0);
    EXPECT_EQ(model.dirtyVictimSealCount(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.correctnessReplayCount(), 1);
}

} // anonymous namespace

} // namespace gem5::Chi

#ifndef UNIT_TEST
#define UNIT_TEST
#endif

#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <limits>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "base/gtest/serialization_fixture.hh"
#include "mem/cache/CHI/HnfSLCSF.hh"

namespace gem5::Chi
{

namespace
{

constexpr uint64_t VictimAddr = 0x80008000;
constexpr uint64_t ReplacementAddr = VictimAddr + 64;

struct BackendGeometry
{
    uint32_t slcSets = 1;
    uint32_t slcWays = 1;
    uint32_t sfSets = 1;
    uint32_t sfWays = 1;
    uint32_t seqEntries = 1;
};

class HnfSlcSfBackendCheckpointTest : public SerializationFixture
{
  protected:
    static std::string serializeBackend(
        const HnfSLCSFBackend& backend, bool preserve_sf = true,
        bool preserve_slc = true)
    {
        std::ostringstream checkpoint;
        {
            Serializable::ScopedCheckpointSection section(checkpoint, "backend");
            backend.serializePersistentState(
                checkpoint, preserve_sf, preserve_slc);
        }
        return checkpoint.str();
    }

    void expectRestoreRejected(const std::string& contents, const BackendGeometry& geometry)
    {
        simulateSerialization(contents);
        HnfSLCSFBackend restored(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                                 geometry.seqEntries);
        CheckpointIn input(getDirName());
        EXPECT_ANY_THROW({
            Serializable::ScopedCheckpointSection section(input, "backend");
            restored.unserializePersistentState(input);
        });
    }
};

void
replaceCheckpointValue(std::string& checkpoint, const std::string& name, const std::string& value)
{
    const std::string key = name + "=";
    const size_t begin = checkpoint.find(key);
    ASSERT_NE(begin, std::string::npos) << name;
    ASSERT_TRUE(begin == 0 || checkpoint[begin - 1] == '\n') << name;
    const size_t value_begin = begin + key.size();
    const size_t end = checkpoint.find('\n', value_begin);
    ASSERT_NE(end, std::string::npos) << name;
    checkpoint.replace(value_begin, end - value_begin, value);
}

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

HnfSLCSFBackend::LookupObservation
probeLine(const HnfSLCSFBackend& backend, uint64_t address, uint32_t requester)
{
    HnfSlcLookupReq request{};
    request.entry = 7;
    request.req.srcid = requester;
    request.txn = PocqTxnKind::ReadShared;
    request.blockAddr = address;
    return backend.probe(request);
}

void
expectLookupObservationsEqual(const HnfSLCSFBackend::LookupObservation& actual,
                              const HnfSLCSFBackend::LookupObservation& expected)
{
    EXPECT_EQ(actual.result.valid, expected.result.valid);
    EXPECT_EQ(actual.result.entry, expected.result.entry);
    EXPECT_EQ(actual.result.slcHit, expected.result.slcHit);
    EXPECT_EQ(actual.result.sfHit, expected.result.sfHit);
    EXPECT_EQ(actual.result.replay, expected.result.replay);
    EXPECT_EQ(actual.result.mcreqNonspec, expected.result.mcreqNonspec);
    EXPECT_EQ(actual.result.snoopBroadcast, expected.result.snoopBroadcast);
    EXPECT_EQ(actual.result.snoopDirected, expected.result.snoopDirected);
    EXPECT_EQ(actual.result.snoopOpcode, expected.result.snoopOpcode);
    EXPECT_EQ(actual.result.snoopTargets, expected.result.snoopTargets);
    EXPECT_EQ(actual.result.rnfid, expected.result.rnfid);
    EXPECT_EQ(actual.result.rnfvec, expected.result.rnfvec);
    EXPECT_EQ(actual.result.slcState, expected.result.slcState);
    EXPECT_EQ(actual.result.sfState, expected.result.sfState);
    EXPECT_EQ(actual.result.dataDirty, expected.result.dataDirty);
    EXPECT_EQ(actual.result.data, expected.result.data);
    EXPECT_EQ(actual.snapshot.lookupEpoch, expected.snapshot.lookupEpoch);
    EXPECT_EQ(actual.snapshot.slc.hit, expected.snapshot.slc.hit);
    EXPECT_EQ(actual.snapshot.slc.set, expected.snapshot.slc.set);
    EXPECT_EQ(actual.snapshot.slc.way, expected.snapshot.slc.way);
    EXPECT_EQ(actual.snapshot.slc.generation, expected.snapshot.slc.generation);
    EXPECT_EQ(actual.snapshot.slc.replacementStamp, expected.snapshot.slc.replacementStamp);
    EXPECT_EQ(actual.snapshot.sf.hit, expected.snapshot.sf.hit);
    EXPECT_EQ(actual.snapshot.sf.set, expected.snapshot.sf.set);
    EXPECT_EQ(actual.snapshot.sf.way, expected.snapshot.sf.way);
    EXPECT_EQ(actual.snapshot.sf.generation, expected.snapshot.sf.generation);
    EXPECT_EQ(actual.snapshot.sf.replacementStamp, expected.snapshot.sf.replacementStamp);
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
     IncompleteLineInstallIsRejectedBeforeStateMutation)
{
    HnfSLCSFBackend backend(64, 2, 2, 2, 2);
    std::vector<uint8_t> incomplete(63, 0xa5);

    EXPECT_ANY_THROW(backend.writeLine(
        VictimAddr, 1, incomplete, PocqTxnKind::WriteUnique));
    EXPECT_FALSE(backend.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, VictimAddr}).result.slcHit);

    const auto original = lineData(0x21);
    backend.writeLine(
        VictimAddr, 1, original, PocqTxnKind::WriteUnique);
    EXPECT_ANY_THROW(backend.writeLine(
        VictimAddr, 1, std::vector<uint8_t>(4, 0xee),
        PocqTxnKind::WriteUnique));
    const auto after = backend.probe(HnfSlcLookupReq{
        1, RawReq{}, PocqTxnKind::ReadShared, VictimAddr});
    EXPECT_TRUE(after.result.slcHit);
    EXPECT_EQ(after.result.data, original);
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

TEST(HnfSlcSfBackendInvariantTest,
     RemovingVectorOwnerSelectsRemainingSharer)
{
    HnfSLCSFBackend backend(64, 1, 1, 1, 4, 1);
    const auto data = lineData(0x42);
    backend.commitRead(
        ReplacementAddr, 0, PocqTxnKind::ReadShared, data, false);
    backend.commitRead(
        ReplacementAddr, 4, PocqTxnKind::ReadShared, data, false);
    backend.commitRead(
        ReplacementAddr, 8, PocqTxnKind::ReadShared, data, false);

    backend.removeSharer(ReplacementAddr, 0);

    const auto observation = probeLine(backend, ReplacementAddr, 63);
    EXPECT_EQ(observation.result.sfState, HnfSfState::EN);
    // SrcIDs 0, 4, and 8 occupy compact directory indices 0, 1, and 2.
    EXPECT_EQ(observation.result.rnfvec, (1ULL << 1) | (1ULL << 2));
    EXPECT_EQ(observation.result.rnfid, 4);
    backend.checkGlobalInvariants();
}

TEST(HnfSlcSfBackendReplacementPolicyTest,
     InvalidWaysPrecedeGoldenPseudoRandomVictims)
{
    constexpr uint64_t Seed = 0x123456789abcdef0ULL;
    constexpr uint64_t Base = 0x90000000;
    const std::vector<uint32_t> expected = {0, 0, 1, 3, 2, 1, 3, 3};
    HnfSLCSFBackend backend(
        64, 1, 4, 1, 32, 8, "pseudo_random", Seed);

    // Empty ways are deterministic and do not consume the random stream.
    for (uint32_t way = 0; way < 4; ++way) {
        const uint64_t address = Base + way * 64;
        const auto observation = probeLine(backend, address, way + 1);
        EXPECT_FALSE(observation.result.slcHit);
        EXPECT_EQ(observation.snapshot.slc.way, way);
        backend.fillCleanShared(address, way + 1, lineData(0x10 + way));
    }

    for (size_t i = 0; i < expected.size(); ++i) {
        const uint64_t address = Base + (4 + i) * 64;
        const auto first = probeLine(backend, address, 20 + i);
        const auto second = probeLine(backend, address, 20 + i);
        expectLookupObservationsEqual(second, first);
        EXPECT_EQ(first.snapshot.slc.way, expected[i]);
        backend.fillCleanShared(address, 20 + i, lineData(0x30 + i));
    }
    backend.checkGlobalInvariants();
}

TEST(HnfSlcSfBackendReplacementPolicyTest,
     LsuAliasesLruAndSfRemainsLru)
{
    constexpr uint64_t Base = 0x91000000;
    HnfSLCSFBackend lru(64, 1, 4, 1, 4, 8, "lru", 2);
    HnfSLCSFBackend alias(64, 1, 4, 1, 4, 8, "lsu", 99);
    HnfSLCSFBackend pseudo(64, 1, 4, 1, 4, 8, "pseudo_random", 2);

    for (uint32_t way = 0; way < 4; ++way) {
        const uint64_t address = Base + way * 64;
        for (HnfSLCSFBackend* backend : {&lru, &alias, &pseudo}) {
            backend->fillCleanShared(
                address, way + 1, lineData(0x40 + way));
        }
    }
    RawReq raw{};
    raw.srcid = 1;
    const HnfSlcLookupReq touch{
        7, raw, PocqTxnKind::ReadShared, Base};
    ASSERT_NO_THROW(lru.lookup(touch));
    ASSERT_NO_THROW(alias.lookup(touch));
    ASSERT_NO_THROW(pseudo.lookup(touch));

    const uint64_t replacement = Base + 4 * 64;
    const auto lru_target = probeLine(lru, replacement, 9).snapshot;
    const auto alias_target = probeLine(alias, replacement, 9).snapshot;
    const auto pseudo_target = probeLine(pseudo, replacement, 9).snapshot;
    EXPECT_EQ(lru_target.slc.way, 1);
    EXPECT_EQ(alias_target.slc.way, lru_target.slc.way);
    EXPECT_EQ(pseudo_target.slc.way, 2);
    EXPECT_EQ(pseudo_target.sf.way, 1);

    EXPECT_THROW(
        HnfSLCSFBackend(64, 1, 4, 1, 4, 8, "not-a-policy", 1),
        std::invalid_argument);
}

TEST(HnfSlcSfBackendReplacementPolicyTest,
     SrripPromotesHitsAgesFullSetsAndChoosesDeterministically)
{
    constexpr uint64_t Base = 0x92000000;
    HnfSLCSFBackend backend(64, 1, 4, 1, 16, 8, "srrip", 7);
    HnfSLCSFBackend alias(64, 1, 4, 1, 16, 8, "rrip", 99);
    EXPECT_EQ(alias.slcReplacementPolicyKind(),
              HnfSLCSFBackend::SlcReplacementPolicy::Srrip);
    EXPECT_EQ(backend.slcCapacityLineCount(), 4);
    EXPECT_EQ(backend.slcValidLineCount(), 0);

    for (uint32_t way = 0; way < 4; ++way) {
        backend.fillCleanShared(
            Base + way * 64, way + 1, lineData(0x50 + way));
        EXPECT_EQ(backend.slcValidLineCount(), way + 1);
        EXPECT_EQ(backend.maxValidWaysInSet(), way + 1);
    }

    RawReq raw{};
    raw.srcid = 1;
    ASSERT_NO_THROW(backend.lookup(HnfSlcLookupReq{
        7, raw, PocqTxnKind::ReadShared, Base}));

    // A hit promotes way 0 to RRPV 0.  The first full-set miss ages the
    // other RRPV-2 lines to 3 and deterministically evicts way 1.
    backend.fillCleanShared(Base + 4 * 64, 5, lineData(0x60));
    EXPECT_TRUE(probeLine(backend, Base, 1).result.slcHit);
    EXPECT_FALSE(probeLine(backend, Base + 64, 1).result.slcHit);

    // Remaining RRPV-3 ways are selected before newly inserted RRPV-2 ways.
    backend.fillCleanShared(Base + 5 * 64, 6, lineData(0x61));
    EXPECT_FALSE(probeLine(backend, Base + 2 * 64, 1).result.slcHit);

    // Once no line has RRPV 3, saturating aging makes the first maximum-RRPV
    // line eligible.  Touching way 3 protects it from that replacement.
    ASSERT_NO_THROW(backend.lookup(HnfSlcLookupReq{
        7, raw, PocqTxnKind::ReadShared, Base + 3 * 64}));
    backend.fillCleanShared(Base + 6 * 64, 7, lineData(0x62));
    EXPECT_FALSE(probeLine(backend, Base + 4 * 64, 1).result.slcHit);
    EXPECT_TRUE(probeLine(backend, Base + 3 * 64, 1).result.slcHit);
    EXPECT_EQ(backend.slcValidLineCount(), 4);

    backend.flushL3(Base);
    EXPECT_EQ(backend.slcValidLineCount(), 3);
    EXPECT_EQ(backend.maxValidWaysInSet(), 3);
    backend.checkGlobalInvariants();
}

TEST_F(HnfSlcSfBackendCheckpointTest, RestoreRejectsNonBooleanPersistentFields)
{
    const BackendGeometry geometry{};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    const std::string checkpoint = serializeBackend(original);

    const std::vector<std::pair<std::string, std::string>> corruptions = {
        {"slcValid", "2"},
        {"sfValid", "2"},
        {"seqValid", "2"},
        {"seqCommittedDirty", "2"},
    };
    for (const auto& [field, value] : corruptions) {
        SCOPED_TRACE(field);
        std::string corrupted = checkpoint;
        replaceCheckpointValue(corrupted, field, value);
        expectRestoreRejected(corrupted, geometry);
    }
}

TEST_F(HnfSlcSfBackendCheckpointTest,
       ClassicCheckpointSerializesCanonicalEmptySf)
{
    const BackendGeometry geometry{};
    HnfSLCSFBackend original(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries);
    const auto data = lineData(0x29);
    original.commitRead(
        VictimAddr, 3, PocqTxnKind::ReadShared, data, false, 17);
    ASSERT_TRUE(probeLine(original, VictimAddr, 3).result.sfHit);

    const std::string checkpoint = serializeBackend(original, false);
    EXPECT_NE(checkpoint.find("slcValid=1\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("sfValid=0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("sfTag=0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("sfState=0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("sfHomeNodeId=0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("sfOwner=0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("sfSharers=0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("sfGeneration=0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("sfReplacementStamp=0\n"),
              std::string::npos);

    simulateSerialization(checkpoint);
    HnfSLCSFBackend restored(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries);
    CheckpointIn input(getDirName());
    {
        Serializable::ScopedCheckpointSection section(input, "backend");
        ASSERT_NO_THROW(restored.unserializePersistentState(input, false));
    }
    const auto observation = probeLine(restored, VictimAddr, 3);
    EXPECT_TRUE(observation.result.slcHit);
    EXPECT_FALSE(observation.result.sfHit);
    EXPECT_EQ(observation.result.data, data);
    restored.checkGlobalInvariants();
}

TEST_F(HnfSlcSfBackendCheckpointTest,
       ClassicRestoreDiscardsSfFromOlderCheckpoint)
{
    const BackendGeometry geometry{};
    HnfSLCSFBackend original(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries);
    const auto data = lineData(0x39);
    original.commitRead(
        VictimAddr, 3, PocqTxnKind::ReadShared, data, false, 17);
    const std::string checkpoint = serializeBackend(original);
    ASSERT_NE(checkpoint.find("sfValid=1\n"), std::string::npos);
    simulateSerialization(checkpoint);

    HnfSLCSFBackend restored(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries);
    CheckpointIn input(getDirName());
    {
        Serializable::ScopedCheckpointSection section(input, "backend");
        ASSERT_NO_THROW(restored.unserializePersistentState(input, false));
    }
    const auto observation = probeLine(restored, VictimAddr, 3);
    EXPECT_TRUE(observation.result.slcHit);
    EXPECT_FALSE(observation.result.sfHit);
    EXPECT_EQ(observation.result.data, data);
    restored.checkGlobalInvariants();
}

TEST_F(HnfSlcSfBackendCheckpointTest,
       FullSystemCheckpointWritesValidSlcAndSerializesEmptyArrays)
{
    BackendGeometry geometry{};
    geometry.slcWays = 2;
    geometry.sfWays = 2;
    HnfSLCSFBackend original(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries);
    const auto dirty_data = lineData(0x49);
    const auto clean_data = lineData(0x69);
    original.writeLine(
        VictimAddr, 3, dirty_data, PocqTxnKind::WriteUnique);
    original.commitRead(
        ReplacementAddr, 5, PocqTxnKind::ReadShared, clean_data, false, 19);

    std::vector<std::pair<uint64_t, std::vector<uint8_t>>> dirty;
    original.forEachDirtySlcLine(
        [&dirty](uint64_t address, const std::vector<uint8_t>& bytes) {
            dirty.emplace_back(address, bytes);
        });
    ASSERT_EQ(dirty.size(), 1);
    EXPECT_EQ(dirty[0].first, VictimAddr);
    EXPECT_EQ(dirty[0].second, dirty_data);

    std::vector<std::pair<uint64_t, std::vector<uint8_t>>> valid;
    original.forEachValidSlcLine(
        [&valid](uint64_t address, const std::vector<uint8_t>& bytes) {
            valid.emplace_back(address, bytes);
        });
    ASSERT_EQ(valid.size(), 2);
    const auto dirty_line = std::find_if(
        valid.begin(), valid.end(), [](const auto& line) {
            return line.first == VictimAddr;
        });
    const auto clean_line = std::find_if(
        valid.begin(), valid.end(), [](const auto& line) {
            return line.first == ReplacementAddr;
        });
    ASSERT_NE(dirty_line, valid.end());
    ASSERT_NE(clean_line, valid.end());
    EXPECT_EQ(dirty_line->second, dirty_data);
    EXPECT_EQ(clean_line->second, clean_data);

    const std::string checkpoint = serializeBackend(original, false, false);
    EXPECT_NE(checkpoint.find("slcValid=0 0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("slcTag=0 0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("slcState=0 0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("slcDataSize=0 0\n"), std::string::npos);
    EXPECT_NE(checkpoint.find("slcData=\n"), std::string::npos);

    simulateSerialization(checkpoint);
    HnfSLCSFBackend restored(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries);
    CheckpointIn input(getDirName());
    {
        Serializable::ScopedCheckpointSection section(input, "backend");
        ASSERT_NO_THROW(
            restored.unserializePersistentState(input, false, false));
    }
    const auto observation = probeLine(restored, VictimAddr, 3);
    EXPECT_FALSE(observation.result.slcHit);
    EXPECT_FALSE(observation.result.sfHit);
    restored.checkGlobalInvariants();
}

TEST_F(HnfSlcSfBackendCheckpointTest, RestoreRejectsSlcValidStateAndDataInconsistency)
{
    const BackendGeometry geometry{};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    original.writeLine(VictimAddr, 3, lineData(0x21), PocqTxnKind::WriteUnique);
    const std::string checkpoint = serializeBackend(original);

    const std::vector<std::pair<std::string, std::string>> corruptions = {
        {"slcValid", "0"},
        {"slcState", std::to_string(static_cast<uint32_t>(HnfSlcState::I))},
        {"slcDataSize", "63"},
    };
    for (const auto& [field, value] : corruptions) {
        SCOPED_TRACE(field);
        std::string corrupted = checkpoint;
        replaceCheckpointValue(corrupted, field, value);
        expectRestoreRejected(corrupted, geometry);
    }
}

TEST_F(HnfSlcSfBackendCheckpointTest, RestoreRejectsDuplicateSlcAndSfTags)
{
    const BackendGeometry geometry{1, 2, 1, 2, 1};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    original.commitRead(VictimAddr, 3, PocqTxnKind::ReadShared, lineData(0x31), false, 17);
    original.commitRead(ReplacementAddr, 5, PocqTxnKind::ReadShared, lineData(0x51), false, 19);
    const std::string checkpoint = serializeBackend(original);

    for (const char* field : {"slcTag", "sfTag"}) {
        SCOPED_TRACE(field);
        std::string corrupted = checkpoint;
        replaceCheckpointValue(corrupted, field, "1 1");
        expectRestoreRejected(corrupted, geometry);
    }
}

TEST_F(HnfSlcSfBackendCheckpointTest, RestoreRejectsSfSharerOwnerAndExclusiveStateViolations)
{
    const BackendGeometry geometry{};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    original.commitRead(VictimAddr, 3, PocqTxnKind::ReadShared, lineData(0x41), false, 17);
    original.commitRead(VictimAddr, 5, PocqTxnKind::ReadShared, lineData(0x41), false, 17);
    const std::string checkpoint = serializeBackend(original);

    const std::vector<std::pair<std::string, std::string>> corruptions = {
        {"sfSharers", "0"},
        {"sfOwner", "6"},
        {"sfState", std::to_string(static_cast<uint32_t>(HnfSfState::EU))},
    };
    for (const auto& [field, value] : corruptions) {
        SCOPED_TRACE(field);
        std::string corrupted = checkpoint;
        replaceCheckpointValue(corrupted, field, value);
        expectRestoreRejected(corrupted, geometry);
    }
}

TEST_F(HnfSlcSfBackendCheckpointTest, RestoreRejectsNonCanonicalDrainedSeqState)
{
    const BackendGeometry geometry{1, 1, 1, 1, 2};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    const std::string checkpoint = serializeBackend(original);

    const std::vector<std::pair<std::string, std::string>> corruptions = {
        {"seqPhase", "1 0"},
        {"seqId", "7 0"},
        {"seqAddress", "64 0"},
        {"seqHomeNodeId", "17 0"},
        {"seqState", std::to_string(static_cast<uint32_t>(HnfSfState::EU)) + " 0"},
        {"seqOwner", "3 0"},
        {"seqSharers", "3 0"},
        {"seqCompletionOpcode", "9 0"},
        {"seqCompletionTransactionId", "31 0"},
        {"seqCommittedDirty", "1 0"},
        {"seqCommittedDataSize", "1 0"},
        {"seqPending", "7"},
    };
    for (const auto& [field, value] : corruptions) {
        SCOPED_TRACE(field);
        std::string corrupted = checkpoint;
        replaceCheckpointValue(corrupted, field, value);
        expectRestoreRejected(corrupted, geometry);
    }
}

TEST_F(HnfSlcSfBackendCheckpointTest, RestoreRefusesLiveBackendOwnership)
{
    const BackendGeometry geometry{};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays,
                             geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    const std::string checkpoint = serializeBackend(original);
    simulateSerialization(checkpoint);

    HnfSLCSFBackend restored(64, geometry.slcSets, geometry.slcWays,
                             geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    const auto target = probeLine(restored, VictimAddr, 3).snapshot;
    ASSERT_TRUE(restored.tryReserveSfResources(
        7, VictimAddr, PocqTxnKind::ReadShared, &target));
    ASSERT_TRUE(restored.isBusy());

    CheckpointIn input(getDirName());
    EXPECT_ANY_THROW({
        Serializable::ScopedCheckpointSection section(input, "backend");
        restored.unserializePersistentState(input);
    });
    EXPECT_TRUE(restored.isBusy());
    EXPECT_TRUE(restored.hasSfReservation(7));
}

TEST_F(HnfSlcSfBackendCheckpointTest,
       RestoredNearMaximumLookupCountersNeverWrap)
{
    const BackendGeometry geometry{};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays,
                             geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    const std::string maximum_minus_one = std::to_string(
        std::numeric_limits<uint64_t>::max() - 1);
    const HnfSlcLookupReq request{
        7, RawReq{}, PocqTxnKind::ReadShared, VictimAddr};

    for (const char* field : {"accessCounter", "lookupAccessCount"}) {
        SCOPED_TRACE(field);
        std::string checkpoint = serializeBackend(original);
        replaceCheckpointValue(checkpoint, field, maximum_minus_one);
        simulateSerialization(checkpoint);

        HnfSLCSFBackend restored(64, geometry.slcSets, geometry.slcWays,
                                 geometry.sfSets, geometry.sfWays,
                                 geometry.seqEntries);
        CheckpointIn input(getDirName());
        {
            Serializable::ScopedCheckpointSection section(input, "backend");
            ASSERT_NO_THROW(restored.unserializePersistentState(input));
        }
        ASSERT_NO_THROW(restored.lookup(request));
        EXPECT_ANY_THROW(restored.lookup(request));
    }
}

TEST_F(HnfSlcSfBackendCheckpointTest,
       RestoredNearMaximumSeqIdentityNeverWraps)
{
    const BackendGeometry geometry{1, 1, 1, 1, 2};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays,
                             geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    std::string checkpoint = serializeBackend(original);
    replaceCheckpointValue(
        checkpoint, "nextSeqId",
        std::to_string(std::numeric_limits<uint64_t>::max() - 1));
    simulateSerialization(checkpoint);

    HnfSLCSFBackend restored(64, geometry.slcSets, geometry.slcWays,
                             geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    CheckpointIn input(getDirName());
    {
        Serializable::ScopedCheckpointSection section(input, "backend");
        ASSERT_NO_THROW(restored.unserializePersistentState(input));
    }

    restored.commitRead(
        VictimAddr, 3, PocqTxnKind::ReadUnique, lineData(0x11), false, 17);
    const auto first_target =
        probeLine(restored, ReplacementAddr, 5).snapshot;
    ASSERT_TRUE(restored.tryReserveSfResources(
        8, ReplacementAddr, PocqTxnKind::ReadUnique, &first_target));
    HnfSLCSFBackend::SeqVictim first_victim{};
    ASSERT_NO_THROW(restored.commitRead(
        ReplacementAddr, 5, PocqTxnKind::ReadUnique, lineData(0x31), false,
        17, &first_target, 8, &first_victim));
    restored.releaseSfResources(8);
    EXPECT_EQ(first_victim.id, std::numeric_limits<uint64_t>::max() - 1);
    EXPECT_EQ(restored.nextSeqIdentity(),
              std::numeric_limits<uint64_t>::max());

    const uint64_t third_address = ReplacementAddr + 64;
    const auto second_target = probeLine(restored, third_address, 7).snapshot;
    ASSERT_TRUE(restored.tryReserveSfResources(
        9, third_address, PocqTxnKind::ReadUnique, &second_target));
    EXPECT_ANY_THROW(restored.commitRead(
        third_address, 7, PocqTxnKind::ReadUnique, lineData(0x51), false, 17,
        &second_target, 9));
    EXPECT_EQ(restored.nextSeqIdentity(),
              std::numeric_limits<uint64_t>::max());
}

TEST(HnfSlcSfBackendSnapshotTest,
     AllocatorSnapshotIsOptionalAndDetectsCounterOnlyProgress)
{
    HnfSLCSFBackend backend(64, 1, 1, 1, 1);
    const HnfSlcLookupReq request{
        7, RawReq{}, PocqTxnKind::ReadShared, VictimAddr};
    const auto storage_only = backend.protectedStateSnapshot(VictimAddr);
    const auto stage_local =
        backend.protectedStateSnapshot(VictimAddr, true);

    ASSERT_NO_THROW(backend.lookup(request));
    EXPECT_EQ(backend.protectedStateSnapshot(VictimAddr), storage_only);
    EXPECT_NE(backend.protectedStateSnapshot(VictimAddr, true), stage_local);
}

TEST_F(HnfSlcSfBackendCheckpointTest,
       ExhaustedPersistentCountersRoundTripAndNeverWrap)
{
    const BackendGeometry geometry{1, 1, 1, 1, 2};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    const std::string checkpoint = serializeBackend(original);
    const std::string maximum = std::to_string(std::numeric_limits<uint64_t>::max());
    const HnfSlcLookupReq request{
        7, RawReq{}, PocqTxnKind::ReadShared, VictimAddr};

    for (const char* field : {"accessCounter", "lookupEpoch", "lookupAccessCount", "nextSeqId"}) {
        SCOPED_TRACE(field);
        std::string exhausted = checkpoint;
        replaceCheckpointValue(exhausted, field, maximum);
        simulateSerialization(exhausted);

        HnfSLCSFBackend restored(64, geometry.slcSets, geometry.slcWays,
                                 geometry.sfSets, geometry.sfWays,
                                 geometry.seqEntries);
        CheckpointIn input(getDirName());
        {
            Serializable::ScopedCheckpointSection section(input, "backend");
            ASSERT_NO_THROW(restored.unserializePersistentState(input));
        }
        ASSERT_EQ(serializeBackend(restored), exhausted);

        if (std::string(field) == "nextSeqId") {
            restored.commitRead(
                VictimAddr, 3, PocqTxnKind::ReadUnique, lineData(0x11),
                false, 17);
            const std::string durable_before = serializeBackend(restored);
            const auto target =
                probeLine(restored, ReplacementAddr, 5).snapshot;
            ASSERT_TRUE(restored.tryReserveSfResources(
                8, ReplacementAddr, PocqTxnKind::ReadUnique, &target));
            const auto state_before = restored.protectedStateSnapshot(
                ReplacementAddr, true);
            const size_t reservations_before =
                restored.sfReservationCount();
            const size_t seq_reservations_before =
                restored.seqReservationCount();
            HnfSLCSFBackend::SeqVictim victim{};

            EXPECT_ANY_THROW(restored.commitRead(
                ReplacementAddr, 5, PocqTxnKind::ReadUnique,
                lineData(0x31), false, 17, &target, 8, &victim));
            EXPECT_EQ(restored.nextSeqIdentity(),
                      std::numeric_limits<uint64_t>::max());
            EXPECT_EQ(restored.protectedStateSnapshot(
                          ReplacementAddr, true),
                      state_before);
            EXPECT_EQ(restored.sfReservationCount(), reservations_before);
            EXPECT_EQ(restored.seqReservationCount(),
                      seq_reservations_before);
            EXPECT_EQ(victim.id, 0);

            restored.releaseSfResources(8);
            EXPECT_EQ(serializeBackend(restored), durable_before);
            continue;
        }

        const std::string state_before = serializeBackend(restored);
        if (std::string(field) == "lookupEpoch") {
            EXPECT_ANY_THROW(restored.invalidateCommitTokens());
            EXPECT_EQ(restored.currentLookupEpoch(),
                      std::numeric_limits<uint64_t>::max());
        } else {
            EXPECT_ANY_THROW(restored.lookup(request));
        }
        EXPECT_EQ(serializeBackend(restored), state_before);
    }
}

TEST_F(HnfSlcSfBackendCheckpointTest,
       PseudoRandomStreamResumesExactlyAfterRestore)
{
    constexpr uint64_t Base = 0x92000000;
    constexpr uint64_t Seed = 0x123456789abcdef0ULL;
    const BackendGeometry geometry{1, 4, 1, 32, 8};
    HnfSLCSFBackend original(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries, "pseudo_random", Seed);

    for (uint32_t i = 0; i < 7; ++i) {
        original.fillCleanShared(
            Base + i * 64, i + 1, lineData(0x60 + i));
    }
    const std::string checkpoint = serializeBackend(original);
    EXPECT_NE(
        checkpoint.find("slcReplacementPolicy=1\n"), std::string::npos);
    EXPECT_NE(
        checkpoint.find("slcReplacementSeed=" + std::to_string(Seed) + "\n"),
        std::string::npos);
    EXPECT_NE(
        checkpoint.find("slcPseudoRandomState="), std::string::npos);
    simulateSerialization(checkpoint);

    HnfSLCSFBackend wrong_policy(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries, "lru", Seed);
    {
        CheckpointIn input(getDirName());
        EXPECT_ANY_THROW({
            Serializable::ScopedCheckpointSection section(input, "backend");
            wrong_policy.unserializePersistentState(input);
        });
    }

    HnfSLCSFBackend overridden_policy(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries, "srrip", 77, true);
    {
        CheckpointIn input(getDirName());
        Serializable::ScopedCheckpointSection section(input, "backend");
        ASSERT_NO_THROW(
            overridden_policy.unserializePersistentState(input));
    }
    EXPECT_EQ(overridden_policy.slcReplacementPolicyKind(),
              HnfSLCSFBackend::SlcReplacementPolicy::Srrip);
    EXPECT_EQ(overridden_policy.slcValidLineCount(),
              original.slcValidLineCount());
    for (uint32_t i = 0; i < 7; ++i) {
        const auto overridden =
            probeLine(overridden_policy, Base + i * 64, i + 1);
        const auto source = probeLine(original, Base + i * 64, i + 1);
        EXPECT_EQ(overridden.result.slcHit, source.result.slcHit);
        if (source.result.slcHit) {
            EXPECT_EQ(overridden.result.slcState, source.result.slcState);
            EXPECT_EQ(overridden.result.dataDirty, source.result.dataDirty);
            EXPECT_EQ(overridden.result.data, source.result.data);
            EXPECT_EQ(overridden.snapshot.slc.way,
                      source.snapshot.slc.way);
            EXPECT_EQ(overridden.snapshot.slc.generation,
                      source.snapshot.slc.generation);
            EXPECT_EQ(overridden.snapshot.slc.replacementStamp,
                      source.snapshot.slc.replacementStamp);
        }
    }

    // The checkpointed stream state, including its original seed, supersedes
    // the construction seed so continuation is bit-for-bit reproducible.
    HnfSLCSFBackend restored(
        64, geometry.slcSets, geometry.slcWays, geometry.sfSets,
        geometry.sfWays, geometry.seqEntries, "pseudo_random", 0);
    {
        CheckpointIn input(getDirName());
        Serializable::ScopedCheckpointSection section(input, "backend");
        ASSERT_NO_THROW(restored.unserializePersistentState(input));
    }
    EXPECT_EQ(serializeBackend(restored), checkpoint);

    for (uint32_t i = 7; i < 15; ++i) {
        const uint64_t address = Base + i * 64;
        const auto expected = probeLine(original, address, 40 + i);
        const auto actual = probeLine(restored, address, 40 + i);
        expectLookupObservationsEqual(actual, expected);
        original.fillCleanShared(
            address, 40 + i, lineData(0x60 + i));
        restored.fillCleanShared(
            address, 40 + i, lineData(0x60 + i));
    }
    EXPECT_EQ(serializeBackend(restored), serializeBackend(original));
}

TEST_F(HnfSlcSfBackendCheckpointTest, DrainedPersistentStateRoundTripsExactly)
{
    const BackendGeometry geometry{2, 2, 2, 2, 2};
    HnfSLCSFBackend original(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    original.commitRead(VictimAddr, 3, PocqTxnKind::ReadShared, lineData(0x61), true, 17);
    original.commitRead(VictimAddr, 5, PocqTxnKind::ReadShared, lineData(0x61), true, 17);
    original.fillCleanShared(ReplacementAddr, 9, lineData(0x81));
    const uint64_t unique_address = VictimAddr + 2 * 64;
    original.writeLine(unique_address, 11, lineData(0xa1), PocqTxnKind::WriteUnique);

    const std::vector<uint64_t> addresses = {VictimAddr, ReplacementAddr, unique_address};
    std::vector<HnfSLCSFBackend::LookupObservation> before;
    for (const uint64_t address : addresses) {
        before.push_back(probeLine(original, address, 3));
    }
    const std::string checkpoint = serializeBackend(original);
    simulateSerialization(checkpoint);

    HnfSLCSFBackend restored(64, geometry.slcSets, geometry.slcWays, geometry.sfSets, geometry.sfWays,
                             geometry.seqEntries);
    CheckpointIn input(getDirName());
    ASSERT_NO_THROW({
        Serializable::ScopedCheckpointSection section(input, "backend");
        restored.unserializePersistentState(input);
    });

    for (size_t i = 0; i < addresses.size(); ++i) {
        SCOPED_TRACE(addresses[i]);
        expectLookupObservationsEqual(probeLine(restored, addresses[i], 3), before[i]);
    }
    EXPECT_EQ(restored.currentLookupEpoch(), original.currentLookupEpoch());
    EXPECT_EQ(restored.currentLookupAccessCount(), original.currentLookupAccessCount());
    EXPECT_EQ(restored.nextSeqIdentity(), original.nextSeqIdentity());
    EXPECT_EQ(serializeBackend(restored), checkpoint);
}

} // anonymous namespace

} // namespace gem5::Chi

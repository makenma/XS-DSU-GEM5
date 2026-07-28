#include <gtest/gtest.h>

#include <cstdint>
#include <type_traits>
#include <variant>
#include <vector>

#include "mem/cache/CHI/HnfSLCSF.hh"
#include "mem/cache/CHI/HnfSLCSFRequest.hh"

namespace gem5::Chi
{

namespace
{

constexpr uint64_t TestAddr = 0x80004000;

HnfSlcLookupResult
lookup(HnfSLCSF& model, uint64_t addr, uint32_t requester,
       PocqTxnKind txn)
{
    HnfSlcLookupReq req{};
    req.entry = 3;
    req.blockAddr = addr;
    req.req.srcid = requester;
    req.txn = txn;
    return model.lookup(req);
}

std::vector<uint8_t>
lineData(uint8_t seed)
{
    std::vector<uint8_t> data(64);
    for (size_t i = 0; i < data.size(); ++i) {
        data[i] = seed + i;
    }
    return data;
}

} // anonymous namespace

TEST(HnfSlcSfRequestTest, AllocatesMonotonicIdsIndependentOfLinkSequence)
{
    SlcSfReqIdAllocator ids;
    RawReq raw{};
    raw.srcid = 7;
    raw.txnid = 11;
    raw.opcode = 0x12;
    raw.qos = 3;
    raw.traceTag = true;

    const auto first = makeSlcSfReqHeader(ids, 2, TestAddr, raw, 99, 101);
    const auto second = makeSlcSfReqHeader(ids, 2, TestAddr, raw, 99, 102);

    EXPECT_TRUE(first.reqId.valid());
    EXPECT_EQ(first.reqId.value + 1, second.reqId.value);
    EXPECT_NE(first.reqId.value, first.trace.linkSequence);
    EXPECT_EQ(first.pocEntryId, 2);
    EXPECT_EQ(first.lineAddress, TestAddr);
    EXPECT_EQ(first.requester, raw.srcid);
    EXPECT_EQ(first.opcode, raw.opcode);
    EXPECT_EQ(first.qos, raw.qos);
    EXPECT_EQ(first.trace.transactionId, raw.txnid);
    EXPECT_TRUE(first.trace.traceTag);
}

TEST(HnfSlcSfRequestTest, MapsEverySynchronousOperationToTypedRequest)
{
    static_assert(!std::is_same_v<SlcSfReqId, uint64_t>);
    SlcSfReqHeader header{};

    SlcSfLookupReq lookupPayload{};
    lookupPayload.header = header;
    lookupPayload.txn = PocqTxnKind::ReadShared;
    const SlcSfRequest lookup = lookupPayload;

    auto fillRequest = [&header](SlcSfUpdateKind kind, PocqTxnKind txn) {
        SlcSfFillReq req{};
        req.header = header;
        req.kind = kind;
        req.txn = txn;
        return SlcSfRequest{req};
    };
    auto updateRequest = [&header](SlcSfUpdateKind kind, PocqTxnKind txn) {
        SlcSfUpdateReq req{};
        req.header = header;
        req.kind = kind;
        req.txn = txn;
        return SlcSfRequest{req};
    };
    auto evictRequest = [&header](SlcSfUpdateKind kind) {
        SlcSfEvictReq req{};
        req.header = header;
        req.kind = kind;
        return SlcSfRequest{req};
    };

    const SlcSfRequest commitRead = fillRequest(SlcSfUpdateKind::CommitRead, PocqTxnKind::ReadShared);
    const SlcSfRequest completeMaintenance =
        updateRequest(SlcSfUpdateKind::CompleteMaintenance, PocqTxnKind::CleanInvalid);
    const SlcSfRequest removeSharer = updateRequest(SlcSfUpdateKind::RemoveSharer, PocqTxnKind::Evict);
    const SlcSfRequest writeLine = fillRequest(SlcSfUpdateKind::WriteLine, PocqTxnKind::WriteUnique);
    const SlcSfRequest flushSf = evictRequest(SlcSfUpdateKind::FlushSf);
    const SlcSfRequest flushL3 = evictRequest(SlcSfUpdateKind::FlushL3);
    const SlcSfRequest writeL3FlushSf = fillRequest(SlcSfUpdateKind::WriteL3FlushSf, PocqTxnKind::WriteUnique);
    const SlcSfRequest completeSfEvict = evictRequest(SlcSfUpdateKind::CompleteSfEvict);
    const SlcSfRequest releaseDirtyVictim = evictRequest(SlcSfUpdateKind::ReleaseDirtyVictim);

    EXPECT_TRUE(std::holds_alternative<SlcSfLookupReq>(lookup));
    EXPECT_EQ(std::get<SlcSfFillReq>(commitRead).kind, SlcSfUpdateKind::CommitRead);
    EXPECT_EQ(std::get<SlcSfUpdateReq>(completeMaintenance).kind, SlcSfUpdateKind::CompleteMaintenance);
    EXPECT_EQ(std::get<SlcSfUpdateReq>(removeSharer).kind, SlcSfUpdateKind::RemoveSharer);
    EXPECT_EQ(std::get<SlcSfFillReq>(writeLine).kind, SlcSfUpdateKind::WriteLine);
    EXPECT_EQ(std::get<SlcSfEvictReq>(flushSf).kind, SlcSfUpdateKind::FlushSf);
    EXPECT_EQ(std::get<SlcSfEvictReq>(flushL3).kind, SlcSfUpdateKind::FlushL3);
    EXPECT_EQ(std::get<SlcSfFillReq>(writeL3FlushSf).kind, SlcSfUpdateKind::WriteL3FlushSf);
    EXPECT_EQ(std::get<SlcSfEvictReq>(completeSfEvict).kind, SlcSfUpdateKind::CompleteSfEvict);
    EXPECT_EQ(std::get<SlcSfEvictReq>(releaseDirtyVictim).kind, SlcSfUpdateKind::ReleaseDirtyVictim);
}

TEST(HnfSlcSfRequestTest, PayloadsOwnLineMaskVictimAndSnoopData)
{
    std::vector<uint8_t> line = {1, 2, 3};
    std::vector<uint8_t> mask = {1, 0, 1};
    SlcSfEvictReq request{};
    request.kind = SlcSfUpdateKind::CompleteSfEvict;
    request.victim = SlcSfVictim{};
    request.victim->line.data = line;
    request.victim->line.byteMask = mask;
    request.snoopData = SlcSfSnoopData{line, mask, true};

    line[0] = 9;
    mask[0] = 0;

    EXPECT_EQ(request.victim->line.data[0], 1);
    EXPECT_EQ(request.victim->line.byteMask[0], 1);
    EXPECT_EQ(request.snoopData->data[0], 1);
    EXPECT_EQ(request.snoopData->byteMask[0], 1);
    EXPECT_TRUE(request.snoopData->dirty);
}

TEST(HnfSlcSfTest, ReadUniqueBroadcastsToOtherVectorSharers)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x10);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    model.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, false);

    const auto result = lookup(model, TestAddr, 8, PocqTxnKind::ReadUnique);
    ASSERT_TRUE(result.slcHit);
    ASSERT_TRUE(result.sfHit);
    EXPECT_EQ(result.sfState, HnfSfState::EN);
    EXPECT_TRUE(result.snoopBroadcast);
    EXPECT_FALSE(result.snoopDirected);
    EXPECT_EQ(result.snoopOpcode, 0x07);
    EXPECT_EQ(result.snoopTargets, (1ULL << 0) | (1ULL << 4));
}

TEST(HnfSlcSfTest, ReadUniqueExcludesRequesterFromBroadcast)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x20);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    model.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, false);

    const auto result = lookup(model, TestAddr, 4, PocqTxnKind::ReadUnique);
    EXPECT_TRUE(result.snoopBroadcast);
    EXPECT_EQ(result.snoopTargets, 1ULL << 0);
}

TEST(HnfSlcSfTest, ReadUniqueIsDirectedToDifferentUniqueOwner)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x30);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadUnique, data, false);
    auto result = lookup(model, TestAddr, 4, PocqTxnKind::ReadUnique);
    ASSERT_FALSE(result.slcHit);
    ASSERT_TRUE(result.sfHit);
    EXPECT_EQ(result.sfState, HnfSfState::EU);
    EXPECT_TRUE(result.snoopDirected);
    EXPECT_FALSE(result.snoopBroadcast);
    EXPECT_EQ(result.snoopTargets, 1ULL << 0);

    model.commitRead(TestAddr, 4, PocqTxnKind::ReadUnique, data, true);
    result = lookup(model, TestAddr, 4, PocqTxnKind::ReadUnique);
    EXPECT_FALSE(result.slcHit);
    EXPECT_EQ(result.sfState, HnfSfState::EU);
    EXPECT_EQ(result.rnfid, 4);
    EXPECT_EQ(result.rnfvec, 1ULL << 4);
    EXPECT_EQ(result.snoopTargets, 0);
}

TEST(HnfSlcSfTest, ReadSharedObtainsDirtyDataFromUniqueOwner)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x40);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadUnique, data, true);
    auto result = lookup(model, TestAddr, 4, PocqTxnKind::ReadShared);
    EXPECT_TRUE(result.snoopDirected);
    EXPECT_EQ(result.snoopOpcode, 0x01);
    EXPECT_EQ(result.snoopTargets, 1ULL << 0);

    model.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, true);
    result = lookup(model, TestAddr, 8, PocqTxnKind::ReadShared);
    EXPECT_TRUE(result.slcHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.sfState, HnfSfState::SN);
    EXPECT_EQ(result.rnfvec, (1ULL << 0) | (1ULL << 4));
    EXPECT_EQ(result.snoopTargets, 0);
}

TEST(HnfSlcSfTest, EvictRemovesTheRequestingSharerOnly)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x50);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    model.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, false);
    model.removeSharer(TestAddr, 0);

    const auto result = lookup(model, TestAddr, 8, PocqTxnKind::ReadUnique);
    EXPECT_TRUE(result.slcHit);
    EXPECT_TRUE(result.sfHit);
    EXPECT_EQ(result.rnfvec, 1ULL << 4);
    EXPECT_EQ(result.snoopTargets, 1ULL << 4);
}

TEST(HnfSlcSfTest, SfVictimEntersSeqAndBlocksConflictingLookups)
{
    HnfSLCSF model(64, 4, 2, 1, 1, 1);
    const uint64_t addrA = TestAddr;
    const uint64_t addrB = TestAddr + 64;
    const uint64_t addrC = TestAddr + 128;
    const auto data = lineData(0x60);

    model.commitRead(addrA, 0, PocqTxnKind::ReadUnique, data, false, 0x90);
    EXPECT_FALSE(lookup(model, addrB, 4, PocqTxnKind::ReadUnique).replay);
    model.commitRead(addrB, 4, PocqTxnKind::ReadUnique, data, false, 0x90);

    ASSERT_EQ(model.seqOccupancy(), 1);
    ASSERT_TRUE(model.hasPendingSeq());
    const auto victim = model.frontPendingSeq();
    EXPECT_EQ(victim.blockAddr, addrA);
    EXPECT_EQ(victim.homeNodeId, 0x90);
    EXPECT_EQ(victim.owner, 0);
    EXPECT_EQ(victim.sharers, 1ULL << 0);
    EXPECT_TRUE(lookup(model, addrA, 8, PocqTxnKind::ReadShared).replay);
    EXPECT_TRUE(lookup(model, addrC, 8, PocqTxnKind::ReadUnique).replay);

    model.markSeqIssued(victim.id);
    model.completeSfEvict(victim.id, {}, false);
    EXPECT_EQ(model.seqOccupancy(), 0);
    EXPECT_FALSE(lookup(model, addrC, 8, PocqTxnKind::ReadUnique).replay);
}

TEST(HnfSlcSfTest, DirtySeqSnoopDataIsPreservedInSlc)
{
    HnfSLCSF model(64, 4, 2, 1, 1, 1);
    const uint64_t addrA = TestAddr;
    const uint64_t addrB = TestAddr + 64;
    const auto oldData = lineData(0x70);
    const auto newData = lineData(0x80);

    model.commitRead(addrA, 0, PocqTxnKind::ReadUnique, oldData, false, 0x90);
    model.commitRead(addrB, 4, PocqTxnKind::ReadUnique, oldData, false, 0x90);

    const auto victim = model.frontPendingSeq();
    model.markSeqIssued(victim.id);
    model.completeSfEvict(victim.id, newData, true);

    const auto result = lookup(model, addrA, 8, PocqTxnKind::ReadShared);
    ASSERT_TRUE(result.slcHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.data, newData);
    EXPECT_FALSE(result.sfHit);
}

TEST(HnfSlcSfTest, ReservationsPreventSeqSlotOvercommit)
{
    HnfSLCSF model(64, 8, 2, 2, 1, 1);
    const uint64_t set0A = TestAddr;
    const uint64_t set1A = TestAddr + 64;
    const uint64_t set0B = TestAddr + 128;
    const uint64_t set1B = TestAddr + 192;
    const auto data = lineData(0x90);

    model.commitRead(set0A, 0, PocqTxnKind::ReadUnique, data, false, 0x90);
    model.commitRead(set1A, 4, PocqTxnKind::ReadUnique, data, false, 0x90);

    ASSERT_TRUE(model.tryReserveSfResources(
        0, set0B, PocqTxnKind::ReadUnique));
    EXPECT_TRUE(model.hasSfReservation(0));
    EXPECT_EQ(model.seqReservationCount(), 1);
    EXPECT_FALSE(model.tryReserveSfResources(
        2, set0B, PocqTxnKind::ReadUnique));
    EXPECT_FALSE(model.tryReserveSfResources(
        1, set1B, PocqTxnKind::ReadUnique));

    model.commitRead(set0B, 8, PocqTxnKind::ReadUnique, data, false, 0x90);
    model.releaseSfResources(0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    ASSERT_EQ(model.seqOccupancy(), 1);
    EXPECT_FALSE(model.tryReserveSfResources(
        1, set1B, PocqTxnKind::ReadUnique));

    const auto victim = model.frontPendingSeq();
    model.markSeqIssued(victim.id);
    model.completeSfEvict(victim.id, {}, false);
    ASSERT_TRUE(model.tryReserveSfResources(
        1, set1B, PocqTxnKind::ReadUnique));
    model.releaseSfResources(1);
}

TEST(HnfSlcSfTest, CurrentOwnerWritebackPreservesLatestData)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto oldData = lineData(0xa0);
    const auto newData = lineData(0xb0);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadUnique,
                     oldData, false);
    model.writeLine(TestAddr, 0, newData,
                    PocqTxnKind::WriteBackFull);

    const auto result =
        lookup(model, TestAddr, 4, PocqTxnKind::ReadShared);
    ASSERT_TRUE(result.slcHit);
    EXPECT_FALSE(result.sfHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.data, newData);
}

TEST(HnfSlcSfTest, StaleWritebackCannotOverwriteNewOwner)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto staleData = lineData(0xc0);
    const auto ownerData = lineData(0xd0);

    model.commitRead(TestAddr, 4, PocqTxnKind::ReadUnique,
                     ownerData, false);
    model.writeLine(TestAddr, 0, staleData,
                    PocqTxnKind::WriteBackFull);

    const auto result =
        lookup(model, TestAddr, 8, PocqTxnKind::ReadShared);
    EXPECT_FALSE(result.slcHit);
    ASSERT_TRUE(result.sfHit);
    EXPECT_EQ(result.rnfid, 4);
    EXPECT_EQ(result.snoopTargets, 1ULL << 4);
}

} // namespace gem5::Chi

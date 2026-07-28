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
    EXPECT_NE(first.reqId, second.reqId);
    EXPECT_EQ(first.trace.linkSequence, second.trace.linkSequence);
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
    static_assert(!std::is_same_v<SlcSfSeqId, SlcSfVictimId>);

    const SlcSfReqHeader header{
        SlcSfReqId{31}, 7, TestAddr, 9, 0x12, 3,
        SlcSfTraceContext{41, 43, 47, true}};
    const std::vector<uint8_t> data = {1, 2, 3};
    const std::vector<uint8_t> mask = {1, 0, 1};

    const SlcSfRequest lookup =
        makeSlcSfLookupReq(header, PocqTxnKind::ReadShared);
    const SlcSfRequest commitRead = makeSlcSfCommitReadReq(
        header, PocqTxnKind::ReadUnique, data, true, 0x90, mask);
    const SlcSfRequest fillCleanShared =
        makeSlcSfFillCleanSharedReq(header, data, mask);
    const SlcSfRequest completeMaintenance =
        makeSlcSfCompleteMaintenanceReq(
            header, PocqTxnKind::CleanInvalid, 0x91);
    const SlcSfRequest removeSharer = makeSlcSfRemoveSharerReq(header);
    const SlcSfRequest writeLine = makeSlcSfWriteLineReq(
        header, data, PocqTxnKind::WriteCleanFull, 0x92, mask);
    const SlcSfRequest flushSf = makeSlcSfFlushSfReq(header);
    const SlcSfRequest flushL3 = makeSlcSfFlushL3Req(header);
    const SlcSfRequest writeL3FlushSf =
        makeSlcSfWriteL3FlushSfReq(header, data, mask);
    const SlcSfRequest completeSfEvict = makeSlcSfCompleteSfEvictReq(
        header, SlcSfSeqId{53}, data, true, mask);
    const SlcSfRequest releaseDirtyVictim =
        makeSlcSfReleaseDirtyVictimReq(header, SlcSfVictimId{59});

    const auto& lookupReq = std::get<SlcSfLookupReq>(lookup);
    EXPECT_EQ(lookupReq.header.reqId, header.reqId);
    EXPECT_EQ(lookupReq.txn, PocqTxnKind::ReadShared);

    const auto& commitReq = std::get<SlcSfFillReq>(commitRead);
    EXPECT_EQ(commitReq.kind(), SlcSfUpdateKind::CommitRead);
    const auto& commit = std::get<SlcSfCommitRead>(commitReq.operation);
    EXPECT_EQ(commit.txn, PocqTxnKind::ReadUnique);
    EXPECT_EQ(commit.homeNodeId, 0x90);
    EXPECT_EQ(commit.line.data, data);
    EXPECT_EQ(commit.line.byteMask, mask);
    EXPECT_TRUE(commit.line.dirty);

    const auto& cleanReq = std::get<SlcSfFillReq>(fillCleanShared);
    EXPECT_EQ(cleanReq.kind(), SlcSfUpdateKind::FillCleanShared);
    const auto& clean =
        std::get<SlcSfFillCleanShared>(cleanReq.operation);
    EXPECT_EQ(clean.line.data, data);
    EXPECT_EQ(clean.line.byteMask, mask);
    EXPECT_FALSE(clean.line.dirty);

    const auto& maintenanceReq =
        std::get<SlcSfUpdateReq>(completeMaintenance);
    EXPECT_EQ(maintenanceReq.kind(), SlcSfUpdateKind::CompleteMaintenance);
    const auto& maintenance =
        std::get<SlcSfCompleteMaintenance>(maintenanceReq.operation);
    EXPECT_EQ(maintenance.txn, PocqTxnKind::CleanInvalid);
    EXPECT_EQ(maintenance.homeNodeId, 0x91);

    const auto& removeReq = std::get<SlcSfUpdateReq>(removeSharer);
    EXPECT_EQ(removeReq.kind(), SlcSfUpdateKind::RemoveSharer);
    EXPECT_TRUE(std::holds_alternative<SlcSfRemoveSharer>(
        removeReq.operation));

    const auto& writeReq = std::get<SlcSfFillReq>(writeLine);
    EXPECT_EQ(writeReq.kind(), SlcSfUpdateKind::WriteLine);
    const auto& write = std::get<SlcSfWriteLine>(writeReq.operation);
    EXPECT_EQ(write.txn, PocqTxnKind::WriteCleanFull);
    EXPECT_EQ(write.homeNodeId, 0x92);
    EXPECT_EQ(write.line.data, data);
    EXPECT_EQ(write.line.byteMask, mask);
    EXPECT_FALSE(write.line.dirty);

    EXPECT_EQ(std::get<SlcSfEvictReq>(flushSf).kind(),
              SlcSfUpdateKind::FlushSf);
    EXPECT_EQ(std::get<SlcSfEvictReq>(flushL3).kind(),
              SlcSfUpdateKind::FlushL3);

    const auto& writeFlushReq =
        std::get<SlcSfFillReq>(writeL3FlushSf);
    EXPECT_EQ(writeFlushReq.kind(), SlcSfUpdateKind::WriteL3FlushSf);
    const auto& writeFlush =
        std::get<SlcSfWriteL3FlushSf>(writeFlushReq.operation);
    EXPECT_EQ(writeFlush.line.data, data);
    EXPECT_EQ(writeFlush.line.byteMask, mask);
    EXPECT_TRUE(writeFlush.line.dirty);

    const auto& sfEvictReq = std::get<SlcSfUpdateReq>(completeSfEvict);
    EXPECT_EQ(sfEvictReq.kind(), SlcSfUpdateKind::CompleteSfEvict);
    const auto& sfEvict =
        std::get<SlcSfCompleteSfEvict>(sfEvictReq.operation);
    EXPECT_EQ(sfEvict.seqId.value, 53);
    EXPECT_EQ(sfEvict.snoopData.data, data);
    EXPECT_EQ(sfEvict.snoopData.byteMask, mask);
    EXPECT_TRUE(sfEvict.snoopData.dirty);

    const auto& releaseReq =
        std::get<SlcSfUpdateReq>(releaseDirtyVictim);
    EXPECT_EQ(releaseReq.kind(), SlcSfUpdateKind::ReleaseDirtyVictim);
    const auto& release =
        std::get<SlcSfReleaseDirtyVictim>(releaseReq.operation);
    EXPECT_EQ(release.victimId.value, 59);
}

TEST(HnfSlcSfRequestTest, PayloadsOwnLineMaskAndTypedVictimState)
{
    std::vector<uint8_t> line = {1, 2, 3};
    std::vector<uint8_t> mask = {1, 0, 1};
    SlcSfSlcVictim slcVictim{
        SlcSfVictimId{61}, TestAddr, HnfSlcState::MU, 7,
        SlcSfCacheLine{line, mask, true}};
    SlcSfSfVictim sfVictim{
        SlcSfSeqId{67}, TestAddr + 64, 0x90, HnfSfState::EU, 9,
        1ULL << 9};
    SlcSfVictim ownedSlc = slcVictim;
    SlcSfVictim ownedSf = sfVictim;
    const auto request = makeSlcSfCompleteSfEvictReq(
        SlcSfReqHeader{}, SlcSfSeqId{67}, line, true, mask);

    line[0] = 9;
    mask[0] = 0;
    slcVictim.line.data[0] = 8;

    const auto& slc = std::get<SlcSfSlcVictim>(ownedSlc);
    const auto& sf = std::get<SlcSfSfVictim>(ownedSf);
    EXPECT_EQ(slc.victimId.value, 61);
    EXPECT_EQ(slc.state, HnfSlcState::MU);
    EXPECT_EQ(slc.line.data[0], 1);
    EXPECT_EQ(slc.line.byteMask[0], 1);
    EXPECT_EQ(sf.seqId.value, 67);
    EXPECT_EQ(sf.state, HnfSfState::EU);
    const auto& completion =
        std::get<SlcSfCompleteSfEvict>(request.operation);
    EXPECT_EQ(completion.snoopData.data[0], 1);
    EXPECT_EQ(completion.snoopData.byteMask[0], 1);
    EXPECT_TRUE(completion.snoopData.dirty);
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

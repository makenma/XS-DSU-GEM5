#include <gtest/gtest.h>

#include <cstdint>
#include <sstream>
#include <string>
#include <vector>

#include "base/gtest/serialization_fixture.hh"
#include "mem/cache/CHI/HnfCoherencyController.hh"
#include "mem/cache/CHI/HnfSLCSF.hh"

namespace gem5::Chi
{

namespace
{

constexpr uint32_t BlockSize = 64;
constexpr uint32_t BeatSize = 32;
constexpr uint32_t HnfNode = 0x90;
constexpr uint32_t SnNode = 0x80;
constexpr uint64_t TestAddr = 0x80004040;

class HnfCcCheckpointTest : public SerializationFixture
{};

std::vector<uint8_t>
lineData(uint8_t seed)
{
    std::vector<uint8_t> data(BlockSize);
    for (size_t i = 0; i < data.size(); ++i) {
        data[i] = seed + i;
    }
    return data;
}

HnfLinkToCcReq
makeRead(uint32_t entry, uint64_t seq, uint32_t requester,
         uint32_t txnid, uint8_t opcode, uint64_t addr = TestAddr)
{
    HnfLinkToCcReq in{};
    in.valid = true;
    in.seq = seq;
    in.tokenId = entry;
    in.req.srcid = requester;
    in.req.tgtid = HnfNode;
    in.req.txnid = txnid;
    in.req.opcode = opcode;
    in.req.addr = addr;
    in.req.size = BlockSize;
    return in;
}

RawDat
makeCompData(uint32_t mc_txnid, uint32_t offset, bool last,
             const std::vector<uint8_t>& data)
{
    RawDat dat{};
    dat.srcid = SnNode;
    dat.tgtid = HnfNode;
    dat.txnid = mc_txnid;
    dat.opcode = 0x04;
    dat.last = last;
    dat.beatOffset = offset;
    dat.data.assign(data.begin() + offset,
                    data.begin() + offset + BeatSize);
    return dat;
}

RawRsp
makeRsp(uint32_t requester, uint32_t txnid, uint8_t opcode)
{
    RawRsp rsp{};
    rsp.srcid = requester;
    rsp.tgtid = HnfNode;
    rsp.txnid = txnid;
    rsp.opcode = opcode;
    return rsp;
}

RawRsp
makeDirtyVictimRsp(const HnfCcTxReq& writeback, uint8_t opcode,
                   uint8_t dbid = 0, uint8_t pcrdtype = 0,
                   uint8_t resp_err = 0)
{
    RawRsp rsp = makeRsp(SnNode, writeback.req.txnid, opcode);
    rsp.tgtid = writeback.req.srcid;
    rsp.dbid = dbid;
    rsp.pcrdtype = pcrdtype;
    rsp.respErr = resp_err;
    return rsp;
}

void
acceptDirtyVictimDbid(HnfCoherencyController& cc,
                      const HnfCcTxReq& writeback, uint8_t dbid)
{
    ASSERT_NE(dbid, 0);
    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(
            writeback, 0x06, dbid, writeback.req.pcrdtype)));
}

RawDat
makeWriteData(uint32_t requester, uint32_t txnid, uint32_t offset,
              bool last, uint8_t opcode, const std::vector<uint8_t>& data)
{
    RawDat dat{};
    dat.srcid = requester;
    dat.tgtid = HnfNode;
    dat.txnid = txnid;
    dat.opcode = opcode;
    dat.last = last;
    dat.beatOffset = offset;
    dat.data.assign(data.begin() + offset,
                    data.begin() + offset + BeatSize);
    return dat;
}

HnfSlcLookupResult
lookup(HnfSLCSF& slcsf, uint32_t requester, PocqTxnKind txn)
{
    HnfSlcLookupReq req{};
    req.req.srcid = requester;
    req.blockAddr = TestAddr;
    req.txn = txn;
    return slcsf.lookup(req);
}

void
pumpOnce(HnfCoherencyController& cc, HnfSLCSF& slcsf, Tick& tick)
{
    slcsf.wakeup(++tick);
    cc.serviceInternalWork(tick);
}

void
pumpUpdate(HnfCoherencyController& cc, HnfSLCSF& slcsf,
           uint32_t entry, Tick& tick)
{
    for (size_t i = 0; i < 32; ++i) {
        if (cc.slcUpdatePhase(entry) ==
            HnfCoherencyController::SlcUpdatePhase::None) {
            return;
        }
        pumpOnce(cc, slcsf, tick);
    }
    FAIL() << "SLCSF update did not complete";
}

void
pumpLookup(HnfCoherencyController& cc, HnfSLCSF& slcsf,
           uint32_t entry, Tick& tick)
{
    for (size_t i = 0; i < 32; ++i) {
        if (cc.slcLookupPhase(entry) ==
            HnfCoherencyController::SlcLookupPhase::None) {
            return;
        }
        pumpOnce(cc, slcsf, tick);
    }
    FAIL() << "SLCSF lookup did not complete";
}

void
pumpSeqCompletion(HnfCoherencyController& cc, HnfSLCSF& slcsf, Tick& tick)
{
    for (size_t i = 0; i < 32; ++i) {
        if (!cc.hasActiveSeqPocq()) {
            return;
        }
        pumpOnce(cc, slcsf, tick);
    }
    FAIL() << "SLCSF SEQ completion did not complete";
}

void
reachDirtyVictimWriteback(HnfCoherencyController& cc, HnfSLCSF& slcsf,
                          Tick& tick, const std::vector<uint8_t>& fill_data,
                          uint64_t replacement_addr = TestAddr + BlockSize,
                          uint32_t requester_txnid = 121)
{
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 8001, 4, requester_txnid, 0x01,
                 replacement_addr), tick).accepted);
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxReq());
    const HnfCcTxReq read = cc.frontTxReq();
    ASSERT_FALSE(read.dirtyVictimId.has_value());
    EXPECT_FALSE(read.req.hnfDirtyVictim);
    EXPECT_EQ(read.entry, 0);
    cc.popTxReq();
    cc.notifyTxReqSent(read);

    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, 0, false, fill_data)));
    EXPECT_FALSE(cc.acceptRxDat(
        makeCompData(1, BeatSize, true, fill_data)));
    for (size_t i = 0; i < 32 && !cc.hasTxReq(); ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    ASSERT_TRUE(cc.hasTxReq());
    ASSERT_TRUE(cc.frontTxReq().dirtyVictimId.has_value());
}

} // anonymous namespace

TEST(HnfCoherencyControllerTest, DirtyVictimDataSurvivesWriteback)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4, false);
    cc.setSlcsf(&slcsf);

    const auto victim_data = lineData(0x91);
    const auto fill_data = lineData(0xa1);
    slcsf.writeLine(
        TestAddr, 0, victim_data, PocqTxnKind::WriteUnique, HnfNode);

    Tick tick = 1000;
    reachDirtyVictimWriteback(cc, slcsf, tick, fill_data);
    const HnfCcTxReq writeback = cc.frontTxReq();
    const SlcSfVictimId victim_id{*writeback.dirtyVictimId};
    EXPECT_EQ(writeback.entry, UINT32_MAX);
    EXPECT_EQ(writeback.req.opcode, 0x5c);
    EXPECT_EQ(writeback.req.addr, TestAddr);
    EXPECT_EQ(writeback.req.AllowRetry, 0);
    EXPECT_EQ(writeback.req.srcid, HnfNode);
    EXPECT_TRUE(writeback.req.hnfDirtyVictim);
    EXPECT_EQ(writeback.req.tgtid, SnNode);
    EXPECT_NE(writeback.req.txnid, 1);
    EXPECT_NE(writeback.req.txnid, 121);
    const auto mapped_victim = cc.dirtyVictimForTxn(writeback.req.txnid);
    ASSERT_TRUE(mapped_victim.has_value());
    EXPECT_EQ(mapped_victim->value, victim_id.value);
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::HandedOff);
    EXPECT_FALSE(cc.hasTxDat());

    cc.popTxReq();
    cc.notifyTxReqSent(writeback);
    EXPECT_FALSE(cc.hasTxDat());
    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x04, 9)));
    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x06, 0)));
    EXPECT_FALSE(cc.hasTxDat());
    acceptDirtyVictimDbid(cc, writeback, 9);
    ASSERT_TRUE(cc.hasTxDat());
    const HnfCcTxDat data = cc.frontTxDat();
    EXPECT_EQ(data.entry, UINT32_MAX);
    ASSERT_TRUE(data.dirtyVictimId.has_value());
    EXPECT_EQ(*data.dirtyVictimId, victim_id.value);
    EXPECT_EQ(data.dat.opcode, 0x03);
    EXPECT_EQ(data.dat.txnid, writeback.req.txnid);
    EXPECT_EQ(data.dat.tgtid, SnNode);
    EXPECT_EQ(data.dat.dbid, 9);
    EXPECT_EQ(data.dat.beatOffset, 0);
    EXPECT_TRUE(data.dat.last);
    EXPECT_EQ(data.dat.data.size(), BlockSize);
    EXPECT_EQ(data.dat.data, victim_data);
    cc.popTxDat();
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::WritebackIssued);

    // The requester continues from the latched Fill response on its own token.
    cc.serviceInternalWork(tick);
    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_FALSE(cc.frontTxDat().dirtyVictimId.has_value());
    EXPECT_EQ(cc.frontTxDat().entry, 0);
    cc.popTxDat();
    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_FALSE(cc.frontTxDat().dirtyVictimId.has_value());
    cc.popTxDat();
    const auto retired = cc.acceptRxRsp(makeRsp(4, 121, 0x02));
    ASSERT_TRUE(retired.has_value());
    EXPECT_EQ(retired->tokenId, 0);
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::WritebackIssued);
}

TEST(HnfCoherencyControllerTest,
     DirtyVictimDownstreamTxnIdDiffersFromRequesterOnAllocatorCollision)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4, false);
    cc.setSlcsf(&slcsf);

    slcsf.writeLine(
        TestAddr, 0, lineData(0xa8), PocqTxnKind::WriteUnique, HnfNode);
    constexpr uint32_t CollidingRequesterTxnId = 0x40000000U;
    Tick tick = 1050;
    reachDirtyVictimWriteback(
        cc, slcsf, tick, lineData(0xa9), TestAddr + BlockSize,
        CollidingRequesterTxnId);

    ASSERT_TRUE(cc.hasTxReq());
    const HnfCcTxReq writeback = cc.frontTxReq();
    ASSERT_TRUE(writeback.dirtyVictimId.has_value());
    EXPECT_NE(writeback.req.txnid, CollidingRequesterTxnId);
    EXPECT_EQ(cc.dirtyVictimForTxn(writeback.req.txnid)->value,
              *writeback.dirtyVictimId);
}

TEST(HnfCoherencyControllerTest, DirtyVictimTxBackpressureMakesProgress)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto victim_data = lineData(0xb1);
    slcsf.writeLine(
        TestAddr, 0, victim_data, PocqTxnKind::WriteUnique, HnfNode);
    Tick tick = 1100;
    reachDirtyVictimWriteback(cc, slcsf, tick, lineData(0xc1));
    const HnfCcTxReq blocked_req = cc.frontTxReq();
    const auto victim_id = *blocked_req.dirtyVictimId;

    for (size_t i = 0; i < 4; ++i) {
        pumpOnce(cc, slcsf, tick);
        ASSERT_TRUE(cc.hasTxReq());
        EXPECT_EQ(cc.frontTxReq().req.txnid, blocked_req.req.txnid);
        EXPECT_EQ(cc.frontTxReq().dirtyVictimId, victim_id);
        EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    }

    cc.popTxReq();
    cc.notifyTxReqSent(blocked_req);
    while (cc.hasTxDat() &&
           !cc.frontTxDat().dirtyVictimId.has_value()) {
        cc.popTxDat();
    }
    EXPECT_FALSE(cc.hasTxDat());
    acceptDirtyVictimDbid(cc, blocked_req, 10);
    while (cc.hasTxDat() &&
           !cc.frontTxDat().dirtyVictimId.has_value()) {
        cc.popTxDat();
    }
    ASSERT_TRUE(cc.hasTxDat());
    const HnfCcTxDat blocked_data = cc.frontTxDat();
    ASSERT_EQ(blocked_data.dirtyVictimId, victim_id);
    for (size_t i = 0; i < 4; ++i) {
        pumpOnce(cc, slcsf, tick);
        ASSERT_TRUE(cc.hasTxDat());
        EXPECT_EQ(cc.frontTxDat().dirtyVictimId, victim_id);
        EXPECT_EQ(cc.frontTxDat().dat.data, victim_data);
        EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    }
    cc.popTxDat();
    if (cc.hasTxDat()) {
        EXPECT_FALSE(cc.frontTxDat().dirtyVictimId.has_value());
    }
    EXPECT_EQ(slcsf.dirtyVictimState(SlcSfVictimId{victim_id}),
              HnfSLCSF::VictimState::WritebackIssued);
}

TEST(HnfCoherencyControllerTest,
     DirtyVictimRetryWaitsForExactPcrdGrantAndReissuesSameTxn)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4, true);
    cc.setSlcsf(&slcsf);

    const auto victim_data = lineData(0xc8);
    slcsf.writeLine(
        TestAddr, 0, victim_data, PocqTxnKind::WriteUnique, HnfNode);
    Tick tick = 1150;
    reachDirtyVictimWriteback(cc, slcsf, tick, lineData(0xc9));
    const HnfCcTxReq initial = cc.frontTxReq();
    const SlcSfVictimId victim_id{*initial.dirtyVictimId};
    ASSERT_EQ(initial.req.AllowRetry, 1);
    EXPECT_TRUE(initial.req.hnfDirtyVictim);
    cc.popTxReq();
    cc.notifyTxReqSent(initial);

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(initial, 0x03, 0, 7)));
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::RetryPending);
    EXPECT_FALSE(cc.hasTxReq());
    EXPECT_FALSE(cc.hasTxDat());

    RawRsp wrongGrant = makeDirtyVictimRsp(initial, 0x07, 0, 7);
    wrongGrant.tgtid ^= 1;
    EXPECT_FALSE(cc.acceptRxRsp(wrongGrant));
    EXPECT_FALSE(cc.hasTxReq());
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::RetryPending);

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(initial, 0x07, 0, 7)));
    ASSERT_TRUE(cc.hasTxReq());
    const HnfCcTxReq retried = cc.frontTxReq();
    EXPECT_EQ(retried.dirtyVictimId, initial.dirtyVictimId);
    EXPECT_EQ(retried.req.txnid, initial.req.txnid);
    EXPECT_EQ(retried.req.AllowRetry, 0);
    EXPECT_EQ(retried.req.pcrdtype, 7);
    EXPECT_TRUE(retried.req.hnfDirtyVictim);
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);

    for (size_t i = 0; i < 3; ++i) {
        pumpOnce(cc, slcsf, tick);
        ASSERT_TRUE(cc.hasTxReq());
        EXPECT_EQ(cc.frontTxReq().req.txnid, initial.req.txnid);
        EXPECT_EQ(cc.frontTxReq().dirtyVictimId, initial.dirtyVictimId);
    }
    cc.popTxReq();
    cc.notifyTxReqSent(retried);

    // A no-retry reissue cannot enter a second RetryAck/PCrdGrant round.
    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(retried, 0x03, 0, 7)));
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::WaitDbid);
    EXPECT_FALSE(cc.hasTxReq());

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(retried, 0x0e, 21, 7)));
    while (cc.hasTxDat() &&
           !cc.frontTxDat().dirtyVictimId.has_value()) {
        cc.popTxDat();
    }
    ASSERT_TRUE(cc.hasTxDat());
    const HnfCcTxDat data = cc.frontTxDat();
    ASSERT_EQ(data.dirtyVictimId, initial.dirtyVictimId);
    EXPECT_EQ(data.dat.txnid, initial.req.txnid);
    EXPECT_EQ(data.dat.dbid, 21);
    EXPECT_EQ(data.dat.data, victim_data);
    cc.popTxDat();
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::WaitComp);

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(retried, 0x04, 21, 7)));
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    EXPECT_TRUE(cc.dirtyVictimForTxn(initial.req.txnid).has_value());
}

TEST(HnfCoherencyControllerTest,
     DirtyVictimCompDbidWaitsForAcceptedDataAndQueuesItOnce)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto victim_data = lineData(0xca);
    slcsf.writeLine(
        TestAddr, 0, victim_data, PocqTxnKind::WriteUnique, HnfNode);
    Tick tick = 1175;
    reachDirtyVictimWriteback(cc, slcsf, tick, lineData(0xcb));
    const HnfCcTxReq writeback = cc.frontTxReq();
    const SlcSfVictimId victim_id{*writeback.dirtyVictimId};
    cc.popTxReq();
    cc.notifyTxReqSent(writeback);

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x05, 22)));
    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::DataQueued);
    for (size_t i = 0; i < 4; ++i) {
        pumpOnce(cc, slcsf, tick);
        ASSERT_TRUE(cc.hasTxDat());
        ASSERT_EQ(cc.frontTxDat().dirtyVictimId,
                  writeback.dirtyVictimId);
        EXPECT_EQ(cc.frontTxDat().dat.dbid, 22);
        EXPECT_EQ(cc.frontTxDat().dat.data, victim_data);
        EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    }

    // A duplicate response cannot replace the assigned DBID or queue data.
    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x06, 23)));
    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.frontTxDat().dat.dbid, 22);
    cc.popTxDat();
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::ReleaseWaiting);
    const SlcSfReqId release_id = cc.dirtyVictimReleaseReqId(victim_id);
    ASSERT_TRUE(release_id.valid());

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x04, 22)));
    EXPECT_EQ(cc.dirtyVictimReleaseReqId(victim_id), release_id);
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
}

TEST(HnfCoherencyControllerTest,
     DirtyVictimDownstreamErrorHoldsOwnerWithoutRelease)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    slcsf.writeLine(
        TestAddr, 0, lineData(0xcc),
        PocqTxnKind::WriteUnique, HnfNode);
    Tick tick = 1190;
    reachDirtyVictimWriteback(cc, slcsf, tick, lineData(0xcd));
    const HnfCcTxReq writeback = cc.frontTxReq();
    const SlcSfVictimId victim_id{*writeback.dirtyVictimId};
    cc.popTxReq();
    cc.notifyTxReqSent(writeback);
    acceptDirtyVictimDbid(cc, writeback, 24);
    ASSERT_TRUE(cc.hasTxDat());
    cc.popTxDat();

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x04, 24, 0, 1)));
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::DownstreamErrorHeld);
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    EXPECT_TRUE(cc.dirtyVictimForTxn(writeback.req.txnid).has_value());
    EXPECT_FALSE(cc.dirtyVictimReleaseReqId(victim_id).valid());
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::WritebackIssued);
    EXPECT_TRUE(cc.hasWork());

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x04, 24)));
    for (size_t i = 0; i < 4; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::DownstreamErrorHeld);
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    EXPECT_EQ(slcsf.victimBufferOccupancy(), 1);
}

TEST(HnfCoherencyControllerTest, DirtyVictimCompletionMatchesVictimId)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    slcsf.writeLine(
        TestAddr, 0, lineData(0xd1), PocqTxnKind::WriteUnique, HnfNode);
    Tick tick = 1200;
    reachDirtyVictimWriteback(cc, slcsf, tick, lineData(0xe1));
    const HnfCcTxReq writeback = cc.frontTxReq();
    const SlcSfVictimId victim_id{*writeback.dirtyVictimId};
    const uint32_t downstream_txn = writeback.req.txnid;
    cc.popTxReq();
    cc.notifyTxReqSent(writeback);
    acceptDirtyVictimDbid(cc, writeback, 11);
    ASSERT_TRUE(cc.hasTxDat());
    cc.popTxDat();

    RawRsp unknown = makeRsp(SnNode, downstream_txn + 1, 0x04);
    unknown.dbid = 11;
    EXPECT_FALSE(cc.acceptRxRsp(unknown));
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    const auto mapped_victim = cc.dirtyVictimForTxn(downstream_txn);
    ASSERT_TRUE(mapped_victim.has_value());
    EXPECT_EQ(mapped_victim->value, victim_id.value);

    RawRsp wrongSource = makeDirtyVictimRsp(writeback, 0x04, 11);
    wrongSource.srcid ^= 1;
    EXPECT_FALSE(cc.acceptRxRsp(wrongSource));
    RawRsp wrongTarget = makeDirtyVictimRsp(writeback, 0x04, 11);
    wrongTarget.tgtid ^= 1;
    EXPECT_FALSE(cc.acceptRxRsp(wrongTarget));
    RawRsp wrongDbid = makeDirtyVictimRsp(writeback, 0x04, 17, 0, 1);
    EXPECT_FALSE(cc.acceptRxRsp(wrongDbid));
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::WaitComp);
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::WritebackIssued);

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x04, 11)));
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);
    EXPECT_TRUE(cc.dirtyVictimForTxn(downstream_txn).has_value());
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::WritebackIssued);
    EXPECT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::ReleaseWaiting);

    for (size_t i = 0;
         i < 16 && cc.dirtyVictimTransactionCount() != 0; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 0);
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::Released);
}

TEST(HnfCoherencyControllerTest, ReleaseDirtyVictimFreesExactlyOneEntry)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    config.respQueueEntries = 2;
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    slcsf.writeLine(
        TestAddr, 0, lineData(0xe2), PocqTxnKind::WriteUnique, HnfNode);
    Tick tick = 1300;
    reachDirtyVictimWriteback(cc, slcsf, tick, lineData(0xe3));
    const HnfCcTxReq writeback = cc.frontTxReq();
    const SlcSfVictimId victim_id{*writeback.dirtyVictimId};
    cc.popTxReq();
    cc.notifyTxReqSent(writeback);
    acceptDirtyVictimDbid(cc, writeback, 12);
    ASSERT_TRUE(cc.hasTxDat());
    cc.popTxDat();

    // Occupy the one-entry request queue with a real CC lookup so the first
    // release attempt must retain ownership and retry with the same ID.
    ASSERT_TRUE(cc.acceptLinkReq(makeRead(
        1, 8002, 5, 122, 0x01, TestAddr + 2 * BlockSize), tick).accepted);
    ASSERT_EQ(cc.slcLookupPhase(1),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x04, 12)));
    ASSERT_EQ(cc.dirtyVictimPhase(victim_id),
              HnfCoherencyController::DirtyVictimPhase::ReleaseIssuePending);
    const SlcSfReqId release_id = cc.dirtyVictimReleaseReqId(victim_id);
    ASSERT_TRUE(release_id.valid());
    EXPECT_EQ(slcsf.victimBufferOccupancy(), 1);

    bool saw_committed_await_ack = false;
    for (size_t i = 0;
         i < 32 && cc.dirtyVictimTransactionCount() != 0; ++i) {
        pumpOnce(cc, slcsf, tick);
        if (cc.dirtyVictimTransactionCount() != 0) {
            EXPECT_EQ(cc.dirtyVictimReleaseReqId(victim_id), release_id);
            if (slcsf.dirtyVictimState(victim_id) ==
                HnfSLCSF::VictimState::ReleaseCommittedAwaitAck) {
                // U2 retains ownership until the CC acknowledges the exact
                // durable terminal response.
                EXPECT_GT(slcsf.respOccupied(), 0);
                EXPECT_EQ(slcsf.victimBufferOccupancy(), 1);
                saw_committed_await_ack = true;
            } else {
                EXPECT_EQ(slcsf.victimBufferOccupancy(), 1);
            }
        }
    }
    EXPECT_TRUE(saw_committed_await_ack);
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 0);
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::Released);
    EXPECT_EQ(slcsf.victimBufferOccupancy(), 0);
}

TEST(HnfCoherencyControllerTest,
     ForcedSmallSlcDirtyEvictionCompletesAndReleases)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto victim_data = lineData(0xf1);
    const auto fill_data = lineData(0xf2);
    slcsf.writeLine(
        TestAddr, 0, victim_data, PocqTxnKind::WriteUnique, HnfNode);
    Tick tick = 1400;
    EXPECT_EQ(cc.pendingDirtyVictimRequesterCount(), 0);
    reachDirtyVictimWriteback(cc, slcsf, tick, fill_data);
    EXPECT_EQ(cc.pendingDirtyVictimRequesterCount(), 1);
    const HnfCcTxReq writeback = cc.frontTxReq();
    const SlcSfVictimId victim_id{*writeback.dirtyVictimId};
    EXPECT_EQ(writeback.req.opcode, 0x5c);
    EXPECT_EQ(writeback.req.addr, TestAddr);
    cc.popTxReq();
    cc.notifyTxReqSent(writeback);
    acceptDirtyVictimDbid(cc, writeback, 13);
    ASSERT_TRUE(cc.hasTxDat());
    const HnfCcTxDat write_data = cc.frontTxDat();
    EXPECT_EQ(write_data.dat.data, victim_data);
    cc.popTxDat();

    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x04, 13)));
    for (size_t i = 0;
         i < 32 && cc.dirtyVictimTransactionCount() != 0; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 0);
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::Released);
    EXPECT_EQ(cc.pendingDirtyVictimRequesterCount(), 1);

    // The requester independently reaches normal completion.
    cc.serviceInternalWork(tick);
    size_t requester_beats = 0;
    while (cc.hasTxDat()) {
        EXPECT_FALSE(cc.frontTxDat().dirtyVictimId.has_value());
        ++requester_beats;
        cc.popTxDat();
    }
    EXPECT_EQ(requester_beats, 2);
    const auto retired = cc.acceptRxRsp(makeRsp(4, 121, 0x02));
    ASSERT_TRUE(retired.has_value());
    EXPECT_EQ(retired->tokenId, 0);
    EXPECT_EQ(cc.pendingDirtyVictimRequesterCount(), 0);
}

TEST(HnfCoherencyControllerTest,
     DirtyVictimRequesterMayRetireBeforeVictimRelease)
{
    HnfSLCSF slcsf(BlockSize, 1, 1, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    slcsf.writeLine(
        TestAddr, 0, lineData(0xf4),
        PocqTxnKind::WriteUnique, HnfNode);
    Tick tick = 1450;
    reachDirtyVictimWriteback(
        cc, slcsf, tick, lineData(0xf5), TestAddr + BlockSize, 125);
    const HnfCcTxReq writeback = cc.frontTxReq();
    const SlcSfVictimId victim_id{*writeback.dirtyVictimId};
    EXPECT_EQ(cc.pendingDirtyVictimRequesterCount(), 1);

    // Requester completion is independent of the downstream writeback.  It
    // may retire while that writeback is still waiting for a DBID.
    cc.serviceInternalWork(tick);
    size_t requester_beats = 0;
    while (cc.hasTxDat()) {
        EXPECT_FALSE(cc.frontTxDat().dirtyVictimId.has_value());
        ++requester_beats;
        cc.popTxDat();
    }
    EXPECT_EQ(requester_beats, 2);
    const auto retired = cc.acceptRxRsp(makeRsp(4, 125, 0x02));
    ASSERT_TRUE(retired.has_value());
    EXPECT_EQ(cc.pendingDirtyVictimRequesterCount(), 0);
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 1);

    cc.popTxReq();
    cc.notifyTxReqSent(writeback);
    acceptDirtyVictimDbid(cc, writeback, 14);
    ASSERT_TRUE(cc.hasTxDat());
    ASSERT_EQ(cc.frontTxDat().dirtyVictimId,
              writeback.dirtyVictimId);
    cc.popTxDat();
    EXPECT_FALSE(cc.acceptRxRsp(
        makeDirtyVictimRsp(writeback, 0x04, 14)));
    for (size_t i = 0;
         i < 32 && cc.dirtyVictimTransactionCount() != 0; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    EXPECT_EQ(cc.dirtyVictimTransactionCount(), 0);
    EXPECT_EQ(slcsf.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::Released);
    EXPECT_EQ(cc.pendingDirtyVictimRequesterCount(), 0);
}

TEST(HnfCoherencyControllerTest, RetireWakesOneSameAddressSleeper)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto first = cc.acceptLinkReq(makeRead(0, 1, 0, 11, 0x01), 0);
    const auto second = cc.acceptLinkReq(makeRead(1, 2, 4, 12, 0x01), 1);
    ASSERT_TRUE(first.accepted);
    ASSERT_TRUE(second.accepted);
    EXPECT_FALSE(cc.hasTxReq());
    Tick tick = 0;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxReq());

    EXPECT_EQ(cc.frontTxReq().entry, 0);
    cc.popTxReq();
    cc.notifyTxReqSent(0);

    const auto data = lineData(0x10);
    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, 0, false, data)));
    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, BeatSize, true, data)));
    pumpUpdate(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxDat());
    cc.popTxDat();
    ASSERT_TRUE(cc.hasTxDat());
    cc.popTxDat();

    const auto retired = cc.acceptRxRsp(makeRsp(0, 11, 0x02));
    ASSERT_TRUE(retired);
    EXPECT_EQ(retired->tokenId, 0);

    pumpLookup(cc, slcsf, 1, tick);
    pumpUpdate(cc, slcsf, 1, tick);
    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.frontTxDat().entry, 1);
}

TEST(HnfCoherencyControllerTest, ReadUniqueSnoopsAllOtherSharers)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto data = lineData(0x40);
    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    slcsf.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, false);

    const auto admitted =
        cc.acceptLinkReq(makeRead(0, 1, 8, 21, 0x07), 0);
    ASSERT_TRUE(admitted.accepted);
    EXPECT_FALSE(cc.hasTxSnp());
    Tick tick = 0;
    pumpLookup(cc, slcsf, 0, tick);

    ASSERT_TRUE(cc.hasTxSnp());
    const HnfCcTxSnp first = cc.frontTxSnp();
    EXPECT_EQ(first.targetNode, 0);
    EXPECT_EQ(first.snp.tgtid, 1);
    EXPECT_EQ(first.snp.opcode, 0x07);
    EXPECT_EQ(first.snp.addr, TestAddr);
    cc.popTxSnp();

    ASSERT_TRUE(cc.hasTxSnp());
    const HnfCcTxSnp second = cc.frontTxSnp();
    EXPECT_EQ(second.targetNode, 4);
    EXPECT_EQ(second.snp.tgtid, 5);
    EXPECT_EQ(second.snp.txnid, first.snp.txnid);
    cc.popTxSnp();
    EXPECT_FALSE(cc.hasTxSnp());

    EXPECT_FALSE(cc.acceptRxRsp(makeRsp(0, first.snp.txnid, 0x01)));
    EXPECT_FALSE(cc.hasTxDat());
    EXPECT_FALSE(cc.acceptRxRsp(makeRsp(4, first.snp.txnid, 0x01)));

    pumpUpdate(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.frontTxDat().entry, 0);
    const auto final = lookup(slcsf, 8, PocqTxnKind::ReadUnique);
    EXPECT_EQ(final.sfState, HnfSfState::EU);
    EXPECT_EQ(final.rnfid, 8);
    EXPECT_EQ(final.snoopTargets, 0);
}

TEST(HnfCoherencyControllerTest, SeqDoesNotRetireBeforeCompleteSfEvictResponse)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 1, 1, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const uint64_t victimAddr = TestAddr;
    const uint64_t replacementAddr = TestAddr + BlockSize;
    const auto data = lineData(0x70);
    slcsf.commitRead(victimAddr, 0, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);
    slcsf.commitRead(replacementAddr, 4, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);
    ASSERT_EQ(slcsf.seqOccupancy(), 1);
    const HnfSLCSF::SeqVictim seq_victim = slcsf.frontPendingSeq();

    cc.serviceInternalWork();
    ASSERT_TRUE(cc.hasTxSnp());
    const HnfCcTxSnp snoop = cc.frontTxSnp();
    EXPECT_EQ(snoop.entry, UINT32_MAX);
    EXPECT_EQ(snoop.targetNode, 0);
    EXPECT_EQ(snoop.snp.srcid, HnfNode);
    EXPECT_EQ(snoop.snp.opcode, 0x09);
    EXPECT_EQ(snoop.snp.addr, victimAddr);
    cc.popTxSnp();

    EXPECT_FALSE(cc.acceptRxRsp(makeRsp(0, snoop.snp.txnid, 0x01)));
    EXPECT_EQ(slcsf.seqOccupancy(), 1);
    EXPECT_FALSE(cc.hasTxSnp());
    Tick tick = 0;
    for (size_t i = 0; i < slcsf.pipelineConfig().updateLatency; ++i) {
        pumpOnce(cc, slcsf, tick);
        EXPECT_EQ(slcsf.seqOccupancy(), 1);
        EXPECT_TRUE(cc.hasActiveSeqPocq());
    }
    pumpOnce(cc, slcsf, tick);
    EXPECT_EQ(slcsf.seqOccupancy(), 1);
    EXPECT_EQ(slcsf.seqPhase(seq_victim.id),
              HnfSLCSF::SeqPhase::CommittedAwaitAck);
    EXPECT_EQ(slcsf.respPendingCount(), 1);
    EXPECT_TRUE(cc.hasActiveSeqPocq());
    pumpOnce(cc, slcsf, tick);
    EXPECT_EQ(slcsf.seqOccupancy(), 0);
    EXPECT_FALSE(cc.hasActiveSeqPocq());

    HnfSlcLookupReq req{};
    req.req.srcid = 8;
    req.blockAddr = victimAddr;
    req.txn = PocqTxnKind::ReadShared;
    EXPECT_FALSE(slcsf.lookup(req).replay);
}

TEST(HnfCoherencyControllerTest, SeqPocqPreservesDirtySnoopData)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 1, 1, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const uint64_t victimAddr = TestAddr;
    const uint64_t replacementAddr = TestAddr + BlockSize;
    const auto oldData = lineData(0x80);
    const auto dirtyData = lineData(0x90);
    slcsf.commitRead(victimAddr, 0, PocqTxnKind::ReadUnique,
                     oldData, false, HnfNode);
    slcsf.commitRead(replacementAddr, 4, PocqTxnKind::ReadUnique,
                     oldData, false, HnfNode);

    cc.serviceInternalWork();
    ASSERT_TRUE(cc.hasTxSnp());
    const HnfCcTxSnp snoop = cc.frontTxSnp();
    cc.popTxSnp();

    RawDat dat{};
    dat.srcid = 0;
    dat.tgtid = HnfNode;
    dat.txnid = snoop.snp.txnid;
    dat.opcode = 0x01;
    dat.last = true;
    dat.beatOffset = 0;
    dat.data = dirtyData;
    EXPECT_FALSE(cc.acceptRxDat(dat));
    EXPECT_EQ(slcsf.seqOccupancy(), 1);
    Tick tick = 0;
    pumpSeqCompletion(cc, slcsf, tick);

    HnfSlcLookupReq req{};
    req.req.srcid = 8;
    req.blockAddr = victimAddr;
    req.txn = PocqTxnKind::ReadShared;
    const auto result = slcsf.lookup(req);
    ASSERT_TRUE(result.slcHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.data, dirtyData);
}

TEST(HnfCoherencyControllerTest, ReadNoSnpReturnsWithoutAllocatingSlcSf)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto admitted =
        cc.acceptLinkReq(makeRead(0, 1, 0, 31, 0x04), 0);
    ASSERT_TRUE(admitted.accepted);
    Tick tick = 0;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxReq());
    cc.popTxReq();
    cc.notifyTxReqSent(0);

    const auto data = lineData(0xa0);
    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, 0, false, data)));
    const auto retired =
        cc.acceptRxDat(makeCompData(1, BeatSize, true, data));
    ASSERT_TRUE(retired);
    EXPECT_EQ(retired->tokenId, 0);

    HnfSlcLookupReq req{};
    req.req.srcid = 4;
    req.blockAddr = TestAddr;
    req.txn = PocqTxnKind::ReadShared;
    const auto result = slcsf.lookup(req);
    EXPECT_FALSE(result.slcHit);
    EXPECT_FALSE(result.sfHit);
}

TEST(HnfCoherencyControllerTest, SeqPocqSleepsForMainAddressHazard)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 1, 1, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const uint64_t victimAddr = TestAddr;
    const uint64_t replacementAddr = TestAddr + BlockSize;
    const auto data = lineData(0xb0);
    slcsf.commitRead(victimAddr, 0, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 1, 4, 41, 0x04, victimAddr), 0).accepted);
    slcsf.commitRead(replacementAddr, 8, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);
    ASSERT_EQ(slcsf.seqOccupancy(), 1);

    cc.serviceInternalWork();
    EXPECT_FALSE(cc.hasTxSnp());

    Tick tick = 0;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxReq());
    cc.popTxReq();
    cc.notifyTxReqSent(0);
    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, 0, false, data)));
    ASSERT_TRUE(cc.acceptRxDat(makeCompData(1, BeatSize, true, data)));

    cc.serviceInternalWork();
    ASSERT_TRUE(cc.hasTxSnp());
    const HnfCcTxSnp snoop = cc.frontTxSnp();
    EXPECT_EQ(snoop.snp.addr, victimAddr);
    cc.popTxSnp();
    EXPECT_FALSE(cc.acceptRxRsp(makeRsp(0, snoop.snp.txnid, 0x01)));
    pumpSeqCompletion(cc, slcsf, tick);
    EXPECT_EQ(slcsf.seqOccupancy(), 0);
}

TEST(HnfCoherencyControllerTest, SeqPocqIgnoresYoungerSleepingWaiters)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 1, 1, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const uint64_t victimAddr = TestAddr;
    const uint64_t replacementAddr = TestAddr + BlockSize;
    const auto data = lineData(0xb8);
    slcsf.commitRead(victimAddr, 0, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 1, 4, 61, 0x04, victimAddr), 0).accepted);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(1, 2, 8, 62, 0x01, victimAddr), 1).accepted);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(2, 3, 12, 63, 0x01, victimAddr), 2).accepted);

    slcsf.commitRead(replacementAddr, 0, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);
    ASSERT_EQ(slcsf.seqOccupancy(), 1);
    cc.serviceInternalWork();
    EXPECT_FALSE(cc.hasTxSnp());

    Tick tick = 0;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxReq());
    cc.popTxReq();
    cc.notifyTxReqSent(0);
    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, 0, false, data)));
    ASSERT_TRUE(cc.acceptRxDat(makeCompData(1, BeatSize, true, data)));

    cc.serviceInternalWork();
    ASSERT_TRUE(cc.hasTxSnp());
    const HnfCcTxSnp snoop = cc.frontTxSnp();
    EXPECT_EQ(snoop.snp.addr, victimAddr);
    cc.popTxSnp();
    EXPECT_FALSE(cc.acceptRxRsp(makeRsp(0, snoop.snp.txnid, 0x01)));
    pumpSeqCompletion(cc, slcsf, tick);
    EXPECT_EQ(slcsf.seqOccupancy(), 0);
}

TEST(HnfCoherencyControllerTest, SeqReservationReplayMakesProgress)
{
    HnfSLCSF slcsf(BlockSize, 8, 2, 2, 1, 1);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const uint64_t set0A = TestAddr;
    const uint64_t set1A = TestAddr + BlockSize;
    const uint64_t set0B = TestAddr + 2 * BlockSize;
    const uint64_t set1B = TestAddr + 3 * BlockSize;
    const auto data = lineData(0xc0);
    slcsf.commitRead(set0A, 0, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);
    slcsf.commitRead(set1A, 4, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 1, 8, 51, 0x07, set0B), 0).accepted);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(1, 2, 12, 52, 0x07, set1B), 1).accepted);
    EXPECT_FALSE(cc.hasTxReq());
    Tick tick = 0;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxReq());
    EXPECT_EQ(cc.frontTxReq().entry, 0);
    cc.popTxReq();
    cc.notifyTxReqSent(0);

    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, 0, false, data)));
    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, BeatSize, true, data)));
    pumpUpdate(cc, slcsf, 0, tick);
    ASSERT_EQ(slcsf.seqOccupancy(), 1);

    cc.serviceInternalWork();
    ASSERT_TRUE(cc.hasTxSnp());
    const HnfCcTxSnp snoop = cc.frontTxSnp();
    EXPECT_EQ(snoop.snp.addr, set0A);
    cc.popTxSnp();
    EXPECT_FALSE(cc.acceptRxRsp(makeRsp(0, snoop.snp.txnid, 0x01)));
    pumpSeqCompletion(cc, slcsf, tick);
    EXPECT_EQ(slcsf.seqOccupancy(), 0);

    cc.serviceInternalWork();
    pumpLookup(cc, slcsf, 1, tick);
    ASSERT_TRUE(cc.hasTxReq());
    EXPECT_EQ(cc.frontTxReq().entry, 1);
}

TEST(HnfCoherencyControllerTest,
     SeqCompletionNoCreditEventuallyProgresses)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 1, 1, 2, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const uint64_t victimAddr = TestAddr;
    const auto data = lineData(0xc8);
    slcsf.commitRead(victimAddr, 0, PocqTxnKind::ReadUnique,
                     data, false, HnfNode);
    slcsf.commitRead(victimAddr + BlockSize, 4,
                     PocqTxnKind::ReadUnique, data, false, HnfNode);

    SlcSfReqHeader blockerHeader{};
    blockerHeader.reqId = SlcSfReqId{1000};
    blockerHeader.pocEntryId = 0;
    blockerHeader.lineAddress = victimAddr + 2 * BlockSize;
    blockerHeader.requester = 0;
    SlcSfRequest blocker = SlcSfLookupReq{
        blockerHeader, PocqTxnKind::ReadShared};
    ASSERT_EQ(slcsf.tryEnqueue(std::move(blocker)),
              SlcSfEnqueueResult::Accepted);

    cc.serviceInternalWork();
    ASSERT_TRUE(cc.hasTxSnp());
    const HnfCcTxSnp snoop = cc.frontTxSnp();
    cc.popTxSnp();
    EXPECT_FALSE(cc.acceptRxRsp(makeRsp(0, snoop.snp.txnid, 0x01)));
    ASSERT_EQ(slcsf.seqOccupancy(), 1);
    const SlcSfReqId completionReqId = cc.seqCompleteReqId();
    ASSERT_TRUE(completionReqId.valid());
    for (size_t i = 0; i < 4; ++i) {
        cc.serviceInternalWork();
        EXPECT_EQ(slcsf.reqOutstanding(), 1);
        EXPECT_EQ(slcsf.seqOccupancy(), 1);
        EXPECT_EQ(cc.seqCompleteReqId(), completionReqId);
    }

    Tick tick = 0;
    std::optional<SlcSfResponse> blockerResponse;
    for (size_t i = 0; i < 16 && !blockerResponse; ++i) {
        slcsf.wakeup(++tick);
        blockerResponse = slcsf.popVisibleResponse();
    }
    ASSERT_TRUE(blockerResponse);
    ASSERT_EQ(blockerResponse->reqId(), SlcSfReqId{1000});

    slcsf.wakeup(++tick);
    cc.serviceInternalWork(tick);
    EXPECT_EQ(slcsf.reqOutstanding(), 1);
    EXPECT_EQ(slcsf.seqOccupancy(), 1);
    EXPECT_EQ(cc.seqCompleteReqId(), completionReqId);
    pumpSeqCompletion(cc, slcsf, tick);
    EXPECT_EQ(slcsf.seqOccupancy(), 0);
    EXPECT_EQ(slcsf.reqOutstanding(), 0);
}

TEST(HnfCoherencyControllerTest, EverySupportedTransactionCompletes)
{
    enum class Flow
    {
        ReadWithCompAck,
        ReadNoSnp,
        ImmediateComp,
        Maintenance,
        CopyBackWrite,
        NonCopyBackWrite,
    };
    struct Case
    {
        const char* name;
        PocqTxnKind txn;
        uint8_t opcode;
        Flow flow;
    };
    const std::vector<Case> cases = {
        {"ReadShared", PocqTxnKind::ReadShared, 0x01,
         Flow::ReadWithCompAck},
        {"ReadUnique", PocqTxnKind::ReadUnique, 0x07,
         Flow::ReadWithCompAck},
        {"ReadNoSnp", PocqTxnKind::ReadNoSnp, 0x04,
         Flow::ReadNoSnp},
        {"ReadOnce", PocqTxnKind::ReadOnce, 0x03,
         Flow::ReadWithCompAck},
        {"CleanInvalid", PocqTxnKind::CleanInvalid, 0x09,
         Flow::Maintenance},
        {"MakeInvalid", PocqTxnKind::MakeInvalid, 0x0a,
         Flow::Maintenance},
        {"MakeUnique", PocqTxnKind::MakeUnique, 0x0c,
         Flow::Maintenance},
        {"Evict", PocqTxnKind::Evict, 0x0d,
         Flow::ImmediateComp},
        {"WriteBackFull", PocqTxnKind::WriteBackFull, 0x5b,
         Flow::CopyBackWrite},
        {"WriteCleanFull", PocqTxnKind::WriteCleanFull, 0x57,
         Flow::CopyBackWrite},
        {"WriteUnique", PocqTxnKind::WriteUnique, 0x59,
         Flow::NonCopyBackWrite},
        {"WriteEvictFull", PocqTxnKind::WriteEvictFull, 0x55,
         Flow::CopyBackWrite},
    };

    for (size_t i = 0; i < cases.size(); ++i) {
        const Case& test = cases[i];
        SCOPED_TRACE(test.name);
        HnfSLCSF slcsf(BlockSize, 8, 4, 8, 4, 2);
        HnfCoherencyController cc(
            BlockSize, BeatSize, 8, SnNode, false, 4);
        cc.setSlcsf(&slcsf);

        const uint32_t requester = 0;
        const uint32_t requesterTxn = 100 + i;
        const uint64_t addr = TestAddr + i * BlockSize;
        ASSERT_TRUE(cc.acceptLinkReq(
            makeRead(0, i + 1, requester, requesterTxn,
                     test.opcode, addr), i).accepted);
        Tick tick = 0;
        pumpLookup(cc, slcsf, 0, tick);

        if (test.flow == Flow::ReadWithCompAck ||
            test.flow == Flow::ReadNoSnp) {
            ASSERT_TRUE(cc.hasTxReq());
            EXPECT_EQ(cc.frontTxReq().entry, 0);
            cc.popTxReq();
            cc.notifyTxReqSent(0);

            const auto data = lineData(0xd0 + i);
            EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, 0, false, data)));
            const auto dataRetire =
                cc.acceptRxDat(makeCompData(1, BeatSize, true, data));
            if (test.flow != Flow::ReadNoSnp) {
                pumpUpdate(cc, slcsf, 0, tick);
            }
            ASSERT_TRUE(cc.hasTxDat());
            cc.popTxDat();
            ASSERT_TRUE(cc.hasTxDat());
            cc.popTxDat();

            if (test.flow == Flow::ReadNoSnp) {
                ASSERT_TRUE(dataRetire);
                EXPECT_EQ(dataRetire->tokenId, 0);
            } else {
                EXPECT_FALSE(dataRetire);
                const auto retired = cc.acceptRxRsp(
                    makeRsp(requester, requesterTxn, 0x02));
                ASSERT_TRUE(retired);
                EXPECT_EQ(retired->tokenId, 0);
            }
        } else if (test.flow == Flow::ImmediateComp ||
                   test.flow == Flow::Maintenance) {
            if (test.flow == Flow::Maintenance ||
                test.txn == PocqTxnKind::Evict) {
                ASSERT_EQ(cc.slcUpdatePhase(0),
                          HnfCoherencyController::SlcUpdatePhase::Waiting);
                EXPECT_FALSE(cc.hasTxRsp());
                pumpUpdate(cc, slcsf, 0, tick);
            }
            ASSERT_TRUE(cc.hasTxRsp());
            ASSERT_TRUE(cc.frontTxRsp().retire);
            EXPECT_FALSE(
                cc.frontTxRsp().traceDirtyVictimRequesterDone);
            EXPECT_EQ(cc.frontTxRsp().retire->tokenId, 0);
            cc.popTxRsp();
        } else {
            ASSERT_TRUE(cc.hasTxRsp());
            EXPECT_FALSE(cc.frontTxRsp().retire);
            EXPECT_FALSE(
                cc.frontTxRsp().traceDirtyVictimRequesterDone);
            cc.popTxRsp();

            const auto data = lineData(0xe0 + i);
            const uint8_t datOpcode =
                test.flow == Flow::NonCopyBackWrite ? 0x03 : 0x02;
            EXPECT_FALSE(cc.acceptRxDat(makeWriteData(
                requester, requesterTxn, 0, false, datOpcode, data)));
            const auto retired = cc.acceptRxDat(makeWriteData(
                requester, requesterTxn, BeatSize, true,
                datOpcode, data));
            EXPECT_FALSE(retired);
            pumpLookup(cc, slcsf, 0, tick);
            ASSERT_EQ(cc.slcUpdatePhase(0),
                      HnfCoherencyController::SlcUpdatePhase::Waiting);
            pumpUpdate(cc, slcsf, 0, tick);
            ASSERT_TRUE(cc.hasDeferredRetire());
            EXPECT_EQ(cc.popDeferredRetire().tokenId, 0);
            EXPECT_FALSE(cc.hasDeferredRetire());
        }

        EXPECT_FALSE(cc.hasWork());
    }
}

TEST(HnfCoherencyControllerTest, WriteDoesNotRetireBeforeUpdateResponse)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 8000, 0, 200, 0x59, TestAddr), 0).accepted);
    ASSERT_TRUE(cc.hasTxRsp());
    cc.popTxRsp();

    const auto data = lineData(0xa1);
    EXPECT_FALSE(cc.acceptRxDat(
        makeWriteData(0, 200, 0, false, 0x03, data)));
    EXPECT_FALSE(cc.acceptRxDat(
        makeWriteData(0, 200, BeatSize, true, 0x03, data)));
    EXPECT_FALSE(cc.hasDeferredRetire());

    Tick tick = 1000;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    EXPECT_FALSE(cc.hasDeferredRetire());
    EXPECT_FALSE(lookup(slcsf, 0, PocqTxnKind::ReadShared).slcHit);

    pumpUpdate(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasDeferredRetire());
    EXPECT_EQ(cc.popDeferredRetire().tokenId, 0);
    EXPECT_FALSE(cc.hasDeferredRetire());
    EXPECT_EQ(lookup(slcsf, 0, PocqTxnKind::ReadShared).data, data);
}

TEST(HnfCoherencyControllerTest, DeferredWriteRetiresTokenExactlyOnce)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    config.respQueueEntries = 1;
    config.maxInflight = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 8001, 0, 201, 0x59, TestAddr), 0).accepted);
    cc.popTxRsp();
    const auto data = lineData(0xa2);
    EXPECT_FALSE(cc.acceptRxDat(
        makeWriteData(0, 201, 0, false, 0x03, data)));
    EXPECT_FALSE(cc.acceptRxDat(
        makeWriteData(0, 201, BeatSize, true, 0x03, data)));

    Tick tick = 1100;
    for (size_t i = 0; i < 32 &&
         cc.slcLookupPhase(0) !=
             HnfCoherencyController::SlcLookupPhase::ResponseLatched; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);

    RawReq blockerRaw{};
    blockerRaw.srcid = 63;
    SlcSfReqIdAllocator blockerIds;
    SlcSfRequest blocker = makeSlcSfLookupReq(
        makeSlcSfReqHeader(
            blockerIds, UINT32_MAX, TestAddr + BlockSize, blockerRaw),
        PocqTxnKind::ReadShared);
    ASSERT_EQ(slcsf.tryEnqueue(std::move(blocker)),
              SlcSfEnqueueResult::Accepted);

    cc.serviceInternalWork(++tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::IssuePending);
    const SlcSfReqId retainedReqId = cc.slcUpdateReqId(0);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateIssue);
    EXPECT_FALSE(cc.hasDeferredRetire());
    for (size_t i = 0; i < 4; ++i) {
        cc.serviceInternalWork(++tick);
        EXPECT_EQ(cc.slcUpdateReqId(0), retainedReqId);
        EXPECT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::IssuePending);
    }

    std::optional<SlcSfResponse> blockerResponse;
    for (size_t i = 0; i < 32 && !blockerResponse; ++i) {
        slcsf.wakeup(++tick);
        blockerResponse = slcsf.popVisibleResponse();
    }
    ASSERT_TRUE(blockerResponse);
    ASSERT_EQ(blockerResponse->pocEntryId(), UINT32_MAX);

    cc.serviceInternalWork(++tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.slcUpdateReqId(0), retainedReqId);
    pumpUpdate(cc, slcsf, 0, tick);

    ASSERT_TRUE(cc.hasDeferredRetire());
    const HnfCcRetireInfo retire = cc.popDeferredRetire();
    EXPECT_EQ(retire.tokenId, 0);
    EXPECT_EQ(retire.allocationSeq, 8001);
    EXPECT_EQ(retire.srcid, 0);
    EXPECT_EQ(retire.txnid, 201);
    EXPECT_FALSE(cc.hasDeferredRetire());
    for (size_t i = 0; i < 4; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    EXPECT_FALSE(cc.hasDeferredRetire());
    EXPECT_EQ(lookup(slcsf, 0, PocqTxnKind::ReadShared).data, data);
}

TEST(HnfCoherencyControllerTest,
     WriteHitCommitsRxPayloadForEveryWriteOpcode)
{
    struct WriteCase
    {
        const char* name;
        uint8_t reqOpcode;
        uint8_t dataOpcode;
    };
    const std::vector<WriteCase> cases = {
        {"WriteBackFull", 0x5b, 0x02},
        {"WriteCleanFull", 0x57, 0x02},
        {"WriteUniqueFull", 0x59, 0x03},
        {"WriteEvictFull", 0x55, 0x02},
    };

    for (size_t i = 0; i < cases.size(); ++i) {
        SCOPED_TRACE(cases[i].name);
        HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8);
        HnfCoherencyController cc(
            BlockSize, BeatSize, 8, SnNode, false, 4);
        cc.setSlcsf(&slcsf);

        const auto oldData = lineData(0x20 + i);
        const auto writeData = lineData(0xa0 + i);
        slcsf.commitRead(
            TestAddr, 0, PocqTxnKind::ReadShared, oldData, false, HnfNode);
        ASSERT_TRUE(cc.acceptLinkReq(
            makeRead(0, 8200 + i, 0, 220 + i,
                     cases[i].reqOpcode, TestAddr), 0).accepted);
        ASSERT_TRUE(cc.hasTxRsp());
        cc.popTxRsp();

        EXPECT_FALSE(cc.acceptRxDat(makeWriteData(
            0, 220 + i, 0, false, cases[i].dataOpcode, writeData)));
        EXPECT_FALSE(cc.acceptRxDat(makeWriteData(
            0, 220 + i, BeatSize, true,
            cases[i].dataOpcode, writeData)));

        Tick tick = 1400 + 100 * i;
        pumpLookup(cc, slcsf, 0, tick);
        ASSERT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::Waiting);
        pumpUpdate(cc, slcsf, 0, tick);

        ASSERT_TRUE(cc.hasDeferredRetire());
        const HnfCcRetireInfo retire = cc.popDeferredRetire();
        EXPECT_EQ(retire.tokenId, 0);
        EXPECT_EQ(retire.allocationSeq, 8200 + i);
        EXPECT_EQ(retire.srcid, 0);
        EXPECT_EQ(retire.txnid, 220 + i);
        EXPECT_FALSE(cc.hasDeferredRetire());

        const auto stored = lookup(slcsf, 0, PocqTxnKind::ReadShared);
        ASSERT_TRUE(stored.slcHit);
        EXPECT_EQ(stored.data, writeData);
    }
}

TEST(HnfCoherencyControllerTest,
     WriteReplayPreservesPayloadAndUsesFreshAttemptIdentity)
{
    struct WriteCase
    {
        const char* name;
        uint8_t reqOpcode;
        uint8_t dataOpcode;
    };
    const std::vector<WriteCase> cases = {
        {"WriteBackFull", 0x5b, 0x02},
        {"WriteCleanFull", 0x57, 0x02},
        {"WriteUniqueFull", 0x59, 0x03},
        {"WriteEvictFull", 0x55, 0x02},
    };

    for (size_t i = 0; i < cases.size(); ++i) {
        SCOPED_TRACE(cases[i].name);
        HnfSLCSFPipelineConfig config{};
        config.replayPenalty = 3;
        HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
        HnfCoherencyController cc(
            BlockSize, BeatSize, 8, SnNode, false, 4);
        cc.setSlcsf(&slcsf);

        const uint64_t sequence = 8250 + i;
        const uint32_t txnid = 250 + i;
        const auto oldData = lineData(0x35 + i);
        const auto writeData = lineData(0xc5 + i);
        slcsf.commitRead(
            TestAddr, 0, PocqTxnKind::ReadShared,
            oldData, false, HnfNode);
        ASSERT_TRUE(cc.acceptLinkReq(
            makeRead(0, sequence, 0, txnid,
                     cases[i].reqOpcode, TestAddr), 0).accepted);
        ASSERT_TRUE(cc.hasTxRsp());
        cc.popTxRsp();
        EXPECT_FALSE(cc.acceptRxDat(makeWriteData(
            0, txnid, 0, false, cases[i].dataOpcode, writeData)));
        EXPECT_FALSE(cc.acceptRxDat(makeWriteData(
            0, txnid, BeatSize, true,
            cases[i].dataOpcode, writeData)));

        Tick tick = 1800 + 100 * i;
        pumpLookup(cc, slcsf, 0, tick);
        ASSERT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::Waiting);
        const SlcSfReqId staleLookupId = cc.slcLookupReqId(0);
        const SlcSfReqId staleUpdateId = cc.slcUpdateReqId(0);
        slcsf.invalidateCommitTokens();

        for (size_t cycle = 0; cycle < 32 &&
             cc.slcUpdatePhase(0) !=
                 HnfCoherencyController::SlcUpdatePhase::ReplayWait;
             ++cycle) {
            pumpOnce(cc, slcsf, tick);
        }
        ASSERT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::ReplayWait);
        EXPECT_FALSE(cc.hasDeferredRetire());

        const Tick retryDeadline = cc.slcsfRetryNotBeforeTick(0);
        ASSERT_GT(retryDeadline, tick);
        cc.serviceInternalWork(retryDeadline);
        EXPECT_EQ(cc.slcLookupPhase(0),
                  HnfCoherencyController::SlcLookupPhase::Waiting);
        EXPECT_NE(cc.slcLookupReqId(0), staleLookupId);
        tick = retryDeadline;

        pumpLookup(cc, slcsf, 0, tick);
        ASSERT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::Waiting);
        EXPECT_NE(cc.slcUpdateReqId(0), staleUpdateId);
        pumpUpdate(cc, slcsf, 0, tick);

        ASSERT_TRUE(cc.hasDeferredRetire());
        const HnfCcRetireInfo retire = cc.popDeferredRetire();
        EXPECT_EQ(retire.tokenId, 0);
        EXPECT_EQ(retire.allocationSeq, sequence);
        EXPECT_EQ(retire.srcid, 0);
        EXPECT_EQ(retire.txnid, txnid);
        EXPECT_FALSE(cc.hasDeferredRetire());
        EXPECT_EQ(
            lookup(slcsf, 0, PocqTxnKind::ReadShared).data,
            writeData);
    }
}

TEST(HnfCoherencyControllerTest,
     UpdateResponseMustMatchExactPendingOperation)
{
    enum class Flow
    {
        Maintenance,
        Evict,
        Write
    };
    struct Case
    {
        const char* name;
        Flow flow;
        uint8_t opcode;
    };
    const std::vector<Case> cases = {
        {"Maintenance", Flow::Maintenance, 0x09},
        {"Evict", Flow::Evict, 0x0d},
        {"Write", Flow::Write, 0x59},
    };

    for (size_t i = 0; i < cases.size(); ++i) {
        SCOPED_TRACE(cases[i].name);
        HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8);
        HnfCoherencyController cc(
            BlockSize, BeatSize, 8, SnNode, false, 4);
        cc.setSlcsf(&slcsf);

        const uint64_t sequence = 8300 + i;
        const uint32_t txnid = 260 + i;
        ASSERT_TRUE(cc.acceptLinkReq(
            makeRead(0, sequence, 0, txnid,
                     cases[i].opcode, TestAddr), 0).accepted);
        if (cases[i].flow == Flow::Write) {
            ASSERT_TRUE(cc.hasTxRsp());
            cc.popTxRsp();
            const auto payload = lineData(0xd0);
            EXPECT_FALSE(cc.acceptRxDat(
                makeWriteData(0, txnid, 0, false, 0x03, payload)));
            EXPECT_FALSE(cc.acceptRxDat(
                makeWriteData(
                    0, txnid, BeatSize, true, 0x03, payload)));
        }

        Tick tick = 2200 + 100 * i;
        pumpLookup(cc, slcsf, 0, tick);
        ASSERT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::Waiting);
        const SlcSfReqId updateId = cc.slcUpdateReqId(0);

        SlcSfReqHeader header{};
        header.reqId = updateId;
        header.pocEntryId = 0;
        header.lineAddress = TestAddr;
        header.requester = 0;
        SlcSfResponse wrongResponse = [&]() {
            if (cases[i].flow == Flow::Maintenance) {
                return makeSlcSfDoneResponse(
                    makeSlcSfRemoveSharerReq(header));
            }
            if (cases[i].flow == Flow::Evict) {
                return makeSlcSfDoneResponse(
                    makeSlcSfCompleteMaintenanceReq(
                        header, PocqTxnKind::CleanInvalid, HnfNode));
            }
            return makeSlcSfDoneResponse(
                makeSlcSfCommitReadReq(
                    header, PocqTxnKind::ReadShared, lineData(0xe0),
                    false, HnfNode));
        }();

        EXPECT_ANY_THROW(cc.consumeSlcsfResponse(wrongResponse));
        EXPECT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::Waiting);
        EXPECT_EQ(cc.slcUpdateReqId(0), updateId);
        EXPECT_FALSE(cc.hasDeferredRetire());

        if (cases[i].flow == Flow::Maintenance) {
            const SlcSfUpdateReq wrongRequest =
                makeSlcSfRemoveSharerReq(header);
            EXPECT_ANY_THROW(cc.consumeSlcsfResponse(
                makeSlcSfReplayResponse(
                    wrongRequest,
                    SlcSfReplay{
                        SlcSfReplayReason::StaleCommitToken,
                        tick + 1, true})));
            EXPECT_ANY_THROW(cc.consumeSlcsfResponse(
                makeSlcSfErrorResponse(
                    wrongRequest,
                    SlcSfError{
                        SlcSfErrorCode::InvalidRequest,
                        "wrong operation"})));
            EXPECT_EQ(cc.slcUpdatePhase(0),
                      HnfCoherencyController::SlcUpdatePhase::Waiting);
            EXPECT_EQ(cc.slcUpdateReqId(0), updateId);
        }

        pumpUpdate(cc, slcsf, 0, tick);
        if (cases[i].flow == Flow::Write) {
            ASSERT_TRUE(cc.hasDeferredRetire());
            const HnfCcRetireInfo retire = cc.popDeferredRetire();
            EXPECT_EQ(retire.tokenId, 0);
            EXPECT_EQ(retire.allocationSeq, sequence);
            EXPECT_EQ(retire.srcid, 0);
            EXPECT_EQ(retire.txnid, txnid);
            EXPECT_FALSE(cc.hasDeferredRetire());
        } else {
            ASSERT_TRUE(cc.hasTxRsp());
            ASSERT_TRUE(cc.frontTxRsp().retire);
            const HnfCcRetireInfo& retire = *cc.frontTxRsp().retire;
            EXPECT_EQ(retire.tokenId, 0);
            EXPECT_EQ(retire.allocationSeq, sequence);
            EXPECT_EQ(retire.srcid, 0);
            EXPECT_EQ(retire.txnid, txnid);
            cc.popTxRsp();
        }
    }
}

TEST(HnfCoherencyControllerTest, EvictWaitsForRemoveSharerResponse)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8);
    const auto data = lineData(0xb1);
    slcsf.commitRead(
        TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    slcsf.commitRead(
        TestAddr, 4, PocqTxnKind::ReadShared, data, false);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 8100, 0, 210, 0x0d, TestAddr), 0).accepted);
    EXPECT_FALSE(cc.hasTxRsp());

    Tick tick = 1200;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    EXPECT_FALSE(cc.hasTxRsp());
    EXPECT_NE(lookup(slcsf, 4, PocqTxnKind::ReadShared).rnfvec & 1, 0);

    pumpUpdate(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxRsp());
    ASSERT_TRUE(cc.frontTxRsp().retire);
    EXPECT_EQ(cc.frontTxRsp().retire->tokenId, 0);
    EXPECT_EQ(lookup(slcsf, 4, PocqTxnKind::ReadShared).rnfvec & 1, 0);
    cc.popTxRsp();
    EXPECT_FALSE(cc.hasWork());
}

TEST(HnfCoherencyControllerTest, UpdateNoCreditEventuallyProgresses)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    config.respQueueEntries = 1;
    config.maxInflight = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    const auto data = lineData(0xb2);
    slcsf.commitRead(
        TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    slcsf.commitRead(
        TestAddr, 4, PocqTxnKind::ReadShared, data, false);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 8101, 0, 211, 0x0d, TestAddr), 0).accepted);
    Tick tick = 1300;
    for (size_t i = 0; i < 32 &&
         cc.slcLookupPhase(0) !=
             HnfCoherencyController::SlcLookupPhase::ResponseLatched; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);

    RawReq blockerRaw{};
    blockerRaw.srcid = 63;
    SlcSfReqIdAllocator blockerIds;
    SlcSfRequest blocker = makeSlcSfLookupReq(
        makeSlcSfReqHeader(
            blockerIds, UINT32_MAX, TestAddr + BlockSize, blockerRaw),
        PocqTxnKind::ReadShared);
    ASSERT_EQ(slcsf.tryEnqueue(std::move(blocker)),
              SlcSfEnqueueResult::Accepted);

    cc.serviceInternalWork(++tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::IssuePending);
    const SlcSfReqId retainedReqId = cc.slcUpdateReqId(0);
    EXPECT_TRUE(retainedReqId.valid());
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateIssue);
    EXPECT_FALSE(cc.hasTxRsp());
    for (size_t i = 0; i < 4; ++i) {
        cc.serviceInternalWork(++tick);
        EXPECT_EQ(cc.slcUpdateReqId(0), retainedReqId);
        EXPECT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::IssuePending);
        EXPECT_EQ(slcsf.reqOutstanding(), 1);
    }

    std::optional<SlcSfResponse> blockerResponse;
    for (size_t i = 0; i < 32 && !blockerResponse; ++i) {
        slcsf.wakeup(++tick);
        blockerResponse = slcsf.popVisibleResponse();
    }
    ASSERT_TRUE(blockerResponse);
    ASSERT_EQ(blockerResponse->pocEntryId(), UINT32_MAX);

    cc.serviceInternalWork(++tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.slcUpdateReqId(0), retainedReqId);
    pumpUpdate(cc, slcsf, 0, tick);

    ASSERT_TRUE(cc.hasTxRsp());
    ASSERT_TRUE(cc.frontTxRsp().retire);
    EXPECT_EQ(cc.frontTxRsp().retire->tokenId, 0);
    cc.popTxRsp();
    EXPECT_EQ(lookup(slcsf, 4, PocqTxnKind::ReadShared).rnfvec & 1, 0);
    for (size_t i = 0; i < 4; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    EXPECT_FALSE(cc.hasTxRsp());
}

TEST(HnfCoherencyControllerTest, EvictReplayRestartsFromFreshLookup)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8);
    const auto data = lineData(0xb3);
    slcsf.commitRead(
        TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    slcsf.commitRead(
        TestAddr, 4, PocqTxnKind::ReadShared, data, false);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 8102, 0, 212, 0x0d, TestAddr), 0).accepted);
    Tick tick = 1400;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    const SlcSfReqId staleLookupId = cc.slcLookupReqId(0);

    slcsf.invalidateCommitTokens();
    for (size_t i = 0; i < 32 &&
         cc.slcUpdatePhase(0) !=
             HnfCoherencyController::SlcUpdatePhase::ReplayWait; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::ReplayWait);
    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    EXPECT_FALSE(cc.hasTxRsp());
    EXPECT_NE(lookup(slcsf, 4, PocqTxnKind::ReadShared).rnfvec & 1, 0);

    const Tick retryDeadline = cc.slcsfRetryNotBeforeTick(0);
    cc.serviceInternalWork(retryDeadline - 1);
    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    cc.serviceInternalWork(retryDeadline);
    tick = retryDeadline;
    ASSERT_EQ(cc.pocqState(0), PocqState::SlcLookup);
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    EXPECT_NE(cc.slcLookupReqId(0), staleLookupId);

    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    pumpUpdate(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxRsp());
    EXPECT_EQ(lookup(slcsf, 4, PocqTxnKind::ReadShared).rnfvec & 1, 0);
    cc.popTxRsp();
}

TEST(HnfCoherencyControllerTest, LookupNoCreditEventuallyProgresses)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    config.respQueueEntries = 1;
    config.maxInflight = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    RawReq blockerRaw{};
    blockerRaw.srcid = 63;
    SlcSfReqIdAllocator blockerIds;
    SlcSfRequest blocker = makeSlcSfLookupReq(
        makeSlcSfReqHeader(
            blockerIds, UINT32_MAX, TestAddr + BlockSize, blockerRaw),
        PocqTxnKind::ReadShared);
    ASSERT_EQ(slcsf.tryEnqueue(std::move(blocker)),
              SlcSfEnqueueResult::Accepted);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 77, 0, 91, 0x01), 0).accepted);
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::IssuePending);
    const SlcSfReqId reqId = cc.slcLookupReqId(0);
    ASSERT_TRUE(reqId.valid());
    for (size_t i = 0; i < 3; ++i) {
        cc.serviceInternalWork();
        EXPECT_EQ(cc.slcLookupPhase(0),
                  HnfCoherencyController::SlcLookupPhase::IssuePending);
        EXPECT_EQ(cc.slcLookupReqId(0), reqId);
        EXPECT_EQ(slcsf.reqOutstanding(), 1);
    }

    Tick tick = 100;
    for (size_t i = 0; i < 16 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    ASSERT_TRUE(slcsf.popVisibleResponse());

    cc.serviceInternalWork();
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    EXPECT_EQ(cc.slcLookupReqId(0), reqId);
    EXPECT_EQ(slcsf.reqOutstanding(), 1);

    pumpLookup(cc, slcsf, 0, tick);
    EXPECT_TRUE(cc.hasTxReq());
}

TEST(HnfCoherencyControllerTest, DrainDoesNotLoseIssuePendingIntent)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    config.respQueueEntries = 1;
    config.maxInflight = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    RawReq blockerRaw{};
    blockerRaw.srcid = 63;
    SlcSfReqIdAllocator blockerIds;
    SlcSfRequest blocker = makeSlcSfLookupReq(
        makeSlcSfReqHeader(
            blockerIds, UINT32_MAX, TestAddr + BlockSize, blockerRaw),
        PocqTxnKind::ReadShared);
    ASSERT_EQ(slcsf.tryEnqueue(std::move(blocker)),
              SlcSfEnqueueResult::Accepted);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7801, 0, 181, 0x01), 0).accepted);
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::IssuePending);
    const SlcSfReqId pending_id = cc.slcLookupReqId(0);
    ASSERT_TRUE(cc.mayGenerateSlcsfIntent());

    slcsf.requestDrain();
    EXPECT_TRUE(slcsf.isDrainRequested());
    EXPECT_FALSE(slcsf.isAdmissionSealed());
    for (size_t i = 0; i < 3; ++i) {
        cc.serviceInternalWork(100 + i);
        EXPECT_EQ(cc.slcLookupPhase(0),
                  HnfCoherencyController::SlcLookupPhase::IssuePending);
        EXPECT_EQ(cc.slcLookupReqId(0), pending_id);
    }

    Tick tick = 110;
    for (size_t i = 0; i < 16 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_TRUE(slcsf.popVisibleResponse());
    cc.serviceInternalWork(tick);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    EXPECT_EQ(cc.slcLookupReqId(0), pending_id);

    pumpLookup(cc, slcsf, 0, tick);
    EXPECT_TRUE(cc.hasTxReq());
    EXPECT_EQ(slcsf.drainingRejectCount(), 0);
}

TEST(HnfCoherencyControllerTest, DrainKeepsProtocolCompletionsEnabled)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7901, 0, 182, 0x01), 0).accepted);
    Tick tick = 200;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxReq());
    const HnfCcTxReq memory_read = cc.frontTxReq();
    cc.popTxReq();
    cc.notifyTxReqSent(memory_read);

    slcsf.requestDrain();
    ASSERT_TRUE(slcsf.isDrainRequested());
    ASSERT_FALSE(slcsf.isAdmissionSealed());
    const auto data = lineData(0x73);
    EXPECT_FALSE(cc.acceptRxDat(
        makeCompData(memory_read.req.txnid, 0, false, data)));
    EXPECT_FALSE(cc.acceptRxDat(
        makeCompData(memory_read.req.txnid, BeatSize, true, data)));
    ASSERT_NE(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::None);

    pumpUpdate(cc, slcsf, 0, tick);
    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.frontTxDat().entry, 0);
    EXPECT_EQ(slcsf.drainingRejectCount(), 0);
}

TEST(HnfCoherencyControllerTest, WaitingLookupIsNotIssuedTwice)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 1234, 0, 92, 0x01), 0).accepted);
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    const SlcSfReqId reqId = cc.slcLookupReqId(0);
    EXPECT_FALSE(cc.hasTxReq());
    EXPECT_EQ(slcsf.reqOutstanding(), 1);

    for (size_t i = 0; i < 8; ++i) {
        cc.serviceInternalWork();
        EXPECT_EQ(cc.slcLookupPhase(0),
                  HnfCoherencyController::SlcLookupPhase::Waiting);
        EXPECT_EQ(cc.slcLookupReqId(0), reqId);
        EXPECT_EQ(slcsf.reqOutstanding(), 1);
        EXPECT_EQ(slcsf.reqIngressCount(), 1);
        EXPECT_EQ(slcsf.respOccupied(), 0);
    }
}

TEST(HnfCoherencyControllerTest, LookupResponseIsConsumedInLaterCcCycle)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto expectedData = lineData(0xf0);
    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     expectedData, true, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 4321, 0, 93, 0x01), 0).accepted);
    const SlcSfReqId reqId = cc.slcLookupReqId(0);

    Tick tick = 200;
    for (size_t i = 0; i < 16 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    EXPECT_FALSE(cc.hasTxDat());
    EXPECT_FALSE(cc.hasTxReq());

    cc.serviceInternalWork();
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);
    EXPECT_FALSE(cc.hasTxDat());
    EXPECT_FALSE(cc.hasTxReq());
    const HnfSlcLookupResult& result = cc.slcLookupResult(0);
    EXPECT_TRUE(result.valid);
    EXPECT_TRUE(result.slcHit);
    EXPECT_TRUE(result.sfHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.slcState, HnfSlcState::MN);
    EXPECT_EQ(result.sfState, HnfSfState::SN);
    EXPECT_EQ(result.data, expectedData);
    const SlcSfCommitToken& token = cc.slcCommitToken(0);
    EXPECT_EQ(token.lookupReqId, reqId);
    EXPECT_EQ(token.lineAddress, TestAddr);
    EXPECT_NE(token.lookupEpoch, 0);
    EXPECT_TRUE(token.slc.hit);
    EXPECT_TRUE(token.sf.hit);
    EXPECT_NE(token.slc.generation, 0);
    EXPECT_NE(token.sf.generation, 0);

    cc.serviceInternalWork(tick);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::None);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_FALSE(cc.hasTxDat());
    pumpUpdate(cc, slcsf, 0, tick);
    EXPECT_TRUE(cc.hasTxDat());
}

TEST(HnfCoherencyControllerTest, ResponseConsumeWidthLimitsEachCcCycle)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 2;
    config.respQueueEntries = 2;
    config.responseConsumeWidth = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 5001, 0, 94, 0x01, TestAddr), 0).accepted);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(1, 5002, 4, 95, 0x01, TestAddr + BlockSize),
        1).accepted);

    Tick tick = 300;
    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() < 2; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 2);

    cc.serviceInternalWork();
    EXPECT_EQ(slcsf.respVisibleCount(), 1);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);
    EXPECT_EQ(cc.slcLookupPhase(1),
              HnfCoherencyController::SlcLookupPhase::Waiting);

    cc.serviceInternalWork();
    EXPECT_EQ(slcsf.respVisibleCount(), 0);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::None);
    EXPECT_EQ(cc.slcLookupPhase(1),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);
}

TEST(HnfCoherencyControllerTest, ResponseConsumeWidthUsesConfiguredBudget)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 2;
    config.respQueueEntries = 2;
    config.maxInflight = 2;
    config.lookupIssueWidth = 2;
    config.responseConsumeWidth = 2;
    config.enableSetLock = true;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 5101, 0, 96, 0x01, TestAddr), 0).accepted);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(1, 5102, 4, 97, 0x01, TestAddr + BlockSize),
        1).accepted);

    Tick tick = 320;
    for (size_t i = 0; i < 16 && slcsf.respVisibleCount() < 2; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 2);

    cc.serviceInternalWork(tick);
    EXPECT_EQ(slcsf.respVisibleCount(), 0);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);
    EXPECT_EQ(cc.slcLookupPhase(1),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);
    EXPECT_NE(cc.slcLookupReqId(0), cc.slcLookupReqId(1));
    EXPECT_EQ(cc.slcCommitToken(0).lookupReqId, cc.slcLookupReqId(0));
    EXPECT_EQ(cc.slcCommitToken(1).lookupReqId, cc.slcLookupReqId(1));
}

TEST(HnfCoherencyControllerTest, MaintenanceResponseWaitsForSlcSfCommit)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     lineData(0x21), false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 6901, 0, 99, 0x09), 0).accepted);

    Tick tick = 350;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    EXPECT_FALSE(cc.hasTxRsp());

    for (size_t i = 0; i < 3; ++i) {
        cc.serviceInternalWork(tick);
        EXPECT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::Waiting);
        EXPECT_FALSE(cc.hasTxRsp());
    }

    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    EXPECT_FALSE(cc.hasTxRsp());

    cc.serviceInternalWork(tick);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::ResponseLatched);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    EXPECT_FALSE(cc.hasTxRsp());

    cc.serviceInternalWork(tick);
    ASSERT_TRUE(cc.hasTxRsp());
    ASSERT_TRUE(cc.frontTxRsp().retire);
    EXPECT_EQ(cc.frontTxRsp().retire->tokenId, 0);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::None);
    EXPECT_FALSE(slcsf.hasSfReservation(0));
}

TEST(HnfCoherencyControllerTest,
     MaintenanceNoCreditRetriesWithoutDuplicateRequest)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    config.respQueueEntries = 1;
    config.maxInflight = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     lineData(0x25), false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 6902, 0, 100, 0x09), 0).accepted);

    Tick tick = 375;
    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    cc.serviceInternalWork(tick);
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);

    RawReq blockerRaw{};
    blockerRaw.srcid = 63;
    SlcSfReqIdAllocator blockerIds;
    SlcSfRequest blocker = makeSlcSfLookupReq(
        makeSlcSfReqHeader(
            blockerIds, UINT32_MAX, TestAddr + BlockSize, blockerRaw),
        PocqTxnKind::ReadShared);
    ASSERT_EQ(slcsf.tryEnqueue(std::move(blocker)),
              SlcSfEnqueueResult::Accepted);

    cc.serviceInternalWork(tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::IssuePending);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateIssue);
    const SlcSfReqId updateId = cc.slcUpdateReqId(0);
    for (size_t i = 0; i < 3; ++i) {
        cc.serviceInternalWork(tick);
        EXPECT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::IssuePending);
        EXPECT_EQ(cc.slcUpdateReqId(0), updateId);
        EXPECT_EQ(slcsf.reqOutstanding(), 1);
        EXPECT_FALSE(cc.hasTxRsp());
    }

    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_TRUE(slcsf.popVisibleResponse());
    cc.serviceInternalWork(tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.slcUpdateReqId(0), updateId);
    EXPECT_EQ(slcsf.reqOutstanding(), 1);
    EXPECT_FALSE(cc.hasTxRsp());

    pumpUpdate(cc, slcsf, 0, tick);
    EXPECT_TRUE(cc.hasTxRsp());
}

TEST(HnfCoherencyControllerTest,
     MaintenanceReplayRestartsFromFreshLookup)
{
    HnfSLCSFPipelineConfig config{};
    config.replayPenalty = 3;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     lineData(0x29), false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 6903, 0, 101, 0x09), 0).accepted);

    Tick tick = 390;
    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    cc.serviceInternalWork(tick);
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);

    slcsf.completeMaintenance(
        TestAddr, 0, PocqTxnKind::CleanInvalid, HnfNode);
    cc.serviceInternalWork(tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);

    for (size_t i = 0; i < 32 &&
         cc.slcUpdatePhase(0) !=
             HnfCoherencyController::SlcUpdatePhase::ReplayWait; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::ReplayWait);
    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    EXPECT_FALSE(cc.hasTxRsp());
    EXPECT_FALSE(slcsf.hasSfReservation(0));
    const Tick deadline = cc.slcsfRetryNotBeforeTick(0);
    EXPECT_GT(deadline, tick);

    cc.serviceInternalWork(deadline - 1);
    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    cc.serviceInternalWork(deadline);
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    const SlcSfReqId freshLookup = cc.slcLookupReqId(0);
    EXPECT_TRUE(freshLookup.valid());

    tick = deadline;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    pumpUpdate(cc, slcsf, 0, tick);
    EXPECT_TRUE(cc.hasTxRsp());
}

TEST(HnfCoherencyControllerTest, ReadResponseWaitsForSlcSfCommit)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto data = lineData(0x31);
    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     data, false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7001, 0, 101, 0x01), 0).accepted);

    Tick tick = 400;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    EXPECT_FALSE(cc.hasTxDat());

    for (size_t i = 0; i < 3; ++i) {
        cc.serviceInternalWork(tick);
        EXPECT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::Waiting);
        EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
        EXPECT_FALSE(cc.hasTxDat());
    }

    pumpUpdate(cc, slcsf, 0, tick);
    EXPECT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.pocqState(0), PocqState::WaitCompAck);
}

TEST(HnfCoherencyControllerTest, ReadUpdateNoCreditRetriesWithoutDuplicate)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    config.respQueueEntries = 1;
    config.maxInflight = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto data = lineData(0x39);
    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     data, false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7010, 0, 110, 0x01), 0).accepted);

    Tick tick = 450;
    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    cc.serviceInternalWork(tick);
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::ResponseLatched);

    RawReq blockerRaw{};
    blockerRaw.srcid = 63;
    SlcSfReqIdAllocator blockerIds;
    SlcSfRequest blocker = makeSlcSfLookupReq(
        makeSlcSfReqHeader(
            blockerIds, UINT32_MAX, TestAddr + BlockSize, blockerRaw),
        PocqTxnKind::ReadShared);
    ASSERT_EQ(slcsf.tryEnqueue(std::move(blocker)),
              SlcSfEnqueueResult::Accepted);

    cc.serviceInternalWork(tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::IssuePending);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateIssue);
    const SlcSfReqId updateId = cc.slcUpdateReqId(0);
    for (size_t i = 0; i < 3; ++i) {
        cc.serviceInternalWork(tick);
        EXPECT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::IssuePending);
        EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateIssue);
        EXPECT_EQ(cc.slcUpdateReqId(0), updateId);
        EXPECT_EQ(slcsf.reqOutstanding(), 1);
        EXPECT_FALSE(cc.hasTxDat());
    }

    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    ASSERT_TRUE(slcsf.popVisibleResponse());
    cc.serviceInternalWork(tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    EXPECT_EQ(cc.slcUpdateReqId(0), updateId);
    EXPECT_EQ(slcsf.reqOutstanding(), 1);

    for (size_t i = 0; i < 3; ++i) {
        cc.serviceInternalWork(tick);
        EXPECT_EQ(cc.slcUpdatePhase(0),
                  HnfCoherencyController::SlcUpdatePhase::Waiting);
        EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
        EXPECT_EQ(slcsf.reqOutstanding(), 1);
    }
    pumpUpdate(cc, slcsf, 0, tick);
    EXPECT_TRUE(cc.hasTxDat());
}

TEST(HnfCoherencyControllerTest,
     NoSetReservationSpansExternalOrResponseWait)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto data = lineData(0x41);
    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     data, false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7002, 0, 102, 0x01), 0).accepted);

    Tick tick = 500;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    EXPECT_FALSE(slcsf.hasSfReservation(0));
    EXPECT_FALSE(cc.hasTxDat());

    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    EXPECT_FALSE(slcsf.hasSfReservation(0));
    EXPECT_FALSE(cc.hasTxDat());

    cc.serviceInternalWork(tick);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::ResponseLatched);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    EXPECT_FALSE(cc.hasTxDat());
    cc.serviceInternalWork(tick);
    EXPECT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.pocqState(0), PocqState::WaitCompAck);
}

TEST(HnfCoherencyControllerTest, FreshLookupAfterStaleReplayCommits)
{
    HnfSLCSFPipelineConfig config{};
    config.replayPenalty = 3;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto data = lineData(0x51);
    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     data, false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7003, 0, 103, 0x01), 0).accepted);

    Tick tick = 600;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    const SlcSfReqId staleLookupId = cc.slcLookupReqId(0);
    const SlcSfReqId staleUpdateId = cc.slcUpdateReqId(0);
    slcsf.invalidateCommitTokens();

    for (size_t i = 0; i < 32 &&
         cc.slcUpdatePhase(0) !=
             HnfCoherencyController::SlcUpdatePhase::ReplayWait; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::ReplayWait);
    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    EXPECT_FALSE(cc.hasTxDat());
    EXPECT_FALSE(slcsf.hasSfReservation(0));
    EXPECT_FALSE(cc.slcCommitToken(0).lookupReqId.valid());
    EXPECT_FALSE(cc.slcLookupReqId(0).valid());
    EXPECT_FALSE(cc.slcUpdateReqId(0).valid());

    const Tick replayObserved = tick;
    const Tick retryDeadline = cc.slcsfRetryNotBeforeTick(0);
    ASSERT_GT(retryDeadline, replayObserved);
    cc.serviceInternalWork(retryDeadline - 1);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::ReplayWait);
    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    EXPECT_FALSE(cc.slcLookupReqId(0).valid());

    cc.serviceInternalWork(retryDeadline);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::None);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcLookup);
    EXPECT_TRUE(cc.slcLookupReqId(0).valid());
    EXPECT_NE(cc.slcLookupReqId(0), staleLookupId);
    tick = retryDeadline;

    for (size_t i = 0; i < 64 && !cc.hasTxDat(); ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::None);
    EXPECT_NE(cc.slcLookupReqId(0), staleLookupId);
    EXPECT_NE(cc.slcUpdateReqId(0), staleUpdateId);
    EXPECT_TRUE(cc.slcCommitToken(0).lookupReqId.valid());
}

TEST(HnfCoherencyControllerTest,
     PenaltyOneReplayRetriesOnVisibleDeadlineEdge)
{
    HnfSLCSFPipelineConfig config{};
    config.replayPenalty = 1;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto data = lineData(0x61);
    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     data, false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7101, 0, 111, 0x01), 0).accepted);

    Tick tick = 650;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    ASSERT_EQ(cc.pocqState(0), PocqState::SlcUpdateWait);
    const SlcSfReqId staleLookupId = cc.slcLookupReqId(0);
    slcsf.invalidateCommitTokens();

    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
        if (slcsf.respVisibleCount() == 0) {
            cc.serviceInternalWork(tick);
        }
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);

    cc.serviceInternalWork(tick);
    EXPECT_EQ(cc.slcsfRetryNotBeforeTick(0), tick);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcLookup);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::None);
    EXPECT_TRUE(cc.slcLookupReqId(0).valid());
    EXPECT_NE(cc.slcLookupReqId(0), staleLookupId);
    EXPECT_EQ(slcsf.reqOutstanding(), 1);
    EXPECT_FALSE(cc.hasTxDat());
}

TEST(HnfCoherencyControllerTest, LookupReplayUsesSleepGraphAndDeadline)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7102, 0, 112, 0x01), 0).accepted);
    const SlcSfReqId staleLookupId = cc.slcLookupReqId(0);
    ASSERT_TRUE(staleLookupId.valid());

    Tick tick = 700;
    for (size_t i = 0; i < 32 && slcsf.respVisibleCount() == 0; ++i) {
        slcsf.wakeup(++tick);
    }
    ASSERT_EQ(slcsf.respVisibleCount(), 1);
    ASSERT_TRUE(slcsf.popVisibleResponse());
    ASSERT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    ASSERT_FALSE(slcsf.hasSfReservation(0));

    SlcSfReqHeader header{};
    header.reqId = staleLookupId;
    header.pocEntryId = 0;
    header.lineAddress = TestAddr;
    header.requester = 0;
    const SlcSfLookupReq request =
        makeSlcSfLookupReq(header, PocqTxnKind::ReadShared);
    const Tick retryDeadline = tick + 5;
    cc.consumeSlcsfResponse(makeSlcSfReplayResponse(
        request,
        SlcSfReplay{
            SlcSfReplayReason::SeqConflict, retryDeadline, true}));

    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::None);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::None);
    EXPECT_FALSE(cc.slcLookupReqId(0).valid());
    EXPECT_FALSE(cc.slcCommitToken(0).lookupReqId.valid());
    EXPECT_FALSE(slcsf.hasSfReservation(0));
    EXPECT_EQ(cc.slcsfRetryNotBeforeTick(0), retryDeadline);

    cc.serviceInternalWork(retryDeadline - 1);
    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    EXPECT_FALSE(cc.slcLookupReqId(0).valid());

    cc.serviceInternalWork(retryDeadline);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcLookup);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    EXPECT_TRUE(cc.slcLookupReqId(0).valid());
    EXPECT_NE(cc.slcLookupReqId(0), staleLookupId);
    EXPECT_EQ(slcsf.reqOutstanding(), 1);
}

TEST(HnfCoherencyControllerTest,
     SameAddressRetireCannotBypassReplayDeadline)
{
    HnfSLCSFPipelineConfig config{};
    config.replayPenalty = 100;
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2, 8, config);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);

    const auto data = lineData(0x71);
    slcsf.commitRead(TestAddr, 0, PocqTxnKind::ReadShared,
                     data, false, HnfNode);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 7103, 0, 113, 0x01), 0).accepted);

    Tick tick = 750;
    pumpLookup(cc, slcsf, 0, tick);
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::Waiting);
    slcsf.invalidateCommitTokens();
    for (size_t i = 0; i < 32 &&
         cc.slcUpdatePhase(0) !=
             HnfCoherencyController::SlcUpdatePhase::ReplayWait; ++i) {
        pumpOnce(cc, slcsf, tick);
    }
    ASSERT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::ReplayWait);
    ASSERT_EQ(cc.pocqState(0), PocqState::Sleep);
    const Tick retryDeadline = cc.slcsfRetryNotBeforeTick(0);
    ASSERT_GT(retryDeadline, tick + 32);

    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(1, 7104, 4, 114, 0x04), tick).accepted);
    pumpLookup(cc, slcsf, 1, tick);
    ASSERT_TRUE(cc.hasTxReq());
    ASSERT_EQ(cc.frontTxReq().entry, 1);
    cc.popTxReq();
    cc.notifyTxReqSent(1);

    EXPECT_FALSE(cc.acceptRxDat(makeCompData(2, 0, false, data)));
    const auto retired =
        cc.acceptRxDat(makeCompData(2, BeatSize, true, data));
    ASSERT_TRUE(retired);
    EXPECT_EQ(retired->tokenId, 1);

    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    EXPECT_EQ(cc.slcUpdatePhase(0),
              HnfCoherencyController::SlcUpdatePhase::ReplayWait);
    EXPECT_FALSE(cc.slcLookupReqId(0).valid());
    cc.serviceInternalWork(retryDeadline - 1);
    EXPECT_EQ(cc.pocqState(0), PocqState::Sleep);
    EXPECT_FALSE(cc.slcLookupReqId(0).valid());

    cc.serviceInternalWork(retryDeadline);
    EXPECT_EQ(cc.pocqState(0), PocqState::SlcLookup);
    EXPECT_EQ(cc.slcLookupPhase(0),
              HnfCoherencyController::SlcLookupPhase::Waiting);
    EXPECT_TRUE(cc.slcLookupReqId(0).valid());
}

TEST(HnfCoherencyControllerTest, MismatchedLookupResponseIsRejected)
{
    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    cc.setSlcsf(&slcsf);
    ASSERT_TRUE(cc.acceptLinkReq(
        makeRead(0, 6001, 0, 96, 0x01), 0).accepted);

    SlcSfReqHeader wrongHeader{};
    wrongHeader.reqId = SlcSfReqId{cc.slcLookupReqId(0).value + 1};
    wrongHeader.pocEntryId = 0;
    wrongHeader.lineAddress = TestAddr;
    const SlcSfLookupReq wrongRequest = makeSlcSfLookupReq(
        wrongHeader, PocqTxnKind::ReadShared);
    const SlcSfResponse wrongResponse = makeSlcSfDoneResponse(
        wrongRequest, HnfSlcLookupResult{}, SlcSfCommitToken{});
    EXPECT_ANY_THROW(cc.consumeSlcsfResponse(wrongResponse));

    SlcSfReqHeader matchingHeader = wrongHeader;
    matchingHeader.reqId = cc.slcLookupReqId(0);
    const SlcSfLookupReq matchingRequest = makeSlcSfLookupReq(
        matchingHeader, PocqTxnKind::ReadShared);
    const SlcSfResponse matchingResponse = makeSlcSfDoneResponse(
        matchingRequest, HnfSlcLookupResult{}, SlcSfCommitToken{});
    cc.consumeSlcsfResponse(matchingResponse);
    EXPECT_ANY_THROW(cc.consumeSlcsfResponse(matchingResponse));

    cc.serviceInternalWork();
    EXPECT_ANY_THROW(cc.consumeSlcsfResponse(matchingResponse));
}

TEST(HnfCoherencyControllerTest,
     CheckpointContentIncludesControllerRequestIdentity)
{
    HnfCoherencyController cc(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    std::ostringstream checkpoint;

    ASSERT_NO_THROW(cc.serializeSlcsfIdentityState(checkpoint));
    const std::string contents = checkpoint.str();
    EXPECT_NE(contents.find("nextRequestId=1\n"), std::string::npos);
    EXPECT_NE(contents.find("nextSnoopTransactionId="), std::string::npos);
    EXPECT_NE(contents.find("nextDirtyVictimTransactionId="),
              std::string::npos);
}

TEST_F(HnfCcCheckpointTest, RestorePreservesControllerRequestIdentity)
{
    HnfCoherencyController original(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    std::ostringstream checkpoint;
    {
        Serializable::ScopedCheckpointSection section(checkpoint, "cc");
        original.serializeSlcsfIdentityState(checkpoint);
    }
    std::string contents = checkpoint.str();
    const std::string old_value = "nextRequestId=1\n";
    const size_t request_id = contents.find(old_value);
    ASSERT_NE(request_id, std::string::npos);
    contents.replace(request_id, old_value.size(), "nextRequestId=701\n");
    simulateSerialization(contents);

    HnfSLCSF slcsf(BlockSize, 4, 2, 4, 2);
    HnfCoherencyController restored(
        BlockSize, BeatSize, 8, SnNode, false, 4);
    CheckpointIn input(getDirName());
    {
        Serializable::ScopedCheckpointSection section(input, "cc");
        restored.unserializeSlcsfIdentityState(input);
    }
    restored.setSlcsf(&slcsf);
    ASSERT_TRUE(restored.acceptLinkReq(
        makeRead(0, 7001, 3, 97, 0x01), 0).accepted);
    EXPECT_EQ(restored.slcLookupReqId(0), SlcSfReqId{701});
}

} // namespace gem5::Chi

#include <gtest/gtest.h>

#include <cstdint>
#include <vector>

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

HnfSlcLookupResult
lookup(HnfSLCSF& slcsf, uint32_t requester, PocqTxnKind txn)
{
    HnfSlcLookupReq req{};
    req.req.srcid = requester;
    req.blockAddr = TestAddr;
    req.txn = txn;
    return slcsf.lookup(req);
}

} // anonymous namespace

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
    ASSERT_TRUE(cc.hasTxReq());

    EXPECT_EQ(cc.frontTxReq().entry, 0);
    cc.popTxReq();
    cc.notifyTxReqSent(0);

    const auto data = lineData(0x10);
    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, 0, false, data)));
    EXPECT_FALSE(cc.acceptRxDat(makeCompData(1, BeatSize, true, data)));
    ASSERT_TRUE(cc.hasTxDat());
    cc.popTxDat();
    ASSERT_TRUE(cc.hasTxDat());
    cc.popTxDat();

    const auto retired = cc.acceptRxRsp(makeRsp(0, 11, 0x02));
    ASSERT_TRUE(retired);
    EXPECT_EQ(retired->tokenId, 0);

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

    ASSERT_TRUE(cc.hasTxDat());
    EXPECT_EQ(cc.frontTxDat().entry, 0);
    const auto final = lookup(slcsf, 8, PocqTxnKind::ReadUnique);
    EXPECT_EQ(final.sfState, HnfSfState::EU);
    EXPECT_EQ(final.rnfid, 8);
    EXPECT_EQ(final.snoopTargets, 0);
}

TEST(HnfCoherencyControllerTest, SeqPocqBackInvalidatesSfVictim)
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
    EXPECT_EQ(slcsf.seqOccupancy(), 0);
    EXPECT_FALSE(cc.hasTxSnp());

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
    EXPECT_EQ(slcsf.seqOccupancy(), 0);

    HnfSlcLookupReq req{};
    req.req.srcid = 8;
    req.blockAddr = victimAddr;
    req.txn = PocqTxnKind::ReadShared;
    const auto result = slcsf.lookup(req);
    ASSERT_TRUE(result.slcHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.data, dirtyData);
}

} // namespace gem5::Chi

#ifndef UNIT_TEST
#define UNIT_TEST
#endif

#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <limits>
#include <unordered_set>
#include <vector>

#include "mem/cache/CHI/Cache2ChiBridge.hh"

namespace gem5
{
namespace Chi
{

namespace
{

constexpr uint32_t TestBlockSize = 64;
constexpr uint32_t TestBeatSize = 32;
constexpr uint32_t TestRequester = 0x20;
constexpr uint32_t TestHome = 0x90;
constexpr uint32_t TestTxn = 11;

std::vector<uint8_t>
testLineData(uint8_t seed)
{
    std::vector<uint8_t> data(TestBlockSize);
    for (size_t i = 0; i < data.size(); ++i) {
        data[i] = seed + i;
    }
    return data;
}

RawDat
makeReadDataBeat(uint32_t offset, bool last,
                 const std::vector<uint8_t>& data)
{
    RawDat dat{};
    dat.qos = 3;
    dat.srcid = TestHome;
    dat.tgtid = TestRequester;
    dat.txnid = TestTxn;
    dat.opcode = 0x04;
    dat.last = last;
    dat.HomeNID = TestHome;
    dat.dbid = 0;
    dat.dataid = static_cast<uint8_t>(offset / TestBeatSize);
    dat.resp = 1;
    dat.beatOffset = offset;
    dat.data.assign(data.begin() + offset,
                    data.begin() + offset + TestBeatSize);
    dat.byteEnable.assign(dat.data.size(), 1);
    dat.chunkValid.assign((dat.data.size() + 7) / 8, 1);
    return dat;
}

} // anonymous namespace

class Cache2ChiBridgeProtocolTestPeer
{
  public:
    static Cache2ChiBridge::TxnEntry
    makeReadTxn()
    {
        Cache2ChiBridge::TxnEntry txn{};
        txn.txnid = TestTxn;
        txn.intent.kind = Cache2ChiBridge::IntentKind::ReadShared;
        txn.intent.txnClass = Cache2ChiBridge::TxnClass::Read;
        txn.intent.needsResponse = true;
        txn.intent.expectsData = true;
        txn.intent.expectsComp = true;
        txn.req.qos = 3;
        txn.req.srcid = TestRequester;
        txn.req.tgtid = TestHome;
        txn.req.txnid = TestTxn;
        txn.readExpectedBytes = TestBlockSize;
        return txn;
    }

    static bool
    accept(Cache2ChiBridge::TxnEntry& txn, const RawDat& dat)
    {
        return Cache2ChiBridge::acceptReadDataBeat(
            txn, dat, TestBeatSize, "Cache2ChiBridgeProtocolTest");
    }

    static bool
    complete(const Cache2ChiBridge::TxnEntry& txn)
    {
        return txn.gotData;
    }

    static uint32_t
    coveredBytes(const Cache2ChiBridge::TxnEntry& txn)
    {
        return txn.readDataBytes;
    }

    static const std::vector<uint8_t>&
    data(const Cache2ChiBridge::TxnEntry& txn)
    {
        return txn.readData;
    }

    static Cache2ChiBridge::SnoopEntry
    makePendingSnoopDataResponse()
    {
        Cache2ChiBridge::SnoopEntry snoop{};
        snoop.snp.srcid = TestHome;
        snoop.snp.txnid = TestTxn;
        const auto data = testLineData(0x80);
        for (uint32_t offset = 0; offset < TestBlockSize;
             offset += TestBeatSize) {
            RawDat dat{};
            dat.srcid = TestRequester;
            dat.tgtid = TestHome;
            dat.txnid = TestTxn;
            dat.dataid = static_cast<uint8_t>(offset / TestBeatSize);
            dat.beatOffset = offset;
            dat.last = offset + TestBeatSize == TestBlockSize;
            dat.data.assign(data.begin() + offset,
                            data.begin() + offset + TestBeatSize);
            snoop.dataBeats.push_back(std::move(dat));
        }
        return snoop;
    }

    static Cache2ChiBridge::SnoopEntry
    makePendingSnoopRsp()
    {
        Cache2ChiBridge::SnoopEntry snoop{};
        snoop.snp.srcid = TestHome;
        snoop.snp.txnid = TestTxn;
        RawRsp rsp{};
        rsp.srcid = TestRequester;
        rsp.tgtid = TestHome;
        rsp.txnid = TestTxn;
        snoop.pendingRsp = rsp;
        return snoop;
    }

    static bool
    advanceSnoopResponse(
        Cache2ChiBridge::SnoopEntry& snoop,
        const std::function<bool(ChannelType, const FlitVariant&)>& enqueue)
    {
        return Cache2ChiBridge::advancePendingSnoopResponse(
            snoop, enqueue);
    }

    static size_t
    nextSnoopDataBeat(const Cache2ChiBridge::SnoopEntry& snoop)
    {
        return snoop.nextDataBeat;
    }

    static std::optional<uint32_t>
    allocateTxnId(uint64_t& next_id, size_t outstanding,
                  uint32_t max_outstanding, uint32_t namespace_count = 1)
    {
        return Cache2ChiBridge::allocateMonotonicTxnId(
            next_id, outstanding, max_outstanding, namespace_count);
    }

    static uint64_t
    firstTxnId(uint32_t base, uint32_t namespace_id,
               uint32_t namespace_count)
    {
        return Cache2ChiBridge::firstTxnIdInNamespace(
            base, namespace_id, namespace_count);
    }
};

TEST(Cache2ChiBridgeProtocolTest,
     Case13NormalPromotedUpgradeKeepsUpgradeResponse)
{
    EXPECT_TRUE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        true, false, false));
}

TEST(Cache2ChiBridgeProtocolTest,
     PrecedingInvalidationRequiresDataBearingResponse)
{
    EXPECT_FALSE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        true, true, true));
}

TEST(Cache2ChiBridgeProtocolTest, SharedSnoopKeepsUpgradeResponse)
{
    EXPECT_TRUE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        true, true, false));
}

TEST(Cache2ChiBridgeProtocolTest, LateInvalidationKeepsUpgradeResponse)
{
    EXPECT_TRUE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        true, false, true));
}

TEST(Cache2ChiBridgeProtocolTest, OrdinaryReadNeverBecomesUpgradeResponse)
{
    EXPECT_FALSE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        false, true, true));
}

TEST(Cache2ChiBridgeProtocolTest, Cmn16HnfHashMatchesManualBitLanes)
{
    constexpr uint32_t HnfCount = 16;
    constexpr uint8_t PaBits = 48;
    for (unsigned outputBit = 0; outputBit < 4; ++outputBit) {
        for (unsigned bit = 6 + outputBit; bit < PaBits; bit += 4) {
            EXPECT_EQ(Cache2ChiBridge::cmnHnfIndex(
                          1ULL << bit, HnfCount, PaBits),
                      1U << outputBit);
        }
    }
}

TEST(Cache2ChiBridgeProtocolTest, Cmn16HnfHashBalancesConsecutiveLines)
{
    std::array<uint32_t, 16> counts{};
    for (uint32_t line = 0; line < 16 * 64; ++line) {
        ++counts[Cache2ChiBridge::cmnHnfIndex(
            static_cast<Addr>(line) << 6, counts.size(), 48)];
    }
    for (const uint32_t count : counts) {
        EXPECT_EQ(count, 64);
    }
}

TEST(Cache2ChiBridgeProtocolTest, CmnHashRejectsUnsupportedGeometry)
{
    EXPECT_ANY_THROW(Cache2ChiBridge::cmnHnfIndex(0, 0, 48));
    EXPECT_ANY_THROW(Cache2ChiBridge::cmnHnfIndex(0, 3, 48));
    EXPECT_ANY_THROW(Cache2ChiBridge::cmnHnfIndex(0, 128, 48));
    EXPECT_ANY_THROW(Cache2ChiBridge::cmnHnfIndex(0, 16, 5));
    EXPECT_ANY_THROW(Cache2ChiBridge::cmnHnfIndex(0, 16, 65));
}

TEST(Cache2ChiBridgeProtocolTest,
     QueuedCompAckCannotAliasNextReqWithOneOutstandingSlot)
{
    uint64_t next_id = 1;

    const auto old_req = Cache2ChiBridgeProtocolTestPeer::allocateTxnId(
        next_id, 0, 1);
    ASSERT_TRUE(old_req);
    EXPECT_FALSE(Cache2ChiBridgeProtocolTestPeer::allocateTxnId(
        next_id, 1, 1));

    // Model enqueueRx(RSP, old CompAck) followed by local freeTxn().  The
    // CompAck may still be in the independent RSP channel, but releasing the
    // sole outstanding slot must not make its ID eligible for the new REQ.
    const auto new_req = Cache2ChiBridgeProtocolTestPeer::allocateTxnId(
        next_id, 0, 1);
    ASSERT_TRUE(new_req);
    EXPECT_NE(*new_req, *old_req);
    EXPECT_EQ(*new_req, *old_req + 1);
}

TEST(Cache2ChiBridgeProtocolTest,
     OutstandingLimitDoesNotDefineTxnIdRecyclingWindow)
{
    uint64_t next_id = 1025;
    for (uint32_t expected = 1025; expected < 1153; ++expected) {
        const auto id = Cache2ChiBridgeProtocolTestPeer::allocateTxnId(
            next_id, 0, 1);
        ASSERT_TRUE(id);
        EXPECT_EQ(*id, expected);
    }
}

TEST(Cache2ChiBridgeProtocolTest,
     NonZeroBaseInterleavedNamespacesNeverCross)
{
    constexpr uint32_t NamespaceCount = 4;
    constexpr uint32_t BaseSpacing = 1024;
    std::array<uint64_t, NamespaceCount> next_ids{};
    std::unordered_set<uint32_t> allocated;

    for (uint32_t ns = 0; ns < NamespaceCount; ++ns) {
        next_ids[ns] = Cache2ChiBridgeProtocolTestPeer::firstTxnId(
            ns * BaseSpacing, ns, NamespaceCount);
        EXPECT_EQ((next_ids[ns] - 1) % NamespaceCount, ns);
    }

    for (uint32_t epoch = 0; epoch < 4096; ++epoch) {
        for (uint32_t ns = 0; ns < NamespaceCount; ++ns) {
            const auto id = Cache2ChiBridgeProtocolTestPeer::allocateTxnId(
                next_ids[ns], 0, 1, NamespaceCount);
            ASSERT_TRUE(id);
            EXPECT_EQ((*id - 1) % NamespaceCount, ns);
            EXPECT_TRUE(allocated.insert(*id).second)
                << "duplicate TxnID " << *id << " in namespace " << ns;
        }
    }
}

TEST(Cache2ChiBridgeProtocolTest, TxnIdExhaustionDoesNotWrap)
{
    uint64_t next_id = std::numeric_limits<uint32_t>::max() - 2ULL;
    const auto last = Cache2ChiBridgeProtocolTestPeer::allocateTxnId(
        next_id, 0, 1, 4);
    ASSERT_TRUE(last);
    EXPECT_EQ(*last, std::numeric_limits<uint32_t>::max() - 2U);
    EXPECT_FALSE(Cache2ChiBridgeProtocolTestPeer::allocateTxnId(
        next_id, 0, 1, 4));
}

TEST(Cache2ChiBridgeProtocolTest, TxnIdNamespaceGeometryIsValidated)
{
    EXPECT_ANY_THROW(Cache2ChiBridgeProtocolTestPeer::firstTxnId(0, 0, 0));
    EXPECT_ANY_THROW(Cache2ChiBridgeProtocolTestPeer::firstTxnId(0, 4, 4));
}

TEST(Cache2ChiBridgeProtocolTest, ReadDataAcceptsTerminalBeatFirst)
{
    auto txn = Cache2ChiBridgeProtocolTestPeer::makeReadTxn();
    const auto data = testLineData(0x40);

    EXPECT_FALSE(Cache2ChiBridgeProtocolTestPeer::accept(
        txn, makeReadDataBeat(TestBeatSize, true, data)));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn),
              TestBeatSize);
    EXPECT_TRUE(Cache2ChiBridgeProtocolTestPeer::accept(
        txn, makeReadDataBeat(0, false, data)));
    EXPECT_TRUE(Cache2ChiBridgeProtocolTestPeer::complete(txn));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::data(txn), data);
}

TEST(Cache2ChiBridgeProtocolTest,
     RejectedReadDataBeatsDoNotPolluteAssembly)
{
    auto txn = Cache2ChiBridgeProtocolTestPeer::makeReadTxn();
    const auto data = testLineData(0x60);

    RawDat wrong_identity = makeReadDataBeat(0, false, data);
    wrong_identity.srcid++;
    EXPECT_ANY_THROW(
        Cache2ChiBridgeProtocolTestPeer::accept(txn, wrong_identity));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn), 0);

    EXPECT_ANY_THROW(Cache2ChiBridgeProtocolTestPeer::accept(
        txn, makeReadDataBeat(0, true, data)));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn), 0);

    RawDat invalid_mask = makeReadDataBeat(0, false, data);
    invalid_mask.byteEnable[0] = 2;
    EXPECT_ANY_THROW(
        Cache2ChiBridgeProtocolTestPeer::accept(txn, invalid_mask));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn), 0);

    invalid_mask = makeReadDataBeat(0, false, data);
    invalid_mask.chunkValid[0] = 2;
    EXPECT_ANY_THROW(
        Cache2ChiBridgeProtocolTestPeer::accept(txn, invalid_mask));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn), 0);

    EXPECT_FALSE(Cache2ChiBridgeProtocolTestPeer::accept(
        txn, makeReadDataBeat(0, false, data)));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn),
              TestBeatSize);
    EXPECT_ANY_THROW(Cache2ChiBridgeProtocolTestPeer::accept(
        txn, makeReadDataBeat(0, false, data)));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn),
              TestBeatSize);

    EXPECT_ANY_THROW(Cache2ChiBridgeProtocolTestPeer::accept(
        txn, makeReadDataBeat(TestBeatSize, false, data)));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn),
              TestBeatSize);

    RawDat overlapping = makeReadDataBeat(TestBeatSize, true, data);
    overlapping.beatOffset = TestBeatSize / 2;
    EXPECT_ANY_THROW(
        Cache2ChiBridgeProtocolTestPeer::accept(txn, overlapping));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::coveredBytes(txn),
              TestBeatSize);

    EXPECT_TRUE(Cache2ChiBridgeProtocolTestPeer::accept(
        txn, makeReadDataBeat(TestBeatSize, true, data)));
    EXPECT_TRUE(Cache2ChiBridgeProtocolTestPeer::complete(txn));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::data(txn), data);
}

TEST(Cache2ChiBridgeProtocolTest,
     SnoopDataBackpressureRetriesWithoutDuplicateBeats)
{
    auto snoop =
        Cache2ChiBridgeProtocolTestPeer::makePendingSnoopDataResponse();
    size_t credits = 1;
    std::vector<FlitVariant> accepted;
    const auto enqueue = [&credits, &accepted](
                             ChannelType, const FlitVariant& flit) {
        if (credits == 0) {
            return false;
        }
        --credits;
        accepted.push_back(flit);
        return true;
    };

    EXPECT_FALSE(Cache2ChiBridgeProtocolTestPeer::advanceSnoopResponse(
        snoop, enqueue));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::nextSnoopDataBeat(snoop), 1);
    ASSERT_EQ(accepted.size(), 1);
    ASSERT_TRUE(std::holds_alternative<RawDat>(accepted[0]));
    EXPECT_EQ(std::get<RawDat>(accepted[0]).dataid, 0);

    credits = 1;
    EXPECT_TRUE(Cache2ChiBridgeProtocolTestPeer::advanceSnoopResponse(
        snoop, enqueue));
    EXPECT_EQ(Cache2ChiBridgeProtocolTestPeer::nextSnoopDataBeat(snoop), 2);
    ASSERT_EQ(accepted.size(), 2);
    ASSERT_TRUE(std::holds_alternative<RawDat>(accepted[1]));
    EXPECT_EQ(std::get<RawDat>(accepted[1]).dataid, 1);
    EXPECT_TRUE(std::get<RawDat>(accepted[1]).last);
}

TEST(Cache2ChiBridgeProtocolTest,
     SnoopRspBackpressureRetriesExactlyOnce)
{
    auto snoop = Cache2ChiBridgeProtocolTestPeer::makePendingSnoopRsp();
    size_t credits = 0;
    std::vector<FlitVariant> accepted;
    const auto enqueue = [&credits, &accepted](
                             ChannelType, const FlitVariant& flit) {
        if (credits == 0) {
            return false;
        }
        --credits;
        accepted.push_back(flit);
        return true;
    };

    EXPECT_FALSE(Cache2ChiBridgeProtocolTestPeer::advanceSnoopResponse(
        snoop, enqueue));
    EXPECT_TRUE(accepted.empty());

    credits = 1;
    EXPECT_TRUE(Cache2ChiBridgeProtocolTestPeer::advanceSnoopResponse(
        snoop, enqueue));
    ASSERT_EQ(accepted.size(), 1);
    ASSERT_TRUE(std::holds_alternative<RawRsp>(accepted[0]));
    EXPECT_EQ(std::get<RawRsp>(accepted[0]).txnid, TestTxn);
}

} // namespace Chi
} // namespace gem5

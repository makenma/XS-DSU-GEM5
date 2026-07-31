#include <gtest/gtest.h>

#include <cstddef>
#include <cstdint>
#include <unordered_map>
#include <vector>

#include "mem/cache/CHI/Chi2ClassicMemBridgeTxnKey.hh"
#include "mem/cache/CHI/Chi2ClassicMemTxnPolicy.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/cache/CHI/base/RspOpcode.hh"

namespace gem5
{
namespace Chi
{
namespace
{

using Policy = Chi2ClassicMemTxnPolicy;

RawReq
makeWriteReq()
{
    RawReq req{};
    req.qos = 5;
    req.srcid = 0x12;
    req.tgtid = 0x40;
    req.txnid = 0x31;
    req.opcode = Policy::WriteNoSnpFullOpcode;
    req.addr = 0x8000;
    req.size = 64;
    req.pcrdtype = 3;
    return req;
}

RawDat
makeWriteDat(const RawReq& req, uint8_t dbid)
{
    RawDat dat{};
    dat.qos = req.qos;
    dat.srcid = req.srcid;
    dat.tgtid = req.tgtid;
    dat.txnid = req.txnid;
    dat.opcode = Policy::NonCopyBackWriteDataOpcode;
    dat.last = 1;
    dat.HomeNID = req.srcid;
    dat.dbid = dbid;
    dat.dataid = 0;
    dat.beatOffset = 0;
    dat.data.resize(64);
    for (size_t index = 0; index < dat.data.size(); ++index) {
        dat.data[index] = static_cast<uint8_t>(index ^ 0xa5);
    }
    dat.byteEnable.assign(64, 1);
    dat.chunkValid.assign(8, 1);
    return dat;
}

TEST(Chi2ClassicMemBridgePolicyTest,
     WriteRequestAndDbidResponseMetadataAreExact)
{
    const RawReq req = makeWriteReq();
    ASSERT_TRUE(Policy::isWriteNoSnpFull(req, 64));
    EXPECT_EQ(decodeReq(req.opcode).minor, ReqMinor::WriteNoSnp);
    EXPECT_EQ(Policy::expectedDataBytes(req, 64), 64U);

    RawReq invalid = req;
    invalid.opcode = Policy::ReadNoSnpOpcode;
    EXPECT_FALSE(Policy::isWriteNoSnpFull(invalid, 64));
    invalid = req;
    invalid.addr += 32;
    EXPECT_FALSE(Policy::isWriteNoSnpFull(invalid, 64));
    invalid = req;
    invalid.size = 32;
    EXPECT_FALSE(Policy::isWriteNoSnpFull(invalid, 64));

    constexpr uint8_t dbid = 7;
    const RawRsp rsp = Policy::makeDbidResp(req, dbid, 0x44, 0x10);
    EXPECT_EQ(decodeRsp(rsp.opcode).minor, RspMinor::DBIDResp);
    EXPECT_EQ(rsp.opcode, Policy::DBIDRespOpcode);
    EXPECT_EQ(rsp.qos, req.qos);
    EXPECT_EQ(rsp.srcid, 0x44U);
    EXPECT_EQ(rsp.tgtid, 0x10U);
    EXPECT_EQ(rsp.txnid, req.txnid);
    EXPECT_EQ(rsp.dbid, dbid);
    EXPECT_EQ(rsp.resp, Policy::RespSC);
    EXPECT_EQ(rsp.respErr, 0);
    EXPECT_EQ(rsp.pcrdtype, req.pcrdtype);

    const auto premature = Policy::transition(
        Policy::Phase::WriteDataPending,
        Policy::Event::ClassicRequestSent);
    EXPECT_FALSE(premature);
}

TEST(Chi2ClassicMemBridgePolicyTest,
     WriteDataRequiresExactIdentityAndFullLineMetadata)
{
    const RawReq req = makeWriteReq();
    constexpr uint8_t dbid = 9;
    const RawDat valid = makeWriteDat(req, dbid);
    ASSERT_TRUE(Policy::isExactWriteData(valid, req, dbid, 64, 0x40));
    EXPECT_EQ(decodeDat(valid.opcode).minor,
              DatMinor::NonCopyBackWriteData);

    RawDat invalid = valid;
    invalid.txnid++;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.dbid++;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.srcid++;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.tgtid++;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.HomeNID++;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.qos++;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.opcode = Policy::CompDataOpcode;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.last = 0;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.dataid = 1;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.beatOffset = 32;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.data.pop_back();
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.byteEnable[17] = 0;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));
    invalid = valid;
    invalid.chunkValid[3] = 0;
    EXPECT_FALSE(Policy::isExactWriteData(invalid, req, dbid, 64, 0x40));

    const auto accepted = Policy::transition(
        Policy::Phase::WriteDataPending,
        Policy::Event::WriteDataAccepted);
    ASSERT_TRUE(accepted);
    EXPECT_EQ(*accepted, Policy::Phase::MemReqQueued);
}

TEST(Chi2ClassicMemBridgePolicyTest,
     DirtyVictimTraceHashIsStableAndPayloadSensitive)
{
    std::vector<uint8_t> line(64);
    for (size_t index = 0; index < line.size(); ++index) {
        line[index] = static_cast<uint8_t>(index);
    }

    EXPECT_EQ(Policy::traceDataHash({}), 0xcbf29ce484222325ULL);
    EXPECT_EQ(Policy::traceDataHash(line), 0x8368214f77995ee5ULL);

    std::vector<uint8_t> changed = line;
    changed[37] ^= 0x80;
    EXPECT_NE(Policy::traceDataHash(changed),
              Policy::traceDataHash(line));
    changed[37] ^= 0x80;
    EXPECT_EQ(Policy::traceDataHash(changed),
              Policy::traceDataHash(line));
}

TEST(Chi2ClassicMemBridgePolicyTest,
     DirtyVictimTraceRequiresExplicitProducerProvenance)
{
    RawReq ordinary = makeWriteReq();
    ordinary.txnid = 0x40000000U;
    ordinary.traceTag = true;
    EXPECT_FALSE(Policy::isHnfDirtyVictimWriteback(ordinary, 64));

    RawReq dirtyVictim = ordinary;
    dirtyVictim.hnfDirtyVictim = true;
    EXPECT_TRUE(Policy::isHnfDirtyVictimWriteback(dirtyVictim, 64));

    dirtyVictim.opcode = Policy::ReadNoSnpOpcode;
    EXPECT_FALSE(Policy::isHnfDirtyVictimWriteback(dirtyVictim, 64));
}

TEST(Chi2ClassicMemBridgePolicyTest,
     ClassicRequestBackpressureRetainsPacketPhase)
{
    const auto blocked = Policy::transition(
        Policy::Phase::MemReqQueued,
        Policy::Event::ClassicRequestSent, false);
    ASSERT_TRUE(blocked);
    EXPECT_EQ(*blocked, Policy::Phase::MemReqQueued);

    const auto sent = Policy::transition(
        Policy::Phase::MemReqQueued,
        Policy::Event::ClassicRequestSent, true);
    ASSERT_TRUE(sent);
    EXPECT_EQ(*sent, Policy::Phase::MemRespPending);

    EXPECT_FALSE(Policy::transition(
        Policy::Phase::WriteDataPending,
        Policy::Event::ClassicRequestSent, false));
}

TEST(Chi2ClassicMemBridgePolicyTest,
     CompRequiresClassicResponseAndChiBackpressureRetainsIt)
{
    const RawReq req = makeWriteReq();
    constexpr uint8_t dbid = 11;

    EXPECT_FALSE(Policy::transition(
        Policy::Phase::MemRespPending,
        Policy::Event::ChiResponseSent));

    const auto classicResp = Policy::transition(
        Policy::Phase::MemRespPending,
        Policy::Event::ClassicResponseReceived);
    ASSERT_TRUE(classicResp);
    EXPECT_EQ(*classicResp, Policy::Phase::ChiResponsePending);

    const RawRsp comp = Policy::makeComp(req, dbid, 0x44, 0x10, false);
    EXPECT_EQ(decodeRsp(comp.opcode).minor, RspMinor::Comp);
    EXPECT_EQ(comp.opcode, Policy::CompOpcode);
    EXPECT_EQ(comp.srcid, 0x44U);
    EXPECT_EQ(comp.tgtid, 0x10U);
    EXPECT_EQ(comp.txnid, req.txnid);
    EXPECT_EQ(comp.dbid, dbid);
    EXPECT_EQ(comp.resp, Policy::RespSC);
    EXPECT_EQ(comp.respErr, 0);

    const auto blocked = Policy::transition(
        *classicResp, Policy::Event::ChiResponseSent, false);
    ASSERT_TRUE(blocked);
    EXPECT_EQ(*blocked, Policy::Phase::ChiResponsePending);
    const auto sent = Policy::transition(
        *classicResp, Policy::Event::ChiResponseSent, true);
    ASSERT_TRUE(sent);
    EXPECT_EQ(*sent, Policy::Phase::Complete);

    const RawRsp errorComp =
        Policy::makeComp(req, dbid, 0x44, 0x10, true);
    EXPECT_EQ(errorComp.respErr, 1);
}

TEST(Chi2ClassicMemBridgePolicyTest,
     ReadNoSnpRegressionUsesOrderedClassicLifecycle)
{
    RawReq req{};
    req.opcode = Policy::ReadNoSnpOpcode;
    req.txnid = 0x52;
    req.addr = 0x9000;
    req.size = 64;
    ASSERT_EQ(decodeReq(req.opcode).minor, ReqMinor::ReadNoSnp);
    EXPECT_EQ(decodeDat(Policy::CompDataOpcode).minor, DatMinor::CompData);

    const auto memPending = Policy::transition(
        Policy::Phase::MemReqQueued,
        Policy::Event::ClassicRequestSent);
    ASSERT_TRUE(memPending);
    EXPECT_EQ(*memPending, Policy::Phase::MemRespPending);
    EXPECT_FALSE(Policy::transition(
        *memPending, Policy::Event::ChiResponseSent));

    const auto datPending = Policy::transition(
        *memPending, Policy::Event::ClassicResponseReceived);
    ASSERT_TRUE(datPending);
    EXPECT_EQ(*datPending, Policy::Phase::ChiResponsePending);

    const auto blocked = Policy::transition(
        *datPending, Policy::Event::ChiResponseSent, false);
    ASSERT_TRUE(blocked);
    EXPECT_EQ(*blocked, Policy::Phase::ChiResponsePending);
    const auto complete = Policy::transition(
        *datPending, Policy::Event::ChiResponseSent, true);
    ASSERT_TRUE(complete);
    EXPECT_EQ(*complete, Policy::Phase::Complete);
}

TEST(Chi2ClassicMemBridgeIdentityTest,
     SameTxnIdFromDifferentHnfsRemainIndependent)
{
    RawReq first = makeWriteReq();
    first.srcid = 0x04;
    first.txnid = 0x31;
    RawReq second = first;
    second.srcid = 0x84;

    const Chi2ClassicTxnKey firstKey =
        Chi2ClassicTxnKey::fromReq(first);
    const Chi2ClassicTxnKey secondKey =
        Chi2ClassicTxnKey::fromReq(second);
    EXPECT_FALSE(firstKey == secondKey);

    std::unordered_map<Chi2ClassicTxnKey, RawReq,
                       Chi2ClassicTxnKeyHash> active;
    ASSERT_TRUE(active.emplace(firstKey, first).second);
    ASSERT_TRUE(active.emplace(secondKey, second).second);
    EXPECT_EQ(active.size(), 2U);
    EXPECT_EQ(active.at(firstKey).srcid, first.srcid);
    EXPECT_EQ(active.at(secondKey).srcid, second.srcid);
    EXPECT_EQ(Chi2ClassicTxnKey::responseTarget(first, 0), first.srcid);
    EXPECT_EQ(Chi2ClassicTxnKey::responseTarget(second, 0), second.srcid);
    EXPECT_EQ(Chi2ClassicTxnKey::responseTarget(first, 0x104), 0x104U);

    RawDat firstDat = makeWriteDat(first, 7);
    RawDat secondDat = makeWriteDat(second, 8);
    EXPECT_EQ(Chi2ClassicTxnKey::fromWriteData(firstDat), firstKey);
    EXPECT_EQ(Chi2ClassicTxnKey::fromWriteData(secondDat), secondKey);

    // hnf_node_id == 0 selects the source HN-F of each original request.
    // Thus a shared SN bridge returns same-numbered transactions to their
    // respective HN-Fs rather than collapsing both onto one destination.
    const RawRsp firstComp =
        Policy::makeComp(first, firstDat.dbid, 0x280, 0, false);
    const RawRsp secondComp =
        Policy::makeComp(second, secondDat.dbid, 0x280, 0, false);
    EXPECT_EQ(firstComp.txnid, secondComp.txnid);
    EXPECT_EQ(firstComp.tgtid, first.srcid);
    EXPECT_EQ(secondComp.tgtid, second.srcid);
    EXPECT_NE(firstComp.tgtid, secondComp.tgtid);
}

TEST(Chi2ClassicMemBridgeIdentityTest,
     DuplicateMeansSameSourceAndTxnId)
{
    RawReq original = makeWriteReq();
    const Chi2ClassicTxnKey originalKey =
        Chi2ClassicTxnKey::fromReq(original);

    RawReq sameIdentity = original;
    sameIdentity.addr += 0x1000;
    EXPECT_EQ(Chi2ClassicTxnKey::fromReq(sameIdentity), originalKey);

    RawReq independent = original;
    independent.srcid++;
    EXPECT_NE(Chi2ClassicTxnKey::fromReq(independent), originalKey);
}

} // anonymous namespace
} // namespace Chi
} // namespace gem5

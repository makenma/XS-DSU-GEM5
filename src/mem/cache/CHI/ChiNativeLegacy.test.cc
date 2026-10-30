#include <gtest/gtest.h>

#include "mem/cache/CHI/ChiNativeLegacy.hh"

namespace gem5::Chi
{

TEST(ChiNativeLegacy, LargeHomeTokensSurviveBackpressureAndReuse)
{
    ChiNativeLegacy adapter;
    for (uint32_t token = 100000; token < 105000; ++token) {
        RawDat dat;
        dat.srcid = 16;
        dat.tgtid = 128;
        dat.txnid = 7;
        dat.dbid = token;
        dat.HomeNID = 16;
        dat.opcode = 4;
        dat.resp = 2;
        std::array<uint8_t, 32> bytes{};
        bytes.fill(token & 255);
        dat.setPayload(bytes.data(), bytes.size());
        const auto native = adapter.receive(dat);
        EXPECT_LE(native.dbid, NativeWire::MaxTxnID);
        EXPECT_TRUE(NativeWire::validate(native).empty());
        EXPECT_EQ(native.data[0], token & 255);
        NativeRsp ack;
        ack.nativeChi = true;
        ack.srcid = 128;
        ack.tgtid = 16;
        ack.txnid = native.dbid;
        ack.opcode = 2;
        // Failed enqueue attempts retain the mapping until actual acceptance.
        for (int retry = 0; retry < 3; ++retry) {
            const auto wire = std::get<RawRsp>(adapter.transmit(ack));
            EXPECT_EQ(wire.txnid, token);
            EXPECT_EQ(adapter.pendingTokens(), 1);
        }
        adapter.accepted(ack);
        EXPECT_EQ(adapter.pendingTokens(), 0);
    }
}

TEST(ChiNativeLegacy, SnoopAddressAndTwoBeatDirtyResponse)
{
    ChiNativeLegacy adapter;
    RawSnp snoop;
    snoop.srcid = 144;
    snoop.tgtid = 0;
    snoop.txnid = 0x12345678;
    snoop.addr = 0x81234540;
    snoop.opcode = 7;
    const auto native = adapter.receive(snoop);
    EXPECT_EQ(NativeWire::physicalSnoopAddress(native.addr), snoop.addr);
    EXPECT_TRUE(NativeWire::validate(native).empty());
    NativeDat response;
    response.nativeChi = true;
    response.srcid = 0;
    response.tgtid = 144;
    response.txnid = native.txnid;
    response.opcode = 1;
    response.resp = 4;
    std::vector<uint8_t> bytes(64), enables(64, 1);
    for (unsigned i = 0; i < bytes.size(); ++i)
        bytes[i] = i;
    const auto beats = NativeWire::packData(
        response, bytes, enables, snoop.addr, 64);
    ASSERT_EQ(beats.size(), 2);
    for (unsigned i = 0; i < beats.size(); ++i) {
        auto wire = std::get<RawDat>(adapter.transmit(beats[i]));
        EXPECT_EQ(wire.txnid, snoop.txnid);
        EXPECT_EQ(wire.dataid, 2 * i);
        EXPECT_TRUE(wire.passDirty);
        EXPECT_EQ(wire.payloadSize(), 32);
        EXPECT_EQ(wire.payloadData()[0], 32 * i);
        // The router copies flits; their owned payload must survive copying.
        const auto copy = wire;
        wire.payload.fill(0);
        EXPECT_EQ(copy.payloadData()[31], 32 * i + 31);
        adapter.accepted(beats[i]);
        EXPECT_EQ(adapter.pendingTokens(), i == 0 ? 1 : 0);
    }
}

TEST(ChiNativeLegacy, HomesHaveIndependentTokenNamespaces)
{
    ChiNativeLegacy adapter;
    RawRsp grant;
    grant.opcode = 6;
    grant.dbid = 90000;
    grant.srcid = 16;
    const auto first = adapter.receive(grant);
    grant.srcid = 144;
    const auto second = adapter.receive(grant);
    EXPECT_EQ(adapter.pendingTokens(), 2);
    for (const auto &native : {first, second}) {
        NativeRsp ack;
        ack.tgtid = native.srcid;
        ack.txnid = native.dbid;
        ack.opcode = 2;
        EXPECT_EQ(std::get<RawRsp>(adapter.transmit(ack)).txnid, 90000);
        adapter.accepted(ack);
    }
    EXPECT_EQ(adapter.pendingTokens(), 0);
}

} // namespace gem5::Chi

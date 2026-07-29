#include <gtest/gtest.h>

#include <array>

#include "mem/cache/CHI/HomeLinkLayer.hh"

namespace gem5::Chi
{
namespace
{

RawReq
request(uint32_t srcid, uint32_t txnid, uint8_t pcrdtype, bool allow_retry)
{
    RawReq req{};
    req.srcid = srcid;
    req.txnid = txnid;
    req.pcrdtype = pcrdtype;
    req.AllowRetry = allow_retry;
    return req;
}

TEST(HomeLinkLayerDrainTest, QuiesceAllowsMatchingStaticEntitlement)
{
    HomeLinkLayer link(nullptr, 64, 32, 4, true);
    const RawReq ordinary = request(7, 11, 3, true);

    EXPECT_TRUE(link.acceptsIncomingFlit(ChannelType::REQ, ordinary));
    link.quiesceNewRequests();
    EXPECT_FALSE(link.acceptsIncomingFlit(ChannelType::REQ, ordinary));

    RawRsp completion{};
    EXPECT_TRUE(link.acceptsIncomingFlit(ChannelType::RSP, completion));

    link.installStaticRetryReservationForTest(7, 3);
    const RawReq reissue = request(7, 11, 3, false);
    EXPECT_TRUE(link.acceptsIncomingFlit(ChannelType::REQ, reissue));
    // A retry may replace its transaction ID. Static PCrd entitlement is
    // identified by requester SrcID and PCrdType only.
    EXPECT_TRUE(link.acceptsIncomingFlit(
        ChannelType::REQ, request(7, 12, 3, false)));
    EXPECT_FALSE(link.acceptsIncomingFlit(
        ChannelType::REQ, request(8, 11, 3, false)));
    EXPECT_FALSE(link.acceptsIncomingFlit(
        ChannelType::REQ, request(7, 11, 2, false)));
    EXPECT_FALSE(link.acceptsIncomingFlit(
        ChannelType::REQ, request(7, 11, 3, true)));

    // Waiting for the reissue is owned state, not a reason to poll each cycle.
    EXPECT_TRUE(link.hasProtocolOwnershipForDrain());
    EXPECT_TRUE(link.mayGenerateSlcsfIntent());
    EXPECT_FALSE(link.hasWork());
}

TEST(HomeLinkLayerDrainTest, PendingRetryBlocksDrainWithoutBusyPolling)
{
    HomeLinkLayer link(nullptr, 64, 32, 4, true);
    link.installPendingRetryForTest(9, 2);

    EXPECT_TRUE(link.hasProtocolOwnershipForDrain());
    EXPECT_TRUE(link.mayGenerateSlcsfIntent());
    EXPECT_FALSE(link.hasWork());
}

TEST(HomeLinkLayerDrainTest, QuiesceDoesNotHideAcceptedRequestPipeline)
{
    HomeLinkLayer link(nullptr, 64, 32, 4, true);
    link.installRequestPipelineEntryForTest(request(5, 17, 0, true));
    link.quiesceNewRequests();

    EXPECT_TRUE(link.hasWork());
    EXPECT_TRUE(link.hasProtocolOwnershipForDrain());
    EXPECT_TRUE(link.mayGenerateSlcsfIntent());
}

TEST(ChiCommonPortTest, PublicChannelApisRejectOutOfRangeChannel)
{
    ChiCommonPort port("test-port", nullptr);
    const ChannelType invalid =
        static_cast<ChannelType>(ChannelType::NUM_CHANNELS);
    const FlitVariant flit = RawReq{};

    EXPECT_ANY_THROW(port.increaseRxCredit(invalid));
    EXPECT_ANY_THROW(port.increaseTxCredit(invalid));
    EXPECT_ANY_THROW(port.checkRxCredit(invalid));
    EXPECT_ANY_THROW(port.checkTxCredit(invalid));
    EXPECT_ANY_THROW(port.enqueueRx(invalid, flit));
    EXPECT_ANY_THROW(port.enqueueTx(invalid, flit));
    EXPECT_ANY_THROW(port.getRxFlit(invalid));
    EXPECT_ANY_THROW(port.getRxFlitNoCredit(invalid));
    EXPECT_ANY_THROW(port.returnRxCredit(invalid));
    EXPECT_ANY_THROW(port.hasRxFlit(invalid));
    EXPECT_ANY_THROW(port.hasTxFlit(invalid));
    EXPECT_ANY_THROW(port.hasTxCredit(invalid));
    EXPECT_ANY_THROW(port.peerHasRxCredit(invalid));
    EXPECT_ANY_THROW(port.peerHasTxCredit(invalid));
    EXPECT_ANY_THROW(port.getTxFlit(invalid));
}

TEST(ChiCommonPortTest, RejectedRxAdmissionDoesNotConsumeCredit)
{
    constexpr size_t numChannels =
        static_cast<size_t>(ChannelType::NUM_CHANNELS);
    const std::array<uint8_t, numChannels> oneCredit{1, 1, 1, 1};
    ChiCommonPort port(
        "test-port", nullptr, InvalidPortID, oneCredit);
    port.setRxAdmissionCallback(
        [](ChannelType, const FlitVariant&) { return false; });

    EXPECT_TRUE(port.peerHasRxCredit(ChannelType::REQ));
    EXPECT_FALSE(port.enqueueRx(ChannelType::REQ, RawReq{}));
    EXPECT_TRUE(port.peerHasRxCredit(ChannelType::REQ));
    EXPECT_FALSE(port.hasRxFlit(ChannelType::REQ));
}

} // anonymous namespace
} // namespace gem5::Chi

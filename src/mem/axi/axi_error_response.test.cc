#include <gtest/gtest.h>

#include <cstdint>
#include <stdexcept>

#include "mem/axi/axi_target_adapter.hh"
#include "mem/axi/axi_validation.hh"

namespace gem5
{
namespace axi
{

namespace
{

AxiAddressRequest
request(uint64_t address, uint16_t beats = 1, uint8_t size = 3,
        uint32_t id = 0)
{
    AxiAddressRequest value;
    value.axiId = id;
    value.address = address;
    value.beatCount = beats;
    value.size = size;
    value.burst = AxiBurst::Incr;
    return value;
}

AxiAddressPacket
addressPacket(uint64_t uid, uint32_t target, const AxiAddressRequest &req,
              AxiResp decode_response)
{
    AxiAddressPacket packet;
    packet.meta.txnUid = uid;
    packet.meta.targetSeq = 0;
    packet.meta.responseSeq = 0;
    packet.meta.srcNode = 0;
    packet.meta.srcPort = 0;
    packet.meta.dstNode = target;
    packet.meta.axiId = req.axiId;
    packet.request = req;
    packet.writeOrdinal = uid;
    packet.decodeResp = decode_response;
    return packet;
}

AxiDataPacket
wPacket(const AxiAddressPacket &aw, uint16_t index, uint8_t value)
{
    AxiDataPacket packet;
    packet.meta = aw.meta;
    packet.writeOrdinal = aw.writeOrdinal;
    packet.beatIndex = index;
    packet.beatCount = aw.request.beatCount;
    packet.last = index + 1 == packet.beatCount;
    packet.byteStrobe = 0xff;
    packet.resp = aw.decodeResp;
    packet.address = aw.request.address;
    packet.functionalData.assign(8, value);
    packet.payloadDigest = payloadDigest(packet.functionalData);
    return packet;
}

AxiTargetConfig
targetConfig(uint32_t node, bool with_memory)
{
    AxiTargetConfig config;
    config.dstNode = node;
    config.dataBusBytes = 8;
    config.capacity = {8, 32, 8, 32};
    config.orphanTransactions = 4;
    config.orphanBeats = 16;
    config.bReadyDepth = 8;
    config.rReadyDepth = 32;
    config.writeServiceDepth = 8;
    config.readServiceDepth = 8;
    config.writeBaseLatency = 0;
    config.readBaseLatency = 0;
    config.sourceQuotas[{0, 0}] = {8, 32, 8, 32};
    if (with_memory)
        config.memoryRanges = {{0, 0x1000, node}};
    return config;
}

} // anonymous namespace

TEST(AxiErrorResponseTest, DecodeMissReadFullDecerr)
{
    const AxiAddressRequest req = request(0x3000, 4);
    const auto validation = validateAxiBurst(req, 8);
    AxiAddressDecoder decoder({{0, 0x1000, 1}}, 2);
    const auto decoded = decoder.decode(req, validation);
    ASSERT_EQ(decoded.dstNode, 2);
    ASSERT_EQ(decoded.response, AxiResp::DecErr);

    AxiTargetState error_target(targetConfig(2, false));
    const auto ar = addressPacket(1, 2, req, decoded.response);
    error_target.acceptAr(ar, 0);
    for (uint16_t index = 0; index < req.beatCount; ++index) {
        ASSERT_TRUE(error_target.hasRPacket());
        const auto &response = error_target.frontRPacket();
        EXPECT_EQ(response.resp, AxiResp::DecErr);
        EXPECT_EQ(response.beatIndex, index);
        EXPECT_EQ(response.last, index + 1 == req.beatCount);
        EXPECT_EQ(response.functionalData,
                  std::vector<uint8_t>(8, 0));
        error_target.popRPacket();
    }
}

TEST(AxiErrorResponseTest, DecodeMissWriteDrainsThenDecerr)
{
    AxiTargetState error_target(targetConfig(2, false));
    const auto aw = addressPacket(
        2, 2, request(0x3000, 2), AxiResp::DecErr);
    error_target.acceptAw(aw, 0);
    error_target.acceptW(wPacket(aw, 0, 0x11), 0);
    EXPECT_FALSE(error_target.hasBPacket());
    error_target.acceptW(wPacket(aw, 1, 0x22), 0);
    ASSERT_TRUE(error_target.hasBPacket());
    EXPECT_EQ(error_target.frontBPacket().resp, AxiResp::DecErr);
    EXPECT_EQ(error_target.completedWrites(), 1);
}

TEST(AxiErrorResponseTest, RangeTailRoutesWholeBurstToError)
{
    const auto req = request(0x17c, 2, 2);
    const auto validation = validateAxiBurst(req, 8);
    ASSERT_TRUE(validation.okay());
    AxiAddressDecoder decoder({{0x100, 0x180, 1}}, 2);
    const auto decoded = decoder.decode(req, validation);
    EXPECT_EQ(decoded.dstNode, 2);
    EXPECT_EQ(decoded.response, AxiResp::DecErr);
}

TEST(AxiErrorResponseTest, TargetFaultReturnsSlverr)
{
    auto config = targetConfig(1, true);
    config.transactionFaults[10] = AxiResp::SlvErr;
    AxiTargetState target(config);
    const auto aw = addressPacket(10, 1, request(0x100), AxiResp::Okay);
    target.acceptAw(aw, 0);
    target.acceptW(wPacket(aw, 0, 0xaa), 0);
    ASSERT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.frontBPacket().resp, AxiResp::SlvErr);
}

TEST(AxiErrorResponseTest, ErrorWriteCommitsNoBytes)
{
    auto config = targetConfig(1, true);
    config.transactionFaults[11] = AxiResp::SlvErr;
    AxiTargetState target(config);
    target.memory().writeByte(0x100, 0x5a);
    const auto aw = addressPacket(11, 1, request(0x100), AxiResp::Okay);
    target.acceptAw(aw, 0);
    target.acceptW(wPacket(aw, 0, 0xee), 0);
    ASSERT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.frontBPacket().resp, AxiResp::SlvErr);
    EXPECT_EQ(target.memory().readByte(0x100), 0x5a);
}

TEST(AxiErrorResponseTest, ErrorReadReturnsZeroData)
{
    auto config = targetConfig(1, true);
    config.transactionFaults[12] = AxiResp::SlvErr;
    AxiTargetState target(config);
    target.memory().writeByte(0x100, 0xa5);
    const auto ar = addressPacket(12, 1, request(0x100), AxiResp::Okay);
    target.acceptAr(ar, 0);
    ASSERT_TRUE(target.hasRPacket());
    const auto &response = target.frontRPacket();
    EXPECT_EQ(response.resp, AxiResp::SlvErr);
    EXPECT_EQ(response.functionalData, std::vector<uint8_t>(8, 0));
    EXPECT_EQ(response.payloadDigest,
              payloadDigest(std::vector<uint8_t>(8, 0)));
}

TEST(AxiErrorResponseTest, NeverProducesExOkay)
{
    auto invalid = targetConfig(1, true);
    invalid.transactionFaults[13] = AxiResp::ExOkay;
    EXPECT_THROW(AxiTargetState target(invalid), std::invalid_argument);

    AxiTargetState error_target(targetConfig(2, false));
    const auto ar = addressPacket(
        14, 2, request(0x3000), AxiResp::DecErr);
    error_target.acceptAr(ar, 0);
    ASSERT_TRUE(error_target.hasRPacket());
    EXPECT_NE(error_target.frontRPacket().resp, AxiResp::ExOkay);
}

} // namespace axi
} // namespace gem5

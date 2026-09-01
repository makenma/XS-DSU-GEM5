#include <gtest/gtest.h>

#include <cstdint>
#include <limits>
#include <vector>

#include "mem/axi/axi_validation.hh"

namespace gem5
{
namespace axi
{

namespace
{

AxiAddressRequest
request(uint64_t address, uint16_t beats, uint8_t size)
{
    AxiAddressRequest value;
    value.axiId = 3;
    value.address = address;
    value.beatCount = beats;
    value.size = size;
    value.burst = AxiBurst::Incr;
    return value;
}

AxiWBeat
beat(uint32_t data_bus_bytes, uint64_t strobe, bool last)
{
    AxiWBeat value;
    value.last = last;
    value.byteStrobe = strobe;
    value.functionalData.resize(data_bus_bytes, 0xa5);
    value.payloadDigest = payloadDigest(value.functionalData);
    return value;
}

} // anonymous namespace

TEST(AxiBurstValidationTest, AcceptsIncrLengths)
{
    for (const uint16_t count : {uint16_t{1}, uint16_t{2}, uint16_t{256}}) {
        const auto result = validateAxiBurst(request(0x1000, count, 2), 64);
        EXPECT_TRUE(result.okay()) << result.error;
        EXPECT_EQ(result.beatBytes, 4);
        EXPECT_EQ(result.spanBytes, static_cast<uint64_t>(count) * 4);
    }
}

TEST(AxiBurstValidationTest, AcceptsDataWidths)
{
    for (const auto &[bytes, size] :
         std::vector<std::pair<uint32_t, uint8_t>>{
             {8, 3}, {16, 4}, {32, 5}, {64, 6}}) {
        const auto result = validateAxiBurst(request(0x800, 1, size), bytes);
        EXPECT_TRUE(result.okay()) << result.error;
        EXPECT_EQ(result.beatBytes, bytes);
    }
}

TEST(AxiBurstValidationTest, MapsNarrowNonZeroLanes)
{
    const auto narrow = request(0x24, 1, 2);
    ASSERT_TRUE(validateAxiBurst(narrow, 64).okay());
    EXPECT_EQ(axiBeatAddress(narrow, 0), 0x24);
    EXPECT_EQ(axiLegalLaneMask(narrow, 0, 64), 0xfULL << 36);

    const auto valid = beat(64, 0x5ULL << 36, true);
    EXPECT_TRUE(validateAxiWriteBeat(narrow, 0, valid, 64).empty());
}

TEST(AxiBurstValidationTest, RejectsOutOfLaneStrobe)
{
    const auto narrow = request(0x24, 1, 2);
    const auto invalid = beat(64, (0xfULL << 36) | 1, true);
    const auto error = validateAxiWriteBeat(narrow, 0, invalid, 64);
    EXPECT_NE(error.find("WSTRB selects a lane outside"), std::string::npos);
    EXPECT_ANY_THROW(requireValidAxiWriteBeat(narrow, 0, invalid, 64));
}

TEST(AxiBurstValidationTest, RoutesUnalignedToDecerr)
{
    const auto result = validateAxiBurst(request(0x22, 1, 2), 64);
    EXPECT_TRUE(result.decerr());
    EXPECT_EQ(result.response, AxiResp::DecErr);

    AxiAddressDecoder decoder({{0, 0x1000, 7}}, 99);
    const auto decoded = decoder.decode(request(0x22, 1, 2), result);
    EXPECT_EQ(decoded.dstNode, 99);
    EXPECT_EQ(decoded.response, AxiResp::DecErr);
}

TEST(AxiBurstValidationTest, Rejects4KiBCrossing)
{
    const auto result = validateAxiBurst(request(0xff8, 2, 3), 64);
    ASSERT_TRUE(result.protocolError());
    EXPECT_NE(result.error.find("crosses a 4 KiB boundary"),
              std::string::npos);
    EXPECT_ANY_THROW(requireValidAxiBurst(result));

    const auto bad_size = validateAxiBurst(request(0, 1, 7), 64);
    EXPECT_NE(bad_size.error.find("SIZE exceeds the configured data bus"),
              std::string::npos);
    EXPECT_ANY_THROW(requireValidAxiBurst(bad_size));
}

TEST(AxiBurstValidationTest, RoutesUnsupportedFeaturesToDecerr)
{
    auto unsupported = request(
        std::numeric_limits<uint64_t>::max(), 256, 6);
    unsupported.burst = AxiBurst::Fixed;
    EXPECT_TRUE(validateAxiBurst(unsupported, 64).decerr());
    unsupported.burst = AxiBurst::Wrap;
    EXPECT_TRUE(validateAxiBurst(unsupported, 64).decerr());

    unsupported = request(0x100, 1, 3);
    unsupported.lock = 1;
    EXPECT_TRUE(validateAxiBurst(unsupported, 64).decerr());
    unsupported.lock = 0;
    unsupported.region = 1;
    EXPECT_TRUE(validateAxiBurst(unsupported, 64).decerr());
}

TEST(AxiBurstValidationTest, RejectsArithmeticOverflow)
{
    const auto address_overflow = validateAxiBurst(request(
        std::numeric_limits<uint64_t>::max() - 3, 1, 3), 64);
    EXPECT_TRUE(address_overflow.protocolError());
    EXPECT_NE(address_overflow.error.find("last address overflows"),
              std::string::npos);

    const auto shift_overflow = validateAxiBurst(request(0, 1, 64), 64);
    EXPECT_TRUE(shift_overflow.protocolError());
    EXPECT_NE(shift_overflow.error.find("shift is not representable"),
              std::string::npos);

    const auto zero_beats = validateAxiBurst(request(0, 0, 3), 64);
    EXPECT_TRUE(zero_beats.protocolError());
    const auto too_many = validateAxiBurst(request(0, 257, 3), 64);
    EXPECT_TRUE(too_many.protocolError());
}

} // namespace axi
} // namespace gem5

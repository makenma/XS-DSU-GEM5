#include <gtest/gtest.h>

#include <array>
#include <deque>
#include <random>
#include <utility>

#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiFlitBuffer.hh"

namespace gem5::Chi
{

TEST(ChiChannelTest, PayloadSurvivesCopiedAndMovedFlit)
{
    RawDat copy;
    {
        std::array<uint8_t, 32> bytes{};
        for (unsigned i = 0; i < bytes.size(); ++i)
            bytes[i] = 3 * i;
        RawDat original;
        original.txnid = 17;
        original.setPayload(bytes.data(), bytes.size());
        copy = original;
        EXPECT_NE(copy.data.data, original.data.data);
        bytes.fill(0);
    }
    FlitVariant flit = std::move(copy);
    auto moved = std::get<RawDat>(std::move(flit));
    EXPECT_EQ(moved.txnid, 17);
    EXPECT_EQ(moved.payloadSize(), 32);
    EXPECT_EQ(moved.data.data, moved.payloadData());
    for (unsigned i = 0; i < 32; ++i)
        EXPECT_EQ(moved.data.data[i], 3 * i);
}

TEST(ChiChannelTest, LegacyPayloadCanBeFrozenAndCopied)
{
    std::array<uint8_t, 64> bytes{};
    bytes.fill(0xa5);
    RawDat original;
    original.data = {bytes.data(), bytes.size()};
    original.ownPayload();
    auto copy = original;
    bytes.fill(0);
    original.payload.fill(0);
    EXPECT_EQ(copy.payloadSize(), 64);
    for (unsigned i = 0; i < 64; ++i)
        EXPECT_EQ(copy.data.data[i], 0xa5);
}

TEST(ChiChannelTest, PayloadLengthIsBounded)
{
    RawDat flit;
    EXPECT_NO_THROW(flit.setPayload(nullptr, 0));
    EXPECT_EQ(flit.payloadSize(), 0);
    std::array<uint8_t, 65> bytes{};
    EXPECT_THROW(flit.setPayload(bytes.data(), bytes.size()),
                 std::invalid_argument);
    EXPECT_THROW(flit.setPayload(nullptr, 32), std::invalid_argument);
}

TEST(ChiChannelTest, CreditSizedBufferWrapsAndPreservesArrivalOrder)
{
    for (unsigned capacity : {0, 1, 3, 4, 8}) {
        ChiFlitBuffer buffer;
        buffer.configure(capacity);
        std::deque<std::pair<uint32_t, uint64_t>> expected;
        std::mt19937 random(17 + capacity);
        for (uint32_t cycle = 0; cycle < 20000; ++cycle) {
            ASSERT_EQ(buffer.empty(), expected.empty());
            ASSERT_EQ(buffer.full(), expected.size() == capacity);
            if (!buffer.full() && (expected.empty() || random() % 2)) {
                RawReq req;
                req.txnid = cycle;
                buffer.push(req, cycle * 1000);
                expected.emplace_back(cycle, cycle * 1000);
            } else if (!expected.empty()) {
                EXPECT_EQ(buffer.front().accepted, expected.front().second);
                auto flit = buffer.pop();
                EXPECT_EQ(std::get<RawReq>(flit).txnid, expected.front().first);
                expected.pop_front();
            }
            EXPECT_EQ(buffer.size(), expected.size());
        }
    }
}

TEST(ChiChannelTest, WireBufferFreezesBorrowedDataAcrossSlotReuse)
{
    ChiFlitBuffer buffer;
    buffer.configure(1);
    std::array<uint8_t, 32> borrowed{};
    borrowed.fill(0xa5);
    RawDat first;
    first.data = {borrowed.data(), borrowed.size()};
    buffer.push(first, 1234);
    borrowed.fill(0x5a);
    auto retained = buffer.pop();
    buffer.push(first, 2345);
    auto next = buffer.pop();
    borrowed.fill(0);
    const auto &a = std::get<RawDat>(retained);
    const auto &b = std::get<RawDat>(next);
    EXPECT_NE(a.payloadData(), b.payloadData());
    for (unsigned i = 0; i < borrowed.size(); ++i) {
        EXPECT_EQ(a.payloadData()[i], 0xa5);
        EXPECT_EQ(b.payloadData()[i], 0x5a);
    }
}

} // namespace gem5::Chi

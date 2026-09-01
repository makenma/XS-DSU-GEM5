#include <gtest/gtest.h>

#include <cstdint>
#include <vector>

#include "mem/axi/axi_protocol_checker.hh"

namespace gem5
{
namespace axi
{

namespace
{

uint64_t
digest(const std::vector<uint8_t> &data)
{
    uint64_t value = 1469598103934665603ULL;
    for (const auto byte : data) {
        value ^= byte;
        value *= 1099511628211ULL;
    }
    return value;
}

AxiCheckerTransaction
expectedRead(uint64_t uid = 1, uint64_t response_seq = 0)
{
    AxiCheckerTransaction txn;
    txn.meta.txnUid = uid;
    txn.meta.targetSeq = response_seq;
    txn.meta.responseSeq = response_seq;
    txn.meta.srcNode = 0;
    txn.meta.srcPort = 0;
    txn.meta.dstNode = 1;
    txn.meta.axiId = 3;
    txn.read = true;
    txn.beatCount = 2;
    txn.response = AxiResp::Okay;
    txn.expectedData = {{0x10, 0x11}, {0x20, 0x21}};
    return txn;
}

AxiCheckerEvent
event(const AxiCheckerTransaction &txn, AxiChannel channel,
      uint16_t index = 0)
{
    AxiCheckerEvent value;
    value.channel = channel;
    value.meta = txn.meta;
    value.beatIndex = index;
    value.beatCount = txn.beatCount;
    value.last = index + 1 == txn.beatCount;
    value.response = txn.response;
    if (channel == AxiChannel::R || channel == AxiChannel::W) {
        value.functionalData = txn.expectedData[index];
        value.payloadDigest = digest(value.functionalData);
    }
    return value;
}

} // anonymous namespace

TEST(AxiProtocolCheckerTest, AcceptsValidTrace)
{
    AxiProtocolChecker checker;
    const auto txn = expectedRead();
    ASSERT_TRUE(checker.addExpected(txn));
    EXPECT_TRUE(checker.observe(event(txn, AxiChannel::Ar)));
    EXPECT_TRUE(checker.observe(event(txn, AxiChannel::R, 0)));
    EXPECT_TRUE(checker.observe(event(txn, AxiChannel::R, 1)));
    EXPECT_TRUE(checker.finish());
}

TEST(AxiProtocolCheckerTest, RejectsDuplicateOrMissingBeat)
{
    const auto txn = expectedRead();
    AxiProtocolChecker duplicate;
    ASSERT_TRUE(duplicate.addExpected(txn));
    ASSERT_TRUE(duplicate.observe(event(txn, AxiChannel::Ar)));
    ASSERT_TRUE(duplicate.observe(event(txn, AxiChannel::R, 0)));
    EXPECT_FALSE(duplicate.observe(event(txn, AxiChannel::R, 0)));
    EXPECT_NE(duplicate.error().find("duplicate beat"), std::string::npos);

    AxiProtocolChecker missing;
    ASSERT_TRUE(missing.addExpected(txn));
    ASSERT_TRUE(missing.observe(event(txn, AxiChannel::Ar)));
    EXPECT_FALSE(missing.finish());
    EXPECT_NE(missing.error().find("missing data beat"), std::string::npos);
}

TEST(AxiProtocolCheckerTest, RejectsBadLast)
{
    AxiProtocolChecker checker;
    const auto txn = expectedRead();
    ASSERT_TRUE(checker.addExpected(txn));
    ASSERT_TRUE(checker.observe(event(txn, AxiChannel::Ar)));
    auto bad = event(txn, AxiChannel::R, 0);
    bad.last = true;
    EXPECT_FALSE(checker.observe(bad));
    EXPECT_NE(checker.error().find("LAST"), std::string::npos);
}

TEST(AxiProtocolCheckerTest, RejectsBadResponseSequence)
{
    AxiProtocolChecker checker;
    const auto txn = expectedRead(2, 1);
    ASSERT_TRUE(checker.addExpected(txn));
    ASSERT_TRUE(checker.observe(event(txn, AxiChannel::Ar)));
    ASSERT_TRUE(checker.observe(event(txn, AxiChannel::R, 0)));
    EXPECT_FALSE(checker.observe(event(txn, AxiChannel::R, 1)));
    EXPECT_NE(checker.error().find("responseSeq"), std::string::npos);
}

TEST(AxiProtocolCheckerTest, RejectsBadData)
{
    AxiProtocolChecker checker;
    const auto txn = expectedRead();
    ASSERT_TRUE(checker.addExpected(txn));
    ASSERT_TRUE(checker.observe(event(txn, AxiChannel::Ar)));
    auto bad = event(txn, AxiChannel::R, 0);
    bad.functionalData[0] ^= 0xff;
    bad.payloadDigest = digest(bad.functionalData);
    EXPECT_FALSE(checker.observe(bad));
    EXPECT_NE(checker.error().find("functional data"), std::string::npos);
}

} // namespace axi
} // namespace gem5

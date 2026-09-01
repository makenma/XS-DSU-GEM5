#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <vector>

#include "mem/axi/axi_determinism.hh"
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
    struct GoldenDraw
    {
        uint64_t seed;
        uint64_t key;
        uint64_t stage;
        uint64_t beat;
        uint64_t kind;
        uint64_t expected;
    };
    // These values are mirrored by the Python integration generator.  Keep a
    // reasonably broad fixed corpus here so either implementation changing its
    // mixing order or enum interpretation fails before a replay is attempted.
    constexpr std::array<GoldenDraw, 40> golden = {{
        {0ULL, 4660ULL, 1ULL, 0ULL, 1ULL, 0x31677aece382ce62ULL},
        {1ULL, 4677ULL, 2ULL, 13ULL, 2ULL, 0x101be0ca4df15567ULL},
        {7ULL, 4728ULL, 3ULL, 26ULL, 3ULL, 0xf2d743c607966570ULL},
        {42ULL, 4813ULL, 4ULL, 39ULL, 4ULL, 0x4f1e6d1da23a7f9eULL},
        {UINT64_MAX, 4932ULL, 5ULL, 52ULL, 5ULL, 0xe26d4454964407e2ULL},
        {0ULL, 5085ULL, 1ULL, 65ULL, 6ULL, 0x17bf6794a34b0eb6ULL},
        {1ULL, 5272ULL, 2ULL, 78ULL, 7ULL, 0xc108d411634279b8ULL},
        {7ULL, 5493ULL, 3ULL, 91ULL, 8ULL, 0xe8c056fd8320c8f7ULL},
        {42ULL, 5748ULL, 4ULL, 104ULL, 9ULL, 0x6c18a344c0fea588ULL},
        {UINT64_MAX, 6037ULL, 5ULL, 117ULL, 10ULL, 0x2022ea34c1939c72ULL},
        {0ULL, 6360ULL, 1ULL, 130ULL, 11ULL, 0x5a8a356f81b25b01ULL},
        {1ULL, 6717ULL, 2ULL, 143ULL, 12ULL, 0x5ac8c643af5f7f24ULL},
        {7ULL, 7108ULL, 3ULL, 156ULL, 1ULL, 0xd71437c08b6b7f1bULL},
        {42ULL, 7533ULL, 4ULL, 169ULL, 2ULL, 0xbd1f9ed820d2fa90ULL},
        {UINT64_MAX, 7992ULL, 5ULL, 182ULL, 3ULL, 0x262d629767f4a61aULL},
        {0ULL, 8485ULL, 1ULL, 195ULL, 4ULL, 0x1c9c30ce0a6f9b2cULL},
        {1ULL, 9012ULL, 2ULL, 208ULL, 5ULL, 0x0f057d0f930bf8c5ULL},
        {7ULL, 9573ULL, 3ULL, 221ULL, 6ULL, 0x397aaa024094b821ULL},
        {42ULL, 10168ULL, 4ULL, 234ULL, 7ULL, 0x64587c68fad6f940ULL},
        {UINT64_MAX, 10797ULL, 5ULL, 247ULL, 8ULL, 0xf7e2308d48cb7a0aULL},
        {0ULL, 11460ULL, 1ULL, 3ULL, 9ULL, 0x7783d7a3fdcc43c4ULL},
        {1ULL, 12157ULL, 2ULL, 16ULL, 10ULL, 0x4e914fe69acd7daeULL},
        {7ULL, 12888ULL, 3ULL, 29ULL, 11ULL, 0xa2f1ea35c2d96247ULL},
        {42ULL, 13653ULL, 4ULL, 42ULL, 12ULL, 0x3ca680548200fa08ULL},
        {UINT64_MAX, 14452ULL, 5ULL, 55ULL, 1ULL, 0x5a14100b2e91579fULL},
        {0ULL, 15285ULL, 1ULL, 68ULL, 2ULL, 0x50994dbae62b9ec9ULL},
        {1ULL, 16152ULL, 2ULL, 81ULL, 3ULL, 0xa98930ed3489521fULL},
        {7ULL, 17053ULL, 3ULL, 94ULL, 4ULL, 0x3721b15c65172f40ULL},
        {42ULL, 17988ULL, 4ULL, 107ULL, 5ULL, 0x24bbbc0faaaca731ULL},
        {UINT64_MAX, 18957ULL, 5ULL, 120ULL, 6ULL, 0x34a0d8861075b7f2ULL},
        {0ULL, 19960ULL, 1ULL, 133ULL, 7ULL, 0x905e1de3a05fd653ULL},
        {1ULL, 20997ULL, 2ULL, 146ULL, 8ULL, 0xba01b542d8861770ULL},
        {7ULL, 22068ULL, 3ULL, 159ULL, 9ULL, 0x0fa98e5b3045a544ULL},
        {42ULL, 23173ULL, 4ULL, 172ULL, 10ULL, 0xb220025a5b0a2b3cULL},
        {UINT64_MAX, 24312ULL, 5ULL, 185ULL, 11ULL, 0xe7267fa0f7a251e2ULL},
        {0ULL, 25485ULL, 1ULL, 198ULL, 12ULL, 0xab3c3798cb7a9abeULL},
        {1ULL, 26692ULL, 2ULL, 211ULL, 1ULL, 0x9c4f6de84e183565ULL},
        {7ULL, 27933ULL, 3ULL, 224ULL, 2ULL, 0xaeba2c3799a89277ULL},
        {42ULL, 29208ULL, 4ULL, 237ULL, 3ULL, 0xe2e559957290b6f7ULL},
        {UINT64_MAX, 30517ULL, 5ULL, 250ULL, 4ULL, 0x4d93f25e0f83859fULL},
    }};
    for (const auto &draw : golden) {
        EXPECT_EQ(
            deterministic::keyedRandom(
                draw.seed, draw.key,
                static_cast<deterministic::Stage>(draw.stage), draw.beat,
                static_cast<deterministic::DrawKind>(draw.kind)),
            draw.expected);
    }
    EXPECT_EQ(
        deterministic::keyedRandom(
            42, 0x1234, deterministic::Stage::Workload, 0,
            deterministic::DrawKind::Qos),
        0x497f52b4d270ca5fULL);

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

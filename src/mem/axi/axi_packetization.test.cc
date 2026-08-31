#include <gtest/gtest.h>

#include <climits>
#include <cstdint>
#include <memory>
#include <ostream>
#include <string>
#include <vector>

#include "mem/axi/axi_packetization.hh"
#include "mem/ruby/network/garnet/Packetization.hh"
#include "mem/ruby/slicc_interface/Message.hh"

namespace gem5
{
namespace axi
{

namespace
{

class LegacyTestMessage : public ruby::Message
{
  public:
    LegacyTestMessage() : Message(0) {}

    ruby::MsgPtr clone() const override
    {
        return std::make_shared<LegacyTestMessage>(*this);
    }

    void print(std::ostream &out) const override
    {
        out << "LegacyTestMessage";
    }
};

class DynamicTestMessage : public LegacyTestMessage
{
  public:
    DynamicTestMessage(int wire_bytes, int semantic_bytes)
        : wireBytes(wire_bytes), semanticBytes(semantic_bytes)
    {}

    ruby::MsgPtr clone() const override
    {
        return std::make_shared<DynamicTestMessage>(*this);
    }

    const int& getWireSizeBytes() const override { return wireBytes; }
    int getSemanticBytes() const { return semanticBytes; }

  private:
    int wireBytes;
    int semanticBytes;
};

ruby::garnet::MessagePacketization
packetize(const ruby::Message &message, uint32_t legacy_bytes,
          uint32_t flit_bytes, std::string &error)
{
    ruby::garnet::MessagePacketization output;
    error = ruby::garnet::resolveMessagePacketization(
        message.getWireSizeBytes(), legacy_bytes, flit_bytes, output);
    return output;
}

} // anonymous namespace

TEST(AxiDynamicWireBytesTest, CountsDefaultFiveChannels)
{
    AxiWireBytes wire_bytes;
    EXPECT_TRUE(normalizeAxiWireBytes(
        {24, 16, 8, 24, 16}, 64, wire_bytes).empty());
    EXPECT_EQ(wire_bytes, (AxiWireBytes{24, 80, 8, 24, 80}));

    const std::array<int, AxiWireSlotCount> expected_flits =
        {2, 5, 1, 2, 5};
    for (size_t i = 0; i < AxiWireSlotCount; ++i) {
        DynamicTestMessage message(wire_bytes[i], 1);
        std::string error;
        const auto output = packetize(message, 8, 16, error);
        EXPECT_TRUE(error.empty()) << "channel index " << i << ": " << error;
        EXPECT_EQ(output.wireBytes, wire_bytes[i]);
        EXPECT_EQ(output.numFlits, expected_flits[i]);
    }
}

TEST(AxiDynamicWireBytesTest, FallsBackForLegacyMessage)
{
    LegacyTestMessage message;
    ASSERT_EQ(message.getWireSizeBytes(), 0);

    std::string error;
    const auto output = packetize(message, 72, 16, error);
    EXPECT_TRUE(error.empty());
    EXPECT_EQ(output.wireBytes, 72);
    EXPECT_EQ(output.numFlits, 5);
}

TEST(AxiDynamicWireBytesTest, IgnoresSemanticBytesForFlits)
{
    DynamicTestMessage sparse_payload(80, 1);
    DynamicTestMessage full_payload(80, 64);
    ASSERT_NE(sparse_payload.getSemanticBytes(),
              full_payload.getSemanticBytes());

    std::string sparse_error;
    std::string full_error;
    const auto sparse = packetize(sparse_payload, 8, 16, sparse_error);
    const auto full = packetize(full_payload, 8, 16, full_error);
    EXPECT_TRUE(sparse_error.empty());
    EXPECT_TRUE(full_error.empty());
    EXPECT_EQ(sparse.wireBytes, full.wireBytes);
    EXPECT_EQ(sparse.numFlits, 5);
    EXPECT_EQ(sparse.numFlits, full.numFlits);
}

TEST(AxiDynamicWireBytesTest, RejectsInvalidWireSize)
{
    ruby::garnet::MessagePacketization output;
    EXPECT_NE(ruby::garnet::resolveMessagePacketization(
                  -1, 8, 16, output).find("wireBytes must be >= 0"),
              std::string::npos);
    EXPECT_NE(ruby::garnet::resolveMessagePacketization(
                  0, 0, 16, output).find(
                      "wireBytes must be >= 1 after legacy fallback"),
              std::string::npos);
    EXPECT_NE(ruby::garnet::resolveMessagePacketization(
                  0, UINT32_MAX, 16, output).find(
                      "wireBytes must fit positive int"),
              std::string::npos);
    EXPECT_NE(ruby::garnet::resolveMessagePacketization(
                  8, 0, 0, output).find("flitBytes must be >= 1"),
              std::string::npos);

    AxiWireBytes wire_bytes;
    EXPECT_FALSE(normalizeAxiWireBytes({24, 16}, 64, wire_bytes).empty());
    EXPECT_FALSE(normalizeAxiWireBytes(
        {24, 0, 8, 24, 16}, 64, wire_bytes).empty());
    EXPECT_FALSE(normalizeAxiWireBytes(
        {24, INT_MAX, 8, 24, 16}, 64, wire_bytes).empty());
}

} // namespace axi
} // namespace gem5

#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_sha256.hh"

namespace
{

std::vector<uint8_t>
bytesOf(const std::string &text)
{
    return std::vector<uint8_t>(text.begin(), text.end());
}

std::string
hexOf(const std::array<uint8_t, 32> &digest)
{
    static const char *digits = "0123456789abcdef";
    std::string text;
    for (const uint8_t byte : digest) {
        text += digits[byte >> 4];
        text += digits[byte & 0xf];
    }
    return text;
}

}

TEST(AgentSha256, MatchesNistVectors)
{
    EXPECT_EQ(hexOf(gem5::ai_mesh::agentSha256(bytesOf(""))),
              "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7"
              "852b855");
    EXPECT_EQ(hexOf(gem5::ai_mesh::agentSha256(bytesOf("abc"))),
              "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f"
              "20015ad");
    EXPECT_EQ(hexOf(gem5::ai_mesh::agentSha256(
                  bytesOf("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlm"
                          "nomnopnopq"))),
              "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd4"
              "19db06c1");
    std::vector<uint8_t> million(1000000, 'a');
    EXPECT_EQ(hexOf(gem5::ai_mesh::agentSha256(million)),
              "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc"
              "7112cd0");
}

TEST(AgentSha256, IncrementalUpdateMatchesSingleShot)
{
    const std::vector<uint8_t> payload =
        bytesOf("incremental hashing must equal single shot hashing");
    const auto single = gem5::ai_mesh::agentSha256(payload);
    gem5::ai_mesh::AgentSha256 incremental;
    size_t offset = 0;
    while (offset < payload.size()) {
        const size_t take = std::min<size_t>(7, payload.size() - offset);
        incremental.update(payload.data() + offset, take);
        offset += take;
    }
    EXPECT_EQ(hexOf(incremental.finish()), hexOf(single));
}

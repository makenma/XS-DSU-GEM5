#include <gtest/gtest.h>

#include <cstring>
#include <vector>

#include "dev/ai_mesh/generated/agent_golden.inc"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{
namespace agent_abi
{
namespace
{

using namespace ::gem5::ai_mesh::agent_abi;

TEST(AgentCrc32cTest, ReferenceVector)
{
    const char *digits = "123456789";
    EXPECT_EQ(crc32c(reinterpret_cast<const uint8_t *>(digits), 9),
              0xE3069283u);
    EXPECT_EQ(crc32c(nullptr, 0), 0u);
}

// The embedded images come from the Python encoder (agent_protocol.py);
// the C++ reader must reproduce every field and verify the CRCs.
TEST(AgentRecordGoldenTest, SqDecodesAndCrcVerifies)
{
    SqDescriptor sq;
    std::memcpy(&sq, kAgentGoldenSq, kSqDescriptorBytes);

    EXPECT_EQ(rdU16(reinterpret_cast<const uint8_t *>(kAgentGoldenSq) +
                    kSqDescriptorAbiMajorOffset),
              kAbiMajor);
    EXPECT_EQ(rdU64(reinterpret_cast<const uint8_t *>(kAgentGoldenSq) +
                    kSqDescriptorSqSeqOffset),
              7u);
    EXPECT_EQ(rdU16(reinterpret_cast<const uint8_t *>(kAgentGoldenSq) +
                    kSqDescriptorOpcodeOffset),
              kSqOpcodeGENERATE);
    const uint32_t stored = rdU32(reinterpret_cast<const uint8_t *>(
                                      kAgentGoldenSq) + kSqDescriptorCrc32Offset);
    EXPECT_EQ(crc32c(kAgentGoldenSq, kSqDescriptorCrcCoverBytes), stored);

    std::vector<uint8_t> corrupt(kAgentGoldenSq,
                                 kAgentGoldenSq + kSqDescriptorBytes);
    corrupt[20] ^= 1;
    EXPECT_NE(crc32c(corrupt.data(), kSqDescriptorCrcCoverBytes), stored);
}

TEST(AgentRecordGoldenTest, CqDecodes)
{
    EXPECT_EQ(rdU64(reinterpret_cast<const uint8_t *>(kAgentGoldenCq) +
                    kCqDescriptorCqSeqOffset), 3u);
    EXPECT_EQ(rdU16(reinterpret_cast<const uint8_t *>(kAgentGoldenCq) +
                    kCqDescriptorStatusOffset), kCqStatusSUCCESS);
    EXPECT_EQ(rdU16(reinterpret_cast<const uint8_t *>(kAgentGoldenCq) +
                    kCqDescriptorFlagsOffset), kCqFlagsMETADATA_VALID);
}

TEST(AgentRecordGoldenTest, MetadataCrcVerifies)
{
    const uint8_t *meta = kAgentGoldenMetadata;
    const uint32_t stored = rdU32(meta + kOutputMetadataCrc32Offset);
    const uint32_t total = rdU32(meta + kOutputMetadataTotalBytesOffset);
    std::vector<uint8_t> covered(meta, meta + total);
    std::memset(covered.data() + kOutputMetadataCrc32Offset, 0, 4);
    EXPECT_EQ(crc32c(covered.data(), total), stored);
    EXPECT_EQ(rdU32(meta + kOutputMetadataRequestIdOffset + 0) |
                  uint64_t(rdU32(meta + kOutputMetadataRequestIdOffset + 4)) << 32,
              9u);
}

} // namespace
} // namespace agent_abi
} // namespace ai_mesh
} // namespace gem5

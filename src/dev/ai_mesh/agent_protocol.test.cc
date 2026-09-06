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

TEST(AgentRecordCodecTest, GeneratedEncodersRoundTrip)
{
    SqDescriptor sq;
    sq.abi_major = kAbiMajor;
    sq.sq_seq = 11;
    sq.request_id = 22;
    sq.completion_cookie = 33;
    const auto sq_bytes = encodeSqDescriptor(sq);
    const auto sq_decoded = decodeSqDescriptor(sq_bytes.data());
    EXPECT_EQ(sq_decoded.sq_seq, sq.sq_seq);
    EXPECT_EQ(sq_decoded.request_id, sq.request_id);
    EXPECT_EQ(sq_decoded.completion_cookie, sq.completion_cookie);
    EXPECT_EQ(crc32c(sq_bytes.data(), kSqDescriptorCrcCoverBytes),
              rdU32(sq_bytes.data() + kSqDescriptorCrcFieldOffset));

    ParameterHeader parameter;
    parameter.magic = 0x504e4741;
    parameter.header_bytes = kParameterHeaderBytes;
    parameter.total_bytes = kParameterHeaderBytes;
    parameter.input_addr = 44;
    const auto parameter_bytes = encodeParameterHeader(parameter);
    const auto parameter_decoded = decodeParameterHeader(parameter_bytes.data());
    EXPECT_EQ(parameter_decoded.input_addr, parameter.input_addr);
    // Parameter CRC covers total_bytes with the CRC field treated as zero
    // (spec 8.3); kParameterHeaderCrcCoverBytes==0 selects that variant.
    {
        std::vector<uint8_t> covered(parameter_bytes.begin(),
                                     parameter_bytes.end());
        std::memset(covered.data() + kParameterHeaderCrc32Offset, 0, 4);
        EXPECT_EQ(crc32c(covered.data(), kParameterHeaderBytes),
                  rdU32(parameter_bytes.data() + kParameterHeaderCrc32Offset));
    }

    CqDescriptor cq;
    cq.cq_seq = 55;
    cq.request_id = 66;
    cq.completion_cookie = 77;
    const auto cq_bytes = encodeCqDescriptor(cq);
    const auto cq_decoded = decodeCqDescriptor(cq_bytes.data());
    EXPECT_EQ(cq_decoded.cq_seq, cq.cq_seq);
    EXPECT_EQ(cq_decoded.request_id, cq.request_id);
    EXPECT_EQ(cq_decoded.completion_cookie, cq.completion_cookie);
}

TEST(AgentAbiRegistryTest, FatalProjectionUsesTypedSite)
{
    const auto projection = fatalSiteProjectionV1(
        FaultSiteV1::MSI_TARGET_OR_B);
    ASSERT_TRUE(projection);
    EXPECT_EQ(projection->sourceClass, FatalSourceClassV1::MSI);
    EXPECT_EQ(projection->componentKind,
              FatalComponentKindV1::NPU_FRONTEND);
    EXPECT_EQ(projection->objectKind, FatalObjectKindV1::MSI);
    EXPECT_EQ(projection->detailCode, E_INTERRUPT);
    EXPECT_FALSE(fatalSiteProjectionV1(FaultSiteV1::RESERVED_16));
}

} // namespace
} // namespace agent_abi
} // namespace ai_mesh
} // namespace gem5

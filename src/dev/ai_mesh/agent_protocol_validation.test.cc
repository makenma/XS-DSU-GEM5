#include <gtest/gtest.h>

#include "dev/ai_mesh/agent_protocol_validation.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

agent_abi::ParameterHeader
parameterHeader()
{
    agent_abi::ParameterHeader header;
    header.total_bytes = 280;
    header.binding_table_offset = 256;
    header.binding_count = 1;
    header.binding_record_bytes = agent_abi::kBindingRecordBytes;
    header.extension_offset = 160;
    header.extension_bytes = 96;
    return header;
}

struct TlvBlock
{
    uint16_t type;
    uint16_t flags;
    std::vector<uint8_t> payload;
};

TlvBlock digestBlock(uint16_t type, uint8_t fill)
{
    return TlvBlock{type, agent_abi::kTlvFlagsREQUIRED,
                    std::vector<uint8_t>(32, fill)};
}

TlvBlock chunkBlock(uint32_t chunk, uint32_t reserved = 0)
{
    TlvBlock block{agent_abi::kTlvTypeOUTPUT_CHUNK_BYTES,
                   agent_abi::kTlvFlagsREQUIRED, std::vector<uint8_t>(8, 0)};
    agent_abi::wrU32(block.payload.data(), chunk);
    agent_abi::wrU32(block.payload.data() + 4, reserved);
    return block;
}

TlvBlock deadlineBlock(uint64_t tick)
{
    TlvBlock block{agent_abi::kTlvTypeDEADLINE, agent_abi::kTlvFlagsREQUIRED,
                   std::vector<uint8_t>(8, 0)};
    agent_abi::wrU64(block.payload.data(), tick);
    return block;
}

std::pair<std::vector<uint8_t>, agent_abi::ParameterHeader>
parameterWithExtension(const std::vector<TlvBlock> &blocks)
{
    std::vector<uint8_t> data(agent_abi::kParameterHeaderBytes, 0);
    for (const TlvBlock &block : blocks) {
        const size_t headerAt = data.size();
        data.resize(headerAt + 8);
        agent_abi::wrU16(data.data() + headerAt, block.type);
        agent_abi::wrU16(data.data() + headerAt + 2, block.flags);
        agent_abi::wrU32(data.data() + headerAt + 4, block.payload.size());
        data.insert(data.end(), block.payload.begin(), block.payload.end());
        data.resize((data.size() + 7) & ~size_t(7), 0);
    }
    agent_abi::ParameterHeader header;
    header.total_bytes = static_cast<uint32_t>(data.size());
    header.binding_table_offset = agent_abi::kParameterHeaderBytes;
    header.binding_count = 0;
    header.binding_record_bytes = agent_abi::kBindingRecordBytes;
    header.extension_offset = agent_abi::kParameterHeaderBytes;
    header.extension_bytes =
        static_cast<uint32_t>(data.size() - agent_abi::kParameterHeaderBytes);
    return {data, header};
}

ParameterTlvValues walk(const std::vector<TlvBlock> &blocks)
{
    auto [data, header] = parameterWithExtension(blocks);
    ParameterTlvValues values;
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header,
                                         values),
              std::nullopt);
    return values;
}

TEST(AgentProtocolValidationTest, ChecksParameterSpansAndOverlap)
{
    auto header = parameterHeader();
    EXPECT_EQ(gate3ParameterStructureError(header, 280), std::nullopt);
    header.binding_table_offset = 4096;
    EXPECT_EQ(gate3ParameterStructureError(header, 280),
              agent_abi::E_REQUEST_BINDING);
    header = parameterHeader();
    header.binding_table_offset = 152;
    EXPECT_EQ(gate3ParameterStructureError(header, 280),
              agent_abi::E_REQUEST_BINDING);
    header = parameterHeader();
    header.binding_table_offset = 160;
    EXPECT_EQ(gate3ParameterStructureError(header, 280),
              agent_abi::E_REQUEST_BINDING);
    header = parameterHeader();
    header.binding_record_bytes = 16;
    EXPECT_EQ(gate3ParameterStructureError(header, 280),
              agent_abi::E_REQUEST_BINDING);
}

TEST(AgentProtocolValidationTest, ChecksMetadataIdentityAndKnownTlvSize)
{
    Gate3MetadataExpectation expected{
        9, 0x4000, 7, 3, 1,
        static_cast<uint16_t>(agent_abi::CqStatus::SUCCESS), 0, 64, 200};
    agent_abi::OutputMetadata header;
    header.magic = 0x4f4e4741;
    header.abi_major = agent_abi::kAbiMajor;
    header.abi_minor = agent_abi::kAbiMinor;
    header.header_bytes = agent_abi::kOutputMetadataBytes;
    header.total_bytes = 200;
    header.terminal_status = expected.cqStatus;
    header.request_id = expected.requestId;
    header.session_id = expected.sessionId;
    header.user_id = expected.userId;
    header.task_seq = expected.taskSequence;
    header.repair_round = expected.repairRound;
    header.output_bytes = expected.cqValue;
    EXPECT_TRUE(gate3MetadataHeaderValid(header, expected));
    expected.outputBytes = 64;
    expected.cqStatus = header.terminal_status = static_cast<uint16_t>(agent_abi::CqStatus::AXI_ERROR);
    EXPECT_FALSE(gate3MetadataHeaderValid(header, expected));
    expected.cqFlags = agent_abi::kCqFlagsPARTIAL_OUTPUT;
    EXPECT_TRUE(gate3MetadataHeaderValid(header, expected));
    expected.outputBytes = 32;
    EXPECT_FALSE(gate3MetadataHeaderValid(header, expected));
    expected.outputBytes = 64;
    expected.cqStatus = header.terminal_status = static_cast<uint16_t>(agent_abi::CqStatus::SUCCESS);
    EXPECT_FALSE(gate3MetadataHeaderValid(header, expected));
    expected.cqFlags = 0;
    header.session_id = 0;
    EXPECT_FALSE(gate3MetadataHeaderValid(header, expected));
    header.session_id = expected.sessionId;
    header.flags = 2;
    EXPECT_FALSE(gate3MetadataHeaderValid(header, expected));
    header.flags = 0;
    auto encoded = agent_abi::encodeOutputMetadata(header);
    std::vector<uint8_t> record(encoded.begin(), encoded.end());
    record.resize(200, 0);
    agent_abi::wrU16(record.data() + 128,
                     agent_abi::kOutputTlvTypeTIMING_BREAKDOWN);
    agent_abi::wrU16(record.data() + 130, agent_abi::kTlvFlagsREQUIRED);
    agent_abi::wrU32(record.data() + 132, 64);
    agent_abi::wrU32(record.data() + 120, 0);
    agent_abi::wrU32(record.data() + 120,
                     agent_abi::crc32c(record.data(), record.size()));
    EXPECT_TRUE(gate3MetadataRecordValid(record, expected));
    agent_abi::wrU32(record.data() + 132, 56);
    agent_abi::wrU32(record.data() + 120, 0);
    agent_abi::wrU32(record.data() + 120,
                     agent_abi::crc32c(record.data(), record.size()));
    EXPECT_FALSE(gate3MetadataRecordValid(record, expected));
}

TEST(AgentProtocolValidationTest, WalksAWellFormedParameterTlvArea)
{
    ParameterTlvValues values = walk({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        deadlineBlock(12345),
        chunkBlock(64),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
    });
    EXPECT_EQ(values.inputDigest, std::vector<uint8_t>(32, 0x11));
    EXPECT_EQ(values.workloadDigest, std::vector<uint8_t>(32, 0x22));
    EXPECT_EQ(values.outputChunkBytes, 64u);
    EXPECT_TRUE(values.hasDeadline);
    EXPECT_EQ(values.deadlineTick, 12345ull);
    EXPECT_EQ(values.skippedOptional, 0u);
}

TEST(AgentProtocolValidationTest, RejectsReservedFlagBitsOnAKnownTlv)
{
    auto [data, header] = parameterWithExtension({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        chunkBlock(64),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
    });
    agent_abi::wrU16(data.data() + header.extension_offset + 2, 3);
    ParameterTlvValues values;
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header, values),
              agent_abi::E_REQUEST_BINDING);
}

TEST(AgentProtocolValidationTest, RejectsOutOfOrderAndDuplicateTypes)
{
    auto [data, header] = parameterWithExtension({
        chunkBlock(64),
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
    });
    ParameterTlvValues values;
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header, values),
              agent_abi::E_REQUEST_BINDING);
    std::tie(data, header) = parameterWithExtension({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x22),
        chunkBlock(64),
    });
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header, values),
              agent_abi::E_REQUEST_BINDING);
}

TEST(AgentProtocolValidationTest, RejectsUnknownRequiredAndWrongSizes)
{
    ParameterTlvValues values;
    auto [data, header] = parameterWithExtension({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        TlvBlock{5, agent_abi::kTlvFlagsREQUIRED, std::vector<uint8_t>(8, 0)},
        chunkBlock(64),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
    });
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header, values),
              agent_abi::E_REQUEST_BINDING);
    std::tie(data, header) = parameterWithExtension({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        chunkBlock(64),
        TlvBlock{agent_abi::kTlvTypeWORKLOAD_ID_DIGEST,
                 agent_abi::kTlvFlagsREQUIRED, std::vector<uint8_t>(16, 0)},
    });
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header, values),
              agent_abi::E_REQUEST_BINDING);
    std::tie(data, header) = parameterWithExtension({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        chunkBlock(64),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
    });
    agent_abi::wrU16(data.data() + header.extension_offset + 58, 0);
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header, values),
              agent_abi::E_REQUEST_BINDING);
}

TEST(AgentProtocolValidationTest, SkipsUnknownOptionalAndCountsIt)
{
    ParameterTlvValues values = walk({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        chunkBlock(64),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
        TlvBlock{65534, 0, std::vector<uint8_t>(4, 0)},
        TlvBlock{65535, 0, std::vector<uint8_t>(8, 0)},
    });
    EXPECT_EQ(values.skippedOptional, 2u);
    EXPECT_EQ(values.outputChunkBytes, 64u);

    auto [data, header] = parameterWithExtension({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        chunkBlock(64),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
        TlvBlock{65535, 2, std::vector<uint8_t>(8, 0)},
    });
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header, values),
              agent_abi::E_REQUEST_BINDING);
}

TEST(AgentProtocolValidationTest, EnforcesAlignedAreaAndZeroPadding)
{
    auto [data, header] = parameterWithExtension({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        chunkBlock(64),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
        TlvBlock{65535, 0, std::vector<uint8_t>(4, 0)},
    });
    ParameterTlvValues values;
    *(data.end() - 1) = 1;
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header,
                                         values),
              agent_abi::E_REQUEST_BINDING);
    *(data.end() - 1) = 0;
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header,
                                         values),
              std::nullopt);
    header.extension_bytes -= 4;
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header,
                                         values),
              agent_abi::E_REQUEST_BINDING);
    header.extension_bytes += 12;
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header,
                                         values),
              agent_abi::E_REQUEST_BINDING);
}

TEST(AgentProtocolValidationTest, ParsesChunkAsU32WithZeroReserved)
{
    ParameterTlvValues values;
    auto [data, header] = parameterWithExtension({
        digestBlock(agent_abi::kTlvTypeINPUT_DIGEST, 0x11),
        chunkBlock(64, 1),
        digestBlock(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, 0x22),
    });
    EXPECT_EQ(parameterTlvStructureError(data.data(), data.size(), header, values),
              agent_abi::E_REQUEST_BINDING);
}

}
}
}

using namespace gem5::ai_mesh;

TEST(AgentProtocolValidationTest, CancellationDiagnosticIsCanonicalAndIdentityBound)
{
    const auto payload = requestCancellationDiagnostic(9);
    EXPECT_EQ(agent_abi::rdU32(payload.data()), agent_abi::E_REQUEST_CANCELLED);
    EXPECT_EQ(agent_abi::rdU16(payload.data() + 4), agent_abi::kDiagnosticComponentFRONTEND);
    EXPECT_EQ(agent_abi::rdU16(payload.data() + 6), 0xffff);
    EXPECT_EQ(agent_abi::rdU32(payload.data() + 20), 1u);
    EXPECT_NE(payload, requestCancellationDiagnostic(10));
    const std::array<uint8_t, 32> expected = {0xa8,0x81,0x2e,0xd3,0x9d,0x65,0x1c,0x7,0xe6,0x44,0xbb,0x96,0xd0,0xcd,0x62,0xf7,0x1e,0xb5,0x59,0x55,0xb9,0xae,0x3f,0x65,0xea,0x2d,0xb8,0xae,0xb9,0xc7,0x28,0x6};
    EXPECT_TRUE(std::equal(expected.begin(), expected.end(), payload.begin() + 24));
}

TEST(AgentProtocolValidationTest, CancelMetadataRejectsIdentityAndPayloadTampering)
{
    Gate3MetadataExpectation expected{9, 4, 7, 3, 1,
        agent_abi::kCqStatusCANCELLED, agent_abi::kCqFlagsMETADATA_VALID, 0, 200};
    expected.metadataFlags = agent_abi::kMetadataFlagsSESSION_ADMITTED;
    agent_abi::OutputMetadata header;
    header.magic = 0x4f4e4741;
    header.abi_major = agent_abi::kAbiMajor;
    header.abi_minor = agent_abi::kAbiMinor;
    header.header_bytes = agent_abi::kOutputMetadataBytes;
    header.total_bytes = 200;
    header.flags = *expected.metadataFlags;
    header.terminal_status = expected.cqStatus;
    header.request_id = expected.requestId;
    header.session_id = expected.sessionId;
    header.user_id = expected.userId;
    header.task_seq = expected.taskSequence;
    header.repair_round = expected.repairRound;
    const auto encoded = agent_abi::encodeOutputMetadata(header);
    std::vector<uint8_t> record(encoded.begin(), encoded.end());
    record.resize(200, 0);
    agent_abi::wrU16(record.data() + 128, agent_abi::kOutputTlvTypeDIAGNOSTIC);
    agent_abi::wrU16(record.data() + 130, agent_abi::kTlvFlagsREQUIRED);
    agent_abi::wrU32(record.data() + 132, 64);
    const auto diagnostic = requestCancellationDiagnostic(expected.requestId);
    std::copy(diagnostic.begin(), diagnostic.end(), record.begin() + 136);
    for (size_t mutation = 0; mutation <= 64; ++mutation) {
        auto candidate = record;
        if (mutation < 64)
            candidate[136 + mutation] ^= 1;
        agent_abi::wrU32(candidate.data() + 120, 0);
        agent_abi::wrU32(candidate.data() + 120,
            agent_abi::crc32c(candidate.data(), candidate.size()));
        EXPECT_EQ(gate3MetadataRecordValid(candidate, expected), mutation == 64) << mutation;
    }
}

TEST(AgentProtocolValidationTest, KvPolicyDiagnosticBindsTheTupleGenerationAndSite)
{
    const auto payload = kvPolicyDiagnostic(9, 4, 5, 3, agent_abi::E_KV_REUSE_REQUIRED);
    ASSERT_TRUE(payload);
    EXPECT_EQ(agent_abi::rdU32(payload->data() + 8), 3u);
    EXPECT_EQ(agent_abi::rdU32(payload->data() + 16), agent_abi::kKvPolicySiteV1REUSE_REQUIRED);
    EXPECT_EQ(agent_abi::rdU32(payload->data() + 20), agent_abi::kDiagnosticSourceKindV1KV_POLICY);
    EXPECT_NE(payload, kvPolicyDiagnostic(9, 4, 5, 4, agent_abi::E_KV_REUSE_REQUIRED));
    EXPECT_NE(payload, kvPolicyDiagnostic(9, 4, 6, 3, agent_abi::E_KV_REUSE_REQUIRED));
    for (const auto detail : {agent_abi::E_KV_REUSE_REQUIRED,
                              agent_abi::E_KV_TOKEN_MISMATCH, agent_abi::E_KV_STATE})
        EXPECT_TRUE(kvPolicyDiagnostic(9, 4, 5, 3, detail));
    EXPECT_NE(payload, kvPolicyDiagnostic(9, 4, 5, 3, agent_abi::E_KV_TOKEN_MISMATCH));
    EXPECT_FALSE(kvPolicyDiagnostic(9, 4, 5, 3, agent_abi::E_REQUEST_BINDING));
    const std::array<uint8_t, 32> golden = {0xb, 0x6b, 0x8e, 0xf3, 0x1d, 0xfd, 0xe5, 0x4d, 0xec, 0x21, 0xf, 0xc5, 0xa9, 0x64, 0xbe, 0x17, 0x4b, 0x41, 0x5a, 0xdb, 0xc1, 0x6c, 0x26, 0x7, 0x4c, 0x14, 0x46, 0x76, 0x98, 0x1c, 0x93, 0x3b};
    EXPECT_TRUE(std::equal(golden.begin(), golden.end(), payload->begin() + 24));
}

TEST(AgentProtocolValidation, TypedErrorSourceDiagnosticHasExactWireGolden)
{
    using namespace gem5::ai_mesh;
    ErrorCandidate failure;
    failure.code = agent_abi::E_AXI_RESPONSE;
    failure.source.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
    failure.source.core_id_or_ffff = 256;
    failure.source.object_kind = mesh_abi::kMeshObjectKindDESCRIPTOR;
    failure.source.ordinal = 257;
    failure.source.generation = 3;
    const auto diagnostic = errorSourceDiagnostic(failure);
    ASSERT_TRUE(diagnostic);
    const std::array<uint8_t, 32> golden{
        0xaa, 0x8a, 0xb9, 0x8b, 0x77, 0x49, 0xbe, 0xeb,
        0xcc, 0x10, 0x22, 0x7c, 0xd6, 0xcb, 0x3e, 0xbb,
        0x40, 0x8e, 0x61, 0xfd, 0x4d, 0x36, 0x45, 0x00,
        0xf7, 0x19, 0xc5, 0xd2, 0xa0, 0x6d, 0x9a, 0x34};
    EXPECT_TRUE(std::equal(golden.begin(), golden.end(), diagnostic->begin() + 24));
    EXPECT_EQ(agent_abi::rdU16(diagnostic->data() + 6), 256);
    EXPECT_EQ(agent_abi::rdU32(diagnostic->data() + 16), 257u);
    failure.source.domain = 6;
    EXPECT_FALSE(errorSourceDiagnostic(failure));
}

TEST(AgentProtocolValidationTest, CancellationBindsFrozenInstanceAndPhase)
{
    const auto payload = requestCancellationDiagnostic(9, {4, 3});
    EXPECT_NE(payload, requestCancellationDiagnostic(9));
    EXPECT_EQ(agent_abi::rdU32(payload.data() + 8), 3u);
    const std::array<uint8_t, 32> golden{0xb4,0x7e,0x9e,0xe6,0x13,0xe3,0xcb,0x3b,0x9d,0x6c,0x6f,0xbb,0x3c,0x0c,0xea,0x8a,0x2e,0x39,0x34,0xd6,0x12,0x94,0x15,0xef,0xec,0x51,0x56,0x63,0x56,0x4a,0x4f,0xe0};
    EXPECT_TRUE(std::equal(golden.begin(), golden.end(), payload.begin() + 24));
}

TEST(AgentProtocolValidation, EveryRecoverableRuntimeDetailHasTypedDiagnostic)
{
    ErrorCandidate failure;
    failure.source.error_class = mesh_abi::kMoeErrorClassCOMMAND;
    failure.source.object_kind = mesh_abi::kErrorSourceObjectKindCOMMAND;
    failure.source.ordinal = 1;
    for (const auto detail : {agent_abi::E_DCORE_COMMAND_STATE,
            agent_abi::E_DCORE_ENGINE,
            agent_abi::E_DCORE_EVENT,
            agent_abi::E_DCORE_EVENT_NAMESPACE,
            agent_abi::E_DCORE_OPCODE,
            agent_abi::E_DCORE_POISON_READ,
            agent_abi::E_DCORE_SRAM_BOUNDS,
            agent_abi::E_DCORE_SRAM_LIFETIME,
            agent_abi::E_DCORE_TIMING_OVERFLOW,
            agent_abi::E_MOE_CAPACITY,
            agent_abi::E_MOE_COMBINE_BYTES,
            agent_abi::E_MOE_DISPATCH_BYTES,
            agent_abi::E_MOE_MATERIALIZE_CAPACITY,
            agent_abi::E_MOE_PROFILE,
            agent_abi::E_MOE_ROUTE_DIGEST,
            agent_abi::E_MOE_ROUTE_REPLAY,
            agent_abi::E_MOE_TOPK_DUP}) {
        failure.code = detail;
        const auto diagnostic = errorSourceDiagnostic(failure);
        ASSERT_TRUE(diagnostic) << uint32_t(detail);
        EXPECT_EQ(agent_abi::rdU32(diagnostic->data()), uint32_t(detail));
    }
}

TEST(AgentProtocolValidation, RejectsSourcesOutsideTheConstructorClosure)
{
    using namespace mesh_abi;
    ErrorCandidate failure;
    failure.code = agent_abi::E_DCORE_ENGINE;
    auto &source = failure.source;
    source.error_class = kMoeErrorClassCOMMAND;
    source.core_id_or_ffff = 2;
    source.ordinal = 1;
    ASSERT_TRUE(errorSourceDiagnostic(failure));
    source.object_kind = kErrorSourceObjectKindDESCRIPTOR;
    EXPECT_FALSE(errorSourceDiagnostic(failure));
    source.error_class = kMoeErrorClassDMA_AXI;
    ASSERT_TRUE(errorSourceDiagnostic(failure));
    source.region_group_id = 7;
    EXPECT_FALSE(errorSourceDiagnostic(failure));
    source.domain = kMeshObjectDomainCOORDINATOR;
    source.error_class = kMoeErrorClassMATERIALIZE;
    source.object_kind = kErrorSourceObjectKindMATERIALIZER_CHECK;
    source.ordinal = kMaterializerCheckSiteCOUNT;
    EXPECT_FALSE(errorSourceDiagnostic(failure));
    source.core_id_or_ffff = UINT16_MAX;
    ASSERT_TRUE(errorSourceDiagnostic(failure));
    source.ordinal = kMaterializerCheckSiteCACHE_BINDING + 1;
    EXPECT_FALSE(errorSourceDiagnostic(failure));
}

TEST(AgentProtocolValidation, CacheTagIndexZeroHasAValidFullKeySource)
{
    ErrorCandidate failure;
    failure.code = agent_abi::E_AXI_RESPONSE;
    auto &source = failure.source;
    source.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
    source.domain = mesh_abi::kMeshObjectDomainWEIGHT_FILL;
    source.object_kind = mesh_abi::kErrorSourceObjectKindWEIGHT_FILL_OBLIGATION;
    agent_abi::wrU32(source.aux_key.data() + 12, 1);
    EXPECT_TRUE(errorSourceDiagnostic(failure));
}

TEST(AgentProtocolValidationTest, NoKvFailureDiagnosticRequiresExactFailureEvidence)
{
    Gate3MetadataExpectation expected{9, 4, 7, 3, 0,
        agent_abi::kCqStatusAXI_ERROR, agent_abi::kCqFlagsMETADATA_VALID, 0, 200};
    expected.failure = ErrorCandidate{123, {}, agent_abi::E_AXI_RESPONSE};
    expected.failure->source.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
    expected.failure->source.object_kind = mesh_abi::kErrorSourceObjectKindDESCRIPTOR;
    expected.failure->source.ordinal = 3;
    const auto diagnostic = errorSourceDiagnostic(*expected.failure);
    ASSERT_TRUE(diagnostic);
    agent_abi::OutputMetadata header;
    header.magic = 0x4f4e4741;
    header.abi_major = agent_abi::kAbiMajor;
    header.abi_minor = agent_abi::kAbiMinor;
    header.header_bytes = agent_abi::kOutputMetadataBytes;
    header.total_bytes = 200;
    header.terminal_status = expected.cqStatus;
    header.request_id = expected.requestId;
    header.session_id = expected.sessionId;
    header.user_id = expected.userId;
    header.task_seq = expected.taskSequence;
    const auto encoded = agent_abi::encodeOutputMetadata(header);
    std::vector<uint8_t> record(encoded.begin(), encoded.end());
    record.resize(200, 0);
    agent_abi::wrU16(record.data() + 128, agent_abi::kOutputTlvTypeDIAGNOSTIC);
    agent_abi::wrU16(record.data() + 130, agent_abi::kTlvFlagsREQUIRED);
    agent_abi::wrU32(record.data() + 132, 64);
    std::copy(diagnostic->begin(), diagnostic->end(), record.begin() + 136);
    for (size_t mutation = 0; mutation <= 64; ++mutation) {
        auto candidate = record;
        if (mutation < 64)
            candidate[136 + mutation] ^= 1;
        agent_abi::wrU32(candidate.data() + 120, 0);
        agent_abi::wrU32(candidate.data() + 120, agent_abi::crc32c(candidate.data(), candidate.size()));
        EXPECT_EQ(gate3MetadataRecordValid(candidate, expected), mutation == 64) << mutation;
    }
    expected.failure.reset();
    agent_abi::wrU32(record.data() + 120, agent_abi::crc32c(record.data(), record.size()));
    EXPECT_FALSE(gate3MetadataRecordValid(record, expected));
}

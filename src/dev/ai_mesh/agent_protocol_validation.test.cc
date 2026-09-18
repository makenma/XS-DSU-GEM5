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

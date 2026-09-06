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

}
}
}

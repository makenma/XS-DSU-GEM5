#include <gtest/gtest.h>
#include "dev/ai_mesh/agent_sq_admission.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::agent_abi;

namespace
{
SqDescriptor request(uint64_t id = 1, uint16_t opcode = kSqOpcodeGENERATE)
{
    SqDescriptor row;
    row.abi_major = kAbiMajor;
    row.abi_minor = kAbiMinor;
    row.opcode = opcode;
    row.request_id = row.completion_cookie = id;
    row.parameter_block_addr = 0x10000;
    row.parameter_block_bytes = kParameterHeaderBytes;
    if (opcode != kSqOpcodeCANCEL)
        row.session_id = 1;
    if (opcode == kSqOpcodeGENERATE)
        row.program_id = row.profile_id = 1;
    return row;
}
}

TEST(AgentSqAdmission, EveryOpcodeSharesRunLifetimeIdentityAndRejectsConflictingCookies)
{
    AgentSqAdmission gate(3);
    for (auto opcode : {kSqOpcodeGENERATE, kSqOpcodeRELEASE_SESSION, kSqOpcodeCANCEL}) {
        const auto id = uint64_t(opcode);
        auto row = request(id, opcode);
        EXPECT_TRUE(gate.accept(row).accepted());
        for (auto other : {kSqOpcodeGENERATE, kSqOpcodeRELEASE_SESSION, kSqOpcodeCANCEL}) {
            const auto result = gate.accept(request(id, other));
            EXPECT_EQ(result.detail, E_REQUEST_IDENTITY);
            EXPECT_EQ(result.flags, kCqFlagsSQ_IDENTITY_ERROR | kCqFlagsDETAIL_IN_CQ);
        }
    }
    EXPECT_EQ(gate.size(), 3u);
    auto row = request(4);
    row.completion_cookie = 1;
    EXPECT_EQ(gate.accept(row).detail, E_REQUEST_IDENTITY);
    EXPECT_EQ(gate.accept(request(0)).detail, E_REQUEST_IDENTITY);
    EXPECT_TRUE(gate.accept(request(4)).capacity_error);
    EXPECT_EQ(gate.opcode(1), kSqOpcodeGENERATE);
    EXPECT_EQ(gate.opcode(2), kSqOpcodeRELEASE_SESSION);
    EXPECT_FALSE(gate.opcode(4));
}

TEST(AgentSqAdmission, UnsupportedAbiNeverReadsOrClaimsIdentity)
{
    AgentSqAdmission gate(1);
    for (bool major : {false, true}) {
        auto row = request(0);
        if (major) ++row.abi_major;
        else ++row.abi_minor;
        const auto result = gate.accept(row);
        EXPECT_EQ(result.flags, kCqFlagsSQ_ABI_ERROR | kCqFlagsDETAIL_IN_CQ);
        EXPECT_EQ(result.detail, E_REQUEST_BINDING);
        EXPECT_EQ(gate.size(), 0u);
    }
    EXPECT_TRUE(gate.accept(request()).accepted());
}

TEST(AgentSqAdmission, TrustedFieldErrorsKeepOneIdentityAndTheExactControlClass)
{
    for (auto opcode : {kSqOpcodeGENERATE, kSqOpcodeRELEASE_SESSION, kSqOpcodeCANCEL}) {
        for (unsigned field = 0; field < 6; ++field) {
            if (opcode == kSqOpcodeGENERATE && field >= 2)
                continue;
            AgentSqAdmission gate(1);
            auto row = request(1, opcode);
            switch (field) {
              case 0: row.flags2 = 1; break;
              case 1: row.reserved = 1; break;
              case 2: row.qos = 1; break;
              case 3: row.flags = 1; break;
              case 4: row.program_id = 1; break;
              case 5: row.profile_id = 1; break;
            }
            const auto result = gate.accept(row);
            EXPECT_EQ(result.detail, E_RESERVED_FIELD);
            EXPECT_EQ(result.flags, kCqFlagsDETAIL_IN_CQ |
                (opcode == kSqOpcodeGENERATE ? 0 : kCqFlagsCONTROL_COMMAND));
            EXPECT_EQ(gate.size(), 1u);
            EXPECT_EQ(gate.accept(request()).detail, E_REQUEST_IDENTITY);
        }
    }
}

TEST(AgentSqAdmission, SequenceKeyedCompletionHasOneExactFlagStatusDetailAndIdentity)
{
    for (auto flag : {kCqFlagsSQ_SEQ_ONLY_ERROR, kCqFlagsSQ_ABI_ERROR, kCqFlagsSQ_IDENTITY_ERROR}) {
        CqDescriptor cq;
        cq.flags = flag | kCqFlagsDETAIL_IN_CQ;
        cq.completion_cookie = 7;
        cq.status = flag == kCqFlagsSQ_SEQ_ONLY_ERROR ? kCqStatusSQ_CRC : kCqStatusPARAM_ERROR;
        cq.output_bytes_or_detail_code = flag == kCqFlagsSQ_SEQ_ONLY_ERROR ? E_SQ_CRC :
            flag == kCqFlagsSQ_ABI_ERROR ? E_REQUEST_BINDING : E_REQUEST_IDENTITY;
        EXPECT_TRUE(sequenceKeyedCqValid(cq));
        auto bad = cq;
        bad.flags |= kCqFlagsCONTROL_COMMAND;
        EXPECT_FALSE(sequenceKeyedCqValid(bad));
        bad = cq;
        bad.request_id = 7;
        EXPECT_FALSE(sequenceKeyedCqValid(bad));
        bad = cq;
        bad.status = kCqStatusSUCCESS;
        EXPECT_FALSE(sequenceKeyedCqValid(bad));
        bad = cq;
        bad.output_bytes_or_detail_code = E_RESERVED_FIELD;
        EXPECT_FALSE(sequenceKeyedCqValid(bad));
    }
}

TEST(AgentSqAdmission, FetchEnvelopeIsValidatedBeforeAnyParameterRead)
{
    for (const uint64_t address : {uint64_t{0}, uint64_t{7}, UINT64_MAX - 7}) {
        AgentSqAdmission gate(1);
        auto row = request();
        row.parameter_block_addr = address;
        EXPECT_EQ(gate.accept(row).detail, E_REQUEST_BINDING);
    }
    for (const uint32_t bytes : {uint32_t{0}, uint32_t{152}, uint32_t{161}, UINT32_MAX - 7}) {
        AgentSqAdmission gate(1);
        auto row = request();
        row.parameter_block_bytes = bytes;
        EXPECT_EQ(gate.accept(row).detail, E_PARAMETER_LENGTH_MISMATCH);
    }
    AgentSqAdmission gate(1);
    auto row = request(1, kSqOpcodeCANCEL);
    row.parameter_block_bytes = 168;
    EXPECT_EQ(gate.accept(row).detail, E_PARAMETER_LENGTH_MISMATCH);
}

TEST(AgentSqAdmission, ControlCompletionUsesItsExactOpcodeAndFlagContract)
{
    CqDescriptor cq;
    cq.request_id = cq.completion_cookie = 7;
    cq.flags = kCqFlagsCONTROL_COMMAND;
    cq.status = kCqStatusBUSY;
    EXPECT_TRUE(controlCqValid(cq, kSqOpcodeRELEASE_SESSION));
    EXPECT_FALSE(controlCqValid(cq, kSqOpcodeCANCEL));
    cq.status = kCqStatusALREADY_TERMINAL;
    EXPECT_TRUE(controlCqValid(cq, kSqOpcodeCANCEL));
    EXPECT_FALSE(controlCqValid(cq, kSqOpcodeRELEASE_SESSION));
    cq.status = kCqStatusSUCCESS;
    cq.output_bytes_or_detail_code = 1;
    EXPECT_FALSE(controlCqValid(cq, kSqOpcodeCANCEL));
    cq.flags |= kCqFlagsDETAIL_IN_CQ;
    cq.status = kCqStatusPARAM_ERROR;
    cq.output_bytes_or_detail_code = E_RESERVED_FIELD;
    EXPECT_TRUE(controlCqValid(cq, kSqOpcodeCANCEL));
    cq.flags |= kCqFlagsPARTIAL_OUTPUT;
    EXPECT_FALSE(controlCqValid(cq, kSqOpcodeCANCEL));
}

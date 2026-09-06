#include <gtest/gtest.h>

#include "dev/ai_mesh/gate3_fatal_reducer.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(Gate3FatalReducerTest, MsiCandidateHasCanonicalWire)
{
    Gate3FatalReducer reducer;
    Gate3PhysicalSourceTokenV1 token{
        agent_abi::PhysicalSourceKindV1::AXI_TRANSACTION,
        static_cast<uint16_t>(agent_abi::AxiTransactionSubkindV1::WRITE_AW_W),
        agent_abi::FatalComponentKindV1::NPU_FRONTEND,
        0, UINT32_MAX, 7, 0};
    ASSERT_TRUE(reducer.stageFault(
        1, agent_abi::FaultSiteV1::MSI_TARGET_OR_B, 0, UINT32_MAX,
        gate3MsiKey(7, 3, 32), 7, token));
    EXPECT_EQ(
        gate3Hex(reducer.first().wire().data(), reducer.first().wire().size()),
        "0100000000000000040000000e00020000000000ffffffff05001400"
        "07000000000000000300000000000000200000000700000000000000"
        "06000200");
    const auto sourceWire = token.wire();
    EXPECT_EQ(
        gate3Hex(sourceWire.data(), sourceWire.size()),
        "000001000200000000000000ffffffff070000000000000000000000"
        "00000000");
}

TEST(Gate3FatalReducerTest, SameTickMinimumUsesTypedNumericKey)
{
    Gate3FatalReducer reducer;
    Gate3PhysicalSourceTokenV1 token{
        agent_abi::PhysicalSourceKindV1::INTERNAL_EDGE, 0,
        agent_abi::FatalComponentKindV1::INTERNAL,
        0, UINT32_MAX, 1, 0};
    ASSERT_TRUE(reducer.stageFault(
        9, agent_abi::FaultSiteV1::MSI_TARGET_OR_B, 0, UINT32_MAX,
        gate3MsiKey(2, 2, 33), 2, token));
    ASSERT_TRUE(reducer.stageFault(
        9, agent_abi::FaultSiteV1::SQ_R_TRANSPORT, 0, UINT32_MAX,
        gate3SqIntakeKey(1, 0), 1, token));
    EXPECT_EQ(reducer.candidateCount(), 2u);
    EXPECT_EQ(reducer.first().sourceClass,
              agent_abi::FatalSourceClassV1::SQ_INTAKE);
    EXPECT_EQ(reducer.first().errorCode, agent_abi::E_AGENT_PROTOCOL_FATAL);
}

TEST(Gate3FatalReducerTest, LaterTickCannotChangeFatalCut)
{
    Gate3FatalReducer reducer;
    Gate3PhysicalSourceTokenV1 token{
        agent_abi::PhysicalSourceKindV1::INTERNAL_EDGE, 0,
        agent_abi::FatalComponentKindV1::INTERNAL,
        0, UINT32_MAX, 1, 0};
    ASSERT_TRUE(reducer.stageInvariant(
        4, agent_abi::InvariantSiteV1::LEDGER_OWNERSHIP,
        0, UINT32_MAX,
        gate3InternalKey(agent_abi::InvariantSiteV1::LEDGER_OWNERSHIP), token));
    EXPECT_FALSE(reducer.stageFault(
        5, agent_abi::FaultSiteV1::SQ_R_TRANSPORT, 0, UINT32_MAX,
        gate3SqIntakeKey(1, 0), 1, token));
    EXPECT_EQ(reducer.candidateCount(), 1u);
}

}
}
}

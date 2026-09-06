#include <gtest/gtest.h>

#include "dev/ai_mesh/agent_protocol_layout.hh"
#include "dev/ai_mesh/agent_ring_state.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(AgentRingTest, DepthOneUsesSequenceAsGeneration)
{
    RingGeometry geometry(1);
    EXPECT_EQ(geometry.slot(SqSeq(0)), 0u);
    EXPECT_EQ(geometry.slot(SqSeq(9)), 0u);
    EXPECT_EQ(geometry.generation(SqSeq(9)), 9u);
}

TEST(AgentRingTest, DepthTwoWrapsAndAdvancesGeneration)
{
    RingGeometry geometry(2);
    EXPECT_EQ(geometry.slot(SqSeq(0)), 0u);
    EXPECT_EQ(geometry.slot(SqSeq(1)), 1u);
    EXPECT_EQ(geometry.slot(SqSeq(2)), 0u);
    EXPECT_EQ(geometry.generation(SqSeq(0)), 0u);
    EXPECT_EQ(geometry.generation(SqSeq(1)), 0u);
    EXPECT_EQ(geometry.generation(SqSeq(2)), 1u);
    EXPECT_EQ(geometry.generation(SqSeq(3)), 1u);
}

TEST(AgentRingTest, FullWindowBackpressuresWithoutOverwrite)
{
    RingState<SqSeq> ring(2);
    ASSERT_TRUE(ring.reserve(2).has_value());
    EXPECT_EQ(ring.producer().value(), 2u);
    EXPECT_EQ(ring.consumer().value(), 0u);
    EXPECT_EQ(ring.occupancy(), 2u);
    EXPECT_FALSE(ring.reserve(1).has_value());
    EXPECT_EQ(ring.producer().value(), 2u);
    EXPECT_TRUE(ring.consume(1));
    EXPECT_EQ(ring.consumer().value(), 1u);
    ASSERT_TRUE(ring.reserve(1).has_value());
    EXPECT_EQ(ring.producer().value(), 3u);
}

TEST(AgentRingTest, SequenceAdditionRejectsOverflow)
{
    EXPECT_FALSE(checkedSequenceAdd<SqSeq>(SqSeq(UINT64_MAX), 1).has_value());
    ASSERT_TRUE(checkedSequenceAdd<SqSeq>(SqSeq(7), 3).has_value());
    EXPECT_EQ(checkedSequenceAdd<SqSeq>(SqSeq(7), 3)->value(), 10u);
}

TEST(AgentRingTest, ConsumeCannotPassProducer)
{
    RingState<CqSeq> ring(1);
    EXPECT_FALSE(ring.consume(1));
    ASSERT_TRUE(ring.reserve(1).has_value());
    EXPECT_TRUE(ring.consume(1));
    EXPECT_FALSE(ring.consume(1));
}

TEST(AgentProtocolLayoutTest, UsesGeneratedRecordStrides)
{
    AgentProtocolLayout layout(0x10001000, 2, 0x10050000, 2);
    EXPECT_EQ(layout.sqAddress(SqSeq(0)), 0x10001000u);
    EXPECT_EQ(layout.sqAddress(SqSeq(1)), 0x10001040u);
    EXPECT_EQ(layout.sqAddress(SqSeq(2)), 0x10001000u);
    EXPECT_EQ(layout.cqAddress(CqSeq(0)), 0x10050000u);
    EXPECT_EQ(layout.cqAddress(CqSeq(1)), 0x10050020u);
    EXPECT_EQ(layout.cqAddress(CqSeq(2)), 0x10050000u);
    EXPECT_EQ(layout.sqSpanBytes(), 128u);
    EXPECT_EQ(layout.cqSpanBytes(), 64u);
}

TEST(AgentProtocolLayoutTest, RejectsAddressOverflow)
{
    EXPECT_THROW(
        AgentProtocolLayout(UINT64_MAX - 31, 2, 0x1000, 1),
        std::invalid_argument);
    EXPECT_THROW(
        AgentProtocolLayout(0x1000, 1, UINT64_MAX - 15, 1),
        std::invalid_argument);
}

}
}
}

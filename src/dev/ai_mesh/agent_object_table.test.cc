#include <gtest/gtest.h>

#include "dev/ai_mesh/agent_object_table.hh"

namespace
{

using gem5::ai_mesh::AgentObjectId;
using gem5::ai_mesh::AgentObjectKind;
using gem5::ai_mesh::AgentObjectState;
using gem5::ai_mesh::AgentObjectTable;

}

TEST(AgentObjectTable, ReserveIsBoundedByCapacity)
{
    AgentObjectTable table(2);
    const auto first = table.reserve(AgentObjectKind::RawLog, 100, 128);
    const auto second = table.reserve(AgentObjectKind::Excerpt, 16, 16);
    const auto third = table.reserve(AgentObjectKind::RawLog, 100, 128);
    ASSERT_TRUE(first.has_value());
    ASSERT_TRUE(second.has_value());
    EXPECT_FALSE(third.has_value());
    EXPECT_EQ(table.liveCount(), 2u);
    EXPECT_NE(*first, *second);
    EXPECT_EQ(table.allocationBytes(*first), 128u);
    EXPECT_EQ(table.plannedBytes(*first), 100u);
}

TEST(AgentObjectTable, ReserveRejectsAllocationBelowPlanned)
{
    AgentObjectTable table(4);
    EXPECT_FALSE(table.reserve(AgentObjectKind::GeneratedCode, 200, 128));
    EXPECT_FALSE(table.reserve(AgentObjectKind::GeneratedCode, 0, 128));
}

TEST(AgentObjectTable, GeneratedCodeCommitsOnlyAtFullPrefix)
{
    AgentObjectTable table(4);
    const auto id = table.reserve(AgentObjectKind::GeneratedCode, 4096, 4096);
    ASSERT_TRUE(id.has_value());
    EXPECT_TRUE(table.beginProduce(*id));
    EXPECT_FALSE(table.commit(*id));
    EXPECT_TRUE(table.extendValidPrefix(*id, 2048));
    EXPECT_FALSE(table.commit(*id));
    EXPECT_FALSE(table.extendValidPrefix(*id, 2049));
    EXPECT_TRUE(table.extendValidPrefix(*id, 2048));
    EXPECT_TRUE(table.commit(*id));
    EXPECT_EQ(table.state(*id), AgentObjectState::Committed);
    EXPECT_EQ(table.validBytes(*id), 4096u);
}

TEST(AgentObjectTable, HostObjectCommitsAtomically)
{
    AgentObjectTable table(4);
    const auto id = table.reserve(AgentObjectKind::RawLog, 104857600,
                                  104857600);
    ASSERT_TRUE(id.has_value());
    EXPECT_TRUE(table.commitHostObject(*id));
    EXPECT_EQ(table.state(*id), AgentObjectState::Committed);
    EXPECT_EQ(table.validBytes(*id), 104857600u);
    EXPECT_FALSE(table.commit(*id));
}

TEST(AgentObjectTable, ReadsAreBoundedByValidPrefix)
{
    AgentObjectTable table(4);
    const auto id = table.reserve(AgentObjectKind::GeneratedCode, 100, 128);
    ASSERT_TRUE(id.has_value());
    table.beginProduce(*id);
    EXPECT_FALSE(table.readAllowed(*id, 0, 1));
    EXPECT_TRUE(table.extendValidPrefix(*id, 100));
    EXPECT_TRUE(table.commit(*id));
    EXPECT_TRUE(table.readAllowed(*id, 0, 100));
    EXPECT_FALSE(table.readAllowed(*id, 100, 1));
    EXPECT_FALSE(table.readAllowed(*id, 127, 1));
    EXPECT_FALSE(table.readAllowed(*id, 0, 101));
}

TEST(AgentObjectTable, ReleaseRequiresDrainedProducerAndZeroRefs)
{
    AgentObjectTable table(4);
    const auto id = table.reserve(AgentObjectKind::GeneratedCode, 10, 16);
    ASSERT_TRUE(id.has_value());
    table.beginProduce(*id);
    table.extendValidPrefix(*id, 10);
    EXPECT_TRUE(table.commit(*id));
    EXPECT_TRUE(table.acquireConsumerRef(*id));
    EXPECT_TRUE(table.acquireConsumerRef(*id));
    EXPECT_EQ(table.state(*id), AgentObjectState::Consuming);
    EXPECT_FALSE(table.release(*id));
    EXPECT_TRUE(table.releaseConsumerRef(*id));
    EXPECT_FALSE(table.release(*id));
    EXPECT_TRUE(table.releaseConsumerRef(*id));
    EXPECT_TRUE(table.release(*id));
    EXPECT_EQ(table.state(*id), AgentObjectState::Released);
    EXPECT_EQ(table.liveCount(), 0u);
    EXPECT_FALSE(table.release(*id));
    EXPECT_TRUE(table.known(*id));
}

TEST(AgentObjectTable, ErrorPathPoisonsDrainsAndReleases)
{
    AgentObjectTable table(4);
    const auto id = table.reserve(AgentObjectKind::GeneratedCode, 10, 16);
    ASSERT_TRUE(id.has_value());
    table.beginProduce(*id);
    table.extendValidPrefix(*id, 4);
    EXPECT_TRUE(table.poison(*id));
    EXPECT_EQ(table.state(*id), AgentObjectState::AbortedPoisoned);
    EXPECT_FALSE(table.errorDrain(*id));
    EXPECT_TRUE(table.markProducerDrained(*id));
    EXPECT_TRUE(table.errorDrain(*id));
    EXPECT_EQ(table.state(*id), AgentObjectState::ErrorDrained);
    EXPECT_TRUE(table.release(*id));
    EXPECT_EQ(table.state(*id), AgentObjectState::Released);
    EXPECT_EQ(table.liveCount(), 0u);
    EXPECT_FALSE(table.poison(*id));
}

TEST(AgentObjectTable, ConsumerRefsOnlyOnCommittedObjects)
{
    AgentObjectTable table(4);
    const auto id = table.reserve(AgentObjectKind::Excerpt, 16, 16);
    ASSERT_TRUE(id.has_value());
    EXPECT_FALSE(table.acquireConsumerRef(*id));
    EXPECT_TRUE(table.commitHostObject(*id));
    EXPECT_TRUE(table.acquireConsumerRef(*id));
    EXPECT_EQ(table.consumerRefs(*id), 1u);
    EXPECT_TRUE(table.releaseConsumerRef(*id));
    EXPECT_FALSE(table.releaseConsumerRef(*id));
}

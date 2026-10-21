#include <gtest/gtest.h>

#include "dev/ai_mesh/memory_address_map.hh"

namespace gem5::ai_mesh
{
TEST(MemoryAddressMapTest, SplitsAnImageAcrossAdjacentOwners)
{
    MemoryAddressMap map({{0x2000, 0x3000, 7}, {0x1000, 0x2000, 5}});
    const auto spans = map.split(0x1ff0, 0x30);
    ASSERT_TRUE(spans);
    ASSERT_EQ(spans->size(), 2);
    EXPECT_EQ(spans->at(0).owner, 5);
    EXPECT_EQ(spans->at(0).address, 0x1ff0);
    EXPECT_EQ(spans->at(0).size, 0x10);
    EXPECT_EQ(spans->at(1).owner, 7);
    EXPECT_EQ(spans->at(1).address, 0x2000);
    EXPECT_EQ(spans->at(1).size, 0x20);
}

TEST(MemoryAddressMapTest, RejectsAHoleEvenWhenBothEndsHaveOwners)
{
    MemoryAddressMap map({{0x1000, 0x1100, 5}, {0x1200, 0x1300, 7}});
    EXPECT_FALSE(map.split(0x10ff, 0x102));
    EXPECT_FALSE(map.split(0x0fff, 2));
    EXPECT_FALSE(map.split(0x12ff, 2));
    ASSERT_TRUE(map.split(0x1200, 0x100));
}

TEST(MemoryAddressMapTest, RejectsOverlapsAndOverflow)
{
    EXPECT_THROW((MemoryAddressMap({{0x1000, 0x1200, 5},
                                    {0x1100, 0x1300, 7}})), std::invalid_argument);
    MemoryAddressMap map({{UINT64_MAX - 16, UINT64_MAX, 5}});
    EXPECT_TRUE(map.split(UINT64_MAX - 16, 16));
    EXPECT_FALSE(map.split(UINT64_MAX - 16, 17));
    EXPECT_FALSE(map.split(UINT64_MAX, 1));
    EXPECT_FALSE(map.split(UINT64_MAX - 16, 0));
    EXPECT_FALSE(MemoryAddressMap({}).split(0, 1));
}
}

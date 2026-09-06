#include <gtest/gtest.h>

#include <stdexcept>

#include "dev/ai_mesh/gate3_msi_id_pool.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(Gate3MsiIdPoolTest, ReusesLowestFreeId)
{
    Gate3MsiIdPool pool(32, 3);
    EXPECT_EQ(pool.acquire(), 32u);
    EXPECT_EQ(pool.acquire(), 33u);
    EXPECT_EQ(pool.acquire(), 34u);
    EXPECT_EQ(pool.acquire(), std::nullopt);
    EXPECT_TRUE(pool.release(33));
    EXPECT_EQ(pool.acquire(), 33u);
}

TEST(Gate3MsiIdPoolTest, RejectsInvalidReleaseAndRange)
{
    EXPECT_THROW(Gate3MsiIdPool(32, 0), std::invalid_argument);
    EXPECT_THROW(Gate3MsiIdPool(UINT32_MAX, 2), std::invalid_argument);
    Gate3MsiIdPool pool(32, 2);
    EXPECT_FALSE(pool.release(31));
    EXPECT_FALSE(pool.release(34));
    EXPECT_EQ(pool.acquire(), 32u);
    EXPECT_TRUE(pool.release(32));
    EXPECT_FALSE(pool.release(32));
}

}
}
}

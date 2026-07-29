#include <gtest/gtest.h>

#include "mem/cache/CHI/Cache2ChiBridge.hh"

namespace gem5
{
namespace Chi
{

TEST(Cache2ChiBridgeProtocolTest,
     Case13NormalPromotedUpgradeKeepsUpgradeResponse)
{
    EXPECT_TRUE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        true, false, false));
}

TEST(Cache2ChiBridgeProtocolTest,
     PrecedingInvalidationRequiresDataBearingResponse)
{
    EXPECT_FALSE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        true, true, true));
}

TEST(Cache2ChiBridgeProtocolTest, SharedSnoopKeepsUpgradeResponse)
{
    EXPECT_TRUE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        true, true, false));
}

TEST(Cache2ChiBridgeProtocolTest, LateInvalidationKeepsUpgradeResponse)
{
    EXPECT_TRUE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        true, false, true));
}

TEST(Cache2ChiBridgeProtocolTest, OrdinaryReadNeverBecomesUpgradeResponse)
{
    EXPECT_FALSE(Cache2ChiBridge::retainPromotedUpgradeResponse(
        false, true, true));
}

} // namespace Chi
} // namespace gem5

#include <gtest/gtest.h>

#include <vector>

#include "mem/ruby/network/garnet/InputCapacityConfig.hh"

namespace gem5::ruby::garnet
{

TEST(GarnetInputCapacityTest, EmptyOverridesPreserveLegacyDepths)
{
    InputCapacityConfig config;
    EXPECT_TRUE(config.configure({1, 4, 1}, {}, {}, 2, false).empty());
    EXPECT_EQ(config.routerDepths(0, 0), (std::vector<uint32_t>{1, 4, 1}));
    EXPECT_EQ(config.routerDepths(1, 3), config.routerDepths(0, 0));
    EXPECT_EQ(config.niDepths(), config.routerDepths(0, 0));
    EXPECT_FALSE(config.extended());
}

TEST(GarnetInputCapacityTest, ResolvedDepthBelongsToReceiverInput)
{
    InputCapacityConfig config;
    ASSERT_TRUE(config.configure({4, 8}, {2, 3},
        {"0:0:0:1", "0:1:0:16", "1:0:0:8", "1:0:1:1"},
        2, false).empty());
    EXPECT_EQ(config.routerDepths(0, 0), (std::vector<uint32_t>{1, 8}));
    EXPECT_EQ(config.routerDepths(0, 1), (std::vector<uint32_t>{16, 8}));
    EXPECT_EQ(config.routerDepths(1, 0), (std::vector<uint32_t>{8, 1}));
    EXPECT_EQ(config.niDepths(), (std::vector<uint32_t>{2, 3}));
    EXPECT_TRUE(config.extended());
    EXPECT_TRUE(config.validatePorts({2, 1}).empty());
}

TEST(GarnetInputCapacityTest, ExplicitUniformMatchesFallback)
{
    InputCapacityConfig config;
    ASSERT_TRUE(config.configure({4, 8}, {},
        {"0:0:0:4", "0:0:1:8", "1:0:0:4", "1:0:1:8"},
        2, false).empty());
    EXPECT_EQ(config.routerDepths(0, 0), config.niDepths());
    EXPECT_EQ(config.routerDepths(1, 0), config.niDepths());
}

TEST(GarnetInputCapacityTest, RejectsDuplicateAndConflictingOverrides)
{
    for (const auto &duplicate : {"0:0:1:4", "0:0:1:8"}) {
        InputCapacityConfig config;
        EXPECT_FALSE(config.configure({4, 8}, {},
            {"0:0:1:4", duplicate}, 1, false).empty());
    }
}

TEST(GarnetInputCapacityTest, RejectsInvalidFieldsAndUnknownCoordinates)
{
    for (const auto &entry : {"0:0:0:0", "0:0:0:-1", "-1:0:0:1",
         "0:-1:0:1", "0:0:2:1", "2:0:0:1", "0:0:0:2147483648",
         "0:0:0:4294967296", "0:0:0", "0:0:0:1:2", "0:0:0:1x",
         "0:0:0:+1", "0:0:0: 1", "0::0:1"}) {
        InputCapacityConfig config;
        EXPECT_FALSE(config.configure({4, 8}, {}, {entry},
                                      2, false).empty()) << entry;
    }
}

TEST(GarnetInputCapacityTest, RejectsNonexistentActualPort)
{
    InputCapacityConfig config;
    ASSERT_TRUE(config.configure({4, 8}, {}, {"1:2:0:1"},
                                 2, false).empty());
    EXPECT_FALSE(config.validatePorts({4, 2}).empty());
    EXPECT_TRUE(config.validatePorts({4, 3}).empty());
}

TEST(GarnetInputCapacityTest, RejectsInvalidNiDepthAndUnsupportedFaultModel)
{
    for (const auto &depths : {std::vector<uint32_t>{1},
                              std::vector<uint32_t>{1, 0}}) {
        InputCapacityConfig config;
        EXPECT_FALSE(config.configure({4, 8}, depths, {},
                                      2, false).empty());
    }
    InputCapacityConfig config;
    EXPECT_FALSE(config.configure({4, 8}, {}, {"0:0:0:1"},
                                  2, true).empty());
    EXPECT_FALSE(config.configure({4, 8}, {1, 1}, {},
                                  2, true).empty());
    EXPECT_TRUE(config.configure({4, 8}, {}, {}, 2, true).empty());
}

}

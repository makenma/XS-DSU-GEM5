#include <gtest/gtest.h>

#include "dev/ai_mesh/serving_instance_binding.hh"

namespace
{

using namespace gem5::ai_mesh;

TEST(ServingInstanceBinding, TranslatesBelowTemplateWithoutUnsignedWrap)
{
    ServingInstanceBinding binding;
    ASSERT_TRUE(binding.bind(7, 0x200000, 0x100000, {0, 128}, {128, 16}));
    EXPECT_EQ(binding.resolve(7, 0x200040, 64, false, 0x800000000),
              std::optional<uint64_t>(0x100040));
    EXPECT_EQ(binding.resolve(7, 0x200080, 16, true, 0x800000000),
              std::optional<uint64_t>(0x100080));
}

TEST(ServingInstanceBinding, EnforcesFrozenReadAndAppendWindows)
{
    ServingInstanceBinding binding;
    ASSERT_TRUE(binding.bind(7, 0x200000, 0x100000, {0, 128}, {128, 16}));
    EXPECT_FALSE(binding.resolve(7, 0x200080, 1, false, 0));
    EXPECT_FALSE(binding.resolve(7, 0x20007f, 1, true, 0));
    EXPECT_FALSE(binding.resolve(7, 0x20008f, 2, true, 0));
    EXPECT_FALSE(binding.resolve(7, 0x1fffff, 1, false, 0));
    EXPECT_FALSE(binding.bind(7, 0, 0, {0, 128}, {0, 0}));
}

TEST(ServingInstanceBinding, OrdinaryExecutionUsesCheckedRegionAddress)
{
    ServingInstanceBinding binding;
    EXPECT_EQ(binding.resolve(7, 64, 32, false, 0x800000000),
              std::optional<uint64_t>(0x800000040));
    EXPECT_FALSE(binding.resolve(7, UINT64_MAX, 32, false, 1));
    EXPECT_FALSE(binding.bind(7, 0, UINT64_MAX, {0, 32}, {0, 0}));
}

}

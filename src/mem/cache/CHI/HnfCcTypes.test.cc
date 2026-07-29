#include <gtest/gtest.h>

#include "mem/cache/CHI/HnfCcTypes.hh"

namespace gem5::Chi
{

TEST(HnfCcAllocationIdentityTest, RejectsLateGenerationBeforeTokenReuse)
{
    constexpr HnfCcAllocationIdentity reused{42, 7, 91};
    constexpr HnfCcAllocationIdentity late{41, 3, 19};

    static_assert(classifyHnfCcIdentity(reused, late) ==
                  HnfCcIdentityMatch::StaleGeneration);
    EXPECT_EQ(classifyHnfCcIdentity(reused, late),
              HnfCcIdentityMatch::StaleGeneration);
}

TEST(HnfCcAllocationIdentityTest, RequiresExactOwnerWithinGeneration)
{
    constexpr HnfCcAllocationIdentity active{42, 7, 91};

    EXPECT_EQ(classifyHnfCcIdentity(active, active),
              HnfCcIdentityMatch::Active);
    EXPECT_EQ(classifyHnfCcIdentity(active, {42, 8, 91}),
              HnfCcIdentityMatch::CorruptOwner);
    EXPECT_EQ(classifyHnfCcIdentity(active, {42, 7, 92}),
              HnfCcIdentityMatch::CorruptOwner);
}

} // namespace gem5::Chi

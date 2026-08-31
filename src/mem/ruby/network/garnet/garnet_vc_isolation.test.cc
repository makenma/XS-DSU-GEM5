#include <gtest/gtest.h>

#include <vector>

#include "mem/ruby/network/garnet/OutVcState.hh"

namespace gem5
{
namespace ruby
{
namespace garnet
{

TEST(GarnetVcIsolationTest, ExhaustsOnlySelectedVc)
{
    std::vector<OutVcState> vcs;
    for (int vc = 0; vc < 4; ++vc)
        vcs.emplace_back(vc, 0, 2);

    vcs[1].decrement_credit();
    vcs[1].decrement_credit();

    EXPECT_FALSE(vcs[1].has_credit());
    EXPECT_EQ(vcs[1].get_credit_count(), 0);
    for (int vc : {0, 2, 3}) {
        EXPECT_TRUE(vcs[vc].has_credit());
        EXPECT_EQ(vcs[vc].get_credit_count(), 2);
        EXPECT_EQ(vcs[vc].get_max_credit_count(), 2);
    }
}

TEST(GarnetVcIsolationTest, PreservesOtherVcs)
{
    std::vector<OutVcState> vcs;
    for (int vc = 0; vc < 4; ++vc)
        vcs.emplace_back(vc, 2, 3);

    for (int i = 0; i < 3; ++i)
        vcs[2].decrement_credit();

    // A different VC remains independently usable while VC 2 is empty.
    vcs[0].decrement_credit();
    EXPECT_EQ(vcs[0].get_credit_count(), 2);
    vcs[0].increment_credit();

    EXPECT_EQ(vcs[0].get_credit_count(), 3);
    EXPECT_EQ(vcs[1].get_credit_count(), 3);
    EXPECT_EQ(vcs[2].get_credit_count(), 0);
    EXPECT_EQ(vcs[3].get_credit_count(), 3);
}

} // namespace garnet
} // namespace ruby
} // namespace gem5

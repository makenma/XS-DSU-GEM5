#include <gtest/gtest.h>

#include <type_traits>
#include <utility>

#include "mem/cache/CHI/SlcSnoopFilter.hh"

namespace gem5::Chi
{
namespace
{

TEST(SlcSnoopFilterTest, IsStandaloneClockedNoncopyableOwner)
{
    static_assert(std::is_base_of_v<ClockedObject, SlcSnoopFilter>);
    static_assert(!std::is_copy_constructible_v<SlcSnoopFilter>);
    static_assert(!std::is_copy_assignable_v<SlcSnoopFilter>);
    static_assert(std::is_same_v<
                  decltype(std::declval<SlcSnoopFilter&>().service()),
                  HnfSLCSF&>);
    static_assert(std::is_same_v<
                  decltype(std::declval<const SlcSnoopFilter&>().service()),
                  const HnfSLCSF&>);
    SUCCEED();
}

} // namespace
} // namespace gem5::Chi

#include <gtest/gtest.h>

#include "dev/ai_mesh/tensor_sram.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TensorSram
makeQueueSram(uint32_t read_ports, uint32_t queue_depth)
{
    return TensorSram(1 << 20, 16, 64, 32, 32, 32,
                      read_ports, 1, 500, queue_depth);
}

TEST(TensorSramQueueTest, OnePortDepthTwoRejectsThirdRequest)
{
    auto sram = makeQueueSram(1, 2);
    ASSERT_TRUE(sram.canReserve(1000, 0x0000, 32, false));
    sram.reserve(1000, 0x0000, 32, false);
    ASSERT_TRUE(sram.canReserve(1000, 0x0200, 32, false));
    sram.reserve(1000, 0x0200, 32, false);
    EXPECT_FALSE(sram.canReserve(1000, 0x0400, 32, false));
}

TEST(TensorSramQueueTest, ReadAndWriteQueueCapacityIsIndependent)
{
    auto sram = makeQueueSram(1, 1);
    sram.reserve(1000, 0x0000, 32, false);
    EXPECT_FALSE(sram.canReserve(1000, 0x0200, 32, false));
    EXPECT_TRUE(sram.canReserve(1000, 0x0000, 32, true));
}

}
}
}

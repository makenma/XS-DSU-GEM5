#include <array>
#include <gtest/gtest.h>
#include "mem/cache/cache_blk.hh"
#include "mem/cache/CHI/ChiNativeRequest.hh"
#include "sim/cur_tick.hh"

namespace gem5::Chi {
namespace {
class ChiNativeRequest : public ::testing::Test
{
    Tick tick = 0;
    Tick *previous = nullptr;
    void SetUp() override
    {
        // Request timestamps use the simulation clock, but this standalone
        // packet test intentionally has no EventQueue or simulated CPU.
        previous = Gem5Internal::_curTickPtr;
        Gem5Internal::_curTickPtr = &tick;
    }
    void TearDown() override { Gem5Internal::_curTickPtr = previous; }
};

RequestPtr request()
{
    auto r = std::make_shared<Request>(0x1000, 64, 0, Request::funcRequestorId);
    r->setContext(0);
    return r;
}

TEST_F(ChiNativeRequest, InvalidatedOrdinaryUpgradeReturnsData)
{
    Packet packet(request(), MemCmd::UpgradeReq);
    promoteInvalidatedUpgrade(&packet);
    ASSERT_EQ(packet.cmd, MemCmd::ReadExReq);
    auto* buffer = packet.getPtr<uint8_t>();
    promoteInvalidatedUpgrade(&packet);
    EXPECT_EQ(packet.getPtr<uint8_t>(), buffer);
    packet.makeTimingResponse();
    EXPECT_EQ(packet.cmd, MemCmd::ReadExResp);
    EXPECT_TRUE(packet.hasData());
}

TEST_F(ChiNativeRequest, InvalidatedStoreConditionalCannotBecomeSuccessfulWrite)
{
    // This is the lower packet used by classic L1 for a store-conditional
    // whose readable line needs writable permission. A preceding invalidation
    // must preserve exactly the failure response recognized by Cache::serviceMSHRTargets.
    Packet upgrade(request(), MemCmd::SCUpgradeReq);
    promoteInvalidatedUpgrade(&upgrade);
    ASSERT_EQ(upgrade.cmd, MemCmd::SCUpgradeFailReq);
    ASSERT_TRUE(upgrade.isLLSC());
    auto* buffer = upgrade.getPtr<uint8_t>();
    promoteInvalidatedUpgrade(&upgrade);
    EXPECT_EQ(upgrade.getPtr<uint8_t>(), buffer);
    upgrade.makeTimingResponse();
    ASSERT_EQ(upgrade.cmd, MemCmd::UpgradeFailResp);
    EXPECT_TRUE(upgrade.hasData());

    // Exercise the real classic reservation gate as well: invalidation clears
    // the LR, so even a returned fill must never let the old SC modify bytes.
    CacheBlk block;
    std::array<uint8_t, 64> memory{};
    memory.fill(0x5a);
    block.data = memory.data();
    Packet load(request(), MemCmd::LoadLockedReq);
    block.trackLoadLocked(&load);
    block.invalidate();
    Packet store(load.req, MemCmd::StoreCondReq);
    store.req->setExtraData(1); // reject stale success, too
    if (block.checkWrite(&store))
        memory.fill(0xa5);
    EXPECT_EQ(store.req->getExtraData(), 0);
    for (auto byte : memory)
        EXPECT_EQ(byte, 0x5a);
}

TEST_F(ChiNativeRequest, UncontendedStoreConditionalStillSucceeds)
{
    CacheBlk block;
    Packet load(request(), MemCmd::LoadLockedReq);
    block.trackLoadLocked(&load);
    Packet store(load.req, MemCmd::StoreCondReq);
    EXPECT_TRUE(block.checkWrite(&store));
    EXPECT_EQ(store.req->getExtraData(), 1);
}
} // anonymous namespace
} // namespace gem5::Chi

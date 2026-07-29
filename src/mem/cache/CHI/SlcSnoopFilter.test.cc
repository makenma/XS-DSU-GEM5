#include <gtest/gtest.h>

#include <cstdint>
#include <type_traits>
#include <utility>
#include <vector>

#include "mem/cache/CHI/SlcSnoopFilter.hh"

namespace gem5::Chi
{
namespace
{

constexpr uint64_t TestAddr = 0x80004000;

SlcSfReqHeader
requestHeader(uint64_t req_id)
{
    SlcSfReqHeader header{};
    header.reqId = SlcSfReqId{req_id};
    header.pocEntryId = req_id;
    header.lineAddress = TestAddr;
    header.requester = 7;
    return header;
}

SlcSfRequest
lookupRequest(uint64_t req_id)
{
    return makeSlcSfLookupReq(
        requestHeader(req_id), PocqTxnKind::ReadShared);
}

std::vector<uint8_t>
lineData(uint8_t value)
{
    return std::vector<uint8_t>(64, value);
}

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

TEST(SlcSnoopFilterTest, CrossClockEdgesNotifyOnlyFutureOwnerWakeups)
{
    constexpr Tick AcceptedTick = 100;
    constexpr Tick ChildPeriod = 3;
    constexpr Tick OwnerPeriod = 7;
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 1;
    config.lookupLatency = 2;
    config.childClockPeriod = ChildPeriod;
    HnfSLCSF service(64, 4, 2, 4, 2, 8, config);
    auto request = lookupRequest(1);

    ASSERT_EQ(service.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(service.registeredReqCredits(), 0);

    auto edge = advanceSlcSnoopFilterService(service, 102);
    EXPECT_TRUE(edge.needsNextEdge);
    EXPECT_FALSE(edge.responseBecameVisible);
    EXPECT_FALSE(edge.creditBecameAvailable);
    EXPECT_EQ(service.reqInflightCount(), 1);

    edge = advanceSlcSnoopFilterService(service, 105);
    EXPECT_TRUE(edge.needsNextEdge);
    EXPECT_EQ(service.respPendingCount(), 0);

    edge = advanceSlcSnoopFilterService(service, 108);
    EXPECT_TRUE(edge.needsNextEdge);
    EXPECT_TRUE(edge.creditBecameAvailable);
    EXPECT_FALSE(edge.responseBecameVisible);
    EXPECT_EQ(service.respPendingCount(), 1);
    EXPECT_EQ(service.respVisibleCount(), 0);

    edge = advanceSlcSnoopFilterService(service, 111);
    EXPECT_FALSE(edge.needsNextEdge);
    EXPECT_FALSE(edge.creditBecameAvailable);
    EXPECT_TRUE(edge.responseBecameVisible);
    ASSERT_EQ(service.respVisibleCount(), 1);

    const Tick visible_tick = 111;
    const Tick earliest_notification = visible_tick + 1;
    const Tick owner_wakeup =
        divCeil(earliest_notification, OwnerPeriod) * OwnerPeriod;
    EXPECT_GT(visible_tick, AcceptedTick);
    EXPECT_EQ(owner_wakeup, 112);
    EXPECT_GT(owner_wakeup, visible_tick);

    auto response = service.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->reqId(), SlcSfReqId{1});
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
}

TEST(SlcSnoopFilterTest, CrossClockReplayUsesAbsoluteChildTick)
{
    constexpr Tick ChildPeriod = 3;
    HnfSLCSFPipelineConfig config{};
    config.lookupLatency = 1;
    config.updateLatency = 3;
    config.replayPenalty = 2;
    config.childClockPeriod = ChildPeriod;
    HnfSLCSF service(64, 1, 1, 1, 1, 8, config);

    auto lookup = lookupRequest(2);
    ASSERT_EQ(service.tryEnqueue(std::move(lookup)),
              SlcSfEnqueueResult::Accepted);
    advanceSlcSnoopFilterService(service, 201);
    advanceSlcSnoopFilterService(service, 204);
    auto edge = advanceSlcSnoopFilterService(service, 207);
    ASSERT_TRUE(edge.responseBecameVisible);
    auto lookup_response = service.popVisibleResponse();
    ASSERT_TRUE(lookup_response.has_value());
    const auto token = std::get<SlcSfLookupResponse>(
        lookup_response->payload()).token;

    service.writeLine(
        TestAddr, 7, lineData(0xa1), PocqTxnKind::WriteUnique);
    auto stale = makeSlcSfFillCleanSharedReq(
        requestHeader(3), lineData(0xb2), {}, token, token.lookupReqId);
    ASSERT_EQ(service.tryEnqueue(std::move(stale)),
              SlcSfEnqueueResult::Accepted);

    advanceSlcSnoopFilterService(service, 210);
    edge = advanceSlcSnoopFilterService(service, 213);
    ASSERT_EQ(service.respPendingCount(), 1);
    EXPECT_TRUE(edge.needsNextEdge);
    edge = advanceSlcSnoopFilterService(service, 216);
    ASSERT_TRUE(edge.responseBecameVisible);
    auto replay_response = service.popVisibleResponse();
    ASSERT_TRUE(replay_response.has_value());
    EXPECT_EQ(replay_response->status(), SlcSfTerminalStatus::Replay);
    const auto& replay =
        std::get<SlcSfReplay>(replay_response->payload());
    EXPECT_EQ(replay.reason, SlcSfReplayReason::StaleCommitToken);
    EXPECT_EQ(replay.retryNotBeforeTick, 219);
    EXPECT_GT(replay.retryNotBeforeTick, 213);
}

} // namespace
} // namespace gem5::Chi

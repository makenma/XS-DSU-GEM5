#include <gtest/gtest.h>

#include <cstdint>
#include <set>
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

TEST(SlcSnoopFilterTest, NoCreditsBeforeInitialization)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 2;
    config.initLatency = 3;
    HnfSLCSF service(64, 4, 2, 4, 2, 8, config);
    service.resetForColdStart();
    auto request = lookupRequest(41);

    EXPECT_FALSE(service.isInitialized());
    EXPECT_EQ(service.registeredReqCredits(), 0);
    EXPECT_EQ(service.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Initializing);
    EXPECT_EQ(std::get<SlcSfLookupReq>(request).header.reqId,
              SlcSfReqId{41});
    EXPECT_EQ(service.reqOutstanding(), 0);
    EXPECT_EQ(service.respOccupied(), 0);
}

TEST(SlcSnoopFilterTest, InitializationCompletesAfterConfiguredLatency)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 2;
    config.initLatency = 3;
    HnfSLCSF service(64, 4, 2, 4, 2, 8, config);
    service.resetForColdStart();

    auto edge = advanceSlcSnoopFilterService(service, 10);
    EXPECT_TRUE(edge.needsNextEdge);
    EXPECT_FALSE(edge.creditBecameAvailable);
    EXPECT_FALSE(service.isInitialized());
    edge = advanceSlcSnoopFilterService(service, 13);
    EXPECT_TRUE(edge.needsNextEdge);
    EXPECT_FALSE(edge.creditBecameAvailable);
    EXPECT_FALSE(service.isInitialized());
    edge = advanceSlcSnoopFilterService(service, 16);
    EXPECT_FALSE(edge.needsNextEdge);
    EXPECT_TRUE(edge.creditBecameAvailable);
    EXPECT_TRUE(service.isInitialized());
    EXPECT_EQ(service.registeredReqCredits(), 2);
    EXPECT_EQ(service.respOccupied(), 0);
}

TEST(SlcSnoopFilterTest, IdleFilterDoesNotSelfWakeForever)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 2;
    config.initLatency = 3;
    HnfSLCSF service(64, 4, 2, 4, 2, 8, config);
    service.resetForColdStart();

    ASSERT_EQ(service.calculateNextWakeupCycle(), 3);
    service.wakeup(30, 3);

    EXPECT_TRUE(service.isInitialized());
    EXPECT_EQ(service.registeredReqCredits(), 2);
    EXPECT_FALSE(service.calculateNextWakeupCycle().has_value());
    EXPECT_FALSE(service.needsServiceWakeup());
}

TEST(SlcSnoopFilterTest, NewEarlierWorkReschedulesWakeup)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 2;
    config.lookupLatency = 8;
    HnfSLCSF service(64, 4, 2, 4, 2, 8, config);
    auto first = lookupRequest(51);

    ASSERT_EQ(service.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    service.wakeup(10);
    ASSERT_EQ(service.currentCycle(), 1);
    ASSERT_EQ(service.calculateNextWakeupCycle(), 9);

    auto second = lookupRequest(52);
    ASSERT_EQ(service.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(service.calculateNextWakeupCycle(), 2);

    const Tick earlier_tick = slcSnoopFilterWakeupTick(
        103, 102, 105, 3, 1);
    EXPECT_EQ(earlier_tick, 105);
    const auto earlier = slcSnoopFilterScheduleDecision(126, earlier_tick);
    EXPECT_FALSE(earlier.schedule);
    EXPECT_TRUE(earlier.reschedule);
    EXPECT_EQ(earlier.when, 105);

    const auto duplicate = slcSnoopFilterScheduleDecision(105, 105);
    EXPECT_FALSE(duplicate.schedule);
    EXPECT_FALSE(duplicate.reschedule);
}

TEST(SlcSnoopFilterTest, CompletionPrecedesNewIssue)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 2;
    config.lookupLatency = 1;
    HnfSLCSF service(64, 4, 2, 4, 2, 8, config);
    auto first = lookupRequest(61);

    ASSERT_EQ(service.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    service.wakeup(10);
    auto second = lookupRequest(62);
    ASSERT_EQ(service.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);

    service.wakeup(20);
    EXPECT_EQ(service.respPendingCount(), 1);
    EXPECT_EQ(service.reqInflightCount(), 1);
    EXPECT_EQ(service.finishedRequestCount(), 1);

    service.wakeup(30);
    EXPECT_EQ(service.respVisibleCount(), 1);
    EXPECT_EQ(service.respPendingCount(), 1);
    EXPECT_EQ(service.finishedRequestCount(), 2);
}

TEST(SlcSnoopFilterTest, ColdResetClearsPersistentAndTransientStorage)
{
    HnfSLCSF service(64, 1, 2, 1, 1, 2);
    service.fillCleanShared(TestAddr, 7, lineData(0xa1));
    service.fillCleanShared(TestAddr + 64, 8, lineData(0xb2));
    ASSERT_EQ(service.seqOccupancy(), 1);

    service.resetForColdStart();

    HnfSlcLookupReq lookup{};
    lookup.blockAddr = TestAddr;
    lookup.req.srcid = 7;
    lookup.txn = PocqTxnKind::ReadShared;
    const HnfSlcLookupResult result = service.lookup(lookup);
    EXPECT_FALSE(result.slcHit);
    EXPECT_FALSE(result.sfHit);
    EXPECT_EQ(service.seqOccupancy(), 0);
    EXPECT_EQ(service.seqReservationCount(), 0);
    EXPECT_EQ(service.sfReservationCount(), 0);
    EXPECT_EQ(service.victimBufferOccupancy(), 0);
    EXPECT_EQ(service.victimReservationCount(), 0);
    EXPECT_EQ(service.dirtyVictimSealCount(), 0);
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

TEST(SlcSnoopFilterTest, DrainWaitsForAllAcceptedWork)
{
    enum class StartingState
    {
        Ingress,
        Ready,
        Inflight,
        RespPending,
        RespVisible
    };

    for (const StartingState state : {
             StartingState::Ingress, StartingState::Ready,
             StartingState::Inflight, StartingState::RespPending,
             StartingState::RespVisible}) {
        HnfSLCSFPipelineConfig config{};
        config.reqQueueEntries = 2;
        config.respQueueEntries = 1;
        config.maxInflight = 1;
        config.lookupLatency = 1;
        HnfSLCSF service(64, 4, 2, 4, 2, 8, config);
        uint64_t next_id = 100;
        size_t accepted = 0;
        std::set<uint64_t> completed;

        const auto enqueue = [&] {
            auto request = lookupRequest(next_id++);
            ASSERT_EQ(service.tryEnqueue(std::move(request)),
                      SlcSfEnqueueResult::Accepted);
            ++accepted;
        };
        const auto consume = [&] {
            while (auto response = service.popVisibleResponse()) {
                EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
                completed.insert(response->reqId().value);
            }
        };

        enqueue();
        if (state != StartingState::Ingress) {
            service.wakeup(10);
        }
        if (state == StartingState::Ready) {
            service.wakeup(20);
            service.wakeup(30);
            ASSERT_EQ(service.respVisibleCount(), 1);
            enqueue();
            service.wakeup(40);
            ASSERT_EQ(service.reqReadyCount(), 1);
        } else if (state == StartingState::Inflight) {
            ASSERT_EQ(service.reqInflightCount(), 1);
        } else if (state == StartingState::RespPending) {
            service.wakeup(20);
            ASSERT_EQ(service.respPendingCount(), 1);
        } else if (state == StartingState::RespVisible) {
            service.wakeup(20);
            service.wakeup(30);
            ASSERT_EQ(service.respVisibleCount(), 1);
        }

        service.requestDrain();
        auto d1_request = lookupRequest(next_id++);
        const SlcSfEnqueueResult d1_result =
            service.tryEnqueue(std::move(d1_request));
        EXPECT_EQ(d1_result, SlcSfEnqueueResult::Accepted);
        if (d1_result == SlcSfEnqueueResult::Accepted) {
            ++accepted;
        }
        service.beginDraining();
        auto rejected = lookupRequest(next_id++);
        EXPECT_EQ(service.tryEnqueue(std::move(rejected)),
                  SlcSfEnqueueResult::Draining);
        EXPECT_FALSE(service.isCompletelyIdle());

        Tick tick = 50;
        for (size_t edge = 0;
             edge < 32 && (!service.isCompletelyIdle() ||
                            completed.size() != accepted);
             ++edge) {
            consume();
            if (service.needsServiceWakeup()) {
                service.wakeup(tick += 10);
            }
        }
        consume();
        EXPECT_EQ(completed.size(), accepted);
        EXPECT_EQ(service.finishedRequestCount(), accepted);
        EXPECT_EQ(service.reqOutstanding(), 0);
        EXPECT_EQ(service.respOccupied(), 0);
        EXPECT_TRUE(service.isCompletelyIdle());
    }
}

TEST(SlcSnoopFilterTest, DrainResumeRestoresAdmission)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 2;
    HnfSLCSF service(64, 4, 2, 4, 2, 8, config);
    service.beginDraining();
    EXPECT_EQ(service.registeredReqCredits(), 0);

    auto rejected = lookupRequest(201);
    EXPECT_EQ(service.tryEnqueue(std::move(rejected)),
              SlcSfEnqueueResult::Draining);
    service.resumeFromDrain();

    EXPECT_FALSE(service.isDrainRequested());
    EXPECT_FALSE(service.isAdmissionSealed());
    EXPECT_EQ(service.registeredReqCredits(), 2);
    EXPECT_EQ(service.tryEnqueue(std::move(rejected)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_TRUE(service.needsServiceWakeup());
}

} // namespace
} // namespace gem5::Chi

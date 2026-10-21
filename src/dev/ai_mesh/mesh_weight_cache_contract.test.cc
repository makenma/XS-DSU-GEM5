#include "dev/ai_mesh/mesh_binary_test_support.hh"
using namespace gem5::ai_mesh::test_support;
namespace gem5::ai_mesh
{
namespace
{
using namespace mesh_abi;
TEST(WeightCacheIdentityTest, MatchesTheCrossLanguageKeyGolden)
{
    using namespace mesh_abi;
    WeightFillKey fill;
    fill.core_id = 3;
    fill.cache_partition_id = 1;
    fill.weight_tag_index = 7;
    fill.cache_generation = 2;
    fill.fill_incarnation = 1;
    const auto fill_wire = weightFillKeyBytes(fill);
    EXPECT_EQ(fill_wire.size(), size_t(kWeightFillKeysBytes));
    const std::array<uint8_t, 16> expected_fill = {
        0x03, 0x00, 0x01, 0x00, 0x07, 0x00, 0x00, 0x00,
        0x02, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00};
    EXPECT_TRUE(std::equal(fill_wire.begin(), fill_wire.end(),
                           expected_fill.begin()));
    EXPECT_EQ(weightFillTrafficId(fill),
              "35f9e3feefe6ec9f42bf46be4e1d644c"
              "725f00f3f9d6658ab64a0fd5c4a0fc4d");

    const auto descriptor_wire = cacheDmaDescriptorKeyBytes(fill, 3);
    EXPECT_EQ(descriptor_wire.size(),
              size_t(kCacheDmaDescriptorKeysBytes));
    EXPECT_EQ(descriptor_wire[16], 3);
    EXPECT_EQ(descriptor_wire[17], 0);

    const ErrorSourceKey source = weightFillSource(fill);
    const auto source_wire = errorSourceKeyBytes(source);
    EXPECT_EQ(source_wire.size(), size_t(kErrorSourceKeysBytes));
    EXPECT_EQ(source_wire[kErrorSourceKeysErrorClassOffset],
              uint8_t(kMoeErrorClassWEIGHT_FILL));
    EXPECT_EQ(source_wire[kErrorSourceKeysDomainOffset],
              uint8_t(kMeshObjectDomainWEIGHT_FILL));
    EXPECT_EQ(source_wire[kErrorSourceKeysObjectKindOffset],
              uint8_t(kErrorSourceObjectKindWEIGHT_FILL_OBLIGATION));
    EXPECT_TRUE(std::equal(fill_wire.begin(), fill_wire.end(),
                           source_wire.begin() +
                               kErrorSourceKeysAuxKeyOffset));
}

TEST(WeightCacheIdentityTest, OrdersNumericallyInsteadOfByWireBytes)
{
    using namespace mesh_abi;
    WeightCacheBaseKey low;
    low.core_id = 0x00FF;
    low.cache_partition_id = 1;
    WeightCacheBaseKey high;
    high.core_id = 0x0100;
    high.cache_partition_id = 1;
    EXPECT_TRUE(weightCacheBaseKeyLess(low, high));
    WeightFillKey low_fill;
    low_fill.core_id = 0x00FF;
    low_fill.cache_partition_id = 1;
    WeightFillKey high_fill;
    high_fill.core_id = 0x0100;
    high_fill.cache_partition_id = 1;
    EXPECT_TRUE(weightFillKeyLess(low_fill, high_fill));
    WeightFillKey next = low_fill;
    next.fill_incarnation = 1;
    EXPECT_TRUE(weightFillKeyLess(low_fill, next));
}

TEST(WeightCacheCoordinatorTest, MatchesTheCrossLanguageTransitionGolden)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 3;
    config.slot_bytes = 4096;
    config.mshr_slots = 3;
    config.eviction_slots = 3;
    config.obligation_slots = 3;
    config.subscriber_slots = 6;
    config.cacheable_tags = {1, 2, 3, 4};
    MoeWeightCache cache(config);

    std::vector<MoeCacheToken> cold;
    ASSERT_EQ(cache.reserve(1, 1, {1, 2}, 10, cold),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(cold.size(), 2u);
    EXPECT_EQ(cold[0].outcome, kCacheResidencyOutcomeNEW_FILL);
    EXPECT_EQ(cold[0].slot_id, 0u);
    EXPECT_EQ(cold[0].fill.fill_incarnation, 1u);
    EXPECT_EQ(cold[1].slot_id, 1u);
    EXPECT_EQ(cold[1].fill.fill_incarnation, 2u);
    for (const auto &token : cold) {
        cache.markFilling(token.fill);
        cache.noteFillSuccess(token.fill, 4096, 0);
    }
    std::vector<MoeCacheToken> warm;
    ASSERT_EQ(cache.reserve(2, 1, {1}, 20, warm),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(warm.size(), 1u);
    EXPECT_EQ(warm[0].outcome, kCacheResidencyOutcomeHIT);
    EXPECT_EQ(warm[0].slot_id, 0u);
    EXPECT_FALSE(warm[0].has_fill);

    std::vector<MoeCacheToken> mixed;
    ASSERT_EQ(cache.reserve(3, 1, {2, 3}, 30, mixed),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(mixed.size(), 2u);
    EXPECT_EQ(mixed[0].outcome, kCacheResidencyOutcomeHIT);
    EXPECT_EQ(mixed[1].outcome, kCacheResidencyOutcomeNEW_FILL);
    EXPECT_EQ(mixed[1].slot_id, 2u);
    EXPECT_EQ(mixed[1].fill.fill_incarnation, 3u);
    cache.markFilling(mixed[1].fill);
    cache.noteFillSuccess(mixed[1].fill, 4096, 0);
    for (const auto &token : cold)
        cache.releaseToken(token.token_id);
    for (const auto &token : warm)
        cache.releaseToken(token.token_id);
    for (const auto &token : mixed)
        cache.releaseToken(token.token_id);

    std::vector<MoeCacheToken> evict;
    ASSERT_EQ(cache.reserve(4, 1, {4}, 40, evict),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(evict.size(), 1u);
    EXPECT_EQ(evict[0].slot_id, 0u);
    EXPECT_EQ(evict[0].fill.fill_incarnation, 4u);
    EXPECT_EQ(cache.evictionFree(), 2u);
    cache.markFilling(evict[0].fill);
    const ErrorSourceKey source = weightFillSource(evict[0].fill);
    cache.noteFillFailure(evict[0].fill,
                          kWeightFillFailureSiteCACHE_FILL_AXI_R, 50,
                          source, true);
    cache.releaseToken(evict[0].token_id);
    EXPECT_EQ(cache.evictionFree(), 3u);
    std::vector<MoeCacheToken> blocked;
    EXPECT_EQ(cache.reserve(5, 1, {4}, 60, blocked),
              MoeWeightCache::ReserveStatus::FAILED);
    EXPECT_TRUE(blocked.empty());
    EXPECT_EQ(cache.nextFillIncarnation(), 5u);
    EXPECT_EQ(cache.nextUseEpoch(), 6u);
    ASSERT_EQ(cache.slots().size(), 3u);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotFree);
    EXPECT_EQ(cache.slots()[1].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[1].weight_tag_index, 2u);
    EXPECT_EQ(cache.slots()[2].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[2].weight_tag_index, 3u);
    EXPECT_EQ(cache.obligations().size(), 0u);
    const std::string snapshot = cache.snapshotJson();
    EXPECT_NE(snapshot.find("\"next_fill_incarnation\":5"), std::string::npos);
    EXPECT_NE(snapshot.find("\"weight_tag_index\":4"), std::string::npos);
}

TEST(WeightCacheIdentityTest, FailureSitesMapToTheFrozenDetailClosure){
    using namespace mesh_abi;
    EXPECT_EQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_AXI_R),
                 agent_abi::E_AXI_RESPONSE);
    EXPECT_EQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_SRAM_BOUNDS),
                 agent_abi::E_DCORE_SRAM_BOUNDS);
    EXPECT_EQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_SRAM_COMMIT),
                 agent_abi::E_DCORE_ENGINE);
    EXPECT_EQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_SOURCE_VALIDITY),
                 agent_abi::E_DCORE_POISON_READ);
    EXPECT_EQ(weightFillFailureDetail(4), std::nullopt);
}

TEST(WeightCacheCoordinatorTest, ErrorWinsOverALateSuccessAndRetiresOnce)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(3, 1, {1, 2}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(tokens.size(), 2u);
    const auto failing = tokens[0].fill;
    cache.advanceCacheEdge();
    cache.advanceEngineEdge();
    cache.markIssued(failing);
    cache.markInFlight(failing);
    cache.noteFillFailure(failing, kWeightFillFailureSiteCACHE_FILL_AXI_R,
                          30, weightFillSource(failing), true);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotErrorHeld);
    EXPECT_EQ(cache.slots()[0].valid_bytes, 0u);
    EXPECT_EQ(cache.mshrFree(), 0u);
    cache.noteFillSuccess(failing, 4096, 31);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotErrorHeld);
    EXPECT_EQ(cache.slots()[0].valid_bytes, 0u);
    EXPECT_EQ(cache.mshrFree(), 0u);
    EXPECT_EQ(cache.obligations().at(1).drained_bytes, 4096u);
    cache.noteFillSuccess(tokens[1].fill, 4096, 32);
    EXPECT_EQ(cache.slots()[1].state, kCacheSlotValid);
    for (const auto &token : tokens)
        cache.releaseToken(token.token_id);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotFree);
    EXPECT_EQ(cache.slots()[1].state, kCacheSlotValid);
    EXPECT_EQ(cache.mshrFree(), config.mshr_slots);
    EXPECT_EQ(cache.evictionFree(), config.eviction_slots);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
    EXPECT_TRUE(cache.hasTombstone(cache.baseKey(1)));
    EXPECT_EQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_AXI_R),
                 agent_abi::E_AXI_RESPONSE);
    std::vector<MoeCacheToken> retry;
    EXPECT_EQ(cache.reserve(4, 1, {1}, 40, retry),
              MoeWeightCache::ReserveStatus::FAILED);
    EXPECT_EQ(cache.nextFillIncarnation(), 3u);
}

TEST(WeightCacheCoordinatorTest, ReleasesAResourceExactlyOncePerFill)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(5, 1, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(tokens.size(), 1u);
    const auto fill = tokens[0].fill;
    cache.markFilling(fill);
    cache.abortBeforeStart(5);
    cache.noteFailureFanoutDone(5);
    EXPECT_EQ(cache.mshrFree(), 1u);
    EXPECT_EQ(cache.obligationFree(), 1u);
    EXPECT_EQ(cache.subscriberFree(), 3u);
    cache.noteFillSuccess(fill, 4096, 20);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[0].valid_bytes, 4096u);
    EXPECT_EQ(cache.mshrFree(), config.mshr_slots);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
    EXPECT_EQ(cache.obligations().size(), 0u);
    EXPECT_EQ(cache.fillLog().size(), 1u);
    EXPECT_EQ(cache.fillLog()[0].bytes, 4096u);
}

TEST(WeightCacheFaultJoinTest, ReleasesOnlyAfterFillDrainAndFanoutMeet)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(tokens.size(), 1u);
    const MoeCacheToken token = tokens[0];
    cache.setTokenConsumers(token.token_id, 0);
    cache.markFilling(token.fill);
    cache.noteInstanceFault(1, 12);
    EXPECT_EQ(cache.subscribersInState(
                  kCacheSubscriberStateTERMINAL_TOMBSTONED), 1u);
    EXPECT_EQ(cache.liveTokens(), 1u);
    cache.noteOwnedWorkDrained(1);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.tokenReleased(token.token_id), false);
    cache.noteFailureFanoutDone(1);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.tokenReleased(token.token_id), false);
    cache.noteFillSuccess(token.fill, 4096, 20);
    EXPECT_TRUE(cache.tokenReleased(token.token_id));
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.tokenReleases(), cache.tokensCreated());
    EXPECT_EQ(cache.wokenSubscribers(), 0u);
    EXPECT_EQ(cache.faultedFillTerminals(), 1u);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[0].pins, 0u);
    EXPECT_EQ(cache.mshrFree(), config.mshr_slots);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
    EXPECT_EQ(cache.obligations().size(), 0u);
}

TEST(WeightCacheFaultJoinTest, FillTerminalFirstStillWaitsForTheDrain)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 2, {2}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    const MoeCacheToken token = tokens[0];
    cache.setTokenConsumers(token.token_id, 0);
    cache.markFilling(token.fill);
    cache.noteInstanceFault(1, 12);
    cache.noteFailureFanoutDone(1);
    cache.noteFillSuccess(token.fill, 4096, 14);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.tokenReleased(token.token_id), false);
    cache.noteOwnedWorkDrained(1);
    EXPECT_TRUE(cache.tokenReleased(token.token_id));
    EXPECT_EQ(cache.tokenReleases(), cache.tokensCreated());
    EXPECT_EQ(cache.slots()[token.slot_id].state, kCacheSlotValid);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
}

TEST(WeightCacheFaultJoinTest, FaultedSubscribersAreNeverWoken)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    cache.setTokenConsumers(tokens[0].token_id, 0);
    cache.markFilling(tokens[0].fill);
    cache.noteInstanceFault(1, 11);
    cache.noteFailureFanoutDone(1);
    cache.noteOwnedWorkDrained(1);
    EXPECT_EQ(cache.hasPendingSubscribers(), false);
    cache.noteFillSuccess(tokens[0].fill, 4096, 12);
    EXPECT_EQ(cache.wokenSubscribers(), 0u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateWOKEN), 0u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateWAITING), 0u);
}

TEST(WeightCacheOwnershipTest, HoldsThePinUntilTheLastConsumerDrains)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(7, 4, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    const MoeCacheToken token = tokens[0];
    cache.setTokenConsumers(token.token_id, 2);
    cache.markFilling(token.fill);
    cache.noteFillSuccess(token.fill, 4096, 20);
    cache.noteConsumerDrained(7, 4, 1);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.tokenReleased(token.token_id), false);
    cache.noteConsumerDrained(7, 4, 1);
    EXPECT_TRUE(cache.tokenReleased(token.token_id));
    cache.noteConsumerDrained(7, 4, 1);
    EXPECT_EQ(cache.tokenReleases(), cache.tokensCreated());
    EXPECT_EQ(cache.slots()[0].pins, 0u);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
}

TEST(WeightCacheReservationTest, PrepareLeavesNoVisibleStateBehind)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 1;
    config.slot_bytes = 4096;
    config.mshr_slots = 1;
    config.eviction_slots = 0;
    config.obligation_slots = 1;
    config.subscriber_slots = 2;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cold(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cold.reserve(1, 1, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    cold.markFilling(tokens[0].fill);
    ASSERT_EQ(cold.slots()[0].state, kCacheSlotFilling);
    const uint32_t pins = cold.slots()[0].pins;
    const uint32_t incarnation = cold.nextFillIncarnation();
    const MoeWeightCache::ReservePlan blocked =
        cold.prepare(2, 1, {2}, 20);
    EXPECT_EQ(blocked.status, MoeWeightCache::ReserveStatus::RESOURCE_WAIT);
    EXPECT_EQ(cold.liveTokens(), 1u);
    EXPECT_EQ(cold.slots()[0].state, kCacheSlotFilling);
    EXPECT_EQ(cold.slots()[0].pins, pins);
    EXPECT_EQ(cold.nextFillIncarnation(), incarnation);
    EXPECT_EQ(cold.mshrFree(), 0u);
    EXPECT_EQ(cold.obligations().size(), 1u);
    const MoeWeightCache::ReservePlan attach = cold.prepare(2, 1, {1}, 20);
    ASSERT_EQ(attach.status, MoeWeightCache::ReserveStatus::COMMITTED);
    EXPECT_EQ(attach.attached.size(), 1u);
    EXPECT_EQ(cold.liveTokens(), 1u);
    EXPECT_EQ(cold.slots()[0].pins, pins);
    cold.noteFillSuccess(tokens[0].fill, 4096, 21);
    const uint32_t epoch = cold.nextUseEpoch();
    const MoeWeightCache::ReservePlan warm = cold.prepare(3, 1, {1}, 22);
    ASSERT_EQ(warm.status, MoeWeightCache::ReserveStatus::COMMITTED);
    EXPECT_EQ(warm.hits.size(), 1u);
    EXPECT_EQ(cold.slots()[0].pins, pins);
    EXPECT_EQ(cold.nextUseEpoch(), epoch);
    EXPECT_EQ(cold.liveTokens(), 1u);
    const std::vector<MoeCacheToken> committed = cold.commit(warm, 22);
    ASSERT_EQ(committed.size(), 1u);
    EXPECT_EQ(committed[0].outcome, kCacheResidencyOutcomeHIT);
    EXPECT_EQ(cold.slots()[0].pins, pins + 1);
    EXPECT_EQ(cold.liveTokens(), 2u);
}

TEST(WeightCacheOwnershipTest, TwoBatchesShareATagWithoutCrossRelease)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> first;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, first),
              MoeWeightCache::ReserveStatus::COMMITTED);
    cache.setTokenConsumers(first[0].token_id, 1);
    cache.markFilling(first[0].fill);
    cache.noteFillSuccess(first[0].fill, 4096, 20);
    std::vector<MoeCacheToken> second;
    ASSERT_EQ(cache.reserve(2, 1, {1}, 21, second),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(second[0].outcome, kCacheResidencyOutcomeHIT);
    cache.setTokenConsumers(second[0].token_id, 1);
    ASSERT_EQ(cache.liveTokens(), 2u);
    cache.noteConsumerDrained(1, 1, 1);
    EXPECT_TRUE(cache.tokenReleased(first[0].token_id));
    EXPECT_FALSE(cache.tokenReleased(second[0].token_id));
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.slots()[first[0].slot_id].pins, 1u);
    cache.noteConsumerDrained(2, 1, 1);
    EXPECT_TRUE(cache.tokenReleased(second[0].token_id));
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.slots()[first[0].slot_id].pins, 0u);
}

TEST(WeightCacheFaultJoinTest, LayerThirtyTwoKeepsItsOwnOccurrence)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 32, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    cache.setTokenConsumers(tokens[0].token_id, 1);
    cache.markFilling(tokens[0].fill);
    cache.noteInstanceFault(1, 11);
    cache.noteFailureFanoutDone(1);
    cache.noteOwnedWorkDrained(1);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.subscribersInState(
                  kCacheSubscriberStateTERMINAL_TOMBSTONED), 1u);
    cache.noteFillSuccess(tokens[0].fill, 4096, 20);
    EXPECT_EQ(cache.tombstonedSubscribers(), 1u);
    EXPECT_EQ(cache.wokenSubscribers(), 0u);
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.slots()[tokens[0].slot_id].state, kCacheSlotValid);
}

TEST(WeightCacheReservationTest, MergedShadowCoversEveryLayerOnce)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 4;
    config.slot_bytes = 4096;
    config.mshr_slots = 4;
    config.eviction_slots = 4;
    config.obligation_slots = 4;
    config.subscriber_slots = 8;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeWeightCache::LayerTags> demand(2);
    demand[0].layer_id = 1;
    demand[0].tags = {1, 2};
    demand[1].layer_id = 2;
    demand[1].tags = {1};
    const MoeWeightCache::ReservePlan plan = cache.prepare(5, demand, 10);
    ASSERT_EQ(plan.status, MoeWeightCache::ReserveStatus::COMMITTED);
    // One physical fill per base key, one token/pin per (batch, layer).
    ASSERT_EQ(plan.fills.size(), 2u);
    EXPECT_EQ(plan.layers.at(1).size(), 2u);
    EXPECT_EQ(plan.layers.at(2).size(), 1u);
    EXPECT_EQ(plan.fills[0].slot_id, 0u);
    EXPECT_EQ(plan.fills[1].slot_id, 1u);
    EXPECT_EQ(plan.first_incarnation, 1u);
    const std::vector<MoeCacheToken> tokens = cache.commit(plan, 10);
    ASSERT_EQ(tokens.size(), 3u);
    EXPECT_EQ(cache.slots()[0].weight_tag_index, 1u);
    EXPECT_EQ(cache.slots()[1].weight_tag_index, 2u);
    EXPECT_EQ(cache.slots()[0].pins, 2u);
    EXPECT_EQ(cache.slots()[1].pins, 1u);
    EXPECT_EQ(cache.nextFillIncarnation(), 3u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateWAITING), 3u);
    uint32_t issues = 0;
    uint32_t attaches = 0;
    for (const auto &token : tokens) {
        if (token.base.weight_tag_index != 1)
            continue;
        if (token.outcome == kCacheResidencyOutcomeNEW_FILL)
            issues++;
        if (token.outcome == kCacheResidencyOutcomeATTACH)
            attaches++;
    }
    EXPECT_EQ(issues, 1u);
    EXPECT_EQ(attaches, 1u);
}

TEST(WeightCacheReservationTest, SubscriberPreflightCountsOccurrences)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 2;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeWeightCache::LayerTags> demand(2);
    demand[0].layer_id = 1;
    demand[0].tags = {1};
    demand[1].layer_id = 2;
    demand[1].tags = {2};
    // Three occurrences over two physical keys: subscriber capacity is the
    // occurrence count, so this is backpressure and never an underflow.
    std::vector<MoeWeightCache::LayerTags> three = demand;
    three[0].tags = {1, 2};
    const MoeWeightCache::ReservePlan blocked =
        cache.prepare(1, three, 10);
    EXPECT_EQ(blocked.status, MoeWeightCache::ReserveStatus::RESOURCE_WAIT);
    EXPECT_EQ(blocked.fills.size(), 0u);
    EXPECT_EQ(cache.subscriberFree(), 2u);
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.slots()[0].pins, 0u);
    EXPECT_EQ(cache.slots()[1].pins, 0u);
    EXPECT_EQ(cache.obligations().size(), 0u);
    const MoeWeightCache::ReservePlan fits = cache.prepare(1, demand, 11);
    ASSERT_EQ(fits.status, MoeWeightCache::ReserveStatus::COMMITTED);
    EXPECT_EQ(fits.fills.size(), 2u);
}

TEST(WeightCacheOwnershipTest, CrossLayerAliasReleasesEachOccurrenceIndependently)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeWeightCache::LayerTags> demand(2);
    demand[0].layer_id = 1;
    demand[0].tags = {1};
    demand[1].layer_id = 2;
    demand[1].tags = {1};
    const MoeWeightCache::ReservePlan plan = cache.prepare(6, demand, 10);
    ASSERT_EQ(plan.status, MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(plan.fills.size(), 1u);
    const std::vector<MoeCacheToken> tokens = cache.commit(plan, 10);
    ASSERT_EQ(tokens.size(), 2u);
    ASSERT_EQ(cache.slots()[0].pins, 2u);
    for (const auto &token : tokens) {
        cache.setTokenConsumers(token.token_id, 1);
        if (token.outcome == kCacheResidencyOutcomeNEW_FILL)
            cache.markFilling(token.fill);
    }
    cache.noteFillSuccess(tokens[0].fill, 4096, 20);
    ASSERT_EQ(cache.subscribersInState(kCacheSubscriberStateWOKEN), 2u);
    const uint64_t first = tokens[0].layer_id == 1 ? tokens[0].token_id
                                                   : tokens[1].token_id;
    const uint64_t second = tokens[0].layer_id == 1 ? tokens[1].token_id
                                                    : tokens[0].token_id;
    cache.noteConsumerDrained(6, 1, 1);
    EXPECT_TRUE(cache.tokenReleased(first));
    EXPECT_FALSE(cache.tokenReleased(second));
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.slots()[0].pins, 1u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateWOKEN), 1u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateRELEASED), 1u);
    EXPECT_EQ(cache.obligations().size(), 1u);
    cache.noteConsumerDrained(6, 2, 1);
    EXPECT_TRUE(cache.tokenReleased(second));
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.slots()[0].pins, 0u);
    EXPECT_EQ(cache.obligations().size(), 0u);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
    EXPECT_EQ(cache.tokenReleases(), cache.tokensCreated());
}

TEST(WeightCacheReservationTest, RejectsAStaleShadow)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    const MoeWeightCache::ReservePlan stale = cache.prepare(1, 1, {1}, 10);
    ASSERT_EQ(stale.status, MoeWeightCache::ReserveStatus::COMMITTED);
    const std::vector<MoeCacheToken> tokens = cache.commit(stale, 10);
    ASSERT_EQ(tokens.size(), 1u);
    bool stale_rejected = false;
    try {
        cache.commit(stale, 11);
    } catch (...) {
        stale_rejected = true;
    }
    EXPECT_TRUE(stale_rejected);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotFreeReserved);
}

TEST(WeightCacheReservationTest, ProtectsASameBatchHitFromItsOwnMissVictim)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 3;
    config.eviction_slots = 3;
    config.obligation_slots = 3;
    config.subscriber_slots = 6;
    config.cacheable_tags = {1, 2, 3};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> cold;
    ASSERT_EQ(cache.reserve(1, 1, {1, 2}, 10, cold),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(cold.size(), 2u);
    for (const auto &token : cold) {
        cache.markFilling(token.fill);
        cache.noteFillSuccess(token.fill, 4096, 11);
    }
    for (const auto &token : cold)
        cache.releaseToken(token.token_id);
    ASSERT_EQ(cache.slots()[0].state, kCacheSlotValid);
    ASSERT_EQ(cache.slots()[1].state, kCacheSlotValid);
    ASSERT_EQ(cache.slots()[0].pins, 0u);
    const MoeWeightCache::ReservePlan mixed = cache.prepare(2, 1, {1, 3}, 20);
    ASSERT_EQ(mixed.status, MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(mixed.hits.size(), 1u);
    ASSERT_EQ(mixed.fills.size(), 1u);
    EXPECT_EQ(mixed.hits[0].first.weight_tag_index, 1u);
    EXPECT_EQ(mixed.fills[0].base.weight_tag_index, 3u);
    EXPECT_EQ(mixed.fills[0].slot_id, 1u);
    EXPECT_TRUE(mixed.fills[0].victim);
    const std::vector<MoeCacheToken> tokens = cache.commit(mixed, 20);
    ASSERT_EQ(tokens.size(), 2u);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[0].weight_tag_index, 1u);
    EXPECT_EQ(cache.slots()[0].valid_bytes, 4096u);
    EXPECT_EQ(cache.slots()[0].pins, 1u);
    EXPECT_EQ(cache.slots()[1].state, kCacheSlotEvictingReserved);
}

TEST(WeightCacheReservationTest, AWaitingCoreLeavesTheOtherCoreUntouched)
{
    using namespace mesh_abi;
    MoeWeightCache::Config open;
    open.core_id = 0;
    open.slot_count = 2;
    open.slot_bytes = 4096;
    open.mshr_slots = 2;
    open.eviction_slots = 2;
    open.obligation_slots = 2;
    open.subscriber_slots = 4;
    open.cacheable_tags = {1, 2};
    MoeWeightCache first(open);
    MoeWeightCache::Config full = open;
    full.core_id = 1;
    full.slot_count = 1;
    full.mshr_slots = 1;
    full.eviction_slots = 0;
    full.obligation_slots = 1;
    full.subscriber_slots = 2;
    MoeWeightCache second(full);
    std::vector<MoeCacheToken> held;
    ASSERT_EQ(second.reserve(9, 1, {1}, 5, held),
              MoeWeightCache::ReserveStatus::COMMITTED);
    second.markFilling(held[0].fill);
    const MoeWeightCache::ReservePlan left = first.prepare(9, 1, {1}, 6);
    const MoeWeightCache::ReservePlan right = second.prepare(9, 1, {2}, 6);
    ASSERT_EQ(left.status, MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(right.status, MoeWeightCache::ReserveStatus::RESOURCE_WAIT);
    // The coordinator drops both shadows: neither core shows a pin, token,
    // incarnation or slot handoff from the abandoned attempt.
    EXPECT_EQ(first.liveTokens(), 0u);
    EXPECT_EQ(first.slots()[0].pins, 0u);
    EXPECT_EQ(first.slots()[0].state, kCacheSlotFree);
    EXPECT_EQ(first.nextFillIncarnation(), 1u);
    EXPECT_EQ(first.mshrFree(), first.mshrSlots());
    EXPECT_EQ(first.obligations().size(), 0u);
    const MoeWeightCache::ReservePlan retry = first.prepare(9, 1, {1}, 7);
    ASSERT_EQ(retry.status, MoeWeightCache::ReserveStatus::COMMITTED);
    const std::vector<MoeCacheToken> tokens = first.commit(retry, 7);
    ASSERT_EQ(tokens.size(), 1u);
    EXPECT_EQ(tokens[0].outcome, kCacheResidencyOutcomeNEW_FILL);
    EXPECT_EQ(first.slots()[0].state, kCacheSlotFreeReserved);
    EXPECT_EQ(first.slots()[0].pins, 1u);
}


}
}

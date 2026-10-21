#include <gtest/gtest.h>

#include "dev/ai_mesh/mesh_weight_cache.hh"

using namespace gem5::ai_mesh;

TEST(WeightCacheCandidates, FillSourceUsesFullKeyCoreAndOnlyAuxForIncarnation)
{
    const mesh_abi::WeightFillKey fill{256, 1, 7, 3, 9};
    const auto source = weightFillSource(fill);
    EXPECT_EQ(source.core_id_or_ffff, 256u);
    EXPECT_EQ(source.object_kind, 5u);
    EXPECT_EQ(source.generation, 0u);
    EXPECT_EQ(source.region_group_id, 0u);
    EXPECT_EQ(source.region_id, 0u);
    EXPECT_EQ(source.ordinal, 0u);
    EXPECT_EQ(source.aux_key, weightFillKeyBytes(fill));
}

TEST(WeightCacheCandidates, AnotherBatchPendingFillDoesNotBlockReadyOwner)
{
    MoeWeightCache::Config config;
    config.slot_count = config.mshr_slots = config.eviction_slots = config.obligation_slots = 2;
    config.slot_bytes = 4096;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> first, second;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, first), MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(cache.reserve(2, 1, {2}, 11, second), MoeWeightCache::ReserveStatus::COMMITTED);
    cache.markFilling(first[0].fill);
    cache.noteFillSuccess(first[0].fill, 128, 20);
    EXPECT_TRUE(cache.hasPendingSubscribers());
    EXPECT_FALSE(cache.hasPendingSubscribers(1));
    EXPECT_TRUE(cache.hasPendingSubscribers(2));
}

TEST(WeightCacheCandidates, FailedSharedFillRejectsEverySubscriberBeforeRetirement)
{
    MoeWeightCache::Config config;
    config.slot_count = config.mshr_slots = config.eviction_slots = config.obligation_slots = 1;
    config.slot_bytes = 4096;
    config.subscriber_slots = 2;
    config.cacheable_tags = {1};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> first, second;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, first), MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(cache.reserve(2, 1, {1}, 11, second), MoeWeightCache::ReserveStatus::COMMITTED);
    cache.markFilling(first[0].fill);
    cache.noteFillFailure(first[0].fill, mesh_abi::kWeightFillFailureSiteCACHE_FILL_AXI_R, 20, {}, true);
    EXPECT_FALSE(cache.hasTombstone(cache.baseKey(1)));
    EXPECT_EQ(cache.prepare(2, 1, {1}, 21).status, MoeWeightCache::ReserveStatus::FAILED);
    EXPECT_EQ(cache.prepare(3, 1, {1}, 21).status, MoeWeightCache::ReserveStatus::FAILED);
    EXPECT_EQ(cache.liveTokens(), 2u);
    for (uint64_t owner : {1, 2}) {
        cache.abortBeforeStart(owner);
        cache.noteFailureFanoutDone(owner);
    }
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_TRUE(cache.hasTombstone(cache.baseKey(1)));
}

TEST(WeightCacheCandidates, FailureRecordKeepsTheSameEdgeMinimumThroughRetirement)
{
    for (bool axi_first : {false, true}) {
        MoeWeightCache::Config config;
        config.slot_count = config.mshr_slots = config.eviction_slots = config.obligation_slots = 1;
        config.slot_bytes = 4096;
        config.subscriber_slots = 1;
        config.cacheable_tags = {1};
        MoeWeightCache cache(config);
        std::vector<MoeCacheToken> tokens;
        ASSERT_EQ(cache.reserve(1, 1, {1}, 10, tokens), MoeWeightCache::ReserveStatus::COMMITTED);
        const auto key = tokens.front().fill;
        cache.markFilling(key);
        auto axi = weightFillSource(key);
        axi.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
        auto sram = axi;
        sram.error_class = mesh_abi::kMoeErrorClassSRAM;
        for (bool is_axi : {axi_first, !axi_first})
            cache.noteFillFailure(key, is_axi ? mesh_abi::kWeightFillFailureSiteCACHE_FILL_AXI_R :
                mesh_abi::kWeightFillFailureSiteCACHE_FILL_SRAM_COMMIT, 20, is_axi ? axi : sram, true);
        ASSERT_TRUE(cache.failure(1));
        EXPECT_EQ(cache.failure(1)->code, uint32_t(agent_abi::E_AXI_RESPONSE));
        EXPECT_EQ(errorSourceKeyBytes(cache.failure(1)->source), errorSourceKeyBytes(axi));
        cache.abortBeforeStart(1);
        cache.noteFailureFanoutDone(1);
        ASSERT_TRUE(cache.failure(1));
        EXPECT_EQ(cache.failure(1)->tick, 20u);
        EXPECT_EQ(cache.failure(1)->code, uint32_t(agent_abi::E_AXI_RESPONSE));
        EXPECT_TRUE(cache.hasTombstone(cache.baseKey(1)));
        EXPECT_EQ(cache.tombstoneCount(), 1u);
    }
}

TEST(WeightCacheCandidates, FillLifecycleRejectsEveryForeignKeyBeforeMutation)
{
    for (unsigned field = 0; field < 4; ++field) {
        for (unsigned operation = 0; operation < 5; ++operation) {
            MoeWeightCache::Config config;
            config.slot_count = config.mshr_slots = config.eviction_slots = config.obligation_slots = 1;
            config.slot_bytes = 4096;
            config.subscriber_slots = 1;
            config.cacheable_tags = {1};
            MoeWeightCache cache(config);
            std::vector<MoeCacheToken> tokens;
            ASSERT_EQ(cache.reserve(1, 1, {1}, 10, tokens), MoeWeightCache::ReserveStatus::COMMITTED);
            const auto key = tokens.front().fill;
            auto foreign = key;
            if (field == 0) ++foreign.core_id;
            if (field == 1) ++foreign.cache_partition_id;
            if (field == 2) ++foreign.cache_generation;
            if (field == 3) ++foreign.fill_incarnation;
            if (operation != 0) {
                cache.markFilling(key);
                cache.advanceEngineEdge();
            }
            if (operation == 2)
                cache.markIssued(key);
            const auto before = std::make_tuple(cache.slots()[0].state,
                cache.slots()[0].valid_bytes, cache.obligations().at(1).state,
                cache.mshrFree(), cache.liveTokens());
            const auto apply = [&] {
                switch (operation) {
                  case 0: cache.markFilling(foreign); break;
                  case 1: cache.markIssued(foreign); break;
                  case 2: cache.markInFlight(foreign); break;
                  case 3: cache.noteFillSuccess(foreign, 128, 20); break;
                  case 4: cache.noteFillFailure(foreign,
                    mesh_abi::kWeightFillFailureSiteCACHE_FILL_AXI_R, 20,
                    weightFillSource(foreign), true); break;
                }
            };
            EXPECT_ANY_THROW(apply()) << field << ':' << operation;
            EXPECT_EQ(std::make_tuple(cache.slots()[0].state,
                cache.slots()[0].valid_bytes, cache.obligations().at(1).state,
                cache.mshrFree(), cache.liveTokens()), before) << field << ':' << operation;
        }
    }
}

TEST(WeightCacheCandidates, WarmSnapshotIsHitForEveryLayerAndPreservesCanonicalLru)
{
    MoeWeightCache::Config config;
    config.slot_count = config.mshr_slots = config.eviction_slots = config.obligation_slots = 2;
    config.slot_bytes = 4096;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> cold;
    ASSERT_EQ(cache.reserve(1, 1, {1, 2}, 10, cold), MoeWeightCache::ReserveStatus::COMMITTED);
    for (const auto &token : cold) {
        cache.markFilling(token.fill);
        cache.noteFillSuccess(token.fill, 128, 20);
        cache.releaseToken(token.token_id);
    }
    const auto before = cache.nextUseEpoch();
    const auto plan = cache.prepare(2, {{1, {2}}, {2, {1, 2}}}, 30);
    const auto warm = cache.commit(plan, 30);
    ASSERT_EQ(warm.size(), 3u);
    EXPECT_EQ(warm[0].layer_id, 1u);
    EXPECT_EQ(warm[0].base.weight_tag_index, 2u);
    for (const auto &token : warm) {
        EXPECT_EQ(token.outcome, mesh_abi::kCacheResidencyOutcomeHIT);
        EXPECT_FALSE(token.has_fill);
    }
    EXPECT_EQ(cache.nextUseEpoch(), before + 3);
    EXPECT_GT(cache.slots()[cold[1].slot_id].last_use_epoch,
              cache.slots()[cold[0].slot_id].last_use_epoch);
}

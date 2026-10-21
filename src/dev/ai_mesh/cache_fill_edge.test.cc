#include <gtest/gtest.h>

#include "dev/ai_mesh/cache_fill_edge.hh"

using namespace gem5::ai_mesh;

TEST(CacheFillEdge, SameEdgeErrorWinsOverSuccessBeforeAnyLinePublication)
{
    for (bool error_first : {false, true}) {
        MoeWeightCache::Config config;
        config.slot_count = config.mshr_slots = config.eviction_slots = config.obligation_slots = 1;
        config.slot_bytes = 4096;
        config.subscriber_slots = 2;
        config.cacheable_tags = {1};
        MoeWeightCache cache(config);
        std::vector<MoeCacheToken> tokens;
        ASSERT_EQ(cache.reserve(1, 1, {1}, 10, tokens), MoeWeightCache::ReserveStatus::COMMITTED);
        const auto key = tokens.front().fill;
        cache.markFilling(key);
        CacheFillEdge edge(1);
        auto source = weightFillSource(key);
        source.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
        const ErrorCandidate failure{20, source, agent_abi::E_AXI_RESPONSE};
        for (bool error : {error_first, !error_first})
            ASSERT_TRUE(edge.stage(cache, key, 20, error ? std::optional<ErrorCandidate>(failure) : std::nullopt,
                                   error ? std::nullopt : std::optional<uint64_t>(128), true));
        EXPECT_EQ(cache.validLines(), 0u);
        EXPECT_FALSE(cache.failure(1));
        edge.commitFailures();
        ASSERT_TRUE(cache.failure(1));
        edge.commitSuccesses();
        EXPECT_EQ(cache.validLines(), 0u);
        EXPECT_EQ(cache.prepare(2, 1, {1}, 20).status, MoeWeightCache::ReserveStatus::FAILED);
        cache.abortBeforeStart(1);
        cache.noteFailureFanoutDone(1);
        EXPECT_TRUE(cache.hasTombstone(cache.baseKey(1)));
        EXPECT_EQ(cache.liveTokens(), 0u);
        EXPECT_EQ(cache.mshrFree(), 1u);
    }
}

TEST(CacheFillEdge, SuccessfulFillBecomesVisibleOnlyAtTheCommitPhase)
{
    MoeWeightCache::Config config;
    config.slot_count = config.mshr_slots = config.eviction_slots = config.obligation_slots = 1;
    config.slot_bytes = 4096;
    config.subscriber_slots = 2;
    config.cacheable_tags = {1};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, tokens), MoeWeightCache::ReserveStatus::COMMITTED);
    cache.markFilling(tokens.front().fill);
    CacheFillEdge edge(1);
    ASSERT_TRUE(edge.stage(cache, tokens.front().fill, 20, std::nullopt, 128, true));
    EXPECT_EQ(cache.validLines(), 0u);
    edge.commitFailures();
    EXPECT_EQ(cache.validLines(), 0u);
    edge.commitSuccesses();
    EXPECT_EQ(cache.validLines(), 1u);
    EXPECT_EQ(cache.mshrFree(), 1u);
}

TEST(CacheFillEdge, FailedResponseRetainsResourcesUntilTheDescriptorDrains)
{
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
    CacheFillEdge edge(1);
    auto source = weightFillSource(key);
    source.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
    ASSERT_TRUE(edge.stage(cache, key, 20, ErrorCandidate{20, source, agent_abi::E_AXI_RESPONSE},
                          std::nullopt, false));
    edge.commitFailures();
    cache.abortBeforeStart(1);
    cache.noteFailureFanoutDone(1);
    edge.commitSuccesses();
    EXPECT_EQ(cache.mshrFree(), 0u);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_FALSE(cache.hasTombstone(cache.baseKey(1)));
    ASSERT_TRUE(edge.stage(cache, key, 30, std::nullopt, std::nullopt, true));
    edge.commitFailures();
    edge.commitSuccesses();
    EXPECT_EQ(cache.mshrFree(), 1u);
    EXPECT_EQ(cache.liveTokens(), 0u);
    ASSERT_TRUE(cache.failure(1));
    EXPECT_EQ(cache.failure(1)->tick, 20u);
    EXPECT_TRUE(cache.hasTombstone(cache.baseKey(1)));
}

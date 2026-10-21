#include <gtest/gtest.h>

#include "dev/ai_mesh/mesh_weight_cache.hh"

using namespace gem5::ai_mesh;

class WeightCacheReplay : public ::testing::Test
{
  protected:
    MoeWeightCache::Config config{0, 2, 4096, 2, 2, 2, 4, 0, {1, 2, 3}};
};

TEST_F(WeightCacheReplay, InstalledWarmStateOwnsHitsAndTheNextColdIdentity)
{
    MoeWeightCache source(config), restored(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(source.reserve(1, 1, {1, 2}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    std::string reason;
    EXPECT_FALSE(source.replayState(reason));
    for (const auto &token : tokens) {
        source.markFilling(token.fill);
        source.noteFillSuccess(token.fill, 128, 20);
    }
    EXPECT_FALSE(source.replayState(reason));
    for (const auto &token : tokens)
        source.releaseToken(token.token_id);
    const auto state = source.replayState(reason);
    ASSERT_TRUE(state) << reason;
    ASSERT_TRUE(restored.installReplay(*state, reason)) << reason;
    EXPECT_EQ(source.snapshotJson(), restored.snapshotJson());
    EXPECT_EQ(restored.nextUseEpoch(), 2u);
    const auto hit = restored.commit(restored.prepare(2, {{1, {2}}, {2, {2}}}, 30), 30);
    ASSERT_EQ(hit.size(), 2u);
    for (const auto &token : hit) {
        EXPECT_EQ(token.outcome, mesh_abi::kCacheResidencyOutcomeHIT);
        EXPECT_FALSE(token.has_fill);
        restored.releaseToken(token.token_id);
    }
    const auto miss = restored.commit(restored.prepare(3, 1, {3}, 40), 40);
    ASSERT_EQ(miss.size(), 1u);
    EXPECT_EQ(miss[0].slot_id, tokens[0].slot_id);
    EXPECT_EQ(miss[0].fill.fill_incarnation, source.nextFillIncarnation());
}

TEST_F(WeightCacheReplay, InvalidStateDoesNotPublishAnySlot)
{
    MoeWeightCache cache(config);
    std::string reason;
    const auto initial = cache.replayState(reason);
    ASSERT_TRUE(initial);
    for (unsigned mode = 0; mode < 8; ++mode) {
        auto bad = *initial;
        bad.slots[0] = {1, 128, 0};
        if (mode == 0) bad.core_id = 1;
        if (mode == 1) bad.next_fill_incarnation = 0;
        if (mode == 2) bad.slots[1] = {1, 128, 1};
        if (mode == 3) bad.slots[0].lru_rank = 1;
        if (mode == 4) bad.slots[1].valid_bytes = 128;
        if (mode == 5) bad.slots[0].valid_bytes = 4097;
        if (mode == 6) bad.slots[0].weight_tag_index = 99;
        if (mode == 7) bad.cache_generation = 1;
        const auto before = cache.snapshotJson();
        EXPECT_FALSE(cache.installReplay(bad, reason)) << mode;
        EXPECT_FALSE(reason.empty());
        EXPECT_EQ(cache.snapshotJson(), before);
    }
}

TEST_F(WeightCacheReplay, LiveOwnerRejectsInstallationWithoutDisturbingItsToken)
{
    MoeWeightCache cache(config);
    std::string reason;
    const auto state = cache.replayState(reason);
    ASSERT_TRUE(state);
    const auto tokens = cache.commit(cache.prepare(1, 1, {1}, 10), 10);
    ASSERT_EQ(tokens.size(), 1u);
    EXPECT_FALSE(cache.installReplay(*state, reason));
    EXPECT_EQ(cache.liveTokens(), 1u);
    cache.markFilling(tokens[0].fill);
    cache.noteFillSuccess(tokens[0].fill, 128, 20);
    EXPECT_FALSE(cache.installReplay(*state, reason));
    EXPECT_EQ(cache.liveTokens(), 1u);
    cache.releaseToken(tokens[0].token_id);
    EXPECT_TRUE(cache.installReplay(*state, reason)) << reason;
    EXPECT_EQ(cache.validLines(), 0u);
}

TEST_F(WeightCacheReplay, FailureProvenanceSurvivesInstallationAndRejectsCorruption)
{
    MoeWeightCache source(config), restored(config);
    const auto tokens = source.commit(source.prepare(1, 1, {1}, 10), 10);
    ASSERT_EQ(tokens.size(), 1u);
    const auto fill = tokens[0].fill;
    source.markFilling(fill);
    source.noteFillFailure(fill, mesh_abi::kWeightFillFailureSiteCACHE_FILL_AXI_R,
                           20, weightFillSource(fill), true);
    source.abortBeforeStart(1);
    source.noteFailureFanoutDone(1);
    std::string reason;
    const auto state = source.replayState(reason);
    ASSERT_TRUE(state) << reason;
    ASSERT_TRUE(restored.installReplay(*state, reason)) << reason;
    ASSERT_TRUE(restored.failure(1));
    EXPECT_EQ(restored.failure(1)->tick, 20u);
    EXPECT_EQ(restored.failure(1)->code, uint32_t(agent_abi::E_AXI_RESPONSE));
    EXPECT_EQ(errorSourceKeyBytes(restored.failure(1)->source),
              errorSourceKeyBytes(weightFillSource(fill)));
    EXPECT_EQ(restored.snapshotJson(), source.snapshotJson());
    EXPECT_NE(restored.snapshotJson().find("\"first_error\":"), std::string::npos);
    EXPECT_EQ(restored.prepare(2, 1, {1}, 30).status, MoeWeightCache::ReserveStatus::FAILED);
    for (unsigned mode = 0; mode < 6; ++mode) {
        auto bad = *state;
        auto &error = bad.failure_tombstones.at(1);
        if (mode == 0) error.code = 0;
        if (mode == 1) error.source.core_id_or_ffff = 1;
        if (mode == 2) error.source.aux_key[4] = 2;
        if (mode == 3) bad.next_fill_incarnation = fill.fill_incarnation;
        if (mode == 4) bad.slots[0] = {1, 128, 0};
        if (mode == 5) bad.cache_generation = 1;
        const auto before = restored.snapshotJson();
        EXPECT_FALSE(restored.installReplay(bad, reason)) << mode;
        EXPECT_EQ(before, restored.snapshotJson());
    }
}

TEST_F(WeightCacheReplay, InstallationInvalidatesPreparedReservations)
{
    MoeWeightCache cache(config);
    const auto stale = cache.prepare(1, 1, {1}, 10);
    std::string reason;
    const auto state = cache.replayState(reason);
    ASSERT_TRUE(state);
    ASSERT_TRUE(cache.installReplay(*state, reason));
    EXPECT_ANY_THROW(cache.commit(stale, 20));
    EXPECT_EQ(cache.pendingFills(), 0u);
    EXPECT_EQ(cache.liveTokens(), 0u);
}

TEST_F(WeightCacheReplay, LargeFailureTickUsesCanonicalU64Json)
{
    MoeWeightCache cache(config);
    const auto tokens = cache.commit(cache.prepare(1, 1, {1}, 10), 10);
    ASSERT_EQ(tokens.size(), 1u);
    const auto fill = tokens[0].fill;
    cache.markFilling(fill);
    cache.noteFillFailure(fill, mesh_abi::kWeightFillFailureSiteCACHE_FILL_AXI_R,
        (uint64_t(1) << 53) + 20, weightFillSource(fill), true);
    cache.abortBeforeStart(1);
    cache.noteFailureFanoutDone(1);
    EXPECT_NE(cache.snapshotJson().find("\"tick\":\"0x0020000000000014\""), std::string::npos);
}

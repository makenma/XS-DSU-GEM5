#include <gtest/gtest.h>

#include "mem/cache/cache_blk.hh"

namespace gem5
{

TEST(CacheBlkCheckpointDirtyTest, SurvivesProtocolDirtyBitClear)
{
    CacheBlk blk;
    blk.insert(0x123, false);

    EXPECT_FALSE(blk.isCheckpointDirty());
    blk.setCoherenceBits(CacheBlk::DirtyBit);
    EXPECT_TRUE(blk.isCheckpointDirty());

    // A coherence transition may hand off the architectural DirtyBit, but a
    // checkpoint still needs to make this resident modified copy durable.
    blk.clearCoherenceBits(CacheBlk::DirtyBit);
    EXPECT_FALSE(blk.isSet(CacheBlk::DirtyBit));
    EXPECT_TRUE(blk.isCheckpointDirty());

    blk.clearCheckpointDirty();
    EXPECT_FALSE(blk.isCheckpointDirty());
}

TEST(CacheBlkCheckpointDirtyTest, InvalidationClearsStickyState)
{
    CacheBlk blk;
    blk.insert(0x456, false);
    blk.setCoherenceBits(CacheBlk::DirtyBit);
    ASSERT_TRUE(blk.isCheckpointDirty());

    blk.invalidate();
    EXPECT_FALSE(blk.isCheckpointDirty());
}

TEST(CacheBlkCheckpointDirtyTest, WritebackPhaseTracksCurrentOwner)
{
    CacheBlk blk;
    blk.insert(0x789, false);

    EXPECT_EQ(blk.checkpointWritebackPhase(), 0u);

    // A former dirty owner is low precedence once ownership has moved on.
    blk.setCoherenceBits(CacheBlk::DirtyBit);
    blk.clearCoherenceBits(CacheBlk::DirtyBit);
    ASSERT_TRUE(blk.isCheckpointDirty());
    EXPECT_EQ(blk.checkpointWritebackPhase(), 0u);

    blk.setCoherenceBits(CacheBlk::WritableBit);
    EXPECT_EQ(blk.checkpointWritebackPhase(), 1u);

    blk.setCoherenceBits(CacheBlk::DirtyBit);
    EXPECT_EQ(blk.checkpointWritebackPhase(), 2u);
    EXPECT_EQ(CacheBlk::CheckpointWritebackPhaseCount, 3u);
}

TEST(CacheBlkCheckpointDirtyTest, WritebackPhaseMatchesMoesiPrecedence)
{
    CacheBlk blk;
    blk.insert(0xabc, false);

    // Shared: a clean non-owner copy must be consolidated first.
    blk.setCoherenceBits(CacheBlk::ReadableBit);
    EXPECT_EQ(blk.checkpointWritebackPhase(), 0u);

    // Exclusive: the clean writable owner outranks shared/former copies.
    blk.setCoherenceBits(CacheBlk::WritableBit);
    EXPECT_EQ(blk.checkpointWritebackPhase(), 1u);

    // Modified: a dirty writable owner has the highest precedence.
    blk.setCoherenceBits(CacheBlk::DirtyBit);
    EXPECT_EQ(blk.checkpointWritebackPhase(), 2u);

    // Owned: DirtyBit remains authoritative even without WritableBit.
    blk.clearCoherenceBits(CacheBlk::WritableBit);
    ASSERT_TRUE(blk.isSet(CacheBlk::DirtyBit));
    EXPECT_EQ(blk.checkpointWritebackPhase(), 2u);
}

} // namespace gem5

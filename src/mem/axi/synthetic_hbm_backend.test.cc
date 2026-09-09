#include <gtest/gtest.h>

#include <cstdint>
#include <limits>
#include <stdexcept>

#include "mem/axi/axi_target_adapter.hh"

namespace gem5
{
namespace axi
{
namespace
{

class SyntheticHbmTargetTest : public ::testing::Test
{
  protected:
    AxiTargetConfig config() const
    {
        AxiTargetConfig result;
        result.dstNode = 1;
        result.dataBusBytes = 32;
        result.capacity = {8, 128, 8, 128};
        result.orphanTransactions = 8;
        result.orphanBeats = 128;
        result.bReadyDepth = 8;
        result.rReadyDepth = 128;
        result.writeServiceDepth = 8;
        result.readServiceDepth = 8;
        result.writeBaseLatency = 10;
        result.readBaseLatency = 10;
        result.sourceQuotas[{0, 0}] = {8, 128, 8, 128};
        result.memoryRanges = {{0, 0x10000, 1}};
        result.syntheticHbmEnabled = true;
        result.syntheticHbmBytesPerCycle = 32;
        result.syntheticHbmQueueDepth = 8;
        return result;
    }

    AxiAddressPacket address(uint64_t uid, uint32_t id) const
    {
        AxiAddressPacket result;
        result.meta.txnUid = uid;
        result.meta.axiId = id;
        result.meta.dstNode = 1;
        result.meta.srcNode = 0;
        result.meta.srcPort = 0;
        result.request.address = 0x1000 + uid * 128;
        result.request.axiId = id;
        result.request.beatCount = 2;
        result.request.size = 5;
        result.request.burst = AxiBurst::Incr;
        result.writeOrdinal = uid;
        return result;
    }

    void write(AxiTargetState &target, const AxiAddressPacket &aw) const
    {
        target.acceptAw(aw, 0);
        for (uint16_t i = 0; i < aw.request.beatCount; ++i) {
            AxiDataPacket beat;
            beat.meta = aw.meta;
            beat.writeOrdinal = aw.writeOrdinal;
            beat.address = aw.request.address;
            beat.beatIndex = i;
            beat.beatCount = aw.request.beatCount;
            beat.last = i + 1 == beat.beatCount;
            beat.byteStrobe = 0xffffffff;
            beat.functionalData.assign(32, 0x5a);
            beat.payloadDigest = payloadDigest(beat.functionalData);
            target.acceptW(beat, 0);
        }
    }
};

TEST_F(SyntheticHbmTargetTest, BaseLatencyAndServiceDoNotCollapse)
{
    AxiTargetState target(config());
    target.acceptAr(address(1, 1), 0);
    target.acceptAr(address(2, 2), 0);
    target.advance(10);
    EXPECT_FALSE(target.hasRPacket());
    target.advance(11);
    EXPECT_FALSE(target.hasRPacket());
    target.advance(12);
    ASSERT_TRUE(target.hasRPacket());
    EXPECT_EQ(target.completedReads(), 1);
    target.advance(14);
    EXPECT_EQ(target.completedReads(), 2);
}

TEST_F(SyntheticHbmTargetTest, ReadAndWriteShareServiceBandwidth)
{
    AxiTargetState target(config());
    target.acceptAr(address(1, 1), 0);
    write(target, address(2, 2));
    target.advance(12);
    EXPECT_EQ(target.completedReads() + target.completedWrites(), 1);
    target.advance(14);
    EXPECT_EQ(target.completedReads(), 1);
    EXPECT_EQ(target.completedWrites(), 1);
    EXPECT_EQ(target.memory().readByte(address(2, 2).request.address), 0x5a);
}

TEST_F(SyntheticHbmTargetTest, HalfRateRequiresTwiceTheServiceCycles)
{
    auto cfg = config();
    cfg.syntheticHbmBytesPerCycle = 16;
    AxiTargetState target(cfg);
    target.acceptAr(address(1, 1), 0);
    target.advance(12);
    EXPECT_FALSE(target.hasRPacket());
    target.advance(14);
    EXPECT_EQ(target.completedReads(), 1);
}

TEST_F(SyntheticHbmTargetTest, DisabledBackendPreservesLegacyTiming)
{
    auto cfg = config();
    cfg.syntheticHbmEnabled = false;
    AxiTargetState target(cfg);
    target.acceptAr(address(1, 1), 0);
    target.acceptAr(address(2, 2), 0);
    target.advance(10);
    EXPECT_EQ(target.completedReads(), 2);
    EXPECT_FALSE(target.syntheticBackendEnabled());
    EXPECT_EQ(target.syntheticBackendStats().submitted, 0);
}

TEST_F(SyntheticHbmTargetTest, BoundedServiceCannotBlockOlderSameIdCommit)
{
    auto cfg = config();
    cfg.syntheticHbmQueueDepth = 1;
    AxiTargetState target(cfg);
    auto younger = address(2, 1);
    younger.meta.targetSeq = 1;
    younger.meta.responseSeq = 1;
    target.acceptAr(younger, 0);
    target.acceptAr(address(1, 1), 0);
    target.advance(12);
    EXPECT_FALSE(target.hasRPacket());
    target.advance(24);
    EXPECT_EQ(target.completedReads(), 2);
    ASSERT_TRUE(target.hasRPacket());
    EXPECT_EQ(target.frontRPacket().meta.txnUid, 1);
    const auto stats = target.syntheticBackendStats();
    EXPECT_EQ(stats.submitted, 2);
    EXPECT_EQ(stats.retired, 2);
    EXPECT_EQ(stats.highWater, 1);
    EXPECT_EQ(stats.readBytesServiced, 128);
}

TEST_F(SyntheticHbmTargetTest, FaultStillDrainsWithoutMemoryCommit)
{
    auto cfg = config();
    cfg.transactionFaults[2] = AxiResp::SlvErr;
    AxiTargetState target(cfg);
    write(target, address(2, 2));
    target.advance(12);
    ASSERT_TRUE(target.hasBPacket());
    EXPECT_EQ(target.frontBPacket().resp, AxiResp::SlvErr);
    EXPECT_EQ(target.memory().readByte(address(2, 2).request.address), 0);
    EXPECT_EQ(target.progress().writeCommittedBytes, 0);
    EXPECT_EQ(target.syntheticBackendStats().writeBytesServiced, 64);
    target.popBPacket();
    EXPECT_EQ(target.occupancy().writeContexts, 0);
}

TEST_F(SyntheticHbmTargetTest, SuccessfulCommitCountsStrobesAndLocalCycle)
{
    AxiTargetState target(config());
    write(target, address(2, 2));
    target.advance(12);
    EXPECT_EQ(target.progress().writeCommittedBytes, 64);
    EXPECT_EQ(target.progress().lastWriteCommitCycle, 12);
    EXPECT_EQ(target.syntheticBackendStats().retired, 1);
}

TEST(SyntheticHbmBackendTest, LatenciesOverlapWithoutSerialBasePenalty)
{
    SyntheticHbmBackend backend(32, 4);
    ASSERT_TRUE(backend.trySubmit(1, SyntheticHbmDirection::Read, 512, 0, 200));
    ASSERT_TRUE(backend.trySubmit(2, SyntheticHbmDirection::Read, 512, 0, 200));
    backend.advance(215);
    EXPECT_FALSE(backend.ready(1));
    backend.advance(216);
    EXPECT_TRUE(backend.ready(1));
    EXPECT_FALSE(backend.ready(2));
    backend.advance(232);
    EXPECT_TRUE(backend.ready(2));
    EXPECT_EQ(backend.completionCycle(1), 216);
    EXPECT_EQ(backend.completionCycle(2), 232);
    EXPECT_EQ(backend.stats().busyCycles, 32);
}

TEST(SyntheticHbmBackendTest, SameCycleAdvanceCannotDuplicateService)
{
    SyntheticHbmBackend backend(32, 2);
    ASSERT_TRUE(backend.trySubmit(1, SyntheticHbmDirection::Read, 64, 0, 0));
    backend.advance(1);
    EXPECT_EQ(backend.stats().readBytesServiced, 32);
    for (int i = 0; i < 10; ++i)
        backend.advance(1);
    EXPECT_FALSE(backend.ready(1));
    EXPECT_EQ(backend.stats().readBytesServiced, 32);
    backend.advance(2);
    EXPECT_TRUE(backend.ready(1));
}

TEST(SyntheticHbmBackendTest, CompletedServiceRetainsFiniteSlotUntilHandoff)
{
    SyntheticHbmBackend backend(32, 1);
    ASSERT_TRUE(backend.trySubmit(1, SyntheticHbmDirection::Read, 32, 0, 0));
    backend.advance(1);
    EXPECT_EQ(backend.stats().ready, 1);
    EXPECT_FALSE(backend.trySubmit(2, SyntheticHbmDirection::Write, 32, 1, 0));
    backend.retire(1);
    EXPECT_TRUE(backend.trySubmit(2, SyntheticHbmDirection::Write, 32, 1, 0));
    backend.advance(2);
    backend.retire(2);
    EXPECT_TRUE(backend.idle());
    EXPECT_EQ(backend.stats().queueFullCycles, 2);
    EXPECT_EQ(backend.stats().requestSlotCycles, 2);
    EXPECT_EQ(backend.stats().highWater, 1);
    EXPECT_EQ(backend.stats().readBytesServiced, 32);
    EXPECT_EQ(backend.stats().writeBytesServiced, 32);
}

TEST(SyntheticHbmBackendTest, ReadWriteRateIsSharedAndPartialCyclesAreBounded)
{
    SyntheticHbmBackend backend(32, 3);
    ASSERT_TRUE(backend.trySubmit(1, SyntheticHbmDirection::Read, 33, 0, 0));
    ASSERT_TRUE(backend.trySubmit(2, SyntheticHbmDirection::Write, 64, 0, 0));
    backend.advance(3);
    EXPECT_TRUE(backend.ready(1));
    EXPECT_FALSE(backend.ready(2));
    EXPECT_EQ(backend.stats().readBytesServiced, 33);
    EXPECT_EQ(backend.stats().writeBytesServiced, 32);
    EXPECT_LE(backend.stats().readBytesServiced +
              backend.stats().writeBytesServiced, 3 * 32);
    backend.advance(4);
    EXPECT_TRUE(backend.ready(2));
}

TEST(SyntheticHbmBackendTest, UnreadyRequestDoesNotOccupyService)
{
    SyntheticHbmBackend backend(32, 2);
    ASSERT_TRUE(backend.trySubmit(1, SyntheticHbmDirection::Read, 32, 0, 20));
    ASSERT_TRUE(backend.trySubmit(2, SyntheticHbmDirection::Write, 32, 0, 0));
    backend.advance(1);
    EXPECT_FALSE(backend.ready(1));
    EXPECT_TRUE(backend.ready(2));
    backend.advance(21);
    EXPECT_TRUE(backend.ready(1));
    EXPECT_EQ(backend.stats().busyCycles, 2);
}

TEST(SyntheticHbmBackendTest, RejectsInvalidCapacityTimeAndLifecycle)
{
    EXPECT_THROW(SyntheticHbmBackend(0, 1), std::invalid_argument);
    EXPECT_THROW(SyntheticHbmBackend(32, 0), std::invalid_argument);
    SyntheticHbmBackend backend(32, 2);
    EXPECT_THROW(backend.trySubmit(1, SyntheticHbmDirection::Read, 0, 0, 0),
                 std::invalid_argument);
    EXPECT_THROW(backend.trySubmit(1, SyntheticHbmDirection::Read, 32,
                                  std::numeric_limits<uint64_t>::max(), 1),
                 std::invalid_argument);
    ASSERT_TRUE(backend.trySubmit(1, SyntheticHbmDirection::Read, 32, 0, 10));
    EXPECT_THROW(backend.trySubmit(1, SyntheticHbmDirection::Read, 32, 0, 10),
                 std::invalid_argument);
    EXPECT_THROW(backend.retire(1), std::logic_error);
    EXPECT_THROW(backend.completionCycle(1), std::logic_error);
    backend.advance(11);
    EXPECT_THROW(backend.advance(10), std::invalid_argument);
    backend.retire(1);
    EXPECT_THROW(backend.retire(1), std::out_of_range);
}

}
}
}

#include <gtest/gtest.h>

#include <cstdint>
#include <vector>

#include "dev/ai_mesh/tensor_sram.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

// Tick-consistent bank/port occupancy: conflict placements must stall
// longer than parallel placements, and a second port must strictly reduce
// the conflict stall (spec: SRAM service participates in timing).
TensorSram makeSram(uint32_t read_ports)
{
    return TensorSram(/*bytes*/ 1 << 20, /*banks*/ 16, /*alignment*/ 64,
                      /*line_bytes*/ 32, /*read bytes/cycle*/ 32,
                      /*write bytes/cycle*/ 32, read_ports, 1,
                      /*line_tick*/ 500);
}

TEST(TensorSramTest, ParallelBanksDoNotStallEachOther)
{
    auto sram = makeSram(1);
    auto a = sram.reserve(1000, 0x0000, 32, false);
    auto b = sram.reserve(1000, 0x0040, 32, false);
    auto c = sram.reserve(1000, 0x0080, 32, false);
    EXPECT_EQ(a.stall_ticks, 500u);
    EXPECT_EQ(b.stall_ticks, 500u);
    EXPECT_EQ(c.stall_ticks, 500u);
    EXPECT_EQ(a.conflict_ticks, 0u);
    EXPECT_EQ(b.conflict_ticks, 0u);
}

TEST(TensorSramTest, SameBankConflictsStall)
{
    auto sram = makeSram(1);
    // Same bank (line 32B, 16 banks): 0x0000, 0x0200, 0x0400 all map to
    // bank 0 modulo 16 lines (0x200/0x20 = 16 lines exactly).
    auto a = sram.reserve(1000, 0x0000, 32, false);
    auto b = sram.reserve(1000, 0x0200, 32, false);
    auto c = sram.reserve(1000, 0x0400, 32, false);
    EXPECT_EQ(a.conflict_ticks, 0u);
    EXPECT_GT(b.conflict_ticks, 0u);
    EXPECT_GT(c.conflict_ticks, b.conflict_ticks);
    EXPECT_GT(c.stall_ticks, b.stall_ticks);
    EXPECT_GT(b.stall_ticks, a.stall_ticks);
}

TEST(TensorSramTest, SecondPortReducesConflict)
{
    auto one = makeSram(1);
    auto two = makeSram(2);
    uint64_t stall_one = 0;
    uint64_t stall_two = 0;
    for (int i = 0; i < 2; i++) {
        stall_one = one.reserve(1000, 0x0000 + i * 0x0200, 32, false).stall_ticks;
        stall_two = two.reserve(1000, 0x0000 + i * 0x0200, 32, false).stall_ticks;
    }
    // The second request on the same bank: 1-port serializes, 2-port does
    // not (each request's own service fits on its own port).
    EXPECT_GT(stall_one, stall_two);
}

TEST(TensorSramTest, UnitsStayTicks)
{
    auto sram = makeSram(1);
    // 32 bytes at 32 B/cycle = 1 cycle = 500 ticks; stall and service are
    // both absolute-tick quantities (no cycle/tick mixing).
    auto result = sram.reserve(1000, 0x1234 & ~uint64_t(31), 32, false);
    EXPECT_EQ(result.service_ticks % 500u, 0u);
    EXPECT_EQ(result.stall_ticks % 500u, 0u);
}

TEST(TensorSramTest, ReadAndWritePortsAreIndependent)
{
    // One read port and one write port per bank: a read and a write on the
    // same bank at the same tick must not contend for each other's ports.
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 16, /*alignment*/ 64,
                    /*line_bytes*/ 32, /*read bytes/cycle*/ 32,
                    /*write bytes/cycle*/ 32, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 500);
    auto rd = sram.reserve(1000, 0x0000, 32, false);
    auto wr = sram.reserve(1000, 0x0000, 32, true);
    EXPECT_EQ(rd.conflict_ticks, 0u);
    EXPECT_EQ(wr.conflict_ticks, 0u);
    auto rd2 = sram.reserve(1000, 0x0000, 32, false);
    EXPECT_GT(rd2.conflict_ticks, 0u);
}

TEST(TensorSramTest, WritePortCountIsNotInflatedByReadPorts)
{
    // read_ports=2/write_ports=1 must still serialize two same-bank writes.
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 16, /*alignment*/ 64,
                    /*line_bytes*/ 32, /*read bytes/cycle*/ 32,
                    /*write bytes/cycle*/ 32, /*read_ports*/ 2,
                    /*write_ports*/ 1, /*line_tick*/ 500);
    auto first = sram.reserve(1000, 0x0000, 32, true);
    auto second = sram.reserve(1000, 0x0000, 32, true);
    EXPECT_EQ(first.conflict_ticks, 0u);
    EXPECT_GT(second.conflict_ticks, 0u);
}

TEST(TensorSramTest, ZeroGeometryParametersAreRejected)
{
    EXPECT_ANY_THROW(TensorSram(1 << 20, 0, 64, 32, 32, 32, 1, 1, 500));
    EXPECT_ANY_THROW(TensorSram(1 << 20, 16, 64, 0, 32, 32, 1, 1, 500));
    EXPECT_ANY_THROW(TensorSram(1 << 20, 16, 64, 32, 32, 32, 0, 1, 500));
    EXPECT_ANY_THROW(TensorSram(1 << 20, 16, 64, 32, 32, 32, 1, 0, 500));
    EXPECT_ANY_THROW(TensorSram(1 << 20, 16, 64, 32, 0, 32, 1, 1, 500));
    EXPECT_ANY_THROW(TensorSram(0, 16, 64, 32, 32, 32, 1, 1, 500));
}

TEST(TensorSramTest, BoundaryCheckDoesNotWrap)
{
    auto sram = makeSram(1);
    const uint64_t capacity = sram.capacity();
    uint8_t scratch[16];
    EXPECT_FALSE(sram.read(capacity, 16, scratch));
    EXPECT_FALSE(sram.write(capacity, 16, scratch));
    EXPECT_FALSE(sram.read(UINT64_MAX, 16, scratch));
    EXPECT_FALSE(sram.write(UINT64_MAX - 15, 16, scratch));
}

TEST(TensorSramTest, BankQueueFullBlocksAdmission)
{
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 16, /*alignment*/ 64,
                    /*line_bytes*/ 32, /*read bytes/cycle*/ 32,
                    /*write bytes/cycle*/ 32, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 500,
                    /*bank_queue_depth*/ 1);
    EXPECT_TRUE(sram.canReserve(1000, 0x0000, 32, false));
    sram.reserve(1000, 0x0000, 32, false);
    EXPECT_FALSE(sram.canReserve(1000, 0x0000, 32, false));
    EXPECT_FALSE(sram.canReserve(1200, 0x0000, 32, false));
    EXPECT_TRUE(sram.canReserve(2000, 0x0000, 32, false));
}

TEST(TensorSramTest, CanReserveHasNoSideEffects)
{
    TensorSram sram(1 << 20, 16, 64, 32, 32, 32, 1, 1, 500, 2);
    EXPECT_TRUE(sram.canReserve(1000, 0x0000, 32, false));
    EXPECT_TRUE(sram.canReserve(1000, 0x0000, 32, false));
    auto first = sram.reserve(1000, 0x0000, 32, false);
    EXPECT_EQ(first.conflict_ticks, 0u);
}

TEST(TensorSramTest, AdmissionChecksEveryBankInTheRange)
{
    TensorSram sram(4096, 4, 32, 32, 32, 32, 1, 1, 500, 1);
    sram.reserve(1000, 32, 32, true);
    EXPECT_FALSE(sram.canReserve(1000, 0, 64, true));
    EXPECT_EQ(sram.bankQueueLoad(1000, 0, 64, true), 1);
    EXPECT_TRUE(sram.canReserve(1500, 0, 64, true));
    EXPECT_TRUE(sram.canReserve(1000, 0, 64, false));
}

TEST(TensorSramTest, MultipleLinesInOneBankUseOneRequestEntry)
{
    TensorSram sram(4096, 2, 32, 32, 32, 32, 1, 1, 500, 2);
    sram.reserve(1000, 0, 160, true);
    EXPECT_EQ(sram.bankQueueLoad(1000, 0, 32, true), 1);
    EXPECT_EQ(sram.bankQueueLoad(1000, 32, 32, true), 1);
    EXPECT_TRUE(sram.canReserve(1000, 0, 32, true));
    EXPECT_EQ(sram.bankQueueLoad(2000, 0, 32, true), 1);
    EXPECT_EQ(sram.bankQueueLoad(2500, 0, 32, true), 0);
}

TEST(TensorSramTest, EmptyRangeConsumesNoQueueEntry)
{
    TensorSram sram(4096, 4, 32, 32, 32, 32, 1, 1, 500, 1);
    sram.reserve(1000, 0, 32, true);
    EXPECT_TRUE(sram.canReserve(1000, 0, 0, true));
    EXPECT_TRUE(sram.canReserve(1000, sram.capacity(), 0, true));
    EXPECT_EQ(sram.bankQueueLoad(1000, 0, 0, true), 0);
}

TEST(TensorSramTest, AdmissionRejectsOutOfBoundsRanges)
{
    TensorSram sram(4096, 4, 32, 32, 32, 32, 1, 1, 500, 1);
    EXPECT_FALSE(sram.canReserve(1000, sram.capacity(), 1, true));
    EXPECT_FALSE(sram.canReserve(1000, UINT64_MAX, 32, true));
    EXPECT_FALSE(sram.canReserve(1000, sram.capacity() + 1, 0, true));
}

TEST(TensorSramTest, FailedTryReserveDoesNotPartiallyReserveOtherBanks)
{
    TensorSram sram(4096, 4, 32, 32, 32, 32, 1, 1, 500, 1);
    ASSERT_TRUE(sram.tryReserve(1000, 32, 32, true));
    EXPECT_FALSE(sram.tryReserve(1000, 0, 64, true));
    auto independent = sram.tryReserve(1000, 0, 32, true);
    ASSERT_TRUE(independent);
    EXPECT_EQ(independent->stall_ticks, 500);
    EXPECT_EQ(independent->conflict_ticks, 0);
}

TEST(TensorSramTest, TryReserveBoundsRequestsUntilTheirFinalBankService)
{
    TensorSram sram(4096, 2, 32, 32, 32, 32, 1, 1, 500, 2);
    ASSERT_TRUE(sram.tryReserve(1000, 0, 160, true));
    ASSERT_TRUE(sram.tryReserve(1000, 0, 32, true));
    EXPECT_FALSE(sram.tryReserve(1000, 0, 32, true));
    EXPECT_EQ(sram.bankQueueLoad(1000, 0, 32, true), 2);
    EXPECT_FALSE(sram.tryReserve(2000, 0, 32, true));
    auto next = sram.tryReserve(2500, 0, 32, true);
    ASSERT_TRUE(next);
    EXPECT_EQ(next->stall_ticks, 1000);
    EXPECT_EQ(sram.bankQueueLoad(2500, 0, 32, true), 2);
}

TEST(TensorSramTest, TryReserveDirectionsHaveIndependentCapacity)
{
    TensorSram sram(4096, 4, 32, 32, 32, 32, 1, 1, 500, 1);
    ASSERT_TRUE(sram.tryReserve(1000, 0, 32, true));
    ASSERT_TRUE(sram.tryReserve(1000, 0, 32, false));
    EXPECT_FALSE(sram.tryReserve(1000, 0, 32, true));
    EXPECT_FALSE(sram.tryReserve(1000, 0, 32, false));
}

TEST(TensorSramTest, ReservationRejectsInvalidSpanAndZeroClock)
{
    TensorSram sram(4096, 4, 32, 32, 32, 32, 1, 1, 500, 1);
    EXPECT_ANY_THROW(sram.reserve(1000, UINT64_MAX, 32, true));
    EXPECT_ANY_THROW(sram.tryReserve(1000, 4096, 1, true));
    EXPECT_ANY_THROW(TensorSram(4096, 4, 32, 32, 32, 32, 1, 1, 0, 1));
}

TEST(TensorSramTest, RejectionAttemptsAreDirectionalAndQueriesArePassive)
{
    TensorSram sram(4096, 4, 32, 32, 32, 32, 1, 1, 500, 1);
    ASSERT_TRUE(sram.tryReserve(1000, 0, 32, true));
    EXPECT_FALSE(sram.canReserve(1000, 0, 32, true));
    EXPECT_FALSE(sram.canReserve(1000, 0, 32, true));
    EXPECT_EQ(sram.reservationRejectionAttempts(true), 0);
    EXPECT_FALSE(sram.tryReserve(1000, 0, 32, true));
    EXPECT_FALSE(sram.tryReserve(1000, 0, 32, true));
    EXPECT_EQ(sram.reservationRejectionAttempts(true), 2);
    EXPECT_EQ(sram.reservationRejectionAttempts(false), 0);
    ASSERT_TRUE(sram.tryReserve(1000, 0, 32, false));
    EXPECT_FALSE(sram.tryReserve(1000, 0, 32, false));
    EXPECT_EQ(sram.reservationRejectionAttempts(false), 1);
    const auto next = sram.tryReserve(1500, 0, 32, true);
    ASSERT_TRUE(next);
    EXPECT_EQ(next->stall_ticks, 500);
    EXPECT_EQ(next->conflict_ticks, 0);
    EXPECT_EQ(sram.reservationRejectionAttempts(true), 2);
}

TensorSram
makePartitionedSram(uint32_t metadata_entries)
{
    TensorSram sram = makeSram(1);
    sram.addPartition(mesh_abi::SramPartitionKind::STATIC_PROGRAM, 0x00000,
                      0x40000, 64, metadata_entries);
    sram.addPartition(mesh_abi::SramPartitionKind::WEIGHT_CACHE, 0x40000,
                      0x40000, 64, 0);
    sram.addPartition(mesh_abi::SramPartitionKind::RUNTIME_SCRATCH, 0x80000,
                      0x40000, 64, 0);
    return sram;
}

TEST(TensorSramPartitionTest, PartitionsAreDisjointAndInsideSram)
{
    auto sram = makePartitionedSram(4);
    EXPECT_TRUE(
        sram.hasPartition(mesh_abi::SramPartitionKind::STATIC_PROGRAM));
    EXPECT_EQ(sram.partition(mesh_abi::SramPartitionKind::WEIGHT_CACHE).base,
              0x40000u);
    EXPECT_ANY_THROW(sram.addPartition(
        mesh_abi::SramPartitionKind::KV_STAGING_CACHE, 0x20000, 0x40000, 64,
        0));
    EXPECT_ANY_THROW(sram.addPartition(
        mesh_abi::SramPartitionKind::KV_STAGING_CACHE, 0x1FF000, 0x2000, 64,
        0));
    EXPECT_ANY_THROW(sram.addPartition(
        mesh_abi::SramPartitionKind::KV_STAGING_CACHE, 0x100, 0x100, 0, 0));
}

TEST(TensorSramPartitionTest, LookupResolvesExactlyOnePartition)
{
    auto sram = makePartitionedSram(4);
    EXPECT_EQ(*sram.partitionOf(0x0, 64),
              mesh_abi::SramPartitionKind::STATIC_PROGRAM);
    EXPECT_EQ(*sram.partitionOf(0x7FFF0, 16),
              mesh_abi::SramPartitionKind::WEIGHT_CACHE);
    EXPECT_EQ(*sram.partitionOf(0x80000, 16),
              mesh_abi::SramPartitionKind::RUNTIME_SCRATCH);
    EXPECT_EQ(*sram.partitionOf(0xBFFF0, 16),
              mesh_abi::SramPartitionKind::RUNTIME_SCRATCH);
    EXPECT_FALSE(sram.partitionOf(0xC0000, 64).has_value());
    EXPECT_FALSE(sram.partitionOf(0x3FFF0, 64).has_value());
    EXPECT_FALSE(sram.partitionOf(0xBFFF0, 32).has_value());
}

TEST(TensorSramPartitionTest, AllocationMetadataIsBoundedPerPartition)
{
    auto sram = makePartitionedSram(2);
    EXPECT_TRUE(sram.registerAllocation(1, 0x00000, 0x1000));
    EXPECT_TRUE(sram.registerAllocation(2, 0x01000, 0x1000));
    EXPECT_FALSE(sram.registerAllocation(3, 0x02000, 0x1000));
    EXPECT_FALSE(sram.registerAllocation(1, 0x03000, 0x1000));
    EXPECT_EQ(sram.partitionAllocationCount(
                  mesh_abi::SramPartitionKind::STATIC_PROGRAM), 2u);
    EXPECT_TRUE(sram.registerAllocation(4, 0x40000, 0x1000));
    EXPECT_TRUE(sram.freeAllocation(1));
    EXPECT_TRUE(sram.registerAllocation(3, 0x02000, 0x1000));
}

TEST(TensorSramPartitionTest, AllocationOutsideAnyPartitionIsRejected)
{
    auto sram = makePartitionedSram(4);
    EXPECT_FALSE(sram.registerAllocation(1, 0xC0000, 0x1000));
    EXPECT_FALSE(sram.registerAllocation(2, 0x3FFF0, 0x100));
    EXPECT_FALSE(sram.registerAllocation(3, 0xBFFF0, 0x20));
    EXPECT_TRUE(sram.allocations.empty());
}

TEST(TensorSramPartitionTest, UnpartitionedSramKeepsLegacyRegistration)
{
    TensorSram sram = makeSram(1);
    EXPECT_TRUE(sram.registerAllocation(1, 0x1000, 0x1000));
    EXPECT_FALSE(sram.registerAllocation(1, 0x2000, 0x1000));
    EXPECT_FALSE(sram.registerAllocation(2, 0x1FFFF0, 0x20));
}

TEST(TensorSramPartitionTest, ReclaimRequiresNoPinAndNoRef)
{
    auto sram = makePartitionedSram(4);
    ASSERT_TRUE(sram.registerAllocation(1, 0x00000, 0x1000));
    EXPECT_TRUE(sram.reclaimable(1));
    ASSERT_TRUE(sram.acquireRef(1));
    EXPECT_FALSE(sram.reclaimable(1));
    EXPECT_FALSE(sram.freeAllocation(1));
    sram.allocations.at(1).pins = 1;
    ASSERT_TRUE(sram.releaseRef(1));
    EXPECT_FALSE(sram.reclaimable(1));
    sram.allocations.at(1).pins = 0;
    EXPECT_TRUE(sram.freeAllocation(1));
    EXPECT_FALSE(sram.releaseRef(1));
}

TEST(TensorSramPartitionTest, ViewsPinTheirBackingAllocation)
{
    auto sram = makePartitionedSram(4);
    ASSERT_TRUE(sram.registerAllocation(1, 0x00000, 0x1000));
    const TensorSram::ViewSlice rows[2] = {{0x0, 0x100}, {0x200, 0x100}};
    EXPECT_TRUE(sram.createView(7, 1, 2, rows));
    EXPECT_EQ(sram.view(7).rank, 2u);
    EXPECT_EQ(sram.view(7).slices[1].offset, 0x200u);
    EXPECT_EQ(sram.allocations.at(1).refcount, 1u);
    EXPECT_FALSE(sram.reclaimable(1));
    EXPECT_TRUE(sram.releaseView(7));
    EXPECT_TRUE(sram.reclaimable(1));
    EXPECT_FALSE(sram.releaseView(7));
}

TEST(TensorSramPartitionTest, ViewsRejectEscapesAndMalformedSlices)
{
    auto sram = makePartitionedSram(4);
    ASSERT_TRUE(sram.registerAllocation(1, 0x00000, 0x1000));
    const TensorSram::ViewSlice escape[1] = {{0x800, 0x900}};
    const TensorSram::ViewSlice zero_extent[1] = {{0x100, 0}};
    const TensorSram::ViewSlice ok[1] = {{0x100, 0x100}};
    EXPECT_FALSE(sram.createView(1, 1, 1, escape));
    EXPECT_FALSE(sram.createView(2, 1, 1, zero_extent));
    EXPECT_FALSE(sram.createView(3, 99, 1, ok));
    TensorSram::ViewSlice too_many[TensorSram::MAX_VIEW_RANK + 1];
    for (auto &slice : too_many)
        slice = {0x0, 0x10};
    EXPECT_FALSE(
        sram.createView(4, 1, TensorSram::MAX_VIEW_RANK + 1, too_many));
    EXPECT_EQ(sram.allocations.at(1).refcount, 0u);
    EXPECT_TRUE(sram.createView(5, 1, 1, ok));
    EXPECT_EQ(sram.allocations.at(1).refcount, 1u);
}

TEST(TensorSramPartitionTest, ViewTableIsBounded)
{
    auto sram = makePartitionedSram(4);
    sram.viewEntries = 1;
    ASSERT_TRUE(sram.registerAllocation(1, 0x00000, 0x1000));
    const TensorSram::ViewSlice ok[1] = {{0x0, 0x10}};
    EXPECT_TRUE(sram.createView(1, 1, 1, ok));
    EXPECT_FALSE(sram.createView(2, 1, 1, ok));
}

TEST(TensorSramPartitionTest, PartitionServiceBytesAreAccounted)
{
    auto sram = makePartitionedSram(4);
    sram.reserve(1000, 0x40000, 64, false);
    sram.reserve(1000, 0x40020, 32, true);
    EXPECT_EQ(sram.partition(mesh_abi::SramPartitionKind::WEIGHT_CACHE)
                  .readBytes, 64u);
    EXPECT_EQ(sram.partition(mesh_abi::SramPartitionKind::WEIGHT_CACHE)
                  .writeBytes, 32u);
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

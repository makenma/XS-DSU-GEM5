#include <gtest/gtest.h>

#include <cstdint>
#include <tuple>
#include <vector>

#include "dev/ai_mesh/generated/golden_splitter.inc"
#include "dev/ai_mesh/mesh_splitter.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(MeshSplitterTest, MatchesCrossLanguageGoldenVectors)
{
    for (const auto &item : golden::splitterCases()) {
        std::vector<AxiBurst> bursts;
        for (uint32_t row = 0; row < item.rows; row++)
            for (const auto &burst : splitBursts(
                     item.base + uint64_t(row) * item.stride, item.row_bytes, item.width,
                     item.max_beats))
                bursts.push_back(burst);
        ASSERT_EQ(bursts.size(), item.bursts.size()) << item.name;
        uint64_t beat_bytes = 0;
        uint64_t useful = 0;
        for (size_t i = 0; i < bursts.size(); i++) {
            EXPECT_EQ(std::make_tuple(bursts[i].beat_base, bursts[i].logical_start,
                                      bursts[i].useful_bytes, bursts[i].beats),
                      item.bursts[i])
                << item.name << " burst " << i;
            beat_bytes += uint64_t(bursts[i].beats) * item.width;
            useful += bursts[i].useful_bytes;
        }
        EXPECT_EQ(beat_bytes, item.beat_bytes) << item.name;
        EXPECT_EQ(useful, item.useful_bytes) << item.name;
    }
}

TEST(MeshSplitterTest, NeverCrosses4kBoundary)
{
    for (uint64_t base = 0xFD0; base < 0x1010; base += 7) {
        auto bursts = splitBursts(base, 257, 32, 16);
        for (const auto &burst : bursts) {
            EXPECT_EQ(burst.beat_base >> 12,
                      (burst.beat_base + uint64_t(burst.beats) * 32 - 1) >> 12);
            EXPECT_GE(burst.beats, 1u);
            EXPECT_LE(burst.beats, 16u);
        }
    }
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

static constexpr uint16_t kKindLoad = 1;   // DmaKind::LOAD
static constexpr uint16_t kKindStore = 2;  // DmaKind::STORE
static constexpr uint16_t kKindP2p = 3;    // DmaKind::P2P_PUSH
static constexpr uint16_t kKindPrefetch = 4; // DmaKind::PREFETCH

DecodedDmaDescriptor makeDescriptor(uint16_t kind, uint64_t src_off, uint64_t dst_off,
                                    uint64_t src_stride, uint64_t dst_stride,
                                    uint32_t rows, uint64_t row_bytes)
{
    DecodedDmaDescriptor descriptor;
    descriptor.kind = kind;
    descriptor.rows = rows;
    descriptor.row_bytes = row_bytes;
    descriptor.src_stride_bytes = src_stride;
    descriptor.dst_stride_bytes = dst_stride;
    descriptor.src.offset_bytes = src_off;
    descriptor.dst.offset_bytes = dst_off;
    descriptor.max_burst_beats = 16;
    return descriptor;
}

TEST(MeshSplitterRemoteTest, StoreUsesRemoteDestination)
{
    auto descriptor = makeDescriptor(kKindStore, 0x2000, 0x2ff0, 64, 64, 1, 64);
    DmaPlan plan = planDescriptorRemote(descriptor, 32);
    EXPECT_EQ(plan.bursts, 2u);
    EXPECT_EQ(plan.beats, 3u);
    // The legacy source-shaped plan disagrees: this pins the direction rule.
    DmaPlan legacy = planDescriptor(descriptor, 32);
    EXPECT_EQ(legacy.bursts, 1u);
    EXPECT_EQ(legacy.beats, 2u);
}

TEST(MeshSplitterRemoteTest, LoadUsesRemoteSource)
{
    auto descriptor = makeDescriptor(kKindLoad, 0x2ff0, 0x2000, 64, 64, 1, 64);
    DmaPlan plan = planDescriptorRemote(descriptor, 32);
    EXPECT_EQ(plan.bursts, 2u);
    EXPECT_EQ(plan.beats, 3u);
    auto prefetch = makeDescriptor(kKindPrefetch, 0x2ff0, 0x2000, 64, 64, 1, 64);
    EXPECT_EQ(planDescriptorRemote(prefetch, 32).bursts, 2u);
}

TEST(MeshSplitterRemoteTest, P2pUsesRemoteDestination)
{
    auto descriptor = makeDescriptor(kKindP2p, 0x2000, 0xff0, 64, 64, 1, 64);
    DmaPlan plan = planDescriptorRemote(descriptor, 32);
    EXPECT_EQ(plan.bursts, 2u);
}

TEST(MeshSplitterRemoteTest, StrideMismatchUsesRemoteStride)
{
    // rows=2: the write side walks dst_stride (128), not src_stride (64).
    auto descriptor = makeDescriptor(kKindStore, 0x0, 0x0, 64, 128, 2, 64);
    DmaPlan plan = planDescriptorRemote(descriptor, 32);
    // row0 [0,64) 2 beats, row1 [128,192) 2 beats -> 2 bursts, 4 beats.
    EXPECT_EQ(plan.bursts, 2u);
    EXPECT_EQ(plan.beats, 4u);
}

TEST(MeshSplitterRemoteTest, Remote4kEdges)
{
    struct Case
    {
        uint64_t base;
        uint32_t bursts;
        uint64_t beats;
    };
    // 64 useful bytes at width 32: 0xfff crosses the page (2 bursts, 3
    // beats); 0x1000 sits page-aligned (1 burst, 2 beats); 0x1001 starts
    // unaligned inside the page (1 burst, 3 beats).
    const Case cases[] = {{0xfffu, 2u, 3u}, {0x1000u, 1u, 2u}, {0x1001u, 1u, 3u}};
    for (const auto &item : cases) {
        auto descriptor = makeDescriptor(kKindStore, 0x2000, item.base, 64, 64, 1, 64);
        DmaPlan plan = planDescriptorRemote(descriptor, 32);
        EXPECT_EQ(plan.bursts, item.bursts) << "base " << item.base;
        EXPECT_EQ(plan.beats, item.beats) << "base " << item.base;
    }
}

TEST(DmaLatencyTest, FormulaMatchesSpecExample)
{
    // setup=2, burst_base_latency=7, 2 bursts, 3 beats -> 2 + 7*2 + 3 = 19.
    EXPECT_EQ(dmaTransferCycles(2, 7, 2, 3), 19u);
    EXPECT_EQ(dmaTransferCycles(0, 0, 0, 0), 0u);
    EXPECT_EQ(dmaTransferCycles(1, 1, 1, 1), 3u);
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

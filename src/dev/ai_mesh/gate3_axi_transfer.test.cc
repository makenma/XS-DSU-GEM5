#include <gtest/gtest.h>

#include <numeric>
#include <vector>

#include "dev/ai_mesh/gate3_axi_transfer.hh"
#include "mem/axi/axi_validation.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(Gate3AxiTransferPlannerTest, SplitsAtFourKiBWithoutOverfetch)
{
    Gate3AxiTransferPlanner planner(64, 256);
    const auto segments = planner.plan(0x10010fc0, 256, 12, 3);
    ASSERT_EQ(segments.size(), 2u);
    EXPECT_EQ(segments[0].request.address, 0x10010fc0u);
    EXPECT_EQ(segments[0].request.size, 6u);
    EXPECT_EQ(segments[0].request.beatCount, 1u);
    EXPECT_EQ(segments[0].logicalBytes, 64u);
    EXPECT_EQ(segments[1].request.address, 0x10011000u);
    EXPECT_EQ(segments[1].request.size, 6u);
    EXPECT_EQ(segments[1].request.beatCount, 3u);
    EXPECT_EQ(segments[1].logicalBytes, 192u);
    for (const auto &segment : segments)
        EXPECT_TRUE(axi::validateAxiBurst(segment.request, 64).okay());
    EXPECT_EQ(segments.front().request.address, 0x10010fc0u);
    EXPECT_EQ(
        segments.back().request.address + segments.back().logicalBytes,
        0x100110c0u);
}

TEST(Gate3AxiTransferPlannerTest, UsesAbsoluteReadLanes)
{
    Gate3AxiTransferPlanner planner(64, 256);
    const auto segments = planner.plan(0x10010fc0, 256, 12, 0, 32);
    ASSERT_EQ(segments.size(), 2u);
    std::vector<uint8_t> actual;
    for (const auto &segment : segments) {
        for (uint16_t beat = 0; beat < segment.request.beatCount; ++beat) {
            std::vector<uint8_t> word(64, 0xee);
            const uint64_t address = axi::axiBeatAddress(segment.request, beat);
            const uint64_t lane = address % 64;
            for (uint64_t byte = 0; byte < 32; ++byte)
                word[lane + byte] = static_cast<uint8_t>(
                    address + byte - 0x10010fc0);
            planner.appendReadBeat(segment, beat, word, actual);
        }
    }
    ASSERT_EQ(actual.size(), 256u);
    for (size_t index = 0; index < actual.size(); ++index)
        EXPECT_EQ(actual[index], static_cast<uint8_t>(index));
}

TEST(Gate3AxiTransferPlannerTest, ExtractsEveryNarrowBeatWidthByAddress)
{
    Gate3AxiTransferPlanner planner(64, 2);
    for (const uint32_t beatLimit : {8u, 16u, 32u}) {
        const uint64_t base = 0x1020 + beatLimit;
        const auto segments = planner.plan(base, 128, 12, 0, beatLimit);
        ASSERT_GT(segments.size(), 1u);
        std::vector<uint8_t> actual;
        for (const auto &segment : segments) {
            EXPECT_LE(segment.request.beatCount, 2u);
            for (uint16_t beat = 0; beat < segment.request.beatCount; ++beat) {
                std::vector<uint8_t> word(64, 0xee);
                const uint64_t address =
                    axi::axiBeatAddress(segment.request, beat);
                const uint64_t lane = address % 64;
                const uint64_t beatBytes =
                    uint64_t{1} << segment.request.size;
                for (uint64_t byte = 0; byte < beatBytes; ++byte) {
                    word[lane + byte] = static_cast<uint8_t>(
                        address + byte - base);
                }
                planner.appendReadBeat(segment, beat, word, actual);
            }
        }
        ASSERT_EQ(actual.size(), 128u);
        for (size_t index = 0; index < actual.size(); ++index)
            EXPECT_EQ(actual[index], static_cast<uint8_t>(index));
    }
}

TEST(Gate3AxiTransferPlannerTest, PacksCqSlotOneIntoUpperBusLanes)
{
    Gate3AxiTransferPlanner planner(64, 256);
    std::vector<uint8_t> bytes(32);
    std::iota(bytes.begin(), bytes.end(), 1);
    const auto segments = planner.plan(0x10050020, 32, 20, 0);
    ASSERT_EQ(segments.size(), 1u);
    const auto beats = planner.packWrite(bytes, segments[0]);
    ASSERT_EQ(beats.size(), 1u);
    EXPECT_EQ(segments[0].request.address, 0x10050020u);
    EXPECT_EQ(segments[0].request.size, 5u);
    EXPECT_EQ(beats[0].byteStrobe, 0xffffffff00000000ULL);
    EXPECT_EQ(
        std::vector<uint8_t>(beats[0].functionalData.begin() + 32,
                             beats[0].functionalData.end()),
        bytes);
}

TEST(Gate3AxiTransferPlannerTest, CoversUnalignedExactRange)
{
    Gate3AxiTransferPlanner planner(64, 4);
    const auto segments = planner.plan(0x1008, 117, 7, 2);
    uint64_t next = 0x1008;
    uint64_t total = 0;
    for (const auto &segment : segments) {
        EXPECT_EQ(segment.request.address, next);
        EXPECT_TRUE(axi::validateAxiBurst(segment.request, 64).okay());
        EXPECT_LE(segment.request.beatCount, 4u);
        next += segment.logicalBytes;
        total += segment.logicalBytes;
    }
    EXPECT_EQ(total, 117u);
    EXPECT_EQ(next, 0x107du);
}

}
}
}

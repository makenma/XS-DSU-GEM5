#include "arch/riscv/instruction_bytes.hh"

#include <gtest/gtest.h>

#include <random>
#include <vector>

namespace gem5::RiscvISA
{

TEST(RiscvInstructionBytes, ActualPCFetchRetainsExistingBehavior)
{
    InstructionBytes bytes;
    for (auto pc : {0x1000ULL, 0x1002ULL}) {
        const auto word = bytes.feed(pc, pc, 0x00100293);
        EXPECT_EQ(word.bits, 0x00100293);
        EXPECT_TRUE(word.ready);
        EXPECT_TRUE(word.exhausted);
        const auto compressed = bytes.feed(pc, pc, 0x45010001);
        EXPECT_EQ(compressed.bits, 1);
        EXPECT_TRUE(compressed.ready);
        EXPECT_FALSE(compressed.exhausted);
    }
}

TEST(RiscvInstructionBytes, SelectsUpperCompressedHalf)
{
    InstructionBytes bytes;
    const auto low = bytes.feed(0x1000, 0x1000, 0x4501a009);
    EXPECT_EQ(low.bits, 0xa009);
    EXPECT_FALSE(low.exhausted);
    const auto high = bytes.feed(0x1002, 0x1000, 0x4501a009);
    EXPECT_EQ(high.bits, 0x4501);
    EXPECT_TRUE(high.ready);
    EXPECT_TRUE(high.exhausted);
}

TEST(RiscvInstructionBytes, JoinsCrossWordAndRetainsUnusedHalf)
{
    InstructionBytes bytes;
    const auto first = bytes.feed(0x103e, 0x103c, 0x02930001);
    EXPECT_FALSE(first.ready);
    EXPECT_TRUE(first.exhausted);
    const auto second = bytes.feed(0x103e, 0x1040, 0x45010010);
    EXPECT_EQ(second.bits, 0x00100293);
    EXPECT_TRUE(second.ready);
    EXPECT_FALSE(second.exhausted);
    const auto next = bytes.feed(0x1042, 0x1040, 0x45010010);
    EXPECT_EQ(next.bits, 0x4501);
    EXPECT_TRUE(next.ready);
    EXPECT_TRUE(next.exhausted);
}

TEST(RiscvInstructionBytes, ResetOrRedirectDiscardsPartialInstruction)
{
    InstructionBytes bytes;
    EXPECT_FALSE(bytes.feed(0x1002, 0x1000, 0x02930001).ready);
    bytes.reset();
    EXPECT_EQ(bytes.feed(0x2002, 0x2000, 0x45010001).bits, 0x4501);
    EXPECT_FALSE(bytes.feed(0x1002, 0x1000, 0x02930001).ready);
    EXPECT_EQ(bytes.feed(0x3002, 0x3002, 0x00100293).bits, 0x00100293);
}

TEST(RiscvInstructionBytes, MixedStreamMatchesAllThreeFetchConventions)
{
    std::mt19937 random(0xcedec0de);
    std::vector<uint8_t> memory;
    std::vector<uint32_t> expected;
    for (unsigned i = 0; i < 20000; ++i) {
        const bool compressed = random() & 1;
        const uint32_t instruction = compressed ? (random() & 0xfffc) | 1 :
            (random() & ~uint32_t(3)) | 3;
        expected.push_back(instruction);
        for (unsigned byte = 0; byte < (compressed ? 2 : 4); ++byte)
            memory.push_back(instruction >> (byte * 8));
    }
    memory.resize(memory.size() + 4);
    auto word = [&](uint64_t pc) {
        uint32_t value = 0;
        for (unsigned byte = 0; byte < 4; ++byte)
            value |= uint32_t(memory.at(pc + byte)) << (8 * byte);
        return value;
    };

    InstructionBytes actualPC, simple, minor;
    uint64_t pc = 0, minorFetch = 0;
    for (const uint32_t instruction : expected) {
        const auto actual = actualPC.feed(pc, pc, word(pc));
        ASSERT_TRUE(actual.ready);
        ASSERT_EQ(actual.bits, instruction);
        uint64_t simpleFetch = pc & ~uint64_t(3);
        auto aligned = simple.feed(pc, simpleFetch, word(simpleFetch));
        if (!aligned.ready) {
            simpleFetch += 4;
            aligned = simple.feed(pc, simpleFetch, word(simpleFetch));
        }
        ASSERT_TRUE(aligned.ready);
        ASSERT_EQ(aligned.bits, instruction);
        auto retained = minor.feed(pc, minorFetch, word(minorFetch));
        if (retained.exhausted) minorFetch += 4;
        if (!retained.ready) {
            retained = minor.feed(pc, minorFetch, word(minorFetch));
            if (retained.exhausted) minorFetch += 4;
        }
        ASSERT_TRUE(retained.ready);
        ASSERT_EQ(retained.bits, instruction);
        pc += (instruction & 3) == 3 ? 4 : 2;
    }
}

} // namespace gem5::RiscvISA

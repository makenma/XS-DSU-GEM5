#include <gtest/gtest.h>

#include <cstdint>

#include "dev/ai_mesh/cancel_join.hh"
#include "dev/ai_mesh/session_record_table.hh"

TEST(SessionRecordTable, AdmitsErasesAndCountsTombstones)
{
    gem5::ai_mesh::SessionRecordTable table(4);
    EXPECT_EQ(table.size(), 0u);
    EXPECT_TRUE(table.insert(10, 10, 1));
    EXPECT_FALSE(table.insert(10, 10, 2));
    EXPECT_EQ(table.size(), 1u);

    const gem5::ai_mesh::SessionRecord *found = table.find(10, 10);
    ASSERT_NE(found, nullptr);
    EXPECT_EQ(found->generation, 1u);
    EXPECT_EQ(table.find(10, 11), nullptr);
    EXPECT_EQ(table.find(999, 999), nullptr);

    EXPECT_TRUE(table.erase(10, 10));
    EXPECT_FALSE(table.erase(10, 10));
    EXPECT_EQ(table.size(), 0u);
    EXPECT_EQ(table.tombstones(), 1u);
    EXPECT_TRUE(table.insert(10, 10, 2));
    EXPECT_EQ(table.find(10, 10)->generation, 2u);
}

TEST(SessionRecordTable, CapacityBoundsInsertions)
{
    gem5::ai_mesh::SessionRecordTable table(2);
    EXPECT_TRUE(table.insert(1, 1, 1));
    EXPECT_TRUE(table.insert(2, 2, 1));
    EXPECT_FALSE(table.insert(3, 3, 1));
    EXPECT_TRUE(table.erase(1, 1));
    EXPECT_TRUE(table.insert(3, 3, 1));
    EXPECT_EQ(table.size(), 2u);
    EXPECT_EQ(table.tombstones(), 1u);
}

TEST(CancelJoin, WinnerMatrix)
{
    namespace abi = gem5::ai_mesh::agent_abi;
    EXPECT_STREQ(gem5::ai_mesh::cancelJoinWinner(
                     abi::kCqStatusSUCCESS, abi::kCqStatusCANCELLED),
                 "CANCEL_WINS");
    EXPECT_STREQ(gem5::ai_mesh::cancelJoinWinner(
                     abi::kCqStatusALREADY_TERMINAL, abi::kCqStatusSUCCESS),
                 "TARGET_SUCCESS_WINS");
    EXPECT_STREQ(gem5::ai_mesh::cancelJoinWinner(
                     abi::kCqStatusALREADY_TERMINAL,
                     abi::kCqStatusPROGRAM_ERROR),
                 "TARGET_ERROR_WINS");
    EXPECT_EQ(gem5::ai_mesh::cancelJoinWinner(
                  abi::kCqStatusSUCCESS, abi::kCqStatusSUCCESS), nullptr);
    EXPECT_EQ(gem5::ai_mesh::cancelJoinWinner(
                  abi::kCqStatusNOT_FOUND, abi::kCqStatusCANCELLED), nullptr);
    EXPECT_EQ(gem5::ai_mesh::cancelJoinWinner(
                  abi::kCqStatusSUCCESS, abi::kCqStatusPROGRAM_ERROR),
              nullptr);
    EXPECT_EQ(gem5::ai_mesh::cancelJoinWinner(
                  abi::kCqStatusALREADY_TERMINAL, abi::kCqStatusCANCELLED),
              nullptr);
}

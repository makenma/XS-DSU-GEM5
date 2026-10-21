#include <gtest/gtest.h>
#include <algorithm>
#include <array>

#include "dev/ai_mesh/instance_failure_reducer.hh"

using namespace gem5::ai_mesh;

TEST(InstanceFailureReducer, EveryPermutationChoosesTheSameTypedFullSource)
{
    std::array<ErrorCandidate, 4> errors{};
    for (size_t i = 0; i < errors.size(); ++i) {
        errors[i].tick = 20;
        errors[i].code = 43;
        errors[i].source.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
        errors[i].source.core_id_or_ffff = 256;
        errors[i].source.ordinal = i + 1;
    }
    errors[0].source.core_id_or_ffff = 255;
    errors[1].source.error_class = mesh_abi::kMoeErrorClassWEIGHT_FILL;
    errors[2].source.error_class = mesh_abi::kMoeErrorClassWEIGHT_FILL;
    errors[1].source.ordinal = errors[2].source.ordinal = 0;
    errors[1].source.aux_key[15] = 2;
    errors[2].source.aux_key[15] = 1;
    std::array<size_t, 4> order{0, 1, 2, 3};
    do {
        InstanceFailureReducer reducer(1, 3);
        ASSERT_TRUE(reducer.begin(8, 9, {11, 12, 13}));
        ASSERT_TRUE(reducer.cancel(9, 11, 19));
        ASSERT_TRUE(reducer.commitEdge(19));
        for (const auto i : order)
            ASSERT_TRUE(reducer.stage(9, errors[i]));
        ASSERT_TRUE(reducer.cancel(9, 12, 20));
        EXPECT_FALSE(reducer.failure(9));
        ASSERT_TRUE(reducer.commitEdge(20));
        const auto &record = *reducer.failure(9);
        EXPECT_EQ(record.batch_id, 8u);
        EXPECT_EQ(record.program_instance_id, 9u);
        EXPECT_EQ(record.first.source.aux_key[15], 1u);
        EXPECT_EQ(record.cancel_tombstone_snapshot, std::vector<uint64_t>({11}));
        EXPECT_EQ(reducer.firstEdgeCandidateCount(9), 4u);
        auto later = errors[2];
        later.tick = 21;
        later.source.aux_key[15] = 0;
        ASSERT_TRUE(reducer.stage(9, later));
        ASSERT_TRUE(reducer.commitEdge(21));
        EXPECT_EQ(reducer.failure(9)->first.source.aux_key[15], 1u);
        EXPECT_EQ(reducer.firstEdgeCandidateCount(9), 4u);
    } while (std::next_permutation(order.begin(), order.end()));
}

TEST(InstanceFailureReducer, BoundedMembershipAndEdgeOrderAreTransactional)
{
    InstanceFailureReducer reducer(1, 2);
    EXPECT_FALSE(reducer.begin(1, 1, {1, 2, 3}));
    EXPECT_FALSE(reducer.begin(1, 1, {1, 1}));
    ASSERT_TRUE(reducer.begin(1, 1, {1, 2}));
    EXPECT_FALSE(reducer.begin(2, 2, {3}));
    EXPECT_FALSE(reducer.cancel(1, 3, 4));
    ASSERT_TRUE(reducer.cancel(1, 1, 4));
    ASSERT_TRUE(reducer.commitEdge(4));
    ErrorCandidate error;
    error.tick = 4;
    EXPECT_FALSE(reducer.stage(1, error));
    EXPECT_FALSE(reducer.commitEdge(3));
    reducer.release(1);
    ASSERT_TRUE(reducer.begin(2, 2, {3}));
    EXPECT_FALSE(reducer.failure(2));
    EXPECT_EQ(reducer.firstEdgeCandidateCount(2), 0u);
}

TEST(InstanceFailureReducer, NumericCoreOrderingDoesNotUseLittleEndianWireOrder)
{
    ErrorCandidate first, second;
    first.source.core_id_or_ffff = 255;
    second.source.core_id_or_ffff = 256;
    EXPECT_TRUE(errorCandidateLess(first, second));
    EXPECT_FALSE(errorCandidateLess(second, first));
    EXPECT_GT(errorSourceKeyBytes(first.source), errorSourceKeyBytes(second.source));
}

TEST(InstanceFailureReducer, SharedPhysicalFaultSealsActiveAndQueuedOwnersTogether)
{
    InstanceFailureReducer reducer(2, 2);
    ASSERT_TRUE(reducer.begin(1, 10, {11, 12}));
    ASSERT_TRUE(reducer.begin(2, 20, {21}));
    EXPECT_FALSE(reducer.begin(3, 30, {31}));
    ASSERT_TRUE(reducer.cancel(10, 11, 4));
    ErrorCandidate physical;
    physical.tick = 5;
    physical.code = agent_abi::E_AXI_RESPONSE;
    physical.source.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
    physical.source.core_id_or_ffff = 256;
    auto competing = physical;
    competing.source.core_id_or_ffff = 257;
    ASSERT_TRUE(reducer.stage(20, competing));
    ASSERT_TRUE(reducer.stage(10, physical));
    ASSERT_TRUE(reducer.stage(20, physical));
    EXPECT_FALSE(reducer.failure(10));
    EXPECT_FALSE(reducer.failure(20));
    ASSERT_TRUE(reducer.commitEdge(5));
    ASSERT_TRUE(reducer.failure(10));
    ASSERT_TRUE(reducer.failure(20));
    EXPECT_EQ(errorSourceKeyBytes(reducer.failure(10)->first.source),
              errorSourceKeyBytes(reducer.failure(20)->first.source));
    EXPECT_EQ(reducer.failure(10)->cancel_tombstone_snapshot, std::vector<uint64_t>{11});
    EXPECT_TRUE(reducer.failure(20)->cancel_tombstone_snapshot.empty());
    reducer.release(10);
    EXPECT_FALSE(reducer.failure(10));
    ASSERT_TRUE(reducer.failure(20));
    ASSERT_TRUE(reducer.begin(3, 30, {31}));
}

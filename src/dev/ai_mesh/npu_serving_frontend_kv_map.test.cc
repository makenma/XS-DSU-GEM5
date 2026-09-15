#include <gtest/gtest.h>

#include <cstdint>
#include <optional>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"
#include "dev/ai_mesh/npu_serving_frontend_kv_map.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(KvAdmissionMap, MixedFlagsAreAParameterError)
{
    const KvAdmissionMapping mapping = mapKvAdmissionOutcome(
        KvAdmissionOutcome::FlagCombination, std::nullopt);
    EXPECT_FALSE(mapping.success);
    EXPECT_FALSE(mapping.admitted);
    EXPECT_FALSE(mapping.protocol_error);
    EXPECT_EQ(mapping.detail, agent_abi::E_KV_FLAG_COMBINATION);
    const std::optional<agent_abi::DetailDispositionV1> disposition =
        agent_abi::detailDispositionV1(mapping.detail);
    ASSERT_TRUE(disposition.has_value());
    EXPECT_EQ(disposition->cqStatus,
              static_cast<int32_t>(agent_abi::CqStatus::PARAM_ERROR));
}

TEST(KvAdmissionMap, ErrorRecordsStayProgramErrors)
{
    const KvAdmissionMapping mapping = mapKvAdmissionOutcome(
        KvAdmissionOutcome::KvState, std::nullopt);
    EXPECT_FALSE(mapping.success);
    EXPECT_FALSE(mapping.admitted);
    EXPECT_FALSE(mapping.protocol_error);
    EXPECT_EQ(mapping.detail, agent_abi::E_KV_STATE);
    const std::optional<agent_abi::DetailDispositionV1> disposition =
        agent_abi::detailDispositionV1(mapping.detail);
    ASSERT_TRUE(disposition.has_value());
    EXPECT_EQ(disposition->cqStatus,
              static_cast<int32_t>(agent_abi::CqStatus::PROGRAM_ERROR));
}

TEST(KvAdmissionMap, SessionOutcomesUseTheAbiDetails)
{
    EXPECT_EQ(mapKvAdmissionOutcome(KvAdmissionOutcome::SessionExists,
                                    std::nullopt).detail,
              agent_abi::E_SESSION_EXISTS);
    EXPECT_EQ(mapKvAdmissionOutcome(KvAdmissionOutcome::SessionNotFound,
                                    std::nullopt).detail,
              agent_abi::E_KV_SESSION_NOT_FOUND);
    EXPECT_EQ(mapKvAdmissionOutcome(KvAdmissionOutcome::StaleGeneration,
                                    std::nullopt).detail,
              agent_abi::E_KV_STALE_GENERATION);
    EXPECT_EQ(mapKvAdmissionOutcome(KvAdmissionOutcome::ContractMismatch,
                                    std::nullopt).detail,
              agent_abi::E_KV_CONTRACT_MISMATCH);
}

TEST(KvAdmissionMap, ClaimedOutcomesReportPromotionDetails)
{
    const KvAdmissionMapping pinned = mapKvAdmissionOutcome(
        KvAdmissionOutcome::Claimed, KvPromotionOutcome::Pinned);
    EXPECT_TRUE(pinned.success);
    EXPECT_TRUE(pinned.admitted);
    EXPECT_FALSE(pinned.protocol_error);

    const KvAdmissionMapping reuse = mapKvAdmissionOutcome(
        KvAdmissionOutcome::Claimed, KvPromotionOutcome::ReuseRequired);
    EXPECT_TRUE(reuse.admitted);
    EXPECT_FALSE(reuse.success);
    EXPECT_EQ(reuse.detail, agent_abi::E_KV_REUSE_REQUIRED);
    EXPECT_EQ(agent_abi::detailDispositionV1(reuse.detail)->cqStatus,
              static_cast<int32_t>(agent_abi::CqStatus::PROFILE_ERROR));

    const KvAdmissionMapping mismatch = mapKvAdmissionOutcome(
        KvAdmissionOutcome::Claimed, KvPromotionOutcome::TokenMismatch);
    EXPECT_TRUE(mismatch.admitted);
    EXPECT_EQ(mismatch.detail, agent_abi::E_KV_TOKEN_MISMATCH);

    const KvAdmissionMapping waiting = mapKvAdmissionOutcome(
        KvAdmissionOutcome::Claimed, KvPromotionOutcome::WaitingSlot);
    EXPECT_TRUE(waiting.admitted);
    EXPECT_FALSE(waiting.success);
    EXPECT_EQ(waiting.detail, agent_abi::E_KV_STATE);
}

TEST(KvAdmissionMap, UnhandledOutcomesStayProtocolErrors)
{
    for (KvAdmissionOutcome outcome : {KvAdmissionOutcome::Waiting,
                                       KvAdmissionOutcome::Backpressure}) {
        const KvAdmissionMapping mapping =
            mapKvAdmissionOutcome(outcome, std::nullopt);
        EXPECT_TRUE(mapping.protocol_error);
        EXPECT_FALSE(mapping.success);
        EXPECT_FALSE(mapping.admitted);
    }
}

}
}
}

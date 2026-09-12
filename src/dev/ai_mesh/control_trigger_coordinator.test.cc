#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <optional>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/control_trigger_coordinator.hh"

namespace
{

void
pushU16(std::vector<uint8_t> &data, uint16_t value)
{
    data.push_back(static_cast<uint8_t>(value));
    data.push_back(static_cast<uint8_t>(value >> 8));
}

void
pushU32(std::vector<uint8_t> &data, uint32_t value)
{
    for (size_t index = 0; index < 4; ++index)
        data.push_back(static_cast<uint8_t>(value >> (8 * index)));
}

void
pushU64(std::vector<uint8_t> &data, uint64_t value)
{
    for (size_t index = 0; index < 8; ++index)
        data.push_back(static_cast<uint8_t>(value >> (8 * index)));
}

struct ActionSpec
{
    uint32_t controlOrdinal;
    uint8_t opcode;
    uint8_t triggerKind;
    bool hasAnchor;
    uint32_t anchorUser;
    uint32_t anchorTask;
    uint16_t anchorRound;
    bool hasAfterOrdinal;
    uint32_t afterControlOrdinal;
};

std::vector<uint8_t>
controlPayload(const std::vector<ActionSpec> &actions)
{
    std::vector<uint8_t> data;
    pushU32(data, static_cast<uint32_t>(actions.size()));
    for (const ActionSpec &action : actions) {
        pushU32(data, action.controlOrdinal);
        data.push_back(action.opcode);
        data.push_back(action.triggerKind);
        data.push_back(action.hasAnchor ? 1 : 0);
        pushU32(data, action.anchorUser);
        pushU32(data, action.anchorTask);
        pushU16(data, action.anchorRound);
        data.push_back(action.hasAfterOrdinal ? 1 : 0);
        pushU32(data, action.afterControlOrdinal);
        pushU32(data, 1);
        pushU32(data, 0);
        pushU16(data, 0);
        pushU32(data, 0);
        pushU16(data, 0);
        pushU64(data, 0);
        pushU64(data, 0);
        pushU32(data, 0);
    }
    return data;
}

std::optional<gem5::ai_mesh::AgentPlanImage>
buildImage(const std::vector<ActionSpec> &actions)
{
    std::vector<uint8_t> body;
    body.push_back('A');
    body.push_back('G');
    body.push_back('P');
    body.push_back('I');
    pushU32(body, 1);
    pushU32(body, 5);
    body.insert(body.end(), 32, 0);
    body.insert(body.end(), 32, 0);
    const std::vector<uint8_t> emptyCount(4, 0);
    for (const uint16_t type : {uint16_t{1}, uint16_t{2}, uint16_t{3},
                                uint16_t{4}}) {
        pushU16(body, type);
        pushU64(body, emptyCount.size());
        body.insert(body.end(), emptyCount.begin(), emptyCount.end());
    }
    const std::vector<uint8_t> payload = controlPayload(actions);
    pushU16(body, 6);
    pushU64(body, payload.size());
    body.insert(body.end(), payload.begin(), payload.end());
    const std::array<uint8_t, 32> digest = gem5::ai_mesh::agentSha256(body);
    body.insert(body.end(), digest.begin(), digest.end());
    return gem5::ai_mesh::AgentPlanImage::parse(body.data(), body.size());
}

const ActionSpec kCancelAfterSqAccept{1, 2, 1, true, 0, 0, 0, false, 0};
const ActionSpec kReleaseScenarioStart{2, 1, 0, false, 0, 0, 0, false, 0};
const ActionSpec kCancelSameEdge{3, 2, 3, true, 1, 0, 0, false, 0};
const ActionSpec kCancelAfterGenerate{4, 2, 4, true, 1, 0, 0, false, 0};
const ActionSpec kReleaseAfterSq{5, 1, 1, true, 2, 0, 1, false, 0};

}

TEST(ControlTriggerCoordinator, LoadsImplementedKindsAndLifecycle)
{
    const auto image = buildImage({kCancelAfterSqAccept, kReleaseScenarioStart,
                                   kCancelSameEdge, kCancelAfterGenerate,
                                   kReleaseAfterSq});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(8);
    ASSERT_TRUE(coordinator.loadFromImage(*image));
    EXPECT_EQ(coordinator.size(), 5u);
    EXPECT_FALSE(coordinator.allTerminal());

    const gem5::ai_mesh::ControlAnchor anchor{0, 0, 0};
    const auto ready = coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::AfterSqAccept, anchor, 0, 10);
    ASSERT_EQ(ready.size(), 1u);
    EXPECT_EQ(coordinator.state(0), gem5::ai_mesh::ControlRecordState::Ready);
    EXPECT_TRUE(coordinator.markMaterialized(0));
    EXPECT_EQ(coordinator.state(0),
              gem5::ai_mesh::ControlRecordState::Materialized);
    EXPECT_FALSE(coordinator.markControlTerminal(1));
    EXPECT_TRUE(coordinator.markControlTerminal(0));
    EXPECT_EQ(coordinator.state(0),
              gem5::ai_mesh::ControlRecordState::ControlTerminal);
    EXPECT_FALSE(coordinator.markMaterialized(0));
    EXPECT_FALSE(coordinator.allTerminal());
}

TEST(ControlTriggerCoordinator, RejectsUnimplementedTriggerKinds)
{
    for (const uint8_t kind : {uint8_t{5}, uint8_t{6}, uint8_t{7}}) {
        const auto image = buildImage({{1, 1, kind, true, 0, 0, 0, false, 0}});
        ASSERT_TRUE(image.has_value());
        gem5::ai_mesh::ControlTriggerCoordinator coordinator(4);
        EXPECT_FALSE(coordinator.loadFromImage(*image)) << "kind " << int(kind);
    }
}

TEST(ControlTriggerCoordinator, CapacityBoundsTheRecordArray)
{
    const auto image = buildImage({kCancelAfterSqAccept, kReleaseScenarioStart});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(1);
    EXPECT_FALSE(coordinator.loadFromImage(*image));
    gem5::ai_mesh::ControlTriggerCoordinator exact(2);
    EXPECT_TRUE(exact.loadFromImage(*image));
}

TEST(ControlTriggerCoordinator, ScenarioStartDeliversNextEdge)
{
    const auto image = buildImage({kReleaseScenarioStart});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(4);
    ASSERT_TRUE(coordinator.loadFromImage(*image));
    const gem5::ai_mesh::ControlAnchor anchor{};
    const auto ready = coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::ScenarioStart, anchor, 0, 100);
    ASSERT_EQ(ready.size(), 1u);
    EXPECT_EQ(ready[0], 0u);
    EXPECT_TRUE(coordinator.due(100).empty());
    ASSERT_EQ(coordinator.due(101).size(), 1u);
    const auto again = coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::ScenarioStart, anchor, 0, 105);
    EXPECT_TRUE(again.empty());
}

TEST(ControlTriggerCoordinator, NextDueEdgeTracksEarliestReadyRecord)
{
    const auto image = buildImage(
        {kCancelAfterSqAccept, kCancelSameEdge, kCancelAfterGenerate});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(4);
    ASSERT_TRUE(coordinator.loadFromImage(*image));
    EXPECT_EQ(coordinator.nextDueEdge(), std::nullopt);
    const gem5::ai_mesh::ControlAnchor one{1, 0, 0};
    ASSERT_EQ(coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::SameEdgeAsTerminal, one, 0,
        50).size(), 1u);
    EXPECT_EQ(coordinator.nextDueEdge(), 50u);
    const gem5::ai_mesh::ControlAnchor zero{0, 0, 0};
    ASSERT_EQ(coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::AfterSqAccept, zero, 0,
        70).size(), 1u);
    EXPECT_EQ(coordinator.nextDueEdge(), 50u);
    ASSERT_TRUE(coordinator.markMaterialized(
        *coordinator.due(50).begin()));
    EXPECT_EQ(coordinator.nextDueEdge(), 71u);
}

TEST(ControlTriggerCoordinator, AnchoredEventMatchesAnchorExactly)
{
    const auto image = buildImage({kCancelAfterSqAccept});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(4);
    ASSERT_TRUE(coordinator.loadFromImage(*image));
    gem5::ai_mesh::ControlAnchor wrong{0, 0, 1};
    EXPECT_TRUE(coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::AfterSqAccept, wrong, 0,
        10).empty());
    gem5::ai_mesh::ControlAnchor right{0, 0, 0};
    const auto ready = coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::AfterSqAccept, right, 0, 10);
    ASSERT_EQ(ready.size(), 1u);
    EXPECT_TRUE(coordinator.due(10).empty());
    EXPECT_EQ(coordinator.due(11).size(), 1u);
}

TEST(ControlTriggerCoordinator, SameEdgeDeliversAtEventEdge)
{
    const auto image = buildImage({kCancelSameEdge});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(4);
    ASSERT_TRUE(coordinator.loadFromImage(*image));
    const gem5::ai_mesh::ControlAnchor anchor{1, 0, 0};
    const auto ready = coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::SameEdgeAsTerminal, anchor, 0, 50);
    ASSERT_EQ(ready.size(), 1u);
    EXPECT_EQ(coordinator.due(50).size(), 1u);
}

TEST(ControlTriggerCoordinator, AllTerminalRequiresEveryRecordTerminal)
{
    const auto image = buildImage({kReleaseScenarioStart, kCancelAfterSqAccept});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(4);
    ASSERT_TRUE(coordinator.loadFromImage(*image));
    const gem5::ai_mesh::ControlAnchor anchor{0, 0, 0};
    coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::ScenarioStart, anchor, 0, 0);
    coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::AfterSqAccept, anchor, 0, 0);
    EXPECT_TRUE(coordinator.markMaterialized(0));
    EXPECT_TRUE(coordinator.markControlTerminal(0));
    EXPECT_FALSE(coordinator.allTerminal());
    EXPECT_TRUE(coordinator.markMaterialized(1));
    EXPECT_FALSE(coordinator.allTerminal());
    EXPECT_TRUE(coordinator.markControlTerminal(1));
    EXPECT_TRUE(coordinator.allTerminal());
}

TEST(ControlTriggerCoordinator, SuppressionTerminatesWaitingRecords)
{
    const auto image = buildImage({kCancelAfterSqAccept, kReleaseScenarioStart});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(4);
    ASSERT_TRUE(coordinator.loadFromImage(*image));
    EXPECT_TRUE(coordinator.markSuppressedByRunCutoff(0));
    EXPECT_EQ(coordinator.state(0),
              gem5::ai_mesh::ControlRecordState::SuppressedByRunCutoff);
    EXPECT_FALSE(coordinator.allTerminal());
    EXPECT_FALSE(coordinator.markSuppressedByRunCutoff(0));
    EXPECT_TRUE(coordinator.markSuppressedByRunCutoff(1));
    EXPECT_TRUE(coordinator.allTerminal());
    EXPECT_TRUE(coordinator.due(UINT64_MAX).empty());
    const gem5::ai_mesh::ControlAnchor anchor{0, 0, 0};
    EXPECT_TRUE(coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::AfterSqAccept, anchor, 0,
        10).empty());
    EXPECT_EQ(coordinator.state(0),
              gem5::ai_mesh::ControlRecordState::SuppressedByRunCutoff);
}

TEST(ControlTriggerCoordinator, SuppressionRejectsReadyRecords)
{
    const auto image = buildImage({kCancelAfterSqAccept});
    ASSERT_TRUE(image.has_value());
    gem5::ai_mesh::ControlTriggerCoordinator coordinator(4);
    ASSERT_TRUE(coordinator.loadFromImage(*image));
    const gem5::ai_mesh::ControlAnchor anchor{0, 0, 0};
    ASSERT_EQ(coordinator.onAuthoritativeEvent(
        gem5::ai_mesh::ControlTriggerEvent::AfterSqAccept, anchor, 0,
        10).size(), 1u);
    EXPECT_FALSE(coordinator.markSuppressedByRunCutoff(0));
    EXPECT_EQ(coordinator.state(0), gem5::ai_mesh::ControlRecordState::Ready);
    EXPECT_TRUE(coordinator.markMaterialized(0));
    EXPECT_FALSE(coordinator.markSuppressedByRunCutoff(0));
    EXPECT_TRUE(coordinator.markControlTerminal(0));
    EXPECT_TRUE(coordinator.allTerminal());
}

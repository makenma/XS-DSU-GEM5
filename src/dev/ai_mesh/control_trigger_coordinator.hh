#ifndef DEV_AI_MESH_CONTROL_TRIGGER_COORDINATOR_HH
#define DEV_AI_MESH_CONTROL_TRIGGER_COORDINATOR_HH

#include <cstdint>
#include <optional>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"

namespace gem5
{
namespace ai_mesh
{

enum class ControlTriggerEvent : uint8_t
{
    ScenarioStart = 0,
    AfterSqAccept = 1,
    AfterFirstOutputChunk = 2,
    SameEdgeAsTerminal = 3,
    AfterGenerateTerminal = 4,
    AfterSessionAdmission = 5,
    AfterBatchFreeze = 6,
    AfterReleaseTerminal = 7,
};

enum class ControlRecordState : uint8_t
{
    WaitTrigger,
    Ready,
    Materialized,
    ControlTerminal,
    LocalSubmitFailed,
    SuppressedByRunCutoff,
};

struct ControlAnchor
{
    uint32_t userId = 0;
    uint32_t taskSeq = 0;
    uint16_t repairRound = 0;
};

class ControlTriggerCoordinator
{
  public:
    explicit ControlTriggerCoordinator(size_t capacity);

    bool loadFromImage(const AgentPlanImage &image);
    std::vector<size_t> onAuthoritativeEvent(ControlTriggerEvent kind,
                                              const ControlAnchor &anchor,
                                              uint32_t afterControlOrdinal,
                                              uint64_t now);
    std::vector<size_t> due(uint64_t edge) const;
    std::optional<uint64_t> nextDueEdge() const;
    bool markMaterialized(size_t index);
    bool markControlTerminal(size_t index);
    bool markLocalSubmitFailed(size_t index);
    bool markSuppressedByRunCutoff(size_t index);
    bool allTerminal() const;
    const AgentControlAction &action(size_t index) const;
    size_t size() const { return records_.size(); }
    ControlRecordState state(size_t index) const;

  private:
    struct Record
    {
        AgentControlAction action;
        ControlRecordState state = ControlRecordState::WaitTrigger;
        uint64_t deliveryEdge = 0;
        bool fired = false;
    };

    bool matchesAnchor(const Record &record, ControlTriggerEvent kind,
                       const ControlAnchor &anchor,
                       uint32_t afterControlOrdinal) const;

    const size_t capacity_;
    std::vector<Record> records_;
};

}

}

#endif

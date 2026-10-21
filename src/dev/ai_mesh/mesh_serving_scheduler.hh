#ifndef DEV_AI_MESH_MESH_SERVING_SCHEDULER_HH
#define DEV_AI_MESH_MESH_SERVING_SCHEDULER_HH

#include <cstdint>
#include <functional>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

constexpr uint64_t kSchedulerMaxTick = UINT64_MAX;

enum class SchedulerPhase : uint8_t
{
    Prefill,
    Decode,
    Publish,
};

enum class SchedulerAction : uint8_t
{
    Run,
    Wait,
    Idle,
    FailHead,
};

struct ServingBatchKey
{
    uint16_t programId;
    uint16_t requestProfileId;
    uint8_t pathKind;
    SchedulerPhase phase;
    uint32_t validTokensPerMember;
    uint32_t kvTokensBefore;
    uint32_t decodeChunkTokens;

    bool operator==(const ServingBatchKey &other) const;
};

struct SchedulerMember
{
    uint64_t requestId = 0;
    SchedulerPhase phase = SchedulerPhase::Prefill;
    uint64_t phaseReadyTick = 0;
    uint64_t deadlineTick = kSchedulerMaxTick;
    uint8_t qos = 0;
    uint16_t programId = 0;
    uint16_t requestProfileId = 0;
    uint8_t pathKind = 0;
    uint32_t validTokensPerMember = 0;
    uint32_t kvTokensBefore = 0;
    uint32_t decodeChunkTokens = 0;
    uint32_t logicalSourceRank = 0;
};

struct SchedulerLimits
{
    uint64_t maxActiveSequences = 0;
    uint64_t maxBatchTokens = 0;
    uint64_t maxPrefillTokensPerBatch = 0;
    uint64_t maxDecodeSequencesPerBatch = 0;
    uint64_t prefillStarvationCycles = 0;
    uint64_t batchWaitCycles = 0;
};

struct SchedulerDecision
{
    SchedulerAction action = SchedulerAction::Idle;
    SchedulerPhase phase = SchedulerPhase::Prefill;
    std::vector<uint64_t> members;
    uint32_t memberCount = 0;
    uint8_t batchEffectiveQos = 0;
    uint64_t waitUntil = kSchedulerMaxTick;
};

SchedulerDecision servingSchedulerDecide(
    const std::vector<SchedulerMember> &members,
    const SchedulerLimits &limits, uint64_t now,
    const std::function<std::vector<uint32_t>(const ServingBatchKey &)>
        &profileCounts, uint64_t clockPeriodTicks = 1);

const char *schedulerPhaseName(SchedulerPhase phase);

}
}

#endif

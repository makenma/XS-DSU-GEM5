#ifndef DEV_AI_MESH_AGENT_REQUEST_SOURCE_HH
#define DEV_AI_MESH_AGENT_REQUEST_SOURCE_HH

#include <array>
#include <cstdint>
#include <optional>
#include <set>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"

namespace gem5
{
namespace ai_mesh
{

struct AgentSubmissionIntent
{
    uint64_t requestId = 0;
    uint64_t completionCookie = 0;
    uint64_t targetRequestId = 0;
    uint64_t sessionId = 0;
    uint64_t inputAddress = 0;
    uint64_t inputBytes = 0;
    uint64_t outputAddress = 0;
    uint64_t outputCapacityBytes = 0;
    uint64_t parameterAddress = 0;
    uint64_t outputMetadataAddress = 0;
    uint64_t kvHandle = 0;
    uint64_t kvGeneration = 0;
    uint64_t controlSessionId = 0;
    uint64_t controlKvHandle = 0;
    uint64_t profileKey = 0;
    uint64_t deadlineTick = 0;
    uint32_t userId = 0;
    uint32_t taskSequence = 0;
    uint32_t outputMetadataCapacityBytes = 0;
    uint32_t maxOutputTokens = 0;
    uint32_t publishChunkBytes = 0;
    uint32_t workloadPlanItemId = 0;
    uint32_t controlGeneration = 0;
    uint16_t repairRound = 0;
    uint16_t programId = 0;
    uint16_t profileId = 0;
    uint8_t commandKind = 0;
    uint8_t kvPolicy = 0;
    uint8_t qos = 0;
    bool hasDeadline = false;
    bool planDriven = false;
    const AgentPlanRound *round = nullptr;
    const AgentPlanTask *task = nullptr;
    std::array<uint8_t, 32> inputDigest{};
    std::array<uint8_t, 32> workloadDigest{};
};

class AgentRequestSource
{
  public:
    virtual ~AgentRequestSource() = default;
    virtual bool exhausted(uint64_t completedRequests) const = 0;
    virtual std::vector<AgentSubmissionIntent> nextBatch(
        const std::set<uint64_t> &issuedRequestIds,
        uint64_t submissionAttempts, uint32_t sqDepth) = 0;
};

std::optional<AgentSubmissionIntent>
buildGenerateIntent(const AgentPlanImage &image, size_t commandIndex);

std::optional<AgentSubmissionIntent>
buildControlIntent(const AgentPlanImage &image, size_t controlIndex);

class ProtocolProfileRequestSource final : public AgentRequestSource
{
  public:
    explicit ProtocolProfileRequestSource(uint32_t requestCount);

    bool exhausted(uint64_t completedRequests) const override;
    std::vector<AgentSubmissionIntent> nextBatch(
        const std::set<uint64_t> &issuedRequestIds,
        uint64_t submissionAttempts, uint32_t sqDepth) override;

  private:
    const uint32_t requestCount;
};

class ReplayPlanRequestSource final : public AgentRequestSource
{
  public:
    explicit ReplayPlanRequestSource(AgentPlanImage &&image);

    bool exhausted(uint64_t completedRequests) const override;
    std::vector<AgentSubmissionIntent> nextBatch(
        const std::set<uint64_t> &issuedRequestIds,
        uint64_t submissionAttempts, uint32_t sqDepth) override;

  private:
    const AgentPlanImage image;
    size_t nextCommand = 0;
    size_t handedOutGenerates = 0;
    size_t generateCount = 0;
};

}
}
#endif

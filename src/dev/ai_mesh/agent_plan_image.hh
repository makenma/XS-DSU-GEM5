#ifndef DEV_AI_MESH_AGENT_PLAN_IMAGE_HH
#define DEV_AI_MESH_AGENT_PLAN_IMAGE_HH

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_surrogate_codec.hh"

namespace gem5
{
namespace ai_mesh
{

constexpr uint8_t kAgentCommandGenerate = 0;
constexpr uint8_t kAgentCommandReleaseSession = 1;
constexpr uint8_t kAgentCommandCancel = 2;
constexpr uint8_t kAgentKvPolicyInitial = 0;
constexpr uint8_t kAgentKvPolicyRequireReuse = 1;
constexpr uint8_t kAgentKvPolicyAllowReprefill = 2;

struct AgentPlanStage
{
    uint64_t nominalNs = 0;
    uint64_t readBytes = 0;
    uint64_t writeBytes = 0;
    uint64_t rawLogBytes = 0;
    uint64_t excerptBytes = 0;
    uint32_t excerptTokens = 0;
    uint16_t hostTokensRequired = 0;
    uint8_t outcome = 0;
};

struct AgentPlanRound
{
    uint64_t requestId = 0;
    uint64_t deadlineTick = 0;
    uint64_t fullContextBytes = 0;
    uint64_t cachedContextBytes = 0;
    uint64_t generatedCodeBytes = 0;
    uint64_t outputCapacityBytes = 0;
    uint64_t profileKey = 0;
    uint64_t promptBytes = 0;
    uint64_t deltaPromptBytes = 0;
    uint32_t itemId = 0;
    uint32_t fullContextTokens = 0;
    uint32_t cachedTokens = 0;
    uint32_t outputTokens = 0;
    uint32_t metadataCapacityBytes = 0;
    uint32_t promptTokens = 0;
    uint32_t deltaPromptTokens = 0;
    uint16_t repairRound = 0;
    uint16_t programId = 0;
    uint16_t profileId = 0;
    uint8_t kvPolicy = 0;
    uint8_t qos = 0;
    bool hasDeadline = false;
    bool hasTest = false;
    bool hasParse = false;
    bool hasPrompt = false;
    bool hasDelta = false;
    std::array<uint8_t, 32> inputDigest{};
    AgentPlanStage compile;
    AgentPlanStage test;
    AgentPlanStage logParse;
};

struct AgentPlanTask
{
    uint64_t thinkNs = 0;
    uint64_t sessionId = 0;
    uint64_t kvHandle = 0;
    uint32_t taskSeq = 0;
    uint32_t generation = 0;
    uint16_t effectiveCap = 0;
    std::vector<AgentPlanRound> rounds;
};

struct AgentPlanUser
{
    uint32_t userId = 0;
    std::vector<AgentPlanTask> tasks;
};

struct AgentCommandRecord
{
    uint64_t requestId = 0;
    uint64_t targetRequestId = 0;
    uint64_t sessionId = 0;
    uint64_t kvHandle = 0;
    uint32_t userId = 0;
    uint32_t perUserCommandSeq = 0;
    uint32_t taskSeq = 0;
    uint32_t controlOrdinal = 0;
    uint32_t generation = 0;
    uint16_t repairRoundOrFFFF = 0;
    uint8_t commandKind = 0;
};

struct AgentHostTaskRecord
{
    uint64_t hostTaskId = 0;
    uint32_t userId = 0;
    uint32_t taskSeq = 0;
    uint16_t repairRound = 0;
    uint8_t stageKind = 0;
};

struct AgentControlAction
{
    uint64_t targetSessionId = 0;
    uint64_t targetKvHandle = 0;
    uint32_t controlOrdinal = 0;
    uint32_t issuerUserId = 0;
    uint32_t issuerTaskSeq = 0;
    uint32_t targetTaskSeq = 0;
    uint32_t afterControlOrdinal = 0;
    uint32_t targetGeneration = 0;
    uint32_t anchorUserId = 0;
    uint32_t anchorTaskSeq = 0;
    uint16_t targetUserId = 0;
    uint16_t targetRepairRound = 0;
    uint16_t anchorRepairRound = 0;
    uint8_t opcode = 0;
    uint8_t triggerKind = 0;
    bool hasAnchor = false;
    bool hasAfterOrdinal = false;
};

struct AgentArenaRecord
{
    uint64_t requestId = 0;
    uint64_t base = 0;
    uint64_t allocationBytes = 0;
    uint64_t initialValidBytes = 0;
    uint32_t userId = 0;
    uint32_t taskSeq = 0;
    uint32_t perUserCommandSeq = 0;
    uint32_t alignment = 0;
    uint16_t repairRoundOrFFFF = 0;
    uint8_t commandKind = 0;
    uint8_t arenaKind = 0;
};

class AgentPlanImage
{
  public:
    static std::optional<AgentPlanImage>
    parse(const uint8_t *data, size_t length);

    const std::vector<AgentPlanUser> &users() const { return users_; }
    const std::vector<AgentCommandRecord> &commands() const
    { return commands_; }
    const std::vector<AgentHostTaskRecord> &hostTasks() const
    { return hostTasks_; }
    const std::vector<AgentArenaRecord> &arena() const { return arena_; }
    const std::vector<AgentControlAction> &controlActions() const
    { return controlActions_; }
    const SurrogateProfileRegistry *surrogateRegistry() const
    { return surrogate_.has_value() ? &surrogate_.value() : nullptr; }
    const std::array<uint8_t, 32> &imageDigest() const { return imageDigest_; }
    const std::array<uint8_t, 32> &workloadPlanDigest() const
    { return workloadPlanDigest_; }
    const std::array<uint8_t, 32> &controlPlanDigest() const
    { return controlPlanDigest_; }
    uint32_t generateCommandCount() const;

  private:
    std::vector<AgentPlanUser> users_;
    std::vector<AgentCommandRecord> commands_;
    std::vector<AgentHostTaskRecord> hostTasks_;
    std::vector<AgentArenaRecord> arena_;
    std::vector<AgentControlAction> controlActions_;
    std::optional<SurrogateProfileRegistry> surrogate_;
    std::array<uint8_t, 32> imageDigest_{};
    std::array<uint8_t, 32> workloadPlanDigest_{};
    std::array<uint8_t, 32> controlPlanDigest_{};
};

std::optional<AgentPlanImage>
loadAgentPlanImageFile(const std::string &path);

}
}
#endif

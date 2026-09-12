#include "dev/ai_mesh/agent_request_source.hh"

#include <limits>

namespace gem5
{
namespace ai_mesh
{

ProtocolProfileRequestSource::ProtocolProfileRequestSource(
    uint32_t requestCount)
    : requestCount(requestCount)
{}

bool
ProtocolProfileRequestSource::exhausted(uint64_t completedRequests) const
{
    return completedRequests >= requestCount;
}

std::vector<AgentSubmissionIntent>
ProtocolProfileRequestSource::nextBatch(
    const std::set<uint64_t> &issuedRequestIds,
    uint64_t submissionAttempts, uint32_t sqDepth)
{
    std::vector<AgentSubmissionIntent> intents;
    if (submissionAttempts == 0 && requestCount > 1 &&
        requestCount <= sqDepth) {
        intents.reserve(requestCount);
        for (uint64_t index = 0; index < requestCount; ++index) {
            AgentSubmissionIntent intent;
            intent.requestId = index + 1;
            intent.completionCookie = index + 1;
            intents.push_back(intent);
        }
        return intents;
    }
    uint64_t candidate = submissionAttempts + 1;
    while (issuedRequestIds.count(candidate) != 0) {
        candidate = candidate == std::numeric_limits<uint64_t>::max() ?
            candidate : candidate + 1;
    }
    AgentSubmissionIntent intent;
    intent.requestId = candidate;
    intent.completionCookie = candidate;
    intents.push_back(intent);
    return intents;
}

namespace
{

constexpr uint8_t kArenaInput = 0;
constexpr uint8_t kArenaParameter = 1;
constexpr uint8_t kArenaOutput = 2;
constexpr uint8_t kArenaMetadata = 3;

const AgentPlanTask *
findTask(const AgentPlanImage &image, const AgentCommandRecord &record)
{
    for (const AgentPlanUser &user : image.users())
        if (user.userId == record.userId)
            for (const AgentPlanTask &task : user.tasks)
                if (task.taskSeq == record.taskSeq)
                    return &task;
    return nullptr;
}

const AgentPlanRound *
findRound(const AgentPlanTask &task, const AgentCommandRecord &record)
{
    for (const AgentPlanRound &round : task.rounds)
        if (round.repairRound == record.repairRoundOrFFFF)
            return &round;
    return nullptr;
}

bool
fillArenaAddresses(const AgentPlanImage &image, const AgentCommandRecord &record,
                   AgentSubmissionIntent &intent)
{
    bool seen[4] = {false, false, false, false};
    for (const AgentArenaRecord &arena : image.arena()) {
        if (arena.requestId != record.requestId ||
                arena.arenaKind > kArenaMetadata)
            continue;
        seen[arena.arenaKind] = true;
        switch (arena.arenaKind) {
          case kArenaInput:
            intent.inputAddress = arena.base;
            break;
          case kArenaParameter:
            intent.parameterAddress = arena.base;
            break;
          case kArenaOutput:
            intent.outputAddress = arena.base;
            break;
          case kArenaMetadata:
            intent.outputMetadataAddress = arena.base;
            break;
        }
    }
    return seen[0] && seen[1] && seen[2] && seen[3];
}

}

std::optional<AgentSubmissionIntent>
buildGenerateIntent(const AgentPlanImage &image, size_t commandIndex)
{
    if (commandIndex >= image.commands().size() ||
            image.commands()[commandIndex].commandKind !=
                kAgentCommandGenerate)
        return std::nullopt;
    const AgentCommandRecord &record = image.commands()[commandIndex];
    const AgentPlanTask *task = findTask(image, record);
    if (task == nullptr)
        return std::nullopt;
    const AgentPlanRound *round = findRound(*task, record);
    if (round == nullptr)
        return std::nullopt;
    const SurrogateProfileRegistry *registry = image.surrogateRegistry();
    if (registry == nullptr)
        return std::nullopt;

    AgentSubmissionIntent intent;
    intent.requestId = record.requestId;
    intent.completionCookie = record.requestId;
    intent.userId = record.userId;
    intent.taskSequence = record.taskSeq;
    intent.repairRound = record.repairRoundOrFFFF;
    intent.sessionId = task->sessionId;
    intent.kvHandle = task->kvHandle;
    intent.kvGeneration = task->generation;
    intent.inputBytes = round->fullContextBytes;
    intent.outputCapacityBytes = round->outputCapacityBytes;
    intent.outputMetadataCapacityBytes = round->metadataCapacityBytes;
    intent.maxOutputTokens = round->outputTokens;
    intent.workloadPlanItemId = round->itemId;
    intent.programId = round->programId;
    intent.profileId = round->profileId;
    intent.profileKey = round->profileKey;
    intent.kvPolicy = round->kvPolicy;
    intent.qos = round->qos;
    intent.hasDeadline = round->hasDeadline;
    intent.deadlineTick = round->deadlineTick;
    intent.inputDigest = round->inputDigest;
    intent.workloadDigest = image.workloadPlanDigest();
    intent.publishChunkBytes = registry->publishChunkBytes();
    intent.round = round;
    intent.task = task;
    if (!fillArenaAddresses(image, record, intent))
        return std::nullopt;
    intent.planDriven = true;
    return intent;
}

std::optional<AgentSubmissionIntent>
buildControlIntent(const AgentPlanImage &image, size_t controlIndex)
{
    if (controlIndex >= image.controlActions().size())
        return std::nullopt;
    const AgentControlAction &action = image.controlActions()[controlIndex];
    const AgentCommandRecord *selected = nullptr;
    for (const AgentCommandRecord &record : image.commands())
        if (record.controlOrdinal == action.controlOrdinal &&
                record.commandKind != kAgentCommandGenerate) {
            selected = &record;
            break;
        }
    if (selected == nullptr)
        return std::nullopt;

    AgentSubmissionIntent intent;
    intent.requestId = selected->requestId;
    intent.completionCookie = selected->requestId;
    intent.userId = selected->userId;
    intent.taskSequence = selected->taskSeq;
    intent.commandKind = selected->commandKind;
    intent.targetRequestId = selected->targetRequestId;
    intent.controlSessionId = selected->sessionId;
    intent.controlKvHandle = selected->kvHandle;
    intent.controlGeneration = selected->generation;
    for (const AgentArenaRecord &arena : image.arena())
        if (arena.requestId == selected->requestId && arena.arenaKind == 1) {
            intent.parameterAddress = arena.base;
            break;
        }
    if (intent.parameterAddress == 0)
        return std::nullopt;
    intent.planDriven = true;
    return intent;
}

ReplayPlanRequestSource::ReplayPlanRequestSource(AgentPlanImage &&image)
    : image(std::move(image))
{
    generateCount = this->image.generateCommandCount();
}

bool
ReplayPlanRequestSource::exhausted(uint64_t completedRequests) const
{
    return completedRequests >= generateCount;
}

std::vector<AgentSubmissionIntent>
ReplayPlanRequestSource::nextBatch(
    const std::set<uint64_t> &issuedRequestIds,
    uint64_t submissionAttempts, uint32_t sqDepth)
{
    std::vector<AgentSubmissionIntent> intents;
    while (nextCommand < image.commands().size() &&
           image.commands()[nextCommand].commandKind !=
               kAgentCommandGenerate)
        ++nextCommand;
    if (nextCommand >= image.commands().size())
        return intents;
    auto intent = buildGenerateIntent(image, nextCommand);
    if (!intent.has_value())
        return intents;
    ++nextCommand;
    ++handedOutGenerates;
    intents.push_back(std::move(*intent));
    return intents;
}

}
}

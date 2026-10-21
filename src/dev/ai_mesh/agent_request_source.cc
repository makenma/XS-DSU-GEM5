#include "dev/ai_mesh/agent_request_source.hh"

#include <limits>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

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
    bool seen[agent_abi::kArenaKindWORKSPACE + 1] = {};
    for (const AgentArenaRecord &arena : image.arena()) {
        if (arena.requestId != record.requestId ||
                arena.arenaKind > agent_abi::kArenaKindWORKSPACE)
            continue;
        if (seen[arena.arenaKind] || !arena.allocationBytes ||
                arena.base > UINT64_MAX - arena.allocationBytes)
            return false;
        seen[arena.arenaKind] = true;
        switch (arena.arenaKind) {
          case agent_abi::kArenaKindINPUT:
            intent.inputAddress = arena.base;
            break;
          case agent_abi::kArenaKindPARAMETER:
            intent.parameterAddress = arena.base;
            intent.parameterCapacityBytes = arena.allocationBytes;
            break;
          case agent_abi::kArenaKindOUTPUT:
            intent.outputAddress = arena.base;
            break;
          case agent_abi::kArenaKindMETADATA:
            intent.outputMetadataAddress = arena.base;
            break;
          case agent_abi::kArenaKindWORKSPACE:
            if (arena.initialValidBytes)
                return false;
            intent.workspaceAddress = arena.base;
            intent.workspaceCapacityBytes = arena.allocationBytes;
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
    const auto *binding = findHostBindingPlan(image.hostBindingPlans(), round->programId, round->profileId);
    if (!registry && (!binding || binding->primaryKvSymbolId))
        return std::nullopt;
    if (binding && !binding->primaryKvSymbolId &&
            (task->kvHandle || task->generation || round->cachedTokens || round->outputTokens || round->repairRound))
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
    intent.publishChunkBytes = binding ? binding->publishChunkBytes : registry->publishChunkBytes();
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
    for (size_t index = 0; index < image.commands().size(); ++index)
        if (image.commands()[index].controlOrdinal == action.controlOrdinal &&
                image.commands()[index].commandKind != kAgentCommandGenerate)
            return buildControlCommandIntent(image, index);
    return std::nullopt;
}

std::optional<AgentSubmissionIntent>
buildControlCommandIntent(const AgentPlanImage &image, size_t commandIndex)
{
    if (commandIndex >= image.commands().size() ||
            image.commands()[commandIndex].commandKind == kAgentCommandGenerate)
        return std::nullopt;
    const auto *selected = &image.commands()[commandIndex];
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

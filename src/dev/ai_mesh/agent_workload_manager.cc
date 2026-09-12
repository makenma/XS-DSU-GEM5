#include "dev/ai_mesh/agent_workload_manager.hh"

#include <algorithm>
#include <array>
#include <utility>

#include "base/logging.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

constexpr uint8_t kStageOutcomeSuccess = 0;
constexpr uint8_t kStageOutcomeFail = 1;
constexpr uint8_t kStageOutcomeNone = 2;

const char *
outcomeName(uint8_t outcome)
{
    switch (outcome) {
      case kStageOutcomeSuccess:
        return "SUCCESS";
      case kStageOutcomeFail:
        return "FAIL";
      case kStageOutcomeNone:
        return "NONE";
    }
    return "NONE";
}

bool
kindIndexValid(uint8_t kind)
{
    return kind < kHostStageKindCount;
}

}

const char *
hostStageKindName(HostStageKindV1 kind)
{
    switch (kind) {
      case HostStageKindV1::Compile:
        return "COMPILE";
      case HostStageKindV1::Test:
        return "TEST";
      case HostStageKindV1::LogParse:
        return "LOG_PARSE";
    }
    return "COMPILE";
}

const char *
hostLocalFaultSiteName(agent_abi::HostLocalFaultSiteV1 site)
{
    switch (site) {
      case agent_abi::HostLocalFaultSiteV1::OBJECT_PRODUCE:
        return "OBJECT_PRODUCE";
      case agent_abi::HostLocalFaultSiteV1::OBJECT_READ:
        return "OBJECT_READ";
    }
    return "OBJECT_PRODUCE";
}

AgentWorkloadManager::AgentWorkloadManager(
    AgentPlanImage &&planImage,
    const AgentWorkloadManagerConfig &managerConfig,
    AgentWorkloadFactsSink factsSink)
    : image(std::move(planImage)), config(managerConfig),
      sink(std::move(factsSink)),
      resources({config.hostCompileSlots, config.hostTestSlots,
                 config.hostLogParseSlots},
                {config.hostServiceQueueDepth, config.hostServiceQueueDepth,
                 config.hostServiceQueueDepth},
                static_cast<uint64_t>(
                    (static_cast<unsigned __int128>(
                         config.hostComputeTokens) *
                     config.hostAvailableFractionQ16) / 65536),
                {config.hostWeightCompile, config.hostWeightTest,
                 config.hostWeightLogParse},
                std::max<uint32_t>(config.hostServiceQueueDepth,
                                   static_cast<uint32_t>(
                                       image.hostTasks().size()))),
      objects(config.agentObjectTableEntries)
{
    agingThresholdTicks = config.hostAgingThresholdNs == 0 ? 0 :
        ceilNsToTicks(config.hostAgingThresholdNs);
    resources.setAgingThresholdTicks(agingThresholdTicks);

    for (const AgentPlanUser &user : image.users())
        for (const AgentPlanTask &planTask : user.tasks)
            for (const AgentPlanRound &round : planTask.rounds)
                for (const AgentPlanStage *stage :
                     {&round.compile, &round.test, &round.logParse})
                    fatal_if(
                        stage->hostTokensRequired > resources.availableTokens(),
                        "host stage requires %u compute tokens, global pool "
                        "provides %llu",
                        stage->hostTokensRequired,
                        static_cast<unsigned long long>(
                            resources.availableTokens()));

    for (const AgentPlanUser &user : image.users()) {
        for (const AgentPlanTask &task : user.tasks) {
            TaskContext context;
            context.userId = user.userId;
            context.taskSeq = task.taskSeq;
            context.effectiveCap = task.effectiveCap;
            context.planTask = &task;
            if (task.taskSeq == 0)
                context.thinkTargetTick = ceilNsToTicks(task.thinkNs);
            tasks.push_back(context);
        }
    }

    for (size_t index = 0; index < image.commands().size(); ++index) {
        const AgentCommandRecord &record = image.commands()[index];
        if (record.commandKind != kAgentCommandGenerate)
            continue;
        for (size_t taskIndex = 0; taskIndex < tasks.size(); ++taskIndex) {
            const TaskContext &task = tasks[taskIndex];
            if (task.userId != record.userId ||
                    task.taskSeq != record.taskSeq)
                continue;
            for (size_t roundIndex = 0;
                 roundIndex < task.planTask->rounds.size(); ++roundIndex) {
                if (task.planTask->rounds[roundIndex].repairRound !=
                        record.repairRoundOrFFFF)
                    continue;
                requestRounds.emplace(record.requestId,
                                      std::make_pair(taskIndex,
                                                     roundIndex));
            }
        }
    }

    for (const AgentHostTaskRecord &record : image.hostTasks()) {
        if (!kindIndexValid(record.stageKind))
            continue;
        hostTaskIds[std::make_tuple(record.userId, record.taskSeq,
                                    record.repairRound,
                                    record.stageKind)] = record.hostTaskId;
        for (size_t taskIndex = 0; taskIndex < tasks.size(); ++taskIndex) {
            const TaskContext &task = tasks[taskIndex];
            if (task.userId == record.userId &&
                    task.taskSeq == record.taskSeq) {
                hostTaskOwner[record.hostTaskId] =
                    std::make_pair(taskIndex, record.stageKind);
                break;
            }
        }
    }

    setMetric("raw_log_bytes", 0);
    setMetric("scanned_log_bytes", 0);
    setMetric("excerpt_bytes", 0);
    setMetric("excerpt_tokens", 0);
    setMetric("npu_fabric_raw_log_bytes", 0);
    setMetric("repair_input_excerpt_bytes", 0);
    setMetric("agent_live_objects", 0);
    setMetric("completed_tasks", 0);
    setMetric("failed_tasks", 0);
    setMetric("infra_failed_tasks", 0);
    setMetric("aging_reservations", 0);
}

Tick
AgentWorkloadManager::ceilNsToTicks(uint64_t ns) const
{
    const unsigned __int128 scaled =
        static_cast<unsigned __int128>(ns) * config.clockTicksPerSecond;
    const unsigned __int128 ticks =
        (scaled + 999999999ull) / 1000000000ull;
    return static_cast<Tick>(ticks);
}

Tick
AgentWorkloadManager::localIoTicks(const AgentPlanStage &stage) const
{
    if (!config.hostLocalIoEnabled)
        return 0;
    const uint64_t bytes = stage.readBytes + stage.writeBytes;
    const uint64_t bytesNs = (bytes + config.hostLocalIoBytesPerNs - 1) /
        config.hostLocalIoBytesPerNs;
    return ceilNsToTicks(config.hostLocalIoFixedNs) +
        ceilNsToTicks(bytesNs);
}

const AgentPlanRound &
AgentWorkloadManager::roundOf(const TaskContext &task) const
{
    return task.planTask->rounds[task.roundIndex];
}

const AgentPlanStage &
AgentWorkloadManager::stagePlan(const TaskContext &task,
                                HostStageKindV1 kind) const
{
    const AgentPlanRound &round = roundOf(task);
    switch (kind) {
      case HostStageKindV1::Compile:
        return round.compile;
      case HostStageKindV1::Test:
        return round.test;
      case HostStageKindV1::LogParse:
        return round.logParse;
    }
    return round.compile;
}

uint64_t
AgentWorkloadManager::hostTaskIdFor(const TaskContext &task,
                                    HostStageKindV1 kind) const
{
    const auto found = hostTaskIds.find(std::make_tuple(
        task.userId, task.taskSeq, roundOf(task).repairRound,
        static_cast<uint8_t>(kind)));
    return found == hostTaskIds.end() ? 0 : found->second;
}

size_t
AgentWorkloadManager::commandIndexOfRound(const TaskContext &task) const
{
    for (size_t index = 0; index < image.commands().size(); ++index) {
        const AgentCommandRecord &record = image.commands()[index];
        if (record.commandKind == kAgentCommandGenerate &&
                record.userId == task.userId &&
                record.taskSeq == task.taskSeq &&
                record.repairRoundOrFFFF == roundOf(task).repairRound)
            return index;
    }
    return image.commands().size();
}

void
AgentWorkloadManager::emitSemantic(const std::string &kind,
                                   const std::string &object,
                                   uint64_t requestId,
                                   const std::string &status)
{
    if (sink.recordSemantic)
        sink.recordSemantic(kind, object, requestId, status);
}

void
AgentWorkloadManager::setMetric(const std::string &name, uint64_t value)
{
    if (sink.setMetric)
        sink.setMetric(name, value);
}

void
AgentWorkloadManager::publishLiveObjects()
{
    setMetric("agent_live_objects", objects.liveCount());
}

bool
AgentWorkloadManager::exhausted(uint64_t completedRequests) const
{
    return allTasksTerminal();
}

bool
AgentWorkloadManager::allTasksTerminal() const
{
    for (const TaskContext &task : tasks)
        if (task.state != AgentTaskFsmState::TaskLifecycleFinal &&
                !task.suppressed)
            return false;
    return true;
}

bool
AgentWorkloadManager::controlAnchorSuppressed(size_t controlIndex) const
{
    const std::vector<AgentControlAction> &actions = image.controlActions();
    if (controlIndex >= actions.size() || !actions[controlIndex].hasAnchor)
        return false;
    const AgentControlAction &action = actions[controlIndex];
    for (const TaskContext &task : tasks)
        if (task.userId == action.anchorUserId &&
                task.taskSeq == action.anchorTaskSeq)
            return task.suppressed;
    return false;
}

std::vector<AgentSubmissionIntent>
AgentWorkloadManager::nextBatch(
    const std::set<uint64_t> &issuedRequestIds,
    uint64_t submissionAttempts, uint32_t sqDepth)
{
    std::vector<AgentSubmissionIntent> intents;
    if (!pendingControls.empty()) {
        intents.push_back(std::move(pendingControls.front()));
        pendingControls.erase(pendingControls.begin());
        return intents;
    }
    size_t selected = tasks.size();
    for (size_t index = 0; index < tasks.size(); ++index) {
        const TaskContext &task = tasks[index];
        if (task.state != AgentTaskFsmState::SubmitNpu || !task.intent)
            continue;
        if (selected == tasks.size() ||
                std::make_pair(task.submitReadyTick, task.userId) <
                    std::make_pair(tasks[selected].submitReadyTick,
                                   tasks[selected].userId))
            selected = index;
    }
    if (selected == tasks.size())
        return intents;
    TaskContext &task = tasks[selected];
    task.state = AgentTaskFsmState::WaitNpu;
    intents.push_back(std::move(*task.intent));
    task.intent.reset();
    return intents;
}

void
AgentWorkloadManager::onGenerateTerminal(uint64_t requestId, bool success,
                                         Tick now)
{
    const auto found = requestRounds.find(requestId);
    if (found == requestRounds.end())
        return;
    TaskContext &task = tasks[found->second.first];
    if (task.state != AgentTaskFsmState::WaitNpu ||
            found->second.second != task.roundIndex)
        return;
    task.currentRequestId = requestId;
    if (!success) {
        enterTaskTerminal(task, AgentTaskOutcome::BusinessFailed, now);
        publishLiveObjects();
        return;
    }
    const AgentPlanRound &round = roundOf(task);
    const auto code = objects.reserve(AgentObjectKind::GeneratedCode,
                                      round.generatedCodeBytes,
                                      round.outputCapacityBytes);
    if (code && objects.beginProduce(*code) &&
            objects.extendValidPrefix(*code, round.generatedCodeBytes) &&
            objects.commit(*code)) {
        task.generatedCode = *code;
        task.ownedObjects.push_back(*code);
    }
    task.state = AgentTaskFsmState::CompileQueue;
    pendingStages.push_back(PendingEnqueue{
        found->second.first, HostStageKindV1::Compile});
    publishLiveObjects();
}

void
AgentWorkloadManager::scheduleGenerateTerminal(uint64_t requestId,
                                               bool success, Tick at)
{
    for (const PendingGenerateTerminal &pending :
         pendingGenerateTerminals) {
        if (pending.requestId == requestId)
            return;
    }
    pendingGenerateTerminals.push_back(
        PendingGenerateTerminal{requestId, success, at});
}

void
AgentWorkloadManager::consumeDueGenerateTerminals(Tick now)
{
    for (size_t index = 0; index < pendingGenerateTerminals.size();) {
        const PendingGenerateTerminal &pending =
            pendingGenerateTerminals[index];
        if (pending.at > now) {
            ++index;
            continue;
        }
        onGenerateTerminal(pending.requestId, pending.success, now);
        pendingGenerateTerminals.erase(
            pendingGenerateTerminals.begin() + index);
    }
}

void
AgentWorkloadManager::materializeControlAction(size_t controlIndex)
{
    auto intent = buildControlIntent(image, controlIndex);
    if (!intent) {
        emitSemantic("CONTROL_REJECT", "CONTROL_WAITER", 0,
                     "E_AGENT_PLAN");
        return;
    }
    pendingControls.push_back(std::move(*intent));
    emitSemantic("CONTROL_READY", "CONTROL_WAITER",
                 pendingControls.back().requestId, "");
}

std::optional<AgentSubmissionIntent>
AgentWorkloadManager::takeControlIntent()
{
    if (pendingControls.empty())
        return std::nullopt;
    AgentSubmissionIntent intent = std::move(pendingControls.front());
    pendingControls.erase(pendingControls.begin());
    return intent;
}

bool
AgentWorkloadManager::anchorForRequest(uint64_t requestId, uint32_t &userId,
                                       uint32_t &taskSeq,
                                       uint16_t &repairRound) const
{
    for (const AgentCommandRecord &record : image.commands())
        if (record.requestId == requestId &&
                record.commandKind == kAgentCommandGenerate) {
            userId = record.userId;
            taskSeq = record.taskSeq;
            repairRound = record.repairRoundOrFFFF;
            return true;
        }
    return false;
}

void
AgentWorkloadManager::onEdge(Tick now)
{
    consumeDueGenerateTerminals(now);
    finishDueStages(now);
    processThinkArrivals(now);
    startGrantedStages(resources.arbitrate(now), now);
    if (resources.hasAgingReservation() != agingReservationActive) {
        agingReservationActive = resources.hasAgingReservation();
        if (agingReservationActive) {
            ++agingReservationCount;
            setMetric("aging_reservations", agingReservationCount);
        }
    }
    lastArbitratedTick = now;
    admitPendingStages(now);
    applyStopCutoff();
    publishLiveObjects();
}

void
AgentWorkloadManager::finishDueStages(Tick now)
{
    for (size_t index = 0; index < tasks.size(); ++index) {
        TaskContext &task = tasks[index];
        if (!task.stage || task.stage->actualDoneTick > now)
            continue;
        const HostStageKindV1 kind = task.stage->kind;
        emitSemantic("HOST_STAGE_DONE", hostStageKindName(kind),
                     task.stage->requestId,
                     outcomeName(stagePlan(task, kind).outcome));
        handleStageOutcome(task, kind, now);
        resources.release(task.stage->id);
        queuedArrivals.erase(task.stage->id.value());
        task.stage.reset();
    }
}

void
AgentWorkloadManager::handleStageOutcome(TaskContext &task,
                                          HostStageKindV1 kind, Tick now)
{
    const size_t taskIndex = &task - tasks.data();
    const AgentPlanRound &round = roundOf(task);
    const AgentPlanStage &stage = stagePlan(task, kind);
    if (kind == HostStageKindV1::LogParse) {
        if (hostFaultDue(task,
                         agent_abi::HostLocalFaultSiteV1::OBJECT_PRODUCE)) {
            hostStageErrorDrain(
                task, agent_abi::HostLocalFaultSiteV1::OBJECT_PRODUCE, now);
            return;
        }
        commitExcerpt(task, stage);
        if (task.rawLog)
            objects.releaseConsumerRef(*task.rawLog);
        task.rawLog.reset();
        scannedLogBytes += stage.readBytes;
        setMetric("scanned_log_bytes", scannedLogBytes);
        task.roundIndex += 1;
        task.state = AgentTaskFsmState::SubmitNpu;
        task.submitReadyTick = now;
        const auto intent = buildGenerateIntent(
            image, commandIndexOfRound(task));
        if (intent) {
            task.intent = intent;
            task.currentRequestId = intent->requestId;
        }
        return;
    }
    const bool success = stage.outcome == kStageOutcomeSuccess;
    if (kind == HostStageKindV1::Compile && success) {
        if (round.hasTest) {
            task.state = AgentTaskFsmState::TestQueue;
            pendingStages.push_back(PendingEnqueue{
                taskIndex, HostStageKindV1::Test});
            return;
        }
        enterTaskTerminal(task, AgentTaskOutcome::BusinessDone, now);
        return;
    }
    releaseGeneratedCodeRef(task);
    if (!success && round.repairRound < task.effectiveCap &&
            round.hasParse) {
        if (hostFaultDue(task,
                         agent_abi::HostLocalFaultSiteV1::OBJECT_PRODUCE)) {
            hostStageErrorDrain(
                task, agent_abi::HostLocalFaultSiteV1::OBJECT_PRODUCE, now);
            return;
        }
        commitRawLog(task, stage.rawLogBytes);
        task.state = AgentTaskFsmState::ParseQueue;
        pendingStages.push_back(PendingEnqueue{
            taskIndex, HostStageKindV1::LogParse});
        return;
    }
    enterTaskTerminal(task, success ? AgentTaskOutcome::BusinessDone :
                                      AgentTaskOutcome::BusinessFailed,
                      now);
}

void
AgentWorkloadManager::enterTaskTerminal(TaskContext &task,
                                        AgentTaskOutcome outcome, Tick now)
{
    releaseGeneratedCodeRef(task);
    if (task.rawLog)
        objects.releaseConsumerRef(*task.rawLog);
    if (outcome == AgentTaskOutcome::InfraFailed)
        for (const AgentObjectId object : task.ownedObjects) {
            objects.poison(object);
            objects.errorDrain(object);
        }
    for (auto object = task.ownedObjects.rbegin();
         object != task.ownedObjects.rend(); ++object)
        objects.release(*object);
    task.ownedObjects.clear();
    task.generatedCode.reset();
    task.rawLog.reset();
    task.state = AgentTaskFsmState::TaskLifecycleFinal;
    task.lifecycleFinalTick = now;
    if (outcome == AgentTaskOutcome::BusinessDone)
        ++completedTaskCount;
    else if (outcome == AgentTaskOutcome::BusinessFailed)
        ++failedTaskCount;
    else
        ++infraFailedTaskCount;
    setMetric("completed_tasks", completedTaskCount);
    setMetric("failed_tasks", failedTaskCount);
    setMetric("infra_failed_tasks", infraFailedTaskCount);
    emitSemantic("TASK_TERMINAL", "TASK", task.currentRequestId,
                 outcome == AgentTaskOutcome::BusinessDone ?
                     "BUSINESS_DONE" :
                 outcome == AgentTaskOutcome::BusinessFailed ?
                     "BUSINESS_FAILED" : "INFRA_FAILED");
    for (TaskContext &next : tasks)
        if (next.userId == task.userId &&
                next.taskSeq == task.taskSeq + 1)
            next.thinkTargetTick =
                now + ceilNsToTicks(next.planTask->thinkNs);
}

bool
AgentWorkloadManager::hostFaultDue(
    const TaskContext &task, agent_abi::HostLocalFaultSiteV1 site) const
{
    return config.hostFaultSite == site && !hostFaultFired &&
        task.taskSeq == config.hostFaultTask &&
        roundOf(task).repairRound == config.hostFaultRound;
}

void
AgentWorkloadManager::hostStageErrorDrain(
    TaskContext &task, agent_abi::HostLocalFaultSiteV1 site, Tick now)
{
    hostFaultFired = true;
    emitSemantic("HOST_FAULT", hostLocalFaultSiteName(site),
                 task.currentRequestId, "E_HOST_LOCAL_OBJECT");
    enterTaskTerminal(task, AgentTaskOutcome::InfraFailed, now);
}

void
AgentWorkloadManager::suppressThink(TaskContext &task)
{
    task.suppressed = true;
    emitSemantic("TASK_SUPPRESSED", "TASK", task.currentRequestId, "");
}

void
AgentWorkloadManager::releaseGeneratedCodeRef(TaskContext &task)
{
    if (task.generatedCode)
        objects.releaseConsumerRef(*task.generatedCode);
}

void
AgentWorkloadManager::commitRawLog(TaskContext &task, uint64_t bytes)
{
    const auto object = objects.reserve(AgentObjectKind::RawLog, bytes,
                                        bytes);
    if (object && objects.commitHostObject(*object)) {
        task.rawLog = *object;
        task.ownedObjects.push_back(*object);
        rawLogBytes += bytes;
        setMetric("raw_log_bytes", rawLogBytes);
    }
}

void
AgentWorkloadManager::commitExcerpt(TaskContext &task,
                                    const AgentPlanStage &stage)
{
    const auto object = objects.reserve(AgentObjectKind::Excerpt,
                                        stage.excerptBytes,
                                        stage.excerptBytes);
    if (object && objects.commitHostObject(*object)) {
        task.ownedObjects.push_back(*object);
        excerptBytes += stage.excerptBytes;
        excerptTokens += stage.excerptTokens;
        setMetric("excerpt_bytes", excerptBytes);
        setMetric("excerpt_tokens", excerptTokens);
        setMetric("repair_input_excerpt_bytes", stage.excerptBytes);
    }
}

void
AgentWorkloadManager::processThinkArrivals(Tick now)
{
    for (TaskContext &task : tasks) {
        if (task.state != AgentTaskFsmState::Think || task.suppressed ||
                !task.thinkTargetTick || *task.thinkTargetTick > now)
            continue;
        if (config.stopAcceptingEnabled && now >= config.stopAcceptingAtTick) {
            suppressThink(task);
            for (TaskContext &follower : tasks)
                if (follower.userId == task.userId &&
                        follower.taskSeq > task.taskSeq &&
                        follower.state == AgentTaskFsmState::Think &&
                        !follower.suppressed && !follower.thinkTargetTick)
                    suppressThink(follower);
            continue;
        }
        const auto intent = buildGenerateIntent(
            image, commandIndexOfRound(task));
        if (!intent)
            continue;
        task.intent = intent;
        task.currentRequestId = intent->requestId;
        task.submitReadyTick = now;
        task.state = AgentTaskFsmState::SubmitNpu;
        emitSemantic("TASK_THINK_READY", "TASK", intent->requestId, "");
    }
}

void
AgentWorkloadManager::startGrantedStages(
    const std::vector<HostTaskId> &granted, Tick now)
{
    for (const HostTaskId id : granted) {
        const auto owner = hostTaskOwner.find(id.value());
        if (owner == hostTaskOwner.end())
            continue;
        TaskContext &task = tasks[owner->second.first];
        const HostStageKindV1 kind =
            static_cast<HostStageKindV1>(owner->second.second);
        if (kind == HostStageKindV1::Compile && task.generatedCode &&
                hostFaultDue(task,
                             agent_abi::HostLocalFaultSiteV1::OBJECT_READ)) {
            resources.release(id);
            queuedArrivals.erase(id.value());
            hostStageErrorDrain(
                task, agent_abi::HostLocalFaultSiteV1::OBJECT_READ, now);
            continue;
        }
        resources.markRunning(id);
        const AgentPlanStage &stage = stagePlan(task, kind);
        const Tick nominalTicks = ceilNsToTicks(stage.nominalNs);
        const Tick ioTicks = localIoTicks(stage);
        StageRun run;
        run.id = id;
        run.kind = kind;
        run.requestId = task.currentRequestId;
        run.startTick = now;
        run.actualDoneTick = now + std::max(nominalTicks, ioTicks);
        task.stage = run;
        task.state = kind == HostStageKindV1::Compile ?
            AgentTaskFsmState::Compiling :
            kind == HostStageKindV1::Test ? AgentTaskFsmState::Testing :
            AgentTaskFsmState::ParsingLog;
        if (kind == HostStageKindV1::Compile && task.generatedCode)
            objects.acquireConsumerRef(*task.generatedCode);
        if (kind == HostStageKindV1::LogParse && task.rawLog)
            objects.acquireConsumerRef(*task.rawLog);
        emitSemantic("HOST_STAGE_START", hostStageKindName(kind),
                     task.currentRequestId, "");
    }
}

void
AgentWorkloadManager::admitPendingStages(Tick now)
{
    std::vector<PendingEnqueue> admitted;
    for (const PendingEnqueue &pendingStage : pendingStages) {
        TaskContext &task = tasks[pendingStage.taskIndex];
        HostServiceTask service;
        service.hostTaskId = HostTaskId(
            hostTaskIdFor(task, pendingStage.kind));
        service.kind = pendingStage.kind;
        service.arrivalTick = now;
        service.userId = task.userId;
        service.taskSeq = task.taskSeq;
        service.repairRound = roundOf(task).repairRound;
        service.hostTokensRequired =
            stagePlan(task, pendingStage.kind).hostTokensRequired;
        if (service.hostTaskId.value() == 0 ||
                resources.tryEnqueue(service) !=
                    HostEnqueueResult::Accepted)
            continue;
        queuedArrivals[service.hostTaskId.value()] = now;
        emitSemantic("HOST_STAGE_ENQUEUE",
                     hostStageKindName(pendingStage.kind),
                     task.currentRequestId, "");
        admitted.push_back(pendingStage);
    }
    pendingStages.erase(
        std::remove_if(pendingStages.begin(), pendingStages.end(),
                       [&admitted](const PendingEnqueue &candidate) {
                           return std::find(admitted.begin(), admitted.end(),
                                            candidate) != admitted.end();
                       }),
        pendingStages.end());
}

void
AgentWorkloadManager::applyStopCutoff()
{
    if (config.stopAfterCompletedTasks == 0 ||
            completedTaskCount + failedTaskCount +
                    infraFailedTaskCount <
                config.stopAfterCompletedTasks)
        return;
    for (TaskContext &task : tasks)
        if (task.state == AgentTaskFsmState::Think && !task.suppressed)
            suppressThink(task);
}

std::optional<Tick>
AgentWorkloadManager::nextWakeTick() const
{
    std::optional<Tick> wake;
    const auto consider = [&wake](Tick tick) {
        if (!wake || tick < *wake)
            wake = tick;
    };
    for (const TaskContext &task : tasks) {
        if (task.stage)
            consider(task.stage->actualDoneTick);
        if (task.state == AgentTaskFsmState::Think && !task.suppressed &&
                task.thinkTargetTick)
            consider(*task.thinkTargetTick);
    }
    if (agingThresholdTicks != 0)
        for (const auto &entry : queuedArrivals) {
            const Tick candidate = entry.second + agingThresholdTicks;
            if (candidate > lastArbitratedTick)
                consider(candidate);
        }
    for (const PendingGenerateTerminal &pending :
         pendingGenerateTerminals)
        consider(pending.at);
    if (!pendingStages.empty() || resources.hasGrantableHead())
        consider(lastArbitratedTick + 1);
    return wake;
}

}
}

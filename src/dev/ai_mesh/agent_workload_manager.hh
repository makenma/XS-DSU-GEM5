#ifndef DEV_AI_MESH_AGENT_WORKLOAD_MANAGER_HH
#define DEV_AI_MESH_AGENT_WORKLOAD_MANAGER_HH

#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <stdexcept>
#include <tuple>
#include <utility>
#include <vector>

#include "dev/ai_mesh/agent_object_table.hh"
#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/agent_request_source.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/host_resource_manager.hh"
#include "sim/core.hh"

namespace gem5
{
namespace ai_mesh
{

enum class AgentTaskFsmState : uint8_t
{
    Think = 0,
    SubmitNpu,
    WaitNpu,
    CompileQueue,
    Compiling,
    TestQueue,
    Testing,
    ParseQueue,
    ParsingLog,
    BusinessDone,
    BusinessFailed,
    ReleaseSubmitting,
    ReleaseWaitCq,
    TaskLifecycleFinal,
};

enum class AgentTaskOutcome : uint8_t
{
    BusinessDone = 0,
    BusinessFailed,
    InfraFailed,
};

struct AgentWorkloadManagerConfig
{
    uint32_t hostComputeTokens = 24;
    uint32_t hostAvailableFractionQ16 = 65536;
    uint32_t hostCompileSlots = 4;
    uint32_t hostTestSlots = 6;
    uint32_t hostLogParseSlots = 4;
    uint32_t hostServiceQueueDepth = 64;
    uint32_t hostReleaseWaiterEntries = 64;
    uint32_t hostWeightCompile = 4;
    uint32_t hostWeightTest = 3;
    uint32_t hostWeightLogParse = 2;
    uint64_t hostAgingThresholdNs = 0;
    bool hostLocalIoEnabled = true;
    uint64_t hostLocalIoFixedNs = 1000;
    uint32_t hostLocalIoBytesPerNs = 64;
    uint32_t agentObjectTableEntries = 4096;
    uint32_t stopAfterCompletedTasks = 0;
    bool stopAcceptingEnabled = false;
    uint64_t stopAcceptingAtTick = 0;
    std::optional<agent_abi::HostLocalFaultSiteV1> hostFaultSite;
    uint32_t hostFaultTask = 0;
    uint32_t hostFaultRound = 0;
    uint64_t clockTicksPerSecond = 1000000000000;
};

struct AgentWorkloadFactsSink
{
    std::function<void(const std::string &kind, const std::string &object,
                       uint64_t requestId, const std::string &status)>
        recordSemantic;
    std::function<void(const std::string &name, uint64_t value)> setMetric;
};

const char *hostStageKindName(HostStageKindV1 kind);

class AgentWorkloadManager final : public AgentRequestSource
{
  public:
    AgentSubmissionIntent intentForRequest(uint64_t requestId) const;

    template<class Archive>
    void serialize(Archive &archive, unsigned)
    {
        auto digest = image.imageDigest();
        archive & digest;
        if (digest != image.imageDigest())
            throw std::runtime_error("E_CHECKPOINT_IDENTITY: Host plan differs");
        size_t count = tasks.size();
        archive & count;
        if (count != tasks.size())
            throw std::runtime_error("checkpoint task population differs from plan");
        for (auto &task : tasks)
            archive & task;
        checkpoint::fields(archive, resources, objects, queuedArrivals, pendingStages,
            pendingControls, pendingGenerateTerminals, lastArbitratedTick,
            agingReservationActive, hostFaultFired, boundaryAdmissions, liveReleaseWaiters,
            completedTaskCount, failedTaskCount, infraFailedTaskCount, agingReservationCount,
            rawLogBytes, scannedLogBytes, excerptBytes, excerptTokens);
        if constexpr (Archive::is_loading::value) {
            for (auto &task : tasks)
                if (task.intent)
                    task.intent = intentForRequest(task.intent->requestId);
            for (auto &intent : pendingControls)
                intent = intentForRequest(intent.requestId);
        }
    }
    AgentWorkloadManager(AgentPlanImage &&planImage,
                         const AgentWorkloadManagerConfig &managerConfig,
                         AgentWorkloadFactsSink factsSink);

    bool exhausted(uint64_t completedRequests) const override;
    std::vector<AgentSubmissionIntent> nextBatch(
        const std::set<uint64_t> &issuedRequestIds,
        uint64_t submissionAttempts, uint32_t sqDepth) override;

    void onEdge(Tick now);
    void observeSessionAdmitted(uint64_t requestId, bool admitted);
    bool onReleaseTerminal(const AgentSubmissionIntent &intent, uint16_t status, Tick now);
    void onGenerateTerminal(uint64_t requestId, bool success, Tick now);
    void scheduleGenerateTerminal(uint64_t requestId, bool success, Tick at);
    std::optional<Tick> nextWakeTick() const;
    bool allTasksTerminal() const;
    bool controlAnchorSuppressed(size_t controlIndex) const;
    bool controlIntentsPending() const { return !pendingControls.empty(); }
    void materializeControlAction(size_t controlIndex);
    std::optional<AgentSubmissionIntent> takeControlIntent();
    bool anchorForRequest(uint64_t requestId, uint32_t &userId,
                          uint32_t &taskSeq, uint16_t &repairRound) const;
    uint64_t completedTasks() const { return completedTaskCount; }
    uint64_t failedTasks() const { return failedTaskCount; }
    uint64_t infraFailedTasks() const { return infraFailedTaskCount; }
    void pauseGenerate(std::set<uint32_t> requiredItems)
    { boundaryAdmissions = std::move(requiredItems); }
    void resumeGenerate() { boundaryAdmissions.reset(); }
    bool generateIsPaused() const { return boundaryAdmissions.has_value(); }
    bool boundaryAdmissionsPending() const
    { return boundaryAdmissions && !boundaryAdmissions->empty(); }
    const HostResourceManager &hostResources() const { return resources; }

  private:
    struct StageRun
    {
        GEM5_CHECKPOINT_AGGREGATE()
        HostTaskId id{0};
        HostStageKindV1 kind = HostStageKindV1::Compile;
        uint64_t requestId = 0;
        Tick startTick = 0;
        Tick actualDoneTick = 0;
    };

    struct PendingEnqueue
    {
        GEM5_CHECKPOINT_AGGREGATE()
        size_t taskIndex = 0;
        HostStageKindV1 kind = HostStageKindV1::Compile;

        bool operator==(const PendingEnqueue &other) const
        {
            return taskIndex == other.taskIndex && kind == other.kind;
        }
    };

    struct TaskContext
    {
        GEM5_CHECKPOINT_FIELDS(state, roundIndex, thinkTargetTick, submitReadyTick,
            lifecycleFinalTick, suppressed, sessionAdmitted, sessionReleased,
            businessOutcome, autoReleaseCommand, currentRequestId, intent, stage,
            ownedObjects, generatedCode, rawLog)
        uint32_t userId = 0;
        uint32_t taskSeq = 0;
        uint16_t effectiveCap = 0;
        AgentTaskFsmState state = AgentTaskFsmState::Think;
        size_t roundIndex = 0;
        const AgentPlanTask *planTask = nullptr;
        std::optional<Tick> thinkTargetTick;
        Tick submitReadyTick = 0;
        Tick lifecycleFinalTick = 0;
        bool suppressed = false;
        bool sessionAdmitted = false;
        bool sessionReleased = false;
        std::optional<AgentTaskOutcome> businessOutcome;
        std::optional<size_t> autoReleaseCommand;
        uint64_t currentRequestId = 0;
        std::optional<AgentSubmissionIntent> intent;
        std::optional<StageRun> stage;
        std::vector<AgentObjectId> ownedObjects;
        std::optional<AgentObjectId> generatedCode;
        std::optional<AgentObjectId> rawLog;
    };

    Tick ceilNsToTicks(uint64_t ns) const;
    Tick localIoTicks(const AgentPlanStage &stage) const;
    const AgentPlanRound &roundOf(const TaskContext &task) const;
    const AgentPlanStage &stagePlan(const TaskContext &task,
                                    HostStageKindV1 kind) const;
    uint64_t hostTaskIdFor(const TaskContext &task,
                           HostStageKindV1 kind) const;
    size_t commandIndexOfRound(const TaskContext &task) const;
    void emitSemantic(const std::string &kind, const std::string &object,
                      uint64_t requestId, const std::string &status = "");
    void setMetric(const std::string &name, uint64_t value);
    void publishLiveObjects();
    void startGrantedStages(const std::vector<HostTaskId> &granted,
                            Tick now);
    void finishDueStages(Tick now);
    void processThinkArrivals(Tick now);
    void handleStageOutcome(TaskContext &task, HostStageKindV1 kind,
                            Tick now);
    void enterTaskTerminal(TaskContext &task, AgentTaskOutcome outcome,
                           Tick now);
    bool hostFaultDue(const TaskContext &task,
                      agent_abi::HostLocalFaultSiteV1 site) const;
    void hostStageErrorDrain(TaskContext &task,
                             agent_abi::HostLocalFaultSiteV1 site, Tick now);
    void suppressThink(TaskContext &task);
    void releaseGeneratedCodeRef(TaskContext &task);
    void commitRawLog(TaskContext &task, uint64_t bytes);
    void commitExcerpt(TaskContext &task, const AgentPlanStage &stage);
    void admitPendingStages(Tick now);
    void applyStopCutoff();
    void finishTaskLifecycle(TaskContext &task, Tick now);
    void scheduleSessionReleases();

    const AgentPlanImage image;
    const AgentWorkloadManagerConfig config;
    AgentWorkloadFactsSink sink;
    HostResourceManager resources;
    AgentObjectTable objects;
    std::vector<TaskContext> tasks;
    std::map<uint64_t, std::pair<size_t, size_t>> requestRounds;
    std::map<std::tuple<uint32_t, uint32_t, uint16_t, uint8_t>, uint64_t>
        hostTaskIds;
    std::map<uint64_t, std::pair<size_t, uint8_t>> hostTaskOwner;
    std::map<uint64_t, Tick> queuedArrivals;
    std::vector<PendingEnqueue> pendingStages;
    std::vector<AgentSubmissionIntent> pendingControls;
    struct PendingGenerateTerminal
    {
        GEM5_CHECKPOINT_AGGREGATE()
        uint64_t requestId;
        bool success;
        Tick at;
    };
    std::vector<PendingGenerateTerminal> pendingGenerateTerminals;
    void consumeDueGenerateTerminals(Tick now);
    Tick agingThresholdTicks = 0;
    Tick lastArbitratedTick = 0;
    bool agingReservationActive = false;
    bool hostFaultFired = false;
    std::optional<std::set<uint32_t>> boundaryAdmissions;
    uint32_t liveReleaseWaiters = 0;
    uint64_t completedTaskCount = 0;
    uint64_t failedTaskCount = 0;
    uint64_t infraFailedTaskCount = 0;
    uint64_t agingReservationCount = 0;
    uint64_t rawLogBytes = 0;
    uint64_t scannedLogBytes = 0;
    uint64_t excerptBytes = 0;
    uint64_t excerptTokens = 0;
};

}
}
#endif

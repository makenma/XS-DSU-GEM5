#ifndef DEV_AI_MESH_AGENT_AXI_DRIVER_HH
#define DEV_AI_MESH_AGENT_AXI_DRIVER_HH

#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_axi_work.hh"
#include "dev/ai_mesh/agent_protocol_layout.hh"
#include "dev/ai_mesh/agent_protocol_validation.hh"
#include "dev/ai_mesh/agent_request_source.hh"
#include "dev/ai_mesh/agent_submission_ledger.hh"
#include "dev/ai_mesh/agent_workload_manager.hh"
#include "dev/ai_mesh/cancel_join.hh"
#include "dev/ai_mesh/control_trigger_coordinator.hh"
#include "dev/ai_mesh/gate3_axi_transfer.hh"
#include "dev/ai_mesh/gate3_completion_ledger.hh"
#include "dev/ai_mesh/gate3_irq_commit_queue.hh"
#include "dev/ai_mesh/gate3_msi_id_pool.hh"
#include "dev/ai_mesh/gate3_observation_recorder.hh"
#include "dev/ai_mesh/gate3_sq_intake_ledger.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/axi/axi_types.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

struct AgentAxiDriverParams;

namespace ai_mesh
{

class AgentAxiDriver : public ClockedObject,
                       public axi::AxiWriteCommitObserver,
                       public Gate3PhaseParticipant
{
  public:
    using Params = AgentAxiDriverParams;

    explicit AgentAxiDriver(const Params &p);

    void init() override;
    void startup() override;
    void wakeup();

    void onAxiWriteCommitted(const axi::AxiAddressRequest &request,
                             const std::vector<axi::AxiDataPacket> &beats,
                             axi::AxiResp resp) override;
    void onAxiWriteCommittedWithMeta(
        const axi::AxiAddressPacket &packet,
        const std::vector<axi::AxiDataPacket> &beats,
        axi::AxiResp resp) override;
    void onGate3FatalCut(Tick tick) override;
    void onGate3NormalCommit(Tick tick) override;

    void notifySqAccepted(uint64_t requestId);
    void notifyFirstOutputChunk(uint64_t requestId);
    void notifyGenerateTerminalVisible(uint64_t requestId);
    void notifyReleaseTerminal(uint64_t requestId);

    uint64_t doorbellFaultTail() const { return controlDoorbellFaultTail; }
    bool drainBegun() const
    { return stage == Stage::Drain || stage == Stage::Done || exitIssued; }

  private:
    enum class Stage : uint8_t
    {
        Prepare,
        Fence,
        Doorbell,
        DoorbellResponse,
        DoorbellProbe,
        DoorbellProbeResponse,
        Completion,
        CqRead,
        CqReadResponse,
        MetadataRead,
        Ack,
        AckResponse,
        Drain,
        Done,
    };

    struct TickEvent : public Event
    {
        AgentAxiDriver *owner;
        explicit TickEvent(AgentAxiDriver *value) : Event(), owner(value) {}
        void process() override { owner->wakeup(); }
        const char *description() const override
        { return "ai_mesh.agent_axi_driver.tick"; }
    };

    struct FatalDrainEvent : public Event
    {
        AgentAxiDriver *owner;
        explicit FatalDrainEvent(AgentAxiDriver *value) : Event(), owner(value) {}
        void process() override { owner->wakeup(); }
        const char *description() const override
        { return "ai_mesh.agent_axi_driver.fatal_drain"; }
    };

    struct ManagerWakeEvent : public Event
    {
        AgentAxiDriver *owner;
        explicit ManagerWakeEvent(AgentAxiDriver *value) : Event(), owner(value) {}
        void process() override { owner->wakeup(); }
        const char *description() const override
        { return "ai_mesh.agent_axi_driver.manager_wake"; }
    };

    struct SubmissionContext
    {
        SqSeq sqSequence;
        RequestId requestId;
        CompletionCookie cookie;
        uint64_t sessionId = 0;
        uint32_t userId = 0;
        uint32_t taskSequence = 0;
        uint16_t repairRound = 0;
        uint64_t metadataAddress = 0;
        uint32_t metadataCapacity = 0;
    };

    struct PendingWriteResponse
    {
        Gate3WriteWork work;
        axi::AxiResp response = axi::AxiResp::Okay;
        uint64_t responseOrdinal = 0;
    };

    enum class MetadataReadPhase : uint8_t
    {
        Header,
        Tail,
    };

    enum class SubmitPhase : uint8_t
    {
        Idle,
        Fence,
        Doorbell,
        DoorbellResponse,
    };

    enum class CompletePhase : uint8_t
    {
        Idle,
        CqRead,
        Metadata,
        AckWait,
        Ack,
        AckResponse,
    };

    void scheduleTick();
    Tick clockEdgeAtOrAfter(Tick deadline) const;
    void scheduleManagerWake(std::optional<Tick> deadline);
    bool managerEdgeDue() const;
    void prepareRequest();
    void prepareLocalRecords();
    void preparePlanRecords(const AgentSubmissionIntent &intent,
                            uint64_t sqSequence, uint64_t requestId,
                            uint64_t cookie);
    void planPump();
    void planAdvanceCompletion();
    void planAdvanceSubmission();
    void planIssueDoorbell();
    void beginCompletionAck();
    void pumpControlDelivery();
    void suppressUnreachableControls();
    void scheduleControlDeliveryWake();
    void beginControlSubmission();
    void noteControlCommandCompletion(uint16_t status);
    void resolveCancelJoin();
    void advanceGenerateBusiness(uint64_t requestId, uint16_t status);
    bool controlCqValid(const agent_abi::CqDescriptor &value) const;
    void noteControlLocalSubmitFailed(uint64_t requestId);
    bool runtimeExhausted(uint64_t completedRequests) const;
    void driveWrite();
    void consumeB();
    void consumeR();
    void handleDoorbellResponse(const Gate3WriteWork &work,
                                axi::AxiResp response);
    void handleCqRead();
    void finishGenerateConsume();
    void handleMetadataRead();
    void handleAckResponse(const Gate3WriteWork &work,
                           axi::AxiResp response);
    void startCqRead();
    void startMetadataRead();
    Gate3MetadataExpectation metadataExpectation() const;
    void processIrqCommits();
    void startDoorbellProbe(uint64_t tail, uint64_t requestId,
                            uint64_t cookie);
    void startAck(bool future = false);
    void finishIfDrained();
    void requestFatal(agent_abi::DetailCode value);
    void requestFault(agent_abi::FaultSiteV1 site,
                      std::vector<uint8_t> objectKey,
                      uint64_t issueOrdinal,
                      Gate3PhysicalSourceTokenV1 sourceToken);
    void recordFatalPublication(const PendingWriteResponse &response);
    void recordAxi(const Gate3WriteWork &work, const char *channel,
                   uint64_t bytes, const std::string &response,
                   const std::string &wstrb = "",
                   std::optional<uint64_t> beatAddress = std::nullopt);
    void recordAxi(const Gate3ReadWork &work, const char *channel,
                   uint64_t bytes, const std::string &response = "",
                   std::optional<uint64_t> beatAddress = std::nullopt);
    void recordSemantic(const std::string &kind, const std::string &object,
                        std::optional<uint64_t> absoluteSeq = std::nullopt,
                        std::optional<uint64_t> requestId = std::nullopt,
                        std::optional<uint64_t> cookie = std::nullopt,
                        const std::string &status = "");
    std::vector<uint8_t> encodeSq(uint64_t sequence, uint64_t requestId,
                                  uint64_t cookie) const;
    std::vector<uint8_t> encodePlanSq(
        uint64_t sequence, uint64_t requestId, uint64_t cookie,
        const AgentSubmissionIntent &intent,
        uint32_t parameterBlockBytes) const;
    std::vector<uint8_t> encodeParameter(uint64_t requestId) const;
    std::vector<uint8_t> encodeControl(uint64_t sequence) const;
    std::vector<uint8_t> makePayload(uint64_t seed, uint64_t bytes) const;
    void storeBytes(uint64_t address, const std::vector<uint8_t> &data);
    Gate3WriteWork makeWrite(const std::string &object,
                             const std::string &control,
                             std::optional<uint64_t> absoluteSeq,
                             std::optional<uint64_t> requestId,
                             std::optional<uint64_t> cookie,
                             uint64_t address, uint32_t axiId,
                             const std::vector<uint8_t> &data,
                             uint8_t qos = 0) const;
    Gate3ReadWork makeRead(const std::string &object,
                           const std::string &control,
                           std::optional<uint64_t> absoluteSeq,
                           std::optional<uint64_t> requestId,
                           std::optional<uint64_t> cookie,
                           uint64_t address, uint64_t bytes,
                           uint32_t axiId, uint8_t qos = 0) const;
    bool mode(const char *value) const;
    void storeSubmissionContext(const SubmissionContext &context,
                                const AgentSubmissionIntent &intent);
    uint64_t requestIdForCurrent() const;
    uint64_t cookieForCurrent() const;
    uint64_t sqSequenceForCurrent() const;
    uint64_t sqTailForCurrent() const;
    uint64_t cqSequenceForCurrent() const;
    uint64_t sqAddress(uint64_t sequence) const;
    uint64_t parameterAddress(uint64_t sequence) const;
    uint64_t promptAddress(uint64_t sequence) const;
    uint64_t outputAddress(uint64_t sequence) const;
    uint64_t metadataAddress(uint64_t sequence) const;
    uint64_t cqAddress(uint64_t sequence) const;
    uint64_t msiAddress(uint64_t sequence) const;

    axi::AxiInitiatorAdapter *const master;
    axi::AxiTargetAdapter *const target;
    Gate3ObservationRecorder *const recorder;
    const bool planDrivenMode;
    const uint32_t dataBusBytes;
    const uint32_t sqDepth;
    const uint32_t cqDepth;
    const uint32_t controlBytes;
    const uint16_t maxBurstBeats;
    const uint64_t hostBase;
    const uint64_t npuControlBase;
    const uint64_t agentProxyControlBase;
    const uint64_t cqRingBase;
    const uint64_t msiBase;
    const std::string profile;
    uint32_t requestCount;
    const uint32_t localVisibilityDelay;
    const uint32_t requestIdCapacity;
    const uint32_t drainCycles;
    const uint32_t doorbellAxiId;
    const uint32_t ackAxiId;
    const uint32_t controlDoorbellErrorOrdinal;
    const uint32_t mutateCancelCommandOrdinal;
    const uint32_t mutateCancelCommandStatus;
    const Tick controlDoorbellBHoldTicks;
    const Tick cqReadDelayTicks;
    const Tick metadataReadDelayTicks;

    AgentProtocolLayout ringLayout;
    Gate3AxiTransferPlanner transferPlanner;
    SubmissionLedger ledger;
    Gate3IrqCommitQueue irqCommitQueue;
    std::unique_ptr<AgentRequestSource> requestSource;
    AgentWorkloadManager *workloadManager = nullptr;
    std::unique_ptr<ControlTriggerCoordinator> coordinator;
    std::map<uint64_t, size_t> controlIndexByRequest;
    std::vector<uint64_t> controlRequestByIndex;
    std::map<uint64_t, CancelJoinRecord> cancelJoins;
    uint32_t cancelJoinCapacity = 4;
    CancelJoinRecord *findCancelJoinByCommand(uint64_t requestId);
    CancelJoinRecord *findCancelJoinByTarget(uint64_t requestId);
    std::set<uint64_t> standaloneControlRequests;
    std::set<uint64_t> consumedGenerateRequests;
    std::set<uint64_t> acceptedGenerateRequests;
    Stage stage = Stage::Prepare;
    TickEvent tickEvent;
    FatalDrainEvent fatalDrainEvent;
    ManagerWakeEvent managerWakeEvent;
    std::optional<Gate3WriteWork> activeWrite;
    std::optional<Gate3ReadWork> activeRead;
    std::optional<PendingWriteResponse> pendingWriteResponse;
    uint64_t currentSqSequence = 0;
    uint64_t currentRequestId = 0;
    uint64_t currentCookie = 0;
    uint32_t currentParameterBlockBytes = 0;
    uint64_t currentCqSequence = 0;
    uint64_t completedRequests = 0;
    uint64_t submissionAttempts = 0;
    uint64_t doorbellAttempts = 0;
    uint64_t localReadyTick = 0;
    uint64_t metadataReadReadyTick = 0;
    uint64_t cqReadReadyTick = 0;
    bool cqReadDelayPending = false;
    uint64_t controlDoorbellFaultTail = 0;
    uint64_t controlDoorbellBHeldUntil = 0;
    MetadataReadPhase metadataReadPhase = MetadataReadPhase::Header;
    uint32_t metadataTotalBytes = 0;
    std::vector<uint8_t> metadataData;
    uint64_t drainUntilTick = 0;
    uint32_t cqConsumer = 0;
    uint32_t cqAck = 0;
    uint32_t liveCqObligations = 0;
    bool currentPrepared = false;
    bool staleSent = false;
    bool duplicateSent = false;
    bool emptySent = false;
    bool sqBackpressureObserved = false;
    bool futureAckSent = false;
    bool futureAckProbe = false;
    bool irqSeen = false;
    uint64_t observedSqHead = 0;
    uint64_t currentDoorbellTail = 0;
    bool exitIssued = false;
    std::set<uint64_t> issuedRequestIds;
    std::set<uint64_t> consumedSqSequences;
    std::vector<SubmissionContext> submissionContexts;
    std::vector<AgentSubmissionIntent> planIntents;
    AgentSubmissionIntent currentIntent;
    SubmitPhase submitPhase = SubmitPhase::Idle;
    AgentSubmissionIntent submitIntent;
    uint64_t submitSqSequence = 0;
    uint64_t submitRequestId = 0;
    uint64_t submitCookie = 0;
    uint64_t submitDoorbellTail = 0;
    uint32_t submitParameterBlockBytes = 0;
    Tick submitLocalReadyTick = 0;
    CompletePhase completePhase = CompletePhase::Idle;
    uint16_t observedCqStatus = 0;
    uint16_t observedCqFlags = 0;
    uint32_t observedCqValue = 0;
    bool cqMetadataPending = false;
    uint64_t responseIngressOrdinal = 0;
};

}
}
#endif

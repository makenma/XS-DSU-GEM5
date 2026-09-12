#ifndef DEV_AI_MESH_NPU_SERVING_FRONTEND_HH
#define DEV_AI_MESH_NPU_SERVING_FRONTEND_HH

#include <array>
#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_axi_work.hh"
#include "dev/ai_mesh/agent_protocol_layout.hh"
#include "dev/ai_mesh/agent_protocol_validation.hh"
#include "dev/ai_mesh/gate3_axi_transfer.hh"
#include "dev/ai_mesh/session_record_table.hh"
#include "dev/ai_mesh/gate3_completion_ledger.hh"
#include "dev/ai_mesh/gate3_msi_id_pool.hh"
#include "dev/ai_mesh/gate3_observation_recorder.hh"
#include "dev/ai_mesh/gate3_sq_intake_ledger.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/npu_execution_selector.hh"
#include "dev/ai_mesh/npu_request_executor.hh"
#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/axi/axi_types.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

struct NpuServingFrontendParams;

namespace ai_mesh
{

inline constexpr uint32_t kSurrogateTimingBreakdownTlvBytes = 72;

class AgentAxiDriver;

class NpuServingFrontend : public ClockedObject,
                           public axi::AxiWriteCommitObserver,
                           public axi::AxiWritePreCommitPolicy,
                           public Gate3PhaseParticipant
{
  public:
    using Params = NpuServingFrontendParams;

    explicit NpuServingFrontend(const Params &p);

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

    axi::AxiWritePreCommitPolicy::Decision onWritePreCommit(
        const axi::AxiAddressRequest &request,
        const std::vector<axi::AxiDataPacket> &beats,
        axi::AxiResp transport_response) override;
    void onGate3FatalCut(Tick tick) override;
    void onGate3NormalCommit(Tick tick) override;

  private:
    enum class Stage : uint8_t
    {
        Doorbell,
        SqRead,
        SqReadResponse,
        ParameterRead,
        ParameterReadResponse,
        PromptRead,
        PromptReadResponse,
        Capacity,
        Execute,
        SqHead,
        SqHeadResponse,
        Output,
        OutputResponse,
        Metadata,
        MetadataResponse,
        CqEntry,
        CqEntryResponse,
        CqTail,
        CqTailResponse,
        Msi,
        MsiResponse,
        Done,
    };

    struct TickEvent : public Event
    {
        NpuServingFrontend *owner;
        explicit TickEvent(NpuServingFrontend *value) : Event(), owner(value) {}
        void process() override { owner->wakeup(); }
        const char *description() const override
        { return "ai_mesh.npu_serving_frontend.tick"; }
    };

    struct FatalDrainEvent : public Event
    {
        NpuServingFrontend *owner;
        explicit FatalDrainEvent(NpuServingFrontend *value) : Event(), owner(value) {}
        void process() override { owner->wakeup(); }
        const char *description() const override
        { return "ai_mesh.npu_serving_frontend.fatal_drain"; }
    };

    struct Doorbell
    {
        uint64_t tail = 0;
        uint64_t txn = 0;
    };

    struct TerminalResult
    {
        CqObligationId obligationId;
        Tick readyTick = 0;
        uint64_t sqSequence = 0;
        uint64_t requestId = 0;
        uint64_t cookie = 0;
        uint8_t qos = 0;
        uint16_t status = 0;
        uint16_t flags = 0;
        uint32_t detailCode = 0;
        std::optional<uint32_t> outputBytes;
        bool readyRecorded = false;
    };

    struct TerminalEvent : public Event
    {
        NpuServingFrontend *owner;
        explicit TerminalEvent(NpuServingFrontend *value) :
            Event(), owner(value) {}
        void process() override { owner->wakeup(); }
        const char *description() const override
        { return "ai_mesh.npu_serving_frontend.terminal"; }
    };

    struct ExecutionEvent : public Event
    {
        NpuServingFrontend *owner;
        explicit ExecutionEvent(NpuServingFrontend *value) :
            Event(), owner(value) {}
        void process() override
        {
            if (owner->recorder->fatalRecorded())
                return;
            if (!owner->executionCurrent()) {
                if (owner->businessParked() && !owner->currentValid)
                    owner->restoreParkedBusiness();
                return;
            }
            owner->startOutput();
            owner->scheduleTick();
        }
        const char *description() const override
        { return "ai_mesh.npu_serving_frontend.execution"; }
    };

    struct ParkedBusiness
    {
        uint64_t sqSequence = 0;
        uint64_t requestId = 0;
        uint64_t cookie = 0;
        uint64_t sessionId = 0;
        uint64_t inputAddress = 0;
        uint64_t inputBytes = 0;
        uint64_t outputAddress = 0;
        uint64_t metadataAddress = 0;
        std::optional<CqObligationId> obligationId;
        std::optional<NpuExecutionRequest> executionRequest;
        uint32_t userId = 0;
        uint32_t taskSeq = 0;
        uint32_t maxOutputTokens = 0;
        uint16_t repairRound = 0;
        uint8_t qos = 0;
        bool sessionAdmitted = false;
        uint64_t publishChunkBytes = 0;
        std::optional<Gate3WriteWork> outputWork;
        uint64_t outputCommittedBytes = 0;
        bool outputChunkNotified = false;
        Tick executionDeadlineTick = 0;
        uint64_t serviceNs = 0;
        Tick readyTick = 0;
        uint64_t deadlineTick = kNpuNoDeadline;
    };

    void scheduleTick();
    void consumeB();
    void consumeR();
    void driveWrite();
    void processDoorbells();
    void commitCurrentSq();
    void startSqRead();
    void startParameterRead();
    void startPromptRead();
    void startSqHead();
    void startOutput();
    void startMetadata();
    void stageTerminalResult();
    void latchGenerateTerminal(uint64_t requestId);
    bool activateReadyTerminal();
    void emitMaturedTerminals();
    void scheduleTerminalWake();
    void startCqEntry();
    void startCqTail();
    void startMsi();
    void handleSqRecord();
    void handleParameterRecord();
    void handleControlParameter(const agent_abi::ParameterHeader &value);
    bool controlMatrixValid(const agent_abi::ParameterHeader &value) const;
    void resolveControlCommand(const agent_abi::ParameterHeader &value);
    void stageTerminalRecord(const std::optional<CqObligationId> &obligation,
                             Tick readyTick, uint64_t sqSequence,
                             uint64_t requestId, uint64_t cookie, uint8_t qos,
                             uint16_t status, uint16_t flags,
                             uint32_t detailCode,
                             std::optional<uint32_t> outputBytes);
    void saveParkedBusiness();
    void restoreParkedBusiness();
    void loadBusinessContext(const ParkedBusiness &parked);
    void enqueueCurrentBusiness(uint64_t serviceNs);
    void dispatchAcceptedBusiness();
    void cancelQueuedTarget(uint64_t requestId, Tick now);
    void scheduleExecutionEvent(Tick deadline);
    bool businessParked() const { return parkedBusiness.has_value(); }
    bool executionCurrent() const
    { return currentValid && stage == Stage::Execute; }
    void cancelParkedTarget(Tick now);
    void maybeNotifyFirstOutputChunk(std::optional<uint64_t> requestId,
                                     bool outputComplete);
    bool pendingControlIntake() const;
    bool outputControlIntakeSuppresses() const;
    void parkOutputForControlIntake();
    bool resolveSessionRelease(uint64_t sessionId, uint64_t kvHandle,
                               uint32_t generation, uint16_t &status);
    void handlePromptRecord();
    void handleWriteResponse(const Gate3WriteWork &work,
                             axi::AxiResp response);
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
                           uint32_t axiId, uint8_t qos = 0,
                           uint32_t maxBeatBytes = 0) const;
    std::vector<uint8_t> makeControl(uint64_t sequence) const;
    std::vector<uint8_t> makeOutput(uint64_t sequence) const;
    std::vector<uint8_t> makeMetadata(uint64_t sequence, uint32_t status) const;
    std::vector<uint8_t> makeCq(uint64_t sequence, uint64_t requestId,
                                uint64_t cookie, uint16_t status,
                                uint16_t flags) const;
    std::optional<uint32_t> terminalOutputBytes() const;
    std::vector<uint8_t> makeMsi(uint64_t sequence) const;
    bool mode(const char *value) const;
    void requestFatal(agent_abi::DetailCode value);
    void requestFault(agent_abi::FaultSiteV1 site,
                      std::vector<uint8_t> objectKey,
                      uint64_t issueOrdinal,
                      Gate3PhysicalSourceTokenV1 sourceToken);
    void cutFatalOwnership();
    void queueErrorCompletion(uint16_t status, uint16_t flags,
                              uint32_t detail_code);
    void finishCurrent();
    void processAckRetire();
    void commitAckRetire();
    void commitAckTargets();
    void commitMsiCompletions();
    void scheduleRetire();
    void pollWriteResponses();
    void handleMsiResponse(const Gate3WriteWork &work,
                           axi::AxiResp response,
                           uint64_t responseOrdinal);

    uint64_t sqAddress(uint64_t sequence) const;
    uint64_t parameterAddress(uint64_t sequence) const;
    uint64_t promptAddress(uint64_t sequence) const;
    uint64_t outputAddress(uint64_t sequence) const;
    uint64_t metadataAddress(uint64_t sequence) const;
    uint64_t cqAddress(uint64_t sequence) const;
    uint64_t msiAddress(uint64_t sequence) const;

    axi::AxiInitiatorAdapter *const master;
    axi::AxiTargetAdapter *const controlTarget;
    Gate3ObservationRecorder *const recorder;
    AgentAxiDriver *const driverRef;
    const uint32_t acceptedQueueEntries;
    const uint32_t dataBusBytes;
    const uint32_t sqDepth;
    const uint32_t cqDepth;
    const uint32_t controlBytes;
    const uint16_t maxBurstBeats;
    const uint64_t hostBase;
    const uint64_t npuControlBase;
    const uint64_t agentProxyControlBase;
    const uint64_t msiBase;
    const uint32_t kvSessionRecordEntries;
    const uint64_t outputBErrorRequest;
    const uint32_t outputBErrorSegment;
    const bool controlCqFirst;
    const std::string profile;
    const bool planExecution;
    uint32_t requestCount;
    const uint32_t sqReadIssueDelay;
    const uint32_t doorbellAxiId;
    const uint32_t sqHeadAxiId;
    const uint32_t cqTailAxiId;
    const uint32_t cqEntryAxiId;
    const uint32_t msiAxiId;
    const uint32_t msiAxiIdCount;
    AgentProtocolLayout ringLayout;
    Gate3AxiTransferPlanner transferPlanner;
    Gate3CompletionLedger completionLedger;
    Gate3MsiIdPool msiIdPool;
    Gate3SqIntakeLedger sqIntakeLedger;
    std::unique_ptr<NpuRequestExecutor> requestExecutor;
    class RetireEvent : public Event
    {
      public:
        NpuServingFrontend *frontend;
        explicit RetireEvent(NpuServingFrontend *f) : Event(), frontend(f) {}
        void process() override { frontend->processAckRetire(); }
        const char *description() const override
        { return "ai_mesh.gate3.ack_retire"; }
    };
    RetireEvent retireEvent;
    TerminalEvent terminalEvent;
    ExecutionEvent executionEvent;
    uint64_t ackReceivedSeq = 0;
    uint64_t msiConfirmedSeq = 0;
    uint32_t liveCqObligations = 0;

    Stage stage = Stage::Doorbell;
    TickEvent tickEvent;
    FatalDrainEvent fatalDrainEvent;
    std::deque<Doorbell> doorbells;
    std::optional<Gate3WriteWork> activeWrite;
    std::optional<Gate3ReadWork> activeRead;
    std::vector<uint8_t> sqData;
    std::vector<uint8_t> parameterData;
    std::vector<uint8_t> promptData;
    uint64_t currentSqSequence = 0;
    uint64_t currentRequestId = 0;
    uint64_t currentCookie = 0;
    uint64_t currentParameterAddress = 0;
    uint32_t currentParameterBlockBytes = 0;
    uint64_t currentInputAddress = 0;
    uint64_t currentInputBytes = 0;
    uint64_t currentOutputCapacityBytes = 0;
    uint64_t currentMetadataCapacityBytes = 0;
    uint64_t currentRequestedProfileKey = 0;
    uint32_t currentInputTokens = 0;
    uint8_t currentQos = 0;
    uint32_t currentParameterBytes = 0;
    uint64_t currentOutputAddress = 0;
    uint64_t currentMetadataAddress = 0;
    uint64_t currentCqSequence = 0;
    uint16_t currentCqStatus = 0;
    uint16_t currentCqFlags = 0;
    uint32_t currentDetailCode = 0;
    uint64_t currentSessionId = 0;
    uint32_t currentUserId = 0;
    uint32_t currentTaskSeq = 0;
    uint32_t currentMaxOutputTokens = 0;
    uint32_t currentWorkloadItemId = 0;
    uint16_t currentRepairRound = 0;
    uint16_t currentProgramId = 0;
    uint16_t currentProfileId = 0;
    std::array<uint8_t, 32> currentInputDigest{};
    std::array<uint8_t, 32> currentWorkloadDigest{};
    std::optional<NpuExecutionRequest> lastExecutionRequest;
    std::optional<ParkedBusiness> parkedBusiness;
    std::map<uint64_t, ParkedBusiness> acceptedQueue;
    std::map<uint64_t, uint64_t> generateDeadlineTicks;
    bool executingBusiness = false;
    Tick currentAcceptTick = 0;
    uint64_t currentPublishChunkBytes = 0;
    uint64_t outputCommittedBytes = 0;
    bool outputChunkNotified = false;
    SessionRecordTable sessionRecords;
    std::set<uint64_t> seenGenerateRequests;
    std::set<uint64_t> terminalGenerateRequests;
    uint16_t currentControlOpcode = 0;
    uint16_t currentSqFlags = 0;
    std::optional<uint32_t> currentOutputBytes;
    bool currentSessionAdmitted = false;
    uint64_t expectedDoorbellTail = 1;
    uint64_t advertisedSqTail = 0;
    uint64_t nextCqSequence = 0;
    uint64_t sqConsumer = 0;
    uint64_t cqAssignments = 0;
    uint64_t msiIssued = 0;
    bool currentValid = false;
    bool currentDuplicate = false;
    bool currentError = false;
    bool cqBackpressureObserved = false;
    std::set<uint64_t> acceptedDoorbellTails;
    std::optional<CqObligationId> currentObligationId;
    std::map<uint64_t, TerminalResult> terminalResults;
    Tick terminalBarrierTick = 0;
    std::map<uint32_t, Gate3WriteWork> msiWrites;
    std::map<uint32_t, std::deque<axi::AxiBBeat>> deferredWriteResponses;
    std::vector<Gate3WriteWork> pendingMsiCompletions;
    std::vector<uint64_t> pendingAckCommits;
    std::vector<CqObligationId> pendingAckRetirements;
    uint64_t responseIngressOrdinal = 0;
    Tick sqReadIssueReadyTick = 0;
    std::optional<SqIntakeId> currentSqIntakeId;
    bool sqCommitPending = false;
    bool outputBErrorFired = false;
};

}
}
#endif

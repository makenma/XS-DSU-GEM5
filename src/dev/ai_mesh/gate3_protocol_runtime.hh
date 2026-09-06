#ifndef DEV_AI_MESH_GATE3_PROTOCOL_RUNTIME_HH
#define DEV_AI_MESH_GATE3_PROTOCOL_RUNTIME_HH

#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_submission_ledger.hh"
#include "dev/ai_mesh/agent_protocol_validation.hh"
#include "dev/ai_mesh/agent_protocol_layout.hh"
#include "dev/ai_mesh/gate3_completion_ledger.hh"
#include "dev/ai_mesh/gate3_axi_transfer.hh"
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
struct NpuServingFrontendParams;

namespace ai_mesh
{

struct Gate3WriteWork
{
    std::string object;
    std::string control;
    std::string direction;
    std::optional<uint64_t> absoluteSeq;
    std::optional<uint64_t> requestId;
    std::optional<uint64_t> cookie;
    uint64_t address = 0;
    uint32_t axiId = 0;
    uint8_t qos = 0;
    axi::AxiAddressRequest request;
    std::vector<axi::AxiWBeat> beats;
    std::vector<uint64_t> semanticBytes;
    std::vector<Gate3AxiSegment> segments;
    std::vector<uint8_t> data;
    size_t segmentIndex = 0;
    size_t nextBeat = 0;
    bool awAccepted = false;
    uint64_t txn = 0;
};

struct Gate3ReadWork
{
    std::string object;
    std::string control;
    std::string direction;
    std::optional<uint64_t> absoluteSeq;
    std::optional<uint64_t> requestId;
    std::optional<uint64_t> cookie;
    uint64_t address = 0;
    uint32_t axiId = 0;
    uint8_t qos = 0;
    axi::AxiAddressRequest request;
    std::vector<Gate3AxiSegment> segments;
    size_t segmentIndex = 0;
    uint16_t beatIndex = 0;
    uint16_t beatCount = 0;
    uint64_t bytes = 0;
    uint64_t bytesConsumed = 0;
    std::vector<uint8_t> data;
    bool accepted = false;
    bool sawError = false;
    uint64_t txn = 0;
    std::optional<SqIntakeId> sqIntakeId;
};

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

    void scheduleTick();
    void prepareRequest();
    void prepareLocalRecords();
    void driveWrite();
    void consumeB();
    void consumeR();
    void handleDoorbellResponse(const Gate3WriteWork &work,
                                axi::AxiResp response);
    void handleCqRead();
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
    const uint32_t dataBusBytes;
    const uint32_t sqDepth;
    const uint32_t cqDepth;
    const uint32_t controlBytes;
    const uint16_t maxBurstBeats;
    const uint64_t hostBase;
    const uint64_t npuControlBase;
    const uint64_t agentProxyControlBase;
    const std::string profile;
    const uint32_t requestCount;
    const uint32_t localVisibilityDelay;
    const uint32_t requestIdCapacity;
    const uint32_t drainCycles;
    const uint32_t doorbellAxiId;
    const uint32_t ackAxiId;

    AgentProtocolLayout ringLayout;
    Gate3AxiTransferPlanner transferPlanner;
    SubmissionLedger ledger;
    Gate3IrqCommitQueue irqCommitQueue;
    Stage stage = Stage::Prepare;
    TickEvent tickEvent;
    FatalDrainEvent fatalDrainEvent;
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
    uint16_t observedCqStatus = 0;
    uint16_t observedCqFlags = 0;
    uint32_t observedCqValue = 0;
    uint64_t responseIngressOrdinal = 0;
};

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
    bool activateReadyTerminal();
    void emitMaturedTerminals();
    void scheduleTerminalWake();
    void startCqEntry();
    void startCqTail();
    void startMsi();
    void handleSqRecord();
    void handleParameterRecord();
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
    const uint32_t dataBusBytes;
    const uint32_t sqDepth;
    const uint32_t cqDepth;
    const uint32_t controlBytes;
    const uint16_t maxBurstBeats;
    const uint64_t hostBase;
    const uint64_t npuControlBase;
    const uint64_t agentProxyControlBase;
    const std::string profile;
    const uint32_t requestCount;
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
    uint8_t currentQos = 0;
    uint32_t currentParameterBytes = 0;
    uint64_t currentOutputAddress = 0;
    uint64_t currentMetadataAddress = 0;
    uint64_t currentCqSequence = 0;
    uint16_t currentCqStatus = 0;
    uint16_t currentCqFlags = 0;
    uint32_t currentDetailCode = 0;
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
};

}
}
#endif

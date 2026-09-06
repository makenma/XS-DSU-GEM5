#ifndef DEV_AI_MESH_GATE3_OBSERVATION_RECORDER_HH
#define DEV_AI_MESH_GATE3_OBSERVATION_RECORDER_HH

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_runtime_types.hh"
#include "dev/ai_mesh/gate3_fatal_reducer.hh"
#include "sim/eventq.hh"
#include "sim/sim_object.hh"

namespace gem5
{

struct Gate3ObservationRecorderParams;

namespace ai_mesh
{

struct Gate3Event
{
    Tick tick = 0;
    uint8_t phase = 0;
    std::string kind;
    std::string object;
    std::optional<uint64_t> absoluteSeq;
    std::optional<uint64_t> requestId;
    std::optional<uint64_t> cookie;
    std::optional<uint32_t> slot;
    std::optional<uint64_t> generation;
    std::optional<uint64_t> txn;
    std::string channel;
    std::string direction;
    std::string control;
    std::optional<uint32_t> axiId;
    std::optional<uint64_t> address;
    std::optional<uint32_t> size;
    std::string response;
    uint64_t bytes = 0;
    std::string wstrb;
    std::string status;
};

struct Gate3FinalState
{
    uint64_t sqTentativeProducerSeq = 0;
    uint64_t sqCommittedProducerSeq = 0;
    uint64_t sqObservedHeadSeq = 0;
    uint64_t sqReusableHeadSeq = 0;
    uint64_t npuSqConsumerSeq = 0;
    uint64_t npuCqProducerSeq = 0;
    uint64_t cqMsiIssuedSeq = 0;
    uint64_t cqNotifiedSeq = 0;
    uint64_t driverCqConsumerSeq = 0;
    uint64_t npuCqAckSeq = 0;
    uint32_t liveSubmissions = 0;
    uint32_t liveContexts = 0;
    uint32_t liveCqObligations = 0;
    uint32_t msiRobEntries = 0;
    uint32_t ackWaitB = 0;
    bool fatal = false;
    uint32_t coreStarts = 0;
    uint32_t cqAssignments = 0;
    uint32_t irqDeliveries = 0;
};

class Gate3PhaseParticipant
{
  public:
    virtual ~Gate3PhaseParticipant() = default;
    virtual void onGate3FatalCut(Tick tick) = 0;
    virtual void onGate3NormalCommit(Tick tick) = 0;
};

class Gate3ObservationRecorder : public SimObject
{
  public:
    using Params = Gate3ObservationRecorderParams;

    explicit Gate3ObservationRecorder(const Params &p);

    void registerPhaseParticipant(Gate3PhaseParticipant *participant);
    void requestNormalCommit();

    void record(Gate3Event event);
    bool hasEvent(const std::string &kind, const std::string &object,
                  uint64_t absoluteSeq) const;
    bool hasAxiResponse(const std::string &control,
                        const std::string &channel,
                        uint64_t absoluteSeq,
                        const std::string &response) const;
    void requestFault(
        agent_abi::FaultSiteV1 site, uint32_t componentLocalId,
        uint32_t endpointId, std::vector<uint8_t> objectKey,
        uint64_t issueOrdinal, Gate3PhysicalSourceTokenV1 sourceToken);
    void requestInvariant(
        agent_abi::InvariantSiteV1 site, uint32_t componentLocalId,
        uint32_t endpointId, std::vector<uint8_t> objectKey,
        Gate3PhysicalSourceTokenV1 sourceToken);
    bool fatalPending() const { return fatalReducer.pending(); }
    bool fatalRecorded() const { return _fatalRecorded; }
    struct FatalSqIntake
    {
        uint64_t intakeId = 0;
        uint64_t expectedSqSeq = 0;
        uint64_t readTag = 0;
        std::string firstError;
        std::string stateAtCut;
        std::string terminalEvidence;
    };

    struct FatalCqObligation
    {
        uint64_t id = 0;
        uint64_t sqSequence = 0;
        uint64_t requestId = 0;
        std::optional<uint64_t> cqSequence;
        std::optional<uint32_t> slot;
        std::string state;
    };

    struct FatalControlRecord
    {
        uint64_t issueOrdinal = 0;
        uint64_t sequence = 0;
        uint32_t axiId = 0;
        std::string stateAtCut;
        std::string terminalEvidence = "NONE";
        std::string targetCommitEvidence = "UNKNOWN";
        std::string transactionTokenWire;
        std::optional<std::string> responseTokenWire;
    };

    struct FatalPublicationRecord
    {
        uint64_t publicationId = 0;
        uint64_t doorbellIssueOrdinal = 0;
        uint64_t baseSequence = 0;
        uint64_t pendingTail = 0;
        std::vector<uint64_t> requestIds;
        std::string stateAtCut;
        std::string terminalEvidence;
        std::string targetCommitEvidence;
        bool ambiguous = false;
        std::string transactionTokenWire;
        std::string responseTokenWire;
    };

    void recordFatalSqIntake(FatalSqIntake intake);
    void updateFatalSqIntakeTerminal(
        SqIntakeId intakeId, const std::string &terminalEvidence);
    void recordFatalCqObligation(FatalCqObligation obligation);
    void recordHostAckIssue(uint64_t issueOrdinal, uint64_t sequence,
                            uint32_t axiId,
                            Gate3PhysicalSourceTokenV1 transactionToken);
    void recordMsiIssue(uint64_t issueOrdinal, uint64_t sequence,
                        uint32_t axiId,
                        Gate3PhysicalSourceTokenV1 transactionToken);
    void recordHostAckTerminal(
        uint64_t issueOrdinal, const std::string &terminalEvidence,
        bool targetCommitted, Gate3PhysicalSourceTokenV1 responseToken);
    void recordMsiTerminal(
        uint64_t issueOrdinal, const std::string &terminalEvidence,
        bool targetCommitted, Gate3PhysicalSourceTokenV1 responseToken);
    void recordHostAckTargetCommit(uint64_t issueOrdinal);
    void recordMsiTargetCommit(uint64_t issueOrdinal);
    void recordFatalPublication(
        uint64_t publicationId, uint64_t doorbellIssueOrdinal,
        uint64_t baseSequence, uint64_t pendingTail,
        std::vector<uint64_t> requestIds, bool targetCommitted,
        std::string terminalEvidence, bool ambiguous,
        Gate3PhysicalSourceTokenV1 transactionToken,
        Gate3PhysicalSourceTokenV1 responseToken);
    const std::map<uint64_t, FatalSqIntake> &fatalSqIntakes() const
    {
        return _fatalSqIntakes;
    }

    std::string fatalSymbol() const;
    uint32_t fatalValue() const;
    void setMetric(const std::string &name, uint64_t value);
    uint64_t metric(const std::string &name) const;
    void write(const Gate3FinalState &final) const;

  private:
    struct PhaseBarrierEvent : public Event
    {
        Gate3ObservationRecorder *owner;
        explicit PhaseBarrierEvent(Gate3ObservationRecorder *value) :
            Event(Event::Maximum_Pri), owner(value) {}
        void process() override { owner->finalizePhase(); }
        const char *description() const override
        { return "ai_mesh.gate3.phase_barrier"; }
    };

    void schedulePhase(bool requested);
    void finalizePhase();
    static std::string optionalValue(const std::optional<uint64_t> &value);
    static std::string optionalValue(const std::optional<uint32_t> &value);
    static std::string fieldValue(const std::string &value);

    const std::string outputPath;
    std::vector<Gate3Event> events;
    std::map<std::string, uint64_t> metrics;
    bool _fatalRecorded = false;
    PhaseBarrierEvent phaseBarrierEvent;
    std::vector<Gate3PhaseParticipant *> phaseParticipants;
    std::map<uint64_t, FatalSqIntake> _fatalSqIntakes;
    std::map<uint64_t, FatalCqObligation> _fatalCqObligations;
    std::map<uint64_t, FatalControlRecord> _hostAckRecords;
    std::map<uint64_t, FatalControlRecord> _msiRecords;
    std::map<uint64_t, uint64_t> _msiTransactionOrdinals;
    std::map<uint64_t, FatalPublicationRecord> _fatalPublications;
    Gate3FatalReducer fatalReducer;
};

}
}

#endif

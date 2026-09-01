#ifndef __MEM_AXI_AXI_TRACE_TESTER_HH__
#define __MEM_AXI_AXI_TRACE_TESTER_HH__

#include <array>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/ruby/common/Consumer.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "sim/clocked_object.hh"

namespace gem5
{

struct AxiTraceTesterParams;

namespace axi
{

class AxiTraceTester : public ClockedObject, public ruby::Consumer
{
  public:
    using Params = AxiTraceTesterParams;

    explicit AxiTraceTester(const Params &p);
    void startup() override;
    void wakeup() override;
    void print(std::ostream &out) const override;

  private:
    enum class Kind { Write, Read };
    enum class StrobeMode { Full, Alternating };

    struct Transaction
    {
        Kind kind = Kind::Write;
        size_t sourceIndex = 0;
        size_t targetIndex = 0;
        AxiAddressRequest request;
        bool wBeforeAw = false;
        uint8_t dataSeed = 0;
        StrobeMode strobeMode = StrobeMode::Full;
        uint64_t expectedUid = 0;
        uint64_t expectedTargetSeq = 0;
        uint64_t expectedResponseSeq = 0;
        std::optional<uint64_t> expectedWriteOrdinal;
        uint64_t arrivalCycle = 0;
        AxiResp expectedResponse = AxiResp::Okay;
        size_t planIndex = 0;

        bool addressAccepted = false;
        AxiCommonMeta actualMeta;
        std::optional<uint64_t> actualWriteOrdinal;
        Tick addressAcceptedTick = 0;
        uint16_t nextW = 0;
        uint16_t responses = 0;
        std::vector<AxiWBeat> writeBeats;
        Tick lastWAcceptedTick = 0;
        Tick completionTick = 0;
        bool completed = false;
    };

    struct TraceObservation
    {
        Tick tick = 0;
        uint64_t phase = 0;
        uint64_t event = 0;
        uint64_t observationOrder = 0;
        std::optional<AxiChannel> channel;
        std::optional<size_t> transactionIndex;
        std::optional<uint16_t> beatIndex;
        std::optional<uint64_t> payloadDigest;
        uint64_t occupancy = 0;
    };

    struct MeasurementCounters
    {
        std::array<uint64_t, 5> packetsInjected{};
        std::array<uint64_t, 5> flitsInjected{};
        std::array<uint64_t, 5> wireBytesInjected{};
        std::array<uint64_t, 5> inputVcOccupancyFlitCycles{};
        std::array<uint64_t, 5> inputVcFullVcCycles{};
        std::array<uint64_t, 5> inputVcFullEvents{};
        std::array<uint64_t, 5> inputVcMaxOccupancy{};
        std::array<uint64_t, 5> routerCreditStalls{};
        std::array<uint64_t, 5> vcAllocStalls{};
        std::array<uint64_t, 5> niCreditStalls{};
        std::array<uint64_t, 5> niVcBusyCycles{};
    };

    Transaction parseTransaction(const std::string &spec) const;
    AxiWBeat makeWBeat(const Transaction &txn, uint16_t index) const;
    void driveSequential();
    void driveConcurrent();
    void driveWriteAddress(Transaction &txn);
    void driveWriteData(Transaction &txn);
    void driveReadAddress(Transaction &txn);
    void consumeSequentialResponse();
    void consumeConcurrentResponses();
    size_t findResponseTransaction(Kind kind, size_t source_index,
                                   uint32_t axi_id) const;
    void completeB(size_t transaction_index, const AxiBBeat &response);
    void completeR(size_t transaction_index, AxiRBeat response);
    void commitShadow(const Transaction &txn);
    void checkTargetMemory(const Transaction &txn) const;
    uint8_t shadowByte(uint64_t address) const;
    bool allTransactionsCompleted() const;
    bool allAdaptersIdle() const;
    void updateHighWaterAndProgress();
    void checkProgressWatchdog();
    bool measurementEnabled() const;
    MeasurementCounters readMeasurementCounters() const;
    void updateMeasurementWindow();
    void checkCaseRequirements() const;
    void writeResult() const;
    void writeCreditLedger() const;
    void writeEventTrace() const;
    void driveRuntimeFault();
    void writeResidualState(const AxiInitiatorResidual &residual) const;
    void captureAcceptedAddress(Transaction &txn,
                                const AxiAddressPacket &packet);
    void observe(uint64_t phase, uint64_t event,
                 std::optional<AxiChannel> channel,
                 std::optional<size_t> transaction_index,
                 std::optional<uint16_t> beat_index,
                 std::optional<uint64_t> payload_digest,
                 uint64_t occupancy);
    uint64_t sourceOccupancy(size_t source_index) const;

    std::vector<AxiInitiatorAdapter *> initiators;
    std::vector<AxiTargetAdapter *> targets;
    ruby::garnet::GarnetNetwork *const network;
    std::vector<uint32_t> targetNodes;
    std::vector<Transaction> transactions;
    std::vector<std::vector<size_t>> writesBySource;
    std::vector<std::vector<size_t>> readsBySource;
    std::vector<size_t> nextAwBySource;
    std::vector<size_t> nextWBySource;
    std::vector<size_t> nextArBySource;
    const std::string caseName;
    const std::string resultJson;
    const std::string eventTraceJsonl;
    const std::string creditLedgerJson;
    const std::string residualStateJson;
    const std::string runtimeFault;
    const uint64_t seed;
    const std::vector<uint32_t> wireHeaderBytes;
    const uint32_t dataBusBytes;
    const uint32_t drainCycles;
    const bool concurrent;
    const Cycles bConsumerStallUntil;
    const Cycles rConsumerStallUntil;
    const int32_t expectedRouterVnet;
    const uint32_t expectedRouterDepth;
    const uint32_t progressWatchdogCycles;
    const uint64_t issueStopCycle;
    const std::vector<uint32_t> livenessBoundComponents;
    const std::vector<uint32_t> localDeliveryDepths;
    const std::vector<uint64_t> measurementWindowCycles;
    const int32_t measurementVnet;

    std::map<uint64_t, uint8_t> shadowMemory;
    size_t currentTransaction = 0;
    uint32_t quietCycles = 0;
    uint64_t writesCompleted = 0;
    uint64_t readsCompleted = 0;
    uint64_t errorTransactions = 0;
    uint64_t wBeatsAccepted = 0;
    uint64_t rBeatsConsumed = 0;
    size_t maxOrphanTransactions = 0;
    size_t maxTargetBReady = 0;
    size_t maxTargetRReady = 0;
    size_t maxSourceB = 0;
    size_t maxSourceR = 0;
    std::vector<uint64_t> completionsBySource;
    std::vector<size_t> completionOrder;
    uint64_t lastProgressValue = 0;
    uint32_t eligibleNoProgressCycles = 0;
    uint32_t maxEligibleNoProgressCycles = 0;
    bool observedEligibleWork = false;
    bool exitRequested = false;
    uint64_t addressAdmissionAttempts = 0;
    uint64_t addressAdmissionsAccepted = 0;
    uint64_t retryCycles = 0;
    std::optional<Cycles> lastRetryCycle;
    uint64_t localFifoFullStalls = 0;
    uint64_t nextObservationOrder = 0;
    std::vector<TraceObservation> traceObservations;
    bool measurementStarted = false;
    bool measurementCompleted = false;
    Tick measurementStartTick = 0;
    Tick measurementEndTick = 0;
    MeasurementCounters measurementStart;
    MeasurementCounters measurementDelta;
};

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_TRACE_TESTER_HH__

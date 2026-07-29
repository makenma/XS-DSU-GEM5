#ifndef __HOMELINKLAYER__HH__
#define __HOMELINKLAYER__HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <iosfwd>
#include <map>
#include <optional>
#include <unordered_map>
#include <variant>
#include <vector>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HomeLinkLayer.hh"
#include "mem/cache/CHI/HnfCcTypes.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/ruby/common/Consumer.hh"

namespace gem5::Chi {

class HomeNodeFull;
class HnfCoherencyController;

class HomeLinkLayer : public ruby::Consumer
{
  public:
    HomeLinkLayer(HomeNodeFull *hnf, uint32_t block_size,
                  uint32_t data_beat_bytes, uint32_t num_poc_entries,
                  bool enable_retry);

    void wakeup() override;
    void print(std::ostream& out) const override;
    /** Local work which can make progress on another HomeNode edge. */
    bool hasWork() const;
    /**
     * Protocol ownership which must be empty at a drained/checkpoint boundary.
     *
     * This is deliberately broader than hasWork(): a granted static retry
     * reservation waits for an upstream reissue and must block drain without
     * forcing the HomeNode to poll every cycle.
     */
    bool hasProtocolOwnershipForDrain() const;
    void quiesceNewRequests() { acceptNewRxReq = false; }
    void resumeNewRequests() { acceptNewRxReq = true; }
    bool newRequestsQuiesced() const { return !acceptNewRxReq; }
    /** Admission predicate for the RX port's pre-enqueue drain gate. */
    bool acceptsIncomingFlit(ChannelType ch, const FlitVariant& flit) const;
    bool mayGenerateSlcsfIntent() const;

    void
    setRxPort(ChiCommonPort* port)
    {
        rxport = port;
        if (rxport) {
            rxport->setRxAdmissionCallback(
                [this](ChannelType ch, const FlitVariant& flit) {
                    return acceptsIncomingFlit(ch, flit);
                });
        }
    }
    void setCc(HnfCoherencyController* controller) { cc = controller; }

#ifdef UNIT_TEST
    /** Narrow state setup used by the drain ownership unit tests. */
    void installStaticRetryReservationForTest(
        uint32_t srcid, uint8_t pcrdtype);
    void installPendingRetryForTest(uint32_t srcid, uint8_t pcrdtype);
    void installRequestPipelineEntryForTest(const RawReq& req);
#endif

  private:
    static constexpr size_t NumCh =
        static_cast<size_t>(ChannelType::NUM_CHANNELS);
    static constexpr size_t NumLlStages = 4;

    enum class TxnKind : uint8_t
    {
        Read,
        Maintenance,
        Write
    };

    enum class TxRspKind : uint8_t
    {
        ShortPath,
        RetryAck,
        PCrdGrant,
        CompDBIDResp,
        Comp
    };

    enum class StageAction : uint8_t
    {
        Stay,
        Advance,
        Drop,
        Error
    };

    struct StageResult
    {
        StageAction action = StageAction::Stay;
    };

    enum class LlPriority : uint8_t
    {
        Low = 0,
        Medium = 1,
        High = 2,
        HHigh = 3,
        Num = 4
    };

    enum class ResourceClass : uint8_t
    {
        Low = 0,
        Medium = 1,
        High = 2,
        HHigh = 3,
        Fvb = 4
    };

    enum class TokenState : uint8_t
    {
        Free,
        AllocPendingCcAck,
        WorkingDynamic,
        WorkingStatic,
        WorkingFvb,
        RetireHeldForPCrdGrant,
        StaticReserved
    };

    struct TxnKey
    {
        uint32_t srcid = 0;
        uint32_t txnid = 0;

        bool operator==(const TxnKey& other) const
        {
            return srcid == other.srcid && txnid == other.txnid;
        }
    };

    struct TxnKeyHash
    {
        std::size_t operator()(const TxnKey& key) const;
    };

    struct PipeEntry
    {
        FlitVariant flit{};
        ChannelType channel = ChannelType::REQ;
        uint32_t stage = 0;
        uint64_t seq = 0;
        uint64_t enterCycle = 0;
        int tokenId = -1;
        uint8_t priority = 0;
        uint8_t resourceClass = 0;
        bool dynamicAllocated = false;
        bool staticAllocated = false;
        bool sentToCc = false;
    };

    using StageQueue = std::deque<PipeEntry>;
    using ChannelPipe = std::array<StageQueue, NumLlStages>;

    struct MinimalHnfTxn
    {
        bool valid = false;
        RawReq req{};
        TxnKey key{};
        TxnKind kind = TxnKind::Read;
        uint32_t entry = 0;
        uint8_t dbid = 0;
        uint32_t expectedDataBytes = 0;
        uint32_t receivedDataBytes = 0;
        int tokenId = -1;
        bool compAckReceived = false;
        bool readDataQueued = false;
        bool compDbidQueued = false;
    };

    struct TxRspPending
    {
        RawRsp rsp{};
        TxRspKind kind = TxRspKind::Comp;
        std::optional<uint32_t> retireEntry;
    };

    struct TxDatPending
    {
        RawDat dat{};
    };

    struct RetryRecord
    {
        uint64_t originSeq = 0;
        uint64_t retryCycle = 0;
        uint32_t srcid = 0;
        uint32_t tgtid = 0;
        uint32_t txnid = 0;
        uint8_t qos = 0;
        uint8_t priority = 0;
        uint8_t pcrdtype = 0;
    };

    struct CreditEvent
    {
        ChannelType ch = ChannelType::REQ;
        uint8_t amount = 1;
        uint64_t dueCycle = 0;
    };

    struct ResourceToken
    {
        int id = -1;
        TokenState state = TokenState::Free;
        uint8_t resourceClass = 0;
        uint8_t reqPriority = 0;
        uint32_t ownerSrcid = 0;
        uint32_t ownerTxnid = 0;
        uint8_t pcrdtype = 0;
        uint64_t allocatedSeq = 0;
        uint64_t allocatedCycle = 0;
        bool pendingStatic = false;
        bool pendingFvb = false;
        uint32_t staticOwnerSrcid = 0;
        uint8_t staticPcrdtype = 0;
        uint8_t staticPriority = 0;
    };

    struct CcAdmitResult
    {
        bool valid = false;
        uint64_t seq = 0;
        int tokenId = -1;
        bool accepted = false;
        uint64_t dueCycle = 0;
        RawReq req{};
    };

    struct CcRetireEvent
    {
        bool valid = false;
        int tokenId = -1;
        uint64_t allocationSeq = 0;
        uint8_t resourceClass = 0;
        uint8_t reqPriority = 0;
        uint32_t srcid = 0;
        uint32_t txnid = 0;
        uint8_t pcrdtype = 0;
    };

    struct PendingWinner
    {
        bool valid = false;
        uint8_t priority = 0;
        uint32_t srcid = 0;
        uint32_t tgtid = 0;
        uint32_t txnid = 0;
        uint8_t qos = 0;
        uint8_t pcrdtype = 0;
    };

    struct PendingRetryTable
    {
        std::map<uint8_t, std::map<uint32_t,
                 std::map<uint8_t, uint16_t>>> cnt;

        uint16_t totalForSrc(uint32_t srcid) const;
        void increment(uint8_t prio, uint32_t srcid, uint8_t pcrdtype);
        void decrement(uint8_t prio, uint32_t srcid, uint8_t pcrdtype);
        bool empty() const;
    };

    HomeNodeFull *m_homenode = nullptr;
    ChiCommonPort *rxport = nullptr;
    HnfCoherencyController *cc = nullptr;

    uint32_t blockSize = 64;
    uint32_t dataBeatBytes = 32;
    uint32_t maxEntries = 32;
    bool retryEnabled = true;

    uint64_t llCycle = 0;
    uint64_t nextSeq = 1;

    std::array<ChannelPipe, NumCh> rxPipe{};

    std::vector<ResourceToken> tokens;
    std::vector<MinimalHnfTxn> entries;
    std::unordered_map<TxnKey, uint32_t, TxnKeyHash> txnLookup;
    std::unordered_map<uint32_t, uint32_t> dbidLookup;

    PendingRetryTable pendingRetry;

    std::deque<HnfLinkToCcReq> linkToCcQ;
    std::deque<CcAdmitResult> ccAdmitQ;
    std::deque<CcRetireEvent> ccRetireQ;
    std::deque<RetryRecord> retryDecisionQ;
    std::deque<RetryRecord> pendingRetryRecords;
    std::deque<TxRspPending> shortPathFifo;
    std::deque<TxRspPending> retryAckFifo;
    std::deque<TxRspPending> pcrdGrantFifo;
    std::deque<TxRspPending> mainPathFifo;
    std::deque<TxDatPending> txDatQ;
    std::deque<CreditEvent> creditEvents;

    size_t retryAckFifoDepth = 16;
    size_t pcrdGrantFifoDepth = 16;
    bool enableTxRspShortPath = false;
    bool acceptNewRxReq = true;

    void doTxReqArb();
    void doTxSnpArb();
    void doTxRspArb();
    void doTxDatArb();
    void doCreditEvents();
    void doCcResultAndRetire();
    void doRetryWakeup();
    void doPcrdGrantWakeup();
    void doRxPipelineWakeup();
    void sampleRxPortsToH0();

    StageResult dispatchStage(PipeEntry& entry);
    StageResult doStageH0Req(PipeEntry& entry, RawReq& req);
    StageResult doStageH1Req(PipeEntry& entry, RawReq& req);
    StageResult doStageH2Req(PipeEntry& entry, RawReq& req);
    StageResult doStageH3Req(PipeEntry& entry, RawReq& req);
    StageResult doStageRsp(PipeEntry& entry, RawRsp& rsp);
    StageResult doStageDat(PipeEntry& entry, RawDat& dat);
    StageResult doStageSnp(PipeEntry& entry, RawSnp& snp);

    bool hasPendingWork() const;
    bool pipelineHasWork() const;
    bool txQueuesHaveWork() const;
    bool portHasRxFlit() const;
    bool portHasRxReq() const;
    bool requestPipelineHasWork() const;
    bool hasHeldRetireToken() const;
    bool hasAllocatedProtocolToken() const;
    bool hasRetryProtocolOwnership() const;
    bool hasLegacyTransactionOwnership() const;
    void scheduleNextCycle();

    LlPriority mapPriority(const RawReq& req) const;
    std::optional<int> allocDynamicToken(LlPriority prio);
    std::optional<int> findStaticReservation(uint32_t srcid,
                                             uint8_t pcrdtype) const;
    void reserveTokenPendingCcAck(int tokenId, const PipeEntry& entry,
                                  const RawReq& req, LlPriority prio,
                                  bool isStatic);
    void releaseToken(ResourceToken& token);
    bool pendingPrioCanUseResource(uint8_t pendingPrio,
                                   uint8_t resourceClass) const;
    bool hasEligiblePendingRetry(uint8_t resourceClass) const;
    PendingWinner selectPendingRetryForResource(uint8_t resourceClass);

    void scheduleRxCreditReturn(ChannelType ch, uint8_t amount,
                                uint64_t dueCycle);
    void queueCcAdmit(const PipeEntry& entry, const RawReq& req,
                      int tokenId, LlPriority prio, bool isStatic);
    void processCcAdmitResult(const CcAdmitResult& result);
    bool tokenEventIsStale(const ResourceToken& token,
                           uint64_t allocation_seq, uint32_t srcid,
                           uint32_t txnid, const char* event) const;
    void queueRetire(uint32_t entry);
    void queueRetire(const HnfCcRetireInfo& info);

    bool allocateRequest(const RawReq& req, uint32_t entry, int tokenId);
    void retireEntry(uint32_t entry);
    void releaseEntry(uint32_t entry);

    void enqueueComp(const RawReq& req, std::optional<uint32_t> retireEntry);
    void enqueueCompDbid(MinimalHnfTxn& txn);
    void enqueueReadData(MinimalHnfTxn& txn);
    RawRsp makeRsp(const RawReq& req, uint8_t opcode) const;

    bool sendRspQueue(std::deque<TxRspPending>& q);

    MinimalHnfTxn* findTxn(const RawRsp& rsp);
    MinimalHnfTxn* findTxn(const RawDat& dat);

    bool isSupportedReadReq(uint8_t opcode) const;
    bool isSupportedMaintenanceReq(uint8_t opcode) const;
    bool isSupportedWriteReq(uint8_t opcode) const;
    uint32_t expectedDataBytes(const RawReq& req) const;
};

}

#endif

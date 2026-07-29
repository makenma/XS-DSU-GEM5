#ifndef __HNF_COHERENCY_CONTROLLER_HH__
#define __HNF_COHERENCY_CONTROLLER_HH__

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <unordered_map>
#include <vector>

#include "mem/cache/CHI/HnfPOCQStateGraph.hh"
#include "mem/cache/CHI/HnfSLCSFRequest.hh"
#include "mem/cache/CHI/HnfSLCSFResponse.hh"
#include "mem/cache/CHI/HnfSeqPOCQStateGraph.hh"

namespace gem5::Chi
{

class HnfSLCSF;

class HnfCoherencyController
{
  public:
    enum class SlcLookupPhase : uint8_t
    {
        None,
        IssuePending,
        Waiting,
        ResponseLatched
    };

    enum class SlcUpdatePhase : uint8_t
    {
        None,
        IssuePending,
        Waiting,
        ResponseLatched,
        ReplayWait
    };

    HnfCoherencyController(uint32_t block_size, uint32_t data_beat_bytes,
                           uint32_t num_entries, uint32_t sn_node_id,
                           bool direct_sn_fake_data,
                           uint32_t rnf_slices);

    void setSlcsf(HnfSLCSF* slcsf) { slcsfUnit = slcsf; }

    HnfCcAdmitResult acceptLinkReq(const HnfLinkToCcReq& req,
                                   uint64_t cycle);
    std::optional<HnfCcRetireInfo> acceptRxRsp(const RawRsp& rsp);
    std::optional<HnfCcRetireInfo> acceptRxDat(const RawDat& dat);
    void serviceInternalWork();
    void serviceInternalWork(Tick current_tick);
    bool hasDeferredRetire() const { return !deferredRetireQ.empty(); }
    HnfCcRetireInfo popDeferredRetire();

    /**
     * Validate and latch one terminal SLCSF response.  This is public so the
     * embedded service boundary can later be replaced by a standalone object
     * without weakening response identity checks.
     */
    void consumeSlcsfResponse(SlcSfResponse response);

    SlcLookupPhase slcLookupPhase(uint32_t entry) const;
    SlcSfReqId slcLookupReqId(uint32_t entry) const;
    const HnfSlcLookupResult& slcLookupResult(uint32_t entry) const;
    const SlcSfCommitToken& slcCommitToken(uint32_t entry) const;
    SlcUpdatePhase slcUpdatePhase(uint32_t entry) const;
    SlcSfReqId slcUpdateReqId(uint32_t entry) const;
    PocqState pocqState(uint32_t entry) const;
    Tick slcsfRetryNotBeforeTick(uint32_t entry) const;

    bool hasWork() const;
    bool hasTxReq() const { return !txReqQ.empty(); }
    bool hasTxSnp() const { return !txSnpQ.empty(); }
    bool hasTxDat() const { return !txDatQ.empty(); }
    bool hasTxRsp() const { return !txRspQ.empty(); }
    bool hasTxWork() const
    { return hasTxReq() || hasTxSnp() || hasTxDat() || hasTxRsp(); }

    const HnfCcTxReq& frontTxReq() const;
    void popTxReq();
    void notifyTxReqSent(uint32_t entry);

    const HnfCcTxSnp& frontTxSnp() const;
    void popTxSnp();

    const HnfCcTxDat& frontTxDat() const;
    void popTxDat();

    const HnfCcTxRsp& frontTxRsp() const;
    void popTxRsp();

  private:
    struct Entry
    {
        HnfCcEntryState state = HnfCcEntryState::Idle;
        PocqState pocqState = PocqState::Idle;
        PocqTxnKind txnKind = PocqTxnKind::Unknown;
        RawReq req{};
        uint64_t seq = 0;
        uint64_t acceptCycle = 0;
        uint64_t blockAddr = 0;
        int tokenId = -1;
        uint8_t priority = 0;
        uint8_t resourceClass = 0;
        bool isStatic = false;
        bool mcReadIssued = false;
        uint32_t mcDataBytes = 0;
        uint32_t writeDataBytes = 0;
        bool needsCompAck = false;
        bool expectsWriteData = false;
        std::optional<uint32_t> sleepingOn;
        std::vector<uint8_t> data;
        HnfSlcLookupResult slcLookupResult{};
        SlcLookupPhase slcLookupPhase = SlcLookupPhase::None;
        SlcSfReqId slcLookupReqId{};
        std::optional<SlcSfRequest> pendingSlcLookup;
        std::optional<SlcSfResponse> latchedSlcResponse;
        SlcSfCommitToken slcCommitToken{};
        SlcUpdatePhase slcUpdatePhase = SlcUpdatePhase::None;
        SlcSfReqId slcUpdateReqId{};
        std::optional<SlcSfRequest> pendingSlcUpdate;
        std::optional<SlcSfResponse> latchedSlcUpdateResponse;
        Tick retryNotBeforeTick = 0;
        bool responseDataDirty = false;
        uint32_t snoopTxnId = 0;
        uint64_t snoopPendingTargets = 0;
        bool snoopDataReceived = false;
        bool slcsfReplay = false;
    };

    struct SeqPocqEntry
    {
        bool valid = false;
        uint64_t seqId = 0;
        SeqPocqState state = SeqPocqState::Idle;
        uint64_t blockAddr = 0;
        uint32_t homeNodeId = 0;
        uint32_t owner = 0;
        uint64_t sharers = 0;
        uint32_t snoopTxnId = 0;
        uint64_t pendingTargets = 0;
        bool dataReceived = false;
        std::vector<uint8_t> data;
    };

    HnfSLCSF* slcsfUnit = nullptr;
    SlcSfReqIdAllocator slcSfReqIds;

    uint32_t blockSize = 64;
    uint32_t dataBeatBytes = 32;
    uint32_t maxEntries = 32;
    uint32_t snNodeId = 0;
    bool directSnFakeData = true;
    uint32_t rnfSlices = 1;
    uint32_t nextSnoopTxnId = 0x80000000U;

    POCQ_StateGraph pocqGraph;
    SEQ_POCQ_StateGraph seqPocqGraph;
    std::vector<Entry> entries;
    SeqPocqEntry seqPocqEntry;
    std::deque<HnfCcTxReq> txReqQ;
    std::deque<HnfCcTxSnp> txSnpQ;
    std::deque<HnfCcTxDat> txDatQ;
    std::deque<HnfCcTxRsp> txRspQ;
    std::deque<HnfCcRetireInfo> deferredRetireQ;
    std::unordered_map<uint32_t, uint32_t> snoopTxnToEntry;

    uint64_t blockAddr(const RawReq& req) const;
    uint32_t expectedDataBytes(const RawReq& req) const;
    bool entryAllocated(const Entry& entry) const;
    bool hasAddressHazard(uint32_t entry, uint64_t addr) const;
    std::optional<uint32_t> findTxn(uint32_t srcid, uint32_t txnid) const;

    std::optional<HnfCcRetireInfo> stepPocq(uint32_t entry,
                                            const PocqEvent& event);
    std::optional<HnfCcRetireInfo> executePocqAction(
        uint32_t entry, PocqActionKind action, const PocqEvent& event);
    void startReadFlow(uint32_t entry);
    void queueSnoops(uint32_t entry);
    void startSlcUpdate(uint32_t entry);
    void completeMaintenance(uint32_t entry);
    void removeSharer(uint32_t entry);
    void completeSnoopTarget(uint32_t entry, uint32_t responder,
                             bool has_data);
    uint32_t targetRouteId(uint32_t target, uint64_t addr) const;
    void queueMcRead(uint32_t entry);
    void queueCompData(uint32_t entry, const std::vector<uint8_t>& data);
    void queueComp(uint32_t entry);
    void queueCompDBIDResp(uint32_t entry);
    void storeWriteData(uint32_t entry);
    void deferRetire(std::optional<HnfCcRetireInfo> retire);
    HnfCcRetireInfo retireEntry(uint32_t entry);
    HnfCcRetireInfo makeRetireInfo(const Entry& entry) const;
    void wakeSleepingEntries(uint64_t addr);
    bool hasMainAddressHazard(uint64_t addr) const;
    uint32_t allocateSnoopTxnId();
    void startSeqPocq();
    void stepSeqPocq(const SeqPocqEvent& event);
    void queueSeqSnoops();
    void completeSeqSnoopTarget(uint32_t responder, bool has_data);
    void retrySlcsfReplayEntries(Tick current_tick);
    void retryPendingSlcLookups();
    void retryPendingSlcUpdates();
    void continueLatchedSlcLookups();
    void continueLatchedSlcUpdates();
    void latchVisibleSlcResponses();
    void tryIssueSlcLookup(uint32_t entry);
    void tryIssueSlcUpdate(uint32_t entry);
    void handleSlcsfReplay(uint32_t entry, const SlcSfResponse& response);
};

} // namespace gem5::Chi

#endif // __HNF_COHERENCY_CONTROLLER_HH__

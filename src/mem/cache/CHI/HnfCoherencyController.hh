#ifndef __HNF_COHERENCY_CONTROLLER_HH__
#define __HNF_COHERENCY_CONTROLLER_HH__

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <vector>

#include "mem/cache/CHI/HnfPOCQStateGraph.hh"

namespace gem5::Chi
{

class HnfSLCSF;

class HnfCoherencyController
{
  public:
    HnfCoherencyController(uint32_t block_size, uint32_t data_beat_bytes,
                           uint32_t num_entries, uint32_t sn_node_id,
                           bool direct_sn_fake_data);

    void setSlcsf(HnfSLCSF* slcsf) { slcsfUnit = slcsf; }

    HnfCcAdmitResult acceptLinkReq(const HnfLinkToCcReq& req,
                                   uint64_t cycle);
    std::optional<HnfCcRetireInfo> acceptRxRsp(const RawRsp& rsp);
    std::optional<HnfCcRetireInfo> acceptRxDat(const RawDat& dat);

    bool hasWork() const;
    bool hasTxReq() const { return !txReqQ.empty(); }
    bool hasTxDat() const { return !txDatQ.empty(); }
    bool hasTxRsp() const { return !txRspQ.empty(); }
    bool hasTxWork() const { return hasTxReq() || hasTxDat() || hasTxRsp(); }

    const HnfCcTxReq& frontTxReq() const;
    void popTxReq();
    void notifyTxReqSent(uint32_t entry);

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
        bool slcUpdatePending = false;
    };

    HnfSLCSF* slcsfUnit = nullptr;

    uint32_t blockSize = 64;
    uint32_t dataBeatBytes = 32;
    uint32_t maxEntries = 32;
    uint32_t snNodeId = 0;
    bool directSnFakeData = true;

    POCQ_StateGraph pocqGraph;
    std::vector<Entry> entries;
    std::deque<HnfCcTxReq> txReqQ;
    std::deque<HnfCcTxDat> txDatQ;
    std::deque<HnfCcTxRsp> txRspQ;

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
    void queueMcRead(uint32_t entry);
    void queueCompData(uint32_t entry, const std::vector<uint8_t>& data);
    void queueComp(uint32_t entry);
    void queueCompDBIDResp(uint32_t entry);
    void storeWriteData(uint32_t entry);
    HnfCcRetireInfo retireEntry(uint32_t entry);
    HnfCcRetireInfo makeRetireInfo(const Entry& entry) const;
    void wakeSleepingEntries(uint64_t addr);
};

} // namespace gem5::Chi

#endif // __HNF_COHERENCY_CONTROLLER_HH__

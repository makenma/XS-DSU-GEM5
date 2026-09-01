#ifndef __MEM_AXI_AXI_INITIATOR_ADAPTER_HH__
#define __MEM_AXI_AXI_INITIATOR_ADAPTER_HH__

#include <array>
#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <string>
#include <tuple>
#include <vector>

#include "mem/axi/axi_types.hh"
#include "mem/axi/axi_validation.hh"

namespace gem5
{
namespace axi
{

struct AxiInitiatorConfig
{
    AxiEndpointKey source;
    uint32_t dataBusBytes = 64;
    uint8_t idWidth = 8;
    uint32_t maxOutstandingWrites = 32;
    uint32_t maxOutstandingReads = 64;
    std::array<uint32_t, 5> fifoDepths = {16, 64, 16, 32, 128};
    uint32_t preAwBursts = 16;
    uint32_t preAwBeats = 256;
    uint32_t bRobTransactions = 64;
    uint32_t rRobBeats = 1024;
    std::vector<AxiRange> ranges;
    uint32_t defaultErrorTarget = 0;
    std::map<uint32_t, AxiQuota> targetQuotas;
};

struct AxiInitiatorOccupancy
{
    size_t aw = 0;
    size_t w = 0;
    size_t b = 0;
    size_t ar = 0;
    size_t r = 0;
    size_t unboundBursts = 0;
    size_t unboundBeats = 0;
    size_t outstandingWrites = 0;
    size_t outstandingReads = 0;
};

struct AxiInitiatorProgress
{
    uint64_t bPacketsBuffered = 0;
    uint64_t rPacketsBuffered = 0;
    uint64_t sameIdResponsesBlocked = 0;
    uint64_t bTransactionsRetired = 0;
    uint64_t rTransactionsRetired = 0;
    uint64_t writeQuotaStalls = 0;
    uint64_t readQuotaStalls = 0;
};

struct AxiInitiatorResidual
{
    uint64_t writeOrdinal = 0;
    size_t bufferedWBeats = 0;
    bool sawWlast = false;
    bool txnUidPresent = false;
};

class AxiInitiatorState
{
  public:
    explicit AxiInitiatorState(const AxiInitiatorConfig &config);

    bool tryAcceptAw(const AxiAddressRequest &aw, Tick accepted_tick = 0);
    bool tryAcceptW(const AxiWBeat &w, Tick accepted_tick = 0);
    bool tryAcceptAr(const AxiAddressRequest &ar, Tick accepted_tick = 0);

    bool hasAwPacket();
    bool hasWPacket();
    bool hasArPacket();
    const AxiAddressPacket &frontAwPacket();
    const AxiDataPacket &frontWPacket();
    const AxiAddressPacket &frontArPacket();
    void popAwPacket();
    void popWPacket();
    void popArPacket();

    bool canAcceptBPacket(const AxiBPacket &packet) const;
    bool canAcceptRPacket(const AxiDataPacket &packet) const;
    void acceptBPacket(const AxiBPacket &packet, Tick ready_tick = 0);
    void acceptRPacket(const AxiDataPacket &packet, Tick ready_tick = 0);
    bool tryConsumeB(AxiBBeat &beat);
    bool tryConsumeR(AxiRBeat &beat);

    void advance();
    AxiInitiatorOccupancy occupancy() const;
    const AxiInitiatorProgress &progress() const { return _progress; }
    std::string finalConsistencyError() const;
    std::optional<AxiInitiatorResidual> finalResidual() const;
    const AxiAddressPacket &lastAcceptedAw() const;
    const AxiAddressPacket &lastAcceptedAr() const;

    uint64_t nextAwOrdinal() const { return _nextAwOrdinal; }
    uint64_t nextWBurstOrdinal() const { return _nextWBurstOrdinal; }
    uint64_t pairedBursts() const { return _pairedBursts; }
    uint64_t openedWBursts() const { return _openedWBursts; }
    uint64_t closedWBursts() const { return _closedWBursts; }

  private:
    struct WriteState
    {
        uint64_t ordinal = 0;
        std::optional<AxiAddressPacket> aw;
        std::deque<AxiDataPacket> stagedW;
        uint16_t observedW = 0;
        bool sawWlast = false;
        bool paired = false;
        bool quotaAcquired = false;
        bool awInjected = false;
        uint16_t wInjected = 0;
        std::optional<AxiBPacket> response;
        Tick responseReadyTick = 0;
        bool responseQueued = false;
        bool orderingBlockCounted = false;
        bool quotaBlockCounted = false;
    };

    struct ReceivedR
    {
        AxiDataPacket packet;
        Tick readyTick = 0;
    };

    struct ReadState
    {
        AxiAddressPacket ar;
        bool quotaAcquired = false;
        bool arInjected = false;
        std::vector<std::optional<ReceivedR>> received;
        uint16_t nextQueuedBeat = 0;
        uint16_t consumedBeats = 0;
        bool orderingBlockCounted = false;
        bool quotaBlockCounted = false;
    };

    struct ReadyR
    {
        uint64_t txnUid = 0;
        AxiDataPacket packet;
    };

    using TargetSeqKey =
        std::tuple<uint32_t, uint16_t, uint32_t, bool, uint32_t>;
    using ResponseSeqKey =
        std::tuple<uint32_t, uint16_t, uint32_t, bool>;

    AxiCommonMeta allocateMeta(const AxiAddressRequest &request,
                               bool read, uint32_t dst_node,
                               uint32_t semantic_bytes,
                               Tick accepted_tick);
    AxiDataPacket makeWPacket(const WriteState &state,
                              const AxiWBeat &beat,
                              uint16_t beat_index,
                              Tick accepted_tick) const;
    bool acquireWriteQuota(WriteState &state);
    bool acquireReadQuota(ReadState &state);
    void releaseWriteQuota(const WriteState &state);
    void releaseReadQuota(const ReadState &state);
    void bindAndValidate(WriteState &state);
    void pumpStagedW();
    void pumpReadyB();
    void pumpReadyR();
    void validateConfig() const;

    AxiInitiatorConfig _config;
    AxiAddressDecoder _decoder;

    uint64_t _nextWriteUid = 0;
    uint64_t _nextReadUid = 0;
    uint64_t _nextAwOrdinal = 0;
    uint64_t _nextWBurstOrdinal = 0;
    uint64_t _pairedBursts = 0;
    uint64_t _openedWBursts = 0;
    uint64_t _closedWBursts = 0;
    std::optional<uint64_t> _openWOrdinal;

    std::map<TargetSeqKey, uint64_t> _nextTargetSeq;
    std::map<ResponseSeqKey, uint64_t> _nextResponseSeq;
    std::map<uint64_t, WriteState> _writesByOrdinal;
    std::map<uint64_t, uint64_t> _writeOrdinalByUid;
    std::map<uint64_t, ReadState> _readsByUid;

    std::map<uint32_t, uint64_t> _nextBRetireSeq;
    std::map<uint32_t, uint64_t> _nextRRetireSeq;

    BoundedFifo<uint64_t> _awReady;
    BoundedFifo<AxiDataPacket> _wReady;
    BoundedFifo<AxiBPacket> _bReady;
    BoundedFifo<uint64_t> _arReady;
    BoundedFifo<ReadyR> _rReady;

    std::map<uint32_t, AxiQuota> _activeQuota;
    std::optional<AxiAddressPacket> _lastAcceptedAw;
    std::optional<AxiAddressPacket> _lastAcceptedAr;
    size_t _unboundBursts = 0;
    size_t _unboundBeats = 0;
    AxiInitiatorProgress _progress;
};

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_INITIATOR_ADAPTER_HH__

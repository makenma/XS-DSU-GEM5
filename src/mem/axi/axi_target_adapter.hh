#ifndef __MEM_AXI_AXI_TARGET_ADAPTER_HH__
#define __MEM_AXI_AXI_TARGET_ADAPTER_HH__

#include <array>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "mem/axi/axi_types.hh"
#include "mem/axi/axi_validation.hh"

namespace gem5
{
namespace axi
{

// Read-only observation hook fired after a write burst's bytes are
// committed to the target memory (or, for error responses, after the
// drained burst is finalized with zero committed bytes) and before the B
// response is made ready.  Implementations must not modify adapter state.
class AxiWriteCommitObserver
{
  public:
    virtual ~AxiWriteCommitObserver() = default;
    virtual void onAxiWriteCommitted(const AxiAddressRequest &request,
                                     const std::vector<AxiDataPacket> &beats,
                                     AxiResp resp) = 0;
};

class AxiSimpleMemory
{
  public:
    explicit AxiSimpleMemory(std::vector<AxiRange> ranges = {});

    bool contains(uint64_t address) const;
    uint8_t readByte(uint64_t address) const;
    void writeByte(uint64_t address, uint8_t value);
    void fill(uint8_t value);

    std::vector<uint8_t> readBeat(const AxiAddressRequest &request,
                                  uint16_t beat_index,
                                  uint32_t data_bus_bytes) const;
    void commitWrite(const AxiAddressRequest &request,
                     const std::vector<AxiDataPacket> &beats,
                     uint32_t data_bus_bytes);

  private:
    std::vector<AxiRange> _ranges;
    std::map<uint64_t, uint8_t> _bytes;
};

struct AxiTargetConfig
{
    uint32_t dstNode = 0;
    uint32_t dataBusBytes = 64;
    AxiTargetCapacity capacity = {64, 4096, 64, 4096};
    uint32_t orphanTransactions = 16;
    uint32_t orphanBeats = 256;
    uint32_t bReadyDepth = 16;
    uint32_t rReadyDepth = 128;
    uint32_t writeServiceDepth = 32;
    uint32_t readServiceDepth = 32;
    uint32_t writeBaseLatency = 1;
    uint32_t readBaseLatency = 1;
    std::map<AxiEndpointKey, AxiQuota> sourceQuotas;
    std::vector<AxiRange> memoryRanges;
    std::map<uint64_t, uint32_t> extraLatency;
    std::map<uint64_t, AxiResp> transactionFaults;
};

struct AxiTargetOccupancy
{
    size_t writeContexts = 0;
    size_t writeReservedBeats = 0;
    size_t orphanTransactions = 0;
    size_t orphanReservedBeats = 0;
    size_t readContexts = 0;
    size_t readReservedBeats = 0;
    size_t bReady = 0;
    size_t rReady = 0;
    size_t writeServices = 0;
    size_t readServices = 0;
};

struct AxiTargetProgress
{
    uint64_t serviceReady = 0;
    uint64_t architecturalCommits = 0;
    uint64_t sameIdReadyBlocked = 0;
    uint64_t orphanOrQuotaStallCycles = 0;
    uint64_t writesCommitted = 0;
    uint64_t readsCommitted = 0;
    std::array<uint64_t, 16> qosTransactions{};
};

class AxiTargetState
{
  public:
    explicit AxiTargetState(const AxiTargetConfig &config);

    bool canAcceptAw(const AxiAddressPacket &packet) const;
    bool canAcceptW(const AxiDataPacket &packet) const;
    bool canAcceptAr(const AxiAddressPacket &packet) const;
    void acceptAw(const AxiAddressPacket &packet, uint64_t now = 0);
    void acceptW(const AxiDataPacket &packet, uint64_t now = 0);
    void acceptAr(const AxiAddressPacket &packet, uint64_t now = 0);

    bool hasBPacket() const { return !_bReady.empty(); }
    bool hasRPacket() const { return !_rReady.empty(); }
    const AxiBPacket &frontBPacket() const { return _bReady.front(); }
    const AxiDataPacket &frontRPacket() const { return _rReady.front(); }
    void popBPacket();
    void popRPacket();

    void advance(uint64_t now = 0);
    AxiTargetOccupancy occupancy() const;
    const AxiTargetProgress &progress() const { return _progress; }
    const AxiSimpleMemory &memory() const { return _memory; }
    AxiSimpleMemory &memory() { return _memory; }

    void setWriteCommitObserver(AxiWriteCommitObserver *observer)
    {
        writeCommitObserver = observer;
    }

    uint64_t completedWrites() const { return _completedWrites; }
    uint64_t completedReads() const { return _completedReads; }

  private:
    struct WriteContext
    {
        AxiCommonMeta meta;
        uint16_t beatCount = 0;
        std::optional<AxiAddressPacket> aw;
        std::vector<std::optional<AxiDataPacket>> beats;
        uint16_t receivedBeats = 0;
        bool quotaReserved = false;
        bool wasOrphan = false;
        bool serviceStarted = false;
        bool serviceReady = false;
        bool orderingBlockCounted = false;
        uint64_t serviceReadyAt = 0;
        AxiResp serviceResponse = AxiResp::Okay;
    };

    struct ReadContext
    {
        AxiAddressPacket ar;
        uint16_t nextBeat = 0;
        bool quotaReserved = false;
        bool serviceStarted = false;
        bool serviceReady = false;
        bool responseEligible = false;
        bool orderingBlockCounted = false;
        uint64_t serviceReadyAt = 0;
        uint64_t responseEligibleAt = 0;
        AxiResp serviceResponse = AxiResp::Okay;
        std::vector<AxiDataPacket> frozenBeats;
    };

    using OrderingKey =
        std::tuple<uint32_t, uint16_t, uint32_t, bool, uint32_t>;

    bool canReserveWrite(const AxiCommonMeta &meta,
                         uint16_t beat_count) const;
    bool canReserveRead(const AxiCommonMeta &meta,
                        uint16_t beat_count) const;
    void reserveWrite(WriteContext &context);
    void releaseWrite(const WriteContext &context);
    void reserveRead(ReadContext &context);
    void releaseRead(const ReadContext &context);
    void validateAddressPacket(const AxiAddressPacket &packet,
                               AxiChannel channel) const;
    void validateDataPacket(const AxiDataPacket &packet) const;
    uint32_t serviceLatency(const AxiCommonMeta &meta, bool read) const;
    AxiResp serviceResponse(const AxiCommonMeta &meta,
                            AxiResp decode_resp) const;
    void startServices(uint64_t now);
    void updateServiceReady(uint64_t now);
    void commitWrites(uint64_t now);
    void commitReads(uint64_t now);
    void generateReads();

    AxiTargetConfig _config;
    AxiSimpleMemory _memory;
    AxiWriteCommitObserver *writeCommitObserver = nullptr;
    std::map<uint64_t, WriteContext> _writes;
    std::map<uint64_t, ReadContext> _reads;
    BoundedFifo<AxiBPacket> _bReady;
    BoundedFifo<AxiDataPacket> _rReady;
    std::map<AxiEndpointKey, AxiQuota> _activeQuota;
    size_t _reservedWriteBeats = 0;
    size_t _reservedReadBeats = 0;
    size_t _orphanTransactions = 0;
    size_t _orphanBeats = 0;
    size_t _activeWriteServices = 0;
    size_t _activeReadServices = 0;
    uint64_t _completedWrites = 0;
    uint64_t _completedReads = 0;
    uint64_t _now = 0;
    std::map<OrderingKey, uint64_t> _nextTargetCommit;
    AxiTargetProgress _progress;
};

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_TARGET_ADAPTER_HH__

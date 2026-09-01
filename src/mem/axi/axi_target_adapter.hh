#ifndef __MEM_AXI_AXI_TARGET_ADAPTER_HH__
#define __MEM_AXI_AXI_TARGET_ADAPTER_HH__

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
    std::map<AxiEndpointKey, AxiQuota> sourceQuotas;
    std::vector<AxiRange> memoryRanges;
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
};

class AxiTargetState
{
  public:
    explicit AxiTargetState(const AxiTargetConfig &config);

    bool canAcceptAw(const AxiAddressPacket &packet) const;
    bool canAcceptW(const AxiDataPacket &packet) const;
    bool canAcceptAr(const AxiAddressPacket &packet) const;
    void acceptAw(const AxiAddressPacket &packet);
    void acceptW(const AxiDataPacket &packet);
    void acceptAr(const AxiAddressPacket &packet);

    bool hasBPacket() const { return !_bReady.empty(); }
    bool hasRPacket() const { return !_rReady.empty(); }
    const AxiBPacket &frontBPacket() const { return _bReady.front(); }
    const AxiDataPacket &frontRPacket() const { return _rReady.front(); }
    void popBPacket();
    void popRPacket();

    void advance();
    AxiTargetOccupancy occupancy() const;
    const AxiSimpleMemory &memory() const { return _memory; }
    AxiSimpleMemory &memory() { return _memory; }

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
    };

    struct ReadContext
    {
        AxiAddressPacket ar;
        uint16_t nextBeat = 0;
        bool quotaReserved = false;
    };

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
    void completeWrites();
    void generateReads();

    AxiTargetConfig _config;
    AxiSimpleMemory _memory;
    std::map<uint64_t, WriteContext> _writes;
    std::map<uint64_t, ReadContext> _reads;
    BoundedFifo<AxiBPacket> _bReady;
    BoundedFifo<AxiDataPacket> _rReady;
    std::map<AxiEndpointKey, AxiQuota> _activeQuota;
    size_t _reservedWriteBeats = 0;
    size_t _reservedReadBeats = 0;
    size_t _orphanTransactions = 0;
    size_t _orphanBeats = 0;
    uint64_t _completedWrites = 0;
    uint64_t _completedReads = 0;
};

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_TARGET_ADAPTER_HH__

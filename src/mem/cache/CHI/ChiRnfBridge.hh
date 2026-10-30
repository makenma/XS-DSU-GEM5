#ifndef __MEM_CACHE_CHI_RNF_BRIDGE_HH__
#define __MEM_CACHE_CHI_RNF_BRIDGE_HH__

#include <array>
#include <deque>
#include <memory>
#include <optional>
#include <vector>

#include "base/statistics.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/packet.hh"
#include "mem/port.hh"
#include "params/ChiRnfBridge.hh"
#include "sim/clocked_object.hh"
#include "sim/serialize.hh"

namespace gem5::Chi
{

/** A finite, exclusive-only RNF for uncached timing CPU ports. Every miss and
 * eviction uses CHI. Local hits are coherent private-cache accesses, not an
 * alternate path to backing memory. I/D/walker ports share this cache.
 */
class ChiRnfBridge : public ClockedObject, public ruby::Consumer
{
  public:
    PARAMS(ChiRnfBridge);
    explicit ChiRnfBridge(const Params &p);
    void init() override;
    Port &getPort(const std::string &name, PortID id = InvalidPortID) override;
    void wakeup() override;
    void print(std::ostream &out) const override;
    DrainState drain() override;
    void drainResume() override;
    void serialize(CheckpointOut &cp) const override;
    void unserialize(CheckpointIn &cp) override;

  private:
    static constexpr unsigned LineBytes = 64;
    static constexpr unsigned ResponseLimit = 32;
    struct Line {
        Addr address = 0;
        bool ns = true;
        bool valid = false;
        bool dirty = false;
        uint64_t lastTouch = 0;
        alignas(LineBytes) std::array<uint8_t, LineBytes> data{};
    };
    class CpuPort : public ResponsePort {
      public:
        CpuPort(const std::string &name, ChiRnfBridge &bridge, PortID id);
        AddrRangeList getAddrRanges() const override;
        bool recvTimingReq(PacketPtr pkt) override;
        Tick recvAtomic(PacketPtr pkt) override;
        void recvFunctional(PacketPtr pkt) override;
        void recvRespRetry() override;
        bool retryRequest = false;
      private:
        ChiRnfBridge &bridge;
        const PortID index;
    };
    enum class Phase { Idle, Read, WritebackDbid, WritebackComp, Maintenance };

    void tick();
    bool recvCpu(PacketPtr pkt, PortID port);
    void retryCpuResponse();
    void progressCpu();
    void accessLine(Line &line);
    void finishCpu();
    void sendCpuRetries();
    void receive(const RawRsp &rsp);
    void receive(const RawDat &dat);
    void receive(const RawSnp &snp);
    void send(ChannelType ch, const FlitVariant &flit);
    void sendLineData(uint32_t target, uint32_t txn, uint32_t dbid,
                      uint8_t opcode, const Line &line, bool valid);
    void startRead(Addr address, bool ns);
    void startWriteback(const Line &line);
    void invalidate(Line &line);
    void invalidateReservation(Addr address, bool ns);
    void snoopCpu(Addr address, bool ns);
    Line *findLine(Addr address, bool ns);
    const Line *findLine(Addr address, bool ns) const;
    Line &slot(Addr address);
    size_t setBase(Addr address) const {
        return ((address / LineBytes) % (cache.size() / cacheWays)) * cacheWays;
    }
    uint32_t home(Addr address) const { return homes[(address / LineBytes) % homes.size()]; }
    uint32_t allocateTxn();
    bool idle() const;
    bool needsTick() const;

    const uint32_t nodeId;
    const std::vector<unsigned> homes;
    const AddrRange memoryRange;
    const Cycles hitLatency;
    const Cycles reservationCycles;
    const unsigned cacheWays;
    std::vector<Line> cache;
    std::vector<std::unique_ptr<CpuPort>> cpuPorts;
    ChiCommonPort chi;
    EventFunctionWrapper tickEvent;
    std::array<std::deque<FlitVariant>, NUM_CHANNELS> outgoing;
    unsigned queuedFlits = 0;

    PacketPtr cpuPacket = nullptr;
    PortID cpuPort = InvalidPortID;
    Tick cpuReady = 0;
    bool cpuResponse = false;
    bool responseBlocked = false;
    Phase phase = Phase::Idle;
    uint32_t nextTxn = 1;
    uint64_t cacheSequence = 0;
    uint32_t activeTxn = 0;
    uint32_t activeHome = 0;
    Line fill;
    unsigned fillMask = 0;
    Line victim;
    bool victimInvalidated = false;
    bool reservationValid = false;
    Addr reservationAddress = 0;
    bool reservationNs = true;
    ContextID reservationContext = InvalidContextID;
    Tick reservationUntil = 0;
    std::optional<RawSnp> deferredSnoop;
    Tick deferredUntil = 0;

    struct Stats : public statistics::Group {
        explicit Stats(statistics::Group *parent);
        statistics::Scalar cpuRequests, hits, misses, readUnique, writebacks;
        statistics::Scalar snoops, dirtySnoops, atomicOps, storeConditionals;
        statistics::Scalar failedStoreConditionals;
        statistics::Scalar deferredSnoops;
    } stats;
};

} // namespace gem5::Chi
#endif


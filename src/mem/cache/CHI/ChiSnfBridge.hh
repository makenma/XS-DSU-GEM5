#ifndef __MEM_CACHE_CHI_SNF_BRIDGE_HH__
#define __MEM_CACHE_CHI_SNF_BRIDGE_HH__

#include <array>
#include <deque>
#include <map>

#include "mem/abstract_mem.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "params/ChiSnfBridge.hh"
#include "sim/eventq.hh"

namespace gem5::Chi
{

/** A CHI SN-F backed by System::physmem, including its native checkpoints.
 * Timing traffic only enters through CHI. The packet port is deliberately
 * restricted to functional loading while this endpoint is quiescent.
 * NS is propagated to AbstractMemory accesses; it does not create a second
 * physical memory at the same address.
 */
class ChiSnfBridge : public memory::AbstractMemory, public ruby::Consumer
{
  public:
    PARAMS(ChiSnfBridge);
    explicit ChiSnfBridge(const Params &p);
    void init() override;
    void wakeup() override;
    void print(std::ostream &out) const override;
    Port &getPort(const std::string &name, PortID idx = InvalidPortID) override;
    DrainState drain() override;
    void serialize(CheckpointOut &cp) const override;
    void unserialize(CheckpointIn &cp) override;
    bool idle() const;

  private:
    class FunctionalPort : public ResponsePort
    {
      public:
        FunctionalPort(const std::string &name, ChiSnfBridge &owner);
        AddrRangeList getAddrRanges() const override;
      protected:
        Tick recvAtomic(PacketPtr pkt) override;
        bool recvTimingReq(PacketPtr pkt) override;
        void recvFunctional(PacketPtr pkt) override;
        void recvRespRetry() override;
      private:
        ChiSnfBridge &owner;
    };

    using Line = std::array<uint8_t, 64>;
    struct Transaction {
        RawReq req{};
        Line data{};
        unsigned received = 0;
        unsigned sent = 0;
        unsigned responses = 0;
        bool acknowledged = false;
    };
    struct Pending {
        Tick due;
        uint32_t dbid;
        FlitVariant flit;
    };

    void tick();
    void receive(const RawReq &req);
    void receive(const RawRsp &rsp);
    void receive(const RawDat &dat);
    void receive(const RawSnp &snp);
    void queue(ChannelType ch, uint32_t dbid, FlitVariant flit, Cycles delay);
    void transmitted(ChannelType ch, const Pending &response);
    void retireIfComplete(uint32_t dbid);
    void accessLine(Transaction &transaction, bool write);
    uint32_t allocateDbid();

    ChiCommonPort chi;
    FunctionalPort functional;
    EventFunctionWrapper tickEvent;
    const uint32_t nodeId;
    const Cycles responseLatency;
    const unsigned maxTransactions;
    RequestorID requestorId = Request::invldRequestorId;
    uint32_t nextDbid = 1;
    std::map<uint32_t, Transaction> transactions;
    std::array<std::deque<Pending>, NUM_CHANNELS> pending;
};

} // namespace gem5::Chi
#endif

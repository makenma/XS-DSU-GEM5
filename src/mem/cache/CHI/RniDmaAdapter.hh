#ifndef CHI_RNI_DMA_ADAPTER_HH
#define CHI_RNI_DMA_ADAPTER_HH
#include <list>
#include <unordered_map>
#include <vector>
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/cache/CHI/DmaPathStats.hh"
#include "params/RniDmaAdapter.hh"
namespace gem5::Chi
{
class RniDmaAdapter : public ClockedObject, public ruby::Consumer
{
  public:
    explicit RniDmaAdapter(const RniDmaAdapterParams &p);
    Port &getPort(const std::string &, PortID = InvalidPortID) override;
    void wakeup() override;
    void init() override { ClockedObject::init(); in.sendRangeChange(); }
    void print(std::ostream &o) const override { o << name(); }
    bool idle() const;
    DrainState drain() override;
    void serialize(CheckpointOut &) const override;
  private:
    class InPort : public ResponsePort {
        RniDmaAdapter &d;
      public:
        InPort(RniDmaAdapter &d) : ResponsePort(d.name()+".dma_side", &d), d(d) {}
        bool recvTimingReq(PacketPtr p) override { return d.accept(p); }
        Tick recvAtomic(PacketPtr) override { panic("RNI requires timing"); }
        void recvFunctional(PacketPtr p) override { d.functional(p); }
        void recvRespRetry() override { d.respBlocked=false; d.schedulePump(); }
        AddrRangeList getAddrRanges() const override;
    } in;
    class LoaderPort : public RequestPort {
      public:
        LoaderPort(RniDmaAdapter &d) : RequestPort(d.name()+".loader_side", &d) {}
        bool recvTimingResp(PacketPtr) override { panic("loader port timing prohibited"); }
        void recvReqRetry() override { panic("loader port retry prohibited"); }
        void recvRangeChange() override {}
    } loader;
    ChiCommonPort chi;
    struct Entry {
        PacketPtr pkt;
        uint64_t id;
        Tick start;
        bool io=false, error=false, ready=false, writing=false;
        bool injected=false, retry=false, grant=false, comp=false, ack=false;
        uint32_t offset=0, count=0, expected=0, received=0, sent=0;
        uint16_t dbid=0;
        RawReq req{};
        std::vector<uint8_t> data, coverage;
    };
    std::list<Entry> parents;
    std::unordered_map<uint32_t, Entry *> children;
    uint32_t node, home, ioNode, maxEntries, maxChi;
    AddrRange memory, ioRange;
    AddrRangeList advertised;
    bool allowIo, reqRetry=false, respBlocked=false;
    uint64_t nextParent=0, nextTxn=0;
    unsigned injectedChildren = 0;
    Tick lastOccupancy=0;
    Tick holdRxUntil;
    EventFunctionWrapper pumpEvent;
    DmaPathStats stats;
    bool accept(PacketPtr);
    void functional(PacketPtr);
    void schedulePump();
    void pump();
    void startChild(Entry &, bool write);
    void consume();
    void finishChild(Entry &);
    void complete(Entry &);
    void updateOccupancy();
    bool lineBusy(const Entry &) const;
};
}
#endif

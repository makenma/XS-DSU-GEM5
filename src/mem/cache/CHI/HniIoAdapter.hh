#ifndef CHI_HNI_IO_ADAPTER_HH
#define CHI_HNI_IO_ADAPTER_HH
#include <optional>
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/cache/CHI/DmaPathStats.hh"
#include "params/HniIoAdapter.hh"
namespace gem5::Chi
{
class HniIoAdapter : public ClockedObject, public ruby::Consumer
{
  public:
    explicit HniIoAdapter(const HniIoAdapterParams &p);
    Port &getPort(const std::string &, PortID=InvalidPortID) override;
    void wakeup() override;
    void print(std::ostream &o) const override { o << name(); }
    bool idle() const;
    DrainState drain() override;
    void serialize(CheckpointOut &) const override;
  private:
    class PioPort : public RequestPort {
        HniIoAdapter &d;
      public:
        PioPort(HniIoAdapter &d): RequestPort(d.name()+".pio_side", &d),d(d) {}
        bool recvTimingResp(PacketPtr p) override { return d.response(p); }
        void recvReqRetry() override { d.blocked=false; d.schedulePump(); }
        void recvRangeChange() override {}
    } pio;
    ChiCommonPort chi;
    uint32_t node;
    AddrRange range;
    RequestorID requestor;
    std::optional<RawReq> req;
    PacketPtr pkt=nullptr;
    bool write=false, blocked=false, sent=false, haveData=false;
    bool dbidSent=false, ready=false, error=false;
    uint16_t nextDbid=0, dbid=0;
    uint64_t sequence=0;
    Tick start=0;
    EventFunctionWrapper pumpEvent;
    DmaPathStats stats;
    void schedulePump();
    void pump();
    bool response(PacketPtr);
    void makePacket();
};
}
#endif

#ifndef CHI_DMA_TIMING_STALL_HH
#define CHI_DMA_TIMING_STALL_HH
#include "base/statistics.hh"
#include "mem/port.hh"
#include "params/DmaTimingStall.hh"
#include "sim/clocked_object.hh"
namespace gem5::Chi
{
class DmaTimingStall : public ClockedObject
{
  public:
    explicit DmaTimingStall(const DmaTimingStallParams &p);
    Port &getPort(const std::string &, PortID=InvalidPortID) override;
    void init() override;
    DrainState drain() override;
  private:
    class Up : public ResponsePort {
        DmaTimingStall &d;
      public:
        Up(DmaTimingStall &d):ResponsePort(d.name()+".upstream",&d),d(d){}
        bool recvTimingReq(PacketPtr p) override { return d.request(p); }
        Tick recvAtomic(PacketPtr) override { panic("stall timing only"); }
        void recvFunctional(PacketPtr p) override { d.down.sendFunctional(p); }
        void recvRespRetry() override { d.down.sendRetryResp(); }
        AddrRangeList getAddrRanges() const override { return d.down.getAddrRanges(); }
    } up;
    class Down : public RequestPort {
        DmaTimingStall &d;
      public:
        Down(DmaTimingStall &d):RequestPort(d.name()+".downstream",&d),d(d){}
        bool recvTimingResp(PacketPtr p) override { return d.response(p); }
        void recvReqRetry() override { d.up.sendRetryReq(); }
        void recvRangeChange() override { d.up.sendRangeChange(); }
    } down;
    Cycles delay;
    bool rejectReq, rejectResp, reqPermit=false, respPermit=false;
    EventFunctionWrapper reqEvent, respEvent;
    statistics::Scalar reqRejections, respRejections;
    bool request(PacketPtr);
    bool response(PacketPtr);
    void maybeDrain();
};
}
#endif

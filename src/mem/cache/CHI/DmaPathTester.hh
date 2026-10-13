#ifndef CHI_DMA_PATH_TESTER_HH
#define CHI_DMA_PATH_TESTER_HH
#include "mem/port.hh"
#include "params/DmaPathTester.hh"
#include "sim/clocked_object.hh"
namespace gem5::Chi
{
class DmaPathTester : public ClockedObject
{
  public:
    explicit DmaPathTester(const DmaPathTesterParams &p);
    void startup() override;
    Port &getPort(const std::string &,PortID=InvalidPortID) override;
  private:
    class PortImpl : public RequestPort {
        DmaPathTester &d;
      public:
        PortImpl(DmaPathTester &d):RequestPort(d.name()+".port",&d),d(d){}
        bool recvTimingResp(PacketPtr p) override { return d.response(p); }
        void recvReqRetry() override { d.blocked=false; d.schedulePump(); }
        void recvRangeChange() override {}
    } port;
    struct State : Packet::SenderState { unsigned index; explicit State(unsigned i):index(i){} };
    RequestorID requestor;
    unsigned phase=0,issued=0,completed=0,previous=0,reordered=0;
    bool blocked=false,ioTest,ioBurst;
    PacketPtr pending=nullptr,rejected=nullptr;
    EventFunctionWrapper pumpEvent,retryEvent;
    void schedulePump();
    void pump();
    bool response(PacketPtr);
    PacketPtr make(unsigned);
};
}
#endif

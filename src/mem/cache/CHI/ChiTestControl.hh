#pragma once
#include <array>
#include "mem/cache/CHI/ChiIntentCache.hh"
#include "mem/cache/CHI/Cache2ChiBridge.hh"
#include "mem/port.hh"
#include "params/ChiTestControl.hh"
#include "sim/clocked_object.hh"
namespace gem5::Chi {
class ChiTestControl : public ClockedObject {
    class PioPort : public ResponsePort {
        ChiTestControl& owner;
      public:
        PioPort(ChiTestControl& o):ResponsePort(o.name()+".pio",&o),owner(o){}
        AddrRangeList getAddrRanges() const override;
        bool recvTimingReq(PacketPtr p) override;
        Tick recvAtomic(PacketPtr p) override;
        void recvFunctional(PacketPtr p) override;
        void recvRespRetry() override;
        bool recvTimingSnoopResp(PacketPtr p) override;
    } port;
    std::array<uint8_t,4096> registers{};
    Addr base, address=0;
    uint32_t nodeId, size=64, stash=0;
    uint8_t opcode=0;
    uint64_t mask=~uint64_t(0);
    ChiIntentCache* cache;
    Cache2ChiBridge* bridge;
    RequestorID requestor;
    RequestPtr trigger;
    ChiIntentCache::Snapshot snapshot;
    std::deque<PacketPtr> responses;
    bool respBlocked=false, prepared=false;
    EventFunctionWrapper work, respond;
    uint64_t load(unsigned offset) const;
    void store(unsigned offset,uint64_t value);
    void access(PacketPtr p);
    void prepare();
    void submit();
    void fail(unsigned error);
  public:
    explicit ChiTestControl(const ChiTestControlParams& p);
    Port& getPort(const std::string&,PortID=InvalidPortID) override;
    void init() override;
    uint64_t resultWord(unsigned offset) const { return load(offset); }
    void setSharedBase(uint64_t physical) { store(40,physical); }
};
}

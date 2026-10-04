#pragma once
#include <array>
#include <deque>
#include <map>
#include <memory>
#include <set>
#include "mem/cache/CHI/ChiNativeWire.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/packet.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/ChiNativeL2.hh"
#include "sim/clocked_object.hh"

namespace gem5::Chi {
// Behavioral coupled L2: real tags/data/MSHRs and native CHI transactions.
// Classic packets end at cpu_side; no classic cache or CHI bridge below it.
class ChiNativeL2 : public ClockedObject, public ruby::Consumer {
    struct Line {
        Addr address=0;
        bool valid=false, unique=false, dirty=false, upperWritable=false;
        uint64_t age=0;
        std::vector<uint8_t> data=std::vector<uint8_t>(64);
    };
    struct Mshr {
        RawReq req;
        PacketPtr packet=nullptr;
        bool victim=false, retry=false, finishing=false, responseData=false;
        bool suppressResponse=false, gotComp=false, gotDbid=false, sentData=false;
        bool gotAnyData=false;
        bool uncached=false, maintenance=false, gotReceipt=false;
        uint8_t followup=0;
        uint16_t dbid=0;
        uint8_t response=0, creditType=0;
        unsigned way=0, attempt=0;
        std::vector<uint8_t> write=std::vector<uint8_t>(64);
        std::vector<uint8_t> mask=std::vector<uint8_t>(64);
        std::unique_ptr<NativeWire::DataAssembler> assembler;
    };
    struct ProbeState : Packet::SenderState {
        uint64_t id;
        explicit ProbeState(uint64_t i): id(i) {}
    };
    struct Probe {
        PacketPtr packet;
        std::function<void(PacketPtr)> complete;
    };
    struct Out { FlitVariant flit; std::function<void()> accepted; };
    class CpuPort : public ResponsePort {
        ChiNativeL2& l2;
      public:
        CpuPort(ChiNativeL2& p):ResponsePort(p.name()+".cpu_side",&p),l2(p) {}
        bool tryTiming(PacketPtr) override {
            if(l2.requests.size()<l2.queueLimit)return true;
            l2.requestBlocked=true;l2.wakeup();return false;
        }
        bool recvTimingReq(PacketPtr p) override { return l2.receive(p); }
        bool recvTimingSnoopResp(PacketPtr p) override { return l2.probeResponse(p); }
        void recvRespRetry() override { l2.responseBlocked=false;l2.wakeup(); }
        Tick recvAtomic(PacketPtr) override;
        void recvFunctional(PacketPtr p) override;
        AddrRangeList getAddrRanges() const override { return l2.ranges; }
    } cpu;
    class FunctionalPort : public RequestPort {
      public:
        FunctionalPort(ChiNativeL2& p):RequestPort(p.name()+".functional",&p) {}
        bool recvTimingResp(PacketPtr) override;
        void recvReqRetry() override;
    } functional;
    ChiCommonPort chi;
    AddrRangeList ranges;
    uint32_t node, nsets, ways, maxMshrs, queueLimit, nextId=0;
    uint64_t age=0, nextProbe=0;
    std::vector<uint32_t> homes;
    std::vector<uint64_t> homeMasks;
    std::vector<Line> lines;
    bool writeEvictOrEvict;
    std::set<PacketId> cleanDataArrived;
    std::map<uint32_t,Mshr> mshrs;
    std::map<Addr,uint32_t> active;
    std::map<uint64_t,Probe> probes;
    std::set<Addr> probing;
    std::deque<PacketPtr> requests, responses;
    std::deque<RawSnp> snoops;
    std::map<std::pair<uint32_t,uint8_t>,unsigned> credits;
    std::array<std::deque<Out>,4> outgoing;
    bool requestBlocked=false,responseBlocked=false;
    EventFunctionWrapper event;
    uint32_t home(Addr) const;
    uint32_t allocateId();
    Line* lookup(Addr);
    unsigned chooseWay(Addr) const;
    bool receive(PacketPtr);
    bool dispatch(PacketPtr);
    void pump();
    void issue(unsigned,PacketPtr,uint8_t,bool=false,uint8_t=0,const FlowGraphIdentity* =nullptr);
    void respondControl(PacketPtr);
    void absorbWriteback(PacketPtr);
    void finishControl(uint32_t);
    void evict(unsigned);
    void receive(const RawRsp&);
    void receive(const RawDat&);
    void snoop(const RawSnp&);
    void probe(Addr,bool,bool,std::function<void(PacketPtr)>);
    bool preparePendingUpperDemand(Addr,bool);
    void collectWritebacks(Addr);
    bool probeResponse(PacketPtr);
    void finish(uint32_t);
    void retire(uint32_t);
    void respond(PacketPtr,Line&,bool);
    void record(const FlowGraphIdentity&,const Line&);
    template<class F> void queue(ChannelType ch,F f,std::function<void()> done={}) {
        const auto error=NativeWire::validate(f);
        fatal_if(!error.empty(),"%s generated invalid native CHI: %s\n",name(),error);
        FlowGraphTrace::get().stamp(f);
        outgoing[ch].push_back({std::move(f),std::move(done)});
        wakeup();
    }
  public:
    explicit ChiNativeL2(const ChiNativeL2Params&);
    Port& getPort(const std::string&,PortID=InvalidPortID) override;
    void init() override;
    void wakeup() override;
    void print(std::ostream& o) const override { o<<name(); }
};
}

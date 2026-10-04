#pragma once
#include <array>
#include <deque>
#include <map>
#include <set>
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/cache/CHI/ChiNativeWire.hh"
#include "mem/cache/CHI/ChiHomeRecency.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/ChiExtendedHome.hh"
#include "sim/clocked_object.hh"

namespace gem5::Chi {
// Extended behavioral HN-F. All transitions are driven by received flits.
class ChiExtendedHome : public ClockedObject, public ruby::Consumer {
    struct Line { std::vector<uint8_t> data; bool dirty=false; std::set<uint32_t> sharers; uint32_t owner=0; bool unique=false; uint64_t lastUse=0; std::vector<std::string> producers; };
    struct Txn {
        RawReq req;
        std::vector<uint8_t> data=std::vector<uint8_t>(64), write=std::vector<uint8_t>(64);
        std::vector<uint8_t> mask=std::vector<uint8_t>(64), coverage=std::vector<uint8_t>(64);
        std::set<uint32_t> snoops;
        bool dirty=false, haveData=false, writeReady=false, waitingAck=false, writeDirty=false;
        bool memoryWrite=false, memoryRead=false, memoryDone=false, stashPull=false;
        bool victim=false,waitingVictim=false,waitingCapacity=false;
        bool evictWithoutData=false;
        uint8_t writeResp=0;
        uint32_t victimParent=0;
        uint32_t snoopId=0, memoryId=0, pullId=0, stashNode=0;
        uint16_t memoryDbid=0, dbid=0;
        std::map<uint32_t,NativeWire::DataAssembler> snoopData;
        std::optional<NativeWire::DataAssembler> incomingWrite, incomingMemory;
        FlowGraphIdentity memoryFlow, snoopFlow, pullFlow;
    };
    struct Out { ChannelType channel; FlitVariant flit; std::function<void()> accepted; };
    ChiCommonPort port;
    uint32_t nodeId, maxEntries, capacity, nextId=1;
    std::vector<uint32_t> snIds;
    std::vector<uint64_t> snMasks;
    std::map<uint32_t,Txn> txns;
    std::map<Addr,Line> lines;
    HomeRecencyIndex recency;
    std::deque<RawReq> pending;
    std::deque<RawReq> rejected;
    std::map<uint32_t,unsigned> credits;
    std::deque<Out> outgoing;
    std::map<uint32_t,uint32_t> snoopOwner, memoryOwner;
    std::map<std::pair<uint32_t,uint32_t>,uint32_t> requester;
    EventFunctionWrapper event;
    uint32_t sn(Addr address) const;
    void pump();
    void start(uint32_t id);
    void touchLine(std::map<Addr,Line>::iterator line);
    void eraseLine(Addr address);
    void finishLookup(uint32_t id);
    void memory(uint32_t id, bool write);
    void respond(uint32_t id);
    void retire(uint32_t id);
    void receiveReq(RawReq req);
    void receiveRsp(const RawRsp& rsp);
    void receiveDat(const RawDat& dat);
    void receiveNativeDat(const RawDat& dat);
    void emit(ChannelType channel, BaseFlit& flit);
    static void encode(RawReq&);
    static void encode(RawSnp&);
    static void encode(RawRsp&) {}
    static void encode(RawDat&) {}
    template<class F> void queue(ChannelType ch,F flit,std::function<void()> accepted={}) {
        encode(flit); emit(ch,flit); outgoing.push_back({ch,std::move(flit),std::move(accepted)});
    }
    void state(const Txn& txn);
    uint16_t dbid() const;
    uint32_t allocateId();
  public:
    explicit ChiExtendedHome(const ChiExtendedHomeParams& p);
    Port& getPort(const std::string&,PortID=InvalidPortID) override;
    void wakeup() override;
    void print(std::ostream& out) const override { out << name(); }
};
}

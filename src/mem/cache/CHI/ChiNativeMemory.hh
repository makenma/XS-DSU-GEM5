#pragma once

#include <deque>
#include <functional>
#include <map>
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/cache/CHI/ChiNativeWire.hh"
#include "mem/port.hh"
#include "mem/packet.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/ChiNativeMemory.hh"
#include "sim/clocked_object.hh"

namespace gem5::Chi {

// The memory boundary consumes canonical native CHI and converts only here to
// SimpleMemory timing packets. It is not a protocol-correcting NoC adapter.
class ChiNativeMemory : public ClockedObject, public ruby::Consumer {
    using Key=std::pair<uint32_t,uint32_t>;
    struct SenderState : Packet::SenderState {
        Key key;
        explicit SenderState(Key key):key(key) {}
    };
    struct Txn {
        RawReq req;
        uint16_t dbid=0;
        std::optional<NativeWire::DataAssembler> incoming;
        PacketPtr packet=nullptr;
        bool submitted=false;
    };
    struct Out {
        ChannelType channel;
        FlitVariant flit;
        std::function<void()> accepted;
    };
    class MemoryPort : public RequestPort {
        ChiNativeMemory& owner;
      public:
        MemoryPort(const std::string& name,ChiNativeMemory& owner):RequestPort(name,&owner),owner(owner) {}
        bool recvTimingResp(PacketPtr packet) override;
        void recvReqRetry() override;
        void recvRangeChange() override {}
    };
    ChiCommonPort chiPort;
    MemoryPort memoryPort;
    uint32_t nodeId,maxOutstanding;
    RequestorID requestorId;
    bool memoryBlocked=false;
    std::map<Key,Txn> txns;
    std::map<uint16_t,Key> dbids;
    std::deque<Key> memoryQueue;
    std::deque<Out> outgoing;
    EventFunctionWrapper event;
    void pump();
    void receiveReq(const RawReq&);
    void receiveDat(const RawDat&);
    bool receiveMemory(PacketPtr);
    void makeMemory(const Key&);
    void retire(const Key&);
    uint16_t allocateDbid() const;
    template<class F> void queue(ChannelType ch,F flit,std::function<void()> done={}) {
        FlowGraphTrace::get().stamp(flit);
        outgoing.push_back({ch,std::move(flit),std::move(done)});
    }
  public:
    explicit ChiNativeMemory(const ChiNativeMemoryParams&);
    Port& getPort(const std::string&,PortID=InvalidPortID) override;
    void wakeup() override;
    void print(std::ostream& out) const override {out<<name();}
};
}

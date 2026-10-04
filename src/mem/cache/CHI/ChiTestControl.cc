#include "mem/cache/CHI/ChiTestControl.hh"
#include "mem/cache/CHI/ChiProtocolIntent.hh"
#include "mem/cache/CHI/FlowGraphTrace.hh"
#include "base/logging.hh"
#include "sim/system.hh"
#include <cstring>
namespace gem5::Chi {
ChiTestControl::ChiTestControl(const ChiTestControlParams& p):ClockedObject(p),port(*this),
 base(p.base),nodeId(p.node_id),cache(p.cache),bridge(p.bridge),requestor(p.system->getRequestorId(this)),
 work([this]{prepare();},name()+".prepare"), respond([this]{
    while(!responses.empty()&&!respBlocked) {
        if(port.sendTimingResp(responses.front())) responses.pop_front(); else respBlocked=true;
    }
 },name()+".response") { bridge->attachIntentCache(cache,p.accept_stash); }
Port& ChiTestControl::getPort(const std::string& n,PortID id) { if(n=="pio") return port; return ClockedObject::getPort(n,id); }
void ChiTestControl::init() { ClockedObject::init(); port.sendRangeChange(); }
AddrRangeList ChiTestControl::PioPort::getAddrRanges() const { return {AddrRange(owner.base,owner.base+4096)}; }
uint64_t ChiTestControl::load(unsigned o) const { uint64_t v; std::memcpy(&v,registers.data()+o,8); return v; }
void ChiTestControl::store(unsigned o,uint64_t v) { std::memcpy(registers.data()+o,&v,8); }
void ChiTestControl::access(PacketPtr p) {
    const unsigned o=p->getAddr()-base;
    panic_if(o+p->getSize()>registers.size(),"Control request crosses page\n");
    if(p->isWrite()) {
        std::memcpy(registers.data()+o,p->getConstPtr<uint8_t>(),p->getSize());
        if(o==256) {
            panic_if(load(264)==1,"CPU submitted a second CHI descriptor before completion\n");
            trigger=p->req; store(264,1); store(272,0); snapshot={}; prepared=false;
            if(!work.scheduled()) schedule(work,clockEdge(Cycles(1)));
        }
    } else if(p->isRead()) p->setData(registers.data()+o);
    if(p->needsResponse()) p->makeResponse();
}
bool ChiTestControl::PioPort::recvTimingReq(PacketPtr p) {
    owner.access(p); owner.responses.push_back(p);
    if(!owner.respond.scheduled()) owner.schedule(owner.respond,owner.clockEdge(Cycles(1)));
    return true;
}
Tick ChiTestControl::PioPort::recvAtomic(PacketPtr p) { owner.access(p); return owner.clockPeriod(); }
void ChiTestControl::PioPort::recvFunctional(PacketPtr p) { owner.access(p); }
void ChiTestControl::PioPort::recvRespRetry() { owner.respBlocked=false; if(!owner.respond.scheduled()) owner.schedule(owner.respond,owner.clockEdge(Cycles(1))); }
bool ChiTestControl::PioPort::recvTimingSnoopResp(PacketPtr p) {
    owner.snapshot.valid=true; owner.snapshot.dirty=true;
    owner.snapshot.data.assign(p->getConstPtr<uint8_t>(),p->getConstPtr<uint8_t>()+64);
    delete p; owner.submit(); return true;
}
void ChiTestControl::fail(unsigned error) { if(prepared) cache->abortIntent(); prepared=false; store(272,error); store(264,3); }
void ChiTestControl::prepare() {
    opcode=load(0);address=load(8);size=load(16);mask=load(24);stash=load(32);
    if(!size || size>64 || (size&(size-1)) || address%size || (address&63)+size>64) {fail(1);return;}
    if(!(chiRead(opcode)||chiWrite(opcode)||chiStash(opcode)||opcode==8||opcode==9||opcode==10||opcode==12||opcode==13)) {fail(2);return;}
    if((chiAllocating(opcode)||chiCopyback(opcode)||opcode==0x19||opcode==0x1d||opcode==12)&&size!=64) {fail(3);return;}
    if(!cache->prepareIntent(address,snapshot)) { schedule(work,clockEdge(Cycles(1))); return; }
    prepared=true;
    // Obtain the current L1 data before copyback or permission-only upgrade.
    // This travels through the real coherent crossbar and the normal snoop path.
    if(chiCopyback(opcode)||opcode==12||opcode==13) {
        auto r=std::make_shared<Request>(address&~Addr(63),64,0,requestor);
        auto* p=new Packet(r,MemCmd::ReadExReq);p->allocate();p->setExpressSnoop();
        if(bridge->probeIntent(p,[this](PacketPtr response) {
            snapshot.valid=true;snapshot.dirty=true;
            snapshot.data.assign(response->getConstPtr<uint8_t>(),response->getConstPtr<uint8_t>()+64);
            delete response;submit();
        })) return;
    }
    submit();
}
void ChiTestControl::submit() {
    if(chiCopyback(opcode)||opcode==12||opcode==13) {
        if(!snapshot.valid || (opcode==0x15&&(snapshot.dirty||!snapshot.writable)) || (opcode==0x0d&&snapshot.dirty) || (opcode==0x1b&&!snapshot.dirty)) {fail(4);return;}
    }
    if((chiStash(opcode)||chiRead(opcode)||opcode==0x18||opcode==0x19||opcode==0x1c||opcode==0x1d)&&snapshot.valid) {fail(5);return;}
    auto r=std::make_shared<Request>(address,size,0,requestor);
    std::vector<bool> be(size); for(unsigned i=0;i<size;++i) be[i]=(mask>>i)&1;
    auto* p=new Packet(r,chiRead(opcode)?MemCmd::ReadReq:chiWrite(opcode)?MemCmd::WriteReq:MemCmd::InvalidateReq);
    p->allocate(); if(chiWrite(opcode)) p->setData(chiCopyback(opcode)?snapshot.data.data():registers.data()+64);
    if(chiWrite(opcode)) r->setByteEnable(be);
    auto* intent=new ChiProtocolIntent;intent->opcode=opcode;intent->stashTarget=stash;
    intent->instructionId=FlowGraphTrace::get().instruction(trigger,nodeId,"chi-test-doorbell",load(48));
    intent->complete=[this](PacketPtr result) {
        std::vector<uint8_t> data;
        if(chiRead(opcode)) { data.assign(result->getConstPtr<uint8_t>(),result->getConstPtr<uint8_t>()+size); std::copy(data.begin(),data.end(),registers.begin()+512); }
        else if(opcode==12) data=snapshot.data;
        cache->commitIntent(address,opcode,data,result->cacheResponding()||(opcode==12&&snapshot.dirty),result->hasSharers());
        prepared=false;store(264,2);
    };
    p->pushSenderState(intent); bridge->submitIntent(p);
}
}

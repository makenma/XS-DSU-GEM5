#include "mem/cache/CHI/ChiNativeMemory.hh"
#include "base/logging.hh"
#include "sim/system.hh"
#include <algorithm>
#include <cstring>

namespace gem5::Chi {
namespace {
// Snapshot the actual classic packet at the memory boundary. This observer
// neither changes packet contents nor participates in timing/backpressure.
nlohmann::ordered_json memoryEvidence(PacketPtr packet,bool request) {
    const unsigned bytes=packet->getSize();
    nlohmann::ordered_json evidence={
        {"address",FlowGraphTrace::hex(packet->getAddr())},
        {"fields",{{"sizeBytes",bytes},{"isWrite",packet->isWrite()},
                   {"command",packet->cmdString()}}}};
    if((request&&packet->isWrite())||(!request&&packet->isRead())) {
        const auto* data=packet->getConstPtr<uint8_t>();
        evidence["payload"]=FlowGraphTrace::bytes(std::vector<uint8_t>(data,data+bytes));
        evidence["validMask"]=std::string(bytes*2,'f');
        evidence["payloadOrigin"]=request?"memory_request":"memory_response";
    }
    if(request&&packet->isWrite()) {
        const auto& enabled=packet->req->getByteEnable();
        std::vector<uint8_t> mask(bytes,0xff);
        if(!enabled.empty())for(unsigned b=0;b<bytes;++b)mask[b]=enabled.at(b)?0xff:0;
        evidence["byteEnable"]=FlowGraphTrace::bytes(mask);
    }
    return evidence;
}
}

ChiNativeMemory::ChiNativeMemory(const ChiNativeMemoryParams& p):
    ClockedObject(p),ruby::Consumer(this),
    chiPort(p.name+".chi_side",static_cast<ruby::Consumer*>(this),0),
    memoryPort(p.name+".mem_side",*this),nodeId(p.node_id),
    maxOutstanding(p.max_outstanding),requestorId(p.system->getRequestorId(this)),
    event([this]{pump();},p.name+".pump") {
    fatal_if(!maxOutstanding || maxOutstanding>4095,"Invalid native SN capacity\n");
}

Port& ChiNativeMemory::getPort(const std::string& name,PortID id) {
    if(name=="chi_side")return chiPort;
    if(name=="mem_side")return memoryPort;
    return ClockedObject::getPort(name,id);
}

void ChiNativeMemory::wakeup() {if(!event.scheduled())schedule(event,nextCycle());}
bool ChiNativeMemory::MemoryPort::recvTimingResp(PacketPtr p) {return owner.receiveMemory(p);}
void ChiNativeMemory::MemoryPort::recvReqRetry() {owner.memoryBlocked=false;owner.wakeup();}

uint16_t ChiNativeMemory::allocateDbid() const {
    for(unsigned id=1;id<=4095;++id)if(!dbids.count(id))return id;
    fatal("Native SN DBID namespace exhausted\n");
}

void ChiNativeMemory::receiveReq(const RawReq& req) {
    const auto error=NativeWire::validate(req);
    fatal_if(!req.nativeChi || !error.empty(),"Invalid native SN request: %s\n",error);
    fatal_if(req.memattr&2,"Native SN-F does not accept Device memory attributes\n");
    fatal_if(req.tgtid!=nodeId || req.expCompAck ||
             (req.opcode!=4&&req.opcode!=0x1c&&req.opcode!=0x1d),
             "Native SN supports HN ReadNoSnp/WriteNoSnp without CompAck\n");
    const auto bytes=NativeWire::sizeBytes(req.size);
    fatal_if(req.addr&(bytes-1),"Native SN request address/Size misalignment\n");
    fatal_if(req.order>1,"Native SN profile supports unordered or Request Accepted ordering\n");
    const Key key{req.srcid,req.txnid};
    fatal_if(txns.count(key),"Duplicate native SN transaction identity\n");
    Txn t;t.req=req;
    if(req.opcode!=4) {
        t.dbid=allocateDbid();t.incoming.emplace(req.addr,bytes);
        dbids.emplace(t.dbid,key);
    }
    txns.emplace(key,std::move(t));
    if(FlowGraphTrace::get().enabled())
        FlowGraphTrace::get().event(req.flow,"allocate",nodeId,{{"opcode",FlowGraphTrace::opcode(REQ,req.opcode)}});
    if(req.opcode==4) {
        if(req.order==1) {
            RawRsp r;r.nativeChi=true;r.srcid=nodeId;r.tgtid=req.srcid;r.txnid=req.txnid;r.opcode=8;
            r.flow=req.flow;r.flow.messageId.clear();queue(RSP,r);
        }
        makeMemory(key);
    } else {
        RawRsp r;r.nativeChi=true;r.srcid=nodeId;r.tgtid=req.srcid;r.txnid=req.txnid;r.opcode=6;
        r.dbid=txns.at(key).dbid;r.flow=req.flow;r.flow.messageId.clear();queue(RSP,r);
    }
}

void ChiNativeMemory::receiveDat(const RawDat& d) {
    fatal_if(!d.nativeChi || d.opcode!=3 || d.tgtid!=nodeId,"Invalid native SN write DAT\n");
    auto found=dbids.find(d.txnid);
    fatal_if(found==dbids.end() || found->second.first!=d.srcid,"Unmatched native SN write DAT DBID/source\n");
    auto& t=txns.at(found->second);
    fatal_if(!t.incoming || t.submitted || t.packet,"Duplicate native SN write transfer\n");
    const auto error=t.incoming->accept(d);fatal_if(!error.empty(),"Native SN write DAT: %s\n",error);
    if(t.incoming->complete()) {
        fatal_if(!t.incoming->allKnown(),"Native memory cannot commit unknown write bytes\n");
        if(t.req.opcode==0x1d) {
            const auto start=NativeWire::transferStart(t.req.addr,NativeWire::sizeBytes(t.req.size));
            const auto& mask=t.incoming->byteEnable();
            fatal_if(std::any_of(mask.begin()+start,mask.begin()+start+NativeWire::sizeBytes(t.req.size),[](auto b){return !b;}),
                     "WriteNoSnpFull requires every requested byte to be enabled\n");
        }
        makeMemory(found->second);
    }
}

void ChiNativeMemory::makeMemory(const Key& key) {
    auto& t=txns.at(key);
    const auto bytes=NativeWire::sizeBytes(t.req.size);
    auto request=std::make_shared<Request>(t.req.addr,bytes,0,requestorId);
    auto* packet=new Packet(request,t.req.opcode==4?MemCmd::ReadReq:MemCmd::WriteReq);
    packet->allocate();packet->pushSenderState(new SenderState(key));
    if(t.req.opcode!=4) {
        const auto start=NativeWire::transferStart(t.req.addr,bytes);
        const auto& data=t.incoming->data();const auto& enables=t.incoming->byteEnable();
        std::copy(data.begin()+start,data.begin()+start+bytes,packet->getPtr<uint8_t>());
        std::vector<bool> mask(enables.begin()+start,enables.begin()+start+bytes);
        request->setByteEnable(mask);
    }
    t.packet=packet;memoryQueue.push_back(key);
}

bool ChiNativeMemory::receiveMemory(PacketPtr packet) {
    auto* state=dynamic_cast<SenderState*>(packet->popSenderState());
    fatal_if(!state,"Native SN memory response lost identity\n");
    const Key key=state->key;delete state;
    auto found=txns.find(key);fatal_if(found==txns.end(),"Unmatched native SN memory response\n");
    auto& t=found->second;
    fatal_if(!t.submitted || t.packet!=packet || packet->isError(),"Native SN memory response failed\n");
    if(FlowGraphTrace::get().enabled())
        FlowGraphTrace::get().event(t.req.flow,"memory_response",nodeId,memoryEvidence(packet,false));
    if(t.req.opcode==4) {
        const auto bytes=NativeWire::sizeBytes(t.req.size);
        const auto start=NativeWire::transferStart(t.req.addr,bytes);
        std::vector<uint8_t> line(64,0);
        std::copy(packet->getConstPtr<uint8_t>(),packet->getConstPtr<uint8_t>()+bytes,line.begin()+start);
        RawDat d;d.nativeChi=true;d.srcid=nodeId;d.tgtid=t.req.srcid;d.txnid=t.req.txnid;
        d.opcode=4;d.HomeNID=nodeId;d.resp=0;d.dbid=0;
        d.flow=t.req.flow;d.flow.messageId.clear();d.flow.payloadOrigin="memory_response";
        auto beats=NativeWire::packData(d,line,{},t.req.addr,bytes);
        for(unsigned b=0;b<beats.size();++b)
            queue(DAT,beats[b],b+1==beats.size()?std::function<void()>{[this,key]{retire(key);}}:std::function<void()>{});
    } else {
        RawRsp r;r.nativeChi=true;r.srcid=nodeId;r.tgtid=t.req.srcid;r.txnid=t.req.txnid;r.opcode=4;
        r.dbid=t.dbid;
        r.flow=t.req.flow;r.flow.messageId.clear();queue(RSP,r,[this,key]{retire(key);});
    }
    t.packet=nullptr;delete packet;wakeup();return true;
}

void ChiNativeMemory::retire(const Key& key) {
    auto found=txns.find(key);fatal_if(found==txns.end(),"Duplicate native SN completion\n");
    FlowGraphTrace::get().event(found->second.req.flow,"complete",nodeId);
    if(found->second.incoming)dbids.erase(found->second.dbid);
    txns.erase(found);
}

void ChiNativeMemory::pump() {
    // A full endpoint leaves requests in its credited receive FIFO. No request
    // is dropped and no unbounded pending-transaction queue hides backpressure.
    while(txns.size()<maxOutstanding) {
        auto flit=chiPort.getTxFlit(REQ);if(!flit)break;
        receiveReq(std::get<RawReq>(*flit));
    }
    while(auto flit=chiPort.getTxFlit(DAT))receiveDat(std::get<RawDat>(*flit));
    fatal_if(chiPort.hasTxFlit(RSP)||chiPort.hasTxFlit(SNP),"Unexpected native SN RSP/SNP\n");
    while(!memoryBlocked&&!memoryQueue.empty()) {
        auto& t=txns.at(memoryQueue.front());
        // A memory target can change WriteReq to WriteResp during acceptance.
        // Capture the request before calling it; emit only for a successful send.
        nlohmann::ordered_json evidence;
        if(FlowGraphTrace::get().enabled())evidence=memoryEvidence(t.packet,true);
        if(!memoryPort.sendTimingReq(t.packet)) {memoryBlocked=true;break;}
        if(FlowGraphTrace::get().enabled())
            FlowGraphTrace::get().event(t.req.flow,"memory_request",nodeId,std::move(evidence));
        t.submitted=true;memoryQueue.pop_front();
    }
    while(!outgoing.empty()) {
        auto& out=outgoing.front();
        if(!chiPort.enqueueRx(out.channel,out.flit))break;
        auto accepted=std::move(out.accepted);outgoing.pop_front();if(accepted)accepted();
    }
    if(!outgoing.empty()||!memoryQueue.empty()||chiPort.hasTxFlit(REQ))wakeup();
}
}

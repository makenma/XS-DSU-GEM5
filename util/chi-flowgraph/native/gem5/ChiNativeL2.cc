#include "mem/cache/CHI/ChiNativeL2.hh"
#include "mem/cache/CHI/ChiNativeRequest.hh"
#include <algorithm>
#include <limits>

namespace gem5::Chi {
ChiNativeL2::ChiNativeL2(const ChiNativeL2Params& p):ClockedObject(p),ruby::Consumer(this),
    cpu(*this),functional(*this),chi(p.name+".chi_side",static_cast<ruby::Consumer*>(this),0),
    ranges(p.ranges.begin(),p.ranges.end()),node(p.node_id),nsets(p.sets),ways(p.assoc),
    maxMshrs(p.mshrs),queueLimit(p.request_queue),homes(p.home_node_ids),
    homeMasks(p.home_hash_masks),lines(nsets*ways),writeEvictOrEvict(p.write_evict_or_evict),
    event([this]{pump();},p.name+".pump") {
    fatal_if(!nsets||!ways||maxMshrs<2||maxMshrs>4096||!queueLimit||
             homes.empty()||homes.size()!=(1ULL<<homeMasks.size()),"Invalid native L2 geometry\n");
}
void ChiNativeL2::init() { ClockedObject::init();cpu.sendRangeChange(); }
Port& ChiNativeL2::getPort(const std::string& p,PortID id) {
    if(p=="cpu_side")return cpu;
    if(p=="chi_side")return chi;
    if(p=="functional")return functional;
    return ClockedObject::getPort(p,id);
}
void ChiNativeL2::wakeup() { if(!event.scheduled())schedule(event,nextCycle()); }
Tick ChiNativeL2::CpuPort::recvAtomic(PacketPtr) { panic("Native L2 requires timing mode\n"); }
void ChiNativeL2::CpuPort::recvFunctional(PacketPtr p) {
    // Loader/debug accesses are functional, never synthesized into timed CHI.
    l2.functional.sendFunctional(p);
    auto* line=l2.lookup(p->getBlockAddr(64));
    if(line && p->getOffset(64)+p->getSize()<=64) {
        if(p->isWrite())std::copy_n(p->getConstPtr<uint8_t>(),p->getSize(),line->data.begin()+p->getOffset(64));
        else if(p->isRead())p->setData(line->data.data()+p->getOffset(64));
    }
}
bool ChiNativeL2::FunctionalPort::recvTimingResp(PacketPtr) { panic("Timing response on functional port\n"); }
void ChiNativeL2::FunctionalPort::recvReqRetry() { panic("Retry on functional port\n"); }
uint32_t ChiNativeL2::home(Addr a) const {
    unsigned index=0;for(unsigned b=0;b<homeMasks.size();++b)index|=__builtin_parityll(a&homeMasks[b])<<b;
    return homes.at(index);
}
uint32_t ChiNativeL2::allocateId() {
    for(unsigned n=0;n<4096;++n) { const auto id=nextId;nextId=(nextId+1)&4095;if(!mshrs.count(id))return id; }
    panic("Native L2 TxnID exhausted\n");
}
ChiNativeL2::Line* ChiNativeL2::lookup(Addr a) {
    const unsigned start=((a/64)%nsets)*ways;
    for(unsigned i=start;i<start+ways;++i)if(lines[i].valid&&lines[i].address==a)return &lines[i];
    return nullptr;
}
unsigned ChiNativeL2::chooseWay(Addr a) const {
    unsigned chosen=lines.size();const unsigned start=((a/64)%nsets)*ways;
    for(unsigned i=start;i<start+ways;++i) {
        if(std::any_of(mshrs.begin(),mshrs.end(),[i](const auto& p){return p.second.way==i;})||
           (lines[i].valid&&probing.count(lines[i].address)))continue;
        if(!lines[i].valid)return i;
        if(chosen==lines.size()||lines[i].age<lines[chosen].age)chosen=i;
    }
    return chosen;
}
bool ChiNativeL2::receive(PacketPtr p) {
    if(requests.size()>=queueLimit) {requestBlocked=true;return false;}
    fatal_if(p->getOffset(64)+p->getSize()>64 && !p->req->isCacheMaintenance(),
             "Native L2 request crosses a cache line: %s\n",p->cmdString());
    requests.push_back(p);wakeup();return true;
}
void ChiNativeL2::record(const FlowGraphIdentity& flow,const Line& l) {
    if(!FlowGraphTrace::get().enabled())return;
    nlohmann::ordered_json state={
        {"state",!l.valid?"I":l.upperWritable?"UC/UD":l.unique?(l.dirty?"UD":"UC"):(l.dirty?"SD":"SC")},
        {"owner",l.upperWritable?"L1":"L2"},
        {"data",FlowGraphTrace::bytes(l.data)},
        {"validMask",std::string(128,l.valid&&!l.upperWritable?'f':'0')}};
    if(!l.upperWritable)state["dirty"]=l.dirty;
    FlowGraphTrace::get().event(flow,"state",node,{{"address",FlowGraphTrace::hex(l.address)},
        {"state",state},
        {"fields",{{"cache","L2"},{"upperMayHaveNewerData",l.upperWritable}}}});
}
void ChiNativeL2::respond(PacketPtr p,Line& l,bool suppress) {
    l.age=++age;
    if(l.unique)l.upperWritable=true;
    if(suppress||!p->needsResponse()) {delete p;return;}
    const bool reads=p->isRead();
    if(!l.unique)p->setHasSharers();
    if(l.dirty&&!p->cacheResponding())p->setCacheResponding();
    p->makeTimingResponse();
    // L1 allocates its read response buffer before issuing a miss. Only an
    // Upgrade promoted to ReadEx needs a new buffer (allocated in finish).
    if(reads)p->setData(l.data.data()+p->getOffset(64));
    responses.push_back(p);wakeup();
}
void ChiNativeL2::respondControl(PacketPtr p) {
    if(!p)return;
    if(!p->needsResponse()) {delete p;return;}
    p->makeTimingResponse();responses.push_back(p);wakeup();
}
void ChiNativeL2::absorbWriteback(PacketPtr p) {
    const Addr address=p->getBlockAddr(64);
    auto* l=lookup(address);
    fatal_if(p->getSize()!=64||p->getOffset(64)||!l,
             "Inclusive native L2 lost L1 data at %#llx\n",address);
    std::copy_n(p->getConstPtr<uint8_t>(),64,l->data.begin());
    l->dirty|=p->cmd==MemCmd::WritebackDirty||p->cmd==MemCmd::WriteClean;
    l->age=++age;l->upperWritable=false;
    if(p->cmd==MemCmd::WriteClean)cleanDataArrived.insert(p->id);
    delete p;
}
bool ChiNativeL2::dispatch(PacketPtr p) {
    const Addr a=p->getBlockAddr(64);
    // The clean request can precede its paired WriteClean. Accept that data
    // independently, even while its CMO waits for a native MSHR.
    if(p->isWriteback()||p->cmd==MemCmd::WriteClean) {
        if(active.count(a))return false;
        absorbWriteback(p);return true;
    }
    if(active.count(a)||probing.count(a))return false;
    auto* l=lookup(a);
    if(p->cmd==MemCmd::CleanEvict) {delete p;return true;}
    if(responses.size()>=queueLimit)return false;
    if(p->req->isCacheMaintenance()) {
        if(mshrs.size()>=maxMshrs-1)return false;
        if(p->isClean()&&p->satisfied()&&!cleanDataArrived.count(p->id))return false;
        cleanDataArrived.erase(p->id);
        const uint8_t op=p->req->isCacheClean()?(p->req->isCacheInvalidate()?9:8):10;
        const unsigned way=l?unsigned(l-lines.data()):unsigned(lines.size());
        // CoupledL2 performs a local release before its system-wide CMO.
        if(l && (op!=8||l->dirty)) {
            const uint8_t release=op==8?0x17:op==9&&l->dirty?0x1b:0x0d;
            issue(way,p,release,true,op);
        } else issue(way,p,op);
        return true;
    }
    if(p->req->isUncacheable()) {
        if(mshrs.size()>=maxMshrs-1)return false;
        fatal_if(p->cacheResponding()||(!p->isRead()&&!p->isWrite())||p->req->isAtomic(),
                 "Unsupported native non-cacheable operation %s\n",p->cmdString());
        // MMIOBridge uses Ptl for both full and partial TileLink Put requests.
        issue(lines.size(),p,p->isRead()?4:0x1c);return true;
    }
    const bool suppress=p->cacheResponding();
    if(l && (!p->needsWritable()||l->unique)) {respond(p,*l,suppress);return true;}
    if(mshrs.size()>=maxMshrs-1)return false; // reserve progress for an inclusive victim
    unsigned way=l?unsigned(l-lines.data()):chooseWay(a);
    if(way==lines.size())return false;
    if(!l&&lines[way].valid) {evict(way);return false;}
    // Classic L1 coalesces a full-line store into InvalidateReq. Acquire
    // unique permission online and retain its data-less InvalidateResp.
    fatal_if(!p->isRead()&&!p->isUpgrade()&&p->cmd!=MemCmd::InvalidateReq,
             "Unsupported native L2 demand %s\n",p->cmdString());
    // Only the classic whole-line-write promise corresponds to the RTL's
    // full_overwrite -> AcquirePerm. A partial write/Upgrade still reads data.
    const uint8_t opcode=p->cmd==MemCmd::InvalidateReq?0x0c:p->needsWritable()?0x07:0x26;
    fatal_if(opcode==12&&(p->getSize()!=64||p->getOffset(64)),
             "MakeUnique requires an aligned whole-line overwrite promise\n");
    issue(way,p,opcode);return true;
}
void ChiNativeL2::issue(unsigned way,PacketPtr p,uint8_t opcode,bool victim,uint8_t followup,const FlowGraphIdentity* parent) {
    const auto id=allocateId();const Addr address=victim?lines[way].address:p->getBlockAddr(64);
    Mshr m;m.packet=p;m.way=way;m.victim=victim;m.followup=followup;
    m.uncached=opcode==4||opcode==0x1c;m.maintenance=opcode>=8&&opcode<=10;
    m.suppressResponse=p&&p->cacheResponding();m.responseData=p&&p->isRead();
    auto& r=m.req;r.nativeChi=true;r.srcid=node;r.tgtid=home(address);r.txnid=id;
    r.opcode=opcode;r.addr=address;r.size=6;r.AllowRetry=1;r.snpattr=1;
    r.memattr=(m.maintenance||opcode==0x0d||opcode==0x17)?5:13;
    r.expCompAck=opcode==0x26||opcode==7||opcode==12||opcode==0x42;
    r.likelyShared=opcode==0x42&&!lines[way].unique;
    if(m.uncached) {
        r.addr=p->getAddr();r.size=NativeWire::sizeCode(p->getSize());
        fatal_if(r.addr&(p->getSize()-1),"Unaligned uncached transfer must be split by CPU\n");
        // This RN-F/HN-F/SN-F topology backs uncached mappings with Normal
        // memory. gem5 STRICT_ORDER also marks SE uncached RAM; it is not a
        // Device memory-type attribute. CPU ordering remains enforced upstream.
        r.snpattr=0;r.memattr=0;r.order=2;
        if(opcode==0x1c) {
            const auto& enables=p->req->getByteEnable();
            for(unsigned i=0;i<p->getSize();++i) {
                const unsigned b=p->getOffset(64)+i;
                m.mask[b]=enables.empty()||enables.at(i);
                if(m.mask[b])m.write[b]=p->getConstPtr<uint8_t>()[i];
            }
        }
    }
    r.flow=parent?FlowGraphTrace::get().child(*parent,"cmo",address):FlowGraphTrace::get().root(victim?"l2-victim":"l2");r.flow.address=address;
    if(p) {r.flow.causeInstructionIds=FlowGraphTrace::get().causes(p->req);FlowGraphTrace::get().bind(p->req,r.flow);}
    if(victim) {m.write=lines[way].data;m.response=lines[way].dirty?(lines[way].unique?6:7):(lines[way].unique?2:1);}
    else if(opcode==7||opcode==0x26||opcode==4)
        m.assembler=std::make_unique<NativeWire::DataAssembler>(r.addr,NativeWire::sizeBytes(r.size));
    if(FlowGraphTrace::get().enabled()) {
        FlowGraphTrace::get().event(r.flow,"allocate",node,{{"opcode",FlowGraphTrace::opcode(REQ,opcode)},
            {"fields",{{"inputPacket",p?p->cmdString():"capacity-victim"},
                       {"cacheMaintenance",p&&p->req->isCacheMaintenance()},
                       {"fullOverwritePromise",p&&p->cmd==MemCmd::InvalidateReq&&!p->req->isCacheMaintenance()},
                       {"selectionSource","online-L1-request-and-L2-state"}}}});
        FlowGraphTrace::get().event(r.flow,"sam",node,{{"fields",{{"homeNodeId",r.tgtid},{"hashMasks",homeMasks}}}});
    }
    active[address]=id;mshrs.emplace(id,std::move(m));queue(REQ,mshrs.at(id).req);
}
void ChiNativeL2::evict(unsigned way) {
    const Addr address=lines[way].address;
    probe(address,true,false,[this,way](PacketPtr p) {
        auto& l=lines[way];
        if(p->cacheResponding()&&p->hasData()) {std::copy_n(p->getConstPtr<uint8_t>(),64,l.data.begin());l.dirty=true;}
        l.upperWritable=false;
        issue(way,nullptr,l.dirty?0x1b:writeEvictOrEvict?0x42:0x0d,true);
    });
}
bool ChiNativeL2::preparePendingUpperDemand(Addr address,bool invalidate) {
    bool precedes=false;
    // L1 marks its MSHR in service once this endpoint accepts the packet,
    // even while it is still in requests and has no native CHI MSHR. A probe
    // must precede that queued demand: deferring it until the demand's fill
    // would deadlock against dispatch()'s probing-address exclusion.
    // Completed packets in responses have already passed the ordering point
    // and deliberately do not participate in this search.
    for(auto* p:requests) {
        if(p->getBlockAddr(64)!=address||!p->needsResponse())continue;
        precedes=true;
        if(invalidate&&p->isUpgrade()) {
            // The preceding invalidation removes L1's retained data. Upgrade
            // commands have no allocated response payload. Preserve failed
            // store-conditional semantics while restoring a data response.
            promoteInvalidatedUpgrade(p);
        }
    }
    return precedes;
}
void ChiNativeL2::probe(Addr address,bool invalidate,bool precedes,std::function<void(PacketPtr)> callback) {
    collectWritebacks(address);
    // Shared by incoming snoops and inclusive victim probes. Keep the active
    // MSHR responseData handling in snoop(), while covering queued demands here.
    const bool pendingUpper=preparePendingUpperDemand(address,invalidate);
    precedes=precedes||pendingUpper;
    const auto id=nextProbe++;
    auto req=std::make_shared<Request>(address,64,0,Request::funcRequestorId);
    auto* p=new Packet(req,invalidate?MemCmd::ReadExReq:MemCmd::ReadReq);
    p->allocate();p->setExpressSnoop();if(precedes)p->setSnoopPrecedesMshr();
    p->pushSenderState(new ProbeState(id));probing.insert(address);
    probes.emplace(id,Probe{p,std::move(callback)});cpu.sendTimingSnoopReq(p);
    if(!p->cacheResponding())probeResponse(p);
}
void ChiNativeL2::collectWritebacks(Addr address) {
    // Accepted writebacks are part of the RN's dirty data, even after L1 has
    // removed its tag. Include this buffer in probe/eviction lookup.
    for(auto it=requests.begin();it!=requests.end();) {
        auto* p=*it;
        if(p->getBlockAddr(64)!=address||(!p->isWriteback()&&p->cmd!=MemCmd::WriteClean)) {++it;continue;}
        absorbWriteback(p);it=requests.erase(it);
    }
}
bool ChiNativeL2::probeResponse(PacketPtr p) {
    auto* state=p->findNextSenderState<ProbeState>();fatal_if(!state,"Missing native L2 probe state\n");
    auto it=probes.find(state->id);fatal_if(it==probes.end(),"Unknown L2 probe\n");
    auto work=std::move(it->second);probes.erase(it);probing.erase(p->getBlockAddr(64));
    collectWritebacks(p->getBlockAddr(64));work.complete(p);delete p->popSenderState();
    const Addr address=p->getBlockAddr(64);
    if(work.packet!=p)delete work.packet;
    delete p;
    const auto m=active.find(address);if(m!=active.end())finish(m->second);
    wakeup();return true;
}
void ChiNativeL2::snoop(const RawSnp& s) {
    const Addr address=NativeWire::physicalSnoopAddress(s.addr)&~Addr(63);
    const bool invalid=s.opcode==7||s.opcode==9||s.opcode==10;
    fatal_if(s.opcode!=1&&s.opcode!=2&&s.opcode!=3&&s.opcode!=4&&s.opcode!=7&&s.opcode!=8&&s.opcode!=9&&s.opcode!=10,
             "Native L2 unsupported SNP %#x\n",s.opcode);
    // Snoop precedes the demand if its CompData has not committed yet.
    const auto a=active.find(address);
    const bool precedes=a!=active.end()&&!mshrs.at(a->second).victim&&!mshrs.at(a->second).finishing;
    // Classic L1 keeps Owned on a shared read snoop. Invalidate its private
    // copy when passing dirty ownership out of the RN, while L2 retains the
    // legal clean shared copy. This is a conservative upper-cache policy.
    const bool invalidateUpper=s.opcode!=3;
    if(precedes&&invalidateUpper)mshrs.at(a->second).responseData=true;
    probe(address,invalidateUpper,precedes,[this,s,address,invalid](PacketPtr p) {
        auto* l=lookup(address);
        if(p->cacheResponding()&&p->hasData()) {
            fatal_if(!l,"Upper cache held line absent from inclusive L2\n");
            std::copy_n(p->getConstPtr<uint8_t>(),64,l->data.begin());l->dirty=true;
        }
        const bool dirty=l&&l->dirty;
        // SnpOnce retains state; other supported reads downgrade to clean shared.
        const bool once=s.opcode==3;
        const bool cleanShared=s.opcode==8;
        const uint8_t cleanState=l&&l->unique?2:1;
        const uint8_t resp=!l?0:invalid?(dirty?4:0):once?(l->unique?2:(dirty?3:1)):
            cleanShared?(cleanState|(dirty?4:0)):(dirty?5:1);
        if(l && s.opcode!=10 && (dirty||s.retToSrc)) {
            RawDat d;d.nativeChi=true;d.srcid=node;d.tgtid=s.srcid;d.txnid=s.txnid;
            d.opcode=1;d.resp=resp;d.flow=s.flow;d.flow.messageId.clear();
            d.flow.payloadOrigin=dirty?"cache_data":"retained_data";
            for(auto beat:NativeWire::packData(d,l->data,std::vector<uint8_t>(64,1),address,64))queue(DAT,std::move(beat));
        } else {
            RawRsp r;r.nativeChi=true;r.srcid=node;r.tgtid=s.srcid;r.txnid=s.txnid;r.opcode=1;
            r.resp=l&&!invalid?((once||cleanShared)?cleanState:1):0;r.flow=s.flow;r.flow.messageId.clear();queue(RSP,r);
        }
        if(l&&!once) {if(!cleanShared)l->unique=false;l->dirty=false;l->upperWritable=false;if(invalid)l->valid=false;record(s.flow,*l);}
        // A writeback retry which lost its copy must not later resurrect stale data.
        auto v=active.find(address);
        if(v!=active.end()&&mshrs.at(v->second).victim) {
            auto& m=mshrs.at(v->second);
            if(l)m.write=l->data;
            if(invalid)m.response=0;else if(!once)m.response=cleanShared?cleanState:1;
        }
    });
}
void ChiNativeL2::receive(const RawRsp& r) {
    const auto error=NativeWire::validate(r);fatal_if(!r.nativeChi||!error.empty()||r.tgtid!=node||r.respErr,"Invalid/error native L2 RSP: %s\n",error);
    if(r.opcode==7) {++credits[{r.srcid,r.pcrdtype}];return;}
    auto it=mshrs.find(r.txnid);fatal_if(it==mshrs.end(),"RSP for unknown native L2 TxnID %u\n",r.txnid);
    auto& m=it->second;fatal_if(r.srcid!=m.req.tgtid,"Native L2 RSP source mismatch\n");
    if(r.opcode==3) {fatal_if(!m.req.AllowRetry||m.retry||m.gotAnyData||m.gotDbid||m.gotComp,"Unexpected RetryAck after response\n");m.retry=true;m.creditType=r.pcrdtype;return;}
    if(r.opcode==8) {
        fatal_if(!m.uncached||m.req.opcode!=4||!m.req.order||m.gotReceipt,"Unexpected ReadReceipt\n");
        // ReadReceipt.DBID is inapplicable: it never supplies a DAT or Ack ID.
        m.gotReceipt=true;finish(r.txnid);return;
    }
    fatal_if(r.opcode!=4&&r.opcode!=5&&r.opcode!=6,"Unsupported native L2 RSP %#x\n",r.opcode);
    if(r.opcode==4||r.opcode==5) {fatal_if(m.gotComp,"Duplicate Comp\n");m.gotComp=true;}
    if(r.opcode==4) {m.dbid=r.dbid;if(m.req.opcode==12)m.response=r.resp;}
    if(r.opcode==4&&m.victim&&m.req.opcode!=0x0d&&!m.gotDbid) {
        fatal_if(m.req.opcode!=0x42,"CAH=0 CopyBack cannot complete without data handshake\n");
        m.finishing=true;
        RawRsp ack;ack.nativeChi=true;ack.srcid=node;ack.tgtid=r.srcid;ack.txnid=r.dbid;ack.opcode=2;
        // CoupledL2's TXRSP uses Resp=I: this RN relinquishes its clean copy.
        ack.resp=0;
        ack.flow=m.req.flow;ack.flow.messageId.clear();
        queue(RSP,ack,[this,id=r.txnid]{finishControl(id);});return;
    }
    if(r.opcode==5||r.opcode==6) {
        fatal_if((!m.victim&&!m.uncached)||m.req.opcode==4||m.req.opcode==0x0d||m.gotDbid,"Unexpected DBIDResp\n");
        m.gotDbid=true;m.dbid=r.dbid;
        RawDat d;d.nativeChi=true;d.srcid=node;d.tgtid=r.srcid;d.txnid=r.dbid;
        d.opcode=m.uncached?3:2;d.resp=m.uncached?0:m.response;
        d.flow=m.req.flow;d.flow.messageId.clear();d.flow.payloadOrigin=m.uncached?"cpu_store":"cache_data";
        auto beats=NativeWire::packData(d,m.write,m.uncached?m.mask:std::vector<uint8_t>(64,m.response?1:0),
                                        m.req.addr,NativeWire::sizeBytes(m.req.size));
        for(unsigned i=0;i<beats.size();++i)queue(DAT,beats[i],i+1==beats.size()?std::function<void()>([this,id=r.txnid]{mshrs.at(id).sentData=true;finish(id);}):std::function<void()>{});
    }
    finish(r.txnid);
}
void ChiNativeL2::receive(const RawDat& d) {
    const auto error=NativeWire::validate(d);fatal_if(!d.nativeChi||!error.empty()||d.tgtid!=node,"Invalid native L2 DAT: %s\n",error);
    auto it=mshrs.find(d.txnid);fatal_if(it==mshrs.end(),"DAT for unknown native L2 transaction\n");
    auto& m=it->second;fatal_if(!m.assembler||d.opcode!=4||d.srcid!=m.req.tgtid||d.HomeNID!=m.req.tgtid,"Unexpected native L2 DAT\n");
    const auto problem=m.assembler->accept(d);fatal_if(!problem.empty(),"Native L2 beat: %s\n",problem);
    m.gotAnyData=true;
    m.dbid=d.dbid;m.response=d.resp;m.gotComp=m.assembler->complete();finish(d.txnid);
}
void ChiNativeL2::finishControl(uint32_t id) {
    auto& m=mshrs.at(id);
    auto* p=m.packet;const unsigned way=m.way;const uint8_t next=m.followup;
    const auto flow=m.req.flow;
    if(m.victim&&way<lines.size()) {
        auto& l=lines[way];
        if(m.req.opcode==0x17) {l.dirty=false;l.upperWritable=false;}
        else {l.valid=false;l.upperWritable=false;}
        record(flow,l);
    }
    if(m.maintenance) {
        auto* l=lookup(m.req.addr&~Addr(63));
        if(l) {l->dirty=false;l->upperWritable=false;if(m.req.opcode!=8)l->valid=false;record(flow,*l);}
    }
    m.packet=nullptr;retire(id);
    if(next)issue(way,p,next,false,0,&flow);
    else respondControl(p);
}
void ChiNativeL2::finish(uint32_t id) {
    auto& m=mshrs.at(id);if(m.finishing||!m.gotComp||probing.count(m.req.addr&~Addr(63)))return;
    if(m.victim||m.maintenance) {
        if(m.victim&&m.req.opcode!=0x0d&&!m.sentData)return;
        finishControl(id);return;
    }
    if(m.uncached) {
        if(m.req.opcode==4) {
            if(m.req.order&&!m.gotReceipt)return;
            fatal_if(!m.assembler->complete()||!m.assembler->allKnown(),"Incomplete uncached read\n");
            auto* p=m.packet;p->setData(m.assembler->data().data()+p->getOffset(64));
        } else if(!m.sentData)return;
        finishControl(id);return;
    }
    if(m.req.opcode!=12)
        fatal_if(!m.assembler||!m.assembler->complete()||!m.assembler->allKnown(),"Native L2 incomplete fill\n");
    m.finishing=true;auto& l=lines[m.way];l.address=m.req.addr;l.valid=true;l.upperWritable=false;
    l.unique=(m.response&3)==2;l.dirty=m.response&4;l.age=++age;
    if(m.assembler)l.data=m.assembler->data();
    else l.upperWritable=true; // MU grants permission; the full store bytes still live in L1.
    fatal_if((m.req.opcode==7||m.req.opcode==12)&&!l.unique,"Unique request did not return unique state\n");
    record(m.req.flow,l);
    RawRsp ack;ack.nativeChi=true;ack.srcid=node;ack.tgtid=m.req.tgtid;ack.txnid=m.dbid;ack.opcode=2;
    ack.flow=m.req.flow;ack.flow.messageId.clear();
    queue(RSP,ack,[this,id] {
        auto& m=mshrs.at(id);auto* p=m.packet;
        if(m.responseData&&p->isUpgrade()&&!m.suppressResponse) promoteInvalidatedUpgrade(p);
        respond(p,lines[m.way],m.suppressResponse);
        record(m.req.flow,lines[m.way]);m.packet=nullptr;retire(id);
    });
}
void ChiNativeL2::retire(uint32_t id) {
    auto& m=mshrs.at(id);FlowGraphTrace::get().event(m.req.flow,"complete",node);
    active.erase(m.req.addr&~Addr(63));mshrs.erase(id);wakeup();
}
void ChiNativeL2::pump() {
    while(auto f=chi.getTxFlit(RSP))receive(std::get<RawRsp>(*f));
    while(auto f=chi.getTxFlit(DAT))receive(std::get<RawDat>(*f));
    while(snoops.size()+probes.size()<maxMshrs &&
          outgoing[DAT].size()+outgoing[RSP].size()<4*maxMshrs) {
        auto f=chi.getTxFlit(SNP);if(!f)break;
        const auto s=std::get<RawSnp>(*f);const auto error=NativeWire::validate(s);
        fatal_if(!s.nativeChi||!error.empty()||s.tgtid!=node||s.srcid!=home(NativeWire::physicalSnoopAddress(s.addr)),"Invalid native SNP: %s\n",error);
        snoops.push_back(s);
    }
    for(auto it=snoops.begin();it!=snoops.end();) {
        const Addr a=NativeWire::physicalSnoopAddress(it->addr)&~Addr(63);
        // Once CompData commits, send CompAck before applying a later snoop.
        auto m=active.find(a);if(probing.count(a)||(m!=active.end()&&(mshrs.at(m->second).finishing||mshrs.at(m->second).gotAnyData))) {++it;continue;}
        const auto s=*it;it=snoops.erase(it);snoop(s);
    }
    for(auto& entry:mshrs) {
        auto& m=entry.second;auto key=std::make_pair(m.req.tgtid,m.creditType);
        if(m.retry&&credits[key]) {--credits[key];m.retry=false;m.req.AllowRetry=0;m.req.pcrdtype=m.creditType;
            m.req.flow.messageId.clear();m.req.flow.attemptId=m.req.flow.legId+"/retry-"+std::to_string(++m.attempt);queue(REQ,m.req);}
    }
    // Callbacks can consume queued writebacks; do not retain deque iterators.
    const auto count=requests.size();
    for(size_t i=0;i<count&&!requests.empty();++i) {auto* p=requests.front();requests.pop_front();if(!dispatch(p))requests.push_back(p);}
    if(requestBlocked&&requests.size()<queueLimit) {requestBlocked=false;cpu.sendRetryReq();}
    for(unsigned ch=0;ch<4;++ch) {
        auto& q=outgoing[ch];
        while(!q.empty()) {if(!chi.enqueueRx(ChannelType(ch),q.front().flit))break;auto done=std::move(q.front().accepted);q.pop_front();if(done)done();}
    }
    while(!responseBlocked&&!responses.empty()) {if(!cpu.sendTimingResp(responses.front())){responseBlocked=true;break;}responses.pop_front();}
    if(!requests.empty()||!responses.empty()||!mshrs.empty()||!snoops.empty()||!probes.empty()||
       std::any_of(outgoing.begin(),outgoing.end(),[](const auto& q){return !q.empty();}))wakeup();
}
}

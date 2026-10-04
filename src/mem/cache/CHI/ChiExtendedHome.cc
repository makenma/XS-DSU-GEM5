#include "mem/cache/CHI/ChiExtendedHome.hh"
#include "mem/cache/CHI/ChiProtocolIntent.hh"
#include "mem/cache/CHI/FlowGraphTrace.hh"
#include "base/logging.hh"
#include <algorithm>

namespace gem5::Chi {
namespace {
// Keep the project's legacy helpers unchanged: these additional requests are
// implemented at this endpoint as real native transactions.
bool homeCopyback(uint8_t op) { return chiCopyback(op)||op==0x17||op==0x42; }
bool homeWriteData(uint8_t op) { return chiWrite(op)||homeCopyback(op); }
bool bypassRequest(uint8_t op) { return op==4||op==0x1c||op==0x1d; }
}
ChiExtendedHome::ChiExtendedHome(const ChiExtendedHomeParams& p) : ClockedObject(p),
    ruby::Consumer(this), port(p.name+".rxport",static_cast<ruby::Consumer*>(this),0),
    nodeId(p.node_id), maxEntries(p.num_entries), capacity(p.slc_lines),
    snIds(p.sn_node_ids), snMasks(p.sn_hash_masks), event([this]{pump();},p.name+".pump") {
    fatal_if(snIds.empty() || snIds.size()!=(1ULL<<snMasks.size()), "CHI SN hash table/masks mismatch\n");
    fatal_if(!maxEntries || maxEntries>254 || !capacity,"Invalid extended HNF capacity\n");
}
Port& ChiExtendedHome::getPort(const std::string& name,PortID id) {
    if(name=="rxport") return port;
    return ClockedObject::getPort(name,id);
}
void ChiExtendedHome::wakeup() { if(!event.scheduled()) schedule(event,nextCycle()); }
uint32_t ChiExtendedHome::sn(Addr address) const {
    unsigned index=0; for(unsigned b=0;b<snMasks.size();++b) index|=__builtin_parityll(address&snMasks[b])<<b;
    return snIds.at(index);
}
void ChiExtendedHome::emit(ChannelType,BaseFlit& f) { FlowGraphTrace::get().stamp(f); }
void ChiExtendedHome::encode(RawReq& r) {
    if(!r.nativeChi) return;
    r.size=NativeWire::sizeCode(r.size);
}
void ChiExtendedHome::encode(RawSnp& s) {
    if(s.nativeChi) {
        s.addr=NativeWire::snoopAddress(s.addr);
        s.retToSrc=s.opcode==1 || s.opcode==3 || s.opcode==4 || s.opcode==7;
        s.doNotGoToSD=true;
    }
}
uint32_t ChiExtendedHome::allocateId() {
    for(unsigned n=0;n<4096;++n) {
        const auto id=nextId;nextId=(nextId+1)&4095;
        if(!txns.count(id)&&!snoopOwner.count(id)&&!memoryOwner.count(id)) return id;
    }
    fatal("HN transaction ID namespace exhausted\n");
}
uint16_t ChiExtendedHome::dbid() const {
    unsigned id=1;
    while(std::any_of(txns.begin(),txns.end(),[id](const auto& p){return p.second.dbid==id;}))++id;
    fatal_if(id>4095,"HN DBID exhausted\n");return id;
}

void ChiExtendedHome::receiveReq(RawReq req) {
    if(req.nativeChi) {
        const auto error=NativeWire::validate(req);
        fatal_if(!error.empty(),"Invalid native HNF request: %s\n",error);
        fatal_if(req.memattr&2,
                 "Native HN-F/SN-F profile does not implement Device memory; use normal noncacheable RAM\n");
        fatal_if(!req.AllowRetry&&req.pcrdtype!=0,"Native HNF only grants PCrdType 0\n");
        fatal_if(req.size>6 || (req.addr&((1ULL<<req.size)-1)),"Invalid native HNF Size/alignment\n");
        req.size=1U<<req.size;
    }
    // Preserve the original project's coarse full-write encodings.
    if(req.opcode==0x55) req.opcode=0x15;
    if(req.opcode==0x57) req.opcode=0x17;
    if(req.opcode==0x58) req.opcode=0x18;
    if(req.opcode==0x59) req.opcode=0x19;
    if(req.opcode==0x5b) req.opcode=0x1b;
    if(req.opcode==0x5c) req.opcode=0x1d;
    fatal_if(req.tgtid!=nodeId || !req.size || req.size>64 || (req.addr%64)+req.size>64,
             "Invalid extended HNF request\n");
    fatal_if(!chiRead(req.opcode) && !homeWriteData(req.opcode) &&
        !(req.opcode>=8 && req.opcode<=0x0d) && !chiStash(req.opcode),"Unsupported extended REQ %#x\n",req.opcode);
    const auto key=std::make_pair(req.srcid,req.txnid);
    fatal_if(requester.count(key),"Duplicate requester transaction\n");
    unsigned reserved=0;for(const auto& credit:credits)reserved+=credit.second;
    if(req.AllowRetry && txns.size()+pending.size()+reserved>=maxEntries) {
        RawRsp rsp;rsp.nativeChi=req.nativeChi;rsp.srcid=nodeId;rsp.tgtid=req.srcid;rsp.txnid=req.txnid;rsp.opcode=3;
        rsp.flow=req.flow;rsp.flow.messageId.clear();queue(RSP,rsp);rejected.push_back(req);return;
    }
    if(!req.AllowRetry) {fatal_if(!credits[req.srcid],"Retry without P-Credit\n");--credits[req.srcid];}
    pending.push_back(req);
}

void ChiExtendedHome::pump() {
    while(auto f=port.getRxFlit(RSP)) receiveRsp(std::get<RawRsp>(*f));
    while(auto f=port.getRxFlit(DAT)) receiveDat(std::get<RawDat>(*f));
    while(auto f=port.getRxFlit(REQ)) receiveReq(std::get<RawReq>(*f));
    for(auto it=pending.begin();it!=pending.end() && txns.size()<maxEntries;) {
        const Addr address=it->addr&~Addr(63);
        const bool hazard=std::any_of(txns.begin(),txns.end(),[address](const auto& p){return (p.second.req.addr&~Addr(63))==address;});
        if(hazard) { FlowGraphTrace::get().wait(it->flow,nodeId,"address",true,"same_address"); ++it; continue; }
        RawReq req=*it; it=pending.erase(it);
        FlowGraphTrace::get().wait(req.flow,nodeId,"address",false,"same_address");
        const uint32_t id=allocateId();
        Txn txn; txn.req=req; txn.dbid=dbid();
        if(req.nativeChi&&req.opcode==0x42) {
            const auto line=lines.find(address);
            // A shared clean copy already at HN can be retained without a
            // transfer. For a unique owner request its current clean copy,
            // even if HN also retained bytes after a previous WriteCleanFull.
            txn.evictWithoutData=line!=lines.end()&&line->second.data.size()==64&&!line->second.unique;
        }
        txns.emplace(id,std::move(txn)); requester[{req.srcid,req.txnid}]=id;
        if(FlowGraphTrace::get().enabled())
            FlowGraphTrace::get().event(req.flow,"allocate",nodeId,{{"opcode",FlowGraphTrace::opcode(REQ,req.opcode)}});
        if(req.nativeChi&&req.opcode==4&&req.order>=2) {
            // RN RequestOrder/EndpointOrder receipt belongs to the upstream
            // transaction; downstream HN-F reads remain independent Order=00.
            RawRsp receipt;receipt.nativeChi=true;receipt.srcid=nodeId;
            receipt.tgtid=req.srcid;receipt.txnid=req.txnid;receipt.opcode=8;
            receipt.flow=req.flow;receipt.flow.messageId.clear();queue(RSP,receipt);
        }
        if(homeWriteData(req.opcode)&&!txns.at(id).evictWithoutData) {
            RawRsp rsp; rsp.nativeChi=req.nativeChi; rsp.srcid=nodeId; rsp.tgtid=req.srcid; rsp.txnid=req.txnid; rsp.opcode=homeCopyback(req.opcode)?5:6;
            rsp.dbid=txns.at(id).dbid; rsp.flow=req.flow; rsp.flow.messageId.clear(); queue(RSP,rsp);
        } else start(id);
    }
    unsigned reserved=0;for(const auto& credit:credits)reserved+=credit.second;
    while(!rejected.empty()&&txns.size()+pending.size()+reserved<maxEntries) {
        auto req=rejected.front();rejected.pop_front();++credits[req.srcid];++reserved;
        RawRsp rsp;rsp.nativeChi=req.nativeChi;rsp.srcid=nodeId;rsp.tgtid=req.srcid;rsp.opcode=7;rsp.flow=req.flow;rsp.flow.messageId.clear();queue(RSP,rsp);
    }
    std::vector<uint32_t> waiting;
    for(const auto& p:txns)if(p.second.waitingCapacity)waiting.push_back(p.first);
    for(auto id:waiting)start(id);
    while(!outgoing.empty()) {
        auto& item=outgoing.front();
        if(!port.enqueueTx(item.channel,item.flit)) break;
        auto accepted=std::move(item.accepted); outgoing.pop_front(); if(accepted) accepted();
    }
    if(!pending.empty() || !outgoing.empty() || !txns.empty() || !rejected.empty()) wakeup();
}

void ChiExtendedHome::touchLine(std::map<Addr,Line>::iterator line) {
    const auto now=curTick();
    recency.update(line->first,line->second.lastUse,now);
    line->second.lastUse=now;
}

void ChiExtendedHome::eraseLine(Addr address) {
    const auto line=lines.find(address);
    if(line==lines.end())return;
    recency.erase(address,line->second.lastUse);
    lines.erase(line);
}

void ChiExtendedHome::start(uint32_t id) {
    auto& t=txns.at(id); const Addr address=t.req.addr&~Addr(63);
    auto found=lines.find(address);
    const bool allocates=chiAllocating(t.req.opcode)||homeCopyback(t.req.opcode)||chiStash(t.req.opcode)||t.req.opcode==0x18||t.req.opcode==0x19||t.req.opcode==0x0c||t.req.opcode==0x0b;
    if(allocates && !t.victim && !t.waitingVictim && found==lines.end() && lines.size()>=capacity) {
        std::set<Addr> busy;
        for(const auto& txn:txns)busy.insert(txn.second.req.addr&~Addr(63));
        const auto candidate=recency.victim(busy);
        const auto victim=candidate?lines.find(*candidate):lines.end();
        if(victim==lines.end()) {t.waitingCapacity=true;FlowGraphTrace::get().wait(t.req.flow,nodeId,"victim",true,"victim_buffer_full");return;}
        const auto victimId=allocateId();Txn replacement;replacement.victim=true;replacement.victimParent=id;
        replacement.req.nativeChi=t.req.nativeChi;
        replacement.dbid=dbid();replacement.req.srcid=nodeId;replacement.req.tgtid=nodeId;replacement.req.txnid=victimId;
        replacement.req.opcode=9;replacement.req.addr=victim->first;replacement.req.size=64;replacement.req.memattr=13;
        replacement.req.flow=FlowGraphTrace::get().child(t.req.flow,"victim",victim->first);
        replacement.req.flow.dataProducerInstructionIds=victim->second.producers;
        t.waitingVictim=true;t.waitingCapacity=false;txns.emplace(victimId,std::move(replacement));
        FlowGraphTrace::get().event(txns.at(victimId).req.flow,"allocate",nodeId,{{"opcode","SLC/SF victim"}});
        start(victimId);return;
    }
    t.waitingCapacity=false;
    if(t.req.nativeChi&&bypassRequest(t.req.opcode)) {
        FlowGraphTrace::get().event(t.req.flow,"lookup",nodeId,
            {{"fields",{{"slcHit",false},{"sfHit",false},{"bypass",true}}}});
        memory(id,chiWrite(t.req.opcode));return;
    }
    if(found!=lines.end() && !found->second.data.empty()) { t.data=found->second.data; t.dirty=found->second.dirty; t.haveData=t.data.size()==64; }
    if(found!=lines.end()) {touchLine(found);if(!homeCopyback(t.req.opcode))t.req.flow.dataProducerInstructionIds=found->second.producers;}
    FlowGraphTrace::get().event(t.req.flow,"lookup",nodeId,{{"fields",{{"slcHit",t.haveData},{"sfHit",found!=lines.end()&&!found->second.sharers.empty()}}}});
    if(chiStash(t.req.opcode)) {
        t.stashNode=t.req.stashNid;
        fatal_if(t.stashNode==nodeId || t.stashNode==t.req.srcid,"Stash target must be another RN\n");
        RawSnp s; s.nativeChi=t.req.nativeChi; s.srcid=nodeId; s.tgtid=t.stashNode; s.txnid=allocateId(); s.addr=address; s.size=64;
        s.opcode=t.req.opcode==0x22?0x0c:0x0b;
        s.flow=FlowGraphTrace::get().child(t.req.flow,"stash",address);
        t.snoopId=s.txnid; t.snoopFlow=s.flow; snoopOwner[s.txnid]=id; t.snoops.insert(t.stashNode);
        queue(SNP,s); return;
    }
    if(bypassRequest(t.req.opcode)) { memory(id,chiWrite(t.req.opcode)); return; }
    if(t.req.opcode==0x0d || homeCopyback(t.req.opcode)) { finishLookup(id); return; }
    if(found!=lines.end()) {
        uint8_t op=3;
        if(t.req.opcode==1) op=1;
        if(t.req.opcode==0x26) op=4;
        if(t.req.opcode==7 || t.req.opcode==0x24 || t.req.opcode==0x25 || t.req.opcode==0x18 || t.req.opcode==0x19) op=7;
        if(t.req.opcode==8) op=8;
        if(t.req.opcode==9) op=9;
        if(t.req.opcode==0x0b)op=9;
        if(t.req.opcode==0x0a || t.req.opcode==0x0c) op=0x0a;
        for(auto target:found->second.sharers) {
            if(target==t.req.srcid && t.req.opcode!=8 && t.req.opcode!=9 && t.req.opcode!=0x0a) continue;
            RawSnp s; s.nativeChi=t.req.nativeChi; s.srcid=nodeId; s.tgtid=target; s.txnid=allocateId(); s.opcode=op; s.addr=address; s.size=64;
            s.flow=FlowGraphTrace::get().child(t.req.flow,"snoop",address);
            snoopOwner[s.txnid]=id; t.snoops.insert(target); queue(SNP,s);
        }
    }
    if(t.snoops.empty()) finishLookup(id);
}

void ChiExtendedHome::finishLookup(uint32_t id) {
    auto& t=txns.at(id); const auto op=t.req.opcode;
    if(homeCopyback(op)) {
        const bool containsData=!t.req.nativeChi || std::any_of(t.mask.begin(),t.mask.end(),[](auto b){return b!=0;});
        if(containsData) {t.data=t.write;t.haveData=true;t.dirty=t.req.nativeChi?(t.dirty||t.writeDirty):op==0x1b;}
        respond(id); return;
    }
    if(op==0x0a || op==0x0c || op==0x0d) { respond(id); return; }
    if(!t.haveData && (chiRead(op) || chiStash(op) || op==0x18)) { memory(id,false); return; }
    if(op==0x18 || op==0x19) {
        if(op==0x19) t.data=t.write;
        else for(unsigned b=0;b<64;++b) if(t.mask[b]) t.data[b]=t.write[b];
        t.haveData=true; t.dirty=true;
    }
    if((op==8 || op==9 || op==0x0b || op==0x24) && t.dirty && !t.memoryDone) { memory(id,true); return; }
    respond(id);
}

void ChiExtendedHome::memory(uint32_t id,bool write) {
    auto& t=txns.at(id); RawReq r; r.nativeChi=t.req.nativeChi;
    r.srcid=nodeId; r.tgtid=sn(t.req.addr); r.txnid=allocateId(); r.addr=t.req.addr&~Addr(63); r.size=64;
    r.opcode=write?(r.nativeChi?0x1d:0x5c):4; r.AllowRetry=1; r.memattr=t.req.memattr; r.ReturnNid=nodeId; r.ReturnTxnId=r.txnid;
    if(r.nativeChi&&write) {r.ReturnNid=0;r.ReturnTxnId=0;}
    r.order=0;r.expCompAck=false;
    if(t.req.opcode==4 || t.req.opcode==0x1c || t.req.opcode==0x1d) {
        r.addr=t.req.addr; r.size=t.req.size;
        if(write) r.opcode=t.req.opcode==0x1c?0x1c:(r.nativeChi?0x1d:0x5c);
    }
    r.flow=FlowGraphTrace::get().child(t.req.flow,write?"memory-write":"memory-read",r.addr);
    t.memoryId=r.txnid; t.memoryFlow=r.flow; t.memoryWrite=write; t.memoryRead=!write;
    t.coverage.assign(64,0); memoryOwner[r.txnid]=id;
    if(FlowGraphTrace::get().enabled()) {
        std::vector<std::string> maskedInput;std::vector<unsigned> parities;unsigned index=0;
        for(unsigned b=0;b<snMasks.size();++b) {maskedInput.push_back(FlowGraphTrace::hex(r.addr&snMasks[b]));parities.push_back(__builtin_parityll(r.addr&snMasks[b]));index|=parities.back()<<b;}
        FlowGraphTrace::get().event(r.flow,"sam",nodeId,{{"target",FlowGraphTrace::get().node(r.tgtid)},
            {"fields",{{"stage","HN→SN"},{"method","xor-parity"},{"masks",snMasks},{"maskedInput",maskedInput},{"parities",parities},{"index",index}}}});
    }
    queue(REQ,r);
}

void ChiExtendedHome::state(const Txn& t) {
    if(!FlowGraphTrace::get().enabled())return;
    auto it=lines.find(t.req.addr&~Addr(63));
    nlohmann::ordered_json snapshot={{"state","I"},{"sfState","I"},{"sharers",nlohmann::ordered_json::array()},{"dirty",false}};
    if(it!=lines.end()) {
        const auto& line=it->second;
        snapshot["state"]=line.data.empty()?"I":line.dirty?"MU":"EN";
        snapshot["sfState"]=line.sharers.empty()?"I":line.unique?"EU":"SN";
        snapshot["dirty"]=line.dirty;
        for(auto node:line.sharers) snapshot["sharers"].push_back(FlowGraphTrace::get().node(node));
        if(line.unique) snapshot["owner"]=FlowGraphTrace::get().node(line.owner);
        if(!line.data.empty()) { snapshot["data"]=FlowGraphTrace::bytes(line.data); snapshot["validMask"]=std::string(128,'f'); }
    }
    FlowGraphTrace::get().event(t.req.flow,"state",nodeId,{{"state",snapshot}});
}

void ChiExtendedHome::respond(uint32_t id) {
    auto& t=txns.at(id); const auto op=t.req.opcode; const Addr address=t.req.addr&~Addr(63);
    if(t.victim) {
        eraseLine(address);state(t);const auto parent=t.victimParent;retire(id);
        txns.at(parent).waitingVictim=false;FlowGraphTrace::get().wait(txns.at(parent).req.flow,nodeId,"victim",false,"victim_buffer_full");start(parent);return;
    }
    if(chiStash(op) && !t.stashPull) {
        RawRsp r; r.nativeChi=t.req.nativeChi; r.srcid=nodeId; r.tgtid=t.req.srcid; r.txnid=t.req.txnid; r.opcode=4;
        r.flow=t.req.flow; r.flow.messageId.clear();
        FlowGraphTrace::get().event(t.req.flow,"stash_ignored",nodeId,{{"description","目标未请求 DataPull，hint 正常完成"}});
        queue(RSP,r,[this,id]{retire(id);}); return;
    }
    // Uncached traffic does not allocate, remove, or re-label a coherent SLC/SF
    // entry. It may share the address lock, but its payload belongs to memory.
    if(!t.req.nativeChi||!bypassRequest(op)) {
        const auto inserted=lines.try_emplace(address);
        auto& line=inserted.first->second;
        touchLine(inserted.first);line.producers=t.req.flow.dataProducerInstructionIds;
        if(op==0x18||op==0x19)line.producers=t.req.flow.causeInstructionIds;
        if(op==7 || (chiStash(op)&&op==0x23)) {
            line.data.clear(); line.dirty=false; line.sharers={chiStash(op)?t.stashNode:t.req.srcid};
            line.owner=*line.sharers.begin(); line.unique=true;
        } else if(op==1 || op==0x26 || (chiStash(op)&&op==0x22)) {
            line.data=t.data; line.dirty=t.dirty; line.unique=false;
            line.sharers.insert(chiStash(op)?t.stashNode:t.req.srcid);
        } else if(op==8) {
            // A clean miss has no payload. Do not invent a zero-filled SLC line
            // from Txn's scratch storage when all snoopees replied without data.
            if(t.haveData||!t.req.nativeChi)line.data=t.data;else line.data.clear();
            line.dirty=false;if(!t.req.nativeChi)line.unique=false;
        }
        else if(op==9 || op==0x0a || op==0x24 || op==0x25) { eraseLine(address); }
        else if(op==0x0c || op==0x0b) { line.data.clear(); line.dirty=false; line.sharers={t.req.srcid}; line.owner=t.req.srcid; line.unique=true; }
        else if(op==0x0d) { line.sharers.erase(t.req.srcid); line.unique=false; }
        else if(homeCopyback(op) || op==0x18 || op==0x19) {
            const bool containsData=!t.req.nativeChi || !homeCopyback(op) ||
                std::any_of(t.mask.begin(),t.mask.end(),[](auto b){return b!=0;});
            if(containsData) {line.data=t.data;line.dirty=t.dirty;}
            if(t.req.nativeChi&&op==0x17&&(t.writeResp&3)) {
                // B4.44: UD_PD/SD_PD transfer dirty responsibility to HN while
                // WriteCleanFull retains UC/SC at RN. Resp=I retains no copy.
                if((t.writeResp&3)==2) {
                    line.sharers={t.req.srcid};line.owner=t.req.srcid;line.unique=true;
                } else {line.sharers.insert(t.req.srcid);line.unique=false;}
            } else {
                if(op==0x18 || op==0x19) line.sharers.clear(); else line.sharers.erase(t.req.srcid);
                line.unique=false;
            }
        }
        const auto empty=lines.find(address);
        if(empty!=lines.end()&&empty->second.data.empty()&&empty->second.sharers.empty())eraseLine(address);
        state(t);
    }
    if(homeCopyback(op)&&!t.evictWithoutData) {retire(id);return;}
    if(chiRead(op) || (chiStash(op)&&t.stashPull)) {
        const auto recipient=chiStash(op)?t.stashNode:t.req.srcid;
        const auto txnid=chiStash(op)?t.pullId:t.req.txnid;
        const auto flow=chiStash(op)?t.pullFlow:t.req.flow;
        uint8_t response=chiAllocating(op)?1:0;
        if(op==7 || (chiStash(op)&&op==0x23)) response=t.dirty?6:2;
        if(chiStash(op)&&op==0x22) response=1;
        const unsigned bytes=chiStash(op)?64:t.req.size;
        const unsigned start=chiStash(op)?0:t.req.addr%64;
        for(unsigned offset=0;offset<bytes;offset+=32) {
            RawDat d; d.nativeChi=t.req.nativeChi; d.srcid=nodeId; d.tgtid=recipient; d.txnid=txnid; d.opcode=4;
            d.dbid=t.dbid; d.HomeNID=nodeId; d.dataid=offset/32; d.beatOffset=offset;
            if(d.nativeChi&&!t.req.expCompAck&&!chiStash(op)) d.dbid=0;
            d.resp=response; d.last=offset+32>=bytes;
            const unsigned count=std::min(32u,bytes-offset);
            d.data.assign(t.data.begin()+start+offset,t.data.begin()+start+offset+count);
            d.byteEnable.assign(count,1); d.chunkValid.assign((count+7)/8,1);
            if(d.nativeChi) {
                const unsigned base=(start+offset)&~31U;
                d.beatOffset=base;d.dataid=base/16;
                d.data.assign(32,0);d.byteEnable.assign(32,0);d.chunkValid.assign(4,1);
                for(unsigned lane=0;lane<32;++lane) if(base+lane>=start&&base+lane<start+bytes) {
                    d.data[lane]=t.data[base+lane];
                }
            }
            d.flow=flow; d.flow.messageId.clear(); d.flow.payloadOrigin=t.memoryRead?"memory_response":"hnf_slc";
            queue(DAT,d);
        }
        t.waitingAck=chiStash(op)||t.req.expCompAck;
        if(chiStash(op)) {
            RawRsp r; r.nativeChi=t.req.nativeChi; r.srcid=nodeId; r.tgtid=t.req.srcid; r.txnid=t.req.txnid; r.opcode=4;
            r.flow=t.req.flow; r.flow.messageId.clear(); queue(RSP,r);
        }
        if(!t.waitingAck) outgoing.back().accepted=[this,id]{retire(id);};
    } else {
        RawRsp r; r.nativeChi=t.req.nativeChi; r.srcid=nodeId; r.tgtid=t.req.srcid; r.txnid=t.req.txnid; r.opcode=4;
        r.dbid=t.dbid; r.resp=(op==0x0c||op==0x0b)?2:0; r.flow=t.req.flow; r.flow.messageId.clear();
        // WEOE Comp is explicitly acknowledged even independently of the
        // request's ExpCompAck. The data-return alternative retires above.
        t.waitingAck=t.evictWithoutData||((op==0x0c||op==0x0b)&&t.req.expCompAck);
        if(r.nativeChi&&!t.waitingAck&&!chiWrite(op))r.dbid=0;
        queue(RSP,r,t.waitingAck?std::function<void()>{}:std::function<void()>{[this,id]{retire(id);}});
    }
}

void ChiExtendedHome::receiveRsp(const RawRsp& r) {
    fatal_if(r.tgtid!=nodeId,"Wrong HNF response target\n");
    // CompAck uses the HN completion DBID, never the independent SN/SNP TxnID.
    if(r.opcode==2) {
        for(auto& p:txns) {
            auto& t=p.second;
            const auto target=chiStash(t.req.opcode)?t.stashNode:t.req.srcid;
            if(t.waitingAck && r.srcid==target && r.txnid==t.dbid) {
                fatal_if(r.nativeChi!=t.req.nativeChi,"CompAck wire mode mismatch\n");
                fatal_if(t.req.nativeChi&&t.evictWithoutData&&r.resp!=0&&r.resp!=1&&r.resp!=2,
                         "WEOE CompAck must report I, SC, or UC\n");
                retire(p.first); return;
            }
        }
        fatal("Unmatched HN CompAck txn=%u source=%u\n",r.txnid,r.srcid);
    }
    auto mem=memoryOwner.find(r.txnid);
    if(mem!=memoryOwner.end()) {
        auto& t=txns.at(mem->second); const auto id=mem->second;
        fatal_if(r.srcid!=sn(t.req.addr) || r.tgtid!=nodeId,"SN response identity mismatch\n");
        if(r.opcode==6) {
            fatal_if(t.req.nativeChi&&(!t.memoryWrite||t.memoryDbid),
                     "Unexpected or duplicate native SN DBIDResp\n");
            t.memoryDbid=r.dbid; RawDat d;d.nativeChi=t.req.nativeChi;
            d.srcid=nodeId; d.tgtid=r.srcid; d.txnid=r.txnid; d.dbid=r.dbid; d.HomeNID=nodeId; d.opcode=3; d.last=1;
            const bool bypass=t.req.opcode==0x1c || t.req.opcode==0x1d;
            const unsigned start=bypass?t.req.addr%64:0, count=bypass?t.req.size:64;
            const auto& source=bypass?t.write:t.data;
            d.data.assign(source.begin()+start,source.begin()+start+count);
            d.byteEnable.assign(count,1); if(bypass) for(unsigned b=0;b<count;++b) d.byteEnable[b]=t.mask[start+b];
            for(unsigned b=0;b<count;++b) if(!d.byteEnable[b]) d.data[b]=0;
            d.chunkValid.assign((count+7)/8,1); d.flow=t.memoryFlow; d.flow.messageId.clear(); d.flow.payloadOrigin=t.victim?"hnf_dirty_victim":"hnf_slc";
            if(d.nativeChi) {
                for(unsigned base=start&~31U;base<start+count;base+=32) {
                    RawDat beat=d;beat.txnid=r.dbid;beat.dbid=0;beat.HomeNID=0;
                    beat.dataid=base/16;beat.beatOffset=base;beat.last=base+32>=start+count;
                    beat.data.assign(32,0);beat.byteEnable.assign(32,0);beat.chunkValid.assign(4,1);
                    for(unsigned lane=0;lane<32;++lane) if(base+lane>=start&&base+lane<start+count) {
                        const auto b=base+lane;
                        beat.byteEnable[lane]=bypass?t.mask[b]:1;
                        if(beat.byteEnable[lane])beat.data[lane]=source[b];
                    }
                    queue(DAT,beat);
                }
            } else queue(DAT,d);
        } else if(r.opcode==4) {
            fatal_if(t.req.nativeChi&&(!t.memoryWrite||!t.memoryDbid||r.dbid!=t.memoryDbid),
                     "Native SN Comp must match the write DBIDResp\n");
            t.memoryDone=true; t.dirty=false; memoryOwner.erase(mem);
            if(t.req.opcode==0x1c || t.req.opcode==0x1d) respond(id); else finishLookup(id);
        } else fatal("Unsupported SN RSP %#x\n",r.opcode);
        return;
    }
    auto snp=snoopOwner.find(r.txnid);
    if(snp!=snoopOwner.end()) {
        const auto id=snp->second; auto& t=txns.at(id);
        fatal_if(!t.snoops.erase(r.srcid) || r.opcode!=1,"Unexpected snoop response\n");
        if(r.nativeChi) FlowGraphTrace::get().event(r.flow,"complete",nodeId);
        if((r.resp&7)==0) {auto line=lines.find(t.req.addr&~Addr(63));if(line!=lines.end()) {line->second.sharers.erase(r.srcid);if(line->second.owner==r.srcid)line->second.unique=false;}}
        else if(r.nativeChi&&(r.resp&3)==1) {
            auto line=lines.find(t.req.addr&~Addr(63));
            if(line!=lines.end()&&line->second.owner==r.srcid)line->second.unique=false;
        }
        snoopOwner.erase(snp);
        if(chiStash(t.req.opcode) && r.srcid==t.stashNode && !t.stashPull) {
            if(!(r.resp&8)) { t.stashPull=false; respond(id); return; }
            t.stashPull=true; t.pullId=r.dbid; t.pullFlow=r.pullFlow;
            // An implicit allocating read shares the original address lock.
            auto found=lines.find(t.req.addr&~Addr(63));
            if(found!=lines.end()) {
                for(auto target:found->second.sharers) if(target!=t.stashNode) {
                    RawSnp s; s.nativeChi=t.req.nativeChi; s.srcid=nodeId; s.tgtid=target; s.txnid=allocateId(); s.opcode=t.req.opcode==0x23?7:4; s.addr=t.req.addr&~Addr(63); s.size=64;
                    s.flow=FlowGraphTrace::get().child(t.pullFlow,"snoop",s.addr);
                    snoopOwner[s.txnid]=id; t.snoops.insert(target); queue(SNP,s);
                }
            }
        }
        if(t.snoops.empty()) finishLookup(id);
        return;
    }
    fatal("Unmatched HN response opcode=%#x txn=%u\n",r.opcode,r.txnid);
}

void ChiExtendedHome::receiveDat(const RawDat& d) {
    if(d.nativeChi) {receiveNativeDat(d);return;}
    if(d.opcode==1) {
        auto it=snoopOwner.find(d.txnid); fatal_if(it==snoopOwner.end(),"Unknown snoop data\n");
        auto& t=txns.at(it->second); const auto id=it->second;
        fatal_if(d.beatOffset+d.data.size()>64 || !t.snoops.count(d.srcid),"Invalid snoop data range/source\n");
        if(t.data.size()!=64)t.data.resize(64);
        std::copy(d.data.begin(),d.data.end(),t.data.begin()+d.beatOffset);
        if(!d.flow.dataProducerInstructionIds.empty())t.req.flow.dataProducerInstructionIds=d.flow.dataProducerInstructionIds;
        t.dirty=t.dirty || (d.resp&4);
        if(d.last) { t.haveData=true; t.snoops.erase(d.srcid);snoopOwner.erase(it);
            if(d.resp==4||d.resp==0) {auto line=lines.find(t.req.addr&~Addr(63));if(line!=lines.end()) {line->second.sharers.erase(d.srcid);if(line->second.owner==d.srcid)line->second.unique=false;}}
            if(t.snoops.empty()) finishLookup(id); }
        return;
    }
    if(d.opcode==4) {
        auto it=memoryOwner.find(d.txnid); fatal_if(it==memoryOwner.end(),"Unknown memory data\n");
        auto& t=txns.at(it->second); const auto id=it->second;
        const unsigned start=t.req.opcode==4?t.req.addr%64:0;
        fatal_if(start+d.beatOffset+d.data.size()>64,"Invalid memory data range\n");
        std::copy(d.data.begin(),d.data.end(),t.data.begin()+start+d.beatOffset);
        if(d.last) {
            t.haveData=true; t.dirty=false; memoryOwner.erase(it);
            if(t.req.opcode==4) respond(id); else finishLookup(id);
        }
        return;
    }
    auto it=requester.find({d.srcid,d.txnid}); fatal_if(it==requester.end(),"Unknown write data\n");
    auto& t=txns.at(it->second); const auto id=it->second;
    fatal_if(d.dbid!=t.dbid || d.data.size()!=d.byteEnable.size() || d.beatOffset+d.data.size()>64,"Invalid write data\n");
    for(unsigned b=0;b<d.data.size();++b) {
        const unsigned offset=d.beatOffset+b;
        fatal_if(t.coverage[offset],"Duplicate write byte\n"); t.coverage[offset]=1;
        t.write[offset]=d.data[b]; t.mask[offset]=d.byteEnable[b];
    }
    if(d.last) { t.writeReady=true; start(id); }
}

void ChiExtendedHome::receiveNativeDat(const RawDat& d) {
    fatal_if(d.tgtid!=nodeId,"Wrong native HNF DAT target\n");
    if(d.opcode==1) {
        auto found=snoopOwner.find(d.txnid);
        fatal_if(found==snoopOwner.end(),"Unmatched native snoop DAT\n");
        const auto id=found->second;auto& t=txns.at(id);
        fatal_if(!t.req.nativeChi || !t.snoops.count(d.srcid),"Native snoop DAT identity mismatch\n");
        auto& data=t.snoopData.try_emplace(d.txnid,t.req.addr,64).first->second;
        const auto error=data.accept(d);fatal_if(!error.empty(),"Native snoop DAT: %s\n",error);
        if(data.complete()) {
            fatal_if(!data.allKnown(),"Native CPU snoop supplied unknown data\n");
            const auto cached=lines.find(t.req.addr&~Addr(63));
            const bool fromOwner=cached!=lines.end()&&cached->second.unique&&cached->second.owner==d.srcid;
            if((d.resp&4)||fromOwner||!t.dirty) {
                t.data=data.data();
                if(!d.flow.dataProducerInstructionIds.empty())t.req.flow.dataProducerInstructionIds=d.flow.dataProducerInstructionIds;
            }
            t.dirty=t.dirty||(d.resp&4);t.haveData=true;
            FlowGraphTrace::get().event(d.flow,"complete",nodeId);
            t.snoops.erase(d.srcid);t.snoopData.erase(d.txnid);snoopOwner.erase(found);
            if((d.resp&3)==0) {auto line=lines.find(t.req.addr&~Addr(63));if(line!=lines.end()) {line->second.sharers.erase(d.srcid);if(line->second.owner==d.srcid)line->second.unique=false;}}
            else if((d.resp&3)==1) {
                auto line=lines.find(t.req.addr&~Addr(63));
                if(line!=lines.end()&&line->second.owner==d.srcid)line->second.unique=false;
            }
            if(t.snoops.empty())finishLookup(id);
        }
        return;
    }
    if(d.opcode==4) {
        auto found=memoryOwner.find(d.txnid);
        fatal_if(found==memoryOwner.end(),"Unmatched native SN CompData\n");
        const auto id=found->second;auto& t=txns.at(id);
        fatal_if(!t.req.nativeChi || d.srcid!=sn(t.req.addr),"Native memory DAT identity mismatch\n");
        if(!t.incomingMemory)t.incomingMemory.emplace(t.req.addr,t.req.opcode==4?t.req.size:64);
        const auto error=t.incomingMemory->accept(d);fatal_if(!error.empty(),"Native memory DAT: %s\n",error);
        if(t.incomingMemory->complete()) {
            fatal_if(!t.incomingMemory->allKnown(),"Native SN supplied unknown data\n");
            t.data=t.incomingMemory->data();t.haveData=true;t.dirty=false;memoryOwner.erase(found);
            if(t.req.opcode==4)respond(id);else finishLookup(id);
        }
        return;
    }
    fatal_if(d.opcode!=2&&d.opcode!=3,"Unsupported native HNF DAT opcode\n");
    auto found=std::find_if(txns.begin(),txns.end(),[&](const auto& p) {
        return p.second.req.srcid==d.srcid && p.second.dbid==d.txnid &&
               homeWriteData(p.second.req.opcode);
    });
    fatal_if(found==txns.end(),"Unknown native write DAT DBID\n");
    const auto id=found->first;auto& t=found->second;
    fatal_if(!t.req.nativeChi || t.writeReady || t.evictWithoutData ||
             d.opcode!=(homeCopyback(t.req.opcode)?2:3),"Invalid native write DAT state/opcode\n");
    if(!t.incomingWrite)t.incomingWrite.emplace(t.req.addr,t.req.size);
    const auto error=t.incomingWrite->accept(d);fatal_if(!error.empty(),"Native write DAT: %s\n",error);
    if(t.incomingWrite->complete()) {
        fatal_if(!t.incomingWrite->allKnown(),"Native write supplied unknown data\n");
        t.write=t.incomingWrite->data();t.mask=t.incomingWrite->byteEnable();
        if(t.req.opcode==0x42)
            fatal_if(d.resp!=0&&d.resp!=1&&d.resp!=2,"WEOE only accepts clean CopyBackWriteData\n");
        if(homeCopyback(t.req.opcode)&&d.resp!=0) {
            fatal_if(std::any_of(t.mask.begin(),t.mask.end(),[](auto b){return b!=1;}),
                     "Full copyback requires every byte enabled unless Resp=I\n");
        }
        if(t.req.opcode==0x1d) {
            const auto start=NativeWire::transferStart(t.req.addr,t.req.size);
            fatal_if(std::any_of(t.mask.begin()+start,t.mask.begin()+start+t.req.size,[](auto b){return b!=1;}),
                     "WriteNoSnpFull requires every requested byte enabled\n");
        }
        t.writeResp=d.resp;t.writeDirty=d.resp&4;t.writeReady=true;start(id);
    }
}

void ChiExtendedHome::retire(uint32_t id) {
    auto it=txns.find(id); fatal_if(it==txns.end(),"Duplicate HN completion\n");
    FlowGraphTrace::get().event(it->second.req.flow,"complete",nodeId);
    requester.erase({it->second.req.srcid,it->second.req.txnid}); txns.erase(it);
}
}

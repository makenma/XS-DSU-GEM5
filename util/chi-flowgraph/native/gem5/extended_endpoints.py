def extend(result, read, baseline, replace):
    prefix='src/mem/cache/CHI/'
    s=read('Cache2ChiBridge.hh')
    s=replace(s,'class Cache2ChiBridge :','class ChiIntentCache;\nclass Cache2ChiBridge :')
    s=replace(s,'        bool hasDbid = false;', '        bool extended = false;\n        bool hasDbid = false;')
    s=replace(s,'    DrainState drain() override;', '''    DrainState drain() override;
    void submitIntent(PacketPtr p) { pendingReqPkts.push(p); schedulePump(); }
    void attachIntentCache(ChiIntentCache* c,bool accept) { intentCache=c; acceptStash=accept; }
    bool extendedProfile=false;
    ChiIntentCache* intentCache=nullptr;
    bool acceptStash=true;
    std::function<void(PacketPtr)> intentSnoopResponse;
    bool probeIntent(PacketPtr packet,std::function<void(PacketPtr)> callback) {
        panic_if(intentSnoopResponse,"Overlapping local intent snoop\\n");
        intentSnoopResponse=std::move(callback);cachePort.sendTimingSnoopReq(packet);
        const bool pending=packet->cacheResponding();
        if(!pending) {intentSnoopResponse={};delete packet;}
        return pending;
    }
    void handleStash(const RawSnp& snp);''')
    result[prefix+'Cache2ChiBridge.hh']=s
    s=baseline(prefix+'Cache2ChiBridge.py')
    s+='\n    extended_profile = Param.Bool(False, "Issue H test intents and RNSD ordinary reads")\n'
    result[prefix+'Cache2ChiBridge.py']=s
    s=read('Cache2ChiBridge.cc')
    s='#include "mem/cache/CHI/ChiProtocolIntent.hh"\n#include "mem/cache/CHI/ChiIntentCache.hh"\n'+s
    s=replace(s,'    nextTxnId = firstTxnIdInNamespace(', '    extendedProfile=p.extended_profile;\n    nextTxnId = firstTxnIdInNamespace(')
    s=replace(s,'        return cacheRespondingCoordinationIntent();', '''        auto intent=cacheRespondingCoordinationIntent();
        if(extendedProfile)intent.requiresCompAck=true;
        return intent;''')
    s=replace(s,'        txn.txnid = *txnid;', '        txn.extended=extendedProfile;\n        txn.txnid = *txnid;')
    s=replace(s,'    ack.txnid = txn.txnid;', '    ack.txnid = txn.extended?txn.dbid:txn.txnid;')
    s=replace(s,'    acceptReadDataBeat(txn, dat, dataBeatBytes, name().c_str());', '    if(txn.extended) {txn.dbid=dat.dbid;txn.hasDbid=true;}\n    acceptReadDataBeat(txn, dat, dataBeatBytes, name().c_str());')
    s=replace(s,'Cache2ChiBridge::cacheRecvTimingSnoopResp(PacketPtr pkt)\n{', '''Cache2ChiBridge::cacheRecvTimingSnoopResp(PacketPtr pkt)
{
    if(intentSnoopResponse) {auto callback=std::move(intentSnoopResponse);intentSnoopResponse={};callback(pkt);return true;}''')
    s=replace(s,'    if (pkt->hasData()) {\n        sendSnoopData(snoop, pkt);', '''    if (extendedProfile && snoop.snp.opcode==0x0a) {
        sendSnoopRsp(snoop,RespState::I);
    } else if (pkt->hasData()) {
        sendSnoopData(snoop, pkt);''')
    s=replace(s,'dat.flow=snoop.snp.flow; dat.flow.messageId.clear();', 'dat.flow=snoop.snp.flow; if(extendedProfile)dat.flow.dataProducerInstructionIds=FlowGraphTrace::get().producers(nodeId,snoop.snp.addr); dat.flow.messageId.clear();')
    s=replace(s,'    MemoryIntent intent{};\n    intent.needsResponse', '''    if(auto* special=dynamic_cast<ChiProtocolIntent*>(pkt->senderState)) {
        const auto op=special->opcode;
        MemoryIntent i{};i.needsResponse=true;i.expectsComp=true;
        i.expectsData=chiRead(op);i.carriesData=chiWrite(op);i.expectsDbid=i.carriesData;
        i.requiresCompAck=chiAllocating(op)||op==12;i.isPartial=op==0x18||op==0x1c;
        i.txnClass=i.expectsData?TxnClass::Read:i.carriesData?TxnClass::Write:TxnClass::Maintenance;
        i.kind=i.expectsData?(op==7?IntentKind::ReadUnique:IntentKind::ReadShared):
            chiCopyback(op)?(op==0x15?IntentKind::WriteEvictFull:IntentKind::WriteBackFull):
            i.carriesData?(i.isPartial?IntentKind::WriteUniquePtl:IntentKind::WriteUniqueFull):
            op==12?IntentKind::MakeUnique:IntentKind::MakeInvalid;
        return i;
    }
    MemoryIntent intent{};
    intent.needsResponse''')
    s=replace(s,'    req.flow = FlowGraphTrace::get().root("rnf"); req.flow.address = req.addr;', '''    if(auto* special=dynamic_cast<ChiProtocolIntent*>(pkt->senderState)) {
        req.opcode=special->opcode;req.addr=pkt->getAddr();req.stashNid=special->stashTarget;
        if(req.opcode==4||req.opcode==0x1c||req.opcode==0x1d) { req.memattr=0;req.snpattr=0; }
        if(req.opcode==13||req.opcode==0x25)req.memattr=5;
    } else if(extendedProfile && req.opcode==1) req.opcode=0x26;
    if(extendedProfile && !dynamic_cast<ChiProtocolIntent*>(pkt->senderState) && req.opcode==12)req.opcode=0x0b;
    if(extendedProfile && req.opcode==13)req.memattr=5;
    req.flow = FlowGraphTrace::get().root("rnf"); req.flow.address = req.addr;
    req.flow.causeInstructionIds=FlowGraphTrace::get().causes(pkt->req);
    if(auto* special=dynamic_cast<ChiProtocolIntent*>(pkt->senderState))
        if(!special->instructionId.empty()) req.flow.causeInstructionIds={special->instructionId};
    if(intent.carriesData) req.flow.dataProducerInstructionIds=FlowGraphTrace::get().producers(nodeId,req.addr);
    FlowGraphTrace::get().bind(pkt->req,req.flow);''')
    s=replace(s,'        intent.kind = IntentKind::MakeUnique;\n        intent.txnClass', '        intent.kind = IntentKind::MakeUnique;\n        if(extendedProfile)intent.requiresCompAck=true;\n        intent.txnClass')
    s=replace(s,'        txn.gotComp = true;\n        txn.lastResponseChannel = RSP;', '        if(txn.extended && txn.intent.kind==IntentKind::MakeUnique) {txn.dbid=rsp.dbid;txn.hasDbid=true;if(rsp.resp==3)txn.pkt->setCacheResponding();}\n        txn.gotComp = true;\n        txn.lastResponseChannel = RSP;',count=2)
    s=replace(s,'        dat.data.assign(src + copied, src + copied + beatBytes);\n        dat.byteEnable.assign(beatBytes, 1);', '''        dat.data.assign(src + copied, src + copied + beatBytes);
        dat.byteEnable.assign(beatBytes, 1);
        if(intent.isPartial) {
            const auto& be=pkt->req->getByteEnable();
            for(unsigned i=0;i<beatBytes;++i) dat.byteEnable[i]=be.at(copied+i);
            for(unsigned i=0;i<beatBytes;++i) if(!dat.byteEnable[i]) dat.data[i]=0;
        }''')
    s=replace(s,'    if (attempts == 0 && transactionLatencyTraceEnabled)', '''    if(snp.opcode==0x0b || snp.opcode==0x0c) { handleStash(snp);return; }
    if (attempts == 0 && transactionLatencyTraceEnabled)''')
    s=replace(s,'    const auto decoded = decodeSnp(snp.opcode);\n    switch', '    if(snp.opcode==8) return MemCmd::ReadCleanReq;\n    const auto decoded = decodeSnp(snp.opcode);\n    switch')
    s=replace(s,'    if(snp.opcode==8) return MemCmd::ReadCleanReq;', '    if(snp.opcode==8) return MemCmd::ReadExReq;')
    s=replace(s,'    return decoded.minor == SnpMinor::SnpUnique ||', '    return snp.opcode==8 || decoded.minor == SnpMinor::SnpUnique ||')
    s=replace(s,'        dat.resp = static_cast<uint8_t>(RespState::Unknown);', '''        dat.resp = extendedProfile?((intent.kind==IntentKind::WriteEvictFull||intent.kind==IntentKind::WriteBackFull||intent.kind==IntentKind::WriteCleanFull)?(pkt->hasSharers()?3:2):0):static_cast<uint8_t>(RespState::Unknown);''')
    s=replace(s,'            snoop.invalidating ? RespState::I_PD : RespState::SD_PD);', '            snoop.invalidating ? RespState::I_PD : RespState::SD_PD);\n        if(extendedProfile)dat.resp=snoop.invalidating?4:3;')
    s=replace(s,'    ack.resp = static_cast<uint8_t>(RespState::Unknown);','    ack.resp = extendedProfile?0:static_cast<uint8_t>(RespState::Unknown);')
    s=replace(s,'    if (dat.resp == static_cast<uint8_t>(RespState::SC) ||', '    if(txn.extended && dat.resp==6 && !txn.pkt->cacheResponding())txn.pkt->setCacheResponding();\n    if (dat.resp == static_cast<uint8_t>(RespState::SC) ||')
    s=replace(s,'    if (txn.intent.needsResponse) {\n        const std::string cmd', '''    if(auto* special=dynamic_cast<ChiProtocolIntent*>(pkt->senderState)) {
        pkt->makeTimingResponse();
        if(txn.intent.expectsData) pkt->setData(txn.readData.data());
        FlowGraphTrace::get().event(txn.req.flow,"complete",nodeId);
        special->complete(pkt);
        if(intentCache) intentCache->recordState(txn.req.addr,txn.req.flow,nodeId);
        delete pkt->popSenderState();delete pkt;
        if(compAck) pendingCompAcks.push({*compAck,txnid}); else freeTxn(txnid);
        return;
    }
    if (txn.intent.needsResponse) {
        const std::string cmd''')
    s+='''
namespace gem5::Chi {
void Cache2ChiBridge::handleStash(const RawSnp& snp) {
    RawRsp rsp{};rsp.srcid=nodeId;rsp.tgtid=snp.srcid;rsp.txnid=snp.txnid;rsp.opcode=1;rsp.flow=snp.flow;rsp.flow.messageId.clear();
    ChiIntentCache::Snapshot snapshot;
    bool accept=acceptStash && intentCache && intentCache->prepareIntent(snp.addr,snapshot);
    if(accept && snapshot.valid) { intentCache->abortIntent();accept=false; }
    auto id=accept?allocateTxnId():std::optional<uint32_t>{};
    if(accept&&!id) { intentCache->abortIntent();accept=false; }
    if(accept) {
        panic_if(*id>255,"Stash implicit read DBID exceeds coarse 8-bit field\\n");
        const auto op=uint8_t(snp.opcode==0x0b?7:0x26);
        auto request=std::make_shared<Request>(snp.addr,64,0,Request::funcRequestorId);
        auto* p=new Packet(request,MemCmd::ReadReq);p->allocate();
        auto* special=new ChiProtocolIntent;special->opcode=op;
        special->complete=[this,address=snp.addr,op](PacketPtr result) {
            std::vector<uint8_t> data(result->getConstPtr<uint8_t>(),result->getConstPtr<uint8_t>()+64);
            intentCache->commitIntent(address,op,data,result->cacheResponding(),result->hasSharers());
        };p->pushSenderState(special);
        TxnEntry t{};t.extended=true;t.txnid=*id;t.pkt=p;t.intent=classify(p);t.req.addr=snp.addr;t.req.size=64;t.req.srcid=nodeId;t.req.tgtid=snp.srcid;t.req.txnid=*id;t.req.opcode=op;t.req.expCompAck=1;
        t.req.flow=FlowGraphTrace::get().child(snp.flow,"data-pull",snp.addr);t.readExpectedBytes=64;
        rsp.pullFlow=t.req.flow; rsp.resp=8;rsp.dbid=*id;
        txns.emplace(*id,std::move(t));FlowGraphTrace::get().event(rsp.pullFlow,"allocate",nodeId,{{"opcode",FlowGraphTrace::opcode(REQ,op)},{"implicitDataPull",true}});
    }
    FlowGraphTrace::get().stamp(rsp);
    SnoopEntry e{};e.snp=snp;e.pendingRsp=rsp;pendingSnoopResponses.push_back(std::move(e));schedulePump();
}
}
'''
    result[prefix+'Cache2ChiBridge.cc']=s
    s=read('Cache2ChiBridgeData.cc')
    s=replace(s,'dat.HomeNID != txn.req.tgtid || dat.dbid != 0 ||','dat.HomeNID != txn.req.tgtid || (!txn.extended && dat.dbid != 0) ||')
    result[prefix+'Cache2ChiBridgeData.cc']=s
    s=read('Chi2ClassicMemBridge.cc')
    s=replace(s,'    std::memcpy(pkt->getPtr<uint8_t>(), dat.data.data(), txn.expectedBytes);', '''    std::memcpy(pkt->getPtr<uint8_t>(), dat.data.data(), txn.expectedBytes);
    if(txn.req.opcode==0x1c) {
        std::vector<bool> mask(dat.byteEnable.begin(),dat.byteEnable.end()); request->setByteEnable(mask);
    }''')
    s=replace(s,'    if (decoded.minor == ReqMinor::WriteNoSnp &&\n        req.opcode == Chi2ClassicMemTxnPolicy::WriteNoSnpFullOpcode)', '    if ((decoded.minor == ReqMinor::WriteNoSnp &&\n        req.opcode == Chi2ClassicMemTxnPolicy::WriteNoSnpFullOpcode) || req.opcode==0x1c)')
    result[prefix+'Chi2ClassicMemBridge.cc']=s
    s=read('Chi2ClassicMemTxnPolicy.hh')
    s=replace(s,'        return block_size != 0 &&', '''        if(req.opcode==0x1c) return req.size && req.size<=block_size && !(req.size&(req.size-1)) && !(req.addr%req.size) && (req.addr%block_size)+req.size<=block_size;
        return block_size != 0 &&''')
    s=replace(s,'            allNonZero(dat.byteEnable) &&', '            (req.opcode==0x1c || allNonZero(dat.byteEnable)) &&')
    result[prefix+'Chi2ClassicMemTxnPolicy.hh']=s
    s=baseline(prefix+'ChiRouterRefModel.cc')
    s='#include "mem/cache/CHI/FlowGraphTrace.hh"\n'+s
    s=replace(s,'    stats.internalSentFlits[chIdx(ch)][direction]++;','    FlowGraphTrace::get().route(flit.flit,name(),direction);\n    stats.internalSentFlits[chIdx(ch)][direction]++;')
    result[prefix+'ChiRouterRefModel.cc']=s
    s=baseline('src/mem/cache/cache.cc')
    s='#include "mem/cache/CHI/FlowGraphTrace.hh"\n'+s
    s=replace(s,'Cache::recvTimingReq(PacketPtr pkt)\n{','Cache::recvTimingReq(PacketPtr pkt)\n{\n    Chi::FlowGraphTrace::get().observeInstruction(pkt->req);\n    if(pkt->isWrite()) Chi::FlowGraphTrace::get().observeWrite(pkt->req);')
    result['src/mem/cache/cache.cc']=s
    s=baseline('src/mem/cache/base.cc')
    s='#include "mem/cache/CHI/FlowGraphTrace.hh"\n'+s
    s=replace(s,'BaseCache::init()\n{', '''BaseCache::init()
{
    // Test controllers can register requestors after cache construction.
    for(auto& slice:prevReqCycles) slice.resize(system->maxRequestors(),Cycles(0));''')
    s=replace(s,'                mshr->allocateTarget(pkt, forward_time, order++,', '''                Chi::FlowGraphTrace::get().merge(mshr->getTarget()->pkt->req,pkt->req);
                mshr->allocateTarget(pkt, forward_time, order++,''')
    result['src/mem/cache/base.cc']=s
    s=baseline('src/cpu/o3/lsq.cc')
    s='#include "mem/cache/CHI/FlowGraphTrace.hh"\n'+s
    s=replace(s,'    req->setByteEnable(byteEnable);','    req->setByteEnable(byteEnable);\n    Chi::FlowGraphTrace::get().bindBufferedStores(req);')
    result['src/cpu/o3/lsq.cc']=s
    s=baseline('src/cpu/o3/lsq_unit.cc')
    s='#include "mem/cache/CHI/FlowGraphTrace.hh"\n'+s
    s=replace(s,'                    request->_numOutstandingPackets++;','                    Chi::FlowGraphTrace::get().bufferStore(req);\n                    request->_numOutstandingPackets++;')
    s=replace(s,'            // finish once store','            Chi::FlowGraphTrace::get().bufferStore(request->mainReq());\n            // finish once store')
    s=replace(s,'        loadQueue.back().instruction()->setSquashed();', '''        Chi::FlowGraphTrace::get().cancelInstruction(cpu->cpuId(),loadQueue.back().instruction()->seqNum);
        loadQueue.back().instruction()->setSquashed();''')
    result['src/cpu/o3/lsq_unit.cc']=s
    s=baseline('src/mem/page_table.cc')
    s=replace(s,'    req->setPaddr(paddr);', '''    req->setPaddr(paddr);
    const auto* entry=lookup(req->getVaddr());
    if(entry && (entry->flags & Uncacheable)) req->setFlags(Request::UNCACHEABLE | Request::STRICT_ORDER);''')
    result['src/mem/page_table.cc']=s
    s=read('SConscript')
    for name in ['ChiIntentCache','ChiTestControl','ChiExtendedHome']:
        s+=f"\nSimObject('{name}.py',sim_objects=['{name}'])\nSource('{name}.cc')\n"
    result[prefix+'SConscript']=s

#include "mem/cache/CHI/RniDmaAdapter.hh"
#include <algorithm>
#include <limits>
#include "base/trace.hh"
#include "debug/DmaPath.hh"
#include "mem/cache/CHI/base/RspOpcode.hh"
namespace gem5::Chi
{
RniDmaAdapter::RniDmaAdapter(const RniDmaAdapterParams &p) : ClockedObject(p),
    ruby::Consumer(this), in(*this), loader(*this), chi(name()+".chi_side", this),
    node(p.node_id), home(p.home_node_id), ioNode(p.io_node_id),
    maxEntries(p.max_entries), maxChi(p.max_chi), memory(p.memory_range),
    ioRange(p.io_range), advertised(p.upstream_ranges.begin(), p.upstream_ranges.end()),
    allowIo(p.allow_io), holdRxUntil(clockEdge(p.test_hold_rx_cycles)),
    pumpEvent([this]{ pump(); }, name()+".pump"), stats(this)
{ fatal_if(!maxEntries || !maxChi || maxChi>maxEntries, "invalid RNI resources"); }
Port &RniDmaAdapter::getPort(const std::string &s, PortID id)
{ if(s=="dma_side") return in; if(s=="chi_side") return chi;
  if(s=="loader_side") return loader;
  return ClockedObject::getPort(s,id); }
void RniDmaAdapter::functional(PacketPtr p)
{
    // SE initializes ELF/stack and proxies syscalls through CPU functional
    // ports. Neither MMIO nor DMA buffers may use that unmeasured facility.
    panic_if(memory.intersects(p->getAddrRange()) || ioRange.intersects(p->getAddrRange()) ||
             !loader.isConnected(), "RNI functional DMA/MMIO bypass prohibited");
    loader.sendFunctional(p);
}
AddrRangeList RniDmaAdapter::InPort::getAddrRanges() const
{ if (!d.advertised.empty()) return d.advertised;
  return d.allowIo ? AddrRangeList{d.memory,d.ioRange} : AddrRangeList{d.memory}; }
void RniDmaAdapter::wakeup() { schedulePump(); }
void RniDmaAdapter::schedulePump()
{ if(!pumpEvent.scheduled()) schedule(pumpEvent,nextCycle()); }
void RniDmaAdapter::updateOccupancy()
{ stats.occupancyTicks+=(curTick()-lastOccupancy)*parents.size(); lastOccupancy=curTick(); }
bool RniDmaAdapter::accept(PacketPtr p)
{
    panic_if(!p->needsResponse(), "RNI requires response-bearing timing requests");
    if (parents.size()>=maxEntries) { reqRetry=true; ++stats.reqRetry; return false; }
    updateOccupancy();
    parents.emplace_back();
    Entry &e=parents.back(); e.pkt=p; e.id=++nextParent; e.start=curTick();
    const bool originalRead=p->isRead();
    const bool originalWrite=p->isWrite();
    const Addr a=p->getAddr(); unsigned n=p->getSize();
    const bool safe=n && a<=std::numeric_limits<Addr>::max()-(n-1);
    e.io=allowIo && safe && ioRange.contains(a) && ioRange.contains(a+n-1);
    const bool ram=safe && memory.contains(a) && memory.contains(a+n-1);
    e.error=(!e.io && !ram) || !(p->cmd==MemCmd::ReadReq || p->cmd==MemCmd::WriteReq) ||
            !p->needsResponse() || n>65536 || (e.io && (n>8 || (a&63)+n>64));
    if(e.error) complete(e);
    ++stats.packets; stats.entries=parents.size();
    if(parents.size()>stats.entryHighWater.value()) stats.entryHighWater=parents.size();
    if(originalRead) stats.readBytes+=n;
    if(originalWrite) stats.writeBytes+=n;
    DPRINTF(DmaPath, "PACKET parent=%llu addr=%#llx bytes=%u io=%u write=%u\n", e.id,a,n,e.io,originalWrite);
    schedulePump(); return true;
}
bool RniDmaAdapter::lineBusy(const Entry &e) const
{
    const Addr line=(e.pkt->getAddr()+e.offset)&~Addr(63);
    for(const auto &p:parents) {
        if(&p==&e) break;
        if(!p.ready && !p.io && p.pkt->isWrite() &&
           ((p.pkt->getAddr()+p.offset)&~Addr(63))==line) return true;
    }
    for(const auto &kv:children) {
        const Entry &p=*kv.second;
        if(&p!=&e && !p.io && p.pkt->isWrite() && p.req.addr==line) return true;
    }
    return false;
}
void RniDmaAdapter::startChild(Entry &e, bool write)
{
    fatal_if(nextTxn==UINT32_MAX,"RNI monotonic TxnID exhausted");
    const Addr a=e.pkt->getAddr()+e.offset;
    e.count=std::min<unsigned>(e.pkt->getSize()-e.offset,64-(a&63));
    e.expected=e.io ? e.count : 64;
    e.writing=write;
    e.req={}; e.req.srcid=node; e.req.tgtid=e.io?ioNode:home;
    e.req.ReturnNid=node; e.req.txnid=++nextTxn; e.req.addr=e.io?a:(a&~Addr(63));
    e.req.size=e.expected; e.req.AllowRetry=e.io?0:1;
    e.req.opcode=e.io ? (write?0x5a:0x04) : (write?0x59:0x03);
    e.req.expCompAck=!write && !e.io;
    e.injected=e.retry=e.grant=e.comp=e.ack=false;
    e.dbid=0; e.sent=e.received=0;
    e.coverage.assign(e.expected,0);
    if(!write) e.data.assign(e.expected,0);
    else if(e.io) {
        e.data.assign(e.pkt->getConstPtr<uint8_t>()+e.offset,
                      e.pkt->getConstPtr<uint8_t>()+e.offset+e.count);
    } else {
        if(e.data.size()!=64) e.data.assign(64,0);
        const auto &be=e.pkt->req->getByteEnable();
        for(unsigned i=0;i<e.count;++i)
            if(be.empty() || be[e.offset+i])
                e.data[(a&63)+i]=e.pkt->getConstPtr<uint8_t>()[e.offset+i];
    }
    children.emplace(e.req.txnid,&e);
}
void RniDmaAdapter::consume()
{
    if (curTick() < holdRxUntil) return;
    while(auto f=chi.getTxFlit(RSP)) {
        auto r=std::get<RawRsp>(*f); auto it=children.find(r.txnid);
        panic_if(it==children.end(),"RNI unknown RSP txn=%u",r.txnid);
        Entry &e=*it->second;
        panic_if(r.srcid!=e.req.tgtid || r.tgtid!=node,"RNI RSP identity");
        const auto op=decodeRsp(r.opcode).minor;
        if(op==RspMinor::RetryAck) { e.retry=true; ++stats.protocolRetry; }
        else if(op==RspMinor::PCrdGrant) {
            panic_if(!e.retry,"RNI grant without RetryAck");
            e.grant=true; e.req.AllowRetry=0; e.req.pcrdtype=r.pcrdtype;
        } else if(op==RspMinor::DBIDResp || op==RspMinor::DBIDRespOrd) {
            panic_if(!e.writing || e.dbid,"RNI unexpected DBID"); e.dbid=r.dbid;
        } else if(op==RspMinor::Comp) {
            panic_if(!e.writing && !r.respErr, "RNI read requires complete CompData");
            e.comp=true; e.error |= r.respErr!=0;
        }
        else panic("RNI unsupported RSP=%#x; HNF late_write_comp is required",r.opcode);
    }
    while(auto f=chi.getTxFlit(DAT)) {
        auto d=std::get<RawDat>(*f); auto it=children.find(d.txnid);
        panic_if(it==children.end(),"RNI unknown DAT txn=%u",d.txnid);
        Entry &e=*it->second;
        unsigned off=d.beatOffset, n=d.data.size();
        panic_if(e.writing || d.opcode!=0x04 || d.srcid!=e.req.tgtid || d.tgtid!=node ||
                 d.HomeNID!=e.req.tgtid || d.dbid || off+n>e.expected || !n ||
                 d.dataid*32!=off || n!=std::min(32u,e.expected-off) ||
                 bool(d.last)!=(off+n==e.expected) || d.byteEnable.size()!=n ||
                 d.chunkValid.size()!=(n+7)/8,"RNI malformed DAT geometry/identity");
        for(unsigned i=0;i<n;++i) {
            panic_if(e.coverage[off+i] || !d.byteEnable[i],"RNI duplicate/masked read DAT");
            e.coverage[off+i]=1; e.data[off+i]=d.data[i];
        }
        if (d.last && e.received == 0 && off != 0) ++stats.reorderedData;
        e.received+=n; e.error |= d.respErr!=0 || d.poison!=0;
        if(e.received==e.expected) e.comp=true;
        DPRINTF(DmaPath,"DAT parent=%llu child=%u offset=%u bytes=%u\n",e.id,d.txnid,off,n);
    }
    panic_if(chi.hasTxFlit(SNP),"RNI owns no cached data; unexpected snoop");
}
void RniDmaAdapter::finishChild(Entry &e)
{
    const uint32_t txn=e.req.txnid;
    children.erase(txn);
    --injectedChildren;
    stats.outstanding=injectedChildren;
    DPRINTF(DmaPath,"CHI_DONE parent=%llu child=%u write=%u\n",e.id,txn,e.writing);
    if(e.error) { complete(e); return; }
    if(!e.writing && e.pkt->isWrite()) { startChild(e,true); return; }
    if(!e.writing) {
        const unsigned off=e.io?0:(e.pkt->getAddr()+e.offset)&63;
        std::copy(e.data.begin()+off,e.data.begin()+off+e.count,
                  e.pkt->getPtr<uint8_t>()+e.offset);
    }
    e.offset+=e.count; e.req.txnid=0;
    if(e.offset==e.pkt->getSize()) complete(e);
}
void RniDmaAdapter::complete(Entry &e)
{
    e.ready=true; e.pkt->makeTimingResponse();
    if(e.error) { e.pkt->setBadAddress(); ++stats.errors; }
}
void RniDmaAdapter::pump()
{
    consume();
    for(auto &e:parents) {
        if(e.ready) continue;
        if(!e.req.txnid) {
            if(children.size()>=maxChi || (!e.io && lineBusy(e))) continue;
            const Addr a=e.pkt->getAddr()+e.offset;
            const bool full=(a&63)==0 && e.pkt->getSize()-e.offset>=64 &&
                            !e.pkt->isMaskedWrite();
            startChild(e,e.pkt->isWrite() && (e.io || full));
        }
        if(e.retry) {
            if(!e.grant) continue;
            if(!chi.enqueueRx(REQ,e.req)) { ++stats.creditStalls; continue; }
            ++stats.chiTransactions; e.retry=e.grant=false;
        } else if(!e.injected) {
            if(!chi.enqueueRx(REQ,e.req)) { ++stats.creditStalls; continue; }
            ++stats.chiTransactions; e.injected=true;
            if(e.io) ++stats.pioRequests;
            if (e.writing) stats.chiWriteBytes+=e.expected;
            else stats.chiReadBytes+=e.expected;
            ++injectedChildren;
            stats.outstanding=injectedChildren;
            if(injectedChildren>stats.chiHighWater.value()) stats.chiHighWater=injectedChildren;
            DPRINTF(DmaPath,"REQ parent=%llu child=%u opcode=%#x addr=%#llx\n",e.id,e.req.txnid,e.req.opcode,e.req.addr);
        }
        if(e.writing && e.dbid) {
            while(e.sent<e.expected) {
                RawDat d{}; d.srcid=node; d.tgtid=e.req.tgtid; d.txnid=e.req.txnid;
                d.HomeNID=e.req.tgtid; d.opcode=0x03; d.dbid=e.dbid;
                d.dataid=e.sent/32; d.beatOffset=e.sent;
                unsigned n=std::min(32u,e.expected-e.sent); d.last=e.sent+n==e.expected;
                d.data.assign(e.data.begin()+e.sent,e.data.begin()+e.sent+n);
                d.byteEnable.assign(n,1); d.chunkValid.assign((n+7)/8,1);
                if(e.io && e.pkt->isMaskedWrite()) {
                    auto &be=e.pkt->req->getByteEnable();
                    for(unsigned i=0;i<n;++i) d.byteEnable[i]=be[e.offset+e.sent+i];
                }
                if(!chi.enqueueRx(DAT,d)) { ++stats.creditStalls; break; }
                e.sent+=n;
            }
        }
        if(!e.comp || (e.writing && !e.error && e.sent<e.expected)) continue;
        if(!e.writing && !e.io && !e.ack) {
            RawRsp ack{}; ack.srcid=node; ack.tgtid=home; ack.txnid=e.req.txnid; ack.opcode=0x02;
            if(!chi.enqueueRx(RSP,ack)) { ++stats.creditStalls; continue; }
            e.ack=true;
        }
        finishChild(e);
    }
    for(auto it=parents.begin();it!=parents.end() && !respBlocked;) {
        if(!it->ready) { ++it; continue; }
        // Save everything needed before sendTimingResp: DmaPort may delete pkt.
        const Tick elapsed=curTick()-it->start;
        const uint64_t parentId=it->id;
        if(!in.sendTimingResp(it->pkt)) { respBlocked=true; ++stats.respRetry; break; }
        DPRINTF(DmaPath,"RESPONSE parent=%llu elapsed=%llu\n",parentId,elapsed);
        updateOccupancy(); ++stats.responses; stats.latencyTicks+=elapsed;
        it=parents.erase(it); stats.entries=parents.size();
    }
    if(reqRetry && parents.size()<maxEntries) { reqRetry=false; in.sendRetryReq(); }
    if(idle()) {
        if(drainState()==DrainState::Draining) signalDrainDone();
    } else if(!respBlocked || !children.empty()) schedulePump();
}
bool RniDmaAdapter::idle() const
{ return parents.empty() && children.empty() && !reqRetry && !respBlocked &&
         !pumpEvent.scheduled() && !chi.hasTxFlit(RSP) && !chi.hasTxFlit(DAT); }
DrainState RniDmaAdapter::drain() { return idle()?DrainState::Drained:DrainState::Draining; }
void RniDmaAdapter::serialize(CheckpointOut &) const
{ fatal("RNI checkpoint/restore is not implemented"); }
}

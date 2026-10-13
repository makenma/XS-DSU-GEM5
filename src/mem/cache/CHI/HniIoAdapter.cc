#include "mem/cache/CHI/HniIoAdapter.hh"
#include <limits>
#include "base/trace.hh"
#include "debug/DmaPath.hh"
#include "sim/system.hh"
namespace gem5::Chi
{
HniIoAdapter::HniIoAdapter(const HniIoAdapterParams &p) : ClockedObject(p),
    ruby::Consumer(this), pio(*this), chi(name()+".chi_side",this), node(p.node_id),
    range(p.io_range), requestor(p.system->getRequestorId(this)),
    pumpEvent([this]{ pump(); },name()+".pump"), stats(this)
{}
Port &HniIoAdapter::getPort(const std::string &s, PortID id)
{ if(s=="pio_side") return pio; if(s=="chi_side") return chi; return ClockedObject::getPort(s,id); }
void HniIoAdapter::wakeup() { schedulePump(); }
void HniIoAdapter::schedulePump()
{ if(!pumpEvent.scheduled()) schedule(pumpEvent,nextCycle()); }
void HniIoAdapter::makePacket()
{
    auto r=std::make_shared<Request>(req->addr,req->size,Request::UNCACHEABLE,requestor);
    pkt=new Packet(r,write?MemCmd::WriteReq:MemCmd::ReadReq); pkt->allocate();
}
bool HniIoAdapter::response(PacketPtr p)
{
    panic_if(p!=pkt || !sent || !p->isResponse(),"HNI unexpected PIO response");
    error=p->isError(); ready=true; schedulePump(); return true;
}
void HniIoAdapter::pump()
{
    if(!req) {
        if(auto f=chi.getRxFlit(REQ)) {
            req=std::get<RawReq>(*f); start=curTick(); ++sequence; ++stats.packets;
            stats.entries=1; stats.entryHighWater=1; stats.outstanding=1; stats.chiHighWater=1;
            write=req->opcode==0x5a || req->opcode==0x5c;
            error=(req->opcode!=0x04 && !write) || req->tgtid!=node || !req->size ||
                  req->size>8 || req->addr>std::numeric_limits<Addr>::max()-(req->size-1) ||
                  !range.contains(req->addr) || !range.contains(req->addr+req->size-1);
            blocked=sent=haveData=dbidSent=false; ready=error;
            if(!error) { makePacket(); haveData=!write; }
            if(write) stats.writeBytes+=req->size; else stats.readBytes+=req->size;
            DPRINTF(DmaPath,"HNI_RECV seq=%llu src=%u txn=%u addr=%#llx bytes=%u write=%u\n",
                    sequence,req->srcid,req->txnid,req->addr,req->size,write);
        }
    }
    if(!req) return;
    if(write && !ready && !dbidSent) {
        RawRsp r{}; r.srcid=node; r.tgtid=req->srcid; r.txnid=req->txnid; r.opcode=0x06;
        if(!dbid) { nextDbid=(nextDbid%4095)+1; dbid=nextDbid; }
        r.dbid=dbid;
        if(chi.enqueueTx(RSP,r)) dbidSent=true;
        else ++stats.creditStalls;
    }
    if(write && dbidSent && !haveData) {
        if(auto f=chi.getRxFlit(DAT)) {
            const auto &d=std::get<RawDat>(*f);
            panic_if(d.srcid!=req->srcid || d.tgtid!=node || d.txnid!=req->txnid ||
                     d.dbid!=dbid || d.opcode!=0x03 || d.HomeNID!=node ||
                     !d.last || d.dataid || d.beatOffset || d.data.size()!=req->size ||
                     d.byteEnable.size()!=req->size,"HNI invalid exact write DAT");
            std::copy(d.data.begin(),d.data.end(),pkt->getPtr<uint8_t>());
            std::vector<bool> be(d.byteEnable.begin(),d.byteEnable.end());
            pkt->req->setByteEnable(be); haveData=true;
        }
    }
    if(haveData && !sent && !blocked && !ready) {
        if(pio.sendTimingReq(pkt)) {
            sent=true; ++stats.pioRequests;
            DPRINTF(DmaPath,"PIO_ACCEPT seq=%llu src=%u txn=%u\n",sequence,req->srcid,req->txnid);
        } else { blocked=true; ++stats.reqRetry; }
    }
    if(ready) {
        bool accepted=false;
        if(write) {
            RawRsp r{}; r.srcid=node; r.tgtid=req->srcid; r.txnid=req->txnid;
            r.opcode=0x04; r.dbid=dbid; r.respErr=error;
            accepted=chi.enqueueTx(RSP,r);
        } else {
            RawDat d{}; d.srcid=node; d.tgtid=req->srcid; d.HomeNID=node;
            d.txnid=req->txnid; d.opcode=0x04; d.last=1; d.respErr=error;
            d.data.assign(req->size,0);
            if(pkt && !error) std::copy(pkt->getConstPtr<uint8_t>(),
                                      pkt->getConstPtr<uint8_t>()+req->size,d.data.begin());
            d.byteEnable.assign(d.data.size(),1); d.chunkValid.assign((d.data.size()+7)/8,1);
            accepted=chi.enqueueTx(DAT,d);
        }
        if(accepted) {
            ++stats.responses; if(error) ++stats.errors;
            stats.latencyTicks+=curTick()-start; stats.occupancyTicks+=curTick()-start;
            delete pkt; pkt=nullptr; req.reset(); dbid=0;
            stats.entries=0; stats.outstanding=0;
        } else ++stats.creditStalls;
    }
    if(req || chi.hasRxFlit(REQ)) schedulePump();
    else if(drainState()==DrainState::Draining) signalDrainDone();
}
bool HniIoAdapter::idle() const
{ return !req && !pkt && !pumpEvent.scheduled() &&
         !chi.hasRxFlit(REQ) && !chi.hasRxFlit(DAT); }
DrainState HniIoAdapter::drain() { return idle()?DrainState::Drained:DrainState::Draining; }
void HniIoAdapter::serialize(CheckpointOut &) const
{ fatal("HNI checkpoint/restore is not implemented"); }
}

#include "mem/cache/CHI/DmaPathTester.hh"
#include "dev/simple_dma_regs.h"
#include "mem/packet_access.hh"
#include "sim/system.hh"
#include "sim/sim_exit.hh"
namespace gem5::Chi
{
DmaPathTester::DmaPathTester(const DmaPathTesterParams &p):ClockedObject(p),port(*this),
    requestor(p.system->getRequestorId(this)),ioTest(p.io_test),ioBurst(p.io_burst),
    pumpEvent([this]{pump();},name()+".pump"),
    retryEvent([this]{port.sendRetryResp();},name()+".responseRetry")
{}
Port &DmaPathTester::getPort(const std::string &s,PortID id)
{ if(s=="port")return port;return ClockedObject::getPort(s,id); }
void DmaPathTester::startup() { schedulePump(); }
void DmaPathTester::schedulePump()
{ if(!pumpEvent.scheduled())schedule(pumpEvent,nextCycle()); }
PacketPtr DmaPathTester::make(unsigned i)
{
    Addr a=SD_BUFFER_PA+i*256; unsigned n=256; bool wr=phase<2;
    if(phase==1) { a+=29; n=130; }
    if(phase==2 && (i&1)) n=64;
    if(ioTest) {
        // Serial directed MMIO sequence: legal halves, unsupported width,
        // out-of-register read, zero length START and status verification.
        const unsigned offs[]={SD_SCRATCH,SD_SCRATCH+4,SD_SCRATCH,SD_SCRATCH,
            0x2c,SD_LENGTH,SD_CONTROL,SD_STATUS,SD_BYTES_DONE,SD_CONTROL};
        a=SD_MMIO_BASE+offs[i]; n=(i==2 || i==5 || i==8)?8:i==3?1:4;
        wr=i==0 || i==1 || i==3 || i==5 || i==6 || i==9;
    }
    if(ioBurst) { a=SD_MMIO_BASE+SD_SCRATCH; n=8; wr=false; }
    auto r=std::make_shared<Request>(a,n,Request::UNCACHEABLE,requestor);
    auto *p=new Packet(r,wr?MemCmd::WriteReq:MemCmd::ReadReq); p->allocate();
    p->pushSenderState(new State(i));
    if(wr) {
        for(unsigned b=0;b<n;++b)p->getPtr<uint8_t>()[b]=phase==0?0xc9:(i*73+b*37)^0x5b;
        if(ioTest) {
            uint64_t val=i==0?0x01234567:i==1?0x89abcdef:(i==6 || i==9)?SD_START:0;
            for(unsigned b=0;b<n;++b)p->getPtr<uint8_t>()[b]=(val>>(b*8))&255;
        }
    }
    if(ioTest && i==9) r->setByteEnable(std::vector<bool>{false,true,true,true});
    if (!ioTest && phase==1) {
        std::vector<bool> be(n);
        for (unsigned b=0; b<n; ++b) be[b]=b%3!=1;
        r->setByteEnable(be);
    }
    return p;
}
void DmaPathTester::pump()
{
    const unsigned count=ioBurst?256:ioTest?10:16;
    while(issued<count && !blocked) {
        if(ioTest && issued!=completed)break;
        if(!pending)pending=make(issued);
        if(!port.sendTimingReq(pending)) { blocked=true; break; }
        pending=nullptr; ++issued;
    }
}
bool DmaPathTester::response(PacketPtr p)
{
    if(!rejected) {
        rejected=p; schedule(retryEvent,clockEdge(Cycles(50))); return false;
    }
    panic_if(rejected!=p,"response changed while blocked"); rejected=nullptr;
    const unsigned i=static_cast<State *>(p->senderState)->index;
    if(ioBurst) {
        panic_if(p->isError() || p->getLE<uint64_t>()!=0,"burst SCRATCH read mismatch");
    } else if(ioTest) {
        panic_if(p->isError()!=(i==3 || i==4 || i==9),"MMIO error mismatch index=%u",i);
        if(i==2)panic_if(p->getLE<uint64_t>()!=0x89abcdef01234567ull,"MMIO half access");
        if(i==7)panic_if(p->getLE<uint32_t>()!=SD_DONE,"zero START must complete");
        if(i==8)panic_if(p->getLE<uint64_t>()!=0,"zero bytes done");
    } else {
        panic_if(p->isError(),"unexpected memory error");
        if(completed && i<previous)++reordered;
        previous=i;
        if(phase==2)for(unsigned b=0;b<p->getSize();++b) {
            uint8_t expected=b>=29 && b<159 && (b-29)%3!=1 ? ((i*73+(b-29)*37)^0x5b)&255:0xc9;
            panic_if(p->getConstPtr<uint8_t>()[b]!=expected,
                     "directed payload/guard mismatch packet=%u byte=%u",i,b);
        }
    }
    delete p->popSenderState(); delete p; ++completed;
    if(completed==(ioBurst?256:ioTest?10:16)) {
        if(ioBurst || ioTest || phase==2) {
            inform("PASS directed %s reordered=%u",ioBurst?"HNI credits":ioTest?"MMIO":"RNI",reordered);
            exitSimLoop("DMA directed test passed",0); return true;
        }
        ++phase;issued=completed=0;previous=0;
    }
    schedulePump();return true;
}
}

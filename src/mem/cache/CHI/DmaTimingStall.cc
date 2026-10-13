#include "mem/cache/CHI/DmaTimingStall.hh"
namespace gem5::Chi
{
DmaTimingStall::DmaTimingStall(const DmaTimingStallParams &p): ClockedObject(p),up(*this),down(*this),
    delay(p.stall_cycles),rejectReq(p.reject_requests),rejectResp(p.reject_responses),
    reqEvent([this]{reqPermit=true;up.sendRetryReq();maybeDrain();},name()+".reqRetry"),
    respEvent([this]{respPermit=true;down.sendRetryResp();maybeDrain();},name()+".respRetry")
{
    reqRejections.name(name()+".reqRejections").desc("Injected request failures");
    respRejections.name(name()+".respRejections").desc("Injected response failures");
    fatal_if(delay==Cycles(0),"stall delay must be positive");
}
Port &DmaTimingStall::getPort(const std::string &s,PortID id)
{ if(s=="upstream")return up;if(s=="downstream")return down;return ClockedObject::getPort(s,id); }
void DmaTimingStall::init() { ClockedObject::init(); up.sendRangeChange(); }
bool DmaTimingStall::request(PacketPtr p)
{
    if(rejectReq && !reqPermit) {
        panic_if(reqEvent.scheduled(),"request repeated before retry");
        ++reqRejections; schedule(reqEvent,clockEdge(delay)); return false;
    }
    bool accepted=down.sendTimingReq(p);
    if(accepted) reqPermit=false;
    return accepted;
}
bool DmaTimingStall::response(PacketPtr p)
{
    if(rejectResp && !respPermit) {
        panic_if(respEvent.scheduled(),"response repeated before retry");
        ++respRejections; schedule(respEvent,clockEdge(delay)); return false;
    }
    bool accepted=up.sendTimingResp(p);
    if(accepted) respPermit=false;
    return accepted;
}
DrainState DmaTimingStall::drain()
{ return (reqEvent.scheduled() || respEvent.scheduled())?DrainState::Draining:DrainState::Drained; }
void DmaTimingStall::maybeDrain()
{
    if (!reqEvent.scheduled() && !respEvent.scheduled() &&
        drainState()==DrainState::Draining) signalDrainDone();
}
}

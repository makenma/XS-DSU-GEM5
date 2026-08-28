#ifndef __HOMENODEFULL__CC__
#define __HOMENODEFULL__CC__

#include "mem/cache/CHI/HomeNodeFull.hh"

#include "debug/HomeLinkLayer.hh"

namespace gem5::Chi
{

HomeNodeFull::HomeNodeFull(const HomeNodeFullParams& p)
    : BasicChiComponent(p),
    Consumer(this),
    slcsf(p.slcsf),
    cc(p.block_size, p.data_beat_bytes, p.num_poc_entries, p.sn_node_id,
       p.sn_node_ids, p.direct_sn_fake_data, p.rnf_slices, p.enable_retry),
    linklayer(this, p.block_size, p.data_beat_bytes, p.num_poc_entries,
              p.enable_retry),
    rxport(p.name + ".rxport", static_cast<ruby::Consumer*>(this),/*PortID*/ 0)
{
    panic_if(!slcsf, "HomeNodeFull requires an SlcSnoopFilter child\n");
    DPRINTF(HomeLinkLayer,
            "HomeNodeFull constructed block=%u beat=%u entries=%u "
            "retry=%u\n",
            p.block_size, p.data_beat_bytes, p.num_poc_entries,
            p.enable_retry);
    linklayer.setRxPort(&rxport);
    cc.setSlcsf(&slcsf->service());
    linklayer.setCc(&cc);
    slcsf->setFutureWakeupCallback([this](Tick earliest) {
        ruby::Consumer::scheduleEventAbsolute(earliest);
    });
}

void
HomeNodeFull::wakeup()
{
    DPRINTF(HomeLinkLayer, "HomeNodeFull wakeup\n");
    linklayer.wakeup();
    advanceDrain();
}

void
HomeNodeFull::advanceDrain()
{
    if (!d1Active) {
        return;
    }

    // Drain is a global fixed-point operation.  Do not quiesce RX admission:
    // an O3 request may still be traversing an RN-F/router when this HN-F is
    // visited during an earlier drain pass.  Rejecting it would leave the CPU
    // permanently waiting for a response.  A later global pass rechecks this
    // node after every producer has drained.
    const bool ready = !hasLinkWork() && slcsf->completelyIdle();
    d1Complete = ready;
    d2Active = ready;
    if (ready) {
        slcsf->testDrainComplete();
        if (drainState() == DrainState::Draining) {
            signalDrainDone();
        }
    }
}

DrainState
HomeNodeFull::drain()
{
    d1Active = true;
    // Admission must remain enabled until the global drain fixed point.
    linklayer.resumeNewRequests();
    slcsf->requestDrain();
    const bool ready = !hasLinkWork() && slcsf->completelyIdle();
    d1Complete = ready;
    d2Active = ready;
    if (linklayer.hasWork()) {
        ruby::Consumer::scheduleEvent(Cycles(1));
    }
    return ready ? DrainState::Drained : DrainState::Draining;
}

void
HomeNodeFull::drainResume()
{
    d1Active = false;
    d1Complete = false;
    d2Active = false;
    linklayer.resumeNewRequests();
    // SlcSnoopFilter is an independently registered Drainable. DrainManager
    // resumes it exactly once; directly calling its override here would resume
    // the child once with DrainState::Drained and then a second time normally.
    if (linklayer.hasWork()) {
        ruby::Consumer::scheduleEvent(Cycles(1));
    }
}

void
HomeNodeFull::loadState(CheckpointIn& cp)
{
    fatal_if(!cp.sectionExists(name()),
             "%s checkpoint is missing its required object section\n",
             name());
    fatal_if(!cp.sectionExists(name() + ".slcsfRequester"),
             "%s checkpoint is missing the SLCSF requester identity section\n",
             name());
    BasicChiComponent::loadState(cp);
}

void
HomeNodeFull::serialize(CheckpointOut& cp) const
{
    panic_if(!d1Active || !d1Complete || !d2Active || hasLinkWork() ||
                 !slcsf->drainRequested() || !slcsf->completelyIdle(),
             "%s checkpoint requires global parent/SLCSF drain fixed point\n",
             name());
    BasicChiComponent::serialize(cp);
    Serializable::ScopedCheckpointSection section(cp, "slcsfRequester");
    cc.serializeSlcsfIdentityState(cp);
}

void
HomeNodeFull::unserialize(CheckpointIn& cp)
{
    BasicChiComponent::unserialize(cp);
    Serializable::ScopedCheckpointSection section(cp, "slcsfRequester");
    cc.unserializeSlcsfIdentityState(cp);
    d1Active = false;
    d1Complete = false;
    d2Active = false;
    linklayer.resumeNewRequests();
}

void
HomeNodeFull::print(std::ostream& out) const
{
    out << "HomeNodeFull(" << name() << ")";
}

Port&
HomeNodeFull::getPort(const std::string& if_name, PortID idx)
{
    if (if_name == "rxport") {
        DPRINTF(HomeLinkLayer, "getPort(rxport)\n");
        return rxport;
    }
    return BasicChiComponent::getPort(if_name, idx);
}

bool
HomeNodeFull::hasLinkWork() const
{
    return linklayer.hasProtocolOwnershipForDrain() || cc.hasWork();
}




}

// namespace gem5{
// Chi::HomeNodeFull*
// HomeNodeFullParams::create() const
// {
//     return new Chi::HomeNodeFull(*this);
// }
// }

#endif

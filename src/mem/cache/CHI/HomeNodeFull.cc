#ifndef __HOMENODEFULL__CC__
#define __HOMENODEFULL__CC__

#include "mem/cache/CHI/HomeNodeFull.hh"

#include "debug/HomeLinkLayer.hh"

namespace gem5::Chi
{

HomeNodeFull::HomeNodeFull(const HomeNodeFullParams& p)
    : BasicChiComponent(p),
    Consumer(this),
    slcsf(p.block_size, p.slc_num_sets, p.slc_num_ways, p.sf_num_sets,
          p.sf_num_ways, p.seq_entries,
          makeEmbeddedSlcsfConfig(p, clockPeriod())),
    cc(p.block_size, p.data_beat_bytes, p.num_poc_entries, p.sn_node_id,
       p.direct_sn_fake_data, p.rnf_slices),
    linklayer(this, p.block_size, p.data_beat_bytes, p.num_poc_entries,
              p.enable_retry),
    rxport(p.name + ".rxport", static_cast<ruby::Consumer*>(this),/*PortID*/ 0)
{
    DPRINTF(HomeLinkLayer,
            "HomeNodeFull constructed block=%u beat=%u entries=%u "
            "retry=%u\n",
            p.block_size, p.data_beat_bytes, p.num_poc_entries,
            p.enable_retry);
    linklayer.setRxPort(&rxport);
    cc.setSlcsf(&slcsf);
    linklayer.setCc(&cc);
}

void
HomeNodeFull::wakeup()
{
    DPRINTF(HomeLinkLayer, "HomeNodeFull wakeup\n");
    // Stage A temporarily drives the embedded service from the HomeNode edge.
    // The standalone SLCSF migration removes this direct call.
    const bool needsNextEdge = advanceEmbeddedSlcsfStageA(
        slcsf, curTick(), [this] { linklayer.wakeup(); },
        [this] { return linklayer.hasWork(); },
        [this] { return cc.hasWork(); });
    if (needsNextEdge) {
        scheduleEvent(Cycles(1));
    }
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
    // Link-layer work includes allocated and issue-pending CC entries. SLCSF
    // work is checked separately so it progresses without a new RX flit.
    return slcsf.hasWork() || linklayer.hasWork() || cc.hasWork();
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

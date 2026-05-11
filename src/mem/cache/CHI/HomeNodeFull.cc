#ifndef __HOMENODEFULL__CC__
#define __HOMENODEFULL__CC__

#include "mem/cache/CHI/HomeNodeFull.hh"

#include "debug/HomeLinkLayer.hh"

namespace gem5::Chi
{


HomeNodeFull::HomeNodeFull(const HomeNodeFullParams& p)
    : BasicChiComponent(p),
    Consumer(this),
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
}

void
HomeNodeFull::wakeup()
{
    DPRINTF(HomeLinkLayer, "HomeNodeFull wakeup\n");
    linklayer.wakeup();
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
    return linklayer.hasWork();
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

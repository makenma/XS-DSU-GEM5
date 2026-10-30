#ifndef __HOMENODEFULL__CC__
#define __HOMENODEFULL__CC__

#include "mem/cache/CHI/HomeNodeFull.hh"

#include <iostream>
#include <algorithm>
#include "sim/serialize.hh"

#include "debug/HomeLinkLayer.hh"

namespace gem5::Chi
{


static std::array<int, 4>
buildThresholds(const HomeNodeFullParams& p)
{
    std::array<int, 4> th = {};
    for (std::size_t i = 0; i < p.qos_thresholds.size() && i < 4; ++i)
        th[i] = p.qos_thresholds[i];
    return th;
}

HomeNodeFull::HomeNodeFull(const HomeNodeFullParams& p)
    : BasicChiComponent(p),
    Consumer(this),
    slcsf(p.slcsf),
    snId(p.sn_node_id),
    rnfNodeIds(p.rnf_node_ids.begin(), p.rnf_node_ids.end()),
    pocq(this, p.pocq_entry_num),
    linklayer(this, &pocq, buildThresholds(p),
              p.rnf_num, p.retry_fifo_size),
    rxport(p.name + ".rxport", static_cast<ruby::Consumer*>(this),/*PortID*/ 0),
    drainEvent([this] { pollDrain(); }, p.name + ".drain")
{
    panic_if(!slcsf, "HomeNodeFull requires an SlcSf child");
    if (rnfNodeIds.empty()) {
        for (int i = 0; i < p.rnf_num; ++i) rnfNodeIds.push_back(i);
    }
    fatal_if(rnfNodeIds.size() != static_cast<std::size_t>(p.rnf_num) ||
             rnfNodeIds.size() != slcsf->model().config().rnfCount,
             "HNF RNF node map must match rnf_num and SF width");
    auto sorted = rnfNodeIds;
    std::sort(sorted.begin(), sorted.end());
    fatal_if(std::adjacent_find(sorted.begin(), sorted.end()) != sorted.end(),
             "HNF RNF node IDs must be unique");
    fatal_if(slcsf->params().clk_domain != p.clk_domain ||
             slcsf->clockPeriod() != clockPeriod(),
             "HNF and SLCSF require the same clock domain and period");
    slcsf->setWakeup([this] { pocq.scheduleEvent(Cycles(1)); });
    slcsf->setVictimConflictQuery([this](const SlcSfRequest &request,
                                       uint64_t cycle) {
        return pocq.victimConflict(request, cycle);
    });

    linklayer.setRxPort(&rxport);
}

uint32_t HomeNodeFull::rnfNodeId(uint32_t index) const
{
    panic_if(index >= rnfNodeIds.size(), "Invalid SF RNF index %u", index);
    return rnfNodeIds[index];
}

uint32_t HomeNodeFull::rnfIndex(uint32_t node) const
{
    const auto found = std::find(rnfNodeIds.begin(), rnfNodeIds.end(), node);
    panic_if(found == rnfNodeIds.end(), "Unknown requesting RNF node %u", node);
    return found - rnfNodeIds.begin();
}

bool HomeNodeFull::checkpointReady()
{
    return pocq.idle() && linklayer.idle() && rxport.drained() &&
           slcsf->checkpointReady();
}

DrainState HomeNodeFull::drain()
{
    if (checkpointReady()) return DrainState::Drained;
    if (!drainEvent.scheduled()) schedule(drainEvent, clockEdge(Cycles(1)));
    return DrainState::Draining;
}

void HomeNodeFull::pollDrain()
{
    if (checkpointReady()) signalDrainDone();
    else schedule(drainEvent, clockEdge(Cycles(1)));
}

void HomeNodeFull::serialize(CheckpointOut &cp) const
{
    panic_if(!pocq.idle() || !linklayer.idle() || !rxport.drained() ||
             !slcsf->model().drained(), "HNF checkpoint requires drained state");
    BasicChiComponent::serialize(cp);
    SERIALIZE_CONTAINER(rnfNodeIds);
    pocq.serialize(cp);
}

void HomeNodeFull::unserialize(CheckpointIn &cp)
{
    BasicChiComponent::unserialize(cp);
    auto configured = rnfNodeIds;
    UNSERIALIZE_CONTAINER(rnfNodeIds);
    fatal_if(configured != rnfNodeIds, "HNF checkpoint RNF node map changed");
    pocq.unserialize(cp);
}

void
HomeNodeFull::wakeup()
{
    // Port arrivals become visible at the next HNF edge, without recursion.
    linklayer.scheduleEvent(Cycles(1));




}

void
HomeNodeFull::releasePocqQos(PoolPriority priority)
{
    linklayer.releaseQos(priority);
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
        return rxport;
    }
    return BasicChiComponent::getPort(if_name, idx);
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

#include "mem/ruby/network/simple/AggregateNetwork.hh"

#include "mem/ruby/network/BasicLink.hh"
#include "mem/ruby/network/MessageBuffer.hh"
#include "params/GarnetNetwork.hh"
#include <climits>

namespace gem5::ruby
{
AggregateNetwork::AggregateNetwork(const GarnetNetworkParams &p,
                                 const std::vector<bool> &ordered)
    : config{p.ruby_system, int(p.ni_flit_size * 1000), true, {}, {}, ordered}
{
    fatal_if(!p.aggregate_queue_entries || p.ni_flit_size > INT_MAX / 1000 ||
             p.aggregate_routers.size() != p.routers.size() ||
             p.aggregate_link_buffers.size() != p.int_links.size() * ordered.size(),
             "aggregate fabric requires bounded queues and complete topology coverage");
    for (auto *router : p.aggregate_routers) {
        fatal_if(!routers.emplace(router->params().router_id, router).second,
                 "duplicate aggregate router identity");
        router->init_net_ptr(&config);
        for (const auto *queue : router->params().port_buffers)
            fatal_if(queue->capacity() != p.aggregate_queue_entries,
                     "aggregate router queue capacity mismatch");
    }
    for (const auto *router : p.routers)
        fatal_if(!routers.count(router->params().router_id),
                 "aggregate topology router identity mismatch");
    size_t cursor = 0;
    for (const auto *link : p.int_links) {
        auto &queues = links[link->params().link_id];
        fatal_if(!queues.empty(), "duplicate aggregate link identity");
        for (size_t vnet = 0; vnet < ordered.size(); ++vnet) {
            auto *queue = p.aggregate_link_buffers.at(cursor++);
            fatal_if(queue->capacity() != p.aggregate_queue_entries,
                     "aggregate link queue capacity mismatch");
            queues.push_back(queue);
        }
    }
}

void AggregateNetwork::ingress(SwitchID router, const std::vector<MessageBuffer *> &queues)
{
    auto *consumer = routers.at(router)->addInPort(queues, false);
    for (auto *queue : queues)
        if (queue) {
            fatal_if(!queue->capacity() || !queue->getConsumer(),
                     "aggregate ingress requires an owned finite queue");
            ingresses.push_back({queue, queue->getConsumer(), consumer});
            endpoint_queues.push_back(queue);
        }
}

void AggregateNetwork::egress(SwitchID router, std::vector<MessageBuffer *> &queues,
                              const NetDest &route, BasicLink *link)
{
    queues.resize(config.ordered.size(), nullptr);
    for (auto *queue : queues)
        if (queue) {
            fatal_if(!queue->capacity(), "aggregate egress requires a finite queue");
            endpoint_queues.push_back(queue);
        }
    egresses.push_back(&routers.at(router)->addOutPort(queues, route,
        link->m_latency, 0, 1, true));
}

void AggregateNetwork::internal(SwitchID source, SwitchID destination, BasicLink *link,
                                const NetDest &route, const PortDirection &direction)
{
    auto &queues = links.at(link->params().link_id);
    routers.at(destination)->addInPort(queues);
    routers.at(source)->addOutPort(queues, route, link->m_latency,
                                   link->m_weight, 1, false, direction);
}

void AggregateNetwork::activate(bool aggregate)
{
    fatal_if(!snapshot().empty(), "aggregate handoff requires a drained fabric");
    if (aggregate == aggregate_active)
        return;
    for (const auto &entry : ingresses)
        fatal_if(entry.queue->getConsumer() != (aggregate_active ? entry.aggregate : entry.exact),
                 "aggregate ingress owner mismatch");
    for (const auto &entry : ingresses)
        entry.queue->handoffConsumer(aggregate_active ? entry.aggregate : entry.exact,
                                     aggregate ? entry.aggregate : entry.exact);
    aggregate_active = aggregate;
}

SimpleNetworkSnapshot AggregateNetwork::snapshot() const
{
    SimpleNetworkSnapshot result;
    for (const auto *queue : endpoint_queues)
        result.queuedMessages += queue->getNumMessages() + queue->getNumStalledMessages();
    for (const auto &[id, queues] : links)
        for (const auto *queue : queues)
            result.queuedMessages += queue->getNumMessages() + queue->getNumStalledMessages();
    for (const auto &[id, router] : routers)
        result += router->quiescenceSnapshot();
    return result;
}

SimpleNetworkSnapshot AggregateNetwork::endpointSnapshot() const
{
    SimpleNetworkSnapshot result;
    for (const auto *egress : egresses)
        result += egress->quiescenceSnapshot();
    return result;
}

bool AggregateNetwork::functionalRead(Packet *packet, WriteMask &mask)
{
    bool found = false;
    for (auto *queue : endpoint_queues)
        found |= queue->functionalRead(packet, mask);
    for (const auto &[id, queues] : links)
        for (auto *queue : queues)
            found |= queue->functionalRead(packet, mask);
    for (const auto &[id, router] : routers)
        found |= router->functionalRead(packet, mask);
    return found;
}

uint32_t AggregateNetwork::functionalWrite(Packet *packet)
{
    uint32_t written = 0;
    for (auto *queue : endpoint_queues)
        written += queue->functionalWrite(packet);
    for (const auto &[id, queues] : links)
        for (auto *queue : queues)
            written += queue->functionalWrite(packet);
    for (const auto &[id, router] : routers)
        written += router->functionalWrite(packet);
    return written;
}
}

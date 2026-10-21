#ifndef MEM_RUBY_NETWORK_SIMPLE_AGGREGATE_NETWORK_HH
#define MEM_RUBY_NETWORK_SIMPLE_AGGREGATE_NETWORK_HH

#include <map>
#include <vector>

#include "mem/ruby/network/simple/NetworkSnapshot.hh"
#include "mem/ruby/network/simple/SimpleNetworkConfig.hh"
#include "mem/ruby/network/simple/Switch.hh"

namespace gem5
{
struct GarnetNetworkParams;
namespace ruby
{
class BasicLink;

class AggregateNetwork
{
  public:
    AggregateNetwork(const GarnetNetworkParams &params, const std::vector<bool> &ordered);
    void ingress(SwitchID router, const std::vector<MessageBuffer *> &queues);
    void egress(SwitchID router, std::vector<MessageBuffer *> &queues,
                const NetDest &route, BasicLink *link);
    void internal(SwitchID source, SwitchID destination, BasicLink *link,
                  const NetDest &route, const PortDirection &direction);
    void activate(bool aggregate);
    bool active() const { return aggregate_active; }
    SimpleNetworkSnapshot snapshot() const;
    SimpleNetworkSnapshot endpointSnapshot() const;
    bool functionalRead(Packet *packet, WriteMask &mask);
    uint32_t functionalWrite(Packet *packet);

  private:
    SimpleNetworkConfig config;
    std::map<SwitchID, Switch *> routers;
    std::map<int, std::vector<MessageBuffer *>> links;
    struct Ingress
    {
        MessageBuffer *queue;
        Consumer *exact;
        Consumer *aggregate;
    };
    std::vector<Ingress> ingresses;
    std::vector<MessageBuffer *> endpoint_queues;
    std::vector<const Throttle *> egresses;
    bool aggregate_active = false;
};
}
}
#endif

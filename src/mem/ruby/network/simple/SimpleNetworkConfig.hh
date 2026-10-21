#ifndef MEM_RUBY_NETWORK_SIMPLE_NETWORK_CONFIG_HH
#define MEM_RUBY_NETWORK_SIMPLE_NETWORK_CONFIG_HH

#include <vector>

namespace gem5::ruby
{
class RubySystem;

struct SimpleNetworkConfig
{
    RubySystem *rubySystem;
    int endpointBandwidth;
    bool finiteBuffers;
    std::vector<int> physicalVnetChannels;
    std::vector<int> physicalVnetBandwidth;
    std::vector<bool> ordered;

    bool isVNetOrdered(int vnet) const { return ordered.at(vnet); }
};
}
#endif

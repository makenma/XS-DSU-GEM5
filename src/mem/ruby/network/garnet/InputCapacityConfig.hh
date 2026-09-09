#ifndef __MEM_RUBY_NETWORK_GARNET_INPUT_CAPACITY_CONFIG_HH__
#define __MEM_RUBY_NETWORK_GARNET_INPUT_CAPACITY_CONFIG_HH__

#include <cstdint>
#include <map>
#include <string>
#include <tuple>
#include <vector>

namespace gem5::ruby::garnet
{

struct RouterInputCapacityKey
{
    uint32_t router;
    uint32_t inport;
    uint32_t vnet;

    bool operator<(const RouterInputCapacityKey &other) const
    {
        return std::tie(router, inport, vnet) <
               std::tie(other.router, other.inport, other.vnet);
    }
};

class InputCapacityConfig
{
  public:
    std::string configure(const std::vector<uint32_t> &fallback,
                          const std::vector<uint32_t> &niDepths,
                          const std::vector<std::string> &overrides,
                          uint32_t routers, bool faultModelEnabled);
    std::vector<uint32_t> routerDepths(uint32_t router,
                                      uint32_t inport) const;
    const std::vector<uint32_t> &niDepths() const { return ni_depths; }
    bool extended() const { return extended_mode; }
    std::string validatePorts(const std::vector<uint32_t> &inports) const;

  private:
    std::vector<uint32_t> default_depths;
    std::vector<uint32_t> ni_depths;
    std::map<RouterInputCapacityKey, uint32_t> entries;
    bool extended_mode = false;
    uint32_t router_count = 0;
};

}

#endif

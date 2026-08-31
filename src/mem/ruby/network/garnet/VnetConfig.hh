#ifndef __MEM_RUBY_NETWORK_GARNET_VNET_CONFIG_HH__
#define __MEM_RUBY_NETWORK_GARNET_VNET_CONFIG_HH__

#include <cstdint>
#include <string>
#include <vector>

#include "mem/ruby/network/garnet/CommonTypes.hh"

namespace gem5
{
namespace ruby
{
namespace garnet
{

/** Inputs needed to normalize Garnet's legacy and per-vnet parameters. */
struct VnetConfigInput
{
    uint32_t virtualNetworks = 0;
    uint32_t vcsPerVnet = 0;
    uint32_t niFlitSize = 0;
    uint32_t legacyCtrlDepth = 0;
    uint32_t legacyDataDepth = 0;
    std::vector<std::string> legacyTypeNames;
    std::vector<std::string> configuredClasses;
    std::vector<uint32_t> configuredDepths;
    bool faultModelEnabled = false;
};

/** Canonical arrays consumed by every Garnet capacity/credit path. */
struct NormalizedVnetConfig
{
    std::vector<VNET_type> types;
    std::vector<uint32_t> depths;
    uint32_t legacyCtrlDepth = 0;
    uint32_t legacyDataDepth = 0;
};

/**
 * Normalize a configuration or return a stable diagnostic.
 *
 * Keeping validation independent of SimObjects makes the production path
 * and the lightweight GTest exercise exactly the same precedence rules.
 */
std::string normalizeVnetConfig(const VnetConfigInput &input,
                                NormalizedVnetConfig &output);

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_NETWORK_GARNET_VNET_CONFIG_HH__

#include "mem/ruby/network/garnet/InputCapacityConfig.hh"

#include <array>
#include <charconv>
#include <limits>
#include <stdexcept>

namespace gem5::ruby::garnet
{

std::string
InputCapacityConfig::configure(const std::vector<uint32_t> &fallback,
                               const std::vector<uint32_t> &niDepths,
                               const std::vector<std::string> &overrides,
                               uint32_t routers, bool faultModelEnabled)
{
    *this = {};
    if (fallback.empty() || routers == 0)
        return "input capacity requires routers and virtual networks";
    for (auto depth : fallback) {
        if (depth == 0 || depth > std::numeric_limits<int>::max())
            return "input VC fallback depth must fit positive int";
    }
    if (!niDepths.empty() && niDepths.size() != fallback.size())
        return "ni_buffers_per_vnet length must equal virtual networks";
    for (auto depth : niDepths) {
        if (depth == 0 || depth > std::numeric_limits<int>::max())
            return "ni_buffers_per_vnet depth must fit positive int";
    }
    if (faultModelEnabled && (!overrides.empty() || !niDepths.empty()))
        return "FaultModel does not support receiver capacity overrides";
    default_depths = fallback;
    ni_depths = niDepths.empty() ? fallback : niDepths;
    router_count = routers;
    extended_mode = !overrides.empty() || !niDepths.empty();
    for (const auto &text : overrides) {
        std::array<uint32_t, 4> fields{};
        size_t begin = 0;
        for (unsigned field = 0; field < fields.size(); ++field) {
            const size_t separator = text.find(':', begin);
            const size_t end = separator == std::string::npos ?
                text.size() : separator;
            if ((field + 1 == fields.size()) !=
                    (separator == std::string::npos) || begin == end)
                return "invalid router_input_vc_depths entry: " + text;
            const auto parsed = std::from_chars(
                text.data() + begin, text.data() + end, fields[field]);
            if (parsed.ec != std::errc{} || parsed.ptr != text.data() + end)
                return "invalid router_input_vc_depths field: " + text;
            begin = end + 1;
        }
        if (fields[0] >= routers)
            return "router_input_vc_depths router does not exist: " + text;
        if (fields[1] > std::numeric_limits<int>::max())
            return "router_input_vc_depths inport must fit nonnegative int";
        if (fields[2] >= fallback.size())
            return "router_input_vc_depths vnet does not exist: " + text;
        if (fields[3] == 0 || fields[3] > std::numeric_limits<int>::max())
            return "router_input_vc_depths depth must fit positive int";
        if (!entries.emplace(RouterInputCapacityKey{
                fields[0], fields[1], fields[2]}, fields[3]).second)
            return "duplicate router_input_vc_depths coordinate: " + text;
    }
    return {};
}

std::vector<uint32_t>
InputCapacityConfig::routerDepths(uint32_t router, uint32_t inport) const
{
    if (router >= router_count)
        throw std::out_of_range("router input capacity router out of range");
    auto resolved = default_depths;
    for (uint32_t vnet = 0; vnet < resolved.size(); ++vnet) {
        const auto found = entries.find({router, inport, vnet});
        if (found != entries.end())
            resolved[vnet] = found->second;
    }
    return resolved;
}

std::string
InputCapacityConfig::validatePorts(const std::vector<uint32_t> &inports) const
{
    if (inports.size() != router_count)
        return "router input capacity topology router count mismatch";
    for (const auto &[key, depth] : entries) {
        if (key.inport >= inports[key.router])
            return "router_input_vc_depths inport does not exist: " +
                   std::to_string(key.router) + ":" +
                   std::to_string(key.inport);
    }
    return {};
}

}

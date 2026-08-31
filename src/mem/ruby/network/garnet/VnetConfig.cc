#include "mem/ruby/network/garnet/VnetConfig.hh"

#include <limits>
#include <sstream>

namespace gem5
{
namespace ruby
{
namespace garnet
{

namespace
{

std::string
sizeError(const char *name, size_t actual, uint32_t expected)
{
    std::ostringstream out;
    out << name << " length " << actual
        << " must equal number_of_virtual_networks " << expected;
    return out.str();
}

} // anonymous namespace

std::string
normalizeVnetConfig(const VnetConfigInput &input,
                    NormalizedVnetConfig &output)
{
    output = {};

    if (input.virtualNetworks == 0)
        return "number_of_virtual_networks must be >= 1";
    if (input.vcsPerVnet == 0)
        return "vcs_per_vnet must be >= 1";
    if (input.niFlitSize == 0)
        return "ni_flit_size must be >= 1";

    if (!input.configuredDepths.empty() &&
        input.configuredDepths.size() != input.virtualNetworks) {
        return sizeError("buffers_per_vnet", input.configuredDepths.size(),
                         input.virtualNetworks);
    }
    if (input.legacyCtrlDepth == 0)
        return "buffers_per_ctrl_vc must be >= 1";
    if (input.legacyDataDepth == 0)
        return "buffers_per_data_vc must be >= 1";
    if (input.legacyCtrlDepth > std::numeric_limits<int>::max())
        return "buffers_per_ctrl_vc must fit positive int";
    if (input.legacyDataDepth > std::numeric_limits<int>::max())
        return "buffers_per_data_vc must fit positive int";
    for (size_t i = 0; i < input.configuredDepths.size(); ++i) {
        if (input.configuredDepths[i] == 0) {
            std::ostringstream out;
            out << "buffers_per_vnet[" << i << "] must be >= 1";
            return out.str();
        }
        if (input.configuredDepths[i] >
            std::numeric_limits<int>::max()) {
            std::ostringstream out;
            out << "buffers_per_vnet[" << i
                << "] must fit positive int";
            return out.str();
        }
    }

    if (input.virtualNetworks >
        std::numeric_limits<uint32_t>::max() / input.vcsPerVnet ||
        static_cast<uint64_t>(input.virtualNetworks) * input.vcsPerVnet >
        static_cast<uint64_t>(std::numeric_limits<int>::max())) {
        return "number_of_virtual_networks * vcs_per_vnet overflows int";
    }

    if (!input.configuredClasses.empty() &&
        input.configuredClasses.size() != input.virtualNetworks) {
        return sizeError("vnet_classes", input.configuredClasses.size(),
                         input.virtualNetworks);
    }
    if (input.configuredClasses.empty() &&
        input.legacyTypeNames.size() != input.virtualNetworks) {
        return sizeError("vnet_type_names", input.legacyTypeNames.size(),
                         input.virtualNetworks);
    }

    output.types.reserve(input.virtualNetworks);
    output.depths.reserve(input.virtualNetworks);
    output.legacyCtrlDepth = input.legacyCtrlDepth;
    output.legacyDataDepth = input.legacyDataDepth;

    bool haveCtrl = false;
    bool haveData = false;
    bool ctrlMismatch = false;
    bool dataMismatch = false;
    uint32_t firstCtrlDepth = 0;
    uint32_t firstDataDepth = 0;

    for (uint32_t i = 0; i < input.virtualNetworks; ++i) {
        VNET_type type;
        if (input.configuredClasses.empty()) {
            type = input.legacyTypeNames[i] == "response" ?
                DATA_VNET_ : CTRL_VNET_;
        } else if (input.configuredClasses[i] == "ctrl") {
            type = CTRL_VNET_;
        } else if (input.configuredClasses[i] == "data") {
            type = DATA_VNET_;
        } else {
            std::ostringstream out;
            out << "vnet_classes[" << i << "]='"
                << input.configuredClasses[i]
                << "' is invalid; expected 'ctrl' or 'data'";
            return out.str();
        }

        const uint32_t depth = input.configuredDepths.empty() ?
            (type == DATA_VNET_ ? input.legacyDataDepth :
                                  input.legacyCtrlDepth) :
            input.configuredDepths[i];
        output.types.push_back(type);
        output.depths.push_back(depth);

        if (type == CTRL_VNET_) {
            if (!haveCtrl) {
                haveCtrl = true;
                firstCtrlDepth = depth;
            } else {
                ctrlMismatch |= firstCtrlDepth != depth;
            }
        } else {
            if (!haveData) {
                haveData = true;
                firstDataDepth = depth;
            } else {
                dataMismatch |= firstDataDepth != depth;
            }
        }
    }

    if (input.faultModelEnabled && ctrlMismatch) {
        return "FaultModel requires one uniform ctrl vnet depth";
    }
    if (input.faultModelEnabled && dataMismatch) {
        return "FaultModel requires one uniform data vnet depth";
    }

    // Compatibility getters expose the vector value only when that class is
    // representable by the stock one-depth-per-class FaultModel interface.
    if (haveCtrl && !ctrlMismatch)
        output.legacyCtrlDepth = firstCtrlDepth;
    if (haveData && !dataMismatch)
        output.legacyDataDepth = firstDataDepth;

    return {};
}

} // namespace garnet
} // namespace ruby
} // namespace gem5

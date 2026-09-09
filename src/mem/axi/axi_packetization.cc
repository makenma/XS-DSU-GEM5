#include "mem/axi/axi_packetization.hh"

#include <limits>
#include <sstream>

namespace gem5
{
namespace axi
{

std::string
normalizeAxiWireBytes(const std::vector<uint32_t> &header_bytes,
                      uint32_t data_bus_bytes, AxiWireBytes &output,
                      bool data_header_sideband)
{
    output.fill(0);

    if (header_bytes.size() != AxiWireSlotCount) {
        std::ostringstream out;
        out << "axi_wire_header_bytes length " << header_bytes.size()
            << " must equal " << AxiWireSlotCount;
        return out.str();
    }
    if (data_bus_bytes == 0)
        return "axi_data_bus_bytes must be >= 1";

    for (size_t i = 0; i < header_bytes.size(); ++i) {
        if (header_bytes[i] == 0) {
            std::ostringstream out;
            out << "axi_wire_header_bytes[" << i << "] must be >= 1";
            return out.str();
        }

        uint64_t bytes = header_bytes[i];
        if (i == static_cast<size_t>(AxiWireSlot::W) ||
            i == static_cast<size_t>(AxiWireSlot::R)) {
            bytes = data_bus_bytes + (data_header_sideband ? 0 : bytes);
        }
        if (bytes > static_cast<uint64_t>(
                std::numeric_limits<int>::max())) {
            std::ostringstream out;
            out << "AXI wire bytes for channel index " << i
                << " must fit positive int";
            return out.str();
        }
        output[i] = static_cast<int>(bytes);
    }

    return {};
}

} // namespace axi
} // namespace gem5

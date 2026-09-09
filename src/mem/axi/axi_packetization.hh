#ifndef __MEM_AXI_AXI_PACKETIZATION_HH__
#define __MEM_AXI_AXI_PACKETIZATION_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace gem5
{
namespace axi
{

enum class AxiWireSlot : size_t
{
    Aw = 0,
    W,
    B,
    Ar,
    R,
    Count
};

constexpr size_t AxiWireSlotCount =
    static_cast<size_t>(AxiWireSlot::Count);
using AxiWireBytes = std::array<int, AxiWireSlotCount>;

std::string normalizeAxiWireBytes(const std::vector<uint32_t> &header_bytes,
                                  uint32_t data_bus_bytes,
                                  AxiWireBytes &output,
                                  bool data_header_sideband = false);

inline int
wireBytesFor(const AxiWireBytes &wire_bytes, AxiWireSlot slot)
{
    return wire_bytes[static_cast<size_t>(slot)];
}

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_PACKETIZATION_HH__

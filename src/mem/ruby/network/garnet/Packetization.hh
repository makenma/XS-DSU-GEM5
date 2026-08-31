#ifndef __MEM_RUBY_NETWORK_GARNET_PACKETIZATION_HH__
#define __MEM_RUBY_NETWORK_GARNET_PACKETIZATION_HH__

#include <cstdint>
#include <limits>
#include <string>

namespace gem5
{
namespace ruby
{
namespace garnet
{

struct MessagePacketization
{
    int wireBytes = 0;
    int numFlits = 0;
};

/**
 * Resolve a message's optional dynamic wire size and compute its flit count.
 *
 * A requested size of zero selects the legacy MessageSizeType byte count.
 * The returned values fit the existing int fields used by flit and bridge
 * bookkeeping.  An empty string denotes success; errors are stable enough
 * for construction/runtime diagnostics and lightweight tests.
 */
inline std::string
resolveMessagePacketization(int requested_wire_bytes,
                            uint32_t legacy_wire_bytes,
                            uint32_t flit_bytes,
                            MessagePacketization &output)
{
    output = {};

    if (requested_wire_bytes < 0)
        return "wireBytes must be >= 0 (zero selects legacy fallback)";
    if (flit_bytes == 0)
        return "flitBytes must be >= 1";

    const uint64_t wire_bytes = requested_wire_bytes == 0 ?
        legacy_wire_bytes : static_cast<uint64_t>(requested_wire_bytes);
    if (wire_bytes == 0)
        return "wireBytes must be >= 1 after legacy fallback";
    if (wire_bytes > static_cast<uint64_t>(
            std::numeric_limits<int>::max())) {
        return "wireBytes must fit positive int";
    }

    const uint64_t num_flits = wire_bytes / flit_bytes +
        (wire_bytes % flit_bytes != 0);
    if (num_flits == 0 || num_flits > static_cast<uint64_t>(
            std::numeric_limits<int>::max())) {
        return "numFlits must fit positive int";
    }

    output.wireBytes = static_cast<int>(wire_bytes);
    output.numFlits = static_cast<int>(num_flits);
    return {};
}

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_NETWORK_GARNET_PACKETIZATION_HH__

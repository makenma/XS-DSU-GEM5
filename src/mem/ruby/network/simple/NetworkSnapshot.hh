#ifndef MEM_RUBY_NETWORK_SIMPLE_NETWORK_SNAPSHOT_HH
#define MEM_RUBY_NETWORK_SIMPLE_NETWORK_SNAPSHOT_HH

#include <cstdint>

namespace gem5::ruby
{
struct SimpleNetworkSnapshot
{
    uint64_t queuedMessages = 0;
    uint64_t serializationUnits = 0;
    uint64_t forwardedMessages = 0;
    uint64_t wireBytes = 0;
    uint64_t blockedCycles = 0;

    bool empty() const { return queuedMessages == 0 && serializationUnits == 0; }
    SimpleNetworkSnapshot &operator+=(const SimpleNetworkSnapshot &other)
    {
        queuedMessages += other.queuedMessages;
        serializationUnits += other.serializationUnits;
        forwardedMessages += other.forwardedMessages;
        wireBytes += other.wireBytes;
        blockedCycles += other.blockedCycles;
        return *this;
    }
};
}
#endif

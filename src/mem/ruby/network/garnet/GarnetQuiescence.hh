#ifndef __MEM_RUBY_NETWORK_GARNET_GARNET_QUIESCENCE_HH__
#define __MEM_RUBY_NETWORK_GARNET_GARNET_QUIESCENCE_HH__

#include <cstdint>

namespace gem5
{
namespace ruby
{
namespace garnet
{

/**
 * Protocol-neutral, read-only Garnet drain state.
 *
 * Flit fields count queued objects, VC fields count non-idle VCs, link fields
 * count objects in link/source staging queues, and creditDeficit is the sum of
 * (initial credits - current credits) over every NI/router output VC.
 */
struct GarnetQuiescenceSnapshot
{
    uint64_t niQueuedFlits = 0;
    uint64_t niQueuedMessages = 0;
    uint64_t routerBufferedFlits = 0;
    uint64_t nonIdleInputVcs = 0;
    uint64_t nonIdleOutputVcs = 0;
    uint64_t dataLinkPendingFlits = 0;
    uint64_t creditLinkPendingCredits = 0;
    uint64_t bridgePendingItems = 0;
    uint64_t creditDeficit = 0;

    bool empty() const
    {
        return niQueuedFlits == 0 && niQueuedMessages == 0 &&
               routerBufferedFlits == 0 && nonIdleInputVcs == 0 &&
               nonIdleOutputVcs == 0 && dataLinkPendingFlits == 0 &&
               creditLinkPendingCredits == 0 && bridgePendingItems == 0 &&
               creditDeficit == 0;
    }

    GarnetQuiescenceSnapshot &operator+=(
        const GarnetQuiescenceSnapshot &other)
    {
        niQueuedFlits += other.niQueuedFlits;
        niQueuedMessages += other.niQueuedMessages;
        routerBufferedFlits += other.routerBufferedFlits;
        nonIdleInputVcs += other.nonIdleInputVcs;
        nonIdleOutputVcs += other.nonIdleOutputVcs;
        dataLinkPendingFlits += other.dataLinkPendingFlits;
        creditLinkPendingCredits += other.creditLinkPendingCredits;
        bridgePendingItems += other.bridgePendingItems;
        creditDeficit += other.creditDeficit;
        return *this;
    }
};

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_NETWORK_GARNET_GARNET_QUIESCENCE_HH__

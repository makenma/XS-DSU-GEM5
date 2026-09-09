#ifndef __MEM_RUBY_NETWORK_GARNET_GARNET_QUIESCENCE_HH__
#define __MEM_RUBY_NETWORK_GARNET_GARNET_QUIESCENCE_HH__

#include <cstdint>
#include <string>
#include <vector>

#include "mem/ruby/network/garnet/DualLane.hh"

namespace gem5
{
namespace ruby
{
namespace garnet
{

/**
 * Protocol-neutral accounting for one upstream output VC and its directed
 * data link.  Credit conservation is checked as
 * initial + returned - sent == current; a drained network additionally has
 * current == initial.  ownerKind is 0 for an NI and 1 for a router.
 */
struct GarnetCreditLedgerEntry
{
    uint32_t ownerKind = 0;
    int32_t ownerId = -1;
    int32_t portId = -1;
    int32_t linkId = -1;
    uint32_t vc = 0;
    uint32_t vnet = 0;
    uint64_t initial = 0;
    uint64_t sent = 0;
    uint64_t returned = 0;
    uint64_t current = 0;
    uint64_t depth = 0;
    // Internal-only identity, normalized by GarnetNetwork to a stable index
    // before the ledger leaves the network.  It is never serialized.
    const void *linkToken = nullptr;

    bool conserved() const
    {
        return initial + returned == sent + current;
    }

    bool restored() const
    {
        return conserved() && current == initial;
    }
};

using GarnetCreditLedger = std::vector<GarnetCreditLedgerEntry>;

struct GarnetInputVcHighWaterEntry
{
    int32_t routerId = -1;
    int32_t inportId = -1;
    uint32_t vc = 0;
    uint32_t vnet = 0;
    uint64_t highWater = 0;
    uint64_t depth = 0;
};

using GarnetInputVcHighWater = std::vector<GarnetInputVcHighWaterEntry>;

struct GarnetReceiverCapacityEntry
{
    uint32_t senderKind = 0;
    int32_t senderId = -1;
    int32_t senderPort = -1;
    std::string senderDirection;
    uint32_t receiverKind = 0;
    int32_t receiverId = -1;
    int32_t receiverPort = -1;
    std::string receiverDirection;
    int32_t linkId = -1;
    uint32_t vnet = 0;
    uint32_t vc = 0;
    uint32_t depth = 0;
    uint32_t initialCredit = 0;
};

using GarnetReceiverCapacityMap = std::vector<GarnetReceiverCapacityEntry>;

struct GarnetInputVcStatsEntry
{
    int32_t routerId = -1;
    int32_t inportId = -1;
    int32_t ingressPort = -1;
    LaneId lane = 0;
    std::string direction;
    uint32_t vnet = 0;
    uint32_t vc = 0;
    uint32_t depth = 0;
    uint32_t occupancy = 0;
    uint64_t highWater = 0;
    uint64_t enqueued = 0;
    uint64_t dequeued = 0;
    uint64_t creditStalls = 0;
    uint64_t noVcStalls = 0;
    uint64_t saLost = 0;
    std::vector<uint64_t> timeHistogram;
};

enum class GarnetInputStall
{
    Credit,
    NoVc,
    SaLost
};

struct GarnetLinkStatsEntry
{
    int32_t linkId = -1;
    uint32_t widthBytes = 0;
    uint64_t flits = 0;
    std::vector<uint64_t> vcFlits;
};

struct GarnetExperimentSnapshot
{
    uint64_t tick = 0;
    uint64_t networkCycle = 0;
    std::vector<GarnetInputVcStatsEntry> inputVcs;
    std::vector<GarnetLinkStatsEntry> links;
    std::vector<uint64_t> packetsInjected;
    std::vector<uint64_t> packetsReceived;
    std::vector<uint64_t> flitsInjected;
    std::vector<uint64_t> flitsReceived;
    std::vector<uint64_t> wireBytesInjected;
    std::vector<uint64_t> wireBytesReceived;
};

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

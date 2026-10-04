#pragma once
#include <deque>
#include <optional>
#include "mem/cache/cache.hh"
#include "params/ChiIntentCache.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi {
// The test adapter uses the same tags/data as ordinary CPU accesses.
class ChiIntentCache : public Cache {
    std::optional<Addr> lockedLine;
    std::deque<PacketPtr> held;
    EventFunctionWrapper releaseEvent;
  protected:
    void recvTimingReq(PacketPtr pkt) override;
  public:
    struct Snapshot { bool valid=false, dirty=false, writable=false; std::vector<uint8_t> data; };
    explicit ChiIntentCache(const ChiIntentCacheParams& p);
    bool prepareIntent(Addr address, Snapshot& snapshot);
    void commitIntent(Addr address, uint8_t opcode, const std::vector<uint8_t>& data,
                      bool dirty, bool shared);
    void abortIntent();
    void recordState(Addr address,const FlowGraphIdentity& flow,uint32_t node);
};
}

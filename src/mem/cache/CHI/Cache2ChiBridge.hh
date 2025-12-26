#ifndef __CACHE2CHIBRIDGE_HH__
#define __CACHE2CHIBRIDGE_HH__

#include <array>
#include <cstdint>
#include <queue>

#include "mem/packet.hh"
#include "mem/port.hh"
#include "params/Cache2ChiBridge.hh"
#include "sim/clocked_object.hh"

// 你 CHI 目录下的类型
#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"

namespace gem5
{

class Cache2ChiBridge : public ClockedObject
{
  public:
    Cache2ChiBridge(const Cache2ChiBridgeParams& p);

    Port& getPort(const std::string& if_name, PortID idx = InvalidPortID) override;

  private:
    /** ============ Cache side port (acts like memory for the cache) ============ */
    class CacheSidePort : public ResponsePort
    {
      private:
        Cache2ChiBridge& owner;

      public:
        CacheSidePort(const std::string& name, Cache2ChiBridge& owner);

        bool recvTimingReq(PacketPtr pkt) override
        { return owner.cacheRecvTimingReq(pkt); }

        Tick recvAtomic(PacketPtr pkt) override
        { return owner.cacheRecvAtomic(pkt); }

        void recvFunctional(PacketPtr pkt) override
        { owner.cacheRecvFunctional(pkt); }

        // 如果你要处理 snoop（可后续补）
        bool recvTimingSnoopResp(PacketPtr pkt) override
        { owner.cacheRecvTimingSnoopResp(pkt); }

        void recvRespRetry() override
        { }

        AddrRangeList getAddrRanges() const override
        { return AddrRangeList(); }
    };

    /** ============ Ports ============ */
    CacheSidePort cachePort;

    // CHI side（你已经在 CHI 文件夹里写了）
    ChiCommonPort chiPort;

    /** ============ Core handlers called by ports ============ */
    bool cacheRecvTimingReq(PacketPtr pkt);
    Tick cacheRecvAtomic(PacketPtr pkt);
    void cacheRecvFunctional(PacketPtr pkt);
    void cacheRecvTimingSnoopResp(PacketPtr pkt);

    /** ============ Bridge internal queues ============ */
    std::queue<PacketPtr> pendingReqPkts; // cache->bridge 暂存
    std::queue<PacketPtr> pendingRespPkts; // bridge->cache 暂存（从CHI回来后组包）

    /** ============ Event pump ============ */
    EventFunctionWrapper pumpEvent;
    void pump();

    void schedulePump();
};




}//namespace gem5

#endif

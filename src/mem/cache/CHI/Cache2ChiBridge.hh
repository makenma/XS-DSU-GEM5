#ifndef __CACHE2CHIBRIDGE_HH__
#define __CACHE2CHIBRIDGE_HH__

#include <cstdint>
#include <deque>
#include <iosfwd>
#include <optional>
#include <queue>
#include <unordered_map>
#include <vector>

#include "mem/packet.hh"
#include "mem/port.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/Cache2ChiBridge.hh"
#include "sim/clocked_object.hh"

// 你 CHI 目录下的类型
#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"

namespace gem5
{

namespace Chi
{

class Cache2ChiBridge : public ClockedObject, public ruby::Consumer
{
  public:
    Cache2ChiBridge(const Cache2ChiBridgeParams& p);

    Port& getPort(const std::string& if_name, PortID idx = InvalidPortID) override;
    void wakeup() override;
    void print(std::ostream& out) const override;

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
        { return owner.cacheRecvTimingSnoopResp(pkt); }

        void recvRespRetry() override
        { owner.cacheRecvRespRetry(); }

        AddrRangeList getAddrRanges() const override
        { return AddrRangeList(); }
    };

    // ============ Mem side port (acts like cache for the memory/CHI) ============
     class MemSidePort : public RequestPort
    {
      private:
        Cache2ChiBridge *owner;
      public:
        MemSidePort(const std::string& name, Cache2ChiBridge *owner);
      protected:
        bool recvTimingResp(PacketPtr pkt) override {
          return owner->memSidePortRecvTimingResp(pkt);
        }
        void recvReqRetry() override {
          return owner->memSidePortRecvReqRetry();
        }
        void recvTimingSnoopReq(PacketPtr pkt) override {
          return owner->memSidePortRecvTimingSnoopReq(pkt);
        }
        void recvFunctionalSnoop(PacketPtr pkt) override {
          return owner->memSidePortRecvFunctionalSnoop(pkt);
        }
        Tick recvAtomicSnoop(PacketPtr pkt) override {
          return owner->memSidePortRecvAtomicSnoop(pkt);
        }
        void recvRangeChange() override {
          return owner->memSidePortRecvRangeChange();
        }
        bool isSnooping() const override {
          return true;
        }
    };


    enum class IntentKind : uint8_t
    {
        ReadShared,
        ReadUnique,
        ReadSharedForceClean,
        MakeUnique,
        MakeInvalid,
        CleanInvalid,
        WriteUniqueFull,
        WriteUniquePtl,
        WriteBackFull,
        WriteCleanFull,
        WriteEvictFull,
        Evict
    };

    enum class TxnClass : uint8_t
    {
        Read,
        Write,
        Maintenance,
        Evict
    };

    enum class RespState : uint8_t
    {
        I = 0,
        SC = 1,
        UC = 2,
        UD_PD = 3,
        SD_PD = 4,
        Unknown = 0xff
    };

    struct MemoryIntent
    {
        IntentKind kind;
        TxnClass txnClass;
        bool needsResponse = false;
        bool expectsData = false;
        bool expectsDbid = false;
        bool expectsComp = false;
        bool requiresCompAck = false;
        bool forceCleanResponse = false;
        bool carriesData = false;
        bool isPartial = false;
    };

    struct TxnEntry
    {
        uint32_t txnid = 0;
        PacketPtr pkt = nullptr;
        MemoryIntent intent{};
        RawReq req{};
        std::vector<RawDat> dataBeats;
        size_t nextDataBeat = 0;
        bool hasDbid = false;
        uint8_t dbid = 0;
        bool gotComp = false;
        bool gotData = false;
        bool sentCompAck = false;
        bool retryBlocked = false;
        bool completed = false;
        std::vector<uint8_t> readData;
    };

    struct SnoopSenderState : public Packet::SenderState
    {
        explicit SnoopSenderState(uint32_t txnid) : txnid(txnid) {}
        uint32_t txnid;
    };

    struct SnoopEntry
    {
        uint32_t txnid = 0;
        RawSnp snp{};
        PacketPtr snoopPkt = nullptr;
        bool invalidating = false;
        bool pendingData = false;
    };

    /** ============ Ports ============ */
    CacheSidePort cachePort;

    // CHI side（你已经在 CHI 文件夹里写了）
    ChiCommonPort chiPort;

    MemSidePort memPort;

    /** ============ Core handlers called by ports ============ */
    bool cacheRecvTimingReq(PacketPtr pkt);
    Tick cacheRecvAtomic(PacketPtr pkt);
    void cacheRecvFunctional(PacketPtr pkt);
    bool cacheRecvTimingSnoopResp(PacketPtr pkt);
    void cacheRecvRespRetry();

    MemoryIntent classify(PacketPtr pkt) const;
    RawReq mapToReq(const MemoryIntent& intent, PacketPtr pkt, uint32_t txnid) const;
    std::vector<RawDat> packDataBeats(const MemoryIntent& intent,
                                      PacketPtr pkt,
                                      uint32_t txnid) const;

    // Mem side port methods
    virtual bool memSidePortRecvTimingResp(PacketPtr pkt);
    virtual void memSidePortRecvReqRetry();
    virtual void memSidePortRecvTimingSnoopReq(PacketPtr pkt);
    virtual void memSidePortRecvFunctionalSnoop(PacketPtr pkt);
    virtual Tick memSidePortRecvAtomicSnoop(PacketPtr pkt);
    virtual void memSidePortRecvRangeChange();



    /** ============ Bridge internal queues ============ */
    std::queue<PacketPtr> pendingReqPkts; // cache->bridge 暂存
    std::queue<PacketPtr> pendingRespPkts; // bridge->cache 暂存（从CHI回来后组包）
    std::queue<uint32_t> retryTxnIds;
    std::unordered_map<uint32_t, TxnEntry> txns;
    std::unordered_map<uint32_t, SnoopEntry> snoops;

    const uint32_t nodeId;
    const uint32_t homeNodeId;
    const uint32_t txnIdBase;
    const uint32_t maxTxns;
    const uint32_t blockSize;
    const uint32_t dataBeatBytes;
    const bool enableRetry;
    const bool sinkHnfTxReq;
    uint32_t nextTxnId = 1;
    uint32_t nextSnoopTxnId = 1;

    /** ============ Event pump ============ */
    EventFunctionWrapper pumpEvent;
    void pump();

    void schedulePump();
    bool hasPumpWork() const;

    std::optional<uint32_t> allocateTxnId();
    void freeTxn(uint32_t txnid);
    uint32_t allocateSnoopTxnId();

    void drainChiTx();
    void handleTxReq(const RawReq& req);
    void handleRsp(const RawRsp& rsp);
    void handleDat(const RawDat& dat);
    void handleSnp(const RawSnp& snp);
    bool sendPendingResponses();
    bool sendCompAck(TxnEntry& txn);
    bool sendTxnData(TxnEntry& txn);
    bool reissueRetriedTxn(uint32_t txnid);
    void maybeComplete(TxnEntry& txn);
    void completeClassicTxn(TxnEntry& txn);

    void sendSnoopRsp(const SnoopEntry& snoop, RespState state);
    void sendSnoopData(SnoopEntry& snoop, PacketPtr pkt);
    MemCmd snoopCmdFor(const RawSnp& snp) const;
    bool snoopInvalidates(const RawSnp& snp) const;
};


}

}//namespace gem5

#endif

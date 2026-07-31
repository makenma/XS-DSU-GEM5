#ifndef __CACHE2CHIBRIDGE_HH__
#define __CACHE2CHIBRIDGE_HH__

#include <cstdint>
#include <deque>
#include <functional>
#include <iosfwd>
#include <optional>
#include <queue>
#include <unordered_map>
#include <unordered_set>
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

#ifdef UNIT_TEST
class Cache2ChiBridgeProtocolTestPeer;
#endif

class Cache2ChiBridge : public ClockedObject, public ruby::Consumer
{
  public:
    Cache2ChiBridge(const Cache2ChiBridgeParams& p);

    Port& getPort(const std::string& if_name, PortID idx = InvalidPortID) override;
    void wakeup() override;
    void print(std::ostream& out) const override;

    /**
     * A promoted classic Upgrade normally has to be restored to an
     * UpgradeResp.  The exception is an invalidating snoop which precedes
     * the same MSHR: the classic cache then replaces the Upgrade target with
     * a ReadEx target and needs the data-bearing response.
     */
    static constexpr bool
    retainPromotedUpgradeResponse(bool promoted, bool snoopPrecedes,
                                  bool snoopInvalidates)
    {
        return promoted && !(snoopPrecedes && snoopInvalidates);
    }

    /** Arm CMN SCG XOR selector for a power-of-two HN-F target table. */
    static uint32_t cmnHnfIndex(Addr address, size_t hnf_count,
                                uint8_t pa_bits);

  private:
#ifdef UNIT_TEST
    friend class Cache2ChiBridgeProtocolTestPeer;
#endif

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
        { return owner.cacheGetAddrRanges(); }
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
        I_PD = 5,
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
        bool respondAsUpgrade = false;
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
        bool retryBlocked = false;
        bool completed = false;
        uint32_t readExpectedBytes = 0;
        std::vector<uint8_t> readData;
        std::vector<uint8_t> readCoverage;
        std::vector<uint8_t> seenReadDataIds;
        uint32_t readDataBytes = 0;
        bool sawReadDataLast = false;
        std::optional<uint8_t> readResp;
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
        std::optional<RawRsp> pendingRsp;
        std::vector<RawDat> dataBeats;
        size_t nextDataBeat = 0;
    };

    struct PendingClassicResponse
    {
        PacketPtr pkt = nullptr;
        std::optional<RawRsp> compAck;
        std::optional<uint32_t> txnid;
    };

    struct PendingCompAck
    {
        RawRsp rsp{};
        uint32_t txnid = 0;
    };

    struct PendingSnoopRetry
    {
        RawSnp snp{};
        uint32_t attempts = 0;
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
    AddrRangeList cacheGetAddrRanges() const;

    MemoryIntent classify(PacketPtr pkt) const;
    RawReq mapToReq(const MemoryIntent& intent, PacketPtr pkt,
                    uint32_t txnid) const;
    std::vector<RawDat> packDataBeats(const MemoryIntent& intent,
                                      PacketPtr pkt,
                                      const RawReq& req) const;
    uint32_t selectHomeNode(Addr address) const;

    // Mem side port methods
    virtual bool memSidePortRecvTimingResp(PacketPtr pkt);
    virtual void memSidePortRecvReqRetry();
    virtual void memSidePortRecvTimingSnoopReq(PacketPtr pkt);
    virtual void memSidePortRecvFunctionalSnoop(PacketPtr pkt);
    virtual Tick memSidePortRecvAtomicSnoop(PacketPtr pkt);
    virtual void memSidePortRecvRangeChange();



    /** ============ Bridge internal queues ============ */
    std::queue<PacketPtr> pendingReqPkts; // cache->bridge 暂存
    std::queue<PendingClassicResponse> pendingRespPkts;
    std::queue<uint32_t> retryTxnIds;
    std::queue<PendingCompAck> pendingCompAcks;
    std::queue<PendingSnoopRetry> pendingSnoopRetries;
    std::deque<SnoopEntry> pendingSnoopResponses;
    std::unordered_map<uint32_t, TxnEntry> txns;
    std::unordered_map<uint32_t, SnoopEntry> snoops;
    std::unordered_set<PacketPtr> promotedUpgradePkts;
    bool cacheRespBlocked = false;

    const uint32_t nodeId;
    const uint32_t homeNodeId;
    const std::vector<uint32_t> homeNodeIds;
    const uint8_t hnfHashPaBits;
    const uint32_t txnIdBase;
    const uint32_t txnIdNamespace;
    const uint32_t txnIdNamespaceCount;
    const uint32_t maxTxns;
    const uint32_t blockSize;
    const uint32_t dataBeatBytes;
    const bool enableRetry;
    const bool sinkHnfTxReq;
    // Keep the allocation cursor wider than the modeled CHI field so that
    // exhaustion is distinguishable from an accidental uint32_t wrap.
    uint64_t nextTxnId = 1;
    uint32_t nextSnoopTxnId = 1;

    /** ============ Event pump ============ */
    EventFunctionWrapper pumpEvent;
    void pump();

    void schedulePump();
    bool hasPumpWork() const;

    std::optional<uint32_t> allocateTxnId();
    static uint64_t firstTxnIdInNamespace(
        uint32_t base, uint32_t namespace_id, uint32_t namespace_count);
    static std::optional<uint32_t> allocateMonotonicTxnId(
        uint64_t& next_id, size_t outstanding, uint32_t max_outstanding,
        uint32_t namespace_count);
    void freeTxn(uint32_t txnid);
    uint32_t allocateSnoopTxnId();

    void drainChiTx();
    void handleTxReq(const RawReq& req);
    void handleRsp(const RawRsp& rsp);
    void handleDat(const RawDat& dat);
    static bool acceptReadDataBeat(TxnEntry& txn, const RawDat& dat,
                                   uint32_t data_beat_bytes,
                                   const char* owner_name);
    void handleSnp(const RawSnp& snp, uint32_t attempts = 0);
    bool sendPendingResponses();
    bool sendPendingCompAcks();
    bool sendPendingSnoopResponses();
    static bool advancePendingSnoopResponse(
        SnoopEntry& snoop,
        const std::function<bool(ChannelType, const FlitVariant&)>& enqueue);
    void queuePendingSnoopResponse(SnoopEntry&& snoop);
    std::optional<RawRsp> makeCompAck(const TxnEntry& txn) const;
    bool sendTxnData(TxnEntry& txn);
    bool reissueRetriedTxn(uint32_t txnid);
    void maybeComplete(TxnEntry& txn);
    void completeClassicTxn(TxnEntry& txn);

    void sendSnoopRsp(SnoopEntry& snoop, RespState state);
    void sendSnoopData(SnoopEntry& snoop, PacketPtr pkt);
    bool respondFromPendingCopyback(const RawSnp& snp);
    bool snoopPrecedesPendingTxn(const RawSnp& snp);
    MemCmd snoopCmdFor(const RawSnp& snp) const;
    bool snoopInvalidates(const RawSnp& snp) const;
};


}

}//namespace gem5

#endif

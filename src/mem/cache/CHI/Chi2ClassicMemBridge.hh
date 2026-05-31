#ifndef __CHI2CLASSICMEMBRIDGE_HH__
#define __CHI2CLASSICMEMBRIDGE_HH__

#include <cstdint>
#include <deque>
#include <optional>
#include <unordered_map>
#include <vector>

#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/packet.hh"
#include "mem/port.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/Chi2ClassicMemBridge.hh"
#include "sim/clocked_object.hh"

namespace gem5
{

namespace Chi
{

class Chi2ClassicMemBridge : public ClockedObject, public ruby::Consumer
{
  public:
    Chi2ClassicMemBridge(const Chi2ClassicMemBridgeParams& p);

    Port& getPort(const std::string& if_name,
                  PortID idx = InvalidPortID) override;
    void wakeup() override;
    void print(std::ostream& out) const override;

  private:
    class MemSidePort : public RequestPort
    {
      public:
        MemSidePort(const std::string& name, Chi2ClassicMemBridge& owner);

      protected:
        bool recvTimingResp(PacketPtr pkt) override;
        void recvReqRetry() override;
        void recvRangeChange() override;

      private:
        Chi2ClassicMemBridge& owner;
    };

    struct SnfSenderState : public Packet::SenderState
    {
        explicit SnfSenderState(uint32_t txnid) : txnid(txnid) {}
        uint32_t txnid;
    };

    struct TxnEntry
    {
        RawReq req{};
        PacketPtr pkt = nullptr;
        uint32_t expectedBytes = 0;
        bool responseSeen = false;
    };

    ChiCommonPort chiPort;
    MemSidePort memPort;

    const uint32_t nodeId;
    const uint32_t hnfNodeId;
    const uint32_t blockSize;
    const uint32_t dataBeatBytes;
    const uint32_t maxOutstanding;
    const RequestorID requestorId;

    std::unordered_map<uint32_t, TxnEntry> txns;
    std::deque<RawDat> txDatQ;
    PacketPtr blockedPkt = nullptr;
    EventFunctionWrapper pumpEvent;

    void schedulePump();
    void pump();
    bool hasPumpWork() const;

    void drainChiReq();
    void acceptReadNoSnp(const RawReq& req);
    PacketPtr makeReadPacket(const RawReq& req);
    bool sendMemPacket(PacketPtr pkt);
    bool recvMemResp(PacketPtr pkt);
    void queueCompData(const TxnEntry& txn, const uint8_t* data,
                       uint32_t data_bytes);
    void sendPendingDat();
    uint32_t expectedDataBytes(const RawReq& req) const;
};

} // namespace Chi
} // namespace gem5

#endif // __CHI2CLASSICMEMBRIDGE_HH__

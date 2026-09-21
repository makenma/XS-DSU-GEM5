#ifndef __CHICOSIMBRIDGE_HH__
#define __CHICOSIMBRIDGE_HH__

#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/packet.hh"
#include "mem/port.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/ChiCosimBridge.hh"
#include "sim/clocked_object.hh"

namespace gem5
{

namespace Chi
{

/** Minimal JSON value used for the co-sim wire protocol (one object per
 * newline-terminated line, see c2xm_pyuvm_env/cosim_protocol.py). */
struct CosimJson
{
    enum class Type { Null, Bool, Num, Str, Arr, Obj };

    Type type = Type::Null;
    bool boolean = false;
    double num = 0;
    std::string str;
    std::vector<CosimJson> arr;
    std::map<std::string, CosimJson> obj;

    bool isObj() const { return type == Type::Obj; }
    const CosimJson* get(const std::string& key) const;
    long asInt(long def = 0) const;
    std::string asStr() const { return str; }

    /** Parse one JSON document; returns nullptr on malformed input. */
    static std::unique_ptr<CosimJson> parse(const std::string& text);
};

/**
 * SN-F co-simulation bridge.
 *
 * Replaces Chi2ClassicMemBridge: instead of translating CHI flits to
 * classic memory packets itself, it ships them to an external RTL
 * testbench and relays that testbench's AXI traffic to this system's
 * memory.  The barrier protocol locks the two simulators to a shared
 * runtime: every quantum_cycles the bridge flushes its outgoing messages,
 * sends "sync" and blocks the gem5 event loop until the peer answers
 * "ack" (processing any mem/bypass requests that arrive meanwhile).
 */
class ChiCosimBridge : public ClockedObject, public ruby::Consumer
{
  public:
    ChiCosimBridge(const ChiCosimBridgeParams& p);

    Port& getPort(const std::string& if_name,
                  PortID idx = InvalidPortID) override;
    void wakeup() override;
    void print(std::ostream& out) const override;

    void startup() override;
    void drainResume() override;

  private:
    /** CHI-side port bound to the router's local port. */
    class ChiPort : public ChiCommonPort
    {
      public:
        ChiPort(const std::string& name, ChiCosimBridge& owner)
            : ChiCommonPort(name, static_cast<ruby::Consumer*>(&owner), 0),
              owner(owner) {}

      private:
        ChiCosimBridge& owner;
    };

    /** Classic request port toward system.membus: carries the testbench's
     * DDR accesses and locally-routed bypass packets. */
    class MemPort : public RequestPort
    {
      public:
        MemPort(const std::string& name, ChiCosimBridge& owner);

      protected:
        bool recvTimingResp(PacketPtr pkt) override;
        void recvReqRetry() override;
        void recvRangeChange() override;

      private:
        ChiCosimBridge& owner;
    };

    /** Classic response port collecting the RNF bypass (uncached)
     * traffic that used to go straight to the membus. */
    class BypassPort : public ResponsePort
    {
      public:
        BypassPort(const std::string& name, ChiCosimBridge& owner);

      protected:
        bool recvTimingReq(PacketPtr pkt) override;
        Tick recvAtomic(PacketPtr pkt) override;
        void recvFunctional(PacketPtr pkt) override;
        void recvRespRetry() override;
        AddrRangeList getAddrRanges() const override;

      private:
        ChiCosimBridge& owner;
    };

    /** Tags mem-side packets with their co-sim message id. */
    struct MemState : public Packet::SenderState
    {
        MemState(uint64_t id_, bool read_) : id(id_), read(read_) {}
        uint64_t id;
        bool read;
    };

    // ports & identity
    ChiPort chiPort;
    MemPort memPort;
    BypassPort bypassPort;
    System& sys;
    const uint32_t nodeId;
    const uint32_t hnfNodeId;
    const uint32_t quantumCycles;
    const double barrierTimeout;
    const std::string socketPath;
    const std::string bypassRoute;
    const RequestorID requestorId;

    // socket state
    int listenFd = -1;
    int connFd = -1;
    std::string rxBuf;
    bool connected = false;
    bool helloSent = false;
    bool peerDone = false;

    // barrier state
    EventFunctionWrapper barrierEvent;
    uint64_t barrierCount = 0;

    // CHI pump state
    EventFunctionWrapper pumpEvent;
    std::deque<RawRsp> txRspQ;   // peer -> gem5 responses, pending credit
    std::deque<RawDat> txDatQ;   // peer -> gem5 data, pending credit

    // messages waiting to be flushed at the next barrier
    std::deque<std::string> outbox;

    // mem-side outstanding state: every outbound classic request goes
    // through one queue, with at most one send attempt per event/retry
    // (this xbar's layer asserts if a port sends while already waiting).
    std::deque<PacketPtr> memReqQ;
    EventFunctionWrapper memSendEvent;
    uint64_t nextMemId = 1;

    // bypass packets crossing the socket, keyed by packet id
    struct BypassEntry
    {
        PacketPtr pkt;
        bool read;
    };
    std::map<uint64_t, BypassEntry> bypassPkts;
    uint64_t nextBypassPkt = 1;
    /** Bypass responses waiting to go out, one send attempt per event. */
    std::deque<PacketPtr> bypassRespQ;
    EventFunctionWrapper respSendEvent;
    void trySendBypassResp(PacketPtr pkt);
    void sendOneBypassResp();
    void queueMemReq(PacketPtr pkt);
    void sendOneMemReq();

    // ---- CHI side ------------------------------------------------------
    void schedulePump();
    bool hasPumpWork() const;
    void pump();
    void drainChiReq();
    void drainChiDat();
    void sendPendingT2g();

    // ---- barrier / socket ----------------------------------------------
    void openListener();
    void tryAccept();
    void barrier();
    bool recvJsonLine(std::unique_ptr<CosimJson>& out, double timeout_s);
    void handleBarrierMessage(const CosimJson& msg);
    void sendLine(const std::string& line);

    // ---- mem side (classic packets) -------------------------------------
    bool memPortRecvResp(PacketPtr pkt);
    bool bypassRecvReq(PacketPtr pkt);
    void issueMemRead(uint64_t id, Addr addr, uint32_t len);
    void issueMemWrite(uint64_t id, Addr addr,
                       const std::vector<uint8_t>& data,
                       const std::vector<uint8_t>& strb);
    void sendBlockedPkt();

    // ---- bypass ----------------------------------------------------------
    bool bypassToLocal(PacketPtr pkt);
    void handleBypassResp(const CosimJson& msg);

    // ---- (de)serialization ------------------------------------------------
    std::string reqToJson(const RawReq& req) const;
    std::string datToJson(const RawDat& dat) const;
    bool jsonToRsp(const CosimJson& j, RawRsp& rsp) const;
    bool jsonToDat(const CosimJson& j, RawDat& dat) const;

    static std::string hexEncode(const std::vector<uint8_t>& bytes);
    static std::vector<uint8_t> hexDecode(const std::string& hex);
    static std::string jsonEscape(const std::string& s);
};

} // namespace Chi
} // namespace gem5

#endif // __CHICOSIMBRIDGE_HH__

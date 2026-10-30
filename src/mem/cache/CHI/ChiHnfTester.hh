#ifndef __MEM_CACHE_CHI_HNF_TESTER_HH__
#define __MEM_CACHE_CHI_HNF_TESTER_HH__

#include <array>
#include <deque>
#include <map>
#include <string>
#include <utility>

#include "mem/cache/CHI/SlcSfTypes.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "params/ChiHnfTester.hh"
#include "sim/eventq.hh"

namespace gem5::Chi
{
class HomeNodeFull;

class ChiHnfTester : public ClockedObject, public ruby::Consumer
{
  public:
    PARAMS(ChiHnfTester);
    explicit ChiHnfTester(const Params &p);
    void startup() override;
    void wakeup() override;
    void print(std::ostream &out) const override;
    Port &getPort(const std::string &name, PortID idx = InvalidPortID) override;

  private:
    using LineKey = std::pair<uint64_t, bool>;
    struct Pending {
        uint64_t due;
        ChannelType channel;
        FlitVariant flit;
    };
    struct Read {
        RawReq req{};
        SlcLineData expected{};
        SlcLineData received{};
        unsigned mask = 0;
        bool complete = false;
    };
    struct Write {
        RawReq req{};
        SlcLineData data{};
        unsigned mask = 0;
    };

    void tick();
    void setup();
    void receive(const RawReq &req);
    void receive(const RawSnp &snp);
    void receive(const RawRsp &rsp);
    void receive(const RawDat &dat);
    void send(ChannelType ch, FlitVariant flit, unsigned delay = 0);
    void sendData(uint32_t src, uint32_t tgt, uint32_t txn, uint32_t dbid,
                  uint8_t opcode, const SlcLineData &data, bool dirty);
    void checkFinished();
    void checkAfterEdge();
    void addRequest(uint32_t src, uint32_t txn, uint8_t opcode,
                    uint64_t addr, bool ns, const SlcLineData &expected);
    void addThroughputRequest();
    void recordFlit(ChannelType ch, const FlitVariant &flit, bool outgoing);
    static SlcLineData pattern(uint64_t addr, bool dirty = false);
    static LineKey key(uint64_t addr, bool ns) { return {addr & ~63ULL, ns}; }

    HomeNodeFull *hnf;
    ChiCommonPort port;
    EventFunctionWrapper tickEvent;
    EventFunctionWrapper checkEvent;
    const std::string scenario;
    const uint64_t maxCycles;
    const unsigned responseDelay;
    const unsigned throughputRequests;
    const bool earlySchedule;
    unsigned throughputIssued = 0;
    uint64_t traceDigest = 14695981039346656037ULL;
    uint64_t cycle = 0;
    uint64_t idleSince = 0;
    uint64_t stallUntil = 0;
    uint32_t nextDbid = 0x10000;
    unsigned readsFromMemory = 0;
    unsigned writesToMemory = 0;
    unsigned snoops = 0;
    unsigned completed = 0;
    unsigned expectedWrites = 0;
    unsigned expectedSnoops = 0;
    bool expectNoMemoryRead = false;
    bool done = false;
    uint32_t hazardAckId = 0;
    bool hazardAckDelivered = false;
    bool hazardHeld = false;
    bool hazardReleased = false;
    bool seqReleaseCheckPending = false;
    uint32_t broadcastAckId = 0;
    bool broadcastAckDelivered = false;
    unsigned seqBroadcastTargets = 0;
    std::deque<Pending> pending;
    std::map<std::pair<uint32_t, uint32_t>, Read> requests;
    std::map<uint32_t, Write> writes;
    std::map<LineKey, SlcLineData> memory;
    std::map<std::pair<uint32_t, LineKey>, SlcLineData> owners;
    std::map<std::pair<uint32_t, LineKey>, bool> ownerDirty;
};
} // namespace gem5::Chi
#endif

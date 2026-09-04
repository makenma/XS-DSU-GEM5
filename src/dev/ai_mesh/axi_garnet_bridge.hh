#ifndef DEV_AI_MESH_AXI_GARNET_BRIDGE_HH
#define DEV_AI_MESH_AXI_GARNET_BRIDGE_HH

#include <cstdint>
#include <deque>
#include <map>
#include <vector>

#include "dev/ai_mesh/mesh_splitter.hh"
#include "mem/axi/axi_types.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

namespace axi
{
class AxiInitiatorAdapter;
}

struct AxiGarnetBridgeParams;

namespace ai_mesh
{

// Per-core AXI master bridge between the DMA engine's burst stream and the
// real AXI-over-Garnet initiator adapter.  All submission queues, the
// outstanding tables and the AXI ID pool are finite; tryAccept*/tryConsume*
// backpressure propagates to submit() returning false.
class AxiGarnetBridge : public ClockedObject
{
  public:
    using Params = AxiGarnetBridgeParams;

    AxiGarnetBridge(const Params &p);

    // Burst submission from the DMA engine.  Returns false when the finite
    // pending queue is full (retry with identical content is side-effect
    // free: nothing is accounted until the adapter accepts).  On success the
    // bridge-owned burst ordinal is returned through *ordinal_out; every
    // completion callback carries that ordinal.
    bool submitRead(uint64_t beat_base, uint32_t beats, uint16_t axi_id,
                    uint8_t qos, uint64_t *ordinal_out);
    bool submitWrite(uint64_t beat_base, uint32_t beats, uint16_t axi_id,
                     uint8_t qos, const std::vector<axi::AxiWBeat> &w_beats,
                     uint64_t *ordinal_out);

    void bindAdapter(axi::AxiInitiatorAdapter *adapter);

    struct Counters
    {
        uint64_t awAccepted = 0;
        uint64_t wAccepted = 0;
        uint64_t arAccepted = 0;
        uint64_t bConsumed = 0;
        uint64_t rBeatsConsumed = 0;
        // Per accepted write burst: 0-based ordinal in submission order.
        std::vector<uint64_t> bOrder;
        uint64_t bErrorCount = 0;
        uint64_t rErrorBeats = 0;
    };
    const Counters &counters() const { return ctr; }

    // Pending work still queued or outstanding (drain accounting).
    uint32_t pendingReads() const { return pending_ar.size(); }
    uint32_t pendingWrites() const { return pending_aw.size(); }
    uint64_t acceptedReads() const { return ctr.arAccepted; }
    uint64_t acceptedWrites() const { return ctr.awAccepted; }
    uint64_t completedWrites() const;
    uint32_t outstandingReads() const;
    uint32_t outstandingWrites() const;
    uint32_t dataBusBytes() const { return data_bus_bytes; }

    struct OrdinalTicks
    {
        Tick addr_accept = 0;  // real AR/AW handshake tick
        Tick first_w = 0;      // first W beat accepted
        Tick resp_last = 0;    // last R beat / B response tick
    };
    const OrdinalTicks *ordinalTicks(uint64_t ordinal) const;

    // Completion notification targets (the DMA engine).
    void setReadBeatSink(void *ctx,
                         void (*on_beat)(void *, uint64_t, uint16_t, bool,
                                         axi::AxiResp, const uint8_t *));
    void setWriteDoneSink(void *ctx,
                          void (*on_done)(void *, uint64_t, axi::AxiResp));
    void setProtocolErrorSink(void *ctx,
                              void (*on_error)(void *, bool));

    bool idle() const;

    void startup() override;

  private:
    struct PendingRead
    {
        axi::AxiAddressRequest ar;
        uint64_t ordinal = 0;
    };
    struct PendingWrite
    {
        axi::AxiAddressRequest aw;
        std::vector<axi::AxiWBeat> beats;
        uint32_t next_beat = 0;
        bool aw_accepted = false;
        uint64_t ordinal = 0;
    };

    struct TickEvent : public Event
    {
        AxiGarnetBridge *bridge;
        explicit TickEvent(AxiGarnetBridge *b) : Event(), bridge(b) {}
        void process() override { bridge->tick(); }
        const char *description() const override
        { return "ai_mesh.axi_bridge.tick"; }
    };

    void tick();
    void scheduleTick();
    void driveWrites();
    void driveReads();
    void consumeB();
    void consumeR();

    axi::AxiInitiatorAdapter *adapter = nullptr;
    struct AdapterBindEvent;
    const uint32_t data_bus_bytes;
    const uint32_t aw_queue_depth;
    const uint32_t ar_queue_depth;
    const uint32_t max_axi_ids;
    const uint16_t axi_id_base;

    std::deque<PendingWrite> pending_aw;
    std::deque<PendingRead> pending_ar;
    // Per AXI ID, write bursts in acceptance order awaiting their B.
    std::map<uint16_t, std::deque<uint64_t>> write_ordinal_by_id;
    // Per AXI ID, the read burst currently being consumed.
    struct ReadHead
    {
        uint64_t ordinal = 0;
        uint32_t beats_seen = 0;
        uint32_t beats_total = 0;
        bool active = false;
    };
    std::map<uint16_t, ReadHead> read_head_by_id;
    std::map<uint64_t, uint32_t> read_beats_of_ordinal;
    std::map<uint64_t, PendingWrite> live_writes;
    std::map<uint64_t, PendingRead> live_reads;
    uint64_t next_ordinal = 0;
    uint16_t id_cursor = 0;

    Counters ctr;
    TickEvent tick_event;

    void *read_sink_ctx = nullptr;
    std::map<uint64_t, OrdinalTicks> ordinal_handshake;
    void (*read_beat_fn)(void *, uint64_t, uint16_t, bool, axi::AxiResp,
                         const uint8_t *) = nullptr;
    void (*protocol_error_fn)(void *, bool) = nullptr;
    void *write_sink_ctx = nullptr;
    void *protocol_error_ctx = nullptr;

    void (*write_done_fn)(void *, uint64_t, axi::AxiResp) = nullptr;
};

} // namespace ai_mesh
} // namespace gem5

#endif

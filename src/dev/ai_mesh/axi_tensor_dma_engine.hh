#ifndef DEV_AI_MESH_AXI_TENSOR_DMA_ENGINE_HH
#define DEV_AI_MESH_AXI_TENSOR_DMA_ENGINE_HH

#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <set>
#include <vector>

#include "dev/ai_mesh/dma_types.hh"
#include "dev/ai_mesh/burst_attribution.hh"
#include "dev/ai_mesh/mesh_runtime_observations.hh"
#include "dev/ai_mesh/mesh_splitter.hh"
#include "mem/axi/axi_types.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

struct AxiTensorDmaEngineParams;

namespace ai_mesh
{

class AxiGarnetBridge;
struct RuntimeArch;

// Real-network DMA engine: descriptors split into AXI bursts that traverse
// the NPU Garnet through the bridge.  LOAD/PREFETCH completes only after
// the last valid R beat's bytes commit into local SRAM (the commit fires on
// the SRAM write-service completion event, not at RLAST); STORE/P2P builds
// its W beats only after the local SRAM read service completes and finishes
// when every burst's successful B is consumed at the source; FILL commits
// each row through the same finite SRAM write service.  Descriptors
// serialize FIFO per direction (read engine / write engine), which keeps
// the accepted transaction order deterministic.
class AxiTensorDmaEngine : public DmaEngineBase
{
  public:
    using Params = AxiTensorDmaEngineParams;

    AxiTensorDmaEngine(const Params &p);
    ~AxiTensorDmaEngine() override;

    void startup() override;
    void regStats() override;

    bool submit(const DecodedDmaDescriptor &descriptor,
                Tick issue_tick) override;
    void bindFillPattern(uint32_t command_id, uint64_t pattern) override;
    bool idle() const override;
    void bindOwner(MeshDummyCore *core, const RuntimeArch *arch) override;
    void beginInstance() override;    const std::map<uint32_t, ActualTraffic> &actualTraffic() const override
    {
        return actual;
    }
    uint32_t liveDescriptors() const override
    {
        return read_queue.size() + write_queue.size();
    }

    bool readFunctional(uint64_t address, uint64_t size,
                        uint8_t *out) override;

    void onReadBeat(uint64_t burst_ordinal, uint16_t beat_index, bool last,
                    axi::AxiResp resp, const uint8_t *data);
    void onWriteDone(uint64_t burst_ordinal, axi::AxiResp resp);
    void onProtocolError(bool read_direction);

    // Completion-point timing exported for the acceptance assertions:
    // first AXI issue/response ticks, last R beat tick, local SRAM commit
    // tick and descriptor terminal tick per descriptor (last instance wins).
    struct DescriptorTiming
    {
        Tick first_ar_tick = 0;
        Tick first_aw_tick = 0;
        Tick first_w_tick = 0;
        Tick first_b_tick = 0;
        Tick last_r_tick = 0;
        Tick local_commit_tick = 0;
        Tick done_tick = 0;
    };
    const std::map<uint32_t, DescriptorTiming> &descriptorTimings() const
    {
        return timings;
    }

    const std::map<uint64_t, Tick> &readCommitTicks() const
    { return read_commit_ticks; }

    // Every burst of the instance that is currently being observed, with the
    // admitted execution it belongs to and the retirement facts it earned.
    const std::vector<BurstAttribution> &instanceBursts() const
    { return instance_bursts; }

    uint64_t submittedReadBursts() const { return read_bursts_submitted; }
    uint64_t submittedWriteBursts() const { return write_bursts_submitted; }
    uint64_t completedReadBursts() const { return read_bursts_completed; }
    uint64_t completedWriteBursts() const { return write_bursts_completed; }
    uint64_t errorReadBursts() const { return read_bursts_errored; }
    uint64_t errorWriteBursts() const { return write_bursts_errored; }
    uint64_t validReadBytes() const { return read_valid_bytes; }
    uint64_t validWriteBytes() const { return write_valid_bytes; }
    uint32_t pendingReadReservations() const;
    uint32_t scheduledReadCommits() const;

    // Strict AXI read window evidence (AR handshake to RLAST consumed).
    uint64_t readArAccepted() const { return read_ar_accepted; }
    uint64_t readRlastConsumed() const { return read_rlast_consumed; }
    uint32_t peakReadWindow() const { return peak_read_window; }
    Tick firstRlastTick() const { return first_rlast_tick; }
    const std::vector<Tick> &arAcceptTicks() const { return ar_accept_ticks; }

    const AxiGarnetBridge *bridgeOf() const { return bridge; }

  private:
    enum class ReadServiceState : uint8_t
    {
        Receiving,
        AwaitingReservation,
        CommitScheduled
    };

    struct ReadBurst
    {
        AxiBurst plan;
        uint64_t dst_base = 0;
        uint32_t descriptor_id = 0;
        uint16_t axi_id = 0;
        bool errored = false;      // latched on ANY error beat (spec 7.4)
        bool seen_error_beat = false;
        bool axiIdReleased = false;  // one-shot AXI ID release at RLAST
        std::vector<uint8_t> packed; // valid lane bytes in logical order
        Tick last_r_tick = 0;
        ReadServiceState service_state = ReadServiceState::Receiving;
    };

    void releaseReadAxiId(ReadBurst &burst);

    struct WriteBurst
    {
        AxiBurst plan;
        uint32_t descriptor_id = 0;
        uint16_t kind = 0;
        uint16_t axi_id = 0;
        bool errored = false;
    };
    struct DescriptorState
    {
        DecodedDmaDescriptor descriptor;
        std::optional<DescriptorKey> key;
        uint32_t bursts_total = 0;
        uint32_t bursts_done = 0;
        uint32_t bursts_errored = 0;
        bool terminal = false;
        bool prepare_pending = false;
        Tick ready_tick = 0;
        std::vector<AxiBurst> plan;
        std::vector<uint64_t> burst_ordinals;
        std::optional<std::vector<axi::AxiWBeat>> prepared_beats;
        // Per-execution facts.  The transport row accumulates across
        // instances; this contribution is exactly one dispatched execution.
        TrafficContribution contribution;
        // Per-burst payload bytes keyed by logical address, folded over the
        // committed bursts at completion: read bursts commit on SRAM service
        // order and a faulted burst commits nothing, so the descriptor digest
        // must not depend on either.
        std::map<uint64_t, std::vector<uint8_t>> ordered_burst_payload;
        std::set<uint64_t> committed_bursts;
        uint64_t discarded_bytes = 0;
        uint64_t drained_bytes = 0;
    };

    struct TickEvent : public Event
    {
        AxiTensorDmaEngine *engine;
        explicit TickEvent(AxiTensorDmaEngine *e) : Event(), engine(e) {}
        void process() override { engine->tick(); }
        const char *description() const override
        {
            return "ai_mesh.axi_dma.tick";
        }
    };
    // SRAM service completion: read-burst local commit.
    struct ReadCommitEvent : public Event
    {
        AxiTensorDmaEngine *engine;
        uint64_t burst_ordinal;
        ReadCommitEvent(AxiTensorDmaEngine *e, uint64_t ordinal)
            : Event(), engine(e), burst_ordinal(ordinal)
        {
            setFlags(AutoDelete);
        }
        void process() override
        {
            engine->commitReadBurst(burst_ordinal);
        }
        const char *description() const override
        {
            return "ai_mesh.axi_dma.read_commit";
        }
    };
    // SRAM read service completion for one write burst: build the beats.
    struct WritePrepareEvent : public Event
    {
        AxiTensorDmaEngine *engine;
        uint32_t descriptor_id;
        uint32_t burst_index;
        uint64_t row_src_local;
        WritePrepareEvent(AxiTensorDmaEngine *e, uint32_t desc, uint32_t index,
                          uint64_t local)
            : Event(), engine(e), descriptor_id(desc), burst_index(index),
              row_src_local(local)
        {
            setFlags(AutoDelete);
        }
        void process() override
        {
            engine->prepareWriteBurst(descriptor_id, burst_index,
                                      row_src_local);
        }
        const char *description() const override
        {
            return "ai_mesh.axi_dma.write_prepare";
        }
    };
    // SRAM write service completion for one FILL row.
    struct FillRowEvent : public Event
    {
        AxiTensorDmaEngine *engine;
        uint32_t descriptor_id;
        uint32_t row;
        FillRowEvent(AxiTensorDmaEngine *e, uint32_t desc, uint32_t row_)
            : Event(), engine(e), descriptor_id(desc), row(row_)
        {
            setFlags(AutoDelete);
        }
        void process() override { engine->commitFillRow(descriptor_id, row); }
        const char *description() const override
        {
            return "ai_mesh.axi_dma.fill_row";
        }
    };

    void tick();
    void scheduleTick();
    void driveReadDescriptor();
    void driveWriteDescriptor();
    void driveFill(DescriptorState &state);
    void commitReadBurst(uint64_t burst_ordinal);
    void tryScheduleReadCommit(uint64_t burst_ordinal, ReadBurst &burst);
    void prepareWriteBurst(uint32_t descriptor_id, uint32_t burst_index,
                           uint64_t row_src_local);
    void commitFillRow(uint32_t descriptor_id, uint32_t row);
    void finishDescriptor(bool read, DmaStatus status);
    std::string recordSourceRows(const DescriptorState &state);
    std::string destinationDigest(const DecodedDmaDescriptor &descriptor);
    void notifyOwner(uint32_t descriptor_id, uint32_t command_id,
                     uint32_t completion_event, DmaStatus status);
    void retireBurst(bool read, uint64_t burst_ordinal, bool errored);
    void notePayload(uint32_t descriptor_id, const uint8_t *data,
                     uint64_t size);
    ActualTraffic &rowOf(uint32_t descriptor_id);
    std::vector<AxiBurst> planOf(const DecodedDmaDescriptor &descriptor) const;

    AxiGarnetBridge *const bridge;
    const uint32_t data_bus_bytes;
    const uint32_t max_burst_beats;
    const Cycles setup_cycles;
    const uint32_t descriptor_queue_depth;
    // Per-direction segment/read-return queue capacity: bounds bursts from
    // issue to local SRAM commit.  Distinct from the AXI outstanding window
    // (AR handshake to RLAST), which the initiator adapter owns.
    const uint32_t segment_queue_depth;
    const uint16_t axi_id_count;
    const uint64_t source_bytes_limit;

    MeshDummyCore *owner = nullptr;

    std::deque<DescriptorState> read_queue;
    std::deque<DescriptorState> write_queue;
    std::map<uint64_t, ReadBurst> live_read_bursts;
    std::map<uint64_t, Tick> read_commit_ticks;
    uint32_t instance_counter = 0;
    std::vector<BurstAttribution> instance_bursts;
    std::map<uint64_t, size_t> attribution_by_ordinal;

    BurstAttribution &noteBurst(const DescriptorState &state,
                                const AxiBurst &plan, uint32_t burst_index,
                                uint64_t ordinal, uint16_t axi_id, bool read);
    BurstAttribution *burstAttribution(uint64_t ordinal);
    void finishBurstAttribution(const DescriptorState &state, Tick done_tick);
    std::map<uint64_t, WriteBurst> live_write_bursts;
    std::map<uint32_t, std::pair<uint64_t, uint64_t>> payload_state;
    std::map<uint32_t, ActualTraffic> actual;
    std::map<uint32_t, DescriptorTiming> timings;
    std::map<uint32_t, uint64_t> fill_patterns;
    // Finite per-direction AXI ID pools: an ID returns to its pool only
    // after its burst's response completed, so the same ID is never live
    // twice (arch axi.max_outstanding_per_id).
    std::deque<uint16_t> free_read_ids;
    std::deque<uint16_t> free_write_ids;
    uint32_t pending_local_events = 0;

    uint64_t read_bursts_submitted = 0;
    uint64_t write_bursts_submitted = 0;
    uint64_t read_bursts_completed = 0;
    uint64_t write_bursts_completed = 0;
    uint64_t read_bursts_errored = 0;
    uint64_t write_bursts_errored = 0;
    uint64_t read_valid_bytes = 0;
    uint64_t write_valid_bytes = 0;
    uint64_t read_ar_accepted = 0;
    uint64_t read_rlast_consumed = 0;
    uint32_t peak_read_window = 0;
    Tick first_rlast_tick = 0;
    std::vector<Tick> ar_accept_ticks;
    uint64_t protocol_dropped_beats = 0;

    TickEvent tick_event;

    static void readBeatTrampoline(void *ctx, uint64_t ordinal,
                                   uint16_t beat_index, bool last,
                                   axi::AxiResp resp, const uint8_t *data);
    static void writeDoneTrampoline(void *ctx, uint64_t ordinal,
                                    axi::AxiResp resp);
    static void protocolErrorTrampoline(void *ctx, bool read_direction);
};

} // namespace ai_mesh
} // namespace gem5

#endif

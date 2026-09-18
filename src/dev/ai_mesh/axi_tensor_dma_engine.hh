#ifndef DEV_AI_MESH_AXI_TENSOR_DMA_ENGINE_HH
#define DEV_AI_MESH_AXI_TENSOR_DMA_ENGINE_HH

#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <vector>

#include "dev/ai_mesh/dma_types.hh"
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

    void bindInstance(const ServingInstanceBinding &value) override
    { instance_binding = value; }

    bool submit(const DecodedDmaDescriptor &descriptor,
                const RuntimeObjectKey &descriptor_key,
                const RuntimeObjectKey &command_key,
                const RuntimeObjectKey &completion_event,
                Tick issue_tick) override;
    void bindFillPattern(RuntimeObjectKey command, uint64_t pattern) override;
    void bindFillContent(RuntimeObjectKey command,
                         const std::vector<uint8_t> &content,
                         bool install_bytes) override;
    void releaseFillBindings(InstanceGeneration instance) override;
    bool idle() const override;
    void bindOwner(MeshDummyCore *core, const RuntimeArch *arch) override;
    const std::map<RuntimeObjectKey, ActualTraffic> &
    actualTraffic() const override
    {
        return actual;
    }
    uint32_t liveDescriptors() const override
    {
        return read_queue.size() + write_queue.size();
    }

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
    const std::map<RuntimeObjectKey, DescriptorTiming> &
    descriptorTimings() const
    {
        return timings;
    }

    const std::map<uint64_t, Tick> &readCommitTicks() const
    { return read_commit_ticks; }

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
        RuntimeObjectKey descriptor_key;
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
        RuntimeObjectKey descriptor_key;
        uint16_t kind = 0;
        uint16_t axi_id = 0;
        bool errored = false;
    };
    struct DescriptorState
    {
        DecodedDmaDescriptor descriptor;
        RuntimeObjectKey descriptor_key;
        RuntimeObjectKey command_key;
        RuntimeObjectKey completion_event;
        uint32_t bursts_total = 0;
        uint32_t bursts_done = 0;
        uint32_t bursts_errored = 0;
        bool terminal = false;
        bool prepare_pending = false;
        Tick ready_tick = 0;
        std::vector<AxiBurst> plan;
        std::vector<uint64_t> burst_ordinals;
        std::optional<std::vector<axi::AxiWBeat>> prepared_beats;
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
        RuntimeObjectKey descriptor;
        uint32_t burst_index;
        uint64_t row_src_local;
        WritePrepareEvent(AxiTensorDmaEngine *e, RuntimeObjectKey desc,
                          uint32_t index, uint64_t local)
            : Event(), engine(e), descriptor(desc), burst_index(index),
              row_src_local(local)
        {
            setFlags(AutoDelete);
        }
        void process() override
        {
            engine->prepareWriteBurst(descriptor, burst_index, row_src_local);
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
        RuntimeObjectKey descriptor;
        uint32_t row;
        FillRowEvent(AxiTensorDmaEngine *e, RuntimeObjectKey desc,
                     uint32_t row_)
            : Event(), engine(e), descriptor(desc), row(row_)
        {
            setFlags(AutoDelete);
        }
        void process() override { engine->commitFillRow(descriptor, row); }
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
    void prepareWriteBurst(RuntimeObjectKey descriptor, uint32_t burst_index,
                           uint64_t row_src_local);
    void commitFillRow(RuntimeObjectKey descriptor, uint32_t row);
    void finishDescriptor(bool read, DmaStatus status);
    void notifyOwner(RuntimeObjectKey descriptor, RuntimeObjectKey command,
                     RuntimeObjectKey completion_event, DmaStatus status);
    void retireBurst(bool read, uint64_t burst_ordinal, bool errored);
    void notePayload(RuntimeObjectKey descriptor, const uint8_t *data,
                     uint64_t size);
    ActualTraffic &rowOf(RuntimeObjectKey descriptor);
    std::vector<AxiBurst> planOf(const DecodedDmaDescriptor &descriptor) const;
    uint64_t remoteRowAddress(const DecodedDmaDescriptor &descriptor,
                              uint32_t row) const;

    ServingInstanceBinding instance_binding;

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

    MeshDummyCore *owner = nullptr;
    const RuntimeArch *arch = nullptr;

    std::deque<DescriptorState> read_queue;
    std::deque<DescriptorState> write_queue;
    std::map<uint64_t, ReadBurst> live_read_bursts;
    std::map<uint64_t, Tick> read_commit_ticks;
    std::map<uint64_t, WriteBurst> live_write_bursts;
    std::map<RuntimeObjectKey, std::pair<uint64_t, uint64_t>> payload_state;
    std::map<RuntimeObjectKey, ActualTraffic> actual;
    std::map<RuntimeObjectKey, DescriptorTiming> timings;
    std::map<RuntimeObjectKey, uint64_t> fill_patterns;
    struct FillContent
    {
        std::vector<uint8_t> bytes;
        bool install_bytes = true;
    };
    std::map<RuntimeObjectKey, FillContent> fill_contents;
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

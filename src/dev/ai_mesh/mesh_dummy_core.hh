#ifndef DEV_AI_MESH_MESH_DUMMY_CORE_HH
#define DEV_AI_MESH_MESH_DUMMY_CORE_HH

#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <utility>
#include <vector>

#include "dev/ai_mesh/dma_types.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/tensor_sram.hh"
#include "base/statistics.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

struct MeshDummyCoreParams;

namespace ai_mesh
{

class DmaEngineBase;
class MeshDispatcher;
class MeshProgramLoader;
class ProgramScoreboard;
class SramBacking;

// Dummy Core: interprets the base Scheduled Mesh IR closed set.  Compute is
// timing-only (analytic cycles + validity/digest annotation); tensor data
// moves are real bytes through the DMA engine and mock transport.
class MeshDummyCore : public ClockedObject
{
  public:
    using Params = MeshDummyCoreParams;

    MeshDummyCore(const Params &p);

    void startup() override;
    void regStats() override;

    // Loader interface: install the immutable command ROM views.
    void installProgram(const std::shared_ptr<const DecodedProgram> &program);

    // Dispatcher interface.
    void dispatchInstance(uint32_t instance_id);
    bool halted() const { return core_halted || error_drained; }
    // Quiescence includes the publication drain: a halted core with a
    // scheduled-but-invisible signal is not quiet (spec: HALT waits for
    // event publication drain).
    bool quiescent() const
    {
        return live_commands == 0 && pending_visibility.empty() &&
               (core_halted || error_drained);
    }

    // DMA engine callbacks.
    struct DmaTagInfo
    {
        uint16_t kind;
        uint16_t dst_space;
    };

    void completeFence(uint32_t command_id, uint32_t signal_event);
    void onInstanceError(Tick tick);
    void onDmaCompleted(uint32_t command_id, uint32_t completion_event,
                        Tick commit_tick, DmaStatus status);
    std::map<uint64_t, DmaTagInfo> liveDmaTagSnapshot() const
    {
        std::map<uint64_t, DmaTagInfo> snapshot;
        for (uint64_t tag : live_dma_tags) {
            auto it = dma_tag_info.find(tag);
            if (it != dma_tag_info.end())
                snapshot[tag] = it->second;
        }
        return snapshot;
    }
    void onTransferCommitted(uint32_t transfer_id);
    void notifyPeerCommit(uint16_t peer_core, uint32_t transfer_id);

    // Functional SRAM accessors used by the transports.
    bool functionalSramRead(uint64_t offset, uint64_t size, uint8_t *out);
    bool functionalSramWrite(uint64_t offset, uint64_t size, const uint8_t *in);

    // DMA engines validate the local source operand before reading SRAM.
    void checkDmaSourceValidity(uint32_t command_id);

    // SRAM bank/port reservation (timing + accounting) for callers outside
    // the core (DMA engines folding service stalls into commit ticks).
    // Reserve the LOCAL endpoint of a DMA descriptor and return its stall.
    bool dmaSramAdmissible(const DecodedDmaDescriptor &descriptor);
    uint64_t reserveDmaSram(const DecodedDmaDescriptor &descriptor, bool is_write);
    TensorSram::ReserveResult reserveSramService(uint64_t offset, uint64_t size,
                                                 bool write)
    {
        return sram.reserve(curTick(), offset, size, write);
    }
    void setSramBacking(SramBacking *backing) { sram.setBacking(backing); }

    void setDispatcher(MeshDispatcher *dispatcher);
    void setScoreboard(ProgramScoreboard *board) { scoreboard = board; }
    uint16_t archCoreId() const { return core_id_value; }
    DmaEngineBase *dmaEngine() const { return dma; }
    bool instanceErrored() const { return instance_error; }
    Tick errorLatchTick() const { return error_latch_tick; }
    Tick workDrainedTick() const { return work_drained_tick; }
    const std::map<uint32_t, Tick> &commandDoneTicks() const
    {
        return command_done_ticks;
    }

    // Counters exposed to stats.txt and the result JSON oracle.
    statistics::Scalar commandsIssued;
    statistics::Scalar commandsCompleted;
    statistics::Scalar commandsErrored;
    statistics::Scalar commandsCancelled;
    statistics::Scalar eventsPublished;
    statistics::Scalar haltCommands;
    statistics::Scalar requestBegins;
    statistics::Scalar requestEnds;
    statistics::Scalar gemmCycles;
    statistics::Scalar reduceCommands;
    statistics::Scalar reduceCycles;
    statistics::Scalar sramBankConflicts;
    statistics::Scalar sramServiceCycles;
    statistics::Scalar poisonReadFaults;
    struct ComputeDigest
    {
        uint32_t command_id = 0;
        uint32_t allocation_id = 0;
        uint64_t offset = 0;
        uint32_t digest_words[4] = {0, 0, 0, 0};
    };
    std::vector<ComputeDigest> computeDigests;
    std::vector<uint32_t> completed_command_ids;
    struct InstanceLedger
    {
        std::vector<uint32_t> completed;
        std::vector<uint32_t> errored;
        std::vector<uint32_t> cancelled;
    };
    InstanceLedger takeInstanceLedger();

    std::vector<uint32_t> instance_completed_ids;
    std::vector<uint32_t> instance_errored_ids;
    std::vector<uint32_t> instance_cancelled_ids;
    std::vector<uint32_t> errored_command_ids;
    std::vector<uint32_t> cancelled_command_ids;
    std::map<uint32_t, Tick> command_done_ticks;
    std::map<uint32_t, Tick> command_issue_ticks;

  private:
    struct VisibilityEvent : public Event
    {
        MeshDummyCore *core;
        uint32_t event_id;
        VisibilityEvent(MeshDummyCore *core_, uint32_t event_)
            : Event(), core(core_), event_id(event_)
        {
            setFlags(AutoDelete);
        }
        void process() override { core->onEventVisible(event_id); }
        const char *description() const override
        {
            return "ai_mesh.core.visibility";
        }
    };

    struct GenerationEvent : public Event
    {
        MeshDummyCore *core;
        uint16_t stream_id;
        GenerationEvent(MeshDummyCore *core_, uint16_t stream_)
            : Event(), core(core_), stream_id(stream_)
        {
            setFlags(AutoDelete);
        }
        void process() override { core->onGenerationDrained(stream_id); }
        const char *description() const override { return "ai_mesh.core.generation"; }
    };

    struct CompletionEvent : public Event
    {
        MeshDummyCore *core;
        uint32_t command_id;
        uint32_t signal_event;
        bool error_terminal;
        bool result_write;
        bool result_serviced = false;

        CompletionEvent(MeshDummyCore *core_, uint32_t command_id_,
                        uint32_t signal_event_, bool error_terminal_ = false,
                        bool result_write_ = false)
            : Event(), core(core_), command_id(command_id_),
              signal_event(signal_event_), error_terminal(error_terminal_),
              result_write(result_write_)
        {
            setFlags(AutoDelete);
        }

        void process() override;
        const char *description() const override { return "ai_mesh.core.complete"; }
    };

    struct TickEvent : public Event
    {
        MeshDummyCore *core;
        explicit TickEvent(MeshDummyCore *core_) : Event(), core(core_) {}
        void process() override { core->tick(); }
        const char *description() const override { return "ai_mesh.core.tick"; }
    };

    static bool fenceScopeMatches(const DmaTagInfo &info, uint16_t scope);

    void tick();
    void scheduleTick();
    void issueRepeat(const DecodedCommand &command, const DecodedAttr *attr);
    void resetSubrangeEvents(uint16_t stream_id);
    void scheduleGenerationCheck(uint16_t stream_id);
    void onGenerationDrained(uint16_t stream_id);
    uint32_t liveStreamCommands(uint16_t stream_id) const;
    bool waitsSatisfied(const DecodedCommand &command) const;
    bool tryIssue(const DecodedCommand &command);
    void issueCompute(const DecodedCommand &command, const DecodedAttr *attr);
    uint64_t reserveOperandReads(const DecodedCommand &command);
    uint64_t operandView(const DecodedOperand &operand, uint64_t &offset,
                         uint64_t &span) const;
    Tick serviceResultWrite(uint32_t command_id);
    void issueControl(const DecodedCommand &command);
    bool issueDma(const DecodedCommand &command);
    uint64_t throughputFor(uint16_t dtype, const std::vector<uint64_t> &by_dtype,
                          uint64_t fallback) const;
        uint64_t computeCycles(const DecodedCommand &command, const DecodedAttr *attr) const;
    void completeCommand(uint32_t command_id, uint32_t signal_event, Tick done_tick,
                         bool error_terminal = false);
    void publishEvent(uint32_t event_id, uint32_t participant = 0,
                       uint32_t generation = 0);
    void cancelAllPendingVisibility();
    void onEventVisible(uint32_t event_id);
    void cancelPendingVisibility(uint32_t event_id);
    void finishIfHalted();
    void cancelRemainingCommands();
    bool tryPinDmaAllocations(const DecodedCommand &command);
    void unpinDmaAllocations(const DecodedCommand &command);
    void checkOperandValidity(const DecodedCommand &command);
    bool readsResultOperand(const DecodedCommand &command) const;
    void markOperandValid(const DecodedCommand &command);
    uint32_t allocationOf(uint32_t allocation_id);

    const DecodedAttr *attrOf(const DecodedCommand &command) const;

    uint16_t core_id_value;
    uint32_t decode_width_value;
    uint32_t admit_window_value;
    Cycles event_visibility;
    bool reference_compute_forbidden;
    uint32_t tensor_queue_depth_value;
    Cycles tensor_setup;
    Cycles tensor_flush;
    uint64_t tensor_macs_per_cycle_value;
    std::vector<uint64_t> tensor_macs_by_dtype_value;
    uint32_t vector_queue_depth_value;
    uint64_t vector_elements_per_cycle_value;
    std::vector<uint64_t> vector_elements_by_dtype_value;
    uint32_t reduce_queue_depth_value;
    Cycles reduce_setup;
    Cycles reduce_flush;
    uint64_t reduce_ops_per_cycle_value;
    std::vector<uint64_t> reduce_ops_by_dtype_value;

    DmaEngineBase *dma;
    MeshDispatcher *dispatcher = nullptr;
    std::shared_ptr<const DecodedProgram> program;
    const uint32_t bank_queue_depth_value;
    TensorSram sram;

    // Scheduler state.
    struct StreamCursor
    {
        uint32_t next_command = 0; // index within the whole command table
        uint32_t end_command = 0;
        // REPEAT admit gate: while a REPEAT is draining its generations the
        // cursor must not decode post-REPEAT commands (spec 4.1).
        uint32_t repeat_gate_command = 0; // 0 = open
        uint32_t post_repeat_next = 0; // cursor resume point after REPEAT
        struct ReplayCursor
        {
            uint32_t repeat_command_index = 0;
            uint32_t subrange_begin = 0;
            uint32_t subrange_end = 0;
            uint32_t generation = 0;
            uint32_t total_generations = 0;
            bool active = false;
        } replay;
    };
    std::map<uint16_t, StreamCursor> cursors; // stream_id -> cursor
    std::map<uint32_t, bool> signaled;         // event visibility snapshot
    std::map<uint32_t, uint32_t> barrier_arrivals;
    std::map<uint32_t, uint32_t> recv_waiters; // transfer_id -> pending command
    std::map<uint32_t, bool> committed_transfers;
    std::map<uint32_t, Event *> pending_visibility;
    uint32_t live_commands = 0;
    ProgramScoreboard *scoreboard = nullptr;
    std::map<uint16_t, uint32_t> live_per_stream;
    uint32_t outstanding_axi = 0;
    // AXI_FENCE waits on the exact tag set of DMA commands accepted before
    // the fence (spec 5.6); post-fence submissions never extend the wait.
    std::map<uint32_t, std::set<uint64_t>> fence_waiters;
    std::set<uint64_t> live_dma_tags;
    std::map<uint64_t, DmaTagInfo> dma_tag_info;
    std::map<uint64_t, uint32_t> dma_tag_command;
    std::map<uint32_t, uint64_t> dma_command_tag;
    uint64_t next_dma_tag = 1;
    bool core_halted = false;
    bool instance_active = false;
    bool instance_error = false;
    bool error_drained = false;
    Tick error_latch_tick = 0;
    Tick work_drained_tick = 0;
    std::map<uint32_t, uint32_t> allocation_pins;
    uint32_t tensor_queue_used = 0;
    uint32_t vector_queue_used = 0;
    uint32_t reduce_queue_used = 0;
    TickEvent tick_event;
};

} // namespace ai_mesh
} // namespace gem5

#endif

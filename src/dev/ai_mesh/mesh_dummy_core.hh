#ifndef DEV_AI_MESH_MESH_DUMMY_CORE_HH
#define DEV_AI_MESH_MESH_DUMMY_CORE_HH

#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <utility>
#include <vector>

#include "dev/ai_mesh/dma_types.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_compute_commit.hh"
#include "dev/ai_mesh/mesh_moe_gate.hh"
#include "dev/ai_mesh/mesh_moe_runtime.hh"
#include "dev/ai_mesh/mesh_weight_cache.hh"
#include "dev/ai_mesh/mesh_weight_tags.hh"
#include "dev/ai_mesh/runtime_key.hh"
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
class MeshDummyCore : public ClockedObject, public MoeOverlayComputePort
,
                       public MoeOverlayDmaPort
{
  public:
    using Params = MeshDummyCoreParams;

    MeshDummyCore(const Params &p);

    void startup() override;
    void regStats() override;

    // Loader interface: install the immutable command ROM views.
    void installProgram(const std::shared_ptr<const DecodedProgram> &program);

    // Dispatcher interface.
    void dispatchInstance(InstanceGeneration instance);
    void armRequest(InstanceGeneration instance);
    void startRequest();
    void disarmRequest();
    mesh_abi::MeshCoreInstanceState instanceState() const
    {
        return instance_state;
    }
    bool instanceActive() const
    {
        switch (instance_state) {
          case mesh_abi::MeshCoreInstanceState::REQUEST_ARMED:
          case mesh_abi::MeshCoreInstanceState::REQUEST_RUNNING:
          case mesh_abi::MeshCoreInstanceState::REQUEST_DRAINING:
          case mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINING:
          case mesh_abi::MeshCoreInstanceState::INSTANCE_OWNED_WORK_DRAINED:
            return true;
          default:
            return false;
        }
    }
    bool instanceErrored() const
    {
        switch (instance_state) {
          case mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINING:
          case mesh_abi::MeshCoreInstanceState::INSTANCE_OWNED_WORK_DRAINED:
          case mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINED:
            return true;
          default:
            return false;
        }
    }
    bool halted() const
    {
        return instance_state ==
                   mesh_abi::MeshCoreInstanceState::INSTANCE_DONE ||
               instance_state ==
                   mesh_abi::MeshCoreInstanceState::INSTANCE_ERROR_DRAINED;
    }
    // Quiescence includes the publication drain: a halted core with a
    // scheduled-but-invisible signal is not quiet (spec: HALT waits for
    // event publication drain).
    bool quiescent() const
    {
        // A core whose arm was undone legitimately holds no work: PROGRAM_READY
        // means no instance was ever started, not that work is outstanding.
        return live_commands == 0 && pending_visibility.empty() &&
               !overlayPending() &&
               (instance_state ==
                    mesh_abi::MeshCoreInstanceState::PROGRAM_READY ||
                instance_state ==
                    mesh_abi::MeshCoreInstanceState::REQUEST_DRAINING ||
                halted());
    }

    // A published overlay that has not drained is outstanding work: the
    // instance cannot finish, and the core keeps ticking its executor until
    // the group exit is published (contract 7.9.2).
    bool tickScheduled() const { return tick_event.scheduled(); }

    // Monotonic work counter for the progress watchdog: static and overlay
    // command completions both count, so a gated stream waiting on its
    // overlay is not mistaken for a hang.
    uint64_t workProgress() const
    {
        uint64_t progress = commandsCompleted.value() + cache_fills_completed;
        for (const auto &kv : overlay_executors)
            progress += kv.second->completedCommands();
        return progress;
    }

    bool overlayPending() const
    {
        for (const auto &kv : overlay_executors)
            if (kv.second->started() && !kv.second->drained())
                return true;
        return false;
    }

    // DMA engine callbacks.
    struct DmaTagInfo
    {
        uint16_t kind;
        uint16_t dst_space;
    };

    void completeFence(RuntimeObjectKey command,
                       RuntimeObjectKey signal_event);
    void onInstanceError(Tick tick);
    void latchInstanceError(Tick tick);
    void onDmaCompleted(RuntimeObjectKey command,
                        RuntimeObjectKey completion_event,
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
    void onTransferCommitted(RuntimeObjectKey transfer);
    void notifyPeerCommit(uint16_t peer_core, RuntimeObjectKey transfer);

    // Functional SRAM accessors used by the transports.
    bool functionalSramRead(uint64_t offset, uint64_t size, uint8_t *out);
    bool functionalSramWrite(uint64_t offset, uint64_t size, const uint8_t *in);

    // DMA engines validate the local source operand before reading SRAM.
    void checkDmaSourceValidity(RuntimeObjectKey command);

    // SRAM bank/port reservation (timing + accounting) for callers outside
    // the core (DMA engines folding service stalls into commit ticks).
    // Reserve the LOCAL endpoint of a DMA descriptor and return its stall.
    bool dmaSramAdmissible(const DecodedDmaDescriptor &descriptor);
    uint64_t reserveDmaSram(const DecodedDmaDescriptor &descriptor, bool is_write);
    std::optional<TensorSram::ReserveResult> tryReserveSramService(
        uint64_t offset, uint64_t size, bool write)
    {
        auto result = sram.tryReserve(curTick(), offset, size, write);
        if (result) {
            sramServiceCycles += result->service_ticks / clockPeriod();
            sramBankConflicts += result->conflict_ticks / clockPeriod();
        }
        return result;
    }
    void setSramBacking(SramBacking *backing) { sram.setBacking(backing); }

    void setDispatcher(MeshDispatcher *dispatcher);
    InstanceGeneration instanceGeneration() const
    {
        return instance_generation;
    }
    RuntimeObjectKey commandKey(uint32_t wire_id) const
    {
        return staticProgramObject(
            instance_generation, mesh_abi::MeshObjectKind::COMMAND, wire_id);
    }
    RuntimeObjectKey eventKey(uint32_t wire_id) const
    {
        return staticProgramObject(
            instance_generation, mesh_abi::MeshObjectKind::EVENT, wire_id);
    }
    RuntimeObjectKey transferKey(uint32_t wire_id) const
    {
        return staticProgramObject(
            instance_generation, mesh_abi::MeshObjectKind::TRANSFER, wire_id);
    }
    RuntimeObjectKey descriptorKey(uint32_t wire_id) const
    {
        return staticProgramObject(instance_generation,
                                   mesh_abi::MeshObjectKind::DESCRIPTOR,
                                   wire_id);
    }
    RuntimeObjectKey allocationKey(uint32_t wire_id) const
    {
        return staticProgramObject(instance_generation,
                                   mesh_abi::MeshObjectKind::ALLOCATION,
                                   wire_id);
    }
    // MoE insertion gate (contract 7.2.2): arm the layer's region before the
    // instance starts, release it when the overlay group exit is published.
    void armRegionGate(const MoeInsertionGate::RegionSpec &spec)
    {
        region_gate.arm(spec);
    }
    void releaseOverlayGroup(uint32_t layer_id)
    {
        region_gate.release(layer_id);
    }
    bool overlayGateArmed(uint32_t layer_id) const
    {
        return region_gate.layerArmed(layer_id);
    }
    MoeInsertionGate::RegionPhase overlayGatePhase(uint32_t layer_id) const
    {
        return region_gate.phase(layer_id);
    }
    std::string overlayGateState() const { return region_gate.describe(); }
    void clearRegionGates() { region_gate = MoeInsertionGate(); }

    // Table index of a static command id, used to place a region's resume
    // point in the decode cursor's own coordinate space.
    uint32_t commandIndex(uint32_t command_id) const
    {
        for (uint32_t index = 0; index < program->commands.size(); index++)
            if (program->commands[index].command_id == command_id)
                return index;
        fatal("core %u has no command %u", core_id_value, command_id);
    }

    // Overlay execution (contract 7.9.2): the core owns one executor per
    // layer, ticks it next to the static decode loop, and reports the group
    // exit so the dispatcher can release the insertion gate.
    // Runtime weight cache (main contract 7.8): installed by the loader under
    // the cached policy; fills leave through the same DMA engine the overlay
    // uses, so they share the finite descriptor queues and the arbiter.
    void installWeightCache(std::unique_ptr<MoeWeightCache> cache);
    MoeWeightCache *weightCache() const { return weight_cache.get(); }
    void installCacheTokens(
        const std::vector<MoeCacheToken> &tokens,
        const std::map<uint32_t, std::map<uint32_t, uint32_t>> &consumers);
    void retryCacheReservations();
    uint64_t cacheBatchId() const { return cache_batch_id; }
    uint64_t cacheFillSource(uint32_t tag_index) const;
    void setDmaGeometry(uint16_t sram_region_id, uint32_t max_burst_beats);
    uint64_t cacheFillBytes(uint32_t tag_index) const;
    uint32_t cacheFillsIssued() const { return cache_fills_issued; }
    uint64_t overlayEntryTick() const { return overlay_entry_tick; }
    uint32_t cacheFillsCompleted() const { return cache_fills_completed; }
    uint32_t cacheFillsErrored() const { return cache_fills_errored; }
    uint32_t cacheFillsRetried() const { return cache_fills_retried; }

    bool submitOverlayDma(const mesh_abi::DmaDescriptor &descriptor,
                          const RuntimeObjectKey &descriptor_key,
                          const RuntimeObjectKey &command_key,
                          uint64_t issue_tick) override;
    void bindFillContent(const RuntimeObjectKey &command,
                         const std::vector<uint8_t> &content,
                         bool install_bytes)
    {
        dma->bindFillContent(command, content, install_bytes);
    }

    void bindFillPattern(const RuntimeObjectKey &command,
                         uint64_t pattern) override;
    const WeightTagSiteV1 &weightTagSite(uint32_t tag_index) const;

    void installOverlay(uint32_t layer_id, const MoeOverlayGraph &graph,
                        const MoeOverlayAddressSpace *addresses = nullptr);
    void setOverlayBus(MoeOverlayEventBus *event_bus)
    {
        overlay_bus = event_bus;
        for (auto &kv : overlay_executors)
            kv.second->setBus(event_bus);
    }
    bool overlayDrained() const;
    bool overlayGroupExited(uint32_t layer_id) const;
    const MoeOverlayExecutor *overlayExecutor(uint32_t layer_id) const
    {
        auto it = overlay_executors.find(layer_id);
        return it == overlay_executors.end() ? nullptr : it->second.get();
    }

    void setScoreboard(ProgramScoreboard *board) { scoreboard = board; }
    uint16_t archCoreId() const { return core_id_value; }
    DmaEngineBase *dmaEngine() const { return dma; }
    Tick errorLatchTick() const { return error_latch_tick; }
    Tick workDrainedTick() const { return work_drained_tick; }
    const std::map<RuntimeObjectKey, Tick> &commandDoneTicks() const
    {
        return command_done_ticks;
    }
    const std::map<RuntimeObjectKey, Tick> &commandIssueTicks() const
    {
        return command_issue_ticks;
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
    uint64_t sramReservationRejectionAttempts(bool is_write) const
    {
        return sram.reservationRejectionAttempts(is_write);
    }

    statistics::Scalar sramBankConflicts;
    statistics::Scalar sramServiceCycles;
    statistics::Scalar poisonReadFaults;
    std::vector<ComputeDigest> computeDigests;
    std::vector<RuntimeObjectKey> completed_command_ids;
    struct InstanceLedger
    {
        std::vector<RuntimeObjectKey> completed;
        std::vector<RuntimeObjectKey> errored;
        std::vector<RuntimeObjectKey> cancelled;
    };
    InstanceLedger takeInstanceLedger();

    std::vector<RuntimeObjectKey> instance_completed_ids;
    std::vector<RuntimeObjectKey> instance_errored_ids;
    std::vector<RuntimeObjectKey> instance_cancelled_ids;
    std::vector<RuntimeObjectKey> errored_command_ids;
    std::vector<RuntimeObjectKey> cancelled_command_ids;
    std::map<RuntimeObjectKey, Tick> command_done_ticks;
    std::map<RuntimeObjectKey, Tick> command_issue_ticks;

  private:
    struct VisibilityEvent : public Event
    {
        MeshDummyCore *core;
        RuntimeObjectKey event;
        VisibilityEvent(MeshDummyCore *core_, RuntimeObjectKey event_)
            : Event(), core(core_), event(event_)
        {
            setFlags(AutoDelete);
        }
        void process() override { core->onEventVisible(event); }
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
        RuntimeObjectKey command;
        RuntimeObjectKey signal_event;
        bool error_terminal;
        bool result_write;
        bool result_serviced = false;

        CompletionEvent(MeshDummyCore *core_, RuntimeObjectKey command_,
                        RuntimeObjectKey signal_event_,
                        bool error_terminal_ = false,
                        bool result_write_ = false)
            : Event(), core(core_), command(command_),
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
    Tick serviceResultWrite(RuntimeObjectKey command);
    void issueControl(const DecodedCommand &command);
    bool issueDma(const DecodedCommand &command);
    // MoeOverlayComputePort: contract cycles for one overlay engine command
    // resolved from the frozen kernel spec of the layer.
    uint64_t computeCycles(const MoeComputeShape &shape) const override;
    bool weightSlotRange(uint32_t tag_index, uint64_t &offset,
                         uint64_t &bytes) const override;
    bool computeAdmissible(uint16_t opcode) const override;
    void noteComputeAdmitted(uint16_t opcode) override;
    void noteComputeFinished(uint16_t opcode, uint64_t cycles) override;
    void noteWeightConsumed(uint32_t layer_id, uint32_t tag_index) override;

    uint64_t throughputFor(uint16_t dtype, const std::vector<uint64_t> &by_dtype,
                          uint64_t fallback) const;
        uint64_t computeCycles(const DecodedCommand &command, const DecodedAttr *attr) const;
    void completeCommand(RuntimeObjectKey command_key,
                         RuntimeObjectKey signal_event, Tick done_tick,
                         bool error_terminal = false);
    void publishEvent(RuntimeObjectKey event,
                      RuntimeObjectKey participant = RuntimeObjectKey(),
                      RepeatGeneration generation = RepeatGeneration());
    void cancelAllPendingVisibility();
    void onEventVisible(RuntimeObjectKey event);
    void cancelPendingVisibility(RuntimeObjectKey event);
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
    MoeInsertionGate region_gate;
    MoeOverlayEventBus *overlay_bus = nullptr;
    std::map<uint32_t, std::unique_ptr<MoeOverlayExecutor>> overlay_executors;
    std::map<uint32_t, const mesh_abi::MoeKernelSpec *> overlay_kernels;
    std::set<uint32_t> overlay_exited;
    std::set<uint32_t> overlay_entry_published;
    uint32_t dmaBytesPerCycle = 32;
    std::unique_ptr<MoeWeightCache> weight_cache;
    std::vector<WeightTagSiteV1> weight_tag_sites;
    uint32_t cache_fills_issued = 0;
    uint64_t overlay_entry_tick = 0;
    uint32_t cache_fills_completed = 0;
    uint32_t cache_fills_errored = 0;
    uint32_t cache_fills_retried = 0;
    uint16_t sram_region_id = 0;
    uint32_t dma_max_burst_beats = 0;
    struct PendingCacheFill
    {
        mesh_abi::WeightFillKey fill;
        uint64_t bytes = 0;
        uint64_t token_id = 0;
    };
    std::map<uint32_t, PendingCacheFill> pending_cache_fills;
    struct QueuedCacheFill
    {
        mesh_abi::DmaDescriptor descriptor;
        RuntimeObjectKey descriptor_key;
        mesh_abi::WeightFillKey fill;
        PendingCacheFill pending;
    };
    std::map<uint32_t, QueuedCacheFill> queued_cache_fills;
    void retryQueuedCacheFills();
    uint64_t cache_batch_id = 1;
    bool owned_work_drain_pending = false;

    uint32_t regionGateRegionId(uint32_t layer_id) const;
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
        RuntimeObjectKey repeat_gate_command; // ordinal 0 = open
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
    std::map<RuntimeObjectKey, RuntimeObjectKey> recv_waiters;
    std::map<RuntimeObjectKey, bool> committed_transfers;
    std::map<RuntimeObjectKey, Event *> pending_visibility;
    uint32_t live_commands = 0;
    ProgramScoreboard *scoreboard = nullptr;
    std::map<uint16_t, uint32_t> live_per_stream;
    uint32_t outstanding_axi = 0;
    // AXI_FENCE waits on the exact tag set of DMA commands accepted before
    // the fence (spec 5.6); post-fence submissions never extend the wait.
    std::map<RuntimeObjectKey, std::set<uint64_t>> fence_waiters;
    std::set<uint64_t> live_dma_tags;
    std::map<uint64_t, DmaTagInfo> dma_tag_info;
    std::map<uint64_t, RuntimeObjectKey> dma_tag_command;
    std::map<RuntimeObjectKey, uint64_t> dma_command_tag;
    uint64_t next_dma_tag = 1;
    InstanceGeneration instance_generation;
    mesh_abi::MeshCoreInstanceState instance_state =
        mesh_abi::MeshCoreInstanceState::PROGRAM_READY;
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

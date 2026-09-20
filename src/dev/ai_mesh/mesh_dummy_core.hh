#ifndef DEV_AI_MESH_MESH_DUMMY_CORE_HH
#define DEV_AI_MESH_MESH_DUMMY_CORE_HH

#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "dev/ai_mesh/dma_command_lifecycle.hh"
#include "dev/ai_mesh/dma_types.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_runtime_observations.hh"
#include "dev/ai_mesh/tensor_sram.hh"
#include "base/statistics.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

struct MeshDummyCoreParams;

namespace ai_mesh
{

class CommandRom;
class DmaEngineBase;
class MeshDispatcher;
class MeshInvocationBinding;
class MeshProgramAdmission;
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

    // Loader interface: install the admitted program, this core's
    // selected-variant command ROM and the resolved dispatch invocation.
    void installAdmission(
        const std::shared_ptr<const MeshProgramAdmission> &admission,
        const std::shared_ptr<const CommandRom> &rom,
        const std::shared_ptr<const MeshInvocationBinding> &invocation);

    // Dispatcher interface.
    void dispatchInstance(uint32_t instance_id);
    bool halted() const { return core_halted || error_drained; }
    // Quiescence must cover the whole physical drain (spec 8.8): every
    // admitted command and descriptor, the DMA engine, outstanding AXI tags
    // and the publication queue.  A halted core with in-flight DMA is not
    // quiescent.
    bool quiescent() const
    {
        return live_commands == 0 && pending_visibility.empty() &&
               dmaDrained() && (core_halted || error_drained);
    }
    bool dmaDrained() const;
    uint64_t progressSnapshot() const;
    std::string waitForGraph() const;
    uint32_t liveCommandCount() const { return live_commands; }
    using EngineKind = ai_mesh::EngineKind;
    static const char *engineName(EngineKind kind);
    struct EngineSlotState
    {
        uint32_t depth = 0;
        std::map<uint32_t, uint32_t> owners;
    };
    const RuntimeObservations &runtimeObservations() const
    {
        return observations;
    }
    uint32_t engineOccupancy(EngineKind kind) const
    {
        auto slot = engine_slots.find(kind);
        return slot == engine_slots.end() ? 0 : slot->second.owners.size();
    }

    size_t liveDmaTagCount() const { return live_dma_tags.size(); }
    uint32_t tagOwner(uint64_t tag) const
    {
        auto it = dma_tag_command.find(tag);
        return it == dma_tag_command.end() ? 0 : it->second;
    }
    size_t allocationPinCount() const
    {
        size_t total = 0;
        for (const auto &entry : allocation_pins)
            total += entry.second;
        return total;
    }
    size_t liveDmaCommandCount() const
    {
        size_t total = 0;
        for (const auto &entry : dma_lifecycle.states())
            if (!entry.second.terminal)
                total++;
        return total;
    }

    // Per-command DMA admission over a possibly multi-descriptor completion
    // group (spec 7.1/8.3): descriptors are submitted incrementally under
    // backpressure and the command completes only when the last one commits.
    // A lifecycle record exists exactly while the command holds its
    // allocation pins, so record existence is the single source of
    // "admitted"; a command that cannot pin has no record at all.
    // Declared before the accessors and hooks that use it.
    // DMA engine callbacks.
    struct DmaTagInfo
    {
        uint16_t kind;
        uint16_t dst_space;
    };

    void completeFence(uint32_t command_id, uint32_t signal_event);
    void onInstanceError(Tick tick);
    void onDmaCompleted(uint32_t command_id, uint32_t descriptor_id,
                        uint32_t completion_event, Tick commit_tick,
                        DmaStatus status);
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
    void markAllocationValid(uint32_t allocation_id);
    const DecodedDmaDescriptor *descriptorById(uint32_t descriptor_id) const;
    void notifyPeerCommit(uint16_t peer_core, uint32_t transfer_id);

    // Functional SRAM accessors used by the transports.
    bool functionalSramRead(uint64_t offset, uint64_t size, uint8_t *out);
    bool functionalSramWrite(uint64_t offset, uint64_t size, const uint8_t *in);

    // Admitted absolute endpoint address of a descriptor: region base,
    // per-core SRAM tile base, allocation offset and shard offset are already
    // folded in.  Runtime consumers use this instead of re-deriving addresses
    // from the raw descriptor offsets.
    uint64_t admittedEndpointAddress(uint32_t descriptor_id, bool source) const;
    // The same endpoint expressed as this core's tile-relative SRAM offset:
    // the absolute admitted address minus the region's per-core tile base, so
    // every local SRAM consumer converts exactly once.
    uint64_t admittedLocalOffset(uint32_t descriptor_id, bool source) const;
    bool admissionBound() const { return admission != nullptr; }

    // Admitted destination allocation span of a descriptor.
    std::optional<DestinationStorageObservation>
    admittedDestinationStorage(uint32_t descriptor_id) const;

    // DMA engines validate the local source operand before reading SRAM.
    void checkDmaSourceValidity(uint32_t command_id);

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
    MeshDispatcher *runtimeDispatcher() const { return dispatcher; }
    void setScoreboard(ProgramScoreboard *board) { scoreboard = board; }
    uint16_t archCoreId() const { return core_id_value; }
    DmaEngineBase *dmaEngine() const { return dma; }
    bool instanceErrored() const { return instance_error; }
    Tick errorLatchTick() const { return error_latch_tick; }
    Tick workDrainedTick() const { return work_drained_tick; }

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

    // One command of one REPEAT generation.  A plain command is dispatched in
    // generation 0; a REPEAT subrange member is dispatched once per generation
    // 0..repeat_count-1 (spec 4.1 sequential generations).
    using CommandGeneration = ai_mesh::CommandGeneration;
    enum class TerminalState
    {
        Completed,
        Errored,
        Cancelled,
    };
    struct TerminalRecord
    {
        uint32_t command_id = 0;
        uint32_t generation = 0;
        TerminalState state = TerminalState::Completed;
    };
    // The terminal ledger is the single source of the per-command/generation
    // outcome: every command of the instance, exactly once, in exactly one
    // state (spec 8.8/12.3.12).
    std::vector<CommandGeneration> dispatchPlan() const;
    struct ResourceSnapshot
    {
        uint32_t live_commands = 0;
        uint32_t live_dma_commands = 0;
        uint32_t live_dma_tags = 0;
        uint32_t allocation_pins = 0;
        std::vector<std::pair<EngineKind, uint32_t>> engine_occupancy;
    };
    struct InstanceLedger
    {
        std::vector<TerminalRecord> terminals;
        ResourceSnapshot resources;
        ObservationFrame observations;
    };
    InstanceLedger takeInstanceLedger();
    ObservationFrame currentObservationFrame() const
    {
        return observations.view();
    }
    const std::vector<TerminalRecord> &currentTerminals() const
    {
        return instance_terminals;
    }
    ResourceSnapshot currentResources() const;
    std::map<uint32_t, Tick> commandIssueTicks() const;
    std::map<uint32_t, Tick> commandDoneTicks() const;
    std::vector<ComputeOutputObservation> computeOutputs() const;

    RuntimeObservations observations;
    std::optional<DescriptorKey> frozenDescriptorKey(uint32_t command_id,
                                                     uint32_t descriptor_id) const;
    void recordDescriptorSubmission(const DescriptorKey &key, Tick submit_tick,
                                    Tick scheduled_completion_tick);
    void recordDescriptorCompletion(const DescriptorKey &key,
                                    Tick completion_tick, bool committed,
                                    Tick commit_tick, DmaStatus status,
                                    const TrafficContribution &transfer);
    struct TransferCommitContent
    {
        std::string source_digest;
        std::string target_digest;
        std::string target_initial_digest;
    };
    void recordTransferCommit(const DecodedDmaDescriptor &descriptor,
                              const TransferCommitContent &content);
    bool transferCommitted(uint32_t transfer_id) const;
    uint32_t receivedAllocation(uint32_t transfer_id) const;
    bool allocationValid(uint32_t allocation_id) const;
    uint32_t receiveNotificationCount(uint32_t transfer_id) const;
    Tick receiveNotificationTick(uint32_t transfer_id) const;
    void recordFillLanding(
        const DescriptorKey &key, const std::string &landing_digest,
        const DestinationStorageObservation &destination_storage,
        const std::vector<SentinelRangeObservation> &sentinel_ranges);
    void recordDescriptorSourceRows(
        const DescriptorKey &key,
        const std::vector<ContentRowObservation> &rows);
    void recordFaultTarget(const DescriptorKey &key,
                           const std::string &before_digest,
                           const std::string &after_digest);

  private:
    struct StreamCursor;
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
    void finalizeRepeat(StreamCursor &cursor);
    void resetSubrangeEvents(uint16_t stream_id);
    void scheduleGenerationCheck(uint16_t stream_id);
    void onGenerationDrained(uint16_t stream_id);
    uint32_t liveStreamCommands(uint16_t stream_id) const;
    bool waitsSatisfied(const DecodedCommand &command) const;
    // One issue attempt: whether the command instance is now owned by its
    // lifecycle (and therefore has a dispatch generation), and whether the
    // decode cursor may move past it.  A partially submitted DMA command is
    // dispatched but holds the cursor until its group is fully submitted.
    struct IssueOutcome
    {
        bool dispatched = false;
        bool resume = false;
    };
    IssueOutcome tryIssue(const DecodedCommand &command, uint32_t generation);
    bool admitWindowBlocked(const StreamCursor &cursor,
                            const DecodedCommand &command,
                            uint32_t generation) const;
    IssueOutcome admitEngine(const DecodedCommand &command, EngineKind kind,
                             uint32_t generation);
    void releaseEngineSlot(EngineKind kind, uint32_t command_id,
                           uint32_t generation);
    void recordEngineBlock(EngineKind kind, uint32_t command_id,
                           uint32_t generation);
    const ObservationFrame *archivedObservationFrame() const;
    void issueCompute(const DecodedCommand &command, const DecodedAttr *attr,
                      EngineKind kind, uint32_t generation);
    uint64_t reserveOperandReads(const DecodedCommand &command);
    uint64_t operandView(const DecodedOperand &operand, uint64_t &offset,
                         uint64_t &span) const;
    Tick serviceResultWrite(uint32_t command_id);
    void issueControl(const DecodedCommand &command, uint32_t generation);
    bool issueDma(const DecodedCommand &command, uint32_t generation);
    uint32_t generationAt(uint16_t stream_id, uint32_t index) const;
    uint32_t issuedGeneration(uint32_t command_id) const;
    void recordTerminal(uint32_t command_id, uint32_t generation,
                        TerminalState state, Tick terminal_tick);
    std::string ledgerMismatch() const;
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
    void cancelDmaCommand(uint32_t command_id);
    void finalizeDmaCommand(uint32_t command_id);
    bool tryPinDmaAllocations(const DecodedCommand &command);
    void unpinDmaAllocations(const DecodedCommand &command);
    void checkOperandValidity(const DecodedCommand &command);
    bool readsResultOperand(const DecodedCommand &command) const;
    void markOperandValid(const DecodedCommand &command);
    void installInitialResidency();
    std::vector<uint32_t> residentAllocations() const;
    void raiseInvalidResidency(uint32_t command_id, uint32_t allocation_id,
                               uint32_t operand_index, bool dma);
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
    std::shared_ptr<const MeshProgramAdmission> admission;
    std::shared_ptr<const CommandRom> command_rom;
    std::shared_ptr<const MeshInvocationBinding> invocation_binding;
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
    std::map<uint32_t, uint32_t> recv_waiters; // transfer_id -> pending command
    std::map<uint32_t, bool> committed_transfers;
    std::map<uint32_t, uint32_t> receive_notifications;
    std::map<uint32_t, Tick> receive_notification_ticks;
    std::map<uint32_t, uint32_t> received_allocations;
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
    DmaCommandLifecycle dma_lifecycle;
    std::map<uint32_t, uint64_t> dma_descriptor_tag;
    // Terminal ledger of the current instance and the dispatch generation of
    // every command it issued, keyed by command id.
    std::vector<TerminalRecord> instance_terminals;
    std::map<uint32_t, uint32_t> command_generations;
    // The never-dispatched part of the plan is logically cancelled once per
    // instance; later cancellation passes only drain physically issued work.
    bool plan_instances_cancelled = false;
    uint64_t next_dma_tag = 1;
    bool core_halted = false;
    bool instance_active = false;
    bool instance_error = false;
    bool error_drained = false;
    Tick error_latch_tick = 0;
    Tick work_drained_tick = 0;
    std::map<uint32_t, uint32_t> allocation_pins;
    std::map<EngineKind, EngineSlotState> engine_slots;
    TickEvent tick_event;
};

} // namespace ai_mesh
} // namespace gem5

#endif

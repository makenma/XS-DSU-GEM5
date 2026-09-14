#ifndef DEV_AI_MESH_MESH_MOE_RUNTIME_HH
#define DEV_AI_MESH_MESH_MOE_RUNTIME_HH

#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_compute_commit.hh"
#include "dev/ai_mesh/mesh_moe_overlay.hh"
#include "dev/ai_mesh/program_scoreboard.hh"
#include "dev/ai_mesh/runtime_key.hh"
#include "dev/ai_mesh/tensor_sram.hh"

namespace gem5
{
namespace ai_mesh
{

class MeshDummyCore;

class MoeOverlayExecutor;

// One materialized overlay command as the runtime consumes it: typed keys
// for waits and signals, plus the service geometry the existing SRAM, DMA
// and engine paths need.
struct MoeRuntimeCommand
{
    RuntimeObjectKey key;
    uint16_t opcode = 0;
    uint16_t phase = 0;
    uint16_t role = 0;
    uint16_t owner_core = 0;
    uint16_t src_core = 0xFFFF;
    uint16_t dst_core = 0xFFFF;
    uint16_t expert_id = 0xFFFF;
    uint32_t chunk_ordinal = 0;
    uint32_t payload_bytes = 0;
    uint32_t allocation_ordinal = 0;
    uint32_t descriptor_region = 0;
    uint32_t descriptor_ordinal = 0;
    std::vector<RuntimeObjectKey> waits;
    std::vector<RuntimeObjectKey> signals;
    // Typed overlay references the DMA descriptor needs (views and transfers
    // of the canonical object graph).
    std::vector<RuntimeObjectKey> view_refs;
    std::vector<RuntimeObjectKey> source_views;
    std::vector<RuntimeObjectKey> destination_views;

    bool isDma() const;
    bool isCompute() const;
    bool isTransfer() const;
};

// Cross-core overlay event delivery: a sender's P2P completion publishes the
// receiver's transfer event, mirroring MeshDummyCore::notifyPeerCommit.
class MoeOverlayEventBus
{
  public:
    void subscribe(uint16_t core_id, MoeOverlayExecutor *executor)
    {
        executors[core_id] = executor;
    }
    void unsubscribe(uint16_t core_id) { executors.erase(core_id); }
    void publish(uint16_t core_id, const RuntimeObjectKey &event) const;
    void clear() { executors.clear(); }

  private:
    std::map<uint16_t, MoeOverlayExecutor *> executors;
};

// Per-core overlay executor (contract 7.9.2).  Consumes the canonical object
// graph, issues every ready command through the shared scoreboard, charges
// SRAM service bytes, and reports when the region's drain join is satisfied
// so the coordinator can publish the group exit.
// Narrow port the overlay executor uses to hand its DMA work to whatever
// engine the core owns.  Keeping the seam here means the executor carries no
// gem5 simulation dependency and stays unit-testable with a stub port.
// Contract compute timing for overlay commands: the core owns the concrete
// kernel shape/dtype/throughput formula, the executor resolves the concrete
// operand geometry of the materialized command (accepted rows, contributor
// fan-in) and only asks for cycles.
struct MoeComputeShape
{
    uint32_t layer_id = 0;
    uint16_t opcode = 0;
    uint16_t role = 0;
    uint32_t payload_bytes = 0;
    uint32_t fan_in = 1;
};

class MoeOverlayComputePort
{
  public:
    virtual ~MoeOverlayComputePort() = default;
    virtual uint64_t computeCycles(const MoeComputeShape &shape) const = 0;
    // Absolute SRAM interval of a resident weight-cache line: a cached expert
    // reads the line through the same operand path as any other view.
    virtual bool weightSlotRange(uint32_t tag_index, uint64_t &offset,
                                 uint64_t &bytes) const = 0;
    // Finite engine admission: the overlay shares the core's tensor, vector
    // and reduce queues with the static program.
    virtual bool computeAdmissible(uint16_t opcode) const = 0;
    virtual void noteComputeAdmitted(uint16_t opcode) = 0;
    virtual void noteComputeFinished(uint16_t opcode, uint64_t cycles) = 0;
    // The weight line is pinned until the expert that reads it drains.
    virtual void noteWeightConsumed(uint32_t layer_id, uint32_t tag_index) = 0;
};

class MoeOverlayDmaPort
{
  public:
    virtual ~MoeOverlayDmaPort() = default;
    virtual bool submitOverlayDma(const mesh_abi::DmaDescriptor &descriptor,
                                  const RuntimeObjectKey &descriptor_key,
                                  const RuntimeObjectKey &command_key,
                                  uint64_t issue_tick) = 0;
    virtual void bindFillPattern(const RuntimeObjectKey &command,
                                 uint64_t pattern) = 0;
    // Exact contract content of a fill: the engine installs the bytes in
    // FUNCTIONAL_BYTES mode and only digests them in the other two modes.
    virtual void bindFillContent(const RuntimeObjectKey &command,
                                 const std::vector<uint8_t> &content,
                                 bool install_bytes)
    {
        (void)command;
        (void)content;
        (void)install_bytes;
    }
};

// Static addresses the overlay image itself cannot carry: program
// allocations referenced by static-backed views and the HBM base of each
// expert's streamed weight slice.
struct MoeOverlayAddressSpace
{
    std::map<uint32_t, std::pair<uint64_t, uint64_t>> allocations;
    std::map<uint32_t, std::pair<uint64_t, uint64_t>> expert_weights;
    uint16_t hbm_region = 0;
    uint16_t sram_region = 0;
    uint32_t max_burst_beats = 0;
};

class MoeOverlayExecutor
{
  public:
    struct Config
    {
        uint16_t core_id = 0;
        uint32_t dma_setup_cycles = 2;
        uint32_t dma_bytes_per_cycle = 32;
        uint32_t compute_cycles_per_row = 4;
        uint32_t compute_row_bytes = 1;
        uint32_t exit_signal_cycles = 1;
        // Ticks per core cycle: SRAM reservations are absolute-tick based, so
        // every service window is anchored in the core's real timeline and
        // the absolute tick itself arrives on each core edge.
        uint64_t ticks_per_cycle = 1;
    };

    MoeOverlayExecutor(const Config &config, ProgramScoreboard *scoreboard,
                       TensorSram *sram, const MoeOverlayEventBus *bus,
                       const MoeOverlayAddressSpace *addresses = nullptr);

    // Installs the object graph of one layer instance.  Only commands owned
    // by this core are executed; their typed keys come from the canonical
    // ordinals, so cross-core references resolve without extra mapping.
    void load(uint32_t layer_id, InstanceGeneration instance,
              const MoeOverlayGraph &graph);

    // Makes the static program's entry events visible and starts ticking from
    // the core edge that published them.
    void publishEntry(const RuntimeObjectKey &event, uint64_t core_tick);

    // Terminal DMA completion from the shared engine (the engine owns the
    // real AXI/Garnet timing, so the executor only retires the command).
    void completeDma(const RuntimeObjectKey &command, uint8_t status);

    // Outcome of handing one overlay DMA command to the shared engine.
    enum class DmaIssue
    {
        SUBMITTED,
        BACKPRESSURE,
        UNRESOLVABLE,
    };

    bool awaitingEngine() const { return !live_dma.empty(); }

    // The executor drives the shared DMA engine for its DMA commands; the
    // core injects the engine and the program-derived address space.
    void bindDma(MoeOverlayDmaPort *port) { dma = port; }

    // The core resolves compute cycles from the frozen kernel spec, so the
    // overlay never invents a second timing model; the shared commit path
    // logs the semantic digest of each overlay compute result.
    void bindCompute(MoeOverlayComputePort *port) { compute = port; }
    void bindComputeCommit(std::vector<ComputeDigest> *digests)
    {
        compute_digests = digests;
    }

    // Advances the executor to the core's absolute tick; returns true when it
    // made progress.
    bool tick(uint64_t core_tick);

    // Cross-core event arrival from the bus or the static program.
    void deliverEvent(const RuntimeObjectKey &event);

    bool drained() const
    {
        if (!loaded || !started_value)
            return false;
        if (failed_value)
            return live.empty();
        return finished.size() == commands.size() && live.empty();
    }

    // A failed DMA completion stops the overlay: no later command may be
    // started and no success event is published, so the region/group exit is
    // never claimed for an errored instance.
    bool failed() const { return failed_value; }
    // Error drain: abandon every live command without publishing success, so
    // an errored instance never claims an overlay exit.
    void abort()
    {
        live.clear();
        live_dma.clear();
        failed_value = true;
    }
    uint8_t failureStatus() const { return failure_status; }
    const RuntimeObjectKey &failedCommand() const { return failed_command; }
    bool started() const { return started_value; }
    uint64_t completedCommands() const { return completed_commands; }
    uint64_t liveCommands() const { return live.size(); }
    uint64_t issuedCommands() const { return issued_commands; }
    std::string describeBlocked() const;
    uint64_t sramReadBytes() const { return sram_read_bytes; }
    uint64_t sramWriteBytes() const { return sram_write_bytes; }
    uint64_t sramServiceCycles() const { return sram_service_cycles; }
    uint64_t sramBankConflicts() const { return sram_bank_conflicts; }
    uint64_t computeCyclesTotal() const { return compute_cycles; }
    uint32_t computeCommits() const { return compute_commits; }
    uint32_t copyThroughCommands() const { return copy_through_commands; }
    uint32_t localReduceCommands() const { return local_reduce_commands; }
    // A completed command's produced overlay view must carry its committed
    // bytes: zero proves every writer (DMA terminal or compute commit)
    // reached its output stage.
    uint32_t uncommittedViews() const;
    uint64_t sramReadBytesForRegion(uint32_t region_id) const
    {
        const auto it = sram_region_bytes.find(region_id);
        return it == sram_region_bytes.end() ? 0 : it->second.first;
    }
    uint64_t sramWriteBytesForRegion(uint32_t region_id) const
    {
        const auto it = sram_region_bytes.find(region_id);
        return it == sram_region_bytes.end() ? 0 : it->second.second;
    }
    // Service bytes of one region split by the view kind that was served, so
    // the independent oracle lanes can be checked operand by operand.
    const std::map<uint32_t, std::pair<uint64_t, uint64_t>> &
    sramKindBytesForRegion(uint32_t region_id) const
    {
        static const std::map<uint32_t, std::pair<uint64_t, uint64_t>> empty;
        const auto it = sram_region_kind_bytes.find(region_id);
        return it == sram_region_kind_bytes.end() ? empty : it->second;
    }
    const std::vector<RuntimeObjectKey> &signalled() const
    {
        return signal_log;
    }
    const std::vector<RuntimeObjectKey> &completedKeys() const
    {
        return completion_log;
    }
    uint32_t layerId() const { return layer_id_value; }
    const std::string &failure() const { return failure_reason; }

    void setBus(MoeOverlayEventBus *event_bus) { bus = event_bus; }

    void reset();

  private:
    // Compute commands walk read service -> engine window -> output write
    // service; each stage owns its own absolute completion tick, so a result
    // is never published before its output write really completed.
    enum class Stage
    {
        READS = 0,
        ENGINE = 1,
        WRITES = 2,
        RETIRE = 3,
    };

    struct Live
    {
        const MoeRuntimeCommand *command = nullptr;
        uint64_t complete_at = 0;
        uint64_t read_done_at = 0;
        uint64_t engine_done_at = 0;
        uint64_t write_done_at = 0;
        uint64_t charged_cycles = 0;
        Stage stage = Stage::RETIRE;
        size_t reads_done = 0;
        size_t writes_done = 0;
        std::vector<std::pair<const MoeOverlayEntry *, uint64_t>> sram_reads;
        std::vector<std::pair<const MoeOverlayEntry *, uint64_t>> sram_writes;
        // False while the shared engine still backpressures the command: the
        // service window starts when the work is really accepted.
        bool armed = false;
    };

    const MoeOverlayEntry *descriptorOf(const MoeRuntimeCommand &command) const;
    const MoeOverlayEntry *viewOf(const RuntimeObjectKey &ref) const;
    RuntimeObjectKey viewKey(uint32_t region_id, uint32_t ordinal) const;
    bool resolveView(const MoeOverlayEntry &view,
                     mesh_abi::DmaEndpoint &endpoint,
                     uint64_t &byte_count) const;
    DmaIssue submitDma(const MoeRuntimeCommand &command);
    DmaIssue submitPush(const MoeRuntimeCommand &command,
                        const MoeOverlayEntry &descriptor,
                        mesh_abi::DmaDescriptor &wire);
    const MoeOverlayEntry *peerDescriptor(const MoeOverlayEntry &descriptor)
        const;
    RuntimeObjectKey overlayDescriptorKey(const MoeOverlayEntry &entry) const;
    const MoeRuntimeCommand *readyCommand() const;
    // Local SRAM views of one command as (view, read, write, bytes) tuples:
    // DMA endpoints come from the referenced descriptor, compute operands from
    // the command's own view references, and the charged span is the operand
    // this command really consumes (one contributor row for a combine, the
    // whole view for an expert).  Views the image cannot address (weight cache
    // slots, instance member bindings) and peer-owned rows are not local
    // service.
    std::vector<std::tuple<const MoeOverlayEntry *, bool, bool, uint64_t>>
    localViews(const MoeRuntimeCommand &command) const;
    void accountSramBytes(const MoeRuntimeCommand &command,
                          const MoeOverlayEntry &view, uint64_t bytes,
                          bool is_write);
    bool reserveComputeSram(Live &entry, uint64_t now_tick);
    bool reserveComputeWrites(Live &entry, uint64_t now_tick);
    void accountDmaSram(const MoeRuntimeCommand &command);
    bool armLive(Live &entry, uint64_t now_tick);
    RuntimeObjectKey eventKey(const MoeOverlayEntry &entry) const;
    void startCommand(const MoeRuntimeCommand &command, uint64_t now);
    void completeCommand(const MoeRuntimeCommand &command,
                         uint64_t charged_cycles);
    MoeComputeShape computeShape(const MoeRuntimeCommand &command) const;
    uint64_t serviceCycles(const MoeRuntimeCommand &command) const;
    void commitCompute(const MoeRuntimeCommand &command);
    void markViewsCommitted(const MoeRuntimeCommand &command);
    void signal(const RuntimeObjectKey &event);

    Config config;
    ProgramScoreboard *scoreboard = nullptr;
    TensorSram *sram = nullptr;
    const MoeOverlayEventBus *bus = nullptr;
    const MoeOverlayAddressSpace *addresses = nullptr;
    const MoeOverlayGraph *graph_ref = nullptr;
    MoeOverlayDmaPort *dma = nullptr;
    MoeOverlayComputePort *compute = nullptr;
    std::map<std::pair<uint32_t, uint32_t>, const MoeOverlayEntry *>
        descriptors;
    std::map<std::pair<uint32_t, uint32_t>, const MoeOverlayEntry *> views;
    std::set<RuntimeObjectKey> live_dma;
    uint32_t layer_id_value = 0;
    InstanceGeneration instance_value;
    std::vector<MoeRuntimeCommand> commands;
    std::vector<Live> live;
    std::set<RuntimeObjectKey> finished;
    static constexpr uint16_t kInvalidCore = 0xFFFF;
    std::vector<RuntimeObjectKey> signal_log;
    std::vector<RuntimeObjectKey> completion_log;
    uint64_t now = 0;
    uint64_t issued_commands = 0;
    uint64_t completed_commands = 0;
    uint64_t sram_read_bytes = 0;
    uint64_t sram_write_bytes = 0;
    uint64_t sram_service_cycles = 0;
    uint64_t sram_bank_conflicts = 0;
    uint64_t compute_cycles = 0;
    uint32_t compute_commits = 0;
    uint32_t copy_through_commands = 0;
    uint32_t local_reduce_commands = 0;
    std::vector<ComputeDigest> *compute_digests = nullptr;
    std::map<std::pair<uint32_t, uint32_t>, uint64_t> committed_views;
    std::map<uint32_t, std::pair<uint64_t, uint64_t>> sram_region_bytes;
    std::map<uint32_t,
             std::map<uint32_t, std::pair<uint64_t, uint64_t>>>
        sram_region_kind_bytes;
    bool started_value = false;
    bool loaded = false;
    bool failed_value = false;
    uint8_t failure_status = 0;
    RuntimeObjectKey failed_command;
    std::string failure_reason;
};

} // namespace ai_mesh
} // namespace gem5

#endif

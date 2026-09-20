#ifndef DEV_AI_MESH_MESH_DISPATCHER_HH
#define DEV_AI_MESH_MESH_DISPATCHER_HH

#include <set>
#include <string>
#include <vector>

#include "base/statistics.hh"
#include "dev/ai_mesh/burst_attribution.hh"
#include "dev/ai_mesh/range_image.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"
#include "dev/ai_mesh/peer_transfer_coverage.hh"
#include "dev/ai_mesh/program_scoreboard.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{
namespace ruby
{
class Network;
}
struct MeshDispatcherParams;

namespace ai_mesh
{

class MeshDummyCore;
class MeshProgramLoader;
class MockAxiTransport;
class NpuMemoryEndpoint;
class PeerSramAperture;

// Dispatches program instances at core edges, watches progress and writes
// the machine-readable result artifact (traffic oracle, per-core counters,
// conservation and drain state) consumed by the acceptance configs.
class MeshDispatcher : public ClockedObject
{
  public:
    using Params = MeshDispatcherParams;

    MeshDispatcher(const Params &p);

    void regStats() override;
    void startup() override;

    void notifyCoreHalted(uint16_t core_id);
    void latchInstanceError(MeshDummyCore *source, Tick tick,
                             const InstanceFailureFacts &facts);
    bool instanceErrored() const { return instance_error_latched; }
    void bindScoreboardTo(MeshDummyCore *core);
    const MeshDummyCore *core(uint16_t core_id) const;
    void routePeerCommit(uint16_t peer_core, uint32_t transfer_id);
    void markReceiverAllocationValid(uint16_t core_id,
                                    uint32_t allocation_id);
    bool registerFence(MeshDummyCore *core, uint32_t command_id,
                       uint32_t signal_event);
    void onDmaTagRetired(uint16_t core_id, uint64_t tag);
    void cancelFencesOf(uint16_t core_id);
    void armTransferExpectation(uint32_t transfer_id);
    // Does a real peer aperture own the release of this transfer?  When one
    // does, only its own observed commit of the transfer's bytes may release
    // the receiver.
    bool expectsTransfer(uint32_t transfer_id) const;
    // Observation-only functional read of an admitted SRAM aperture address,
    // local or peer: the dispatcher owns the aperture table, so it is the one
    // place that resolves an endpoint address to the tile that holds it.
    bool readFunctional(uint64_t address, uint64_t size, uint8_t *out) const;
    const std::string &resultPath() const { return result_json_path; }
    const std::string &receiverFault() const { return receiver_fault; }
    const std::string &residencyFault() const { return residency_fault; }
    void reportRuntimeDiagnostic(const RuntimeDiagnostic &record,
                                 const std::string &business);

    // Read-only view of the most recent archived instance ledger for a core;
    // consumers of finished observations query this instead of a core copy.
    const MeshDummyCore::InstanceLedger *lastInstanceLedger(uint16_t core_id) const;
    std::vector<const MeshDummyCore::InstanceLedger *>
    instanceLedgers(uint16_t core_id) const;

  private:
    struct StartEvent : public Event
    {
        MeshDispatcher *dispatcher;
        explicit StartEvent(MeshDispatcher *d) : Event(), dispatcher(d) {}
        void process() override { dispatcher->dispatch(); }
        const char *description() const override
        { return "ai_mesh.dispatcher.start"; }
    };

    struct WatchdogEvent : public Event
    {
        MeshDispatcher *dispatcher;
        explicit WatchdogEvent(MeshDispatcher *d) : Event(), dispatcher(d) {}
        void process() override { dispatcher->checkWatchdog(); }
        const char *description() const override
        { return "ai_mesh.dispatcher.watchdog"; }
    };

    struct DrainEvent : public Event
    {
        MeshDispatcher *dispatcher;
        explicit DrainEvent(MeshDispatcher *d) : Event(), dispatcher(d) {}
        void process() override { dispatcher->checkDrain(); }
        const char *description() const override
        { return "ai_mesh.dispatcher.drain"; }
    };

    void dispatch();
    void checkWatchdog();
    // Re-evaluate the drain gate on a real clock edge while the drain is open,
    // so the exit happens as soon as every owner is empty rather than at the
    // watchdog's staleness deadline.
    void checkDrain();
    uint64_t progressSample() const;
    void checkAllHalted();
    void writeResultJson();
    void writeInstanceBarriers(
        std::ostream &out,
        const std::vector<ProgramScoreboard::BarrierGroup> &barriers);
    void writeConservationJson(std::ofstream &out);
    bool anyCoreErrored() const;
    bool anyInstanceErrored() const;
    // The unified drain gate: every real owner must have emptied before the
    // instance is archived or the program exits.
    bool drainSatisfied();

    MeshProgramLoader *const loader;
    const std::vector<MeshDummyCore *> cores;
    MockAxiTransport *const transport;
    const std::vector<PeerSramAperture *> apertures;
    NpuMemoryEndpoint *const endpoint;
    ruby::Network *const network;
    const std::string result_json_path;
    const std::string receiver_fault;
    const std::string residency_fault;
    const std::string drain_fault;
    const uint64_t destination_bytes_limit;
    const uint32_t total_instances;
    const uint64_t watchdog_ticks_value;

    struct PendingFence
    {
        uint16_t core_id;
        uint32_t command_id;
        uint32_t signal_event;
        std::set<std::pair<uint16_t, uint64_t>> waiting;
    };
    // One frame's observed transfer coverage on one receiving tile.  The
    // coverage is a per-frame fact: the tile's expectations are re-armed by
    // every instance, so the frame owns its own evidence.
    struct ApertureFrame
    {
        uint16_t core_id = 0;
        std::vector<PeerTransferCoverage> transfers;
    };

    struct InstanceRecord
    {
        uint32_t instance_id = 0;
        bool finalized = false;
        std::map<uint16_t, MeshDummyCore::InstanceLedger> cores;
        // Every real burst of this instance with its admitted execution and
        // retirement facts, preserved before the engines start the next one.
        std::vector<BurstAttribution> bursts;
        // Raw bytes every real destination owner held when this instance was
        // archived: the per-instance physical post-image of each writer.
        std::vector<DestinationSegment> destinations;
        std::vector<ApertureFrame> apertures;
        std::vector<ProgramScoreboard::BarrierGroup> barriers;
    };
    std::vector<BurstAttribution> instanceBursts() const;
    std::vector<DestinationSegment> instanceDestinations(
        const std::map<uint16_t, MeshDummyCore::InstanceLedger> &ledgers) const;
    bool readDestinationBytes(uint16_t kind, uint16_t owner_core,
                              uint64_t address, uint64_t size,
                              std::vector<uint8_t> &out) const;
    static void writeInstanceDestinations(
        std::ostream &out, const std::vector<DestinationSegment> &segments);
    static void writeInstanceBursts(std::ostream &out,
                                    const std::vector<BurstAttribution> &bursts);
    static void writeInstanceApertures(std::ostream &out,
                                       const std::vector<ApertureFrame> &frames);
    std::vector<InstanceRecord> instance_records;

    std::vector<PendingFence> pending_fences;

    bool instance_error_latched = false;
    InstanceFailureFacts instance_root_facts;

    ProgramScoreboard scoreboard;
    uint32_t instance_counter = 0;
    bool started = false;
    std::set<uint16_t> halted_cores;
    uint64_t last_progress_snapshot = 0;
    // The drain window: the first owner observation after the first core
    // halted, and the last one taken at the exit point.
    bool drain_active = false;
    Tick drain_begin_tick = 0;
    uint64_t drain_begin_pending = 0;
    Tick drain_end_tick = 0;
    uint64_t drain_end_pending = 0;
    // Instances whose archive went through the unified drain gate.
    uint32_t drained_instances = 0;
    uint64_t dma_tags_retired = 0;
    uint32_t watchdog_fired_count = 0;

    statistics::Scalar instancesDispatched;
    statistics::Scalar instancesCompleted;

    StartEvent start_event;
    WatchdogEvent watchdog_event;
    DrainEvent drain_event;
};

} // namespace ai_mesh
} // namespace gem5

#endif

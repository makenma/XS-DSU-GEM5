#ifndef DEV_AI_MESH_MESH_DISPATCHER_HH
#define DEV_AI_MESH_MESH_DISPATCHER_HH

#include "dev/ai_mesh/cache_fill_edge.hh"
#include <set>
#include <string>
#include <vector>

#include "base/statistics.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/burst_attribution.hh"
#include "dev/ai_mesh/range_image.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_dma_observer.hh"
#include "dev/ai_mesh/mesh_execution_view.hh"
#include "dev/ai_mesh/serving_mesh_dispatch.hh"
#include "dev/ai_mesh/mesh_moe_runtime.hh"
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
class NpuMemoryRegistry;
class PeerSramAperture;

// Dispatches program instances at core edges, watches progress and writes
// the machine-readable result artifact (traffic oracle, per-core counters,
// conservation and drain state) consumed by the acceptance configs.
class MeshDispatcher : public ClockedObject, public ServingMeshDispatch
{
  public:
    using Params = MeshDispatcherParams;

    MeshDispatcher(const Params &p);

    void regStats() override;
    void startup() override;
    void serialize(CheckpointOut &cp) const override;
    void unserialize(CheckpointIn &cp) override;
    void drainResume() override;
    template<class Archive> void serialize(Archive &archive, unsigned);


    void begin();
    void notifyCoreHalted(uint16_t core_id);
    void notifyOverlayExit(uint32_t layer_id);
    void stageInstanceFailure(const ErrorCandidate &candidate);
    void stageInstanceFailure(uint64_t instance, const ErrorCandidate &candidate);
    void commitInstanceFailure();
    void noteMemberCancel(uint64_t instance, uint64_t request, uint64_t tick) override;
    bool instanceErrored() const { return instance_error_latched; }
    mesh_abi::MeshBatchState batchState() const { return batch_state; }
    bool armInstance();
    bool startInstance();
    void abortBeforeStart();
    // Deterministic prestart failure (tombstoned demand): the batch never
    // runs, but every set-up core has to be undone and the run has to reach a
    // defined error terminal instead of hanging.
    void commitPrestartFailure();
    // Snapshot the live dispatcher state to the result artifact.  The single
    // writer is also driven by external prestart-cancel observation, which
    // ends the run before any program terminal.
    void writeResultJson();

    // MoE insertion gate coordination (contract 7.2.2).  The dispatcher arms
    // every participating region of the loaded program before the instance
    // starts and releases the layer's gate once its overlay group exit is
    // published.
    void configureServingCapacity(uint32_t capacity) override;
    std::optional<InstanceGeneration> selectInstance(
        uint32_t entrypoint_id, uint32_t profile_id,
        std::string &reason) override;
    void setInstanceObserver(MeshInstanceObserver *observer);
    void setDmaObserver(MeshDmaObserver *observer);
    bool prepareMoe(const mesh_abi::AgentInstanceProfile &profile,
        const std::vector<ServingBatchMember> &members, const ServingInstanceBinding &binding,
        std::shared_ptr<const MoeOverlaySet> &overlays, MoePreparationError &error) override;
    void bindInstance(const ServingInstanceBinding &value,
                      std::shared_ptr<const MoeOverlaySet> overlays = {}) override;
    bool canAccessKv(uint64_t address, uint64_t bytes) const override;
    void bindFillContent(const ServingFillBinding &binding) override;
    void bindSemanticDigest(
        const std::array<uint8_t, 32> &digest) override;
    void cancelPendingInstance(uint64_t instance = 0) override;
    void failCurrentInstance(const ErrorCandidate &failure) override;
    void beginSelectedInstance(std::optional<ErrorCandidate> failure = {}) override;
    bool replayQuiescent() const override;
    void finishExecution() override;
    const ProfileExecutionView *executionView() const;
    void advanceAfterInstance();
    void commitProgramTerminal();
    void armOverlayGates();
    bool reserveWeightCaches();
    bool retryCacheReservations();
    bool commitCacheReservations();
    void queueCacheFill(MoeWeightCache &cache, const mesh_abi::WeightFillKey &key,
                        std::optional<ErrorCandidate> failure, std::optional<uint64_t> valid_bytes, bool descriptor_drained);
    // Single write entry for leaving the reservation-wait window: the retry
    // event, the progress watchdog and the frozen reservation queue belong to
    // the window, so every termination path has to drop all three.
    void releaseWaitWindow();
    bool cacheReservationWaiting() const
    {
        return cache_reservation_waiting;
    }
    // Read-only wait-window observability: the window's own bookkeeping is the
    // evidence a prestart cancel has to leave clean.
    size_t pendingReservations() const
    {
        return cache_reservation_queue.size();
    }
    bool retryEventScheduled() const { return cache_retry_event.scheduled(); }
    bool progressWatchdogScheduled() const
    {
        return watchdog_event.scheduled();
    }
    uint64_t instanceStarts() const { return batch_starts; }
    uint64_t instanceGeneration() const { return instance_counter.value(); }
    uint64_t cacheReservationAttempts() const
    {
        return cache_reservation_attempts;
    }
    uint64_t cacheReservationCommits() const
    {
        return cache_reservation_commits;
    }
    bool overlayGroupExited(uint32_t layer_id);
    bool allOverlayGroupsExited() const;
    std::string overlayGateState() const;
    InstanceGeneration currentInstance() const { return instance_counter; }
    const std::vector<MeshDummyCore *> &participants() const
    {
        return participant_cores;
    }
    void bindScoreboardTo(MeshDummyCore *core);
    const MeshDummyCore *core(uint16_t core_id) const;
    void routePeerCommit(uint16_t peer_core, uint32_t transfer_id);
    void captureDestination(const MeshDummyCore &core, const DescriptorKey &key,
                            DestinationSampling sampling);
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
    std::string checkpointState;
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

    // A batch waiting for cache resources retries on every core edge until the
    // coordinator can commit; the wait never starts the cores.
    struct CacheRetryEvent : public Event
    {
        MeshDispatcher *dispatcher;
        explicit CacheRetryEvent(MeshDispatcher *d) : Event(25), dispatcher(d) {}
        void process() override { dispatcher->commitCacheReservations(); }
        const char *description() const override
        { return "ai_mesh.dispatcher.cache_retry"; }
    };

    // A halted participant may still be draining (publication/overlay work),
    // in which case the settle edge has to be retried until every participant
    // is quiescent.
    struct SettleRetryEvent : public Event
    {
        MeshDispatcher *dispatcher;
        explicit SettleRetryEvent(MeshDispatcher *d) : Event(), dispatcher(d) {}
        void process() override { dispatcher->retryInstanceSettle(); }
        const char *description() const override
        { return "ai_mesh.dispatcher.settle_retry"; }
    };

    // One core's whole batch demand: every layer of this core is covered by a
    // single reservation shadow so physical keys, slots, MSHR/eviction,
    // incarnation and epoch are shared inside one transaction.
    struct CacheReservationRequest
    {
        uint32_t request_id = 0;
        uint32_t core_id = 0;
        std::vector<MoeWeightCache::LayerTags> demand;
        std::vector<MoeCacheToken> tokens;
        std::map<uint32_t, std::map<uint32_t, uint32_t>> consumers;
    };

    std::vector<CacheReservationRequest> cacheDemands(const MoeOverlaySet &overlays) const;
    MoeWeightCache::ReserveStatus reserveCacheDemand(uint64_t batch,
        std::vector<CacheReservationRequest> &requests);
    struct ServingCandidate
    {
        InstanceGeneration instance;
        const ProfileExecutionView *view = nullptr;
        ServingInstanceBinding binding;
        std::shared_ptr<const MoeOverlaySet> overlays;
        std::vector<ServingFillBinding> fills;
        std::array<uint8_t, 32> digest{};
        bool has_digest = false;
        bool failure_pending = false;
        bool ready = false;
        bool waited = false;
        bool attached = false;
        std::vector<CacheReservationRequest> demand;
    };
    std::vector<std::optional<ServingCandidate>> serving_candidates;
    ServingCandidate *staging_candidate = nullptr;
    std::optional<ServingCandidate> active_candidate;
    InstanceGeneration allocated_instance;
    struct CandidateEvent
    {
        GEM5_CHECKPOINT_AGGREGATE()
        uint64_t instance;
        Tick tick;
        std::string event;
    };
    std::vector<CandidateEvent> candidate_events;
    uint64_t candidate_waits = 0;
    uint64_t candidate_attaches = 0;
    uint64_t candidate_resumes = 0;
    void pumpServingCandidates();
    bool resolveCacheReservations(
        const std::vector<CacheReservationRequest> &requests);
    MeshDummyCore *coreOf(uint16_t core_id) const;

    // One program terminal for the exit cause, the JSON terminal and the
    // per-core idle accounting.
    enum class ProgramTerminal
    {
        NONE = 0,
        DONE = 1,
        ERROR_DRAINED = 2,
        PRESTART_FAILED = 3,
    };
    std::string programCause() const;
    const char *terminalName() const;
    ProgramTerminal program_terminal = ProgramTerminal::NONE;
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
    void retryInstanceSettle();
    std::string outputSpanDigest() const;
    void writeInstanceBarriers(
        std::ostream &out,
        const std::vector<ProgramScoreboard::BarrierGroup> &barriers);
    void writeConservationJson(std::ofstream &out);
    int erroredCoreId() const;
    bool anyCoreErrored() const { return erroredCoreId() >= 0; }
    bool anyInstanceErrored() const;
    // The unified drain gate: every real owner must have emptied before the
    // instance is archived or the program exits.
    bool drainSatisfied();

    MeshProgramLoader *const loader;
    const std::vector<MeshDummyCore *> cores;
    MockAxiTransport *const transport;
    const std::vector<PeerSramAperture *> apertures;
    const NpuMemoryRegistry &memory;
    const uint64_t output_span_base;
    const uint64_t output_span_bytes;
    ruby::Network *const network;
    const std::string result_json_path;
    const std::string receiver_fault;
    const std::string residency_fault;
    const std::string drain_fault;
    const uint64_t destination_bytes_limit;
    const uint32_t total_instances;
    const bool autostart;
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
        GEM5_CHECKPOINT_AGGREGATE()
        uint16_t core_id = 0;
        std::vector<PeerTransferCoverage> transfers;
    };

    struct InstanceRecord
    {
        GEM5_CHECKPOINT_AGGREGATE()
        InstanceGeneration instance;
        std::optional<BatchFailureRecord> failure;
        bool finalized = false;
        std::map<uint16_t, MeshDummyCore::InstanceLedger> cores;
        // Every real burst of this instance with its admitted execution and
        // retirement facts, preserved before the engines start the next one.
        std::vector<BurstAttribution> bursts;
        std::vector<DestinationSegment> destinations;
        std::vector<ApertureFrame> apertures;
        std::vector<ProgramScoreboard::BarrierGroup> barriers;
    };
    std::vector<BurstAttribution> instanceBursts() const;
    void capturePendingDestinations();
    std::vector<DestinationSegment> live_destinations;
    std::set<DescriptorKey> sampled_destinations;
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
    std::optional<int> drained_error_core;
    std::string output_digest_before;
    ServingInstanceBinding instance_binding;
    std::shared_ptr<const MoeOverlaySet> serving_overlays;
    std::vector<ServingFillBinding> pending_fill_bindings;
    std::array<uint8_t, 32> semantic_digest = {};
    bool semantic_digest_set = false;

    ProgramScoreboard scoreboard;
    InstanceGeneration instance_counter;
    std::set<uint32_t> overlay_exits_seen;
    MoeOverlayEventBus overlay_bus;
    mesh_abi::MeshBatchState batch_state =
        mesh_abi::MeshBatchState::PROGRAM_READY;
    std::vector<MeshDummyCore *> participant_cores;
    const ProfileExecutionView *selected_view = nullptr;
    MeshInstanceObserver *instance_observer = nullptr;
    bool started = false;
    std::set<uint16_t> halted_cores;
    uint64_t last_progress_snapshot = 0;
    std::vector<CacheReservationRequest> cache_reservation_queue;
    uint32_t next_cache_request_id = 1;
    uint32_t cache_queue_capacity = 0;
    // The arm is set up once; while the reservation waits the batch stays
    // un-armed so no core start and no overlay entry can happen.
    bool cache_reservation_waiting = false;
    bool batch_setup_done = false;
    uint64_t cache_reservation_attempts = 0;
    uint64_t cache_identity_reassignments = 0;
    uint64_t cache_reservation_commits = 0;
    uint64_t prestart_failures = 0;
    uint64_t batch_starts = 0;
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

    class FailureEvent : public Event
    {
      public:
        MeshDispatcher *dispatcher;
        explicit FailureEvent(MeshDispatcher *d) : Event(20), dispatcher(d) {}
        void process() override { dispatcher->commitInstanceFailure(); }
        const char *description() const override { return "ai_mesh.instance_failure"; }
    };
    const uint32_t failure_record_capacity;
    InstanceFailureReducer failure_reducer;
    FailureEvent failure_event;
    std::unique_ptr<CacheFillEdge> cache_fill_edge;
    bool failure_pending = false;
    bool edge_commit_active = false;
    StartEvent start_event;
    WatchdogEvent watchdog_event;
    CacheRetryEvent cache_retry_event;
    SettleRetryEvent settle_retry_event;
    DrainEvent drain_event;
};

} // namespace ai_mesh
} // namespace gem5

#endif

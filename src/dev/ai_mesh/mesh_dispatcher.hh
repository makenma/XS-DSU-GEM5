#ifndef DEV_AI_MESH_MESH_DISPATCHER_HH
#define DEV_AI_MESH_MESH_DISPATCHER_HH

#include <set>
#include <string>
#include <vector>

#include "base/statistics.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_dma_observer.hh"
#include "dev/ai_mesh/mesh_execution_view.hh"
#include "dev/ai_mesh/serving_mesh_dispatch.hh"
#include "dev/ai_mesh/mesh_moe_runtime.hh"
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
class MeshDispatcher : public ClockedObject, public ServingMeshDispatch
{
  public:
    using Params = MeshDispatcherParams;

    MeshDispatcher(const Params &p);

    void regStats() override;
    void startup() override;

    void begin();
    void notifyCoreHalted(uint16_t core_id);
    void notifyOverlayExit(uint32_t layer_id);
    void latchInstanceError(MeshDummyCore *source, Tick tick);
    bool instanceErrored() const { return instance_error_latched; }
    mesh_abi::MeshBatchState batchState() const { return batch_state; }
    bool armInstance();
    bool startInstance();
    void abortBeforeStart();
    // Deterministic prestart failure (tombstoned demand): the batch never
    // runs, but every set-up core has to be undone and the run has to reach a
    // defined error terminal instead of hanging.
    void commitPrestartFailure();
    void commitErrorTerminal();
    // Snapshot the live dispatcher state to the result artifact.  The single
    // writer is also driven by external prestart-cancel observation, which
    // ends the run before any program terminal.
    void writeResultJson();

    // MoE insertion gate coordination (contract 7.2.2).  The dispatcher arms
    // every participating region of the loaded program before the instance
    // starts and releases the layer's gate once its overlay group exit is
    // published.
    bool installSelectors(
        const std::vector<std::pair<uint32_t, uint32_t>> &selectors,
        std::string &reason) override;
    void setInstanceObserver(MeshInstanceObserver *observer);
    void setDmaObserver(MeshDmaObserver *observer);
    void bindInstance(const ServingInstanceBinding &value) override;
    bool canAccessKv(uint64_t address, uint64_t bytes) const override;
    void bindFillContent(const ServingFillBinding &binding) override;
    void bindSemanticDigest(
        const std::array<uint8_t, 32> &digest) override;
    void cancelPendingInstance() override;
    void failCurrentInstance() override;
    void beginFirstInstance() override;
    void resumeInstance() override;
    const ProfileExecutionView *executionView() const;
    void advanceAfterInstance();
    void armOverlayGates();
    bool reserveWeightCaches();
    bool retryCacheReservations();
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
    void routePeerCommit(uint16_t peer_core, uint32_t transfer_id);
    bool registerFence(MeshDummyCore *core, uint32_t command_id,
                       uint32_t signal_event);
    void onDmaTagRetired(uint16_t core_id, uint64_t tag);
    void cancelFencesOf(uint16_t core_id);
    void armTransferExpectation(uint32_t transfer_id);

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

    // A batch waiting for cache resources retries on every core edge until the
    // coordinator can commit; the wait never starts the cores.
    struct CacheRetryEvent : public Event
    {
        MeshDispatcher *dispatcher;
        explicit CacheRetryEvent(MeshDispatcher *d) : Event(), dispatcher(d) {}
        void process() override { dispatcher->retryCacheReservations(); }
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
        std::map<uint32_t, std::map<uint32_t, uint32_t>> consumers;
    };

    std::vector<MeshDispatcher::CacheReservationRequest> cacheDemands() const;
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
    void dispatch();
    void checkWatchdog();
    void checkAllHalted();
    void retryInstanceSettle();
    std::string outputSpanDigest() const;
    void writeConservationJson(std::ofstream &out);
    int erroredCoreId() const;
    bool anyCoreErrored() const { return erroredCoreId() >= 0; }

    MeshProgramLoader *const loader;
    const std::vector<MeshDummyCore *> cores;
    MockAxiTransport *const transport;
    const std::vector<PeerSramAperture *> apertures;
    NpuMemoryEndpoint *const endpoint;
    const uint64_t output_span_base;
    const uint64_t output_span_bytes;
    ruby::Network *const network;
    const std::string result_json_path;
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
    struct InstanceRecord
    {
        std::map<uint16_t, MeshDummyCore::InstanceLedger> cores;
    };
    std::vector<InstanceRecord> instance_records;

    std::vector<PendingFence> pending_fences;

    bool instance_error_latched = false;
    // Whether the latched error belongs to a running instance.  Only such an
    // error owns the ERROR_DRAINED terminal; an error raised while the batch
    // was still arming belongs to the prestart-failure terminal instead.
    bool error_latched_during_run = false;
    std::string output_digest_before;
    ServingInstanceBinding instance_binding;
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
    std::vector<std::pair<uint32_t, uint32_t>> execution_selectors;
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
    uint32_t watchdog_fired_count = 0;

    statistics::Scalar instancesDispatched;
    statistics::Scalar instancesCompleted;

    StartEvent start_event;
    WatchdogEvent watchdog_event;
    CacheRetryEvent cache_retry_event;
    SettleRetryEvent settle_retry_event;
};

} // namespace ai_mesh
} // namespace gem5

#endif

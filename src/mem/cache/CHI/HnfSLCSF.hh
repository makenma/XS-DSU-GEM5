#ifndef __HNF_SLCSF_HH__
#define __HNF_SLCSF_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "mem/cache/CHI/HnfSLCSFBackend.hh"
#include "mem/cache/CHI/HnfSLCSFRequest.hh"
#include "mem/cache/CHI/HnfSLCSFResponse.hh"

namespace gem5::Chi
{

enum class SlcSfEnqueueResult : uint8_t
{
    Accepted,
    NoCredit,
    Initializing,
    Draining
};

enum class SlcSfCancelResult : uint8_t
{
    Cancelled,
    NotCancellable,
    TooLate,
    NotFound
};

enum class SlcSfCompletionAckResult : uint8_t
{
    Acknowledged,
    NotVisible,
    IdentityMismatch,
    Stale
};

enum class SlcSfSetLockMode : uint8_t
{
    Read,
    Write
};

enum class SlcSfStatOperation : uint8_t
{
    Lookup,
    Fill,
    Update,
    Evict,
    NumOperations
};

enum class SlcSfStatHitCombination : uint8_t
{
    MissMiss,
    SlcHit,
    SfHit,
    SlcSfHit,
    NumCombinations
};

enum class SlcSfStatVictim : uint8_t
{
    CleanSlc,
    DirtySlc,
    Sf,
    NumVictims
};

constexpr size_t SlcSfReplayReasonCount =
    static_cast<size_t>(SlcSfReplayReason::Cancelled) + 1;

/** Testable, non-architectural telemetry owned by the timed service. */
struct HnfSLCSFStatsSnapshot
{
    std::array<uint64_t,
               static_cast<size_t>(SlcSfStatOperation::NumOperations)>
        operations{};
    std::array<uint64_t,
               static_cast<size_t>(SlcSfStatHitCombination::NumCombinations)>
        hitCombinations{};
    std::array<uint64_t,
               static_cast<size_t>(SlcSfStatVictim::NumVictims)>
        victims{};
    std::array<uint64_t, SlcSfReplayReasonCount> replayReasons{};
    uint64_t directedSnoops = 0;
    uint64_t broadcastSnoops = 0;
    uint64_t reqFullCycles = 0;
    uint64_t respFullCycles = 0;
    uint64_t reqOccupancySamples = 0;
    uint64_t reqOccupancyTotal = 0;
    uint64_t reqOccupancyMax = 0;
    uint64_t respOccupancySamples = 0;
    uint64_t respOccupancyTotal = 0;
    uint64_t respOccupancyMax = 0;
    uint64_t inflightOccupancySamples = 0;
    uint64_t inflightOccupancyTotal = 0;
    uint64_t inflightOccupancyMax = 0;
    uint64_t serviceLatencySamples = 0;
    uint64_t serviceLatencyTotal = 0;
    uint64_t acceptedToVisibleSamples = 0;
    uint64_t acceptedToVisibleLatencyTotal = 0;
    uint64_t noCredit = 0;
    uint64_t serviceStalls = 0;
    uint64_t setLockConflicts = 0;
};

/** One fully correlated request lifecycle, retained for diagnostics/tests. */
struct HnfSLCSFTraceRecord
{
    SlcSfReqId reqId{};
    uint32_t pocEntryId = 0;
    uint64_t lineAddress = 0;
    SlcSfOperationKind operation = SlcSfOperationKind::Lookup;
    std::optional<Tick> acceptedTick;
    std::optional<Tick> issueTick;
    std::optional<Tick> completeTick;
    std::optional<Tick> visibleTick;
    SlcSfTerminalStatus status = SlcSfTerminalStatus::Error;
    std::optional<SlcSfReplayReason> replayReason;
};

/** Optional child-owned gem5 statistics sink. */
class HnfSLCSFStatsSink
{
  public:
    virtual ~HnfSLCSFStatsSink() = default;
    virtual void accepted(SlcSfStatOperation operation) = 0;
    virtual void sampledOccupancy(size_t req, size_t resp, size_t inflight,
                                  uint64_t cycles, bool req_full,
                                  bool resp_full) = 0;
    virtual void sampledStorage(uint64_t valid_lines,
                                uint64_t capacity_lines,
                                uint32_t max_valid_ways,
                                uint64_t cycles) = 0;
    virtual void issued(uint64_t configured_latency) = 0;
    virtual void victim(
        SlcSfStatVictim victim, uint32_t requester, uint32_t set,
        HnfSLCSFBackend::SlcReplacementPolicy policy) = 0;
    virtual void terminal(const SlcSfResponse& response) = 0;
    virtual void becameVisible(uint64_t latency) = 0;
    virtual void rejectedNoCredit() = 0;
    virtual void stalled(bool set_lock_conflict) = 0;
};

struct SlcSfSetLockRequest
{
    std::optional<uint32_t> slcSet;
    std::optional<uint32_t> sfSet;
    SlcSfSetLockMode mode = SlcSfSetLockMode::Read;
};

/**
 * Exclusive SLC/SF set ownership for an issued service window.
 *
 * Both requested lock classes are checked before either is installed. This
 * makes crossed SLC/SF set requests retry without partial ownership and
 * therefore avoids lock-order deadlock.
 */
class HnfSLCSFSetLockManager
{
  public:
    HnfSLCSFSetLockManager(size_t slc_sets, size_t sf_sets);

    bool tryAcquire(uint64_t owner, const SlcSfSetLockRequest& request);
    void release(uint64_t owner);
    bool holds(uint64_t owner) const;
    size_t heldLockCount(uint64_t owner) const;
    bool holdsExact(
        uint64_t owner, const SlcSfSetLockRequest& request) const;
    size_t heldLockCount() const;

  private:
    struct Holder
    {
        uint64_t owner = 0;
        SlcSfSetLockMode mode = SlcSfSetLockMode::Read;
    };

    std::vector<std::optional<Holder>> slcOwners;
    std::vector<std::optional<Holder>> sfOwners;
};

struct HnfSLCSFPipelineConfig
{
    size_t reqQueueEntries = 8;
    size_t respQueueEntries = 8;
    size_t maxInflight = 1;
    size_t lookupIssueWidth = 1;
    size_t fillIssueWidth = 1;
    size_t updateIssueWidth = 1;
    size_t lookupLatency = 4;
    size_t fillLatency = 4;
    size_t updateLatency = 3;
    // Legacy configuration fields kept so existing configs/checkpoints remain
    // loadable.  Dirty SLC victims are transferred directly to the PoCQ and
    // neither value participates in service timing or capacity checks.
    size_t victimLatency = 3;
    size_t sfEvictLatency = 2;
    size_t replayPenalty = 2;
    size_t victimBufferEntries = 2;
    size_t responseConsumeWidth = 1;
    bool enableSetLock = false;
    Tick childClockPeriod = 1;
    size_t initLatency = 16;
    // Keep policy fields trailing so existing positional test/config
    // initializers preserve their historical queue/latency meaning.
    std::string slcReplacementPolicy = "lru";
    uint64_t slcReplacementSeed = 1;
    bool allowReplacementPolicyOverride = false;
};

template <class Params>
HnfSLCSFPipelineConfig
makeEmbeddedSlcsfConfig(const Params& p, Tick child_clock_period = 1)
{
    HnfSLCSFPipelineConfig config{};
    config.slcReplacementPolicy = p.slc_replacement_policy;
    config.slcReplacementSeed = p.slc_replacement_seed;
    config.allowReplacementPolicyOverride =
        p.slc_restore_allow_policy_override;
    config.reqQueueEntries = p.slcsf_req_queue_entries;
    config.respQueueEntries = p.slcsf_resp_queue_entries;
    config.maxInflight = p.slcsf_max_inflight;
    config.lookupIssueWidth = p.slcsf_lookup_issue_width;
    config.fillIssueWidth = p.slcsf_fill_issue_width;
    config.updateIssueWidth = p.slcsf_update_issue_width;
    config.lookupLatency = p.slcsf_lookup_latency;
    config.fillLatency = p.slcsf_fill_latency;
    config.updateLatency = p.slcsf_update_latency;
    config.victimLatency = p.slcsf_victim_latency;
    config.sfEvictLatency = p.slcsf_sf_evict_latency;
    config.replayPenalty = p.slcsf_replay_penalty;
    config.victimBufferEntries = p.slcsf_victim_buffer_entries;
    config.responseConsumeWidth = p.slcsf_response_consume_width;
    config.enableSetLock = p.slcsf_enable_set_lock;
    config.childClockPeriod = child_clock_period;
    config.initLatency = p.init_latency;
    return config;
}

/**
 * Compatibility adapter for synchronous callers during the timing migration.
 *
 * Storage and coherence semantics live in HnfSLCSFBackend.  Keeping this
 * adapter preserves the existing controller interface until callers migrate
 * to owned, asynchronous requests.
 */
class HnfSLCSF : public HnfSLCSFBackend
{
  public:
    enum class MutationStage : uint8_t
    {
        U0DecodeValidate,
        U1PrepareResources,
        U2ArrayWrite,
        U3CheckLatch
    };

    HnfSLCSF(uint32_t block_size, uint32_t slc_num_sets,
             uint32_t slc_num_ways, uint32_t sf_num_sets,
             uint32_t sf_num_ways, uint32_t seq_entries = 8,
             HnfSLCSFPipelineConfig pipeline_config = {},
             HnfSLCSFStatsSink* stats_sink = nullptr);
    HnfSLCSF(const HnfSLCSF&) = delete;
    HnfSLCSF& operator=(const HnfSLCSF&) = delete;
    HnfSLCSF(HnfSLCSF&&) = delete;
    HnfSLCSF& operator=(HnfSLCSF&&) = delete;

    /**
     * Transfer request ownership into this wakeup's ingress buffer.
     *
     * A rejected rvalue is deliberately not moved from, allowing a caller to
     * retry the same owned message when registered credit becomes available.
     */
    SlcSfEnqueueResult tryEnqueue(SlcSfRequest&& request);
    /** Explicit-tick form used at a cross-clock admission boundary. */
    SlcSfEnqueueResult tryEnqueue(
        SlcSfRequest&& request, Tick accepted_tick);

    /** Notify the timing owner after an accepted request creates work. */
    void setWorkAvailableCallback(std::function<void()> callback)
    {
        workAvailableCallback = std::move(callback);
    }

    /** Advance the registered request boundary at the current simulator tick. */
    void wakeup();

    /** Advance the registered request boundary at an explicit absolute tick. */
    void wakeup(Tick now);

    /**
     * Advance one registered boundary after intentionally skipping idle
     * child-clock edges.  The timing owner may only skip to a cycle returned
     * by calculateNextWakeupCycle().
     */
    void wakeup(Tick now, uint64_t elapsed_cycles);

    size_t registeredReqCredits() const;
    size_t registeredReqCredits(Tick observer_tick) const;
    /** Raw registered grants, including grants produced at this Tick. */
    size_t registeredReqCreditGrants() const { return visibleReqCredits; }
    size_t reqIngressCount() const { return reqIngress.size(); }
    size_t reqReadyCount() const { return reqReady.size(); }
    size_t reqInflightCount() const { return inflightRequests.size(); }
    size_t reqOutstanding() const;
    size_t reqCapacity() const { return config.reqQueueEntries; }
    uint64_t currentCycle() const { return wakeupCycle; }
    size_t mutationStageCount(MutationStage stage) const;
    /** There is no SLC-side victim buffer in the RTL-aligned model. */
    size_t victimBufferCapacity() const { return 0; }
    size_t victimBufferOccupancy() const { return 0; }
    /** Transient U1/U2 captures, not retained writeback-buffer entries. */
    size_t victimReservationCount() const;

    size_t respReservedCount() const { return inflightRequests.size(); }
    size_t respPendingCount() const { return respPending.size(); }
    size_t respVisibleCount() const;
    size_t respVisibleCount(Tick observer_tick) const;
    /** Raw queue occupancy, including responses produced at this Tick. */
    size_t rawRespVisibleCount() const { return respVisible.size(); }
    size_t respOccupied() const;
    size_t respCapacity() const { return config.respQueueEntries; }
    uint64_t noCreditRejectCount() const { return noCreditRejects; }
    uint64_t initializingRejectCount() const { return initializingRejects; }
    uint64_t drainingRejectCount() const { return drainingRejects; }
    uint64_t serviceStallCount() const { return serviceStalls; }
    uint64_t correctnessReplayCount() const { return correctnessReplays; }
    uint64_t finishedRequestCount() const { return finishedRequests; }
    uint64_t cancelledRequestCount() const { return cancelledRequests; }
    size_t setLockCount() const { return setLocks.heldLockCount(); }
    const SlcSfResponse* frontVisibleResponse() const;
    SlcSfCompletionAckResult acknowledgeVisibleCompletion(
        const SlcSfResponse& response);
    /** Pop only ordinary terminal responses; durable Done requires exact ACK. */
    std::optional<SlcSfResponse> popVisibleResponse();
    const HnfSLCSFPipelineConfig& pipelineConfig() const { return config; }
    const HnfSLCSFStatsSnapshot& statsSnapshot() const { return stats; }
    uint64_t acceptedRequestCount() const { return acceptedTotal; }
    uint64_t terminalDoneCount() const { return terminalDoneTotal; }
    uint64_t terminalReplayCount() const { return terminalReplayTotal; }
    uint64_t terminalErrorCount() const { return terminalErrorTotal; }
    const std::optional<HnfSLCSFTraceRecord>& lastCompletedTrace() const
    {
        return completedTrace;
    }

    /** Cancel accepted work only while it is still safe to discard. */
    SlcSfCancelResult cancelRequest(
        uint32_t poc_entry_id, SlcSfReqId req_id, Tick now);

    /** Re-probe storage and validate every identity/version token field. */
    bool validateCommitToken(const SlcSfCommitToken& token,
                             SlcSfReqId expected_lookup_req_id,
                             uint64_t expected_line_address) const;

    bool hasWork() const;
    /** Work which requires another service clock edge, excluding visible data. */
    bool needsServiceWakeup() const;
    /** Earliest logical child cycle at which registered state can advance. */
    std::optional<uint64_t> calculateNextWakeupCycle() const;
    /** Settle time-weighted occupancy before an external state transition. */
    void settleOccupancy(Tick observer_tick);
    bool isBusy() const
    {
        return hasWork() || HnfSLCSFBackend::isBusy() ||
            setLockCount() != 0;
    }
    /** No accepted, durable, or transient owner remains at a drain boundary. */
    bool isCompletelyIdle() const { return initialized && !isBusy(); }

    bool isInitialized() const { return initialized; }
    bool isDrainRequested() const { return drainRequested; }
    bool isAdmissionSealed() const { return draining; }
    uint64_t nextVictimIdentity() const { return nextVictimId; }
    uint64_t nextReservationIdentity() const { return nextCompletionNonce; }
    uint64_t nextSetLockIdentity() const { return nextSetLockOwner; }

    /** Reset persistent and transient state and enter modeled cold init. */
    void resetForColdStart();
    void beginInitialization();
    void finishInitialization();
    /** Record parent D1 without rejecting intents from allocated work. */
    void requestDrain();
    void beginDraining();
    void resumeFromDrain();

    /** Serialize only after coordinated drain has sealed all admission. */
    void serializePersistentState(CheckpointOut& cp) const;
    /** Restore a drained image as initialized, idle, and admission-ready. */
    void unserializePersistentState(CheckpointIn& cp);

#ifdef UNIT_TEST
    uint64_t backendGlobalInvariantCheckCountForTest() const
    {
        return backendGlobalInvariantChecks;
    }
    /** Narrow fault injection used only by the backend permit test. */
    bool corruptInstalledDirtyVictimIdForTest(SlcSfVictimId replacement);
#endif

  private:
    enum class FinishReason : uint8_t
    {
        Done,
        Replay,
        Error,
        Cancelled
    };

    struct InflightRequest
    {
        explicit InflightRequest(SlcSfRequest&& incoming)
            : request(std::move(incoming))
        {}

        SlcSfRequest request;
        uint64_t issueCycle = 0;
        uint64_t completeCycle = 0;
        uint64_t configuredLatency = 0;
        bool latencyStatsRecorded = false;
        std::optional<MutationStage> mutationStage;
        std::optional<SlcSfResponse> terminalResponse;
        std::optional<SlcSfReplayReason> terminalReplayReason;
        bool resourcesPrepared = false;
        bool mutationCommitted = false;
        bool mutationStalled = false;
        bool earlyLookupReplay = false;
        bool cleanupDone = false;
        std::optional<SlcSfCompletionLease> completionLease;
        std::optional<SlcSfVictimId> slcVictimId;
        std::optional<SlcSfSlcVictim> slcVictim;
        std::optional<DirtyVictimSeal> slcVictimSeal;
        std::optional<SlcSfSfVictim> sfVictim;
        std::optional<uint64_t> setLockOwner;
        bool tokenValidated = false;
        std::optional<ProtectedStateSnapshot> rollbackSnapshot;
    };

    static void validateConfig(const HnfSLCSFPipelineConfig& config);
    bool wasAccepted(SlcSfReqId req_id) const;
    void rememberAccepted(SlcSfReqId req_id);
    uint64_t baseServiceLatency(const SlcSfRequest& request) const;
    uint64_t serviceLatency(const SlcSfRequest& request) const;
    bool mutationProducesSfVictim(const SlcSfRequest& request) const;
    Tick replayDeadline() const;
    bool lookupReplaysAtL0(const SlcSfRequest& request) const;
    void promotePendingResponses();
    void completeInflightRequests();
    SlcSfResponse makeTerminalResponse(
        const SlcSfRequest& request,
        std::optional<SlcSfSlcVictim> slc_victim = std::nullopt,
        std::optional<SlcSfSfVictim> sf_victim = std::nullopt,
        std::optional<SlcSfCompletionLease> completion_lease =
            std::nullopt);
    std::optional<SlcSfError> validateMutationRequest(
        const SlcSfRequest& request) const;
    bool validateMutationToken(const SlcSfRequest& request) const;
    bool prepareMutationResources(InflightRequest& request);
    void captureDirtyVictim(InflightRequest& request,
                            const LookupSnapshot& target);
    void discardDirtyVictimCapture(InflightRequest& request);
    bool claimDurableCompletion(InflightRequest& request);
    bool durableClaimMatches(const InflightRequest& request) const;
    void rollbackDurableClaim(InflightRequest& request);
    void rollbackPreparedResources(InflightRequest& request);
    void assertVictimAccounting() const;
    void executeMutation(InflightRequest& request);
    void advanceMutation(InflightRequest& request);
    bool tryAcquireSetLock(InflightRequest& request);
    std::optional<SlcSfStatVictim> pendingSlcVictimKind(
        const SlcSfRequest& request) const;
    void recordConfiguredLatency(
        InflightRequest& request, uint64_t configured_latency);
    void latchMutationResponse(InflightRequest& request);
    void finishInflight(
        InflightRequest& request, FinishReason reason,
        SlcSfResponse response, Tick complete_tick);
    void promoteIngressRequests();
    void issueReadyRequests();
    void updateRegisteredCredits(Tick production_tick);
    void sampleOccupancy(uint64_t cycles);
    void checkLifecycle() const;
    void assertRequestAccounting() const;
    void assertResponseAccounting() const;
    void assertGlobalInvariants() const;
    void runBackendGlobalInvariantCheck();
    void recordTerminalStats(const SlcSfResponse& response);
    void recordServiceStall(bool set_lock_conflict = false);

    HnfSLCSFPipelineConfig config;
    std::deque<SlcSfRequest> reqIngress;
    std::deque<SlcSfRequest> reqReady;
    // Membership in this queue is the terminal response reservation.  Keeping
    // request issue and reservation in one state prevents either from becoming
    // observable without the other.
    std::deque<InflightRequest> inflightRequests;
    std::deque<SlcSfResponse> respPending;
    std::deque<SlcSfResponse> respVisible;
    std::deque<Tick> respVisibleTicks;
    HnfSLCSFSetLockManager setLocks;
    /** Opaque identity shared only with leases minted by this service. */
    std::shared_ptr<const uint8_t> completionProducer;
    uint64_t nextVictimId = 1;
    uint64_t nextCompletionNonce = 1;
    uint64_t nextSetLockOwner = 1;
    size_t visibleReqCredits = 0;
    std::deque<std::optional<Tick>> visibleReqCreditTicks;
    uint64_t wakeupCycle = 0;
    Tick wakeupTick = 0;
    std::optional<Tick> lastOccupancyTick;
    bool initialized = true;
    size_t initializationCyclesRemaining = 0;
    bool drainRequested = false;
    bool draining = false;
    uint64_t noCreditRejects = 0;
    uint64_t initializingRejects = 0;
    uint64_t drainingRejects = 0;
    uint64_t serviceStalls = 0;
    uint64_t correctnessReplays = 0;
    uint64_t finishedRequests = 0;
    uint64_t cancelledRequests = 0;
    HnfSLCSFStatsSnapshot stats;
    HnfSLCSFStatsSink* statsSink = nullptr;
    std::unordered_map<uint64_t, HnfSLCSFTraceRecord> activeTraces;
    // Exact duplicate tombstones compressed into disjoint inclusive ranges.
    // The production allocator is contiguous, so this remains one range.
    std::map<uint64_t, uint64_t> acceptedIdRanges;
    std::optional<HnfSLCSFTraceRecord> completedTrace;
    uint64_t acceptedTotal = 0;
    uint64_t terminalDoneTotal = 0;
    uint64_t terminalReplayTotal = 0;
    uint64_t terminalErrorTotal = 0;
#ifdef UNIT_TEST
    uint64_t backendGlobalInvariantChecks = 0;
    struct VictimIdCorruptionForTest
    {
        SlcSfReqId reqId;
        SlcSfVictimId replacement;
    };
    // Set only by the UNIT_TEST hook and consumed immediately inside U2, so
    // production boundary invariants stay exact during fault injection.
    std::optional<VictimIdCorruptionForTest>
        pendingVictimIdCorruptionForTest;
#endif
    std::function<void()> workAvailableCallback;
};

} // namespace gem5::Chi

#endif // __HNF_SLCSF_HH__

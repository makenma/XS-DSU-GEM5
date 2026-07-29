#ifndef __HNF_SLCSF_HH__
#define __HNF_SLCSF_HH__

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>

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
    size_t victimLatency = 3;
    size_t sfEvictLatency = 2;
    size_t replayPenalty = 2;
    size_t victimBufferEntries = 2;
    size_t responseConsumeWidth = 1;
    bool enableSetLock = false;
    Tick childClockPeriod = 1;
};

template <class Params>
HnfSLCSFPipelineConfig
makeEmbeddedSlcsfConfig(const Params& p, Tick child_clock_period = 1)
{
    HnfSLCSFPipelineConfig config{};
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
             HnfSLCSFPipelineConfig pipeline_config = {});

    /**
     * Transfer request ownership into this wakeup's ingress buffer.
     *
     * A rejected rvalue is deliberately not moved from, allowing a caller to
     * retry the same owned message when registered credit becomes available.
     */
    SlcSfEnqueueResult tryEnqueue(SlcSfRequest&& request);

    /** Advance the registered request boundary at the current simulator tick. */
    void wakeup();

    /** Advance the registered request boundary at an explicit absolute tick. */
    void wakeup(Tick now);

    size_t registeredReqCredits() const { return visibleReqCredits; }
    size_t reqIngressCount() const { return reqIngress.size(); }
    size_t reqReadyCount() const { return reqReady.size(); }
    size_t reqInflightCount() const { return inflightRequests.size(); }
    size_t reqOutstanding() const;
    size_t reqCapacity() const { return config.reqQueueEntries; }
    uint64_t currentCycle() const { return wakeupCycle; }
    size_t mutationStageCount(MutationStage stage) const;

    size_t respReservedCount() const { return inflightRequests.size(); }
    size_t respPendingCount() const { return respPending.size(); }
    size_t respVisibleCount() const { return respVisible.size(); }
    size_t respOccupied() const;
    size_t respCapacity() const { return config.respQueueEntries; }
    std::optional<SlcSfResponse> popVisibleResponse();
    const HnfSLCSFPipelineConfig& pipelineConfig() const { return config; }

    /** Re-probe storage and validate every identity/version token field. */
    bool validateCommitToken(const SlcSfCommitToken& token,
                             SlcSfReqId expected_lookup_req_id,
                             uint64_t expected_line_address) const;

    bool hasWork() const;
    bool isBusy() const { return hasWork() || HnfSLCSFBackend::isBusy(); }

    // Stage-A lifecycle gates. Later lifecycle stories drive these at modeled
    // initialization and drain boundaries.
    void beginInitialization();
    void finishInitialization();
    void beginDraining();
    void resumeFromDrain();

  private:
    struct InflightRequest
    {
        SlcSfRequest request;
        uint64_t issueCycle = 0;
        uint64_t completeCycle = 0;
        std::optional<MutationStage> mutationStage;
        std::optional<SlcSfResponse> terminalResponse;
        bool resourcesPrepared = false;
        bool mutationCommitted = false;
        bool mutationStalled = false;
        bool earlyLookupReplay = false;
    };

    static void validateConfig(const HnfSLCSFPipelineConfig& config);
    uint64_t serviceLatency(const SlcSfRequest& request) const;
    Tick replayDeadline() const;
    bool lookupReplaysAtL0(const SlcSfRequest& request) const;
    void promotePendingResponses();
    void completeInflightRequests();
    SlcSfResponse makeTerminalResponse(const SlcSfRequest& request);
    std::optional<SlcSfError> validateMutationRequest(
        const SlcSfRequest& request) const;
    bool validateMutationToken(const SlcSfRequest& request) const;
    bool prepareMutationResources(InflightRequest& request);
    void executeMutation(InflightRequest& request);
    void advanceMutation(InflightRequest& request);
    void latchMutationResponse(InflightRequest& request);
    void promoteIngressRequests();
    void issueReadyRequests();
    void updateRegisteredCredits();
    void assertRequestAccounting() const;
    void assertResponseAccounting() const;

    HnfSLCSFPipelineConfig config;
    std::deque<SlcSfRequest> reqIngress;
    std::deque<SlcSfRequest> reqReady;
    // Membership in this queue is the terminal response reservation.  Keeping
    // request issue and reservation in one state prevents either from becoming
    // observable without the other.
    std::deque<InflightRequest> inflightRequests;
    std::deque<SlcSfResponse> respPending;
    std::deque<SlcSfResponse> respVisible;
    size_t visibleReqCredits = 0;
    uint64_t wakeupCycle = 0;
    Tick wakeupTick = 0;
    bool initialized = true;
    bool draining = false;
};

template <class LinkWakeup, class LinkHasWork, class CcHasWork>
bool
advanceEmbeddedSlcsfStageA(HnfSLCSF& slcsf, Tick now,
                           LinkWakeup&& linkWakeup,
                           LinkHasWork&& linkHasWork,
                           CcHasWork&& ccHasWork)
{
    slcsf.wakeup(now);
    linkWakeup();
    return slcsf.hasWork() || linkHasWork() || ccHasWork();
}

} // namespace gem5::Chi

#endif // __HNF_SLCSF_HH__

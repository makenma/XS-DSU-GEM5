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
};

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

    size_t respReservedCount() const { return inflightRequests.size(); }
    size_t respPendingCount() const { return respPending.size(); }
    size_t respVisibleCount() const { return respVisible.size(); }
    size_t respOccupied() const;
    size_t respCapacity() const { return config.respQueueEntries; }
    std::optional<SlcSfResponse> popVisibleResponse();

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
    };

    static void validateConfig(const HnfSLCSFPipelineConfig& config);
    void promotePendingResponses();
    void completeInflightRequests();
    SlcSfResponse makeTerminalResponse(const SlcSfRequest& request);
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

} // namespace gem5::Chi

#endif // __HNF_SLCSF_HH__

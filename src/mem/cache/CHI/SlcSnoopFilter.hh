#ifndef __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__
#define __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__

#include <algorithm>
#include <functional>
#include <limits>

#include "base/statistics.hh"
#include "mem/cache/CHI/HnfSLCSF.hh"
#include "params/SlcSnoopFilter.hh"
#include "sim/clocked_object.hh"

namespace gem5
{
class System;
}

namespace gem5::Chi
{

struct SlcSnoopFilterAdvance
{
    bool needsNextEdge = false;
    bool responseBecameVisible = false;
    bool creditBecameAvailable = false;
    bool drainBecameReady = false;
};

inline bool
slcSnoopFilterShouldNotifyOwner(const SlcSnoopFilterAdvance& advance)
{
    return advance.responseBecameVisible ||
        advance.creditBecameAvailable || advance.drainBecameReady;
}

struct SlcSnoopFilterScheduleDecision
{
    bool schedule = false;
    bool reschedule = false;
    Tick when = 0;
};

inline bool
slcSnoopFilterDrainReady(const HnfSLCSF& service)
{
    // gem5 drains every object concurrently and repeats the global drain
    // pass after producers have stopped.  A consumer must therefore keep
    // accepting late requests while an earlier pass is in progress; sealing
    // admission here can deadlock an O3 request which was still in flight
    // when the HN-F was visited.  The final global fixed point guarantees
    // that no producer can create more work.
    return service.isDrainRequested() && service.isCompletelyIdle();
}

/** Split a long weighted statistics sample into the API's int-sized chunks. */
template <class Sample>
inline void
slcSnoopFilterSampleChunks(uint64_t cycles, Sample&& sample)
{
    while (cycles != 0) {
        const int chunk = static_cast<int>(std::min<uint64_t>(
            cycles, std::numeric_limits<int>::max()));
        sample(chunk);
        cycles -= static_cast<uint64_t>(chunk);
    }
}

/** Decide whether an absent or existing service event needs changing. */
inline SlcSnoopFilterScheduleDecision
slcSnoopFilterScheduleDecision(
    std::optional<Tick> scheduled, std::optional<Tick> desired)
{
    if (!desired) {
        return {};
    }
    return {
        !scheduled.has_value(),
        scheduled && *desired < *scheduled,
        *desired};
}

/** Map a logical-cycle delta onto future child-clock edges. */
inline Tick
slcSnoopFilterWakeupTick(
    Tick now, std::optional<Tick> last_service_tick, Tick first_future_edge,
    Tick clock_period, uint64_t elapsed_cycles)
{
    panic_if(elapsed_cycles == 0 || clock_period == 0,
             "SlcSnoopFilter invalid wakeup delta or clock period\n");
    if (last_service_tick &&
        elapsed_cycles <= (MaxTick - *last_service_tick) / clock_period) {
        const Tick continuing =
            *last_service_tick + elapsed_cycles * clock_period;
        if (continuing > now) {
            return continuing;
        }
    }
    panic_if(elapsed_cycles - 1 >
                 (MaxTick - first_future_edge) / clock_period,
             "SlcSnoopFilter wakeup tick overflows\n");
    return first_future_edge + (elapsed_cycles - 1) * clock_period;
}

#ifdef UNIT_TEST
/** Advance the ordinary service at one absolute child-clock boundary. */
inline SlcSnoopFilterAdvance
advanceSlcSnoopFilterService(HnfSLCSF& service, Tick now)
{
    const bool had_visible_response = service.rawRespVisibleCount() != 0;
    const bool had_credit = service.registeredReqCreditGrants() != 0;
    const bool was_drain_ready = slcSnoopFilterDrainReady(service);
    service.wakeup(now);
    return {
        service.needsServiceWakeup(),
        !had_visible_response && service.rawRespVisibleCount() != 0,
        !had_credit && service.registeredReqCreditGrants() != 0,
        !was_drain_ready && slcSnoopFilterDrainReady(service)};
}
#endif

/**
 * Clocked ownership boundary for the HNF SLC and snoop filter.
 *
 * HnfSLCSF remains an ordinary C++ service: it owns storage, policy,
 * transient resources, queues, and pipeline state by value and has no event
 * ownership. This wrapper is the sole owner of its clocked wakeup event.
 */
class SlcSnoopFilter : public ClockedObject
{
  public:
    explicit SlcSnoopFilter(const SlcSnoopFilterParams& p);

    SlcSnoopFilter(const SlcSnoopFilter&) = delete;
    SlcSnoopFilter& operator=(const SlcSnoopFilter&) = delete;
    SlcSnoopFilter(SlcSnoopFilter&&) = delete;
    SlcSnoopFilter& operator=(SlcSnoopFilter&&) = delete;

    HnfSLCSF& service() { return slcsf; }
    const HnfSLCSF& service() const { return slcsf; }

    bool hasWork() const { return slcsf.hasWork(); }
    bool initialized() const { return slcsf.isInitialized(); }
    void requestDrain() { slcsf.requestDrain(); }
    bool drainRequested() const { return slcsf.isDrainRequested(); }
    bool admissionSealed() const { return slcsf.isAdmissionSealed(); }
    bool completelyIdle() const { return slcsf.isCompletelyIdle(); }
    uint64_t slcValidLineCount() const
    {
        return slcsf.slcValidLineCount();
    }
    uint64_t slcCapacityLineCount() const
    {
        return slcsf.slcCapacityLineCount();
    }
    uint32_t maxValidWaysInSet() const
    {
        return slcsf.maxValidWaysInSet();
    }
    void rearmSlcFullExit();
    void sealAdmission();
    void testDrainComplete();

    void initState() override;
    void startup() override;
    void loadState(CheckpointIn& cp) override;
    void preDumpStats() override;
    void resetStats() override;
    DrainState drain() override;
    void drainResume() override;
    void memWriteback() override;
    void serialize(CheckpointOut& cp) const override;
    void unserialize(CheckpointIn& cp) override;

    /** Register the owner callback used only to request a future wakeup. */
    void setFutureWakeupCallback(std::function<void(Tick)> callback)
    {
        futureWakeupCallback = std::move(callback);
    }

  private:
    struct SlcSnoopFilterStats : public statistics::Group,
                                 public HnfSLCSFStatsSink
    {
        SlcSnoopFilterStats(statistics::Group* parent,
                            const SlcSnoopFilterParams& p);

        void accepted(SlcSfStatOperation operation) override;
        void sampledOccupancy(size_t req, size_t resp, size_t inflight,
                              uint64_t cycles, bool req_full,
                              bool resp_full) override;
        void sampledStorage(uint64_t valid_lines,
                            uint64_t capacity_lines,
                            uint32_t max_valid_ways,
                            uint64_t cycles) override;
        void issued(uint64_t configured_latency) override;
        void victim(
            SlcSfStatVictim victim, uint32_t requester, uint32_t set,
            HnfSLCSFBackend::SlcReplacementPolicy policy) override;
        void terminal(const SlcSfResponse& response) override;
        void becameVisible(uint64_t latency) override;
        void rejectedNoCredit() override;
        void stalled(bool set_lock_conflict) override;

        statistics::Scalar lookupOperations;
        statistics::Scalar fillOperations;
        statistics::Scalar updateOperations;
        statistics::Scalar evictOperations;
        statistics::Scalar missMissLookups;
        statistics::Scalar slcHitLookups;
        statistics::Scalar sfHitLookups;
        statistics::Scalar slcSfHitLookups;
        statistics::Scalar directedSnoops;
        statistics::Scalar broadcastSnoops;
        statistics::Scalar cleanSlcVictims;
        statistics::Scalar dirtySlcVictims;
        statistics::Scalar sfVictims;
        statistics::Scalar slcValidLines;
        statistics::Scalar slcPeakValidLines;
        statistics::Scalar slcCapacityLines;
        statistics::Scalar slcOccupancyPercent;
        statistics::Scalar maxValidWaysPerSet;
        statistics::Scalar fullSlcCycles;
        statistics::Scalar replacementAttempts;
        statistics::Scalar totalSlcVictims;
        statistics::Vector victimByRequester;
        statistics::Vector victimBySet;
        statistics::Vector victimByPolicy;
        statistics::Scalar staleTokenReplays;
        statistics::Scalar resourceConflictReplays;
        statistics::Scalar seqConflictReplays;
        statistics::Scalar victimBufferFullReplays;
        statistics::Scalar cancelledReplays;
        statistics::Scalar requestFullCycles;
        statistics::Scalar responseFullCycles;
        statistics::Scalar noCredit;
        statistics::Scalar serviceStalls;
        statistics::Scalar setLockConflicts;
        statistics::Distribution requestOccupancy;
        statistics::Distribution responseOccupancy;
        statistics::Distribution inflightOccupancy;
        statistics::Distribution configuredServiceLatency;
        statistics::SparseHistogram acceptedToVisibleLatency;
    } stats;

    std::optional<Tick> calculateNextWakeup() const;
    void ensureWakeup();
    void processServiceEvent();

    EventFunctionWrapper serviceEvent;
    HnfSLCSF slcsf;
    uint64_t scheduledServiceCycle = 0;
    std::optional<Tick> lastServiceTick;
    std::function<void(Tick)> futureWakeupCallback;
    System* const system;
    const bool exitOnSlcFull;
    bool fullExitSignaled = false;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__

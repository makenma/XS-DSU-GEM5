#ifndef __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__
#define __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__

#include <functional>

#include "mem/cache/CHI/HnfSLCSF.hh"
#include "params/SlcSnoopFilter.hh"
#include "sim/clocked_object.hh"

namespace gem5::Chi
{

struct SlcSnoopFilterAdvance
{
    bool needsNextEdge = false;
    bool responseBecameVisible = false;
    bool creditBecameAvailable = false;
};

struct SlcSnoopFilterScheduleDecision
{
    bool schedule = false;
    bool reschedule = false;
    Tick when = 0;
};

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

/** Advance the ordinary service at one absolute child-clock boundary. */
inline SlcSnoopFilterAdvance
advanceSlcSnoopFilterService(HnfSLCSF& service, Tick now)
{
    const bool had_visible_response = service.respVisibleCount() != 0;
    const bool had_credit = service.registeredReqCredits() != 0;
    service.wakeup(now);
    return {
        service.needsServiceWakeup(),
        !had_visible_response && service.respVisibleCount() != 0,
        !had_credit && service.registeredReqCredits() != 0};
}

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
    void sealAdmission();
    void testDrainComplete();

    void initState() override;
    void startup() override;
    DrainState drain() override;
    void drainResume() override;

    /** Register the owner callback used only to request a future wakeup. */
    void setFutureWakeupCallback(std::function<void(Tick)> callback)
    {
        futureWakeupCallback = std::move(callback);
    }

  private:
    std::optional<Tick> calculateNextWakeup() const;
    void ensureWakeup();
    void processServiceEvent();

    EventFunctionWrapper serviceEvent;
    HnfSLCSF slcsf;
    uint64_t scheduledServiceCycle = 0;
    std::optional<Tick> lastServiceTick;
    std::function<void(Tick)> futureWakeupCallback;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__

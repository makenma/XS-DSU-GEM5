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

    void initState() override;
    void startup() override;

    /** Register the owner callback used only to request a future wakeup. */
    void setFutureWakeupCallback(std::function<void(Tick)> callback)
    {
        futureWakeupCallback = std::move(callback);
    }

  private:
    void scheduleServiceEvent();
    void processServiceEvent();

    EventFunctionWrapper serviceEvent;
    HnfSLCSF slcsf;
    std::function<void(Tick)> futureWakeupCallback;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__

#ifndef __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__
#define __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__

#include "mem/cache/CHI/HnfSLCSF.hh"
#include "params/SlcSnoopFilter.hh"
#include "sim/clocked_object.hh"

namespace gem5::Chi
{

/**
 * Clocked ownership boundary for the HNF SLC and snoop filter.
 *
 * HnfSLCSF remains an ordinary C++ service: it owns storage, policy,
 * transient resources, queues, and pipeline state by value and has no event
 * ownership. Later migration stages attach its wakeup to this object's clock.
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

    void advanceEmbedded(Tick now) { slcsf.wakeup(now); }
    bool hasWork() const { return slcsf.hasWork(); }

  private:
    HnfSLCSF slcsf;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_SLC_SNOOP_FILTER_HH__

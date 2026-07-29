#include "mem/cache/CHI/SlcSnoopFilter.hh"

#include "base/trace.hh"
#include "debug/SlcSnoopFilter.hh"

namespace gem5::Chi
{

SlcSnoopFilter::SlcSnoopFilter(const SlcSnoopFilterParams& p)
    : ClockedObject(p),
      serviceEvent([this] { processServiceEvent(); },
                   name() + ".serviceEvent"),
      slcsf(p.block_size, p.slc_num_sets, p.slc_num_ways, p.sf_num_sets,
            p.sf_num_ways, p.seq_entries,
            makeEmbeddedSlcsfConfig(p, clockPeriod()))
{
    slcsf.setWorkAvailableCallback([this] { scheduleServiceEvent(); });
    DPRINTF(SlcSnoopFilter,
            "Created block=%u SLC=%ux%u SF=%ux%u SEQ=%u\n",
            p.block_size, p.slc_num_sets, p.slc_num_ways, p.sf_num_sets,
            p.sf_num_ways, p.seq_entries);
}

void
SlcSnoopFilter::initState()
{
    ClockedObject::initState();
    slcsf.resetForColdStart();
}

void
SlcSnoopFilter::startup()
{
    ClockedObject::startup();
    if (!slcsf.isInitialized()) {
        scheduleServiceEvent();
    }
}

void
SlcSnoopFilter::scheduleServiceEvent()
{
    const Tick when = clockEdge() > curTick() ?
        clockEdge() : clockEdge(Cycles(1));
    if (!serviceEvent.scheduled()) {
        schedule(serviceEvent, when);
    } else if (when < serviceEvent.when()) {
        reschedule(serviceEvent, when);
    }
}

void
SlcSnoopFilter::processServiceEvent()
{
    const SlcSnoopFilterAdvance advance =
        advanceSlcSnoopFilterService(slcsf, curTick());

    if ((advance.responseBecameVisible ||
         advance.creditBecameAvailable) && futureWakeupCallback) {
        panic_if(curTick() == MaxTick,
                 "%s cannot notify its owner after MaxTick\n", name());
        futureWakeupCallback(curTick() + 1);
    }
    if (advance.needsNextEdge) {
        scheduleServiceEvent();
    }
}

} // namespace gem5::Chi

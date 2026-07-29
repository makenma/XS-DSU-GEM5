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
    slcsf.setWorkAvailableCallback([this] { ensureWakeup(); });
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
    ensureWakeup();
}

DrainState
SlcSnoopFilter::drain()
{
    // The parent owns the D1 -> D2 boundary.  Merely record the request here;
    // allocated upstream protocol owners must remain able to enqueue in D1.
    slcsf.requestDrain();
    return slcsf.isCompletelyIdle() ? DrainState::Drained :
                                      DrainState::Draining;
}

void
SlcSnoopFilter::sealAdmission()
{
    slcsf.beginDraining();
    ensureWakeup();
    testDrainComplete();
}

void
SlcSnoopFilter::testDrainComplete()
{
    if (slcsf.isCompletelyIdle()) {
        signalDrainDone();
    }
}

void
SlcSnoopFilter::drainResume()
{
    const bool had_credit = slcsf.registeredReqCredits() != 0;
    slcsf.resumeFromDrain();
    ensureWakeup();
    if (!had_credit && slcsf.registeredReqCredits() != 0 &&
        futureWakeupCallback) {
        panic_if(curTick() == MaxTick,
                 "%s cannot notify its owner after MaxTick\n", name());
        futureWakeupCallback(curTick() + 1);
    }
}

void
SlcSnoopFilter::serialize(CheckpointOut& cp) const
{
    panic_if(serviceEvent.scheduled(),
             "%s cannot checkpoint with a scheduled service event\n", name());
    ClockedObject::serialize(cp);
    Serializable::ScopedCheckpointSection section(cp, "slcsf");
    slcsf.serializePersistentState(cp);
}

void
SlcSnoopFilter::unserialize(CheckpointIn& cp)
{
    panic_if(serviceEvent.scheduled(),
             "%s cannot restore over a scheduled service event\n", name());
    ClockedObject::unserialize(cp);
    Serializable::ScopedCheckpointSection section(cp, "slcsf");
    slcsf.unserializePersistentState(cp);
    scheduledServiceCycle = slcsf.currentCycle();
    lastServiceTick.reset();
}

std::optional<Tick>
SlcSnoopFilter::calculateNextWakeup() const
{
    const std::optional<uint64_t> cycle = slcsf.calculateNextWakeupCycle();
    if (!cycle) {
        return std::nullopt;
    }
    panic_if(*cycle <= slcsf.currentCycle(),
             "%s calculated a non-future service cycle\n", name());
    const Tick next_edge = clockEdge() > curTick() ?
        clockEdge() : clockEdge(Cycles(1));
    return slcSnoopFilterWakeupTick(
        curTick(), lastServiceTick, next_edge, clockPeriod(),
        *cycle - slcsf.currentCycle());
}

void
SlcSnoopFilter::ensureWakeup()
{
    const std::optional<Tick> desired = calculateNextWakeup();
    const std::optional<Tick> scheduled = serviceEvent.scheduled() ?
        std::optional<Tick>(serviceEvent.when()) : std::nullopt;
    const SlcSnoopFilterScheduleDecision decision =
        slcSnoopFilterScheduleDecision(scheduled, desired);
    if (!decision.schedule && !decision.reschedule) {
        return;
    }

    const std::optional<uint64_t> cycle = slcsf.calculateNextWakeupCycle();
    panic_if(!cycle, "%s lost service work while scheduling\n", name());
    scheduledServiceCycle = *cycle;
    if (decision.schedule) {
        schedule(serviceEvent, decision.when);
    } else {
        reschedule(serviceEvent, decision.when);
    }
}

void
SlcSnoopFilter::processServiceEvent()
{
    panic_if(scheduledServiceCycle <= slcsf.currentCycle(),
             "%s service event lacks a future logical cycle\n", name());
    const uint64_t elapsed_cycles =
        scheduledServiceCycle - slcsf.currentCycle();
    const bool had_visible_response = slcsf.respVisibleCount() != 0;
    const bool had_credit = slcsf.registeredReqCredits() != 0;
    slcsf.wakeup(curTick(), elapsed_cycles);
    lastServiceTick = curTick();
    const SlcSnoopFilterAdvance advance{
        slcsf.needsServiceWakeup(),
        !had_visible_response && slcsf.respVisibleCount() != 0,
        !had_credit && slcsf.registeredReqCredits() != 0};

    if ((advance.responseBecameVisible ||
         advance.creditBecameAvailable) && futureWakeupCallback) {
        panic_if(curTick() == MaxTick,
                 "%s cannot notify its owner after MaxTick\n", name());
        futureWakeupCallback(curTick() + 1);
    }
    testDrainComplete();
    ensureWakeup();
}

} // namespace gem5::Chi

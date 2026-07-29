#include "mem/cache/CHI/SlcSnoopFilter.hh"

#include <algorithm>
#include <limits>

#include "base/trace.hh"
#include "debug/SlcSnoopFilter.hh"

namespace gem5::Chi
{

SlcSnoopFilter::SlcSnoopFilterStats::SlcSnoopFilterStats(
    statistics::Group* parent, const SlcSnoopFilterParams& p)
    : statistics::Group(parent, "stats"),
      ADD_STAT(lookupOperations, statistics::units::Count::get(),
               "Accepted lookup operations"),
      ADD_STAT(fillOperations, statistics::units::Count::get(),
               "Accepted fill operations"),
      ADD_STAT(updateOperations, statistics::units::Count::get(),
               "Accepted update operations"),
      ADD_STAT(evictOperations, statistics::units::Count::get(),
               "Accepted evict operations"),
      ADD_STAT(missMissLookups, statistics::units::Count::get(),
               "Completed lookups missing in both SLC and SF"),
      ADD_STAT(slcHitLookups, statistics::units::Count::get(),
               "Completed SLC-hit, SF-miss lookups"),
      ADD_STAT(sfHitLookups, statistics::units::Count::get(),
               "Completed SLC-miss, SF-hit lookups"),
      ADD_STAT(slcSfHitLookups, statistics::units::Count::get(),
               "Completed lookups hitting in both SLC and SF"),
      ADD_STAT(directedSnoops, statistics::units::Count::get(),
               "Lookup results requesting a directed snoop"),
      ADD_STAT(broadcastSnoops, statistics::units::Count::get(),
               "Lookup results requesting a broadcast snoop"),
      ADD_STAT(cleanSlcVictims, statistics::units::Count::get(),
               "Clean SLC victims produced"),
      ADD_STAT(dirtySlcVictims, statistics::units::Count::get(),
               "Dirty SLC victims produced"),
      ADD_STAT(sfVictims, statistics::units::Count::get(),
               "SF victims produced"),
      ADD_STAT(staleTokenReplays, statistics::units::Count::get(),
               "Replays caused by stale commit tokens"),
      ADD_STAT(resourceConflictReplays, statistics::units::Count::get(),
               "Replays caused by transient resource conflicts"),
      ADD_STAT(seqConflictReplays, statistics::units::Count::get(),
               "Replays caused by SEQ conflicts"),
      ADD_STAT(victimBufferFullReplays, statistics::units::Count::get(),
               "Replays caused by a full VictimBuffer"),
      ADD_STAT(cancelledReplays, statistics::units::Count::get(),
               "Terminal Replay responses produced by cancellation"),
      ADD_STAT(requestFullCycles, statistics::units::Cycle::get(),
               "Child cycles with full request occupancy"),
      ADD_STAT(responseFullCycles, statistics::units::Cycle::get(),
               "Child cycles with full response occupancy"),
      ADD_STAT(noCredit, statistics::units::Count::get(),
               "Admission attempts rejected for lack of credit"),
      ADD_STAT(serviceStalls, statistics::units::Count::get(),
               "Issued-work attempts stalled before semantic mutation"),
      ADD_STAT(setLockConflicts, statistics::units::Count::get(),
               "Service stalls caused by a set-lock conflict"),
      ADD_STAT(requestOccupancy, statistics::units::Count::get(),
               "Request occupancy sampled over child cycles"),
      ADD_STAT(responseOccupancy, statistics::units::Count::get(),
               "Response occupancy sampled over child cycles"),
      ADD_STAT(inflightOccupancy, statistics::units::Count::get(),
               "In-flight occupancy sampled over child cycles"),
      ADD_STAT(configuredServiceLatency, statistics::units::Cycle::get(),
               "Configured service latency sampled at issue"),
      ADD_STAT(acceptedToVisibleLatency, statistics::units::Cycle::get(),
               "Cycles from accepted request to visible response")
{
    requestOccupancy.init(0, p.slcsf_req_queue_entries, 1);
    responseOccupancy.init(0, p.slcsf_resp_queue_entries, 1);
    inflightOccupancy.init(0, p.slcsf_max_inflight, 1);
    const size_t max_service_latency =
        p.slcsf_fill_latency + p.slcsf_victim_latency +
        p.slcsf_sf_evict_latency;
    configuredServiceLatency.init(0, std::max<size_t>(1, max_service_latency),
                                  1);
    acceptedToVisibleLatency.init(
        0, std::max<size_t>(1024, max_service_latency), 1);
}

void
SlcSnoopFilter::SlcSnoopFilterStats::accepted(SlcSfStatOperation operation)
{
    switch (operation) {
      case SlcSfStatOperation::Lookup: ++lookupOperations; break;
      case SlcSfStatOperation::Fill: ++fillOperations; break;
      case SlcSfStatOperation::Update: ++updateOperations; break;
      case SlcSfStatOperation::Evict: ++evictOperations; break;
      case SlcSfStatOperation::NumOperations:
        panic("SlcSnoopFilter received invalid operation statistic\n");
    }
}

void
SlcSnoopFilter::SlcSnoopFilterStats::sampledOccupancy(
    size_t req, size_t resp, size_t inflight, uint64_t cycles,
    bool req_full, bool resp_full)
{
    panic_if(cycles > static_cast<uint64_t>(std::numeric_limits<int>::max()),
             "SlcSnoopFilter occupancy sample is too large\n");
    const int samples = static_cast<int>(cycles);
    requestOccupancy.sample(req, samples);
    responseOccupancy.sample(resp, samples);
    inflightOccupancy.sample(inflight, samples);
    requestFullCycles += req_full ? cycles : 0;
    responseFullCycles += resp_full ? cycles : 0;
}

void
SlcSnoopFilter::SlcSnoopFilterStats::issued(uint64_t configured_latency)
{
    configuredServiceLatency.sample(configured_latency);
}

void
SlcSnoopFilter::SlcSnoopFilterStats::terminal(const SlcSfResponse& response)
{
    if (const auto* lookup =
            std::get_if<SlcSfLookupResponse>(&response.payload())) {
        if (lookup->result.slcHit && lookup->result.sfHit) {
            ++slcSfHitLookups;
        } else if (lookup->result.slcHit) {
            ++slcHitLookups;
        } else if (lookup->result.sfHit) {
            ++sfHitLookups;
        } else {
            ++missMissLookups;
        }
        directedSnoops += lookup->result.snoopDirected;
        broadcastSnoops += lookup->result.snoopBroadcast;
    }
    if (const auto* replay = std::get_if<SlcSfReplay>(&response.payload())) {
        switch (replay->reason) {
          case SlcSfReplayReason::StaleCommitToken: ++staleTokenReplays; break;
          case SlcSfReplayReason::ResourceConflict:
            ++resourceConflictReplays; break;
          case SlcSfReplayReason::SeqConflict: ++seqConflictReplays; break;
          case SlcSfReplayReason::VictimBufferFull:
            ++victimBufferFullReplays; break;
          case SlcSfReplayReason::Cancelled: ++cancelledReplays; break;
        }
    }
    if (const auto* fill =
            std::get_if<SlcSfFillResponse>(&response.payload())) {
        if (fill->slcVictim) {
            fill->slcVictim->line.dirty ? ++dirtySlcVictims :
                                          ++cleanSlcVictims;
        }
        sfVictims += fill->sfVictim.has_value();
    } else if (const auto* update =
                   std::get_if<SlcSfUpdateResponse>(&response.payload())) {
        sfVictims += update->sfVictim.has_value();
    } else if (const auto* evict =
                   std::get_if<SlcSfEvictResponse>(&response.payload())) {
        sfVictims += evict->sfVictim.has_value();
    }
}

void
SlcSnoopFilter::SlcSnoopFilterStats::becameVisible(uint64_t latency)
{
    acceptedToVisibleLatency.sample(latency);
}

void
SlcSnoopFilter::SlcSnoopFilterStats::rejectedNoCredit()
{
    ++noCredit;
}

void
SlcSnoopFilter::SlcSnoopFilterStats::stalled(bool set_lock_conflict)
{
    ++serviceStalls;
    setLockConflicts += set_lock_conflict;
}

SlcSnoopFilter::SlcSnoopFilter(const SlcSnoopFilterParams& p)
    : ClockedObject(p), stats(this, p),
      serviceEvent([this] { processServiceEvent(); },
                   name() + ".serviceEvent"),
      slcsf(p.block_size, p.slc_num_sets, p.slc_num_ways, p.sf_num_sets,
            p.sf_num_ways, p.seq_entries,
            makeEmbeddedSlcsfConfig(p, clockPeriod()), &stats)
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

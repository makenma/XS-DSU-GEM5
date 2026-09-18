#include "dev/ai_mesh/prestart_cancel_driver.hh"

#include <fstream>
#include <sstream>

#include "base/logging.hh"
#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "params/PrestartCancelDriver.hh"
#include "sim/cur_tick.hh"
#include "sim/eventq.hh"
#include "sim/sim_exit.hh"

namespace gem5
{

namespace ai_mesh
{

PrestartCancelDriver::PrestartCancelDriver(const Params &p)
    : SimObject(p),
      dispatcher(p.dispatcher),
      cores(p.cores.begin(), p.cores.end()),
      evidence_path(p.evidence_path),
      poll_interval(p.poll_interval),
      wait_timeout(p.wait_timeout),
      observe_ticks(p.observe_ticks),
      cancel_on_wait(p.cancel_on_wait),
      poll_event(this),
      observe_event(this)
{
    fatal_if(dispatcher == nullptr, "cancel driver has no dispatcher");
    fatal_if(poll_interval == 0, "cancel driver poll interval must be nonzero");
    fatal_if(wait_timeout == 0, "cancel driver wait timeout must be nonzero");
    fatal_if(observe_ticks == 0, "cancel driver observe horizon must be nonzero");
}

void
PrestartCancelDriver::startup()
{
    schedule(&poll_event, curTick() + poll_interval);
}

uint64_t
PrestartCancelDriver::commandsIssued() const
{
    uint64_t issued = 0;
    for (const MeshDummyCore *core : cores)
        issued += core->commandsIssued.value();
    return issued;
}

void
PrestartCancelDriver::poll()
{
    if (!wait_observed) {
        if (dispatcher->cacheReservationWaiting()) {
            wait_observed = true;
            wait_tick = curTick();
            starts_before_cancel = dispatcher->instanceStarts();
            issued_before_cancel = commandsIssued();
        } else if (curTick() >= wait_timeout) {
            failure = "reservation wait never observed before the wait timeout";
            finish("PRESTART_CANCEL_WAIT_TIMEOUT");
            return;
        }
    }
    if (wait_observed && !cancel_issued && !cancel_on_wait) {
        cancelled_generation = dispatcher->instanceGeneration();
        starts_before_cancel = dispatcher->instanceStarts();
        issued_before_cancel = commandsIssued();
        schedule(&observe_event, curTick() + observe_ticks);
        return;
    }
    if (wait_observed && !cancel_issued) {
        cancelled_generation = dispatcher->instanceGeneration();
        starts_before_cancel = dispatcher->instanceStarts();
        issued_before_cancel = commandsIssued();
        dispatcher->cancelPendingInstance();
        cancel_issued = true;
        cancel_tick = curTick();
        pending_after_cancel = dispatcher->pendingReservations();
        retry_after_cancel = dispatcher->retryEventScheduled();
        watchdog_after_cancel = dispatcher->progressWatchdogScheduled();
        schedule(&observe_event, curTick() + observe_ticks);
        return;
    }
    if (!cancel_issued) {
        schedule(&poll_event, curTick() + poll_interval);
        return;
    }
}

void
PrestartCancelDriver::observe()
{
    if (cancel_on_wait && !cancel_issued) {
        failure = "cancel entry was never issued";
        finish("PRESTART_CANCEL_NOT_ISSUED");
        return;
    }
    observe_tick = curTick();
    starts_after_cancel = dispatcher->instanceStarts();
    issued_after_cancel = commandsIssued();
    generation_after_cancel = dispatcher->instanceGeneration();
    if (starts_after_cancel > starts_before_cancel &&
        generation_after_cancel == cancelled_generation)
        failure = "cancelled instance started after the cancel";
    finish("PRESTART_CANCEL_OBSERVED");
}

void
PrestartCancelDriver::finish(const std::string &cause)
{
    dispatcher->writeResultJson();
    const bool advanced =
        wait_observed && (cancel_issued || !cancel_on_wait);
    const bool no_late_start = !(starts_after_cancel > starts_before_cancel &&
                                 generation_after_cancel == cancelled_generation);
    const bool no_late_issue = issued_after_cancel <= issued_before_cancel;
    const bool window_clean = pending_after_cancel == 0 && !retry_after_cancel &&
                              !watchdog_after_cancel;
    std::ostringstream out;
    out << "{\n";
    out << "  \"driver\": \"prestart_cancel\",\n";
    out << "  \"cause\": \"" << cause << "\",\n";
    out << "  \"wait_observed\": " << (wait_observed ? "true" : "false")
        << ",\n";
    out << "  \"cancel_issued\": " << (cancel_issued ? "true" : "false")
        << ",\n";
    out << "  \"cancel_on_wait\": "
        << (cancel_on_wait ? "true" : "false") << ",\n";
    out << "  \"reservation_commits\": "
        << dispatcher->cacheReservationCommits() << ",\n";
    out << "  \"reservation_waiting\": "
        << (dispatcher->cacheReservationWaiting() ? "true" : "false")
        << ",\n";
    out << "  \"advanced_past_cancel\": " << (advanced ? "true" : "false")
        << ",\n";
    out << "  \"observe_ticks\": " << observe_ticks << ",\n";
    out << "  \"wait_tick\": " << wait_tick << ",\n";
    out << "  \"cancel_tick\": " << cancel_tick << ",\n";
    out << "  \"observe_tick\": " << observe_tick << ",\n";
    out << "  \"cancelled_generation\": " << cancelled_generation << ",\n";
    out << "  \"generation_after_cancel\": " << generation_after_cancel
        << ",\n";
    out << "  \"wait_attempts\": " << dispatcher->cacheReservationAttempts()
        << ",\n";
    out << "  \"pending_after_cancel\": " << pending_after_cancel << ",\n";
    out << "  \"retry_scheduled_after_cancel\": "
        << (retry_after_cancel ? "true" : "false") << ",\n";
    out << "  \"watchdog_scheduled_after_cancel\": "
        << (watchdog_after_cancel ? "true" : "false") << ",\n";
    out << "  \"starts_before_cancel\": " << starts_before_cancel << ",\n";
    out << "  \"starts_after_cancel\": " << starts_after_cancel << ",\n";
    out << "  \"issued_before_cancel\": " << issued_before_cancel << ",\n";
    out << "  \"issued_after_cancel\": " << issued_after_cancel << ",\n";
    out << "  \"no_late_start\": " << (no_late_start ? "true" : "false")
        << ",\n";
    out << "  \"no_late_issue\": " << (no_late_issue ? "true" : "false")
        << ",\n";
    out << "  \"wait_window_clean\": " << (window_clean ? "true" : "false")
        << ",\n";
    out << "  \"failure\": \"" << failure << "\"\n";
    out << "}\n";
    std::ofstream handle(evidence_path);
    fatal_if(!handle, "cannot write cancel driver evidence %s",
             evidence_path);
    handle << out.str();
    handle.close();
    if (!failure.empty())
        exitSimLoop(failure.c_str());
    else
        exitSimLoop(cause.c_str());
}

}
}
